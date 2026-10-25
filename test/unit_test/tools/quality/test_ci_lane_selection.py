"""Exercise the separated CI lane selector against complete Git events."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


SCRIPT = Path(__file__).resolve().parents[4] / "deployment/runner/select-ci-lanes.sh"
WORKFLOW = yaml.safe_load((SCRIPT.parents[2] / ".github/workflows/pr-smoke.yml").read_text(encoding="utf-8"))


class SmokeWorkflowContractTest(unittest.TestCase):
    def test_formatting_and_go_regression_use_separate_flags(self):
        jobs = WORKFLOW["jobs"]
        self.assertEqual(jobs["changed-paths"]["outputs"]["has_go_format_changes"], "${{ steps.select.outputs.has_go_format_changes }}")
        self.assertEqual(jobs["go-format"]["if"], "${{ needs.changed-paths.outputs.has_go_format_changes == 'true' }}")
        qualification = yaml.safe_load((SCRIPT.parents[2] / ".github/workflows/tests.yml").read_text(encoding="utf-8"))
        self.assertEqual(qualification["jobs"]["ragflow_preflight"]["outputs"]["has_go_changes"], "${{ steps.detect_changes.outputs.has_go_changes }}")
        regression = next(step for step in qualification["jobs"]["build_image"]["steps"] if step.get("name") == "Go regression")
        self.assertEqual(regression["if"], "${{ inputs.profile == 'release' || needs.ragflow_preflight.outputs.has_go_changes == 'true' }}")

    def test_python_smoke_includes_selector_regression(self):
        steps = WORKFLOW["jobs"]["python-smoke"]["steps"]
        self.assertTrue(any("test/unit_test/tools/quality/test_ci_lane_selection.py" in step.get("run", "") for step in steps))

    def test_push_and_pr_pass_their_complete_base_to_selection(self):
        self.assertEqual(WORKFLOW["on"]["push"]["branches"], ["main"])
        self.assertIn("pull_request", WORKFLOW["on"])
        steps = WORKFLOW["jobs"]["changed-paths"]["steps"]
        resolver = next(step for step in steps if step.get("id") == "base")
        self.assertIn("github.event.before", resolver["env"]["BASE_SHA"])
        self.assertIn("github.event.pull_request.base.sha", resolver["env"]["BASE_SHA"])
        selector = next(step for step in steps if step.get("id") == "select")
        self.assertEqual(selector["env"]["CI_BEFORE"], "${{ github.event.before }}")
        self.assertEqual(selector["env"]["CI_PR_BASE"], "${{ github.event.pull_request.base.sha }}")

    def test_all_diff_consumers_use_resolved_base_and_events_do_not_cancel_each_other(self):
        jobs = WORKFLOW["jobs"]
        self.assertEqual(jobs["changed-paths"]["outputs"]["base_sha"], "${{ steps.base.outputs.sha }}")
        for name, job in jobs.items():
            for step in job["steps"]:
                if '"$BASE_SHA"' in step.get("run", "") or 'os.environ["BASE_SHA"]' in step.get("run", ""):
                    if step.get("id") == "base":
                        continue
                    expected = "${{ steps.base.outputs.sha }}" if name == "changed-paths" else "${{ needs.changed-paths.outputs.base_sha }}"
                    self.assertEqual(step["env"]["BASE_SHA"], expected)
        self.assertIn("github.event_name", WORKFLOW["concurrency"]["group"])
        self.assertIn("github.ref", WORKFLOW["concurrency"]["group"])


@unittest.skipIf(sys.platform == "win32", "The CI lane selector runs in Bash on Linux")
class CiLaneSelectionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        self.git("init", "-q")
        self.git("config", "user.name", "CI Test")
        self.git("config", "user.email", "ci@example.invalid")
        self.commit("README.md", "initial")
        self.before = self.git("rev-parse", "HEAD").stdout.strip()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, text=True, capture_output=True)

    def commit(self, name, contents):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-qm", name)

    def select(self, event="push", ref="refs/heads/main", before=None, pr_base=""):
        output = self.repo / "outputs.txt"
        output.write_text("", encoding="utf-8")
        env = {
            **os.environ,
            "GITHUB_EVENT_NAME": event,
            "GITHUB_REF": ref,
            "GITHUB_OUTPUT": str(output),
            "CI_BEFORE": before if before is not None else self.before,
            "CI_PR_BASE": pr_base,
        }
        subprocess.run(["bash", str(SCRIPT)], cwd=self.repo, env=env, check=True)
        return dict(line.split("=", 1) for line in output.read_text().splitlines())

    def resolve_base(self, base):
        output = self.repo / "base.txt"
        output.write_text("", encoding="utf-8")
        resolver = next(step for step in WORKFLOW["jobs"]["changed-paths"]["steps"] if step.get("id") == "base")
        subprocess.run(
            ["bash", "-c", resolver["run"]],
            cwd=self.repo,
            env={**os.environ, "BASE_SHA": base, "GITHUB_OUTPUT": str(output)},
            check=True,
        )
        return dict(line.split("=", 1) for line in output.read_text().splitlines())["sha"]

    def test_workflow_diff_includes_all_pushed_commits(self):
        self.commit("api/example.py", "value = 1")
        self.commit("web/example.ts", "export const value = 1")
        base = self.resolve_base(self.before)
        self.assertEqual(set(self.git("diff", "--name-only", base, "HEAD").stdout.splitlines()), {"api/example.py", "web/example.ts"})

    def test_workflow_missing_base_checks_entire_tree_instead_of_skipping(self):
        self.commit("api/example.py", "value = 1")
        for value in ("", "0" * 40, "f" * 40, "invalid"):
            with self.subTest(base=value):
                base = self.resolve_base(value)
                self.assertEqual(set(self.git("diff", "--name-only", base, "HEAD").stdout.splitlines()), {"README.md", "api/example.py"})
                self.assertEqual(set(self.select(before=value).values()), {"true"})

    def test_push_includes_every_commit_in_event(self):
        self.commit("api/example.py", "value = 1")
        self.commit("web/example.ts", "export const value = 1")
        self.assertEqual(
            self.select(),
            {"has_go_changes": "false", "has_go_format_changes": "false", "has_python_changes": "true", "has_web_changes": "true"},
        )

    def test_python_and_documentation_do_not_select_go(self):
        self.commit("api/example.py", "value = 1")
        self.commit("docs/develop/guide.md", "guide")
        self.assertEqual(
            self.select(),
            {"has_go_changes": "false", "has_go_format_changes": "false", "has_python_changes": "true", "has_web_changes": "false"},
        )

    def test_documentation_only_push_skips_source_lanes(self):
        self.commit("docs/develop/guide.md", "guide")
        self.assertEqual(
            self.select(),
            {"has_go_changes": "false", "has_go_format_changes": "false", "has_python_changes": "false", "has_web_changes": "false"},
        )

    def test_native_cache_tooling_with_runner_documentation_only_selects_python(self):
        self.commit("deployment/runner/native_artifacts.py", "# native cache change")
        self.commit("test/unit_test/deployment/test_build_artifacts.py", "# cache tests")
        self.commit("deployment/runner/README.md", "# Native cache")
        for event, ref, pr_base in (("push", "refs/heads/main", ""), ("pull_request", "refs/pull/1/merge", self.before)):
            with self.subTest(event=event):
                self.assertEqual(
                    self.select(event=event, ref=ref, pr_base=pr_base),
                    {"has_go_changes": "false", "has_go_format_changes": "false", "has_python_changes": "true", "has_web_changes": "false"},
                )

    def test_deployment_documentation_only_skips_source_lanes(self):
        for name in ("deployment/runner/README.md", "deployment/local/README.md"):
            with self.subTest(path=name):
                self.commit(name, "# Build documentation")
                self.assertEqual(
                    self.select(),
                    {"has_go_changes": "false", "has_go_format_changes": "false", "has_python_changes": "false", "has_web_changes": "false"},
                )

    def test_frontend_readme_still_selects_web(self):
        self.commit("web/README.md", "# Frontend")
        self.assertEqual(
            self.select(),
            {"has_go_changes": "false", "has_go_format_changes": "false", "has_python_changes": "false", "has_web_changes": "true"},
        )

    def test_unknown_deployment_input_with_readme_still_selects_all_lanes(self):
        self.commit("deployment/runner/README.md", "# Runner")
        self.commit("deployment/runner/build-go-linux.sh", "# build command change")
        self.assertEqual(set(self.select().values()), {"true"})

    def test_ci_selector_and_its_tests_only_select_python(self):
        self.commit("deployment/runner/select-ci-lanes.sh", "# selector change")
        for include_tests in (False, True):
            if include_tests:
                self.commit("test/unit_test/tools/quality/test_ci_lane_selection.py", "# selector tests")
            for event, ref, pr_base in (("push", "refs/heads/main", ""), ("pull_request", "refs/pull/1/merge", self.before)):
                with self.subTest(include_tests=include_tests, event=event):
                    self.assertEqual(
                        self.select(event=event, ref=ref, pr_base=pr_base),
                        {"has_go_changes": "false", "has_go_format_changes": "false", "has_python_changes": "true", "has_web_changes": "false"},
                    )
        self.assertEqual(set(self.select(before="0" * 40).values()), {"true"})

    def test_ci_selector_with_unknown_file_still_selects_all_lanes(self):
        self.commit("deployment/runner/select-ci-lanes.sh", "# selector change")
        self.commit("deployment/runner/unknown-policy.conf", "unknown")
        self.assertEqual(set(self.select().values()), {"true"})

    def test_ci_selector_keeps_go_and_web_changes_in_the_same_push(self):
        self.commit("deployment/runner/select-ci-lanes.sh", "# selector change")
        self.commit("internal/example.go", "package example")
        self.commit("web/example.ts", "export const value = 1")
        self.assertEqual(set(self.select().values()), {"true"})

    def test_deleted_python_source_still_selects_python(self):
        self.commit("api/removed.py", "value = 1")
        before = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("rm", "api/removed.py")
        self.git("commit", "-qm", "remove Python source")
        self.assertEqual(
            self.select(before=before),
            {"has_go_changes": "false", "has_go_format_changes": "false", "has_python_changes": "true", "has_web_changes": "false"},
        )

    def test_documentation_embedded_in_web_selects_web(self):
        self.commit("api/example.py", "value = 1")
        self.commit("docs/references/http_api_reference.md", "# API")
        self.assertEqual(
            self.select(),
            {"has_go_changes": "false", "has_go_format_changes": "false", "has_python_changes": "true", "has_web_changes": "true"},
        )

    def test_go_rename_to_python_still_selects_go(self):
        self.commit("internal/example.go", "package example")
        before = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("mv", "internal/example.go", "internal/example.py")
        self.git("commit", "-qm", "rename Go source")
        self.assertEqual(
            self.select(before=before),
            {"has_go_changes": "true", "has_go_format_changes": "false", "has_python_changes": "true", "has_web_changes": "false"},
        )

    def test_native_dependency_python_selects_go(self):
        self.commit("ragflow_deps/prepare_native.py", "raise SystemExit(0)")
        self.assertEqual(
            self.select(),
            {"has_go_changes": "true", "has_go_format_changes": "false", "has_python_changes": "true", "has_web_changes": "false"},
        )

    def test_go_package_selector_python_selects_go(self):
        self.commit("tools/quality/select_go_test_packages.py", "raise SystemExit(0)")
        self.assertEqual(
            self.select(),
            {"has_go_changes": "true", "has_go_format_changes": "false", "has_python_changes": "true", "has_web_changes": "false"},
        )

    def test_go_build_inputs_without_sources_keep_regression_but_skip_formatting(self):
        for name in ("go.mod", "go.sum", "ragflow_deps/prepare_native.py", "tools/quality/select_go_test_packages.py"):
            before = self.git("rev-parse", "HEAD").stdout.strip()
            self.commit(name, "build input")
            for event, ref in (("push", "refs/heads/main"), ("pull_request", "refs/pull/1/merge")):
                with self.subTest(path=name, event=event):
                    selected = self.select(event=event, ref=ref, before=before, pr_base=before)
                    self.assertEqual(selected["has_go_changes"], "true")
                    self.assertEqual(selected["has_go_format_changes"], "false")

    def test_existing_go_source_selects_formatting_on_push_and_pr(self):
        self.commit("internal/example.go", "package example")
        for event, ref in (("push", "refs/heads/main"), ("pull_request", "refs/pull/1/merge")):
            with self.subTest(event=event):
                selected = self.select(event=event, ref=ref, pr_base=self.before)
                self.assertEqual(selected["has_go_changes"], "true")
                self.assertEqual(selected["has_go_format_changes"], "true")

    def test_deleted_go_source_keeps_regression_but_skips_formatting(self):
        self.commit("internal/removed.go", "package example")
        before = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("rm", "internal/removed.go")
        self.git("commit", "-qm", "remove Go source")
        for event, ref in (("push", "refs/heads/main"), ("pull_request", "refs/pull/1/merge")):
            with self.subTest(event=event):
                selected = self.select(event=event, ref=ref, before=before, pr_base=before)
                self.assertEqual(selected["has_go_changes"], "true")
                self.assertEqual(selected["has_go_format_changes"], "false")

    def test_go_rename_formats_the_surviving_target(self):
        self.commit("internal/before.go", "package example")
        before = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("mv", "internal/before.go", "internal/after.go")
        self.git("commit", "-qm", "rename Go source")
        selected = self.select(before=before)
        self.assertEqual(selected["has_go_changes"], "true")
        self.assertEqual(selected["has_go_format_changes"], "true")

    def test_eval_corpus_change_selects_python_without_full_matrix(self):
        self.commit("test/evals/source_workbench/cases.json", '{"cases": []}')
        self.commit("agent/business_requirements/golden_model_quality/v1.json", '{"cases": []}')
        self.assertEqual(
            self.select(),
            {"has_go_changes": "false", "has_go_format_changes": "false", "has_python_changes": "true", "has_web_changes": "false"},
        )

    def test_tag_and_unknown_input_run_all_lanes(self):
        self.commit("web/example.ts", "export const value = 1")
        self.assertEqual(set(self.select(ref="refs/tags/v1.2.3").values()), {"true"})
        self.commit("internal/binding/cpp/example.cc", "int value = 1;")
        self.assertEqual(set(self.select().values()), {"true"})

    def test_missing_push_base_and_pr_diff_are_fail_closed(self):
        self.commit("service.go", "package service")
        self.assertEqual(set(self.select(before="0" * 40).values()), {"true"})
        self.assertEqual(
            self.select(event="pull_request", ref="refs/pull/1/merge", pr_base=self.before),
            {"has_go_changes": "true", "has_go_format_changes": "true", "has_python_changes": "false", "has_web_changes": "false"},
        )


if __name__ == "__main__":
    unittest.main()
