"""Exercise the separated CI lane selector against complete Git events."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[4] / "deployment/runner/select-ci-lanes.sh"


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

    def test_push_includes_every_commit_in_event(self):
        self.commit("api/example.py", "value = 1")
        self.commit("web/example.ts", "export const value = 1")
        self.assertEqual(
            self.select(),
            {"has_go_changes": "false", "has_python_changes": "true", "has_web_changes": "true"},
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
            {"has_go_changes": "true", "has_python_changes": "false", "has_web_changes": "false"},
        )


if __name__ == "__main__":
    unittest.main()
