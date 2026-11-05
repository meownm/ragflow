"""Verify the source artifacts consumed by frozen installs, before any deployment."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from textwrap import dedent
import tomllib
import warnings
from email.parser import BytesParser

import pytest
from packaging.requirements import Requirement

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
        project = tomllib.loads(archive.extractfile(name.replace("PKG-INFO", "pyproject.toml")).read().decode())
        for raw_requirement in project["build-system"]["requires"]:
            requirement = Requirement(raw_requirement)
            if requirement.name == "setuptools":
                assert "83.0.0" in requirement.specifier
                assert "80.9.0" not in requirement.specifier
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
        assert "litellm==1.103.1" in requirements
        assert not any("unclecode-litellm" in req for req in requirements)
    elif entry["name"] == "moodlepy":
        assert any(req.startswith("attrs ") and ">=23.2.0" in req and "<27" in req for req in requirements)
    elif entry["name"] == "agentrun-sdk":
        memory = [req for req in requirements if req.startswith("agentrun-mem0ai")]
        assert len(memory) == 1 and 'extra == "memory"' in memory[0]
    elif entry["name"] == "infinity-sdk":
        assert "pyarrow<24,>=23.0.1" in requirements
        assert not any(req.startswith("setuptools") for req in requirements)
    elif entry["name"] == "qianfan":
        assert "diskcache-community (==5.8.3)" in requirements
        assert all(package["name"] != "diskcache" for package in lock["package"])


def test_qianfan_cache_keeps_model_info_and_rejects_untrusted_pickle(tmp_path):
    # Use the SDK's normal process startup and an isolated on-disk cache.
    program = dedent("""\
        from datetime import datetime, timezone
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch

        from diskcache_community import Cache
        from qianfan.resources.typing import QfLLMInfo
        from qianfan.utils.cache import base

        def unexpected_cache_result():
            return "untrusted function called"

        class DisallowedCacheValue:
            def __reduce__(self):
                return unexpected_cache_result, ()

        config = SimpleNamespace(CACHE_DIR=sys.argv[1], DISABLE_CACHE=False)
        catalog = {
            "models": {"chat": {"sample": QfLLMInfo(endpoint="sample")}},
            "update_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
        with Cache(config.CACHE_DIR) as previous:
            previous.set("legacy", catalog)
            previous.set("tampered", DisallowedCacheValue())
        with patch.object(base, "get_config", return_value=config), base.KvCache() as current:
            assert current.get(key="legacy") == catalog
            assert current.get(key="tampered", default="missing") == "missing"
            current.set(key="current", value=catalog)
            assert current.get(key="current") == catalog
        """)
    subprocess.run([sys.executable, "-B", "-c", program, str(tmp_path)], check=True)
