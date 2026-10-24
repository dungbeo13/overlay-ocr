"""Web search hits through SearXNG (app.lookup.searxng): pages about a Vietnamese patch ("<game> Việt hóa")
and the game's wiki when no structured source named it.

The base URL comes from the app's own SearXNG on 127.0.0.1 (SearxngService), a URL the user set, or, when
allowed and nothing else is set up, one of the tested public instances (they see the query: the game title
only). Hits are only shown as unverified links; nothing is downloaded. A hit counts as a patch page only when
its TITLE says so (việt hóa / bản dịch / patch) and names this game (not a sequel, another volume, a warez or
18+ site, Wikipedia, a font or apk download).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlsplit

from app.lookup.names import norm_title
from app.lookup.sources.mediawiki import api_from_url
from app.lookup.types import PatchLink

log = logging.getLogger(__name__)

SearchFn = Callable[[str, str], list]  # (query, language) -> hits with .title / .url / .content
MAX_PATCH_HITS = 4
# Words a patch page's title has (plain: no diacritics). "vietsub" is anime / film subtitles, not a game patch.
_PATCH_WORDS = ("viet hoa", "viet ngu", "ban dich", "patch", "ban tieng viet", "vietnamese translation",
                "vietnamese patch")
_SKIP_HOSTS = ("youtube.com", "youtu.be", "tiktok.com", "pinterest.", "store.steampowered.com", "play.google.com",
               "apps.apple.com", "wikipedia.org", "fahasa.com", "tiki.vn", "shopee.vn", "lazada.vn",
               "opensubtitles.", "subscene.", "animevn", "animehay", "anime47", "vuighe",
               # warez / 18+ / SEO-spam hosts seen for "<game> Việt hóa" (2026-10-25)
               "linkneverdie", "tamhongame", "erovns", "mod18", "aow.vn", "uptodown", "thinkmay.net",
               "viethoapc.blogspot", "taigame", "apkpure", "apkmody", "modcombo", "fshare.vn",
               "inviva.vn", "bcdhouse.vn", "diamondspa.com.vn", "dammesangtao.com")
_BAD_TITLE = re.compile(r"(?<!\w)(tai ve game|tai game|tai mien phi|fshare|mega|google drive|\| *drive|"
                        r"apk|crack|full crack|18\+|font|hentai|wikipedia)(?!\w)")
_STOPWORDS = frozenset({"the", "a", "an", "of", "and", "to", "in", "on", "for", "with", "when", "they", "cry", "no",
                        "wo", "ni", "wa", "ga", "de", "la", "le", "is", "it", "you", "my", "your", "game", "vol"})
_EDITION = re.compile(r"^(\d+|ii|iii|iv|vi|vii|viii|ix|re|reboot|zero|plus|remake|remaster|remastered|hd|elite|"
                      r"royal|reload|gamma|linear|darling|vol|volume|chapter|part|phan|season|dlc|reflection|kiwami|"
                      r"fd|append)$")
_WIKIPEDIA_SUFFIX = re.compile(r"\s*[–—-]\s*Wikipedia(?: tiếng Việt)?\s*$", re.IGNORECASE)


def _plain(text: str) -> str:
    t = unicodedata.normalize("NFKD", (text or "").replace("đ", "d").replace("Đ", "D"))
    return " ".join("".join(ch for ch in t if not unicodedata.combining(ch)).casefold().split())


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", _plain(text))


def _field(hit: Any, name: str) -> str:
    return str((hit.get(name) if isinstance(hit, dict) else getattr(hit, name, "")) or "")


def url_key(url: str) -> str:
    """One key per page: no scheme / www / trailing slash, %7E == ~, a forum thread's /page-N dropped."""
    u = unquote((url or "").strip())
    u = re.sub(r"^https?://(www\.)?", "", u, flags=re.IGNORECASE)
    u = re.sub(r"/page-\d+/?$|[?&]page=\d+$", "", u, flags=re.IGNORECASE)
    return u.rstrip("/").casefold()


def _distinctive(title: str) -> list[str]:
    return [w for w in _tokens(title) if w not in _STOPWORDS and (len(w) > 3 or w.isdigit() or not w.isascii())]


def _mentions(title: str, text: str) -> bool:
    """`text` names the game `title`: the whole title, or most of its distinctive words including the longest one
    ("Higurashi When They Cry": "when", "they", "cry" alone are no match)."""
    key = norm_title(title)
    hay = norm_title(text)
    if key and key in hay:
        return True
    words = _distinctive(title)
    if not words:
        return False
    have = set(_tokens(text))
    main = max(words, key=len)
    return main in have and sum(w in have for w in words) >= max(1, (len(words) * 2 + 2) // 3)


def _other_edition(title: str, text: str) -> bool:
    """`text` names another edition or sequel of `title` ("STEINS;GATE 0", "STEINS;GATE RE:BOOT", "Nekopara Vol. 4"
    for "NEKOPARA Vol. 1"): the word right after the title's words, or the first one that differs, is an
    edition word the title does not have."""
    want = _tokens(title)
    have = _tokens(text)
    if not want or not have:
        return False
    for i in range(len(have)):
        k = 0
        while k < len(want) and i + k < len(have) and have[i + k] == want[k]:
            k += 1
        if k == 0:
            continue
        nxt = have[i + k] if i + k < len(have) else ""
        if k < len(want) and nxt and nxt != want[k] and _EDITION.match(nxt):
            return True  # "nekopara vol 4" for "nekopara vol 1"
        if k == len(want) and nxt and nxt not in want and _EDITION.match(nxt) \
                and not (nxt == "viet" or nxt.startswith("hoa")):
            return True  # "steins gate 0", "steins gate re boot"
        if k == len(want):
            return False
    return False


def resolve_base_url(*, service: Any = None, url: str = "", public_fallback: bool = True,
                     timeout: float = 30.0) -> tuple[str, str]:
    """(base URL, "local" | "external" | "public") of a SearXNG to ask; ("", reason) when there is none.
    `service` (a SearxngService the caller owns and stops) is asked first, then `url`. The public instances only
    when NO SearXNG is set up (no URL, no managed install): the consent window named the one set up, and a title
    never goes to a public server instead of it. Imports app.lookup.searxng lazily (it may be missing)."""
    try:
        from app.lookup import searxng
    except ImportError:
        return "", "SearXNG chưa có trong bản cài này"
    if service is not None:
        try:
            base = service.ensure_running(timeout=timeout)
        except Exception:
            log.warning("SearXNG did not start", exc_info=True)
            base = None
        if base:
            return base, "local" if searxng._is_loopback(base) else "external"
    elif url:
        try:
            if searxng.probe(url):
                return url, "local" if searxng._is_loopback(url) else "external"
        except Exception:
            log.debug("SearXNG probe of %s failed", url, exc_info=True)
    if public_fallback and not configured(service, url):
        for inst in list(getattr(searxng, "PUBLIC_INSTANCES", []))[:3]:
            try:
                if searxng.probe(inst):
                    return inst, "public"
            except Exception:
                continue
    return "", "không có máy tìm kiếm SearXNG nào trả lời"


def configured(service: Any = None, url: str = "") -> bool:
    """A SearXNG is set up: a URL, or the app's managed install."""
    if (url or "").strip() or str(getattr(service, "url", "") or "").strip():
        return True
    if service is not None and getattr(service, "managed", False):
        try:
            return bool(service.is_installed())
        except Exception:
            return False
    return False


def make_search(base_url: str, timeout: float = 10.0) -> SearchFn:
    """search(query, language) on `base_url`; SearXNG's errors become LookupHttpError (a source error)."""
    from app.lookup import searxng
    from app.lookup.http import LookupHttpError

    def run(query: str, language: str) -> list:
        try:
            return searxng.search(base_url, query, language=language, timeout=timeout)
        except searxng.SearxngError as e:
            raise LookupHttpError(str(e), status=getattr(e, "status", None), url=base_url) from e
    return run


def patch_hit(title: str, name: str, url: str) -> bool:
    """A search hit (`name` = its page title) is a page about a Vietnamese patch of the game `title`."""
    host = (urlsplit(url).hostname or "").casefold()
    if not url.startswith(("http://", "https://")) or not host or any(h in host for h in _SKIP_HOSTS):
        return False
    plain = _plain(name)
    if not any(w in plain for w in _PATCH_WORDS) or _BAD_TITLE.search(plain):
        return False
    if "/store/" in urlsplit(url).path.casefold():
        return False
    return _mentions(title, name) and not _other_edition(title, name)


def vietnamese_patches(search: SearchFn, title: str) -> list[PatchLink]:
    """Pages that talk about a Vietnamese version of `title` (unverified links, at most MAX_PATCH_HITS)."""
    out: list[PatchLink] = []
    seen: set[str] = set()
    for hit in search(f"{title} Việt hóa", "vi") or []:
        url, content = _field(hit, "url"), _field(hit, "content")
        name = " ".join(_WIKIPEDIA_SUFFIX.sub("", _field(hit, "title")).split())
        key = url_key(url)
        if not key or key in seen or not patch_hit(title, name, url):
            continue
        seen.add(key)
        url = re.sub(r"/page-\d+/?$", "/", url)  # a forum thread from its first page
        name = re.sub(r"\s*\|\s*Trang \d+\b", "", name)
        host = (urlsplit(url).hostname or "").removeprefix("www.")
        out.append(PatchLink(title=name[:140] or url, url=url, source="web",
                             note=f"tìm web: {host} — chưa kiểm chứng", verified=False))
        if len(out) >= MAX_PATCH_HITS:
            break
    log.debug("web patches for %r: %d of the hits kept", title, len(out))
    return out


def discover_wikis(search: SearchFn, title: str) -> list[str]:
    """MediaWiki API URLs of wikis about `title` found by a web search (Fandom / wiki.gg)."""
    apis: list[str] = []
    for hit in search(f"{title} wiki characters", "en") or []:
        api, _page = api_from_url(_field(hit, "url"))
        if api and api not in apis and _mentions(title, f"{_field(hit, 'title')} {_field(hit, 'url')}"):
            apis.append(api)
    return apis[:2]
