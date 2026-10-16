"""Pure duplication guard; no model verdict or live database is involved."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from test.evals.business_documents import test_live_model_quality as live
from test.unit_test.tools.quality.test_business_document_quality_report import _placement_evidence
from business_documents.domain.content import apply_change_plan
from business_documents.domain.requirement_coverage import validate_reporting_monitoring_uniqueness
from business_documents.domain.errors import RuleViolation
from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.assets import prompt_descriptor
from business_documents.application.errors import ValidationError


@pytest.fixture()
def documents():
    case = json.loads(live.MODEL_GOLDEN_PATH.read_text(encoding="utf-8"))["cases"][-1]
    base = case["seed"]["base_document"]
    evidence = _placement_evidence(case)
    template = json.loads((live.ASSET_ROOT / "templates/business_requirements.v1.json").read_text(encoding="utf-8"))
    schema = json.loads((live.ASSET_ROOT / "contracts/document_draft.v2.schema.json").read_text(encoding="utf-8"))
    return base, apply_change_plan(base, evidence["change_plan"], template, schema)


def test_relocation_and_monitoring_reference_are_allowed(documents):
    before, after = documents
    monitoring = next(s for s in after["sections"] if s["id"] == "5.5")
    monitoring["blocks"].append({"type": "paragraph", "text": "Определения метрик приведены в разделе 5.4."})
    validate_reporting_monitoring_uniqueness(before, after)


@pytest.mark.parametrize("format", ["list", "table", "paragraph"])
def test_metric_names_with_navigation_are_allowed(documents, format):
    before, after = documents
    labels = [row[0] for row in next(s for s in after["sections"] if s["id"] == "5.4")["blocks"][0]["rows"]]
    if format == "list":
        block = {"type": "list", "items": [f"{label}: см. 5.4." for label in labels]}
    elif format == "table":
        block = {"type": "table", "headers": ["Метрика", "Ссылка"], "rows": [[label, "См. 5.4."] for label in labels]}
    else:
        block = {"type": "paragraph", "text": "; ".join(labels) + ": см. раздел 5.4."}
    next(s for s in after["sections"] if s["id"] == "5.5")["blocks"].append(block)
    validate_reporting_monitoring_uniqueness(before, after)


@pytest.mark.parametrize("format", ["table", "list"])
def test_full_metric_duplicates_are_rejected(documents, format):
    before, after = documents
    definitions = deepcopy(next(s for s in after["sections"] if s["id"] == "5.4")["blocks"][0])
    if format == "list":
        definitions = {"type": "list", "items": [": ".join(row) for row in definitions["rows"]]}
    next(s for s in after["sections"] if s["id"] == "5.5")["blocks"].append(definitions)
    with pytest.raises(RuleViolation) as failure:
        validate_reporting_monitoring_uniqueness(before, after)
    assert failure.value.code == "DUPLICATE_REQUIREMENT_CONTENT"
    assert failure.value.details == {"duplicate_section_pairs": [["5.4", "5.5"]]}


def test_unchanged_existing_duplication_does_not_block_an_unrelated_edit(documents):
    _, before = documents
    definitions = deepcopy(next(s for s in before["sections"] if s["id"] == "5.4")["blocks"][0])
    next(s for s in before["sections"] if s["id"] == "5.5")["blocks"].append(definitions)
    after = deepcopy(before)
    after["sections"][0]["blocks"].append({"type": "paragraph", "text": "Уточнение другого раздела."})
    validate_reporting_monitoring_uniqueness(before, after)


def test_scalar_table_cells_are_allowed_without_mutating_the_document(documents):
    before, after = documents
    reporting = next(s for s in after["sections"] if s["id"] == "5.4")
    reporting["blocks"].append({"type": "table", "headers": ["Порог", "Значение", "Включено", "Примечание"], "rows": [["Порог времени", 5, True, None]]})
    original = deepcopy(after)
    validate_reporting_monitoring_uniqueness(before, after)
    assert after == original


def test_ai_rejects_duplicate_definitions_before_inference(documents):
    before, after = documents
    case = json.loads(live.MODEL_GOLDEN_PATH.read_text(encoding="utf-8"))["cases"][-1]
    plan = _placement_evidence(case)["change_plan"]
    definitions = deepcopy(next(s for s in after["sections"] if s["id"] == "5.4")["blocks"])
    next(o for o in plan["operations"] if o["section_id"] == "5.5")["content"]["blocks"].extend(definitions)

    class NoInference:
        def generate(self, *_):
            raise AssertionError("Duplicate content must be rejected before inference")

    job = SimpleNamespace(tenant_id="quality", payload={"current_revision": {"document_ast": before}, "protocol": {}})
    with pytest.raises(ValidationError) as failure:
        BusinessDocumentAI(NoInference())._verify_requirements(job, plan)
    assert failure.value.code == "DUPLICATE_REQUIREMENT_CONTENT"


def test_duplicate_feedback_reaches_the_next_planner_request(documents):
    before, _ = documents
    payload = {"prompt": prompt_descriptor("PLAN_CHANGES"), "current_revision": {"document_ast": before}, "protocol": {}}
    original = deepcopy(payload)
    details = {"duplicate_section_pairs": [["5.4", "5.5"]]}
    job = SimpleNamespace(job_type="PLAN_CHANGES", attempt=2, payload=payload, error={"code": "DUPLICATE_REQUIREMENT_CONTENT", "details": details})
    prompt = BusinessDocumentAI._prompt(job)
    assert prompt.input_payload["job_input"]["retry_feedback"] == {"code": "DUPLICATE_REQUIREMENT_CONTENT", "details": details}
    assert payload == original
