"""A character's address notes <-> structured rows, for the glossary editor's pronoun picker.

The translator reads pronouns from a character's free-text notes; app.translate.llm_prompt.address_pair is
the contract, and nothing here changes what it reads:
  "gọi Ren là 'anh', xưng 'em'"  how the character addresses one listener (Ren), and itself toward Ren
  "gọi khách là 'anh'"           a category rule: anyone the notes do not pair the character with
  "xưng 'em'"                    the first one is the character's own word when it has no self_pronoun
address_pair takes the word between the quotes (it decides its case), the FIRST "gọi <listener> là" for a
listener, the character's self_pronoun (else the first "xưng" of the notes) for every listener, and category
rules only when they all give the same word.

parse_notes() splits notes into AddressNotes: rows (listener, gọi là, tự xưng riêng), a standalone "xưng"
and the rest of the text (age, role, voice), verbatim. render_notes() writes them back deterministically:
the rest first, then the standalone "xưng", then one rule per row, joined with "; ". Only clean segments are
parsed: a rule that starts the notes or a line, or follows punctuation or "và" ("17 tuổi; gọi Ren là 'anh',
xưng 'em'"); anything else ("thường gọi Ren là 'anh'", English notes) stays in the rest of the text, where
address_pair still reads it. Source order is kept where address_pair depends on it (its first "xưng", the
first "gọi X là" per listener): rest that holds such phrases stays before the rows when it came before them
(`head`), the rest of it goes after them, and a standalone "xưng" that followed a row's own word stays after
the rows (`self_after`). A word cell may carry an aside in brackets ("anh (お兄ちゃん)", "em (khi bực:
'mày')"): it is written after the quoted word, which is all address_pair takes. parse_notes() returns a fixed
point (rendering it and parsing again gives the same structure). Rendering the parse of any notes keeps what
address_pair makes of them (tests/test_glossary_ui.py checks every notes shape in the repo and random
recombinations of them); the editor keeps a character's notes verbatim until its rows change.

Notes typed by hand without the quotes ("gọi Ren là anh, xưng em") are no rule for address_pair:
unquoted_phrases() finds them and convert_unquoted() turns them into rows (the editor's "Chuyển vào bảng").

pair_infos() asks address_pair itself what the model will be told for each listener, so the editor's preview
is the truth, not a re-implementation.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.translate.contextual import Character, TranslationContext
from app.translate.llm_prompt import address_pair

__all__ = [
    "AddressRule", "AddressNotes", "PairInfo", "parse_notes", "render_notes", "split_word", "join_word",
    "word_problem", "self_problem", "listener_problem", "rule_problems", "is_blank", "complete_rules", "rule_target",
    "category_understood", "notes_own", "pair_infos", "suggest_self", "suggest_call", "address_phrases",
    "unquoted_phrases", "convert_unquoted", "rename_listener",
    "SELF_WORDS", "CALL_WORDS", "CATEGORY_LISTENERS", "EXTRA_LISTENERS", "MAX_WORD", "MAX_LISTENER",
]

MAX_WORD = 20  # address_pair reads at most 20 characters between the quotes
MAX_LISTENER = 40

SELF_WORDS = ("tôi", "em", "anh", "chị", "tớ", "mình", "tao", "ta", "cháu", "con", "ông", "bà", "thầy", "cô",
              "bác", "chú", "dì", "mẹ", "bố", "bổn cô nương", "lão phu")
CALL_WORDS = ("anh", "em", "chị", "cậu", "bạn", "mày", "ngươi", "ông", "bà", "cô", "thầy", "chú", "bác", "dì",
              "cháu", "con", "mẹ", "bố", "ngài", "quý khách", "Sensei", "tiền bối")
# Listener groups offered in the picker ("gọi khách là 'anh'"), then more groups / titles. Whether address_pair
# reads one as a rule for anyone is asked of address_pair itself (category_understood); one it does not read only
# reaches the model as notes.
CATEGORY_LISTENERS = ("người lạ", "khách", "mọi người", "người khác")
EXTRA_LISTENERS = ("học sinh", "Sensei")

_Q = "'\"“”‘’"  # the quotes address_pair accepts around a word


def _word(name: str) -> str:
    """A quoted word, then an optional aside on the same line: "(…)" or "hoặc '…'" (kept with the word)."""
    return (rf"[{_Q}](?P<{name}>[^{_Q}\n]{{1,{MAX_WORD}}})[{_Q}]"
            rf"(?:[ \t]*(?P<{name}_p>[(（][^()（）\n]*[)）])|[ \t]+(?P<{name}_a>hoặc[ \t]+[{_Q}][^{_Q}\n]{{1,{MAX_WORD}}}[{_Q}]))?")


def _listener(name: str) -> str:
    return rf"(?P<{name}>[^{_Q}\n;,.:()（）!?]{{1,{MAX_LISTENER}}}?)"


# One alternation, scanned left to right: "gọi X là 'a'" with its own ", xưng 'b'" (on the same line), then
# "với X xưng 'b'" (or "xưng 'b' với X" ending a clause), then a standalone "xưng 'b'".
_RULE = re.compile(
    rf"gọi\s+{_listener('cl')}\s+là\s*{_word('cw')}(?:(?:[ \t]*,[ \t]*|[ \t]+)(?:và[ \t]+)?xưng\s*{_word('co')})?"
    rf"|với\s+{_listener('wl')}\s+(?:thì\s+)?xưng\s*{_word('wo')}"
    rf"|xưng\s*{_word('xo')}[ \t]+với[ \t]+{_listener('xl')}(?=[ \t]*(?:[;,.:!?)）\n]|$))"
    rf"|xưng\s*{_word('sw')}",
    re.IGNORECASE)
_BOUNDARY = ".;,:!?\n(（"
_CONNECTORS = ("và", "còn", "nhưng")  # "17 tuổi và gọi Ren là 'anh'": the rule still starts a segment
_EDGE = " \t\n,;.:"
_SPLIT_WORD = re.compile(r"^\s*([^()（）]*?)\s*(?:[(（](.*)[)）])?\s*$", re.DOTALL)
_ADDRESS_PHRASE = re.compile(rf"(?:gọi\s+[^{_Q}\n]{{1,{MAX_LISTENER}}}?\s+là|xưng|calls?\s+[^{_Q}\n]{{1,40}}?)"
                             rf"\s*[{_Q}][^{_Q}\n]{{1,{MAX_WORD}}}[{_Q}]", re.IGNORECASE)
# The same phrases typed without the quotes ("Gọi Ren là con, xưng ta"): one word, then the end of the clause.
_BARE = r"[^\W\d_]+"
_CLAUSE_END = r"(?=[ \t]*(?:[,;.!?:)）(（\n]|$))"
_UNQUOTED = re.compile(
    rf"gọi\s+{_listener('l')}\s+là[ \t]+(?P<c>{_BARE}){_CLAUSE_END}"
    rf"(?:[ \t]*,?[ \t]*(?:và[ \t]+)?xưng[ \t]+(?P<o>{_BARE}){_CLAUSE_END})?"
    rf"|xưng[ \t]+(?P<s>{_BARE}){_CLAUSE_END}",
    re.IGNORECASE)
_PROBE = "__address_probe__"
_PROBE_LISTENER = "__address_probe_listener__"


@dataclass(frozen=True)
class AddressRule:
    """One row of the picker. `call` / `own` are word cells: the word, optionally with an aside in
    brackets ("anh (お兄ちゃん)"). `own` is only for this listener; empty: the character's Tự xưng."""

    listener: str  # as written: "Ren", "ông Genzou", "khách", "người lạ"
    call: str = ""  # what the character calls the listener
    own: str = ""  # what the character calls itself toward this listener only


@dataclass(frozen=True)
class AddressNotes:
    other: str = ""  # everything that is not an address rule, verbatim (age, role, voice, ...)
    self_word: str = ""  # a standalone "xưng 'em'" of the notes, as a word cell
    rules: tuple[AddressRule, ...] = ()
    # Source order where address_pair depends on it (render_notes): `head` is the start of `other` that came
    # before the first rule and holds address phrases (written before the rows while `other` still starts
    # with it); `self_after`: the standalone "xưng" came after a row's own word (written after the rows);
    # `self_first`: it came before the address phrases of `head` (written before it).
    head: str = ""
    self_after: bool = False
    self_first: bool = False


@dataclass(frozen=True)
class PairInfo:
    """What address_pair gives for the speaker toward `listener` (None: anyone the characters do not
    name, i.e. the category rules)."""

    listener: Character | None
    own: str | None
    other: str | None
    defined: bool
    source: str  # "rule" (a note names the listener), "derived" (from the listener's Tự xưng), "category", ""


# --- word cells -----------------------------------------------------------------------------------------

def _clean(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text or "").split())


def split_word(cell: str) -> tuple[str, str]:
    """("anh", "お兄ちゃん") for "anh (お兄ちゃん)"; ("anh", "") for "anh". A cell whose brackets do not
    parse comes back whole as the word (word_problem reports it)."""
    text = _clean(cell)
    m = _SPLIT_WORD.match(text)
    if not m:
        return text, ""
    return m.group(1).strip(), (m.group(2) or "").strip()


def join_word(word: str, aside: str = "") -> str:
    word, aside = _clean(word), _clean(aside)
    return f"{word} ({aside})" if word and aside else word


def word_problem(cell: str) -> str | None:
    """Why a word cell cannot be written into the notes (Vietnamese), None when it can (or is empty)."""
    text = _clean(cell)
    if not text:
        return None
    word, aside = split_word(text)
    if any(ch in text for ch in "()（）") and not _SPLIT_WORD.match(text):
        return f"“{text}”: dấu ngoặc chưa đúng — ví dụ: anh (お兄ちゃん)"
    if any(ch in word for ch in "()（）") or any(ch in aside for ch in "()（）"):
        return f"“{text}”: dấu ngoặc chưa đúng — ví dụ: anh (お兄ちゃん)"
    if not word:
        return f"“{text}”: thiếu từ xưng hô trước dấu ngoặc"
    if any(ch in word for ch in _Q):
        return f"“{word}”: không cần dấu nháy, chỉ ghi từ"
    if re.search(r"\shoặc\s|/|,|;", word):
        return f"“{word}”: mỗi ô chỉ một từ; cách gọi khác ghi trong ngoặc, ví dụ: anh (hoặc 'tiền bối')"
    if len(word) > MAX_WORD:
        return f"“{word}”: dài quá ({len(word)} ký tự, tối đa {MAX_WORD})"
    return None


def self_problem(text: str) -> str | None:
    """Why a Tự xưng (Character.self_pronoun, which address_pair uses as is) is not a single word."""
    t = _clean(text)
    if not t:
        return None
    if re.search(rf"[{_Q}()（）;,/]|\shoặc\s", t):
        return f"Tự xưng “{t}”: chỉ ghi một từ, ví dụ: em (cách khác ghi ở “Ghi chú khác”)"
    if len(t) > 32:
        return f"Tự xưng “{t}”: dài quá (tối đa 32 ký tự)"
    return None


def listener_problem(listener: str) -> str | None:
    text = _clean(listener)
    if not text:
        return None
    if re.search(rf"[{_Q};,.:()（）!?]", text):
        return f"“{text}”: tên người nghe không được có dấu câu hay dấu nháy"
    if len(text) > MAX_LISTENER:
        return f"“{text}”: tên người nghe dài quá (tối đa {MAX_LISTENER} ký tự)"
    if text.casefold().startswith(("là ", "với ")) or text.casefold() in ("là", "với"):
        return f"“{text}”: chỉ ghi tên người nghe"
    return None


def is_blank(rule: AddressRule) -> bool:
    return not (_clean(rule.listener) or _clean(rule.call) or _clean(rule.own))


def _rule_ok(rule: AddressRule) -> bool:
    return (bool(_clean(rule.listener)) and bool(_clean(rule.call) or _clean(rule.own))
            and listener_problem(rule.listener) is None and word_problem(rule.call) is None
            and word_problem(rule.own) is None)


def complete_rules(an: AddressNotes) -> tuple[AddressRule, ...]:
    """The rows render_notes writes: a listener, at least one word, nothing invalid."""
    return tuple(r for r in an.rules if _rule_ok(r))


def rule_problems(an: AddressNotes, speaker: Character | None = None) -> list[tuple[int, str]]:
    """(row, message) for rows that cannot be saved; blank rows are ignored (dropped on save)."""
    out: list[tuple[int, str]] = []
    seen: dict[str, int] = {}
    own_names = {_clean(n).casefold() for n in ((speaker.name, speaker.name_vi) if speaker else ()) if n}
    for i, r in enumerate(an.rules):
        if is_blank(r):
            continue
        listener = _clean(r.listener)
        if not listener:
            out.append((i, "chưa chọn người nghe"))
            continue
        problem = listener_problem(listener) or word_problem(r.call) or word_problem(r.own)
        if problem:
            out.append((i, problem))
            continue
        if not (_clean(r.call) or _clean(r.own)):
            out.append((i, f"“{listener}”: chưa có từ nào — điền ô “Gọi là”"))
            continue
        key = listener.casefold()
        if key in own_names:
            out.append((i, f"“{listener}” là chính nhân vật này"))
        elif not _clean(r.call):
            continue  # "với X xưng 'y'" next to "gọi X là 'z'" is fine: one is only a note
        elif key in seen:
            out.append((i, f"“{listener}” có hai dòng “Gọi là” (dòng {seen[key] + 1} và {i + 1}); "
                           "app chỉ dùng dòng đầu"))
        else:
            seen[key] = i
    return out


# --- notes <-> structure ---------------------------------------------------------------------------------

def _cell(m: re.Match, name: str) -> str:
    word = m.group(name)
    if word is None:
        return ""
    paren, alt = m.group(f"{name}_p"), m.group(f"{name}_a")
    aside = paren.strip()[1:-1] if paren else (alt or "")
    return join_word(word.strip(), aside)


def _at_boundary(text: str, pos: int) -> bool:
    """A clean segment start: the start of the notes or of a line, after punctuation, "và" / "còn" / "nhưng",
    or right after a quoted word or its aside ("xưng 'em' gọi Ren là 'anh'": a rule written on)."""
    before = text[:pos].rstrip(" \t")  # a line break is a boundary
    return (not before or before[-1] in _BOUNDARY or before[-1] in _Q + ")）"
            or before.split()[-1].casefold() in _CONNECTORS)


def _strip_piece(piece: str, *, lead: bool = False, trail: bool = False) -> str:
    """A piece of free text between rules, without the separators (and "và" / "còn" / "nhưng") that
    joined it to the rule before (`lead`) or after it (`trail`)."""
    p = piece.strip(_EDGE).strip()
    while True:
        words = p.split()
        if lead and words and words[0].casefold() in _CONNECTORS:
            p = " ".join(words[1:]).strip(_EDGE).strip()
        elif trail and words and words[-1].casefold() in _CONNECTORS:
            p = " ".join(words[:-1]).strip(_EDGE).strip()
        else:
            return p


_OPEN, _CLOSE = "(（", ")）"


def _drop_empty_brackets(pieces: list[str]) -> list[str]:
    """A rule written inside brackets ("Hay cười (gọi Mio là 'em').") leaves "(" at the end of the text before
    it and ")" at the start of the text after it: drop that empty pair instead of keeping "(; )"."""
    out = list(pieces)
    for i, p in enumerate(out):
        if not p.rstrip(" \t").endswith(tuple(_OPEN)):
            continue
        for j in range(i + 1, len(out)):
            q = out[j].lstrip(" \t")
            if q.startswith(tuple(_CLOSE)):
                out[i] = out[i].rstrip(" \t")[:-1]
                out[j] = q[1:]
                break
            if _strip_piece(q):
                break  # other text inside the brackets: they are not empty
    return out


def _parse_once(text: str) -> AddressNotes:
    rules: list[AddressRule] = []
    self_word = ""
    self_after = False
    self_piece = -1  # index of the text piece right after the standalone "xưng"
    first_rule_piece = -1  # index of the text piece right before the first rule
    pieces: list[str] = []
    last = 0
    for m in _RULE.finditer(text):
        if not _at_boundary(text, m.start()):
            continue  # "thường gọi Ren là 'anh'": stays in the text (address_pair still reads it)
        if m.group("sw") is None:
            if first_rule_piece < 0:
                first_rule_piece = len(pieces)
            if m.group("cw") is not None:
                rules.append(AddressRule(_clean(m.group("cl")), _cell(m, "cw"), _cell(m, "co")))
            elif m.group("wo") is not None:
                rules.append(AddressRule(_clean(m.group("wl")), "", _cell(m, "wo")))
            else:  # "xưng 'tôi' với người lạ"
                rules.append(AddressRule(_clean(m.group("xl")), "", _cell(m, "xo")))
        elif self_word:
            continue  # a second standalone "xưng" stays text (address_pair only reads the first)
        else:
            self_word = _cell(m, "sw")
            self_after = any(r.own for r in rules)  # a row's own word came first: address_pair reads that one
            self_piece = len(pieces) + 1
        pieces.append(text[last:m.start()])
        last = m.end()
    pieces.append(text[last:])
    pieces = _drop_empty_brackets(pieces)
    n = len(pieces)
    kept = [" ".join(_strip_piece(p, lead=i > 0, trail=i < n - 1).split()) for i, p in enumerate(pieces)]
    other = "; ".join(p for p in kept if p)
    head = ""
    self_first = False
    # the text before the first rule (before the standalone "xưng" when there is no rule), when address_pair
    # reads phrases in it
    anchor = first_rule_piece if first_rule_piece >= 0 else self_piece - 1
    before = [p for p in kept[:anchor + 1] if p] if anchor >= 0 else []
    if before and address_phrases("; ".join(before)):
        head = "; ".join(before)
        self_first = first_rule_piece >= 0 and 0 <= self_piece <= first_rule_piece and not any(
            address_phrases(p) for p in kept[:self_piece])
    return AddressNotes(other=other, self_word=self_word, rules=tuple(rules), head=head,
                        self_after=bool(self_word) and self_after, self_first=bool(self_word) and self_first)


def parse_notes(notes: str) -> AddressNotes:
    """The rows, standalone "xưng" and remaining text of `notes` (see the module docstring). The result is
    a fixed point: joining the leftover pieces may turn one into a clean segment, so the rendering is parsed
    again (each rendering keeps what address_pair reads) until nothing moves."""
    an = _parse_once(unicodedata.normalize("NFC", notes or ""))
    for _ in range(4):
        again = _parse_once(render_notes(an))
        if again == an:
            break
        an = again
    return an


def _quoted(cell: str) -> str:
    word, aside = split_word(cell)
    return f"'{word}'" + (f" ({aside})" if aside else "")


def _split_head(other: str, head: str) -> tuple[str, str]:
    """`other` as (the part written before the rows, the part after them) when it still starts with `head`;
    text the user added right after the head stays with it (up to the next "; ")."""
    rest = other[len(head):]
    m = re.match(r"\s*;\s*", rest)
    if m:
        return head, rest[m.end():]
    cut = rest.find("; ")
    return (other, "") if cut < 0 else (head + rest[:cut], rest[cut + 2:])


def render_notes(an: AddressNotes) -> str:
    """Notes text for `an`: deterministic; incomplete or invalid rows are left out. The rest of the text
    comes first, unless it still holds address phrases address_pair reads ("khi giận thì xưng 'tao'"):
    then the part of it that came before the rows (`head`) stays before them and the rest goes after
    them, so address_pair meets the same first "xưng" / "gọi X là" as in the notes the rows came from."""
    rows = []
    for r in complete_rules(an):
        listener = _clean(r.listener)
        if _clean(r.call):
            s = f"gọi {listener} là {_quoted(r.call)}"
            if _clean(r.own):
                s += f", xưng {_quoted(r.own)}"
        else:
            s = f"với {listener} xưng {_quoted(r.own)}"
        rows.append(s)
    sw = []
    if _clean(an.self_word) and word_problem(an.self_word) is None:
        sw = [f"xưng {_quoted(an.self_word)}"]
    own_first = an.self_after and any(_clean(r.own) for r in complete_rules(an))
    address = [*rows, *sw] if own_first else [*sw, *rows]
    other = _strip_piece(_clean(an.other))
    if not other:
        return "; ".join(address)
    head = _strip_piece(_clean(an.head))
    if head and other.startswith(head) and address:
        first, tail = (_strip_piece(p) for p in _split_head(other, head))
        if an.self_first and not own_first:
            return "; ".join(p for p in (*sw, first, *rows, tail) if p)
        return "; ".join(p for p in (first, *address, tail) if p)
    return "; ".join([*address, other] if address_phrases(other) else [other, *address])


def address_phrases(text: str, ignore: Sequence[str] = ()) -> list[str]:
    """Address phrases left in free text that address_pair may still read ("thường gọi Ren là 'anh'");
    `ignore`: names whose "gọi <name> là" is no rule of this character (its own names: "Học sinh gọi
    Sensei là 'Sensei'" in Sensei's notes says how others address it)."""
    skip = {_clean(n).casefold() for n in ignore if _clean(n)}
    out = []
    for m in _ADDRESS_PHRASE.finditer(text or ""):
        phrase = _clean(m.group(0))
        named = re.match(r"(?:gọi|calls?)\s+(.+?)\s+(?:là\s*)?[" + _Q + "]", phrase, re.IGNORECASE)
        if named and named.group(1).casefold() in skip:
            continue
        out.append(phrase)
    return out


# Words an unquoted phrase may use: pronouns and kinship words ("xưng hô", "xưng vương" are no pronoun);
# a "Gọi là" word may also be a capitalised name or title ("gọi Aris là Aris", "là Sensei").
_BARE_WORDS = frozenset(w for w in (*SELF_WORDS, *CALL_WORDS) if " " not in w) | {
    "bạn", "nhóc", "cưng", "cha", "má", "ba", "chàng", "nàng", "thiếp", "trẫm", "huynh", "đệ", "tỷ", "muội", "lão"}


def _unquoted_matches(text: str):
    for m in _UNQUOTED.finditer(text or ""):
        if m.group("s") is not None:
            if m.group("s").casefold() in _BARE_WORDS:
                yield m
            continue
        call, own = m.group("c"), m.group("o")
        if listener_problem(m.group("l")) or not (call.casefold() in _BARE_WORDS or call[:1].isupper()):
            continue
        if own is not None and own.casefold() not in _BARE_WORDS:
            continue
        yield m


def unquoted_phrases(text: str) -> list[str]:
    """Address phrases typed without the quotes address_pair needs ("Gọi Ren là con, xưng ta"): address_pair
    ignores them, convert_unquoted() turns them into rows."""
    return [_clean(m.group(0)) for m in _unquoted_matches(text)]


def convert_unquoted(an: AddressNotes, self_pronoun: str = "") -> tuple[AddressNotes, str]:
    """(`an` with the unquoted phrases of its rest turned into rows, the Tự xưng to use): "gọi X là y[, xưng
    z]" becomes the row (X, y, z); a lone "xưng z" becomes Tự xưng when there is none, else the standalone
    "xưng" (if the notes have none). The rest keeps everything else."""
    text = an.other or ""
    rules = list(an.rules)
    self_word, own = an.self_word, _clean(self_pronoun)
    pieces: list[str] = []
    last = 0
    for m in _unquoted_matches(text):
        if m.group("s") is not None:
            word = _clean(m.group("s"))
            if not own:
                own = word
            elif word.casefold() != own.casefold() and not self_word:
                self_word = word
        else:
            rules.append(AddressRule(_clean(m.group("l")), _clean(m.group("c")), _clean(m.group("o") or "")))
        pieces.append(text[last:m.start()])
        last = m.end()
    pieces.append(text[last:])
    n = len(pieces)
    other = "; ".join(p for p in (" ".join(_strip_piece(x, lead=i > 0, trail=i < n - 1).split())
                                  for i, x in enumerate(pieces)) if p)
    head = an.head if an.head and other.startswith(an.head) else ""
    return replace(an, other=other, rules=tuple(rules), self_word=self_word, head=head), own


def rename_listener(notes: str, old: str, new: str) -> str:
    """`notes` with the listener `old` of its address rules ("gọi Ren là", "với Ren xưng", "calls Ren") renamed
    to `new` (a character's name changed); everything else verbatim."""
    old, new = _clean(old), _clean(new)
    if not old or not new or old == new or not notes:
        return notes
    name = re.escape(old)
    hon = r"(?:(?:ông|bà|anh|chị|cô|chú|bác|em|cậu|bạn|ngài)\s+)?"
    pattern = re.compile(rf"(\b(?:gọi|với|calls?)\s+{hon}){name}(?=\s+(?:là|thì|xưng|[{_Q}]))", re.IGNORECASE)
    return pattern.sub(lambda m: m.group(1) + new, notes)


# --- what address_pair makes of it ------------------------------------------------------------------------

def _named(characters: Sequence[Character]) -> tuple[Character, ...]:
    return tuple(c for c in characters if _clean(c.name))


def category_understood(listener: str) -> bool:
    """address_pair treats "gọi <listener> là 'x'" as a rule for anyone (khách, người lạ, mọi người...)."""
    text = _clean(listener)
    if not text or listener_problem(text):
        return False
    probe = Character(_PROBE, "", "", "", f"gọi {text} là 'x'")
    return address_pair(_PROBE, None, TranslationContext(characters=(probe,)))[1:] == ("x", True)


def rule_target(listener: str, characters: Sequence[Character]) -> Character | None:
    """The character a "gọi <listener> là" note pairs with, as address_pair matches it (name or Vietnamese
    name, optionally after an honorific such as "ông" / "cô"); None for a category or an unknown name."""
    text = _clean(listener)
    if not text or listener_problem(text) or category_understood(text):
        return None
    probe = Character(_PROBE, "", "", "", f"gọi {text} là 'x'")
    for c in _named(characters):
        lc = replace(c, self_pronoun="")
        if address_pair(_PROBE, c.name, TranslationContext(characters=(probe, lc)))[1:] == ("x", True):
            return c
    return None


def notes_own(notes: str) -> str | None:
    """The word address_pair takes from the notes for the speaker itself when it has no self_pronoun."""
    probe = Character(_PROBE, "", "", "", f"{notes or ''}; gọi {_PROBE_LISTENER} là 'x'")
    listener = Character(_PROBE_LISTENER, "", "")
    own, _other, ok = address_pair(_PROBE, _PROBE_LISTENER, TranslationContext(characters=(probe, listener)))
    return own if ok else None


def _category_rules_removed(speaker: Character) -> Character:
    an = parse_notes(speaker.notes)
    kept = tuple(r for r in an.rules if not category_understood(r.listener))
    return replace(speaker, notes=render_notes(replace(an, rules=kept)))


def _same(a: Character, b: Character) -> bool:
    return _clean(a.name).casefold() == _clean(b.name).casefold()


def pair_infos(speaker: Character, characters: Sequence[Character]) -> list[PairInfo]:
    """address_pair for `speaker` toward every other character of `characters` (in order), then toward a
    listener the characters do not name (None). `characters` may or may not include the speaker; the
    speaker's entry is replaced by `speaker` (the editor passes the notes being edited).

    address_pair only reads the speaker and the listener (character names are unique in a profile), so
    each call gets just those two: the same answer as with the whole profile, without indexing every
    name once per listener (on the 80-character Blue Archive profile about a third of the time)."""
    if not _clean(speaker.name):
        return []
    chars = tuple(c for c in _named(characters) if not _same(c, speaker))
    no_cat = _category_rules_removed(speaker)
    out: list[PairInfo] = []
    for lc in chars:
        own, other, ok = address_pair(speaker.name, lc.name, TranslationContext(characters=(speaker, lc)))
        source = ""
        if ok:  # only a label: the two characters alone say where the pair comes from
            if address_pair(speaker.name, lc.name, TranslationContext(
                    characters=(no_cat, replace(lc, self_pronoun=""))))[2]:
                source = "rule"
            elif address_pair(speaker.name, lc.name, TranslationContext(characters=(no_cat, lc)))[2]:
                source = "derived"
            else:
                source = "category"
        out.append(PairInfo(lc, own, other, ok, source))
    own, other, ok = address_pair(speaker.name, None, TranslationContext(characters=(speaker,)))
    out.append(PairInfo(None, own, other, ok, "category" if ok else ""))
    return out


# --- suggestions ---------------------------------------------------------------------------------------------

# (keywords in the role text, word for nam, nữ, unknown); the first match wins.
_ROLE_SELF = (
    (("giáo viên", "cô giáo", "thầy giáo", "giảng viên", "chủ nhiệm", "hiệu trưởng", "thầy của", "cô của"),
     "thầy", "cô", "tôi"),
    (("sư phụ", "chúa tể", "ma vương", "nữ hoàng", "nhà vua"), "ta", "ta", "ta"),
    (("cháu nội", "cháu ngoại", "bé gái", "bé trai", "đứa bé", "đứa trẻ"), "cháu", "cháu", "cháu"),
    (("bà nội", "bà ngoại", "bà lão", "bà cụ"), "bà", "bà", "bà"),
    (("ông nội", "ông ngoại", "ông lão", "ông cụ"), "ông", "ông", "ông"),
    (("người mẹ", "mẹ của"), "mẹ", "mẹ", "mẹ"),
    (("người cha", "bố của", "cha của"), "bố", "bố", "bố"),
    (("anh trai", "đàn anh", "senpai"), "anh", "anh", "anh"),
    (("chị gái", "đàn chị"), "chị", "chị", "chị"),
    (("đàn em", "em gái", "em trai", "kouhai"), "em", "em", "em"),
    (("bạn thân", "bạn cùng lớp", "bạn học"), "tớ", "tớ", "tớ"),
    (("khách hàng", "chủ tiệm", "ông chủ", "bà chủ", "quản lý"), "tôi", "tôi", "tôi"),
    (("học sinh", "sinh viên", "nhân viên"), "em", "em", "em"),
)
_GENDER = {"nam": 0, "male": 0, "m": 0, "nữ": 1, "nu": 1, "female": 1, "f": 1}
# what the speaker's own word suggests for the listener: (nam / unknown, nữ)
_CALL_FROM_OWN = {
    "em": ("anh", "chị"), "anh": ("em", "em"), "chị": ("em", "em"), "tao": ("mày", "mày"), "tớ": ("cậu", "cậu"),
    "mình": ("cậu", "cậu"), "ta": ("ngươi", "ngươi"), "cháu": ("ông", "bà"), "con": ("bố", "mẹ"),
    "ông": ("cháu", "cháu"), "bà": ("cháu", "cháu"), "chú": ("cháu", "cháu"), "bác": ("cháu", "cháu"),
    "dì": ("cháu", "cháu"), "thầy": ("em", "em"), "cô": ("em", "em"), "mẹ": ("con", "con"), "bố": ("con", "con"),
    "cha": ("con", "con"), "tôi": ("anh", "chị"),
}
_CALL_FOR_GROUP = {"khách": "quý khách", "khách hàng": "quý khách", "người lạ": "bạn", "người khác": "bạn",
                   "mọi người": "mọi người", "học sinh": "em", "sensei": "Sensei"}


def _gender(g: str) -> int | None:
    return _GENDER.get((g or "").strip().casefold())


def suggest_self(gender: str, role_text: str = "") -> str:
    """A Tự xưng to propose from the gender and the role words of the notes ("giáo viên", "bà nội",
    "học sinh"...); "tôi" (neutral) when nothing says more."""
    role = _clean(role_text).casefold()
    g = _gender(gender)
    for words, male, female, unknown in _ROLE_SELF:
        if any(w in role for w in words):
            return male if g == 0 else female if g == 1 else unknown
    return "tôi"


# What to call a listener whose role or own word marks them as an elder / teacher: (keywords in its notes,
# word for nam / unknown, nữ), then its own word -> the word.
_LISTENER_ROLE = (
    (("thầy của", "cô của", "sư phụ", "giáo viên", "thầy giáo", "cô giáo", "giảng viên", "hiệu trưởng", "chủ nhiệm"),
     "thầy", "cô"),
    (("ông nội", "ông ngoại", "ông lão", "ông cụ"), "ông", "ông"),
    (("bà nội", "bà ngoại", "bà lão", "bà cụ"), "bà", "bà"),
    (("người mẹ", "mẹ của"), "mẹ", "mẹ"),
    (("người cha", "bố của", "cha của"), "bố", "bố"),
)
_ELDER_SELF = {"thầy": "thầy", "cô": "cô", "ông": "ông", "bà": "bà", "chú": "chú", "bác": "bác", "dì": "dì",
               "lão phu": "ông", "mẹ": "mẹ", "bố": "bố", "cha": "bố"}


def _listener_role_word(listener: Character) -> str:
    role = _clean(listener.notes).casefold()
    female = _gender(listener.gender) == 1
    for words, male_word, female_word in _LISTENER_ROLE:
        if any(w in role for w in words):
            return female_word if female else male_word
    return _ELDER_SELF.get(_clean(listener.self_pronoun).casefold(), "")


def suggest_call(own: str, listener: Character | None = None, listener_text: str = "") -> str:
    """A "Gọi là" word to propose: what address_pair derives from the listener's Tự xưng when that fits
    the speaker's (anh -> anh, tao -> mày, tớ -> cậu...); for a listener that is an elder or teacher by its
    notes or own word, that title (thầy, cô, ông, bà...); for a listener without a Tự xưng, from the
    speaker's own word and the listener's gender (em -> anh / chị, cháu -> ông / bà...); a default for
    groups. "" when unsure (a listener whose Tự xưng gives no pair with the speaker's)."""
    own = _clean(own).casefold()
    if listener is not None:
        if _clean(listener.self_pronoun):
            s = Character(_PROBE, "", "", own)
            lc = Character(_PROBE_LISTENER, "", listener.gender, listener.self_pronoun)
            _own, other, ok = address_pair(_PROBE, _PROBE_LISTENER, TranslationContext(characters=(s, lc)))
            if ok and other:
                return other
        role = _listener_role_word(listener)
        if role:
            return role
        if _clean(listener.self_pronoun):
            return ""  # e.g. "tớ" toward someone who says "tôi": no peer word to guess
    if listener is None:
        group = _CALL_FOR_GROUP.get(_clean(listener_text).casefold())
        if group:
            return group
    pair = _CALL_FROM_OWN.get(own)
    if pair is None:
        return ""
    if own == "tôi" and (listener is None or _gender(listener.gender) is None):
        return "bạn"
    return pair[1] if listener is not None and _gender(listener.gender) == 1 else pair[0]
