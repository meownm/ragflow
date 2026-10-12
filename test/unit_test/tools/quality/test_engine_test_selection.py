"""Exercise engine selection with Git history and the actual publication gate."""

from io import BytesIO
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from tools.quality import select_engine_tests as selector


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for args in (("init", "-q"), ("config", "user.name", "Test"), ("config", "user.email", "test@example.invalid")):
        subprocess.run(["git", *args], check=True, capture_output=True)

    def commit(path, content="content"):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], check=True, capture_output=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()

    base = commit("README.md")
    monkeypatch.setattr(selector, "successful_base", lambda env: base)
    return commit, base


@pytest.mark.parametrize(
    "path",
    [
        "web/src/pages/search/index.tsx",
        "api/apps/business_documents/ai.py",
        "business_documents/application/documents.py",
        "api/source_workbench/adapters.py",
        "test/playwright/e2e/test_next_apps_chat.py",
        "internal/engine/README.md",
    ],
)
def test_application_ui_and_test_changes_skip_engines(repo, path):
    commit, _ = repo
    commit(path)
    assert selector.select({})["required"] is False


@pytest.mark.parametrize(
    "path",
    [
        "common/doc_store/doc_store_base.py",
        "rag/utils/es_conn.py",
        "rag/utils/infinity_conn.py",
        "rag/nlp/search.py",
        "rag/svr/task_executor.py",
        "internal/engine/infinity/chunk.go",
        "internal/ingestion/pipeline/run.go",
        "internal/service/nlp/retrieval.go",
        "docker/infinity_conf.toml",
        "docker/.env",
        ".github/workflows/tests.yml",
        "api/apps/restful_apis/chunk_api.py",
        "sdk/python/ragflow_sdk/modules/session.py",
        "test/testcases/test_sdk_api/test_retrieval.py",
    ],
)
def test_engine_behavior_and_runtime_changes_select_both(repo, path):
    commit, _ = repo
    commit(path)
    result = selector.select({})
    assert result["required"] is True
    assert result["reason"] == "engine-boundary-changed"
    assert path in result["paths"]


def test_earlier_unverified_engine_change_is_not_hidden_by_later_ui_commit(repo):
    commit, base = repo
    commit("rag/utils/es_conn.py")
    commit("web/ui.tsx")
    result = selector.select({"GITHUB_EVENT_NAME": "push"})
    assert result["required"] is True
    assert result["base"] == base


def test_unclassified_runtime_boundary_gets_live_acceptance(repo):
    commit, _ = repo
    commit("new_runtime/package.py")
    result = selector.select({})
    assert result["required"] is True
    assert result["reason"] == "unclassified-boundary-changed"
    assert result["paths"] == ["new_runtime/package.py"]


def test_rename_outside_engine_boundary_still_selects_engines(repo, monkeypatch):
    commit, _ = repo
    base = commit("rag/utils/es_conn.py")
    monkeypatch.setattr(selector, "successful_base", lambda env: base)
    subprocess.run(["git", "mv", "rag/utils/es_conn.py", "moved.py"], check=True, capture_output=True)
    commit("web/ui.tsx")
    assert selector.select({})["paths"] == ["rag/utils/es_conn.py"]


def test_pr_uses_complete_base_diff_without_actions_lookup(repo, monkeypatch):
    commit, base = repo
    commit("rag/nlp/search.py")
    commit("web/ui.tsx")
    monkeypatch.setattr(selector, "successful_base", lambda env: pytest.fail("PR must use its own base"))
    assert selector.select({"GITHUB_EVENT_NAME": "pull_request", "CI_PR_BASE": base})["required"] is True


def test_already_verified_revision_does_not_rerun_on_schedule_or_release(repo):
    for event in ("push", "schedule", "workflow_call"):
        assert selector.select({"GITHUB_EVENT_NAME": event})["required"] is False


def test_cli_exports_baseline_for_separated_lane_selection(repo, monkeypatch, tmp_path):
    _, base = repo
    output = tmp_path / "outputs.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    selector.main()
    assert dict(line.split("=", 1) for line in output.read_text().splitlines()) == {
        "run_engine_tests": "false",
        "engine_test_reason": "no-engine-changes",
        "engine_test_base": base,
    }


@pytest.mark.parametrize(
    "path,template",
    [
        ("pyproject.toml", '[project]\nname="ragflow"\nversion="VERSION"\ndependencies=["elastic-client==1"]\n'),
        ("uv.lock", '[[package]]\nname="ragflow"\nversion="VERSION"\nsource={virtual="."}\n[[package]]\nname="elastic-client"\nversion="1"\n'),
    ],
)
def test_release_version_bump_skips_engines_but_dependency_change_runs_them(repo, monkeypatch, path, template):
    commit, _ = repo
    base = commit(path, template.replace("VERSION", "1.0"))
    monkeypatch.setattr(selector, "successful_base", lambda env: base)
    commit(path, template.replace("VERSION", "1.1"))
    assert selector.select({})["required"] is False
    commit(path, template.replace("VERSION", "1.1").replace("==1", "==2").replace('version="1"', 'version="2"'))
    assert selector.select({})["required"] is True


@pytest.mark.parametrize("base", ["", "0" * 40, "f" * 40])
def test_missing_history_selects_engines(repo, monkeypatch, base):
    monkeypatch.setattr(selector, "successful_base", lambda env: base)
    assert selector.select({})["reason"] == "baseline-unavailable"


def test_api_failure_selects_engines(repo, monkeypatch):
    def unavailable(env):
        raise OSError("offline")

    monkeypatch.setattr(selector, "successful_base", unavailable)
    assert selector.select({})["required"] is True


def test_actions_baseline_ignores_pr_and_current_run(monkeypatch):
    def response(request, timeout):
        assert "/actions/workflows/tests.yml/runs?branch=main&status=success" in request.full_url
        assert timeout == 15
        return BytesIO(
            json.dumps(
                {
                    "workflow_runs": [
                        {"id": 3, "event": "push", "head_sha": "c" * 40},
                        {"id": 2, "event": "pull_request", "head_sha": "b" * 40},
                        {"id": 1, "event": "push", "head_sha": "a" * 40},
                    ]
                }
            ).encode()
        )

    monkeypatch.setattr(selector, "urlopen", response)
    assert (
        selector.successful_base(
            {
                "GITHUB_REF": "refs/tags/v1.0",
                "CI_DEFAULT_BRANCH": "main",
                "GITHUB_REPOSITORY": "owner/repo",
                "CI_WORKFLOW_FILE": "tests.yml",
                "GH_TOKEN": "fixture",
                "GITHUB_RUN_ID": "3",
            }
        )
        == "a" * 40
    )


def test_only_qualification_owner_uses_selector():
    jobs = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text())["jobs"]
    preflight = jobs["ragflow_preflight"]
    selection = next(step for step in preflight["steps"] if step.get("id") == "engine_tests")
    assert selection["run"] == "python3 tools/quality/select_engine_tests.py"
    assert selection["env"]["QUALIFICATION_PROFILE"] == "${{ inputs.profile || 'targeted' }}"
    for name in ("ragflow_tests_infinity", "ragflow_tests_elasticsearch"):
        assert jobs[name]["if"] == "${{ needs.ragflow_preflight.outputs.run_engine_tests == 'true' }}"


def test_release_always_selects_both_even_for_verified_sha(repo):
    assert selector.select({"QUALIFICATION_PROFILE": "release"})["required"] is True


@pytest.mark.parametrize("state", ["failure", "cancelled", "skipped", ""])
@pytest.mark.parametrize("lane", ["ragflow_preflight", "ragflow_tests_infinity", "ragflow_tests_elasticsearch"])
def test_required_failure_cannot_become_pass(state, lane):
    from tools.quality.release_evidence import RELEASE_LANES, qualification_status

    jobs = dict.fromkeys(RELEASE_LANES, "success")
    jobs[lane] = state
    assert qualification_status(jobs) != "PASS"
