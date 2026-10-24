"""llama-server process manager: 127.0.0.1 only, windowless, never outlives the app.

The process is created suspended, put into a Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE and
only then resumed, so it (and anything it spawns) dies when the job handle closes: on stop(), and
also when this process exits or crashes, so no orphan can keep holding VRAM. The handle is not
inheritable. Windows has no SIGTERM for a windowless console process, so stopping means
TerminateProcess; the GPU driver frees the model's VRAM as the process exits.

Measured setup (bench/results/llm_setup.md): --host 127.0.0.1 --port <free> -ngl 999 -c 4096 -np 1
--no-webui. /health answers 503 while the model loads and 200 once it is ready.
"""
from __future__ import annotations

import ctypes
import logging
import shutil
import socket
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import httpx

from app.config import LLM_HOME

from .models import find_llama_server

log = logging.getLogger(__name__)

__all__ = ["LlamaServer", "free_port", "HOST"]

HOST = "127.0.0.1"
CREATE_NO_WINDOW = 0x08000000
CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_TERMINATE, _PROCESS_SET_QUOTA, _PROCESS_SUSPEND_RESUME = 0x0001, 0x0100, 0x0800
_MANAGED_ARGS = frozenset({"-m", "--model", "--host", "--port", "-ngl", "--gpu-layers", "--n-gpu-layers", "-c",
                           "--ctx-size", "-np", "--parallel"})
_PORT_ATTEMPTS = 3  # a free port can be taken by someone else before llama-server binds it

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
_k32.CreateJobObjectW.restype = wintypes.HANDLE
_k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
_k32.SetInformationJobObject.restype = wintypes.BOOL
_k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
_k32.AssignProcessToJobObject.restype = wintypes.BOOL
_k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
_k32.TerminateJobObject.restype = wintypes.BOOL
_k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(wintypes.DWORD)]
_k32.QueryInformationJobObject.restype = wintypes.BOOL
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL
_ntdll = ctypes.WinDLL("ntdll")
_ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
_ntdll.NtResumeProcess.restype = ctypes.c_long


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                    "OtherOperationCount", "ReadTransferCount",
                                                    "WriteTransferCount", "OtherTransferCount")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]


def _active_processes(job: int) -> int:
    """Processes still alive in `job` (-1 if the job cannot be queried)."""
    info = _Accounting()
    if not _k32.QueryInformationJobObject(job, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(info),
                                          ctypes.sizeof(info), None):
        return -1
    return int(info.ActiveProcesses)


def _kill_on_close_job() -> int:
    """A new (non-inheritable) job object that kills its processes when its last handle closes."""
    job = _k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _k32.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits),
                                        ctypes.sizeof(limits)):
        err = ctypes.get_last_error()
        _k32.CloseHandle(job)
        raise OSError(err, "SetInformationJobObject failed")
    return job


def _assign_and_resume(job: int, pid: int) -> None:
    """Put the suspended process `pid` into `job`, then let it run."""
    handle = _k32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_SET_QUOTA | _PROCESS_SUSPEND_RESUME, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess failed")
    try:
        if not _k32.AssignProcessToJobObject(job, handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        status = _ntdll.NtResumeProcess(handle)
        if status != 0:
            raise OSError(0, f"NtResumeProcess failed (NTSTATUS 0x{status & 0xFFFFFFFF:08X})")
    finally:
        _k32.CloseHandle(handle)


def free_port() -> int:
    """A TCP port on 127.0.0.1 that nothing listens on right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


class _PortTaken(Exception):
    pass


class LlamaServer:
    """One llama-server process serving `model_path` on http://127.0.0.1:<port>. Thread-safe: start()
    and stop() may be called from different threads; stop() aborts a start() that is still loading.

    `exe` defaults to models.find_llama_server(); a ``.py`` exe runs under this Python interpreter
    (test fakes). `extra_args` are appended to the command line; the host, port, model, context,
    GPU layers and slot count are managed here and cannot be overridden through them.
    """

    def __init__(self, model_path: Path, *, ctx: int = 4096, gpu_layers: int = 999, exe: Path | None = None,
                 port: int | None = None, log_path: Path | None = None, extra_args: list[str] | None = None):
        self.model_path = Path(model_path)
        self.ctx = int(ctx)
        self.gpu_layers = int(gpu_layers)
        self.exe = Path(exe) if exe is not None else None
        self.extra_args = [str(a) for a in (extra_args or [])]
        managed = sorted(a for a in self.extra_args if a.split("=", 1)[0] in _MANAGED_ARGS)
        if managed:
            raise ValueError(f"extra_args may not set {', '.join(managed)} (managed by LlamaServer)")
        self._fixed_port = port
        self.port: int | None = port
        self.log_path = Path(log_path) if log_path is not None else (
            LLM_HOME / "logs" / f"llama-server-{self.model_path.stem}.log")
        self.model_name = self.model_path.stem
        self.base_url: str | None = None
        self.load_s = 0.0  # last successful start: spawn -> /health ready
        self._lock = threading.Lock()  # guards the fields below and base_url
        self._start_lock = threading.Lock()  # serialises start() calls; stop() never takes it
        self._abort = threading.Event()  # set by stop(): a start() in progress gives up
        self._closed = False  # close(): no start() any more, even one that has not begun yet
        self._proc: subprocess.Popen | None = None
        self._job: int | None = None

    # --- state --------------------------------------------------------------------------------
    @property
    def pid(self) -> int | None:
        with self._lock:
            return self._proc.pid if self._proc is not None else None

    @property
    def exit_code(self) -> int | None:
        """Exit code of the current process once it has exited (a crash), else None."""
        with self._lock:
            return self._proc.poll() if self._proc is not None else None

    def is_running(self) -> bool:
        """The process is alive and answered /health (it may still crash later: check again)."""
        with self._lock:
            return self.base_url is not None and self._proc is not None and self._proc.poll() is None

    def _resolve_exe(self) -> Path:
        exe = self.exe or find_llama_server()
        if exe is None or not exe.is_file():
            raise FileNotFoundError(f"llama-server not found ({exe or 'run python -m tools.setup_llm'})")
        return exe

    def command(self, port: int) -> list[str]:
        """The command line for `port` (always bound to 127.0.0.1)."""
        exe = self._resolve_exe()
        args = ["-m", str(self.model_path), "--host", HOST, "--port", str(port), "-ngl", str(self.gpu_layers),
                "-c", str(self.ctx), "-np", "1", "--no-webui", *self.extra_args]
        if exe.suffix.lower() == ".py":
            return [sys.executable, str(exe), *args]
        return [str(exe), *args]

    def log_tail(self, n: int = 30) -> str:
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-n:])

    # --- lifecycle ----------------------------------------------------------------------------
    def start(self, timeout: float = 180.0) -> str:
        """Start the server and block until /health says it is ready; returns the base URL. Returns
        at once when it is already running. Raises RuntimeError (with the tail of the server log)
        when it exits, does not get ready within `timeout` s, or stop() is called meanwhile, and
        FileNotFoundError when the model or llama-server.exe is missing. After close() it raises
        RuntimeError at once."""
        with self._start_lock:
            with self._lock:
                if self._closed:
                    raise RuntimeError("llama-server start cancelled: the server was closed")
                if self.base_url is not None and self._proc is not None and self._proc.poll() is None:
                    return self.base_url
                self._abort.clear()
            self._cleanup()  # a process that died since the last start
            if not self.model_path.is_file():
                raise FileNotFoundError(f"model not found: {self.model_path}")
            self._resolve_exe()
            deadline = time.monotonic() + timeout
            t0 = time.perf_counter()
            attempts = 1 if self._fixed_port else _PORT_ATTEMPTS
            for attempt in range(attempts):
                port = self._fixed_port or free_port()
                proc = self._spawn(port)
                try:
                    url = self._wait_ready(proc, port, deadline, timeout, last=attempt == attempts - 1)
                except _PortTaken:
                    log.info("port %d was taken before llama-server could bind it; trying another", port)
                    self._cleanup(proc)
                    continue
                except BaseException:
                    self._cleanup(proc)
                    raise
                self.load_s = time.perf_counter() - t0
                log.info("llama-server pid %d ready at %s (%s) in %.1f s", proc.pid, url, self.model_name,
                         self.load_s)
                return url
            raise RuntimeError(f"llama-server could not bind a port after {attempts} attempts:\n{self.log_tail()}")

    def _spawn(self, port: int) -> subprocess.Popen:
        cmd = self.command(port)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._open_log() as log_fh:  # the child keeps its own handle
            job = _kill_on_close_job()
            try:
                # cwd = the exe's folder: llama-server loads its ggml / CUDA DLLs from there.
                proc = subprocess.Popen(cmd, cwd=str(self._resolve_exe().parent), stdin=subprocess.DEVNULL,
                                        stdout=log_fh, stderr=subprocess.STDOUT,
                                        creationflags=CREATE_NO_WINDOW | CREATE_SUSPENDED)
            except BaseException:
                _k32.CloseHandle(job)
                raise
        try:
            _assign_and_resume(job, proc.pid)
        except OSError as exc:
            proc.kill()
            proc.wait(5)
            _k32.CloseHandle(job)
            raise RuntimeError(f"could not run llama-server inside a kill-on-close job object: {exc}") from exc
        with self._lock:
            self._proc, self._job, self.port = proc, job, port
        log.info("llama-server pid %d starting: %s", proc.pid, subprocess.list2cmdline(cmd))
        return proc

    def _open_log(self):
        """The log file, new for this process; the previous one is kept as <name>.1 (the output of a
        server that crashed). Right after a stop the old file can still be locked for a moment
        (antivirus scanning it on close): retry briefly, then copy it (it is not lost when truncated below)
        or, if it cannot even be opened for writing, use a new name."""
        old = self.log_path.with_name(self.log_path.name + ".1")
        try:
            keep = self.log_path.is_file() and self.log_path.stat().st_size > 0
        except OSError:
            keep = False
        for attempt in range(10):
            if not keep:
                break
            try:
                self.log_path.replace(old)
                break
            except OSError:
                if attempt < 9:
                    time.sleep(0.1)
        else:
            try:
                shutil.copyfile(self.log_path, old)  # still locked for a rename: keep a copy instead
            except OSError:
                log.warning("could not keep the previous llama-server log %s", self.log_path)
        for _ in range(10):
            try:
                return open(self.log_path, "wb")
            except PermissionError:
                time.sleep(0.1)
        self.log_path = self.log_path.with_name(f"{self.log_path.stem}-{time.strftime('%H%M%S')}{self.log_path.suffix}")
        return open(self.log_path, "wb")

    def _wait_ready(self, proc: subprocess.Popen, port: int, deadline: float, timeout: float, *, last: bool) -> str:
        url = f"http://{HOST}:{port}"
        with httpx.Client(timeout=httpx.Timeout(2.0), trust_env=False) as client:
            while True:
                if self._abort.is_set():
                    raise RuntimeError("llama-server start cancelled: stop() was called")
                code = proc.poll()
                if code is not None:
                    tail = self.log_tail()
                    if not last and self._fixed_port is None and "bind" in tail.lower():
                        raise _PortTaken()
                    raise RuntimeError(f"llama-server exited with code {code} while loading "
                                       f"{self.model_path.name}:\n{tail}")
                try:
                    if client.get(url + "/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError(f"llama-server not ready after {timeout:.0f} s:\n{self.log_tail()}")
                self._abort.wait(0.05)
        with self._lock:
            if self._abort.is_set() or self._proc is not proc:
                raise RuntimeError("llama-server start cancelled: stop() was called")
            self.base_url = url
        return url

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the server (and abort a start() in progress). Safe to call any time, repeatedly; the
        server can be started again afterwards."""
        self._abort.set()
        self._cleanup(timeout=timeout)

    def close(self, timeout: float = 10.0) -> None:
        """stop() for good: a start() that has not begun yet (another thread about to call it) is
        refused too, so a stop that comes first is never lost."""
        with self._lock:
            self._closed = True
        self.stop(timeout)

    def _cleanup(self, only: subprocess.Popen | None = None, timeout: float = 10.0) -> None:
        """Kill and reap the current process (only if it is `only`, when given) and close its job."""
        with self._lock:
            if only is not None and self._proc is not only:
                return
            proc, job = self._proc, self._job
            self._proc, self._job, self.base_url = None, None, None
        if proc is None and job is None:
            return
        t0 = time.perf_counter()
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        deadline = time.monotonic() + timeout
        if job is not None:
            _k32.TerminateJobObject(job, 1)  # anything the server started
        if proc is not None:
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                log.warning("llama-server pid %d did not exit within %.0f s", proc.pid, timeout)
        if job is not None:
            # Termination is asynchronous: wait until every process of the job is gone, so the VRAM
            # (and the log file) are free when stop() returns.
            while _active_processes(job) > 0 and time.monotonic() < deadline:
                time.sleep(0.02)
            _k32.CloseHandle(job)
        if proc is not None:
            log.info("llama-server pid %d stopped in %.0f ms (exit code %s)", proc.pid,
                     (time.perf_counter() - t0) * 1000, proc.returncode)

    def __enter__(self) -> "LlamaServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
