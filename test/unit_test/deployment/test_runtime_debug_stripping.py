"""Exercise native loader preservation with a real DWARF shared library."""

import ctypes
import base64
import csv
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "linux" or not shutil.which("cc") or not shutil.which("strip"), reason="Requires Linux dependency builder with cc/binutils")

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("strip_runtime_debug", ROOT / "deployment/runner/strip_runtime_debug.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


@pytest.fixture
def library(tmp_path):
    source = tmp_path / "probe.c"
    source.write_text('static int state; const char *label = "runtime"; int answer(int value) { state += value; return state + 42; }\n')
    target = tmp_path / "libprobe.so"
    subprocess.run(["cc", "-g", "-shared", "-fPIC", str(source), "-o", str(target)], check=True)
    return target


def test_native_function_and_loader_bytes_survive_debug_removal(library):
    library.chmod(0o4750)
    os.setxattr(library, "user.ragflow-test", b"preserved")
    before = library.stat()
    assert native._layout(library)[0]
    result = native.strip_debug(library.parent)
    assert result == {"stripped_files": 1, "saved_file_bytes": before.st_size - library.stat().st_size, "retained_layout_files": [], "updated_package_records": 0}
    assert result["saved_file_bytes"] > 0
    assert not native._layout(library)[0]
    after = library.stat()
    assert (before.st_mode, before.st_uid, before.st_gid, before.st_mtime_ns) == (after.st_mode, after.st_uid, after.st_gid, after.st_mtime_ns)
    assert os.getxattr(library, "user.ragflow-test") == b"preserved"
    loaded = ctypes.CDLL(str(library))
    assert loaded.answer(5) == 47
    assert loaded.answer(2) == 49
    assert ctypes.c_char_p.in_dll(loaded, "label").value == b"runtime"
    assert native.strip_debug(library.parent) == {"stripped_files": 0, "saved_file_bytes": 0, "retained_layout_files": [], "updated_package_records": 0}


@pytest.mark.parametrize("offset", [80, 4096])
def test_corrupted_stripper_output_is_rejected_without_replacing_original(library, offset):
    original = library.read_bytes()
    tool = library.parent / "bad-strip"
    tool.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, subprocess, sys\n"
        "subprocess.run(['strip', *sys.argv[1:]], check=True)\n"
        "p = pathlib.Path(sys.argv[-2])\n"
        "data = bytearray(p.read_bytes())\n"
        f"data[{offset}] ^= 1\n"
        "p.write_bytes(data)\n"
    )
    tool.chmod(0o755)
    result = native.strip_debug(library.parent, str(tool))
    assert result == {"stripped_files": 0, "saved_file_bytes": 0, "retained_layout_files": [library.name], "updated_package_records": 0}
    assert library.read_bytes() == original
    assert not list(library.parent.glob(".ragflow-debug-*"))


def test_missing_stripper_preserves_original_and_cleans_temporary(library):
    original = library.read_bytes()
    with pytest.raises(FileNotFoundError):
        native.strip_debug(library.parent, str(library.parent / "absent"))
    assert library.read_bytes() == original
    assert not list(library.parent.glob(".ragflow-debug-*"))


def test_symlinks_are_not_followed(library):
    root = library.parent / "dependencies"
    root.mkdir()
    (root / library.name).symlink_to(library)
    (root / "external").symlink_to(library.parent, target_is_directory=True)
    assert native.strip_debug(root)["stripped_files"] == 0
    assert native._layout(library)[0]
    with pytest.raises(ValueError, match="unlinked dependency directory"):
        native.strip_debug(root / "external")


def test_malformed_elf_is_an_error(tmp_path):
    path = tmp_path / "broken.so"
    path.write_bytes(b"\x7fELF")
    with pytest.raises(ValueError, match="ELF64"):
        native.strip_debug(tmp_path)
    assert path.read_bytes() == b"\x7fELF"


def test_installed_package_record_tracks_actual_binary_bytes(library):
    root = library.parent
    packages = root / "lib/python3.13/site-packages"
    package = packages / "probe.dist-info"
    package.mkdir(parents=True)
    target = packages / library.name
    library.rename(target)
    record = package / "RECORD"
    record.write_text(f"{target.name},sha256=original,0\nunmodified.py,sha256=keep,12\nprobe.dist-info/RECORD,,\n")
    assert native.strip_debug(root)["updated_package_records"] == 1
    rows = list(csv.reader(record.read_text().splitlines()))
    digest = base64.urlsafe_b64encode(hashlib.sha256(target.read_bytes()).digest()).rstrip(b"=").decode()
    assert rows == [[target.name, "sha256=" + digest, str(target.stat().st_size)], ["unmodified.py", "sha256=keep", "12"], ["probe.dist-info/RECORD", "", ""]]
