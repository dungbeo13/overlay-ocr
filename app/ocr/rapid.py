"""RapidOCR 3.10 (PP-OCRv6 det/rec on ONNX Runtime) as an OcrEngine.

PP-OCRv6 det/rec models are multilingual: one model file per size, routed by rapidocr's
default_models.yaml. The "tiny" models do not cover Japanese.
"""
from __future__ import annotations

import os
import threading
from typing import Literal

import cv2
import numpy as np

from .base import OcrEngine, TextBox
from .textnorm import quad_to_xywh, sort_top_to_bottom

Lang = Literal["en", "ja"]
DetSize = Literal["tiny", "small", "medium"]
RecSize = Literal["tiny", "small", "medium"]
Device = Literal["cpu", "cuda"]

_RAPID_LANG = {"en": "en", "ja": "japan"}
_SIZES = ("tiny", "small", "medium")
CUDA_EP = "CUDAExecutionProvider"

_dll_lock = threading.Lock()
_dlls_loaded = False


def preload_cuda_dlls() -> None:
    """Load CUDA/cuDNN DLLs from the nvidia-* pip wheels once per process."""
    global _dlls_loaded
    with _dll_lock:
        if not _dlls_loaded:
            import onnxruntime

            onnxruntime.preload_dlls()
            _dlls_loaded = True


def supported_langs(task: Literal["det", "rec"], size: str) -> list[str]:
    """Languages rapidocr routes for a PP-OCRv6 det/rec model size (rapidocr lang codes)."""
    from rapidocr.utils.model_resolver import list_supported_langs
    from rapidocr.utils.typings import ModelType, OCRVersion, TaskType

    return list_supported_langs(TaskType(task), OCRVersion.PPOCRV6, ModelType(size))


def check_config(lang: str, det: str, rec: str, device: str = "cpu") -> None:
    """Raise ValueError if this lang/det/rec/device combination is not available."""
    if lang not in _RAPID_LANG:
        raise ValueError(f"lang must be one of {sorted(_RAPID_LANG)}, got {lang!r}")
    if device not in ("cpu", "cuda"):
        raise ValueError(f"device must be 'cpu' or 'cuda', got {device!r}")
    for task, size in (("det", det), ("rec", rec)):
        if size not in _SIZES:
            raise ValueError(f"{task} must be one of {_SIZES}, got {size!r}")
        if _RAPID_LANG[lang] not in supported_langs(task, size):
            ok = [s for s in _SIZES if _RAPID_LANG[lang] in supported_langs(task, s)]
            raise ValueError(
                f"PP-OCRv6 {task} '{size}' model does not support lang={lang!r} "
                f"(rapidocr routes {lang!r} only to {task} sizes {ok})"
            )


class RapidOcrEngine(OcrEngine):
    """PP-OCRv6 detection + recognition via rapidocr/onnxruntime.

    After construction `providers` holds each ONNX session's active execution providers,
    e.g. {"det": ["CUDAExecutionProvider", "CPUExecutionProvider"], "rec": [...]}.
    With device="cuda" construction fails unless CUDA EP is really the primary provider.

    allow_spinning=False (default) disables ORT's busy-wait thread pools: measured on the
    i7-13700KF it was both faster (263 vs 360 ms, 1280x720 dialog, 8 threads) and kept ~6
    instead of ~16 logical cores busy, because det/rec pools spun against each other.
    """

    def __init__(
        self,
        lang: Lang = "en",
        det: DetSize = "small",
        rec: RecSize = "small",
        device: Device = "cpu",
        threads: int | None = None,
        *,
        use_cls: bool = False,
        text_score: float = 0.5,
        allow_spinning: bool = False,
        cudnn_algo: Literal["EXHAUSTIVE", "HEURISTIC", "DEFAULT"] = "HEURISTIC",
        det_limit_side_len: int | None = None,
        det_limit_type: Literal["min", "max"] | None = None,
    ) -> None:
        check_config(lang, det, rec, device)
        ncpu = os.cpu_count() or 1
        if threads is not None and not 1 <= threads <= ncpu:
            # rapidocr silently drops values outside 1..cpu_count (ORT default threads instead)
            raise ValueError(f"threads must be in 1..{ncpu} or None, got {threads}")
        if device == "cuda":
            preload_cuda_dlls()

        from rapidocr import EngineType, LangRec, ModelType, OCRVersion, RapidOCR

        self.lang, self.det, self.rec, self.device, self.threads = lang, det, rec, device, threads
        rlang = _RAPID_LANG[lang]
        params: dict = {
            "Global.log_level": "warning",
            "Global.use_cls": use_cls,  # 0/180 deg classifier; screen text is upright
            "Global.text_score": text_score,
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.ocr_version": OCRVersion.PPOCRV6,
            "Det.model_type": ModelType(det),
            "Det.lang_type": rlang,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.ocr_version": OCRVersion.PPOCRV6,
            "Rec.model_type": ModelType(rec),
            "Rec.lang_type": LangRec(rlang),
            "Cls.engine_type": EngineType.ONNXRUNTIME,
            "EngineConfig.onnxruntime.use_cuda": device == "cuda",
            "EngineConfig.onnxruntime.intra_op_num_threads": threads or -1,
            # rapidocr defaults to EXHAUSTIVE, which re-benchmarks convs for every new input
            # shape (rec widths vary per batch) and causes multi-100 ms spikes.
            "EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_algo_search": cudnn_algo,
        }
        if det_limit_side_len is not None:
            params["Det.limit_side_len"] = det_limit_side_len
        if det_limit_type is not None:
            params["Det.limit_type"] = det_limit_type

        self._ocr = RapidOCR(params=params)
        # Build sessions now (downloads models on first use) rather than on the first frame.
        text_det = self._ocr._load_det_model()
        _install_fast_det_preprocess(text_det)
        wrappers = {"det": text_det.session, "rec": self._ocr._load_rec_model().session}
        if use_cls:
            wrappers["cls"] = self._ocr._load_cls_model().session
        if not allow_spinning:
            for w in wrappers.values():
                w.session = _without_spinning(w.session)

        self.providers: dict[str, list[str]] = {k: list(w.session.get_providers()) for k, w in wrappers.items()}
        if device == "cuda":
            bad = {k: p for k, p in self.providers.items() if not p or p[0] != CUDA_EP}
            if bad:
                raise RuntimeError(f"CUDA requested but sessions are not running on {CUDA_EP}: {bad}")
        self.name = f"rapid-{det}-{rec}-{device}" + (f"-t{threads}" if threads else "") + ("-spin" if allow_spinning else "")

    @property
    def uses_cuda(self) -> bool:
        return all(p and p[0] == CUDA_EP for p in self.providers.values())

    def warmup(self, runs: int = 3) -> None:
        # A blank image never reaches the recogniser (and makes rapidocr log a warning),
        # so warm up on a line of real text.
        img = np.full((96, 480, 3), 255, dtype=np.uint8)
        cv2.putText(img, "Warm up 123", (12, 64), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 3)
        for _ in range(runs):
            self.recognize(img)

    def recognize(self, image_bgr: np.ndarray) -> list[TextBox]:
        res = self._ocr(image_bgr)
        quads = getattr(res, "boxes", None)
        txts = getattr(res, "txts", None)
        if quads is None or txts is None or len(txts) == 0:
            return []
        scores = getattr(res, "scores", None) or [None] * len(txts)
        h, w = image_bgr.shape[:2]
        out = [
            TextBox(text=t, box=quad_to_xywh(q, w, h), score=None if s is None else float(s))
            for q, t, s in zip(quads, txts, scores)
            if t and t.strip()
        ]
        return sort_top_to_bottom(out)


def _install_fast_det_preprocess(text_det) -> None:
    """Swap rapidocr's numpy det normalisation for one fused OpenCV call.

    rapidocr normalises with float64 numpy ops (~50 ms for a 3328x736 det input, more than
    the CUDA det itself). (x/255 - 0.5)/0.5 == (x - 127.5)/127.5, which
    cv2.dnn.blobFromImage computes and lays out as NCHW float32 in one pass.
    """
    from rapidocr.ch_ppocr_det.utils import DetPreProcess

    orig = text_det.get_preprocess()
    if not (np.allclose(orig.mean, 0.5) and np.allclose(orig.std, 0.5)):
        return  # custom normalisation configured; keep rapidocr's implementation

    class _FastDetPreProcess(DetPreProcess):
        def __call__(self, img: np.ndarray) -> np.ndarray | None:
            resized = self.resize(img)
            if resized is None:
                return None
            return cv2.dnn.blobFromImage(resized, 1 / 127.5, None, (127.5, 127.5, 127.5), False, False)

    fast = _FastDetPreProcess(orig.limit_side_len, orig.limit_type, list(orig.mean), list(orig.std))
    text_det.get_preprocess = lambda: fast  # TextDetector calls this on every frame


def _without_spinning(sess):
    """Recreate an onnxruntime session with intra-op thread spin-waiting disabled.

    Spinning keeps pool threads busy between ops/frames; turning it off trades a little
    latency for much less CPU stolen from a game running alongside.
    """
    import onnxruntime as ort

    opts = sess.get_session_options()
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return ort.InferenceSession(
        sess._model_path,
        sess_options=opts,
        providers=sess.get_providers(),
        provider_options=[sess.get_provider_options()[p] for p in sess.get_providers()],
    )
