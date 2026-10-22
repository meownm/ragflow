"""Job-aware CreateProcess boundary; retain stdlib Popen's pipes and lifetime."""

import ctypes
import os
import subprocess
import sys
import types
from ctypes import wintypes as w


class _Startup(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("reserved", w.LPWSTR),
        ("desktop", w.LPWSTR),
        ("title", w.LPWSTR),
        ("x", w.DWORD),
        ("y", w.DWORD),
        ("xsize", w.DWORD),
        ("ysize", w.DWORD),
        ("xchars", w.DWORD),
        ("ychars", w.DWORD),
        ("fill", w.DWORD),
        ("flags", w.DWORD),
        ("show", w.WORD),
        ("reserved_size", w.WORD),
        ("reserved_bytes", w.LPVOID),
        ("stdin", w.HANDLE),
        ("stdout", w.HANDLE),
        ("stderr", w.HANDLE),
    ]


class _ExtendedStartup(ctypes.Structure):
    _fields_ = [("startup", _Startup), ("attributes", w.LPVOID)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


def _create_process(job, application, command, process_security, thread_security, inherit, flags, env, cwd, startup):
    """Create a child atomically in its job, including the requested pipe handles."""
    if process_security is not None or thread_security is not None:
        raise ValueError("custom process security is unsupported")
    supplied = startup.lpAttributeList or {}
    if set(supplied) - {"handle_list"}:
        raise ValueError("unsupported process startup attribute")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    size_type = ctypes.c_size_t
    for name, arguments, result in (
        ("InitializeProcThreadAttributeList", [w.LPVOID, w.DWORD, w.DWORD, ctypes.POINTER(size_type)], w.BOOL),
        ("UpdateProcThreadAttribute", [w.LPVOID, w.DWORD, size_type, w.LPVOID, size_type, w.LPVOID, w.LPVOID], w.BOOL),
        ("DeleteProcThreadAttributeList", [w.LPVOID], None),
        ("CreateProcessW", [w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL, w.DWORD, w.LPVOID, w.LPCWSTR, w.LPVOID, w.LPVOID], w.BOOL),
    ):
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    handles = supplied.get("handle_list") or []
    values = [(0x2000D, (w.HANDLE * 1)(job))]  # PROC_THREAD_ATTRIBUTE_JOB_LIST
    if handles:
        values.append((0x20002, (w.HANDLE * len(handles))(*handles)))
    size = size_type()
    kernel.InitializeProcThreadAttributeList(None, len(values), 0, ctypes.byref(size))
    if not size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    attributes = ctypes.create_string_buffer(size.value)
    if not kernel.InitializeProcThreadAttributeList(attributes, len(values), 0, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        for key, value in values:
            if not kernel.UpdateProcThreadAttribute(attributes, 0, key, value, ctypes.sizeof(value), None, None):
                raise ctypes.WinError(ctypes.get_last_error())
        environment = None
        if env is not None:
            pairs = [(os.fsdecode(key), os.fsdecode(value)) for key, value in env.items()]
            if any(not key or "\0" in key + value or "=" in key[1:] for key, value in pairs):
                raise ValueError("invalid child environment")
            block = "\0".join(key + "=" + value for key, value in sorted(pairs, key=lambda item: item[0].upper())) + "\0"
            environment = ctypes.create_unicode_buffer(block)
        info = _ExtendedStartup()
        info.startup.cb = ctypes.sizeof(info)
        info.startup.flags, info.startup.show = startup.dwFlags, startup.wShowWindow
        info.startup.stdin, info.startup.stdout, info.startup.stderr = startup.hStdInput, startup.hStdOutput, startup.hStdError
        info.attributes = ctypes.cast(attributes, w.LPVOID)
        process = _ProcessInfo()
        arguments = ctypes.create_unicode_buffer(command) if command is not None else None
        sys.audit("_winapi.CreateProcess", application, command, cwd)
        if not kernel.CreateProcessW(application, arguments, None, None, inherit, flags | 0x80400, environment, cwd, ctypes.byref(info), ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        return process.process, process.thread, process.pid, process.tid
    finally:
        kernel.DeleteProcThreadAttributeList(attributes)


class _JobPopen(subprocess.Popen):
    def __init__(self, command, *, job, **options):
        self._job = job
        super().__init__(command, **options)

    def _execute_child(self, *args, **kwargs):
        # CPython's Windows entry point has no public Job-list hook. Rebind only
        # its native creation call for this invocation; do not patch globals or
        # copy the stdlib's quoting, pipe, shell and process-handle implementation.
        original = subprocess.Popen._execute_child
        native = types.SimpleNamespace(**vars(original.__globals__["_winapi"]))
        native.CreateProcess = lambda *values: _create_process(self._job, *values)
        execute = types.FunctionType(original.__code__, {**original.__globals__, "_winapi": native}, argdefs=original.__defaults__, closure=original.__closure__)
        execute.__kwdefaults__ = original.__kwdefaults__
        return execute(self, *args, **kwargs)
