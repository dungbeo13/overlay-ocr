"""Looks a game up once: local template first, then the online sources, merged into one LookupReport.

    report = LookupService.from_config(config).run(identity, progress_cb=..., cancel_event=...)

run() is blocking and thread-safe (no Qt; call it from a worker thread). Order:
  0. profiles/templates/*.json matched by exe / Steam id / title: no network at all (Blue Archive).
  1. identify the game, in parallel: Steam, Wikidata, VNDB, each by title (or the Steam id from the
     game's appmanifest); then cross-check the ids they give each other (Wikidata P1733 / P3180, VNDB's
     Wikidata link) and pick one game, with alternatives when the match is not clear;
  2. details, in parallel: VNDB characters + Vietnamese releases, Wikidata characters + fictional
     places, web hits (SearXNG: "<game> Việt hóa");
  3. the game's wiki (Fandom id from Wikidata, its characters' wiki pages, a web hit or a guess), used only
     when its name matches the game.
Phases 2 and 3 run only when the game is certain (confidence "high": two sources agree, or an exact title);
otherwise the report holds the candidates and the user picks the right game first.
Only the titles in GameIdentity.query_titles() (and the ids the sources return) are sent. Every source
has its own timeout, the whole run a budget (~30 s); a failing source leaves the others' results. The
source jobs run on daemon threads: a cancel returns at once, and nothing holds the app open at exit.
Spoilers: VNDB's spoiler characters are never shown, and Wikidata / wiki characters or aliases matching
them are dropped (those sources have no spoiler flag); when VNDB knows the cast, the others only add details.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.lookup.game_id import identity_from_title
from app.lookup.http import CACHE_DIR, HttpClient, LookupCancelled, LookupHttpError, Session
from app.lookup.names import is_cjk, latin_key, name_key, norm_title, title_similarity
from app.lookup.sources import mediawiki, steam, vndb, web, wikidata
from app.lookup.templates import TEMPLATES_DIR, find_template
from app.lookup.types import (CANCELLED, EMPTY, ERROR, OK, RUNNING, SKIPPED, SOURCES, TIMEOUT, CharacterCandidate,
                              GameCandidate, GameIdentity, LookupReport, PatchLink, SourceStatus, TermCandidate)

log = logging.getLogger(__name__)

BUDGET_S = 30.0
SOURCE_TIMEOUT_S = 10.0
MAX_CHARACTERS = 60
WIKI_ONLY_BELOW = 5  # with fewer characters from VNDB + Wikidata, the wiki's own characters are added
LICENSES = {
    "steam": "Dữ liệu cửa hàng Steam, chỉ dùng cho cá nhân",
    "wikidata": "CC0 1.0",
    "vndb": "ODbL 1.0 (vndb.org)",
    "wiki": "CC BY-SA (nội dung wiki; app chỉ lấy tên)",
}
ProgressCallback = Callable[[SourceStatus], None]


@dataclass
class _Found:
    steam: steam.SteamInfo | None = None
    wd: wikidata.WikidataGame | None = None
    vn: vndb.VnInfo | None = None
    site: mediawiki.WikiSite | None = None
    wiki_articles: list[str] | None = None
    web_apis: list[str] | None = None


Job = Callable[["_Run", Session], "tuple[dict[str, int], Callable[[], None]]"]


class _Run:
    """State of one run. Only the thread that called run() changes it: a source job returns its counts
    and an `apply` closure, applied when the job finished in time (a late job changes nothing)."""

    def __init__(self, service: "LookupService", report: LookupReport, sources: tuple[str, ...],
                 progress: ProgressCallback | None, cancel: threading.Event | None):
        self.service = service
        self.report = report
        self.sources = sources
        self.progress = progress
        self.cancel = cancel or threading.Event()
        self.start = time.monotonic()
        self.deadline = self.start + service.budget_s
        self.sessions: dict[str, Session] = {}
        self.statuses: dict[str, SourceStatus] = {}
        self.found = _Found()
        self.wd_chars: list[CharacterCandidate] = []
        self.wiki_chars: list[CharacterCandidate] = []
        self.spoilers: list[CharacterCandidate] = []  # VNDB's spoiler characters: never shown, used as a filter
        self.derived: dict[str, str] = {}  # source -> the source whose id or title found it (no independent agreement)

    def session(self, name: str) -> Session:
        if name not in self.sessions:
            self.sessions[name] = self.service.client.session(name, deadline=self.deadline, cancel=self.cancel,
                                                              timeout=self.service.source_timeout_s)
        return self.sessions[name]

    def status(self, name: str) -> SourceStatus:
        if name not in self.statuses:
            self.statuses[name] = SourceStatus(name)
            self.report.statuses.append(self.statuses[name])
        return self.statuses[name]

    def emit(self, st: SourceStatus) -> None:
        if self.progress is not None:
            try:
                self.progress(st)
            except Exception:
                log.warning("lookup progress callback failed", exc_info=True)

    def left(self) -> float:
        return self.deadline - time.monotonic()


class LookupService:
    def __init__(self, *, client: HttpClient | None = None, cache_dir: Path | None = CACHE_DIR,
                 searxng_service: Any = None, searxng_url: str = "", searxng_public_fallback: bool = False,
                 web_search: web.SearchFn | None = None, templates_dir: Path = TEMPLATES_DIR,
                 budget_s: float = BUDGET_S, source_timeout_s: float = SOURCE_TIMEOUT_S,
                 max_characters: int = MAX_CHARACTERS, sources: tuple[str, ...] = SOURCES) -> None:
        self._own_client = client is None
        self.client = client or HttpClient(cache_dir=cache_dir, timeout=source_timeout_s)
        self.searxng_service = searxng_service
        self.searxng_url = searxng_url
        self.searxng_public_fallback = searxng_public_fallback
        self.web_search = web_search
        self.templates_dir = templates_dir
        self.budget_s = budget_s
        self.source_timeout_s = source_timeout_s
        self.max_characters = max_characters
        self.sources = tuple(s for s in sources if s in SOURCES)

    @classmethod
    def from_config(cls, config: Any, **kw) -> "LookupService":
        kw.setdefault("sources", tuple(getattr(config, "lookup_sources", SOURCES) or SOURCES))
        kw.setdefault("searxng_url", getattr(config, "searxng_url", "") or "")
        kw.setdefault("searxng_public_fallback", bool(getattr(config, "searxng_public_fallback", False)))
        return cls(**kw)

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    # --- the run ---------------------------------------------------------------------------

    def run(self, identity: GameIdentity | str, sources: list[str] | tuple[str, ...] | None = None,
            progress_cb: ProgressCallback | None = None, cancel_event: threading.Event | None = None, *,
            use_templates: bool = True) -> LookupReport:
        ident = identity if isinstance(identity, GameIdentity) else identity_from_title(str(identity))
        titles = ident.query_titles()
        report = LookupReport(query=titles[0] if titles else "", identity=ident)
        wanted = tuple(s for s in (sources if sources is not None else self.sources) if s in SOURCES)
        r = _Run(self, report, wanted, progress_cb, cancel_event)
        try:
            if use_templates and self._template(r):
                return report
            if not titles:
                report.notes.append("Không có tên game để tra cứu.")
                return report
            self._phase(r, {"steam": self._steam, "wikidata": self._wikidata, "vndb": self._vndb})
            self._cross_check(r)
            self._choose(r)
            if report.title and report.confidence == "high" and not r.cancel.is_set():
                self._phase(r, {"vndb": self._vndb_details, "wikidata": self._wikidata_details, "web": self._web})
                self._phase(r, {"wiki": self._wiki})
                self._merge(r)
        finally:
            for name in SOURCES:
                if name not in wanted:
                    st = r.status(name)
                    st.state, st.message = SKIPPED, "không bật"
                elif name not in r.statuses and not report.template:
                    st = r.status(name)  # enabled, never reached
                    st.state = CANCELLED if r.cancel.is_set() else SKIPPED
                    st.message = "đã hủy" if r.cancel.is_set() else "không tìm thấy game" if not report.title \
                        else "chờ bạn chọn đúng game" if report.confidence != "high" else ""
            if r.cancel.is_set():
                for st in report.statuses:
                    if st.state == RUNNING:
                        st.state, st.message = CANCELLED, "đã hủy"
            report.cancelled = r.cancel.is_set()
            report.elapsed_s = round(time.monotonic() - r.start, 2)
        return report

    def _template(self, r: _Run) -> bool:
        match = find_template(r.report.identity, self.templates_dir)
        if match is None:
            return False
        rep = r.report
        rep.template, rep.title, rep.src_lang, rep.confidence = str(match.path), match.profile.name, \
            match.profile.src_lang, "high"
        st = r.status("template")
        st.state, st.message = OK, f"{match.path.name} ({match.reason})"
        st.found = {"characters": len(match.profile.characters), "terms": len(match.profile.terms)}
        rep.notes.append(f"Đã có hồ sơ mẫu “{match.profile.name}” đi kèm app; không cần tra cứu trên mạng.")
        r.emit(st)
        return True

    def _phase(self, r: _Run, jobs: dict[str, Job]) -> None:
        """Run the enabled `jobs` in parallel until they finish, the budget runs out or the run is cancelled.
        Each job has its own daemon thread: one still waiting for a server when the run returns finishes (or
        fails) in the background, changes nothing, and never keeps the app from exiting."""
        jobs = {k: v for k, v in jobs.items() if k in r.sources}
        if not jobs or r.cancel.is_set():
            return
        if r.left() < 1.0:
            for name in jobs:
                st = r.status(name)
                if st.state == RUNNING or (st.state in (OK, EMPTY) and not st.found):
                    st.state, st.message = TIMEOUT, "hết thời gian tra cứu"
            return
        futures: dict[Future, str] = {}
        t0 = {}
        for name, fn in jobs.items():
            st = r.status(name)
            prev = st.state
            st.state = RUNNING
            r.emit(st)
            t0[name] = (time.monotonic(), prev)
            fut: Future = Future()
            threading.Thread(target=_run_job, args=(fut, fn, r, r.session(name)), name=f"lookup-{name}",
                             daemon=True).start()
            futures[fut] = name
        pending = set(futures)
        end = time.monotonic() + max(0.5, r.left())
        while pending and not r.cancel.is_set():  # short slices: a cancel is seen within 0.2 s
            left = end - time.monotonic()
            if left <= 0:
                break
            _done, pending = wait(pending, timeout=min(0.2, left))
        for fut, name in futures.items():
            st = r.status(name)
            started, prev = t0[name]
            st.elapsed_s = round(st.elapsed_s + time.monotonic() - started, 2)
            st.requests, st.cached = r.session(name).counts()
            if fut in pending:
                if r.cancel.is_set():
                    st.state, st.message = CANCELLED, "đã hủy"
                else:
                    st.state, st.message = TIMEOUT, "quá thời gian, dùng kết quả của các nguồn khác"
            else:
                try:
                    found, apply = fut.result()
                    apply()
                    for k, v in (found or {}).items():
                        st.found[k] = st.found.get(k, 0) + v
                    st.state = OK if any(st.found.values()) else (prev if prev in (ERROR, TIMEOUT) else EMPTY)
                except LookupCancelled as e:
                    st.state = CANCELLED if r.cancel.is_set() else TIMEOUT
                    st.message = "đã hủy" if r.cancel.is_set() else f"hết thời gian ({e})"
                except LookupHttpError as e:
                    st.state, st.message = ERROR, str(e)
                    log.info("lookup %s: %s (%s)", name, e, e.url)
                except Exception as e:  # a parser bug must not lose the other sources
                    st.state, st.message = ERROR, f"lỗi: {type(e).__name__}"
                    log.warning("lookup %s failed", name, exc_info=True)
            r.emit(st)

    # --- phase 1: which game ---------------------------------------------------------------

    def _titles(self, r: _Run) -> list[str]:
        return r.report.identity.query_titles() if r.report.identity else [r.report.query]

    def _steam(self, r: _Run, s: Session):
        info = steam.lookup(s, self._titles(r), r.report.identity.steam_appid)
        return {"games": int(info is not None)}, lambda: setattr(r.found, "steam", info)

    def _wikidata(self, r: _Run, s: Session):
        appid = r.report.identity.steam_appid
        game = wikidata.lookup(s, self._titles(r), steam_appid=str(appid) if appid else None)
        return {"games": int(game is not None)}, lambda: setattr(r.found, "wd", game)

    def _vndb(self, r: _Run, s: Session):
        vn = vndb.lookup(s, self._titles(r))
        return {"games": int(vn is not None)}, lambda: setattr(r.found, "vn", vn)

    def _cross_check(self, r: _Run) -> None:
        """Follow the ids the sources give each other (cheap, one request each at most)."""
        f = r.found

        def attempt(name: str, fn: Callable[[Session], Any]) -> Any:
            if name not in r.sources or r.cancel.is_set() or r.left() < 1.0:
                return None
            st = r.status(name)
            t = time.monotonic()
            try:
                return fn(r.session(name))
            except (LookupHttpError, LookupCancelled) as e:
                log.info("lookup %s cross-check: %s", name, e)
                return None
            except Exception:
                log.warning("lookup %s cross-check failed", name, exc_info=True)
                return None
            finally:
                st.elapsed_s = round(st.elapsed_s + time.monotonic() - t, 2)
                st.requests, st.cached = r.session(name).counts()

        def found_by(name: str, value: Any, origin: str) -> Any:
            if value is not None:
                r.derived[name] = origin  # it agrees with `origin` by construction: no confirmation
            return value

        if f.wd is None and f.vn is not None and f.vn.wikidata:
            f.wd = found_by("wikidata", attempt("wikidata", lambda s: wikidata.lookup(s, [], qid=f.vn.wikidata)), "vndb")
        if f.wd is None and f.steam is not None and f.steam.score >= 0.9:
            f.wd = found_by("wikidata", attempt("wikidata", lambda s: wikidata.lookup(
                s, [], steam_appid=str(f.steam.appid))), "steam")
        if f.wd is not None and f.wd.vndb:
            if f.vn is None or (f.vn.id not in f.wd.vndb and f.vn.score < 0.99):
                f.vn = found_by("vndb", attempt("vndb", lambda s: vndb.get_vn(s, f.wd.vndb[0])), "wikidata") or f.vn
        if f.wd is not None and f.wd.steam:
            if f.steam is None or (str(f.steam.appid) not in f.wd.steam and f.steam.score < 0.99):
                try:
                    appid = int(f.wd.steam[0])
                except ValueError:
                    appid = 0
                if appid:
                    f.steam = found_by("steam", attempt("steam", lambda s: steam.lookup(s, [], appid)), "wikidata") \
                        or f.steam
        if f.steam is None and (f.wd is not None or f.vn is not None):
            # A Japanese title Steam does not know: once more with the English title another source found
            # ("ひぐらしのなく頃に" -> "Higurashi When They Cry"), accepted only when it is that title.
            alt = (f.wd.labels.get("en", "") if f.wd else "") or (f.vn.title_in("en") or f.vn.title if f.vn else "")
            if alt and norm_title(alt) not in {norm_title(t) for t in self._titles(r)}:
                f.steam = found_by("steam", attempt("steam", lambda s: steam.lookup(s, [alt], min_score=0.9)),
                                   "wikidata" if f.wd is not None and f.wd.labels.get("en") else "vndb")
        for name, value in (("steam", f.steam), ("wikidata", f.wd), ("vndb", f.vn)):
            st = r.statuses.get(name)
            if st is not None and value is not None and st.state in (EMPTY, ERROR, TIMEOUT):
                st.state, st.found = OK, {**st.found, "games": 1}

    def _choose(self, r: _Run) -> None:
        f, rep = r.found, r.report
        query = rep.query
        cands = [c for c in (f.steam.candidate() if f.steam else None, f.wd.candidate() if f.wd else None,
                             f.vn.candidate() if f.vn else None) if c is not None]
        if not cands:
            rep.notes.append("Không tìm thấy game này trên Steam, Wikidata hay VNDB.")
            return
        def linked(a: str, b: str) -> bool:  # the two sources' ids name the same game (Wikidata holds the links)
            pair = {a, b}
            if pair == {"wikidata", "steam"}:
                return bool(f.wd and f.steam and str(f.steam.appid) in f.wd.steam)
            if pair == {"wikidata", "vndb"}:
                return bool(f.wd and f.vn and (f.vn.id in f.wd.vndb or f.vn.wikidata == f.wd.qid))
            return False

        # Agreement: two sources that found the game on their own and point at each other is stronger than any title
        # match ("Persona" -> Steam's "Persona 6" -> its Wikidata item by that Steam id confirms nothing).
        def independent(a: str, b: str) -> bool:
            return r.derived.get(a) != b and r.derived.get(b) != a

        agree = sum(1 for other in ("steam", "vndb") if linked("wikidata", other) and independent("wikidata", other))
        by_source = {c.source: c for c in cands}
        for _ in range(2):  # a source found through another one is only as sure as that one's title match
            for c in cands:
                origin = by_source.get(r.derived.get(c.source, ""))
                if origin is not None:
                    c.score = min(c.score, origin.score)
        best_c = by_source["wikidata"] if agree else max(cands, key=lambda c: c.score)
        # The game is the agreeing sources (else the best match) and every source linked to them by id; a source
        # that found something else ("human" next to "Persona 6") only becomes an alternative, lending no title or id.
        cluster = {best_c.source}
        for _ in range(2):
            cluster |= {c.source for c in cands if any(linked(c.source, x) for x in cluster)}
        dropped = [c for c in cands if c.source not in cluster and title_similarity(c.title, best_c.title) < 0.85]
        for c in dropped:
            setattr(f, {"steam": "steam", "wikidata": "wd", "vndb": "vn"}[c.source], None)
        cands = [c for c in cands if c not in dropped]
        best = max(c.score for c in cands)
        rep.confidence = "high" if agree or best >= 0.99 else "medium" if best >= 0.85 else "low"
        rep.candidates = cands
        rep.ids = {k: v for c in cands for k, v in c.ids.items()}
        if f.wd and f.wd.fandom:
            rep.ids["fandom"] = f.wd.fandom[0]
        if agree or len(cluster) > 1:  # sources linked by id: one game, Wikidata's English label first
            rep.title = (f.wd.labels.get("en") if f.wd else "") or (f.vn.title_in("en") if f.vn else "") \
                or (f.vn.title if f.vn else "") or (f.steam.name if f.steam else "") or query
        else:  # the title of the best match
            rep.title = ((f.vn.title_in("en") or f.vn.title) if best_c.source == "vndb" and f.vn else "") \
                or best_c.title or query
        ja = [(f.vn.title_in("ja") if f.vn else ""), (f.wd.labels.get("ja", "") if f.wd else ""),
              (f.steam.name_ja if f.steam else ""), (f.vn.alttitle if f.vn else "")]
        rep.title_ja = next((t for t in ja if t and is_cjk(t)), next((t for t in ja if t), ""))
        vi = [(f.wd.labels.get("vi", "") if f.wd else ""), (f.vn.title_in("vi") if f.vn else "")]
        rep.title_vi = next((t for t in vi if t and norm_title(t) != norm_title(rep.title)), "")
        rep.src_lang = (f.vn.olang if f.vn and f.vn.olang else "") or (f.wd.original_language if f.wd else "") \
            or (f.wd.country_language if f.wd else "") or None
        chosen = {norm_title(rep.title)} | {v for c in cands for v in c.ids.values()}
        alts: list[GameCandidate] = []
        for a in dropped + [a for src in (f.steam, f.wd, f.vn) for a in getattr(src, "alternatives", []) or []]:
            if norm_title(a.title) in chosen or any(v in chosen for v in a.ids.values()):
                continue
            chosen.add(norm_title(a.title))
            alts.append(a)
        alts.sort(key=lambda a: -a.score)
        rep.alternatives = alts[:5] if rep.confidence != "high" or best < 0.99 else alts[:3]
        # Vietnamese: Steam lists the official languages, else Wikidata's language of work (P407: Genshin Impact
        # is not on Steam); VNDB releases are checked in phase 2.
        if f.steam is not None:
            rep.vi_official = f.steam.vietnamese
            rep.vi_note = "Steam ghi tiếng Việt là ngôn ngữ chính thức của game." if f.steam.vietnamese else \
                "Steam không ghi tiếng Việt trong các ngôn ngữ chính thức."
        elif f.wd is not None and "vi" in f.wd.languages:
            rep.vi_official = True
            rep.vi_note = "Wikidata ghi game có tiếng Việt chính thức."
        if rep.vi_official:
            rep.vi_note += " Đổi ngôn ngữ trong game sang tiếng Việt có thể tốt hơn dịch màn hình."
        if rep.confidence != "high":
            rep.notes.append(f"Chưa chắc đây đúng là “{rep.title}”: chọn đúng game rồi bấm “Tra cứu game này”. "
                             "Chưa có hồ sơ nháp nào được dựng.")

    # --- phase 2: details ------------------------------------------------------------------

    def _vndb_details(self, r: _Run, s: Session):
        vn = r.found.vn
        if vn is None:
            return {}, lambda: None
        chars, hidden = vndb.characters_and_spoilers(s, vn.id)
        patches = vndb.vi_releases(s, vn.id) if "vi" in vn.languages or not vn.languages else []

        def apply() -> None:
            r.report.characters += chars  # merged with the others in _merge
            r.report.patches += patches
            r.spoilers += hidden
        return {"characters": len(chars), "patches": len(patches)}, apply

    def _wikidata_details(self, r: _Run, s: Session):
        wd = r.found.wd
        if wd is None:
            return {}, lambda: None
        chars = wikidata.characters(s, wd.characters, src_lang=r.report.src_lang) if wd.characters else []
        places = wikidata.places(s, wd.places) if wd.places and r.left() > 3 else []
        articles = [c.ids.get("fandom", "") for c in chars if c.ids.get("fandom")]
        wid, arts = mediawiki.pick_fandom_wiki(r.report.title, wd.fandom, articles)

        def apply() -> None:
            r.wd_chars += chars
            r.report.terms += places
            if wid:
                r.found.wiki_articles = arts
                r.report.ids["fandom"] = wid
        return {"characters": len(chars), "terms": len(places)}, apply

    def _web(self, r: _Run, s: Session):
        search = self.web_search
        notes: list[str] = []
        if r.cancel.is_set():
            raise LookupCancelled("cancelled")
        # An official Vietnamese version (Steam / Wikidata): patch pages would be guides to switch the language.
        want_patches = not r.report.vi_official
        need_wiki = "wiki" in r.sources and not (r.found.wd and (r.found.wd.fandom or r.found.wd.characters))
        if not want_patches and not need_wiki:
            return {}, lambda: None  # nothing to search for: SearXNG is not even started
        if search is None:
            base, kind = web.resolve_base_url(service=self.searxng_service, url=self.searxng_url,
                                              public_fallback=self.searxng_public_fallback,
                                              timeout=min(20.0, max(1.0, r.left() - 5)))
            if not base:
                raise LookupHttpError(kind)
            if kind == "public":
                notes.append(f"Tìm web qua máy SearXNG công cộng {base} (máy đó thấy tên game và IP của bạn).")
            elif kind == "external":
                notes.append(f"Tìm web qua SearXNG ở {base}.")
            search = web.make_search(base, timeout=min(self.source_timeout_s, max(1.0, r.left())))
        inner = search

        def search(query: str, language: str) -> list:  # nothing more is sent once the run is cancelled / over
            if r.cancel.is_set():
                raise LookupCancelled("cancelled")
            if r.left() < 0.5:
                raise LookupCancelled("out of time")
            return inner(query, language)
        title = r.report.title or r.report.query
        patches = web.vietnamese_patches(search, title) if want_patches else []
        apis: list[str] = []
        if need_wiki and r.left() > 4:
            apis = web.discover_wikis(search, title)

        def apply() -> None:
            r.report.patches += patches
            r.report.notes += notes
            r.found.web_apis = apis
        return {"patches": len(patches), "wikis": len(apis)}, apply

    # --- phase 3: the game's wiki ------------------------------------------------------------

    def _wiki(self, r: _Run, s: Session):
        rep, f = r.report, r.found
        apis: list[str] = []
        if rep.ids.get("fandom"):
            apis.append(mediawiki.fandom_api(rep.ids["fandom"]))
        apis += f.web_apis or []
        site: mediawiki.WikiSite | None = None
        tried: set[str] = set()
        # Every wiki (Wikidata's, a web hit's or a guessed one) must be about this game or its series: a web hit
        # for "Ever17" is the Memories Off wiki, Wikidata's for Persona 5 Royal the whole Megami Tensei one.
        candidates = [a for a in dict.fromkeys(apis) if a][:2] + \
            [a for a in (mediawiki.fandom_api(w) for w in mediawiki.guess_fandom_ids(rep.title)[:3]) if a]
        for api in dict.fromkeys(candidates):
            if api in tried or r.cancel.is_set():
                continue
            tried.add(api)
            try:
                cand = mediawiki.site_info(s, api)
            except LookupHttpError:
                continue
            if cand is not None and mediawiki.site_matches(rep.title, cand):
                site = cand
                break
            log.info("lookup wiki: %s is not about %r; not used", cand.name if cand else api, rep.title)
        if site is None:
            return {}, lambda: None
        lang = rep.src_lang if rep.src_lang in ("ja", "en") else None
        series = bool(mediawiki.franchise_words(rep.title, site))  # a series wiki: its places are every game's
        chars = mediawiki.characters(s, site, titles=f.wiki_articles or [], src_lang=lang, game=rep.title)
        terms = mediawiki.terms(s, site) if lang == "ja" and not series and r.left() > 4 else []

        def apply() -> None:
            f.site = site
            rep.ids.setdefault("wiki", site.base or site.api)
            r.wiki_chars += chars
            rep.terms += terms
        return {"characters": len(chars), "terms": len(terms)}, apply

    # --- merge -----------------------------------------------------------------------------

    def _merge(self, r: _Run) -> None:
        rep, f = r.report, r.found
        cjk = rep.src_lang in ("ja", None)
        vn_chars = [c for c in rep.characters if "vndb" in c.sources]  # VNDB's, spoilers already left out
        hidden = _SpoilerKeys.of(r.spoilers, vn_chars)
        vn_chars = hidden.clean(vn_chars)  # their aliases naming a spoiler character
        others = hidden.clean(r.wd_chars), hidden.clean(r.wiki_chars)
        if vn_chars:  # VNDB knows the cast (and its spoilers): Wikidata and the wiki only add details to it
            rep.characters = merge_characters(vn_chars, limit=self.max_characters, enrich=others[0] + others[1],
                                              prefer_cjk=cjk)
        else:
            structured = merge_characters(others[0], limit=10_000, prefer_cjk=cjk)
            if len(structured) >= WIKI_ONLY_BELOW:  # Wikidata knows the cast: the wiki only fills gaps
                rep.characters = merge_characters(structured, limit=self.max_characters, enrich=others[1],
                                                  prefer_cjk=cjk)
            else:
                rep.characters = merge_characters(structured, others[1], limit=self.max_characters, prefer_cjk=cjk)
        if hidden:
            rep.characters = hidden.clean(rep.characters)  # aliases the enrichment brought in
        if rep.src_lang != "ja":  # an English game's text has no kanji aliases
            for c in rep.characters:
                c.aliases = [(o, ro) for o, ro in c.aliases if not is_cjk(o)]
        terms: list[TermCandidate] = []
        if rep.title_ja and is_cjk(rep.title_ja) and rep.src_lang == "ja" and rep.title:
            terms.append(TermCandidate(rep.title_ja, rep.title, "term", note="tên game", sources=["title"]))
        rep.terms = merge_terms(terms + rep.terms, rep.characters)
        rep.patches = merge_patches(rep.patches)
        if any(p.official for p in rep.patches if p.source == "vndb"):
            rep.vi_official = True
            rep.vi_note = "VNDB có bản phát hành tiếng Việt chính thức. Đổi ngôn ngữ trong game sang tiếng Việt có " \
                          "thể tốt hơn dịch màn hình."
        for name, url in (("steam", f.steam.url if f.steam else ""), ("wikidata", f.wd.url if f.wd else ""),
                          ("vndb", f.vn.url if f.vn else ""), ("wiki", f.site.base if f.site else "")):
            if url:
                label = f.site.name if name == "wiki" and f.site else {"steam": "Steam", "wikidata": "Wikidata",
                                                                         "vndb": "VNDB"}.get(name, name)
                rep.attribution.append({"source": label, "url": url, "license": LICENSES[name]})


def _run_job(fut: Future, fn: Job, r: _Run, session: Session) -> None:
    try:
        fut.set_result(fn(r, session))
    except BaseException as e:  # noqa: BLE001 - handed to the run thread through the future
        fut.set_exception(e)


# --- spoilers ------------------------------------------------------------------------------------------

@dataclass
class _SpoilerKeys:
    names: set[str]
    latins: set[str]
    ids: set[str]

    @classmethod
    def of(cls, hidden: list[CharacterCandidate], visible: list[CharacterCandidate] = ()) -> "_SpoilerKeys":
        """The spoiler characters' own names (not their aliases: Shion's alias "Mion" is another character), minus
        the names of characters VNDB shows (its spoiler "variant" entries share the name: another worldline's
        Okabe is c80100, the Okabe everyone meets c6498)."""
        shown = {name_key(c.original) for c in visible}
        shown_latin = {latin_key(c.romanized) for c in visible}
        names = {name_key(c.original) for c in hidden} - {""} - shown
        latins = {k for k in (latin_key(c.romanized) for c in hidden) if len(k) > 4} - shown_latin
        ids = {c.ids.get("vndb", "") for c in hidden} - {""}
        return cls(names, latins, ids)

    def __bool__(self) -> bool:
        return bool(self.names or self.latins or self.ids)

    def name(self, original: str, romanized: str = "") -> bool:
        lk = latin_key(romanized)
        return (bool(original) and name_key(original) in self.names) or (len(lk) > 4 and lk in self.latins)

    def character(self, c: CharacterCandidate) -> bool:
        return self.name(c.original, c.romanized) or bool(c.ids.get("vndb") and c.ids["vndb"] in self.ids)

    def clean(self, chars: list[CharacterCandidate]) -> list[CharacterCandidate]:
        """`chars` without the spoiler characters, and without the aliases naming one."""
        out = [c for c in chars if not self.character(c)]
        for c in out:
            c.aliases = [(o, ro) for o, ro in c.aliases if not self.name(o, ro)]
        return out


# --- merging (module level: also used by the builder and tests) ------------------------------------

def _alias_keys(c: CharacterCandidate) -> tuple[set[str], set[str]]:
    originals = {name_key(o) for o, _ in c.aliases if o} - {""}
    latins = {latin_key(r) for _, r in c.aliases if r} - {""}
    return originals, {k for k in latins if len(k) > 4}


_TITLE_AFFIXES = re.compile(r"^(ドクター|ミスター|ミス|プロフェッサー)|(博士|教授|先生|さん|くん|君|ちゃん|様|さま|殿)$")


def _core(original: str) -> str:
    """A name without a title word: 中鉢博士 / ドクター中鉢 -> 中鉢."""
    key = name_key(original)
    core = _TITLE_AFFIXES.sub("", key)
    return core if len(core) >= 2 and is_cjk(core) else key


def _same_character(a: CharacterCandidate, b: CharacterCandidate) -> bool:
    if name_key(a.original) and name_key(a.original) == name_key(b.original):
        return True
    if is_cjk(a.original) and is_cjk(b.original) and _core(a.original) == _core(b.original) \
            and (_core(a.original) != name_key(a.original) or _core(b.original) != name_key(b.original)):
        return True  # 中鉢博士 (Wikidata) = ドクター中鉢 (VNDB)
    for k in ("vndb", "wikidata"):
        if a.ids.get(k) and a.ids.get(k) == b.ids.get(k):
            return True
    la, lb = latin_key(a.romanized), latin_key(b.romanized)
    if la and la == lb and (not is_cjk(a.original) or not is_cjk(b.original) or len(la) > 6):
        return True
    # One source's name is another's alias (Wikidata フェイリス・ニャンニャン = VNDB 秋葉 留未穂 "Faris NyanNyan").
    for x, y in ((a, b), (b, a)):
        originals, latins = _alias_keys(x)
        if name_key(y.original) in originals or (len(latin_key(y.romanized)) > 4 and latin_key(y.romanized) in latins):
            return True
    return False


def _add_alias(c: CharacterCandidate, original: str, romanized: str) -> None:
    alias = (original, romanized) if is_cjk(original) else ("", original or romanized)
    key = name_key(alias[0]) or latin_key(alias[1])
    if key and key not in {name_key(o) or latin_key(r) for o, r in c.aliases} and key != name_key(c.original):
        c.aliases.append(alias)


def _absorb(into: CharacterCandidate, other: CharacterCandidate, prefer_cjk: bool = True) -> None:
    """Fill `into` from `other`; the name the game's text uses (Japanese script for a Japanese game,
    Latin otherwise) becomes `original`, other spellings become aliases."""
    if name_key(other.original) != name_key(into.original):
        if is_cjk(other.original) != is_cjk(into.original) and is_cjk(other.original) == prefer_cjk:
            _add_alias(into, into.original, into.romanized)
            into.original, into.native = other.original, other.native
        else:
            _add_alias(into, other.original, other.romanized)
    else:
        into.native = into.native or other.native
    if not into.romanized:
        into.romanized = other.romanized
    if not into.parts and other.parts:
        into.parts = list(other.parts)
    if not into.gender:
        into.gender = other.gender
    if not into.role:
        into.role = other.role
    if not into.label_vi:
        into.label_vi = other.label_vi
    for o, ro in other.aliases:
        if o:
            _add_alias(into, o, ro)
        else:
            _add_alias(into, ro, "")
    for s in other.sources:
        if s not in into.sources:
            into.sources.append(s)
    for k, v in other.ids.items():
        into.ids.setdefault(k, v)


def merge_characters(*groups: list[CharacterCandidate], limit: int = MAX_CHARACTERS,
                     enrich: list[CharacterCandidate] = (), prefer_cjk: bool = True) -> list[CharacterCandidate]:
    """One entry per character across sources (first group wins a conflict: VNDB, then Wikidata, then
    the wiki), main characters first, at most `limit`. Entries of `enrich` only add to characters
    already found (a franchise wiki lists the sequels' characters too). `prefer_cjk`: the game's text
    is Japanese, so a Japanese-script name wins over a romanized one (else the other way round)."""
    out: list[CharacterCandidate] = []
    for group, may_add in [(g, True) for g in groups] + [(enrich, False)]:
        for c in group or []:
            if not c.original:
                continue
            twin = next((o for o in out if _same_character(o, c)), None)
            if twin is not None:
                _absorb(twin, c, prefer_cjk)
            elif may_add:
                out.append(CharacterCandidate(**{**c.__dict__, "parts": list(c.parts), "aliases": list(c.aliases),
                                                 "sources": list(c.sources), "ids": dict(c.ids)}))
    order = {r: i for i, r in enumerate(("main", "primary", "side", "appears"))}
    out.sort(key=lambda c: (order.get(c.role, 4 if len(c.sources) > 1 else 5)))
    return out[:limit]


def merge_terms(terms: list[TermCandidate], characters: list[CharacterCandidate] = ()) -> list[TermCandidate]:
    taken = {name_key(c.original) for c in characters}
    out: dict[str, TermCandidate] = {}
    for t in terms:
        key = name_key(t.source)
        if not key or not t.target or key in taken:
            continue
        if key in out:
            for s in t.sources:
                if s not in out[key].sources:
                    out[key].sources.append(s)
            continue
        out[key] = TermCandidate(t.source, t.target, t.kind, t.note, list(t.sources), t.auto)
    return list(out.values())


def merge_patches(patches: list[PatchLink]) -> list[PatchLink]:
    out: dict[str, PatchLink] = {}
    for p in patches:
        key = web.url_key(p.url)
        if key and key not in out:
            out[key] = p
    return sorted(out.values(), key=lambda p: (not p.official, not p.verified))


__all__ = ["LookupService", "ProgressCallback", "merge_characters", "merge_terms", "merge_patches"]
