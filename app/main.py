"""Application controller: CLI, startup order and the wiring between the translation pipeline
(worker threads) and the GUI (overlay, tray, hotkeys, region selector, anti-cheat guard).

Startup order matters: QApplication first (Qt makes the process Per-Monitor-DPI-aware v2, so
dxcam imported later by the pipeline sees physical pixels), then EcoQoS off and OpenCV
single-threaded, then config, tray, hotkeys and the game guard, and only then the pipeline
threads, so a blocked game that is already in the foreground is never captured.

Pipeline states arrive on the worker thread and are handed to the GUI thread through a queued
Qt signal. Command-line overrides apply to this run only; changes made in the app (region,
source language, anti-cheat mode, LLM mode, glossary profile) are saved to data/config.json.

Contextual LLM tier: LlmService owns the local llama-server (loaded on a background thread at
startup, stopped to free VRAM after llm_release_after_guard_s behind an anti-cheat game and
restarted when the pipeline next needs it). The pipeline gets the active glossary profile's
context with the recent dialogue (ProfileStore + MemoryBank) and reports back the lines the LLM
translated (dialogue memory, auto terms). A foreground game whose exe a profile lists switches to
that profile.

One instance per data directory (app.single_instance): a second launch asks the running one to show
its tray menu and exits. The Settings window (apply_settings applies most changes live) and the
first-run guide are opened from the tray. run_app.pyw starts the app without a console
(main(windowless=True)): startup errors are then also shown in a message box.

Game lookup (app.lookup, app.ui.lookup_dialog): a foreground exe no profile lists whose files say it is a game
(read on a worker thread: version resource, Steam manifests, engine markers; never the process) is offered
ONCE (lookup_state.json) with a tray balloon and a "Tra cứu game" menu entry, never a window over the game.
Nothing is sent before the user agrees in the consent window, and then only the game's title. The app owns
the SearXNG it starts for web hits (stopped on exit) and writes what a lookup found into a profile only when
the user picks "Tạo hồ sơ" / "Thêm vào hồ sơ" (merge_into: the user's entries are never overwritten).
"""
from __future__ import annotations

import argparse
import copy
import ctypes
import dataclasses
import faulthandler
import hashlib
import io
import json
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from typing import Any, TextIO

from PySide6.QtCore import QObject, Qt, QtMsgType, QTimer, QUrl, Signal, qInstallMessageHandler
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QLineEdit, QMessageBox

from app import __version__, qt_gc
from app.capture.base import Region
from app.config import CONFIG_PATH, DATA_DIR, LOG_DIR, PROFILES_DIR, PROJECT_ROOT, Config, Hotkeys
from app.llm.models import model_label
from app.overlay import EXCLUSION_ERROR, OverlayWindow, RegionSelector, apply_to_widget
from app.pipeline import OverlayState, TranslationPipeline, region_from_config, state_to_dict
from app.single_instance import InstanceLock, InstanceServer, instance_key, pipe_exists, send_command
from app.translate.contextual import ContextualTranslator, ScreenItem, ScreenResult, TranslationContext
from app.ui import GameGuard, HotkeyManager, TrayController, format_combo
from app.ui.first_run import should_show as should_show_first_run
from app.ui.game_guard import matches as guard_matches
from app.ui.qt_vi import install as install_vietnamese_qt_texts
from app.winutil import set_app_user_model_id, set_power_throttling

log = logging.getLogger("app")

APP_NAME = "Screen Translator"
VERSION_TEXT = f"{APP_NAME} {__version__}"  # what --version prints (a box in a windowless run)
LANGS = ("ja", "en")
GUARD_MODES = ("pause", "warn", "off")
LLM_MODES = ("off", "refine", "only")
SHUTDOWN_TIMEOUT_S = 5.0
SNAPSHOT_INTERVAL_S = 1.0
LLM_START_TIMEOUT_S = 240.0  # model load from a cold disk
LLM_STOP_TIMEOUT_S = 10.0
LLM_RESTART_S = 5.0  # first automatic restart after a failed / crashed server (doubles)
LLM_MAX_RESTARTS = 3  # automatic restarts in a row; choosing an LLM mode in the tray starts over
LLM_STABLE_S = 60.0  # a server up this long counts as working: the restart count starts over
LLM_IDLE_RELEASE_S = 600.0  # stop the server (free its VRAM) after this long without an LLM job
LLM_IDLE_CHECK_MS = 30_000
MEMORY_ROLES = ("dialog", "subtitle", None)  # screen items whose lines go into the dialogue memory
MEMORY_GAP_S = 180.0  # dialogue before a pause this long is an earlier scene: not sent as history

MSG_LLM_OFF = "LLM: tắt"
MSG_LLM_LOADING = "LLM: đang tải model…"
MSG_LLM_READY = "LLM: sẵn sàng ({model})"
MSG_LLM_NO_MODEL = "LLM: chưa có model"
MSG_LLM_FAILED = "LLM lỗi: {err}"
MSG_LLM_CRASHED = "server đã dừng"
MSG_LLM_RELEASED = "LLM: đã giải phóng VRAM (game anti-cheat)"
MSG_LLM_WAITING = "LLM: chờ game anti-cheat đóng"
MSG_LLM_IDLE = "LLM: đã giải phóng VRAM (không dùng {min:g} phút)"
MSG_PROFILE_SWITCHED = "Đã chuyển hồ sơ"
MSG_ALREADY_RUNNING = "App đang chạy ở khay hệ thống (biểu tượng 文). Bấm vào biểu tượng để mở menu."
MSG_RUNNING = "Screen Translator đang chạy — bấm vào biểu tượng 文 ở khay hệ thống để mở menu."
MSG_NOT_RESPONDING = "Screen Translator đang chạy nhưng không phản hồi."
MSG_NOT_RESPONDING_FIX = ("Đợi một lát rồi mở lại. Nếu vẫn thấy thông báo này, tắt “pythonw.exe” trong Task Manager "
                          "rồi mở lại app.")
MSG_STARTUP_FAILED = "Screen Translator không khởi động được."
MSG_BAD_ARGS = "Shortcut (hoặc lệnh) mở app có tham số không hợp lệ."
MSG_BAD_ARGS_FIX = ("Tạo lại shortcut: bấm đúp “Tạo lại shortcut.cmd” trong thư mục app, hoặc chạy "
                    "“python -m tools.install_shortcut” bằng python của venv.")
DEFAULT_PROFILE = "default"
MSG_NEW_GAME = "Bấm để tra cứu dữ liệu cộng đồng (tên nhân vật, thuật ngữ, bản Việt hóa). Chưa gửi gì lên mạng."
MSG_NEW_GAME_TEMPLATE = "Có sẵn hồ sơ mẫu “{name}” cho game này. Bấm để xem và dùng (không cần lên mạng)."
MSG_SEARXNG_INSTALLED = "Đã cài xong SearXNG: phần tra cứu game giờ tìm được cả trang Việt hóa trên web."
LOOKUP_TOKEN = "lookup:"  # balloon token of a "Game mới" message
FIRST_RUN_DELAY_MS = 800  # the guide opens once the tray icon is up
RESTART_WAIT_S = 30.0  # a restarted instance waits this long for the old one to exit
HANDOVER_WAIT_S = 45.0  # a second launch waits this long for a first one that is still starting (cold disk)
HANDOVER_HUNG_TRIES = 3  # the pipe is there but nobody answers this many times in a row: the app is hung
# Options for this run only that "Khởi động lại app" must not carry over: the restart exists to apply what
# Settings saved (OCR device, capture backend...), and the guide / --exit-after belong to the first run.
RESTART_DROP_OPTIONS = {"--lang": 1, "--device": 1, "--capture": 1, "--llm-mode": 1, "--no-llm": 0, "--llm-model": 1,
                        "--no-guard": 0, "--exit-after": 1, "--first-run": 0, "--wait-for-instance": 1}
_DETACHED_PROCESS, _CREATE_NEW_PROCESS_GROUP = 0x00000008, 0x00000200


# --- command line ---------------------------------------------------------------------------

def parse_region(text: str) -> list[int]:
    """"L,T,W,H" (physical px) -> [l, t, w, h]."""
    try:
        values = [int(v.strip()) for v in text.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(f"region must be L,T,W,H integers, got {text!r}") from None
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
        raise argparse.ArgumentTypeError(f"region must be L,T,W,H with W,H > 0, got {text!r}")
    return values


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app", description="Real-time screen translation overlay (EN/JA -> VI).")
    p.add_argument("--region", type=parse_region, metavar="L,T,W,H",
                   help="region to translate, physical px (use --region=-1920,0,800,600 for negative values); "
                        "this run only")
    p.add_argument("--lang", choices=LANGS, help="source language (this run only)")
    p.add_argument("--device", choices=("cuda", "cpu"), help="OCR device (this run only)")
    p.add_argument("--capture", choices=("auto", "dxcam", "mss", "wgc"),
                   help="capture backend (default auto: dxcam, GDI while dxcam delivers nothing); this run only")
    p.add_argument("--llm-mode", choices=LLM_MODES,
                   help="contextual LLM tier: refine (Google first, then the LLM), only (wait for the LLM), "
                        "off; this run only")
    p.add_argument("--no-llm", action="store_true", help="same as --llm-mode off")
    p.add_argument("--llm-model", metavar="NAME", help="GGUF file name in the models dir (this run only)")
    p.add_argument("--profile", metavar="NAME", help="glossary profile (this run only)")
    p.add_argument("--no-hotkeys", action="store_true", help="do not register global hotkeys")
    p.add_argument("--no-guard", action="store_true", help="do not watch for anti-cheat games (this run only)")
    p.add_argument("--exit-after", type=float, metavar="SECONDS", help="quit automatically after this many seconds")
    p.add_argument("--debug-dump", type=Path, metavar="DIR",
                   help="append every published state to DIR/states.jsonl and every LLM screen result to "
                        "DIR/llm.jsonl, and save overlay snapshots")
    p.add_argument("--log-level", default="INFO", type=str.upper,
                   choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    p.add_argument("--first-run", action="store_true",
                   help="show the first-run guide now (it opens by itself once, not in --exit-after runs)")
    p.add_argument("--wait-for-instance", type=float, default=0.0, metavar="SECONDS",
                   help=argparse.SUPPRESS)  # "Khởi động lại app": wait for the old instance to exit
    p.add_argument("--version", action="version", version=VERSION_TEXT, help="print the version and exit")
    return p


def restart_command(orig_argv: list[str] | None = None, executable: str | None = None) -> list[str]:
    """This process's command line (venv interpreter, -m app / run_app.pyw, options) for a new instance
    that first waits for this one to let go of the single-instance lock. The overrides of Settings fields
    and of this run only (RESTART_DROP_OPTIONS: --capture, --device, --lang, --first-run, --exit-after...) are
    left out, so the new instance uses what is saved."""
    argv = list(sys.orig_argv if orig_argv is None else orig_argv)[1:]
    out: list[str] = []
    skip = 0
    for a in argv:
        if skip:
            skip -= 1
            continue
        name = a.split("=", 1)[0]
        if name in RESTART_DROP_OPTIONS:
            skip = RESTART_DROP_OPTIONS[name] if "=" not in a else 0
            continue
        out.append(a)
    return [executable or sys.executable, *out, "--wait-for-instance", f"{RESTART_WAIT_S:g}"]


def apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    if args.region is not None:
        config.region = list(args.region)
    if args.lang is not None:
        config.src_lang = args.lang
    if args.device is not None:
        config.ocr_device = args.device
    if args.capture is not None:
        config.capture_backend = args.capture
    if args.no_guard:
        config.guard_mode = "off"
    if args.llm_mode is not None:
        config.llm_mode = args.llm_mode
    if args.no_llm:
        config.llm_mode = "off"
    if args.llm_model is not None:
        config.llm_model = args.llm_model
    if args.profile is not None and args.profile.strip():
        config.profile = args.profile.strip()
    return config


# --- logging --------------------------------------------------------------------------------

_QT_LEVELS = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}


def _qt_message_handler(msg_type, _context, message) -> None:
    logging.getLogger("qt").log(_QT_LEVELS.get(msg_type, logging.WARNING), "%s", message)


def setup_logging(level: str, log_dir: Path = LOG_DIR) -> Path:
    """Rotating data/logs/app.log plus stderr (when there is one). Returns the log file path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "app.log"
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:  # pythonw has no console
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "hpack", "comtypes", "dxcam", "PIL", "asyncio"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))
    qInstallMessageHandler(_qt_message_handler)

    def _excepthook(exc_type, exc, tb) -> None:
        logging.getLogger("app").critical("unhandled exception", exc_info=(exc_type, exc, tb))

    def _thread_excepthook(a: threading.ExceptHookArgs) -> None:
        logging.getLogger("app").critical("unhandled exception in thread %s", a.thread.name if a.thread else "?",
                                          exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    return path


# --- debug dump -----------------------------------------------------------------------------

class DebugDump:
    """--debug-dump DIR: states.jsonl (one line per published state), overlay_<seq>.png snapshots and
    llm.jsonl (one line per LLM screen result the app received: speakers, new terms, memory size)."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self._states: TextIO | None = open(self.dir / "states.jsonl", "a", encoding="utf-8")
        self._llm: TextIO | None = None
        self._llm_lock = threading.Lock()  # written on the pipeline worker thread
        self.snapshots = 0

    def write_run_info(self, info: dict) -> None:
        (self.dir / "run.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_state(self, state: OverlayState, recv_ts: float) -> None:
        if self._states is None:
            return
        d = state_to_dict(state)
        d["recv_ts"] = recv_ts  # when the GUI thread received it (the overlay repaints within ~33 ms)
        self._states.write(json.dumps(d, ensure_ascii=False) + "\n")
        self._states.flush()

    def write_screen_result(self, record: dict) -> None:
        """Any thread."""
        with self._llm_lock:
            if self._states is None:
                return  # closed
            if self._llm is None:
                self._llm = open(self.dir / "llm.jsonl", "a", encoding="utf-8")
            self._llm.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._llm.flush()

    def save_snapshot(self, overlay: OverlayWindow, seq: int) -> bool:
        ok = overlay.snapshot().save(str(self.dir / f"overlay_{seq}.png"))
        self.snapshots += int(ok)
        return ok

    def close(self) -> None:
        with self._llm_lock:
            if self._states is not None:
                self._states.close()
                self._states = None
            if self._llm is not None:
                self._llm.close()
                self._llm = None


# --- local LLM server -----------------------------------------------------------------------

def _alive(widget: Any) -> bool:
    """A window we keep a reference to still exists (WA_DeleteOnClose deletes it when closed)."""
    if widget is None:
        return False
    try:
        import shiboken6

        return bool(shiboken6.isValid(widget))
    except Exception:
        return False


def _short_error(exc: BaseException, limit: int = 80) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _default_resolve_model(config: Config) -> Path | None:
    from app.llm.models import resolve_model

    return resolve_model(config)


def _default_translator_factory(base_url: str, model: str) -> ContextualTranslator:
    from app.translate.llm_local import LocalLlmTranslator

    return LocalLlmTranslator(base_url, model)


def llama_children() -> list:
    """llama-server processes that are children of this process (psutil.Process objects)."""
    import psutil

    try:
        children = psutil.Process().children(recursive=True)
    except psutil.Error:
        return []
    out = []
    for c in children:
        try:
            if "llama-server" in c.name().lower():
                out.append(c)
        except psutil.Error:
            pass
    return out


def reap_llama_children(timeout: float = 3.0) -> int:
    """Kill llama-server processes this process started that are still alive (should be none after
    LlmService.stop(); the job object would also kill them at exit). Returns how many were left."""
    import psutil

    left = llama_children()
    for p in left:
        log.warning("llama-server pid %d still running after stop; killing it", p.pid)
        try:
            p.kill()
        except psutil.Error:
            pass
    if left:
        psutil.wait_procs(left, timeout=timeout)
    return len(left)


def profile_version(profile: Any) -> str:
    """Profile.version() without the auto terms the LLM proposed itself: those must not make every
    tier-2 translation on screen and in the cache out of date (and the next job would propose more);
    a user edit (terms, characters, style, confirming an auto term, deleting one: `rejected`) does."""
    user = profile.copy()
    user.terms = [t for t in user.terms if not getattr(t, "auto", False)]
    version = str(user.version())
    rejected = sorted(getattr(profile, "rejected", None) or ())
    if not rejected:
        return version
    return hashlib.sha1(json.dumps([version, rejected], ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


class LlmService:
    """The local llama-server (127.0.0.1 only) behind the contextual tier. Thread-safe.

    start() loads it on a background thread, never the caller's. translator() is the pipeline's
    contextual_factory (translation thread): a LocalLlmTranslator while the server is up, else None;
    it also notices a crashed server and restarts a server that is wanted but not running (released
    behind an anti-cheat game or when idle, crashed, failed), with a growing delay and at most
    LLM_MAX_RESTARTS times in a row (a server counts as working again once it has stayed up
    LLM_STABLE_S). stop() also abandons a load in progress; with wait=False the process is stopped
    on a background thread (the state changes at once). Status texts go to `on_status`, any thread.
    """

    def __init__(self, config: Config, *, on_status: Callable[[str], None] | None = None,
                 resolve_model: Callable[[Config], Path | None] | None = None,
                 server_factory: Callable[[Path], Any] | None = None,
                 translator_factory: Callable[[str, str], ContextualTranslator] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._config = config
        self.on_status = on_status
        self._resolve = resolve_model or _default_resolve_model
        self._server_factory = server_factory or self._default_server_factory
        self._translator_factory = translator_factory or _default_translator_factory
        self._clock = clock
        self._lock = threading.Lock()
        self.wanted = config.llm_mode != "off"  # the app's LLM mode is not "off"
        self.guarded = False  # an anti-cheat game is in front: never load a model then
        self._server: Any = None
        self._base_url: str | None = None
        self._model = ""
        self._generation = 0  # changes whenever the server (base_url) changes
        self._load_gen = 0  # the load allowed to finish; stop() bumps it
        self._loader: threading.Thread | None = None
        self._loader_gen = 0  # the load `_loader` runs
        self._failures = 0
        self._retry_at = 0.0
        self._ready_at = 0.0  # when the current server became ready
        self._last_used = clock()  # last translator() call that returned a translator
        self._permanent: str | None = None  # no model / runtime missing: no automatic retry
        self.status = ""
        self.starts = 0  # diagnostics / tests
        # Translation-thread state.
        self._translator: ContextualTranslator | None = None
        self._translator_gen = -1

    def _default_server_factory(self, model_path: Path) -> Any:
        from app.llm.server import LlamaServer

        return LlamaServer(model_path, ctx=self._config.llm_ctx, gpu_layers=self._config.llm_gpu_layers)

    # --- state ---------------------------------------------------------------------------
    def _set_status(self, text: str) -> None:
        self.status = text
        cb = self.on_status
        if cb is not None:
            try:
                cb(text)
            except Exception:
                log.warning("LLM status callback failed", exc_info=True)

    def is_running(self) -> bool:
        with self._lock:
            return self._base_url is not None

    def is_loading(self) -> bool:
        """A load is in progress (one that stop() abandoned does not count)."""
        with self._lock:
            return self._loading_locked()

    def _loading_locked(self) -> bool:
        return (self._loader is not None and self._loader.is_alive() and self._loader_gen == self._load_gen
                and self._base_url is None)

    @property
    def model_name(self) -> str:
        with self._lock:
            return self._model

    def idle_s(self) -> float:
        """Seconds since the pipeline last got a translator from translator()."""
        return self._clock() - self._last_used

    # --- lifecycle -----------------------------------------------------------------------
    def start(self, *, lazy: bool = False) -> bool:
        """Load the server in the background unless it is up or loading, unwanted or guarded. A lazy
        start (translator()) respects the restart limit and delay; an explicit one resets them."""
        with self._lock:
            if not self.wanted or self.guarded or self._base_url is not None or self._loading_locked():
                return False
            if lazy:
                if self._permanent is not None or self._failures >= LLM_MAX_RESTARTS \
                        or self._clock() < self._retry_at:
                    return False
            else:
                self._failures, self._retry_at, self._permanent = 0, 0.0, None
            self._load_gen += 1
            self._loader_gen = self._load_gen
            self._loader = threading.Thread(target=self._load, args=(self._load_gen,), name="llm-start",
                                            daemon=True)
            self._loader.start()
            self.starts += 1
            self._last_used = self._clock()
        return True

    def restart(self) -> None:
        """Stop the server and load it again (another model was chosen), on a background thread."""
        def run() -> None:
            self.stop(wait=True)
            self.start()

        threading.Thread(target=run, name="llm-restart", daemon=True).start()

    def _load(self, gen: int) -> None:
        with self._lock:
            if gen != self._load_gen:
                return  # stopped before this thread ran
        self._set_status(MSG_LLM_LOADING)
        t0 = time.perf_counter()
        server = None
        try:
            path = self._resolve(self._config)
            if path is None:
                self._failed(gen, MSG_LLM_NO_MODEL, permanent=True)
                log.warning("no LLM model found (llm_model=%r); the contextual tier stays off",
                            self._config.llm_model)
                return
            server = self._server_factory(Path(path))
            with self._lock:
                if gen != self._load_gen:
                    return  # stopped before the process was started
                self._server = server
            log.info("starting llama-server for %s", Path(path).name)
            url = server.start(LLM_START_TIMEOUT_S)  # blocking: model load
        except Exception as exc:
            with self._lock:
                abandoned = gen != self._load_gen
            if abandoned:  # stop() killed the server while it loaded
                log.info("llama-server start abandoned: %s", _short_error(exc))
            else:
                log.warning("llama-server start failed", exc_info=True)
            if server is not None:
                self._stop_server(server)
            with self._lock:
                if self._server is server:
                    self._server = None
            self._failed(gen, MSG_LLM_FAILED.format(err=_short_error(exc)),
                         permanent=isinstance(exc, (ImportError, FileNotFoundError)))
            return
        with self._lock:
            ok = gen == self._load_gen and self._server is server
            if ok:
                self._base_url = str(url)
                self._model = str(getattr(server, "model_name", "") or Path(path).stem)
                self._generation += 1
                self._ready_at = self._clock()  # _failures is reset once it has stayed up (translator())
        if not ok:  # stop() came while loading
            self._stop_server(server)
            return
        log.info("llama-server ready at %s (%s) in %.1f s", url, self._model, time.perf_counter() - t0)
        self._set_status(MSG_LLM_READY.format(model=model_label(self._model)))

    def _failed(self, gen: int, message: str, *, permanent: bool = False) -> None:
        with self._lock:
            if gen != self._load_gen:
                return  # stopped meanwhile: not a failure
            self._failures += 1
            self._retry_at = self._clock() + LLM_RESTART_S * 2 ** (self._failures - 1)
            if permanent:
                self._permanent = message
        self._set_status(message)

    @staticmethod
    def _stop_server(server: Any) -> None:
        """Stop `server` for good: close() also refuses a start() another thread has not begun yet
        (a stop that comes first is never lost); servers without it are stopped."""
        t0 = time.perf_counter()
        try:
            close = getattr(server, "close", None)
            (close if callable(close) else server.stop)()
        except Exception:
            log.warning("llama-server stop failed", exc_info=True)
        log.info("llama-server stopped in %.0f ms", (time.perf_counter() - t0) * 1000)

    def stop(self, *, wait: bool = True, status: str | None = None, timeout: float = LLM_STOP_TIMEOUT_S) -> None:
        """Stop the server (frees its VRAM) and abandon a load in progress."""
        with self._lock:
            self._load_gen += 1
            server, self._server = self._server, None
            self._base_url = None
            self._generation += 1
            loader = self._loader

        def finish() -> None:
            if server is not None:
                self._stop_server(server)
            if status:  # the abandoned loader sets no status of its own
                self._set_status(status)
            if loader is not None and loader is not threading.current_thread() and loader.is_alive():
                loader.join(timeout)

        if wait:
            finish()
        else:
            threading.Thread(target=finish, name="llm-stop", daemon=True).start()

    def translator(self) -> ContextualTranslator | None:
        """The pipeline's contextual_factory (translation thread)."""
        with self._lock:
            url, model, gen, server = self._base_url, self._model, self._generation, self._server
            if url is not None and self._failures and self._clock() - self._ready_at >= LLM_STABLE_S:
                self._failures = 0  # it has stayed up: earlier crashes do not count against restarts
        if url is not None and not self._alive(server):
            self._crashed(server)
            url = None
        if url is None:
            self._translator = None
            self.start(lazy=True)
            return None
        if self._translator is None or self._translator_gen != gen:
            try:
                self._translator = self._translator_factory(url, model)
            except Exception:
                log.warning("contextual translator creation failed", exc_info=True)
                self._translator = None
                return None
            self._translator_gen = gen
        self._last_used = self._clock()
        return self._translator

    @staticmethod
    def _alive(server: Any) -> bool:
        check = getattr(server, "is_running", None)
        if server is None or not callable(check):
            return server is not None
        try:
            return bool(check())
        except Exception:
            return True  # unknown: do not restart on a guess

    def _crashed(self, server: Any) -> None:
        with self._lock:
            if self._server is not server:
                return
            self._server, self._base_url = None, None
            self._generation += 1
            self._failures += 1
            self._retry_at = self._clock() + LLM_RESTART_S * 2 ** (self._failures - 1)
            failures = self._failures
        code = getattr(server, "exit_code", None)
        tail = ""
        try:
            tail = server.log_tail() if callable(getattr(server, "log_tail", None)) else ""
        except Exception:
            pass
        log.warning("llama-server exited unexpectedly (exit code %s; %d in a row%s)%s", code, failures,
                    "; no more automatic restarts" if failures >= LLM_MAX_RESTARTS else "",
                    f"; last lines of its log:\n{tail}" if tail else "")
        self._stop_server(server)  # reaps the process / closes its job object
        self._set_status(MSG_LLM_FAILED.format(err=MSG_LLM_CRASHED))


# --- the application ------------------------------------------------------------------------

class App(QObject):
    """Owns every component and routes user intents (tray, hotkeys, guard) to them. GUI thread."""

    _state_arrived = Signal(object)  # OverlayState, emitted on the pipeline worker thread
    _llm_status_arrived = Signal(str)  # from the LLM loader / stop threads
    _profiles_changed = Signal()  # ProfileStore listener, any thread
    _game_identified = Signal(object, str)  # GameIdentity, exe path: from the identify thread (files only)

    def __init__(self, qapp: QApplication, config: Config, persisted: Config, args: argparse.Namespace, *,
                 llm: LlmService | None = None, profiles: Any = None, memory: Any = None) -> None:
        super().__init__()
        self.qapp = qapp
        self.config = config  # effective settings for this run (CLI overrides applied)
        self._persisted = persisted  # what goes back to config.json
        self.args = args
        self._paused_by_user = False
        self._text_visible = True
        self._shutting_down = False
        self._exclusion_error = False
        self._guarded = False
        self._last_state: OverlayState | None = None
        self._last_status: tuple[str, str] | None = None
        self.states_received = 0

        # Glossary profiles and dialogue memory. The worker thread reads them through _context() and
        # writes through _on_screen_result(); _profile_name is only assigned on the GUI thread.
        self.profiles = profiles if profiles is not None else self._open_profiles()
        self.memory = memory if memory is not None else self._open_memory()
        if config.llm_mode not in LLM_MODES:
            log.warning("unknown llm_mode %r; using off", config.llm_mode)
            config.llm_mode = "off"
        self._profile_name = self._initial_profile((config.profile or DEFAULT_PROFILE).strip() or DEFAULT_PROFILE)
        self._ctx_key = self._profile_key()
        self._editor: Any = None
        self._profiles_changed.connect(self._on_profiles_changed, Qt.ConnectionType.QueuedConnection)
        if self.profiles is not None:
            try:
                self.profiles.add_listener(self._on_profiles_event)
            except Exception:
                log.warning("cannot watch glossary profile changes", exc_info=True)

        self.llm = llm if llm is not None else LlmService(config)
        self.llm.wanted = config.llm_mode != "off"
        self.llm.on_status = self._llm_status_arrived.emit
        self._llm_status_arrived.connect(self._on_llm_status, Qt.ConnectionType.QueuedConnection)
        self._llm_release_timer = QTimer(self)
        self._llm_release_timer.setSingleShot(True)
        self._llm_release_timer.timeout.connect(self._release_llm)
        self.llm_releases = 0  # diagnostics / tests
        self._llm_idle_timer = QTimer(self)
        self._llm_idle_timer.setInterval(LLM_IDLE_CHECK_MS)
        self._llm_idle_timer.timeout.connect(self._check_llm_idle)

        self._dump = DebugDump(args.debug_dump) if args.debug_dump else None
        self._last_snapshot = 0.0
        self._last_snapshot_seq = -1
        self._snapshot_timer = QTimer(self)
        self._snapshot_timer.setSingleShot(True)
        self._snapshot_timer.timeout.connect(self._take_snapshot)

        self._state_arrived.connect(self._on_state, Qt.ConnectionType.QueuedConnection)

        self.overlay = OverlayWindow(config)
        self.overlay.exclusion_changed.connect(self._on_exclusion_changed)
        self.selector = RegionSelector(self)
        self.selector.selected.connect(self._on_region_selected)
        self.selector.cancelled.connect(self._on_selection_cancelled)

        self.tray = TrayController(config, self)
        self.tray.select_region.connect(self.select_region)
        self.tray.toggle_pause.connect(self.toggle_pause)
        self.tray.toggle_overlay.connect(self.toggle_text)
        self.tray.set_lang.connect(self.set_lang)
        self.tray.set_guard_mode.connect(self.set_guard_mode)
        self.tray.open_data_dir.connect(self.open_data_dir)
        self.tray.quit.connect(self.quit)
        self.tray.set_profile.connect(lambda name: self.set_profile(name))
        self.tray.new_profile.connect(self.new_profile)
        self.tray.edit_glossary.connect(self.edit_glossary)
        self.tray.set_llm_mode.connect(self.set_llm_mode)
        self.tray.set_llm_model.connect(self.set_llm_model)
        self.tray.open_settings.connect(self.open_settings)
        self.tray.open_guide.connect(self.show_guide)
        self.tray.lookup_game.connect(self.open_lookup)
        self.tray.lookup_request.connect(lambda: self.open_lookup_request())
        self.tray.import_profile.connect(lambda how: self.import_profile(how))
        self.tray.message_clicked.connect(self._on_message_clicked)
        # Game lookup: nothing is sent before the user agrees for that game (ConsentDialog).
        self._lookup_state_obj: Any = None  # LookupState (CONFIG_PATH's folder: lookup_state.json)
        self._lookup_seen: set[str] = set()  # exe paths (else names) considered this run (identified at most once)
        self._pending_games: dict[str, Any] = {}  # key -> GameIdentity offered and not answered (tray entries)
        self._lookup_windows: list[Any] = []  # open consent / lookup / request / import / install windows
        self._searxng: Any = None  # SearxngService, made on first use; stopped on exit
        self._searxng_lock = threading.Lock()  # lookups ask for it from their worker threads
        self.make_lookup_service: Callable[[], Any] = self._default_lookup_service  # tests replace it
        self._game_identified.connect(self._on_game_identified, Qt.ConnectionType.QueuedConnection)
        self._settings: Any = None  # SettingsDialog while open
        self._guide: Any = None  # FirstRunGuide while open
        self._hotkeys_suspended = False  # a hotkey field in Settings is recording
        self._restart_changed: set[str] = set()  # RESTART_FIELDS saved from Settings during this run

        self.hotkeys: HotkeyManager | None = None
        self.guard: GameGuard | None = None
        self.pipeline = TranslationPipeline(
            config, self._emit_state, contextual_factory=self._contextual_factory,
            context_provider=self._context, on_screen_result=self._on_screen_result)
        qapp.aboutToQuit.connect(self.shutdown)

    @staticmethod
    def _open_profiles() -> Any:
        try:
            from app.profiles import ProfileStore

            return ProfileStore(PROFILES_DIR)
        except Exception:
            log.warning("glossary profiles unavailable; translating without a glossary", exc_info=True)
            return None

    @staticmethod
    def _open_memory() -> Any:
        try:
            from app.translate.memory import MemoryBank

            return MemoryBank()
        except Exception:
            log.warning("dialogue memory unavailable; translating without recent lines", exc_info=True)
            return None

    def _initial_profile(self, name: str) -> str:
        """`name` as the store spells it; "default" (with a warning) when there is no such profile."""
        if self.profiles is None:
            return name
        try:
            canonical = self.profiles.get(name).name
        except Exception:
            log.warning("cannot read glossary profile %r", name, exc_info=True)
            return DEFAULT_PROFILE
        if canonical.casefold() != name.casefold():
            log.warning("glossary profile %r not found; using %r", name, canonical)
        return canonical

    @property
    def profile_name(self) -> str:
        return self._profile_name

    # --- glossary profiles (pipeline callbacks: worker thread) ---------------------------------

    def _profile_key(self) -> str:
        """"<profile>:<version>" of the active profile; a change redoes the tier-2 translations."""
        name = self._profile_name
        if self.profiles is None:
            return f"{name}:"
        try:
            profile = self.profiles.get(name)
            return f"{profile.name}:{profile_version(profile)}"
        except Exception:
            log.warning("cannot read glossary profile %r", name, exc_info=True)
            return f"{name}:?"

    def _context(self) -> TranslationContext:
        """context_provider (worker thread): the active profile's glossary, characters and style plus
        the last llm_history_lines dialogue lines of the current conversation (none from before a
        pause of MEMORY_GAP_S). Its version ignores auto terms (profile_version)."""
        name = self._profile_name
        history: tuple = ()
        if self.memory is not None:
            try:
                history = tuple(self.memory.get(name).recent(max(0, int(self.config.llm_history_lines)),
                                                             max_gap_s=MEMORY_GAP_S))
            except Exception:
                log.warning("dialogue memory read failed", exc_info=True)
        if self.profiles is None:
            return TranslationContext(profile=name, history=history)
        profile = self.profiles.get(name)
        return dataclasses.replace(profile.to_context(history), version=profile_version(profile))

    def _contextual_factory(self) -> ContextualTranslator | None:
        """contextual_factory (translation thread): the local LLM translator while its server is up.
        Asking also (re)starts a wanted server in the background (e.g. after the anti-cheat release)."""
        if self._shutting_down or self.config.llm_mode == "off":
            return None
        return self.llm.translator()

    def _on_screen_result(self, result: ScreenResult, items: tuple[ScreenItem, ...],
                          ctx: TranslationContext | None = None) -> None:
        """on_screen_result (worker thread): remember the dialogue lines the LLM translated (screen
        order, with their speaker) and keep the names / terms it proposes, both in the profile the
        job was made for (`ctx.profile`: a profile switch may have happened since)."""
        name = (ctx.profile if ctx is not None and ctx.profile else "") or self._profile_name
        memory: Any = None
        if self.memory is not None:
            # A speaker label next to the dialogue ("name" role) names the speaker the LLM left open.
            labels = [it.text.strip().rstrip(":：") for it in items if it.role_hint == "name"]
            fallback = labels[0] if len(labels) == 1 else None
            try:
                memory = self.memory.get(name)
                for it in items:
                    text = result.translations.get(it.id)
                    if text and it.role_hint in MEMORY_ROLES:
                        memory.add(result.speakers.get(it.id) or fallback, it.text, text)
            except Exception:
                log.warning("dialogue memory update failed", exc_info=True)
        added = 0
        if self.config.llm_auto_terms and result.new_terms and self.profiles is not None:
            try:
                added = self.profiles.add_terms(name, list(result.new_terms))
            except Exception:
                log.warning("storing the LLM's terms in profile %r failed", name, exc_info=True)
        if self._dump is not None:
            try:
                self._dump.write_screen_result({
                    "ts": time.time(), "profile": name, "model": result.model,
                    "latency_s": round(result.latency_s, 3),
                    "items": [{"id": it.id, "text": it.text, "role": it.role_hint} for it in items],
                    "translations": {str(k): v for k, v in result.translations.items()},
                    "speakers": {str(k): v for k, v in result.speakers.items()},
                    "new_terms": [[t.source, t.target, t.kind] for t in result.new_terms],
                    "auto_terms_added": added, "memory_lines": len(memory) if memory is not None else None,
                })
            except OSError:
                log.warning("debug dump: writing llm.jsonl failed", exc_info=True)

    def _on_profiles_event(self, _name: str) -> None:
        """ProfileStore listener (any thread)."""
        if not self._shutting_down:
            self._profiles_changed.emit()

    def _on_profiles_changed(self) -> None:
        """A profile file changed (editor, auto terms, hand edit): refresh the tray; a new version of
        the active profile redoes its tier-2 translations."""
        if self._shutting_down or self.profiles is None:
            return
        try:
            names = self.profiles.names()
            active = self.profiles.get(self._profile_name).name
        except Exception:
            log.warning("cannot list glossary profiles", exc_info=True)
            return
        if active != self._profile_name:  # deleted or renamed: the store falls back to "default"
            log.info("glossary profile %r is gone; using %r", self._profile_name, active)
            self._set_active_profile(active)
        self.tray.set_profiles(names, self._profile_name)
        if _alive(self._guide):
            self._guide.set_profiles(names, self._profile_name)
        self._update_context_key()

    def _update_context_key(self) -> None:
        key = self._profile_key()
        if key != self._ctx_key:
            log.info("translation context %s -> %s", self._ctx_key, key)
            self._ctx_key = key
            self.pipeline.set_context_version(key)

    def _set_active_profile(self, name: str) -> None:
        self._profile_name = name
        self.config.profile = name
        self._persisted.profile = name
        self._save_config()

    def set_profile(self, name: str, *, reason: str = "tray") -> None:
        """Make `name` the active glossary profile (tray, auto-switch); saved to config.json."""
        if self.profiles is None or self._shutting_down:
            return
        try:
            profile = self.profiles.get(name)
            names = self.profiles.names()
        except Exception:
            log.warning("cannot read glossary profile %r", name, exc_info=True)
            return
        if profile.name.casefold() != str(name).strip().casefold():
            log.warning("glossary profile %r not found; using %r", name, profile.name)
        changed = profile.name != self._profile_name
        if changed:
            log.info("glossary profile -> %s (%s)", profile.name, reason)
            self._set_active_profile(profile.name)
        self.tray.set_profiles(names, self._profile_name)
        if _alive(self._guide):
            self._guide.set_profiles(names, self._profile_name)
        if changed and profile.src_lang in LANGS:
            self.set_lang(profile.src_lang)
        self._update_context_key()
        if changed and reason.startswith("auto"):
            lang = {"ja": "tiếng Nhật", "en": "tiếng Anh"}.get(self.config.src_lang, self.config.src_lang)
            self.tray.show_message(MSG_PROFILE_SWITCHED, f"Hồ sơ: {profile.name} ({lang}) — {reason[5:].strip()}")

    def _on_foreground_changed(self, exe: str) -> None:
        """GameGuard: a new foreground exe; a profile that lists it becomes active. One that no profile lists may
        be a new game: a lookup is offered (once per game, see _consider_new_game)."""
        if self.profiles is None or self._shutting_down or not exe:
            return
        path = str(getattr(self.guard, "foreground_path", "") or "")
        path = path if os.path.basename(path).casefold() == exe.casefold() else ""
        try:
            # The full path (local only) lets a "<game folder>/SiglusEngine.exe" entry match its own game only.
            name = self.profiles.match_process(path or exe)
        except Exception:
            log.warning("profile lookup for %s failed", exe, exc_info=True)
            return
        if name and name != self._profile_name:
            log.info("foreground %s belongs to glossary profile %s: switching", exe, name)
            self.set_profile(name, reason=f"auto: {exe}")
        elif not name:
            self._consider_new_game(exe, path)

    def _ask_profile_name(self) -> str | None:
        """Modal name prompt (capture-excluded like every window of ours); None when cancelled."""
        dlg = QInputDialog()
        dlg.setWindowTitle("Tạo hồ sơ mới")
        dlg.setLabelText("Tên hồ sơ (ví dụ: tên game):")
        dlg.setInputMode(QInputDialog.InputMode.TextInput)
        dlg.setTextEchoMode(QLineEdit.EchoMode.Normal)
        dlg.setOkButtonText("Tạo")
        dlg.setCancelButtonText("Hủy")
        exclusion = apply_to_widget(dlg)  # noqa: F841 - must live as long as the dialog
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        text = dlg.textValue().strip()
        dlg.deleteLater()
        return text if ok and text else None

    def new_profile(self) -> None:
        if self.profiles is None or self._shutting_down:
            return
        name = self._ask_profile_name()
        if not name:
            return
        try:
            profile = self.profiles.create(name)
        except (OSError, ValueError) as exc:
            log.warning("cannot create glossary profile %r", name, exc_info=True)
            self.tray.show_message("Không tạo được hồ sơ", _short_error(exc), error=True)
            return
        log.info("glossary profile %s created", profile.name)
        self.set_profile(profile.name, reason="new")
        self.edit_glossary()
        if self.config.lookup_ask_new_games and not profile.characters and not profile.terms:
            from app.lookup.game_id import identity_from_title

            # Offer to fill it from community data: the consent window (nothing is sent before "Tra cứu").
            self.open_lookup(identity=identity_from_title(profile.name), target=profile.name)

    def edit_glossary(self) -> None:
        """Open the glossary editor for the active profile, non-modal (translation keeps running)."""
        if self.profiles is None or self._shutting_down:
            return
        editor = self._editor
        if editor is not None:
            try:
                if editor.isVisible():
                    editor.raise_()
                    editor.activateWindow()
                    return
            except RuntimeError:  # already deleted
                pass
        from app.ui.glossary_editor import GlossaryEditor

        editor = GlossaryEditor(self.profiles, self._profile_name)
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        editor.saved.connect(lambda _name: self._on_profiles_changed())
        editor.finished.connect(lambda _result, e=editor: self._on_editor_closed(e))
        self._editor = editor
        editor.show()
        editor.raise_()
        editor.activateWindow()
        log.info("glossary editor opened for %s", self._profile_name)

    def _on_editor_closed(self, editor: Any) -> None:
        if self._editor is editor:
            self._editor = None

    # --- game lookup (app.lookup) ------------------------------------------------------------------

    def lookup_state(self) -> Any:
        """The "asked once per game" state, next to config.json (DATA_DIR/lookup_state.json)."""
        if self._lookup_state_obj is None:
            from app.lookup.state import LookupState

            self._lookup_state_obj = LookupState(CONFIG_PATH.parent / "lookup_state.json")
        return self._lookup_state_obj

    def _consider_new_game(self, exe: str, path: str = "") -> None:
        """A foreground exe that no profile lists. Unless it is never a game (shell, browser, IDE, an anti-cheat
        game the guard pauses for…), its FILES are read on a worker thread (app.lookup.game_id.identify: version
        resource, Steam manifests, folder, engine markers; never the process) and _on_game_identified decides."""
        if self._shutting_down or not self.config.lookup_ask_new_games:
            return
        name = os.path.basename(exe).casefold()
        seen = os.path.normcase(path) if path else name  # two RPG Maker games both run Game.exe
        if not name or seen in self._lookup_seen:
            return
        self._lookup_seen.add(seen)
        from app.ui.lookup_dialog import is_non_game

        if is_non_game(exe, path):
            return
        if (self.config.guard_mode or "pause") == "pause" and guard_matches(exe, self.config.blocked_processes):
            return  # an anti-cheat game: the app does not translate while it is in front
        threading.Thread(target=self._identify_game, args=(exe, path), name="identify-game", daemon=True).start()

    def _identify_game(self, exe: str, path: str) -> None:
        """Worker thread: files only."""
        try:
            from app.lookup.game_id import identify
            from app.ui.lookup_dialog import looks_like_new_game

            ident = identify(path or exe)
            game = looks_like_new_game(ident, path)
        except Exception:
            log.warning("identifying %s failed", exe, exc_info=True)
            return
        if not game:
            log.debug("foreground %s does not look like a game (%s)", exe, ident.hints or "no markers")
            return
        if not self._shutting_down:
            self._game_identified.emit(ident, path)

    def _on_game_identified(self, ident: Any, _path: str) -> None:
        """A new game is in front: ONE tray balloon and a "Tra cứu game" entry (never a window over the game);
        remembered as "offered", so it is offered once per game. Windows hides balloons while a full-screen game
        runs: until the user answers, the entry is listed again after a restart (restore_pending_games)."""
        if self._shutting_down or not self.config.lookup_ask_new_games:
            return
        try:
            if self.profiles is not None and self.profiles.match_process(ident.exe_path or ident.exe_name):
                return  # a profile for it appeared meanwhile
            key = ident.key
            state = self.lookup_state()
            if key in self._pending_games or not state.should_ask(key):
                return
            from app.lookup.templates import find_template

            template = find_template(ident)  # local files: profiles/templates/*.json
            state.mark(key, "offered", title=ident.title, exe=ident.exe_name)
        except Exception:
            log.warning("new-game check for %s failed", getattr(ident, "exe_name", "?"), exc_info=True)
            return
        self._pending_games[key] = ident
        self._refresh_pending_games()
        text = MSG_NEW_GAME_TEMPLATE.format(name=template.profile.name) if template is not None else MSG_NEW_GAME
        self.tray.show_message(f"Game mới: {ident.title}", text, msecs=10000, token=LOOKUP_TOKEN + key)
        log.info("new game in front: %s (%s; %s): lookup offered in the tray, nothing sent", ident.title,
                 ident.exe_name, ", ".join(ident.hints) or "-")

    def restore_pending_games(self) -> int:
        """At startup: the games offered in an earlier run and never answered (their balloon may have been hidden
        behind a full-screen game) are listed again under "Tra cứu game", without a balloon. Files only."""
        if not self.config.lookup_ask_new_games:
            return 0
        try:
            from app.lookup.game_id import identity_from_key

            rows = self.lookup_state().offered()
        except Exception:
            log.warning("cannot read the games offered before", exc_info=True)
            return 0
        added = 0
        for key, st in rows:
            ident = identity_from_key(key, st.title, st.exe)
            if not ident.titles or key in self._pending_games:
                continue
            try:
                if self.profiles is not None and ident.exe_name and not ident.generic_exe \
                        and self.profiles.match_process(ident.exe_name):
                    continue  # it has a profile now
            except Exception:
                pass
            self._pending_games[key] = ident
            added += 1
        if added:
            self._refresh_pending_games()
            log.info("game lookup: %d game(s) offered before are listed again in the tray", added)
        return added

    def _refresh_pending_games(self) -> None:
        self.tray.set_pending_games([(k, i.title) for k, i in self._pending_games.items()])

    def _drop_pending(self, key: str) -> None:
        if self._pending_games.pop(key, None) is not None:
            self._refresh_pending_games()

    def _on_message_clicked(self, token: str) -> None:
        if token.startswith(LOOKUP_TOKEN) and not self._shutting_down:
            self.open_lookup(token[len(LOOKUP_TOKEN):])

    def _current_game(self) -> Any:
        """The game a manual "Tra cứu game…" is about: the newest one offered, else the last foreground exe when its
        files say it is a game, else the active profile's name (else an empty identity: the user types a name)."""
        from app.lookup.game_id import identify, identity_from_title
        from app.lookup.types import GameIdentity
        from app.ui.lookup_dialog import is_non_game, looks_like_new_game

        if self._pending_games:
            return list(self._pending_games.values())[-1]
        path = str(getattr(self.guard, "foreground_path", "") or "")
        if path and not is_non_game(os.path.basename(path), path):
            try:
                ident = identify(path)
                if looks_like_new_game(ident, path):
                    return ident
            except Exception:
                log.debug("identify %s failed", path, exc_info=True)
        if self._profile_name.casefold() != DEFAULT_PROFILE:
            return identity_from_title(self._profile_name)
        return GameIdentity()

    def _web_target(self) -> str:
        """Who receives the title when the "web" source runs ("" = no SearXNG: the source is skipped). In the
        order the lookup tries them (SearxngService.ensure_running, web.resolve_base_url): an instance answering at
        searxng_url, else our own on 127.0.0.1, else (only when nothing is set up) a public one."""
        try:
            from app.lookup import searxng
        except Exception:
            return ""
        url = (self.config.searxng_url or "").strip()
        local = self.config.searxng_managed and searxng.installed()
        engines = "SearXNG hỏi tiếp Google, Bing, DuckDuckGo… bằng tên game"
        if url and not searxng._is_loopback(url):
            return f"SearXNG ở {url}" + ("; nếu máy đó không trả lời: SearXNG trên máy này (127.0.0.1)" if local else "") \
                + f"; {engines}"
        if local or url:
            return f"SearXNG trên máy này ({url or '127.0.0.1'}); {engines}"
        if self.config.searxng_public_fallback and searxng.PUBLIC_INSTANCES:
            return "một máy SearXNG công cộng (máy đó thấy tên game và địa chỉ IP của bạn)"
        return ""

    def _lookup_sources(self) -> list[str]:
        from app.lookup.types import SOURCES

        sources = [s for s in (self.config.lookup_sources or []) if s in SOURCES]
        return [s for s in sources if s != "web" or self._web_target()]

    def searxng_service(self) -> Any:
        """The SearxngService lookups use (made on first use, from any thread: one service, so one SearXNG; the app
        stops it on exit and on a settings change)."""
        with self._searxng_lock:
            if self._searxng is None and not self._shutting_down:
                try:
                    from app.lookup.searxng import SearxngService

                    self._searxng = SearxngService(self.config.searxng_url, self.config.searxng_managed)
                except Exception:
                    log.warning("SearXNG is not available", exc_info=True)
            return self._searxng

    def _reset_searxng(self) -> None:
        """SearXNG settings changed (or it was reinstalled): stop the old service in the background."""
        with self._searxng_lock:
            old, self._searxng = self._searxng, None
        if old is not None:
            threading.Thread(target=old.close, name="searxng-stop", daemon=True).start()

    def _stop_searxng_now(self) -> None:
        if self._searxng is not None:
            self._searxng.stop()

    def searxng_status(self) -> tuple[str, bool]:
        """(Vietnamese status, installed) for the Settings tab; no network."""
        from app.lookup.searxng import SearxngService

        svc = self._searxng or SearxngService(self.config.searxng_url, self.config.searxng_managed)
        return svc.status_text(), svc.is_installed()

    def _default_lookup_service(self) -> Any:
        from app.lookup.service import LookupService

        return LookupService.from_config(self.config, searxng_service=self.searxng_service(),
                                         cache_dir=CONFIG_PATH.parent / "lookup_cache")

    def _existing_profile(self, name: str) -> Any:
        if self.profiles is None or not name:
            return None
        try:
            return self.profiles.get(name) if self.profiles.exists(name) else None
        except Exception:
            return None

    def _show_lookup_window(self, dlg: Any) -> Any:
        """Track a non-modal lookup window (deleted when closed; closed at exit). Opened only by a user click."""
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._lookup_windows.append(dlg)
        dlg.finished.connect(lambda _result, d=dlg: self._forget_lookup_window(d))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def _forget_lookup_window(self, dlg: Any) -> None:
        """Dropped after the `finished` emission returns (not inside it): the last reference deletes the window."""
        def drop() -> None:
            self._lookup_windows = [w for w in self._lookup_windows if w is not dlg]
        QTimer.singleShot(0, self, drop)

    def lookup_windows(self) -> list[Any]:
        """The lookup windows open now."""
        return [w for w in self._lookup_windows if _alive(w) and w.isVisible()]

    def open_lookup(self, key: str = "", *, identity: Any = None, target: str = "") -> Any:
        """Tray "Game mới: X…" / its balloon (`key`), "Tra cứu game…" (no key: the current game or a typed name),
        or the offer after "Tạo hồ sơ mới" (`identity`, `target`). A shipped template (Blue Archive) is shown
        first, without network; otherwise the consent window asks before anything is sent."""
        if self._shutting_down:
            return None
        from app.lookup.templates import find_template
        from app.ui.lookup_dialog import ConsentDialog, LookupDialog

        ident = identity if identity is not None else self._pending_games.get(key) if key else None
        manual = ident is None or identity is not None
        if ident is None:
            ident = self._current_game()
        if ident.exe_name:  # the same game asked twice (a second click on the balloon): its window comes forward
            for w in self.lookup_windows():
                if isinstance(w, (ConsentDialog, LookupDialog)) and w.identity.key == ident.key:
                    w.bring_to_front()
                    return w
        try:
            template = find_template(ident) if (ident.titles or ident.exe_name) else None
        except Exception:
            log.warning("template check failed", exc_info=True)
            template = None
        if template is not None:
            log.info("game lookup: template %s matches %s (no network)", template.path.name, ident.title)
            return self._start_lookup(ident, "", target=target, sources=[])
        dlg = ConsentDialog(ident, list(self.config.lookup_sources or []), web_target=self._web_target(), manual=manual)
        offered = key if key in self._pending_games else ""
        if offered:  # the user has seen the question now: no longer listed again after a restart
            try:
                self.lookup_state().mark(offered, "asked", title=ident.title, exe=ident.exe_name)
            except Exception:
                log.warning("lookup state not updated", exc_info=True)
        dlg.lookup.connect(lambda title, i=ident, t=target: self._start_lookup(i, title, target=t))
        dlg.declined.connect(lambda never, k=offered: self._on_lookup_declined(k, never))
        dlg.request_instead.connect(lambda title, i=ident: self.open_lookup_request(i, title))
        log.info("game lookup: asking before sending %s (%s)", ident.query_titles() or "a typed title",
                 ident.exe_name or "manual")
        return self._show_lookup_window(dlg)

    def _on_lookup_declined(self, key: str, never: bool) -> None:
        log.info("game lookup declined%s for %s", " (never ask again)" if never else "", key or "a manual lookup")
        if key and never:
            ident = self._pending_games.get(key)
            self.lookup_state().mark(key, "declined", title=getattr(ident, "title", ""),
                                     exe=getattr(ident, "exe_name", ""))
            self._drop_pending(key)

    def _start_lookup(self, ident: Any, title: str = "", *, target: str = "", sources: list[str] | None = None) -> Any:
        """The lookup window for `ident` (with the title the user confirmed); the run starts at once."""
        from functools import partial

        from app.ui.lookup_dialog import LookupDialog, with_title

        ident = with_title(ident, title)  # exactly the titles the consent window showed
        key = ident.key if ident.exe_name else ""
        if key and key in self._pending_games:
            try:  # a template opened straight away (no consent window): the user has answered for this game
                st = self.lookup_state().get(key)
                if st is not None and st.state == "offered":
                    self.lookup_state().mark(key, "asked", title=ident.title, exe=ident.exe_name)
            except Exception:
                log.warning("lookup state not updated", exc_info=True)
        dlg = LookupDialog(ident, make_service=self.make_lookup_service,
                           sources=self._lookup_sources() if sources is None else sources,
                           apply=partial(self._apply_lookup_draft, key=key, exe=ident.exe_name, title=ident.title,
                                         folder=ident.folder_name if ident.generic_exe else ""),
                           exists=self._existing_profile, active_profile=self._profile_name,
                           web_target=self._web_target(), profile_name=target)
        dlg.report_ready.connect(lambda report, k=key: self._on_lookup_report(k, report))
        dlg.request_claude.connect(lambda game: self.open_lookup_request(game))
        dlg.import_requested.connect(lambda i=ident: self.import_profile("file", identity=i))
        dlg.edit_requested.connect(self._edit_profile)
        self._show_lookup_window(dlg)
        dlg.start()
        return dlg

    def _on_lookup_report(self, key: str, report: Any) -> None:
        """Nothing found: remembered ("empty", or "failed" when every source failed: asked again in a week) and
        the app goes on as before. Found: "done" once the user saves it (_apply_lookup_draft)."""
        if not key or report.cancelled or report.found_anything:
            return
        failed = [s for s in report.statuses if s.state in ("error", "timeout")]
        answered = [s for s in report.statuses if s.state in ("ok", "empty")]
        ident = report.identity
        self.lookup_state().mark(key, "failed" if failed and not answered else "empty",
                                 title=getattr(ident, "title", ""), exe=getattr(ident, "exe_name", ""))
        self._drop_pending(key)

    def _apply_lookup_draft(self, draft: Any, name: str, *, key: str = "", exe: str = "", title: str = "",
                            folder: str = "") -> str:
        """Save the checked part of a lookup / import into profile `name` (created, or merged into without touching
        the user's entries), make it active and add the game's exe to its processes. An exe every game of an engine
        shares (Game.exe, SiglusEngine.exe) is added with its game `folder` ("Summer Pockets/SiglusEngine.exe"), or
        not at all. Never into the default profile (every game without a profile uses it). Returns the message."""
        from app.lookup.game_id import GENERIC_EXES
        from app.lookup.profile_builder import merge_into
        from app.profiles import Profile, exe_matches

        if self.profiles is None or self._shutting_down:
            raise ValueError("hồ sơ không dùng được lúc này")
        name = " ".join((name or "").split())
        if not name:
            raise ValueError("chưa có tên hồ sơ")
        if name.casefold() == DEFAULT_PROFILE:
            raise ValueError("không thêm vào hồ sơ Mặc định (mọi game chưa có hồ sơ đều dùng nó): hãy đặt tên riêng "
                             "cho game này")
        incoming = draft.copy()
        exe = os.path.basename(exe or "")
        entry = exe
        if exe.casefold() in GENERIC_EXES:
            entry = f"{folder}/{exe}" if folder and "/" not in folder and "\\" not in folder else ""
        incoming.processes = [p for p in incoming.processes if p.casefold() not in GENERIC_EXES]
        if entry and not any(p.casefold() == entry.casefold() or (entry == exe and exe_matches(exe, p))
                             for p in incoming.processes):
            incoming.processes.append(entry)
        existed = self.profiles.exists(name)
        base = self.profiles.get(name) if existed else Profile(name)
        merged, stats = merge_into(base, incoming, new=not existed)
        merged.name = base.name if existed else name
        self.profiles.save(merged)
        log.info("game lookup: %s profile %s (+%d characters, +%d terms, %d kept; processes %s)",
                 "merged into" if existed else "created", merged.name, len(stats.added_characters),
                 len(stats.added_terms), len(stats.kept_characters) + len(stats.kept_terms), merged.processes)
        if key:
            self.lookup_state().mark(key, "done", title=title or incoming.name, exe=exe, profile=merged.name)
            self._drop_pending(key)
        self.set_profile(merged.name, reason="lookup")
        msg = (f"{'Đã thêm vào' if existed else 'Đã tạo'} hồ sơ “{merged.name}”: {len(stats.added_characters)} nhân vật, "
               f"{len(stats.added_terms)} thuật ngữ mới")
        kept = len(stats.kept_characters) + len(stats.kept_terms)
        if kept:
            msg += f"; {kept} mục đã có được giữ nguyên"
        auto = bool(entry) and any(p.casefold() == entry.casefold() or exe_matches(entry, p) for p in merged.processes)
        return msg + ". Hồ sơ này đang được dùng" + (f" và tự bật khi bạn mở {exe}." if auto else ".")

    def _edit_profile(self, name: str) -> None:
        if name:
            self.set_profile(name, reason="lookup")
        self.edit_glossary()

    def open_lookup_request(self, game: Any = None, title: str = "") -> Any:
        """"Tạo yêu cầu tra cứu cho Claude…": the prompt (claude_request.build_request) to copy or save; no network."""
        if self._shutting_down:
            return None
        from app.lookup.claude_request import build_request
        from app.lookup.types import GameIdentity, LookupReport
        from app.ui.lookup_dialog import RequestDialog, with_title

        if game is None:
            game = self._current_game()
        if isinstance(game, GameIdentity):
            game = with_title(game, title)
        profile = None
        ident = game.identity if isinstance(game, LookupReport) else game
        name = (game.title or game.query) if isinstance(game, LookupReport) else (ident.title if ident else "")
        if isinstance(game, LookupReport) and game.found_anything and game.confidence == "high":
            from app.lookup.profile_builder import build_profile

            try:
                profile = build_profile(game)
            except Exception:
                log.warning("draft for the request failed", exc_info=True)
        elif self._profile_name.casefold() != DEFAULT_PROFILE:
            # The active profile only when it is this game's: another game's glossary is no "what the app has".
            active = self._existing_profile(self._profile_name)
            if active is not None and self._profile_is_for(active, ident, name):
                profile = active
        text = build_request(game if (isinstance(game, LookupReport) or name) else "(tên game)", profile=profile)
        dlg = RequestDialog(text, title=name)
        dlg.import_requested.connect(lambda i=ident: self.import_profile("paste", identity=i))
        log.info("lookup request for %s shown (nothing sent)", name or "?")
        return self._show_lookup_window(dlg)

    @staticmethod
    def _profile_is_for(profile: Any, ident: Any, title: str) -> bool:
        """`profile` belongs to the game `ident` / `title`: it lists the game's exe, or has its name."""
        from app.lookup.names import norm_title
        from app.profiles import exe_matches

        exe = str(getattr(ident, "exe_path", "") or getattr(ident, "exe_name", "") or "")
        if exe and any(exe_matches(exe, p) for p in profile.processes):
            return True
        titles = [title] + list(getattr(ident, "titles", []) or [])
        return any(t and norm_title(t) == norm_title(profile.name) for t in titles)

    def import_profile(self, how: str = "paste", *, identity: Any = None) -> Any:
        """"Nhập hồ sơ từ file…" / "Dán hồ sơ từ clipboard": preview, then create or merge (_apply_lookup_draft).
        Only an import started from a game's own window (`identity`) is bound to that game (its exe, its "done"
        state); from the tray or Settings it is just a profile."""
        if self._shutting_down or self.profiles is None:
            return None
        from functools import partial

        from app.ui.lookup_dialog import ImportDialog

        ident = identity
        exe = getattr(ident, "exe_name", "") or ""
        key = ident.key if ident is not None and exe else ""
        folder = ident.folder_name if ident is not None and ident.generic_exe else ""
        dlg = ImportDialog(apply=partial(self._apply_lookup_draft, key=key, exe=exe, title=getattr(ident, "title", ""),
                                         folder=folder),
                           exists=self._existing_profile, active_profile=self._profile_name,
                           fallback_name=getattr(ident, "title", "") or "", exe_name=exe)
        dlg.edit_requested.connect(self._edit_profile)
        self._show_lookup_window(dlg)
        if how == "file":
            dlg.choose_file()
        elif how == "clipboard":
            dlg.paste_clipboard()
        return dlg

    def open_searxng_install(self) -> Any:
        """Settings "Cài SearXNG trên máy…": tools/setup_searxng.py in the background, its output shown live."""
        if self._shutting_down:
            return None
        from app.ui.lookup_dialog import SearxngInstallDialog

        for w in self.lookup_windows():
            if isinstance(w, SearxngInstallDialog):
                w.bring_to_front()
                return w
        dlg = SearxngInstallDialog(before_start=self._stop_searxng_now)
        dlg.install_finished.connect(self._on_searxng_installed)
        return self._show_lookup_window(dlg)

    def _on_searxng_installed(self, ok: bool, port: int) -> None:
        if not ok or self._shutting_down:
            return
        from app.lookup.searxng import _is_loopback

        url = f"http://127.0.0.1:{int(port)}"
        current = (self._persisted.searxng_url or "").strip()
        if not current or _is_loopback(current):  # a URL the user set to another machine is kept
            for cfg in (self.config, self._persisted):
                cfg.searxng_url, cfg.searxng_managed = url, True
            self._save_config()
            self._sync_settings("searxng_url", url)
            self._sync_settings("searxng_managed", True)
            log.info("SearXNG installed: searxng_url -> %s", url)
        self._reset_searxng()
        if _alive(self._settings):
            self._settings.refresh_searxng_status()
        self.tray.show_message(APP_NAME, MSG_SEARXNG_INSTALLED)

    def forget_lookup_games(self) -> None:
        """Settings "Quên các game đã hỏi": every game without a profile is offered once more."""
        try:
            self.lookup_state().forget(None)
        except Exception:
            log.warning("cannot reset the lookup state", exc_info=True)
        self._lookup_seen.clear()
        self._pending_games.clear()
        self._refresh_pending_games()
        log.info("game lookup: the games asked about are forgotten")
        if _alive(self._settings):
            self._settings.set_lookup_info(0)

    def _lookup_games_count(self) -> int | None:
        try:
            return len(self.lookup_state().items())
        except Exception:
            return None

    # --- LLM ---------------------------------------------------------------------------------

    def _on_llm_status(self, text: str) -> None:
        if self._shutting_down:
            return
        log.info("%s", text)
        self.tray.set_llm_status(text)
        if _alive(self._settings):
            self._settings.set_llm_status(text)
        if _alive(self._guide):
            self._guide.set_llm_status(text)

    def set_llm_mode(self, mode: str) -> None:
        """off | refine | only (tray). Choosing a mode that is on also restarts a failed server."""
        if mode not in LLM_MODES:
            log.warning("unknown LLM mode %r", mode)
            return
        self.tray.set_llm_mode_checked(mode)
        if self._shutting_down:
            return
        if _alive(self._guide):
            self._guide.set_llm_mode(mode)
        changed = mode != self.config.llm_mode
        if changed:
            log.info("LLM mode -> %s", mode)
            self.config.llm_mode = mode
            self._persisted.llm_mode = mode
            self._save_config()
            self.pipeline.set_llm_mode(mode)
        self._sync_settings("llm_mode", mode)
        self.llm.wanted = mode != "off"
        if mode == "off":
            self._llm_release_timer.stop()
            if changed:
                self.llm.stop(wait=False, status=MSG_LLM_OFF)
        elif self._guarded:
            self.tray.set_llm_status(MSG_LLM_WAITING)
        else:
            self.llm.start()

    def set_llm_model(self, kind: str) -> None:
        """"auto" | "light" | "quality" (tray): saved as config.llm_model ("" for auto); a running or
        loading server is reloaded with the new model."""
        if kind not in ("auto", "light", "quality"):
            log.warning("unknown LLM model choice %r", kind)
            return
        value = "" if kind == "auto" else kind
        self.tray.set_llm_model_checked(value)
        if self._shutting_down or value == (self.config.llm_model or ""):
            return
        log.info("LLM model -> %s", kind)
        self.config.llm_model = value
        self._persisted.llm_model = value
        self._save_config()
        self._sync_settings("llm_model", value)
        if self.llm.is_running() or self.llm.is_loading():
            self.llm.restart()

    def _sync_settings(self, name: str, value: Any) -> None:
        """A setting changed outside the Settings window (tray, hotkey, profile switch, the guide): an open
        window shows it, so its Lưu does not put the old value back."""
        if _alive(self._settings):
            try:
                self._settings.sync_field(name, copy.deepcopy(value))
            except Exception:
                log.warning("cannot update the settings window", exc_info=True)

    def _check_llm_idle(self) -> None:
        """Free the model's VRAM after LLM_IDLE_RELEASE_S without an LLM job (a game in front that is
        not on the anti-cheat list, the user reading); it loads again when text needs it."""
        if self._shutting_down or not self.llm.is_running() or self.llm.idle_s() < LLM_IDLE_RELEASE_S:
            return
        log.info("releasing the LLM server: no LLM job for %.0f s", self.llm.idle_s())
        self.llm.stop(wait=False, status=MSG_LLM_IDLE.format(min=LLM_IDLE_RELEASE_S / 60))

    def _schedule_llm_release(self) -> None:
        delay = float(self.config.llm_release_after_guard_s)
        if delay < 0 or not (self.llm.is_running() or self.llm.is_loading()):
            return
        self._llm_release_timer.start(max(0, int(delay * 1000)))

    def _release_llm(self) -> None:
        """The anti-cheat guard has been active llm_release_after_guard_s: stop the server (frees its
        VRAM). The pipeline starts it again when it next needs it, after the guard clears."""
        if not self._guarded or self._shutting_down:
            return
        if self.llm.is_running() or self.llm.is_loading():
            log.info("releasing the LLM server: an anti-cheat game has been in front for %s s",
                     self.config.llm_release_after_guard_s)
            self.llm_releases += 1
            self.llm.stop(wait=False, status=MSG_LLM_RELEASED)

    # --- startup ---------------------------------------------------------------------------

    def start(self, *, guide_pending: bool = False, announce: bool = False) -> None:
        """Start everything. `guide_pending`: the first-run guide opens in a moment (it explains the region
        itself: no balloon on top of it); `announce`: a windowless start (the shortcut) says in a balloon
        where the app went, as nothing else appears."""
        self.tray.show()
        if not self.tray.is_available():
            log.warning("system tray not available; use the hotkeys")
        self._check_exclusion()
        if self.args.no_hotkeys:
            self.tray.set_hotkeys(Hotkeys("", "", "", "", ""))
            log.info("global hotkeys disabled (--no-hotkeys)")
        else:
            self._register_hotkeys()
        self._init_profile_menu()
        self.restore_pending_games()  # games offered before and never answered: in the tray again (no balloon)
        if self.config.guard_mode != "off" or self.profiles is not None:
            self._start_guard()  # with guard_mode "off" it only reports the foreground exe (profiles)
        if self.config.guard_mode == "off":
            log.info("anti-cheat guard off%s", " (--no-guard)" if self.args.no_guard else "")
        region = region_from_config(self.config)
        if self._dump is not None:
            self._dump.write_run_info({
                "pid": os.getpid(), "gui_thread_id": threading.get_native_id(), "start_ts": time.time(),
                "argv": sys.argv,
                "region": self.config.region, "src_lang": self.config.src_lang, "device": self.config.ocr_device,
                "capture": self.config.capture_backend, "guard_mode": self.config.guard_mode,
                "llm_mode": self.config.llm_mode, "llm_model": self.config.llm_model, "profile": self._profile_name,
            })
        self.pipeline.start()
        log.info("started: region=%s lang=%s device=%s capture=%s guard=%s llm=%s profile=%s", region,
                 self.config.src_lang, self.config.ocr_device, self.config.capture_backend, self.config.guard_mode,
                 self.config.llm_mode, self._profile_name)
        if self.config.llm_mode == "off":
            self.tray.set_llm_status(MSG_LLM_OFF)
        elif self._guarded:
            self.tray.set_llm_status(MSG_LLM_WAITING)  # loaded when the pipeline next needs it
        else:
            self.llm.start()  # background thread; the GUI never waits for the model
        self._llm_idle_timer.start()
        if guide_pending:
            pass  # the guide is about to open: one prompt at a time
        elif region is None:
            key = format_combo(self.config.hotkeys.select_region)
            has_key = self.hotkeys is not None and "select_region" in self.hotkeys.registered()
            hint = f"Nhấn {key} hoặc chọn “Chọn vùng dịch” trong menu khay (bấm vào biểu tượng 文)." \
                if has_key else "Chọn “Chọn vùng dịch” trong menu khay (bấm vào biểu tượng 文)."
            self.tray.show_message(APP_NAME, hint)
        elif announce:
            self.tray.show_message(APP_NAME, MSG_RUNNING, msecs=4000)
        if self.args.exit_after is not None:
            QTimer.singleShot(max(0, int(self.args.exit_after * 1000)), self._exit_timer)

    def _check_exclusion(self) -> None:
        if self.overlay.exclusion_ok():
            log.info("overlay is excluded from screen capture (WDA_EXCLUDEFROMCAPTURE)")
        else:
            self._on_exclusion_changed(False)

    def _register_hotkeys(self) -> None:
        self.hotkeys = HotkeyManager(self)
        self.hotkeys.triggered.connect(self._on_hotkey)
        errors = self._register_all_hotkeys()
        if errors:
            self.tray.show_message("Không đăng ký được phím tắt", "\n".join(errors.values()), error=True)

    def _register_all_hotkeys(self) -> dict[str, str]:
        """Register config.hotkeys; returns name -> reason for the ones that failed."""
        assert self.hotkeys is not None
        for f in fields(Hotkeys):
            combo = getattr(self.config.hotkeys, f.name)
            if combo:
                self.hotkeys.register(f.name, combo)
            else:
                self.hotkeys.unregister(f.name)
        errors = {k: v for k, v in self.hotkeys.last_errors().items() if getattr(self.config.hotkeys, k, "")}
        self._show_registered_hotkeys()
        log.info("hotkeys registered: %s", ", ".join(f"{k}={format_combo(v)}" for k, v in
                                                      self.hotkeys.registered().items()) or "none")
        if errors:
            log.warning("hotkeys not registered: %s", "; ".join(f"{k}: {v}" for k, v in errors.items()))
        return errors

    def _show_registered_hotkeys(self) -> None:
        """The tray shows the hotkeys that really work (failed ones without a shortcut)."""
        active = Hotkeys("", "", "", "", "")
        registered = self.hotkeys.registered() if self.hotkeys is not None else {}
        for f in fields(Hotkeys):
            if f.name in registered:
                setattr(active, f.name, registered[f.name])
        self.tray.set_hotkeys(active)
        if _alive(self._guide):
            self._guide.set_hotkeys(active)

    def _init_profile_menu(self) -> None:
        names = [self._profile_name]
        if self.profiles is not None:
            try:
                names = self.profiles.names()
            except Exception:
                log.warning("cannot list glossary profiles", exc_info=True)
        self.tray.set_profiles(names, self._profile_name)
        self.tray.set_llm_mode_checked(self.config.llm_mode)

    def _start_guard(self) -> None:
        if self.guard is None:
            self.guard = GameGuard(self.config, 1000, self)
            self.guard.changed.connect(self._on_guard_changed)
            self.guard.warn.connect(self._on_guard_warn)
            fg = getattr(self.guard, "foreground_changed", None)
            if fg is not None:
                fg.connect(self._on_foreground_changed)
        if not self.guard.is_running():
            self.guard.start()  # checks at once: a blocked game in front pauses before the first grab
            log.info("anti-cheat guard on (mode %s, %d blocked processes)", self.config.guard_mode,
                     len(self.config.blocked_processes))

    # --- pipeline -> GUI ---------------------------------------------------------------------

    def _emit_state(self, state: OverlayState) -> None:
        """Pipeline callback (worker thread): hand the state to the GUI thread."""
        if not self._shutting_down:
            self._state_arrived.emit(state)

    def _on_state(self, state: OverlayState) -> None:
        if self._shutting_down:
            return
        recv_ts = time.time()
        self.states_received += 1
        self._last_state = state
        self.overlay.set_state(state)
        self._refresh_tray()
        status = (state.status, state.message if state.status != "running" else "")
        if status != self._last_status:
            self._last_status = status
            log.info("status: %s%s", state.status, f" ({state.message})" if state.message else "")
        if self._dump is not None:
            self._dump.write_state(state, recv_ts)
            self._schedule_snapshot()

    def _refresh_tray(self) -> None:
        if self._last_state is not None:
            self.tray.update_status(self._last_state, paused_by_user=self._paused_by_user,
                                    text_visible=self._text_visible)

    def _schedule_snapshot(self) -> None:
        if self._snapshot_timer.isActive():
            return  # the pending snapshot will show the newest state
        wait = SNAPSHOT_INTERVAL_S - (time.monotonic() - self._last_snapshot)
        self._snapshot_timer.start(max(0, int(wait * 1000)))

    def _take_snapshot(self) -> None:
        state = self.overlay.state()
        if self._dump is None or state is None or self._shutting_down:
            return
        if state.seq == self._last_snapshot_seq or state.region is None or state.status == "guarded":
            return
        self._last_snapshot = time.monotonic()
        self._last_snapshot_seq = state.seq
        try:
            self._dump.save_snapshot(self.overlay, state.seq)
        except Exception:
            log.warning("overlay snapshot failed", exc_info=True)

    # --- user intents ------------------------------------------------------------------------

    def _on_hotkey(self, name: str) -> None:
        log.info("hotkey: %s", name)
        actions = {
            "select_region": self.select_region,
            "toggle_pause": self.toggle_pause,
            "toggle_overlay": self.toggle_text,
            "switch_lang": lambda: self.set_lang("en" if self.config.src_lang == "ja" else "ja"),
            "quit": self.quit,
        }
        action = actions.get(name)
        if action is not None:
            action()

    def select_region(self) -> None:
        if self._shutting_down:
            return
        if self.selector.is_active():
            self.selector.start()  # already open: bring it to the front
            return
        log.info("region selection started")
        self.pipeline.set_paused(True, "selecting")
        self.overlay.set_suspended(True)
        self.selector.start()

    def _on_region_selected(self, region: Region) -> None:
        values = [region.left, region.top, region.width, region.height]
        log.info("region selected: %s", values)
        self.config.region = list(values)
        self._persisted.region = list(values)
        self._save_config()
        if self.memory is not None:  # another region: another conversation
            try:
                self.memory.get(self._profile_name).clear()
            except Exception:
                log.warning("dialogue memory clear failed", exc_info=True)
        self.pipeline.set_region(region)
        self.overlay.set_region(region)
        self.pipeline.set_paused(False, "selecting")
        self.overlay.set_suspended(False)
        if _alive(self._guide):
            self._guide.region_finished(list(values))

    def _on_selection_cancelled(self) -> None:
        log.info("region selection cancelled")
        if self._shutting_down:
            return
        self.pipeline.set_paused(False, "selecting")
        self.overlay.set_suspended(False)
        if _alive(self._guide):
            self._guide.region_finished(None)

    def toggle_pause(self) -> None:
        self.set_user_paused(not self._paused_by_user)

    def set_user_paused(self, paused: bool) -> None:
        self._paused_by_user = paused
        self.pipeline.set_paused(paused, "user")
        log.info("paused by user" if paused else "resumed by user")
        self._refresh_tray()

    def toggle_text(self) -> None:
        self._text_visible = not self._text_visible
        self.overlay.set_text_visible(self._text_visible)
        log.info("translations %s", "shown" if self._text_visible else "hidden")
        self._refresh_tray()

    def set_lang(self, lang: str) -> None:
        if lang not in LANGS:
            log.warning("unknown source language %r", lang)
            return
        self.tray.set_lang_checked(lang)
        if lang == self.config.src_lang:
            return
        log.info("source language -> %s", lang)
        self.config.src_lang = lang
        self._persisted.src_lang = lang
        self._save_config()
        self.pipeline.set_src_lang(lang)
        if _alive(self._guide):
            self._guide.set_lang_checked(lang)
        self._sync_settings("src_lang", lang)

    def set_guard_mode(self, mode: str) -> None:
        if mode not in GUARD_MODES:
            log.warning("unknown anti-cheat mode %r", mode)
            return
        self.tray.set_guard_checked(mode)
        if mode == self.config.guard_mode:
            return
        log.info("anti-cheat mode -> %s", mode)
        self.config.guard_mode = mode
        self._persisted.guard_mode = mode
        self._save_config()
        self._sync_settings("guard_mode", mode)
        if mode != "off":
            self._start_guard()
        if self.guard is not None:
            self.guard.refresh()  # "off" clears a guarded state at once

    def open_data_dir(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA_DIR)))

    # --- settings window ---------------------------------------------------------------------

    def open_settings(self, tab: str | None = None) -> None:
        """The Settings window (non-modal; one at a time), on `tab` ("hotkeys", "guard", ...)."""
        if self._shutting_down:
            return
        dlg = self._settings
        if not _alive(dlg):
            from app.ui.settings_dialog import SettingsDialog

            dlg = SettingsDialog(copy.deepcopy(self._persisted), apply=self.apply_settings, llm_status=self.llm.status,
                                 hotkeys_disabled=self.args.no_hotkeys or self.hotkeys is None,
                                 searxng_status=self.searxng_status, lookup_games=self._lookup_games_count())
            dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dlg.show_guide_requested.connect(self.show_guide)
            dlg.open_data_dir_requested.connect(self.open_data_dir)
            dlg.restart_requested.connect(self.restart)
            dlg.hotkey_capture.connect(self._suspend_hotkeys)
            dlg.lookup_request_requested.connect(lambda: self.open_lookup_request())
            dlg.import_profile_requested.connect(lambda how: self.import_profile(how))
            dlg.searxng_install_requested.connect(self.open_searxng_install)
            dlg.forget_games_requested.connect(self.forget_lookup_games)
            dlg.finished.connect(lambda _result, d=dlg: self._on_settings_closed(d))
            self._settings = dlg
            log.info("settings window opened")
        dlg.set_tab(tab)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_settings_closed(self, dlg: Any) -> None:
        if self._settings is dlg:
            self._settings = None
        self._suspend_hotkeys(False)

    def apply_settings(self, new: Config) -> Any:
        """SettingsDialog's Lưu: apply every changed setting (live where possible), save config.json and
        return an ApplyResult (settings a restart applies, hotkeys Windows refused)."""
        from app.ui.settings_dialog import OVERLAY_FIELDS, RESTART_FIELDS, ApplyResult, settings_diff

        result = ApplyResult()
        if self._shutting_down:
            result.errors.append("App đang thoát.")
            return result
        changed = settings_diff(self._persisted, new)
        log.info("settings changed: %s", ", ".join(changed) or "nothing")
        overlay = False
        for name in changed:
            value = copy.deepcopy(getattr(new, name))
            if name == "hotkeys":
                result.errors += self._apply_hotkeys(value)
                continue  # persisted by _apply_hotkeys (a refused combo keeps the old one)
            if name == "src_lang":
                self.set_lang(value)
            elif name == "llm_mode":
                self.set_llm_mode(value)
            elif name == "llm_model":
                self._set_llm_model_value(value)
            elif name == "guard_mode":
                self.set_guard_mode(value)
            elif name in RESTART_FIELDS:
                self._restart_changed.add(name)  # what this run uses stays until the restart
            else:
                setattr(self.config, name, copy.deepcopy(value))
                if name in OVERLAY_FIELDS:
                    overlay = True
                elif name == "blocked_processes" and self.guard is not None:
                    self.guard.refresh()
                elif name == "poll_ms":
                    self._apply_poll_ms(int(value))
                elif name == "llm_release_after_guard_s" and self._llm_release_timer.isActive():
                    self._llm_release_timer.stop()
                    self._schedule_llm_release()
                elif name in ("searxng_url", "searxng_managed"):
                    self._reset_searxng()  # the next lookup makes a service with the new settings
                elif name == "lookup_ask_new_games" and value:
                    self._lookup_seen.clear()  # the game in front may be considered again
            setattr(self._persisted, name, value)  # also when a CLI override made the live value equal
        if overlay:
            self.overlay.update()  # the layout cache is keyed on font family / min size
        self._save_config()
        # Every save names all the settings changed in Settings this run that still differ from what runs
        # (set back to the running value: no restart needed any more).
        result.restart = [label for name, label in RESTART_FIELDS.items()
                          if name in self._restart_changed and getattr(self._persisted, name) != getattr(self.config, name)]
        result.config = copy.deepcopy(self._persisted)
        return result

    def _set_llm_model_value(self, value: str) -> None:
        """config.llm_model from Settings: "" / auto / light / quality, or a GGUF file name kept as is."""
        kind = (value or "").strip().lower() or "auto"
        if kind in ("auto", "light", "quality"):
            self.set_llm_model(kind)
            return
        self.tray.set_llm_model_checked(value)
        if value == self.config.llm_model:
            return
        log.info("LLM model -> %s", value)
        self.config.llm_model = value
        self._persisted.llm_model = value
        self._save_config()
        self._sync_settings("llm_model", value)
        if self.llm.is_running() or self.llm.is_loading():
            self.llm.restart()

    def _apply_poll_ms(self, ms: int) -> None:
        setter = getattr(self.pipeline, "set_poll_ms", None)
        if callable(setter):
            setter(ms)
        elif hasattr(self.pipeline, "_poll_s"):
            # TranslationPipeline reads the interval on its worker thread before every grab; a float
            # assignment is atomic, so the next poll uses it.
            self.pipeline._poll_s = max(0.01, ms / 1000)
        log.info("poll interval -> %d ms", ms)

    def _apply_hotkeys(self, new: Hotkeys) -> list[str]:
        """Re-register the hotkeys that changed. A combo Windows refuses (another app holds it) keeps the
        old one working and saved; returns the Vietnamese reasons. If another changed row took that old
        combo meanwhile, that row goes back to its old combo too (and so on), so no combo is ever saved
        twice; an action whose old combo cannot be registered again is saved without one, and said so."""
        from app.ui.settings_dialog import HOTKEY_LABELS, same_combo

        old = copy.deepcopy(self.config.hotkeys)
        if self.hotkeys is None or self.args.no_hotkeys:
            self.config.hotkeys = copy.deepcopy(new)
            self._persisted.hotkeys = copy.deepcopy(new)
            return []
        self._suspend_hotkeys(False)
        changed = [f.name for f in fields(Hotkeys) if not same_combo(getattr(new, f.name), getattr(old, f.name))]
        for name in changed:  # all first: two actions may swap their combos
            self.hotkeys.unregister(name)
        final = copy.deepcopy(new)
        errors: list[str] = []
        back: list[str] = []  # rows going back to their old combo
        for name in changed:
            combo = getattr(new, name)
            if not combo or self.hotkeys.register(name, combo):
                continue
            reason = self.hotkeys.last_errors().get(name, f"không đăng ký được {format_combo(combo)}")
            errors.append(f"{HOTKEY_LABELS.get(name, name)}: {reason}")
            back.append(name)
        while back:
            name = back.pop(0)
            prev = getattr(old, name)
            setattr(final, name, prev)
            if not prev:
                continue
            holder = next((n for n in changed if n != name and n not in back and getattr(final, n)
                           and same_combo(getattr(final, n), prev)), None)
            if holder is not None:  # another changed row took this one's old combo: it goes back as well
                self.hotkeys.unregister(holder)
                errors.append(f"{HOTKEY_LABELS.get(holder, holder)}: giữ phím cũ, vì "
                              f"{format_combo(prev)} vẫn là phím của “{HOTKEY_LABELS.get(name, name)}”")
                back.append(holder)
            if not self.hotkeys.register(name, prev):
                setattr(final, name, "")
                errors.append(f"{HOTKEY_LABELS.get(name, name)}: không đăng ký lại được {format_combo(prev)} — việc "
                              "này hiện không có phím tắt")
        self.config.hotkeys = final
        self._persisted.hotkeys = copy.deepcopy(final)
        self._show_registered_hotkeys()
        log.info("hotkeys now: %s", ", ".join(f"{k}={format_combo(v)}" for k, v in
                                               self.hotkeys.registered().items()) or "none")
        return errors

    def _suspend_hotkeys(self, suspended: bool) -> None:
        """Release the global hotkeys while a Settings hotkey field records (else the combo the user
        presses there would fire instead of reaching the field); register them again after."""
        if self.hotkeys is None or self._shutting_down or suspended == self._hotkeys_suspended:
            return
        self._hotkeys_suspended = suspended
        if suspended:
            self.hotkeys.unregister_all()
            log.info("global hotkeys paused while a hotkey is being recorded")
        else:
            self._register_all_hotkeys()

    def restart(self) -> None:
        """"Khởi động lại app": start this command line again (the new instance waits for this one to
        let go of the single-instance lock) and quit."""
        if self._shutting_down or not self._editor_saved_or_dropped():
            return
        cmd = restart_command()
        log.info("restarting: %s", subprocess.list2cmdline(cmd))
        try:
            subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), close_fds=True,
                             creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP)
        except OSError as exc:
            log.warning("restart failed", exc_info=True)
            self.tray.show_message("Không khởi động lại được", _short_error(exc), error=True)
            return
        self.quit()

    def _editor_saved_or_dropped(self) -> bool:
        """Before a restart: unsaved glossary-editor changes are saved, dropped or the restart is cancelled
        (the user's answer). True: go on."""
        editor = self._editor
        try:
            if editor is None or not editor.isVisible() or not editor.is_dirty():
                return True
        except RuntimeError:  # already deleted
            return True
        box = QMessageBox(QMessageBox.Icon.Question, "Khởi động lại app",
                          "Cửa sổ “Thuật ngữ & nhân vật” còn thay đổi chưa lưu. Lưu trước khi khởi động lại?",
                          QMessageBox.StandardButton.NoButton, editor)
        save = box.addButton("Lưu rồi khởi động lại", QMessageBox.ButtonRole.AcceptRole)
        drop = box.addButton("Bỏ thay đổi", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Hủy", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.setEscapeButton(cancel)
        exclusion = apply_to_widget(box)  # noqa: F841 - must live as long as the box
        box.exec()
        clicked = box.clickedButton()
        box.deleteLater()
        if clicked is save:
            if editor.save():
                return True
            editor.raise_()  # what blocks saving is in its status line
            return False
        return clicked is drop

    # --- first-run guide ---------------------------------------------------------------------

    def show_guide(self) -> None:
        """The first-run guide (tray "Hướng dẫn nhanh…", Settings, or once at the first start)."""
        if self._shutting_down:
            return
        guide = self._guide
        if not _alive(guide):
            from app.ui.first_run import FirstRunGuide

            guide = FirstRunGuide(self.config, profiles=self._profile_names(), active_profile=self._profile_name,
                                  llm_status=self.llm.status)
            guide.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            guide.set_lang.connect(self.set_lang)
            guide.set_profile.connect(lambda name: self.set_profile(name, reason="guide"))
            guide.new_profile.connect(self.new_profile)
            guide.edit_glossary.connect(self.edit_glossary)
            guide.select_region.connect(self.select_region)
            guide.open_settings.connect(lambda: self.open_settings("hotkeys"))
            guide.dismissed.connect(self._on_guide_dismissed)
            guide.finished.connect(lambda _result, g=guide: self._on_guide_closed(g))
            self._guide = guide
            if self.hotkeys is not None:
                self._show_registered_hotkeys()
            log.info("first-run guide opened")
        guide.show()
        guide.raise_()
        guide.activateWindow()

    def _on_guide_dismissed(self, completed: bool) -> None:
        log.info("first-run guide %s", "finished" if completed else "skipped")
        if not self._persisted.first_run_done:
            self._persisted.first_run_done = True
            self.config.first_run_done = True
            self._save_config()

    def _on_guide_closed(self, guide: Any) -> None:
        if self._guide is guide:
            self._guide = None

    def _profile_names(self) -> list[str]:
        if self.profiles is not None:
            try:
                return self.profiles.names()
            except Exception:
                log.warning("cannot list glossary profiles", exc_info=True)
        return [self._profile_name]

    # --- second launch -----------------------------------------------------------------------

    def on_instance_command(self, command: str) -> None:
        """A command from another launch of the app (app.single_instance)."""
        if self._shutting_down:
            return
        if command == "show":
            self.tray.show_message(APP_NAME, MSG_ALREADY_RUNNING)
            self.tray.popup_menu()
        elif command == "settings":
            self.open_settings()
        elif command == "guide":
            self.show_guide()
        elif command == "quit":
            QTimer.singleShot(150, self.quit)  # the "ok" reply leaves before the socket goes away

    def instance_status(self) -> dict:
        """The "ping" answer: what a launcher or test needs to know about this running instance."""
        try:
            console = bool(ctypes.windll.kernel32.GetConsoleWindow())
        except (AttributeError, OSError):
            console = None
        return {
            "pid": os.getpid(), "exe": os.path.basename(sys.executable), "console": console,
            "tray_visible": self.tray.tray_icon().isVisible(), "tray_available": self.tray.is_available(),
            "status": self._last_state.status if self._last_state is not None else None,
            "profile": self._profile_name, "llm": self.llm.status,
            "settings_open": _alive(self._settings), "guide_open": _alive(self._guide),
            "hotkeys": self.hotkeys.registered() if self.hotkeys is not None else {},
        }

    # --- guard / exclusion -------------------------------------------------------------------

    def _on_guard_changed(self, guarded: bool, exe: str) -> None:
        self._guarded = guarded
        self.llm.guarded = guarded  # no model load while a protected game is in front
        if guarded:
            log.info("anti-cheat game in the foreground (%s): capture paused, overlay hidden", exe)
            self.pipeline.set_paused(True, "guard")  # returns once no grab is running
            self.overlay.set_guarded(True)
            if self.llm.is_loading():  # no model load (disk, CUDA init, VRAM) behind a protected game
                log.info("abandoning the LLM model load: an anti-cheat game is in front")
                self.llm.stop(wait=False, status=MSG_LLM_WAITING)
            else:
                self._schedule_llm_release()
        else:
            log.info("anti-cheat game left the foreground: resuming%s",
                     " (still paused by the user)" if self._paused_by_user else "")
            self._llm_release_timer.stop()
            self.overlay.set_guarded(False)
            self.pipeline.set_paused(False, "guard")

    def _on_guard_warn(self, exe: str) -> None:
        self.tray.show_message(
            "Game có anti-cheat",
            f"Bạn đang chơi {exe} và app vẫn đang chụp màn hình (chế độ Chỉ cảnh báo). "
            "Chọn Chế độ anti-cheat > Tạm dừng để app tự dừng khi chơi game.",
            error=True)

    def _on_exclusion_changed(self, ok: bool) -> None:
        if ok and self._exclusion_error:
            log.info("capture exclusion restored: resuming")
            self._exclusion_error = False
            self.pipeline.set_error(None)
        elif not ok and not self._exclusion_error:
            log.warning("capture exclusion failed: pipeline paused so our own translations are never OCR'd")
            self._exclusion_error = True
            self.pipeline.set_error(EXCLUSION_ERROR)
            self.tray.show_message(APP_NAME, EXCLUSION_ERROR, error=True)

    # --- shutdown ----------------------------------------------------------------------------

    def _exit_timer(self) -> None:
        log.info("--exit-after %.1f s reached", self.args.exit_after)
        self.quit()

    def quit(self) -> None:
        self.shutdown()
        self.qapp.quit()

    def shutdown(self) -> None:
        """Stop everything (idempotent). Called by quit() and on QApplication.aboutToQuit."""
        if self._shutting_down:
            return
        self._shutting_down = True
        t0 = time.perf_counter()
        log.info("shutting down")
        self._snapshot_timer.stop()
        self._llm_release_timer.stop()
        self._llm_idle_timer.stop()
        if self.guard is not None:
            self.guard.stop()
        if self.hotkeys is not None:
            self.hotkeys.close()
        if self.selector.is_active():
            self.selector.cancel()
        self._close_editor()
        self._close_dialogs()
        searxng, self._searxng = self._searxng, None
        if searxng is not None:  # our SearXNG never outlives the app (its job object covers a crash)
            try:
                searxng.close()
            except Exception:
                log.warning("stopping SearXNG failed", exc_info=True)
        if self.profiles is not None:
            try:
                self.profiles.remove_listener(self._on_profiles_event)
            except Exception:
                pass
        self.pipeline.stop(SHUTDOWN_TIMEOUT_S)  # closes the LLM client on its loop while the server is up
        self.llm.wanted = False
        self.llm.on_status = None
        self.llm.stop(wait=True)
        try:
            if reap_llama_children():
                log.warning("a llama-server outlived LlmService.stop(); killed")
        except Exception:
            log.warning("llama-server child check failed", exc_info=True)
        self.overlay.shutdown()
        self.tray.hide()
        self._save_config()
        if self._dump is not None:
            self._dump.close()
        log.info("shutdown complete in %.0f ms (%d states received)", (time.perf_counter() - t0) * 1000,
                 self.states_received)

    def _close_editor(self) -> None:
        editor, self._editor = self._editor, None
        if editor is None:
            return
        try:
            if editor.is_dirty():
                log.warning("glossary editor closed at exit with unsaved changes")
            editor.ask_before_discard = False  # no modal question while quitting
            editor.close()
        except RuntimeError:  # already deleted
            pass

    def _close_dialogs(self) -> None:
        """Settings (unsaved changes are dropped, logged) and the guide (not counted as seen)."""
        settings, self._settings = self._settings, None
        guide, self._guide = self._guide, None
        try:
            if _alive(settings):
                if settings.is_dirty():
                    log.warning("settings window closed at exit with unsaved changes")
                settings.ask_before_discard = False
                settings.close()
        except RuntimeError:
            pass
        try:
            if _alive(guide):
                guide.close_quietly()
        except RuntimeError:
            pass
        windows, self._lookup_windows = self._lookup_windows, []
        for w in windows:  # lookups are cancelled, an install is stopped (no question while quitting)
            try:
                if _alive(w):
                    if hasattr(w, "ask_before_stop"):
                        w.ask_before_stop = False
                    w.close()
            except RuntimeError:
                pass

    def _save_config(self) -> None:
        try:
            self._persisted.save(CONFIG_PATH)
        except OSError:
            log.warning("could not save %s", CONFIG_PATH, exc_info=True)


# --- entry point ----------------------------------------------------------------------------

def _preload_comtypes() -> None:
    """Import dxcam (and with it comtypes) on the main thread, after QApplication.

    comtypes calls CoInitializeEx on the thread that imports it and registers an atexit
    CoUninitialize, which runs on the main thread. Imported lazily on the pipeline thread, that
    unbalanced CoUninitialize tore down the main thread's COM (Qt's OleInitialize) and the process
    crashed after a clean shutdown (0xC0000005). Imported here, the two calls pair up.
    """
    try:
        import dxcam  # noqa: F401
    except Exception:
        log.warning("dxcam import failed; screen capture may not work", exc_info=True)

def show_error_box(title: str, text: str, details: str = "") -> str:
    """Modal, capture-excluded message box (a windowless run has no console to print errors on). Like every
    window of the app it does not show in screenshots, so it offers to copy its text (to paste it when
    asking for help) and to open the log folder. Returns "copy", "logs" or "close" (tests)."""
    qapp = QApplication.instance() or QApplication(sys.argv[:1])
    box = QMessageBox(QMessageBox.Icon.Critical, title, text)
    if details:
        box.setInformativeText(details)
    copy_button = box.addButton("Sao chép chi tiết", QMessageBox.ButtonRole.ActionRole)
    logs_button = box.addButton("Mở thư mục nhật ký", QMessageBox.ButtonRole.ActionRole)
    close_button = box.addButton("Đóng", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(close_button)
    box.setEscapeButton(close_button)
    box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    exclusion = apply_to_widget(box)  # noqa: F841 - must live as long as the box
    box.exec()
    clicked = box.clickedButton()
    box.deleteLater()
    if clicked is copy_button:
        QApplication.clipboard().setText(f"{title}\n{text}\n\n{details}".strip())
        qapp.processEvents()
        return "copy"
    if clicked is logs_button:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR)))
        return "logs"
    return "close"


def show_info_box(title: str, text: str) -> None:
    """Modal, capture-excluded information box: what a windowless run shows instead of printing (--version)."""
    QApplication.instance() or QApplication(sys.argv[:1])
    box = QMessageBox(QMessageBox.Icon.Information, title, text)
    box.addButton("Đóng", QMessageBox.ButtonRole.AcceptRole)
    box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    exclusion = apply_to_widget(box)  # noqa: F841 - must live as long as the box
    box.exec()
    box.deleteLater()


def report_startup_error(exc: BaseException, log_path: Path | None, *, show: bool) -> None:
    """Log a failure before the app is up and, in a windowless run, show it in Vietnamese."""
    log.critical("startup failed", exc_info=(type(exc), exc, exc.__traceback__))
    if show:
        where = f"\n\nChi tiết trong nhật ký:\n{log_path}" if log_path else ""
        show_error_box(f"{APP_NAME}: lỗi khởi động", MSG_STARTUP_FAILED, f"{_short_error(exc, 300)}{where}")


def _hand_over(lock: InstanceLock, windowless: bool, *, wait_s: float = HANDOVER_WAIT_S) -> int | None:
    """Another instance owns this data directory: ask it to show its menu, then exit (0). It may still be
    starting (cold disk at sign-in: the lock comes before its socket) or just quitting: while it holds the
    lock without a socket, keep asking for up to `wait_s`; when it lets go of the lock, this launch starts
    normally (None). Only a socket that exists but never answers means the app is hung (the box, 1)."""
    log.info("%s is already running for %s: asking it to show its tray menu", APP_NAME, DATA_DIR)
    deadline = time.monotonic() + max(0.0, wait_s)
    silent = 0
    while True:
        if send_command(lock.key, "show", timeout_s=2.0) is not None:
            return 0
        if lock.try_acquire():
            log.info("the other instance has exited: starting")
            return None
        silent = silent + 1 if pipe_exists(lock.key) else 0  # listening but not answering
        if silent >= HANDOVER_HUNG_TRIES or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    log.warning("the running instance did not answer on %s", lock.key)
    if windowless:
        show_error_box(APP_NAME, MSG_NOT_RESPONDING, MSG_NOT_RESPONDING_FIX)
    return 1


def _parse_args(argv: list[str] | None, windowless: bool) -> argparse.Namespace:
    """argparse; in a windowless run its error text (written to a stderr nobody sees) goes to a box, and so
    does --version (its stdout goes nowhere either)."""
    if not windowless:
        return build_parser().parse_args(argv)
    err, out, real_err, real_out = io.StringIO(), io.StringIO(), sys.stderr, sys.stdout
    sys.stderr, sys.stdout = err, out
    try:
        return build_parser().parse_args(argv)
    except SystemExit as exc:
        if exc.code in (0, None):
            if out.getvalue().strip() == VERSION_TEXT:
                sys.stderr, sys.stdout = real_err, real_out
                show_info_box(APP_NAME, f"{VERSION_TEXT}\n\nDịch chữ trên màn hình (Nhật / Anh → Việt).")
        else:
            text = err.getvalue().strip()[-600:]
            try:
                setup_logging("INFO")
                log.error("invalid command line %s: %s", sys.argv[1:] if argv is None else argv, text)
            except OSError:
                pass
            reason = text.rsplit("error:", 1)[-1].strip() if "error:" in text else text
            show_error_box(f"{APP_NAME}: lỗi khởi động", MSG_BAD_ARGS, f"{MSG_BAD_ARGS_FIX}\n\nChi tiết: {reason}")
        raise
    finally:
        sys.stderr, sys.stdout = real_err, real_out


def _enable_crash_log() -> None:
    """Windowless: a native crash (no console to print on) leaves its Python stack in logs/crash.log. The
    stack of the previous run, if any, is kept as crash.log.1 (the user relaunches right after a crash)."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / "crash.log"
        try:
            if path.stat().st_size > 0:
                path.replace(path.with_name("crash.log.1"))
        except OSError:
            pass
        fh = open(path, "w", encoding="utf-8")  # noqa: SIM115 - open for the process lifetime
        faulthandler.enable(fh, all_threads=True)
        _enable_crash_log.file = fh  # type: ignore[attr-defined]
    except OSError:
        log.warning("crash log unavailable", exc_info=True)


def main(argv: list[str] | None = None, *, windowless: bool = False) -> int:
    """Run the app. windowless=True (run_app.pyw under pythonw.exe): no console, so a startup error is
    also shown in a message box, and a native crash is written to logs/crash.log."""
    set_app_user_model_id()  # before any window: the taskbar shows "Screen Translator", not Python
    qapp = QApplication.instance() or QApplication(sys.argv[:1])  # first: makes the process PMv2
    qapp.setApplicationName(APP_NAME)
    qapp.setApplicationVersion(__version__)
    qapp.setQuitOnLastWindowClosed(False)  # tray app: closing the selector must not quit
    qt_gc.install(qapp)  # before any worker thread: cyclic garbage with Qt wrappers is freed on this thread only
    install_vietnamese_qt_texts(qapp)  # Qt's own texts (text-field menus, standard buttons) in Vietnamese
    try:
        from app.ui.tray import make_icon

        qapp.setWindowIcon(make_icon())  # every window and box: the 文 icon, not Python's
    except Exception:
        logging.getLogger("app").debug("window icon unavailable", exc_info=True)

    args = _parse_args(argv, windowless)

    log_path: Path | None = None
    try:
        log_path = setup_logging(args.log_level)
    except OSError as exc:
        if not windowless:
            raise
        show_error_box(f"{APP_NAME}: lỗi khởi động", MSG_STARTUP_FAILED, f"Không ghi được nhật ký: {exc}")
        return 1

    lock = InstanceLock(instance_key(DATA_DIR))
    if not lock.acquire(max(0.0, args.wait_for_instance)):
        rc = _hand_over(lock, windowless)
        if rc is not None:
            return rc
    if windowless:
        _enable_crash_log()

    app: App | None = None
    server: InstanceServer | None = None
    heartbeat = QTimer()  # lets the Python interpreter run signal handlers while Qt's loop is busy
    try:
        if not set_power_throttling(False):
            log.warning("could not opt out of EcoQoS power throttling; OCR may be slow while a game has focus")
        import cv2

        cv2.setNumThreads(1)
        _preload_comtypes()

        persisted = Config.load(CONFIG_PATH)
        config = apply_overrides(copy.deepcopy(persisted), args)
        log.info("%s starting (pid %d, GUI thread %d, log %s, config %s%s)", VERSION_TEXT, os.getpid(),
                 threading.get_native_id(), log_path, CONFIG_PATH, ", windowless" if windowless else "")

        app = App(qapp, config, persisted, args)
        server = InstanceServer(lock.key, status=app.instance_status, parent=app)
        server.command.connect(app.on_instance_command)
        if not server.listen():
            log.warning("single-instance socket unavailable: a second launch cannot reach this one")
        signal.signal(signal.SIGINT, lambda *_: QTimer.singleShot(0, app.quit))
        heartbeat.start(250)
        heartbeat.timeout.connect(lambda: None)

        guide = should_show_first_run(persisted.first_run_done, automated=args.exit_after is not None,
                                      forced=args.first_run)
        app.start(guide_pending=guide, announce=windowless and args.exit_after is None)
        if guide:
            QTimer.singleShot(FIRST_RUN_DELAY_MS, app.show_guide)
    except Exception as exc:
        report_startup_error(exc, log_path, show=windowless)
        if app is not None:
            try:
                app.shutdown()
            except Exception:
                log.warning("shutdown after the failed start failed", exc_info=True)
        if server is not None:
            server.close()
        lock.release()
        if not windowless:
            raise
        logging.shutdown()
        return 1

    rc = qapp.exec()
    app.shutdown()
    heartbeat.stop()
    server.close()
    lock.release()
    log.info("exit code %d", rc)
    logging.shutdown()
    return rc
