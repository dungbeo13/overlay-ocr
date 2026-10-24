"""Block tracking across OCR passes: stable ids, translation reuse, typewriter waits, staleness.

Each OCR pass yields TextGroups. A group continues an existing block when their boxes overlap
(IoU >= 0.3 or one centre inside the other) and the text is the same (exact below 12 chars,
<= 10% normalised Levenshtein distance from 12 chars) -> same id, translation kept. A slightly
different reading counts as OCR jitter until the next pass reads it again: then the text really
changed (a number, "can" -> "can't") and the block is re-translated, its old translation shown
dimmed meanwhile. A group whose text extends the block's text (typewriter effect) keeps the id.

Text that may still be changing is translated only once an OCR pass confirms it: a new block
needs one more pass with the same text, a grown block a pass at least 600 ms after it last grew
(next_check() says when that pass is due); text ending a sentence is translated at once.
Anything else is a new block. Old blocks covered by a new block are replaced at once; blocks
that just vanished are kept for one pass (OCR flicker) and removed after 2 consecutive misses,
or at once when the caller's `gone` check says the pixels under them changed (a scene change).
A pass that only regroups lines the tracker already knows (line spacing right at the layout
threshold) keeps the tracked grouping. Failed texts stay backed off even if their block is
re-created.

Translations have a quality tier (1 machine translation, 2 contextual LLM): a worse tier never
replaces a better one for the same text. The LLM tier is scheduled separately (llm_due /
mark_llm / apply_llm): it refines blocks whose text is confirmed, once per LLM "epoch" (the
pipeline starts a new epoch when the glossary profile changes).

Not thread-safe: the pipeline drives one tracker from its worker thread.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from rapidfuzz.distance import Levenshtein

from app.ocr.textnorm import join_lines, normalize_for_cer, union_rects
from app.translate.textnorm import needs_translation

from .colors import colors_close
from .layout import TextGroup
from .types import BGR, Block, Rect

SENTENCE_END = (".", "!", "?", "。", "！", "？", "」", "』", "…")
_TRAILING_CLOSERS = "\"'”’)）]】»"


@dataclass
class TrackerParams:
    min_iou: float = 0.3
    fuzzy_min_len: int = 12  # below this, "same text" means exactly equal (after normalisation)
    max_distance: float = 0.10  # normalised Levenshtein distance for "same text"
    typewriter_wait_s: float = 0.6  # grown text must be read unchanged this long after it grew
    typewriter_max_hold_s: float = 2.0  # ...but never wait longer than this without a confirming pass
    confirm_new: bool = True  # a new block that does not end a sentence waits for one more pass
    stale_min_share: float = 0.7  # on growth, keep the old translation dimmed if old >= this share of new
    same_line_iou: float = 0.6  # two OCR line boxes this similar are the same line (regrouping check)
    max_misses: int = 2  # remove a block after this many consecutive passes without it
    error_backoff_s: float = 5.0  # first retry delay after a failed translation (doubles, capped)
    error_backoff_max_s: float = 60.0
    backoff_memory: int = 256  # failed texts remembered across block re-creation
    geometry_tol_px: int = 2  # box jitter up to this is ignored (no repaint)
    color_tol: int = 24  # background change up to this per channel is ignored


# -- geometry / text helpers ------------------------------------------------------------------
def iou(a: Rect, b: Rect) -> float:
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def centre_inside(a: Rect, b: Rect) -> bool:
    """True if the centre of `a` lies inside `b`."""
    cx, cy = a[0] + a[2] / 2, a[1] + a[3] / 2
    return b[0] <= cx <= b[0] + b[2] and b[1] <= cy <= b[1] + b[3]


def boxes_overlap(a: Rect, b: Rect, min_iou: float = 0.3) -> bool:
    return iou(a, b) >= min_iou or centre_inside(a, b) or centre_inside(b, a)


def normalize_source(text: str, lang: str) -> str:
    return normalize_for_cer(text, lang)


def same_text(a_norm: str, b_norm: str, min_len: int = 12, max_distance: float = 0.10) -> bool:
    """Equal, or (both >= min_len chars) within max_distance normalised Levenshtein distance."""
    if a_norm == b_norm:
        return True
    if min(len(a_norm), len(b_norm)) < min_len:
        return False
    return Levenshtein.normalized_distance(a_norm, b_norm) <= max_distance


def is_growth(old_norm: str, new_norm: str) -> bool:
    """`new` extends `old` (typewriter). The last old char may differ: half-drawn glyphs misread."""
    if len(new_norm) <= len(old_norm) or not old_norm:
        return False
    if new_norm.startswith(old_norm):
        return True
    return len(old_norm) >= 4 and new_norm.startswith(old_norm[:-1])


def ends_sentence(text: str) -> bool:
    """True if the text ends like a finished sentence (closing quotes/brackets are skipped)."""
    t = text.rstrip()
    return t.endswith(SENTENCE_END) or t.rstrip(_TRAILING_CLOSERS).endswith(SENTENCE_END)


def _close_rect(a: Rect, b: Rect, tol: int) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(
        (a[0], a[1], a[0] + a[2], a[1] + a[3]), (b[0], b[1], b[0] + b[2], b[1] + b[3])))


# -- tracker ----------------------------------------------------------------------------------
@dataclass
class TrackedBlock:
    id: int
    group: TextGroup  # current geometry (its .source may be a jittered reading of `source`)
    source: str  # canonical text: what gets translated and published
    norm: str
    bg: BGR = (0, 0, 0)
    fg: BGR = (255, 255, 255)
    translation: str | None = None
    stale: str | None = None
    error: str | None = None
    error_count: int = 0
    retry_at: float = 0.0
    inflight: str | None = None  # source text currently being translated
    changed_at: float = 0.0  # when `source` last changed
    seen_at: float = 0.0  # last OCR pass that matched this block
    growing: bool = False  # the text may still change: wait for a confirming pass
    settle_s: float = 0.0  # a confirming pass must come at least this long after changed_at
    alt_norm: str | None = None  # a different reading from the last pass, not yet confirmed
    misses: int = 0
    quality: int = 0  # tier of `translation` (0 none, 1 MT, 2 LLM)
    speaker: str | None = None  # speaker the LLM attributed the current text to
    llm_inflight: str | None = None  # source text currently in an LLM job
    llm_epoch: int = -1  # LLM epoch of the tier-2 translation
    llm_skip: tuple[str, int] | None = None  # (source, epoch) the LLM returned nothing for
    mt_fallback: str | None = None  # source the LLM could not translate: machine translation allowed ("only" mode)
    llm_lookup: tuple[str, int] | None = None  # (source, epoch) already looked up in the tier-2 cache ("only" mode)

    def to_block(self) -> Block:
        g = self.group
        return Block(
            id=self.id, box=g.box, lines=g.lines, source=self.source, vertical=g.vertical,
            bg=self.bg, fg=self.fg, line_height=g.line_height, translation=self.translation,
            stale_translation=self.stale if self.translation is None else None, error=self.error,
            quality=self.quality if self.translation is not None else 0, speaker=self.speaker,
        )


@dataclass
class TrackResult:
    added: list[int] = field(default_factory=list)
    grown: list[int] = field(default_factory=list)  # same id, longer text (typewriter)
    changed: list[int] = field(default_factory=list)  # same id, a different reading read twice in a row
    kept: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    missed: list[int] = field(default_factory=list)  # absent this pass, still shown
    alt: list[int] = field(default_factory=list)  # kept, but read differently this pass (unconfirmed)
    regrouped: int = 0  # groups rebuilt to keep the tracked line grouping


class BlockTracker:
    def __init__(self, lang: str = "ja", params: TrackerParams | None = None, *, first_id: int = 1) -> None:
        self.lang = lang
        self.p = params or TrackerParams()
        self._next_id = first_id
        self._tracks: dict[int, TrackedBlock] = {}
        self._backoff: dict[tuple[str, str], tuple[float, int, str]] = {}  # (lang, norm) -> retry_at, count, msg

    # -- state ----------------------------------------------------------------------------
    def reset(self, lang: str | None = None) -> None:
        """Drop all blocks (ids are never reused, so late results for old blocks are ignored).
        Translation backoff survives: a text that just failed is not retried at once."""
        self._tracks.clear()
        if lang is not None:
            self.lang = lang

    def __len__(self) -> int:
        return len(self._tracks)

    def get(self, block_id: int) -> TrackedBlock | None:
        return self._tracks.get(block_id)

    @property
    def tracks(self) -> list[TrackedBlock]:
        return list(self._tracks.values())

    def blocks(self) -> tuple[Block, ...]:
        ordered = sorted(self._tracks.values(), key=lambda t: (t.group.box[1], t.group.box[0], t.id))
        return tuple(t.to_block() for t in ordered)

    def pending_count(self) -> int:
        """Blocks whose current text is being translated right now (and has no translation yet)."""
        return sum(1 for t in self._tracks.values()
                   if t.inflight is not None and t.inflight == t.source and t.translation is None)

    def waiting_count(self) -> int:
        """Blocks without a translation for their current text (in flight, queued or backing off)."""
        return sum(1 for t in self._tracks.values() if t.translation is None)

    def next_check(self) -> float | None:
        """When an OCR pass is needed to confirm text that may still be changing (None: none is).
        A new block needs any later pass, a grown one a pass typewriter_wait_s after it grew."""
        times = [max(t.changed_at + t.settle_s, t.seen_at + 1e-3)
                 for t in self._tracks.values() if t.growing and t.translation is None]
        return min(times, default=None)

    # -- one OCR pass ---------------------------------------------------------------------
    def update(
        self,
        groups: Sequence[TextGroup],
        now: float,
        colors: Sequence[tuple[BGR, BGR]] | None = None,
        *,
        gone: Callable[[TrackedBlock], bool] | None = None,
        new_settle_s: float = 0.0,
    ) -> TrackResult:
        """Match this pass's groups against the tracked blocks. colors[i] = (bg, fg) of groups[i].

        `gone(track)` may confirm that an unmatched block really vanished (e.g. the pixels under it
        changed); such a block is removed at once instead of being kept for max_misses passes.
        `new_settle_s`: how long after this pass the confirming pass for new blocks must come (0: the
        next pass; the pipeline waits longer for blocks found by a confirming pass itself, so text
        whose reading keeps flickering cannot trigger an OCR pass on every poll).
        """
        p = self.p
        res = TrackResult()
        groups, colors, res.regrouped = self._keep_partition(list(groups), None if colors is None else list(colors))
        norms = [normalize_source(g.source, self.lang) for g in groups]

        # Candidate pairs: kind 0 = same text, 1 = same within the fuzzy tolerance, 2 = typewriter
        # growth; best kind, then best IoU.
        pairs: list[tuple[int, float, int, int]] = []
        for tid, t in self._tracks.items():
            for gi, g in enumerate(groups):
                if not boxes_overlap(t.group.box, g.box, p.min_iou):
                    continue
                if t.norm == norms[gi]:
                    kind = 0
                elif is_growth(t.norm, norms[gi]):
                    kind = 2
                elif same_text(t.norm, norms[gi], p.fuzzy_min_len, p.max_distance):
                    kind = 1
                else:
                    continue
                pairs.append((kind, -iou(t.group.box, g.box), tid, gi))
        pairs.sort()

        matched_tracks: set[int] = set()
        matched_groups: set[int] = set()
        for kind, _, tid, gi in pairs:
            if tid in matched_tracks or gi in matched_groups:
                continue
            matched_tracks.add(tid)
            matched_groups.add(gi)
            t = self._tracks[tid]
            g = groups[gi]
            t.misses = 0
            if kind == 2:
                old_len = len(t.norm)
                self._set_text(t, g, norms[gi], now, keep_stale=old_len >= p.stale_min_share * len(norms[gi]))
                t.settle_s = p.typewriter_wait_s
                t.growing = not ends_sentence(g.source)
                res.grown.append(tid)
            elif kind == 0:
                t.alt_norm = None
                if t.growing and now > t.changed_at and now - t.changed_at >= t.settle_s - 1e-6:
                    t.growing = False  # read unchanged by a later pass: it stopped changing
                self._refresh_geometry(t, g)
                res.kept.append(tid)
            elif t.alt_norm == norms[gi]:
                # The same different reading twice in a row: the text changed (a digit, a negation).
                self._set_text(t, g, norms[gi], now, keep_stale=True)
                t.growing = False
                res.changed.append(tid)
            else:  # OCR jitter, or a change the next pass will confirm
                t.alt_norm = norms[gi]
                self._refresh_geometry(t, g)
                res.kept.append(tid)
                res.alt.append(tid)
            t.seen_at = now
            if colors is not None:
                self._refresh_colors(t, *colors[gi])

        for tid in list(self._tracks):
            if tid in matched_tracks:
                continue
            t = self._tracks[tid]
            # Other text now sits where this block was: it was replaced, not missed by OCR.
            replaced = any(boxes_overlap(t.group.box, g.box, p.min_iou) for g in groups)
            t.misses += 1
            if replaced or t.misses >= p.max_misses or (gone is not None and gone(t)):
                del self._tracks[tid]
                res.removed.append(tid)
            else:
                res.missed.append(tid)

        for gi, g in enumerate(groups):
            if gi in matched_groups:
                continue
            t = TrackedBlock(id=self._next_id, group=g, source=g.source, norm=norms[gi], changed_at=now,
                             seen_at=now)
            self._next_id += 1
            if colors is not None:
                t.bg, t.fg = colors[gi]
            if not needs_translation(t.norm):  # nothing to translate: show the text as is
                t.translation = t.source
            else:
                # Possibly the first glyphs of a typewriter line: translate once a pass confirms it.
                t.growing = p.confirm_new and not ends_sentence(g.source)
                t.settle_s = new_settle_s
                self._restore_backoff(t)
            self._tracks[t.id] = t
            res.added.append(t.id)
        return res

    def _set_text(self, t: TrackedBlock, g: TextGroup, norm: str, now: float, *, keep_stale: bool) -> None:
        """New canonical text for a block; its translation becomes a dimmed preview if the change is small."""
        t.group = g
        t.source, t.norm = g.source, norm
        t.alt_norm = None
        if t.translation is not None or not keep_stale:
            t.stale = t.translation if keep_stale else None
        t.translation = None
        t.quality, t.speaker = 0, None
        t.error, t.error_count, t.retry_at = None, 0, 0.0
        t.changed_at = now
        self._restore_backoff(t)

    def _keep_partition(
        self, groups: list[TextGroup], colors: list[tuple[BGR, BGR]] | None,
    ) -> tuple[list[TextGroup], list[tuple[BGR, BGR]] | None, int]:
        """Undo a regrouping of OCR lines the tracker already knows.

        Line spacing right at the layout threshold makes a static 2-line dialog flip between one
        block and two as the boxes jitter by a pixel. If a group holds exactly the lines of several
        tracked blocks with exactly their text, it is split back; if several groups hold exactly
        the lines of one tracked block (same text within the fuzzy tolerance), they are merged.
        """
        if not self._tracks or not groups:
            return groups, colors, 0
        tracks = list(self._tracks.values())
        min_iou = self.p.same_line_iou

        def owner(r: Rect) -> tuple[int, int] | None:  # (track id, index of the matching tracked line)
            best, best_iou = None, min_iou
            for t in tracks:
                for li, tr in enumerate(t.group.lines):
                    v = iou(tr, r)
                    if v >= best_iou:
                        best, best_iou = (t.id, li), v
            return best

        owners = [[owner(r) for r in g.lines] for g in groups]
        replace: dict[int, list[TextGroup]] = {}

        for gi, g in enumerate(groups):  # split one group back into tracked blocks
            own = owners[gi]
            if len(own) < 2 or any(o is None for o in own):
                continue
            order = list(dict.fromkeys(o[0] for o in own))
            if len(order) < 2:
                continue
            parts = {tid: [r for r, o in zip(g.lines, own) if o[0] == tid] for tid in order}
            if any(len(parts[tid]) != len(self._tracks[tid].group.lines) for tid in order):
                continue
            joined = join_lines([self._tracks[tid].source for tid in order], self.lang)
            if normalize_source(joined, self.lang) != normalize_source(g.source, self.lang):
                continue  # the text changed as well: not just a regrouping
            replace[gi] = [TextGroup(union_rects(parts[tid]), tuple(parts[tid]), self._tracks[tid].source,
                                     g.vertical, self._tracks[tid].group.line_height) for tid in order]

        single: dict[int, list[int]] = {}  # track id -> groups made only of that track's lines
        for gi, own in enumerate(owners):
            if gi in replace or not own or any(o is None for o in own):
                continue
            ids = {o[0] for o in own}
            if len(ids) == 1:
                single.setdefault(ids.pop(), []).append(gi)
        for tid, gis in single.items():  # merge several groups back into one tracked block
            t = self._tracks[tid]
            if len(gis) < 2 or sorted(o[1] for gi in gis for o in owners[gi]) != list(range(len(t.group.lines))):
                continue
            gis.sort(key=lambda gi: min(o[1] for o in owners[gi]))
            source = join_lines([groups[gi].source for gi in gis], self.lang)
            if not same_text(t.norm, normalize_source(source, self.lang), self.p.fuzzy_min_len, self.p.max_distance):
                continue
            lines = tuple(r for _, r in sorted((o[1], r) for gi in gis for r, o in zip(groups[gi].lines, owners[gi])))
            replace[gis[0]] = [TextGroup(union_rects(lines), lines, source, groups[gis[0]].vertical,
                                         t.group.line_height)]
            for gi in gis[1:]:
                replace[gi] = []

        if not replace:
            return groups, colors, 0
        out_g: list[TextGroup] = []
        out_c: list[tuple[BGR, BGR]] = []
        for gi, g in enumerate(groups):
            for ng in replace.get(gi, [g]):
                out_g.append(ng)
                if colors is not None:
                    out_c.append(colors[gi])
        return out_g, (out_c if colors is not None else None), len(replace)

    def _refresh_geometry(self, t: TrackedBlock, g: TextGroup) -> None:
        tol = max(self.p.geometry_tol_px, int(0.1 * max(t.group.line_height, g.line_height)))
        if (not _close_rect(t.group.box, g.box, tol) or len(g.lines) != len(t.group.lines)
                or g.vertical != t.group.vertical):
            t.group = g

    def _refresh_colors(self, t: TrackedBlock, bg: BGR, fg: BGR) -> None:
        if not colors_close(t.bg, bg, self.p.color_tol):  # small sampling noise must not repaint
            t.bg, t.fg = bg, fg

    def _restore_backoff(self, t: TrackedBlock) -> None:
        entry = self._backoff.get((self.lang, t.norm))
        if entry is not None:
            t.retry_at, t.error_count, t.error = entry

    # -- translation scheduling ---------------------------------------------------------
    def _is_due(self, t: TrackedBlock, now: float) -> bool:
        if t.translation is not None or t.inflight == t.source or now < t.retry_at:
            return False
        return not (t.growing and now - t.changed_at < self.p.typewriter_max_hold_s)

    def due(self, now: float) -> list[TrackedBlock]:
        """Blocks that need a translation request now."""
        return [t for t in self._tracks.values() if self._is_due(t, now)]

    def take_due(self, now: float, *, limit: int | None = None,
                 where: Callable[[TrackedBlock], bool] | None = None) -> list[tuple[int, str]]:
        """Mark due blocks (those passing `where`) as in flight and return their (id, source) for one
        translate_batch."""
        items = []
        for t in sorted(self.due(now), key=lambda t: (t.group.box[1], t.group.box[0])):
            if limit is not None and len(items) >= limit:
                break
            if where is not None and not where(t):
                continue
            t.inflight = t.source
            t.growing = False
            items.append((t.id, t.source))
        return items

    def release(self, block_id: int, source: str) -> None:
        """Undo take_due for a block whose request was not sent: it stays due."""
        t = self._tracks.get(block_id)
        if t is not None and t.inflight == source:
            t.inflight = None

    def apply_translation(self, block_id: int, source: str, translation: str, quality: int = 1) -> bool:
        """Apply a result if the block still exists with the same source and does not show a better
        tier already; returns True if applied."""
        self._backoff.pop((self.lang, normalize_source(source, self.lang)), None)
        t = self._tracks.get(block_id)
        if t is None:
            return False
        if t.inflight == source:
            t.inflight = None
        translation = unicodedata.normalize("NFC", translation)
        if t.source != source:
            # The text changed a little meanwhile: the older translation is a useful dimmed preview.
            old = normalize_source(source, self.lang)
            small = (t.norm.startswith(old) and len(old) >= self.p.stale_min_share * len(t.norm)) \
                or same_text(old, t.norm, self.p.fuzzy_min_len, self.p.max_distance)
            if t.translation is None and small:
                t.stale = translation
            return False
        if t.translation is not None and t.quality > quality:
            return False  # e.g. Google's answer arriving after the LLM's
        t.translation = translation
        t.quality = quality
        t.stale = None
        t.error, t.error_count, t.retry_at = None, 0, 0.0
        t.growing = False
        return True

    # -- contextual (LLM) tier -------------------------------------------------------------
    def confirmed(self, t: TrackedBlock, now: float) -> bool:
        """The block's text is not (or no longer considered) still changing."""
        return not (t.growing and now - t.changed_at < self.p.typewriter_max_hold_s)

    def needs_llm(self, t: TrackedBlock, epoch: int) -> bool:
        """True if the block's current text has no tier-2 translation from `epoch` (and the LLM did
        not already decline it in that epoch)."""
        if not needs_translation(t.norm):
            return False
        if t.translation is not None and t.quality >= 2 and t.llm_epoch >= epoch:
            return False
        return t.llm_skip != (t.source, epoch)

    def llm_due(self, now: float, epoch: int) -> list[TrackedBlock]:
        """Blocks to put in the next LLM job: confirmed text that needs a tier-2 translation and is not
        in a job already, in screen order."""
        out = [t for t in self._tracks.values()
               if t.llm_inflight != t.source and self.confirmed(t, now) and self.needs_llm(t, epoch)]
        return sorted(out, key=lambda t: (t.group.box[1], t.group.box[0], t.id))

    def mark_llm(self, block_id: int, source: str) -> None:
        t = self._tracks.get(block_id)
        if t is not None and t.source == source:
            t.llm_inflight = source
            t.growing = False

    def finish_llm(self, block_id: int, source: str) -> None:
        """The LLM job holding this block is over (whatever its outcome)."""
        t = self._tracks.get(block_id)
        if t is not None and t.llm_inflight == source:
            t.llm_inflight = None

    def apply_llm(self, block_id: int, source: str, translation: str, speaker: str | None, epoch: int) -> bool:
        """Apply a tier-2 translation if the block still exists with the same source and has none from
        this epoch yet. Replaces a tier-1 translation in place (no empty or dimmed step)."""
        t = self._tracks.get(block_id)
        if t is None or t.source != source:
            return False
        translation = unicodedata.normalize("NFC", translation or "").strip()
        if not translation:
            return False
        if t.translation is not None and t.quality >= 2 and t.llm_epoch >= epoch:
            return False
        t.translation = translation
        t.quality, t.speaker, t.llm_epoch = 2, speaker, epoch
        t.stale = None
        t.error, t.error_count, t.retry_at = None, 0, 0.0
        t.growing = False
        return True

    def skip_llm(self, block_id: int, source: str, epoch: int, *, allow_mt: bool = False) -> None:
        """The LLM returned nothing for this text: do not ask it again in this epoch; `allow_mt` lets
        machine translation take it ("only" mode)."""
        t = self._tracks.get(block_id)
        if t is not None and t.source == source:
            t.llm_skip = (source, epoch)
            if allow_mt:
                t.mt_fallback = source

    def allow_mt(self, block_id: int, source: str) -> None:
        """Let machine translation take this text ("only" mode) while the LLM may still refine it."""
        t = self._tracks.get(block_id)
        if t is not None and t.source == source:
            t.mt_fallback = source

    def apply_error(self, block_id: int, source: str, message: str, now: float) -> bool:
        """Record a failed translation; the text is not retried before the backoff expires, even
        if its block is re-created meanwhile (OCR jitter, a pause, a scene that comes back)."""
        key = (self.lang, normalize_source(source, self.lang))
        prev = self._backoff.get(key)
        t = self._tracks.get(block_id)
        count = max(prev[1] if prev else 0, t.error_count if t is not None and t.source == source else 0) + 1
        retry_at = now + min(self.p.error_backoff_max_s, self.p.error_backoff_s * 2 ** (count - 1))
        self._backoff.pop(key, None)
        self._backoff[key] = (retry_at, count, message)
        while len(self._backoff) > self.p.backoff_memory:
            del self._backoff[next(iter(self._backoff))]
        if t is None:
            return False
        if t.inflight == source:
            t.inflight = None
        if t.source != source:
            return False
        t.error, t.error_count, t.retry_at = message, count, retry_at
        return True
