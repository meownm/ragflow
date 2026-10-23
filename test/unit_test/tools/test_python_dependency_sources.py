"""Verify the source artifacts consumed by frozen installs, before any deployment."""

import hashlib
import json
from pathlib import Path
import tarfile
import tomllib
import warnings
from email.parser import BytesParser

import pytest

ROOT = Path(__file__).resolve().parents[3]
VENDOR = ROOT / "vendor/python"


@pytest.mark.parametrize("entry", json.loads((VENDOR / "artifacts.json").read_text(encoding="utf-8")), ids=lambda entry: entry["name"])
def test_source_artifact_declares_resolvable_dependencies(entry):
    path = VENDOR / entry["file"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = next(package for package in lock["package"] if package["name"] == entry["name"])
    assert locked["sdist"]["hash"] == "sha256:" + entry["sha256"]
    with tarfile.open(path) as archive:
        name = next(name for name in archive.getnames() if name.count("/") == 1 and name.endswith("/PKG-INFO"))
        metadata = BytesParser().parsebytes(archive.extractfile(name).read())
        license_name = "LICENSE.txt" if entry["name"] == "scholarly" else "LICENSE"
        assert any(name.endswith("/" + license_name) for name in archive.getnames())
        if entry["name"] == "scholarly":
            with warnings.catch_warnings():
                warnings.simplefilter("error", SyntaxWarning)
                for member in archive.getmembers():
                    if member.isfile() and member.name.endswith(".py"):
                        compile(archive.extractfile(member).read(), member.name, "exec")
    assert metadata["Version"] == entry["version"]
    requirements = metadata.get_all("Requires-Dist")
    if entry["name"] == "crawl4ai":
        assert "litellm==1.88.6" in requirements
        assert not any("unclecode-litellm" in req for req in requirements)
    elif entry["name"] == "moodlepy":
        assert any(req.startswith("attrs ") and ">=23.2.0" in req and "<27" in req for req in requirements)
    elif entry["name"] == "agentrun-sdk":
        memory = [req for req in requirements if req.startswith("agentrun-mem0ai")]
        assert len(memory) == 1 and 'extra == "memory"' in memory[0]
