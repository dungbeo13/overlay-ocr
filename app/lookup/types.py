"""Data types of the game lookup: what is known about a game locally, what each source found, and the
report the service hands to the UI. Plain dataclasses (no Qt); LookupReport.to_dict() is JSON-safe."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Source names, in the order the settings list them.
SOURCES = ("steam", "wikidata", "vndb", "wiki", "web")
SOURCE_TITLES = {"steam": "Steam", "wikidata": "Wikidata", "vndb": "VNDB", "wiki": "Wiki game (MediaWiki/Fandom)",
                 "web": "Tìm kiếm web (SearXNG)", "template": "Hồ sơ mẫu có sẵn", "files": "Tệp của game"}

# SourceStatus.state values
RUNNING, OK, EMPTY, ERROR, TIMEOUT, SKIPPED, CANCELLED = "running", "ok", "empty", "error", "timeout", "skipped", "cancelled"


@dataclass
class GameIdentity:
    """A game as identified from files on disk only (never from the process).

    `titles` are the names that may be sent online after the user agrees (never paths, user or machine
    names); everything else stays local."""

    exe_name: str = ""  # basename, e.g. "STEINS;GATE.exe"
    exe_path: str = ""  # local only, never sent
    titles: list[str] = field(default_factory=list)  # best first
    product_name: str = ""  # version resource
    file_description: str = ""
    company: str = ""
    folder_name: str = ""
    steam_appid: int | None = None  # from steamapps/appmanifest_*.acf
    steam_name: str = ""
    engine: str = ""  # "Unity", "Ren'Py", "RPG Maker MV/MZ", ... from marker files
    generic_exe: bool = False  # Game.exe, launcher.exe...: the exe name alone does not identify the game
    in_steam_library: bool = False
    hints: list[str] = field(default_factory=list)  # why it looks like a game (diagnostics)

    @property
    def key(self) -> str:
        """Stable per-game key for the "asked once" state: the exe name (with its folder when the exe name
        is generic), else the Steam app id, else the first title."""
        if self.exe_name:
            exe = self.exe_name.casefold()
            return f"exe:{self.folder_name.casefold()}/{exe}" if self.generic_exe and self.folder_name else f"exe:{exe}"
        if self.steam_appid:
            return f"steam:{self.steam_appid}"
        return f"title:{(self.titles[0] if self.titles else '').casefold()}"

    @property
    def title(self) -> str:
        return self.titles[0] if self.titles else ""

    def query_titles(self, limit: int = 2) -> list[str]:
        """The titles a lookup sends (the only data that leaves the machine)."""
        return self.titles[:limit]

    @property
    def looks_like_game(self) -> bool:
        return bool(self.steam_appid or self.engine or self.in_steam_library or self.hints)


@dataclass
class GameCandidate:
    """One possible match for the game, from one source."""

    source: str
    title: str
    score: float = 0.0  # title similarity 0..1 (plus cross-source agreement)
    ids: dict[str, str] = field(default_factory=dict)  # {"steam": "412830", "vndb": "v2002", ...}
    description: str = ""  # "2009 visual novel game", release year...
    title_ja: str = ""


@dataclass
class CharacterCandidate:
    """A character as one or more sources describe it (merged by the service)."""

    original: str  # as written in the game's own text (牧瀬紅莉栖; "Sayori" for an English game)
    romanized: str = ""  # "Makise Kurisu" (family name first for Japanese names, as in the source text)
    parts: list[tuple[str, str]] = field(default_factory=list)  # [(牧瀬, Makise), (紅莉栖, Kurisu)]
    aliases: list[tuple[str, str]] = field(default_factory=list)  # [(original, romanized)]
    gender: str = ""  # "nam" | "nữ" | ""
    role: str = ""  # "main" | "primary" | "side" | "appears" | ""
    label_vi: str = ""  # a Vietnamese label from Wikidata, when there is one
    sources: list[str] = field(default_factory=list)
    ids: dict[str, str] = field(default_factory=dict)  # {"vndb": "c6487", "wikidata": "Q3817446", "wiki": "Kurisu Makise"}
    native: bool = False  # `original` is the Japanese text's own spelling even when it is Latin ("2B", "9S")


@dataclass
class TermCandidate:
    source: str  # as written in the game's text
    target: str  # rendering (romanized / official English / Vietnamese label)
    kind: str = "term"  # "place" | "term" | "item" | "skill" | "person"
    note: str = ""
    sources: list[str] = field(default_factory=list)
    auto: bool = False  # lower confidence (aliases): stored as a suggestion the user confirms


@dataclass
class PatchLink:
    """A page about a Vietnamese version: only shown, opened in the browser when the user clicks."""

    title: str
    url: str  # the patch's own page when known (group site, forum thread), else info_url
    source: str  # "vndb" | "web" | "steam"
    note: str = ""
    official: bool = False
    verified: bool = True  # False for web search hits (not checked by anyone)
    info_url: str = ""  # the database page describing it (VNDB release page)


@dataclass
class SourceStatus:
    name: str
    state: str = RUNNING
    message: str = ""
    elapsed_s: float = 0.0
    requests: int = 0
    cached: int = 0
    found: dict[str, int] = field(default_factory=dict)  # {"characters": 12, "terms": 3, "patches": 1}


@dataclass
class LookupReport:
    query: str  # the title the lookup started from
    identity: GameIdentity | None = None
    title: str = ""  # the chosen game's display title (English / romanized)
    title_ja: str = ""
    title_vi: str = ""
    src_lang: str | None = None  # "ja" | "en" | other ISO code | None (unknown)
    ids: dict[str, str] = field(default_factory=dict)
    vi_official: bool | None = None  # Steam / VNDB list an official Vietnamese version
    vi_note: str = ""
    characters: list[CharacterCandidate] = field(default_factory=list)
    terms: list[TermCandidate] = field(default_factory=list)
    patches: list[PatchLink] = field(default_factory=list)
    candidates: list[GameCandidate] = field(default_factory=list)  # the chosen ones, one per source
    alternatives: list[GameCandidate] = field(default_factory=list)  # close matches the user may mean instead
    confidence: str = ""  # "high" | "medium" | "low" | "" (nothing found)
    statuses: list[SourceStatus] = field(default_factory=list)
    template: str = ""  # path of the local template profile that matched (no network then)
    attribution: list[dict[str, str]] = field(default_factory=list)  # {"source", "url", "license"}
    notes: list[str] = field(default_factory=list)  # human-readable remarks for the UI (Vietnamese)
    elapsed_s: float = 0.0
    cancelled: bool = False

    @property
    def found_anything(self) -> bool:
        return bool(self.template or self.characters or self.terms or self.patches or self.title)

    @property
    def unsure(self) -> bool:
        return self.confidence in ("low", "medium") and bool(self.alternatives)

    def status(self, name: str) -> SourceStatus | None:
        return next((s for s in self.statuses if s.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.identity is not None:
            d["identity"]["exe_path"] = ""  # never leaves the machine through a report either
        return d
