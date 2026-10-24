"""Contextual translator backed by the local llama-server (OpenAI-compatible /v1/chat/completions).

One request per screen; the answer is constrained by a JSON schema (response_format json_schema in
the OpenAI shape - llama.cpp silently ignores {"type": "json_schema", "schema": ...} without the
wrapper, see bench/results/llm_setup.md). Greedy decoding with cache_prompt, so the stable prompt
prefix (rules, glossary, characters) is reused from the server's KV cache. Ids that come back
missing or unusable (still Japanese, prompt text, ...) and answers that dropped a glossary term are
asked for once more on their own, with a note saying what was wrong; then whatever is usable is
returned. The prompt shape per model and the pronoun clean-up come from llm_prompt.PromptOptions:
after finalize_result (fix_pronouns, then repair_roles for a pronoun in the wrong role), a line whose
roles are still wrong by its source (a vocative, "cho", the subject of an explicit "I" / 私が, who did a
favour for whom: 作ってくれたのか -> "Anh làm … cho em à?" from the brother) is asked once more with a note
naming the word (reask_roles); that request only happens for such lines, at
most MAX_ROLE_REASK_ITEMS of them in one request per screen.

Only items worth an LLM answer are asked: not context-only items (ScreenItem.context_only), not OCR
debris (ノノノ), not roles or lengths the model handles worse than machine translation (the 1.8B:
dialogue up to 200 characters), and nothing whose answer could not fit max_tokens; those come back
missing, so the pipeline keeps the machine translation. Items are sent whole (never cut) and split
over several requests when their estimated answer exceeds max_tokens.
Measured by bench/eval_llm.py: the 7B ~0.6 s, the 1.8B ~0.12 s per speaker-label + line screen;
about 0.28 s (7B) per extra item. The server process belongs to app.llm.server.LlamaServer.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace

import httpx

from .base import TranslationError
from .contextual import ContextualTranslator, ScreenItem, ScreenResult, TranslationContext
from .llm_prompt import (
    FAVOUR_ANCHOR,
    ROLE_NAME,
    SLOT_ANCHORS,
    PromptOptions,
    build_messages,
    finalize_result,
    glossary_misses,
    japanese_ratio,
    line_role_issues,
    low_information,
    note_words,
    parse_response,
    reask_issues,
    requested_items,
    response_schema,
    retry_note,
    same_shape,
    screen_role_issues,
    screen_speakers,
)
from .pronoun_roles import analyze, judged_slots

log = logging.getLogger(__name__)

__all__ = ["LocalLlmTranslator", "answer_tokens", "MAX_ROLE_REASK_ITEMS"]

SAMPLING = {"temperature": 0.0, "repeat_penalty": 1.0}  # greedy: reproducible, cache friendly
_CONTEXT_ERRORS = ("context", "n_ctx", "too long", "exceed")
_ANSWER_OVERHEAD = 96  # tokens of JSON around the answers
MAX_ROLE_REASK_ITEMS = 3  # lines of one screen asked again for their pronoun roles (one request at most)
REASK_BUDGET_SHARE = 0.6  # no role re-ask once this share of the caller's time budget is used


def _slots_still_judged(source: str, answer: str, pair: tuple[str, str], src: str, asked: Sequence,
                        act_in_view: bool = False) -> bool:
    """`answer` (asked again for `asked`) still has the slots asked about in the checker's view: as many
    of the explicit source pronouns' (subject / object / owner), and of who does an act for whom
    ("favour") one at least - the giver and the one it is for belong to one act, either shows its
    direction ("Đây là thứ em làm cho anh": em, after "là", is no clause subject). Fewer issues then mean
    right slots, not slots out of the checker's view (pronoun_roles.judged_slots). `act_in_view`: one favour
    slot at least whatever was asked (an answer that left out a wrong word: "Cô ơi, cho em mượn…", not "Mẹ ơi, gà
    chiên nhé?")."""
    judged = judged_slots(source, answer, *pair, src)
    favour = sum(slot == FAVOUR_ANCHOR for *_, slot in judged)
    return (len(judged) - favour >= sum(i.anchor in SLOT_ANCHORS for i in asked)
            and favour >= (1 if act_in_view else min(1, sum(i.anchor == FAVOUR_ANCHOR for i in asked))))


def _pronoun_count(text: str, pair: tuple[str, str]) -> int:
    return sum(not u.vocative for u in analyze(text, *pair))


def _favour_slots_added(source: str, old: str, new: str, pair: tuple[str, str], src: str) -> int:
    """Slots of who does a favour for whom that `new` has in the checker's view beyond those of `old`."""
    def count(text: str) -> int:
        return sum(slot == FAVOUR_ANCHOR for *_, slot in judged_slots(source, text, *pair, src))
    return max(0, count(new) - count(old))


def _token_budget(items: Sequence[ScreenItem], cap: int) -> int:
    """max_tokens for a screen: JSON overhead per item plus a generous allowance per source char,
    so a looping model stops early instead of running to `cap`."""
    chars = sum(len(it.text) for it in items)
    return max(64, min(cap, _ANSWER_OVERHEAD + 48 * len(items) + 4 * chars))


def answer_tokens(text: str) -> int:
    """Rough estimate of the tokens of the Vietnamese answer for `text`, its JSON included: ~3 per
    Japanese character, ~0.6 per other character (Hy-MT2; a 30-line, ~1000-character Japanese screen
    ran past 1024 tokens after ~19 lines)."""
    ja = round(japanese_ratio(text) * sum(ch.isalpha() for ch in text))
    return 24 + 3 * ja + int(0.6 * (len(text) - ja))


class LocalLlmTranslator(ContextualTranslator):
    name = "llm"
    quality_tier = 2

    def __init__(self, base_url: str, model: str, *, timeout: float = 30.0, max_tokens: int = 1024,
                 client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.prompt_options = PromptOptions.for_model(model)
        self._own_client = client is None
        # trust_env=False: never route 127.0.0.1 through an HTTP(S)_PROXY from the environment.
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0), trust_env=False)
        self.requests = 0
        self.retries = 0
        self.role_retries = 0  # screens asked again for their pronoun roles (reask_roles)
        self.role_retry_items = 0  # lines in those requests
        self.role_retry_taken = 0  # lines whose answer asked again replaced the first one
        self.last_role_reask: dict[int, str] = {}  # item id -> outcome of its role re-ask on the last screen
        self.last_role_candidates: dict[int, str] = {}  # item id -> the answer that re-ask gave (taken or not)
        self.last_timings: dict = {}  # llama-server "timings" of the last response (prompt/cache/predicted)

    def skip_reason(self, it: ScreenItem) -> str | None:
        """Why `it` is left to machine translation (None: ask the model)."""
        opt = self.prompt_options
        if it.context_only and not (it.role_hint == ROLE_NAME and opt.label_terms):
            return "context"  # an unknown speaker label still is: its rendering may become an auto term
        if opt.roles is not None and it.role_hint not in opt.roles:
            return f"role {it.role_hint}"
        if low_information(it.text):
            return "no real text"
        if opt.max_item_chars is not None and len(it.text.strip()) > opt.max_item_chars:
            return f"longer than {opt.max_item_chars} characters"
        if answer_tokens(it.text) > self.max_tokens - _ANSWER_OVERHEAD:
            return "answer would not fit max_tokens"
        return None

    def _chunks(self, asked: Sequence[ScreenItem]) -> list[list[ScreenItem]]:
        """`asked` split (in order) so that each request's estimated answer fits max_tokens."""
        budget = self.max_tokens - _ANSWER_OVERHEAD
        out: list[list[ScreenItem]] = []
        used = budget + 1
        for it in asked:
            need = answer_tokens(it.text)
            if used + need > budget:
                out.append([])
                used = 0
            out[-1].append(it)
            used += need
        return out

    async def _ask(self, items: Sequence[ScreenItem], asked: Sequence[ScreenItem], src: str, tgt: str,
                   ctx: TranslationContext, notes: Mapping[int, str] | None = None) -> ScreenResult:
        """One request per chunk of `asked`; a failure after something was translated keeps that."""
        translations: dict[int, str] = {}
        speakers: dict[int, str | None] = {}
        terms: list = []
        for chunk in self._chunks(asked):
            try:
                r = await self._request(items, chunk, src, tgt, ctx, notes=notes)
            except TranslationError as exc:
                if not translations:
                    raise
                log.warning("LLM request for %d more items failed: %s", len(chunk), exc)
                break
            translations.update(r.translations)
            speakers.update(r.speakers)
            terms += r.new_terms
        return ScreenResult(translations, speakers, tuple(terms))

    async def translate_screen(self, items: list[ScreenItem], src: str, tgt: str,
                               ctx: TranslationContext) -> ScreenResult:
        t0 = time.perf_counter()
        self.last_role_reask, self.last_role_candidates = {}, {}
        items = [it for it in items if it.text and it.text.strip()]
        asked = []
        for it in requested_items(items, ctx):
            why = self.skip_reason(it)
            if why is None:
                asked.append(it)
            elif why != "context":
                log.debug("not asking the LLM for item %d (%s): %.40r", it.id, why, it.text)
        translations: dict[int, str] = {}
        speakers: dict[int, str | None] = {}
        terms: list = []
        if asked:
            first = await self._ask(items, asked, src, tgt, ctx)
            translations.update(first.translations)
            speakers.update(first.speakers)
            terms += first.new_terms
            # Ask once more, with a note, for ids that came back missing / unusable and for answers
            # that dropped a glossary term. Not for dropped names: the model turns a name used as a
            # form of address into the pronoun, and a note does not change that (bench/eval_llm.py).
            misses = {it.id: glossary_misses(it.text, translations[it.id], ctx, people=False)
                      for it in asked if it.id in translations}
            retry = [it for it in asked if it.id not in translations or misses.get(it.id)]
            if retry:
                self.retries += 1
                notes = {it.id: retry_note(misses.get(it.id, ()), self.prompt_options.lang) for it in retry}
                retry_items = [it for it in items if it in retry or it.role_hint == ROLE_NAME]  # labels: speakers
                log.info("LLM answer: %d of %d items missing or unusable, %d dropped glossary entries; asking again",
                         sum(it.id not in translations for it in retry), len(asked),
                         sum(it.id in translations for it in retry))
                try:
                    second = await self._ask(retry_items, retry, src, tgt, ctx, notes=notes)
                except TranslationError as exc:
                    if not translations:
                        raise
                    log.warning("LLM retry for %d items failed: %s", len(retry), exc)
                else:
                    for it in retry:
                        tr = second.translations.get(it.id)
                        if tr is None:
                            continue
                        if it.id in translations and \
                                len(glossary_misses(it.text, tr, ctx, people=False)) >= len(misses[it.id]):
                            continue  # no better than the first answer
                        translations[it.id] = tr
                        speakers[it.id] = second.speakers.get(it.id)
                    terms += second.new_terms
                still = sum(it.id not in translations for it in asked)
                if still:
                    log.info("LLM gave nothing usable for %d of %d items", still, len(asked))
        merged = ScreenResult(translations, speakers, tuple(terms))
        final = self._finalize(merged, items, ctx, src)
        if self.prompt_options.reask_roles and asked:
            final = await self._reask_roles(final, merged, items, asked, src, tgt, ctx, t0)
        return replace(final, model=self.model, latency_s=time.perf_counter() - t0)

    def _reask_time(self, t0: float) -> float | None:
        """Seconds a role re-ask may take within time_budget_s (None: no budget; <= 0: no time for one). Not
        once more than REASK_BUDGET_SHARE of the budget is gone; a margin is kept for the rest of the job."""
        budget = self.time_budget_s
        if budget is None:
            return None
        used = time.perf_counter() - t0
        if used > REASK_BUDGET_SHARE * budget:
            return 0.0
        return budget - used - max(0.3, 0.1 * budget)

    def _finalize(self, merged: ScreenResult, items: Sequence[ScreenItem], ctx: TranslationContext,
                  src: str) -> ScreenResult:
        opt = self.prompt_options
        return finalize_result(merged, items, ctx, fix=opt.fix_pronouns, label_terms=opt.label_terms,
                               repair=opt.repair_roles, src=src)

    async def _reask_roles(self, final: ScreenResult, merged: ScreenResult, items: Sequence[ScreenItem],
                           asked: Sequence[ScreenItem], src: str, tgt: str, ctx: TranslationContext,
                           t0: float | None = None) -> ScreenResult:
        """Ask once more for the lines whose pronoun roles are still wrong after the repair (a vocative,
        "cho", a question about the listener, a greeting, the subject / object / possessor of an
        explicit source pronoun, who did a favour for whom, tao/mày it does not put in; not "sẽ" alone),
        with a note naming each wrong word and whom it means (and, for a favour, who does it for whom and
        the structure to write). At most MAX_ROLE_REASK_ITEMS lines (the frame / greeting / vocative /
        favour kinds before the explicit-pronoun slots) that fit one request, each asked once: a bad
        screen adds one request at most. A new answer replaces the old one only with fewer role issues
        (fewer of those asked about, and fewer of any kind: no new "sẽ" either), the same pronoun uses
        and speech acts (same_shape: a model that follows a wrong note by dropping or adding a pronoun
        changes who does what; a favour's note may have it add the one it is for, "mẹ … cho con", one
        pronoun per benefactive slot it adds, leave out a wrong word it named, "Cô ơi, cho em mượn…", and
        write "cho" for "giúp"), no pronoun the note did not ask for, the explicit-pronoun and favour slots
        asked about still judged (_slots_still_judged: right now, not out of the checker's view; one favour
        slot at least when the answer has fewer pronouns) and no glossary term the first answer kept
        dropped; a neutral bạn / tôi it put
        where the note asked for the pair's word becomes that word (note_words). Within time_budget_s only
        (_reask_time): a re-ask that would not fit is not sent, or cut short, and the first answer stays
        ("no time"). Each line's outcome is in last_role_reask and logged."""
        found = screen_role_issues(final, items, ctx, src)
        retry = [it for it in asked if it.id in found]
        if not retry:
            return final
        left = self._reask_time(t0 if t0 is not None else time.perf_counter())
        if left is not None and left <= 0.2:
            log.info("LLM answer: pronoun roles wrong in %d items; no time left to ask again", len(retry))
            self.last_role_reask.update({it.id: "no time" for it in retry})
            return final
        # the established kinds first, then lines whose only issues are an explicit pronoun's slots
        first = sorted(retry, key=lambda it: all(i.anchor in SLOT_ANCHORS for i in found[it.id][0]))
        chosen = {it.id for it in self._chunks(first[:MAX_ROLE_REASK_ITEMS])[0]}
        for it in retry:
            if it.id not in chosen:
                self.last_role_reask[it.id] = "over the cap"
        retry = [it for it in retry if it.id in chosen]
        self.role_retries += 1
        self.role_retry_items += len(retry)
        log.info("LLM answer: pronoun roles wrong in %d of %d items; asking again for %d", len(found), len(asked),
                 len(retry))
        try:
            second = await asyncio.wait_for(
                self._ask([it for it in items if it in retry or it.role_hint == ROLE_NAME], retry, src, tgt, ctx,
                          notes={it.id: found[it.id][1] for it in retry}), left)
        except TimeoutError:
            log.warning("LLM role retry for %d items cut short after %.1f s: the first answer stays", len(retry), left)
            self.last_role_reask.update({it.id: "no time" for it in retry})
            return final
        except TranslationError as exc:
            log.warning("LLM role retry for %d items failed: %s", len(retry), exc)
            self.last_role_reask.update({it.id: "failed" for it in retry})
            return final
        trial = self._finalize(replace(merged, translations={**merged.translations, **second.translations}),
                               items, ctx, src)
        trial = replace(trial, translations={**trial.translations, **{
            it.id: note_words(trial.translations[it.id], final.translations[it.id], found[it.id][0])
            for it in retry if it.id in trial.translations}})
        before, after = line_role_issues(final, items, ctx, src), line_role_issues(trial, items, ctx, src)
        translations = dict(final.translations)
        for it in retry:
            old, tr = final.translations[it.id], trial.translations.get(it.id)
            if tr is not None:
                self.last_role_candidates[it.id] = tr
            left = after.get(it.id, ([],))[0]
            asked_for = {i.expected_word for i in found[it.id][0] if i.expected_word}
            pair = before[it.id][3:5]  # (own, other): con / mẹ / ta count as the pronouns they are here
            # a note on who does a favour for whom asks for "<giver> … cho <for>": the new answer may add a
            # pronoun there ("con có thể cho thêm…" -> "mẹ có thể cho con…"), one per benefactive slot it adds, leave
            # out a wrong word it named ("em cho cô mượn…" -> "cho em mượn…") and write "cho" for "giúp"
            act = any(i.act for i in found[it.id][0])
            added = _favour_slots_added(it.text, old, tr, pair, src) if tr is not None and act else 0
            dropped = len({(i.start, i.end) for i in found[it.id][0] if i.act}) if act else 0
            if it.id not in second.translations or tr is None:
                why = "no answer"
            elif tr == old:
                why = "unchanged"
            elif len(reask_issues(left)) >= len(found[it.id][0]) or len(left) >= len(before[it.id][0]):
                why = "no fewer role issues"
            elif not same_shape(old, tr, asked_for, pair, added, dropped, act):
                why = "changed more than the roles"  # dropped / added a pronoun, or one the note did not ask for
            elif not _slots_still_judged(it.text, tr, pair, src, found[it.id][0],
                                         act and _pronoun_count(tr, pair) < _pronoun_count(old, pair)):
                # fewer issues because the checker no longer sees the slots asked about, not because they are
                # right now (the 1.8B: "Em là người sẽ đưa anh đi" -> "Em sẽ là anh gửi cho")
                why = "slots not judged"
            elif set(glossary_misses(it.text, tr, ctx, people=False)) - \
                    set(glossary_misses(it.text, old, ctx, people=False)):
                why = "dropped a glossary term"
            else:
                why = "taken"
                translations[it.id] = tr
                self.role_retry_taken += 1
            self.last_role_reask[it.id] = why
            log.info("LLM role retry for item %d (%s): %s", it.id,
                     ", ".join(f"'{i.found}' -> '{i.expected_word}' [{i.anchor}]" for i in found[it.id][0]), why)
        return replace(final, translations=translations)

    def _body(self, items: Sequence[ScreenItem], asked: Sequence[ScreenItem], src: str, tgt: str,
              ctx: TranslationContext, notes: Mapping[int, str] | None = None) -> dict:
        """Request for `asked` (the items to translate) on a screen of `items` (all of them, for the
        speakers). Every speaker is pinned: the one from the layout, else null (a model's own guess
        is made up more often than not)."""
        ids = [it.id for it in asked]
        if self.prompt_options.shape == "native":
            schema = response_schema(ids, flat=True)
        else:
            layout = screen_speakers(items, ctx)
            schema = response_schema(ids, {it.id: layout.get(it.id) for it in asked})
        return {
            "model": self.model,
            "messages": build_messages(items, src, tgt, ctx, self.prompt_options, ask=ids, notes=notes),
            "max_tokens": _token_budget(asked, self.max_tokens),
            "cache_prompt": True,
            "response_format": {"type": "json_schema", "json_schema": {"name": "screen", "strict": True,
                                                                       "schema": schema}},
            **SAMPLING,
        }

    async def _request(self, items: Sequence[ScreenItem], asked: Sequence[ScreenItem], src: str, tgt: str,
                       ctx: TranslationContext, notes: Mapping[int, str] | None = None) -> ScreenResult:
        body = self._body(items, asked, src, tgt, ctx, notes)
        resp = await self._post(body)
        if resp.status_code == 400 and ctx.history and any(w in resp.text.lower() for w in _CONTEXT_ERRORS):
            shorter = replace(ctx, history=ctx.history[len(ctx.history) // 2 + 1:])
            log.warning("prompt too long for the server's context; retrying with %d history lines", len(shorter.history))
            ctx = shorter
            body = self._body(items, asked, src, tgt, ctx, notes)
            resp = await self._post(body)
        if resp.status_code != 200:
            status = resp.status_code
            raise TranslationError(f"llama-server HTTP {status}: {_error_text(resp)}",
                                   retryable=status >= 500 or status == 429)
        try:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise TranslationError(f"llama-server: unexpected response ({type(exc).__name__})", retryable=True) from None
        self.last_timings = data.get("timings") or {}
        if choice.get("finish_reason") == "length":
            log.warning("LLM answer cut at max_tokens=%d (%d items)", body["max_tokens"], len(asked))
        return parse_response(content, asked, ctx)

    async def _post(self, body: dict) -> httpx.Response:
        self.requests += 1
        try:
            return await self._client.post(self.base_url + "/v1/chat/completions", json=body)
        except httpx.TimeoutException as exc:
            raise TranslationError(f"llama-server timed out ({type(exc).__name__})", retryable=True) from None
        except httpx.HTTPError as exc:
            raise TranslationError(f"llama-server unreachable ({type(exc).__name__}: {exc})", retryable=True) from None

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()


def _error_text(resp: httpx.Response, limit: int = 200) -> str:
    try:
        data = resp.json()
        err = data.get("error") if isinstance(data, dict) else None
        msg = err.get("message") if isinstance(err, dict) else err
        text = str(msg or data)
    except ValueError:
        text = resp.text
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
