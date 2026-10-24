"""Tests for app.pipeline (no GPU, screen capture or network unless asked).

Run: python -m unittest tests.test_pipeline -v
Real smoke (CUDA RapidOCR on a synthetic sample + one live Google gtx request):
    $env:PIPELINE_REAL_SMOKE = "1"; python -m unittest tests.test_pipeline.RealSmokeTest -v
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import statistics
import threading
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

from app.capture.base import CaptureBackend, Frame, Region
from app.config import Config
from app.ocr.base import OcrEngine, TextBox
from app.pipeline import (
    BlockTracker,
    ChangeDetector,
    OverlayState,
    PipelineTuning,
    TextGroup,
    TranslationPipeline,
    compare,
    contrast_ratio,
    fingerprint,
    layout_blocks,
    pick_foreground,
    sample_background,
    state_to_dict,
)
from app.pipeline.colors import BLACK, WHITE, block_colors, ring_pixels
from app.pipeline.layout import role_hints
from app.pipeline.matching import ends_sentence, is_growth, same_text
from app.translate.base import TranslationError, TranslatorEngine
from app.translate.contextual import (Character, ContextualTranslator, ScreenItem, ScreenResult, Term,
                                      TranslationContext)

ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC = ROOT / "bench" / "data" / "ocr" / "synthetic"

cv2.setNumThreads(1)


class FakeClock:
    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


def tb(text: str, box: tuple[int, int, int, int], score: float | None = 1.0) -> TextBox:
    return TextBox(text, box, score)


def grp(text: str, box: tuple[int, int, int, int]) -> TextGroup:
    return TextGroup(box=box, lines=(box,), source=text, vertical=False, line_height=box[3])


def put(img: np.ndarray, text: str, org: tuple[int, int], scale: float = 1.2, color=(255, 255, 255)) -> None:
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA)


# -- fingerprint / change detection ----------------------------------------------------------
def textured(h: int, w: int, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = cv2.resize(rng.integers(40, 200, (9, 16, 3), dtype=np.uint8), (w, h), interpolation=cv2.INTER_LINEAR)
    return base


class FingerprintTest(unittest.TestCase):
    def test_grid_shape_and_small_regions(self):
        fp = fingerprint(np.zeros((1440, 2560, 3), np.uint8))
        self.assertEqual(fp.grid.shape, (16, 32))
        self.assertEqual(fp.detail.shape, (360, 640))
        self.assertEqual(fingerprint(np.zeros((10, 20, 3), np.uint8)).grid.shape, (10, 20))
        img = np.zeros((90, 320, 3), np.uint8)
        img[:, :160] = 200
        self.assertEqual(fingerprint(img).grid[0, :16].tolist(), [200.0] * 16)  # mean luminance per cell

    def test_brightness_drift_below_threshold_is_not_a_change(self):
        img = textured(720, 1280)
        put(img, "Some dialog text on screen", (90, 560))
        brighter = cv2.add(img, np.full_like(img, 12))
        diff = compare(fingerprint(img), fingerprint(brighter))
        self.assertFalse(diff.changed)
        self.assertEqual(diff.changed_cells, 0)

    def test_sensor_like_noise_is_not_a_change(self):
        img = textured(720, 1280)
        noisy = np.clip(img + np.random.default_rng(0).normal(0, 6, img.shape), 0, 255).astype(np.uint8)
        self.assertFalse(compare(fingerprint(img), fingerprint(noisy)).changed)

    def test_subtitle_line_swap_is_a_change(self):
        for h, w in ((200, 1280), (720, 1280), (1440, 2560)):  # subtitle strip, game window, full screen
            a = textured(h, w, seed=3)
            b = a.copy()
            put(a, "You kept the letter all these years?", (w // 5, h - 40), 1.4 * w / 1280)
            put(b, "I never thought we would meet again.", (w // 5, h - 40), 1.4 * w / 1280)
            diff = compare(fingerprint(a), fingerprint(b))
            self.assertTrue(diff.changed, (w, h, diff))
        # On the tall regions fewer than 5% of the cells change (band rule), and similar ink density
        # keeps the cell means within 16 levels: only the mean-absolute-difference test sees it.
        self.assertLess(diff.changed_cells, 0.05 * diff.total_cells)
        self.assertFalse(compare(fingerprint(a), fingerprint(b), detail_threshold=None).changed)

    def test_thin_two_row_change_counts(self):
        a = np.full((720, 1280, 3), 30, np.uint8)  # cells are 40 x 45 px
        b = a.copy()
        b[45:135, 400:520] = 230  # exactly 2 cell rows x 3 cell columns
        diff = compare(fingerprint(a), fingerprint(b))
        self.assertEqual(diff.changed_cells, 6)
        self.assertLess(diff.changed_cells, 0.05 * diff.total_cells)
        self.assertEqual(diff.band_cells, 6)
        self.assertTrue(diff.changed)

    def test_small_blink_is_not_a_change(self):
        a = np.full((300, 800, 3), 40, np.uint8)
        b = a.copy()
        b[200:215, 700:715] = 255  # a blinking "next" arrow
        diff = compare(fingerprint(a), fingerprint(b))
        self.assertFalse(diff.changed)
        self.assertLessEqual(diff.changed_cells, 2)  # may straddle two cells


class ChangeDetectorTest(unittest.TestCase):
    POLL = 0.15

    def setUp(self):
        self.clock = FakeClock()
        self.det = ChangeDetector(clock=self.clock)
        self.a = np.full((200, 640, 3), 20, np.uint8)
        put(self.a, "First line of text", (20, 120))
        self.b = np.full((200, 640, 3), 20, np.uint8)
        put(self.b, "Another sentence here", (20, 120))

    def poll(self, img):
        self.clock.advance(self.POLL)
        return self.det.update(img)

    def test_settle_waits_one_unchanged_poll(self):
        self.assertEqual(self.det.update(self.a), "initial")
        self.assertIsNone(self.poll(self.a))
        self.assertIsNone(self.poll(self.b))  # changed, but still moving
        self.assertTrue(self.det.dirty)
        self.assertEqual(self.poll(self.b), "settled")
        self.assertFalse(self.det.dirty)
        self.assertIsNone(self.poll(self.b))

    def test_idle_rescan_every_two_seconds(self):
        self.assertEqual(self.det.update(self.a), "initial")
        t0 = self.clock.t
        reason = None
        while reason is None:
            reason = self.poll(self.a)
        self.assertEqual(reason, "idle")
        self.assertGreaterEqual(self.clock.t - t0, 2.0)
        self.assertLess(self.clock.t - t0, 2.0 + self.POLL + 1e-6)

    def test_continuous_change_forces_ocr_after_600ms(self):
        self.det.update(self.a)
        frames = [np.full((200, 640, 3), v, np.uint8) for v in (60, 120, 180, 240)]
        first_change = None
        reasons = []
        for i in range(12):
            r = self.poll(frames[i % 4])
            if first_change is None:
                first_change = self.clock.t
            reasons.append((round(self.clock.t - first_change, 2), r))
            if r is not None:
                break
        elapsed, reason = reasons[-1]
        self.assertEqual(reason, "forced")
        self.assertGreaterEqual(elapsed, 0.6 - 1e-6)
        self.assertLess(elapsed, 0.6 + self.POLL + 1e-6)

    def test_reset_triggers_initial_pass(self):
        self.det.update(self.a)
        self.det.reset()
        self.assertEqual(self.poll(self.a), "initial")

    def test_requested_pass_fires_on_an_unchanged_screen(self):
        self.assertEqual(self.det.update(self.a), "initial")
        t0 = self.clock.t
        self.det.request_pass(t0 + 0.4)
        self.det.request_pass(t0 + 0.9)  # the earliest request wins
        reasons = [(round(self.clock.t + self.POLL - t0, 2), self.poll(self.a)) for _ in range(5)]
        self.assertEqual(reasons, [(0.15, None), (0.3, None), (0.45, "confirm"), (0.6, None), (0.75, None)])
        self.assertIsNone(self.det.requested)  # cleared by the pass
        # While the screen is changing the request waits for the settled pass, which serves it.
        self.det.request_pass(self.clock.t)
        self.assertIsNone(self.poll(self.b))
        self.assertEqual(self.poll(self.b), "settled")
        self.assertIsNone(self.det.requested)

    def test_typewriter_tail_is_below_the_change_threshold(self):
        # Why the pipeline asks for confirming passes: the last glyphs of a typed line change too
        # few cells (here 3 characters on a 1280x720 region) to count as a change.
        a = np.full((720, 1280, 3), 30, np.uint8)
        put(a, "The lighthouse keeper has not been seen since the sto", (90, 600), 1.2)
        b = a.copy()
        put(b, "The lighthouse keeper has not been seen since the storm.", (90, 600), 1.2)
        self.assertFalse(compare(fingerprint(a), fingerprint(b)).changed)


# -- layout -----------------------------------------------------------------------------------
class LayoutTest(unittest.TestCase):
    def test_paragraph_lines_merge(self):
        boxes = [
            tb("Beans and squash grow well together because they use", (118, 131, 900, 30)),
            tb("space and nutrients differently. Short transfers can make", (119, 164, 930, 29)),
            tb("a network feel smaller.", (119, 197, 300, 29)),
        ]
        groups = layout_blocks(boxes, "en")
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual(g.source, "Beans and squash grow well together because they use space and nutrients "
                                   "differently. Short transfers can make a network feel smaller.")
        self.assertEqual(len(g.lines), 3)
        self.assertEqual(g.box, (118, 131, 931, 95))
        self.assertFalse(g.vertical)
        self.assertEqual(g.line_height, 29)

    def test_paragraph_gap_splits_blocks(self):
        # Real RapidOCR boxes from bench/data/ocr/synthetic/document/ja_0.png (paragraph gap ~0.75 h).
        boxes = [
            tb("コントロールパネル", (121, 69, 306, 36)), tb("入口での待ち時間を避けるため", (118, 131, 1692, 30)),
            tb("多くの都市に市民農園が", (119, 164, 1682, 29)), tb("る。", (119, 197, 34, 29)),
            tb("ほとんどの農園は", (120, 248, 1680, 26)), tb("てれば驚くほど", (120, 279, 1679, 29)),
            tb("モリ使用量も減った。", (121, 313, 189, 26)),
        ]
        groups = layout_blocks(boxes, "ja")
        self.assertEqual([g.source for g in groups], [
            "コントロールパネル", "入口での待ち時間を避けるため多くの都市に市民農園がる。",
            "ほとんどの農園はてれば驚くほどモリ使用量も減った。",
        ])

    def test_dialog_name_hud_and_text_stay_separate(self):
        # Real RapidOCR boxes from game_dialog/ja_0.png: name 10 px above the line it labels.
        boxes = [
            tb("クエスト：鍵を探せ", (19, 14, 208, 34), 0.996), tb("08:45", (1192, 16, 69, 31), 0.971),
            tb("ミラ隊長", (89, 484, 108, 34)),
            tb("北の塔には鍵がかかっているが、鍵は井戸の近くでなくしたらしい。", (86, 528, 798, 34)),
        ]
        groups = layout_blocks(boxes, "ja")
        self.assertEqual([g.source for g in groups], [
            "クエスト：鍵を探せ", "ミラ隊長", "北の塔には鍵がかかっているが、鍵は井戸の近くでなくしたらしい。"])

    def test_side_by_side_dialog_boxes_stay_separate(self):
        boxes = [
            tb("Where are you going this late", (100, 500, 420, 34)), tb("at night, Elara?", (100, 538, 220, 34)),
            tb("I am looking for the old key", (900, 500, 400, 34)), tb("near the well.", (900, 538, 200, 34)),
        ]
        groups = layout_blocks(boxes, "en")
        self.assertEqual(sorted(g.source for g in groups), [
            "I am looking for the old key near the well.", "Where are you going this late at night, Elara?"])

    def test_ui_items_and_buttons_stay_separate(self):
        boxes = [  # real boxes from plain_ui/en_0.png
            tb("View", (23, 74, 72, 38)), tb("Cancel", (33, 120, 59, 28)), tb("Settings", (33, 165, 69, 31)),
            tb("Apply", (1000, 663, 56, 35)), tb("Cancel", (1093, 663, 61, 32)), tb("Close", (1190, 663, 56, 32)),
        ]
        groups = layout_blocks(boxes, "en")
        self.assertEqual(len(groups), 6)

    def test_same_row_fragments_join(self):
        groups = layout_blocks([tb("Press the", (100, 100, 150, 30)), tb("button", (262, 101, 90, 30))], "en")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].source, "Press the button")
        self.assertEqual(groups[0].lines, ((100, 100, 150, 30), (262, 101, 90, 30)))

    def test_vertical_japanese_columns_right_to_left(self):
        boxes = [  # real RapidOCR boxes from vertical_ja/ja_0.png (OCR order is not reading order)
            tb("信じていいの?", (734, 71, 48, 279), 0.96), tb("まだ終わってない", (790, 73, 45, 313), 0.997),
            tb("あの日の約束を", (125, 297, 45, 275), 0.999), tb("今の音は何だ？", (178, 295, 48, 276), 0.955),
            tb("まさか本当に来るとは", (431, 248, 45, 392)), tb("もう少しだけ待って", (483, 247, 48, 357)),
        ]
        groups = layout_blocks(boxes, "ja")
        self.assertEqual(len(groups), 3)
        self.assertTrue(all(g.vertical for g in groups))
        by_x = sorted(groups, key=lambda g: -g.box[0])
        self.assertEqual([g.source for g in by_x], [
            "まだ終わってない信じていいの?", "もう少しだけ待ってまさか本当に来るとは", "今の音は何だ？あの日の約束を"])
        self.assertEqual(by_x[0].lines[0], (790, 73, 45, 313))  # rightmost column first
        self.assertEqual(by_x[0].line_height, 46)

    def test_junk_is_dropped(self):
        boxes = [
            tb("", (10, 10, 50, 20)), tb("   ", (10, 40, 50, 20)), tb("▼", (600, 280, 20, 20)),
            tb("。", (300, 300, 20, 20)), tb("…", (340, 300, 20, 20)), tb("Blurry words", (10, 100, 200, 30), 0.3),
            tb("18,200", (1100, 20, 120, 30)), tb("Real text here", (10, 200, 220, 30), None),
            tb("zero", (10, 300, 0, 30)),
        ]
        groups = layout_blocks(boxes, "en")
        self.assertEqual([g.source for g in groups], ["Real text here"])

    def test_single_letter_boxes_are_dropped_before_row_joining(self):
        # Two decorative circles read as "O" on one row used to join into "O O" (2 letters) -> "ồ ồ".
        boxes = [tb("O", (683, 466, 183, 184)), tb("O", (880, 450, 190, 200)), tb("Inventory", (40, 40, 160, 34)),
                 tb("Potion", (40, 100, 110, 34)), tb("x3", (160, 101, 40, 34))]
        self.assertEqual([g.source for g in layout_blocks(boxes, "en")], ["Inventory", "Potion x3"])
        self.assertEqual([g.source for g in layout_blocks([tb("ミ", (10, 10, 30, 30))], "ja")], ["ミ"])

    def test_english_tall_boxes_are_not_vertical(self):
        groups = layout_blocks([tb("ab", (10, 10, 20, 80))], "en")
        self.assertFalse(groups[0].vertical)


class RoleHintTest(unittest.TestCase):
    SIZE = (1280, 720)

    @staticmethod
    def multi(text: str, lines: list[tuple[int, int, int, int]]) -> TextGroup:
        xs = [r[0] for r in lines]
        ys = [r[1] for r in lines]
        x1 = max(r[0] + r[2] for r in lines)
        y1 = max(r[1] + r[3] for r in lines)
        box = (min(xs), min(ys), x1 - min(xs), y1 - min(ys))
        return TextGroup(box, tuple(lines), text, False, lines[0][3])

    def test_speaker_name_above_a_dialog_box(self):
        # Geometry of tools/demo_text_window's Japanese dialog scene (OCR boxes are padded).
        groups = layout_blocks([
            tb("セラ", (96, 470, 70, 42)),
            tb("灯台の明かりが消えてから、もう三日も経った。", (96, 541, 700, 44)),
            tb("今夜こそ、あの丘の向こうを確かめに行こう。", (96, 599, 690, 44)),
        ], "ja")
        self.assertEqual([g.source for g in groups][0], "セラ")
        self.assertEqual(role_hints(groups, self.SIZE), ["name", "dialog"])

    def test_name_left_of_the_line_and_plain_dialog(self):
        groups = [grp("Mira:", (40, 300, 80, 30)), grp("Where are you going this late at night?", (140, 300, 640, 30)),
                  grp("Captain Vex", (40, 100, 160, 30))]
        self.assertEqual(role_hints(groups, self.SIZE), ["name", "dialog", "ui"])

    def test_menu_items_are_ui_not_names(self):
        items = ["冒険を続ける", "新しい旅を始める", "装備を整える", "設定", "タイトルに戻る"]
        groups = [grp(text, (116, 165 + i * 92, 40 * len(text), 44)) for i, text in enumerate(items)]
        self.assertEqual(role_hints(groups, self.SIZE), ["ui"] * 5)

    def test_bottom_centred_lines_are_subtitles(self):
        sub = self.multi("I kept the map you drew for me, even after the river swallowed the old bridge.",
                         [(388, 570, 504, 50), (243, 624, 794, 50)])
        top = self.multi("I kept the map you drew for me, even after the river swallowed the old bridge.",
                         [(388, 70, 504, 50), (243, 124, 794, 50)])
        one = grp("Do you remember the old bridge?", (440, 640, 400, 40))
        self.assertEqual(role_hints([sub, top, one], self.SIZE), ["subtitle", "dialog", "subtitle"])
        self.assertEqual(role_hints([one]), ["dialog"])  # no region size: no subtitle guess


# -- tracking ---------------------------------------------------------------------------------
LONG = "The lighthouse keeper has not been seen since the storm"
BOX = (90, 540, 840, 34)


class MatchingHelpersTest(unittest.TestCase):
    def test_same_text_rules(self):
        self.assertTrue(same_text("Settings", "Settings"))
        self.assertFalse(same_text("Settings", "Setlings"))  # short: exact only
        self.assertTrue(same_text(LONG, LONG.replace("not", "n0t")))  # 1/55 edits
        self.assertFalse(same_text(LONG, "The lighthouse keeper was found after the storm"))

    def test_growth_and_sentence_end(self):
        self.assertTrue(is_growth("北の塔には", "北の塔には鍵が"))
        self.assertTrue(is_growth("The lighthouse k", "The lighthouse keeper"))  # half-drawn last glyph
        self.assertFalse(is_growth("北の塔には鍵が", "北の塔には"))
        self.assertFalse(is_growth("Hello", "Goodbye there"))
        for s in ("Done.", "本当？", "「行こう」", "Wait...", 'He said "go!"', "そうか…"):
            self.assertTrue(ends_sentence(s), s)
        for s in ("北の塔には", "and then", "Stage 3-2"):
            self.assertFalse(ends_sentence(s), s)


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.tr = BlockTracker("en")

    def show(self, groups, now: float, tr: BlockTracker | None = None):
        """Two OCR passes reading the same groups (the second one confirms new text)."""
        tr = self.tr if tr is None else tr  # (an empty tracker is falsy)
        res = tr.update(groups, now)
        tr.update(groups, now + 0.15)
        return res

    def translate_all(self, now: float, prefix: str = "VI:") -> list[tuple[int, str]]:
        items = self.tr.take_due(now)
        for bid, src in items:
            self.assertTrue(self.tr.apply_translation(bid, src, prefix + src))
        return items

    def test_new_text_waits_for_a_confirming_pass(self):
        res = self.tr.update([grp(LONG, BOX), grp("Done.", (20, 20, 90, 30))], 0.0)
        self.assertEqual(len(res.added), 2)
        # A sentence end is final; other text may be the first glyphs of a typewriter line.
        self.assertEqual([src for _, src in self.tr.take_due(0.0)], ["Done."])
        self.assertAlmostEqual(self.tr.next_check(), 0.001)  # any later pass confirms it
        self.tr.update([grp(LONG, BOX), grp("Done.", (20, 20, 90, 30))], 0.15)
        self.assertIsNone(self.tr.next_check())
        self.assertEqual([src for _, src in self.tr.take_due(0.15)], [LONG])
        # Without any confirming pass it still goes out after typewriter_max_hold_s.
        tr = BlockTracker("en")
        tr.update([grp(LONG, BOX)], 0.0)
        self.assertEqual(tr.take_due(1.9), [])
        self.assertEqual(len(tr.take_due(2.0)), 1)

    def test_ocr_jitter_keeps_id_and_translation(self):
        res = self.show([grp(LONG, BOX), grp("Settings", (20, 20, 90, 30))], 0.0)
        self.assertEqual(len(res.added), 2)
        self.assertEqual(len(self.translate_all(0.15)), 2)
        before = {b.id: b for b in self.tr.blocks()}
        jittered = [grp(LONG.replace("not", "n0t"), (91, 538, 838, 35)), grp("Settings", (21, 19, 90, 31))]
        res = self.tr.update(jittered, 1.0)
        self.assertEqual(res.added, [])
        self.assertEqual(sorted(res.kept), sorted(before))
        self.assertEqual(len(res.alt), 1)  # a different reading, not confirmed yet
        self.assertEqual(self.tr.take_due(1.0), [])
        after = {b.id: b for b in self.tr.blocks()}
        self.assertEqual(after, before)  # nothing visible changed (small jitter ignored)
        self.assertEqual(after[min(after)].translation, "VI:" + LONG)
        # The next pass reads the original again: it was jitter, nothing is re-translated.
        res = self.tr.update([grp(LONG, BOX), grp("Settings", (20, 20, 90, 30))], 1.15)
        self.assertEqual((res.changed, res.alt), ([], []))
        self.assertEqual(self.tr.take_due(1.2), [])
        self.assertEqual({b.id: b for b in self.tr.blocks()}, before)

    def test_small_real_changes_are_retranslated(self):
        cases = [
            ("en", "You received 120 gold coins.", "You received 125 gold coins."),
            ("en", "You can open the door now", "You can't open the door now"),
            ("en", "Defeat 3 more wolves near the mill", "Defeat 2 more wolves near the mill"),
            ("ja", "残り時間は10分です。急いで北の塔へ向かってください。", "残り時間は15分です。急いで北の塔へ向かってください。"),
            ("ja", "扉を開けることができる。急いでください。", "扉を開けることができない。急いでください。"),
        ]
        for lang, old, new in cases:
            with self.subTest(new=new):
                tr = BlockTracker(lang)
                self.show([grp(old, BOX)], 0.0, tr)
                [(bid, src)] = tr.take_due(0.15)
                tr.apply_translation(bid, src, "TR(old)")
                res = tr.update([grp(new, BOX)], 1.0)
                self.assertEqual((res.kept, res.alt), ([bid], [bid]))
                self.assertEqual(tr.blocks()[0].translation, "TR(old)")  # one reading is not enough
                res = tr.update([grp(new, BOX)], 1.15)
                self.assertEqual(res.changed, [bid])  # read twice: same block, new text
                b = tr.blocks()[0]
                self.assertEqual((b.id, b.source, b.translation, b.stale_translation), (bid, new, None, "TR(old)"))
                self.assertEqual(tr.take_due(1.15), [(bid, new)])

    def test_large_move_updates_geometry_but_keeps_translation(self):
        self.show([grp(LONG, BOX)], 0.0)
        self.translate_all(0.15)
        self.tr.update([grp(LONG, (100, 548, 840, 34))], 1.0)
        b = self.tr.blocks()[0]
        self.assertEqual(b.box, (100, 548, 840, 34))
        self.assertEqual(b.translation, "VI:" + LONG)

    def test_real_change_gets_new_block_and_translation(self):
        self.show([grp(LONG, BOX)], 0.0)
        self.translate_all(0.15)
        old_id = self.tr.blocks()[0].id
        new_text = "Rest here for the night before you cross the bridge"
        res = self.tr.update([grp(new_text, (90, 541, 800, 34))], 2.0)
        self.assertEqual(res.removed, [old_id])  # replaced at once, not after 2 misses
        blocks = self.tr.blocks()
        self.assertEqual(len(blocks), 1)
        self.assertNotEqual(blocks[0].id, old_id)
        self.assertIsNone(blocks[0].translation)
        self.assertIsNone(blocks[0].stale_translation)
        self.assertEqual(self.tr.take_due(2.0), [])
        self.tr.update([grp(new_text, (90, 541, 800, 34))], 2.15)
        self.assertEqual(self.tr.take_due(2.15), [(blocks[0].id, new_text)])

    def test_typewriter_growth_waits_for_a_confirming_pass_then_translates_once(self):
        tr = BlockTracker("ja")
        tr.update([grp("北の", (86, 528, 70, 34))], 0.0)
        self.assertEqual(tr.take_due(0.0), [])  # first glyphs of a typewriter line: not sent
        bid = tr.blocks()[0].id
        res = tr.update([grp("北の塔には鍵がかか", (86, 528, 290, 34))], 0.15)
        self.assertEqual(res.grown, [bid])
        self.assertAlmostEqual(tr.next_check(), 0.75)  # typewriter_wait_s after the last growth
        requests = []
        t = 0.15
        while t < 1.9:  # polls without an OCR pass: nothing is sent, however long
            requests += tr.take_due(t)
            t += 0.15
        self.assertEqual(requests, [])
        tr.update([grp("北の塔には鍵がかかっ", (86, 528, 310, 34))], 1.9)  # the confirming pass: still growing
        self.assertEqual(tr.take_due(1.9), [])
        tr.update([grp("北の塔には鍵がかかっ", (86, 528, 310, 34))], 2.2)  # too early to confirm
        self.assertEqual(tr.take_due(2.2), [])
        tr.update([grp("北の塔には鍵がかかっ", (86, 528, 310, 34))], 2.5)
        self.assertIsNone(tr.next_check())
        [(bid2, src)] = tr.take_due(2.5)
        self.assertEqual((bid2, src), (bid, "北の塔には鍵がかかっ"))
        tr.apply_translation(bid, src, "Tháp phía bắc bị khóa")
        # A little more text: the old translation stays as a dimmed preview.
        tr.update([grp("北の塔には鍵がかかってい", (86, 528, 330, 34))], 3.0)
        self.assertEqual(tr.blocks()[0].stale_translation, "Tháp phía bắc bị khóa")
        # Much more text: the preview would be misleading; the original stays readable instead.
        tr.update([grp("北の塔には鍵がかかっているが、鍵は井戸の", (86, 528, 600, 34))], 3.3)
        self.assertIsNone(tr.blocks()[0].stale_translation)
        # Growth that completes a sentence is translated immediately.
        full = "北の塔には鍵がかかっているが、鍵は井戸の近くでなくしたらしい。"
        tr.update([grp(full, (86, 528, 798, 34))], 3.6)
        self.assertEqual(tr.take_due(3.6), [(bid, full)])

    def test_removed_after_two_missed_passes(self):
        name, line = grp("Merchant Vex", (84, 491, 150, 33)), grp(LONG, BOX)
        self.tr.update([name, line], 0.0)
        name_id = next(b.id for b in self.tr.blocks() if b.source == "Merchant Vex")
        res = self.tr.update([line], 1.0)
        self.assertEqual(res.missed, [name_id])
        self.assertIn(name_id, [b.id for b in self.tr.blocks()])
        res = self.tr.update([name, line], 2.0)  # back: same id, counter reset
        self.assertIn(name_id, res.kept)
        self.tr.update([line], 3.0)
        res = self.tr.update([line], 4.0)
        self.assertEqual(res.removed, [name_id])
        self.assertEqual([b.source for b in self.tr.blocks()], [LONG])

    def test_stale_results_are_dropped(self):
        tr = BlockTracker("ja")
        self.show([grp("北の塔には", (86, 528, 160, 34))], 0.0, tr)
        [(bid, src)] = tr.take_due(0.15)
        tr.update([grp("北の塔には鍵", (86, 528, 200, 34))], 0.2)  # grew a little while in flight
        self.assertFalse(tr.apply_translation(bid, src, "Tháp phía bắc"))
        b = tr.blocks()[0]
        self.assertIsNone(b.translation)
        self.assertEqual(b.stale_translation, "Tháp phía bắc")  # kept only as a dimmed preview
        # A block that was replaced or removed ignores its late result.
        self.show([grp(LONG, BOX)], 0.0)
        [(old_id, old_src)] = self.tr.take_due(0.15)
        self.tr.update([grp("Completely different words now", BOX)], 0.5)
        self.assertFalse(self.tr.apply_translation(old_id, old_src, "late"))
        self.assertTrue(all(b.translation is None for b in self.tr.blocks()))

    def test_error_backoff(self):
        self.show([grp(LONG, BOX)], 10.0)
        [(bid, src)] = self.tr.take_due(10.15)
        self.assertTrue(self.tr.apply_error(bid, src, "Lỗi dịch: timeout", 10.0))
        self.assertEqual(self.tr.blocks()[0].error, "Lỗi dịch: timeout")
        self.assertEqual(self.tr.take_due(14.9), [])
        self.assertEqual(self.tr.take_due(15.0), [(bid, src)])
        self.tr.apply_error(bid, src, "again", 15.0)
        self.assertEqual(self.tr.take_due(24.9), [])  # second failure: 10 s
        self.assertEqual(len(self.tr.take_due(25.0)), 1)
        self.assertTrue(self.tr.apply_translation(bid, src, "ok"))
        self.assertIsNone(self.tr.blocks()[0].error)

    def test_backoff_survives_block_recreation(self):
        tr = BlockTracker("en")
        self.show([grp("Mira", (20, 20, 80, 30))], 0.0, tr)
        [(bid, src)] = tr.take_due(0.15)
        tr.apply_error(bid, src, "Lỗi dịch: HTTP 429", 0.2)
        self.show([grp("Mlra", (20, 20, 80, 30))], 0.3, tr)  # short-label jitter: a new block...
        self.show([grp("Mira", (20, 20, 80, 30))], 0.6, tr)  # ...and the original reading again
        self.assertEqual(tr.take_due(1.0), [])
        self.assertEqual(tr.blocks()[0].error, "Lỗi dịch: HTTP 429")  # still shown as failed
        tr.reset()  # a pause / resume
        self.show([grp("Mira", (20, 20, 80, 30))], 2.0, tr)
        self.assertEqual(tr.take_due(5.1), [])
        self.assertEqual([src for _, src in tr.take_due(5.2)], ["Mira"])  # 5 s after the failure

    def test_inflight_not_resent_release_and_pending_count(self):
        self.show([grp(LONG, BOX)], 0.0)
        [(bid, src)] = self.tr.take_due(0.15)
        self.assertEqual(self.tr.pending_count(), 1)
        self.assertEqual(self.tr.take_due(1.0), [])
        self.tr.release(bid, src)  # the request could not be sent: due again
        self.assertEqual(self.tr.pending_count(), 0)
        self.assertEqual(self.tr.take_due(1.0), [(bid, src)])

    def test_regrouping_of_the_same_lines_keeps_the_tracked_blocks(self):
        # Real boxes from a run where a static 2-line dialog flipped between one block and two.
        l1, l2 = "Where are you going this late", "at night, captain Elara?"
        split = [TextGroup((95, 543, 589, 39), ((95, 543, 589, 39),), l1, False, 39),
                 TextGroup((99, 602, 547, 38), ((99, 602, 547, 38),), l2, False, 38)]
        merged = TextGroup((95, 543, 589, 100), ((95, 543, 589, 39), (98, 600, 549, 41)), f"{l1} {l2}", False, 40)
        self.show(split, 0.0)
        self.translate_all(0.15)
        before = self.tr.blocks()
        res = self.tr.update([merged], 1.0)
        self.assertEqual((res.added, res.removed, res.grown, res.regrouped), ([], [], [], 1))
        self.assertEqual([(b.id, b.source, b.translation) for b in self.tr.blocks()],
                         [(b.id, b.source, b.translation) for b in before])
        # ...and the other way round: a merged block stays merged when a pass splits it.
        tr = BlockTracker("en")
        self.show([merged], 0.0, tr)
        [(bid, src)] = tr.take_due(0.15)
        tr.apply_translation(bid, src, "VI")
        res = tr.update(split, 1.0)
        self.assertEqual((res.added, res.removed, res.kept), ([], [], [bid]))
        self.assertEqual([(b.id, b.translation) for b in tr.blocks()], [(bid, "VI")])
        # A regrouping that also changes the text is a real change.
        changed = TextGroup(merged.box, merged.lines, f"{l1} at dawn, captain Mira?", False, 40)
        res = self.tr.update([changed], 2.0)
        self.assertEqual(res.regrouped, 0)
        self.assertTrue(res.grown or res.added)

    def test_translation_is_nfc(self):
        self.show([grp(LONG, BOX)], 0.0)
        [(bid, src)] = self.tr.take_due(0.15)
        self.tr.apply_translation(bid, src, "Người")  # fully decomposed
        self.assertEqual(self.tr.blocks()[0].translation, "Người")  # "Người", precomposed


class LlmTierTrackerTest(unittest.TestCase):
    def setUp(self):
        self.tr = BlockTracker("en")

    def show(self, groups, now: float):
        self.tr.update(groups, now)
        self.tr.update(groups, now + 0.15)

    def test_llm_tier_replaces_mt_and_mt_never_replaces_llm(self):
        self.show([grp(LONG, BOX)], 0.0)
        [(bid, src)] = self.tr.take_due(0.15)
        self.assertEqual([t.id for t in self.tr.llm_due(0.15, 0)], [bid])  # confirmed: the LLM may take it too
        self.tr.mark_llm(bid, src)
        self.assertEqual(self.tr.llm_due(0.15, 0), [])  # in a job already
        self.assertTrue(self.tr.apply_llm(bid, src, "LLM", "Mira", 0))
        self.assertFalse(self.tr.apply_translation(bid, src, "MT"))  # Google's late answer loses
        b = self.tr.blocks()[0]
        self.assertEqual((b.translation, b.quality, b.speaker, b.stale_translation), ("LLM", 2, "Mira", None))
        self.assertEqual(self.tr.pending_count(), 0)
        self.tr.finish_llm(bid, src)
        self.assertEqual(self.tr.llm_due(0.2, 0), [])  # done in this epoch
        self.assertFalse(self.tr.apply_llm(bid, src, "LLM again", None, 0))
        self.assertEqual([t.id for t in self.tr.llm_due(0.2, 1)], [bid])  # a new epoch redoes it...
        self.assertEqual(self.tr.blocks()[0].translation, "LLM")  # ...while the old one stays shown
        self.assertTrue(self.tr.apply_llm(bid, src, "LLM v2", None, 1))
        self.tr.skip_llm(bid, src, 2)
        self.assertEqual(self.tr.llm_due(0.2, 2), [])  # declined in epoch 2: not asked again
        # Grown text drops tier and speaker; the LLM translation becomes the dimmed preview.
        self.tr.update([grp(LONG + " again", BOX)], 1.0)
        b = self.tr.blocks()[0]
        self.assertEqual((b.translation, b.quality, b.speaker, b.stale_translation), (None, 0, None, "LLM v2"))

    def test_llm_waits_for_confirmed_text_and_better_tiers_win(self):
        self.tr.update([grp(LONG, BOX)], 0.0)
        self.assertEqual(self.tr.llm_due(0.0, 0), [])  # may be the first glyphs of a typewriter line
        self.tr.update([grp(LONG, BOX)], 0.15)
        [t] = self.tr.llm_due(0.15, 0)
        self.assertTrue(self.tr.apply_llm(t.id, t.source, "LLM", None, 0))
        self.assertFalse(self.tr.apply_translation(t.id, t.source, "MT", quality=1))
        self.assertTrue(self.tr.apply_translation(t.id, t.source, "Human", quality=3))
        self.assertEqual(self.tr.blocks()[0].quality, 3)
        self.assertFalse(self.tr.apply_llm(t.id, "other text", "LLM", None, 5))  # stale source: dropped


# -- colours ----------------------------------------------------------------------------------
class ColorTest(unittest.TestCase):
    def test_contrast_and_foreground(self):
        self.assertAlmostEqual(contrast_ratio(WHITE, BLACK), 21.0, places=6)
        self.assertAlmostEqual(contrast_ratio((128, 128, 128), (128, 128, 128)), 1.0)
        self.assertEqual(pick_foreground((40, 30, 25)), WHITE)
        self.assertEqual(pick_foreground((235, 235, 235)), BLACK)
        self.assertEqual(pick_foreground((0, 220, 255)), BLACK)  # yellow (BGR)
        self.assertEqual(pick_foreground((200, 60, 30)), WHITE)  # dark blue (BGR)

    def test_background_sampled_around_text(self):
        img = np.zeros((200, 600, 3), np.uint8)
        img[:] = (180, 40, 30)  # BGR navy-ish dialog box
        img[:, 500:] = (255, 255, 255)  # unrelated area far from the text
        put(img, "Hello there", (30, 110), 1.5)
        rect = (24, 70, 300, 52)
        bg, fg = block_colors(img, [rect])
        self.assertEqual(bg, (180, 40, 30))
        self.assertEqual(fg, WHITE)

    def test_ring_is_clipped_at_image_edges(self):
        img = np.full((50, 80, 3), 200, np.uint8)
        self.assertGreater(len(ring_pixels(img, (0, 0, 30, 20))), 0)
        self.assertEqual(sample_background(img, [(-5, -5, 100, 100)]), (200, 200, 200))  # covers everything


# -- threaded end-to-end with fakes -----------------------------------------------------------
def _key(img: np.ndarray) -> bytes:
    return hashlib.blake2b(np.ascontiguousarray(img).tobytes(), digest_size=16).digest()


class Scene:
    """Renders frames that change over time and remembers the true text boxes of each frame.

    `bar_speed` (px/s) sweeps a bright vertical bar across the background, under the text: every
    frame differs, like video or an animated game background.
    """

    def __init__(self, script, size=(200, 640), bar_speed: float = 0.0) -> None:
        self.script = script  # elapsed seconds -> list of (text, (x, baseline_y))
        self.size = size
        self.bar_speed = bar_speed
        self.t0 = time.monotonic()
        self.truth: dict[bytes, list[TextBox]] = {}
        self._cache: dict[tuple, np.ndarray] = {}
        self.lock = threading.Lock()

    def render(self) -> np.ndarray:
        elapsed = time.monotonic() - self.t0
        lines = tuple(self.script(elapsed))
        bar = int(elapsed * self.bar_speed) % self.size[1] if self.bar_speed else -1
        with self.lock:
            img = self._cache.get((lines, bar))
            if img is None:
                h, w = self.size
                img = np.full((h, w, 3), (40, 25, 20), np.uint8)
                if bar >= 0:
                    img[:, bar:bar + 120] = (200, 180, 160)
                    self._cache.clear()  # every frame differs: do not keep them all
                boxes = []
                for text, (x, y) in lines:
                    put(img, text, (x, y), 0.8)
                    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
                    boxes.append(TextBox(text, (x - 4, y - th - 5, tw + 8, th + base + 10), 0.99))
                img.setflags(write=False)  # like dxcam's cached frames
                self._cache[(lines, bar)] = img
                self.truth[_key(img)] = boxes
        return img


class FakeCapture(CaptureBackend):
    name = "fake"

    def __init__(self, scene: Scene) -> None:
        self.scene = scene
        self.grabs = 0
        self.closed = False
        self.thread = threading.get_ident()

    def grab(self, region: Region) -> Frame | None:
        if self.closed:
            raise RuntimeError("closed")
        self.grabs += 1
        return Frame(self.scene.render(), time.perf_counter(), region)

    def close(self) -> None:
        self.closed = True


class FakeOcr(OcrEngine):
    """Returns the scene's true boxes. `breaks_after`: recognize() fails for good after that many
    calls (a GPU reset); `miss_every`: every n-th call misses all text (OCR flicker)."""

    def __init__(self, scene: Scene, lang: str, breaks_after: int | None = None, miss_every: int | None = None) -> None:
        self.scene, self.lang = scene, lang
        self.name = f"fake-{lang}"
        self.thread = threading.get_ident()
        self.calls = 0
        self.closed = False
        self.breaks_after, self.miss_every = breaks_after, miss_every

    def recognize(self, image_bgr: np.ndarray) -> list[TextBox]:
        self.calls += 1
        time.sleep(0.01)
        if self.breaks_after is not None and self.calls > self.breaks_after:
            raise RuntimeError("CUDNN_STATUS_EXECUTION_FAILED")
        if self.miss_every and self.calls % self.miss_every == 0:
            return []
        return list(self.scene.truth.get(_key(image_bgr), []))

    def close(self) -> None:
        self.closed = True


class FakeTranslator(TranslatorEngine):
    name = "fake"

    def __init__(self, delay: float = 0.05, fail: bool = False) -> None:
        self.delay, self.fail = delay, fail
        self.calls: list[list[str]] = []
        self.thread = threading.get_ident()
        self.call_threads: set[int] = set()
        self.closed_on: int | None = None
        self.active = self.max_active = self.cancelled = 0

    async def translate_batch(self, texts, src, tgt, *, context=None, glossary=None):
        self.calls.append(list(texts))
        self.call_threads.add(threading.get_ident())
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1
        if self.fail:
            raise TranslationError("HTTP 503", retryable=True)
        return [f"VI:{t}" for t in texts]

    async def aclose(self) -> None:
        self.closed_on = threading.get_ident()


class FakeContextual(ContextualTranslator):
    """Translates every item as `prefix + text` (or via `respond`) after `delay` seconds."""

    def __init__(self, delay: float = 0.1, fail: bool = False, prefix: str = "LLM:", model: str = "fake-llm",
                 speakers: dict[str, str] | None = None, skip: tuple[str, ...] = (), new_terms: tuple = (),
                 respond=None) -> None:
        self.delay, self.fail, self.prefix, self.model = delay, fail, prefix, model
        self.speakers, self.skip, self.new_terms, self.respond = speakers or {}, skip, new_terms, respond
        self.calls: list[list[ScreenItem]] = []
        self.call_times: list[float] = []
        self.contexts: list[TranslationContext] = []
        self.call_threads: set[int] = set()
        self.active = self.max_active = self.cancelled = self.closed = 0

    def texts(self, call: int) -> list[str]:
        return [it.text for it in self.calls[call]]

    async def translate_screen(self, items, src, tgt, ctx):
        self.calls.append(list(items))
        self.call_times.append(time.monotonic())
        self.contexts.append(ctx)
        self.call_threads.add(threading.get_ident())
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1
        if self.fail:
            raise TranslationError("HTTP 500")
        if self.respond is not None:
            return self.respond(items, ctx)
        return ScreenResult({it.id: f"{self.prefix}{it.text}" for it in items if it.text not in self.skip},
                            {it.id: self.speakers.get(it.text) for it in items}, self.new_terms, self.model, self.delay)

    async def aclose(self) -> None:
        self.closed += 1


FIRST = "Hello world, this is the first line"
SECOND = "A completely different second line"


class Harness:
    def __init__(self, script, *, translator_kwargs: dict | None = None, poll_ms: int = 30, region=True,
                 ocr_failures: int = 0, tuning: PipelineTuning | None = None, config_kwargs: dict | None = None,
                 ocr_kwargs: dict | None = None, ocr_create_delay: float = 0.0, translator_create_delay: float = 0.0,
                 capture_cls: type[FakeCapture] = FakeCapture, bar_speed: float = 0.0,
                 llm: FakeContextual | None = None, llm_ready=None, context_provider=None):
        self.llm = llm
        self.llm_ready = llm_ready or (lambda: True)
        self.factory_calls = 0
        self.factory_threads: set[int] = set()
        self.screen_results: list[tuple[ScreenResult, tuple[ScreenItem, ...]]] = []
        self.screen_contexts: list[TranslationContext] = []
        self.screen_result_threads: set[int] = set()
        self.scene = Scene(script, bar_speed=bar_speed)
        self.captures: list[FakeCapture] = []
        self.ocrs: list[FakeOcr] = []
        self.ocr_failures = ocr_failures
        self.ocr_kwargs = ocr_kwargs or {}  # applied to the first engine only
        self.ocr_create_delay = ocr_create_delay
        self.capture_cls = capture_cls
        self.translator_kwargs = translator_kwargs or {}
        self.translator_create_delay = translator_create_delay
        self.translator: FakeTranslator | None = None
        self.states: list[OverlayState] = []
        self.state_threads: set[int] = set()
        self.cond = threading.Condition()
        cfg = Config(**{"src_lang": "en", "poll_ms": poll_ms, "region": [0, 0, 640, 200] if region else None,
                        **(config_kwargs or {})})
        self.pipeline = TranslationPipeline(
            cfg, self.on_state,
            capture_factory=self.make_capture, ocr_factory=self.make_ocr,
            translator_factory=self.make_translator, tuning=tuning or PipelineTuning(paused_wait_s=0.1),
            contextual_factory=self.make_contextual if llm is not None else None,
            context_provider=context_provider,
            on_screen_result=self.on_screen_result if llm is not None else None,
        )

    def make_contextual(self) -> FakeContextual | None:
        self.factory_calls += 1
        self.factory_threads.add(threading.get_ident())
        return self.llm if self.llm_ready() else None

    def on_screen_result(self, result: ScreenResult, items: tuple[ScreenItem, ...], ctx: TranslationContext) -> None:
        self.screen_results.append((result, items))
        self.screen_contexts.append(ctx)
        self.screen_result_threads.add(threading.get_ident())

    def make_translator(self) -> FakeTranslator:
        time.sleep(self.translator_create_delay)
        self.translator = FakeTranslator(**self.translator_kwargs)
        return self.translator

    def make_capture(self) -> FakeCapture:
        cap = self.capture_cls(self.scene)
        self.captures.append(cap)
        return cap

    def make_ocr(self, lang: str) -> FakeOcr:
        time.sleep(self.ocr_create_delay)
        if self.ocr_failures > 0:
            self.ocr_failures -= 1
            raise RuntimeError("CUDA init failed")
        ocr = FakeOcr(self.scene, lang, **(self.ocr_kwargs if not self.ocrs else {}))
        self.ocrs.append(ocr)
        return ocr

    def sent(self) -> list[str]:
        return [t for call in (self.translator.calls if self.translator else []) for t in call]

    def on_state(self, state: OverlayState) -> None:
        with self.cond:
            self.states.append(state)
            self.state_threads.add(threading.get_ident())
            self.cond.notify_all()

    def mark(self) -> int:
        with self.cond:
            return self.states[-1].seq if self.states else 0

    def wait_for(self, pred, timeout: float = 5.0, after: int = 0) -> OverlayState:
        """First state with seq > `after` that satisfies `pred`."""
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                for s in self.states:
                    if s.seq > after and pred(s):
                        return s
                left = deadline - time.monotonic()
                if left <= 0:
                    last = self.states[-1] if self.states else None
                    raise AssertionError(f"condition not met within {timeout}s; last state: {last}")
                self.cond.wait(left)


def translated(state: OverlayState, text: str) -> bool:
    return any(b.source == text and b.translation == f"VI:{text}" for b in state.blocks)


class PipelineThreadedTest(unittest.TestCase):
    def test_end_to_end_states_translations_pause_and_stop(self):
        h = Harness(lambda t: [(FIRST, (20, 80))] if t < 0.8 else [(SECOND, (20, 80))])
        p = h.pipeline
        main = threading.get_ident()
        p.start()
        try:
            s1 = h.wait_for(lambda s: translated(s, FIRST), 3.0)
            s2 = h.wait_for(lambda s: translated(s, SECOND), 4.0)
            self.assertEqual([b.source for b in s2.blocks], [SECOND])
            first_id = next(b.id for b in s1.blocks if b.source == FIRST)
            self.assertNotEqual(s2.blocks[0].id, first_id)
            self.assertEqual(s2.status, "running")
            self.assertEqual(s2.message, "EN→VI")
            self.assertEqual(s2.region, Region(0, 0, 640, 200))
            b = s2.blocks[0]
            self.assertEqual(b.bg, (40, 25, 20))
            self.assertEqual(b.fg, (255, 255, 255))
            # A "translating" state was published in between, with pending > 0.
            self.assertTrue(any(s.pending > 0 and s.message.startswith("Đang dịch") for s in h.states))
            # Each text went to the translator once, in one batch per pass.
            sent = [t for call in h.translator.calls for t in call]
            self.assertEqual(sorted(sent), sorted([FIRST, SECOND]))

            # Threads: factories ran on the worker / loop threads, never on the caller's thread.
            self.assertEqual(len(h.ocrs), 1)
            self.assertNotEqual(h.ocrs[0].thread, main)
            self.assertEqual(h.captures[0].thread, h.ocrs[0].thread)
            self.assertNotEqual(h.translator.thread, h.ocrs[0].thread)
            self.assertEqual(h.translator.call_threads, {h.translator.thread})
            self.assertNotIn(main, h.state_threads)

            # Pause: no grab at all after set_paused returns; overlay state cleared.
            mark = h.mark()
            p.set_paused(True)
            self.assertTrue(p.is_paused())
            self.assertTrue(p.is_paused("user"))
            self.assertFalse(p.is_paused("guard"))
            grabs = h.captures[0].grabs
            paused = h.wait_for(lambda s: s.status == "paused", 2.0, after=mark)
            self.assertEqual(paused.blocks, ())
            self.assertEqual(paused.message, "Tạm dừng")
            time.sleep(0.4)
            self.assertEqual(h.captures[0].grabs, grabs)
            self.assertFalse(h.captures[0].closed)  # a user pause keeps the backend

            # Guard pause on top: capture released; guard status wins.
            mark = h.mark()
            p.set_paused(True, "guard")
            h.wait_for(lambda s: s.status == "guarded" and s.message == "Đã tạm dừng: game có anti-cheat", 2.0,
                       after=mark)
            time.sleep(0.2)
            self.assertTrue(h.captures[0].closed)
            mark = h.mark()
            p.set_paused(False, "guard")
            h.wait_for(lambda s: s.status == "paused", 2.0, after=mark)  # still paused by the user
            time.sleep(0.2)
            self.assertEqual(len(h.captures), 1)
            mark = h.mark()
            p.set_paused(False)
            resumed = h.wait_for(lambda s: s.status == "running" and translated(s, SECOND), 3.0, after=mark)
            self.assertEqual(len(h.captures), 2)  # recreated on the worker thread after the guard
            self.assertEqual(h.captures[1].thread, h.ocrs[0].thread)
            self.assertGreater(h.captures[1].grabs, 0)
            self.assertGreater(resumed.seq, s2.seq)

            # Sequence numbers strictly increase; consecutive states always differ visibly.
            seqs = [s.seq for s in h.states]
            self.assertEqual(seqs, sorted(set(seqs)))
            for a, b2 in zip(h.states, h.states[1:]):
                self.assertNotEqual((a.blocks, a.status, a.message, a.pending, a.region),
                                    (b2.blocks, b2.status, b2.message, b2.pending, b2.region))
            self.assertIsInstance(state_to_dict(p.snapshot())["blocks"], list)
        finally:
            t0 = time.monotonic()
            p.stop(timeout=5.0)
            stop_s = time.monotonic() - t0
        self.assertLess(stop_s, 2.0)
        self.assertFalse(p.running)
        self.assertFalse(p._worker.is_alive())
        self.assertFalse(p._tthread.is_alive())
        self.assertEqual(h.translator.closed_on, h.translator.thread)  # aclose ran on its own loop
        self.assertTrue(all(c.closed for c in h.captures))
        self.assertTrue(h.ocrs[0].closed)
        grabs = h.captures[-1].grabs
        time.sleep(0.2)
        self.assertEqual(h.captures[-1].grabs, grabs)
        p.stop()  # idempotent

    def test_region_and_language_changes_reset_and_recreate_ocr_on_worker(self):
        h = Harness(lambda t: [(FIRST, (20, 80))], region=False)
        p = h.pipeline
        p.start()
        try:
            idle = h.wait_for(lambda s: s.status == "idle", 3.0)
            self.assertIn("Ctrl+Alt+R", idle.message)
            self.assertEqual(h.captures, [])  # nothing captured without a region
            p.set_region(Region(0, 0, 640, 200))
            en = h.wait_for(lambda s: translated(s, FIRST), 3.0)
            mark = h.mark()
            p.set_src_lang("ja")
            s = h.wait_for(lambda s: s.src_lang == "ja" and s.status == "running" and s.blocks, 3.0, after=mark)
            self.assertEqual(s.message, "Đang dịch 1…" if s.pending else "JA→VI")
            self.assertGreater(s.blocks[0].id, en.blocks[0].id)  # blocks were reset
            self.assertEqual([o.lang for o in h.ocrs], ["en", "ja"])
            self.assertTrue(h.ocrs[0].closed)
            self.assertEqual(h.ocrs[1].thread, h.ocrs[0].thread)
            h.wait_for(lambda s: s.src_lang == "ja" and translated(s, FIRST), 3.0, after=mark)
            mark = h.mark()
            p.set_region(None)
            h.wait_for(lambda s: s.status == "idle" and s.blocks == () and s.region is None, 2.0, after=mark)
        finally:
            p.stop()

    def test_translation_error_is_surfaced_and_backed_off(self):
        h = Harness(lambda t: [(FIRST, (20, 80))], translator_kwargs={"delay": 0.01, "fail": True})
        p = h.pipeline
        with self.assertLogs("app.pipeline.pipeline", "WARNING") as logs:
            p.start()
            try:
                s = h.wait_for(lambda s: any(b.error for b in s.blocks), 3.0)
                self.assertTrue(s.blocks[0].error.startswith("Lỗi dịch"))
                self.assertIsNone(s.blocks[0].translation)
                self.assertTrue(s.message.startswith("Lỗi dịch"))
                time.sleep(1.5)
                self.assertEqual(len(h.translator.calls), 1)  # no retry within 5 s
            finally:
                p.stop()
        self.assertTrue(any("HTTP 503" in line for line in logs.output))

    def test_failed_request_holds_new_requests_too(self):
        # After a rate limit / network failure, other texts must not go out at once either.
        h = Harness(lambda t: [(FIRST, (20, 80))] if t < 0.4 else [(SECOND, (20, 80))],
                    translator_kwargs={"delay": 0.01, "fail": True})
        p = h.pipeline
        with self.assertLogs("app.pipeline.pipeline", "WARNING") as logs:
            p.start()
            try:
                h.wait_for(lambda s: any(b.source == SECOND for b in s.blocks), 3.0)
                time.sleep(1.0)
                self.assertEqual(h.sent(), [FIRST])
            finally:
                p.stop()
        self.assertTrue(any("no new translation requests for 5 s" in line for line in logs.output))

    def test_error_pause_and_bad_reason(self):
        h = Harness(lambda t: [(FIRST, (20, 80))])
        p = h.pipeline
        with self.assertRaises(ValueError):
            p.set_paused(True, "nap")
        p.set_error("Không ẩn được overlay khỏi capture")
        p.start()
        try:
            s = h.wait_for(lambda s: s.status == "error", 3.0)
            self.assertEqual(s.message, "Không ẩn được overlay khỏi capture")
            time.sleep(0.3)
            self.assertEqual(h.captures, [])
            mark = h.mark()
            p.set_error(None)
            h.wait_for(lambda s: translated(s, FIRST), 3.0, after=mark)
        finally:
            p.stop()

    def test_ocr_engine_failure_is_shown_and_retried(self):
        h = Harness(lambda t: [(FIRST, (20, 80))], ocr_failures=1,
                    tuning=PipelineTuning(paused_wait_s=0.05, ocr_retry_s=0.3))
        p = h.pipeline
        with self.assertLogs("app.pipeline.pipeline", "WARNING"):
            p.start()
            try:
                s = h.wait_for(lambda s: s.status == "error", 3.0)
                self.assertEqual(s.message, "Lỗi OCR: CUDA init failed")
                self.assertEqual(h.captures, [])  # no OCR engine -> nothing captured
                h.wait_for(lambda s: translated(s, FIRST), 3.0, after=s.seq)
            finally:
                p.stop()

    def test_stop_before_start_and_start_twice(self):
        h = Harness(lambda t: [])
        h.pipeline.stop()
        with self.assertRaises(RuntimeError):
            h.pipeline.start()
        h2 = Harness(lambda t: [])
        h2.pipeline.start()
        try:
            with self.assertRaises(RuntimeError):
                h2.pipeline.start()
        finally:
            h2.pipeline.stop()

    def test_typewriter_line_is_translated_once_soon_after_it_is_complete(self):
        typed, t0, per_char = "The keeper was not seen after the storm", 0.3, 0.05
        end = t0 + per_char * len(typed)

        def script(t):
            n = max(0, min(len(typed), int((t - t0) / per_char)))
            return [(typed[:n], (10, 100))] if n else []

        h = Harness(script, poll_ms=150)
        p = h.pipeline
        p.start()
        try:
            s = h.wait_for(lambda s: translated(s, typed), end + 2.5)
        finally:
            p.stop()
        done_after = s.timestamp - (h.scene.t0 + time.time() - time.monotonic()) - end
        partial = [x for x in h.sent() if x != typed]
        self.assertLessEqual(len(partial), 1, h.sent())  # no stream of half-typed requests
        self.assertLess(done_after, 1.5)  # the tail is seen and confirmed, not left for an idle rescan

    def test_dead_ocr_engine_is_rebuilt(self):
        h = Harness(lambda t: [(FIRST, (20, 80))] if t < 0.5 else [(SECOND, (20, 80))],
                    ocr_kwargs={"breaks_after": 2}, tuning=PipelineTuning(paused_wait_s=0.05, ocr_failure_retry_s=0.05))
        p = h.pipeline
        with self.assertLogs("app.pipeline.pipeline", "WARNING") as logs:
            p.start()
            try:
                h.wait_for(lambda s: translated(s, FIRST), 3.0)
                h.wait_for(lambda s: s.status == "error" and s.message.startswith("Lỗi OCR"), 3.0)
                h.wait_for(lambda s: translated(s, SECOND), 5.0)
            finally:
                p.stop()
        self.assertEqual(len(h.ocrs), 2)
        self.assertTrue(h.ocrs[0].closed)
        self.assertEqual(h.ocrs[0].calls, 2 + PipelineTuning().ocr_max_failures)
        self.assertTrue(any("rebuilding the OCR engine" in line for line in logs.output))

    def test_batches_of_blocks_that_left_the_screen_are_cancelled(self):
        h = Harness(lambda t: [(FIRST, (20, 80))] if t < 0.4 else [(SECOND, (20, 80))],
                    translator_kwargs={"delay": 1.0})
        p = h.pipeline
        p.start()
        try:
            h.wait_for(lambda s: translated(s, SECOND), 4.0)
        finally:
            p.stop()
        self.assertEqual(h.sent(), [FIRST, SECOND])
        self.assertEqual((p.cancelled_batches, h.translator.cancelled), (1, 1))
        self.assertFalse(any(translated(s, FIRST) for s in h.states))

    def test_inflight_limit_counts_every_language_batch(self):
        lines = [("Open the gate.", (20, 60)), ("扉を開けろ。", (20, 140))]  # EN text in JA mode: 2 batches
        h = Harness(lambda t: lines, translator_kwargs={"delay": 0.3},
                    config_kwargs={"src_lang": "ja", "max_inflight_translations": 1})
        p = h.pipeline
        p.start()
        try:
            h.wait_for(lambda s: all(translated(s, text) for text, _ in lines), 4.0)
        finally:
            p.stop()
        self.assertEqual(h.translator.max_active, 1)
        self.assertEqual(sorted(h.sent()), sorted(text for text, _ in lines))

    def test_animated_background_does_not_drop_a_block_on_one_ocr_miss(self):
        h = Harness(lambda t: [(FIRST, (20, 80))], bar_speed=400, ocr_kwargs={"miss_every": 4},
                    poll_ms=50)
        p = h.pipeline
        p.start()
        try:
            h.wait_for(lambda s: translated(s, FIRST), 3.0)
            time.sleep(3.0)
        finally:
            p.stop()
        self.assertGreaterEqual(h.ocrs[0].calls, 5)  # at least one pass missed the text
        ids = {b.id for s in h.states for b in s.blocks}
        self.assertEqual(len(ids), 1)
        self.assertEqual(h.sent(), [FIRST])

    def test_no_ocr_engine_while_guarded_and_released_when_guarded_long(self):
        h = Harness(lambda t: [(FIRST, (20, 80))], tuning=PipelineTuning(paused_wait_s=0.05,
                                                                         release_ocr_after_guard_s=0.3))
        p = h.pipeline
        p.set_paused(True, "guard")  # a protected game is already in front at startup
        p.start()
        try:
            h.wait_for(lambda s: s.status == "guarded", 2.0)
            time.sleep(0.3)
            self.assertEqual((h.ocrs, h.captures), ([], []))  # no CUDA/model load, no capture
            mark = h.mark()
            p.set_paused(False, "guard")
            h.wait_for(lambda s: translated(s, FIRST), 3.0, after=mark)
            p.set_paused(True, "guard")
            time.sleep(0.8)
            self.assertTrue(h.ocrs[0].closed)  # freed while the game stays in front
            mark = h.mark()
            p.set_paused(False, "guard")
            h.wait_for(lambda s: translated(s, FIRST), 3.0, after=mark)
            self.assertEqual(len(h.ocrs), 2)
        finally:
            p.stop()

    def test_background_capture_backend_is_released_on_a_user_pause(self):
        class BackgroundCapture(FakeCapture):
            background_capture = True  # like WGC: copies frames even when nobody grabs

        h = Harness(lambda t: [(FIRST, (20, 80))], capture_cls=BackgroundCapture)
        p = h.pipeline
        p.start()
        try:
            h.wait_for(lambda s: translated(s, FIRST), 3.0)
            p.set_paused(True)
            h.wait_for(lambda s: s.status == "paused", 2.0)
            time.sleep(0.2)
            self.assertTrue(h.captures[0].closed)
        finally:
            p.stop()

    def test_stop_while_the_ocr_engine_loads_does_no_more_work(self):
        h = Harness(lambda t: [(FIRST, (20, 80))], ocr_create_delay=1.0)
        p = h.pipeline
        p.start()
        time.sleep(0.2)
        with self.assertLogs("app.pipeline.pipeline", "WARNING"):
            p.stop(timeout=0.3)  # gives up waiting for the worker
        states = len(h.states)
        time.sleep(1.3)
        self.assertEqual(h.captures, [])  # the worker left right after the engine load
        self.assertEqual(len(h.states), states)
        self.assertTrue(h.ocrs and h.ocrs[0].closed)

    def test_stop_while_the_translator_is_created(self):
        h = Harness(lambda t: [], region=False, translator_create_delay=0.4)
        p = h.pipeline
        p.start()
        time.sleep(0.1)
        with self.assertNoLogs("app.pipeline.pipeline", "WARNING"):
            p.stop(timeout=3.0)
        self.assertEqual(h.translator.closed_on, h.translator.thread)  # aclose ran (and did not fail)


def refined(state: OverlayState, text: str, prefix: str = "LLM:") -> bool:
    return any(b.source == text and b.translation == f"{prefix}{text}" and b.quality == 2 for b in state.blocks)


def history(states: list[OverlayState], block_id: int) -> list[tuple[str | None, int, str | None]]:
    """(translation, quality, stale_translation) of one block in every state that shows it."""
    return [(b.translation, b.quality, b.stale_translation) for s in states for b in s.blocks if b.id == block_id]


NAME = "Captain Mira"
LINES = ("Where are you going this late at night", "The lighthouse keeper has not been seen",
         "Nobody crossed the bridge after the storm", "We should wait here until the fog lifts")


class LlmTierThreadedTest(unittest.TestCase):
    def assert_never_blank(self, states, block_id):
        seq = history(states, block_id)
        first = next(i for i, x in enumerate(seq) if x[0] is not None)
        self.assertTrue(all(x[0] is not None and x[2] is None for x in seq[first:]), seq)  # no empty / dimmed step
        return [x for x in seq[first:]]

    def test_refine_shows_google_first_then_replaces_it_in_place(self):
        llm = FakeContextual(delay=0.4, speakers={FIRST: "Mira"})
        ctx = TranslationContext(profile="game", version="v1", terms=(Term("Mira", "Mira", "person"),))
        h = Harness(lambda t: [(FIRST, (20, 80))], llm=llm, context_provider=lambda: ctx)
        p = h.pipeline
        main = threading.get_ident()
        p.start()
        try:
            final = h.wait_for(lambda s: refined(s, FIRST), 4.0)
            time.sleep(0.3)
        finally:
            p.stop()
        b = next(b for b in final.blocks if b.source == FIRST)
        self.assertEqual((b.quality, b.speaker), (2, "Mira"))
        shown = self.assert_never_blank(h.states, b.id)
        self.assertEqual(shown[0][:2], (f"VI:{FIRST}", 1))  # Google's translation first...
        self.assertEqual(shown[-1][:2], (f"LLM:{FIRST}", 2))  # ...replaced by the LLM's
        self.assertEqual({x[0] for x in shown}, {f"VI:{FIRST}", f"LLM:{FIRST}"})
        self.assertTrue(any(s.message == "Đang tinh chỉnh 1…" for s in h.states))
        self.assertEqual(final.message, "EN→VI")
        self.assertEqual((h.sent(), len(llm.calls)), ([FIRST], 1))
        [item] = llm.calls[0]
        self.assertEqual((item.id, item.text, item.role_hint), (b.id, FIRST, "dialog"))
        self.assertIs(llm.contexts[0], ctx)
        # Threads: the factory and the LLM run on the translation loop, the callback on the worker.
        self.assertEqual(llm.call_threads, {h.translator.thread})
        self.assertEqual(h.factory_threads, {h.translator.thread})
        self.assertNotIn(main, h.screen_result_threads | llm.call_threads)
        self.assertEqual(h.screen_result_threads, {h.ocrs[0].thread})
        [(res, items)] = h.screen_results  # once per applied job
        self.assertEqual((res.translations, res.speakers, res.model), ({b.id: f"LLM:{FIRST}"}, {b.id: "Mira"}, "fake-llm"))
        self.assertEqual([it.text for it in items], [FIRST])
        d = state_to_dict(final)["blocks"][0]
        self.assertEqual((d["quality"], d["speaker"]), (2, "Mira"))
        self.assertEqual(llm.closed, 1)  # aclose on the loop at shutdown

    def test_newest_wins_no_backlog_and_results_for_changed_text_dropped(self):
        # The name label stays, so the first job is not cancelled; the line changes 3 times meanwhile.
        def script(t):
            line = LINES[0] if t < 0.4 else LINES[1] if t < 0.7 else LINES[2] if t < 1.0 else LINES[3]
            return [(NAME, (20, 40)), (line, (20, 130))]

        llm = FakeContextual(delay=1.2)
        h = Harness(script, llm=llm)
        p = h.pipeline
        p.start()
        try:
            final = h.wait_for(lambda s: refined(s, LINES[3]) and refined(s, NAME), 5.0)
            time.sleep(0.3)
        finally:
            p.stop()
        self.assertEqual(llm.max_active, 1)  # one job in flight
        self.assertEqual(len(llm.calls), 2, [llm.texts(i) for i in range(len(llm.calls))])  # B, C never queued
        self.assertEqual(llm.texts(0), [NAME, LINES[0]])
        self.assertEqual(llm.texts(1), [NAME, LINES[3]])  # the name as context, the newest line
        for s in h.states:  # an LLM result only ever shows on the text it was made for
            for b in s.blocks:
                if b.quality == 2:
                    self.assertEqual(b.translation, f"LLM:{b.source}")
        self.assertFalse(any(b.source in LINES[:3] and b.quality == 2 for s in h.states for b in s.blocks))
        name_id = next(b.id for b in final.blocks if b.source == NAME)
        self.assert_never_blank(h.states, name_id)
        self.assertEqual([sorted(r.translations.values()) for r, _ in h.screen_results],
                         [[f"LLM:{NAME}"], [f"LLM:{LINES[3]}"]])

    def test_glossary_names_skip_machine_translation(self):
        # A speaker label that is exactly a character name shows the profile's rendering at once (as
        # tier 2), in refine mode and with the LLM off, and is never sent to Google; the LLM still
        # sees it as context.
        ctx = TranslationContext(profile="game", version="v1",
                                 characters=(Character(NAME, "Thuyền trưởng Mira", "nữ", "tôi"),))
        script = lambda t: [(NAME, (20, 40)), (FIRST, (20, 130))]  # noqa: E731
        for mode in ("refine", "off"):
            with self.subTest(mode=mode):
                llm = FakeContextual(delay=0.4)
                h = Harness(script, llm=llm, context_provider=lambda: ctx, config_kwargs={"llm_mode": mode})
                p = h.pipeline
                p.start()
                try:
                    done = (lambda s: refined(s, FIRST)) if mode == "refine" else (lambda s: translated(s, FIRST))
                    final = h.wait_for(done, 4.0)
                    time.sleep(0.2)
                finally:
                    p.stop()
                name = next(b for b in final.blocks if b.source == NAME)
                self.assertEqual((name.translation, name.quality), ("Thuyền trưởng Mira", 2))
                shown = [x[:2] for x in history(h.states, name.id) if x[0] is not None]
                self.assertEqual(set(shown), {("Thuyền trưởng Mira", 2)})  # never a Google rendering first
                self.assertEqual(h.sent(), [FIRST])
                if mode == "refine":
                    self.assertEqual(llm.texts(0), [NAME, FIRST])  # context for the speaker
                    self.assertEqual(sorted(h.screen_results[0][0].translations.values()), [f"LLM:{FIRST}"])
                else:
                    self.assertEqual(llm.calls, [])

    def test_a_job_whose_text_is_gone_is_cancelled(self):
        h = Harness(lambda t: [(FIRST, (20, 80))] if t < 0.4 else [(SECOND, (20, 80))], llm=FakeContextual(delay=1.0))
        p = h.pipeline
        p.start()
        try:
            h.wait_for(lambda s: refined(s, SECOND), 4.0)
        finally:
            p.stop()
        self.assertEqual(h.llm.cancelled, 1)
        self.assertEqual(p.cancelled_llm_jobs, 1)
        self.assertEqual([h.llm.texts(i) for i in range(len(h.llm.calls))], [[FIRST], [SECOND]])
        self.assertEqual(len(h.screen_results), 1)

    def test_only_mode_waits_for_the_llm_without_google(self):
        llm = FakeContextual(delay=0.3)
        h = Harness(lambda t: [(FIRST, (20, 80))], llm=llm, config_kwargs={"llm_mode": "only"})
        p = h.pipeline
        p.start()
        try:
            final = h.wait_for(lambda s: refined(s, FIRST), 4.0)
        finally:
            p.stop()
        self.assertEqual(h.sent(), [])  # no Google request while the LLM works
        waiting = [s for s in h.states if s.blocks and s.blocks[0].translation is None]
        self.assertTrue(any(s.pending == 1 and s.message == "Đang dịch 1…" for s in waiting), waiting)
        self.assertEqual([x[:2] for x in self.assert_never_blank(h.states, final.blocks[0].id)][0], (f"LLM:{FIRST}", 2))

    def test_only_mode_falls_back_to_google_while_no_llm_is_ready(self):
        ready = threading.Event()
        h = Harness(lambda t: [(FIRST, (20, 80))], llm=FakeContextual(delay=0.05), llm_ready=ready.is_set,
                    config_kwargs={"llm_mode": "only"}, tuning=PipelineTuning(paused_wait_s=0.1, llm_probe_s=0.2))
        p = h.pipeline
        p.start()
        try:
            s = h.wait_for(lambda s: translated(s, FIRST), 3.0)
            self.assertEqual(s.blocks[0].quality, 1)
            time.sleep(0.6)
            probes = h.factory_calls
            self.assertLessEqual(probes, 8)  # asked about every llm_probe_s, not on every loop
            self.assertEqual(h.llm.calls, [])
            ready.set()  # the server is up: the Google translation is refined in place
            final = h.wait_for(lambda s: refined(s, FIRST), 3.0, after=s.seq)
        finally:
            p.stop()
        self.assertEqual(h.sent(), [FIRST])
        self.assert_never_blank(h.states, final.blocks[0].id)

    def test_refine_without_a_model_keeps_a_steady_status(self):
        # No model installed (contextual_factory always None): the pipeline keeps probing every
        # llm_probe_s, but the status must not flip to "refining" and back on every probe.
        h = Harness(lambda t: [(FIRST, (20, 80))], llm=FakeContextual(delay=0.05), llm_ready=lambda: False,
                    tuning=PipelineTuning(paused_wait_s=0.1, llm_probe_s=0.15))
        p = h.pipeline
        p.start()
        try:
            s = h.wait_for(lambda s: translated(s, FIRST), 3.0)
            mark = len(h.states)
            time.sleep(1.0)
        finally:
            p.stop()
        self.assertEqual(s.blocks[0].quality, 1)
        self.assertGreaterEqual(h.factory_calls, 3)  # it did keep asking
        later = h.states[mark:]
        self.assertFalse([x.message for x in later if x.message.startswith("Đang tinh chỉnh")])
        self.assertLessEqual(len(later), 1, [x.message for x in later])
        self.assertEqual(h.llm.calls, [])

    def test_only_mode_falls_back_to_google_when_the_llm_times_out(self):
        llm = FakeContextual(delay=3.0)
        h = Harness(lambda t: [(FIRST, (20, 80))], llm=llm, config_kwargs={"llm_mode": "only"},
                    tuning=PipelineTuning(paused_wait_s=0.1, llm_timeout_s=0.3, llm_hold_s=5.0))
        p = h.pipeline
        t0 = time.monotonic()
        with self.assertLogs("app.pipeline.pipeline", "WARNING") as logs:
            p.start()
            try:
                s = h.wait_for(lambda s: translated(s, FIRST), 4.0)
                waited = time.monotonic() - t0
                h.wait_for(lambda s: s.message == "LLM lỗi: quá 0.3 s", 2.0)
            finally:
                p.stop()
        self.assertGreaterEqual(waited, 0.3)  # the LLM had its chance first
        self.assertEqual((len(llm.calls), llm.cancelled, h.sent()), (1, 1, [FIRST]))
        self.assertTrue(any("no LLM job for 5 s" in line for line in logs.output))

    def test_llm_cache_hit_skips_google(self):
        h = Harness(lambda t: [(FIRST, (20, 80))] if t < 0.8 or t >= 1.6 else [(SECOND, (20, 80))],
                    llm=FakeContextual(delay=0.1))
        p = h.pipeline
        p.start()
        try:
            first = h.wait_for(lambda s: refined(s, FIRST), 3.0)
            h.wait_for(lambda s: refined(s, SECOND), 3.0)
            first_id = next(b.id for b in first.blocks if b.source == FIRST)
            again = h.wait_for(lambda s: any(b.source == FIRST and b.id != first_id and b.translation for b in s.blocks),
                               3.0)
            time.sleep(0.3)
        finally:
            p.stop()
        b = next(b for b in again.blocks if b.source == FIRST)
        self.assertEqual((b.translation, b.quality), (f"LLM:{FIRST}", 2))  # straight from the cache
        self.assertEqual(h.sent(), [FIRST, SECOND])  # no Google request for the repeated line
        self.assertEqual([h.llm.texts(i) for i in range(len(h.llm.calls))], [[FIRST], [SECOND]])

    def test_tier2_cache_is_per_speaker_and_cached_dialogue_reaches_the_memory(self):
        # The same line said by someone else needs its own LLM translation (anh/em, tao/mày depend on
        # who speaks); said again by the same speaker it comes from the cache, and still reaches
        # on_screen_result (the dialogue memory).
        line = LINES[0]

        def script(speakers):
            def at(t):
                sp = speakers[min(int(t / 0.8), len(speakers) - 1)]
                return [(sp, (20, 40)), (line if sp != "Ren" else LINES[1], (20, 130))]
            return at

        for third, llm_calls in (("Sera", 2), ("Kenji", 3)):
            with self.subTest(third=third):
                llm = FakeContextual(delay=0.05)
                h = Harness(script(["Sera", "Ren", third]), llm=llm)
                p = h.pipeline
                p.start()
                try:
                    h.wait_for(lambda s: refined(s, LINES[1]), 4.0)  # Ren's screen
                    mark = h.mark()
                    h.wait_for(lambda s: any(b.source == third for b in s.blocks) and refined(s, line), 4.0, mark)
                    time.sleep(0.3)
                finally:
                    p.stop()
                self.assertEqual(len(llm.calls), llm_calls, [llm.texts(i) for i in range(len(llm.calls))])
                last_items = h.screen_results[-1][1]
                self.assertEqual([it.text for it in last_items], [third, line])
                self.assertEqual(h.screen_results[-1][0].translations[last_items[1].id], f"LLM:{line}")
                self.assertTrue(all(c is not None for c in h.screen_contexts))
                final = h.states[-1]
                b = next(b for b in final.blocks if b.source == line)
                self.assertEqual(b.speaker if third == "Sera" else third, third)  # cached: speaker from the label

    def test_a_job_served_from_the_tier2_cache_keeps_the_speakers(self):
        # A line whose Google batch went out before its label was confirmed (no speaker, no cache hit
        # there) is found in the tier-2 cache by the LLM job that follows, under the label's speaker. That
        # result keeps the speaker, as the batch path does, for the overlay and the dialogue memory (the
        # e2e "speaker attribution" lost it when the job path won the race).
        from app.pipeline.pipeline import _LlmJob  # noqa: PLC0415
        from app.translate.cache import TranslationCache  # noqa: PLC0415
        line = LINES[0]
        h = Harness(lambda t: [], llm=FakeContextual(delay=0.0))
        p = h.pipeline
        p._llm_cache = TranslationCache(None)
        items = (ScreenItem(1, "Sera", (20, 20, 60, 20), "name"), ScreenItem(2, line, (20, 60, 400, 20), "dialog"))
        ctx = TranslationContext(profile="p", version="v")
        for needed, cached in (({2: line}, {2: f"LLM:{line}"}), ({1: "Sera", 2: line}, {2: f"LLM:{line}"})):
            with self.subTest(needed=needed):
                keys, layout = p._tier2_keys(items, ctx)
                job = _LlmJob(1, items, needed, "en", "vi", ctx, p._ctx_key(ctx), 0, time.perf_counter(), keys,
                              layout=layout)
                self.assertIsNotNone(p._current_contextual())
                p._llm_cache_put(job, cached)
                asyncio.run(p._run_llm_job(job))
                r = p._results.get_nowait()  # all cached, or the cached part first (partial)
                self.assertEqual((r.result.translations, r.result.speakers), (cached, {2: "Sera"}))
                while not p._results.empty():
                    p._results.get_nowait()

    def test_lines_left_with_known_wrong_roles_are_not_cached(self):
        # A line the role re-ask did not get to (over the cap, no time left, the request failed) keeps roles
        # the checker found wrong: it is shown, but not cached, so the same text on a later screen is asked
        # again. The job's timeout is the translator's time budget (the re-ask must not cost the first answer).
        from app.pipeline.pipeline import _LlmJob  # noqa: PLC0415
        from app.translate.cache import TranslationCache  # noqa: PLC0415

        class Reasked(FakeContextual):
            outcome = "over the cap"

            async def translate_screen(self, items, src, tgt, ctx):
                self.budget = self.time_budget_s
                result = await super().translate_screen(items, src, tgt, ctx)
                self.last_role_reask = {3: self.outcome, 4: "taken"}
                return result

        items = (ScreenItem(1, "Sera", (20, 20, 60, 20), "name"),
                 ScreenItem(2, LINES[0], (20, 60, 400, 20), "dialog"),
                 ScreenItem(3, LINES[1], (20, 100, 400, 20), "dialog"),
                 ScreenItem(4, LINES[2], (20, 140, 400, 20), "dialog"))
        ctx = TranslationContext(profile="p", version="v")
        for outcome in ("over the cap", "no time", "failed", "rejected"):
            with self.subTest(outcome=outcome):
                llm = Reasked(delay=0.0)
                llm.outcome = outcome
                h = Harness(lambda t: [], llm=llm)
                p = h.pipeline
                p._llm_cache = TranslationCache(None)
                keys, layout = p._tier2_keys(items, ctx)
                needed = {it.id: it.text for it in items[1:]}
                job = _LlmJob(1, items, needed, "en", "vi", ctx, p._ctx_key(ctx), 0, time.perf_counter(), keys,
                              layout=layout, timeout=7.5)
                self.assertIsNotNone(p._current_contextual())
                asyncio.run(p._run_llm_job(job))
                self.assertEqual(llm.budget, 7.5)
                r = p._results.get_nowait()
                self.assertLessEqual({2, 3, 4}, set(r.result.translations))  # all shown
                cached = {bid: p._llm_cache_get(text, "en", keys.get(bid, job.key)) for bid, text in needed.items()}
                self.assertEqual(cached[2], f"LLM:{LINES[0]}")
                self.assertEqual(cached[4], f"LLM:{LINES[2]}")
                self.assertEqual(cached[3], f"LLM:{LINES[1]}" if outcome == "rejected" else None)

    def test_jobs_ask_for_at_most_llm_max_needed_with_context_items(self):
        lines = [(f"Menu entry number {i} for the test", (20, 30 + 60 * i)) for i in range(3)]
        llm = FakeContextual(delay=0.05)
        h = Harness(lambda t: lines, llm=llm,
                    tuning=PipelineTuning(paused_wait_s=0.1, llm_max_needed=2, llm_max_items=3))
        p = h.pipeline
        p.start()
        try:
            h.wait_for(lambda s: all(refined(s, text) for text, _ in lines), 5.0)
        finally:
            p.stop()
        first = llm.calls[0]
        self.assertEqual(len(first), 3)
        self.assertEqual(sum(not it.context_only for it in first), 2)  # asked for
        self.assertEqual(sum(it.context_only for it in first), 1)  # only there for context
        asked = [it.text for call in llm.calls for it in call if not it.context_only]
        self.assertEqual(sorted(asked), sorted(text for text, _ in lines))  # each asked once

    def test_a_job_too_big_for_the_timeout_is_split_not_held(self):
        class SlowPerItem(FakeContextual):
            async def translate_screen(self, items, src, tgt, ctx):
                self.delay = 0.15 * sum(1 for it in items if not it.context_only)
                return await super().translate_screen(items, src, tgt, ctx)

        lines = [(f"Quest log entry number {i} here", (20, 30 + 60 * i)) for i in range(3)]
        llm = SlowPerItem()
        h = Harness(lambda t: lines, llm=llm,
                    tuning=PipelineTuning(paused_wait_s=0.1, llm_max_needed=4, llm_timeout_s=0.4,
                                          llm_timeout_per_item_s=0.0, llm_timeout_per_char_s=0.0, llm_hold_s=30.0))
        p = h.pipeline
        with self.assertLogs("app.pipeline.pipeline", "WARNING") as logs:
            p.start()
            try:
                h.wait_for(lambda s: all(refined(s, text) for text, _ in lines), 8.0)
            finally:
                p.stop()
        needed = [sum(1 for it in call if not it.context_only) for call in llm.calls]
        self.assertEqual(needed[:3], [3, 1, 2])  # halved after the timeout, doubled again after a success
        self.assertFalse(any("no LLM job for" in line for line in logs.output), logs.output)  # no hold
        self.assertTrue(any("next jobs ask for at most 1" in line for line in logs.output))

    def test_llm_errors_back_off_without_holding_up_google(self):
        llm = FakeContextual(delay=0.01, fail=True)
        h = Harness(lambda t: [(FIRST, (20, 80))] if t < 0.3 else [(FIRST, (20, 80)), (SECOND, (20, 150))], llm=llm,
                    tuning=PipelineTuning(paused_wait_s=0.1, llm_hold_s=0.4))
        p = h.pipeline
        with self.assertLogs("app.pipeline.pipeline", "WARNING"):
            p.start()
            try:
                h.wait_for(lambda s: translated(s, FIRST), 3.0)
                h.wait_for(lambda s: translated(s, SECOND), 1.5)  # Google is not held by the LLM's failures
                h.wait_for(lambda s: s.message == "LLM lỗi: HTTP 500", 2.0)
                time.sleep(max(0.0, 1.9 - (time.monotonic() - h.scene.t0)))
            finally:
                p.stop()
        gaps = [b - a for a, b in zip(llm.call_times, llm.call_times[1:])]
        self.assertEqual(len(llm.calls), 3, gaps)  # at ~0, +0.4, +0.8 more; the next one would be 1.6 s later
        self.assertGreaterEqual(gaps[0], 0.38)
        self.assertGreaterEqual(gaps[1], 0.78)
        self.assertEqual(sorted(h.sent()), sorted([FIRST, SECOND]))
        self.assertEqual(h.screen_results, [])

    def test_context_change_redoes_tier2_in_place_and_mode_off(self):
        version = ["v1"]

        def respond(items, ctx):
            return ScreenResult({it.id: f"LLM[{ctx.version}]:{it.text}" for it in items})

        h = Harness(lambda t: [(FIRST, (20, 80))] if t < 2.0 else [(FIRST, (20, 80)), (SECOND, (20, 150))],
                    llm=FakeContextual(delay=0.1, respond=respond),
                    context_provider=lambda: TranslationContext(profile="p", version=version[0]))
        p = h.pipeline
        p.start()
        try:
            s1 = h.wait_for(lambda s: refined(s, FIRST, "LLM[v1]:"), 3.0)
            version[0] = "v2"
            p.set_context_version("p:v2")
            s2 = h.wait_for(lambda s: refined(s, FIRST, "LLM[v2]:"), 3.0, after=s1.seq)
            p.set_llm_mode("off")
            with self.assertRaises(ValueError):
                p.set_llm_mode("sometimes")
            h.wait_for(lambda s: translated(s, SECOND), 3.0, after=s2.seq)
            time.sleep(0.3)
        finally:
            p.stop()
        shown = self.assert_never_blank(h.states, s1.blocks[0].id)
        self.assertEqual([x[0] for x in shown][-1], f"LLM[v2]:{FIRST}")
        self.assertEqual(len(h.llm.calls), 2)  # the new text after "off" went to Google only
        self.assertEqual(h.sent(), [FIRST, SECOND])
        self.assertEqual(p.llm_mode, "off")


# -- real hardware / network smoke (opt-in) ---------------------------------------------------
@unittest.skipUnless(os.environ.get("PIPELINE_REAL_SMOKE") == "1", "set PIPELINE_REAL_SMOKE=1 (CUDA + network)")
class RealSmokeTest(unittest.TestCase):
    SAMPLE = SYNTHETIC / "game_dialog" / "ja_0.png"

    def test_real_ocr_layout_tracker(self):
        from app.ocr.rapid import RapidOcrEngine

        img = cv2.imread(str(self.SAMPLE))
        t0 = time.perf_counter()
        engine = RapidOcrEngine("ja", "small", "small", "cuda")
        init_ms = (time.perf_counter() - t0) * 1000
        engine.warmup(2)
        ro = img.copy()
        ro.setflags(write=False)  # dxcam frames are read-only
        ocr_ms = []
        for _ in range(5):
            t1 = time.perf_counter()
            boxes = engine.recognize(ro)
            ocr_ms.append((time.perf_counter() - t1) * 1000)
        boxes2 = engine.recognize(ro)
        t2 = time.perf_counter()
        groups = layout_blocks(boxes, "ja")
        t3 = time.perf_counter()
        colors = [block_colors(img, g.lines) for g in groups]
        t4 = time.perf_counter()
        tracker = BlockTracker("ja")
        res = tracker.update(groups, 0.0, colors)
        res2 = tracker.update(layout_blocks(boxes2, "ja"), 0.5, colors)
        t5 = time.perf_counter()
        fp_ms = []
        for _ in range(10):
            t6 = time.perf_counter()
            fingerprint(ro)
            fp_ms.append((time.perf_counter() - t6) * 1000)
        print(f"\n[smoke] RapidOCR ja small/small cuda on {img.shape[1]}x{img.shape[0]}: init {init_ms:.0f} ms, "
              f"recognize p50 {statistics.median(ocr_ms):.1f} ms (runs {[round(x) for x in ocr_ms]}); "
              f"layout {(t3 - t2) * 1000:.2f} ms, colours {(t4 - t3) * 1000:.2f} ms, 2 tracker passes "
              f"{(t5 - t4) * 1000:.2f} ms, fingerprint p50 {statistics.median(fp_ms):.2f} ms")
        for g, (bg, fg) in zip(groups, colors):
            print(f"[smoke]   {g.box} bg={bg} fg={fg} {g.source!r}")
        self.assertEqual([g.source for g in groups][-2:][0], "ミラ隊長")
        self.assertTrue(groups[-1].source.startswith("北の塔には鍵が"))
        self.assertEqual(len(res.added), len(groups))
        self.assertEqual(res2.added, [])  # second pass on the same frame keeps every id

    def test_real_pipeline_with_live_google(self):
        from app.translate import CachedTranslator, GoogleGtxTranslator, TranslationCache

        img = cv2.imread(str(self.SAMPLE))
        img.setflags(write=False)
        h, w = img.shape[:2]

        class StaticCapture(CaptureBackend):
            def grab(self, region):
                return Frame(img, time.perf_counter(), region)

        states: list[OverlayState] = []
        done = threading.Event()

        def on_state(s: OverlayState) -> None:
            states.append(s)
            if s.blocks and all(b.translation or b.error for b in s.blocks):
                done.set()

        cfg = Config(src_lang="ja", region=[0, 0, w, h])
        p = TranslationPipeline(
            cfg, on_state, capture_factory=StaticCapture,
            translator_factory=lambda: CachedTranslator(GoogleGtxTranslator(), TranslationCache(None)),
        )
        t0 = time.perf_counter()
        p.start()
        try:
            self.assertTrue(done.wait(30), f"no translations; last state {states[-1] if states else None}")
            total = time.perf_counter() - t0
        finally:
            p.stop()
        final = states[-1] if states[-1].blocks else next(s for s in reversed(states) if s.blocks)
        m = final.metrics
        print(f"\n[smoke] pipeline start -> all translated {total:.2f} s (includes OCR engine load); "
              f"ocr {m.get('ocr_ms')} ms, grab {m.get('grab_ms')} ms, translate batch "
              f"{m.get('translate_ms')} ms for {m.get('translate_items')} texts; {len(states)} states")
        for b in final.blocks:
            print(f"[smoke]   #{b.id} {b.source!r} -> {b.translation!r} err={b.error}")
        self.assertTrue(all(b.translation for b in final.blocks), [b.error for b in final.blocks])

    def test_real_dxcam_grabs_and_guard_release(self):
        """Default (dxcam) capture on the worker thread. OCR is stubbed: no screen text is read."""
        shapes: list[tuple] = []

        class ShapeOnlyOcr(OcrEngine):
            name = "shape-only"

            def recognize(self, image_bgr):
                shapes.append(image_bgr.shape)
                return []

        states: list[OverlayState] = []
        cfg = Config(src_lang="en", region=[100, 100, 800, 300])
        p = TranslationPipeline(cfg, states.append, ocr_factory=lambda lang: ShapeOnlyOcr(),
                                translator_factory=FakeTranslator)
        p.start()
        try:
            deadline = time.monotonic() + 10
            while not shapes and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(shapes[0], (300, 800, 3))
            time.sleep(1.0)
            grabs_running = p.grab_count
            p.set_paused(True, "guard")
            g0 = p.grab_count
            time.sleep(0.6)
            self.assertEqual(p.grab_count, g0)
            self.assertEqual(p.snapshot().status, "guarded")
            self.assertIsNone(p._capture)  # dxcam released while a guarded game has focus
            p.set_paused(False, "guard")
            deadline = time.monotonic() + 5
            while p.grab_count == g0 and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertGreater(p.grab_count, g0)
        finally:
            p.stop()
        print(f"\n[smoke] dxcam via pipeline: {grabs_running} grabs in ~1 s before guard, "
              f"{len(shapes)} OCR passes, grab {states[-1].metrics.get('grab_ms') if states else '?'} ms")


if __name__ == "__main__":
    unittest.main()
