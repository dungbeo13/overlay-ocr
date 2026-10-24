"""Contract for context-aware (LLM) translation of a whole screen.

Unlike TranslatorEngine.translate_batch (independent strings), a ContextualTranslator sees every
text block on screen at once plus a TranslationContext: the active profile's glossary, its
characters (how each one speaks and addresses others) and the recent dialogue. That is what lets
it keep names consistent and pick Vietnamese pronouns (anh/em, cậu/tớ, tao/mày, ...) correctly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

Rect = tuple[int, int, int, int]  # x, y, w, h (region-relative physical px)


@dataclass(frozen=True)
class Term:
    """A fixed rendering: `source` (as it appears on screen) is always translated as `target`."""

    source: str
    target: str
    kind: str = "term"  # "person" | "place" | "item" | "skill" | "term" | ...
    note: str = ""
    auto: bool = False  # proposed by the LLM, not yet confirmed by the user


@dataclass(frozen=True)
class Character:
    name: str  # as it appears in the source text (e.g. セラ)
    name_vi: str  # fixed Vietnamese rendering (e.g. Sera)
    gender: str = ""  # "nam" | "nữ" | "" (unknown)
    self_pronoun: str = ""  # how the character refers to themself in Vietnamese (tôi, tớ, em, ta, ...)
    notes: str = ""  # free text: age, role, relationships, how they address others


@dataclass(frozen=True)
class HistoryLine:
    speaker: str | None  # source-text speaker name, if known
    source: str
    translation: str


@dataclass(frozen=True)
class ScreenItem:
    id: int  # the pipeline's block id
    text: str  # source text of the block
    box: Rect = (0, 0, 0, 0)
    role_hint: str | None = None  # layout guess: "name" (speaker label), "dialog", "ui", "subtitle" or None
    context_only: bool = False  # on screen for context (speakers, scene); it needs no new translation


@dataclass(frozen=True)
class TranslationContext:
    profile: str = "default"
    style: str = ""  # free-text style notes from the profile ("giọng kiếm hiệp", "giữ hậu tố -san", ...)
    terms: tuple[Term, ...] = ()
    characters: tuple[Character, ...] = ()
    history: tuple[HistoryLine, ...] = ()  # oldest first
    version: str = ""  # changes whenever terms/characters/style change (part of cache keys)
    # (part, character name): a name box showing only 紅莉栖 is the character 牧瀬紅莉栖 (gender, pronouns).
    # From an imported profile's lookup["aliases"]; parts several characters share are never listed.
    aliases: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ScreenResult:
    translations: dict[int, str]  # item id -> Vietnamese; ids the model skipped are absent
    speakers: dict[int, str | None] = field(default_factory=dict)  # item id -> source speaker name
    new_terms: tuple[Term, ...] = ()  # names/terms the model rendered and proposes to keep fixed
    model: str = ""
    latency_s: float = 0.0


class ContextualTranslator(ABC):
    name: str = "llm"
    model: str = ""
    quality_tier: int = 2
    # how long the caller waits for the current screen (the pipeline sets it per job; None: no limit): an
    # optional extra request (the role re-ask) is skipped or cut short so the answer already there is kept
    time_budget_s: float | None = None

    @abstractmethod
    async def translate_screen(self, items: list[ScreenItem], src: str, tgt: str,
                               ctx: TranslationContext) -> ScreenResult:
        """Translate all `items` of one screen together. Raise TranslationError on failure."""

    async def aclose(self) -> None:
        """Release clients (the server process is owned by its manager, not the translator)."""
