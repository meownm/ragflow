"""Lossless asset sharing on the filesystem used by the image build."""

import importlib.util
import os
from pathlib import Path
import stat
import shutil
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Run against the Linux image filesystem, including symlinks and xattrs")


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("deduplicate_runtime", ROOT / "deployment/runner/deduplicate_runtime.py")
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)
DATA = b"model weights" * 8192


def test_identical_assets_keep_paths_bytes_and_share_storage(tmp_path):
    first, second, other = (tmp_path / name for name in ("layout.onnx", "layout.manual.onnx", "layout.paper.onnx"))
    first.write_bytes(DATA)
    second.write_bytes(DATA)
    other.write_bytes(b"x" * len(DATA))
    before = {p.name: p.read_bytes() for p in (first, second, other)}
    assert assets.deduplicate([tmp_path]) == {"linked_files": 1, "saved_file_bytes": len(DATA)}
    assert os.path.samefile(first, second)
    assert not os.path.samefile(first, other)
    assert {p.name: p.read_bytes() for p in (first, second, other)} == before
    assert assets.deduplicate([tmp_path]) == {"linked_files": 0, "saved_file_bytes": 0}


def test_atomic_model_update_keeps_other_profiles_unchanged(tmp_path):
    first, second, update = (tmp_path / name for name in ("layout.onnx", "layout.manual.onnx", "update.onnx"))
    first.write_bytes(DATA)
    second.write_bytes(DATA)
    assets.deduplicate([tmp_path])
    update.write_bytes(b"new model")
    os.replace(update, second)
    assert first.read_bytes() == DATA
    assert second.read_bytes() == b"new model"
    assert not os.path.samefile(first, second)


def test_assets_with_different_permissions_are_not_shared(tmp_path):
    first, second = tmp_path / "read-only", tmp_path / "writable"
    first.write_bytes(DATA)
    second.write_bytes(DATA)
    first.chmod(stat.S_IRUSR)
    second.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert assets.deduplicate([tmp_path])["linked_files"] == 0
    assert not os.path.samefile(first, second)


def test_symlinks_do_not_pull_external_files_into_the_asset_tree(tmp_path):
    outside, root = tmp_path / "external", tmp_path / "assets"
    outside.mkdir()
    root.mkdir()
    (outside / "weights").write_bytes(DATA)
    (root / "weights").write_bytes(DATA)
    (root / "linked-directory").symlink_to(outside, target_is_directory=True)
    (root / "linked-file").symlink_to(outside / "weights")
    assert assets.deduplicate([root])["linked_files"] == 0
    assert not os.path.samefile(root / "weights", outside / "weights")
    assert (root / "linked-file").is_symlink()
    with pytest.raises(ValueError, match="unlinked asset directory"):
        assets.deduplicate([root / "linked-directory"])


def test_different_extended_attributes_are_not_shared(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.write_bytes(DATA)
    second.write_bytes(DATA)
    os.setxattr(first, "user.ragflow-test", b"first")
    os.setxattr(second, "user.ragflow-test", b"second")
    assert assets.deduplicate([tmp_path])["linked_files"] == 0
    assert os.getxattr(first, "user.ragflow-test") == b"first"
    assert os.getxattr(second, "user.ragflow-test") == b"second"


def test_copy_cache_stamp_is_stable_until_the_recipe_changes(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    (root / "one").write_bytes(DATA)
    (root / "two").write_bytes(DATA)
    recipe = tmp_path / "deduplicate_runtime.py"
    shutil.copyfile(ROOT / "deployment/runner/deduplicate_runtime.py", recipe)
    command = [sys.executable, "-B", str(recipe), str(root)]
    subprocess.run(command, check=True, capture_output=True)
    first = (root / ".ragflow-asset-layout").read_bytes()
    subprocess.run(command, check=True, capture_output=True)
    assert (root / ".ragflow-asset-layout").read_bytes() == first
    with recipe.open("a") as stream:
        stream.write("\n# Changed build recipe\n")
    subprocess.run(command, check=True, capture_output=True)
    assert (root / ".ragflow-asset-layout").read_bytes() != first
    assert (root / "one").read_bytes() == (root / "two").read_bytes() == DATA
