"""Recent dialogue per profile, sent to the contextual translator so it can tell who speaks to whom.

Lines are kept in memory only (a game session's worth); each profile has its own DialogueMemory so
switching games never mixes their conversations. recent(max_gap_s=...) gives only the current
stretch of dialogue: lines before a pause longer than that belong to an earlier scene (or another
program on the same profile) and would only mislead the listener guess. Thread-safe: the pipeline
adds lines from the asyncio thread while the GUI may clear them.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from .contextual import HistoryLine

__all__ = ["DialogueMemory", "MemoryBank"]


def _same_text(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


class DialogueMemory:
    """The last `maxlen` dialogue lines, oldest first."""

    def __init__(self, maxlen: int = 50, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._lines: deque[tuple[float, HistoryLine]] = deque(maxlen=max(1, int(maxlen)))
        self._clock = clock
        self._lock = threading.Lock()

    def add(self, speaker: str | None, source: str, translation: str) -> None:
        """Append a line. An exact repeat of the last line is ignored (it still counts as dialogue
        now); the same source line again with a new translation (Google first, then the LLM) or a
        newly known speaker replaces it."""
        source = (source or "").strip()
        translation = (translation or "").strip()
        speaker = (speaker or "").strip() or None
        if not source:
            return
        now = self._clock()
        with self._lock:
            last = self._lines[-1][1] if self._lines else None
            if last is not None and _same_text(last.source, source) and (speaker is None or last.speaker in (None, speaker)):
                merged = HistoryLine(speaker=speaker or last.speaker, source=last.source,
                                     translation=translation or last.translation)
                self._lines[-1] = (now, merged)
                return
            self._lines.append((now, HistoryLine(speaker=speaker, source=source, translation=translation)))

    def recent(self, n: int, *, max_gap_s: float | None = None) -> tuple[HistoryLine, ...]:
        """The last `n` lines, oldest first. With `max_gap_s`, only those after the last pause longer
        than that (nothing when the newest line is that old)."""
        if n <= 0:
            return ()
        with self._lock:
            lines = list(self._lines)[-n:]
        if max_gap_s is not None:
            t = self._clock()
            for i in range(len(lines) - 1, -1, -1):
                if t - lines[i][0] > max_gap_s:
                    lines = lines[i + 1:]
                    break
                t = lines[i][0]
        return tuple(line for _, line in lines)

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._lines)


class MemoryBank:
    """One DialogueMemory per profile name, created on first use."""

    def __init__(self, maxlen: int = 50, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._maxlen = maxlen
        self._clock = clock
        self._memories: dict[str, DialogueMemory] = {}
        self._lock = threading.Lock()

    def get(self, profile: str) -> DialogueMemory:
        with self._lock:
            mem = self._memories.get(profile)
            if mem is None:
                mem = self._memories[profile] = DialogueMemory(self._maxlen, clock=self._clock)
            return mem

    def clear_all(self) -> None:
        with self._lock:
            memories = list(self._memories.values())
        for mem in memories:
            mem.clear()
