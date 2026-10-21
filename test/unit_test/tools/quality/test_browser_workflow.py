"""Independent browser inventory and image SPA qualification contracts."""

import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

import yaml

from tools.quality.browser_inventory import expected_inventory, verify_junit
from tools.quality.release_runtime import spa_manifest, validate_spa, record_browser, validate_browsers

ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/browser-regression.yml").read_text())


class BrowserWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.spa = self.root / "web/dist"
        self.spa.mkdir(parents=True)
        (self.spa / "index.html").write_text("fixture")
        self.sha = "a" * 40
        self.image = "registry/ragflow@sha256:" + "b" * 64
        self.manifest = self.root / "browser-build.json"
        self.manifest.write_text(json.dumps(spa_manifest(self.spa, self.sha, self.image)))
        self.mandatory, self.inventory_digest = expected_inventory()
        self.digest = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        self.env = {"BROWSER": "chromium"}
        self.results = self.root / "browser-results"
        self.results.mkdir()
        for browser in ("chromium", "firefox", "webkit"):
            (self.results / (browser + ".json")).write_text(
                json.dumps({"browser": browser, "sha": self.sha, "manifest_sha256": self.digest, "result": "success", "tests": len(self.mandatory), "inventory_sha256": self.inventory_digest})
            )

    def execute(self, job, name, success=True):
        try:
            record_browser(self.root / "browser-junit.xml", self.manifest, self.env["BROWSER"], self.sha)
        except (ValueError, OSError, ET.ParseError):
            self.assertFalse(success)
        else:
            self.assertTrue(success)

    def test_spa_source_digest_and_closed_content(self):
        original = json.loads(self.manifest.read_text())
        validate_spa(original, self.spa, self.sha, self.image)
        for sha, image in (("c" * 40, self.image), (self.sha, "other@sha256:" + "b" * 64)):
            with self.assertRaises(ValueError):
                validate_spa(original, self.spa, sha, image)
        (self.spa / "extra.js").write_text("extra")
        with self.assertRaises(ValueError):
            validate_spa(original, self.spa, self.sha, self.image)
        (self.spa / "extra.js").unlink()
        (self.spa / "index.html").write_text("changed")
        with self.assertRaises(ValueError):
            validate_spa(original, self.spa, self.sha, self.image)

    def test_missing_extra_duplicate_skipped_or_foreign_browser_receipts(self):
        validate_browsers(self.results, self.manifest, self.sha)
        path = self.results / "webkit.json"
        original = json.loads(path.read_text())
        for key, value in (("browser", "chromium"), ("sha", "f" * 40), ("result", "skipped"), ("tests", 0), ("tests", True), ("inventory_sha256", "c" * 64), ("manifest_sha256", "d" * 64)):
            path.write_text(json.dumps({**original, key: value}))
            with self.assertRaises(ValueError):
                validate_browsers(self.results, self.manifest, self.sha)
        path.unlink()
        with self.assertRaises(ValueError):
            validate_browsers(self.results, self.manifest, self.sha)

    def test_workflow_consumes_one_image_spa_and_locked_axe(self):
        jobs = WORKFLOW["jobs"]
        assert jobs["browser"]["strategy"]["matrix"]["browser"] == ["chromium", "firefox", "webkit"]
        assert jobs["browser"]["strategy"]["fail-fast"] is False
        assert jobs["browser-gate"]["if"] == "always()"
        assert jobs["browser-gate"]["needs"] == ["browser-input", "browser"]
        scripts = "\n".join(step.get("run", "") for job in jobs.values() for step in job["steps"])
        assert "pnpm build" not in scripts and "npm run build" not in scripts
        assert "verify-spa" in scripts and "verify-browsers" in scripts
        assert "sha256sum browser-tools/axe.min.js" in scripts
        assert "--require-hashes" in scripts
        assert "uv pip install --no-config --require-hashes" in scripts

    def test_expected_inventory_matches_configured_suites(self):
        parsed = ast.parse((ROOT / "test/run_browser_regression.py").read_text())
        suites = next(ast.literal_eval(node.value) for node in parsed.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "SUITES" for target in node.targets))
        self.assertEqual({node.split("::", 1)[0] for node in self.mandatory}, set(suites))

    def junit(self, nodes=None):
        nodes = sorted(self.mandatory) if nodes is None else nodes
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite", tests=str(len(nodes)), failures="0", errors="0", skipped="0")
        for node in nodes:
            path, name = node.split("::", 1)
            ET.SubElement(suite, "testcase", classname=path.removesuffix(".py").replace("/", "."), name=name)
        return ET.ElementTree(root)

    def test_receipt_rejects_reduced_extra_duplicate_skipped_error_and_wrong_identity(self):
        self.env["BROWSER"] = "chromium"
        for change in ("one", "missing", "extra", "duplicate", "wrong", "skipped", "failure", "error", "suite-error", "root-error", "wrong-count"):
            with self.subTest(change=change):
                tree = self.junit()
                suite = tree.getroot().find("testsuite")
                case = suite.find("testcase")
                if change == "one":
                    tree = self.junit([sorted(self.mandatory)[0]])
                elif change == "missing":
                    suite.remove(case)
                    suite.set("tests", str(len(self.mandatory) - 1))
                elif change in {"extra", "duplicate"}:
                    clone = ET.fromstring(ET.tostring(case))
                    if change == "extra":
                        clone.set("name", "test_unexpected")
                    suite.append(clone)
                    suite.set("tests", str(len(self.mandatory) + 1))
                elif change == "wrong":
                    case.set("name", "test_renamed_without_review")
                elif change == "suite-error":
                    suite.set("errors", "1")
                elif change == "root-error":
                    tree.getroot().set("failures", "1")
                elif change == "wrong-count":
                    suite.set("tests", "999")
                else:
                    ET.SubElement(case, change)
                tree.write(self.root / "browser-junit.xml")
                self.execute("browser", "Record successful browser", False)

    def test_actual_helper_accepts_all_cases_and_rejects_reduced_report(self):
        report = self.root / "report.xml"
        self.junit().write(report)
        self.assertEqual(verify_junit(report), {"tests": len(self.mandatory), "inventory_sha256": self.inventory_digest})
        self.junit([sorted(self.mandatory)[0]]).write(report)
        with self.assertRaisesRegex(ValueError, "inventory mismatch"):
            verify_junit(report)

    def test_invalid_inventory_schema_count_and_duplicates_fail(self):
        from tools.quality.browser_inventory import INVENTORY

        original = json.loads(INVENTORY.read_bytes())
        path = self.root / "inventory.json"
        for key, value in (("schema", 2), ("schema", True), ("expected_count", 1), ("nodeids", []), ("nodeids", sorted(self.mandatory)[:-1] + [sorted(self.mandatory)[0]])):
            with self.subTest(key=key):
                path.write_text(json.dumps({**original, key: value}))
                with self.assertRaises(ValueError):
                    expected_inventory(path)
