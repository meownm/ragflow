"""Compare Docker rebuilds in two disposable source trees on the same builder.

No cache pruning, registry publication or deployment. Logs and timings are saved
after every build, including failures. Source probes are always restored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.quality.artifact_lifecycle import docker_args, managed, note, note_all


def run(before: Path, after: Path, output: Path, prefix: str, scenarios: tuple[str, ...] = ("unchanged", "python", "frontend", "version")) -> dict:
    roots = {"before": before.resolve(), "after": after.resolve()}
    output = output.resolve()
    if roots["before"].is_relative_to(roots["after"]) or roots["after"].is_relative_to(roots["before"]) or any(output.is_relative_to(root) or (root / ".git").exists() for root in roots.values()):
        raise ValueError("Use separate disposable source trees and an external evidence directory")
    note(output)
    output.mkdir(parents=True, exist_ok=True)
    failure = None
    try:
        report = {
            "schema": 1,
            "status": "INCOMPLETE",
            "cache": "existing builder cache; no pruning",
            "order": "each variant: initial then selected probes; avoids competing cache working sets",
            "scenarios": list(scenarios),
            "docker_version": subprocess.check_output(["docker", "version", "--format", "{{.Server.Version}}"], text=True).strip(),
            "inputs": {label: {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ("Dockerfile", "pyproject.toml", "uv.lock")} for label, root in roots.items()},
            "native_artifact_bytes": {label: {path.name: path.stat().st_size for path in (root / "bin").glob("*") if path.is_file()} for label, root in roots.items()},
            "builds": [],
        }

        def save() -> None:
            (output / "measurements.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

        def build(label: str, root: Path, scenario: str) -> None:
            version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
            image = f"{prefix}:{label}-{scenario}"
            command = ["docker", "build", "--progress", "plain", "--build-arg", f"RAGFLOW_BUILD_VERSION={version}-benchmark", "--build-arg", "RAGFLOW_SOURCE_REVISION=", "-t", image, str(root)]
            log = output / f"{label}-{scenario}.log"
            print(f"BUILD {label} {scenario}", flush=True)
            started = time.monotonic()
            with log.open("w", encoding="utf-8") as stream:
                result = subprocess.run(docker_args(command), stdout=stream, stderr=subprocess.STDOUT, check=False)
            row = {"variant": label, "scenario": scenario, "seconds": round(time.monotonic() - started, 3), "exit_code": result.returncode, "image": image, "command": command, "log": log.name}
            text = log.read_text(encoding="utf-8")
            row["run_steps"] = [{"step": match[2], "cached": re.search(r"(?m)^#" + match[1] + r" CACHED$", text) is not None} for match in re.finditer(r"(?m)^#(\d+) (\[[^\]]+\] RUN[^\n]*)", text)]
            if result.returncode == 0:
                metadata = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
                row.update(image_id=metadata["Id"], docker_size_bytes=metadata["Size"], layers=len(metadata["RootFS"]["Layers"]))
            report["builds"].append(row)
            save()
            if result.returncode:
                raise RuntimeError(f"Build failed; see {log}")
            print(json.dumps(row), flush=True)

        save()
        for label, root in roots.items():
            build(label, root, "initial")
            for scenario in scenarios:
                originals = {}
                try:
                    if scenario == "python":
                        if (root / "api/build_benchmark_probe.py").exists():
                            raise ValueError("Benchmark probe already exists")
                        originals[root / "api/build_benchmark_probe.py"] = None
                        (root / "api/build_benchmark_probe.py").write_text('"""Disposable Docker cache benchmark."""\n')
                    elif scenario == "frontend":
                        path = root / "web/index.html"
                        originals[path] = path.read_bytes()
                        path.write_bytes(originals[path] + b"\n<!-- disposable build benchmark -->\n")
                    elif scenario == "version":
                        path = root / "pyproject.toml"
                        originals[path] = path.read_bytes()
                        project = tomllib.loads(originals[path].decode())
                        old = project["project"]["version"]
                        parts = old.split(".")
                        parts[-1] = str(int(parts[-1]) + 1)
                        new = ".".join(parts)
                        path.write_bytes(originals[path].replace(f'version = "{old}"'.encode(), f'version = "{new}"'.encode(), 1))
                        path = root / "uv.lock"
                        originals[path] = path.read_bytes()
                        text, count = re.subn(
                            r'(name = "' + re.escape(project["project"]["name"]) + r'"\r?\nversion = ")' + re.escape(old) + '"', lambda match: match[1] + new + '"', originals[path].decode()
                        )
                        if count != 1:
                            raise ValueError("Cannot find unique root lock version")
                        path.write_text(text, encoding="utf-8", newline="\n")
                    build(label, root, scenario)
                finally:
                    for path, contents in originals.items():
                        if contents is None:
                            path.unlink(missing_ok=True)
                        else:
                            path.write_bytes(contents)
        report["status"] = "PASS"
        save()
        note(output / "measurements.json", receipt=True)
        note(output)
        return report
    except BaseException as error:
        failure = error
        raise
    finally:
        note_all([(output, "evidence", False)], primary=failure)


@managed("build-benchmark", days=7)
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image-prefix", default="local/ragflow-build-benchmark")
    parser.add_argument("--scenarios", nargs="+", choices=("unchanged", "python", "frontend", "version"), default=("unchanged", "python", "frontend", "version"))
    args = parser.parse_args()
    run(args.before, args.after, args.output, args.image_prefix, tuple(args.scenarios))


if __name__ == "__main__":
    main()
