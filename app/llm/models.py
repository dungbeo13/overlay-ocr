"""Which GGUF model to load and where llama-server.exe is.

Models live in config.MODELS_DIR (installed by tools/setup_llm.py). The recommended files come from
bench/results/llm_setup.md: "light" (Hy-MT2 1.8B, +2.0 GB VRAM, ~0.4 s per screen) and "quality"
(Hy-MT2 7B, +5.3 GB VRAM, ~1 s per screen, follows character notes for pronouns much better).

config.llm_model "" (or "auto") picks "quality" when the GPU has AUTO_QUALITY_FREE_MB free right
before the load, else "light": the 1.8B gets meanings and names wrong where Google is right
(bench/eval_llm.py, the LLM reviews), so it is only the fallback for a GPU that is busy (a game).
"""
from __future__ import annotations

import ctypes
import logging
import re
import shutil
from pathlib import Path

from app.config import LLAMA_DIR, MODELS_DIR

log = logging.getLogger(__name__)

__all__ = ["RECOMMENDED", "DEFAULT_KIND", "AUTO", "list_models", "recommended_model", "resolve_model",
           "find_llama_server", "free_vram_mb", "auto_kind", "model_kind", "model_label"]

# kind -> file names in MODELS_DIR, best first (keep in sync with tools/setup_llm.py MODELS).
RECOMMENDED: dict[str, tuple[str, ...]] = {
    "light": ("Hy-MT2-1.8B-Q6_K.gguf",),
    "quality": ("Hy-MT2-7B-Q4_K_M.gguf",),
}
DEFAULT_KIND = "light"  # the model when "auto" cannot pick the quality one: light enough next to a game
AUTO = "auto"
AUTO_QUALITY_FREE_MB = 7500  # the 7B takes ~5.3 GB; keep ~2 GB for OCR and whatever else runs
KIND_LABELS = {"light": "Nhẹ 1.8B", "quality": "Chất lượng 7B"}
_SHARD = re.compile(r"-(\d{5})-of-\d{5}\.gguf$", re.IGNORECASE)


class _NvmlMemory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


def free_vram_mb(index: int = 0) -> int | None:
    """Free memory of GPU `index` in MiB via NVML (nvml.dll from the NVIDIA driver: a device-wide
    figure, no process is looked at), None when it cannot be read."""
    try:
        nvml = ctypes.CDLL("nvml.dll")
    except OSError:
        return None
    nvml.nvmlInit_v2.argtypes = []
    nvml.nvmlInit_v2.restype = ctypes.c_int
    nvml.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
    nvml.nvmlDeviceGetHandleByIndex_v2.restype = ctypes.c_int
    nvml.nvmlDeviceGetMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_NvmlMemory)]
    nvml.nvmlDeviceGetMemoryInfo.restype = ctypes.c_int
    nvml.nvmlShutdown.argtypes = []
    nvml.nvmlShutdown.restype = ctypes.c_int
    if nvml.nvmlInit_v2() != 0:
        return None
    try:
        handle = ctypes.c_void_p()
        mem = _NvmlMemory()
        if nvml.nvmlDeviceGetHandleByIndex_v2(index, ctypes.byref(handle)) != 0 \
                or nvml.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem)) != 0:
            return None
        return int(mem.free // (1024 * 1024))
    finally:
        nvml.nvmlShutdown()


def model_kind(name: str) -> str | None:
    """"light" / "quality" for a recommended model's file name or stem, else None."""
    stem = Path(str(name)).name.lower().removesuffix(".gguf")
    for kind, files in RECOMMENDED.items():
        if any(f.lower().removesuffix(".gguf") == stem for f in files):
            return kind
    return None


def model_label(name: str) -> str:
    """Name for the UI: "Chất lượng 7B" for a recommended model, else the file stem."""
    kind = model_kind(name)
    return KIND_LABELS[kind] if kind else Path(str(name)).name.removesuffix(".gguf")


def list_models() -> list[Path]:
    """GGUF models in MODELS_DIR, by name. Vision projectors (mmproj-*) and the 2nd+ shards of a
    split model are left out (llama-server loads a split model from its first shard)."""
    try:
        files = [p for p in MODELS_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".gguf"]
    except OSError:
        return []
    out = []
    for p in files:
        name = p.name.lower()
        shard = _SHARD.search(name)
        if name.startswith("mmproj") or (shard and int(shard.group(1)) != 1):
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.name.lower())


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def recommended_model(kind: str = "light") -> Path | None:
    """The installed model for `kind` ("light" | "quality"): its recommended file, else the other
    kind's, else whatever is installed (the smallest for light, the largest for quality)."""
    if kind not in RECOMMENDED:
        raise ValueError(f"unknown model kind {kind!r} (expected one of {', '.join(RECOMMENDED)})")
    installed = list_models()
    by_name = {p.name.lower(): p for p in installed}
    for k in (kind, *(k for k in RECOMMENDED if k != kind)):
        for name in RECOMMENDED[k]:
            if name.lower() in by_name:
                return by_name[name.lower()]
    if not installed:
        return None
    pick = min if kind == "light" else max
    return pick(installed, key=_size)


def auto_kind() -> str:
    """"quality" when the GPU has AUTO_QUALITY_FREE_MB free now, else DEFAULT_KIND."""
    free = free_vram_mb()
    kind = "quality" if free is not None and free >= AUTO_QUALITY_FREE_MB else DEFAULT_KIND
    log.info("LLM model auto: %s (%s MiB VRAM free, %d needed for the quality model)", kind,
             "?" if free is None else free, AUTO_QUALITY_FREE_MB)
    return kind


def resolve_model(config) -> Path | None:
    """config.llm_model: a file name in MODELS_DIR (or an absolute path, or "light" / "quality");
    "" / "auto" = auto_kind()'s recommended model; a missing file = DEFAULT_KIND's (with a warning).
    None when no model is installed."""
    name = str(getattr(config, "llm_model", "") or "").strip()
    if not name or name.lower() == AUTO:
        return recommended_model(auto_kind())
    if name.lower() in RECOMMENDED:
        return recommended_model(name.lower())
    p = Path(name)
    for cand in ((p,) if p.is_absolute() else (MODELS_DIR / p, MODELS_DIR / f"{name}.gguf")):
        if cand.is_file():
            return cand
    log.warning("llm_model %r is not in %s; using the recommended %s model", name, MODELS_DIR, DEFAULT_KIND)
    return recommended_model(DEFAULT_KIND)


def find_llama_server() -> Path | None:
    """LLAMA_DIR/llama-server.exe (installed by tools/setup_llm.py), else one on PATH."""
    exe = LLAMA_DIR / "llama-server.exe"
    if exe.is_file():
        return exe
    found = shutil.which("llama-server")
    return Path(found) if found else None
