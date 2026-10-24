"""Which games the app already asked about (DATA_DIR/lookup_state.json), so it asks ONCE per game.

    {"format": 1, "games": {"exe:steins;gate.exe": {"state": "done", "title": "Steins;Gate",
                                                    "profile": "Steins;Gate", "ts": 1761350000}}}

States: "offered" (the tray balloon was asked for, the user has not answered yet: Windows hides balloons
while a full-screen game runs, so the game is listed again under "Tra cứu game" after a restart, without a
balloon), "asked" (the user opened the question / said "later"), "declined" (never for this game), "done"
(looked up; `profile` is what was made), "empty" (looked up, nothing found), "failed" (network error: may
be asked again after RETRY_FAILED_S). Thread-safe; written atomically; a broken file is kept aside as .bad
and the state starts empty.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import DATA_DIR

log = logging.getLogger(__name__)

STATE_PATH = DATA_DIR / "lookup_state.json"
STATES = ("offered", "asked", "declined", "done", "empty", "failed")
MAX_OFFERED = 10  # games listed again at startup
RETRY_FAILED_S = 7 * 86400
MAX_GAMES = 2000


@dataclass
class GameState:
    state: str
    title: str = ""
    exe: str = ""
    profile: str = ""
    ts: float = 0.0


class LookupState:
    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._games: dict[str, GameState] | None = None

    def _load(self) -> dict[str, GameState]:
        if self._games is not None:
            return self._games
        games: dict[str, GameState] = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for key, v in (raw.get("games") or {}).items():
                if isinstance(v, dict) and v.get("state") in STATES:
                    games[str(key)] = GameState(state=v["state"], title=str(v.get("title") or ""),
                                                exe=str(v.get("exe") or ""), profile=str(v.get("profile") or ""),
                                                ts=float(v.get("ts") or 0))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError) as e:
            log.warning("lookup state %s unreadable (%s); kept as .bad, starting empty", self.path, e)
            try:
                os.replace(self.path, self.path.with_name(self.path.name + ".bad"))
            except OSError:
                pass
        self._games = games
        return games

    def _save(self) -> None:
        games = self._games or {}
        if len(games) > MAX_GAMES:  # oldest first
            for key in sorted(games, key=lambda k: games[k].ts)[: len(games) - MAX_GAMES]:
                del games[key]
        data = {"format": 1, "games": {k: asdict(v) for k, v in games.items()}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, key: str) -> GameState | None:
        with self._lock:
            st = self._load().get(key)
            return GameState(**asdict(st)) if st else None

    def should_ask(self, key: str, now: float | None = None) -> bool:
        """True when this game was never asked about (or its last lookup failed long enough ago)."""
        st = self.get(key)
        if st is None:
            return True
        if st.state == "failed":
            return (now or time.time()) - st.ts >= RETRY_FAILED_S
        return False

    def mark(self, key: str, state: str, *, title: str = "", exe: str = "", profile: str = "") -> None:
        if state not in STATES:
            raise ValueError(f"unknown lookup state {state!r}")
        with self._lock:
            games = self._load()
            old = games.get(key)
            games[key] = GameState(state=state, title=title or (old.title if old else ""),
                                   exe=exe or (old.exe if old else ""), profile=profile or (old.profile if old else ""),
                                   ts=time.time())
            try:
                self._save()
            except OSError:
                log.warning("lookup state not saved", exc_info=True)

    def forget(self, key: str | None = None) -> None:
        """Ask again about `key` (every game when None)."""
        with self._lock:
            games = self._load()
            if key is None:
                games.clear()
            else:
                games.pop(key, None)
            try:
                self._save()
            except OSError:
                log.warning("lookup state not saved", exc_info=True)

    def items(self) -> dict[str, GameState]:
        with self._lock:
            return {k: GameState(**asdict(v)) for k, v in self._load().items()}

    def offered(self, limit: int = MAX_OFFERED) -> list[tuple[str, GameState]]:
        """Games offered but never answered, newest last (the tray lists them again at startup)."""
        rows = [(k, v) for k, v in self.items().items() if v.state == "offered"]
        rows.sort(key=lambda kv: kv[1].ts)
        return rows[-limit:]
