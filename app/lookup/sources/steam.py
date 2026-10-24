"""Steam store API (key-free): storesearch to find the app, appdetails for its names and languages.

Used for: the official English name, the Japanese store name (l=japanese), and supported_languages, which
says whether Vietnamese is an official language. Data is for the user's personal use (Steam's API terms).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.lookup.http import Session
from app.lookup.names import strip_html, title_similarity
from app.lookup.types import GameCandidate

log = logging.getLogger(__name__)

SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
DETAILS_URL = "https://store.steampowered.com/api/appdetails"
STORE_PAGE = "https://store.steampowered.com/app/{appid}/"
DETAIL_FILTERS = "basic,developers,publishers,release_date"


@dataclass
class SteamInfo:
    appid: int
    name: str = ""
    name_ja: str = ""
    type: str = ""
    languages: list[str] = field(default_factory=list)  # English names as the store lists them
    audio_languages: list[str] = field(default_factory=list)
    developers: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    release: str = ""
    score: float = 0.0
    alternatives: list[GameCandidate] = field(default_factory=list)

    @property
    def vietnamese(self) -> bool:
        return any(lang.casefold() == "vietnamese" for lang in self.languages)

    @property
    def url(self) -> str:
        return STORE_PAGE.format(appid=self.appid)

    def candidate(self) -> GameCandidate:
        return GameCandidate("steam", self.name, self.score, {"steam": str(self.appid)},
                             ", ".join(x for x in (self.release, ", ".join(self.developers)) if x), self.name_ja)


def parse_languages(html: str) -> tuple[list[str], list[str]]:
    """("English, Japanese<strong>*</strong><br><strong>*</strong>languages with full audio support")
    -> (["English", "Japanese"], ["Japanese"])."""
    text = re.split(r"<br\s*/?>", html or "", maxsplit=1)[0]
    langs, audio = [], []
    for part in text.split(","):
        full = "<strong>*</strong>" in part or part.strip().endswith("*")
        name = strip_html(part).replace("*", "").strip()
        if name:
            langs.append(name)
            if full:
                audio.append(name)
    return langs, audio


def search(session: Session, title: str, limit: int = 8) -> list[tuple[int, str, float]]:
    """(appid, name, similarity) of the store's apps for `title`, best first."""
    data = session.get_json(SEARCH_URL, {"term": title, "l": "english", "cc": "US"})
    out = []
    for item in (data or {}).get("items", [])[:limit]:
        if not isinstance(item, dict) or item.get("type") != "app" or not item.get("id"):
            continue
        name = str(item.get("name") or "")
        out.append((int(item["id"]), name, title_similarity(title, name)))
    out.sort(key=lambda x: -x[2])
    return out


def details(session: Session, appid: int, lang: str = "english", filters: str = DETAIL_FILTERS) -> dict:
    data = session.get_json(DETAILS_URL, {"appids": appid, "l": lang, "filters": filters})
    entry = (data or {}).get(str(appid)) or {}
    return (entry.get("data") or {}) if entry.get("success") else {}


def lookup(session: Session, titles: list[str], appid: int | None = None, *, japanese: bool = True,
           min_score: float = 0.72) -> SteamInfo | None:
    """The game's store data: by app id (from the local appmanifest) or by the best title match."""
    alternatives: list[GameCandidate] = []
    score = 1.0
    if not appid:
        best: tuple[int, str, float] | None = None
        for title in titles[:2]:
            hits = search(session, title)
            if hits and (best is None or hits[0][2] > best[2]):
                best = hits[0]
                alternatives = [GameCandidate("steam", n, s, {"steam": str(a)}) for a, n, s in hits[1:4]
                                if s >= min_score - 0.1]
            if best and best[2] >= 0.99:
                break
        if best is None or best[2] < min_score:
            return None
        appid, score = best[0], best[2]
    data = details(session, appid)
    if not data:
        return None
    info = SteamInfo(appid=appid, name=str(data.get("name") or ""), type=str(data.get("type") or ""), score=score,
                     developers=[str(d) for d in data.get("developers") or []],
                     publishers=[str(p) for p in data.get("publishers") or []],
                     release=str((data.get("release_date") or {}).get("date") or ""), alternatives=alternatives)
    info.languages, info.audio_languages = parse_languages(str(data.get("supported_languages") or ""))
    if info.type and info.type not in ("game", "dlc"):
        log.info("steam %s is a %s, not a game", appid, info.type)
    if japanese and ("Japanese" in info.languages or not info.languages):
        try:
            ja = details(session, appid, "japanese", "basic")
            name_ja = str(ja.get("name") or "")
            if name_ja and name_ja != info.name:
                info.name_ja = name_ja
        except Exception as e:  # the Japanese name is a bonus; keep the English answer
            log.debug("steam japanese details for %s failed: %s", appid, e)
    return info
