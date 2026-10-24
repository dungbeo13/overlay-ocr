"""Windows.Media.Ocr (built-in Windows OCR) as an OcrEngine, via the winrt packages.

WinRT blocking waits are forbidden on STA threads (e.g. the Qt GUI thread) and one
OcrEngine runs a single RecognizeAsync at a time, so every WinRT call is made on one
private MTA worker thread; recognize() just hands the frame over and waits.
"""
from __future__ import annotations

import ctypes
import threading
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

from .base import OcrEngine, TextBox
from .textnorm import sort_top_to_bottom, strip_cjk_spaces, union_rects

INSTALL_HINT = (
    "Install the Windows OCR language pack for {tag}:\n"
    "  Settings > Time & language > Language & region > Add a language > {name}\n"
    "  (make sure the optional 'Optical character recognition' feature is included), or in an\n"
    "  elevated PowerShell:\n"
    "    Add-WindowsCapability -Online -Name 'Language.OCR~~~{cap}~0.0.1.0'\n"
    "  List what is installed: Get-WindowsCapability -Online | Where-Object Name -like 'Language.OCR*'"
)
_LANG_NAMES = {"ja": "Japanese", "en": "English"}


class OcrLanguageMissing(RuntimeError):
    """The requested Windows OCR language pack is not installed."""


def _com_init_mta() -> None:
    # COINIT_MULTITHREADED; S_FALSE/RPC_E_CHANGED_MODE are fine (already initialised).
    ctypes.windll.ole32.CoInitializeEx(None, 0x0)


_lang_executor: ThreadPoolExecutor | None = None
_lang_lock = threading.Lock()


def _shared_executor() -> ThreadPoolExecutor:
    global _lang_executor
    with _lang_lock:
        if _lang_executor is None:
            _lang_executor = ThreadPoolExecutor(1, "winocr-query", initializer=_com_init_mta)
        return _lang_executor


def available_languages() -> list[str]:
    """BCP-47 tags of installed OCR recognizer languages, e.g. ['en-US', 'ja']."""

    def query() -> list[str]:
        from winrt.windows.media.ocr import OcrEngine as WinOcrEngine

        return [lang.language_tag for lang in WinOcrEngine.available_recognizer_languages]

    return _shared_executor().submit(query).result()


def is_language_available(lang_tag: str) -> bool:
    def query() -> bool:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine as WinOcrEngine

        return bool(WinOcrEngine.is_language_supported(Language(lang_tag)))

    return _shared_executor().submit(query).result()


def install_hint(lang_tag: str) -> str:
    base = lang_tag.split("-")[0].lower()
    cap = lang_tag if "-" in lang_tag else {"ja": "ja-JP", "en": "en-US"}.get(base, lang_tag)
    return INSTALL_HINT.format(tag=lang_tag, name=_LANG_NAMES.get(base, lang_tag), cap=cap)


class WindowsOcrEngine(OcrEngine):
    """Built-in Windows OCR. Returns line boxes (union of word rects); score is always None."""

    def __init__(self, lang_tag: str = "en-US") -> None:
        self.lang_tag = lang_tag
        self.name = f"winocr-{lang_tag}"
        self._cjk = lang_tag.split("-")[0].lower() in ("ja", "zh", "ko")
        self._pool = ThreadPoolExecutor(1, f"winocr-{lang_tag}", initializer=_com_init_mta)
        try:
            self._pool.submit(self._create).result()
        except Exception:
            self._pool.shutdown(wait=False)
            raise

    def _create(self) -> None:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine as WinOcrEngine

        lang = Language(self.lang_tag)
        engine = WinOcrEngine.try_create_from_language(lang) if WinOcrEngine.is_language_supported(lang) else None
        if engine is None:
            installed = [x.language_tag for x in WinOcrEngine.available_recognizer_languages]
            hint = install_hint(self.lang_tag)
            print(hint)
            raise OcrLanguageMissing(
                f"Windows OCR language {self.lang_tag!r} is not installed (installed: {installed}).\n{hint}"
            )
        self._engine = engine
        self.max_dim = int(WinOcrEngine.max_image_dimension)
        self._SoftwareBitmap, self._fmt, self._alpha = SoftwareBitmap, BitmapPixelFormat.BGRA8, BitmapAlphaMode.IGNORE

    def _run(self, bgra: np.ndarray) -> list[TextBox]:
        h, w = bgra.shape[:2]
        # numpy arrays satisfy the buffer protocol that winrt accepts for IBuffer: one memcpy.
        bmp = self._SoftwareBitmap.create_copy_with_alpha_from_buffer(bgra, self._fmt, w, h, self._alpha)
        try:
            result = self._engine.recognize_async(bmp).get()
        finally:
            bmp.close()
        out: list[TextBox] = []
        for line in result.lines:
            words = list(line.words)
            if not words:
                continue
            rects = [(r.x, r.y, r.width, r.height) for r in (wd.bounding_rect for wd in words)]
            text = line.text
            if self._cjk:
                text = strip_cjk_spaces(text)
            if text.strip():
                out.append(TextBox(text=text, box=union_rects(rects), score=None))
        return out

    def recognize(self, image_bgr: np.ndarray) -> list[TextBox]:
        h, w = image_bgr.shape[:2]
        if h < 1 or w < 1:
            return []
        scale = 1.0
        if max(h, w) > self.max_dim:  # Windows OCR rejects images above MaxImageDimension
            scale = self.max_dim / max(h, w)
            image_bgr = cv2.resize(image_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        bgra = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2BGRA)
        boxes = self._pool.submit(self._run, bgra).result()
        if scale != 1.0:
            inv = 1 / scale
            boxes = [TextBox(b.text, tuple(int(round(v * inv)) for v in b.box), b.score) for b in boxes]
        return sort_top_to_bottom(boxes)

    def close(self) -> None:
        self._pool.shutdown(wait=True)

    def __del__(self) -> None:
        try:
            self._pool.shutdown(wait=False)
        except Exception:
            pass
