"""User settings, stored as JSON in the data directory (default <project>/data)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SCREEN_TRANSLATOR_HOME", PROJECT_ROOT / "data"))
CONFIG_PATH = DATA_DIR / "config.json"
CACHE_PATH = DATA_DIR / "cache.sqlite3"
LOG_DIR = DATA_DIR / "logs"
PROFILES_DIR = DATA_DIR / "profiles"  # one glossary profile per JSON file

# Local LLM runtime and models live outside the project on a short path (large files, and the
# CUDA DLLs fail to load past MAX_PATH).
LLM_HOME = Path(os.environ.get("SCREEN_TRANSLATOR_LLM_HOME", Path.home() / ".screen-translator"))
LLAMA_DIR = LLM_HOME / "llama.cpp"  # llama-server.exe and its DLLs
MODELS_DIR = LLM_HOME / "models"  # *.gguf

# Games that ship anti-cheat (Vanguard / EAC / BattlEye / VAC / ...). While one of them
# owns the foreground window the app pauses capture and hides the overlay. Case-insensitive exe
# names; users add their own games in config.json.
DEFAULT_BLOCKED_PROCESSES = [
    "VALORANT-Win64-Shipping.exe",
    "FortniteClient-Win64-Shipping.exe",
    "r5apex.exe",
    "r5apex_dx12.exe",
    "cs2.exe",
    "dota2.exe",
    "GenshinImpact.exe",
    "StarRail.exe",
    "ZenlessZoneZero.exe",
    "League of Legends.exe",
    "PUBG-Win64-Shipping.exe",
    "TslGame.exe",
    "RainbowSix.exe",
    "EscapeFromTarkov.exe",
    "DeadByDaylight-Win64-Shipping.exe",
]


@dataclass
class Hotkeys:
    """Global hotkeys, "ctrl+alt+<key>" style (modifiers: ctrl, alt, shift, win)."""

    select_region: str = "ctrl+alt+r"
    toggle_pause: str = "ctrl+alt+t"
    toggle_overlay: str = "ctrl+alt+h"  # hide translations to see the original text
    switch_lang: str = "ctrl+alt+l"
    quit: str = "ctrl+alt+q"


@dataclass
class Config:
    src_lang: str = "ja"  # "en" | "ja"
    tgt_lang: str = "vi"
    region: list[int] | None = None  # last region [left, top, width, height], physical px

    # Pipeline
    capture_backend: str = "auto"  # "auto" (dxcam, GDI/mss while dxcam delivers nothing) | "dxcam" | "mss" | "wgc"
    poll_ms: int = 150
    ocr_device: str = "cuda"  # "cuda" | "cpu"
    ocr_det: str = "small"
    ocr_rec: str = "small"
    ocr_threads: int | None = None  # CPU only; None = engine default
    translator: str = "gtx"
    max_inflight_translations: int = 4

    # Contextual LLM tier (local llama.cpp server on 127.0.0.1)
    llm_mode: str = "refine"  # "off" | "refine" (Google first, LLM replaces it) | "only" (wait for the LLM)
    llm_model: str = ""  # GGUF file name in MODELS_DIR; "" = the recommended default
    llm_ctx: int = 4096
    llm_gpu_layers: int = 999  # offload everything that fits
    llm_history_lines: int = 12  # recent dialogue lines sent as context
    llm_auto_terms: bool = True  # store names/terms the LLM proposes in the active profile
    llm_release_after_guard_s: int = 30  # stop the server (free VRAM) after this long behind an anti-cheat game
    profile: str = "default"  # active glossary profile (auto-switched by a profile's `processes`)

    # Overlay
    font_family: str = "Segoe UI"
    min_font_px: int = 12
    box_alpha: int = 235  # 0-255 opacity of the box that hides the original text
    show_border: bool = True

    # Anti-cheat guard: "pause" (stop capture + hide overlay), "warn" (status only) or "off"
    guard_mode: str = "pause"
    blocked_processes: list[str] = field(default_factory=lambda: list(DEFAULT_BLOCKED_PROCESSES))

    hotkeys: Hotkeys = field(default_factory=Hotkeys)

    # Game lookup (app.lookup): community data for a new game's glossary profile, asked once per game.
    # Only the game's title is sent, and only after the user agrees for that game.
    lookup_ask_new_games: bool = True
    lookup_sources: list[str] = field(default_factory=lambda: ["steam", "wikidata", "vndb", "wiki", "web"])
    searxng_url: str = ""  # a SearXNG to use for web hits; filled in when the app runs its own on 127.0.0.1
    searxng_managed: bool = True  # start / stop the SearXNG installed under LLM_HOME/searxng when needed
    # Off by default: a public instance sees the game title and the user's IP (and none answers JSON today).
    searxng_public_fallback: bool = False  # with no SearXNG set up, try the tested public instances

    # UI
    first_run_done: bool = False  # the first-run guide was finished or skipped (tray "Hướng dẫn nhanh…" reopens it)

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        """Read `path`, ignoring unknown keys and falling back to defaults for missing ones."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in raw.items() if k in known}
        hk = data.pop("hotkeys", None)
        cfg = cls(**data)
        if isinstance(hk, dict):
            hk_known = {f.name for f in fields(Hotkeys)}
            cfg.hotkeys = Hotkeys(**{k: v for k, v in hk.items() if k in hk_known})
        return cfg

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
