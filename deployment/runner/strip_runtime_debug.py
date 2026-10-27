"""Remove native dependency DWARF data after verifying the loader's bytes.

Build-only: original wheels remain in python_dependencies for native debugging.
Do not discard ordinary symbols, unwind tables, CUDA data or executable sections.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile


def _read(stream, offset: int, size: int) -> bytes:
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("Truncated ELF")
    return data


def _layout(path: Path) -> tuple[bool, tuple] | None:
    with path.open("rb") as stream:
        header = stream.read(64)
        if header[:4] != b"\x7fELF":
            return None
        if len(header) != 64 or header[4:7] != b"\x02\x01\x01":
            raise ValueError(f"Expected ELF64 little endian: {path}")
        phoff, shoff = struct.unpack_from("<QQ", header, 32)
        phsize, phnum, shsize, shnum, names_index = struct.unpack_from("<HHHHH", header, 54)
        if phsize != 56 or shsize != 64 or not shnum or names_index >= shnum or phnum == 65535:
            raise ValueError(f"Unsupported ELF tables: {path}")
        sections = [struct.unpack("<IIQQQQIIQQ", _read(stream, shoff + i * shsize, shsize)) for i in range(shnum)]
        names = _read(stream, sections[names_index][4], sections[names_index][5])
        allocated = []
        debug = False
        for section in sections:
            name = names[section[0] :].split(b"\0", 1)[0]
            if name.startswith((b".debug", b".zdebug")):
                if section[2] & 2:
                    raise ValueError(f"Allocated debug section: {path}")
                debug = True
            if section[2] & 2:  # SHF_ALLOC; NOBITS has no file payload.
                data = b"" if section[1] == 8 else _read(stream, section[4], section[5])
                allocated.append((name, section[1:4], section[5], section[8:], hashlib.sha256(data).digest()))
        programs = _read(stream, phoff, phsize * phnum)
        loaded = []
        for i in range(phnum):
            segment = struct.unpack_from("<IIQQQQQQ", programs, i * phsize)
            if segment[0] == 1:  # PT_LOAD, including padding between sections.
                data = bytearray(_read(stream, segment[2], segment[5]))
                if segment[2] == 0:
                    # Only the section table's location/count/name index may change.
                    data[40:48] = b"\0" * 8
                    data[60:64] = b"\0" * 4
                loaded.append(hashlib.sha256(data).digest())
        return debug, (header[:40], header[48:60], programs, allocated, loaded)


def strip_debug(root: Path, tool: str = "strip") -> dict:
    if not root.is_dir() or root.is_symlink() or root.is_junction():
        raise ValueError(f"Expected an unlinked dependency directory: {root}")
    changed = saved = 0
    retained = []
    replacements = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if not (Path(directory) / name).is_symlink() and not (Path(directory) / name).is_junction())
        for name in sorted(files):
            path = Path(directory) / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or not (name.endswith(".so") or ".so." in name):
                continue
            layout = _layout(path)
            if layout is None or not layout[0]:
                continue
            fd, temporary_name = tempfile.mkstemp(prefix=".ragflow-debug-", dir=directory)
            os.close(fd)
            temporary = Path(temporary_name)
            try:
                subprocess.run([tool, "--strip-debug", "--no-merge-notes", "--preserve-dates", "-o", str(temporary), str(path)], check=True)
                result = _layout(temporary)
                if result is None or result[0] or result[1] != layout[1]:
                    # Some wheel packers relocate loaded sections. Retain their
                    # originals rather than accepting even harmless repacking.
                    retained.append(str(path.relative_to(root)))
                    continue
                if temporary.stat().st_size >= info.st_size:
                    raise ValueError(f"ELF debug removal did not reduce size: {path}")
                os.chown(temporary, info.st_uid, info.st_gid)
                shutil.copystat(path, temporary)
                saved += info.st_size - temporary.stat().st_size
                os.replace(temporary, path)
                replacements[path.resolve()] = path
                changed += 1
            finally:
                temporary.unlink(missing_ok=True)
    records = 0
    for record in sorted(root.glob("lib/python*/site-packages/*.dist-info/RECORD")):
        rows = list(csv.reader(io.StringIO(record.read_text())))
        modified = False
        for row in rows:
            target = replacements.get((record.parent.parent / row[0]).resolve())
            if target is not None:
                with target.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").digest()
                row[1:] = ["sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode(), str(target.stat().st_size)]
                modified = True
        if modified:
            output = io.StringIO(newline="")
            csv.writer(output, lineterminator="\n").writerows(rows)
            record.write_text(output.getvalue())
            records += 1
    return {"stripped_files": changed, "saved_file_bytes": saved, "retained_layout_files": retained, "updated_package_records": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    print(json.dumps(strip_debug(parser.parse_args().root)))
