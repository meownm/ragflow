"""Exercise hook startup and argument forwarding through a real POSIX shell."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[4]
BOOTSTRAP = ROOT / "tools/hooks/lefthook.sh"


class LefthookBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.git = shutil.which("git")
        self.shell = shutil.which("sh")
        if not self.shell and self.git:
            candidate = Path(self.git).parents[1] / "bin/sh.exe"
            if candidate.is_file():
                self.shell = str(candidate)
        if not self.git or not self.shell:
            self.skipTest("Git and a POSIX shell are required")
        self.directory = tempfile.TemporaryDirectory(prefix="hooks with spaces-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        subprocess.run([self.git, "init", "--quiet", str(self.root)], check=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ)
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "LEFTHOOK", "LEFTHOOK_BIN", "RAGFLOW_HOOK_PYTHON"):
            self.env.pop(name, None)
        tool_dirs = [self.bin, Path(self.git).parent, Path(self.shell).parent]
        if os.name == "nt":
            tool_dirs.append(Path(self.git).parents[1] / "usr/bin")
        self.env.update(
            PATH=os.pathsep.join(str(p) for p in tool_dirs),
            HOOK_ARGS=str(self.root / "args.txt"),
            HOOK_PATH=str(self.root / "path.txt"),
        )

    def script(self, path, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n" + source, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def prepare(self):
        self.script(self.root / ".venv/bin/python", "exit 0\n")

    def run_hook(self, *args):
        return subprocess.run(
            [self.shell, str(BOOTSTRAP), *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_forwards_message_path_and_selects_prepared_python(self):
        self.prepare()
        self.script(self.bin / "lefthook", 'printf "%s\\n" "$@" > "$HOOK_ARGS"\ncommand -v python > "$HOOK_PATH"\nprintf "%s\\n" "$RAGFLOW_HOOK_PYTHON" >> "$HOOK_PATH"\n')
        args = ["run", "prepare-commit-msg", ".git/message with spaces", "message"]
        result = self.run_hook(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "args.txt").read_text().splitlines(), args)
        self.assertIn("/.venv/bin/python", (self.root / "path.txt").read_text().replace("\\", "/"))
        self.assertEqual((self.root / "path.txt").read_text().splitlines()[-1], "python")

    def test_backend_failure_blocks_hook(self):
        self.prepare()
        self.script(self.bin / "lefthook", "exit 19\n")
        self.assertEqual(self.run_hook("run", "pre-commit").returncode, 19)

    def test_missing_backend_fails_without_package_manager_fallback(self):
        self.prepare()
        self.script(self.bin / "pnpm", 'touch "$HOOK_ARGS"\nexit 0\n')
        result = self.run_hook("run", "pre-commit")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Lefthook is missing", result.stderr)
        self.assertFalse((self.root / "args.txt").exists())

    def test_missing_venv_fails_before_running_backend(self):
        self.script(self.bin / "lefthook", 'touch "$HOOK_ARGS"\nexit 0\n')
        result = self.run_hook("run", "pre-commit")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prepared .venv", result.stderr)
        self.assertFalse((self.root / "args.txt").exists())

    def test_direct_ci_python_defaults_to_python3(self):
        self.script(self.bin / "python3", 'printf "%s\\n" "$@" > "$HOOK_ARGS"\n')
        result = subprocess.run([self.shell, str(ROOT / "tools/hooks/python.sh"), "tool.py", "file with spaces"], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "args.txt").read_text().splitlines(), ["tool.py", "file with spaces"])

    def test_python_uses_selected_interpreter_with_spaces(self):
        python = self.bin / "selected python"
        self.script(python, 'printf "%s\\n" "$@" > "$HOOK_ARGS"\n')
        self.env["RAGFLOW_HOOK_PYTHON"] = str(python)
        result = subprocess.run([self.shell, str(ROOT / "tools/hooks/python.sh"), "tool.py"], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "args.txt").read_text().splitlines(), ["tool.py"])


if __name__ == "__main__":
    unittest.main()
