"""Published projections retain loaded order, authorship and access boundaries."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock

import pytest

from business_documents.application.errors import BusinessDocumentError, ValidationError
from business_documents.application.queries import DocumentPage, DocumentQueries, RevisionHistory, project_job
from business_documents.domain.content import section_hash
from business_documents.domain.review import build_review_state


def document(**changes):
    return {
        "id": "doc",
        "tenant_id": "tenant",
        "owner_id": "owner",
        "chat_id": None,
        "document_type": "business_requirements",
        "catalog_entry_id": "L5",
        "title": "Requirements",
        "idea": "Initial business need",
        "dataset_ids": [],
        "template_version": "1",
        "policy_version": "1",
        "lifecycle_state": "REVIEW",
        "operation_state": "IDLE",
        "state_version": 7,
        "active_review_cycle": 2,
        "current_revision_id": "revision",
        "last_error": None,
        "update_time": 100,
        **changes,
    }


def section(text="Before"):
    return {"id": "1", "title": "Scope", "blocks": [{"type": "paragraph", "text": text}]}


def revision(identifier="revision", **changes):
    return {
        "id": identifier,
        "revision_number": 3,
        "author_id": None,
        "document_ast": {"sections": [section()]},
        "body_markdown": "Before",
        "content_hash": "content-hash",
        "source_event_ids": [],
        "create_time": 101,
        **changes,
    }


def event(identifier, kind, payload, actor="owner", actor_type="USER"):
    return {"id": identifier, "event_type": kind, "payload": payload, "actor_id": actor, "actor_type": actor_type, "create_time": 99}


def history(rows):
    return RevisionHistory(tuple(rows), {}, {}, {}, {}, {}, {}, {})


def empty_review():
    return build_review_state("doc", 2, questions=(), answers=(), proposals=(), decisions=(), comments=(), events=())


@pytest.mark.parametrize("error", [None, {}, "", {"code": "PROVIDER_UNAVAILABLE", "message": "Retry later"}])
def test_job_projection_normalizes_absent_error_without_losing_failure(error):
    row = dict.fromkeys(("job_type", "status", "progress", "progress_stage", "progress_message", "attempt", "max_attempts", "available_at", "lease_expires_at", "create_time", "update_time"))
    row.update(id="running-job", error=error)
    assert project_job(row)["error"] == (error or None)
    assert row["error"] == error


@pytest.fixture()
def queries():
    reader, content = Mock(), Mock()
    reader.document.return_value = document()
    reader.revisions.return_value = history([revision()])
    reader.revision.return_value = revision()
    reader.review.return_value = empty_review()
    reader.review_history.return_value = ()
    reader.users.return_value = {"owner": {"nickname": "Owner"}}
    reader.latest_job.return_value = None
    reader.preview.return_value = None
    reader.exports.return_value = ()
    reader.bindings.return_value = {}
    return DocumentQueries(reader, content, lambda: 1000), reader, content


def test_shared_reader_projection_has_no_mutating_actions_but_owner_does(queries):
    scenario, reader, _ = queries
    foreign = scenario.get_document("doc", "reader")
    own = scenario.get_document("doc", "owner")
    assert foreign["permissions"] == {"read": True, "edit": False, "delete": False, "assign": False}
    assert foreign["allowed_commands"] == []
    assert "ADD_COMMENT" in own["allowed_commands"]
    assert own["current_revision"]["section_texts"] == {"1": "Before"}
    reader.review.assert_called_with("doc", 2)


def test_agreed_source_body_carries_only_canonical_template_headings(queries):
    scenario, reader, content = queries
    reader.source_documents.return_value = (document(),)
    reader.source_agreements.return_value = ({**event("agreed", "ReviewAgreedWithoutChanges", {"revision_id": "revision"}), "document_id": "doc", "sequence": 1},)
    reader.source_revisions.return_value = (revision(document_id="doc"),)
    content.published_template.return_value = {
        "rendering": {"body_heading_base_level": 2},
        "sections": [{"id": "2", "title": "Нормативно-правовые акты (НПА)"}, {"id": "3.2", "title": "Географический охват"}],
    }
    metadata = scenario.agreed_sources("owner", ["doc"])
    content.published_template.assert_not_called()
    assert "section_headings" not in metadata[0]
    bodies = scenario.agreed_sources("owner", ["doc"], include_body=True)
    assert bodies[0]["body_markdown"] == "Before"
    assert bodies[0]["section_headings"] == ["## 2. Нормативно-правовые акты (НПА)", "### 3.2. Географический охват"]
    content.published_template.assert_called_once()


def test_intake_projection_has_no_revision_and_does_not_load_history(queries):
    scenario, reader, _ = queries
    reader.document.return_value = document(lifecycle_state="INTAKE", current_revision_id=None)
    result = scenario.get_document("doc", "owner")
    assert result["current_revision"] is None
    assert result["change_preview"] is None
    assert result["allowed_commands"] == ["ARCHIVE", "REQUEST_INTAKE_ASSESSMENT"]
    reader.revisions.assert_not_called()


@pytest.mark.parametrize("page,size", [(0, 20), (1, 0), (1, 101), ("1", 20), (1, "20")])
def test_invalid_pagination_never_calls_loader(queries, page, size):
    scenario, reader, _ = queries
    with pytest.raises(ValidationError, match="page must") as caught:
        scenario.list_documents("owner", page, size)
    assert caught.value.code == "INVALID_PAGINATION"
    reader.page.assert_not_called()


def test_scope_and_loaded_page_preserve_permissions_and_order(queries):
    scenario, reader, _ = queries
    reader.page.return_value = DocumentPage((document(id="other", owner_id="missing"), document()), 9, {"owner": {"nickname": ""}}, {}, {"doc": {"page_url": "https://eva/page"}}, {})
    result = scenario.list_documents("owner", 2, 10, scope="all")
    reader.page.assert_called_once_with(None, 2, 10)
    assert [row["document_id"] for row in result["items"]] == ["other", "doc"]
    assert [row["owner_name"] for row in result["items"]] == ["missing", "owner"]
    assert [row["permissions"]["edit"] for row in result["items"]] == [False, True]
    assert result["total"] == 9
    assert result["items"][1]["eva_page_url"] == "https://eva/page"
    assert result["items"][0]["current_revision_number"] is None
    reader.page.return_value = DocumentPage((document(),), 1, {}, {}, {}, {})
    mine = scenario.list_documents("owner", 2, 10, scope="mine")
    reader.page.assert_called_with("owner", 2, 10)
    assert [item["document_id"] for item in mine["items"]] == ["doc"]
    with pytest.raises(ValidationError) as caught:
        scenario.list_documents("owner", scope="tenant-private")
    assert caught.value.code == "INVALID_DOCUMENT_SCOPE"
    assert reader.page.call_count == 2


def test_access_user_assignment_requires_real_privilege_and_normalizes_loaded_roles(queries):
    scenario, reader, _ = queries
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.list_access_users("owner", access_role="ADMIN")
    assert caught.value.status == 403
    reader.active_users.assert_not_called()
    reader.active_users.return_value = [
        {"id": "a", "nickname": "A", "email": "a@example.invalid", "business_document_role": "ADMIN", "is_superuser": False},
        {"id": "b", "nickname": "B", "email": "b@example.invalid", "business_document_role": "AUTHOR_EDITOR", "is_superuser": True},
    ]
    result = scenario.list_access_users("moderator", access_role="EXTENDED_MODERATOR")
    assert [(row["user_id"], row["role"]) for row in result["items"]] == [("a", "AUTHOR_EDITOR"), ("b", "ADMIN")]


def test_revision_authorship_uses_explicit_then_request_then_job_then_user_never_worker(queries):
    scenario, reader, _ = queries
    rows = [revision("saved", author_id="saved-author"), revision("request"), revision("job"), revision("user"), revision("worker")]
    events = {
        "saved": event("saved", "DraftCreated", {"revision_id": "saved", "requested_by_actor_id": "wrong"}),
        "request": event("request", "DraftCreated", {"revision_id": "request", "requested_by_actor_id": "initiator"}, actor_type="AI"),
        "job": event("job", "ChangesApplied", {"revision_id": "job", "job_id": "job-id"}, actor_type="AI"),
        "user": event("user", "DraftCreated", {"revision_id": "user", "job_id": "invalid-job", "requested_by_actor_id": ""}, actor="user-author"),
        "worker": event("worker", "ChangesApplied", {"revision_id": "worker", "job_id": "empty-job"}, actor="worker-process", actor_type="AI"),
        "foreign": event("foreign", "DraftCreated", {"revision_id": "another-document", "requested_by_actor_id": "foreign"}),
        "malformed": event("malformed", "DraftCreated", None),
        "unrelated": event("unrelated", "BusinessDocumentJobRequested", {"revision_id": "worker"}),
    }
    reader.revisions.return_value = replace(
        history(rows),
        events=events,
        jobs={"job-id": {"payload": {"requested_by_actor_id": "job-author"}}, "invalid-job": {"payload": "historical malformed payload"}, "empty-job": {"payload": {"requested_by_actor_id": None}}},
        users={"initiator": {"nickname": "Initiator", "email": "i@example.invalid"}},
    )
    result = scenario.list_revisions("doc")
    assert [row["author_id"] for row in result] == ["saved-author", "initiator", "job-author", "user-author", None]
    assert result[1]["author_name"] == "Initiator"
    assert result[0]["author_name"] is None
    assert result[-1]["author_login"] is None


def test_change_basis_keeps_selected_source_order_and_truthful_actors(queries):
    scenario, reader, _ = queries
    events = {
        "draft": event("draft", "DraftCreated", {"requested_by_actor_id": "initiator"}, actor="worker", actor_type="AI"),
        "answer": event("answer", "QuestionAnswered", {"question_id": "q", "answer_id": "a"}),
        "proposal": event("proposal", "ProposalDecided", {"proposal_id": "p", "decision": "ACCEPTED"}),
        "comment": event("comment", "AuthorCommentAdded", {"comment_id": "c"}),
        "import": event("import", "EvaDocumentImported", {"page_url": "https://eva/page", "remote_version": 4}),
        "pull": event("pull", "EvaDocumentPulled", {}),
        "applied": event("applied", "ChangesApplied", {}),
        "agreed": event("agreed", "ReviewAgreedWithoutChanges", {}),
        "rejected": event("rejected", "ProposalDecided", {"proposal_id": "p", "decision": "REJECTED"}),
        "missing-answer": event("missing-answer", "QuestionAnswered", {"question_id": "gone", "answer_id": "a"}),
        "missing-proposal": event("missing-proposal", "ProposalDecided", {"proposal_id": "gone", "decision": "ACCEPTED"}),
        "missing-comment": event("missing-comment", "AuthorCommentAdded", {"comment_id": "gone"}),
    }
    sources = ["comment", "answer", "draft", "proposal", "import", "pull", "applied", "agreed", "rejected", "missing-answer", "missing-proposal", "missing-comment", "gone", 42]
    reader.revisions.return_value = replace(
        history([revision(source_event_ids=sources)]),
        events=events,
        questions={"q": {"text": "Which scope?", "target_section_id": "1", "options": [None, {"option_id": "other"}, {"option_id": "selected", "label": "Chosen scope"}]}},
        answers={"a": {"selected_option_id": "selected", "custom_answer": ""}},
        proposals={"p": {"text": "Proposal text", "rationale": "Reason", "target_section_id": "1"}},
        comments={"c": {"text": "Comment text", "section_id": "1", "anchor": {"selected_text": "Before"}}},
        users={"owner": {"nickname": "Owner", "email": "owner@example.invalid"}, "initiator": {"nickname": "", "email": ""}},
    )
    result = scenario.get_revision("doc", "revision")["change_basis"]
    assert [item["event_id"] for item in result] == sources[:6]
    assert [item["type"] for item in result] == ["COMMENT", "QUESTION", "INITIAL_DRAFT", "PROPOSAL", "EVA_SYNC", "EVA_SYNC"]
    assert result[0]["details"] == "Before"
    assert result[1]["details"] == "Chosen scope"
    assert result[2]["summary"] == "Initial business need"
    assert result[2]["initiated_by_actor_id"] == "initiator"
    assert result[2]["initiated_by_actor_name"] is None
    assert "actor_name" not in result[2]
    assert result[4]["details"] == "4"
    assert result[5]["summary"] == "Связанная страница EVA" and result[5]["details"] is None


@pytest.mark.parametrize(
    "custom,options,selected,expected",
    [("Custom scope", [{"option_id": "chosen", "label": "Label"}], "chosen", "Custom scope"), ("", [{"option_id": "chosen"}], "chosen", "chosen"), ("", [], "removed-option", None)],
)
def test_historical_answer_details_use_custom_text_then_known_option_without_invention(queries, custom, options, selected, expected):
    scenario, reader, _ = queries
    reader.revisions.return_value = replace(
        history([revision(source_event_ids=["e"])]),
        events={"e": event("e", "QuestionAnswered", {"question_id": "q", "answer_id": "a"})},
        questions={"q": {"options": options, "text": "Scope", "target_section_id": "1"}},
        answers={"a": {"custom_answer": custom, "selected_option_id": selected}},
    )
    assert scenario.get_revision("doc", "revision")["change_basis"][0]["details"] == expected


@pytest.mark.parametrize("cursor", [-1, True, "0", None])
def test_stream_cursor_is_strict_and_never_passes_bad_value_to_event_reader(queries, cursor):
    scenario, reader, _ = queries
    reader.job.return_value = {"tenant_id": "tenant"}
    with pytest.raises(ValidationError) as caught:
        scenario.read_job_stream_events("tenant", "reader", "doc", "job", cursor)
    assert caught.value.code == "INVALID_EVENT_CURSOR"
    reader.stream_events.assert_not_called()


@pytest.mark.parametrize("missing", ["document", "foreign-document", "job", "foreign-job"])
def test_stream_never_reads_events_for_unknown_or_foreign_tenant(queries, missing):
    scenario, reader, _ = queries
    reader.job.return_value = {"tenant_id": "tenant"}
    if missing == "document":
        reader.document.return_value = None
    elif missing == "foreign-document":
        reader.document.return_value = document(tenant_id="another-tenant")
    else:
        reader.job.return_value = None if missing == "job" else {"tenant_id": "another-tenant"}
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.read_job_stream_events("tenant", "reader", "doc", "job", 0)
    assert caught.value.status == 404
    reader.stream_events.assert_not_called()
    if "document" in missing:
        reader.job.assert_not_called()


def test_stream_keeps_cursor_order_and_terminal_status(queries):
    scenario, reader, _ = queries
    reader.job.return_value = {"tenant_id": "tenant"}
    events = [{"sequence": 8, "type": "delta"}, {"sequence": 9, "type": "completed"}]
    reader.stream_events.return_value = events, "COMPLETED"
    result = scenario.read_job_stream_events("tenant", "reader", "doc", "job", 7)
    assert result == {"events": events, "status": "COMPLETED", "job_id": "job"}
    reader.stream_events.assert_called_once_with("job", 7)


@pytest.mark.parametrize("preview", [None, {"id": "another-job"}])
def test_stale_preview_cannot_load_or_apply_revision(queries, preview):
    scenario, reader, content = queries
    reader.preview.return_value = preview
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.get_change_preview("doc", "job")
    assert caught.value.code == "CHANGE_PREVIEW_STALE"
    reader.revision.assert_not_called()
    content.apply_change_plan.assert_not_called()


@pytest.mark.parametrize("result", [None, {"output": None}, {"output": {}}])
def test_preview_requires_validated_plan_before_reading_base_revision(queries, result):
    scenario, reader, content = queries
    reader.preview.return_value = {"id": "job", "result": result}
    problem = ValidationError("INVALID_CHANGE_PLAN", "Plan is required")
    content.validate_contract.side_effect = problem
    with pytest.raises(ValidationError) as caught:
        scenario.get_change_preview("doc", "job")
    assert caught.value is problem
    content.validate_contract.assert_called_once_with("change_plan", None)
    reader.revision.assert_not_called()


def test_preview_missing_revision_fails_after_plan_validation(queries):
    scenario, reader, content = queries
    plan = {"operations": []}
    reader.preview.return_value = {"id": "job", "result": {"output": {"change_plan": plan}}}
    reader.revision.return_value = None
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.get_change_preview("doc", "job")
    assert caught.value.code == "REVISION_NOT_FOUND" and caught.value.status == 404
    content.validate_contract.assert_called_once_with("change_plan", plan)
    content.apply_change_plan.assert_not_called()


def test_preview_projects_ordered_sources_and_independent_before_after_content(queries):
    scenario, reader, content = queries
    source_ids = ["comment", "answer", "proposal", "eva"]
    plan = {"operations": [{"section_id": "1", "source_event_ids": source_ids, "content": {"blocks": []}}], "acknowledged_no_change_event_ids": ["unknown"]}
    base = {"sections": [section()]}
    draft = {"sections": [{**section("After"), "evidence_refs": ["evidence"]}]}
    reader.revision.return_value = revision(document_ast=base)
    reader.preview.return_value = {
        "id": "job",
        "base_revision_id": "revision",
        "result": {"output": {"change_plan": plan}},
        "payload": {
            "source_events": [
                None,
                {"event_id": "comment", "event_type": "AuthorCommentAdded", "payload": {"comment_id": "c"}},
                {"event_id": "answer", "event_type": "QuestionAnswered", "payload": {"question_id": "q"}},
                {"event_id": "proposal", "event_type": "ProposalDecided", "payload": {"proposal_id": "p"}},
                {"event_id": "eva", "event_type": "EvaDocumentPulled", "payload": None},
            ],
            "protocol": {
                "questions": [None, {"question_id": "q", "text": "Question"}],
                "proposals": [None, {"proposal_id": "p", "text": "Proposal"}],
                "comments": [None, {"comment_id": "c", "text": "Comment"}],
            },
        },
    }
    content.apply_change_plan.return_value = draft
    before = deepcopy(base)
    result = scenario.get_change_preview("doc", "job")
    assert base == before
    assert result["state_version"] == 7 and result["base_revision_id"] == "revision"
    item = result["sections"][0]
    assert (item["before"], item["after"]) == ("Before", "After")
    assert (item["before_evidence_refs"], item["after_evidence_refs"]) == ([], ["evidence"])
    assert [source["kind"] for source in item["sources"]] == ["comment", "question", "proposal", "eva"]
    assert [source["text"] for source in item["sources"]] == ["Comment", "Question", "Proposal", None]
    assert result["acknowledged_no_change_sources"][0]["text"] is None
    content.apply_change_plan.assert_called_once_with(base, plan)


def test_revision_snapshot_hashes_only_identified_sections_and_legacy_text_is_empty(queries):
    scenario, reader, _ = queries
    reader.revisions.return_value = history([revision(document_ast={"sections": [section(), None, {"id": 4}, {"title": "legacy"}]})])
    result = scenario.revision_snapshot(document(), "revision")
    assert result["section_hashes"] == {"1": section_hash(result["document_ast"]["sections"][0])}
    assert result["document_ast"]["sections"][0]["blocks"][0]["id"]
    reader.revisions.return_value = history([revision(document_ast=None)])
    assert scenario.get_revision("doc", "revision")["section_texts"] == {}


def test_missing_revision_is_not_a_successful_empty_projection(queries):
    scenario, reader, _ = queries
    reader.revisions.return_value = history([])
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.get_revision("doc", "foreign-revision")
    assert caught.value.code == "REVISION_NOT_FOUND"
    reader.revisions.assert_called_once_with("doc", "foreign-revision")


def test_review_projection_chooses_latest_answer_even_when_loader_order_is_reversed(queries):
    scenario, reader, _ = queries
    question = {
        "id": "q",
        "stage": "REVIEW",
        "review_cycle": 2,
        "target_section_id": "1",
        "semantic_tag": "scope",
        "text": "Scope?",
        "options": [{"option_id": "option", "label": "Chosen"}],
        "allow_custom_answer": True,
        "evidence_refs": [],
        "source_event_ids": [],
    }
    newest = {"id": "newest", "question_id": "q", "selected_option_id": None, "custom_answer": "Newest", "actor_id": "owner", "create_time": 200}
    oldest = {**newest, "id": "oldest", "custom_answer": "Oldest", "create_time": 100}
    protocol_event = {**event("answer-event", "QuestionAnswered", {"question_id": "q", "answer_id": "newest"}), "sequence": 1}
    reader.review.return_value = build_review_state("doc", 2, questions=[question], answers=[newest, oldest], proposals=(), decisions=(), comments=(), events=[protocol_event])
    result = scenario.get_document("doc", "reader")
    assert result["protocol"]["questions"][0]["answer"] == {"answer_id": "newest", "selected_option_id": None, "custom_answer": "Newest", "actor_id": "owner", "source_event_id": "answer-event"}
    assert result["protocol"]["questions"][0]["status"] == "ANSWERED"
    assert result["allowed_commands"] == []


@pytest.mark.parametrize("damage,code", [("duplicate-target", "DUPLICATE_CHANGE_TARGET"), ("unknown-target", "CHANGE_SECTION_NOT_FOUND"), ("stale-hash", "SECTION_HASH_CONFLICT")])
def test_preview_real_content_adapter_rejects_bad_plan_targets_without_mutating_base(queries, damage, code):
    from test.unit_test.api.apps.business_documents.test_business_document_worker_exports import _minimal_ast, document_commands

    _, reader, _ = queries
    scenario = DocumentQueries(reader, document_commands._content, lambda: 1000)
    base = _minimal_ast()
    target = base["sections"][0]
    operation = {
        "operation_id": "op1",
        "type": "REPLACE_SECTION_CONTENT",
        "section_id": target["id"],
        "expected_section_hash": section_hash(target),
        "source_event_ids": ["feedback"],
        "content": {"blocks": [{"type": "paragraph", "text": "Updated requirements"}]},
    }
    operations = [operation]
    if damage == "duplicate-target":
        operations.append({**operation, "operation_id": "op2"})
    elif damage == "unknown-target":
        operation["section_id"] = "999"
    else:
        operation["expected_section_hash"] = "sha256:" + "0" * 64
    plan = {"schema_version": "1", "base_revision_id": "revision", "source_state_version": 6, "acknowledged_no_change_event_ids": [], "operations": operations}
    reader.preview.return_value = {"id": "job", "base_revision_id": "revision", "result": {"output": {"change_plan": plan}}, "payload": {}}
    reader.revision.return_value = revision(document_ast=base)
    before = deepcopy(base)
    plan_before = deepcopy(plan)
    with pytest.raises(ValidationError) as caught:
        scenario.get_change_preview("doc", "job")
    assert caught.value.code == code
    assert base == before
    assert plan == plan_before


def test_revision_text_projection_preserves_supported_list_image_and_reference_blocks(queries):
    scenario, reader, _ = queries
    blocks = [
        {"type": "list", "items": ["First", "Second"]},
        {"type": "image", "alt": "Architecture", "url": "https://images.invalid/architecture.png"},
        {"type": "reference", "label": "Evidence", "url": "https://evidence.invalid/source"},
    ]
    reader.revisions.return_value = history([revision(document_ast={"sections": [{**section(), "blocks": blocks}]})])
    result = scenario.get_revision("doc", "revision")
    assert result["section_texts"] == {"1": "- First\n- Second\n\n![Architecture](https://images.invalid/architecture.png)\n\n[Evidence](https://evidence.invalid/source)"}
    identified = result["document_ast"]["sections"][0]["blocks"]
    assert all(block["id"] for block in identified)
    assert [{key: value for key, value in block.items() if key != "id"} for block in identified] == blocks
