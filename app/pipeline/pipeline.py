"""Threaded translation pipeline: capture -> change detection -> OCR -> layout -> tracking ->
translation -> OverlayState. No Qt here; `on_state` is called from the worker thread.

Threads:
- worker: owns the capture backend, the OCR engine, the ChangeDetector and the BlockTracker
  (all created and used on this thread). It grabs the region every poll_ms, runs OCR when the
  detector says so, schedules translations and applies their results, and publishes states.
- translate: an asyncio loop that owns the translator (created there: its SQLite cache and
  HTTP client must stay on one thread). Batches are submitted with run_coroutine_threadsafe;
  results come back through a queue and are applied on the worker thread. A batch whose
  blocks have all left the screen is cancelled, so it does not hold up the rate limit.

Pausing (any reason active) stops grabbing completely; set_paused() takes the capture lock,
so once it returns no further grab starts. While the anti-cheat guard is active the capture
backend is closed as well (any pause closes a backend that captures in the background, WGC)
and recreated on resume; the OCR engine is not loaded while guarded and is released when the
guard lasts long.

Contextual tier (llm_mode "refine" / "only"): besides the per-text machine translation (tier 1)
the worker builds ONE job per screen for a ContextualTranslator (a local LLM, tier 2): the blocks
that need a tier-2 translation (at most llm_max_needed, fewer after a job timed out) plus nearby
confirmed blocks as context only (ScreenItem.context_only: speaker labels, the rest of a menu),
each with a layout role hint, and the profile context from `context_provider`. The job's timeout
grows with what it asks for. Only one job is in flight (one local GPU); when the screen changes
meanwhile, its results are applied to the blocks whose text is unchanged and the next job is
built from what is on screen then (no backlog). "refine" shows tier 1 first and replaces it in
place; "only" sends no machine translation while the LLM works and falls back to it when the
LLM is not ready, fails or times out. Tier-2 results are cached (namespace: LLM model + profile
context version, and for dialogue the speaker and the guessed listener, since the Vietnamese
pronouns depend on them), so repeated text shows the LLM translation at once without a Google
call; dialogue served that way still reaches on_screen_result (the dialogue memory).
A text that is exactly a user glossary entry or character name of the profile (a speaker label)
gets its fixed rendering at once, as tier 2, in every mode: no machine translation of a known name.
LLM failures back off on their own (5 s doubling to 60 s) without holding up tier 1; a job that
timed out because it was big only makes the next jobs smaller.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import cv2
import numpy as np

from app.capture.base import CaptureBackend, Region
from app.config import CACHE_PATH, Config
from app.ocr.base import OcrEngine, TextBox
from app.translate.base import TranslationError, TranslatorEngine
from app.translate.cache import TIER_LLM, TIER_MT, CacheNamespace, TranslationCache
from app.translate.contextual import ContextualTranslator, ScreenItem, ScreenResult, TranslationContext
from app.translate.llm_prompt import DIALOGUE_ROLES, glossary_rendering, guess_listeners, screen_speakers
from app.translate.textnorm import is_passthrough, needs_translation, normalize_text, source_lang_for

from .colors import block_colors
from .fingerprint import ChangeDetector, DetectorParams
from .layout import LayoutParams, TextGroup, layout_blocks, role_hints
from .matching import BlockTracker, TrackedBlock, TrackerParams
from .types import OverlayState

log = logging.getLogger(__name__)

PAUSE_REASONS = ("user", "guard", "selecting", "error")
LLM_MODES = ("off", "refine", "only")
LLM_CACHE_ENGINE = "llm"  # engine name of tier-2 cache entries (the model is part of the key)
# Bumped when the post-edit of LLM answers changes (r2: pronoun-role repair; r3: questions about the
# listener, greetings and explicit source pronouns asked again; r4: the subject / object of an explicit
# source pronoun asked again too, "I'll protect you" -> "em sẽ bảo vệ anh" from the brother; r5: no re-ask of
# right lines the r4 checker misread - "Em được anh cứu", "Tao thua mày", お前は俺が戻るまで… - and the
# post-edit's listener as the prompt's; r6: who did a favour for whom asked again - 弁当作ってくれたのか ->
# "Anh làm bữa trưa cho em à?" from the brother - with a note that says so; r7: no help / give slot judged when
# the line's favours go both ways, which the r6 rules rewrote wrong - 手伝ってくれてありがとう、今度は私が手伝って
# あげるね - and per clause instead), so answers cached before are translated again instead of being shown as they
# were ("Anh xin lỗi nhé" from the sister, "Bà không sao chứ?").
LLM_CACHE_REVISION = "r7"
# role re-ask outcomes (LocalLlmTranslator.last_role_reask) of a line whose roles are known wrong and that was
# not asked again: not cached
UNSETTLED_REASK = frozenset({"over the cap", "no time", "failed"})

MSG_NO_REGION = "Chưa chọn vùng — nhấn {hotkey}"
MSG_PAUSED = "Tạm dừng"
MSG_SELECTING = "Đang chọn vùng…"
MSG_GUARDED = "Đã tạm dừng: game có anti-cheat"
MSG_ERROR = "Lỗi"
MSG_LOADING_OCR = "Đang tải OCR…"
MSG_TRANSLATING = "Đang dịch {n}…"
MSG_TRANSLATE_ERROR = "Lỗi dịch: {err}"
MSG_OCR_FAILED = "Lỗi OCR: {err}"
MSG_CAPTURE_FAILED = "Lỗi chụp màn hình: {err}"
MSG_TRANSLATOR_FAILED = "Không khởi động được bộ dịch: {err}"
MSG_REFINING = "Đang tinh chỉnh {n}…"
MSG_LLM_ERROR = "LLM lỗi: {err}"
MSG_LLM_TIMEOUT = "quá {s:g} s"

THUMB_SIZE = (16, 8)  # w, h of the grey thumbnail kept per block
THUMB_CHANGED = 10.0  # mean absolute grey difference (0-255) that means "the text there is gone"


@dataclass
class PipelineTuning:
    """Knobs that are not user settings (tests shorten the timings)."""

    detector: DetectorParams = field(default_factory=DetectorParams)
    layout: LayoutParams = field(default_factory=LayoutParams)
    tracker: TrackerParams = field(default_factory=TrackerParams)
    paused_wait_s: float = 0.5  # worker wake-up interval while nothing is captured
    ocr_retry_s: float = 5.0  # retry OCR engine creation after a failure
    ocr_max_failures: int = 3  # recognize() failures in a row before the engine is rebuilt
    ocr_failure_retry_s: float = 1.0  # OCR again this soon after a failed recognize()
    release_ocr_after_guard_s: float = 30.0  # free the OCR engine (VRAM) when guarded this long
    capture_retry_s: float = 2.0  # retry capture creation / grabbing after a failure
    release_capture_on: frozenset[str] = frozenset({"guard", "error"})  # close the capture backend
    vanish_threshold: float = THUMB_CHANGED  # a missed block whose pixels changed this much is removed at once
    translate_hold_s: float = 5.0  # no new requests this long after a failed one (doubles, capped)
    translate_hold_max_s: float = 60.0
    aclose_timeout_s: float = 2.0
    llm_timeout_s: float = 8.0  # an LLM job taking longer failed ("only" mode falls back to tier 1) ...
    llm_timeout_per_item_s: float = 0.4  # ... plus this per text asked beyond the first ...
    llm_timeout_per_char_s: float = 0.01  # ... and this per character asked beyond 200 ...
    llm_timeout_max_s: float = 30.0  # ... up to this
    llm_hold_s: float = 5.0  # no LLM job this long after a failed one (doubles, capped)
    llm_hold_max_s: float = 60.0
    llm_probe_s: float = 1.0  # ask contextual_factory again this soon while it returns None
    llm_settle_s: float = 1.0  # wait up to this long for text still being confirmed before a job
    llm_max_needed: int = 12  # texts the LLM translates per job (halved after a job timed out)
    llm_max_chars: int = 1200  # characters of those texts per job
    llm_max_items: int = 24  # screen items per job, context included
    llm_context_chars: int = 1200  # characters of the context items


@dataclass
class _Batch:
    seq: int
    items: list[tuple[int, str]]  # (block id, source)
    src: str
    tgt: str
    t0: float
    llm_keys: dict[int, str] | None = None  # block id -> tier-2 cache key: look the text up there first
    epoch: int = 0  # LLM epoch when the batch was built
    lookup_only: bool = False  # tier-2 cache lookup only, no machine translation ("only" mode)
    speakers: dict[int, str | None] = field(default_factory=dict)  # dialogue speaker per block (from the layout)
    screen: tuple[ScreenItem, ...] = ()  # the screen when the batch was built (for on_screen_result)
    ctx: TranslationContext | None = None


@dataclass
class _BatchResult:
    batch: _Batch
    translations: list[str | None] | None  # None entries: lookup-only misses
    error: str | None
    latency_ms: float
    retryable: bool = True  # the failure may go away (rate limit, network): hold all requests a while
    tiers: list[int] | None = None  # per text: TIER_MT or TIER_LLM (tier-2 cache hit)


@dataclass
class _LlmJob:
    seq: int
    items: tuple[ScreenItem, ...]  # the whole screen, in screen order
    needed: dict[int, str]  # block id -> source that needs a tier-2 translation
    src: str
    tgt: str
    ctx: TranslationContext
    key: str  # tier-2 cache key of `ctx` (profile + version)
    epoch: int
    t0: float
    keys: dict[int, str] = field(default_factory=dict)  # block id -> tier-2 cache key (dialogue: + speaker>listener)
    timeout: float = 8.0
    layout: dict[int, str | None] = field(default_factory=dict)  # block id -> speaker from the label above it
    applied: dict[int, str] = field(default_factory=dict)  # block id -> translation put on screen
    speakers: dict[int, str | None] = field(default_factory=dict)
    new_terms: tuple = ()
    model: str = ""


@dataclass
class _LlmResult:
    job: _LlmJob
    result: ScreenResult | None  # None: failed or no translator
    error: str | None
    latency_ms: float
    partial: bool = False  # tier-2 cache hits sent ahead of the LLM call; the job is still running
    unavailable: bool = False  # contextual_factory returned None
    cached: int = 0  # needed texts served from the tier-2 cache
    timed_out: bool = False


def region_from_config(config: Config) -> Region | None:
    r = config.region
    if not r or len(r) != 4:
        return None
    left, top, width, height = (int(v) for v in r)
    return Region(left, top, width, height) if width > 0 and height > 0 else None


def format_hotkey(spec: str) -> str:
    """"ctrl+alt+r" -> "Ctrl+Alt+R"."""
    return "+".join(p.strip().capitalize() if len(p.strip()) > 1 else p.strip().upper() for p in spec.split("+"))


def state_to_dict(state: OverlayState) -> dict:
    """JSON-friendly view of a state (used for --debug-dump states.jsonl)."""
    r = state.region
    return {
        "seq": state.seq,
        "ts": state.timestamp,
        "status": state.status,
        "message": state.message,
        "src_lang": state.src_lang,
        "tgt_lang": state.tgt_lang,
        "pending": state.pending,
        "region": None if r is None else [r.left, r.top, r.width, r.height],
        "blocks": [
            {
                "id": b.id, "box": list(b.box), "source": b.source, "translation": b.translation,
                "stale_translation": b.stale_translation, "error": b.error, "vertical": b.vertical,
                "line_height": b.line_height, "bg": list(b.bg), "fg": list(b.fg),
                "lines": [list(r) for r in b.lines], "quality": b.quality, "speaker": b.speaker,
            }
            for b in state.blocks
        ],
        "metrics": state.metrics,
    }


def _short_error(exc: BaseException, limit: int = 80) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _thumb(image: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray | None:
    """Tiny grey thumbnail of `box` (image coordinates), None if the box is outside the image."""
    h, w = image.shape[:2]
    x0, y0 = max(0, box[0]), max(0, box[1])
    x1, y1 = min(w, box[0] + box[2]), min(h, box[1] + box[3])
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    grey = cv2.cvtColor(np.ascontiguousarray(image[y0:y1, x0:x1]), cv2.COLOR_BGR2GRAY)
    return cv2.resize(grey, THUMB_SIZE, interpolation=cv2.INTER_AREA).astype(np.int16)


def _normalized_result(r: ScreenResult) -> ScreenResult:
    """Item ids as ints, translations as non-empty stripped strings (a model may echo ids as text)."""
    def by_id(d) -> dict[int, Any]:
        out: dict[int, Any] = {}
        for k, v in (d or {}).items():
            try:
                out[int(k)] = v
            except (TypeError, ValueError):
                continue
        return out

    translations = {k: v.strip() for k, v in by_id(r.translations).items() if isinstance(v, str) and v.strip()}
    speakers = {k: (v.strip() if isinstance(v, str) and v.strip() else None) for k, v in by_id(r.speakers).items()}
    return ScreenResult(translations, speakers, tuple(r.new_terms or ()), r.model, r.latency_s)


def _offset_group(g: TextGroup, dx: int, dy: int) -> TextGroup:
    def mv(r):
        return (r[0] + dx, r[1] + dy, r[2], r[3])

    return TextGroup(mv(g.box), tuple(mv(r) for r in g.lines), g.source, g.vertical, g.line_height)


class TranslationPipeline:
    def __init__(
        self,
        config: Config,
        on_state: Callable[[OverlayState], None],
        *,
        capture_factory: Callable[[], CaptureBackend] | None = None,
        ocr_factory: Callable[[str], OcrEngine] | None = None,
        translator_factory: Callable[[], TranslatorEngine] | None = None,
        clock: Callable[[], float] = time.monotonic,
        tuning: PipelineTuning | None = None,
        contextual_factory: Callable[[], ContextualTranslator | None] | None = None,
        context_provider: Callable[[], TranslationContext] | None = None,
        on_screen_result: Callable[[ScreenResult, tuple[ScreenItem, ...], TranslationContext], None] | None = None,
    ) -> None:
        """`contextual_factory` is called on the translation thread before every LLM job and returns
        None while no LLM is ready; `context_provider` (worker thread) gives the active profile's
        context, recent dialogue included, whenever a job is built; `on_screen_result` (worker
        thread) gets, once per job, the tier-2 translations that job put on screen (translations /
        speakers hold only those ids), all the job's items in screen order and the context the job
        was built with (its profile, not the one active now); also for dialogue served from the
        tier-2 cache."""
        self._config = config
        self._on_state = on_state
        self._capture_factory = capture_factory or self._default_capture_factory
        self._ocr_factory = ocr_factory or self._default_ocr_factory
        self._translator_factory = translator_factory or self._default_translator_factory
        self._contextual_factory = contextual_factory
        self._context_provider = context_provider
        self._on_screen_result = on_screen_result
        self._clock = clock
        self.tuning = tuning or PipelineTuning()
        self._poll_s = max(0.01, config.poll_ms / 1000)
        self._max_inflight = max(1, int(config.max_inflight_translations))
        self._tgt_lang = config.tgt_lang

        # Shared between threads.
        self._capture_lock = threading.Lock()  # guards _pause_reasons/_error_message and every grab
        self._pause_reasons: set[str] = set()
        self._error_message: str | None = None
        self._commands: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
        self._results: queue.SimpleQueue[_BatchResult] = queue.SimpleQueue()
        self._wake = threading.Event()
        self._stop_evt = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._started = self._stopped = False
        self._worker: threading.Thread | None = None
        self._tthread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._translator: TranslatorEngine | None = None
        self._translator_ready = threading.Event()
        self._translator_error: str | None = None
        self.grab_count = 0  # diagnostics / tests
        self.ocr_passes = 0
        self.cancelled_batches = 0
        self.llm_jobs = 0
        self.cancelled_llm_jobs = 0

        # Translation-thread state (contextual tier).
        self._contextual: ContextualTranslator | None = None  # last one contextual_factory returned
        self._llm_model = ""  # its model name: part of the tier-2 cache namespace
        self._llm_cache: TranslationCache | None = None
        self._closing: set[asyncio.Task] = set()

        # Worker-thread state.
        self._region = region_from_config(config)
        self._src_lang = config.src_lang
        self._capture: CaptureBackend | None = None
        self._capture_retry_at = 0.0
        self._ocr: OcrEngine | None = None
        self._ocr_lang: str | None = None
        self._ocr_retry_at = 0.0
        self._ocr_failures = 0  # recognize() failures in a row
        self._ocr_rebuilds = 0  # engines dropped for failing since the last successful recognize()
        self._guard_since: float | None = None
        self._faults: dict[str, str] = {}  # "ocr_init" | "capture" | "ocr" | "worker" -> message
        self._detector = ChangeDetector(self.tuning.detector, clock)
        self._tracker = BlockTracker(self._src_lang, self.tuning.tracker)
        self._thumbs: dict[int, np.ndarray] = {}  # block id -> grey thumbnail when last seen
        self._active = False
        self._next_poll = 0.0
        self._inflight_batches = 0
        self._batches: dict[int, tuple[_Batch, concurrent.futures.Future]] = {}  # submitted, no result yet
        self._batch_seq = 0
        self._hold_until = 0.0  # no new translation requests before this (after failures)
        self._fail_streak = 0
        mode = config.llm_mode if config.llm_mode in LLM_MODES else "off"
        if mode != config.llm_mode:
            log.warning("unknown llm_mode %r; the LLM tier is off", config.llm_mode)
        self._llm_mode = mode
        self._llm_job: tuple[_LlmJob, concurrent.futures.Future] | None = None  # at most one in flight
        self._llm_seq = 0
        self._llm_epoch = 0  # a new epoch re-translates every tier-2 block (profile / glossary change)
        self._ctx_version = ""  # set_context_version(); used when there is no context_provider
        self._llm_ready: bool | None = None  # None: not asked yet; False: contextual_factory gave None
        self._llm_probe_at = 0.0  # ask contextual_factory again from this time on
        self._llm_hold_until = 0.0  # no LLM job before this (after failures)
        self._llm_fail_streak = 0
        self._llm_error: str | None = None
        self._llm_size_cap = self.tuning.llm_max_needed  # texts per job; halved after a job timed out
        self._metrics: dict[str, Any] = {}
        self._published_key: tuple | None = None
        self._seq = 0
        self._state = OverlayState(seq=0, region=self._region, status="idle", src_lang=self._src_lang,
                                   tgt_lang=self._tgt_lang, timestamp=time.time())

    # -- default factories --------------------------------------------------------------
    def _default_capture_factory(self) -> CaptureBackend:
        from app.capture import create_backend

        return create_backend(self._config.capture_backend or "auto")

    def _default_ocr_factory(self, lang: str) -> OcrEngine:
        from app.ocr.rapid import RapidOcrEngine

        c = self._config
        device = c.ocr_device
        if device == "cuda" and self._ocr_rebuilds >= 2:
            log.warning("OCR keeps failing on CUDA; using the CPU")
            device = "cpu"
        if device == "cuda":
            try:
                engine = RapidOcrEngine(lang, c.ocr_det, c.ocr_rec, "cuda", c.ocr_threads)
                engine.warmup(1)  # inside the try: an engine that builds but cannot run falls back too
                return engine
            except Exception:
                log.warning("CUDA OCR unavailable; falling back to CPU", exc_info=True)
        engine = RapidOcrEngine(lang, c.ocr_det, c.ocr_rec, "cpu", c.ocr_threads)
        engine.warmup(1)
        return engine

    @staticmethod
    def _default_translator_factory() -> TranslatorEngine:
        from app.translate import CachedTranslator, GoogleGtxTranslator, TranslationCache

        return CachedTranslator(GoogleGtxTranslator(), TranslationCache(CACHE_PATH))

    # -- public API (any thread) ----------------------------------------------------------
    def start(self) -> None:
        with self._lifecycle_lock:
            if self._started or self._stopped:
                raise RuntimeError("TranslationPipeline can only be started once")
            self._started = True
        self._tthread = threading.Thread(target=self._translator_main, name="pipeline-translate", daemon=True)
        self._worker = threading.Thread(target=self._worker_main, name="pipeline-worker", daemon=True)
        self._tthread.start()
        self._worker.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                return
            self._stopped = True
        deadline = time.monotonic() + timeout
        self._stop_evt.set()
        self._wake.set()
        if self._worker is not None:
            self._worker.join(max(0.0, deadline - time.monotonic()))
            if self._worker.is_alive():
                log.warning("pipeline worker did not stop within %.1f s", timeout)
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:  # already closed
                pass
        if self._tthread is not None:
            self._tthread.join(max(0.5, deadline - time.monotonic()))
            if self._tthread.is_alive():
                log.warning("translation thread did not stop within %.1f s", timeout)

    def set_region(self, region: Region | None) -> None:
        self._commands.put(("region", region))
        self._wake.set()

    def set_src_lang(self, lang: str) -> None:
        self._commands.put(("lang", lang))
        self._wake.set()

    def set_llm_mode(self, mode: str) -> None:
        """"off" | "refine" | "only" (see the module docstring); applies from the next worker loop."""
        if mode not in LLM_MODES:
            raise ValueError(f"unknown LLM mode {mode!r}; expected one of {LLM_MODES}")
        self._commands.put(("llm_mode", mode))
        self._wake.set()

    def set_context_version(self, version: str) -> None:
        """The profile context changed (profile switch, glossary edit): when `version` differs from the
        last one, the LLM job in flight is dropped and every tier-2 translation on screen is redone
        (it stays visible until the new one arrives)."""
        self._commands.put(("ctx_version", str(version)))
        self._wake.set()

    def invalidate(self) -> None:
        """Redo every tier-2 translation on screen with the current context (see set_context_version)."""
        self._commands.put(("invalidate", None))
        self._wake.set()

    def set_paused(self, paused: bool, reason: str = "user") -> None:
        if reason not in PAUSE_REASONS:
            raise ValueError(f"unknown pause reason {reason!r}; expected one of {PAUSE_REASONS}")
        with self._capture_lock:  # waits for a grab in progress; none starts after this returns
            if paused:
                self._pause_reasons.add(reason)
            else:
                self._pause_reasons.discard(reason)
                if reason == "error":
                    self._error_message = None
        self._wake.set()

    def set_error(self, message: str | None) -> None:
        """Pause with reason "error" and show `message` (None clears the error and that pause)."""
        with self._capture_lock:
            self._error_message = message
            if message is None:
                self._pause_reasons.discard("error")
            else:
                self._pause_reasons.add("error")
        self._wake.set()

    def is_paused(self, reason: str | None = None) -> bool:
        with self._capture_lock:
            return bool(self._pause_reasons) if reason is None else reason in self._pause_reasons

    def snapshot(self) -> OverlayState:
        return self._state

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    @property
    def llm_mode(self) -> str:
        """The LLM mode the worker currently applies (set_llm_mode takes effect on its next loop)."""
        return self._llm_mode

    # -- translation thread ---------------------------------------------------------------
    def _translator_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            try:
                self._translator = self._translator_factory()
            except Exception as exc:
                log.exception("translator creation failed")
                self._translator_error = _short_error(exc)
            # Tier-2 entries share the machine translation's cache (CachedTranslator) when there is one.
            cache = getattr(self._translator, "cache", None)
            self._llm_cache = cache if isinstance(cache, TranslationCache) else TranslationCache(None)
            self._translator_ready.set()
            self._wake.set()
            if not self._stop_evt.is_set():
                loop.run_forever()
        finally:
            self._shutdown_loop(loop)

    def _shutdown_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        try:
            try:  # a stop() that came before run_forever() left loop.stop pending: let it run out here
                loop.run_until_complete(asyncio.sleep(0))
            except RuntimeError:
                pass
            tasks = [t for t in asyncio.all_tasks(loop) if not t.done() and t not in self._closing]
            for t in tasks:
                t.cancel()
            if tasks:
                loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            if self._closing:  # aclose() of replaced contextual translators: let them finish
                loop.run_until_complete(asyncio.wait(list(self._closing), timeout=self.tuning.aclose_timeout_s))
            ctr, self._contextual = self._contextual, None
            if ctr is not None:
                try:
                    loop.run_until_complete(asyncio.wait_for(ctr.aclose(), self.tuning.aclose_timeout_s))
                except Exception:
                    log.warning("contextual translator aclose failed", exc_info=True)
            tr = self._translator
            if tr is not None:
                try:
                    loop.run_until_complete(asyncio.wait_for(tr.aclose(), self.tuning.aclose_timeout_s))
                except Exception:
                    log.warning("translator aclose failed", exc_info=True)
                cache_close = getattr(getattr(tr, "cache", None), "close", None)
                if callable(cache_close):  # CachedTranslator: its SQLite connection lives on this thread
                    try:
                        cache_close()
                    except Exception:
                        log.warning("translation cache close failed", exc_info=True)
            if self._llm_cache is not None and self._llm_cache is not getattr(tr, "cache", None):
                self._llm_cache.close()
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()
            log.debug("translation thread stopped")

    def _post(self, result: _BatchResult | _LlmResult) -> None:
        self._results.put(result)
        self._wake.set()

    async def _run_batch(self, batch: _Batch) -> None:
        texts = [src for _, src in batch.items]
        error: str | None = None
        retryable = True
        translations: list[str | None] | None = [None] * len(texts)
        tiers = [TIER_MT] * len(texts)
        try:
            todo = list(range(len(texts)))
            if batch.llm_keys:  # a tier-2 translation of the same text (same speaker / listener) skips Google
                for i, (bid, text) in enumerate(batch.items):
                    key = batch.llm_keys.get(bid)
                    hit = self._llm_cache_get(text, batch.src, key) if key is not None else None
                    if hit is not None:
                        translations[i], tiers[i] = hit, TIER_LLM
                todo = [i for i in todo if translations[i] is None]
            if todo and not batch.lookup_only:
                assert self._translator is not None
                out = await self._translator.translate_batch([texts[i] for i in todo], batch.src, batch.tgt)
                if len(out) != len(todo):
                    raise TranslationError(f"{len(out)} results for {len(todo)} texts")
                for i, tr in zip(todo, out):
                    translations[i] = tr
        except asyncio.CancelledError:
            raise  # cancelled by the worker (its blocks are gone) or at shutdown: no result
        except Exception as exc:  # tier-2 cache hits found before the failure are still good
            error = _short_error(exc)
            retryable = bool(getattr(exc, "retryable", True))
        latency = (time.perf_counter() - batch.t0) * 1000
        self._post(_BatchResult(batch, translations, error, latency, retryable, tiers))

    # -- contextual tier (translation thread) ----------------------------------------------
    def _llm_ns(self, text: str, src: str, key: str) -> CacheNamespace:
        return CacheNamespace(f"{LLM_CACHE_ENGINE}-{LLM_CACHE_REVISION}", self._llm_model, source_lang_for(text, src),
                              self._tgt_lang, key)

    def _llm_cache_get(self, text: str, src: str, key: str) -> str | None:
        if self._llm_cache is None or not self._llm_model:
            return None
        try:
            hit = self._llm_cache.get(self._llm_ns(text, src, key), text, min_tier=TIER_LLM, fuzzy=False)
        except sqlite3.Error:
            log.warning("tier-2 cache lookup failed", exc_info=True)
            return None
        return hit.translation if hit is not None else None

    def _llm_cache_put(self, job: _LlmJob, translations: dict[int, str]) -> None:
        if self._llm_cache is None or not self._llm_model:
            return
        texts = {it.id: it.text for it in job.items}
        groups: dict[CacheNamespace, list[tuple[str, str]]] = {}
        for bid, tr in translations.items():
            src = texts.get(bid)
            if src and tr and tr.strip() and needs_translation(src):
                groups.setdefault(self._llm_ns(src, job.src, job.keys.get(bid, job.key)), []).append((src, tr))
        try:
            for ns, pairs in groups.items():
                self._llm_cache.put_many(ns, [p for p in pairs if not is_passthrough(*p)], TIER_LLM)
                echoed = [p for p in pairs if is_passthrough(*p)]  # names kept as they are, or an echo
                if echoed:
                    self._llm_cache.put_many(ns, echoed, TIER_LLM, persist=False)
        except sqlite3.Error:
            log.warning("tier-2 cache write failed", exc_info=True)

    def _current_contextual(self) -> ContextualTranslator | None:
        """Ask contextual_factory for the translator (None while no LLM is ready); a replaced one is
        closed here, on the loop that used it."""
        tr: ContextualTranslator | None = None
        if self._contextual_factory is not None:
            try:
                tr = self._contextual_factory()
            except Exception:
                log.warning("contextual_factory failed", exc_info=True)
        old = self._contextual
        if tr is not old:
            self._contextual = tr
            if old is not None:
                task = asyncio.ensure_future(self._aclose_quietly(old))
                self._closing.add(task)
                task.add_done_callback(self._closing.discard)
        if tr is not None:
            self._llm_model = str(getattr(tr, "model", "") or getattr(tr, "name", "") or LLM_CACHE_ENGINE)
        return tr

    async def _aclose_quietly(self, tr: ContextualTranslator) -> None:
        try:
            await asyncio.wait_for(tr.aclose(), self.tuning.aclose_timeout_s)
        except Exception:
            log.warning("contextual translator aclose failed", exc_info=True)

    async def _run_llm_job(self, job: _LlmJob) -> None:
        t0 = time.perf_counter()

        def ms() -> float:
            return (time.perf_counter() - t0) * 1000

        tr = self._current_contextual()
        if tr is None:
            self._post(_LlmResult(job, None, None, ms(), unavailable=True))
            return
        job.model = self._llm_model
        hits: dict[int, str] = {}
        for bid, src in job.needed.items():
            hit = self._llm_cache_get(src, job.src, job.keys.get(bid, job.key))
            if hit is not None:
                hits[bid] = hit
        # cached lines keep the speaker of their label (as on the batch path): the dialogue memory and the
        # overlay need it, and the cache key already holds it
        hit_speakers = {bid: job.layout.get(bid) for bid in hits}
        if len(hits) == len(job.needed):  # every text seen before in this context: no LLM call
            self._post(_LlmResult(job, ScreenResult(hits, hit_speakers, model=job.model), None, ms(), cached=len(hits)))
            return
        if hits:  # show these now; the LLM call below still sees the whole screen
            self._post(_LlmResult(job, ScreenResult(hits, hit_speakers, model=job.model), None, ms(), partial=True,
                                  cached=len(hits)))
        result: ScreenResult | None = None
        error: str | None = None
        timed_out = False
        try:
            tr.time_budget_s = job.timeout  # an optional role re-ask must not cost the answer already there
            result = await asyncio.wait_for(tr.translate_screen(list(job.items), job.src, job.tgt, job.ctx),
                                            job.timeout)
            if not isinstance(result, ScreenResult):
                raise TranslationError(f"translate_screen returned {type(result).__name__}")
            result = _normalized_result(result)
        except asyncio.CancelledError:
            raise  # cancelled by the worker (its text left the screen, new context) or at shutdown
        except TimeoutError:
            result, error, timed_out = None, MSG_LLM_TIMEOUT.format(s=round(job.timeout, 1)), True
        except Exception as exc:
            result, error = None, _short_error(exc)
        if result is not None:  # only what the job asked for: context items were not translated for themselves
            # not a line whose roles are known wrong and that was not asked again (over the re-ask cap, no time
            # left, the request failed): the same text on a later screen gets its chance
            unsettled = {bid for bid, why in (getattr(tr, "last_role_reask", None) or {}).items()
                         if why in UNSETTLED_REASK}
            self._llm_cache_put(job, {bid: text for bid, text in result.translations.items()
                                      if bid in job.needed and bid not in unsettled})
        self._post(_LlmResult(job, result, error, ms(), cached=len(hits), timed_out=timed_out))

    # -- worker thread --------------------------------------------------------------------
    def _worker_main(self) -> None:
        try:
            while not self._stop_evt.is_set():
                self._wake.clear()
                self._drain_commands()
                self._ensure_ocr()
                if self._stop_evt.is_set():  # stop() may have given up on a slow engine load
                    break
                self._drain_results()
                now = self._clock()
                active = self._update_activity(now)
                if active and now >= self._next_poll:
                    self._next_poll = now + self._poll_s
                    self._poll(now)
                self._cancel_dead_batches()
                self._cancel_dead_llm_job()
                self._submit_due()
                self._submit_llm()
                self._request_confirmation()
                self._publish_if_changed()
                if self._stop_evt.is_set():
                    break
                timeout = (self._next_poll - self._clock()) if active else self.tuning.paused_wait_s
                if timeout > 0:
                    self._wake.wait(min(timeout, 1.0))
        except Exception as exc:
            log.exception("pipeline worker crashed")
            self._faults["worker"] = f"{MSG_ERROR}: {_short_error(exc)}"
            self._publish_if_changed()
        finally:
            self._close_capture()
            self._close_ocr()
            log.debug("pipeline worker stopped")

    def _reasons(self) -> frozenset[str]:
        with self._capture_lock:
            return frozenset(self._pause_reasons)

    def _drain_commands(self) -> None:
        while True:
            try:
                cmd, arg = self._commands.get_nowait()
            except queue.Empty:
                return
            if cmd == "region":
                if arg != self._region:
                    log.info("region set to %s", arg)
                self._region = arg
                self._reset_tracking()
            elif cmd == "lang":
                if arg != self._src_lang:
                    log.info("source language %s -> %s", self._src_lang, arg)
                    self._src_lang = arg
                    self._close_ocr()
                    self._ocr_retry_at = 0.0
                    self._reset_tracking()
            elif cmd == "llm_mode":
                if arg != self._llm_mode:
                    log.info("LLM mode %s -> %s", self._llm_mode, arg)
                    self._llm_mode = arg
                    if arg == "off":
                        self._drop_llm_job("LLM off")
                        self._llm_error = None
            elif cmd == "ctx_version":
                if arg != self._ctx_version:
                    self._ctx_version = arg
                    self._new_llm_epoch(f"context {arg!r}")
            elif cmd == "invalidate":
                self._new_llm_epoch("invalidated")

    def _reset_tracking(self) -> None:
        self._tracker.reset(self._src_lang)
        self._detector.reset()
        self._next_poll = self._clock()

    def _ensure_ocr(self) -> None:
        if self._ocr is not None and self._ocr_lang == self._src_lang:
            return
        if self._clock() < self._ocr_retry_at or self._stop_evt.is_set():
            return
        if "guard" in self._reasons():
            return  # no CUDA / model load (CPU burst, VRAM) while a protected game is in front
        self._close_ocr()
        lang = self._src_lang
        self._publish_if_changed()  # shows "loading" (or the previous failure) while the engine is created
        t0 = time.perf_counter()
        try:
            engine = self._ocr_factory(lang)
        except Exception as exc:
            log.warning("OCR engine creation failed (lang=%s)", lang, exc_info=True)
            self._faults["ocr_init"] = MSG_OCR_FAILED.format(err=_short_error(exc))
            self._ocr_retry_at = self._clock() + self.tuning.ocr_retry_s
            return
        self._faults.pop("ocr_init", None)
        self._ocr, self._ocr_lang = engine, lang
        self._metrics["ocr_engine"] = getattr(engine, "name", type(engine).__name__)
        log.info("OCR engine %s (lang=%s) ready in %.0f ms", self._metrics["ocr_engine"], lang,
                 (time.perf_counter() - t0) * 1000)

    def _close_ocr(self) -> None:
        engine, self._ocr, self._ocr_lang = self._ocr, None, None
        close = getattr(engine, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                log.warning("OCR engine close failed", exc_info=True)

    def _close_capture(self) -> None:
        cap, self._capture = self._capture, None
        if cap is not None:
            try:
                cap.close()
            except Exception:
                log.warning("capture close failed", exc_info=True)

    def _update_activity(self, now: float) -> bool:
        reasons = self._reasons()
        active = not reasons and self._region is not None and self._ocr is not None
        if active != self._active:
            self._active = active
            if active:
                log.info("pipeline running on %s", self._region)
                self._detector.reset()
                self._next_poll = now
            else:
                log.info("pipeline idle (paused: %s)", ",".join(sorted(reasons)) or "-")
                self._tracker.reset(self._src_lang)  # never leave stale translations on screen
                self._detector.reset()
        # A backend that captures in the background (WGC) is closed on any pause, others on guard/error.
        release = bool(reasons & self.tuning.release_capture_on) \
            or (bool(reasons) and getattr(self._capture, "background_capture", False))
        if release and self._capture is not None:
            log.info("releasing capture while paused (%s)", ",".join(sorted(reasons)))
            with self._capture_lock:
                self._close_capture()
        if "guard" in reasons:
            if self._guard_since is None:
                self._guard_since = now
            elif self._ocr is not None and now - self._guard_since >= self.tuning.release_ocr_after_guard_s:
                log.info("releasing the OCR engine: a protected game has been in front for %.0f s",
                         now - self._guard_since)
                self._close_ocr()
        else:
            self._guard_since = None
        return active

    def _ensure_capture(self, now: float) -> CaptureBackend | None:
        if self._capture is None and now >= self._capture_retry_at and not self._stop_evt.is_set():
            try:
                self._capture = self._capture_factory()
            except Exception as exc:
                log.warning("capture creation failed", exc_info=True)
                self._faults["capture"] = MSG_CAPTURE_FAILED.format(err=_short_error(exc))
                self._capture_retry_at = now + self.tuning.capture_retry_s
        return self._capture

    def _poll(self, now: float) -> None:
        region = self._region
        if region is None or self._stop_evt.is_set() or self.is_paused():
            return
        cap = self._ensure_capture(now)  # creating the backend grabs nothing yet
        if cap is None:
            return
        t0 = time.perf_counter()
        with self._capture_lock:
            if self._pause_reasons or self._stop_evt.is_set():
                return
            try:
                frame = cap.grab(region)
            except Exception as exc:
                log.warning("grab failed; recreating the capture backend", exc_info=True)
                self._faults["capture"] = MSG_CAPTURE_FAILED.format(err=_short_error(exc))
                self._close_capture()
                self._capture_retry_at = now + self.tuning.capture_retry_s
                return
            self.grab_count += 1
        grab_ms = (time.perf_counter() - t0) * 1000
        if frame is None:
            return
        self._faults.pop("capture", None)
        image = frame.image
        reason = self._detector.update(image, now)
        if reason is None:
            return

        t1 = time.perf_counter()
        try:
            boxes: list[TextBox] = self._ocr.recognize(image) if self._ocr is not None else []
        except Exception as exc:
            self._on_ocr_failure(exc, now)
            return
        t2 = time.perf_counter()
        if self.is_paused() or self._stop_evt.is_set():
            return  # paused during OCR: drop the result instead of showing it
        self._faults.pop("ocr", None)
        self._ocr_failures = self._ocr_rebuilds = 0

        groups = layout_blocks(boxes, self._src_lang, self.tuning.layout)
        colors = [block_colors(image, g.lines) for g in groups]
        dx, dy = frame.region.left - region.left, frame.region.top - region.top
        if dx or dy:  # the backend clamped the region (e.g. to one monitor)
            groups = [_offset_group(g, dx, dy) for g in groups]

        def image_box(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
            return box[0] - dx, box[1] - dy, box[2], box[3]

        def gone(t) -> bool:
            # OCR flicker leaves the pixels as they were; a scene change does not.
            old, new = self._thumbs.get(t.id), _thumb(image, image_box(t.group.box))
            return old is not None and new is not None \
                and float(np.mean(np.abs(new - old))) > self.tuning.vanish_threshold

        # Only a settled screen can tell a vanished block from OCR flicker by its pixels: over video or an
        # animated background ("forced" passes) the pixels under every block change all the time.
        res = self._tracker.update(
            groups, now, colors, gone=gone if reason == "settled" else None,
            new_settle_s=self.tuning.tracker.typewriter_wait_s if reason == "confirm" else 0.0)
        if res.alt and reason in ("settled", "idle"):
            self._detector.request_pass(now)  # read it again at the next poll: OCR jitter or a real change?
        for bid in (*res.added, *res.kept, *res.grown, *res.changed):
            t = self._tracker.get(bid)
            thumb = _thumb(image, image_box(t.group.box)) if t is not None else None
            if thumb is not None:
                self._thumbs[bid] = thumb
        alive = {t.id for t in self._tracker.tracks}
        self._thumbs = {k: v for k, v in self._thumbs.items() if k in alive}
        t3 = time.perf_counter()
        self.ocr_passes += 1
        m = self._metrics
        m.update(
            passes=self.ocr_passes, reason=reason, grab_ms=round(grab_ms, 2), ocr_ms=round((t2 - t1) * 1000, 1),
            layout_ms=round((t3 - t2) * 1000, 2), boxes=len(boxes), blocks=len(self._tracker),
            grabs=self.grab_count, llm_mode=self._llm_mode,
        )
        log.info(
            "ocr pass %d (%s): grab %.1f ms, ocr %.1f ms, layout %.1f ms; %d boxes -> %d blocks "
            "(+%d new, %d grown, %d changed, %d removed, %d missed, %d regrouped); translations in flight %d, "
            "last %s ms",
            self.ocr_passes, reason, grab_ms, m["ocr_ms"], m["layout_ms"], len(boxes), len(self._tracker),
            len(res.added), len(res.grown), len(res.changed), len(res.removed), len(res.missed), res.regrouped,
            self._tracker.pending_count(), m.get("translate_ms", "-"),
        )

    def _on_ocr_failure(self, exc: Exception, now: float) -> None:
        self._ocr_failures += 1
        log.warning("OCR failed (%d in a row)", self._ocr_failures, exc_info=True)
        self._faults["ocr"] = MSG_OCR_FAILED.format(err=_short_error(exc))
        if self._ocr_failures >= self.tuning.ocr_max_failures:
            # A dead engine (GPU reset, CUDNN_STATUS_EXECUTION_FAILED) never recovers: build a new one.
            # The default factory moves to the CPU if new engines keep failing.
            log.warning("rebuilding the OCR engine after %d failed passes", self._ocr_failures)
            self._ocr_failures = 0
            self._ocr_rebuilds += 1
            self._close_ocr()
            self._ocr_retry_at = 0.0
        else:
            self._detector.request_pass(now + self.tuning.ocr_failure_retry_s)

    def _request_confirmation(self) -> None:
        """Ask the detector for the OCR pass that confirms text which may still be changing."""
        at = self._tracker.next_check()
        if at is not None and "ocr" not in self._faults:
            self._detector.request_pass(at)

    def _submit_due(self) -> None:
        if not self._translator_ready.is_set() or self._stop_evt.is_set():
            return
        now = self._clock()
        # "only" mode while the LLM can take the text: no machine translation except for texts the LLM
        # declined; the others get one tier-2 cache lookup (per text and epoch) and wait for the LLM.
        epoch = self._llm_epoch
        only = self._llm_mode == "only" and self._llm_usable(now)

        def wanted(t: TrackedBlock) -> bool:
            return t.mt_fallback == t.source or t.llm_lookup != (t.source, epoch)

        if self._translator is None:  # creation failed: surface it on the blocks, retry never
            msg = MSG_TRANSLATOR_FAILED.format(err=self._translator_error or "?")
            for block_id, src in self._tracker.take_due(
                    now, where=(lambda t: t.mt_fallback == t.source) if only else None):
                self._tracker.apply_error(block_id, src, msg, now)
            return
        slots = self._max_inflight - self._inflight_batches
        if slots <= 0 or self._loop is None or now < self._hold_until:
            return
        confirm_at = self._tracker.next_check()
        if confirm_at is not None and now - 2 * self._poll_s < confirm_at <= now + self._poll_s:
            return  # the pass confirming the rest of this text is one poll away: send it all in one batch
        items = self._tracker.take_due(now, where=wanted if only else None)
        if not items:
            return
        ctx = self._llm_context() if self._context_provider is not None or self._llm_enabled() else None
        if ctx is not None:
            items = self._apply_glossary(items, ctx)
            if not items:
                return
        llm_keys: dict[int, str] | None = None
        speakers: dict[int, str | None] = {}
        screen: tuple[ScreenItem, ...] = ()
        if ctx is not None and self._llm_enabled():
            screen = self._screen(now)
            llm_keys, speakers = self._tier2_keys(screen, ctx)
        lookup: set[int] = set()
        if only:
            for bid, src in items:
                t = self._tracker.get(bid)
                if t is not None and t.mt_fallback != src:
                    lookup.add(bid)
                    t.llm_lookup = (src, epoch)
        # One batch per source language (English text in Japanese mode goes out with sl=en) and kind.
        by_lang: dict[tuple[str, bool], list[tuple[int, str]]] = {}
        for item in items:
            by_lang.setdefault((source_lang_for(item[1], self._src_lang), item[0] in lookup), []).append(item)
        for (src_lang, lookup_only), group in by_lang.items():
            if slots <= 0:  # at the in-flight limit: these stay due
                for block_id, src in group:
                    self._tracker.release(block_id, src)
                    t = self._tracker.get(block_id)
                    if t is not None and lookup_only:
                        t.llm_lookup = None
                continue
            self._batch_seq += 1
            batch = _Batch(self._batch_seq, group, src_lang, self._tgt_lang, time.perf_counter(), llm_keys, epoch,
                           lookup_only, speakers, screen, ctx)
            coro = self._run_batch(batch)
            try:
                fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
            except RuntimeError:  # loop closed (shutting down)
                coro.close()
                for block_id, src in group:
                    self._tracker.apply_error(block_id, src, "stopped", now)
                continue
            self._batches[batch.seq] = (batch, fut)
            self._inflight_batches += 1
            slots -= 1
            log.debug("translation batch %d: %d texts (%s)", batch.seq, len(group), src_lang)

    def _apply_glossary(self, items: list[tuple[int, str]], ctx: TranslationContext) -> list[tuple[int, str]]:
        """Show the profile's fixed rendering of texts that are exactly a glossary entry or character
        name (as tier 2, like a tier-2 cache hit) instead of sending them to machine translation, which
        would show a wrong name ("セラ" -> "huyết thanh") until the LLM replaced it. Returns the rest."""
        if not ctx.terms and not ctx.characters:
            return items
        rest: list[tuple[int, str]] = []
        for bid, src in items:
            target = glossary_rendering(src, ctx)
            if target is None:
                rest.append((bid, src))
                continue
            self._tracker.release(bid, src)
            self._tracker.apply_llm(bid, src, target, None, self._llm_epoch)
        if len(rest) < len(items):
            self._metrics["glossary_hits"] = self._metrics.get("glossary_hits", 0) + len(items) - len(rest)
        return rest

    def _cancel_dead_batches(self) -> None:
        """Cancel requests whose blocks have all left the screen (fast dialogue, a scene change), so
        the text on screen now does not queue behind them in the translator's rate limit."""
        if not self._batches:
            return
        dead: list[int] = []
        live_texts: set[tuple[str, str]] = set()
        for seq, (batch, _) in self._batches.items():
            if any(self._tracker.get(bid) is not None for bid, _ in batch.items):
                live_texts.update((batch.src, normalize_text(src, batch.src)) for _, src in batch.items)
            else:
                dead.append(seq)
        for seq in dead:
            batch, fut = self._batches[seq]
            # CachedTranslator sends a text once for all callers: keep a request a live batch waits on.
            if any((batch.src, normalize_text(src, batch.src)) in live_texts for _, src in batch.items):
                continue
            if fut.cancel():
                del self._batches[seq]
                self._inflight_batches = max(0, self._inflight_batches - 1)
                self.cancelled_batches += 1
                self._metrics["cancelled_batches"] = self.cancelled_batches
                log.info("cancelled translation batch %d (%d texts): its blocks left the screen",
                         seq, len(batch.items))

    def _drain_results(self) -> None:
        while True:
            try:
                r = self._results.get_nowait()
            except queue.Empty:
                return
            if isinstance(r, _LlmResult):
                self._apply_llm_result(r)
                continue
            if self._batches.pop(r.batch.seq, None) is None:
                continue  # cancelled just as it finished: already accounted for, its blocks are gone
            self._inflight_batches = max(0, self._inflight_batches - 1)
            now = self._clock()
            items = r.batch.items
            translations = r.translations or [None] * len(items)
            tiers = r.tiers or [TIER_MT] * len(items)
            msg = MSG_TRANSLATE_ERROR.format(err=r.error) if r.error is not None else ""
            applied = hits = sent = 0
            cached: dict[int, str] = {}
            for (bid, src), tr, tier in zip(items, translations, tiers):
                if tr is not None and tier >= TIER_LLM:  # tier-2 cache hit
                    hits += 1
                    self._tracker.release(bid, src)
                    if self._tracker.apply_llm(bid, src, tr, r.batch.speakers.get(bid), r.batch.epoch):
                        applied += 1
                        cached[bid] = tr
                elif tr is not None:
                    sent += 1
                    applied += self._tracker.apply_translation(bid, src, tr, tier)
                elif r.error is not None:
                    sent += 1
                    self._tracker.apply_error(bid, src, msg, now)
                else:  # lookup-only miss: the text waits for the LLM
                    self._tracker.release(bid, src)
            if hits:
                self._metrics["llm_cache_hits"] = self._metrics.get("llm_cache_hits", 0) + hits
            if cached and r.batch.ctx is not None and r.batch.epoch == self._llm_epoch:
                roles = {it.id: it.role_hint for it in r.batch.screen}
                if any(roles.get(bid, "dialog") in DIALOGUE_ROLES for bid in cached):  # the dialogue memory
                    self._notify_screen_result(
                        ScreenResult(cached, {bid: r.batch.speakers.get(bid) for bid in cached}, (), self._llm_model),
                        r.batch.screen, r.batch.ctx)
            if r.error is None:
                if sent:
                    self._fail_streak, self._hold_until = 0, 0.0
                    self._metrics["translate_ms"] = round(r.latency_ms)
                    self._metrics["translate_items"] = sent
                    log.info("translated %d texts in %.0f ms (%d applied, %d stale%s)", len(items), r.latency_ms,
                             applied, len(items) - applied, f", {hits} from the LLM cache" if hits else "")
                elif hits:
                    log.info("%d of %d texts from the LLM cache (%d applied)", hits, len(items), applied)
            else:
                log.warning("translation of %d texts failed after %.0f ms: %s", sent, r.latency_ms, r.error)
                if r.retryable:  # rate limit, network, server: do not hammer the endpoint with new texts
                    self._fail_streak += 1
                    hold = min(self.tuning.translate_hold_max_s,
                               self.tuning.translate_hold_s * 2 ** (self._fail_streak - 1))
                    self._hold_until = now + hold
                    log.warning("no new translation requests for %.0f s", hold)

    # -- contextual tier (worker thread) ----------------------------------------------------
    def _llm_enabled(self) -> bool:
        return self._llm_mode != "off" and self._contextual_factory is not None

    def _llm_usable(self, now: float) -> bool:
        """An LLM job could start now (not backing off, and contextual_factory not known to give None)."""
        return (self._llm_enabled() and now >= self._llm_hold_until
                and (self._llm_ready is not False or now >= self._llm_probe_at))

    def _llm_context(self) -> TranslationContext:
        if self._context_provider is not None:
            try:
                ctx = self._context_provider()
                if isinstance(ctx, TranslationContext):
                    return ctx
                log.warning("context_provider returned %s, not a TranslationContext", type(ctx).__name__)
            except Exception:
                log.warning("context_provider failed; translating without the profile", exc_info=True)
        return TranslationContext(version=self._ctx_version)

    @staticmethod
    def _ctx_key(ctx: TranslationContext) -> str:
        return f"{ctx.profile}:{ctx.version}"

    @classmethod
    def _tier2_keys(cls, items: tuple[ScreenItem, ...],
                    ctx: TranslationContext) -> tuple[dict[int, str], dict[int, str | None]]:
        """(tier-2 cache key, speaker) per item. A dialogue line's Vietnamese depends on who says it
        to whom (anh/em, tao/mày), so its key adds the speaker (the label above it) and the guessed
        listener, exactly as the LLM prompt sees them; labels and UI text use the context key alone."""
        base = cls._ctx_key(ctx)
        speakers = screen_speakers(items, ctx)
        listeners = guess_listeners(items, speakers, ctx.history, ctx)
        keys: dict[int, str] = {}
        out: dict[int, str | None] = {}
        for it in items:
            dialogue = it.role_hint in DIALOGUE_ROLES
            out[it.id] = speakers.get(it.id) if dialogue else None
            keys[it.id] = f"{base}|{out[it.id] or ''}>{listeners.get(it.id) or ''}" if dialogue else base
        return keys, out

    def _screen(self, now: float) -> tuple[ScreenItem, ...]:
        """Every confirmed text block on screen as a ScreenItem, in screen order, with role hints."""
        tracks = sorted((t for t in self._tracker.tracks if needs_translation(t.norm) and self._tracker.confirmed(t, now)),
                        key=lambda t: (t.group.box[1], t.group.box[0], t.id))
        region = self._region
        size = (region.width, region.height) if region is not None else (0, 0)
        roles = role_hints([t.group for t in tracks], size)
        return tuple(ScreenItem(t.id, t.source, tuple(t.group.box), role) for t, role in zip(tracks, roles))

    def _screen_items(self, due: list[TrackedBlock], now: float) -> tuple[tuple[ScreenItem, ...], dict[int, str]]:
        """The job's items (screen order, with role hints) and the blocks it asks for: the due blocks
        first (at most llm_max_needed - fewer after a timeout - and llm_max_chars), then the blocks
        nearest to them as context only (speaker labels, the rest of a menu), within llm_max_items /
        llm_context_chars. Context costs prompt tokens only, never answer tokens."""
        p = self.tuning
        screen = self._screen(now)
        needed: dict[int, str] = {}
        chars = 0
        cap = max(1, min(p.llm_max_needed, self._llm_size_cap))
        for t in due:
            if needed and (len(needed) >= cap or chars + len(t.source) > p.llm_max_chars):
                break
            needed[t.id] = t.source
            chars += len(t.source)
        order = {it.id: i for i, it in enumerate(screen)}
        at = [order[b] for b in needed if b in order]
        chosen = set(needed)
        context_chars = 0
        for it in sorted((it for it in screen if it.id not in chosen),
                         key=lambda it: (min((abs(order[it.id] - i) for i in at), default=0), order[it.id])):
            if len(chosen) >= p.llm_max_items:
                break
            if context_chars + len(it.text) <= p.llm_context_chars:
                chosen.add(it.id)
                context_chars += len(it.text)
        items = tuple(replace(it, context_only=it.id not in needed) for it in screen if it.id in chosen)
        return items, needed

    def _llm_timeout(self, needed: dict[int, str]) -> float:
        """llm_timeout_s, plus llm_timeout_per_item_s per text beyond the first and
        llm_timeout_per_char_s per character beyond 200, up to llm_timeout_max_s."""
        p = self.tuning
        chars = sum(len(s) for s in needed.values())
        t = (p.llm_timeout_s + p.llm_timeout_per_item_s * max(0, len(needed) - 1)
             + p.llm_timeout_per_char_s * max(0, chars - 200))
        return min(max(p.llm_timeout_s, p.llm_timeout_max_s), t)

    def _submit_llm(self) -> None:
        if not self._llm_enabled() or self._llm_job is not None or self._loop is None:
            return
        if not self._translator_ready.is_set() or self._stop_evt.is_set():
            return
        now = self._clock()
        if not self._llm_usable(now):
            return
        due = self._tracker.llm_due(now, self._llm_epoch)
        if not due:
            return
        if self._tracker.next_check() is not None and now - min(t.changed_at for t in due) < self.tuning.llm_settle_s:
            return  # other text is still being confirmed: one job should see the whole screen
        ctx = self._llm_context()
        items, needed = self._screen_items(due, now)
        if not needed:
            return
        self._llm_seq += 1
        keys, layout = self._tier2_keys(items, ctx)
        job = _LlmJob(self._llm_seq, items, needed, self._src_lang, self._tgt_lang, ctx, self._ctx_key(ctx),
                      self._llm_epoch, time.perf_counter(), keys, self._llm_timeout(needed), layout=layout)
        coro = self._run_llm_job(job)
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError:  # loop closed (shutting down)
            coro.close()
            return
        for bid, src in needed.items():
            self._tracker.mark_llm(bid, src)
        self._llm_job = (job, fut)
        self.llm_jobs += 1
        log.debug("LLM job %d: %d items on screen, %d to translate", job.seq, len(items), len(needed))

    def _cancel_dead_llm_job(self) -> None:
        """Drop the LLM job when none of the text it was asked for is still on screen (newest wins)."""
        if self._llm_job is None:
            return
        job, fut = self._llm_job
        for bid, src in job.needed.items():
            t = self._tracker.get(bid)
            if bid not in job.applied and t is not None and t.source == src:
                return
        if fut.cancel():
            self.cancelled_llm_jobs += 1
            self._metrics["llm_cancelled"] = self.cancelled_llm_jobs
            log.info("cancelled LLM job %d (%d texts): its text left the screen", job.seq, len(job.needed))
            self._end_llm_job(job, notify=True)

    def _drop_llm_job(self, reason: str) -> None:
        """Forget the job in flight (its results will be ignored) without calling on_screen_result."""
        if self._llm_job is None:
            return
        job, fut = self._llm_job
        if fut.cancel():
            self.cancelled_llm_jobs += 1
            self._metrics["llm_cancelled"] = self.cancelled_llm_jobs
        log.info("dropped LLM job %d: %s", job.seq, reason)
        self._end_llm_job(job, notify=False)

    def _new_llm_epoch(self, reason: str) -> None:
        self._llm_epoch += 1
        self._drop_llm_job(reason)
        log.info("LLM epoch %d (%s): tier-2 translations on screen will be redone", self._llm_epoch, reason)

    def _end_llm_job(self, job: _LlmJob, *, notify: bool) -> None:
        if self._llm_job is not None and self._llm_job[0] is job:
            self._llm_job = None
        for bid, src in job.needed.items():
            self._tracker.finish_llm(bid, src)
        if not notify or not job.applied or job.epoch != self._llm_epoch:
            return
        self._notify_screen_result(
            ScreenResult(dict(job.applied), {bid: job.speakers.get(bid) for bid in job.applied}, job.new_terms,
                         job.model, (time.perf_counter() - job.t0)), job.items, job.ctx)

    def _notify_screen_result(self, result: ScreenResult, items: tuple[ScreenItem, ...],
                              ctx: TranslationContext) -> None:
        if self._on_screen_result is None:
            return
        try:
            self._on_screen_result(result, items, ctx)
        except Exception:
            log.exception("on_screen_result callback failed")

    def _apply_llm_result(self, r: _LlmResult) -> None:
        job = r.job
        if self._llm_job is None or self._llm_job[0] is not job:
            return  # dropped or cancelled meanwhile
        now = self._clock()
        if r.unavailable:
            if self._llm_ready is not False:
                log.info("no contextual translator ready%s", "; Google meanwhile" if self._llm_mode == "only" else "")
            self._llm_ready = False
            self._llm_probe_at = now + self.tuning.llm_probe_s
            self._llm_error = None
            self._end_llm_job(job, notify=False)
            return
        res = r.result
        if res is not None:
            job.model = res.model or job.model
            for bid, src in job.needed.items():
                text = res.translations.get(bid)
                if bid in job.applied or text is None:
                    continue
                speaker = res.speakers.get(bid)
                if self._tracker.apply_llm(bid, src, text, speaker, job.epoch):
                    job.applied[bid] = text
                    job.speakers[bid] = speaker
            if res.new_terms:
                job.new_terms = (*job.new_terms, *res.new_terms)
        if r.partial:
            return
        self._metrics.update(llm_ms=round(r.latency_ms), llm_items=len(job.items), llm_needed=len(job.needed),
                             llm_jobs=self.llm_jobs, llm_model=job.model)
        only = self._llm_mode == "only"
        if r.error is None:
            if self._llm_ready is not True:
                log.info("contextual translator ready (%s)", job.model or "?")
            self._llm_ready, self._llm_fail_streak, self._llm_hold_until = True, 0, 0.0
            self._llm_error = None
            self._llm_size_cap = min(self.tuning.llm_max_needed, self._llm_size_cap * 2)
            for bid, src in job.needed.items():
                if bid not in job.applied:  # nothing usable for it: do not ask again in this epoch
                    self._tracker.skip_llm(bid, src, job.epoch, allow_mt=only)
            log.info("refined %d of %d texts in %.0f ms (%d items on screen, %d from the LLM cache)",
                     len(job.applied), len(job.needed), r.latency_ms, len(job.items), r.cached)
        elif r.timed_out and len(job.needed) > 1:
            # Too much for one job, not a broken server: ask for less at a time (no hold, so unrelated
            # screens are not blocked), machine translation meanwhile in "only" mode.
            self._llm_size_cap = max(1, len(job.needed) // 2)
            for bid, src in job.needed.items():
                if bid not in job.applied and only:
                    self._tracker.allow_mt(bid, src)
            log.warning("LLM job %d (%d texts) timed out after %.0f ms; next jobs ask for at most %d texts",
                        job.seq, len(job.needed), r.latency_ms, self._llm_size_cap)
        else:
            self._llm_fail_streak += 1
            hold = min(self.tuning.llm_hold_max_s, self.tuning.llm_hold_s * 2 ** (self._llm_fail_streak - 1))
            self._llm_hold_until = now + hold
            self._llm_error = MSG_LLM_ERROR.format(err=r.error)
            if r.timed_out:  # one text the model cannot finish in time: machine translation keeps it
                for bid, src in job.needed.items():
                    if bid not in job.applied:
                        self._tracker.skip_llm(bid, src, job.epoch, allow_mt=only)
            log.warning("LLM job %d (%d texts) failed after %.0f ms: %s; no LLM job for %.0f s%s", job.seq,
                        len(job.needed), r.latency_ms, r.error, hold,
                        " (Google meanwhile)" if only else "")
        self._end_llm_job(job, notify=True)

    def _pending_count(self, now: float) -> int:
        """Texts without a translation that are being translated (tier 1, or the LLM in "only" mode)."""
        n = self._tracker.pending_count()
        if self._llm_mode == "only" and self._llm_enabled():
            usable = self._llm_usable(now)
            epoch = self._llm_epoch
            for t in self._tracker.tracks:
                if t.translation is not None or t.inflight == t.source:
                    continue
                if t.llm_inflight == t.source or (usable and t.mt_fallback != t.source and t.error is None
                                                  and self._tracker.confirmed(t, now)
                                                  and self._tracker.needs_llm(t, epoch)):
                    n += 1
        return n

    def _refining_count(self) -> int:
        """Texts showing a tier-1 translation that the LLM job in flight is refining. None while the
        job only probes a contextual translator known to be unavailable (no model, server loading):
        the status would flip to "refining" and back every llm_probe_s."""
        if self._llm_job is None or self._llm_ready is False:
            return 0
        return sum(1 for t in self._tracker.tracks
                   if t.llm_inflight == t.source and t.translation is not None and t.quality < 2)

    # -- publishing -----------------------------------------------------------------------
    def _status(self) -> tuple[str, str]:
        with self._capture_lock:
            reasons = set(self._pause_reasons)
            error_message = self._error_message
        if "error" in reasons:
            return "error", error_message or MSG_ERROR
        if "guard" in reasons:
            return "guarded", MSG_GUARDED
        if "selecting" in reasons:
            return "paused", MSG_SELECTING
        if "user" in reasons:
            return "paused", MSG_PAUSED
        if self._region is None:
            return "idle", MSG_NO_REGION.format(hotkey=format_hotkey(self._config.hotkeys.select_region))
        if self._faults:
            return "error", next(iter(self._faults.values()))
        if self._ocr is None:
            return "running", MSG_LOADING_OCR
        pending = self._pending_count(self._clock())
        if pending:
            return "running", MSG_TRANSLATING.format(n=pending)
        refining = self._refining_count()
        if refining:
            return "running", MSG_REFINING.format(n=refining)
        errors = [b.error for b in self._tracker.tracks if b.error and b.translation is None]
        if errors:
            return "running", errors[0]
        if self._llm_error and self._llm_enabled():
            return "running", self._llm_error
        return "running", f"{self._src_lang.upper()}→{self._tgt_lang.upper()}"

    def _publish_if_changed(self) -> None:
        status, message = self._status()
        blocks = self._tracker.blocks() if status == "running" else ()
        pending = self._pending_count(self._clock())
        key = (self._region, blocks, status, message, self._src_lang, self._tgt_lang, pending)
        if key == self._published_key:
            return
        self._published_key = key
        self._seq += 1
        state = OverlayState(
            seq=self._seq, region=self._region, blocks=blocks, status=status, message=message,
            src_lang=self._src_lang, tgt_lang=self._tgt_lang, pending=pending, timestamp=time.time(),
            metrics=dict(self._metrics),
        )
        self._state = state
        try:
            self._on_state(state)
        except Exception:
            log.exception("on_state callback failed")
