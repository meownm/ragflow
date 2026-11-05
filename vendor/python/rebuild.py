"""Rebuild dependency source archives from hash-pinned upstream releases."""

import argparse
import difflib
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import urllib.request


def replace(path, old, new):
    before = path.read_text(encoding="utf-8")
    if before.count(old) != 1:
        raise ValueError(f"Expected exactly one patch target in {path}")
    after = before.replace(old, new)
    path.write_bytes(after.encode("utf-8"))
    root = next(parent for parent in path.parents if (parent / "pyproject.toml").is_file())
    name = path.relative_to(root).as_posix()
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="a/" + name, tofile="b/" + name, n=0))


def download_verified(entry):
    with urllib.request.urlopen(entry["url"], timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError(f"Upstream hash mismatch: {entry['url']}")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True, help="New directory inside a reserved disposable task")
    work = parser.parse_args().work_dir.resolve()
    work.mkdir(parents=True, exist_ok=False)
    target = Path(__file__).resolve().parent
    packages = json.loads((target / "upstream.json").read_text(encoding="utf-8"))
    results = []
    for entry in packages:
        original = work / entry["filename"]
        original.write_bytes(download_verified(entry))
        source_dir = work / entry["name"]
        source_dir.mkdir()
        with tarfile.open(original) as archive:
            archive.extractall(source_dir, filter="data")
        source = next(source_dir.iterdir())
        if "license" in entry:
            (source / "LICENSE").write_bytes(download_verified(entry["license"]))
        project = source / "pyproject.toml"
        version = f"{entry['version']}+ragflow.{entry.get('revision', 1)}"
        if entry["name"] == "crawl4ai":
            patch = replace(project, '"unclecode-litellm==1.81.13"', '"litellm==1.103.1"')
            patch += replace(source / "crawl4ai/__version__.py", entry["version"], version)
            patch += replace(project, 'requires = ["setuptools>=64.0.0", "wheel"]', 'requires = ["setuptools==83.0.0", "wheel==0.45.1"]')
        elif entry["name"] == "moodlepy":
            patch = replace(project, 'version = "0.24.1"', f'version = "{version}"')
            patch += replace(project, '\nattrs = "^22.2.0"', '\nattrs = ">=23.2.0,<27"')
            patch += replace(project, 'requires = ["poetry>=0.12"]\nbuild-backend = "poetry.masonry.api"', 'requires = ["poetry-core==2.2.1"]\nbuild-backend = "poetry.core.masonry.api"')
            patch += replace(source / "moodle/version.py", entry["version"], version)
        elif entry["name"] == "agentrun-sdk":
            patch = replace(project, 'version = "0.0.51"', f'version = "{version}"')
            patch += replace(project, '    "agentrun-mem0ai>=0.0.10",\n', "")
            patch += replace(project, "[project.optional-dependencies]\n", '[project.optional-dependencies]\nmemory = ["agentrun-mem0ai>=0.0.10"]\n')
            patch += replace(project, 'requires = ["setuptools>=61.0", "wheel"]', 'requires = ["setuptools==83.0.0", "wheel==0.45.1"]')
            patch += replace(source / "agentrun/__init__.py", '__version__ = "0.0.51"', f'__version__ = "{version}"')
        elif entry["name"] == "scholarly":
            patch = replace(source / "setup.py", "version='1.7.11'", f"version='{version}'")
            patch += replace(project, 'requires = ["setuptools", "wheel"]', 'requires = ["setuptools==83.0.0", "wheel==0.45.1"]')
            patch += replace(source / "scholarly/_scholarly.py", 're.search("cites=[\\d+,]*",', 're.search(r"cites=[\\d+,]*",')
        elif entry["name"] == "infinity-sdk":
            patch = replace(project, 'version = "0.7.0"', f'version = "{version}"')
            patch += replace(project, '    "setuptools>=78.1.1,<81.0.0",\n', "")
            patch += replace(project, '"pyarrow>=21.0.0,<23.0.0"', '"pyarrow>=23.0.1,<24"')
            patch += replace(project, 'requires = ["setuptools>=78.1.1", "wheel"]', 'requires = ["setuptools==83.0.0", "wheel==0.45.1"]')
        elif entry["name"] == "qianfan":
            patch = replace(project, 'version = "0.4.6"', f'version = "{version}"')
            patch += replace(project, 'python = ">=3.7,<4"', 'python = ">=3.10,<4"')
            patch += replace(project, 'diskcache = ">=5.6.3"', 'diskcache-community = "==5.8.3"')
            patch += replace(project, '    "poetry-core"', '    "poetry-core==2.2.1"')
            cache = source / "qianfan/utils/cache/base.py"
            patch += replace(
                cache, "from typing import Any\n\nfrom diskcache import Cache", "from pickle import UnpicklingError\nfrom typing import Any\n\nfrom diskcache_community import Cache, SafeDisk"
            )
            patch += replace(cache, "from qianfan.config import get_config\n", "from qianfan.config import get_config\nfrom qianfan.resources.typing import QfLLMInfo\n")
            patch += replace(cache, "super().__init__(directory=get_config().CACHE_DIR, **kwargs)", "super().__init__(directory=get_config().CACHE_DIR, disk=SafeDisk.with_types(QfLLMInfo), **kwargs)")
            patch += replace(
                cache,
                "        return super().get(**kwargs)",
                '        try:\n            return super().get(**kwargs)\n        except (UnpicklingError, ValueError):\n            return kwargs.get("default")',
            )
        else:
            raise ValueError(f"Unknown dependency source: {entry['name']}")
        (target / "patches").mkdir(exist_ok=True)
        (target / "patches" / (entry["name"] + ".patch")).write_bytes(patch.encode("utf-8"))
        output = work / (entry["name"] + "-dist")
        subprocess.run(["uv", "build", "--sdist", "--no-sources", "--no-config", "--default-index", "https://pypi.org/simple", "--out-dir", str(output), str(source)], check=True)
        built = next(output.glob("*.tar.gz"))
        destination = target / built.name
        with tarfile.open(built) as incoming, destination.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as outgoing:
                    for member in sorted(incoming.getmembers(), key=lambda item: item.name):
                        member.uid = member.gid = member.mtime = 0
                        member.uname = member.gname = ""
                        member.pax_headers = {}
                        outgoing.addfile(member, incoming.extractfile(member) if member.isfile() else None)
        results.append({"name": entry["name"], "version": version, "file": destination.name, "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "upstream_sha256": entry["sha256"]})
    (target / "artifacts.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
