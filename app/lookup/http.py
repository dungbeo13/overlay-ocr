"""Polite JSON-over-HTTP client of the game lookup.

Every request names the app in its User-Agent, has a timeout, respects the run's deadline and cancel
event, and is counted per source. The client keeps no cookies (a wiki's Set-Cookie is never sent back).
Successful answers are cached on disk (DATA_DIR/lookup_cache, one JSON file per request, `ttl_s` old at
most), so asking about the same game again sends nothing; an API error answered with HTTP 200 (MediaWiki /
Wikidata {"error": ...}: ratelimited, maxlag...) is a source error and is not cached.

Tests and the CLI's --record / --replay use FixtureRecorder / FixtureTransport: a fixture is one JSON file
per request, keyed by request_key(), so a recorded run replays without the network.
"""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

from app import __version__
from app.config import DATA_DIR

log = logging.getLogger(__name__)

PROJECT_URL = "https://github.com/dungbeo13/overlay-ocr"
USER_AGENT = f"ScreenTranslator/{__version__} (+{PROJECT_URL}; game glossary lookup, one query per game)"
CACHE_DIR = DATA_DIR / "lookup_cache"
CACHE_TTL_S = 14 * 86400
DEFAULT_TIMEOUT_S = 10.0
MAX_RESPONSE_BYTES = 4_000_000


class LookupHttpError(Exception):
    """A request failed (network, HTTP status, not JSON, too large)."""

    def __init__(self, message: str, *, status: int | None = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


class LookupCancelled(Exception):
    """The run was cancelled or ran out of time before this request."""


class _NoCookies(http.cookiejar.CookiePolicy):
    """A cookie policy that neither stores nor returns any cookie."""

    netscape, rfc2965, hide_cookie2 = True, False, False

    def set_ok(self, cookie, request) -> bool:
        return False

    def return_ok(self, cookie, request) -> bool:
        return False

    def domain_return_ok(self, domain, request) -> bool:
        return False

    def path_return_ok(self, path, request) -> bool:
        return False


def no_cookies() -> http.cookiejar.CookieJar:
    """A jar that keeps nothing (httpx.Client takes a CookieJar as it is; an httpx.Cookies would be copied into a
    jar with the default policy)."""
    return http.cookiejar.CookieJar(policy=_NoCookies())


def api_error(data: Any) -> str:
    """The error code of a JSON API that answers errors with HTTP 200 ({"error": {"code": "maxlag", ...}}), else ""."""
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if err is None and isinstance(data.get("errors"), list) and data["errors"]:
        err = data["errors"][0]
    if err is None or err == "" or err is False:
        return ""
    if isinstance(err, dict):
        return str(err.get("code") or err.get("info") or err.get("message") or "error")[:80]
    return str(err)[:80]


def canonical_url(url: str, params: dict | None = None) -> str:
    """scheme://host/path?sorted&query (params merged into the URL's own query)."""
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    for k, v in (params or {}).items():
        if v is None:
            continue
        query.append((str(k), str(v)))
    query.sort()
    base = f"{parts.scheme}://{parts.netloc.lower()}{parts.path}"
    return f"{base}?{urlencode(query)}" if query else base


def request_key(method: str, url: str, params: dict | None = None, body: Any = None) -> str:
    blob = f"{method.upper()} {canonical_url(url, params)}\n"
    if body is not None:
        blob += json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def host_tag(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    for tag in ("steampowered", "wikidata", "vndb", "fandom"):
        if tag in host:
            return {"steampowered": "steam"}.get(tag, tag)
    return host.split(":")[0].replace(".", "-")[:24] or "http"


@dataclass
class _Counter:
    requests: int = 0
    cached: int = 0
    bytes: int = 0


@dataclass
class HttpStats:
    by_source: dict[str, _Counter] = field(default_factory=dict)

    def get(self, source: str) -> _Counter:
        return self.by_source.setdefault(source, _Counter())


class FixtureRecorder:
    """Writes each successful answer as a fixture file (`trim(url, data)` may shrink it first)."""

    def __init__(self, directory: Path, trim: Callable[[str, Any], Any] | None = None):
        self.directory = Path(directory)
        self.trim = trim
        self._lock = threading.Lock()

    def __call__(self, method: str, url: str, params: dict | None, body: Any, status: int, data: Any) -> None:
        key = request_key(method, url, params, body)
        if self.trim is not None:
            data = self.trim(canonical_url(url, params), data)
        rec = {"request": {"method": method.upper(), "url": canonical_url(url, params), "body": body},
               "status": status, "json": data}
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{host_tag(url)}_{key}.json"
            path.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


class FixtureTransport(httpx.BaseTransport):
    """Answers requests from fixture files (see FixtureRecorder); an unknown request gets a 599 and is
    listed in `missing` (tests assert it stays empty)."""

    def __init__(self, directory: Path):
        self.fixtures: dict[str, dict] = {}
        self.missing: list[str] = []
        self.seen: list[str] = []
        for path in sorted(Path(directory).glob("*.json")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
                req = rec["request"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
            self.fixtures[request_key(req["method"], req["url"], None, req.get("body"))] = rec

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = None
        if request.content:
            try:
                body = json.loads(request.content.decode("utf-8"))
            except ValueError:
                body = None
        url = str(request.url)
        key = request_key(request.method, url, None, body)
        self.seen.append(canonical_url(url))
        rec = self.fixtures.get(key)
        if rec is None:
            self.missing.append(f"{request.method} {canonical_url(url)} {json.dumps(body, ensure_ascii=False)}")
            return httpx.Response(599, json={"error": "no fixture"}, request=request)
        return httpx.Response(int(rec.get("status", 200)), json=rec.get("json"), request=request)


Recorder = Callable[[str, str, "dict | None", Any, int, Any], None]


class HttpClient:
    """Shared by the sources of a run (thread-safe: httpx.Client is, the cache writes are atomic)."""

    def __init__(self, *, cache_dir: Path | None = CACHE_DIR, ttl_s: float = CACHE_TTL_S,
                 timeout: float = DEFAULT_TIMEOUT_S, transport: httpx.BaseTransport | None = None,
                 recorder: Recorder | None = None, user_agent: str = USER_AGENT) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.ttl_s = ttl_s
        self.timeout = timeout
        self.recorder = recorder
        self.stats = HttpStats()
        self._lock = threading.Lock()
        self._client = httpx.Client(headers={"User-Agent": user_agent, "Accept": "application/json"},
                                    timeout=timeout, follow_redirects=True, transport=transport, cookies=no_cookies())

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def session(self, source: str, *, deadline: float | None = None,
                cancel: threading.Event | None = None, timeout: float | None = None) -> "Session":
        return Session(self, source, deadline, cancel, timeout or self.timeout)

    # --- internals -------------------------------------------------------------------------

    def _cache_path(self, key: str) -> Path | None:
        return self.cache_dir / f"{key}.json" if self.cache_dir else None

    def _cache_get(self, key: str) -> Any:
        path = self._cache_path(key)
        if path is None:
            return None
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - float(rec.get("ts", 0)) <= self.ttl_s and rec.get("json") is not None \
                    and not api_error(rec.get("json")):  # an error body cached by an older build: asked again
                return rec
        except (OSError, ValueError, TypeError):
            pass
        return None

    def _cache_put(self, key: str, url: str, data: Any) -> None:
        path = self._cache_path(key)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"ts": time.time(), "url": url, "json": data}, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            log.debug("lookup cache: cannot write %s", path, exc_info=True)

    def request_json(self, method: str, url: str, *, params: dict | None = None, body: Any = None,
                     source: str = "", deadline: float | None = None, cancel: threading.Event | None = None,
                     timeout: float | None = None, use_cache: bool = True, counter: _Counter | None = None) -> Any:
        key = request_key(method, url, params, body)
        with self._lock:
            counters = [self.stats.get(source)] + ([counter] if counter is not None else [])
        if use_cache:
            rec = self._cache_get(key)
            if rec is not None:
                with self._lock:
                    for c in counters:
                        c.cached += 1
                return rec.get("json")
        if cancel is not None and cancel.is_set():
            raise LookupCancelled("cancelled")
        limit = timeout or self.timeout
        if deadline is not None:
            left = deadline - time.monotonic()
            if left < 0.5:
                raise LookupCancelled("out of time")
            limit = min(limit, left)
        with self._lock:
            for c in counters:
                c.requests += 1
        shown = canonical_url(url, params)
        t0 = time.monotonic()
        try:
            if method.upper() == "POST":
                resp = self._client.post(url, params=params, json=body, timeout=limit)
            else:
                resp = self._client.get(url, params=params, timeout=limit)
        except httpx.TimeoutException as e:
            raise LookupHttpError(f"timeout after {limit:.0f} s", url=shown) from e
        except httpx.HTTPError as e:
            raise LookupHttpError(f"network error: {type(e).__name__}", url=shown) from e
        log.debug("lookup %s %s -> %s (%d bytes, %.2f s)", method, shown, resp.status_code, len(resp.content),
                  time.monotonic() - t0)
        with self._lock:
            for c in counters:
                c.bytes += len(resp.content)
        if resp.status_code != 200:
            raise LookupHttpError(f"HTTP {resp.status_code}", status=resp.status_code, url=shown)
        if len(resp.content) > MAX_RESPONSE_BYTES:
            raise LookupHttpError("response too large", url=shown)
        try:
            data = resp.json()
        except ValueError as e:
            raise LookupHttpError("not JSON", status=resp.status_code, url=shown) from e
        code = api_error(data)
        if code:  # an API error with HTTP 200: never cached (a retry must ask again)
            raise LookupHttpError(f"API error: {code}", status=resp.status_code, url=shown)
        if use_cache and data is not None:
            self._cache_put(key, shown, data)
        if self.recorder is not None:
            try:
                self.recorder(method, url, params, body, resp.status_code, data)
            except Exception:
                log.warning("lookup: recording %s failed", shown, exc_info=True)
        return data


class Session:
    """The client as one source of one run sees it: its own counters, the run's deadline and cancel."""

    def __init__(self, client: HttpClient, source: str, deadline: float | None, cancel: threading.Event | None,
                 timeout: float) -> None:
        self.client = client
        self.source = source
        self.deadline = deadline
        self.cancel = cancel
        self.timeout = timeout
        self.counter = _Counter()

    def get_json(self, url: str, params: dict | None = None, *, timeout: float | None = None) -> Any:
        return self.client.request_json("GET", url, params=params, source=self.source, deadline=self.deadline,
                                        cancel=self.cancel, timeout=timeout or self.timeout, counter=self.counter)

    def post_json(self, url: str, body: Any, *, timeout: float | None = None) -> Any:
        return self.client.request_json("POST", url, body=body, source=self.source, deadline=self.deadline,
                                        cancel=self.cancel, timeout=timeout or self.timeout, counter=self.counter)

    @property
    def cancelled(self) -> bool:
        return bool(self.cancel is not None and self.cancel.is_set())

    def counts(self) -> tuple[int, int]:
        """(requests sent, answers served from the cache) by this session."""
        return self.counter.requests, self.counter.cached
