"""Demo window for end-to-end tests: cycles game-like scenes of Japanese / English text.

A normal topmost window at a fixed physical position (default client 120,120 1280x720) that never
takes focus. Scenes (all sentences are original): a Japanese dialog box over a colourful landscape,
an English outlined subtitle over a busy background, a Japanese menu, an English paragraph, and a
Japanese line typed out character by character. The "llm" set is one Japanese conversation between
a sister and her older brother (and a friend who is not in the glossary), a page per scene, for the
contextual LLM tier (names, pronouns, auto terms).

--log FILE gets JSON lines: first {"rect": [l, t, w, h], ...} (physical client rect), then
{"ts": time.time(), "scene", "lines", "final", "cycle"} every time the visible text changes
("final" is false while the typewriter line is still growing). --shots DIR saves the last frame
of each scene once as DIR/<scene>.png (used to composite overlay snapshots for review).

Run: python -m tools.demo_text_window --log demo.jsonl --duration 30 [--scenes ja|en|mixed|llm]
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import random
import sys
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPolygonF)
from PySide6.QtWidgets import QApplication, QWidget

from app.capture.base import Region
from app.overlay.geometry import physical_to_logical

JA_FONT = "Yu Gothic UI"
EN_FONT = "Segoe UI"

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetClientRect.restype = wintypes.BOOL
_user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.ClientToScreen.restype = wintypes.BOOL
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.SetThreadExecutionState.argtypes = [wintypes.DWORD]
_kernel32.SetThreadExecutionState.restype = wintypes.DWORD
_ES_CONTINUOUS, _ES_DISPLAY_REQUIRED = 0x80000000, 0x00000002


def keep_display_on(on: bool) -> None:
    """Like a video player: keep the display from idling off while the demo runs (Desktop Duplication
    delivers no frames while the monitor sleeps). Released on exit or with on=False."""
    _kernel32.SetThreadExecutionState(_ES_CONTINUOUS | (_ES_DISPLAY_REQUIRED if on else 0))


def client_rect(hwnd: int) -> Region:
    """Physical client rect of `hwnd` in screen coordinates (the process is PMv2)."""
    rc = wintypes.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(rc))
    pt = wintypes.POINT(0, 0)
    _user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return Region(pt.x, pt.y, rc.right - rc.left, rc.bottom - rc.top)


# --- drawing helpers -----------------------------------------------------------------------

def font(family: str, px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(family)
    f.setPixelSize(px)
    f.setWeight(weight)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def draw_text(p: QPainter, x: float, baseline: float, text: str, f: QFont, color: QColor,
              outline: QColor | None = None, outline_px: float = 0.0) -> float:
    """Draw `text` with its baseline at (x, baseline); optional outline. Returns the advance width."""
    if outline is not None and outline_px > 0:
        path = QPainterPath()
        path.addText(QPointF(x, baseline), f, text)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(outline, outline_px * 2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(path, pen)
        p.fillPath(path, color)
        p.restore()
    else:
        p.setFont(f)
        p.setPen(color)
        p.drawText(QPointF(x, baseline), text)
    return QFontMetricsF(f).horizontalAdvance(text)


def wrap(text: str, f: QFont, width: float) -> list[str]:
    fm = QFontMetricsF(f)
    lines, cur = [], ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if cur and fm.horizontalAdvance(cand) > width:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def landscape(p: QPainter, w: int, h: int) -> None:
    sky = QLinearGradient(0, 0, 0, h)
    sky.setColorAt(0.0, QColor(64, 120, 214))
    sky.setColorAt(0.55, QColor(244, 168, 96))
    sky.setColorAt(1.0, QColor(196, 92, 120))
    p.fillRect(QRectF(0, 0, w, h), sky)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 236, 140))
    p.drawEllipse(QPointF(w * 0.78, h * 0.22), 70, 70)
    p.setBrush(QColor(255, 255, 255, 170))
    for cx, cy, rx in ((180, 110, 90), (260, 95, 70), (620, 160, 110), (700, 140, 80), (1040, 80, 60)):
        p.drawEllipse(QPointF(cx, cy), rx, rx * 0.45)
    for color, base, amp, phase in ((QColor(70, 150, 90), 0.55, 60, 0.3), (QColor(40, 110, 70), 0.68, 45, 1.7),
                                    (QColor(30, 80, 60), 0.80, 30, 2.9)):
        pts = [QPointF(0, h)]
        for i in range(0, 41):
            x = w * i / 40
            pts.append(QPointF(x, h * base - amp * math.sin(i / 40 * 6.28 * 1.3 + phase)))
        pts.append(QPointF(w, h))
        p.setBrush(color)
        p.drawPolygon(QPolygonF(pts))
    p.setBrush(QColor(20, 60, 40))
    for x in (90, 150, 420, 860, 930, 1180):
        p.drawPolygon(QPolygonF([QPointF(x, h * 0.62), QPointF(x - 26, h * 0.74), QPointF(x + 26, h * 0.74)]))


def busy_background(p: QPainter, w: int, h: int, seed: int = 7) -> None:
    rnd = random.Random(seed)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor(30, 90, 140))
    g.setColorAt(1, QColor(150, 60, 40))
    p.fillRect(QRectF(0, 0, w, h), g)
    p.setPen(Qt.PenStyle.NoPen)
    for _ in range(170):
        c = QColor(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256), rnd.randrange(90, 230))
        p.setBrush(c)
        x, y = rnd.uniform(-40, w), rnd.uniform(-40, h)
        rw, rh = rnd.uniform(20, 180), rnd.uniform(10, 120)
        if rnd.random() < 0.5:
            p.drawRect(QRectF(x, y, rw, rh))
        else:
            p.drawEllipse(QRectF(x, y, rw, rh))
    p.setPen(QPen(QColor(255, 255, 255, 60), 6))
    for i in range(-h, w, 48):
        p.drawLine(QPointF(i, h), QPointF(i + h, 0))


def dialog_box(p: QPainter, rect: QRectF) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QPen(QColor(255, 255, 255, 170), 2))
    p.setBrush(QColor(8, 12, 32, 205))
    p.drawRoundedRect(rect, 14, 14)
    p.restore()


# --- scenes --------------------------------------------------------------------------------

@dataclass
class Scene:
    name: str
    lang: str
    frames: int  # number of distinct frames (typewriter > 1)
    render: Callable[[QPainter, int, int, int], list[str]]  # (painter, w, h, frame) -> visible lines


JA_DIALOG_NAME = "セラ"
JA_DIALOG_LINES = ("灯台の明かりが消えてから、もう三日も経った。", "今夜こそ、あの丘の向こうを確かめに行こう。")
EN_SUBTITLE_LINES = ("I kept the map you drew for me,", "even after the river swallowed the old bridge.")
JA_MENU_ITEMS = ("冒険を続ける", "新しい旅を始める", "装備を整える", "設定", "タイトルに戻る")
EN_HEADING = "Harbor Notice"
EN_PARAGRAPH = ("The harbor office opens at dawn, but the clerk rarely arrives before the second bell. "
                "Travelers who need a permit should bring two copies of their papers and a little patience. "
                "When the fog rolls in, the ferry waits in the inner bay until the lighthouse signals "
                "that the channel is clear.")
JA_TYPEWRITER = "扉の向こうで、誰かが小さな声で歌っている。"
# LLM scenario: one conversation, one page per scene (speaker label above the line). Who speaks to whom
# decides the Vietnamese pronouns: Sera (younger sister) says "anh" to Ren and "em" of herself; Ren
# (older brother) says "em" (or "mày") to Sera. Kenji is not in the profile tools.e2e_check writes:
# his name label is what the LLM proposes as a new (auto) term, once it has labelled two of his lines.
JA_TALK = (
    ("セラ", "お兄ちゃん、灯台の鍵はどこにあるの？"),
    ("レン", "お前が昨日なくしたんだろう。井戸の近くを探してみろ。"),
    ("セラ", "ごめんね。一緒に探してくれる？"),
    ("レン", "仕方ないな。日が暮れる前に行くぞ、セラ。"),
    ("ケンジ", "おーい、セラ！灯台の鍵なら俺が拾っておいたぞ。"),
    ("ケンジ", "井戸のそばに落ちてたんだ。ほら、受け取れ。"),  # a 2nd line: his label is a speaker, not a heading
)


def render_ja_dialog(p: QPainter, w: int, h: int, _frame: int) -> list[str]:
    landscape(p, w, h)
    dialog_box(p, QRectF(60, 450, w - 120, 230))
    draw_text(p, 100, 505, JA_DIALOG_NAME, font(JA_FONT, 30, QFont.Weight.Bold), QColor(255, 222, 130))
    body = font(JA_FONT, 32, QFont.Weight.DemiBold)
    for i, line in enumerate(JA_DIALOG_LINES):
        draw_text(p, 100, 575 + i * 58, line, body, QColor(255, 255, 255))
    return [JA_DIALOG_NAME, *JA_DIALOG_LINES]


def render_en_subtitle(p: QPainter, w: int, h: int, _frame: int) -> list[str]:
    busy_background(p, w, h)
    f = font(EN_FONT, 40, QFont.Weight.DemiBold)
    fm = QFontMetricsF(f)
    for i, line in enumerate(EN_SUBTITLE_LINES):
        x = (w - fm.horizontalAdvance(line)) / 2
        draw_text(p, x, 608 + i * 54, line, f, QColor(255, 255, 255), QColor(0, 0, 0), 3.0)
    return list(EN_SUBTITLE_LINES)


def render_ja_menu(p: QPainter, w: int, h: int, _frame: int) -> list[str]:
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor(18, 22, 48))
    g.setColorAt(1, QColor(70, 34, 84))
    p.fillRect(QRectF(0, 0, w, h), g)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    for cx, cy, r, a in ((900, 260, 190, 60), (1050, 480, 140, 45), (780, 560, 90, 35)):
        p.setBrush(QColor(140, 180, 255, a))
        p.drawEllipse(QPointF(cx, cy), r, r)
    p.setPen(QPen(QColor(190, 200, 255, 140), 2))
    p.setBrush(QColor(0, 0, 0, 150))
    p.drawRoundedRect(QRectF(70, 110, 500, 500), 18, 18)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 45))
    p.drawRoundedRect(QRectF(90, 158, 460, 62), 10, 10)  # highlight on the first item
    f = font(JA_FONT, 32, QFont.Weight.DemiBold)
    for i, item in enumerate(JA_MENU_ITEMS):
        draw_text(p, 120, 200 + i * 92, item, f, QColor(245, 245, 255))
    return list(JA_MENU_ITEMS)


def render_en_paragraph(p: QPainter, w: int, h: int, _frame: int) -> list[str]:
    p.fillRect(QRectF(0, 0, w, h), QColor(236, 230, 214))
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QPen(QColor(150, 130, 100), 2))
    p.setBrush(QColor(248, 244, 232))
    p.drawRoundedRect(QRectF(60, 60, w - 120, h - 120), 10, 10)
    head = font(EN_FONT, 40, QFont.Weight.Bold)
    draw_text(p, 110, 150, EN_HEADING, head, QColor(70, 40, 20))
    body = font(EN_FONT, 30)
    lines = wrap(EN_PARAGRAPH, body, w - 240)
    for i, line in enumerate(lines):
        draw_text(p, 110, 240 + i * 46, line, body, QColor(40, 36, 32))
    return [EN_HEADING, *lines]


def render_ja_typewriter(p: QPainter, w: int, h: int, frame: int) -> list[str]:
    g = QLinearGradient(0, 0, 0, h)
    g.setColorAt(0, QColor(14, 10, 40))
    g.setColorAt(1, QColor(64, 32, 92))
    p.fillRect(QRectF(0, 0, w, h), g)
    rnd = random.Random(3)
    p.setPen(Qt.PenStyle.NoPen)
    for _ in range(60):
        p.setBrush(QColor(255, 255, 255, rnd.randrange(80, 200)))
        p.drawEllipse(QPointF(rnd.uniform(0, w), rnd.uniform(0, h * 0.55)), 1.6, 1.6)
    dialog_box(p, QRectF(60, 480, w - 120, 180))
    text = JA_TYPEWRITER[:frame + 1]
    draw_text(p, 100, 585, text, font(JA_FONT, 34, QFont.Weight.DemiBold), QColor(255, 255, 255))
    return [text]


def render_ja_talk(page: int) -> Callable[[QPainter, int, int, int], list[str]]:
    """One page of JA_TALK, laid out like ja_dialog (name label 10 px above the dialogue box text)."""
    speaker, line = JA_TALK[page]

    def render(p: QPainter, w: int, h: int, _frame: int) -> list[str]:
        landscape(p, w, h)
        dialog_box(p, QRectF(60, 450, w - 120, 230))
        draw_text(p, 100, 505, speaker, font(JA_FONT, 30, QFont.Weight.Bold), QColor(255, 222, 130))
        draw_text(p, 100, 575, line, font(JA_FONT, 32, QFont.Weight.DemiBold), QColor(255, 255, 255))
        return [speaker, line]

    return render


SCENES = {
    "ja_dialog": Scene("ja_dialog", "ja", 1, render_ja_dialog),
    "en_subtitle": Scene("en_subtitle", "en", 1, render_en_subtitle),
    "ja_menu": Scene("ja_menu", "ja", 1, render_ja_menu),
    "en_paragraph": Scene("en_paragraph", "en", 1, render_en_paragraph),
    "ja_typewriter": Scene("ja_typewriter", "ja", len(JA_TYPEWRITER), render_ja_typewriter),
    **{f"ja_talk_{i + 1}": Scene(f"ja_talk_{i + 1}", "ja", 1, render_ja_talk(i)) for i in range(len(JA_TALK))},
}
SCENE_SETS = {
    "mixed": ["ja_dialog", "en_subtitle", "ja_menu", "en_paragraph", "ja_typewriter"],
    "ja": ["ja_dialog", "ja_menu", "ja_typewriter"],
    "en": ["en_subtitle", "en_paragraph"],
    "llm": [f"ja_talk_{i + 1}" for i in range(len(JA_TALK))],  # contextual tier: names and pronouns
}


# --- window --------------------------------------------------------------------------------

class DemoWindow(QWidget):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle(f"Screen Translator demo (tự đóng sau {args.duration:.0f} s)")
        self.args = args
        self.size_px = args.size
        self.names = SCENE_SETS[args.scenes]
        self.scene_idx = -1
        self.frame = 0
        self.cycle = 0
        self.image = QImage(self.size_px[0], self.size_px[1], QImage.Format.Format_RGB32)
        self.image.fill(QColor(0, 0, 0))
        self.shots_saved: set[str] = set()
        self.log = open(args.log, "w", encoding="utf-8") if args.log else None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.advance)
        left, top = args.pos
        self.setGeometry(physical_to_logical(Region(left, top, *self.size_px)))

    def write(self, record: dict) -> None:
        if self.log is not None:
            self.log.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.log.flush()

    def start(self) -> None:
        self.show()
        QApplication.processEvents()
        rect = client_rect(int(self.winId()))
        self.write({"rect": [rect.left, rect.top, rect.width, rect.height], "pid": QApplication.applicationPid(),
                    "ts": time.time(), "scenes": self.names, "interval": self.args.interval})
        self.advance()

    def advance(self) -> None:
        scene = SCENES[self.names[self.scene_idx]] if self.scene_idx >= 0 else None
        if scene is not None and self.frame + 1 < scene.frames:
            self.frame += 1
        else:
            if scene is not None:
                self.save_shot(scene)
            self.scene_idx = (self.scene_idx + 1) % len(self.names)
            if self.scene_idx == 0 and scene is not None:
                self.cycle += 1
            self.frame = 0
            scene = SCENES[self.names[self.scene_idx]]
        lines = self.render(scene)
        self.repaint()  # synchronous, so the logged timestamp is when the frame went to the screen
        final = self.frame + 1 >= scene.frames
        self.write({"ts": time.time(), "scene": scene.name, "lang": scene.lang, "lines": lines, "final": final,
                    "frame": self.frame, "cycle": self.cycle})
        if scene.frames > 1 and not final:
            self.timer.start(self.args.typewriter_ms)
        else:
            typing = (scene.frames - 1) * self.args.typewriter_ms / 1000
            self.timer.start(int(max(0.5, self.args.interval + (1.0 if scene.frames > 1 else 0) - typing) * 1000))

    def render(self, scene: Scene) -> list[str]:
        p = QPainter(self.image)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        lines = scene.render(p, self.size_px[0], self.size_px[1], self.frame)
        p.end()
        return lines

    def save_shot(self, scene: Scene) -> None:
        if self.args.shots and scene.name not in self.shots_saved:
            self.args.shots.mkdir(parents=True, exist_ok=True)
            self.image.save(str(self.args.shots / f"{scene.name}.png"))
            self.shots_saved.add(scene.name)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        img = QImage(self.image)
        img.setDevicePixelRatio(self.devicePixelRatioF())
        p.drawImage(0, 0, img)
        p.end()

    def closeEvent(self, e) -> None:
        if self.log is not None:
            self.log.close()
            self.log = None
        super().closeEvent(e)


def _pair(text: str) -> tuple[int, int]:
    a, b = (int(v) for v in text.split(","))
    return a, b


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, help="JSON-lines log (rect first, then one line per change)")
    ap.add_argument("--duration", type=float, default=30.0, help="quit after this many seconds")
    ap.add_argument("--scenes", choices=sorted(SCENE_SETS), default="mixed")
    ap.add_argument("--interval", type=float, default=4.0, help="seconds per scene (typewriter: +1 s)")
    ap.add_argument("--typewriter-ms", type=int, default=65, help="ms per typed character")
    ap.add_argument("--pos", type=_pair, default=(120, 120), help="client top-left, physical px")
    ap.add_argument("--size", type=_pair, default=(1280, 720), help="client size, physical px")
    ap.add_argument("--shots", type=Path, help="save the final frame of each scene here once")
    ap.add_argument("--allow-display-sleep", action="store_true",
                    help="do not keep the display on while running")
    args = ap.parse_args(argv)

    app = QApplication(sys.argv[:1])
    if not args.allow_display_sleep:
        keep_display_on(True)
    win = DemoWindow(args)
    win.start()
    QTimer.singleShot(int(args.duration * 1000), app.quit)
    rc = app.exec()
    win.close()
    keep_display_on(False)
    return rc


if __name__ == "__main__":
    sys.exit(main())
