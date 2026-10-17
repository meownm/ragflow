"""Capture the real verifier request before inference; no verdict is supplied."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from test.evals.business_documents import test_live_model_quality as live
from api.apps.business_documents.ai import BusinessDocumentAI
from test.unit_test.tools.quality.test_business_document_quality_report import _placement_evidence


class RequestCaptured(RuntimeError):
    pass


def test_every_verifier_request_retains_the_confirmed_placement_answer():
    import json

    case = json.loads(live.MODEL_GOLDEN_PATH.read_text(encoding="utf-8"))["cases"][-1]
    evidence = _placement_evidence(case)
    seed = case["seed"]
    selected = next(option["label"] for option in seed["options"] if option["option_id"] == seed["selected_option_id"])
    # A long proposal must not displace the author's confirmed placement answer.
    proposal = "\n".join(f"Поле отчёта {number}: исходное значение сохраняется." for number in range(30))
    payload = {
        "active_change_input_event_ids": ["proposal", "answer"],
        "current_revision": {"document_ast": deepcopy(seed["base_document"])},
        "protocol": {
            "comments": [],
            "proposals": [{"decision": "ACCEPTED", "decision_event_id": "proposal", "text": proposal}],
            "questions": [{"text": seed["question"], "options": seed["options"], "answer": {"source_event_id": "answer", "selected_option_id": seed["selected_option_id"], "custom_answer": None}}],
        },
    }

    class Capture:
        def generate(self, tenant, system, request, *, timeout_seconds=None):
            self.request = request["job_input"]
            raise RequestCaptured

    capture = Capture()
    with pytest.raises(RequestCaptured):
        BusinessDocumentAI(capture)._verify_requirements(SimpleNamespace(tenant_id="quality-context", payload=payload), evidence["change_plan"])
    request = capture.request
    assert request["sources"] == {"proposal": {"text": proposal}, "answer": {"text": selected, "context": seed["question"]}}
    assert not {"fragments", "replacement_fragments"} & request.keys()
