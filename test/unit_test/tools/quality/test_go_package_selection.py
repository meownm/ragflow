"""The PR Go lane must include consumers and fail closed on uncertain inputs."""

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[4] / "tools/quality/select_go_test_packages.py"
SPEC = importlib.util.spec_from_file_location("select_go_test_packages", SCRIPT)
SELECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SELECTOR)


class GoPackageSelectionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path("/ragflow")
        self.packages = [
            {"Dir": str(self.root / "internal/changed"), "ImportPath": "ragflow/internal/changed"},
            {
                "Dir": str(self.root / "internal/consumer"),
                "ImportPath": "ragflow/internal/consumer",
                "Deps": ["ragflow/internal/changed"],
            },
            {
                "Dir": str(self.root / "internal/test_helper"),
                "ImportPath": "ragflow/internal/test_helper",
                "Deps": ["ragflow/internal/changed"],
            },
            {
                "Dir": str(self.root / "internal/test_consumer"),
                "ImportPath": "ragflow/internal/test_consumer",
                "TestImports": ["ragflow/internal/test_helper"],
            },
            {"Dir": str(self.root / "internal/unrelated"), "ImportPath": "ragflow/internal/unrelated"},
        ]

    def test_go_change_includes_direct_and_test_consumers(self):
        self.assertEqual(
            SELECTOR.select_packages(["internal/changed/code.go", "api/view.py", "docs/develop/guide.md"], self.packages, self.root),
            ["./internal/changed", "./internal/consumer", "./internal/test_consumer", "./internal/test_helper"],
        )

    def test_unknown_inputs_or_removed_package_run_full_suite(self):
        for paths in (
            ["internal/changed/code.go", "go.mod"],
            ["internal/changed/code.go", "internal/binding/cpp/tokenizer.cc"],
            ["internal/changed/code.go", "docs/03_contracts/openapi.yaml"],
            ["internal/changed/code.go", "ragflow_deps/prepare_native.py"],
            ["internal/changed/code.go", "tools/quality/select_go_test_packages.py"],
            ["internal/removed/code.go"],
        ):
            with self.subTest(paths=paths):
                self.assertEqual(SELECTOR.select_packages(paths, self.packages, self.root), ["./..."])


if __name__ == "__main__":
    unittest.main()
