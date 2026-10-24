"""Look a game up the way the app does, and print what each source found.

    python -m tools.lookup_game "Steins;Gate"
    python -m tools.lookup_game --exe "D:\\SteamLibrary\\steamapps\\common\\STEINS;GATE\\STEINS;GATE.exe"
    python -m tools.lookup_game "Doki Doki Literature Club" --sources vndb,wikidata --json
    python -m tools.lookup_game "Steins;Gate" --profile-out data/sg.json # the draft profile
    python -m tools.lookup_game "Steins;Gate" --request                  # the "Tạo yêu cầu tra cứu" prompt
    python -m tools.lookup_game "Steins;Gate" --record tests/data/lookup/steins_gate   # trimmed fixtures
    python -m tools.lookup_game "Steins;Gate" --replay tests/data/lookup/steins_gate   # offline

Network goes to the same places as the app (Steam, Wikidata, VNDB, the game's wiki, SearXNG for "web"),
with only the title in the queries. --exe reads files only (version resource, Steam manifest).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.lookup.game_id import identify, identity_from_title
from app.lookup.http import CACHE_DIR, FixtureRecorder, FixtureTransport, HttpClient
from app.lookup.profile_builder import build_profile, describe
from app.lookup.service import LookupService
from app.lookup.types import SOURCES, LookupReport
from app.profiles import profile_to_json

# Claims the parsers read (app.lookup.sources.wikidata): everything else is dropped from fixtures.
_WD_PROPS = {"P31", "P674", "P1733", "P3180", "P4073", "P6262", "P407", "P364", "P495", "P21", "P1449", "P840",
             "P1434", "P577"}
_STEAM_KEYS = {"name", "type", "steam_appid", "supported_languages", "developers", "publishers", "release_date"}
_NIHONGO_TPL = re.compile(r"\{\{\s*nihongo\s*\|[^{}]*\}\}", re.IGNORECASE)
_TPL_OPENER = re.compile(r"\{\{[^{}|]*$")  # "{{Character", "{{Tabs"
SNIPPET_CHARS = 60  # search-hit snippets kept in fixtures (nothing reads them; title and url are what tests use)


def _trim_lead(text: str) -> str:
    """A wiki page's lead section cut to what mediawiki.parse_lead reads: `|param` lines, bare template
    openers, `}}` and {{Nihongo|...}} templates. The lead prose and every {{Quote|...}} are dropped (wiki
    prose is CC BY-SA, quoted lines are the game's)."""
    out = []
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith("|"):
            out.append(line)  # infobox parameters
            continue
        opener = _TPL_OPENER.match(s)
        head = "}}" if s.startswith("}}") else (opener.group(0) if opener else "")
        nihongo = "".join(_NIHONGO_TPL.findall(line))
        if head or nihongo:
            out.append(head + nihongo)
    return "\n".join(out)[:4000]


def trim_snippet(text: Any) -> Any:
    """A search hit's page snippet cut to SNIPPET_CHARS (third-party page text is not kept in fixtures)."""
    if not isinstance(text, str) or len(text) <= SNIPPET_CHARS:
        return text
    return text[:SNIPPET_CHARS - 1].rstrip() + "…"


def trim_search_hits(hits: Any) -> Any:
    """SearXNG results (or recorded web hits): `content` snippets shortened, title / url / engine kept."""
    for hit in hits if isinstance(hits, list) else []:
        if isinstance(hit, dict) and "content" in hit:
            hit["content"] = trim_snippet(hit["content"])
    return hits


def trim_fixture(url: str, data: Any) -> Any:
    """Shrink a recorded answer to what the parsers use (fixtures stay small and readable)."""
    if "wikidata.org" in url and isinstance(data, dict) and "entities" in data:
        for e in data["entities"].values():
            if not isinstance(e, dict):
                continue
            e.pop("sitelinks", None)
            e.pop("aliases", None)
            claims = e.get("claims") or {}
            e["claims"] = {p: [{"mainsnak": {"datavalue": {"value": ((c.get("mainsnak") or {}).get("datavalue") or {})
                                                           .get("value")}}, "rank": c.get("rank", "normal")}
                               for c in v] for p, v in claims.items() if p in _WD_PROPS}
            for k in list(e):
                if k not in ("id", "type", "labels", "descriptions", "claims", "missing"):
                    e.pop(k)
    elif "store.steampowered.com/api/appdetails" in url and isinstance(data, dict):
        for entry in data.values():
            if isinstance(entry, dict) and isinstance(entry.get("data"), dict):
                entry["data"] = {k: v for k, v in entry["data"].items() if k in _STEAM_KEYS}
    elif "api.vndb.org" in url and isinstance(data, dict):
        for item in data.get("results") or []:
            if isinstance(item, dict) and "extlinks" in item and "/release" not in url:
                item["extlinks"] = [x for x in item["extlinks"] if (x or {}).get("name") == "wikidata"]
            if isinstance(item, dict) and item.get("notes"):
                item["notes"] = ""
    elif "/api.php" in url and isinstance(data, dict):
        for page in ((data.get("query") or {}).get("pages") or {}).values():
            for rev in page.get("revisions") or []:
                main = (rev.get("slots") or {}).get("main") or {}
                if "*" in main:
                    main["*"] = _trim_lead(main["*"])
                    main.pop("contentmodel", None)
                    main.pop("contentformat", None)
        general = (data.get("query") or {}).get("general")
        if isinstance(general, dict):
            data["query"]["general"] = {k: general[k] for k in ("sitename", "server", "articlepath", "wikiid", "lang")
                                        if k in general}
    elif urlsplit(url).path.rstrip("/").endswith("/search") and isinstance(data, dict):  # a SearXNG answer
        trim_search_hits(data.get("results"))
    return data


def _short(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def print_report(report: LookupReport, *, limit: int = 25, out=sys.stdout) -> None:
    w = out.write
    ident = report.identity
    w(f"Tra cứu: {report.query!r}" + (f"   (exe: {ident.exe_name})" if ident and ident.exe_name else "") + "\n")
    if ident and (ident.steam_appid or ident.engine or ident.product_name):
        w(f"  tệp game: steam={ident.steam_appid or '-'} engine={ident.engine or '-'} "
          f"product={ident.product_name or '-'} titles={ident.titles}\n")
    w("\nNguồn          trạng thái  thời gian  yêu cầu  kết quả\n")
    for st in report.statuses:
        found = ", ".join(f"{k}={v}" for k, v in st.found.items() if v) or "-"
        req = f"{st.requests}" + (f"+{st.cached}c" if st.cached else "")
        w(f"  {st.name:<12} {st.state:<10} {st.elapsed_s:>7.2f}s  {req:>7}  {found}"
          + (f"  ({_short(st.message, 70)})" if st.message else "") + "\n")
    w(f"\nTổng: {report.elapsed_s:.2f} s" + ("  [đã hủy]" if report.cancelled else "") + "\n\n")
    for line in describe(report):
        w(f"* {line}\n")
    if report.ids:
        w("  ids: " + ", ".join(f"{k}={v}" for k, v in report.ids.items()) + "\n")
    if report.confidence:
        w(f"  độ chắc chắn: {report.confidence}\n")
    for a in report.alternatives:
        w(f"  ? {a.source}: {a.title} ({a.score:.2f}) {', '.join(f'{k}={v}' for k, v in a.ids.items())}"
          + (f" — {_short(a.description, 60)}" if a.description else "") + "\n")
    if report.patches:
        w("\nViệt hóa (chỉ là đường link):\n")
        for p in report.patches:
            flag = "chính thức" if p.official else ("đã có trên VNDB" if p.verified else "chưa kiểm chứng")
            w(f"  - [{p.source}, {flag}] {_short(p.title, 60)}\n      {p.url}\n")
            if p.note:
                w(f"      {_short(p.note, 110)}\n")
    if report.characters:
        w(f"\nNhân vật ({len(report.characters)}):\n")
        for c in report.characters[:limit]:
            parts = " / ".join(f"{a}={b}" for a, b in c.parts)
            aliases = ", ".join(f"{a}={b}" if a else b for a, b in c.aliases[:4])
            w(f"  {c.original:<14} {c.romanized:<22} {c.gender or '?':<3} {c.role or '-':<8} [{','.join(c.sources)}]"
              + (f"  {parts}" if parts else "") + (f"  aka {_short(aliases, 60)}" if aliases else "") + "\n")
        if len(report.characters) > limit:
            w(f"  … {len(report.characters) - limit} nhân vật nữa\n")
    if report.terms:
        w(f"\nThuật ngữ ({len(report.terms)}):\n")
        for t in report.terms[:limit]:
            w(f"  {t.source} -> {t.target} ({t.kind}) [{','.join(t.sources)}]" + (f" {t.note}" if t.note else "") + "\n")
    if report.attribution:
        w("\nNguồn dữ liệu:\n")
        for a in report.attribution:
            w(f"  {a['source']}: {a['url']} — {a['license']}\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tra cứu dữ liệu game (như app làm khi gặp game mới).")
    ap.add_argument("title", nargs="?", default="", help="tên game (bỏ trống khi dùng --exe)")
    ap.add_argument("--exe", default="", help="đường dẫn tệp .exe của game (chỉ đọc tệp, không đụng process)")
    ap.add_argument("--sources", default=",".join(SOURCES), help=f"nguồn, cách nhau bằng dấu phẩy ({','.join(SOURCES)})")
    ap.add_argument("--json", action="store_true", help="in báo cáo + hồ sơ nháp dạng JSON")
    ap.add_argument("--profile-out", default="", help="ghi hồ sơ nháp ra tệp JSON")
    ap.add_argument("--request", action="store_true", help="in yêu cầu tra cứu cho Claude (Markdown)")
    ap.add_argument("--record", default="", help="ghi các câu trả lời (đã rút gọn) làm fixture vào thư mục này")
    ap.add_argument("--replay", default="", help="chạy offline từ fixture trong thư mục này")
    ap.add_argument("--no-cache", action="store_true", help="không dùng cache trên đĩa")
    ap.add_argument("--budget", type=float, default=30.0, help="giới hạn thời gian cả lượt (giây)")
    ap.add_argument("--searxng", default="", help="URL SearXNG cho nguồn web (mặc định: theo config.json)")
    ap.add_argument("--no-public", action="store_true", help="không dùng máy SearXNG công cộng")
    ap.add_argument("--managed", action="store_true", help="tự bật SearXNG đã cài (tắt khi xong)")
    ap.add_argument("--no-template", action="store_true", help="bỏ qua hồ sơ mẫu có sẵn")
    ap.add_argument("--name-order", choices=("source", "given_first"), default="source")
    ap.add_argument("--aliases", action="store_true", help="thêm biệt danh vào hồ sơ nháp (có thể lộ nội dung truyện)")
    ap.add_argument("--limit", type=int, default=25, help="số dòng nhân vật / thuật ngữ in ra")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if not args.title and not args.exe:
        ap.error("cần tên game hoặc --exe")
    ident = identify(args.exe) if args.exe else identity_from_title(args.title)
    if args.title and args.exe:
        ident.titles = [args.title] + [t for t in ident.titles if t != args.title]
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    transport = FixtureTransport(Path(args.replay)) if args.replay else None
    recorder = FixtureRecorder(Path(args.record), trim=trim_fixture) if args.record else None
    cache = None if (args.no_cache or args.record or args.replay) else CACHE_DIR
    client = HttpClient(cache_dir=cache, transport=transport, recorder=recorder)

    from app.config import Config
    cfg = Config.load()
    service_obj = None
    searxng_url = args.searxng or cfg.searxng_url
    if "web" in sources and args.managed and not args.replay:
        try:
            from app.lookup.searxng import SearxngService
            service_obj = SearxngService(url=searxng_url, managed=True)
        except Exception as e:  # SearXNG is optional
            print(f"(SearXNG không dùng được: {e})", file=sys.stderr)
    web_search = None
    if args.replay and "web" in sources:
        web_search = lambda q, lang: []  # noqa: E731 - web hits are not recorded
    svc = LookupService(client=client, searxng_service=service_obj, searxng_url=searxng_url,
                        searxng_public_fallback=not args.no_public and cfg.searxng_public_fallback,
                        web_search=web_search, budget_s=args.budget)

    def progress(st) -> None:
        if not args.json:
            print(f"  … {st.name}: {st.state}", file=sys.stderr, flush=True)

    t0 = time.monotonic()
    try:
        report = svc.run(ident, sources, progress_cb=progress, use_templates=not args.no_template)
    finally:
        client.close()
        if service_obj is not None:
            try:
                service_obj.stop()
            except Exception:
                pass
    profile = build_profile(report, name_order=args.name_order, include_aliases=args.aliases)
    if args.request:
        from app.lookup.claude_request import build_request
        print(build_request(report, profile=profile))
        return 0
    if args.json:
        print(json.dumps({"report": report.to_dict(), "profile": json.loads(profile_to_json(profile))},
                         ensure_ascii=False, indent=1))
    else:
        print_report(report, limit=args.limit)
        print(f"\nHồ sơ nháp “{profile.name}”: {len(profile.characters)} nhân vật, {len(profile.terms)} thuật ngữ "
              f"({sum(t.auto for t in profile.terms)} gợi ý), src_lang={profile.src_lang}, "
              f"processes={profile.processes}  [{time.monotonic() - t0:.1f} s]")
        for c in profile.characters[:args.limit]:
            print(f"  nhân vật  {c.name} = {c.name_vi}" + (f" ({c.gender})" if c.gender else "")
                  + (f"  {_short(c.notes, 60)}" if c.notes else ""))
        for t in profile.terms[:args.limit]:
            print(f"  thuật ngữ {t.source} -> {t.target} [{t.kind}{', gợi ý' if t.auto else ''}]"
                  + (f"  {_short(t.note, 50)}" if t.note else ""))
        hidden = max(0, len(profile.characters) - args.limit) + max(0, len(profile.terms) - args.limit)
        if hidden:
            print(f"  … {hidden} mục nữa (--profile-out FILE hoặc --json để xem hết)")
        if transport is not None and transport.missing:
            print(f"\n{len(transport.missing)} yêu cầu không có fixture:")
            for m in transport.missing:
                print("  " + m)
    if args.profile_out:
        out = Path(args.profile_out)
        out.parent.mkdir(parents=True, exist_ok=True)  # data\ does not exist yet on a fresh clone
        out.write_text(profile_to_json(profile), encoding="utf-8")
        print(f"Đã ghi {args.profile_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
