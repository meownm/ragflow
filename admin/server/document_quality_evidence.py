"""Private, persistent evidence for administrative model-quality executions."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import traceback
from zipfile import ZIP_DEFLATED, ZipFile


_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
_FILES = (
    "manifest.json",
    "report.json",
    "report.original.json",
    "manifest.original.json",
    "report.exchanges.json",
    "report.calls.jsonl",
    "report.errors.jsonl",
    "golden.json",
    "pytest.log",
    "runner-error.txt",
)


def _root() -> Path:
    root = Path(os.environ.get("BUSINESS_DOCUMENT_QUALITY_EVIDENCE_DIR") or Path(__file__).resolve().parents[2] / "quality-evidence")
    if root.is_symlink():
        raise ValueError("Quality evidence root must not be a symlink")
    return root


def _directory(kind: str, identifier: str) -> Path:
    if kind not in {"run", "campaign"} or not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise ValueError("Invalid quality evidence identity")
    directory = _root() / f"{kind}-{identifier}"
    if directory.is_symlink():
        raise ValueError("Quality evidence directory must not be a symlink")
    return directory


def evidence_available(kind: str, identifier: str) -> bool:
    try:
        manifest = _directory(kind, identifier) / "manifest.json"
        return not manifest.is_symlink() and manifest.is_file()
    except ValueError:
        return False


def _write_manifest(directory: Path, value: dict) -> None:
    pending = directory / "manifest.json.tmp"
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(directory / "manifest.json")


def _prune() -> None:
    root = _root()
    now = time.time()
    days = int(os.environ.get("BUSINESS_DOCUMENT_QUALITY_EVIDENCE_DAYS", "14"))
    maximum = int(os.environ.get("BUSINESS_DOCUMENT_QUALITY_EVIDENCE_MAX_BYTES", str(1024**3)))
    if days < 1 or maximum < 1:
        raise ValueError("Quality evidence retention must be positive")
    finished = []
    total = 0
    for directory in root.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            kind, identifier = directory.name.split("-", 1)
            if _directory(kind, identifier) != directory:
                continue
            manifest_path = directory / "manifest.json"
            if manifest_path.is_symlink():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                continue
            size = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file() and not path.is_symlink())
            total += size
            if manifest.get("state") == "FINISHED" and isinstance(manifest.get("finished_at"), (int, float)):
                finished.append((manifest["finished_at"], directory, size))
            elif manifest.get("state") == "RUNNING" and isinstance(manifest.get("started_at"), (int, float)) and now - manifest["started_at"] > days * 86400:
                shutil.rmtree(directory)
                total -= size
        except (ValueError, OSError):
            continue
    finished.sort()
    latest = {}
    for _stamp, directory, _size in finished:
        try:
            product = json.loads((directory / "manifest.json").read_text(encoding="utf-8")).get("document_type", "business_requirements")
            latest[product] = directory
        except (OSError, ValueError):
            continue
    # Each product keeps its last completed execution independently.
    for stamp, directory, size in finished:
        if directory in latest.values():
            continue
        if now - stamp > days * 86400 or total > maximum:
            shutil.rmtree(directory)
            total -= size


@contextmanager
def record_quality_evidence(kind: str, identifier: str, metadata: dict):
    directory = _directory(kind, identifier)
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _prune()
    directory.mkdir(mode=0o700)
    manifest = {"schema_version": 1, "kind": kind, "id": identifier, "state": "RUNNING", "started_at": time.time(), **metadata}
    _write_manifest(directory, manifest)
    try:
        yield directory
    except BaseException:
        (directory / "runner-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        manifest.update(state="FINISHED", finished_at=time.time())
        manifest["files"] = {
            name: {"bytes": path.stat().st_size, "sha256": _digest(path)} for name in _FILES if name != "manifest.json" and (path := directory / name).is_file() and not path.is_symlink()
        }
        _write_manifest(directory, manifest)
        _prune()


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def evidence_archive(kind: str, identifier: str):
    """Return a seekable archive owned by the caller; never include pytest databases."""
    directory = _directory(kind, identifier)
    if not evidence_available(kind, identifier):
        raise FileNotFoundError(identifier)
    archive = tempfile.SpooledTemporaryFile(max_size=8 * 1024**2)
    try:
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
            for name in _FILES:
                path = directory / name
                if path.is_symlink():
                    raise ValueError("Linked quality evidence file")
                if path.is_file():
                    bundle.write(path, arcname=name)
        archive.seek(0)
        return archive
    except BaseException:
        archive.close()
        raise
