"""Wikidata (CC0, key-free): the game item, its labels (ja/en/vi), ids of the same game elsewhere
(Steam P1733, VNDB P3180, Fandom wiki P4073) and its characters (P674) with their labels and sex (P21).

Requests: wbsearchentities (or a haswbstatement search when the Steam / VNDB id is known), one
wbgetentities for the game and one for up to MAX_CHARACTERS characters."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.lookup.http import Session
from app.lookup.names import fix_long_vowels, is_cjk, title_similarity
from app.lookup.types import CharacterCandidate, GameCandidate, TermCandidate

log = logging.getLogger(__name__)

API = "https://www.wikidata.org/w/api.php"
ITEM_URL = "https://www.wikidata.org/wiki/{qid}"
LANGS = "ja|en|vi|mul"  # "mul": the label for every language (Wikidata keeps "Genshin Impact" only there)
MAX_CHARACTERS = 50  # one wbgetentities request

P_INSTANCE, P_CHARACTERS, P_STEAM, P_VNDB, P_FANDOM_WIKI, P_FANDOM_ARTICLE = "P31", "P674", "P1733", "P3180", "P4073", "P6262"
P_LANGUAGE, P_ORIGINAL_LANGUAGE, P_COUNTRY, P_SEX, P_NICKNAME, P_PLACE, P_UNIVERSE, P_DATE = \
    "P407", "P364", "P495", "P21", "P1449", "P840", "P1434", "P577"
GAME_CLASSES = {"Q7889", "Q689445", "Q4393107", "Q865493", "Q21125433", "Q64170203", "Q1150710"}
_SEX = {"Q6581097": "nam", "Q2449503": "nam", "Q6581072": "nữ", "Q1052281": "nữ"}
_LANG_CODES = {"Q5287": "ja", "Q1860": "en", "Q9217": "ko", "Q7850": "zh", "Q13414913": "zh", "Q18130932": "zh",
               "Q9199": "vi", "Q150": "fr", "Q188": "de", "Q1321": "es", "Q7737": "ru"}
# Country of origin -> the language its games' text is first written in (only where that is a safe bet).
_COUNTRY_LANG = {"Q17": "ja", "Q30": "en", "Q145": "en", "Q16": "en", "Q408": "en", "Q664": "en", "Q27": "en"}
_GAME_WORDS = ("video game", "visual novel", "game", "ゲーム", "ノベル", "trò chơi", "eroge", "rpg")
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿"))  # "Nhà Lữ Hành‍" (a vi label)
_NOT_GAME_WORDS = ("anime", "manga", "television", "film", "novel series", "light novel", "character", "soundtrack",
                   "franchise", "wiki", "universe", "board game", "album", "song", "episode", "media mix")
_PLACEHOLDER_NAMES = {"mc", "protagonist", "main character", "player", "主人公", "プレイヤー", "あなた", "you", "the player"}


@dataclass
class WikidataGame:
    qid: str
    labels: dict[str, str] = field(default_factory=dict)
    description: str = ""
    instance_of: list[str] = field(default_factory=list)
    steam: list[str] = field(default_factory=list)
    vndb: list[str] = field(default_factory=list)
    fandom: list[str] = field(default_factory=list)  # Fandom wiki ids (P4073), e.g. "blue-archive"
    characters: list[str] = field(default_factory=list)  # QIDs
    places: list[str] = field(default_factory=list)  # QIDs: narrative locations (P840), fictional universe (P1434)
    languages: list[str] = field(default_factory=list)  # ISO codes of P364 (original) then P407
    original_language: str = ""
    country_language: str = ""
    year: str = ""
    score: float = 0.0
    alternatives: list[GameCandidate] = field(default_factory=list)

    @property
    def url(self) -> str:
        return ITEM_URL.format(qid=self.qid)

    @property
    def is_game(self) -> bool:
        return bool(set(self.instance_of) & GAME_CLASSES)

    def candidate(self) -> GameCandidate:
        ids = {"wikidata": self.qid}
        if self.steam:
            ids["steam"] = self.steam[0]
        if self.vndb:
            ids["vndb"] = self.vndb[0]
        return GameCandidate("wikidata", self.labels.get("en") or self.labels.get("ja") or self.qid, self.score, ids,
                             self.description, self.labels.get("ja", ""))


# --- claim helpers ----------------------------------------------------------------------------

def _values(entity: dict, prop: str) -> list:
    out = []
    for claim in (entity.get("claims") or {}).get(prop, []) or []:
        if not isinstance(claim, dict) or claim.get("rank") == "deprecated":
            continue
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if value is not None:
            out.append(value)
    return out


def _ids(entity: dict, prop: str) -> list[str]:
    return [v["id"] if isinstance(v, dict) else str(v) for v in _values(entity, prop)
            if (isinstance(v, dict) and v.get("id")) or isinstance(v, str)]


def _labels(entity: dict) -> dict[str, str]:
    """{lang: label}; the multilingual ("mul") label stands in for a missing English one (Latin script) or a
    missing Japanese one (Japanese script)."""
    out = {lang: " ".join(str(v.get("value") or "").translate(_INVISIBLE).split())
           for lang, v in (entity.get("labels") or {}).items() if isinstance(v, dict) and v.get("value")}
    out = {k: v for k, v in out.items() if v}
    mul = out.pop("mul", "")
    if mul:
        out.setdefault("ja" if is_cjk(mul) else "en", mul)
    return out


def _gamelike(description: str) -> bool:
    return any(w in (description or "").casefold() for w in _GAME_WORDS)


def _description(entity: dict) -> str:
    d = entity.get("descriptions") or {}
    for lang in ("en", "ja", "vi"):
        if isinstance(d.get(lang), dict) and d[lang].get("value"):
            return str(d[lang]["value"])
    return ""


def game_from_entity(entity: dict) -> WikidataGame:
    g = WikidataGame(qid=str(entity.get("id") or ""), labels=_labels(entity), description=_description(entity),
                     instance_of=_ids(entity, P_INSTANCE), steam=[str(v) for v in _values(entity, P_STEAM)],
                     vndb=[str(v) for v in _values(entity, P_VNDB) if str(v).startswith("v")],
                     fandom=[str(v) for v in _values(entity, P_FANDOM_WIKI)],
                     characters=_ids(entity, P_CHARACTERS), places=_ids(entity, P_PLACE) + _ids(entity, P_UNIVERSE))
    original = [_LANG_CODES.get(q, "") for q in _ids(entity, P_ORIGINAL_LANGUAGE)]
    g.original_language = "ja" if "ja" in original else next((c for c in original if c), "")
    g.languages = [c for c in dict.fromkeys(original + [_LANG_CODES.get(q, "") for q in _ids(entity, P_LANGUAGE)]) if c]
    countries = [_COUNTRY_LANG[q] for q in _ids(entity, P_COUNTRY) if q in _COUNTRY_LANG]
    g.country_language = "ja" if "ja" in countries else (countries[0] if countries else "")
    dates = [v.get("time", "") for v in _values(entity, P_DATE) if isinstance(v, dict)]
    g.year = min((d[1:5] for d in dates if len(d) >= 5), default="")
    return g


# --- requests ---------------------------------------------------------------------------------

def get_entities(session: Session, qids: list[str], props: str = "labels|claims|descriptions") -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(qids), 50):
        batch = [q for q in qids[i:i + 50] if q]
        if not batch:
            continue
        data = session.get_json(API, {"action": "wbgetentities", "ids": "|".join(batch), "props": props,
                                      "languages": LANGS, "format": "json"})
        for qid, entity in ((data or {}).get("entities") or {}).items():
            if isinstance(entity, dict) and "missing" not in entity:
                out[qid] = entity
    return out


def search(session: Session, title: str, limit: int = 7) -> list[GameCandidate]:
    """Game-like items for `title` (by label / alias), best first."""
    lang = "ja" if is_cjk(title) else "en"
    data = session.get_json(API, {"action": "wbsearchentities", "search": title, "language": lang, "uselang": lang,
                                  "type": "item", "limit": limit, "format": "json"})
    out: list[GameCandidate] = []
    for hit in (data or {}).get("search", []) or []:
        if not isinstance(hit, dict) or not hit.get("id"):
            continue
        desc = str(hit.get("description") or "")
        low = desc.casefold()
        label = str(hit.get("label") or "")
        matched = str((hit.get("match") or {}).get("text") or label)
        score = max(title_similarity(title, label), title_similarity(title, matched))
        if any(w in low for w in _NOT_GAME_WORDS):
            continue
        if desc and not _gamelike(desc):
            continue  # "human" for "Persona": a described item that is no game is never the game
        if any(w in low for w in ("video game", "visual novel")):
            score += 0.03
        out.append(GameCandidate("wikidata", label, round(min(score, 1.0), 3), {"wikidata": str(hit["id"])}, desc))
    out.sort(key=lambda c: -c.score)
    return out


def find_by_id(session: Session, prop: str, value: str) -> list[str]:
    """QIDs of items with `prop` = `value` (e.g. P1733 = a Steam app id)."""
    data = session.get_json(API, {"action": "query", "list": "search", "srsearch": f"haswbstatement:{prop}={value}",
                                  "srlimit": 5, "srprop": "", "format": "json"})
    return [str(h.get("title")) for h in ((data or {}).get("query") or {}).get("search", []) or []
            if isinstance(h, dict) and str(h.get("title", "")).startswith("Q")]


def lookup(session: Session, titles: list[str], *, steam_appid: str | None = None, vndb_id: str | None = None,
           qid: str | None = None, min_score: float = 0.72) -> WikidataGame | None:
    """The game's item: by a known QID, by the Steam / VNDB id, else by the best title match."""
    alternatives: list[GameCandidate] = []
    score = 1.0
    by_title = False
    if not qid:
        for prop, value in ((P_STEAM, steam_appid), (P_VNDB, vndb_id)):
            if value:
                found = find_by_id(session, prop, str(value))
                if found:
                    qid = found[0]
                    break
    if not qid:
        by_title = True
        best: list[GameCandidate] = []
        for title in titles[:2]:
            hits = search(session, title)
            if hits and (not best or hits[0].score > best[0].score):
                best = hits
            if best and best[0].score >= 0.99:
                break
        if not best or best[0].score < min_score:
            return None
        qid, score = best[0].ids["wikidata"], best[0].score
        alternatives = [c for c in best[1:4] if c.score >= min_score - 0.1]
    entity = get_entities(session, [qid]).get(qid)
    if not entity:
        return None
    game = game_from_entity(entity)
    if by_title and not game.is_game and not _gamelike(game.description):
        log.info("wikidata %s (%s) is not a game: not used", qid, game.description or "no description")
        return None  # a title match only counts when the item is a game (an id from Steam / VNDB says so itself)
    game.score, game.alternatives = score, alternatives
    return game


def characters(session: Session, qids: list[str], limit: int = MAX_CHARACTERS,
               src_lang: str | None = "ja") -> list[CharacterCandidate]:
    """Characters with their name as the game's text writes it: the Japanese label for a Japanese game,
    the English one otherwise (the other one is kept as an alias)."""
    out: list[CharacterCandidate] = []
    entities = get_entities(session, qids[:limit], props="labels|claims")
    for qid in qids[:limit]:
        e = entities.get(qid)
        if not e:
            continue
        labels = _labels(e)
        ja, en = labels.get("ja", ""), fix_long_vowels(labels.get("en", ""))
        original = (ja or en) if src_lang in ("ja", None) else (en or ja)
        if not original or original.casefold() in _PLACEHOLDER_NAMES or en.casefold() in _PLACEHOLDER_NAMES:
            continue
        sexes = {_SEX[q] for q in _ids(e, P_SEX) if q in _SEX}
        c = CharacterCandidate(original=original, romanized=en, gender=sexes.pop() if len(sexes) == 1 else "",
                               label_vi=labels.get("vi", ""), sources=["wikidata"], ids={"wikidata": qid},
                               native=bool(ja) and original == ja)
        if ja and original != ja:
            c.aliases.append((ja, en))
        vndb_chars = [str(v) for v in _values(e, P_VNDB) if str(v).startswith("c")]
        if vndb_chars:
            c.ids["vndb"] = vndb_chars[0]
        articles = [str(v) for v in _values(e, P_FANDOM_ARTICLE)]
        if articles:
            c.ids["fandom"] = "|".join(articles)
        out.append(c)
    return out


def places(session: Session, qids: list[str], limit: int = 10) -> list[TermCandidate]:
    """Fictional places / worlds the game item names (Teyvat); real places are left to the translator, and a
    franchise's "universe" item ("Persona universe") is no word of the game's text."""
    out: list[TermCandidate] = []
    for qid, e in get_entities(session, qids[:limit], props="labels|descriptions").items():
        labels = _labels(e)
        desc = _description(e).casefold()
        ja, target = labels.get("ja", ""), labels.get("vi") or fix_long_vowels(labels.get("en", ""))
        if "universe" in labels.get("en", "").casefold() or ja.endswith(("の世界", "ユニバース")):
            continue
        if ja and target and "fictional" in desc and ja != target:
            kind = "place" if any(w in desc for w in ("place", "city", "location", "country", "world", "planet",
                                                          "island", "town", "school", "academy")) else "term"
            out.append(TermCandidate(ja, target, kind, sources=["wikidata"]))
    return out
