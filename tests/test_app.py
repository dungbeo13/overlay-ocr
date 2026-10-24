"""Tests for the integration layer: app.main (CLI, config, App wiring with a fake pipeline, debug dump,
the local-LLM service with a fake server, glossary profiles), the dxcam -> GDI capture fallback and
the mixed-language helpers.

Run: python -m unittest tests.test_app -v
Qt runs on the offscreen platform when no QApplication exists yet (no window appears; the overlay
cannot be capture-excluded there, so the App must hold the pipeline in its error pause). No GPU, model
or llama-server is needed: the server and the contextual translator are fakes, profiles live in a
temporary directory.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtWidgets import QApplication

from app.capture.base import CaptureBackend, Frame, Region
from app.capture.fallback import FallbackCapture
from app.config import Config
from app.ocr.base import TextBox
from app.ocr.textnorm import join_lines
from app.pipeline.layout import is_lone_latin_letter, layout_blocks
from app.pipeline.types import Block, OverlayState
from app.translate.contextual import ContextualTranslator, ScreenItem, ScreenResult, Term, TranslationContext
from app.translate.textnorm import source_lang_for


def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([sys.argv[0]])
    return app


def pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        time.sleep(0.003)


def pump_until(pred, timeout: float = 3.0) -> bool:
    """Process Qt events until pred() is true (False on timeout)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        time.sleep(0.005)
    return bool(pred())


# --- fakes for the local LLM ------------------------------------------------------------------

class FakeServer:
    """Stands in for app.llm.server.LlamaServer: start() blocks for `load_s` (model load)."""

    instances: list["FakeServer"] = []

    def __init__(self, model_path: Path, load_s: float = 0.05, fail: Exception | None = None,
                 block: threading.Event | None = None) -> None:
        self.model_path = Path(model_path)
        self.model_name = self.model_path.stem
        self.load_s, self.fail, self.block = load_s, fail, block
        self.started = self.stopped = False
        self.crashed = False
        self.start_thread: int | None = None
        self.stop_calls = 0
        FakeServer.instances.append(self)

    def start(self, timeout: float) -> str:
        self.start_thread = threading.get_ident()
        if self.block is not None:
            self.block.wait(5.0)
        else:
            time.sleep(self.load_s)
        if self.fail is not None:
            raise self.fail
        if self.stopped:
            raise RuntimeError("stopped while loading")
        self.started = True
        return "http://127.0.0.1:18080"

    def stop(self) -> None:
        self.stop_calls += 1
        self.stopped = True
        if self.block is not None:
            self.block.set()

    def is_running(self) -> bool:
        return self.started and not self.stopped and not self.crashed

    @property
    def running(self) -> bool:
        return self.is_running()


class FakeLlmTranslator(ContextualTranslator):
    def __init__(self, base_url: str, model: str) -> None:
        self.base_url, self.model = base_url, model

    async def translate_screen(self, items, src, tgt, ctx):
        return ScreenResult({it.id: f"LLM:{it.text}" for it in items}, model=self.model)


def make_llm_service(config: Config, **server_kw):
    from app.main import LlmService

    svc = LlmService(config, resolve_model=lambda c: Path("Hy-MT2-1.8B-Q6_K.gguf"),
                     server_factory=lambda p: FakeServer(p, **server_kw),
                     translator_factory=FakeLlmTranslator)
    return svc


# --- mixed-language helpers ---------------------------------------------------------------------

class MixedLanguageTests(unittest.TestCase):
    def test_source_lang_for(self):
        self.assertEqual(source_lang_for("冒険を続ける", "ja"), "ja")
        self.assertEqual(source_lang_for("Harbor Notice", "ja"), "en")
        self.assertEqual(source_lang_for("HP 100/100", "ja"), "en")
        self.assertEqual(source_lang_for("Lv.5 勇者", "ja"), "ja")  # any kana/kanji: Japanese
        self.assertEqual(source_lang_for("12:30", "ja"), "ja")  # no letters: unchanged
        self.assertEqual(source_lang_for("Start", "en"), "en")
        self.assertEqual(source_lang_for("セーブ", "en"), "ja")

    def test_join_lines_keeps_english_word_breaks_in_ja_mode(self):
        self.assertEqual(join_lines(["They give", "residents a place"], "ja"), "They give residents a place")
        self.assertEqual(join_lines(["灯台の明かりが", "消えた。"], "ja"), "灯台の明かりが消えた。")
        self.assertEqual(join_lines(["HPが", "100"], "ja"), "HPが100")
        self.assertEqual(join_lines(["a", "b"], "en"), "a b")

    def test_lone_latin_letters_are_dropped(self):
        self.assertTrue(is_lone_latin_letter("O"))
        self.assertTrue(is_lone_latin_letter("C."))
        self.assertTrue(is_lone_latin_letter("x2"))
        self.assertFalse(is_lone_latin_letter("OK"))
        self.assertFalse(is_lone_latin_letter("火"))
        self.assertFalse(is_lone_latin_letter("あ"))
        boxes = [TextBox("O", (900, 80, 140, 140), 0.8), TextBox("冒険を続ける", (100, 400, 200, 40), 0.99)]
        self.assertEqual([g.source for g in layout_blocks(boxes, "ja")], ["冒険を続ける"])


class VanishedBlockTests(unittest.TestCase):
    def test_missed_block_is_removed_at_once_when_its_pixels_changed(self):
        from app.pipeline.layout import TextGroup
        from app.pipeline.matching import BlockTracker
        from app.pipeline.pipeline import THUMB_CHANGED, _thumb

        tr = BlockTracker("ja")
        g = TextGroup((10, 10, 100, 20), ((10, 10, 100, 20),), "冒険を続ける", False, 20)
        tr.update([g], 0.0)
        self.assertEqual(tr.update([], 0.2, gone=lambda t: False).missed, [1])  # OCR flicker: kept
        self.assertEqual(tr.update([g], 0.4).kept, [1])
        self.assertEqual(tr.update([], 0.6, gone=lambda t: True).removed, [1])  # scene changed: gone now

        dark = np.zeros((60, 200, 3), np.uint8)
        text = dark.copy()
        text[14:26, 12:100] = 255  # "glyphs"
        box = (10, 10, 100, 20)
        self.assertGreater(float(np.abs(_thumb(text, box) - _thumb(dark, box)).mean()), THUMB_CHANGED)
        noisy = np.clip(text.astype(int) + np.random.default_rng(1).integers(-6, 7, text.shape), 0, 255)
        self.assertLess(float(np.abs(_thumb(noisy.astype(np.uint8), box) - _thumb(text, box)).mean()), 3)
        self.assertIsNone(_thumb(text, (500, 500, 10, 10)))


# --- capture fallback ---------------------------------------------------------------------------

class FakeCapture(CaptureBackend):
    def __init__(self, name: str, frames: bool = True, raises: bool = False) -> None:
        self.name, self.frames, self.raises = name, frames, raises
        self.grabs = 0
        self.closed = False

    def grab(self, region: Region) -> Frame | None:
        self.grabs += 1
        if self.raises:
            raise OSError("duplication lost")
        if not self.frames:
            return None
        return Frame(np.zeros((region.height, region.width, 3), np.uint8), time.perf_counter(), region)

    def close(self) -> None:
        self.closed = True


class FallbackCaptureTests(unittest.TestCase):
    R = Region(0, 0, 8, 4)

    def make(self, primary: FakeCapture):
        self.now = 0.0
        self.fallback = FakeCapture("mss")
        cap = FallbackCapture(lambda: primary, lambda: self.fallback, after_s=1.0, retry_primary_s=5.0,
                              clock=lambda: self.now)
        return cap

    def test_primary_frames_are_used(self):
        primary = FakeCapture("dxcam")
        cap = self.make(primary)
        self.assertIsNotNone(cap.grab(self.R))
        self.assertEqual((primary.grabs, self.fallback.grabs), (1, 0))

    def test_switches_after_no_frames_and_retries_primary(self):
        primary = FakeCapture("dxcam", frames=False)
        cap = self.make(primary)
        self.assertIsNone(cap.grab(self.R))  # static screen: give dxcam a moment
        self.now = 0.5
        self.assertIsNone(cap.grab(self.R))
        self.now = 1.1
        self.assertIsNotNone(cap.grab(self.R))  # fallback now
        self.assertTrue(cap.using_fallback)
        n = primary.grabs
        self.now = 3.0
        cap.grab(self.R)
        self.assertEqual(primary.grabs, n)  # not retried before retry_primary_s
        primary.frames = True
        self.now = 6.2
        self.assertIsNotNone(cap.grab(self.R))
        self.assertFalse(cap.using_fallback)
        self.assertEqual(primary.grabs, n + 1)

    def test_primary_retries_back_off(self):
        # Each retry recreates the duplication and blocks a grab for ~0.3 s: not every 5 s for ever.
        primary = FakeCapture("dxcam", frames=False)
        cap = self.make(primary)
        cap.grab(self.R)
        self.now = 1.0
        cap.grab(self.R)  # GDI from now on
        tries = []
        for i in range(1, 600):  # a grab every 0.25 s for 150 s
            self.now = 1.0 + i * 0.25
            before = primary.grabs
            cap.grab(self.R)
            if primary.grabs > before:
                tries.append(self.now)
        self.assertEqual(tries, [6.0, 16.0, 36.0, 76.0, 136.0])  # 5, 10, 20, 40, then every 60 s
        primary.frames = True
        self.now = 196.0
        self.assertIsNotNone(cap.grab(self.R))
        self.assertFalse(cap.using_fallback)
        primary.frames = False  # dxcam drops out again later: the backoff starts over
        self.now = 200.0
        cap.grab(self.R)
        self.now = 201.5
        cap.grab(self.R)
        self.assertTrue(cap.using_fallback)
        n = primary.grabs
        self.now = 206.5
        cap.grab(self.R)
        self.assertEqual(primary.grabs, n + 1)

    def test_primary_error_falls_back_at_once(self):
        primary = FakeCapture("dxcam", raises=True)
        cap = self.make(primary)
        self.assertIsNotNone(cap.grab(self.R))
        self.assertTrue(primary.closed)
        self.assertTrue(cap.using_fallback)
        cap.close()
        self.assertTrue(self.fallback.closed)
        with self.assertRaises(RuntimeError):
            cap.grab(self.R)


# --- app.main --------------------------------------------------------------------------------

class FakePipeline:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.started = self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self, timeout: float = 5.0) -> None:
        self.stopped = True

    def __getattr__(self, name: str):
        if name.startswith("set_"):
            return lambda *a: self.calls.append((name, *a))
        raise AttributeError(name)


class CliTests(unittest.TestCase):
    def test_parse_and_overrides_do_not_touch_persisted_config(self):
        from app.main import apply_overrides, build_parser

        args = build_parser().parse_args(["--region=-1920,0,800,600", "--lang", "en", "--device", "cpu",
                                          "--no-guard", "--capture", "mss", "--exit-after", "2.5"])
        persisted = Config(region=[1, 2, 3, 4], src_lang="ja", guard_mode="pause")
        cfg = apply_overrides(copy.deepcopy(persisted), args)
        self.assertEqual((cfg.region, cfg.src_lang, cfg.ocr_device, cfg.guard_mode, cfg.capture_backend),
                         ([-1920, 0, 800, 600], "en", "cpu", "off", "mss"))
        self.assertEqual((persisted.region, persisted.src_lang, persisted.guard_mode), ([1, 2, 3, 4], "ja", "pause"))
        self.assertEqual(args.exit_after, 2.5)
        for bad in (["--region", "1,2,3"], ["--region", "0,0,0,10"], ["--lang", "fr"], ["--llm-mode", "maybe"]):
            with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
                build_parser().parse_args(bad)

    def test_llm_and_profile_overrides(self):
        from app.main import apply_overrides, build_parser

        persisted = Config(llm_mode="refine", profile="default")
        cfg = apply_overrides(copy.deepcopy(persisted), build_parser().parse_args(
            ["--llm-mode", "only", "--llm-model", "Hy-MT2-7B-Q4_K_M.gguf", "--profile", " Sea of Stars "]))
        self.assertEqual((cfg.llm_mode, cfg.llm_model, cfg.profile), ("only", "Hy-MT2-7B-Q4_K_M.gguf", "Sea of Stars"))
        self.assertEqual((persisted.llm_mode, persisted.llm_model, persisted.profile), ("refine", "", "default"))
        cfg = apply_overrides(Config(), build_parser().parse_args(["--llm-mode", "only", "--no-llm"]))
        self.assertEqual(cfg.llm_mode, "off")  # --no-llm wins


class AppTestBase(unittest.TestCase):
    """An App with a fake pipeline, a fake llama-server, and profiles in a temporary directory."""

    ARGS = ["--no-hotkeys", "--no-guard", "--region", "10,20,300,100"]
    CONFIG: dict = {}
    SERVER_KW: dict = {}  # FakeServer arguments

    def setUp(self):
        from app import main as app_main
        from app.main import App, build_parser
        from app.profiles import ProfileStore
        from app.translate.memory import MemoryBank

        self.qapp = qapp()
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.config_path = tmp / "config.json"
        patcher = mock.patch.object(app_main, "CONFIG_PATH", self.config_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dump = tmp / "dump"
        args = build_parser().parse_args([*self.ARGS, "--debug-dump", str(self.dump)])
        self.persisted = Config(region=[1, 2, 3, 4], **self.CONFIG)
        self.config = app_main.apply_overrides(copy.deepcopy(self.persisted), args)
        self.store = ProfileStore(tmp / "profiles", rescan_s=0.0)
        self.memory = MemoryBank()
        FakeServer.instances = []
        self.llm = make_llm_service(self.config, **self.SERVER_KW)
        self.prepare()
        self.app = App(self.qapp, self.config, self.persisted, args, llm=self.llm, profiles=self.store,
                       memory=self.memory)
        self.fake = FakePipeline()
        self.app.pipeline = self.fake
        self.app.start()

    def prepare(self) -> None:
        """Hook: runs before the App is created (profiles to put in the store, ...)."""

    def tearDown(self):
        self.app.shutdown()
        self.qapp.aboutToQuit.disconnect(self.app.shutdown)
        self.app.overlay.deleteLater()
        pump(20)
        self.tmp.cleanup()

    def saved_config(self) -> dict:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def calls(self, name: str) -> list[tuple]:
        return [c for c in self.fake.calls if c[0] == name]


class AppWiringTests(AppTestBase):
    def state(self, seq: int, **kw) -> OverlayState:
        base = dict(seq=seq, region=Region(10, 20, 300, 100), status="running", timestamp=time.time())
        base.update(kw)
        return OverlayState(**base)

    def test_start_holds_pipeline_when_overlay_is_not_excluded(self):
        self.assertTrue(self.fake.started)
        if not self.app.overlay._native:  # offscreen platform: no HWND, exclusion impossible
            self.assertIn(("set_error", "Lỗi: không ẩn được overlay khỏi capture"), self.fake.calls)

    def test_states_cross_threads_reach_overlay_tray_and_dump(self):
        block = Block(id=1, box=(10, 10, 100, 20), lines=((10, 10, 100, 20),), source="冒険", translation="Phiêu lưu")
        t = threading.Thread(target=lambda: self.app._emit_state(self.state(1, blocks=(block,))))
        t.start()
        t.join()
        pump(50)
        self.assertEqual(self.app.overlay.state().seq, 1)
        self.assertIn("JA→VI", self.app.tray.tooltip())
        pump(1100)  # snapshot timer
        rows = [json.loads(x) for x in (self.dump / "states.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["seq"], 1)
        self.assertEqual(rows[0]["blocks"][0]["translation"], "Phiêu lưu")
        self.assertIn("recv_ts", rows[0])
        self.assertTrue((self.dump / "overlay_1.png").exists())
        self.assertTrue((self.dump / "run.json").exists())

    def test_user_pause_guard_lang_and_text_toggle(self):
        self.app.toggle_pause()
        self.app._on_guard_changed(True, "game.exe")
        self.assertTrue(self.app.overlay._guarded)
        self.app._on_guard_changed(False, "")
        self.app.toggle_pause()
        self.assertEqual([c for c in self.fake.calls if c[0] == "set_paused"],
                         [("set_paused", True, "user"), ("set_paused", True, "guard"),
                          ("set_paused", False, "guard"), ("set_paused", False, "user")])
        self.app.set_lang("en")
        self.app.set_lang("en")
        self.app.set_lang("xx")
        self.assertEqual([c for c in self.fake.calls if c[0] == "set_src_lang"], [("set_src_lang", "en")])
        self.assertEqual(json.loads(self.config_path.read_text(encoding="utf-8"))["src_lang"], "en")
        self.app.toggle_text()
        self.assertFalse(self.app.overlay.text_visible())

    def test_region_selection_saves_config_and_resumes(self):
        self.app.select_region()
        self.assertIn(("set_paused", True, "selecting"), self.fake.calls)
        self.assertTrue(self.app.selector.is_active())
        self.app.selector.simulate_press(100, 100)
        self.app.selector.simulate_release(500, 300)
        self.assertIn(("set_region", Region(100, 100, 400, 200)), self.fake.calls)
        self.assertEqual(self.fake.calls[-1], ("set_paused", False, "selecting"))
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["region"], [100, 100, 400, 200])
        self.assertEqual(saved["guard_mode"], "pause")  # --no-guard is not persisted

    def test_guard_mode_from_tray_is_saved(self):
        self.app.set_guard_mode("warn")
        self.assertIsNotNone(self.app.guard)
        self.assertEqual(json.loads(self.config_path.read_text(encoding="utf-8"))["guard_mode"], "warn")
        self.app.set_guard_mode("off")
        self.assertFalse(self.app.guard.current()[0])

    def test_exclusion_lost_and_restored(self):
        self.app._exclusion_error = False
        self.app._on_exclusion_changed(False)
        self.app._on_exclusion_changed(True)
        self.assertEqual(self.fake.calls[-2:], [("set_error", "Lỗi: không ẩn được overlay khỏi capture"),
                                                ("set_error", None)])

    def test_shutdown_is_idempotent(self):
        self.app.shutdown()
        self.app.shutdown()
        self.assertTrue(self.fake.stopped)
        self.app._emit_state(self.state(5))  # ignored after shutdown
        pump(20)
        self.assertNotEqual(getattr(self.app.overlay.state(), "seq", None), 5)

    def test_tier2_blocks_are_drawn_like_tier1(self):
        from app.overlay.render import block_text

        b = Block(id=1, box=(10, 10, 100, 20), lines=((10, 10, 100, 20),), source="冒険", translation="Phiêu lưu",
                  quality=2, speaker="セラ")
        self.assertEqual(block_text(b), ("Phiêu lưu", False))  # not dimmed: a refinement never flashes


# --- local LLM service ---------------------------------------------------------------------------

class LlmServiceTests(unittest.TestCase):
    def setUp(self):
        FakeServer.instances = []
        self.statuses: list[str] = []

    def service(self, config: Config | None = None, **kw):
        svc = make_llm_service(config or Config(llm_mode="refine"), **kw)
        svc.on_status = self.statuses.append
        self.addCleanup(lambda: svc.stop(wait=True))
        return svc

    @staticmethod
    def wait(pred, timeout: float = 3.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end and not pred():
            time.sleep(0.01)
        return bool(pred())

    def test_start_loads_in_the_background_and_hands_out_one_translator(self):
        svc = self.service(load_s=0.4)
        caller = threading.get_ident()
        t0 = time.perf_counter()
        self.assertTrue(svc.start())
        self.assertLess(time.perf_counter() - t0, 0.1)  # never waits for the model
        self.assertFalse(svc.start())  # already loading
        self.assertTrue(svc.is_loading())
        self.assertIsNone(svc.translator())  # not up yet: the pipeline uses Google meanwhile
        self.assertTrue(self.wait(svc.is_running))
        [server] = FakeServer.instances
        self.assertNotEqual(server.start_thread, caller)
        tr = svc.translator()
        self.assertIsInstance(tr, FakeLlmTranslator)
        self.assertIs(svc.translator(), tr)
        self.assertEqual((tr.base_url, tr.model, svc.model_name), ("http://127.0.0.1:18080", "Hy-MT2-1.8B-Q6_K",
                                                                   "Hy-MT2-1.8B-Q6_K"))
        self.assertEqual(self.statuses, ["LLM: đang tải model…", "LLM: sẵn sàng (Nhẹ 1.8B)"])
        svc.stop(status="LLM: tắt")
        self.assertTrue(server.stopped)
        self.assertFalse(svc.is_running())
        self.assertEqual(self.statuses[-1], "LLM: tắt")

    def test_stop_abandons_a_load_in_progress(self):
        gate = threading.Event()
        svc = self.service(block=gate)
        svc.start()
        self.assertTrue(self.wait(lambda: bool(FakeServer.instances) and FakeServer.instances[0].start_thread))
        t0 = time.perf_counter()
        svc.stop(wait=True)
        self.assertLess(time.perf_counter() - t0, 2.0)
        self.assertFalse(svc.is_running() or svc.is_loading())
        self.assertTrue(all(s.stopped for s in FakeServer.instances))
        self.assertNotIn("LLM: sẵn sàng (Nhẹ 1.8B)", self.statuses)
        self.assertFalse(any(s.startswith("LLM lỗi") for s in self.statuses))  # stopping is not a failure

    def test_lazy_restart_after_a_release_and_after_a_crash(self):
        svc = self.service()
        svc.start()
        self.assertTrue(self.wait(svc.is_running))
        svc.stop()  # e.g. released behind an anti-cheat game
        self.assertIsNone(svc.translator())  # the pipeline asks: a wanted server is loaded again
        self.assertTrue(self.wait(svc.is_running))
        self.assertEqual(len(FakeServer.instances), 2)
        self.assertIsNotNone(svc.translator())
        FakeServer.instances[-1].crashed = True
        with self.assertLogs("app", "WARNING"):
            self.assertIsNone(svc.translator())
        self.assertEqual(self.statuses[-1], "LLM lỗi: server đã dừng")
        self.assertEqual(len(FakeServer.instances), 2)  # restart waits LLM_RESTART_S
        svc.guarded = True
        self.assertFalse(svc.start())  # never while a protected game is in front
        svc.guarded, svc.wanted = False, False
        self.assertFalse(svc.start())  # LLM mode off

    def test_missing_model_is_not_retried_until_asked(self):
        from app.main import LlmService

        svc = LlmService(Config(), resolve_model=lambda c: None, server_factory=lambda p: FakeServer(p))
        svc.on_status = self.statuses.append
        with self.assertLogs("app", "WARNING"):
            svc.start()
            self.assertTrue(self.wait(lambda: not svc.is_loading() and self.statuses[-1:] == ["LLM: chưa có model"]))
        self.assertIsNone(svc.translator())
        self.assertFalse(svc.start(lazy=True))
        self.assertEqual(FakeServer.instances, [])

    def test_failed_start_is_reported(self):
        svc = self.service(fail=OSError("CUDA out of memory"))
        with self.assertLogs("app", "WARNING"):
            svc.start()
            self.assertTrue(self.wait(lambda: self.statuses[-1:] == ["LLM lỗi: CUDA out of memory"]))
        self.assertTrue(FakeServer.instances[0].stopped)
        self.assertFalse(svc.is_running())

    def test_no_llama_server_children(self):
        from app.main import llama_children, reap_llama_children

        self.assertEqual(llama_children(), [])
        self.assertEqual(reap_llama_children(), 0)

    def test_restart_limit_counts_servers_that_crash_soon_after_loading(self):
        from app.main import LLM_MAX_RESTARTS, LLM_STABLE_S, LlmService

        now = [1000.0]
        svc = LlmService(Config(llm_mode="refine"), resolve_model=lambda c: Path("m.gguf"),
                         server_factory=lambda p: FakeServer(p, load_s=0.0), translator_factory=FakeLlmTranslator,
                         clock=lambda: now[0])
        svc.on_status = self.statuses.append
        self.addCleanup(lambda: svc.stop(wait=True))
        svc.start()
        self.assertTrue(self.wait(svc.is_running))
        with self.assertLogs("app", "WARNING") as logs:
            for _ in range(LLM_MAX_RESTARTS + 2):  # ready, then dead on its first use (CUDA error, no VRAM)
                if svc.is_running():
                    FakeServer.instances[-1].crashed = True
                svc.translator()
                now[0] += 30.0  # past every restart delay, below LLM_STABLE_S
                svc.translator()
                self.wait(lambda: svc.is_running() or not svc.is_loading(), 1.0)
        self.assertEqual(len(FakeServer.instances), LLM_MAX_RESTARTS)  # the first + LLM_MAX_RESTARTS - 1 restarts
        self.assertTrue(any("no more automatic restarts" in line for line in logs.output), logs.output)
        # A server that stays up LLM_STABLE_S counts as working: the count starts over.
        svc.start()  # explicit (tray): resets the limit
        self.assertTrue(self.wait(svc.is_running))
        with self.assertLogs("app", "WARNING"):
            FakeServer.instances[-1].crashed = True
            svc.translator()
        now[0] += 30.0
        svc.translator()
        self.assertTrue(self.wait(svc.is_running))
        self.assertEqual(svc._failures, 1)
        now[0] += LLM_STABLE_S + 1
        self.assertIsNotNone(svc.translator())
        self.assertEqual(svc._failures, 0)

    def test_stop_closes_servers_that_can_refuse_a_later_start(self):
        class Closable(FakeServer):
            def close(self) -> None:
                self.closed = True
                self.stop()

        from app.main import LlmService

        gate = threading.Event()
        svc = LlmService(Config(llm_mode="refine"), resolve_model=lambda c: Path("m.gguf"),
                         server_factory=lambda p: Closable(p, block=gate), translator_factory=FakeLlmTranslator)
        svc.start()
        self.assertTrue(self.wait(lambda: bool(FakeServer.instances) and FakeServer.instances[0].start_thread))
        svc.stop(wait=True)
        self.assertTrue(getattr(FakeServer.instances[0], "closed", False))  # close(), not just stop()
        self.assertFalse(svc.is_loading() or svc.is_running())


# --- App: LLM lifecycle, profiles, dialogue memory ------------------------------------------------

class AppLlmTests(AppTestBase):
    CONFIG = {"llm_release_after_guard_s": 0.3}

    def running(self) -> list[FakeServer]:
        return [s for s in FakeServer.instances if s.is_running()]

    def test_server_starts_at_startup_and_shutdown_stops_it(self):
        self.assertTrue(pump_until(lambda: self.llm.is_running()))
        self.assertTrue(pump_until(lambda: self.app.tray.llm_status() == "LLM: sẵn sàng (Nhẹ 1.8B)"))
        self.assertIsInstance(self.app._contextual_factory(), FakeLlmTranslator)
        self.app.shutdown()
        self.assertEqual(self.running(), [])
        self.assertFalse(self.llm.is_running() or self.llm.wanted)
        self.assertIsNone(self.app._contextual_factory())  # nothing is restarted after shutdown
        time.sleep(0.1)
        self.assertEqual(len(FakeServer.instances), 1)

    def test_guard_release_timer_stops_the_server_and_it_restarts_lazily(self):
        self.assertTrue(pump_until(self.llm.is_running))
        first = FakeServer.instances[0]
        self.app._on_guard_changed(True, "dota2.exe")
        self.assertTrue(self.llm.guarded)
        pump(100)
        self.assertTrue(first.is_running())  # not before llm_release_after_guard_s
        self.assertTrue(pump_until(lambda: first.stopped, 2.0))
        self.assertEqual(self.app.llm_releases, 1)
        self.assertTrue(pump_until(lambda: "giải phóng" in self.app.tray.llm_status()))
        self.assertIsNone(self.app._contextual_factory())  # still guarded: nothing is loaded
        pump(100)
        self.assertEqual(len(FakeServer.instances), 1)
        self.app._on_guard_changed(False, "")
        self.assertEqual(len(FakeServer.instances), 1)  # lazily: when the pipeline next asks
        self.assertIsNone(self.app._contextual_factory())
        self.assertTrue(pump_until(self.llm.is_running))
        self.assertEqual(len(FakeServer.instances), 2)
        self.assertIsInstance(self.app._contextual_factory(), FakeLlmTranslator)

    def test_guard_that_clears_quickly_keeps_the_server(self):
        self.assertTrue(pump_until(self.llm.is_running))
        self.app._on_guard_changed(True, "dota2.exe")
        pump(100)
        self.app._on_guard_changed(False, "")
        pump(500)
        self.assertEqual((self.app.llm_releases, len(self.running())), (0, 1))

    def test_llm_mode_from_tray(self):
        self.assertTrue(pump_until(self.llm.is_running))
        self.app.set_llm_mode("off")
        self.assertEqual(self.calls("set_llm_mode"), [("set_llm_mode", "off")])
        self.assertEqual(self.saved_config()["llm_mode"], "off")
        self.assertTrue(pump_until(lambda: self.running() == []))
        self.assertTrue(pump_until(lambda: self.app.tray.llm_status() == "LLM: tắt"))
        self.assertIsNone(self.app._contextual_factory())
        self.app.set_llm_mode("only")
        self.assertTrue(pump_until(self.llm.is_running))
        self.assertEqual(self.saved_config()["llm_mode"], "only")
        with self.assertLogs("app", "WARNING"):
            self.app.set_llm_mode("sometimes")
        self.assertEqual(self.calls("set_llm_mode"), [("set_llm_mode", "off"), ("set_llm_mode", "only")])
        self.assertEqual(self.app.config.llm_mode, "only")

    def test_llm_model_from_tray_reloads_the_server(self):
        self.assertTrue(pump_until(self.llm.is_running))
        self.assertTrue(self.app.tray._model_actions["auto"].isChecked())
        self.app.set_llm_model("quality")
        self.assertEqual((self.app.config.llm_model, self.saved_config()["llm_model"]), ("quality", "quality"))
        self.assertTrue(self.app.tray._model_actions["quality"].isChecked())
        self.assertTrue(pump_until(lambda: len(FakeServer.instances) == 2 and self.llm.is_running()))
        self.assertTrue(FakeServer.instances[0].stopped)
        self.assertEqual(len(self.running()), 1)  # never two models at once
        self.app.set_llm_model("auto")
        self.assertEqual(self.saved_config()["llm_model"], "")
        with self.assertLogs("app", "WARNING"):
            self.app.set_llm_model("huge")

    def test_idle_server_is_released_and_comes_back(self):
        self.assertTrue(pump_until(self.llm.is_running))
        self.app._check_llm_idle()
        self.assertTrue(self.llm.is_running())  # just used
        self.llm._last_used -= 3600
        self.app._check_llm_idle()
        self.assertTrue(pump_until(lambda: self.running() == []))
        self.assertTrue(pump_until(lambda: "không dùng" in self.app.tray.llm_status()))
        self.assertIsNone(self.app._contextual_factory())  # text again: loaded lazily
        self.assertTrue(pump_until(self.llm.is_running))
        self.assertEqual(len(FakeServer.instances), 2)


class AppGuardWhileLoadingTests(AppTestBase):
    SERVER_KW = {"load_s": 3.0}

    def test_guard_abandons_a_load_in_progress(self):
        self.assertTrue(pump_until(lambda: bool(FakeServer.instances) and FakeServer.instances[0].start_thread))
        self.assertTrue(self.llm.is_loading())  # inside the (3 s) model load
        self.app._on_guard_changed(True, "dota2.exe")
        self.assertTrue(pump_until(lambda: not self.llm.is_loading(), 1.0))  # not after the 3 s load
        self.assertTrue(pump_until(lambda: FakeServer.instances[0].stopped, 1.0))
        self.assertTrue(pump_until(lambda: "chờ game anti-cheat" in self.app.tray.llm_status()))
        self.assertIsNone(self.app._contextual_factory())  # nothing loads while guarded
        self.assertEqual(len(FakeServer.instances), 1)


class AppNoLlmTests(AppTestBase):
    ARGS = [*AppTestBase.ARGS, "--no-llm"]

    def test_no_server_with_llm_off(self):
        pump(150)
        self.assertEqual(FakeServer.instances, [])
        self.assertEqual(self.app.tray.llm_status(), "LLM: tắt")
        self.assertIsNone(self.app._contextual_factory())
        self.app.set_lang("en")  # saves config.json
        self.assertEqual(self.saved_config()["llm_mode"], "refine")  # --no-llm is not persisted


class AppProfileTests(AppTestBase):
    CONFIG = {"profile": "Sea of Stars", "llm_history_lines": 3, "src_lang": "en"}

    def prepare(self) -> None:
        from app.profiles import Profile

        self.store.save(Profile("Sea of Stars", processes=["SeaOfStars.exe"], style="giọng phiêu lưu",
                                terms=[Term("ゼール", "Zale", "person")]))
        self.store.save(Profile("Ys X", processes=["ys10.exe"], src_lang="ja"))

    def test_active_profile_context_with_history(self):
        from app.main import profile_version

        self.assertEqual(self.app.profile_name, "Sea of Stars")
        self.assertEqual(self.app.tray.active_profile(), "Sea of Stars")
        mem = self.memory.get("Sea of Stars")
        for i in range(5):
            mem.add("ゼール", f"line {i}", f"dòng {i}")
        ctx = self.app._context()
        self.assertEqual((ctx.profile, ctx.style), ("Sea of Stars", "giọng phiêu lưu"))
        self.assertEqual([t.target for t in ctx.terms], ["Zale"])
        self.assertEqual([h.source for h in ctx.history], ["line 2", "line 3", "line 4"])  # llm_history_lines
        profile = self.store.get("Sea of Stars")
        self.assertEqual(ctx.version, profile_version(profile))
        # Auto terms the LLM proposes do not change the version (no re-translation, cache stays valid)...
        self.assertEqual(self.store.add_terms("Sea of Stars", [Term("ヴァレア", "Valere", "person")]), 1)
        ctx2 = self.app._context()
        self.assertEqual(ctx2.version, ctx.version)
        self.assertIn("Valere", [t.target for t in ctx2.terms])
        # ...a user edit does.
        p = self.store.get("Sea of Stars")
        p.style = "giọng cổ trang"
        self.store.save(p)
        ctx3 = self.app._context()
        self.assertNotEqual(ctx3.version, ctx.version)
        # Deleting a (wrong) auto term in the editor is a user edit too: the old renderings are redone.
        p = self.store.get("Sea of Stars")
        p.terms = [t for t in p.terms if not t.auto]
        p.rejected = ["ヴァレア"]  # what the editor records for a deleted suggestion
        self.store.save(p)
        self.assertNotEqual(self.app._context().version, ctx3.version)

    def test_profile_switch_from_tray_and_by_foreground_exe(self):
        self.assertTrue(any(t.strip() == "Ys X" for t in self.app.tray.action_texts()))
        self.app.set_profile("Ys X")
        self.assertEqual((self.app.profile_name, self.app.tray.active_profile()), ("Ys X", "Ys X"))
        self.assertEqual(self.saved_config()["profile"], "Ys X")
        [(_, key)] = self.calls("set_context_version")
        self.assertTrue(key.startswith("Ys X:"))
        self.assertEqual(self.calls("set_src_lang"), [("set_src_lang", "ja")])  # the profile's language
        self.app.set_profile("Ys X")  # same profile: nothing to redo
        self.assertEqual(len(self.calls("set_context_version")), 1)
        with self.assertLogs("app", "INFO") as logs, mock.patch.object(self.app.tray, "show_message") as shown:
            self.app._on_foreground_changed("seaofstars.EXE")
        self.assertEqual(self.app.profile_name, "Sea of Stars")
        self.assertTrue(any("seaofstars.EXE" in line and "Sea of Stars" in line for line in logs.output))
        [(args, _)] = shown.call_args_list  # an automatic switch is announced
        self.assertIn("Sea of Stars", args[1])
        self.assertIn("seaofstars.EXE", args[1])
        self.assertEqual(len(self.calls("set_context_version")), 2)
        self.app._on_foreground_changed("notepad.exe")  # no profile lists it: keep the current one
        self.assertEqual(self.app.profile_name, "Sea of Stars")
        with self.assertLogs("app", "WARNING"):
            self.app.set_profile("No such game")
        self.assertEqual(self.app.profile_name, "default")

    def test_glossary_edits_redo_tier2_but_auto_terms_do_not(self):
        self.store.add_terms("Sea of Stars", [Term("ヴァレア", "Valere", "person")])
        pump(100)
        self.assertEqual(self.calls("set_context_version"), [])
        p = self.store.get("Sea of Stars")
        p.terms.append(Term("ソルスティス", "Solstice", "place"))
        self.store.save(p)  # what the glossary editor's save does
        self.assertTrue(pump_until(lambda: len(self.calls("set_context_version")) == 1))

    def test_screen_results_feed_memory_and_auto_terms(self):
        items = (ScreenItem(1, "ゼール", (40, 400, 80, 30), "name"),
                 ScreenItem(2, "行こう、ヴァレア！", (40, 440, 600, 30), "dialog"),
                 ScreenItem(3, "設定", (900, 20, 60, 30), "ui"),
                 ScreenItem(4, "夜明けまでに戻る。", (300, 650, 500, 30), "subtitle"))
        result = ScreenResult({1: "Zale", 2: "Đi thôi, Valere!", 3: "Cài đặt", 4: "Sẽ về trước bình minh."},
                              {2: None, 4: "ヴァレア"}, (Term("ヴァレア", "Valere", "person"),), "fake")
        self.app._on_screen_result(result, items)
        lines = self.memory.get("Sea of Stars").recent(10)
        self.assertEqual([(h.speaker, h.source, h.translation) for h in lines],
                         [("ゼール", "行こう、ヴァレア！", "Đi thôi, Valere!"),  # speaker from the name label
                          ("ヴァレア", "夜明けまでに戻る。", "Sẽ về trước bình minh.")])
        self.assertIn("Valere", [t.target for t in self.store.get("Sea of Stars").terms if t.auto])
        self.app.config.llm_auto_terms = False
        self.app._on_screen_result(ScreenResult({2: "x"}, {}, (Term("セレスト", "Celeste", "place"),)), items)
        self.assertNotIn("Celeste", [t.target for t in self.store.get("Sea of Stars").terms])
        # --debug-dump: one llm.jsonl line per screen result (speakers, new terms, memory size).
        rows = [json.loads(x) for x in (self.dump / "llm.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["profile"], rows[0]["model"], rows[0]["memory_lines"], rows[0]["auto_terms_added"]),
                         ("Sea of Stars", "fake", 2, 1))
        self.assertEqual(rows[0]["speakers"], {"2": None, "4": "ヴァレア"})
        self.assertEqual(rows[0]["new_terms"], [["ヴァレア", "Valere", "person"]])
        self.assertEqual([it["role"] for it in rows[0]["items"]], ["name", "dialog", "ui", "subtitle"])
        self.assertEqual((rows[1]["memory_lines"], rows[1]["auto_terms_added"]), (3, 0))
        # A job built for another profile (the user switched meanwhile) files its lines and terms there.
        self.app.config.llm_auto_terms = True
        self.app._on_screen_result(ScreenResult({2: "Đi thôi!"}, {}, (Term("ヴァルケン", "Valken", "place"),)), items,
                                   TranslationContext(profile="Ys X"))
        self.assertEqual([h.translation for h in self.memory.get("Ys X").recent(5)], ["Đi thôi!"])
        self.assertEqual(len(self.memory.get("Sea of Stars")), 3)
        self.assertIn("Valken", [t.target for t in self.store.get("Ys X").terms])
        self.assertNotIn("Valken", [t.target for t in self.store.get("Sea of Stars").terms])

    def test_glossary_editor_is_non_modal_and_new_profile(self):
        self.app.edit_glossary()
        editor = self.app._editor
        self.assertIsNotNone(editor)
        self.assertTrue(editor.isVisible())
        self.assertFalse(editor.isModal())
        self.app.edit_glossary()  # already open: brought to the front, not opened twice
        self.assertIs(self.app._editor, editor)
        editor.ask_before_discard = False
        editor.reject()
        pump(50)
        self.assertIsNone(self.app._editor)

        with mock.patch.object(self.app, "_ask_profile_name", return_value="Octopath"):
            self.app.new_profile()
        self.assertEqual(self.app.profile_name, "Octopath")
        self.assertIn("Octopath", self.store.names())
        self.assertTrue(self.app._editor is not None and self.app._editor.isVisible())
        self.app.shutdown()  # closes the editor without asking
        self.assertIsNone(self.app._editor)


if __name__ == "__main__":
    unittest.main()
