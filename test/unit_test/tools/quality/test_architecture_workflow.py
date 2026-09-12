"""Contracts for the always-triggered T3 architecture workflow."""

import copy
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(os.environ.get("ARCHITECTURE_CANDIDATE_ROOT", Path(__file__).resolve().parents[4])).resolve()
WORKFLOW = ROOT / ".github/workflows/architecture.yml"
ACTION_REFS = {
    "actions/checkout": "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
    "actions/download-artifact": "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "actions/setup-node": "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
    "actions/upload-artifact": "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    "astral-sh/setup-uv": "astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e",
    "pnpm/action-setup": "pnpm/action-setup@b906affcce14559ad1aafd4ab0e942779e9f58b1",
}


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _candidate_workflows() -> dict[str, dict]:
    workflow_directory = ROOT / ".github/workflows"
    documents = {}
    for path in sorted(item for item in workflow_directory.iterdir() if item.is_file() and item.suffix.lower() in {".yaml", ".yml"}):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        repository_path = path.relative_to(ROOT).as_posix()
        assert isinstance(document, dict), f"Workflow {repository_path} must contain a mapping"
        assert isinstance(document.get("jobs"), dict), f"Workflow {repository_path} must contain jobs"
        documents[repository_path] = document
    return documents


def _assert_unique_required_status_context(workflows: dict[str, dict]) -> None:
    owners = []
    for path, workflow in workflows.items():
        for job_id, job in workflow["jobs"].items():
            assert isinstance(job_id, str) and isinstance(job, dict), f"Workflow {path} has an invalid job"
            effective_name = job.get("name", job_id)
            assert isinstance(effective_name, str), f"Workflow {path} job {job_id} has an invalid name"
            assert "${{" not in effective_name, f"Workflow {path} job {job_id} has a dynamic status context"
            if effective_name == "architecture-policy":
                owners.append((path, job_id))
    assert owners == [(".github/workflows/architecture.yml", "architecture-policy")]


def _step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def _commands(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def _assert_required_workflow_controls(workflow: dict) -> None:
    events = workflow[True]
    assert set(events) == {"pull_request_target", "merge_group", "push"}
    assert set(events["pull_request_target"]["types"]) == {"opened", "synchronize", "reopened", "ready_for_review", "edited"}
    assert events["merge_group"] == {"types": ["checks_requested"]}
    assert "paths" not in events["pull_request_target"] and "paths-ignore" not in events["pull_request_target"]
    assert "paths" not in events["merge_group"] and "paths-ignore" not in events["merge_group"]
    assert "paths" not in events["push"] and "paths-ignore" not in events["push"]
    assert "cancel-in-progress" not in workflow["concurrency"]

    jobs = workflow["jobs"]
    action_uses = [step["uses"] for job in jobs.values() for step in job["steps"] if "uses" in step]
    assert set(action_uses) == set(ACTION_REFS.values())
    assert all(re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", action) for action in action_uses)

    plan = jobs["architecture-policy-plan"]
    candidate = _step(plan, "Resolve candidate revision")
    assert candidate["id"] == "candidate"
    assert set(candidate["env"]) == {"EVENT_NAME", "EVENT_SHA", "PR_MERGE_SHA", "MERGE_GROUP_HEAD_SHA"}
    assert all(branch in candidate["run"] for branch in ("pull_request_target)", "merge_group)", "push)"))
    assert "Candidate revision is not an immutable commit SHA" in candidate["run"]
    assert plan["outputs"]["candidate_sha"] == "${{ steps.candidate.outputs.sha }}"

    checkouts = {
        job_id: next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        for job_id, job in jobs.items()
    }
    assert checkouts["architecture-policy-plan"]["with"] == {
        "fetch-depth": 0,
        "persist-credentials": False,
        "ref": "${{ steps.candidate.outputs.sha }}",
    }
    for job_id in ("architecture-policy-analysis", "architecture-policy"):
        assert checkouts[job_id]["with"] == {
            "fetch-depth": 0,
            "persist-credentials": False,
            "ref": "${{ needs.architecture-policy-plan.outputs.candidate_sha }}",
        }


def _hash_locked_requirements(input_path: Path, lock_path: Path) -> tuple[list[str], set[str]]:
    direct_requirements = [line.strip() for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    package_blocks = []
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith((" ", "#")):
            package_blocks.append([line])
        elif package_blocks:
            package_blocks[-1].append(line)
    assert package_blocks
    assert all(re.search(r"--hash=sha256:[0-9a-f]{64}(?:\s|\\|$)", "\n".join(block)) for block in package_blocks)
    locked_requirements = {block[0].split(" \\", 1)[0] for block in package_blocks}
    assert set(direct_requirements) <= locked_requirements
    return direct_requirements, {requirement.split("==", 1)[0].lower() for requirement in locked_requirements}


def test_protected_policy_sources_are_tracked():
    policy = json.loads((ROOT / "tools/quality/architecture-policy.json").read_text(encoding="utf-8"))
    protected_sources = sorted({path for lane in policy["lanes"] for path in lane.get("protected_sources", [])})
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--", *protected_sources],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    tracked_sources = {path for path in completed.stdout.split("\0") if path}
    assert tracked_sources == set(protected_sources)

    requirements_input = ROOT / "services/asr-online-service/architecture-contract-requirements.in"
    requirements_lock = ROOT / "services/asr-online-service/architecture-contract-requirements.txt"
    direct_requirements, locked_package_names = _hash_locked_requirements(requirements_input, requirements_lock)
    assert direct_requirements == [
        "fastapi==0.115.14",
        "httpx==0.27.2",
        "pydantic-settings==2.15.0",
        "pytest==8.4.2",
        "pytest-asyncio==1.4.0",
        "python-docx==1.2.0",
        "python-multipart==0.0.9",
    ]

    assert locked_package_names.isdisjoint({"gigaam", "numpy", "tone", "torch", "transformers", "uvicorn", "watchfiles"})

    root_requirements, root_locked_package_names = _hash_locked_requirements(
        ROOT / "tools/quality/architecture-contract-requirements.in",
        ROOT / "tools/quality/architecture-contract-requirements.txt",
    )
    assert root_requirements == [
        "flask==3.1.2",
        "jinja2==3.1.6",
        "json-repair==0.35.0",
        "jsonschema==4.26.0",
        "nltk==3.9.2",
        "pandas==2.3.3",
        "peewee==3.19.0",
        "pydantic==2.12.5",
        "pyjwt==2.8.0",
        "pymysql==1.1.2",
        "pytest==9.0.2",
        "pytest-asyncio==1.3.0",
        "python-docx==1.2.0",
        "pyyaml==6.0.3",
        "quart==0.20.0",
        "quart-auth==0.11.0",
        "requests==2.32.5",
        "xxhash==3.6.0",
    ]
    assert root_locked_package_names.isdisjoint({"elasticsearch", "litellm", "torch", "transformers", "xgboost"})

    helper_path = ROOT / "tools/quality/run_isolated_python.py"
    spec = importlib.util.spec_from_file_location("architecture_isolated_runner", helper_path)
    isolated_runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated_runner)
    with patch.dict(
        os.environ,
        {
            "PATH": "preserved",
            "NODE_OPTIONS": "--require=attacker.js",
            "NODE_PATH": "attacker-modules",
            "NODE_EXTRA_CA_CERTS": "attacker.pem",
            "GITHUB_ENV": "command-file",
            "ARCHITECTURE_EVIDENCE_DIR": "evidence",
            "API_TOKEN": "secret",
        },
        clear=True,
    ):
        child_environment = isolated_runner.sanitized_child_environment()
    assert child_environment == {"PATH": "preserved"}
    assert "env=sanitized_child_environment()" in (ROOT / "tools/quality/inspect_typescript.py").read_text(encoding="utf-8")
    assert "env=sanitized_child_environment()" in (ROOT / "tools/quality/check_runtime_graph_policy.py").read_text(encoding="utf-8")

    node_install_inputs = {"web/.npmrc", "web/package.json", "web/pnpm-lock.yaml"}
    for lane_id in ("python-architecture", "runtime-graph"):
        lane = next(item for item in policy["lanes"] if item["id"] == lane_id)
        assert node_install_inputs <= set(lane["protected_sources"])
        assert node_install_inputs <= set(lane["selectors"]["paths"])

    python_lane = next(item for item in policy["lanes"] if item["id"] == "python-architecture")
    assert python_lane["requires_os_isolation"] is True
    assert python_lane["reported_hashes"]["sandbox_runner_sha256"] == "tools/quality/run_architecture_sandbox.py"
    assert python_lane["reported_hashes"]["sandbox_probe_sha256"] == "tools/quality/probe_architecture_sandbox.py"
    assert python_lane["reported_hashes"]["module_map_sha256"] == "tools/quality/module-map.yaml"
    assert python_lane["reported_hashes"]["upstream_base_sha256"] == "tools/quality/upstream-base.json"
    for lane_id in ("python-architecture", "runtime-graph", "go-build-plan", "policy-fixtures"):
        lane = next(item for item in policy["lanes"] if item["id"] == lane_id)
        assert "tools/quality/run_architecture_sandbox.py" in lane["protected_sources"]
        assert any(path == "tools/quality/run_architecture_sandbox.py" or path == "tools/quality/" for path in lane["selectors"].get("paths", []) + lane["selectors"].get("prefixes", []))


def test_workflow_is_unconditional_report_only_and_fail_closed():
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    _assert_required_workflow_controls(workflow)

    jobs = workflow["jobs"]
    assert list(jobs) == ["architecture-policy-plan", "architecture-policy-analysis", "architecture-policy"]
    assert all("permissions" not in job for job in jobs.values())
    assert [job_id for job_id, job in jobs.items() if job["name"] == "architecture-policy"] == ["architecture-policy"]
    assert jobs["architecture-policy"]["if"] == "always()"
    assert set(jobs["architecture-policy"]["needs"]) == {"architecture-policy-plan", "architecture-policy-analysis"}
    assert all("continue-on-error" not in step for job in jobs.values() for step in job["steps"])
    assert all("|| true" not in step.get("run", "") for job in jobs.values() for step in job["steps"])
    assert {job_id: [step.get("name") for step in job["steps"]] for job_id, job in jobs.items()} == {
        "architecture-policy-plan": [
            "Resolve candidate revision",
            "Check out candidate for static planning",
            "Set up policy interpreter",
            "Create fresh plan directory",
            "Resolve comparison base",
            "Materialize authoritative policy bundle",
            "Select authoritative architecture lanes",
            "Evaluate candidate policy with authoritative checker",
            "Reject candidate policy coverage reduction",
            "Upload architecture plan",
        ],
        "architecture-policy-analysis": [
            "Check out candidate for analysis",
            "Download architecture plan",
            "Create fresh analysis directory",
            "Reject selected lanes with untrusted sources",
            "Set up pnpm",
            "Set up Node.js",
            "Set up Python and uv",
            "Materialize protected producer sources",
            "Prepare isolated candidate workspace",
            "Prepare root Python contracts",
            "Prepare ASR architecture contract environment",
            "Exercise architecture policy fixtures",
            "Prepare locked TypeScript parser",
            "Classify TypeScript and Go runtime graphs",
            "Plan changed Go build profiles",
            "Check configured Python architecture",
            "Upload architecture analysis",
        ],
        "architecture-policy": [
            "Create fresh final evidence directory",
            "Require a completed trusted plan",
            "Require completed analysis",
            "Check out candidate for final static verification",
            "Set up final policy interpreter",
            "Download trusted plan",
            "Download analysis reports",
            "Resolve and verify comparison base",
            "Re-materialize authoritative policy bundle",
            "Recompute authoritative selection",
            "Re-evaluate candidate policy",
            "Recheck candidate policy coverage",
            "Stage downloaded reports beside fresh plan",
            "Aggregate architecture evidence",
            "Upload final architecture evidence",
        ],
    }


def test_required_status_context_is_unique_across_candidate_workflows():
    policy = json.loads((ROOT / "tools/quality/architecture-policy.json").read_text(encoding="utf-8"))
    fixture_lane = next(lane for lane in policy["lanes"] if lane["id"] == "policy-fixtures")
    assert ".github/workflows/" in fixture_lane["selectors"]["prefixes"]

    workflows = _candidate_workflows()
    _assert_unique_required_status_context(workflows)

    duplicate = copy.deepcopy(workflows)
    duplicate[".github/workflows/candidate-spoof.yml"] = {
        "jobs": {"spoof": {"name": "architecture-policy", "runs-on": "ubuntu-latest", "steps": []}}
    }
    with pytest.raises(AssertionError):
        _assert_unique_required_status_context(duplicate)

    dynamic = copy.deepcopy(workflows)
    dynamic[".github/workflows/candidate-spoof.yml"] = {
        "jobs": {"spoof": {"name": "${{ format('architecture-{0}', 'policy') }}", "runs-on": "ubuntu-latest", "steps": []}}
    }
    with pytest.raises(AssertionError):
        _assert_unique_required_status_context(dynamic)


def test_workflow_uses_base_policy_as_authoritative_when_protocol_is_available():
    job = _workflow()["jobs"]["architecture-policy-plan"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 15
    checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["with"]["ref"] == "${{ steps.candidate.outputs.sha }}"
    assert ACTION_REFS["astral-sh/setup-uv"] in {step.get("uses") for step in job["steps"]}

    candidate = _step(job, "Resolve candidate revision")
    assert candidate["id"] == "candidate"
    assert set(candidate["env"]) == {"EVENT_NAME", "EVENT_SHA", "PR_MERGE_SHA", "MERGE_GROUP_HEAD_SHA"}
    assert "pull_request_target)" in candidate["run"]
    assert "merge_group)" in candidate["run"]
    assert "push)" in candidate["run"]
    assert "Candidate revision is not an immutable commit SHA" in candidate["run"]
    assert job["outputs"]["candidate_sha"] == "${{ steps.candidate.outputs.sha }}"

    comparison_step = _step(job, "Resolve comparison base")
    assert set(comparison_step["env"]) == {
        "EVENT_NAME",
        "CANDIDATE_SHA",
        "PR_BASE_SHA",
        "MERGE_GROUP_BASE_SHA",
        "PUSH_BEFORE_SHA",
    }
    assert "pull_request_target)" in comparison_step["run"]
    assert "merge_group)" in comparison_step["run"]
    assert "Candidate checkout does not match the planned revision" in comparison_step["run"]

    source = _step(job, "Materialize authoritative policy bundle")["run"]
    assert "git show" in source
    assert "TRUSTED_BASE_PROTOCOL = 1" in source
    assert 'source="base"' in source
    assert 'source="candidate-bootstrap"' in source
    assert "NOT_ENABLED" in source
    selection = _step(job, "Select authoritative architecture lanes")["run"]
    comparison = _step(job, "Reject candidate policy coverage reduction")["run"]
    assert '"${POLICY_RUNNER}"' in selection and '"${POLICY_FILE}"' in selection
    assert "--github-output" in selection
    assert "--base-plan" in comparison and "--candidate-plan" in comparison

    commands = _commands(job)
    for forbidden in (
        "uv sync",
        "pnpm ",
        "pytest",
        ".venv/bin/python",
        "tools/quality/check_architecture.py",
        "tools/quality/inspect_typescript.py",
        "tools/quality/inspect_go.py",
        "tools/quality/check_runtime_graph_policy.py",
        "tools/quality/check_go_build_profiles.py",
    ):
        assert forbidden not in commands
    upload = _step(job, "Upload architecture plan")
    assert upload["if"] == "always() && steps.evidence.outcome == 'success'"
    assert upload["with"]["name"].startswith("architecture-policy-plan-")
    assert upload["with"]["include-hidden-files"] is True


def test_workflow_rejects_enforcement_downgrades():
    def use_candidate_controlled_trigger(workflow: dict) -> None:
        workflow[True]["pull_request"] = workflow[True].pop("pull_request_target")

    def omit_merge_queue_trigger(workflow: dict) -> None:
        workflow[True].pop("merge_group")

    def restore_manual_candidate_dispatch(workflow: dict) -> None:
        workflow[True]["workflow_dispatch"] = None

    def cancel_running_required_check(workflow: dict) -> None:
        workflow["concurrency"]["cancel-in-progress"] = True

    def use_mutable_action_tag(workflow: dict) -> None:
        _step(workflow["jobs"]["architecture-policy-plan"], "Check out candidate for static planning")["uses"] = "actions/checkout@v6"

    def persist_checkout_credentials(workflow: dict) -> None:
        _step(workflow["jobs"]["architecture-policy-analysis"], "Check out candidate for analysis")["with"]["persist-credentials"] = True

    def use_unbound_event_sha(workflow: dict) -> None:
        _step(workflow["jobs"]["architecture-policy"], "Check out candidate for final static verification")["with"]["ref"] = "${{ github.sha }}"

    mutations = {
        "candidate-controlled pull_request trigger": use_candidate_controlled_trigger,
        "missing merge_group trigger": omit_merge_queue_trigger,
        "candidate workflow_dispatch": restore_manual_candidate_dispatch,
        "cancel-in-progress": cancel_running_required_check,
        "mutable action tag": use_mutable_action_tag,
        "persisted checkout credentials": persist_checkout_credentials,
        "unbound final event SHA": use_unbound_event_sha,
    }
    for _name, mutate in mutations.items():
        candidate = copy.deepcopy(_workflow())
        mutate(candidate)
        with pytest.raises(AssertionError):
            _assert_required_workflow_controls(candidate)


def test_analysis_job_contains_candidate_lifecycle_and_publishes_reports():
    job = _workflow()["jobs"]["architecture-policy-analysis"]
    assert job["needs"] == "architecture-policy-plan"
    assert job["if"] == "needs.architecture-policy-plan.result == 'success'"
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 45
    uses = {step.get("uses") for step in job["steps"]}
    assert {
        ACTION_REFS["actions/checkout"],
        ACTION_REFS["actions/download-artifact"],
        ACTION_REFS["pnpm/action-setup"],
        ACTION_REFS["actions/setup-node"],
        ACTION_REFS["astral-sh/setup-uv"],
        ACTION_REFS["actions/upload-artifact"],
    }.issubset(uses)

    checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {
        "fetch-depth": 0,
        "persist-credentials": False,
        "ref": "${{ needs.architecture-policy-plan.outputs.candidate_sha }}",
    }
    evidence = _step(job, "Create fresh analysis directory")
    assert evidence["env"] == {"CANDIDATE_SHA": "${{ needs.architecture-policy-plan.outputs.candidate_sha }}"}
    assert "Analysis checkout does not match the planned revision" in evidence["run"]

    commands = _commands(job)
    source_gate = _step(job, "Reject selected lanes with untrusted sources")
    assert set(source_gate["env"]) == {
        "PYTHON_SELECTED",
        "PYTHON_RUNNABLE",
        "RUNTIME_SELECTED",
        "RUNTIME_RUNNABLE",
        "GO_SELECTED",
        "GO_RUNNABLE",
        "FIXTURES_SELECTED",
        "FIXTURES_RUNNABLE",
    }
    assert 'if [[ "${selected}" == "true" && "${runnable}" != "true" ]]' in source_gate["run"]
    assert "Selected architecture lane has no trusted source bundle" in source_gate["run"]
    sandbox = _step(job, "Prepare isolated candidate workspace")
    assert sandbox["id"] == "sandbox"
    assert sandbox["env"] == {
        "EVIDENCE_DIR": "${{ steps.evidence.outputs.directory }}",
        "CANDIDATE_SHA": "${{ needs.architecture-policy-plan.outputs.candidate_sha }}",
    }
    assert 'run_architecture_sandbox.py" prepare' in sandbox["run"]
    assert '--source-root "${GITHUB_WORKSPACE}"' in sandbox["run"]
    assert '--candidate-root "${candidate_root}"' in sandbox["run"]
    assert '--manifest "${EVIDENCE_DIR}/sandbox-manifest.json"' in sandbox["run"]
    assert '--github-output "${GITHUB_OUTPUT}"' in sandbox["run"]
    root_contracts = _step(job, "Prepare root Python contracts")
    assert root_contracts["env"] == {
        "EVIDENCE_DIR": "${{ steps.evidence.outputs.directory }}",
        "CANDIDATE_ROOT": "${{ steps.sandbox.outputs.candidate_root }}",
    }
    assert 'trusted_requirements="${EVIDENCE_DIR}/protected-sources/tools/quality/architecture-contract-requirements.txt"' in root_contracts["run"]
    assert '[[ -e "${CANDIDATE_ROOT}/.venv" || -L "${CANDIDATE_ROOT}/.venv" ]]' in root_contracts["run"]
    assert 'uv venv "${CANDIDATE_ROOT}/.venv" --python 3.13' in root_contracts["run"]
    assert 'uv pip sync --python "${CANDIDATE_ROOT}/.venv/bin/python" --require-hashes --strict "${trusted_requirements}"' in root_contracts["run"]
    assert "uv sync --python 3.13 --group test --frozen --no-install-project" not in commands
    assert '[[ -e "${asr_environment}" || -L "${asr_environment}" ]]' in commands
    assert 'uv venv "${asr_environment}" --python 3.13' in commands
    asr_contracts = _step(job, "Prepare ASR architecture contract environment")
    assert 'trusted_requirements="${EVIDENCE_DIR}/protected-sources/services/asr-online-service/architecture-contract-requirements.txt"' in asr_contracts["run"]
    assert 'uv pip sync --python "${CANDIDATE_ROOT}/services/asr-online-service/.architecture-venv/bin/python" --require-hashes --strict "${trusted_requirements}"' in asr_contracts["run"]
    assert "uv sync --project services/asr-online-service" not in commands
    assert "pnpm --dir web install" not in commands
    parser_step = _step(job, "Prepare locked TypeScript parser")
    assert 'trusted_web="${EVIDENCE_DIR}/protected-sources/web"' in parser_step["run"]
    assert 'install_web="${RUNNER_TEMP}/architecture-typescript-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in parser_step["run"]
    assert 'candidate_modules="${CANDIDATE_ROOT}/web/node_modules"' in parser_step["run"]
    assert '-L "${candidate_modules}"' in parser_step["run"]
    assert "--frozen-lockfile --ignore-scripts --ignore-pnpmfile --ignore-workspace" in parser_step["run"]
    assert 'cp "${trusted_web}/package.json" "${trusted_web}/pnpm-lock.yaml" "${trusted_web}/.npmrc" "${install_web}/"' in parser_step["run"]
    assert "Refusing stale TypeScript install or pre-existing candidate node_modules" in parser_step["run"]
    assert 'cp -RL "${install_web}/node_modules/typescript" "${candidate_modules}/typescript"' in parser_step["run"]
    assert 'test -f "${candidate_modules}/typescript/lib/typescript.js"' in parser_step["run"]
    assert "tools/quality/run_architecture_sandbox.py" in commands
    assert "tools/quality/check_runtime_graph_policy.py" in commands
    assert "tools/quality/check_go_build_profiles.py" in commands
    materialize = _step(job, "Materialize protected producer sources")["run"]
    assert '"${POLICY_RUNNER}"' in materialize and " materialize " in materialize.replace("\n", " ")
    assert '--output-directory "${EVIDENCE_DIR}/protected-sources"' in materialize
    assert commands.count('python -I -B "${isolated_runner}"') == 4
    assert commands.count('"${trusted_root}" tools/quality/') == 4
    assert commands.count('--root "${CANDIDATE_ROOT}"') >= 5
    assert commands.count('--root "${GITHUB_WORKSPACE}"') == 1
    python_check = _step(job, "Check configured Python architecture")["run"]
    assert 'run_architecture_sandbox.py" run' in python_check
    assert '--candidate-root "${CANDIDATE_ROOT}"' in python_check
    assert '--trusted-root "${trusted_root}"' in python_check
    assert '--evidence-dir "${EVIDENCE_DIR}"' in python_check
    assert '--comparison-base "${COMPARISON_BASE}"' in python_check
    assert "tools/quality/check_architecture.py --root" not in python_check
    assert " fixtures " in commands.replace("\n", " ")
    assert "--junit-output" in commands
    assert commands.count("--selection-sha256") == 3
    step_names = [step.get("name") for step in job["steps"]]
    fixture_index = step_names.index("Exercise architecture policy fixtures")
    sandbox_index = step_names.index("Prepare isolated candidate workspace")
    assert sandbox_index < step_names.index("Prepare root Python contracts")
    for name in ("Prepare locked TypeScript parser", "Classify TypeScript and Go runtime graphs", "Plan changed Go build profiles", "Check configured Python architecture"):
        assert fixture_index < step_names.index(name)
    assert _step(job, "Exercise architecture policy fixtures")["if"] == "${{ success() && needs.architecture-policy-plan.outputs.policy_fixtures == 'true' }}"
    assert _step(job, "Classify TypeScript and Go runtime graphs")["if"] == "${{ success() && needs.architecture-policy-plan.outputs.runtime_graph == 'true' }}"
    assert _step(job, "Plan changed Go build profiles")["if"] == "${{ success() && needs.architecture-policy-plan.outputs.go_build_plan == 'true' }}"
    assert _step(job, "Check configured Python architecture")["if"] == "${{ success() && needs.architecture-policy-plan.outputs.python_architecture == 'true' }}"
    for name in ("Set up pnpm", "Set up Node.js", "Prepare locked TypeScript parser"):
        assert "policy_fixtures" not in _step(job, name)["if"]
    upload = _step(job, "Upload architecture analysis")
    assert upload["if"] == "always() && steps.evidence.outcome == 'success'"
    assert upload["with"]["name"].startswith("architecture-policy-analysis-")
    assert upload["with"]["include-hidden-files"] is True


def test_final_aggregate_runs_on_fresh_trusted_job():
    job = _workflow()["jobs"]["architecture-policy"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 15
    assert _step(job, "Create fresh final evidence directory")
    analysis_gate = _step(job, "Require completed analysis")
    assert analysis_gate["env"]["ANALYSIS_RESULT"] == "${{ needs.architecture-policy-analysis.result }}"
    assert 'if [[ "${ANALYSIS_RESULT}" != "success" ]]' in analysis_gate["run"]
    checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["with"]["ref"] == "${{ needs.architecture-policy-plan.outputs.candidate_sha }}"

    comparison_step = _step(job, "Resolve and verify comparison base")
    assert set(comparison_step["env"]) == {
        "EVENT_NAME",
        "CANDIDATE_SHA",
        "PR_BASE_SHA",
        "MERGE_GROUP_BASE_SHA",
        "PUSH_BEFORE_SHA",
        "PLANNED_BASE",
    }
    assert "pull_request_target)" in comparison_step["run"]
    assert "merge_group)" in comparison_step["run"]
    assert "Final checkout does not match the planned revision" in comparison_step["run"]

    source = _step(job, "Re-materialize authoritative policy bundle")["run"]
    assert "git show" in source
    assert "TRUSTED_BASE_PROTOCOL = 1" in source
    assert 'source="candidate-bootstrap"' in source
    assert "NOT_ENABLED" in source
    assert "PLANNED_SOURCE" in source
    recompute = _step(job, "Recompute authoritative selection")["run"]
    assert '"${POLICY_RUNNER}"' in recompute and '"${POLICY_FILE}"' in recompute
    assert "cmp" in recompute and "architecture-policy-plan-final-input/selection.json" in recompute
    comparison = _step(job, "Recheck candidate policy coverage")["run"]
    assert "--base-plan" in comparison and "--candidate-plan" in comparison
    assert "cmp" in comparison and "architecture-policy-plan-final-input/policy-compatibility.json" in comparison
    aggregate = _step(job, "Aggregate architecture evidence")["run"]
    assert '"${POLICY_RUNNER}"' in aggregate and '"${POLICY_FILE}"' in aggregate
    assert " aggregate " in aggregate.replace("\n", " ")
    assert '--candidate-plan "${EVIDENCE_DIR}/candidate-policy-selection.json"' in aggregate
    assert '--compatibility "${EVIDENCE_DIR}/policy-compatibility.json"' in aggregate

    commands = _commands(job)
    for forbidden in (
        "uv sync",
        "pnpm ",
        "pytest",
        ".venv/bin/python",
        "tools/quality/check_architecture.py",
        "tools/quality/inspect_typescript.py",
        "tools/quality/inspect_go.py",
        "tools/quality/check_runtime_graph_policy.py",
        "tools/quality/check_go_build_profiles.py",
    ):
        assert forbidden not in commands


def test_artifacts_flow_from_plan_and_analysis_to_fresh_final_aggregate():
    jobs = _workflow()["jobs"]
    plan = jobs["architecture-policy-plan"]
    analysis = jobs["architecture-policy-analysis"]
    final = jobs["architecture-policy"]
    plan_name = _step(plan, "Upload architecture plan")["with"]["name"]
    analysis_plan_name = _step(analysis, "Download architecture plan")["with"]["name"]
    final_plan_name = _step(final, "Download trusted plan")["with"]["name"]
    analysis_name = _step(analysis, "Upload architecture analysis")["with"]["name"]
    final_analysis_name = _step(final, "Download analysis reports")["with"]["name"]
    assert plan_name == analysis_plan_name == final_plan_name
    assert analysis_name == final_analysis_name

    staging = _step(final, "Stage downloaded reports beside fresh plan")["run"]
    for report in ("architecture-python.json", "runtime-graph.json", "go-build-plan.json", "policy-fixtures.json"):
        assert report in staging
    assert "policy-compatibility.json" in _step(final, "Recheck candidate policy coverage")["run"]
    assert "--candidate-plan" in _step(final, "Aggregate architecture evidence")["run"]
    assert "--compatibility" in _step(final, "Aggregate architecture evidence")["run"]
    upload = _step(final, "Upload final architecture evidence")
    assert upload["if"] == "always() && steps.evidence.outcome == 'success'"
    assert upload["with"]["name"].startswith("architecture-policy-${{ github.run_id }}-")
    assert upload["with"]["include-hidden-files"] is True
