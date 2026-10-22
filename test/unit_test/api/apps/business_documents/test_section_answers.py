"""Section answers survive intake and do not bypass substantive review gates."""

import pytest
from playhouse.test_utils import count_queries

from test.unit_test.api.apps.business_documents import test_business_document_service as service


@pytest.fixture()
def database():
    yield from service.database.__wrapped__()


def _draft(section_id="2", schema_version="1"):
    draft = service._draft()
    if schema_version == "2":
        draft.update(schema_version="2", section_scope="required")
    next(section for section in draft["sections"] if section["id"] == section_id)["blocks"] = [{"type": "paragraph", "text": "Требования не определены"}]
    return draft


def _question(stage="REVIEW", tag="npa.applicability", section_id="2"):
    question = service._question_batch(stage, tag)
    question["questions"][0].update(
        target_section_id=section_id,
        text="Есть ли требования по разделу?",
        options=[{"option_id": "ABSENT", "label": "Требований нет"}, {"option_id": "NOT_APPLICABLE", "label": "Неприменимо"}],
    )
    return question


def _answer(document, question, answer="ABSENT"):
    payload = {
        "question_id": question["question_id"],
        "selected_option_id": None if answer == "custom" else answer,
        "custom_answer": "НПА не применимы, требований нет." if answer == "custom" else None,
    }
    service.document_commands.execute(service.TENANT, service.AUTHOR, document["document_id"], service._command(document, "ANSWER_QUESTION", payload))
    return service.document_queries.get_document(document["document_id"], service.AUTHOR)


def _apply(document, warnings=()):
    requested = service.document_commands.execute(
        service.TENANT,
        service.AUTHOR,
        document["document_id"],
        service._command(document, "APPLY_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}),
    )
    return service._complete(
        requested["job_id"],
        {
            "change_plan": {
                "schema_version": "1",
                "base_revision_id": document["current_revision"]["revision_id"],
                "source_state_version": requested["state_version"],
                "operations": [],
                "acknowledged_no_change_event_ids": [q["answer"]["source_event_id"] for q in document["protocol"]["questions"] if q["answer"]],
            },
            "warnings": list(warnings),
        },
    )


@pytest.mark.parametrize("schema_version", ["1", "2"])
@pytest.mark.parametrize("marker_count", [1, 2, 3])
def test_marker_only_section_requires_confirmation_before_ordinary_agreement(database, schema_version, marker_count):
    draft = _draft(schema_version=schema_version)
    next(section for section in draft["sections"] if section["id"] == "2")["blocks"] = [{"type": "paragraph", "text": "Требования не определены"} for _ in range(marker_count)]

    document = service._request_and_complete_draft(service._create(), draft=draft)

    assert document["current_revision"]["section_texts"]["2"] == "Требования не определены"
    section = next(s for s in document["current_revision"]["document_ast"]["sections"] if s["id"] == "2")
    assert len(section["blocks"]) == 1
    assert [(q["target_section_id"], q["status"]) for q in document["protocol"]["questions"]] == [("2", "OPEN")]
    assert any(w["code"] == "REQUIRED_SECTION_EMPTY" and w.get("section_id") == "2" for w in document["current_revision"]["document_ast"]["warnings"])
    with pytest.raises(service.BusinessDocumentError) as caught:
        _apply(document)
    assert caught.value.code == "REVIEW_ASSESSMENT_REQUIRED"
    assert service.document_queries.get_document(document["document_id"], service.AUTHOR)["lifecycle_state"] == "REVIEW"

    document = _answer(document, document["protocol"]["questions"][0])
    document = service._complete_review_assessment(document)
    final = _apply(document)

    assert final["lifecycle_state"] == "AGREED"
    assert final["current_revision"]["section_texts"]["2"] == "Требования не определены"
    assert not final["current_revision"]["document_ast"].get("warnings")


@pytest.mark.parametrize("section_id", ["2", "4.1", "4.3"])
@pytest.mark.parametrize("schema_version", ["1", "2"])
def test_intake_confirmation_does_not_become_a_review_gap_or_diagram_warning(database, section_id, schema_version):
    document = service._create()
    requested = service.document_commands.execute(service.TENANT, service.AUTHOR, document["document_id"], service._command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = service._complete(requested["job_id"], _question("INTAKE", section_id=section_id))
    document = _answer(document, document["protocol"]["questions"][0], "custom")

    document = service._request_and_complete_draft(document, draft=_draft(section_id, schema_version))

    assert not document["protocol"]["questions"]
    assert document["current_revision"]["section_texts"][section_id] == "Требования не определены"
    assert not document["current_revision"]["document_ast"].get("warnings")
    with count_queries() as queries:
        records = service.document_writer.assessment(document["document_id"], 1)
    assert records.answered_section_ids == {section_id}
    assert records.questions == ()
    assert queries.count == 4
    assert service.document_writer.review(document["document_id"], 1).answered_section_ids == {section_id}
    requested = service.document_commands.execute(service.TENANT, service.AUTHOR, document["document_id"], service._command(document, "REQUEST_REVIEW_ASSESSMENT"))
    payload = service.BusinessDocumentJob.get_by_id(requested["job_id"]).payload
    assert payload["answered_section_ids"] == [section_id]
    assert payload["protocol"]["questions"] == []
    document = service._complete(requested["job_id"], {"schema_version": "1", "questions": [], "proposals": [], "comment_dispositions": []})
    assert _apply(document)["lifecycle_state"] == "AGREED"


@pytest.mark.parametrize("schema_version", ["1", "2"])
@pytest.mark.parametrize("tag", ["empty_section:2", "npa.applicability"])
@pytest.mark.parametrize("answer", ["ABSENT", "NOT_APPLICABLE", "custom"])
def test_answered_placeholder_allows_ordinary_apply_to_agree_without_changing_body(database, schema_version, tag, answer):
    document = service._request_and_complete_draft(service._create(), draft=_draft(schema_version=schema_version), review_questions=_question(tag=tag))
    body = document["current_revision"]["body_markdown"]
    document = _answer(document, document["protocol"]["questions"][0], answer)
    document = service._complete_review_assessment(document)
    assert all(q["status"] == "ANSWERED" for q in document["protocol"]["questions"])

    final = _apply(document)

    assert final["lifecycle_state"] == "AGREED"
    assert final["current_revision"]["body_markdown"] == body
    assert not final["current_revision"]["document_ast"].get("warnings")


def test_new_substantive_model_question_stays_open_after_an_answer_to_the_same_section(database):
    document = service._request_and_complete_draft(service._create(), draft=_draft(), review_questions=_question())
    document = _answer(document, document["protocol"]["questions"][0], "custom")
    requested = service.document_commands.execute(service.TENANT, service.AUTHOR, document["document_id"], service._command(document, "REQUEST_REVIEW_ASSESSMENT"))
    question = _question(tag="npa.followup")["questions"][0]
    question.pop("stage")
    question["text"] = "Есть ли отдельные требования по персональным данным?"
    document = service._complete(requested["job_id"], {"schema_version": "1", "questions": [question], "proposals": [], "comment_dispositions": []})

    warning = {"code": "SOURCE_NOTE", "section_id": "2", "message": "Примечание источника сохраняется."}
    final = _apply(document, [warning])

    assert final["lifecycle_state"] == "REVIEW"
    assert [(q["semantic_tag"], q["status"]) for q in final["protocol"]["questions"]] == [("npa.applicability", "ANSWERED"), ("npa.followup", "OPEN")]
    assert warning in final["current_revision"]["document_ast"]["warnings"]
    assert "REQUEST_EXPORT" not in final["allowed_commands"]


def test_independent_warning_survives_ordinary_agreement_after_section_answer(database):
    document = service._request_and_complete_draft(service._create(), draft=_draft())
    document = _answer(document, document["protocol"]["questions"][0])
    document = service._complete_review_assessment(document)
    warning = {"code": "REQUIREMENT_COVERAGE_FAILED", "section_id": "2", "message": "Независимое требование требует проверки."}

    final = _apply(document, [warning])

    assert final["lifecycle_state"] == "AGREED"
    assert final["current_revision"]["document_ast"]["warnings"] == [warning]
    assert "REQUEST_EXPORT" in final["allowed_commands"]
    assert "CONFIRM_PREPARED_CHANGES" not in final["allowed_commands"]


@pytest.mark.parametrize("schema_version", ["1", "2"])
def test_real_requirements_do_not_inherit_the_old_empty_section_warning(database, schema_version):
    document = service._request_and_complete_draft(service._create(), draft=_draft(schema_version=schema_version))
    question = document["protocol"]["questions"][0]
    text = "Применяется нормативный акт о защите персональных данных."
    service.document_commands.execute(
        service.TENANT,
        service.AUTHOR,
        document["document_id"],
        service._command(
            document,
            "ANSWER_QUESTION",
            {
                "question_id": question["question_id"],
                "selected_option_id": None,
                "custom_answer": text,
            },
        ),
    )
    document = service._complete_review_assessment(service.document_queries.get_document(document["document_id"], service.AUTHOR))
    section = next(s for s in document["current_revision"]["document_ast"]["sections"] if s["id"] == "2")
    requested = service.document_commands.execute(
        service.TENANT, service.AUTHOR, document["document_id"], service._command(document, "APPLY_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]})
    )

    final = service._complete(
        requested["job_id"],
        {
            "change_plan": {
                "schema_version": "1",
                "base_revision_id": document["current_revision"]["revision_id"],
                "source_state_version": requested["state_version"],
                "acknowledged_no_change_event_ids": [],
                "operations": [
                    {
                        "operation_id": "npa",
                        "type": "REPLACE_SECTION_CONTENT",
                        "section_id": "2",
                        "expected_section_hash": service.section_hash(section),
                        "source_event_ids": [document["protocol"]["questions"][0]["answer"]["source_event_id"]],
                        "content": {"blocks": [{"type": "paragraph", "text": text}]},
                    }
                ],
            }
        },
    )

    assert final["lifecycle_state"] == "AGREED"
    assert final["current_revision"]["section_texts"]["2"] == text
    assert not final["current_revision"]["document_ast"].get("warnings")


def test_answer_to_another_document_does_not_resolve_the_same_section_here(database):
    first = service._request_and_complete_draft(service._create(title="Первый документ"), draft=_draft())
    _answer(first, first["protocol"]["questions"][0])

    second = service._request_and_complete_draft(service._create(title="Второй документ"), draft=_draft())

    assert [(q["semantic_tag"], q["status"]) for q in second["protocol"]["questions"]] == [("empty_section:2", "OPEN")]
    assert second["current_revision"]["document_ast"]["warnings"][0]["code"] == "REQUIRED_SECTION_EMPTY"
