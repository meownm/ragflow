"""Share identical installed assets without removing their public file paths.

Use only for model, browser, dependency and compiler-resource trees whose files
are read at runtime. Updates must replace files atomically, as wheel/model
installers do; mutable application data and configuration are outside this scope.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid


def _attributes(path: Path, info: os.stat_result) -> tuple:
    xattrs = tuple((name, os.getxattr(path, name)) for name in sorted(os.listxattr(path))) if hasattr(os, "listxattr") else ()
    return info.st_size, stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid, xattrs


def deduplicate(roots: list[Path]) -> dict:
    candidates = defaultdict(list)
    for root in roots:
        if not root.is_dir() or root.is_symlink() or root.is_junction():
            raise ValueError(f"Expected an unlinked asset directory: {root}")
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(name for name in dirs if not (Path(directory) / name).is_symlink() and not (Path(directory) / name).is_junction())
            for name in sorted(files):
                path = Path(directory) / name
                info = path.lstat()
                if stat.S_ISREG(info.st_mode) and info.st_size >= 64 * 1024:
                    candidates[_attributes(path, info)].append(path)

    linked = saved = 0
    for paths in candidates.values():
        if len(paths) < 2:
            continue
        content = {}
        for path in sorted(set(paths)):
            info = path.stat()
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest not in content:
                content[digest] = path
                continue
            source = content[digest]
            original = source.stat()
            if (original.st_dev, original.st_ino) == (info.st_dev, info.st_ino):
                continue
            temporary = path.with_name(".ragflow-asset-" + uuid.uuid4().hex)
            try:
                os.link(source, temporary, follow_symlinks=False)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            linked += 1
            saved += info.st_size
    return {"linked_files": linked, "saved_file_bytes": saved}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    roots = parser.parse_args().roots
    result = deduplicate(roots)
    # COPY hashes file content, so bind the physical layout to copied bytes too.
    # Otherwise a byte-equivalent old layer can bypass a changed sharing recipe.
    layout = {"recipe": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "roots": sorted(str(root) for root in roots)}
    (roots[0] / ".ragflow-asset-layout").write_text(hashlib.sha256(json.dumps(layout, sort_keys=True).encode()).hexdigest() + "\n")
    print(json.dumps(result))
