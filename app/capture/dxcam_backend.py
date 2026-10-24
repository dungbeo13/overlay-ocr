"""DXGI Desktop Duplication capture through dxcam (copies only the requested region on the GPU).

Desktop Duplication only hands out a frame when the desktop changed since the last
AcquireNextFrame, so dxcam's one-shot grab() returns None on a static screen. This backend keeps,
per output, the pixels of the most recent frame for the union of recently requested regions and
serves crops of it, so callers always get the current pixels (with the timestamp of when they were
captured). A region that is not covered by the cache on a static screen forces a fresh duplication,
whose first frame is always the full current desktop (~0.1-0.2 s, happens once per new region).

Use one DxcamCapture per process: dxcam keeps a singleton camera per output.
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.winutil import enable_per_monitor_dpi_awareness

from . import enum_monitors, intersect
from .base import CaptureBackend, Frame, Region

log = logging.getLogger(__name__)

_Rect = tuple[int, int, int, int]  # output-relative (left, top, right, bottom), dxcam's format


def _union(rects) -> _Rect:
    rects = list(rects)
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


def _contains(outer: _Rect | None, inner: _Rect) -> bool:
    return outer is not None and outer[0] <= inner[0] and outer[1] <= inner[1] \
        and outer[2] >= inner[2] and outer[3] >= inner[3]


@dataclass
class _Output:
    device_idx: int
    output_idx: int | None  # None = let dxcam pick the primary output
    bounds: Region
    camera: Any = None
    roi: _Rect | None = None  # area covered by `image`
    image: np.ndarray | None = None  # BGR, read-only, pixels of the latest acquired frame
    timestamp: float = 0.0
    recent: collections.deque = field(default_factory=lambda: collections.deque(maxlen=8))
    last_refresh: float = -1e9


class DxcamCapture(CaptureBackend):
    """Desktop Duplication capture of any region; multi-monitor aware.

    A region that spans several monitors is clamped to the monitor it overlaps most;
    Frame.region always describes the pixels actually returned. When the returned area equals
    the cached area the image is shared with the cache and marked read-only (copy before
    drawing on it).
    """

    name = "dxcam"

    def __init__(self, recent_regions: int = 8, min_refresh_interval_s: float = 1.0) -> None:
        enable_per_monitor_dpi_awareness()
        import dxcam  # sets PMv1 at import time unless PMv2 is already active

        self._dxcam = dxcam
        self._min_refresh_interval = min_refresh_interval_s
        self._outputs = self._discover_outputs(recent_regions)
        self.refresh_count = 0  # forced duplication restarts (diagnostics)
        self._lock = threading.Lock()  # grab()/close() are serialized, so any thread may call them
        self._closed = False

    def _discover_outputs(self, recent_regions: int) -> list[_Output]:
        outputs: list[_Output] = []
        # dxcam has no public API for output desktop rects; its factory (module-private) has them.
        factory = vars(self._dxcam).get("__factory")
        try:
            for d, outs in enumerate(factory.outputs):
                for o, out in enumerate(outs):
                    out.update_desc()
                    if not out.attached_to_desktop:
                        continue
                    rc = out.desc.DesktopCoordinates
                    outputs.append(_Output(d, o, Region(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)))
        except Exception:  # factory layout changed: fall back to the primary output only
            log.warning("dxcam output discovery failed; using the primary monitor only", exc_info=True)
            mon = next(m for m in enum_monitors() if m.primary)
            outputs = [_Output(0, None, mon.bounds)]
        for out in outputs:
            out.recent = collections.deque(maxlen=recent_regions)
        if not outputs:
            raise RuntimeError("dxcam found no desktop outputs")
        return outputs

    @property
    def output_bounds(self) -> list[Region]:
        return [o.bounds for o in self._outputs]

    def _pick(self, region: Region) -> tuple[_Output, Region] | tuple[None, None]:
        best, best_rect, best_area = None, None, 0
        for out in self._outputs:
            rect = intersect(region, out.bounds)
            if rect is not None and rect.width * rect.height > best_area:
                best, best_rect, best_area = out, rect, rect.width * rect.height
        return best, best_rect

    def _camera(self, out: _Output) -> Any:
        if out.camera is None or out.camera.is_released:
            out.camera = self._dxcam.create(
                device_idx=out.device_idx, output_idx=out.output_idx,
                output_color="BGR", processor_backend="cv2",
            )
        return out.camera

    def _acquire(self, out: _Output, rect: _Rect) -> np.ndarray | None:
        try:
            return self._camera(out).grab(region=rect, copy=True, new_frame_only=True)
        except Exception:
            log.warning("dxcam grab failed; restarting duplication", exc_info=True)
            self._release_camera(out)
            return None

    def _refresh(self, out: _Output, rect: _Rect) -> np.ndarray | None:
        """Restart the duplication: its first frame is the full current desktop."""
        now = time.perf_counter()
        if now - out.last_refresh < self._min_refresh_interval:
            return None
        out.last_refresh = now
        self.refresh_count += 1
        self._release_camera(out)
        for _ in range(15):
            img = self._acquire(out, rect)
            if img is not None or out.camera is None:  # got it, or the duplication failed
                return img
            time.sleep(0.01)
        return None

    def _release_camera(self, out: _Output) -> None:
        if out.camera is not None:
            try:
                out.camera.release()
            except Exception:
                log.debug("dxcam release failed", exc_info=True)
            out.camera = None

    def grab(self, region: Region) -> Frame | None:
        with self._lock:
            return self._grab(region)

    def _grab(self, region: Region) -> Frame | None:
        if self._closed:
            raise RuntimeError("DxcamCapture is closed")
        out, rect = self._pick(region)
        if out is None:
            return None
        ob = out.bounds
        rel: _Rect = (rect.left - ob.left, rect.top - ob.top, rect.right - ob.left, rect.bottom - ob.top)
        out.recent.append(rel)
        want = _union(out.recent)  # copy everything recently asked for, so alternating regions stay current

        img = self._acquire(out, want)
        if img is None and not _contains(out.roi, rel):
            img = self._refresh(out, want)
        if img is not None:
            img.setflags(write=False)
            out.roi, out.image, out.timestamp = want, img, time.perf_counter()
        if out.image is None or not _contains(out.roi, rel):
            return None

        if rel == out.roi:
            pixels = out.image
        else:
            x0, y0 = rel[0] - out.roi[0], rel[1] - out.roi[1]
            pixels = np.ascontiguousarray(out.image[y0:y0 + rect.height, x0:x0 + rect.width])
        return Frame(pixels, out.timestamp, rect)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for out in self._outputs:
                self._release_camera(out)
                out.image = None
