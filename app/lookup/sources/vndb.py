"""VNDB Kana API (https://api.vndb.org/kana, key-free; data under ODbL): visual novels, their characters
(romanized name, original script, aliases, sex, role; anything marked as a spoiler is skipped) and
Vietnamese releases (shown as links only).

Requests: one vn search (or a lookup by id), one character query, one release query."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.lookup.http import Session
from app.lookup.names import is_cjk, nfc, pair_parts, title_similarity
from app.lookup.types import CharacterCandidate, GameCandidate, PatchLink

log = logging.getLogger(__name__)

API = "https://api.vndb.org/kana"
VN_URL = "https://vndb.org/{id}"
VN_FIELDS = ("id,title,alttitle,olang,released,languages,votecount,titles.lang,titles.title,titles.latin,titles.main,"
             "titles.official,developers.name,extlinks.name,extlinks.url")
CHAR_FIELDS = "id,name,original,aliases,sex,vns.id,vns.role,vns.spoiler"
RELEASE_FIELDS = ("id,title,alttitle,languages.lang,languages.mtl,languages.title,official,patch,freeware,released,"
                  "platforms,producers.name,extlinks.url,extlinks.label")
ROLE_ORDER = {"main": 0, "primary": 1, "side": 2, "appears": 3}
_SEX = {"m": "nam", "f": "nữ"}
_PLACEHOLDER_NAMES = {"mc", "protagonist", "main character", "player", "主人公", "プレイヤー", "you"}


@dataclass
class VnInfo:
    id: str
    title: str = ""
    alttitle: str = ""  # usually the original-script title
    olang: str = ""
    released: str = ""
    languages: list[str] = field(default_factory=list)
    titles: list[dict] = field(default_factory=list)
    developers: list[str] = field(default_factory=list)
    wikidata: str = ""
    votecount: int = 0
    score: float = 0.0
    alternatives: list[GameCandidate] = field(default_factory=list)

    @property
    def url(self) -> str:
        return VN_URL.format(id=self.id)

    def title_in(self, lang: str) -> str:
        """The title in `lang`: its main / official one first."""
        rows = [t for t in self.titles if t.get("lang") == lang and t.get("title")]
        rows.sort(key=lambda t: (not t.get("main"), not t.get("official")))
        return str(rows[0]["title"]) if rows else ""

    def candidate(self) -> GameCandidate:
        ids = {"vndb": self.id}
        if self.wikidata:
            ids["wikidata"] = self.wikidata
        return GameCandidate("vndb", self.title, self.score, ids,
                             ", ".join(x for x in (self.released[:4], ", ".join(self.developers[:2])) if x),
                             self.title_in("ja") or (self.alttitle if is_cjk(self.alttitle) else ""))


def _vn(item: dict, query: str = "") -> VnInfo:
    vn = VnInfo(id=str(item.get("id") or ""), title=str(item.get("title") or ""),
                alttitle=str(item.get("alttitle") or ""), olang=str(item.get("olang") or ""),
                released=str(item.get("released") or ""), languages=[str(x) for x in item.get("languages") or []],
                titles=[t for t in item.get("titles") or [] if isinstance(t, dict)],
                developers=[str(d.get("name")) for d in item.get("developers") or [] if isinstance(d, dict)],
                votecount=int(item.get("votecount") or 0))
    for link in item.get("extlinks") or []:
        url = str((link or {}).get("url") or "")
        if (link or {}).get("name") == "wikidata" and "/wiki/Q" in url:
            vn.wikidata = url.rsplit("/", 1)[-1]
    if query:
        names = [vn.title, vn.alttitle] + [str(t.get(k) or "") for t in vn.titles for k in ("title", "latin")]
        vn.score = max((title_similarity(query, n) for n in names if n), default=0.0)
    return vn


def search(session: Session, title: str, limit: int = 5) -> list[VnInfo]:
    data = session.post_json(f"{API}/vn", {"filters": ["search", "=", title], "fields": VN_FIELDS,
                                           "sort": "searchrank", "results": limit})
    out = [_vn(item, title) for item in (data or {}).get("results", []) if isinstance(item, dict)]
    out.sort(key=lambda v: (-round(v.score, 2), -v.votecount))
    return out


def get_vn(session: Session, vid: str) -> VnInfo | None:
    data = session.post_json(f"{API}/vn", {"filters": ["id", "=", vid], "fields": VN_FIELDS, "results": 1})
    results = [r for r in (data or {}).get("results", []) if isinstance(r, dict)]
    if not results:
        return None
    vn = _vn(results[0])
    vn.score = 1.0
    return vn


def lookup(session: Session, titles: list[str], *, vid: str | None = None, min_score: float = 0.75) -> VnInfo | None:
    """The visual novel: by a known id (e.g. from Wikidata), else by the best title match."""
    if vid:
        return get_vn(session, vid)
    best: list[VnInfo] = []
    for title in titles[:2]:
        hits = search(session, title)
        if hits and (not best or hits[0].score > best[0].score):
            best = hits
        if best and best[0].score >= 0.99:
            break
    if not best or best[0].score < min_score:
        return None
    vn = best[0]
    vn.alternatives = [v.candidate() for v in best[1:4] if v.score >= min_score - 0.1]
    return vn


def _aliases(raw: list) -> list[tuple[str, str]]:
    """VNDB lists an original-script alias followed by its reading ("クリス", "Chris"): pairs; a Latin alias
    without an original form stays alone with an empty original."""
    items = [nfc(str(a)) for a in raw or [] if isinstance(a, str) and a.strip()]
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(items):
        a = items[i]
        if is_cjk(a):
            reading = items[i + 1] if i + 1 < len(items) and not is_cjk(items[i + 1]) else ""
            out.append((a, reading))
            i += 2 if reading else 1
        else:
            out.append(("", a))
            i += 1
    return out


def characters(session: Session, vid: str, limit: int = 100) -> list[CharacterCandidate]:
    """Characters of `vid` without spoilers, main characters first."""
    return characters_and_spoilers(session, vid, limit)[0]


def characters_and_spoilers(session: Session, vid: str,
                            limit: int = 100) -> tuple[list[CharacterCandidate], list[CharacterCandidate]]:
    """(characters without spoilers, main first; the characters VNDB marks as a spoiler of `vid`). The second list
    is never shown: other sources' characters and aliases matching it are dropped (Wikidata has no spoiler flag)."""
    data = session.post_json(f"{API}/character", {"filters": ["vn", "=", ["id", "=", vid]], "fields": CHAR_FIELDS,
                                                  "sort": "id", "results": min(100, limit)})
    out: list[tuple[int, CharacterCandidate]] = []
    hidden: list[CharacterCandidate] = []
    for item in (data or {}).get("results", []) or []:
        if not isinstance(item, dict):
            continue
        appearance = next((v for v in item.get("vns") or [] if isinstance(v, dict) and v.get("id") == vid), {})
        name = nfc(str(item.get("name") or ""))
        original = nfc(str(item.get("original") or "")) or name
        if int(appearance.get("spoiler") or 0) > 0:  # the character itself is a spoiler of this VN
            if original:
                hidden.append(CharacterCandidate(original=original, romanized=name, parts=pair_parts(original, name),
                                                 aliases=_aliases(item.get("aliases") or []), sources=["vndb"],
                                                 ids={"vndb": str(item.get("id") or "")}))
            continue
        if not original or name.casefold() in _PLACEHOLDER_NAMES or original.casefold() in _PLACEHOLDER_NAMES:
            continue
        sex = item.get("sex") or []
        role = str(appearance.get("role") or "")
        c = CharacterCandidate(original=original, romanized=name, parts=pair_parts(original, name),
                               aliases=_aliases(item.get("aliases") or []),
                               gender=_SEX.get(str(sex[0]) if sex else "", ""),  # the apparent sex, never the spoiler one
                               role=role, sources=["vndb"], ids={"vndb": str(item.get("id") or "")},
                               native=bool(item.get("original")))
        out.append((ROLE_ORDER.get(role, 9), c))
    out.sort(key=lambda x: x[0])
    return [c for _, c in out], hidden


def vi_releases(session: Session, vid: str) -> list[PatchLink]:
    """Vietnamese releases of `vid` (patches and official versions) as links."""
    data = session.post_json(f"{API}/release", {"filters": ["and", ["vn", "=", ["id", "=", vid]], ["lang", "=", "vi"]],
                                                "fields": RELEASE_FIELDS, "sort": "released", "results": 10})
    out: list[PatchLink] = []
    for item in (data or {}).get("results", []) or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        lang = next((x for x in item.get("languages") or [] if isinstance(x, dict) and x.get("lang") == "vi"), {})
        title = nfc(str(lang.get("title") or item.get("title") or ""))
        page = VN_URL.format(id=item["id"])
        links = [str(x.get("url")) for x in item.get("extlinks") or []
                 if isinstance(x, dict) and str(x.get("url", "")).startswith(("http://", "https://"))]
        producers = ", ".join(str(p.get("name")) for p in item.get("producers") or [] if isinstance(p, dict))
        official = bool(item.get("official"))
        bits = ["bản chính thức" if official else "bản dịch của cộng đồng (không chính thức)"]
        if item.get("patch"):
            bits.append("bản vá")
        if lang.get("mtl"):
            bits.append("dịch máy")
        if producers:
            bits.append(producers)
        released = str(item.get("released") or "")
        if released:
            bits.append("chưa phát hành" if released == "TBA" else released)
        out.append(PatchLink(title=title, url=links[0] if links else page, source="vndb", note=" · ".join(bits),
                             official=official, info_url=page))
    return out
