"""Release applicability is reviewed policy, never inferred from a skipped case."""

import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools.quality.api_case_evidence import CaseEvidencePlugin, ENGINES, SUITES, expected_cases, verify_report


class APICaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.report = Path(self.directory.name) / "cases.json"
        self.inventory = Path(self.directory.name) / "inventory.json"
        self.suite, self.engine = "restful_api", "infinity"
        self.required = "test/testcases/restful_api/test_datasets.py::test_required"
        self.excluded = "test/testcases/restful_api/test_datasets.py::test_pagerank"
        exclusion = {
            "kind": "engine_contract",
            "reason": "Infinity rejects pagerank; rejection test is mandatory",
            "skip_reason": "engine: pagerank unsupported",
            "contract": "dataset API pagerank validation",
        }
        self.policy = {
            "schema": 1,
            "purpose": "mandatory-release-api-cases",
            "model_profile": "local",
            "suites": {suite: {engine: {"required": [f"test/testcases/{suite}/test_contract.py::test_required"], "not_applicable": {}} for engine in ENGINES} for suite in SUITES},
        }
        self.policy["suites"][self.suite][self.engine] = {"required": [self.required], "not_applicable": {self.excluded: exclusion}}
        self.identity = {"repository": "meownm/ragflow", "sha": "a" * 40, "run_id": "123", "run_attempt": "2", "image_digest": "sha256:" + "b" * 64}
        self.data = {
            "schema": 1,
            "purpose": "api-case-execution",
            "identity": {**self.identity, "suite": self.suite, "engine": self.engine, "model_profile": "local"},
            "exit_status": 0,
            "collection_errors": [],
            "collected": [self.required, self.excluded],
            "cases": [
                {"nodeid": self.required, "phases": [self.phase(name) for name in ("setup", "call", "teardown")]},
                {"nodeid": self.excluded, "phases": [self.phase("setup", "skipped", skip_reason=exclusion["skip_reason"]), self.phase("teardown")]},
            ],
        }

    @staticmethod
    def phase(name, outcome="passed", **fields):
        return {"when": name, "outcome": outcome, "wasxfail": False, **fields}

    def verify(self):
        self.report.write_text(json.dumps(self.data), encoding="utf-8")
        self.inventory.write_text(json.dumps(self.policy), encoding="utf-8")
        return verify_report(self.report, suite=self.suite, engine=self.engine, identity=self.identity, inventory=self.inventory)

    def test_checked_in_inventory_has_all_suites_and_engines(self):
        for suite in SUITES:
            for engine in ENGINES:
                required, exclusions, digest = expected_cases(suite, engine)
                self.assertGreater(len(required), 0)
                self.assertEqual(len(digest), 64)
                self.assertFalse(set(required) & set(exclusions))
                self.assertTrue(all("issues/" not in exclusion["reason"] for exclusion in exclusions.values()))

    def test_only_reviewed_provider_cases_are_skipped_by_local_fixture(self):
        source = Path(__file__).resolve().parents[4] / "test/testcases/conftest.py"
        assignments = {
            target.id: statement.value
            for statement in ast.parse(source.read_text(encoding="utf-8")).body
            if isinstance(statement, ast.Assign)
            for target in statement.targets
            if isinstance(target, ast.Name)
        }
        provider_cases = ast.literal_eval(assignments["_CLOUD_PROVIDER_CASES"])
        skip_reason = ast.literal_eval(assignments["_LOCAL_PROVIDER_SKIP"])
        for engine in ENGINES:
            reviewed = {}
            for suite in SUITES:
                _, exclusions, _ = expected_cases(suite, engine)
                reviewed.update({node: exclusion for node, exclusion in exclusions.items() if exclusion["kind"] == "external_provider"})
            self.assertEqual(set(reviewed), provider_cases)
            self.assertTrue(all(exclusion["skip_reason"] == skip_reason for exclusion in reviewed.values()))

    def test_pass_with_exact_reviewed_capability_skip(self):
        result = self.verify()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["passed"], 1)
        self.assertEqual(result["not_applicable"][0]["nodeid"], self.excluded)

    def test_na_case_can_execute_successfully(self):
        self.data["cases"][1]["phases"] = [self.phase(name) for name in ("setup", "call", "teardown")]
        self.assertEqual(self.verify()["passed"], 2)

    def test_missing_or_extra_case_blocks(self):
        original = copy.deepcopy(self.data)
        for change in ("missing", "extra", "missing_collection", "extra_collection"):
            with self.subTest(change=change):
                self.data = copy.deepcopy(original)
                if change == "missing":
                    self.data["cases"].pop()
                elif change == "extra":
                    self.data["cases"].append({"nodeid": self.required + "_extra", "phases": []})
                elif change == "missing_collection":
                    self.data["collected"].pop()
                else:
                    self.data["collected"].append(self.required + "_extra")
                with self.assertRaisesRegex(ValueError, "mismatch"):
                    self.verify()

    def test_duplicate_case_collection_and_phase_block(self):
        original = copy.deepcopy(self.data)
        for target in ("cases", "collected", "phases"):
            with self.subTest(target=target):
                self.data = copy.deepcopy(original)
                values = self.data[target] if target != "phases" else self.data["cases"][0]["phases"]
                values.append(copy.deepcopy(values[0]))
                with self.assertRaisesRegex(ValueError, "duplicate"):
                    self.verify()

    def test_failed_cancelled_or_collection_error_blocks(self):
        original = copy.deepcopy(self.data)
        for code in (1, 2, 3, 4, 5):
            self.data = copy.deepcopy(original)
            self.data["exit_status"] = code
            with self.assertRaisesRegex(ValueError, "did not succeed"):
                self.verify()
        self.data = original
        self.data["collection_errors"] = ["broken_module.py"]
        with self.assertRaisesRegex(ValueError, "did not succeed"):
            self.verify()

    def test_required_skip_unknown_skip_or_bug_skip_blocks(self):
        original = copy.deepcopy(self.data)
        for index, reason in ((0, "engine: pagerank unsupported"), (1, "issues/5851"), (1, "unknown")):
            with self.subTest(index=index, reason=reason):
                self.data = copy.deepcopy(original)
                self.data["cases"][index]["phases"][0] = self.phase("setup", "skipped", skip_reason=reason)
                with self.assertRaisesRegex(ValueError, "unapproved skip"):
                    self.verify()

    def test_xfail_xpass_failure_and_teardown_error_block(self):
        original = copy.deepcopy(self.data)
        for when, outcome, xfail in (("call", "skipped", True), ("call", "passed", True), ("call", "failed", False), ("setup", "failed", False), ("teardown", "failed", False)):
            self.data = copy.deepcopy(original)
            self.data["cases"][0]["phases"] = [self.phase(name, outcome if name == when else "passed", wasxfail=xfail if name == when else False) for name in ("setup", "call", "teardown")]
            with self.assertRaisesRegex(ValueError, "failed, errored or xfailed"):
                self.verify()

    def test_incomplete_or_teardown_skip_blocks(self):
        self.data["cases"][0]["phases"].pop()
        with self.assertRaisesRegex(ValueError, "incomplete execution"):
            self.verify()
        self.data["cases"][0]["phases"].append(self.phase("teardown"))
        self.data["cases"][1]["phases"] = [self.phase("setup"), self.phase("call"), self.phase("teardown", "skipped", skip_reason="engine: pagerank unsupported")]
        with self.assertRaisesRegex(ValueError, "setup/teardown"):
            self.verify()

    def test_foreign_attempt_sha_digest_engine_suite_profile_blocks(self):
        original = copy.deepcopy(self.data)
        for key in self.data["identity"]:
            with self.subTest(key=key):
                self.data = copy.deepcopy(original)
                self.data["identity"][key] += "-foreign"
                with self.assertRaisesRegex(ValueError, "origin mismatch"):
                    self.verify()

    def test_missing_trusted_identity_blocks(self):
        del self.identity["image_digest"]
        with self.assertRaisesRegex(ValueError, "trusted API identity"):
            self.verify()

    def test_invalid_source_digest_or_attempt_blocks(self):
        original = self.identity.copy()
        for key, value in (("sha", "abc"), ("image_digest", "mutable:tag"), ("run_attempt", "0")):
            self.identity = {**original, key: value}
            with self.assertRaisesRegex(ValueError, "invalid API"):
                self.verify()

    def test_inventory_duplicate_or_unexplained_exclusion_blocks(self):
        lane = self.policy["suites"][self.suite][self.engine]
        lane["required"].append(self.required)
        with self.assertRaisesRegex(ValueError, "duplicate API inventory"):
            self.verify()
        lane["required"].pop()
        del lane["not_applicable"][self.excluded]["contract"]
        with self.assertRaisesRegex(ValueError, "contract"):
            self.verify()

    def test_collector_records_exact_skip_reason_and_phases(self):
        from types import SimpleNamespace

        config = SimpleNamespace(getoption=lambda name: "local")
        plugin = CaseEvidencePlugin(self.report, self.suite, config)
        session = SimpleNamespace(items=[SimpleNamespace(nodeid=self.required)])
        plugin.pytest_collection_finish(session)
        plugin.pytest_runtest_logreport(SimpleNamespace(nodeid=self.required, when="setup", outcome="skipped", skipped=True, longrepr=("path", 1, "Skipped: prerequisite missing")))
        plugin.pytest_runtest_logreport(SimpleNamespace(nodeid=self.required, when="teardown", outcome="passed", skipped=False))
        plugin.pytest_sessionfinish(session, 0)
        recorded = json.loads(self.report.read_text())
        self.assertEqual(recorded["collected"], [self.required])
        self.assertEqual(recorded["cases"][0]["phases"][0]["skip_reason"], "prerequisite missing")
        self.assertEqual(len(recorded["cases"][0]["phases"]), 2)


if __name__ == "__main__":
    unittest.main()
