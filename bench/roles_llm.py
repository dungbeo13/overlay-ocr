"""Labelled pronoun-role lines through the real LLM tier: shared by bench/eval_roles_reask.py and
bench/eval_roles_e2e.py.

Each labelled line (bench.eval_roles_heldout.load_lines: speaker, listener, own, other, source,
src_lang, optionally translation / label / wrong / ref) becomes one screen as the app sees it - the
speaker label (role "name") and the dialogue line - with a minimal TranslationContext: the two
characters, each with its self_pronoun and a note "gọi <other person> là '<word>', xưng '<own word>'"
(so llm_prompt.address_pair defines the pair exactly as labelled), and one history line by the
listener (the app guesses the listener from the last other speaker). Models run on the local
llama-server (app.llm.server.LlamaServer on 127.0.0.1), one at a time, always stopped afterwards;
nothing starts while a blocked game is in the foreground.

InjectingTransport answers the next request with a given first answer (the labelled translation)
without sending it, and forwards every other request to the real server: the app's own path
(LocalLlmTranslator.translate_screen -> finalize -> _reask_roles) then runs on that answer.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
import unicodedata
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from app.config import LLM_HOME, Config
from app.llm.models import RECOMMENDED, recommended_model
from app.llm.server import LlamaServer
from app.translate.contextual import Character, HistoryLine, ScreenItem, TranslationContext
from bench.eval_llm import SCENARIOS_DIR, line_pair, listener_of
from bench.eval_roles_heldout import FAVOUR, benefactive, group_of
from bench.eval_roles_heldout import line_lang as heldout_line_lang

LABEL_ID, LINE_ID = 1, 2


def first_word(pair) -> str:
    """One pair word (a labelled own / other may be a list of accepted words)."""
    return pair if isinstance(pair, str) else next(iter(pair), "")


def line_context(ln: dict) -> TranslationContext:
    """The two characters of a labelled line and one history line by the listener."""
    sp, li = ln["speaker"], ln["listener"]
    own, other = first_word(ln["own"]), first_word(ln["other"])
    chars = (Character(sp, sp, "", own, f"gọi {li} là '{other}', xưng '{own}'"),
             Character(li, li, "", other, f"gọi {sp} là '{own}', xưng '{other}'"))
    return TranslationContext(profile="roles-bench", characters=chars, history=(HistoryLine(li, "…", "…"),),
                              version="roles-bench")


def line_items(ln: dict) -> list[ScreenItem]:
    return [ScreenItem(LABEL_ID, ln["speaker"], (80, 600, 140, 34), "name"),
            ScreenItem(LINE_ID, ln["source"], (80, 640, 1100, 90), "dialog")]


def line_lang(ln: dict) -> str:
    """The source language to translate a labelled line from, as the app always has one (a file that
    leaves src_lang out or says "auto": Japanese when the line has kana / kanji, else English; load_lines
    has already done this for the lines it loads)."""
    return heldout_line_lang(ln["source"], ln.get("src_lang"))


def scene_lines(names: list[str]) -> list[dict]:
    """The lines of bench scenes (bench/data/translate/scenarios) that have a role-tagged "ref" and a
    pronoun pair for their speaker -> listener, as labelled lines without a translation."""
    out = []
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        sc = json.loads(path.read_text(encoding="utf-8"))
        if names != ["all"] and sc["name"] not in names:
            continue
        chars = {c["name"]: c for c in sc.get("characters", [])}
        for n, line in enumerate(sc.get("lines", []), 1):
            sp, li, pair = line.get("speaker"), listener_of(sc, line), line_pair(sc, line)
            if not (line.get("ref") and pair and pair[0] and pair[1] and sp in chars and li in chars):
                continue
            src = unicodedata.normalize("NFC", line["text"])
            out.append({"id": f"{sc['name']}#{n}", "file": path.name, "cat": line.get("split") or sc.get("split", ""),
                        "src_lang": sc["lang"], "speaker": chars[sp].get("name_vi") or sp,
                        "listener": chars[li].get("name_vi") or li, "own": pair[0], "other": pair[1],
                        "source": src, "translation": "", "label": "", "wrong": [], "group": group_of(src),
                        FAVOUR: benefactive(src), "ref": unicodedata.normalize("NFC", line["ref"])})
    return out


class InjectingTransport(httpx.AsyncBaseTransport):
    """Forwards requests to the real llama-server; the one after inject() is answered with the given
    translations (in the request's own answer shape: the chat items or the native flat object)
    without being sent. Counts and times the forwarded requests."""

    def __init__(self, real: httpx.AsyncBaseTransport | None = None) -> None:
        self._real = real or httpx.AsyncHTTPTransport()
        self._pending: tuple[dict[int, str], dict[int, str | None]] | None = None
        self.forwarded = 0
        self.forwarded_s = 0.0

    def inject(self, translations: dict[int, str], speakers: dict[int, str | None] | None = None) -> None:
        self._pending = (translations, speakers or {})

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._pending is not None:
            translations, speakers = self._pending
            self._pending = None
            schema = json.loads(request.content)["response_format"]["json_schema"]["schema"]
            if "items" in schema.get("properties", {}):
                ids = [p["properties"]["id"]["const"] for p in schema["properties"]["items"]["prefixItems"]]
                content = {"items": [{"id": i, "speaker": speakers.get(i), "translation": translations[i]}
                                     for i in ids if i in translations], "new_terms": []}
            else:
                content = {str(i): translations[int(i)] for i in schema["required"] if int(i) in translations}
            return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(content, ensure_ascii=False)}}], "timings": {}})
        t0 = time.perf_counter()
        resp = await self._real.handle_async_request(request)
        await resp.aread()
        self.forwarded += 1
        self.forwarded_s += time.perf_counter() - t0
        return resp

    async def aclose(self) -> None:
        await self._real.aclose()


def client_for(transport: httpx.AsyncBaseTransport, timeout: float = 30.0) -> httpx.AsyncClient:
    # trust_env=False: never route 127.0.0.1 through an HTTP(S)_PROXY from the environment
    return httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(timeout, connect=5.0), trust_env=False)


class BlockedGame(RuntimeError):
    """A blocked game came to the foreground during a run: the model is unloaded and the run stops."""


def blocked_game() -> str | None:
    """The exe of a blocked game in the foreground (then no model is loaded), else None."""
    from app.ui.game_guard import GameGuard, matches  # noqa: PLC0415
    exe = GameGuard.foreground_process_name()
    return exe if matches(exe, Config.load().blocked_processes) else None


def check_guard(n: int, every: int = 20) -> None:
    """Every `every` lines of a run: raise BlockedGame when a blocked game is in the foreground (the
    caller's running_server then stops llama-server on the way out)."""
    if n % every == 0:
        game = blocked_game()
        if game:
            raise BlockedGame(f"a blocked game ({game}) came to the foreground: stopping")


def model_path(kind: str) -> Path | None:
    path = recommended_model(kind)
    return path if path is not None and path.name in RECOMMENDED.get(kind, ()) else None


@contextlib.asynccontextmanager
async def running_server(kind: str, path: Path) -> AsyncIterator[tuple[str, str]]:
    """(base url, model name) of a llama-server for `path`, stopped on the way out whatever happens."""
    server = LlamaServer(path, log_path=LLM_HOME / "logs" / f"eval-{path.stem}.log")
    print(f"[{kind}] {path.name}: starting llama-server")
    t0 = time.perf_counter()
    try:
        url = await asyncio.to_thread(server.start)
        print(f"[{kind}] ready in {time.perf_counter() - t0:.1f} s")
        yield url, server.model_name
    finally:
        await asyncio.to_thread(server.stop)
        print(f"[{kind}] llama-server stopped")
