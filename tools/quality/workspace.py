"""Lifetime of a disposable build tree shared with the local Windows sweeper."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

MARKER = "ragflow-disposable-v2\n"
_JOB_PATTERN = re.compile(r"Global\\RAGFlow-build-[0-9a-f]{32}\Z")
_job = None
_job_handle = None
_guard = None
_BACKUP = re.compile(r"\.(dump|bak)(\.(gz|zip|zst))?$|\.sql\.(gz|zip|zst)$|(backup|database|postgres|mysql).*\.sql$", re.IGNORECASE)


class _ProtectedTask(OSError):
    pass


def _kernel():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, result in (
        ("CreateJobObjectW", [wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE),
        ("OpenJobObjectW", [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        ("CreateMutexW", [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        ("ReleaseMutex", [wintypes.HANDLE], wintypes.BOOL),
        ("QueryInformationJobObject", [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID], wintypes.BOOL),
        ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
    ):
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    return kernel


def process_job() -> str:
    """Keep a queryable job for children without changing the owner's jobs."""
    global _job, _job_handle, _guard
    if os.name != "nt":
        return ""
    if _job is None:
        import ctypes

        kernel = _kernel()
        name = "Global\\RAGFlow-build-" + uuid.uuid4().hex
        handle = kernel.CreateJobObjectW(None, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        guard = None
        try:
            # The guard stays outside this job and keeps its name queryable
            # after the owner exits, until every registered descendant exits.
            guard = subprocess.Popen(
                [sys.executable, "-B", str(Path(__file__).resolve()), "watch-job", "--job", name, "--owner", str(os.getpid())],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            guard.stdin.write(b"G")
            guard.stdin.close()
            ready = []
            reader = threading.Thread(target=lambda: ready.append(guard.stdout.readline()), daemon=True)
            reader.start()
            reader.join(timeout=10)
            if reader.is_alive() or len(ready) != 1 or ready[0].strip() != b"ready":
                raise OSError("build process guard did not become ready")
            guard.stdout.close()
        except BaseException:
            if guard is not None:
                guard.kill()
                guard.wait(timeout=10)
                if guard.stdin and not guard.stdin.closed:
                    guard.stdin.close()
                guard.stdout.close()
            kernel.CloseHandle(handle)
            raise
        # No kill-on-close limit. The guard exits when the last member exits.
        _job, _job_handle, _guard = name, handle, guard
    return _job


def _watch_job(name: str, owner: int):
    """Hold the job name and a handle to the original owner, avoiding PID reuse."""
    if not _JOB_PATTERN.fullmatch(name):
        raise ValueError("invalid build process job")
    if sys.stdin.buffer.read(1) != b"G":
        return
    import ctypes

    kernel = _kernel()
    handle = kernel.OpenJobObjectW(4, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    process = kernel.OpenProcess(0x100000, False, owner)  # SYNCHRONIZE
    if not process:
        kernel.CloseHandle(handle)
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        print("ready", flush=True)
        while kernel.WaitForSingleObject(process, 0) == 258 or job_active(name):
            time.sleep(0.1)
    finally:
        kernel.CloseHandle(process)
        kernel.CloseHandle(handle)


def job_active(name: str) -> bool:
    if not name and os.name != "nt":
        return False
    if not isinstance(name, str) or not _JOB_PATTERN.fullmatch(name):
        raise ValueError("invalid build process job")
    import ctypes
    from ctypes import wintypes

    kernel = _kernel()
    handle = kernel.OpenJobObjectW(4, False, name)  # JOB_OBJECT_QUERY
    if not handle:
        error = ctypes.get_last_error()
        if error == 2:  # Destroyed only when all handles AND all members are gone.
            return False
        raise ctypes.WinError(error)
    try:
        # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION: ActiveProcesses at offset 40.
        accounting = ctypes.create_string_buffer(48)
        if not kernel.QueryInformationJobObject(handle, 1, accounting, len(accounting), None):
            raise ctypes.WinError(ctypes.get_last_error())
        active = wintypes.DWORD.from_buffer(accounting, 40).value
        return active > 0
    finally:
        kernel.CloseHandle(handle)


def start_process(command, **options) -> subprocess.Popen:
    """Create the child in its job atomically, including on owner failure."""
    if os.name != "nt":
        return subprocess.Popen(command, **options)
    process_job()
    try:
        from ._windows_process import _JobPopen
    except ImportError:
        from _windows_process import _JobPopen
    return _JobPopen(command, job=_job_handle, **options)


@contextmanager
def _task_lock(path: Path):
    """Serialize reservation, final validation and deletion without lock files."""
    if os.name != "nt":
        yield
        return
    import ctypes

    kernel = _kernel()
    name = "Global\\RAGFlow-temp-" + hashlib.sha256(os.path.normcase(str(path)).encode()).hexdigest()
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        state = kernel.WaitForSingleObject(handle, 0)
        if state not in (0, 0x80):  # A dead owner leaves an abandoned, acquirable mutex.
            raise OSError("temporary task mutation is busy")
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def _unlinked(path: Path):
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("linked disposable workspace path")


def _open_lease(path: Path, *, create: bool = True, exclusive: bool = False):
    if os.name != "nt":
        return path.open("xb" if create else "r+b")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_new = create
    create_file = kernel.CreateFileW
    create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create_file.restype = wintypes.HANDLE
    # Allow our own rmtree while the lease remains open; exclusive access by
    # the sweeper is still denied until the entire workspace has been removed.
    handle = create_file(str(path), 0xC0000000, 7 if create_new and not exclusive else 4, None, 1 if create_new else 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(wintypes.HANDLE(handle))
        raise
    return os.fdopen(descriptor, "w+b")


def _metadata(path: Path) -> str:
    for name in (".ragflow-temp", ".process-job", ".lease"):
        _unlinked(path / name)
    if (path / ".ragflow-temp").read_text(encoding="utf-8") != MARKER:
        raise ValueError("unmanaged disposable workspace")
    return (path / ".process-job").read_text(encoding="utf-8").strip()


def _retire(path: Path, job: str | None = None, *, remove_pin: bool = False, lease=None):
    """Retain an identity-bound, locked receipt until the whole tree is gone."""
    with _task_lock(path):
        _unlinked(path)
        if job is None:
            job = _metadata(path) if not remove_pin or (path / ".ragflow-temp").exists() else "Global\\RAGFlow-build-" + uuid.uuid4().hex
        if job_active(job):
            raise _ProtectedTask("build job still has active processes")
        owned_lease = _open_lease(path / ".lease", create=False) if lease is None and (path / ".lease").exists() else None
        try:
            pin = path / ".keep" if remove_pin else None
            _tree_stats(path, ignore_pin=pin)
            receipt = path.parent / (".ragflow-cleanup-" + uuid.uuid4().hex + ".json")
            with _open_lease(receipt, exclusive=True) as stream:
                stream.write(json.dumps({"version": 1, "original": path.name, "identity": _directory_identity(path), "job": job}).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
                if pin is not None:
                    pin.unlink(missing_ok=True)
                _remove_tree(path)
                receipt.unlink()
        finally:
            if owned_lease:
                owned_lease.close()


def _pytest_current_link(path: Path) -> bool:
    """Accept pytest's sibling numbered-directory alias, including a stale one."""
    prefix = path.name.removesuffix("current")
    if not prefix or prefix == path.name:
        return False
    try:
        target = Path(os.path.abspath(path.parent / path.readlink()))
        _unlinked(target.parent)
        # Windows readlink may use the extended \\?\ spelling of the same parent.
        if not target.parent.samefile(path.parent) or not re.fullmatch(re.escape(prefix) + r"[0-9]+", target.name):
            return False
        try:
            status = target.lstat()
        except FileNotFoundError:
            return True
        return stat.S_ISDIR(status.st_mode) and not (stat.S_ISLNK(status.st_mode) or getattr(status, "st_file_attributes", 0) & 0x400)
    except (OSError, ValueError):
        return False


def _guard_entry(path: Path, *, ignore_pin: Path | None = None):
    status = path.lstat()
    name = path.name.casefold()
    if (name == ".keep" and path != ignore_pin) or name in {".git", "backup", "backups"} or _BACKUP.search(name):
        raise _ProtectedTask(f"Protected entry: {path}")
    linked = stat.S_ISLNK(status.st_mode) or bool(getattr(status, "st_file_attributes", 0) & 0x400)
    # Disposable links are entries to unlink, never traversal roots.
    if linked:
        if stat.S_ISLNK(status.st_mode) and _pytest_current_link(path):
            return status, True
        if any(parent.name.casefold() == "node_modules" for parent in path.parents) and (stat.S_ISLNK(status.st_mode) or path.is_junction()):
            return status, True
        raise _ProtectedTask(f"Linked entry: {path}")
    return status, False


def _tree_stats(path: Path, *, ignore_pin: Path | None = None) -> dict:
    _unlinked(path)

    def scan(directory):
        latest, size = directory.stat().st_mtime, 0
        with os.scandir(directory) as entries:
            for entry in entries:
                child = Path(entry.path)
                status, linked = _guard_entry(child, ignore_pin=ignore_pin)
                latest = max(latest, status.st_mtime)
                if not linked and stat.S_ISDIR(status.st_mode):
                    nested = scan(child)
                    latest, size = max(latest, nested["latest"]), size + nested["size"]
                else:
                    size += status.st_size
        return {"latest": latest, "size": size}

    return scan(path)


def _remove_tree(path: Path):
    """Never unlink protected entries, including ones created after preflight."""
    _unlinked(path)

    def remove(directory):
        with os.scandir(directory) as entries:
            for entry in entries:
                if (path / ".keep").exists():
                    raise _ProtectedTask(f"Protected entry: {path / '.keep'}")
                child = Path(entry.path)
                status, linked = _guard_entry(child)
                if linked:
                    if child.is_junction():
                        child.rmdir()
                    else:
                        child.unlink()
                elif stat.S_ISDIR(status.st_mode):
                    remove(child)
                else:
                    try:
                        child.unlink()
                    except PermissionError:
                        _guard_entry(child)
                        if os.name != "nt" or not child.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY:
                            raise
                        child.chmod(stat.S_IWRITE)
                        child.unlink()
        directory.rmdir()

    remove(path)


def _reserve(path: Path):
    with _task_lock(path):
        _unlinked(path / ".keep")
        path.mkdir(exist_ok=True)
        (path / ".keep").write_text("Active task. Protected until explicit completion; save unique results and backups first.\n", encoding="utf-8")


def _scan(path: Path) -> dict:
    with _task_lock(path):
        job = _metadata(path)
        with _open_lease(path / ".lease", create=False):
            if job_active(job):
                return {"active": True}
            return {"active": False, **_tree_stats(path)}


def _directory_identity(path: Path) -> list[int]:
    status = path.stat()
    if not path.is_dir():
        raise ValueError("cleanup target is not a directory")
    return [status.st_dev, status.st_ino, getattr(status, "st_birthtime_ns", 0)]


def _receipt(root: Path, receipt: Path, stream=None, *, expected_task: str | None = None) -> dict:
    if receipt.parent != root or not re.fullmatch(r"\.ragflow-cleanup-[0-9a-f]{32}\.json", receipt.name):
        raise ValueError("invalid cleanup receipt path")
    _unlinked(receipt)
    value = json.loads(stream.read().decode("utf-8") if stream else receipt.read_text(encoding="utf-8"))
    if value.get("version") != 1 or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", value.get("original", "")):
        raise ValueError("invalid cleanup receipt")
    target = root / value["original"]
    if expected_task is not None and target != root / expected_task:
        raise ValueError("cleanup receipt belongs to another task")
    _unlinked(target)
    identity = value["identity"]
    if not isinstance(identity, list) or len(identity) != 3 or any(type(item) is not int for item in identity) or not identity[1]:
        raise ValueError("invalid cleanup directory identity")
    exists = target.exists()
    matches = exists and _directory_identity(target) == identity
    return {"path": str(target), "exists": exists, "matches": matches, "job": value["job"], "active": job_active(value["job"])}


@contextmanager
def disposable_workspace(path: Path):
    path = path.absolute()
    _unlinked(path)
    job = process_job()
    path.mkdir(parents=True, exist_ok=False)
    lease = None
    try:
        (path / ".ragflow-temp").write_text(MARKER, encoding="utf-8")
        (path / ".process-job").write_text(job, encoding="utf-8")
        lease = _open_lease(path / ".lease")
        lease.write(b"ragflow-build-v1\n")
        lease.flush()
        yield path
    finally:
        try:
            _unlinked(path)
            _retire(path, job, lease=lease)
        finally:
            if lease is not None:
                lease.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("scan", "reserve", "retire", "inspect", "recover", "watch-job"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--path", type=Path)
    parser.add_argument("--job")
    parser.add_argument("--owner", type=int)
    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--expected-task", help="Restrict receipt inspection/recovery to this task name")
    args = parser.parse_args()
    if args.action == "watch-job":
        _watch_job(args.job, args.owner)
        return
    if args.root is None or args.path is None:
        parser.error("--root and --path are required for cleanup")
    root, path = args.root.absolute(), args.path.absolute()
    _unlinked(root)
    _unlinked(path)
    if path.parent != root or root.name != "ragflow" or root.parent.name != "tmp":
        raise ValueError("cleanup target must be directly inside tmp/ragflow")
    if args.action == "reserve":
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", path.name):
            raise ValueError("invalid task name")
        _reserve(path)
        return
    if args.action == "scan":
        print(json.dumps(_scan(path)))
        return
    if args.action in {"inspect", "recover"}:
        if args.action == "inspect":
            print(json.dumps(_receipt(root, path, expected_task=args.expected_task)))
            return
        with _open_lease(path, create=False) as stream:
            info = _receipt(root, path, stream, expected_task=args.expected_task)
            target = Path(info["path"])
            with _task_lock(target):
                # The directory may have been replaced since the inspector ran.
                stream.seek(0)
                info = _receipt(root, path, stream, expected_task=args.expected_task)
                if info["active"]:
                    raise _ProtectedTask("cleanup job still has active processes")
                if info["matches"]:
                    lease = _open_lease(target / ".lease", create=False) if (target / ".lease").exists() else None
                    try:
                        pin = target / ".keep" if args.manual else None
                        _tree_stats(target, ignore_pin=pin)
                        if pin is not None:
                            pin.unlink(missing_ok=True)
                        _remove_tree(target)
                    finally:
                        if lease:
                            lease.close()
                path.unlink()
                print(json.dumps({"removed": info["matches"], "completed": not info["exists"] or info["matches"], "replaced": info["exists"] and not info["matches"]}))
        return
    _retire(path, remove_pin=args.manual)


if __name__ == "__main__":
    main()
