#!/usr/bin/env python3
"""Select changed Go packages and their in-module consumers for PR checks.

The caller runs this with the same CGO environment as ``build.sh --test``.
Anything that cannot be classified safely selects the full Go suite.
"""

import json
from pathlib import Path
import subprocess
import sys


FULL_SUITE = ["./..."]


def changes_go_tooling(path: str) -> bool:
    return (path.startswith("ragflow_deps/") and path.endswith(".py")) or path == "tools/quality/select_go_test_packages.py" or path == "test/unit_test/tools/quality/test_go_package_selection.py"


def requires_full_suite(paths: list[str]) -> bool:
    if not paths or not any(path.endswith(".go") for path in paths):
        return True
    return any(
        changes_go_tooling(path)
        or not (path.endswith(".go") or path.endswith(".py") or path.startswith("web/") or path == "README.md" or (path.startswith("docs/") and path.endswith((".md", ".mdx"))))
        for path in paths
    )


def changed_paths(base: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", base, "HEAD"],
        check=True,
        capture_output=True,
    )
    return [path.decode("utf-8") for path in result.stdout.split(b"\0") if path]


def go_packages() -> list[dict]:
    result = subprocess.run(["go", "list", "-json", "./..."], check=True, capture_output=True, text=True)
    decoder = json.JSONDecoder()
    packages = []
    data = result.stdout
    offset = 0
    while offset < len(data):
        while offset < len(data) and data[offset].isspace():
            offset += 1
        if offset < len(data):
            package, offset = decoder.raw_decode(data, offset)
            packages.append(package)
    return packages


def select_packages(paths: list[str], packages: list[dict], root: Path) -> list[str]:
    root = root.resolve()
    # A build tool, native input, fixture or other unknown path can affect many
    # packages. Only Go source plus independently tested Python/web paths can
    # use package selection.
    if requires_full_suite(paths):
        return FULL_SUITE

    changed_dirs = {(root / path.rsplit("/", 1)[0]).resolve() if "/" in path else root for path in paths if path.endswith(".go")}
    by_dir = {Path(package["Dir"]).resolve(): package for package in packages}
    if not changed_dirs.issubset(by_dir):
        return FULL_SUITE

    changed_imports = {by_dir[directory]["ImportPath"] for directory in changed_dirs}
    by_import = {package["ImportPath"]: package for package in packages}
    selected = []
    for package in packages:
        imports = set(package.get("Deps", []))
        for test_import in package.get("TestImports", []) + package.get("XTestImports", []):
            imports.add(test_import)
            imports.update(by_import.get(test_import, {}).get("Deps", []))
        if package["ImportPath"] in changed_imports or imports.intersection(changed_imports):
            relative = Path(package["Dir"]).resolve().relative_to(root).as_posix()
            selected.append("." if relative == "." else f"./{relative}")

    return sorted(set(selected)) or FULL_SUITE


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: select_go_test_packages.py BASE_SHA", file=sys.stderr)
        return 2
    root = Path.cwd().resolve()
    paths = changed_paths(sys.argv[1])
    # Avoid running go list when inputs already require the complete suite.
    if requires_full_suite(paths):
        selected = FULL_SUITE
    else:
        selected = select_packages(paths, go_packages(), root)
    print(f"Go test packages: {', '.join(selected)}", file=sys.stderr)
    sys.stdout.buffer.write(b"\0".join(package.encode("utf-8") for package in selected) + b"\0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
