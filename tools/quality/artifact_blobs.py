"""Compressed content-addressed recovery bytes shared by image generations."""

import gzip
import hashlib
from pathlib import Path
import tempfile

from tools.quality.artifact_registry import unlinked


def digest(path: Path) -> str:
    unlinked(path)
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def put(stream, root: Path) -> dict:
    directory = root / "blobs"
    unlinked(directory)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    value, size = hashlib.sha256(), 0
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".pending-", delete=False) as output:
            temporary = Path(output.name)
            with gzip.GzipFile(filename="", fileobj=output, mode="wb", compresslevel=1, mtime=0) as compressed:
                while chunk := stream.read(1024 * 1024):
                    value.update(chunk)
                    size += len(chunk)
                    compressed.write(chunk)
        raw = value.hexdigest()
        destination = directory / (raw + ".gz")
        unlinked(destination)
        if destination.exists():
            with gzip.open(destination, "rb") as existing:
                if hashlib.file_digest(existing, "sha256").hexdigest() != raw:
                    raise ValueError("Recovery blob integrity mismatch")
        else:
            temporary.replace(destination)
        return {"name": destination.name, "sha256": digest(destination), "bytes": destination.stat().st_size, "raw_sha256": raw, "raw_bytes": size}
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def path(root: Path, blob: dict) -> Path:
    import re

    if not re.fullmatch(r"[0-9a-f]{64}\.gz", blob["name"]):
        raise ValueError("Invalid recovery blob name")
    result = root / "blobs" / blob["name"]
    if digest(result) != blob["sha256"] or result.stat().st_size != blob["bytes"]:
        raise ValueError("Recovery blob checksum mismatch")
    return result
