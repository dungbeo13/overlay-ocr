"""Game wikis through the MediaWiki API (Fandom, wiki.gg, ...; text under CC BY-SA, only names are used).

Characters come from the pages Wikidata links (P6262) and the wiki's character category; places and
organizations from their categories. Only the lead section of each page is fetched (rvsection=0, up to
BATCH pages per request), and the infobox's Japanese-name / romaji / gender fields are read from it."""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from app.lookup.http import Session
from app.lookup.names import fix_long_vowels, is_cjk, nfc, norm_title, pair_parts, split_parts, title_similarity
from app.lookup.types import CharacterCandidate, TermCandidate

log = logging.getLogger(__name__)

BATCH = 20  # lead sections per request
MAX_PAGES = 40
# Wikis about every franchise at once (Wikidata links characters to them too): never "the game's wiki".
GENERIC_WIKIS = frozenset({"hero", "villains", "yandere", "doblaje", "characterprofile", "vsbattles", "deathbattlefanon",
                           "allthetropes", "listofdeaths", "femalevillains", "evil", "pure-good-wiki", "ultimate-pop-culture",
                           "ultimatepopculture", "nicknames", "dubbing", "deadliestfiction", "the-loud-house-fanon",
                           "fictionalcrossover", "wikia", "community", "fanlore", "tvtropes", "love-interest", "lovelive"})
_JA_KEYS = {"japanese", "japanesename", "jpname", "jname", "namejp", "jpn", "kanji", "nameja", "jap", "japname",
            "jpnname", "namekanji", "kana", "nativename", "originalname", "japanesetitle", "jpnameoriginal", "jptitle",
            "nameoriginal", "ja", "jp", "nihongo", "katakana", "japanesekanji"}
_ROMAJI_KEYS = {"romaji", "rōmaji", "romanized", "romanization", "romanji", "nameromaji", "japaneseromaji", "jpromaji"}
_GENDER_KEYS = {"gender", "sex"}
_PARAM_RE = re.compile(r"^\s*\|\s*([^=|\n{}]+?)\s*=(.*)$", re.MULTILINE)
_NIHONGO_RE = re.compile(r"\{\{\s*nihongo\s*\|([^|{}]*)\|([^|{}]*)(?:\|([^|{}]*))?", re.IGNORECASE)
_GENDER = {"female": "nữ", "woman": "nữ", "girl": "nữ", "女": "nữ", "女性": "nữ", "male": "nam", "man": "nam", "boy": "nam",
           "男": "nam", "男性": "nam"}
_TERM_CATEGORIES = (("Locations", "place"), ("Organizations", "term"))
_KATAKANA_RE = re.compile(r"[゠-ヿｦ-ﾟ・･ー]+")
PLACEHOLDER_NAMES = {"mc", "protagonist", "main character", "player", "主人公", "プレイヤー", "you", "the player",
                     "characters", "character list", "narrator"}


@dataclass
class WikiSite:
    api: str
    name: str = ""  # sitename
    wiki_id: str = ""
    lang: str = ""
    base: str = ""  # article path, e.g. https://x.fandom.com/wiki/

    def page_url(self, title: str) -> str:
        return self.base + title.replace(" ", "_") if self.base else ""


@dataclass
class WikiPage:
    title: str
    japanese: str = ""
    romaji: str = ""
    gender: str = ""
    fields: dict[str, str] = field(default_factory=dict)


def fandom_api(wiki_id: str) -> str:
    """"steins-gate" -> https://steins-gate.fandom.com/api.php; "es.doblaje" -> https://doblaje.fandom.com/es/api.php."""
    wid = wiki_id.strip().lower()
    if not re.fullmatch(r"([a-z]{2,3}(-[a-z]+)?\.)?[a-z0-9][a-z0-9-]*", wid):
        return ""
    if "." in wid:
        lang, slug = wid.split(".", 1)
        return f"https://{slug}.fandom.com/{lang}/api.php"
    return f"https://{wid}.fandom.com/api.php"


def api_from_url(url: str) -> tuple[str, str]:
    """(api URL, page title) of a wiki article URL (Fandom / wiki.gg), ("", "") for anything else."""
    parts = urlsplit(url or "")
    host = parts.netloc.lower()
    m = re.match(r"^(?:/([a-z]{2,3}(?:-[a-z]+)?))?/wiki/(.+)$", parts.path)
    title = unquote(m.group(2)).replace("_", " ") if m else ""
    if host.endswith(".fandom.com"):
        lang = m.group(1) if m and m.group(1) else ""
        return f"https://{host}/{lang + '/' if lang else ''}api.php", title
    if host.endswith(".wiki.gg"):
        return f"https://{host}/api.php", title
    return "", ""


# --- wikitext -------------------------------------------------------------------------------

def clean_wikitext(value: str) -> str:
    """The first line of a template value as plain text; a name broken over two lines after "・" / "="
    ("フェイリス・<br>ニャンニャン") is joined again."""
    v = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", value or "", flags=re.DOTALL | re.IGNORECASE)
    lines = re.split(r"<br\s*/?>|\n", v)
    v = lines[0]
    for nxt in lines[1:]:
        if not v.rstrip().endswith(("・", "＝", "=", "･")):
            break
        v = v.rstrip() + nxt.strip()
    v = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", v)
    v = re.sub(r"\{\{[^{}]*\}\}", "", v)
    v = re.sub(r"<[^>]+>", "", v).replace("'''", "").replace("''", "")
    return nfc(v).strip(" *:;,-")


def parse_lead(title: str, wikitext: str) -> WikiPage:
    page = WikiPage(title=title)
    for key, value in _PARAM_RE.findall(wikitext or ""):
        k = re.sub(r"[\s_\-]+", "", key.strip().casefold())
        if k in _ROMAJI_KEYS:  # "Feirisu<br>NyanNyan": one name over two lines
            value = re.sub(r"<br\s*/?>", " ", value)
        v = clean_wikitext(value)
        if not v:
            continue
        page.fields.setdefault(k, v)
        if k in _JA_KEYS and not page.japanese and is_cjk(v):
            page.japanese = re.split(r"[(（]", v)[0].strip()
        elif k in _ROMAJI_KEYS and not page.romaji and not is_cjk(v):
            page.romaji = fix_long_vowels(re.split(r"[(（]", v)[0].strip())
        elif k in _GENDER_KEYS and not page.gender:
            page.gender = _GENDER.get(v.split()[0].casefold().strip(".,;"), "") if v.split() else ""
    if not page.japanese:
        m = _NIHONGO_RE.search(wikitext or "")
        if m and is_cjk(m.group(2)):
            page.japanese = clean_wikitext(m.group(2))
            if not page.romaji and m.group(3) and not is_cjk(m.group(3)):
                page.romaji = fix_long_vowels(clean_wikitext(m.group(3)))
    return page


# --- requests --------------------------------------------------------------------------------

def site_info(session: Session, api: str) -> WikiSite | None:
    data = session.get_json(api, {"action": "query", "meta": "siteinfo", "format": "json"})
    general = ((data or {}).get("query") or {}).get("general") or {}
    if not general.get("sitename"):
        return None
    base = str(general.get("server") or "") + str(general.get("articlepath") or "/wiki/$1").replace("$1", "")
    return WikiSite(api=api, name=str(general.get("sitename")), wiki_id=str(general.get("wikiid") or ""),
                    lang=str(general.get("lang") or ""), base=base)


def _pages(data) -> list[WikiPage]:
    out = []
    for p in (((data or {}).get("query") or {}).get("pages") or {}).values():
        if not isinstance(p, dict) or "missing" in p or int(p.get("ns", 0)) != 0:
            continue
        revs = p.get("revisions") or [{}]
        slot = ((revs[0] or {}).get("slots") or {}).get("main") or revs[0] or {}
        out.append(parse_lead(str(p.get("title") or ""), str(slot.get("*") or slot.get("content") or "")))
    return out


_LEAD = {"prop": "revisions", "rvprop": "content", "rvslots": "main", "rvsection": "0", "format": "json"}


def lead_sections(session: Session, api: str, titles: list[str]) -> list[WikiPage]:
    out: list[WikiPage] = []
    for i in range(0, len(titles), BATCH):
        batch = titles[i:i + BATCH]
        if batch:
            out += _pages(session.get_json(api, {"action": "query", "titles": "|".join(batch), "redirects": "1", **_LEAD}))
    return out


def category_pages(session: Session, api: str, category: str, limit: int = BATCH) -> list[WikiPage]:
    """Lead sections of the pages in `category` (one request, generator=categorymembers)."""
    data = session.get_json(api, {"action": "query", "generator": "categorymembers", "gcmtitle": f"Category:{category}",
                                  "gcmtype": "page", "gcmnamespace": "0", "gcmlimit": str(limit), **_LEAD})
    return _pages(data)


def find_category(session: Session, api: str, title: str = "", word: str = "characters") -> str:
    """The category of `title`'s characters on a wiki ("Persona 5 Royal Characters" on a series wiki,
    "Characters" on the game's own wiki)."""
    query = f"{title} {word}".strip()
    data = session.get_json(api, {"action": "query", "list": "search", "srsearch": query, "srnamespace": "14",
                                  "srlimit": "10", "srprop": "", "format": "json"})
    hits = [str(h.get("title", "")).split(":", 1)[-1] for h in ((data or {}).get("query") or {}).get("search", []) or []]
    hits = [h for h in hits if word in h.casefold() and not any(w in h.casefold() for w in ("minor", "image", "gallery"))]
    hits.sort(key=lambda h: (-title_similarity(query, h), len(h)))
    return hits[0] if hits else ""


# --- discovery -------------------------------------------------------------------------------

def pick_fandom_wiki(title: str, wiki_ids: list[str], article_ids: list[str]) -> tuple[str, list[str]]:
    """(wiki id, its article titles) of the game's own Fandom wiki among Wikidata's P4073 values and the
    "wiki:Article" P6262 values of its characters; ("", []) when none looks like the game's wiki."""
    articles: dict[str, list[str]] = {}
    counts: Counter[str] = Counter()
    for item in article_ids:
        for one in item.split("|"):
            if ":" in one:
                wid, art = one.split(":", 1)
                articles.setdefault(wid.lower(), []).append(art.replace("_", " "))
                counts[wid.lower()] += 1

    def own(wid: str) -> bool:
        slug = wid.split(".", 1)[-1]
        if slug in GENERIC_WIKIS or "." in wid:
            return False
        return title_similarity(title, slug) >= 0.55 or norm_title(slug) in norm_title(title) \
            or norm_title(title) in norm_title(slug)

    for wid in wiki_ids:
        if own(wid.lower()):
            w = wid.lower()
            return w, articles.get(w, []) or next((a for k, a in articles.items() if norm_title(k) == norm_title(w)), [])
    for wid, _n in counts.most_common():
        if own(wid):
            return wid, articles[wid]
    return "", []


def guess_fandom_ids(title: str) -> list[str]:
    """Likely Fandom ids for `title`: the series part first ("Danganronpa: Trigger Happy Havoc" ->
    "danganronpa"), then the whole title hyphenated / joined."""
    out: list[str] = []
    head = re.split(r"\s*[:：\-–—]\s+|\s*[:：]\s*", title or "", maxsplit=1)[0]
    for text in (head, title or ""):
        words = re.findall(r"[a-z0-9]+", text.casefold())
        if words:
            out += ["-".join(words), "".join(words)]
    return [w for w in dict.fromkeys(out) if 2 < len(w) <= 50]


_STOPWORDS = frozenset({"the", "a", "an", "of", "and", "to", "in", "on", "no", "wo", "ni", "wa", "de", "la", "le", "vol",
                        "volume", "game", "edition", "remastered", "remaster", "hd", "plus"})


def _site_name(site: WikiSite) -> str:
    return re.sub(r"(?i)\b(wiki|wikia|fandom|encyclopedia)\b", " ", site.name or "").strip(" -:")


def site_matches(title: str, site: WikiSite) -> bool:
    """The wiki is about this game or its series ("Danganronpa Wiki" for "Danganronpa: Trigger Happy Havoc")."""
    name = _site_name(site)
    key, tkey = norm_title(name), norm_title(title)
    return bool(key) and (title_similarity(title, name) >= 0.6 or (len(key) >= 5 and tkey.startswith(key)))


def franchise_words(title: str, site: WikiSite) -> list[str]:
    """The words of `title` a series wiki's name does not have ("Danganronpa Wiki", "Danganronpa: Trigger Happy
    Havoc" -> trigger, happy, havoc); [] when the wiki is the game's own (its name is the whole title)."""
    if title_similarity(title, _site_name(site)) >= 0.9:
        return []
    have = {w for w in re.findall(r"[a-z0-9]+", _site_name(site).casefold())}
    return [w for w in re.findall(r"[a-z0-9]+", (title or "").casefold())
            if w not in have and w not in _STOPWORDS and (len(w) > 2 or w.isdigit())]


# --- characters / terms ----------------------------------------------------------------------

def _character(page: WikiPage, src_lang: str | None, site: WikiSite) -> CharacterCandidate | None:
    name = re.sub(r"\s*\([^)]*\)$", "", page.title).strip()  # "Yuri (DDLC)" -> "Yuri"
    if src_lang == "ja" or (src_lang is None and page.japanese):
        if not page.japanese:
            return None
        original = page.japanese
        romanized = page.romaji
        katakana = bool(_KATAKANA_RE.fullmatch(original.replace(" ", "")))
        # A katakana name is a foreign-style name or a nickname: the page's (official English) title is
        # better than a letter-by-letter romaji ("モルガナ": Morgana, not "Morugana"); so is a romaji
        # field that says more than the name ("Sugimura Haru no fianse").
        if not romanized or katakana or len(romanized.split()) > len(split_parts(original)) + 1:
            romanized = fix_long_vowels(name)
        # Name parts pair in source order: the romaji field's, or a katakana name's English title.
        parts = pair_parts(original, romanized) if romanized == page.romaji or katakana else []
    else:
        original = romanized = name
        parts = []
    if not original or "(" in original or "/" in page.title or original.casefold() in PLACEHOLDER_NAMES \
            or page.title.casefold() in PLACEHOLDER_NAMES or page.title.casefold().startswith(("list of", "character list")) \
            or re.search(r"(?i)\s(and|&)\s", page.title):  # "Angel and Julian": a page about two characters
        return None
    return CharacterCandidate(original=original, romanized=romanized, parts=parts,
                              gender=page.gender, sources=["wiki"], native=bool(page.japanese) and original == page.japanese,
                              ids={"wiki": page.title, "wiki_url": site.page_url(page.title)})


def characters(session: Session, site: WikiSite, *, titles: list[str] = (), src_lang: str | None = "ja",
               game: str = "", max_pages: int = MAX_PAGES) -> list[CharacterCandidate]:
    """Characters from the pages Wikidata names (`titles`) and the wiki's category for `game`'s
    characters (else its "Characters" category). On a series wiki ("Megami Tensei Wiki" for "Persona 5 Royal") only
    a category naming this game is used: its plain "Characters" holds every game of the series."""
    pages: list[WikiPage] = lead_sections(session, site.api, list(dict.fromkeys(titles))[:max_pages]) if titles else []
    if len(pages) < max_pages:
        found: list[WikiPage] = []
        own = franchise_words(game, site) if game else []
        category = find_category(session, site.api, game) if game else ""
        if own and not any(w in category.casefold() for w in own):
            category = ""  # the series' category, not this game's
        for category in dict.fromkeys([category, "" if own else "Characters"]):
            if category:
                found = category_pages(session, site.api, category, min(BATCH * 2, max_pages - len(pages)))
                if found:
                    break
        have = {p.title for p in pages}
        pages += [p for p in found if p.title not in have]
    out = []
    seen: set[str] = set()
    for page in pages[:max_pages]:
        c = _character(page, src_lang, site)
        if c is not None and c.original.casefold() not in seen:  # "Monika (DDLC)" and "Monika (DDLC Plus)"
            seen.add(c.original.casefold())
            out.append(c)
    return out


def terms(session: Session, site: WikiSite, *, limit: int = BATCH) -> list[TermCandidate]:
    """Places and organizations with a Japanese name in their infobox (Japanese games only)."""
    out: list[TermCandidate] = []
    for category, kind in _TERM_CATEGORIES:
        for page in category_pages(session, site.api, category, limit):
            ja = page.japanese.replace(" ", "")
            if not is_cjk(ja) or "(" in page.title or "/" in page.title:
                continue
            out.append(TermCandidate(ja, page.title, kind, note="tên trên wiki", sources=["wiki"]))
    return out
