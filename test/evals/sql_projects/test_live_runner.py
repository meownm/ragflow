"""Control doubles verify runner mechanics, not real model qualifications."""

import json
from contextlib import contextmanager
from copy import deepcopy

import pytest

from test.evals.sql_projects.golden import BASE, GoldenCase, Turn, proposal
from test.evals.sql_projects import run_live
from test.evals.sql_projects.discriminators import oracle_for
from test.unit_test.api.apps.business_documents import test_business_document_ai_cancellation as model_fixtures

model_boundary = model_fixtures.model_boundary


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [None, "qa-configured-model"])
async def test_cli_model_selection_reaches_provider_without_changing_profile(model_boundary, monkeypatch, tmp_path, model):
    import sys
    import httpx

    config, _, bundle, calls = model_boundary
    config["api_base"] = "http://provider/v1"
    original = deepcopy(config)
    expected = model or config["llm_name"]

    async def show(_self, url, **options):
        assert url == "http://provider/api/show" and options["json"] == {"model": expected}
        return httpx.Response(200, json={"model_info": {"qwen.context_length": 131072}}, request=httpx.Request("POST", url))

    async def chat(_self, _system, _messages, parameters):
        calls["parameters"].append(parameters)
        return json.dumps({"response_kind": "EXPLANATION", "answer": "LIMIT100."})

    @contextmanager
    def database(**_options):
        yield {}

    monkeypatch.setattr(httpx.AsyncClient, "post", show)
    monkeypatch.setattr(bundle, "async_chat", chat)
    monkeypatch.setattr(run_live, "disposable_database", database)
    monkeypatch.setattr(run_live, "configure_model_catalog", lambda: None)
    monkeypatch.setattr(run_live, "source_identity", lambda: {"head": "control", "sources": {}})
    monkeypatch.setattr(run_live, "execute_postgres", lambda *_args, **_kwargs: pytest.fail("Explanation must not execute SQL"))
    out = tmp_path / "evidence"
    argv = ["run_live", "--tenant-id", "synthetic", "--out", str(out), "--case", "G-explain-limit", "--repeats", "1"]
    if model:
        argv += ["--model", model]
    monkeypatch.setattr(sys, "argv", argv)
    assert await run_live.main() == 2
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    turn = json.loads((out / "turns.jsonl").read_text(encoding="utf-8"))
    assert summary["requested_model"] == model
    assert turn["events"][0]["model"]["model"] == expected
    assert calls["bundle"][0][0][1]["llm_name"] == expected
    assert config == original


def test_model_catalog_bootstrap_does_not_initialize_application_services(monkeypatch):
    from common import settings

    monkeypatch.setattr(settings, "init_settings", lambda: pytest.fail("A model-only test must not start search/storage/auth"))
    monkeypatch.setattr(settings, "FACTORY_LLM_INFOS", [])
    run_live.configure_model_catalog()
    assert settings.FACTORY_LLM_INFOS
    assert all("name" in item for item in settings.FACTORY_LLM_INFOS)


class ModelControl:
    def __init__(self, responses, *, audit=True):
        self.responses = iter(responses)
        self.inputs = []
        self.audit = audit

    async def async_generate(self, tenant, system, payload):
        self.inputs.append(payload)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value

    def consume_execution_audit(self):
        return (
            {
                "provider": "test-control",
                "model": "synthetic-control",
                "model_type": "chat",
                "parameters": {"temperature": 0, "top_p": 0.1, "max_tokens": 4096, "api_key": "secret-canary"},
                "token_usage": {},
            }
            if self.audit
            else None
        )


@pytest.mark.asyncio
async def test_different_inputs_use_a_fixed_actual_provider_budget(model_boundary, monkeypatch):
    _, _, bundle, calls = model_boundary
    response = {"response_kind": "EXPLANATION", "answer": "Ответ."}

    async def chat(_self, _system, _messages, parameters):
        calls["parameters"].append(parameters)
        return json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(bundle, "async_chat", chat)
    case = GoldenCase("CONTROL", "runner", (Turn("Объясни", "EXPLANATION", review="Explain"), Turn("Поясни подробнее, сохранив запрос", "EXPLANATION", review="Expand")), BASE)
    model = run_live.RAGFlowLLMAdapter(max_output_tokens=run_live.OUTPUT_TOKEN_BUDGET)
    results = await run_live.run_cases("synthetic", {}, model=model, cases=(case,), repeats=1)
    assert [record["status"] for record in results] == ["REVIEW_REQUIRED", "REVIEW_REQUIRED"]
    assert [parameters["max_completion_tokens"] for parameters in calls["parameters"]] == [4096, 4096]
    assert all(record["events"][0]["model"]["parameters"]["max_completion_tokens"] == 4096 for record in results)
    assert all("_output_token_budget" not in record["events"][0]["input"] for record in results)


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, "4096"])
def test_invalid_caller_output_budget_is_rejected_before_provider_access(budget):
    with pytest.raises(ValueError, match="positive integer"):
        run_live.RAGFlowLLMAdapter(max_output_tokens=budget)


@pytest.mark.asyncio
async def test_caller_budget_cannot_exceed_configured_provider_ceiling(model_boundary):
    config, _, _, calls = model_boundary
    config["max_output_tokens"] = 1024
    await run_live.RAGFlowLLMAdapter(max_output_tokens=4096).async_generate("synthetic", "System", {"context": "Input"})
    assert calls["parameters"][0]["max_completion_tokens"] == 1024


@pytest.mark.asyncio
async def test_caller_budget_cannot_exceed_remaining_context(model_boundary):
    config, _, _, calls = model_boundary
    config["max_tokens"] = 2048
    await run_live.RAGFlowLLMAdapter(max_output_tokens=4096).async_generate("synthetic", "System", {"context": "Input"})
    assert 0 < calls["parameters"][0]["max_completion_tokens"] < 2048


def proposed(turn):
    return {"sql": turn.query.oracle.sql, "parameters": turn.query.bindings(), "summary": turn.text}


@pytest.mark.asyncio
async def test_successful_query_is_explicitly_applied_before_next_turn_and_explanation_does_not_run(monkeypatch):
    edit = proposal("amount_min")
    case = GoldenCase("CONTROL", "runner", (edit, Turn("Объясни порог", "EXPLANATION", review="Explain threshold50")), BASE)
    model = ModelControl([proposed(edit), {"response_kind": "EXPLANATION", "answer": "Порог50."}])
    calls = []

    def execute(config, sql, values, **options):
        calls.append((sql, values))
        oracle = oracle_for(edit.query.oracle) if config.get("discriminator") else edit.query.oracle
        return {"columns": oracle.columns, "rows": oracle.rows, "row_count": len(oracle.rows)}

    monkeypatch.setattr(run_live, "execute_postgres", execute)
    results = await run_live.run_cases("synthetic", {}, discriminator_database={"discriminator": True}, model=model, cases=(case,), repeats=1)
    assert [record["status"] for record in results] == ["AUTOMATED_PASS", "REVIEW_REQUIRED"]
    assert len(calls) == 2
    assert model.inputs[1]["context"]["current_sql"] == edit.query.oracle.sql
    assert model.inputs[1]["context"]["current_parameters"] == edit.query.bindings()
    assert results[0]["test_action"] == "APPLY_ACCEPTED_PROPOSAL_TO_NEXT_TURN"
    assert "test_action" not in results[1]
    assert "secret-canary" not in json.dumps(results)


@pytest.mark.asyncio
async def test_missing_execution_audit_cannot_be_counted_as_a_real_model_pass():
    case = GoldenCase("CONTROL", "runner", (Turn("Объясни", "EXPLANATION", review="Explain"),), BASE)
    results = await run_live.run_cases("synthetic", {}, model=ModelControl([{"response_kind": "EXPLANATION", "answer": "Ответ."}], audit=False), cases=(case,), repeats=1)
    assert results[0]["status"] == "FAIL"
    assert results[0]["events"][0]["audit_error"]


@pytest.mark.asyncio
async def test_provider_error_is_recorded_without_credentials_and_stops_dependent_turns():
    case = GoldenCase("CONTROL", "runner", (proposal("amount_min"), proposal("amount_max")), BASE)
    results = await run_live.run_cases("synthetic", {}, model=ModelControl([RuntimeError("api_key=secret-canary")]), cases=(case,), repeats=1)
    assert len(results) == 1
    assert results[0]["errors"] == ["Execution failed: RuntimeError"]
    assert "secret-canary" not in json.dumps(results)


@pytest.mark.asyncio
async def test_each_repeat_starts_from_its_original_sql_instead_of_reusing_previous_state(monkeypatch):
    edit = proposal("amount_min")
    case = GoldenCase("CONTROL", "runner", (edit,), BASE)
    model = ModelControl([proposed(edit), proposed(edit), proposed(edit)])

    def execute(config, *args, **kwargs):
        oracle = oracle_for(edit.query.oracle) if config.get("discriminator") else edit.query.oracle
        return {"columns": oracle.columns, "rows": oracle.rows, "row_count": len(oracle.rows)}

    monkeypatch.setattr(run_live, "execute_postgres", execute)
    results = await run_live.run_cases("synthetic", {}, discriminator_database={"discriminator": True}, model=model, cases=(case,), repeats=3)
    assert [item["repetition"] for item in results] == [1, 2, 3]
    assert all(payload["context"]["current_sql"] == BASE.oracle.sql for payload in model.inputs)


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["max_tokens", "temperature", "top_p", "reasoning_effort", "enable_thinking"])
async def test_changed_generation_controls_cannot_qualify_comparable_repeats(control):
    class ChangingControls(ModelControl):
        def consume_execution_audit(self):
            audit = super().consume_execution_audit()
            if len(self.inputs) == 2:
                audit["parameters"][control] = {"max_tokens": 8192, "temperature": 1, "top_p": 0.8, "reasoning_effort": "high", "enable_thinking": True}[control]
            return audit

    case = GoldenCase("CONTROL", "runner", (Turn("Объясни", "EXPLANATION", review="Explain"),), BASE)
    response = {"response_kind": "EXPLANATION", "answer": "Ответ."}
    results = await run_live.run_cases("synthetic", {}, model=ChangingControls([response, response]), cases=(case,), repeats=2)
    assert results[1]["status"] == "FAIL"
    assert "Model identity or sampling controls changed during the run" in results[1]["errors"]
