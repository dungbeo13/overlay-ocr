"""SearXNG for game lookups: a client for its JSON API and a manager for our own local instance.

Our instance (tools/setup_searxng.py installs it into SEARXNG_HOME, in its own venv) runs SearXNG's built-in
server (``python -m searx.webapp``, werkzeug, threaded): one interpreter (plus the venv launcher and its conhost),
90-105 MB of RAM in all, ready 1.5 s after the spawn (about 4 s the very first time, while Python compiles it;
measured 2026-10-25). It is spawned like llama-server (app/llm/server.py): windowless, inside a kill-on-close Job
Object so it never outlives the app, output to DATA_DIR/logs/searxng.log. It binds 127.0.0.1 only: the bind
address, port, limiter, public-instance, image-proxy and debug settings are forced through SEARXNG_* environment
variables (they override settings.yml), and once it answers, the listening sockets of its process tree are checked
to be loopback-only (otherwise it is stopped). It starts on demand and stops after IDLE_STOP_S without use, on
stop()/close() and, through the job object, when the app exits or crashes.

An instance that already answers at the configured URL (e.g. the Docker one from deploy/searxng/) is reused
instead of starting a second one.

Public instances almost all refuse API clients: format=json is disabled (HTTP 403) or their bot limiter answers
429 to a request that only accepts JSON. We send an honest User-Agent and Accept header and do not try to look like
a browser to get past that, so PUBLIC_INSTANCES only lists instances that answered such a request. Privacy: a public
instance sees the query (a game title) and our IP address; the local instance sends the query to the search
engines itself (they see our IP, no cookies or profile).
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app import __version__
from app.config import LLM_HOME, LOG_DIR, PROJECT_ROOT
from app.llm.server import (CREATE_NO_WINDOW, CREATE_SUSPENDED, _active_processes, _assign_and_resume, _k32,
                            _kill_on_close_job, free_port)

log = logging.getLogger(__name__)

__all__ = ["SEARXNG_HOME", "SearchHit", "SearxngError", "SearxngRefused", "search", "probe", "PUBLIC_INSTANCES",
           "installed", "SearxngService", "STATUS_TEXT", "setup_command"]

SEARXNG_HOME = LLM_HOME / "searxng"
HOST = "127.0.0.1"
DEFAULT_PORT = 8888
IDLE_STOP_S = 600.0  # stop our instance after 10 min without a search
USER_AGENT = f"screen-translator/{__version__} (+https://github.com/dungbeo13/overlay-ocr)"
_PORT_ATTEMPTS = 3

# Public instances that answered GET /search?format=json to an honest API client (our User-Agent, Accept:
# application/json, one request each). Tested 2026-10-25 against the 66 instances of
# https://searx.space/data/instances.json (92 listed) on the normal web with HTTP 200 and >= 80 % monthly uptime:
# none did. 50 answered 429 (bot limiter: no text/html in Accept), 7 answered 403 (JSON disabled, or a web
# firewall), 3 answered 418 (bot blocker) and 6 an HTML page (a bot challenge). Re-test with
# python -m tools.setup_searxng --test-public, and list here only instances that answer with JSON.
PUBLIC_INSTANCES: list[str] = []
_EXTERNAL_RECHECK_S = 30.0  # a reused instance is probed again after this long

STATUS_TEXT = {
    "missing": "Chưa cài SearXNG",
    "off": "Đã cài, đang tắt (tự bật khi cần tra cứu)",
    "starting": "Đang khởi động…",
    "running": "Đang chạy trên máy (chỉ 127.0.0.1)",
    "external": "Dùng SearXNG đang chạy sẵn",
    "error": "Không khởi động được (xem data/logs/searxng.log)",
}


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    content: str = ""
    engine: str = ""


class SearxngError(RuntimeError):
    """A search failed: transport error, HTTP error, or not SearXNG's JSON."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class SearxngRefused(SearxngError):
    """The instance refuses API use: JSON output disabled (403) or rate-limited by its bot limiter (429)."""


def short_path(text: str) -> str:
    """`text` with the user's home folder written as ~ (logs are shared to show what was sent; no user name)."""
    try:
        home = str(Path.home())
    except (RuntimeError, OSError):
        return text
    if not home or len(home) < 4:
        return text
    return re.sub(re.escape(home), "~", str(text), flags=re.IGNORECASE)


# --- client -----------------------------------------------------------------------------------------------------
def _is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _base(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"not an http(s) URL: {url!r}")
    return url


def _accepts(url: str, timeout: float = 0.3) -> bool:
    """Something accepts TCP connections at the URL's host:port. A local listener accepts at once; Windows
    retries a refused localhost connect for about 2 s, so a short timeout means nothing is there."""
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection((parts.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def _client(base_url: str, timeout: float) -> httpx.Client:
    # No proxy for 127.0.0.1: a system proxy must not see (or break) local traffic.
    return httpx.Client(timeout=httpx.Timeout(timeout), trust_env=not _is_loopback(base_url),
                        headers={"User-Agent": USER_AGENT}, follow_redirects=False)


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def search(base_url: str, query: str, *, language: str = "all", timeout: float = 10.0,
           client=None) -> list[SearchHit]:
    """GET <base_url>/search?format=json. Results are deduplicated by URL (http/https only), in SearXNG's order.
    Raises SearxngRefused for 403/429, SearxngError for any other HTTP, transport or format error, and for an
    empty answer in which every engine failed (their errors are in the message). `client` is an httpx.Client
    (or anything with the same get()); by default a new one is used for this call."""
    base = _base(base_url)
    params = {"q": query, "format": "json", "language": language or "all"}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    own = client is None
    c = _client(base, timeout) if own else client
    try:
        r = c.get(base + "/search", params=params, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise SearxngError(f"{base}: {type(e).__name__}: {e}") from e
    finally:
        if own:
            c.close()
    if r.status_code in (403, 429):
        why = "JSON output disabled" if r.status_code == 403 else "rate-limited by its bot limiter"
        raise SearxngRefused(f"{base}: HTTP {r.status_code} ({why})", r.status_code)
    if r.status_code != 200:
        raise SearxngError(f"{base}: HTTP {r.status_code}", r.status_code)
    try:
        data = r.json()
    except ValueError as e:
        ctype = r.headers.get("content-type", "")
        raise SearxngError(f"{base}: answer is not JSON ({ctype or 'no content-type'})", r.status_code) from e
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise SearxngError(f"{base}: JSON without a results list", r.status_code)
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        engine = item.get("engine") or next(iter(item.get("engines") or []), "")
        hits.append(SearchHit(_clean(item.get("title")) or url, url, _clean(item.get("content")), _clean(engine)))
    failed = [e for e in (data.get("unresponsive_engines") or []) if isinstance(e, (list, tuple)) and e]
    if failed:
        msg = ", ".join(f"{e[0]} ({e[1]})" if len(e) > 1 else str(e[0]) for e in failed)
        if not hits:
            raise SearxngError(f"{base}: no engine answered: {msg}", r.status_code)
        log.info("SearXNG %s: engines without an answer for this query: %s", base, msg)
    return hits


def probe(base_url: str, timeout: float = 5.0) -> bool:
    """True when a SearXNG instance answers at `base_url`: /healthz is 200 and /config is SearXNG's JSON. Sends
    no query (whether format=json is enabled shows on the first search: SearxngRefused)."""
    try:
        base = _base(base_url)
    except ValueError:
        return False
    if _is_loopback(base) and not _accepts(base):
        return False  # nothing listens: skip HTTP (a refused localhost connect takes ~2 s on Windows)
    try:
        with _client(base, timeout) as c:
            if c.get(base + "/healthz").status_code != 200:
                return False
            r = c.get(base + "/config", headers={"Accept": "application/json"})
            data = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return False
    return isinstance(data, dict) and "engines" in data and "version" in data


# --- local install ----------------------------------------------------------------------------------------------
def venv_python(home: Path = SEARXNG_HOME) -> Path:
    return home / "venv" / "Scripts" / "python.exe"


def settings_path(home: Path = SEARXNG_HOME) -> Path:
    return home / "settings.yml"


def install_info(home: Path = SEARXNG_HOME) -> dict | None:
    """install.json written by tools/setup_searxng.py as its last step (commit, version, port, ...)."""
    try:
        info = json.loads((home / "install.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def installed(home: Path = SEARXNG_HOME) -> bool:
    """Our native SearXNG is installed completely (venv, patched source, settings, install.json)."""
    info = install_info(home)
    return bool(info and info.get("commit") and venv_python(home).is_file() and settings_path(home).is_file()
                and (home / "src" / "searx" / "webapp.py").is_file())


def setup_command() -> str:
    """The PowerShell command that installs it (for a "copy command" button)."""
    exe = Path(sys.executable)
    console = exe.with_name("python.exe")
    return f'cd "{PROJECT_ROOT}"; & "{console if console.is_file() else exe}" -m tools.setup_searxng'


def port_free(port: int) -> bool:
    """Nothing listens on 127.0.0.1:`port` and it can be bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        if s.connect_ex((HOST, port)) == 0:
            return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((HOST, port))
    except OSError:
        return False
    return True


def child_env(home: Path, port: int) -> dict[str, str]:
    """Environment for the server: ours, minus inherited SEARXNG_* / PYTHON* / venv variables, plus the forced
    local-only settings."""
    drop = ("SEARXNG_", "PYTHON", "__PYVENV_LAUNCHER__", "VIRTUAL_ENV")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(drop)}
    env.update({
        "SEARXNG_SETTINGS_PATH": str(settings_path(home)),
        "SEARXNG_BIND_ADDRESS": HOST,
        "SEARXNG_PORT": str(port),
        "SEARXNG_LIMITER": "false",
        "SEARXNG_PUBLIC_INSTANCE": "false",
        "SEARXNG_IMAGE_PROXY": "false",
        "SEARXNG_DEBUG": "false",  # debug mode would add werkzeug's reloader (a second process)
        "PYTHONUTF8": "1",  # log lines with Japanese / Vietnamese text must not hit cp1252
        "PYTHONUNBUFFERED": "1",
    })
    return env


def listening_addresses(pids: list[int]) -> list[tuple[str, int]]:
    """(ip, port) of the TCP sockets the processes `pids` listen on (psutil; empty if they are gone)."""
    import psutil

    out: list[tuple[str, int]] = []
    for pid in pids:
        try:
            conns = psutil.Process(pid).net_connections(kind="tcp")
        except psutil.Error:
            continue
        out += [(c.laddr.ip, c.laddr.port) for c in conns if c.status == psutil.CONN_LISTEN and c.laddr]
    return out


class _PortTaken(Exception):
    pass


class SearxngService:
    """SearXNG for the lookup: reuses an instance answering at `url`, else (when `managed` and installed) runs our
    own on 127.0.0.1. Thread-safe; ensure_running() blocks while it starts, stop() aborts that.

    `command` replaces ``<venv python> -m searx.webapp`` (tests: a fake server that reads SEARXNG_PORT and
    SEARXNG_BIND_ADDRESS like the real one); it counts as installed."""

    def __init__(self, url: str = "", managed: bool = True, *, home: Path = SEARXNG_HOME,
                 log_path: Path | None = None, idle_stop_s: float = IDLE_STOP_S, command: list[str] | None = None):
        self.url = (url or "").strip().rstrip("/")
        self.managed = bool(managed)
        self.home = Path(home)
        self.log_path = Path(log_path) if log_path is not None else LOG_DIR / "searxng.log"
        self.idle_stop_s = float(idle_stop_s)
        self.command = list(command) if command else None
        self.base_url: str | None = None  # our running instance, or the reused one
        self.last_error = ""  # why the last ensure_running() returned None
        self._failed = False  # the last start of our instance failed: status() "error"
        self.start_s = 0.0  # last successful start: spawn -> /healthz answered
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()  # serialises ensure_running(); stop() never takes it
        self._abort = threading.Event()
        self._closed = False
        self._starting = False
        self._external = False
        self._external_checked = 0.0
        self._proc: subprocess.Popen | None = None
        self._job: int | None = None
        self._idle_stop: threading.Event | None = None
        self._last_used = 0.0

    # --- state ----------------------------------------------------------------------------------------------
    def is_installed(self) -> bool:
        return self.command is not None or installed(self.home)

    def status(self) -> str:
        """"missing", "off", "starting", "running" (ours), "external" (reused) or "error" (last start failed).
        Cheap: no network."""
        with self._lock:
            if self._starting:
                return "starting"
            if self._proc is not None and self._proc.poll() is None and self.base_url:
                return "running"
            if self._external and self.base_url:
                return "external"
            if self._failed:
                return "error"
        if (self.managed and self.is_installed()) or (not self.managed and self.url):
            return "off"
        return "missing"

    def status_text(self) -> str:
        """status() in Vietnamese, for the Settings tab."""
        status = self.status()
        if status == "off" and not self.managed:
            return f"Không có SearXNG trả lời ở {self.url}" if self.last_error else "Dùng SearXNG ở " + self.url
        return STATUS_TEXT[status]

    @property
    def pid(self) -> int | None:
        with self._lock:
            return self._proc.pid if self._proc is not None else None

    def pids(self) -> list[int]:
        """Our server's process tree (the venv launcher and the interpreter it starts)."""
        import psutil

        pid = self.pid
        if pid is None:
            return []
        try:
            root = psutil.Process(pid)
            return [pid] + [c.pid for c in root.children(recursive=True)]
        except psutil.Error:
            return []

    def memory_mb(self) -> float:
        """Resident memory of our server's process tree, MiB."""
        import psutil

        total = 0
        for pid in self.pids():
            try:
                total += psutil.Process(pid).memory_info().rss
            except psutil.Error:
                pass
        return total / 2**20

    def touch(self) -> None:
        """Note a use: the idle timer starts again."""
        with self._lock:
            self._last_used = time.monotonic()

    def log_tail(self, n: int = 30) -> str:
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-n:])

    # --- lifecycle --------------------------------------------------------------------------------------------
    def ensure_running(self, timeout: float = 30.0) -> str | None:
        """Base URL of a SearXNG to query, or None (not installed / not reachable / failed: see last_error)."""
        with self._start_lock:
            with self._lock:
                if self._closed:
                    return None
                now = time.monotonic()
                if self._proc is not None and self._proc.poll() is None and self.base_url:
                    self._last_used = now
                    return self.base_url
                if self._external and self.base_url and now - self._external_checked < _EXTERNAL_RECHECK_S:
                    return self.base_url
                self._abort.clear()
            self._cleanup()  # a process that died since the last start
            if self.url and probe(self.url, timeout=min(5.0, timeout)):
                with self._lock:
                    if not self._external:
                        log.info("reusing the SearXNG instance at %s", self.url)
                    self._external, self.base_url, self.last_error, self._failed = True, self.url, "", False
                    self._external_checked = time.monotonic()
                return self.url
            with self._lock:
                self._external, self.base_url = False, None
            if not self.managed:
                with self._lock:
                    self.last_error = f"no SearXNG answers at {self.url}" if self.url else ""
                return None
            if not self.is_installed():
                return None
            with self._lock:
                self._starting = True
            try:
                url = self._start(timeout)
            except Exception as e:  # noqa: BLE001 - reported through status() / last_error
                if self._abort.is_set():  # stop() / close() during the start: a deliberate stop, not a failure
                    log.info("SearXNG start stopped: %s", e)
                    with self._lock:
                        self.last_error, self._failed = "", False
                    return None
                log.warning("SearXNG did not start: %s", e)
                with self._lock:
                    self.last_error, self._failed = str(e), True
                return None
            finally:
                with self._lock:
                    self._starting = False
            with self._lock:
                self.last_error, self._failed = "", False
            return url

    def search(self, query: str, *, language: str = "all", timeout: float = 10.0) -> list[SearchHit]:
        """ensure_running() + the module's search(); raises SearxngError when no instance is available."""
        base = self.ensure_running()
        if base is None:
            raise SearxngError(self.last_error or "SearXNG is not available")
        try:
            return search(base, query, language=language, timeout=timeout)  # the module-level function
        finally:
            self.touch()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop our instance (and abort a start in progress). A reused instance is not ours: left alone."""
        self._abort.set()
        self._cleanup(timeout=timeout)
        with self._lock:
            if self._external:
                self._external, self.base_url = False, None

    def close(self, timeout: float = 10.0) -> None:
        """stop() for good (app exit): later ensure_running() calls return None."""
        with self._lock:
            self._closed = True
        self.stop(timeout)

    def _preferred_port(self) -> int:
        if self.url and _is_loopback(self.url):
            port = urlsplit(self.url).port
            if port:
                return port
        info = install_info(self.home) or {}
        try:
            return int(info.get("port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            return DEFAULT_PORT

    def _start(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        t0 = time.perf_counter()
        preferred = self._preferred_port()
        for attempt in range(_PORT_ATTEMPTS):
            port = preferred if attempt == 0 and port_free(preferred) else free_port()
            proc = self._spawn(port)
            try:
                url = self._wait_ready(proc, port, deadline, timeout)
                self._check_loopback_only(proc)
            except _PortTaken:
                log.info("port %d was taken before SearXNG could bind it; trying another", port)
                self._cleanup(proc)
                continue
            except BaseException:
                self._cleanup(proc)
                raise
            self.start_s = time.perf_counter() - t0
            stop_evt = threading.Event()
            with self._lock:
                self._idle_stop = stop_evt
                self._last_used = time.monotonic()
            threading.Thread(target=self._idle_watch, args=(proc, stop_evt), name="searxng-idle",
                             daemon=True).start()
            log.info("SearXNG pid %d ready at %s in %.1f s", proc.pid, url, self.start_s)
            return url
        raise RuntimeError(f"SearXNG could not bind a port after {_PORT_ATTEMPTS} attempts:\n{self.log_tail()}")

    def _cmd(self) -> list[str]:
        return list(self.command) if self.command else [str(venv_python(self.home)), "-m", "searx.webapp"]

    def _spawn(self, port: int) -> subprocess.Popen:
        cmd = self._cmd()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._open_log() as log_fh:  # the child keeps its own handle
            job = _kill_on_close_job()
            try:
                proc = subprocess.Popen(cmd, cwd=str(self.home if self.home.is_dir() else self.log_path.parent),
                                        env=child_env(self.home, port), stdin=subprocess.DEVNULL, stdout=log_fh,
                                        stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW | CREATE_SUSPENDED)
            except BaseException:
                _k32.CloseHandle(job)
                raise
        try:
            _assign_and_resume(job, proc.pid)
        except OSError as exc:
            proc.kill()
            proc.wait(5)
            _k32.CloseHandle(job)
            raise RuntimeError(f"could not run SearXNG inside a kill-on-close job object: {exc}") from exc
        with self._lock:
            self._proc, self._job = proc, job
        log.info("SearXNG pid %d starting on %s:%d: %s", proc.pid, HOST, port,
                 short_path(subprocess.list2cmdline(cmd)))
        return proc

    def _open_log(self):
        """A new log for this process; the previous one is kept as <name>.1 (retried briefly: right after a stop
        the old file can still be locked)."""
        old = self.log_path.with_name(self.log_path.name + ".1")
        for attempt in range(10):
            try:
                if self.log_path.is_file() and self.log_path.stat().st_size > 0:
                    self.log_path.replace(old)
                break
            except OSError:
                time.sleep(0.1)
        else:
            try:
                shutil.copyfile(self.log_path, old)
            except OSError:
                pass
        for _ in range(10):
            try:
                return open(self.log_path, "wb")
            except PermissionError:
                time.sleep(0.1)
        self.log_path = self.log_path.with_name(f"{self.log_path.stem}-{time.strftime('%H%M%S')}{self.log_path.suffix}")
        return open(self.log_path, "wb")

    def _wait_ready(self, proc: subprocess.Popen, port: int, deadline: float, timeout: float) -> str:
        url = f"http://{HOST}:{port}"
        with httpx.Client(timeout=httpx.Timeout(2.0), trust_env=False) as client:
            while True:
                if self._abort.is_set():
                    raise RuntimeError("SearXNG start cancelled: stop() was called")
                code = proc.poll()
                if code is not None:
                    tail = self.log_tail()
                    low = tail.lower()
                    if "address already in use" in low or "10048" in low or f"port {port} is in use" in low:
                        raise _PortTaken()
                    raise RuntimeError(f"SearXNG exited with code {code} while starting:\n{tail}")
                try:
                    if client.get(url + "/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError(f"SearXNG not ready after {timeout:.0f} s:\n{self.log_tail()}")
                self._abort.wait(0.1)
        with self._lock:
            if self._abort.is_set() or self._proc is not proc:
                raise RuntimeError("SearXNG start cancelled: stop() was called")
            self.base_url = url
        return url

    def _check_loopback_only(self, proc: subprocess.Popen) -> None:
        """Stop right here if any process of the tree listens on something other than loopback."""
        try:
            addrs = listening_addresses(self.pids())
        except Exception:  # noqa: BLE001 - psutil missing or refused: the env vars still force 127.0.0.1
            log.warning("could not list SearXNG's listening sockets", exc_info=True)
            return
        bad = [f"{ip}:{port}" for ip, port in addrs if not ipaddress.ip_address(ip.split("%")[0]).is_loopback]
        if bad:
            raise RuntimeError(f"SearXNG listens on a non-loopback address ({', '.join(bad)}): stopped")

    def _idle_watch(self, proc: subprocess.Popen, stop_evt: threading.Event) -> None:
        step = min(30.0, max(0.05, self.idle_stop_s / 4))
        while not stop_evt.wait(step):
            with self._lock:
                if self._proc is not proc:
                    return
                idle = time.monotonic() - self._last_used
            if proc.poll() is not None:
                log.warning("SearXNG pid %d exited (code %s)", proc.pid, proc.returncode)
                self._cleanup(proc)
                return
            if idle >= self.idle_stop_s:
                log.info("SearXNG unused for %.0f s: stopping it", idle)
                self._cleanup(proc)
                return

    def _cleanup(self, only: subprocess.Popen | None = None, timeout: float = 10.0) -> None:
        """Kill and reap our process (only if it is `only`, when given) and close its job."""
        with self._lock:
            if only is not None and self._proc is not only:
                return
            proc, job, stop_evt = self._proc, self._job, self._idle_stop
            self._proc, self._job, self._idle_stop = None, None, None
            if not self._external:
                self.base_url = None
        if stop_evt is not None:
            stop_evt.set()
        if proc is None and job is None:
            return
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        deadline = time.monotonic() + timeout
        if job is not None:
            _k32.TerminateJobObject(job, 1)  # the interpreter the venv launcher started
        if proc is not None:
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                log.warning("SearXNG pid %d did not exit within %.0f s", proc.pid, timeout)
        if job is not None:
            while _active_processes(job) > 0 and time.monotonic() < deadline:
                time.sleep(0.02)
            _k32.CloseHandle(job)
        if proc is not None:
            log.info("SearXNG pid %d stopped (exit code %s)", proc.pid, proc.returncode)

    def __enter__(self) -> "SearxngService":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
