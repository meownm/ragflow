"""Separate the virtual project's release version from Docker dependency inputs.

Only build-stage copies are normalized. Runtime manifests retain the exact source
bytes; dependency versions, hashes, sources and resolution markers are unchanged.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import tomllib


def normalize(project_text: str, lock_text: str) -> tuple[str, str]:
    project = tomllib.loads(project_text)
    lock = tomllib.loads(lock_text)
    name = project["project"]["name"]
    roots = [package for package in lock["package"] if package.get("source") == {"virtual": "."}]
    if len(roots) != 1 or roots[0]["name"] != name:
        raise ValueError("Expected one virtual root project in uv.lock")
    if roots[0]["version"] != project["project"]["version"]:
        raise ValueError("Project and lock root versions differ")

    def version(section: str) -> str:
        result, count = re.subn(r'(?m)^version[ \t]*=[ \t]*"[^"\n]+"[ \t]*$', 'version = "0.0.0"', section)
        if count != 1:
            raise ValueError("Expected exactly one version in the project section")
        return result

    project_result = re.sub(r"(?ms)^\[project\]\n.*?(?=^\[|\Z)", lambda match: version(match[0]), project_text)

    def package(match: re.Match) -> str:
        text = match[0]
        parsed = tomllib.loads(text)["package"][0]
        return version(text) if parsed.get("source") == {"virtual": "."} else text

    lock_result = re.sub(r"(?ms)^\[\[package\]\]\n.*?(?=^\[\[package\]\]|\Z)", package, lock_text)
    project["project"]["version"] = "0.0.0"
    roots[0]["version"] = "0.0.0"
    if tomllib.loads(project_result) != project or tomllib.loads(lock_result) != lock:
        raise ValueError("Normalization changed more than the virtual project version")
    return project_result, lock_result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project, lock = normalize(
        (args.root / "pyproject.toml").read_text(encoding="utf-8"),
        (args.root / "uv.lock").read_text(encoding="utf-8"),
    )
    args.output.mkdir(parents=True, exist_ok=True)
    for name, contents in (("pyproject.toml", project), ("uv.lock", lock)):
        (args.output / name).write_text(contents, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
