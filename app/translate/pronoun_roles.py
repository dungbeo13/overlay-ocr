"""Who each Vietnamese pronoun of a translated line refers to, and whether that fits the source.

Vietnamese pronouns encode who speaks to whom, and the same word changes role with the direction:
a younger sister calls herself "em" and her brother "anh"; he calls himself "anh" and her "em". A
check that only asks whether the words used belong to the pair passes "Anh xin lỗi nhé" from the
sister, where "anh" makes her refer to herself as her brother. This module judges ROLES:

- analyze(): the pronoun uses of a translation and the role each one plays given the speaker's pair
  (own = what the speaker calls themself, other = what they call the listener). Non-pronoun uses are
  left out: third person (anh ấy, cô ta), kinship and common nouns (anh trai, em gái, cô giáo, con
  mèo, bạn bè), plurals and groups (chúng em, các anh, anh em, bà cháu), compounds (tôi luyện, mày
  mò, lông mày, tao nhã, cô đơn), titles before a name (anh Kenji) and parts of names (Tuấn Anh).
  Vocatives (Anh ơi, thưa cô, ", em.", "Có sao không em?") always address the listener.
- source_frames(): high-precision speech acts of the SOURCE line with the semantic role of their
  agent and patient: apology / thanks by the speaker or demanded from the listener, forgiveness,
  requests to the listener, offers and volition of the speaker, questions about the listener's state
  (大丈夫？, 怪我はない？, 何してるの？, 寝てる？, "Are you okay?") and plain statements of the speaker's own
  (お腹すいた！, 怒ってないよ, うれしいよ, 寒い…, うん、大丈夫), greetings (ただいま / "I'm home" and 行ってきます /
  お先に失礼します by the speaker, おかえり / 行ってらっしゃい for the listener, いただきます, ごちそうさま,
  おやすみ), explicit source pronouns. Japanese drops subjects, so a frame is the strongest signal of who
  does what. Reported and quoted acts are no frames (謝れって言われた, ごめんって伝えてって, 『ごめん』って言えば,
  「ただいま」って, 『大丈夫か？』って聞いた, 『俺に任せろ』って言った, "Did he say sorry", "told me to say
  sorry"); brackets around the whole line and a speaker label (セラ：…, セラ「…」) are no quotes. 私が (and
  先生が / お姉ちゃんが from the teacher / the older sister, a role word the speaker calls themself by)
  counts as "I will" only in a main clause (not 私が卒業したら, 私がついてるから、). Cues no development line
  tested a rewrite on (ごちそうさま, 任せなさい, 何とかする, a role word + が) are Frame.tentative: their
  issues have basis "cue" (asked again, never rewritten). Benefactives ("favour": who does an act for whom,
  弁当作ってくれたのか, 手伝ってもらって悪いな, クッキー焼いてあげるね, "You made this for me?", "I'll cook for
  you") only where no one else may be the giver or the one it is for (_ja_favours: not 母さんが作ってくれた
  to a friend, ケンジくんが…, 〜てくれたら, 『買ってやる』って, 手伝ってもらった？, 助けてくれたのはケンジくん, 〜てやるな,
  自分でやる / 一緒にやろう: the particle で and an adverb in に; but これ、あげる, この前あげたマフラー、使ってる？, and a
  name called in a question or thanks: たくみくん、…くれたの？); always tentative.
- check_line(): maps frames to Vietnamese anchors (xin lỗi, cảm ơn, tha lỗi, giúp, nhờ, làm ơn,
  cho ... , để <pronoun>, <pronoun> sẽ, <pronoun> đi (học) đây, có sao / bị thương / mệt / đói / làm gì,
  về rồi, mừng ... về, xin phép, mời, chúc, ngủ ngon) and checks the pronoun in the subject slot
  (directly before the anchor, adverbs such as cũng / thật / rất and the focus "mới là người phải"
  allowed) and the object slot (directly after). A question frame bears on a Vietnamese question, a
  statement of the speaker's on a statement. When source and translation have as many sentences,
  each frame bears on its own sentence only ("Why should I apologize? You apologize!"). An explicit
  source pronoun with a clear slot (お前は…じゃ, 私の, お前を, お前は俺が守る, "I'll protect you") fills the
  same slot of its sentence (_pronoun_slots: as many subjects / possessors / objects on both sides,
  simple sentences only; "your hand" -> "tay mẹ" is an "owner"; the subject of "sẽ" that such a
  pronoun names too, 私がやる -> "Anh sẽ làm", is reported under the pronoun). With `expected` (a role-tagged
  reference, "{S:Em} xin lỗi {L:anh}"), the reference's slots and subject-verb contexts are checked as
  well. "Ý em là sao?" (what do you mean) needs the listener. The owner of a thing is no subject of a
  state word ("Hộp cơm của em có ổn không?"; but "Chân anh có sao không?"). A benefactive (or a request
  for the speaker) needs the one who does it first in the clause of "cho / giúp / giùm / hộ / thay / tặng
  <the one it is for>" and the one it is for after it ("Em làm cơm hộp cho anh à?" from the brother for
  弁当作ってくれたのか, not "Anh làm bữa trưa cho em à?"), "<for> được <giver> <verb>" or "<for> nhận … từ
  <giver>" (_favour_slots: not "cho phép / cho nên / cho kịp", the causative "làm cho anh vui", "cho" before a
  thing, a converse verb's subject (mượn, xin), a possessor ("áo của con giúp con"), a clause with another
  person, a report, a condition (nếu, "… thì"), a sentence whose own acts go both ways); issues anchor
  "favour", with RoleIssue.act saying who does it for whom. A source sentence with acts in several clauses
  (手伝ってくれてありがとう、今度は私が手伝ってあげるね; 手伝ってくれたら、ジュースおごってあげる; 迎えに来てくれて
  ありがとう、荷物持つね) has its act anchors and favour slots judged per clause when the clauses line up
  (_Expect.split_acts), else not at all; no help / give / favour slot is judged by a sentence with favours both
  ways (_ben_directions).

High precision first: a slot is only judged when its frame is unambiguous (frames that disagree on
who acts switch the check off), when the pronoun's role follows from the pair (or is inherently
first / second person: tôi, tao, tớ / mày, bạn, ngươi) and the pronoun is adjacent to the anchor;
not when the act is reported or caused ("Mẹ bảo anh phải xin lỗi em", "nhắn là xin lỗi em"),
conditional ("Nếu anh giúp em") or has another subject ("nhân viên sẽ giúp anh", "Kenji đã xin lỗi
anh"), and nothing in a line that calls someone outside the pair ("Chị ơi" toward a stranger) or
uses a kinship pronoun outside it ("Để chị dán băng cho em" from a sister who says "em" to her
brother: she is talking to a child).
What it cannot judge: pronouns outside an anchor and without an explicit source pronoun (most of
them: "Anh đã bắt được" with no frame), a wrong word in the right role (tôi for em: the pair check's
job), sentences restructured around another verb ("I'll have it ready" -> "Thứ Sáu anh sẽ lấy được
máy" is flagged although right), source pronouns in reported, quoted, conditional, passive, emotive
(好き, 大事), reflexive ("yourself", "your own", 自分) or request sentences, in a time clause (俺が戻るまで),
coordinated ("you and I"), a generic "you" ("You never know"), an English "we" with no "you" apart from it
(it may include the listener: "We'll be late!" -> "Con sẽ muộn mất"), translations that make the source's
subject an agent, a cause or a converse verb's partner ("Em được anh cứu", "Em hết hồn vì anh", "Tao thua
mày" for "You saved / scared / beat me") or a topic ("Em thì để anh đưa về"), and source cues outside the
lists below (most volitionals and imperatives). Spans index the NFC-normalised translation (identical
for the usual NFC text).
"""
from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left, bisect_right, insort
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

__all__ = ["SELF", "LISTENER", "UNKNOWN", "PronounUse", "Frame", "RoleIssue", "analyze", "source_frames",
           "check_line", "judged_slots", "parse_ref", "ref_disagreements", "anchor_classes"]

SELF, LISTENER, UNKNOWN = "self", "listener", "unknown"
SELF_AS_LISTENER = "self_as_listener"  # the speaker refers to themself with a listener word
LISTENER_AS_SELF = "listener_as_self"  # the listener is referred to with the speaker's own word

Pair = str | Iterable[str] | None


@dataclass(frozen=True)
class PronounUse:
    word: str  # as written
    start: int
    end: int
    role: str  # SELF | LISTENER | UNKNOWN
    vocative: bool = False  # Anh ơi / thưa anh / ", anh.": addresses the listener


@dataclass(frozen=True)
class Frame:
    # apology | thanks | forgive | request | offer | volition | state | home | go | eat | goodnight | favour
    # | self_ref | listener_ref. "favour": a benefactive (〜てくれた, 〜てもらって, 〜てあげる, "you made this for
    # me", "I'll cook for you"): agent = the one who does it, patient = the one it is done for
    kind: str
    agent: str | None  # SELF (the speaker) | LISTENER | None
    patient: str | None  # who it is done to / for, None when the cue does not say
    cue: str
    start: int
    end: int
    # a newer cue no development line has tested a rewrite on (ごちそうさま, 任せなさい, 何とかする, a role word
    # the speaker calls themself by + が): its slots are asked again, never rewritten (RoleIssue.basis "cue")
    tentative: bool = False


@dataclass(frozen=True)
class RoleIssue:
    kind: str  # SELF_AS_LISTENER | LISTENER_AS_SELF
    start: int  # span of the wrong pronoun in the translation
    end: int
    found: str
    expected_word: str | None  # the pair's word for the role the slot needs, capitalised like `found`
    message: str
    # "frame" (a source speech act), "sentence" (a speech act of its own source sentence only, while the
    # line as a whole has the other person do it too), "cue" (a tentative frame's act, Frame.tentative),
    # "vocative", "phrase" ("Ý em là sao?"), "pronoun" (an explicit source pronoun's slot) or "ref"
    basis: str = "frame"
    # the slot's anchor class (apology, thanks, help, state, home, go, ...), "vocative", "meaning", "context",
    # "favour" (who does a benefactive act / whom it is for: "Em làm cơm cho anh à?") or, for basis "pronoun",
    # "subject" / "possessor" / "object"
    anchor: str = ""
    # a slot of who does an act for whom (anchor favour, give, help): (the one who does it, the one it is done
    # for, the source cue, "request" | "favour"), for a note that says so in plain words (llm_prompt.role_note)
    act: tuple[str, str, str, str] | None = None


# --- Vietnamese words ------------------------------------------------------------------------------

_FIRST = frozenset({"tôi", "tao", "tớ"})  # first person whatever the pair says
_SECOND = frozenset({"mày", "bạn", "ngươi"})  # second person whatever the pair says
_KIN = frozenset({"anh", "em", "chị", "ông", "bà", "cô", "chú", "bác", "cháu", "con", "thầy", "bố", "mẹ", "cha",
                  "má", "ba", "dì", "cậu", "mợ", "thím", "dượng"})
_BASE = frozenset({"tôi", "tớ", "mình", "tao", "mày", "bạn", "cậu", "anh", "em", "chị", "ông", "bà", "cháu", "con",
                   "cô", "chú", "bác", "thầy", "dì", "ngươi", "ngài", "quý khách", "tiền bối"})
_PAIR_ONLY = frozenset({"mình", "ta", "con", "bố", "mẹ", "cha", "má", "ba"})  # pronouns only when in the pair
_MULTI = ("quý khách", "tiền bối")
_PLURAL_BEFORE = frozenset({"chúng", "các", "bọn", "tụi", "mấy", "những", "mọi", "hai", "vài",
                            "đứa", "thằng", "một", "người", "cái"})  # plurals; classifiers: "đứa cháu ngoan"
_THIRD_AFTER = frozenset({"ấy", "ta", "nọ"})  # anh ấy, cô ta, ông nọ
_DEMONSTRATIVE_AFTER = frozenset({"này", "đấy", "đó", "kia"})  # "anh này nói..." (not "trả mày này.", "bà đấy à")
_FINAL_PARTICLES = frozenset({"à", "ạ", "nhé", "nha", "nhỉ", "chứ", "hả", "ư", "thôi", "mà", "đâu", "ơi", "nữa"})
# A pronoun syllable followed / preceded by these is a noun or part of a compound, not a pronoun.
_NOT_AFTER = {
    "anh": {"trai", "hùng", "ruột", "họ", "rể", "cả", "linh", "dũng", "minh", "tuấn", "đào", "quốc", "chàng", "bạn",
            "hai", "út", "lớn"},
    "em": {"gái", "trai", "bé", "út", "họ", "ruột", "dâu", "rể", "nhỏ"},
    "chị": {"gái", "ruột", "họ", "dâu", "cả", "hai"},
    "ông": {"nội", "ngoại", "trời", "chủ", "lão", "cụ", "già", "xã", "bụt", "tổ"},
    "bà": {"nội", "ngoại", "cụ", "lão", "già", "chủ", "xã", "bầu", "đỡ"},
    "cháu": {"gái", "trai", "nội", "ngoại", "chắt", "đích"},
    "con": {"gái", "trai", "người", "đường", "vật", "số", "cái", "nít", "mèo", "chó", "gà", "cá", "chim", "bò", "heo",
            "lợn", "ngựa", "chuột", "sâu", "bọ", "kiến", "ong", "rắn", "khỉ", "tàu", "dao", "thuyền", "đò", "sông",
            "suối", "mắt", "tim", "dấu", "rối", "đẻ", "nuôi", "ruột", "út", "cả", "đầu", "bé", "nhỏ", "búp", "quay",
            "diều", "tem", "trỏ", "chữ", "bài", "đực", "cái"},
    "cô": {"giáo", "gái", "bé", "đơn", "độc", "dâu", "nương", "lập", "đặc", "đọng", "nhi", "nàng", "út", "hồn"},
    "chú": {"ý", "thích", "tâm", "trọng", "giải", "bé", "rể", "thuật", "chó", "mèo", "chim", "bộ", "lùn", "cuội"},
    "bác": {"sĩ", "bỏ", "học", "ái", "vật"},
    "thầy": {"giáo", "thuốc", "bói", "trò", "tu", "lang", "cúng", "pháp", "dạy", "đồ"},
    "cậu": {"bé", "chủ", "ấm", "ruột", "bạn", "học", "nhóc", "trai"},
    "bạn": {"bè", "thân", "học", "gái", "trai", "cùng", "của", "bạn", "đời", "đọc", "đồng", "hữu", "nghề", "nhậu",
            "chí", "tù"},
    "tôi": {"luyện", "rèn", "tớ"},
    "tao": {"nhã", "khang", "ngộ", "đàn", "loạn", "nhân"},
    "mày": {"mò", "râu"},
    "mình": {"mẩy"},
    "dì": {"ghẻ"},
}
_NOT_BEFORE = {
    "anh": {"tiếng", "nước", "người"}, "em": {"người"}, "chị": {"người"}, "bạn": {"người", "kết", "làm", "bè"},
    "mày": {"lông", "nhíu", "chân", "cau", "chau", "nhăn"},
    "mình": {"một", "tự", "thân", "bản", "hết", *_KIN},  # "bà cháu mình": we
    "tớ": {"đầy", "tôi"}, "ta": {"người", "hắn", "nó"},
}
_VOC_PARTICLES = frozenset({"ạ", "à", "nhé", "nha", "ơi", "nhỉ"})
_VOC_LEAD = frozenset({"thưa"})
# "… không em?", "Ăn chưa con?" (not "sao": "Tại sao em?" may be "why me?")
_Q_FINAL = frozenset({"không", "chưa", "à", "hả", "chứ", "nhỉ", "vậy", "thế", "ư", "nào"})
_SENT_END = re.compile(r"[.!?…。！？]")
_BREAK = re.compile(r"[^\s]")  # anything but whitespace between two words breaks adjacency
_WORD = re.compile(r"[^\W\d_]+")


@dataclass
class _Tok:
    text: str
    low: str
    start: int
    end: int
    gap: str  # the text between the previous word and this one ("" for the first word)

    @property
    def adjacent(self) -> bool:
        """Only whitespace between this word and the previous one."""
        return bool(self.gap) and not _BREAK.search(self.gap)


def _tokens(text: str, multi: Iterable[str] = ()) -> list[_Tok]:
    """Words of `text`; two-syllable pronouns (quý khách, tiền bối, ...) become one token."""
    raw = [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(text)]
    multis = {m for m in (*_MULTI, *multi) if " " in m}
    out: list[_Tok] = []
    i, prev_end = 0, 0
    while i < len(raw):
        w, s, e = raw[i]
        if i + 1 < len(raw) and not _BREAK.search(text[e:raw[i + 1][1]]) \
                and f"{w.lower()} {raw[i + 1][0].lower()}" in multis:
            w2, _, e2 = raw[i + 1]
            out.append(_Tok(text[s:e2], f"{w.lower()} {w2.lower()}", s, e2, text[prev_end:s] if out else ""))
            prev_end, i = e2, i + 2
            continue
        out.append(_Tok(w, w.lower(), s, e, text[prev_end:s] if out else ""))
        prev_end, i = e, i + 1
    return out


def _words(p: Pair) -> tuple[str, ...]:
    if not p:
        return ()
    items = [p] if isinstance(p, str) else list(p)
    return tuple(dict.fromkeys(w.strip().lower() for w in items if w and w.strip()))


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def _is_upper(tok: _Tok) -> bool:
    return tok.text[:1].isupper()


def _sentence_start(toks: list[_Tok], i: int) -> bool:
    return i == 0 or bool(_SENT_END.search(toks[i].gap)) or bool(re.search(r"[\"“”'‘’「『(\[—–-]", toks[i].gap))


def _excluded(toks: list[_Tok], i: int, words: frozenset[str]) -> bool:
    """toks[i] (a pronoun-looking word) is not a pronoun use: third person, a noun, a group, a title
    or part of a name."""
    t = toks[i]
    w = t.low
    nxt = toks[i + 1] if i + 1 < len(toks) and toks[i + 1].adjacent else None
    prv = toks[i - 1] if i > 0 and t.adjacent else None
    after_next = toks[i + 2] if nxt is not None and i + 2 < len(toks) and toks[i + 2].adjacent else None
    if prv is not None and (prv.low in _PLURAL_BEFORE or prv.low in _NOT_BEFORE.get(w, ())):
        return True
    if nxt is not None:
        if nxt.low in _THIRD_AFTER or nxt.low in _NOT_AFTER.get(w, ()):
            return True
        if nxt.low in _DEMONSTRATIVE_AFTER and after_next is not None and after_next.low not in _FINAL_PARTICLES:
            return True
        if _is_upper(nxt) and nxt.low not in words:  # anh Kenji, ông Carter, bà Nà: a title before a name
            return True
        if w == "bạn" and (nxt.low in words or nxt.low in _BASE or nxt.low in ("ta", "mình")):
            return True  # "xe của bạn anh": the friend of a person, not "you"
    if w in _KIN and ((nxt is not None and nxt.low in _KIN) or (prv is not None and prv.low in _KIN)):
        return True  # anh em, chị em, ông bà, bà cháu, bố anh: a group or a kinship noun phrase
    if prv is not None and _is_upper(t) and _is_upper(prv) and not _sentence_start(toks, i):
        return True  # Tuấn Anh: part of a name
    return False


def _vocative(toks: list[_Tok], i: int) -> bool:
    t = toks[i]
    nxt = toks[i + 1] if i + 1 < len(toks) and toks[i + 1].adjacent else None
    prv = toks[i - 1] if i > 0 and t.adjacent else None
    if nxt is not None and nxt.low == "ơi":
        return True
    if prv is not None and prv.low in _VOC_LEAD:
        return True
    if prv is not None and prv.low == "này" and _sentence_start(toks, i - 1):
        return True  # "Này anh, ..."
    if i > 0 and ("," in t.gap and not _SENT_END.search(t.gap) or prv is not None and prv.low in _Q_FINAL):
        # ", anh." / ", em ạ!" at the end of a sentence; "Có sao không em?", "Ăn chưa con?": after a final
        # question particle
        j = i + 1
        if j < len(toks) and toks[j].adjacent and toks[j].low in _VOC_PARTICLES:
            j += 1
        return j >= len(toks) or bool(_SENT_END.search(toks[j].gap))
    return False


def _role(w: str, own: tuple[str, ...], other: tuple[str, ...]) -> str:
    in_own, in_other = w in own, w in other
    if in_own and not in_other:
        return SELF
    if in_other and not in_own:
        return LISTENER
    if in_own and in_other:
        return UNKNOWN
    return SELF if w in _FIRST else LISTENER if w in _SECOND else UNKNOWN


def _analyze(toks: list[_Tok], own: tuple[str, ...], other: tuple[str, ...]) -> dict[int, PronounUse]:
    words = frozenset((*own, *other))
    out: dict[int, PronounUse] = {}
    for i, t in enumerate(toks):
        w = t.low
        if not (w in words or (w in _BASE and w not in _PAIR_ONLY)):
            continue
        if _excluded(toks, i, words):
            continue
        if _vocative(toks, i):
            out[i] = PronounUse(t.text, t.start, t.end, LISTENER, True)
        else:
            out[i] = PronounUse(t.text, t.start, t.end, _role(w, own, other))
    return out


def analyze(translation: str, own: Pair, other: Pair) -> list[PronounUse]:
    """The pronoun uses in `translation` with the role each plays for a speaker who calls themself
    `own` and the listener `other` (a word or several: ("anh", "tiền bối")). Words outside the
    pair are SELF / LISTENER only when inherently first / second person (tôi, tao, tớ / mày, bạn,
    ngươi), else UNKNOWN; a vocative is always the LISTENER."""
    own_w, other_w = _words(own), _words(other)
    toks = _tokens(_nfc(translation), (*own_w, *other_w))
    return list(_analyze(toks, own_w, other_w).values())


# --- source frames ---------------------------------------------------------------------------------

_JA_SELF = r"(?:わたくし|わたし|あたし|アタシ|俺|オレ|おれ|僕|ボク|ぼく|私|儂|わし|拙者)"
_JA_OTHER = (r"(?:お前|おまえ|オマエ|あなた|アナタ|あんた|アンタ|(?<![぀-ヿ一-鿿])君|キミ|てめえ|てめぇ|テメェ|テメエ|"
             r"貴様|きさま|お主|おぬし|そなた)")
_END = r"(?=[\s、。！？!?…〜ー」』)）]|$)"
_END_STRICT = r"(?=[\s。！？!?…〜ー」』)）]|$)"  # not 、: "〜て、" is mostly a connective
_END_DECL = r"(?=[\s、。！!…〜ー」』)）]|$)"  # a statement: "手伝う？" asks whether the LISTENER will
# the て of a te-form; で only after ん / い (読んで, 急いで, 〜ないで), not the particle of 自分で / 後で / 一人で
_TE = r"(?:て|(?<=[んい])で)"
# "ごめんって言って" / "ありがとうと言え": the listener is told to say it. Not 言った / 言ってた / 言われた /
# 言えば (reported or hypothetical: who says it is not the listener's act asked for here).
_SAY_IMP = (rf"[』」]?(?:って|と)(?:言|い)(?:って(?:よ|ね|ってば|ください)?{_END_STRICT}|え(?:よ)?{_END}"
            rf"|いなさい|いな(?:よ)?{_END})")
# A quoted or reported word is no act of the speaker: 『ごめん』って, ごめんって伝えて, ありがとうと言ってた
# (but ごめんってば is an emphatic apology).
_QUOTED = r"[』」]?(?:って(?!ば)|と(?:言|い|伝)|で済|じゃ済|じゃな|なんて|[、,](?:って(?!ば)|と(?:言|伝)))"
# Greetings, questions about the listener and the speaker's own feelings: a cue that starts its clause
# (no subject of its own before it: not ケンジは大丈夫？), is no quoted word (「ただいま」って) and ends there.
_CLAUSE_START = r"(?:^|(?<=[\s、。！？!?…「『(（ー〜～]))"
_NOT_QUOTED = r"(?![？?！!。…]*(?:[』」][^\s。！？!?…」』]|[』」]?(?:って(?!ば)|と(?:言|い[っうわ]|聞|伝))))"  # 「…」も言わず
_CUE_END = r"(?:ー|〜|～|っ)*(?=[\s、。！？!?…」』)）]|$)"
_CUE_DECL = r"(?:ー|〜|～|っ)*(?=[\s、。！!…」』)）]|$)"  # not 行ってくる？ ("are you going?": the listener goes)
# A question about the listener may start with the listener (お前、大丈夫か), an adverb or a body part
# (足、大丈夫？, 怪我は): its subject is still the listener ("Chân anh có sao không?").
_STATE_PRE = (rf"{_CLAUSE_START}(?:{_JA_OTHER}(?:は|も|って)?、?\s*)?"
              r"(?:本当に|ほんとに|ほんとうに|ホントに|もう|まだ|全然|ちゃんと|どこも|どこか|どっか|なんか|すごく|かなり|少し"
              r"|ちょっと|あんまり)?"
              r"(?:(?:体|身体|からだ|足|手|頭|腕|顔|傷|具合|調子|気分)(?:は|、)?)?")
_STATE_ASK = (r"(?:大丈夫|平気)(?:だった|でした|なの|なん|です)?"
              r"|お?(?:怪我|けが|ケガ)(?:は|を)?(?:ない|無い|ありません|ございません|なかった|しなかった|した|して(?:ない|ません|いない|る))?"
              r"|お?元気(?:だった|にしてた|にしてる|してた|してる|でした|です)?"
              r"|疲れ(?:てない|てる|た|ました|ていない|てません|ちゃった)"
              r"|(?:寒|暑|痛|眠|怖)(?:くない|い|かった|くなかった)"
              r"|(?:お腹|おなか|腹)(?:が|、)?(?:すいて|空いて|減って|へって)(?:ない|る)|(?:お腹|おなか|腹)(?:すいた|空いた|減った|へった)"
              r"|風邪(?:を)?(?:ひいて|引いて)(?:ない|る)|風邪(?:を)?(?:ひいた|引いた)|熱(?:が)?(?:ある|出て(?:る|ない))"
              r"|寝て(?:る|ない|た)|眠れ(?:た|てる|てない|ない|なかった)"
              r"|どう(?:した|かした)|何(?:を)?(?:して|やって)(?:る|いる|ん|ます)|泣いて(?:る|いる|ん)"
              r"|(?:ご飯|ごはん|朝ご飯|昼ご飯|晩ご飯)(?:は|、)?(?:もう)?(?:食べた|食べて(?:ない|る))|怒って(?:る|ない)")
# the speaker's own feeling or sensation (寒い…, 痛い！: Japanese says these of others only with がる / そう)
_STATE_SELF = (r"(?:ただ|本当に|ほんとに|すごく|とても|めっちゃ|ちょっと|少し|もう|全然|別に)?"
               r"(?:(?:お腹|おなか)(?:が)?(?:すいた|空いた|ぺこぺこ|ペコペコ)|腹(?:減った|へった)|疲れた|怒ってない"
               r"|心配(?:だった|した|してた|して(?:る|た)|なの|なんだ)|(?:嬉し|うれし)(?:い|かった)"
               r"|(?:寒|暑|痛|眠|怖)(?:い|かった|くない))")
# an answer about the speaker's own state: うん、大丈夫。 ("Ừ, em không sao")
_STATE_ANSWER = r"(?:(?<=うん[、。\s])|(?<=はい[、。\s])|(?<=ええ[、。\s])|(?<=ああ[、。\s]))(?:もう|全然|本当に|ほんとに)?(?:大丈夫|平気)"
# (kind, agent, patient, pattern). Agent / patient are the speaker (SELF) or the listener. Within a kind
# the first pattern that covers a span wins (source_frames).
_JA_FRAMES: tuple[tuple[str, str, str | None, re.Pattern], ...] = tuple(
    (k, a, p, re.compile(rx)) for k, a, p, rx in (
        # the speaker as the explicit subject of the act ("なんで私が謝らなきゃ", "私も手伝う"): first, so a
        # later demand in the line conflicts with it instead of deciding alone
        ("apology", SELF, None, rf"{_JA_SELF}(?:が|も|は|こそ)[^、。！？!?…\s「」『』]{{0,4}}?(?<!感)謝"
                                rf"(?!って(?:くれ|ほしい|欲しい|もら))"),
        ("offer", SELF, None, rf"{_JA_SELF}(?:が|も|は|こそ)[^、。！？!?…\s「」『』]{{0,4}}?(?:手伝|助け)"
                              rf"(?!て(?:くれ|ほしい|欲しい|もら))"),
        # the listener is asked / told to apologise or thank (before the speaker's own cues). Only the
        # imperative / request forms: not 謝ってくれた (someone did), 謝れって言われた (reported), 謝ってって
        ("apology", LISTENER, SELF, rf"(?<!感)謝(?:って(?:(?:よ|ね|ってば|ください|下さい|ほしい|欲しい)(?:の)?{_END}"
                                    rf"|(?:くれ|もらえ|いただけ)(?:る|ない|ます|ません|ませんか)?(?:か|かい|かな|の)?{_END}"
                                    rf"|{_END_STRICT})"
                                    rf"|れ(?![るばた]|って(?!ば)|と)|りなさい|罪しろ|罪して|らないで|らなくて|らなく)"
                                    rf"|(?:ごめん|ごめんなさい){_SAY_IMP}|ごめんなさいは[？?]"),
        ("thanks", LISTENER, SELF, rf"ありがとう?{_SAY_IMP}|礼を言え|感謝(?:しろ|しなさい)"),
        ("apology", SELF, LISTENER, rf"ごめん(?!{_QUOTED})|すみません|すいません|すまん|すまない"
                                    r"|済まない|済みません|申し訳(?:ない|ありません|ございません|なかった)"
                                    r"|失礼(?:します|しました|いたします|致します|いたしました)"
                                    r"|(?<!感)謝(?:ります|りたい|罪します)"
                                    rf"|(?:^|(?<=[\s、。！？!?…「『]))(?:{_JA_SELF}(?:が|も)?)?"
                                    rf"(?:悪い|わりい|わりぃ|悪ぃ|わるい|悪かった)(?:な|ね|よ|けど|です)?{_END}"),
        ("thanks", SELF, LISTENER, rf"ありがと(?!う?{_QUOTED})|感謝(?:します|してる|してます|しています"
                                   r"|する|いたします)|サンキュー?"
                                   rf"|(?:ごちそう|ご馳走|御馳走)(?:さま|様)(?:でした)?{_NOT_QUOTED}"),
        ("forgive", LISTENER, SELF, rf"許して(?!あげ|やる|やろう|おく)|勘弁して|見逃して"),
        ("forgive", SELF, LISTENER, r"許して(?:あげ|やる|やろう)|許す(?:よ|から|わ)"),
        ("request", LISTENER, SELF, rf"{_TE}(?:くれ(?:る|ない|ます|ません|ませんか|よ|ないか)?|ちょうだい"
                                    rf"|もらえ(?:る|ない|ます|ません)|いただけ(?:る|ない|ます|ません))"
                                    rf"(?:か|かい|かな|の)?{_END}"
                                    # not the honorific statement ようこそお越しくださいました ("you came")
                                    rf"|{_TE}?(?:ください|下さい)(?!ま(?:した|す|して))|お願い|頼む|頼みます"
                                    rf"|(?:手伝っ|助け)て(?:よ|ね|ってば)?{_END_STRICT}"),  # "help!": for the speaker
        # an imperative: the listener acts, for whom is not said ("見て！" is not "for me")
        # not the quotative 返すって / だって / 伝えてって, not 〜なくて (because not), not ごめんなさい,
        # not the greetings お疲れ / おかえりなさい / おやすみなさい
        ("request", LISTENER, None, rf"(?<![うくすつぬふむゆるぐずづぶぷだたいよねなんてで]っ)(?<!任せ)(?<!なん)(?<![んく])て"
                                    rf"(?:よ|ね|ってば)?{_END_STRICT}"
                                    rf"|(?<!ごめん)(?<!かえり)(?<!帰り)(?<!やすみ)(?<!休み)(?<!任せ)なさい(?:よ)?{_END_STRICT}"
                                    rf"|(?<=[一-鿿])(?<!疲)(?:ろ|え|け|せ|れ|げ|べ|め)(?:よ)?{_END_STRICT}"
                                    rf"|(?:やれ|しろ|やめろ|来い|黙れ)(?:よ)?{_END_STRICT}"),
        ("offer", SELF, LISTENER, rf"{_TE}(?:あげ(?:る|よう|ます|たい|た)|やる(?!な|ぞ?ら)|やろう|やった(?!ら))"),
        ("offer", SELF, None, rf"{_TE}お(?:く|き(?:ます|ました)|いた)|[っん][とど](?:く|いた)(?:ね|よ|から|わ|ぞ)?{_END}"
                              r"|任せ(?:て|ろ|とけ|なさい)|ましょうか"
                              # 手伝うよ / 手伝おうか (no other subject before it), 行ってきます
                              rf"|(?<![がはも])手伝(?:う(?:よ|ね|から|わ|ぞ|ぜ)?{_END_DECL}|おう(?:か(?:な)?|よ|ね)?{_END})"
                              rf"|(?<![がはも])(?:行|い)って(?:き(?:ます|まーす)|くる(?:ね|よ|わ|ぞ)?){_END_DECL}"),
        ("volition", SELF, None, rf"{_JA_SELF}が(?![？?、。！!…\s])"),  # kept only for a main clause (_main_clause)
        ("self_ref", SELF, None, _JA_SELF),
        ("listener_ref", LISTENER, None, _JA_OTHER),
        # a question about the listener's state (大丈夫？, 怪我はない？, 何してるの？: "Anh có sao không?") and a
        # statement of the speaker's own feeling or sensation (お腹すいた！, 怒ってないよ, うれしいよ: Japanese
        # says these of others only with らしい / そう / みたい); kept by _asks / _states (source_frames)
        ("state", LISTENER, None, rf"{_STATE_PRE}(?:{_STATE_ASK}){_NOT_QUOTED}"),
        ("state", SELF, None, rf"{_CLAUSE_START}{_STATE_SELF}{_NOT_QUOTED}|{_STATE_ANSWER}{_NOT_QUOTED}"),
        # greetings: the speaker is home (ただいま: "Con về rồi đây"), the listener is (おかえり: "Anh về rồi à"),
        # the speaker leaves (行ってきます, お先に失礼します: "Cháu đi học đây ạ"), the listener leaves
        # (行ってらっしゃい), the speaker eats (いただきます: "Con mời mẹ ăn cơm"), good night ("Chúc anh ngủ ngon")
        ("home", SELF, None, rf"{_CLAUSE_START}(?:ただいま|只今)(?:戻りました|帰りました|戻った|帰った|もどりました)?"
                             rf"{_CUE_END}{_NOT_QUOTED}"),
        ("home", LISTENER, None, rf"{_CLAUSE_START}(?:おかえり|お帰り)(?:なさい(?:ませ)?)?{_CUE_END}{_NOT_QUOTED}"),
        ("go", SELF, None, rf"(?<![がはも])(?:行|い)って(?:き(?:ます|まーす|ま〜す)|くる(?:ね|よ|わ|ぞ|から)?|来ます|まいります"
                           rf"|参ります){_CUE_DECL}{_NOT_QUOTED}"
                           rf"|{_CLAUSE_START}お先に(?:失礼|しつれい)(?:します|しまーす|しま〜す|いたします|致します|しました)?"
                           rf"{_CUE_DECL}{_NOT_QUOTED}"),
        ("go", LISTENER, None, rf"{_CLAUSE_START}(?:行|い)ってらっしゃい(?:ませ)?{_CUE_END}{_NOT_QUOTED}"),
        ("eat", SELF, None, rf"{_CLAUSE_START}(?:いただきます|頂きます|いただきまーす){_CUE_END}{_NOT_QUOTED}"),
        ("goodnight", LISTENER, None, rf"{_CLAUSE_START}(?:おやすみ|お休み)(?:なさい)?{_CUE_END}{_NOT_QUOTED}"),
    ))
# 私が in a clause that is conditional, concessive, quoted or subordinate ("私が卒業したら、…", "私がいないと",
# "私がついてるから、…") says nothing about who does the rest of the line.
_JA_SUBORDINATE = re.compile(r"たら|れば|ければ|なら|ても|でも|ないと|なきゃ|なくちゃ|なければ|なくて|まで|ので|のに|って"
                             r"|とでも|ように|と思|(?<!何)とか|たり|[ばと]$")  # 何とかする: "I'll manage"
_JA_PLAIN_VERB = re.compile(r"(?:[るうくすつぬむぶぐ]|ます|よう|おう)(?:よ|ね|わ|ぞ|ぜ|さ|から|けど|し)?$")
_JA_NOT_VOLITION = re.compile(r"(?:た|だ|です|でした)(?:よ|ね|わ)?$")
_EN_IMP = r"(?:^|(?<=[.!?…,]\s)|(?<=\bjust\s)|(?<=\bnow\s)|(?<=\bplease\s)|(?<=\byou\s))"
_EN_FRAMES: tuple[tuple[str, str, str | None, re.Pattern], ...] = tuple(
    (k, a, p, re.compile(rx, re.IGNORECASE)) for k, a, p, rx in (
        # demands only: not "Did he say sorry", "told me to say sorry", "say "sorry"" (a quoted word)
        ("apology", LISTENER, SELF, rf"{_EN_IMP}apologi[sz]e(?: to me| now)?\b|{_EN_IMP}(?:say|tell me) "
                                    r"(?:you're |you are )?sorry\b"
                                    r"|\byou (?:should|must|need to|have to|better|owe me an?) (?:apologi|be sorry|apology"
                                    r"|say sorry)"),
        ("thanks", LISTENER, SELF, rf"\bthank me\b|{_EN_IMP}say thank you\b"),
        ("apology", SELF, LISTENER, r"\b(?:I'?m|I am)\s+(?:(?:so|really|very|truly|terribly|deeply|awfully)\s+)*sorry\b"
                                    r"|(?:^[\"“'‘]?|(?<=[.!?…,;:]\s)|(?<=[.!?…,;:]))sorry\b|\bI apologi[sz]e\b"
                                    r"|\b(?:my|our) apologies\b|\bexcuse me\b|\bpardon me\b"),
        # not someone else's thanks passed on: "asked me to tell you thanks"
        ("thanks", SELF, LISTENER, r"(?<!\bto )(?<!\btell you )(?<!\btold you )\bthank(?:s|\s+you|\s+u)\b(?!\s+to\b)"
                                   r"|\bI (?:really |truly |do )?appreciate\b|\bI'?m (?:so |really |very )?grateful\b"),
        ("forgive", LISTENER, SELF, r"\bforgive me\b"),
        ("forgive", SELF, LISTENER, r"\bI forgive you\b"),
        ("offer", SELF, LISTENER, r"\bwould you like me to\b|\bdo you want me to\b|\bwhat can I do for you\b"
                                  r"|\b(?:can|may|shall) I (?:help|get|do|show|offer)\b"),
        ("request", LISTENER, SELF, r"\b(?:can|could|would|will) you\b(?! like| want| believe| be | imagine| know| think)"
                                    r"|\bwould you mind\b|\bhelp me\b|\bdo me a favou?r\b"),
        ("request", LISTENER, None, r"\bplease\b"),  # "Please wait here": not necessarily for the speaker
        ("offer", SELF, None, r"\bI'll\b|\bI will\b|\bI'?m going to\b|\bI am going to\b|\bI can\b(?!'t|not)"
                              r"|\blet me\b|\bshall I\b"),
        ("self_ref", SELF, None, r"\b(?:I'm|I'll|I'd|I've|I|me|my|mine|myself)\b"),
        ("listener_ref", LISTENER, None, r"\b(?:you're|you'll|you've|you'd|yourself|yours|your|you|sir|ma'am)\b"),
        ("state", LISTENER, None, r"\b(?:are|r)\s+(?:you|u)\s+(?:feeling\s+)?(?:ok|okay|alright|all right|hurt|injured"
                                  r"|cold|hungry|tired|sick|sleepy|scared|crying|angry)\b"
                                  r"|(?:^|(?<=[,.!?…]\s)|(?<=^hey\s))(?:you|u)\s+(?:ok|okay|alright|all right)\b"
                                  r"|\bdid\s+you\s+(?:eat|get hurt|hurt yourself)\b|\bhave\s+you\s+eaten\b"
                                  r"|\bwhat\s+are\s+you\s+doing\b"
                                  r"|\bwhat'?s\s+wrong\b(?!\s+with\s+(?!you\b|u\b))"  # not "with me / my phone / it"
                                  r"|\bwhat\s+happened\s+to\s+you\b|\bhow\s+are\s+you\b(?!\s+(?:going|gonna)\b)"
                                  r"|\bhow\s+have\s+you\s+been\b|\bdid\s+you\s+sleep\b"),
        ("home", SELF, None, r"\bI'?m\s+(?:home|back)\b(?!\s*\?)"),
        ("home", LISTENER, None, r"\bwelcome\s+(?:home|back)\b"),
        ("go", SELF, None, r"\bI'?m\s+(?:off|heading\s+(?:out|off)|leaving|going\s+(?:now|out))\b(?!\s*\?)"),
        ("goodnight", LISTENER, None, r"\bgood\s?night\b|^night\b"),
    ))


def source_frames(source: str, src_lang: str, own: Pair = None, other: Pair = None) -> list[Frame]:
    """Speech acts and explicit pronouns of the source line, in text order. Only unambiguous cues:
    an apology / thanks the listener is told to give is not the speaker's (謝って, "say sorry");
    "〜てくれる？" asks the listener to act for the speaker; "〜てあげる" / "let me" is the speaker's
    offer; 大丈夫？ asks about the listener, お腹すいた！ is the speaker's own state; ただいま / 行ってきます
    are the speaker's, おかえり / 行ってらっしゃい the listener's coming and going. Japanese (src_lang "ja",
    also zh/ko text with kana) or English (anything else). Quoted and reported acts (『ごめん』って,
    謝れって言われた, "Did he say sorry", 「ただいま」って) are no frames; 私が is volition only in a main
    clause (not 私が卒業したら / 私がいないと / 私が作ったケーキ). With the pair (`own`, `other`), a role
    word the speaker calls themself by (先生が from the teacher who says "cô", お姉ちゃんが from the older
    sister) is volition like 私が. A cue inside a bracketed quote the line goes on after (『大丈夫か？』って,
    『俺に任せろ』って言った, 「…」も言わず) is no act of the speaker's; one pair of brackets around the whole
    line and a speaker label (セラ：…, セラ「…」) are not quotes (_source_text). Benefactives ("favour": who
    does it for whom, 〜てくれた / 〜てもらって / 〜てあげる, "you made this for me") only where the giver and
    the one it is for are clear (_ja_favours, _en_favours); they are Frame.tentative (asked again, never
    rewritten)."""
    text = _source_text(source, src_lang)
    ja = _is_ja(text, src_lang)
    table = _JA_FRAMES if ja else _EN_FRAMES
    quotes = _quoted_spans(text) if ja else []
    out: list[Frame] = []
    taken: list[tuple[int, int, str]] = []  # (start, end, kind): a demand's cue is not also the speaker's act
    of_kind: dict[str, list[tuple[int, int]]] = {}  # per kind: sorted spans that do not overlap
    for kind, agent, patient, rx in table:
        spans = of_kind.setdefault(kind, [])
        for m in rx.finditer(text):
            if not m.group(0):
                continue
            s, e = m.span()
            if any(a <= s and e <= b for a, b in quotes):
                continue  # 『俺に任せろ』って言った (but 『ごめん』って言って: the listener is told to say it)
            k = bisect_left(spans, (e,))  # the spans that start before this one ends: only the last can overlap
            if k and spans[k - 1][1] > s:
                continue
            if kind == "request" and patient is None and any(ts <= s < te for ts, te, _ in taken):
                continue  # the て of 謝って / 許して / 任せて, already a frame of its own
            if kind == "volition" and not _main_clause(text, e):
                continue
            if kind == "state" and not (_state_kept(text, s, e, agent) if ja else _state_kept_en(text, s)):
                continue
            taken.append((s, e, kind))
            insort(spans, (s, e))
            out.append(Frame(kind, agent, patient, m.group(0), s, e, _tentative(kind, m.group(0), text, e)))
    grateful = any(f.kind in ("thanks", "apology") and f.agent == SELF for f in out)
    out += _ja_favours(text, _words(own), _words(other), quotes, grateful) if ja else _en_favours(text)
    if ja and own:
        for r in _role_words(text, _words(own), _words(other)):
            if r.role == SELF and text.startswith("が", r.end) and _main_clause(text, r.end + 1) \
                    and not any(a <= r.start < b for a, b in quotes) \
                    and not any(r.start < te and ts < r.end + 1 for ts, te in of_kind.get("volition", ())):
                out.append(Frame("volition", SELF, None, text[r.start:r.end + 1], r.start, r.end + 1, True))
    out.sort(key=lambda f: (f.start, f.end))
    return out


_TENTATIVE = re.compile(r"(?:ごちそう|ご馳走|御馳走)|任せなさい")


def _tentative(kind: str, cue: str, text: str, end: int) -> bool:
    """A cue whose rewrite no development line has tested (Frame.tentative): ごちそうさま (thanks),
    任せなさい (no longer a request), 私が何とかする (no longer a condition)."""
    if kind == "volition":
        return "何とか" in re.match(r"[^、。！？!?…\s「」『』,.]*", text[end:]).group(0)
    return kind in ("thanks", "offer") and bool(_TENTATIVE.match(cue))


_JA_QUOTE = (re.compile(r"「[^「」]*」"), re.compile(r"『[^『』]*』"))
_AFTER_DIALOGUE = "。！？!?…」』)）「『"  # what may follow a bracketed line of dialogue (else it is quoted)


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    """Bracketed spans the sentence goes on after (『大丈夫か？』って聞いた, 「ただいま」も言わず): quotes."""
    out = []
    for rx in _JA_QUOTE:
        for m in rx.finditer(text):
            nxt = text[m.end():m.end() + 1]
            if nxt and not nxt.isspace() and nxt not in _AFTER_DIALOGUE:
                out.append(m.span())
    return out


_Q_TAIL = re.compile(r"(?:ん|の|なの|なん|だ|です|でした|ます|ました|でしょう|でしょ|だろう|だろ|じゃない|かい|かよ|か|よ|ね"
                     r"|な(?!い))*")
_S_TAIL = re.compile(r"(?:ー|〜|～|っ|ぁ|あ|よ|わ|なあ|な|ぞ|ぜ|の|んだ|ん|です|だ|もん|からね|から)*")
_SAID = re.compile(r"[？?！!。…]*[』」]?(?:って(?!ば)|と(?:言|い[っうわ]|聞|伝))")  # 大丈夫か？って聞いた


def _sentence_at(text: str, pos: int) -> str:
    sents = _source_sentences(text)
    k = bisect_right([a for a, _ in sents], pos) - 1
    return text[sents[k][0]:sents[k][1]] if k >= 0 and pos < sents[k][1] else text


def _asks(text: str, pos: int) -> bool:
    """The clause ending at `pos` (plus particles) is a question: 大丈夫？, 怪我はないかい, not 大丈夫かな."""
    m = _Q_TAIL.match(text, pos)
    tail, nxt = m.group(0), text[m.end():m.end() + 1]
    if nxt in ("？", "?"):
        return not tail.endswith(("かな", "かしら"))
    return bool(re.search(r"(?:か|かい)$", tail)) and nxt in ("", "。", "…", " ", "　", "」", "』", "、")


def _states(text: str, pos: int) -> bool:
    """The clause ending at `pos` is a plain statement of the speaker's (お腹すいた！, 怒ってないよ): not a
    question, not shared or a guess (うれしいね, 疲れたでしょ)."""
    m = _S_TAIL.match(text, pos)
    return text[m.end():m.end() + 1] in ("", "。", "！", "!", "…", " ", "　", "、")


_JA_THIRD = re.compile(r"あいつ|こいつ|そいつ|あの子|その子|この子|あの人|その人|彼女|彼(?!ら)|奴|やつ")


def _state_kept(text: str, start: int, end: int, agent: str | None) -> bool:
    """A question about the listener's state (in a sentence without the speaker's own pronoun or a third
    person: not 私の顔、大丈夫？, あいつ、何してるんだ？) or a statement of the speaker's (in one without a
    listener pronoun or a third person)."""
    sent = _sentence_at(text, start)
    if _JA_THIRD.search(sent):
        return False
    if agent == LISTENER:
        return not re.search(_JA_SELF, sent) and _asks(text, end) and not _SAID.match(text, _Q_TAIL.match(text, end).end())
    return not re.search(_JA_OTHER, sent) and _states(text, end) and not _SAID.match(text, _S_TAIL.match(text, end).end())


_EN_ME = re.compile(r"\b(?:I|I'm|I'll|I've|I'd|me|my|mine|myself)\b", re.IGNORECASE)
_EN_THIRD = re.compile(r"\b(?:he|she|they|him|her|his|them|their|he's|she's|they're)\b", re.IGNORECASE)


def _state_kept_en(text: str, start: int) -> bool:
    """An English question about the listener in a sentence without the speaker or a third person
    ("Are you okay with me coming?", "Are you okay with him?" are not about the listener's state)."""
    sent = _sentence_at(text, start)
    return not (_EN_ME.search(sent) or _EN_THIRD.search(sent))


# A speaker label (セラ：…, 【セラ】「…」, セラ「…」) and one pair of brackets around the whole line are no
# quotes: the line is that person's dialogue. Not a name with a particle (お兄ちゃんが『…』: reported).
_SPEAKER_LABEL = re.compile(r"^[^\s\d「」『』：:。、！？!?…]{1,12}[：:]\s*(?=\S)")
_WHOLE_QUOTE = re.compile(r"^(?:[^\s\d「」『』：:。、！？!?…]{0,12}(?<![぀-ゟ]))?(?:「([^「」]*)」|『([^『』]*)』)\s*$")


def _dialogue(text: str) -> str:
    t = text.strip()
    m = _SPEAKER_LABEL.match(t)
    if m:
        t = t[m.end():]
    q = _WHOLE_QUOTE.match(t)
    if q:
        t = q.group(1) if q.group(1) is not None else q.group(2)
    return t


def _source_text(source: str, src_lang: str) -> str:
    text = unicodedata.normalize("NFKC", source or "").replace("’", "'") if src_lang != "ja" else (source or "")
    return _dialogue(text) if _is_ja(text, src_lang) else text


def _is_ja(text: str, src_lang: str) -> bool:
    return src_lang == "ja" or bool(re.search(r"[぀-ヿ]", text))


def _main_clause(text: str, pos: int) -> bool:
    """The clause from `pos` (after 私が) to the next punctuation ends in a plain verb form and is not
    conditional / subordinate / quoted: 僕がやるよ, 俺が払う, not 私が卒業したら, 私がついてるから、."""
    clause = re.match(r"[^、。！？!?…\s「」『』,.]*", text[pos:]).group(0)
    delim = text[pos + len(clause):pos + len(clause) + 1]
    if not clause or _JA_SUBORDINATE.search(clause) or _JA_NOT_VOLITION.search(clause):
        return False
    if delim in ("、", ",") and re.search(r"(?:から|けど|し)$", clause):
        return False  # 僕がやるから、… : a reason for the rest of the line
    return bool(_JA_PLAIN_VERB.search(clause))


_SRC_SENT_END = re.compile(r"[。！？!?…]+[」』)）\"”’']*|(?<!\bMr)(?<!\bMs)(?<!\bMrs)(?<!\bDr)\.+(?=\s|$|[\"”’'])")  # not Ms. Mori


@lru_cache(maxsize=256)
def _source_sentences(text: str) -> tuple[tuple[int, int], ...]:
    """Spans of the source's sentences (those with a word in them); cached, as every frame and source
    pronoun of a line asks for them."""
    out, pos = [], 0
    for m in _SRC_SENT_END.finditer(text):
        if re.search(r"\w", text[pos:m.start()]):
            out.append((pos, m.end()))
        pos = m.end()
    if re.search(r"\w", text[pos:]):
        out.append((pos, len(text)))
    return tuple(out)


# --- benefactives: who does it for whom -------------------------------------------------------------
#
# 〜てくれる / くださる: the subject does it for the speaker (the listener when no one else is named: 作って
# くれたの？); 〜てもらう / いただく: the speaker has the に-agent do it (the listener when unnamed: 手伝って
# もらって悪いな); 〜てあげる / やる / 差し上げる: the speaker does it for the listener (or a role word the
# speaker calls themself by: お母さんが作ってあげる). A frame only where nothing in the sentence before the cue
# may be the giver or the one it is for instead (a name, a third person, a role word outside the pair, the
# listener as an あげる subject, another noun with が / は: 母さんが作ってくれた to a friend, 彼ね、…作ってくれる
# の), the cue is no condition, quote or report (〜てくれたら, 〜てくれるって, 〜てくれと言われた, 『買ってやる』
# って), and, with no giver named, the line makes the listener the giver: a question (作ってくれたのか), thanks,
# relief or an apology (〜てくれてありがとう, 助かった, 来てもらってすみません), the listener called at the start
# (お兄ちゃん、迎えに来てくれたの). Not 〜てあげて (asks the listener to do it for someone), not もらう in a
# question (手伝ってもらった？: the listener got the help), not あげる in a question that is no offer
# (作ってあげたの？). English: "thanks for <-ing>", "you made / did you … / can you … for me", "I … / let me
# … for you", in a sentence without a third person, "we", a quote, a report or a condition.

_FAV_JA = re.compile(
    r"(?P<pre>[^\s、。！？!?…「」『』()（）]{0,6}?)"
    r"(?:(?P<te>" + _TE + r")(?:"
    r"(?P<k>(?:くれ|呉れ)(?:ませんでした|ました|ません|ます|なかった|ない|た|て|る)?"
    r"|(?:くださ|下さ)(?:いませんか|いました|います|った|って|る))"
    r"|(?P<m>(?:もら|貰)(?:えませんか|えません|えます|えない|える|った|って|う)"
    r"|(?:いただ|頂)(?:けませんか|けません|けます|けない|ける|きました|きます|いた|いて|き|く))"
    # not the prohibitive 〜てやるな / 〜てあげるな (甘やかしてやるなよ: "don't spoil him")
    r"|(?P<a>(?:あげ|上げ)(?:ませんでした|ましょうか|ましょう|ました|ません|ます|なかった|ない|ようか|よう|た|る(?!な(?!ら)))"
    r"|やった|やる(?!な(?!ら))|やろうか|やろう|やらない|差し上げ(?:ましょうか|ました|ます|る|た)))"
    # main verbs: お前がくれたお守り, これくれるの？; お前にやるよ (a に-recipient of the pair's: not 一緒にやろう,
    # すぐにやる), これ、あげる, この前あげたマフラー (a gift of the speaker's named by what follows: not 手をあげた)
    r"|(?P<kmain>(?:(?<=[をがはもに、\s])|(?<=これ)|(?<=それ)|(?<=あれ)|(?<=何)|(?<=なに)|^)"
    r"(?:くれ|呉れ)(?:ませんでした|ました|ません|ます|なかった|ない|た|る))"
    r"|(?P<amain>(?<=に)(?:あげ(?:ます|る|よう|た)|やる(?!な(?!ら))|やろう|差し上げ(?:ます|る)))"
    r"|(?P<agive>(?:(?<=これ)|(?<=それ)|(?<=あれ)|(?<=これ、)|(?<=それ、)|(?<=あれ、)|(?<=これは)|(?<=これを))"
    r"あげ(?:る(?!な(?!ら))|ます|よう))"
    r"|(?P<anom>(?<![をでて])あげた(?=[一-鿿゠-ヿ]))"
    # honorifics on a verb stem: お越しいただき, ご来店いただき, お越しくださいました
    r"|(?P<hon>[おご](?:(?![をがはにもで])[一-鿿ぁ-ゖ]){1,4}?(?:(?P<honk>(?:くださ|下さ)(?:いました|います|った|って|る))"
    r"|(?P<honm>(?:いただ|頂)(?:けませんか|けます|ける|きました|きます|いた|いて|き|く)))))")
# a condition, a quote or report, a guess after the cue: 〜てくれたら, 〜てくれれば, 〜てくれても, 〜てくれると,
# 〜てくれるって, 〜てやるなら, 〜てくれるかな (not 〜てもらってばかり: "only")
_FAV_TAIL_SKIP = re.compile(r"ら|れば|ば(?!かり|っか)|ても|も(?!う)|でも|なら|と(?!も)|って(?!ば)|そう(?:だ|です|な)|らしい"
                            r"|みたい|べき|はず|かな|かしら")
# the speaker thanks, is relieved or apologises (手伝ってもらって悪いな, 助かったよ, おかげで), not as a quote (not
# 〜てくれてありがとう、ってケンジに言っといて: someone else's thanks passed on)
_FAV_GRATEFUL = re.compile(r"(?:ありがと|有難|有り難|感謝|助か|嬉し|うれし|サンキュ|おかげ|お陰|お礼|すまな|すまん|すみません"
                           r"|すいません|ごめん|悪い|悪かった|わりい|わりぃ|申し訳|恐縮)"
                           r"(?![^\s、。！？!?…」』]{0,6}?[、,\s]*(?:って(?!ば)|と(?:言|い[っうわ]|伝)))")
_FAV_LEAD = re.compile(r"[\s、,！!…]*(?:(?:ねえ|ねぇ|ねー|あのね|あの|おい|ほら|えっ|え|あっ|あ|もう|ちょっと)[\s、,！!…]*)?")
# a name called at the start of a line that asks or thanks (たくみくん、…くれたの？, 先輩、…ありがとうございます): the
# listener, not someone else (ケンジくん、私に謝ってくれたよ: a statement about Kenji stays no frame)
_FAV_NAMED_VOC = re.compile(_FAV_LEAD.pattern + r"(?:[一-鿿゠-ヿぁ-ゖ]{1,6}?(?:さん|くん|ちゃん|君|様|さま|先輩|殿)|先輩)"
                            r"(?=[、,！!])")
# the giver named after the cue (a cleft or a relative clause on a person): 助けてくれたのはケンジくんだ, 作ってくれた人
_FAV_GIVER_AFTER = re.compile(r"の(?:は|が|も)|人|方|者|やつ|奴|子(?![供ど])")
# someone else who may be the giver or the one it is for (a role word of the pair is masked out first)
_FAV_PEOPLE = re.compile(
    r"母|父|兄|姉|妹|弟|祖母|祖父|友達|友だち|ともだち|みんな|皆|家族|子供|子ども|息子|娘|彼氏|旦那|妻|夫|嫁|先輩|後輩|上司|部長"
    r"|課長|社長|店長|店員|先生|医者|誰|だれ|おじさん|おばさん|叔父|叔母|伯父|伯母|親(?!切)|相手|あの子|この子|その子"
    r"|[ァ-ヺー]{2,}(?=[がはも]|って|さん|くん|ちゃん|君|様|さま)"
    r"|[一-鿿゠-ヿぁ-ゖ]{1,6}?(?:(?<!たく)さん|くん|ちゃん(?!と)|君|様|さま|先輩|殿|氏)")
# another noun as the subject or topic (雨が止んでくれた, 弁当は作ってくれた？), not a time word (昨日は)
_FAV_NOUN_TOPIC = re.compile(r"(?P<n>[一-鿿゠-ヿ々ー]+)(?P<p>が|は|も(?!う))")
_FAV_TIME = re.compile(r"今日|昨日|明日|今朝|今夜|今晩|今回|今度|前回|先日|先週|先月|去年|今年|今|前|昔|最初|最後|次|実|本当"
                       r"|毎日|毎朝|毎晩|朝|夜|昼|夕方|週末|本日|先程|時|一度|一回|何")
_SUBJ_PARTICLES = frozenset({"が", "は", "も", "こそ", "って", "ってば", "だって"})
_REC_PARTICLES = frozenset({"に", "には", "にも", "へ", "のため", "を", "のこと", "から"})


def _fav_roles(window: str, own_w: tuple[str, ...], other_w: tuple[str, ...],
               addressed: bool = False) -> tuple[set[str], set[str], bool, bool]:
    """(subjects, recipients / に-agents, the listener called at the start, someone else named) of the text
    of a sentence before a benefactive cue: the pair's pronouns and role words by their particle. With
    `addressed` (the line asks or thanks), a name called at the start is the listener (_FAV_NAMED_VOC)."""
    subj: set[str] = set()
    rec: set[str] = set()
    vocative = blocked = False
    named = _FAV_NAMED_VOC.match(window) if addressed else None
    if named:
        window = "□" * named.end() + window[named.end():]
        vocative = True
    hits: list[tuple[int, int, str]] = []
    for m in _JA_PRONOUN_RX.finditer(window):
        s, cue = m.start(), m.group(0)
        if re.fullmatch(r"[぀-ゟ]+", cue) and s > 0 and "぀" <= window[s - 1] <= "ゟ" \
                and window[s - 1] not in _KANA_PRONOUN_OK_BEFORE:
            continue  # こわし, かわした: no pronoun
        hits.append((s, m.end(), SELF if m.group("s") else LISTENER))
    for m in _ROLE_RX.finditer(window):
        if any(s < m.end() and m.start() < e for s, e, _ in hits):
            continue
        vi = _ROLE_WORDS_JA[next(int(k[1:]) for k, v in m.groupdict().items() if v)][1]
        in_own, in_other = any(w in own_w for w in vi), any(w in other_w for w in vi)
        hits.append((m.start(), m.end(), SELF if in_own and not in_other else LISTENER if in_other and not in_own
                     else UNKNOWN))
    masked = list(window)
    for s, e, role in hits:
        masked[s:e] = "□" * (e - s)
        if role == UNKNOWN or _JA_PLURAL_AFTER.match(window, e):
            blocked = True  # a role word outside the pair (ケンジのお母さん is a third person anyway), 私たち
            continue
        p = _JA_PARTICLE.match(window, e)
        part, nxt = p.group("p"), window[p.end():p.end() + 1]
        if p.group("lim"):
            blocked = True  # お前だけが: not clear
        elif part in _SUBJ_PARTICLES:
            subj.add(role)
        elif part in _REC_PARTICLES:
            rec.add(role)
        elif part == "の":
            pass  # a possessor: お前の宿題
        elif part is None:
            if nxt in ("、", ",", "！", "!", " ", "　") and _FAV_LEAD.fullmatch(window[:s]):
                vocative |= role == LISTENER  # お兄ちゃん、迎えに来てくれたの？: the listener is called
            else:
                subj.add(role)  # これ、お前、作ってくれたのか / お前作ってくれたの？: a topic, a dropped が
        else:
            blocked = True
    rest = "".join(masked)
    if _FAV_PEOPLE.search(rest) or _JA_THIRD.search(rest):
        blocked = True
    if any(m.group("p") == "が" or not _FAV_TIME.fullmatch(m.group("n")) for m in _FAV_NOUN_TOPIC.finditer(rest)):
        blocked = True
    return subj, rec, vocative, blocked


def _ja_favours(text: str, own_w: tuple[str, ...], other_w: tuple[str, ...], quotes: list[tuple[int, int]],
                grateful: bool) -> list[Frame]:
    """Benefactive frames of a Japanese line (see the section comment); `grateful`: the speaker thanks or
    apologises somewhere in the line."""
    out: list[Frame] = []
    grateful = grateful or any(not any(a <= m.start() < b for a, b in quotes) for m in _FAV_GRATEFUL.finditer(text))
    sents = _source_sentences(text)
    starts = [a for a, _ in sents]
    for m in _FAV_JA.finditer(text):
        pos = next(m.start(g) for g in ("te", "kmain", "amain", "agive", "anom", "hon") if m.group(g))
        if any(a <= pos < b for a, b in quotes) or _FAV_TAIL_SKIP.match(text, m.end()) \
                or _FAV_GIVER_AFTER.match(text, m.end()):
            continue
        k = bisect_right(starts, pos) - 1
        s0, s1 = sents[k] if k >= 0 else (0, len(text))
        asks = _asks(text, m.end())
        subj, rec, vocative, blocked = _fav_roles(text[s0:pos], own_w, other_w, asks or grateful)
        if blocked:
            continue
        if m.group("k") or m.group("kmain") or m.group("honk"):  # くれる: the subject gives to the speaker
            if SELF in subj or rec - {SELF} or not (LISTENER in subj or asks or grateful or vocative):
                continue
            giver, ben = LISTENER, SELF
        elif m.group("m") or m.group("honm"):  # もらう: the speaker has the に-agent do it
            if subj - {SELF} or rec - {LISTENER} or asks or not (LISTENER in rec or grateful):
                continue
            giver, ben = LISTENER, SELF
        else:  # あげる / やる: the speaker does it for the listener
            if subj - {SELF} or rec - {LISTENER} or (asks and not re.search(r"(?:よう|ろう|ましょう)か?$", m.group(0))):
                continue
            if m.group("amain") and LISTENER not in rec:
                continue  # 一緒にやろう, すぐにやる: に is no recipient (お前にやる is)
            if m.group("anom") and not (text[s0:s1].rstrip(" 　。…")[-1:] in ("？", "?") or vocative):
                continue  # この前あげたマフラー、使ってる？ asks the listener about the gift
            giver, ben = SELF, LISTENER
        out.append(Frame("favour", giver, ben, m.group(0), m.start(), m.end(), True))
    return out


_EN_FAV_SKIP = re.compile(r"[\"“”]|\b(?:said|says|told|tells|asked|asks|if|unless|whether|he|she|they|him|her|them|his"
                          r"|their|he's|she's|they're|we|us|our|we're|we'll|we've)\b", re.IGNORECASE)
_EN_FAV: tuple[tuple[str, str, re.Pattern], ...] = tuple((g, b, re.compile(rx, re.IGNORECASE)) for g, b, rx in (
    (LISTENER, SELF, r"\bthank(?:s|\s+you|\s+u)\b(?:\s+(?:so|very)\s+much|\s+a\s+lot|\s+again)?\s+for\s+"
                     r"(?!the\b|this\b|that\b|it\b|your\b|everything\b|nothing\b|all\b|today\b|tonight\b|now\b)[a-z]+ing\b"),
    (LISTENER, SELF, r"\b(?:did|would|will|could|can)\s+(?:you|u)\b[^.!?;:,]*?\bfor\s+me\b"),
    (LISTENER, SELF, r"\b(?:you|u)(?:'ve|\s+have)?\s+(?:(?:really|actually|even|just|always|already|all|finally)\s+)?"
                     r"(?:made|bought|cooked|baked|got|brought|did|fixed|saved|kept|wrote|picked|found|drew|knitted|knit"
                     r"|sewed|prepared|packed|covered|cleaned|carried|sent|left|chose|ordered|booked|reserved|grabbed|built"
                     r"|painted)\b[^.!?;:,]*?\bfor\s+me\b"),
    (SELF, LISTENER, r"\b(?:I|I'll|I've|I'd|I'm|let\s+me)\b[^.!?;:,]*?\bfor\s+you\b"),
))


# Benefactive cues of a source whether they make a frame or not (a condition, a third person, a quote): who
# gives in each, the listener (くれる / もらう, "for me", "thanks for …") or the speaker (あげる / やる, "for you").
# A line with both (手伝ってくれてありがとう、今度は私が手伝ってあげるね; 手伝ってくれたら、ジュースおごってあげる;
# "Thanks for helping me, I'll help you next time") has acts going both ways: which "giúp" / "cho" renders
# which is not known, so no help / give / favour slot of the line is judged (_frame_expectations).
_BEN_DIRS_JA = ((LISTENER, re.compile(_TE + r"(?:くれ|呉れ|くださ|下さ|もら|貰|いただ|頂)|(?<![てで])(?:くれ|呉れ)(?:た|る|て|ない)"
                                      r"|[おご][^\s、。！？!?…]{1,4}?(?:いただ|くださ)")),
                # not 〜てあげて(くれる) / 〜てやって: the listener asked to do it for someone
                (SELF, re.compile(_TE + r"(?:(?:あげ|上げ|差し上げ|やっ)(?!て)|やる|やろ|やら)")))
_BEN_DIRS_EN = ((LISTENER, re.compile(r"\bfor\s+me\b|\bthank(?:s|\s+you|\s+u)\s+(?:\w+\s+){0,3}?for\b|\bhelp(?:ed|ing)?\s+me\b"
                                      r"|\b(?:you|u)(?:'ve)?\s+(?:\w+\s+)?(?:helped|saved|covered|gave|made|bought|brought|fixed)"
                                      r"\b", re.IGNORECASE)),
                (SELF, re.compile(r"\bfor\s+you\b|\bI'?ll\s+(?:\w+\s+)?(?:help|make|cook|buy|get|do|give|pay|treat)\b"
                                  r"|\blet\s+me\b", re.IGNORECASE)))


def _ben_directions(text: str, ja: bool) -> frozenset[str]:
    """Who gives in the benefactive cues of `text` (a source sentence, see _BEN_DIRS_JA)."""
    return frozenset(who for who, rx in (_BEN_DIRS_JA if ja else _BEN_DIRS_EN) if rx.search(text))


# "for you / me" that is no favour: "I've been waiting for you", "Did you ask for me?", "I'm too old for you"
_EN_FAV_NOT = re.compile(r"\b(?:wait(?:s|ed|ing)?|look(?:s|ed|ing)?|search(?:es|ed|ing)?|ask(?:s|ed|ing)?|too|fell"
                         r"|fall(?:s|en|ing)?|vote[sd]?|voting|stand(?:s|ing)?|stood|speak(?:s|ing)?|spoke|go(?:es|ne)?"
                         r"|went|meant|mean[st]?)\s+(?:\w+\s+){0,2}?for\s+(?:you|me)\b", re.IGNORECASE)


def _en_favours(text: str) -> list[Frame]:
    """Benefactive frames of an English line (see the section comment)."""
    out: list[Frame] = []
    for giver, ben, rx in _EN_FAV:
        for m in rx.finditer(text):
            if _EN_FAV_SKIP.search(_sentence_at(text, m.start())) or _EN_FAV_NOT.search(m.group(0)) \
                    or any(f.start < m.end() and m.start() < f.end for f in out):
                continue
            out.append(Frame("favour", giver, ben, m.group(0), m.start(), m.end(), True))
    return out


# --- Vietnamese anchors and their slots ------------------------------------------------------------

# Words allowed between a subject pronoun and its anchor ("Em cũng thật sự xin lỗi", "Anh có thể giúp").
_PRE_ADV = frozenset({"cũng", "thật", "thực", "sự", "rất", "thành", "lòng", "chân", "xin", "muốn", "phải", "nên",
                      "cần", "đã", "sẽ", "vẫn", "còn", "chỉ", "lại", "luôn", "không", "chưa", "đừng", "hãy", "có",
                      "thể", "làm", "ơn", "cứ", "thử", "nói", "định", "đang", "vừa", "mới", "vô", "cùng", "nhất",
                      "chắc", "chắn", "đến", "tới", "sang", "qua"})
_OBJ_SKIP = {"forgive": {"cho"}, "help": {"đỡ", "cho", "được"}, "ask": {"được"}, "apology": {"với"}}
_SUBORDINATE = frozenset({"để", "cho", "nếu", "khi", "mà", "vì", "thấy", "nhìn", "giúp", "nhờ"})  # embedded subject
_CARRY = frozenset({"đưa", "dẫn", "chở", "rủ", "kêu", "gọi", "mời", "cùng", "với", "theo", "bảo"})  # "đưa em đi"
# A state word's subject may be the owner of a body part ("Chân anh có sao không?"), not of a thing
# ("Hộp cơm của em có ổn không?", "Máy em bị sao vậy?": whose box / phone it is says nothing)
_BODY = frozenset({"chân", "tay", "đầu", "người", "mặt", "mắt", "bụng", "lưng", "vai", "cổ", "họng", "răng", "tim",
                   "ngực", "trán", "gối", "tai", "mũi", "miệng", "môi", "da", "eo", "hông", "ngón", "thương", "thể",
                   "giọng", "dày", "tử"})  # the last word of vết thương, cơ thể, dạ dày, bao tử


def _owner(toks: list[_Tok], i: int) -> bool:
    """toks[i] (a state word's subject) is in no subject position but owns the word before it, which is
    no body part: "Hộp cơm của em", "Máy em" (not "Chân anh", "Vết thương của anh", nor after a function
    word or a verb: "Chỉ là con lo thôi", "Làm em lo quá")."""
    if _vi_func(toks, i) == "subj":
        return False
    prv = toks[i - 1].low
    if prv == "của":
        return i < 2 or not toks[i - 1].adjacent or toks[i - 2].low not in _BODY
    return not (prv in _BODY or prv in _NOT_SUBJECT or prv in _PRE_ADV or prv in _OBJECT_BEFORE)
_HO_NOT_BEFORE = frozenset({"ủng", "bảo", "phù", "cứu", "canh", "giám", "che", "hộ", "y"})
_HO_NOT_AFTER = frozenset({"chiếu", "khẩu", "tống", "lý", "sinh", "gia", "vệ", "pháp", "tịch", "dân"})
_NHO_NOT_AFTER = frozenset({"có", "vào", "đó", "vậy", "thế", "trời", "ơn", "đâu", "mà"})
_LET_LEAD = frozenset({"thôi", "vậy", "cứ", "hay", "thế", "nào", "vâng", "dạ", "ừ", "được", "ok", "để"})
_ANCHOR_WORDS = {
    ("xin", "lỗi"): "apology",
    ("cảm", "ơn"): "thanks", ("cám", "ơn"): "thanks", ("cảm", "tạ"): "thanks", ("đa", "tạ"): "thanks",
    ("biết", "ơn"): "thanks",
    ("tha", "lỗi"): "forgive", ("tha", "thứ"): "forgive", ("bỏ", "qua"): "forgive",
    ("làm", "ơn"): "please",
    ("giúp",): "help", ("giùm",): "help", ("hộ",): "help",
    ("nhờ",): "ask", ("cho",): "give", ("để",): "let", ("sẽ",): "will",
    ("xin", "phép"): "excuse", ("ngủ", "ngon"): "sleep", ("mời",): "invite", ("chúc",): "wish", ("mừng",): "welcome",
}
# State words a question about the listener / a statement of the speaker's own state is about, with the
# source cues they render (_STATE_KEYS): "Anh có sao không?", "Em có bị thương không?", "Con đói quá".
_STATE_WORDS = {"ổn": "ổn", "mệt": "mệt", "đói": "đói", "khỏe": "khỏe", "khoẻ": "khỏe", "lạnh": "lạnh", "nóng": "nóng",
                "đau": "đau", "khóc": "khóc", "giận": "giận", "vui": "vui", "sợ": "sợ", "ốm": "ốm", "ăn": "ăn",
                "lo": "lo", "ngủ": "ngủ", "sốt": "sốt"}
_STATE_NOT_AFTER = {"ổn": {"định", "thỏa"}, "lo": {"liệu", "xa"}, "vui": {"lòng"}, "nóng": {"tính", "nảy"},
                    "lạnh": {"lùng", "nhạt"}, "đau": {"khổ"}, "ăn": {"nói", "mặc", "cắp", "trộm", "hiếp"},
                    "sốt": {"ruột", "sắng"}}
_STATE_NOT_BEFORE = {"khỏe": {"sức"}, "lạnh": {"tủ", "máy"}, "ăn": {"đồ", "món", "thức", "bữa"}, "lo": {"đừng"}}
_STATE_AFTER_BI = {"thương": "thương", "cảm": "cảm"}  # bị thương, bị cảm
_SAO_BEFORE = frozenset({"có", "không", "bị", "làm"})  # có sao, không sao, bị sao, làm sao
_Q_PARTICLES = frozenset({"vậy", "thế", "đấy", "đó", "à", "hả", "chứ", "không", "rồi", "ư"})
_LEAVE_NOT_BEFORE = frozenset({"qua", "đến", "tới", "lại", "vào", "sang", "lên", "xuống", "ra", "ở", "đâu", "về"})
_HOME_NEXT = frozenset({"rồi", "nhà", "đây", "nè", "ạ", "à", "đấy", "đó", "hả", "nha", "nhé"})
_STATE_KEYS = (  # source cue -> the state words that render it
    (re.compile(r"大丈夫|平気|\b(?:ok|okay|alright|all right)\b", re.I), frozenset({"sao", "ổn"})),
    (re.compile(r"怪我|けが|ケガ|\bhurt|\binjured", re.I), frozenset({"thương", "sao", "đau"})),
    (re.compile(r"元気|\bhow (?:are|have) you", re.I), frozenset({"khỏe", "ổn"})),
    (re.compile(r"疲れ|\btired", re.I), frozenset({"mệt"})),
    (re.compile(r"寒|\bcold", re.I), frozenset({"lạnh"})),
    (re.compile(r"暑"), frozenset({"nóng"})),
    (re.compile(r"痛"), frozenset({"đau"})),
    (re.compile(r"眠|寝|\bsleep", re.I), frozenset({"ngủ"})),
    (re.compile(r"熱"), frozenset({"sốt"})),
    (re.compile(r"怖|\bscared", re.I), frozenset({"sợ"})),
    (re.compile(r"お腹|おなか|腹|\bhungry", re.I), frozenset({"đói"})),
    (re.compile(r"風邪|\bsick", re.I), frozenset({"cảm", "ốm"})),
    (re.compile(r"どう(?:した|かした)|\bwhat'?s wrong|\bwhat happened to you", re.I), frozenset({"sao"})),
    (re.compile(r"何(?:を)?(?:して|やって)|\bwhat are you doing", re.I), frozenset({"làmgì"})),
    (re.compile(r"泣|\bcrying", re.I), frozenset({"khóc"})),
    (re.compile(r"食べ|\beat", re.I), frozenset({"ăn"})),
    (re.compile(r"怒って|\bangry", re.I), frozenset({"giận"})),
    (re.compile(r"心配"), frozenset({"lo"})),
    (re.compile(r"嬉し|うれし"), frozenset({"vui"})),
)


def _state_keys(cue: str) -> frozenset[str]:
    return frozenset().union(*(keys for rx, keys in _STATE_KEYS if rx.search(cue)))


@dataclass(frozen=True)
class _Anchor:
    cls: str
    first: int  # token indices
    last: int
    key: str = ""  # a state anchor's word (sao, mệt, làmgì, ...)


def _after_interjections(toks: list[_Tok], i: int) -> bool:
    """Only interjections between the sentence start and toks[i] ("Thôi được, để em…", "Cứ để em")."""
    j = i - 1
    while j >= 0 and toks[j].low in _LET_LEAD:
        if _sentence_start(toks, j):
            return True
        j -= 1
    return False


def _leave_end(toks: list[_Tok], i: int) -> int | None:
    """toks[i] is "đi" / "về" of "<đi|về> (up to three words) đây" at the end of its clause ("Cháu đi học
    đây ạ", "Con về rồi đây"): the index of "đây". Not "đến đây" / "qua đây" (here) or "đi đâu đây"."""
    for j in range(i + 1, min(i + 5, len(toks))):
        if not toks[j].adjacent:
            return None
        if toks[j].low == "đây":
            if toks[j - 1].low in _LEAVE_NOT_BEFORE and j - 1 > i:
                return None
            k = j + 1
            if k < len(toks) and toks[k].adjacent and toks[k].low in _VOC_PARTICLES:
                k += 1
            return j if k >= len(toks) or not toks[k].adjacent else None
    return None


def _state_anchor(toks: list[_Tok], i: int) -> _Anchor | None:
    """A state word ("Anh có sao không?", "Em có mệt không?", "Con đói quá", "Anh đang làm gì vậy?")."""
    w = toks[i].low
    prv = toks[i - 1].low if i > 0 and toks[i].adjacent else ""
    nxt = toks[i + 1].low if i + 1 < len(toks) and toks[i + 1].adjacent else ""
    if w == "sao":
        if prv in _SAO_BEFORE or nxt in _Q_PARTICLES or not nxt:
            return _Anchor("state", i, i, "sao")
        return None
    if w == "làm" and nxt == "gì":
        return _Anchor("state", i, i + 1, "làmgì")
    if w == "buồn" and nxt == "ngủ":
        return _Anchor("state", i, i + 1, "ngủ")
    if prv == "bị" and w in _STATE_AFTER_BI:
        return _Anchor("state", i, i, _STATE_AFTER_BI[w])
    key = _STATE_WORDS.get(w)
    if key and nxt not in _STATE_NOT_AFTER.get(w, ()) and prv not in _STATE_NOT_BEFORE.get(w, ()):
        return _Anchor("state", i, i, key)
    return None


def _anchors(toks: list[_Tok]) -> list[_Anchor]:
    out: list[_Anchor] = []
    i = 0
    while i < len(toks):
        st = _state_anchor(toks, i)
        if st:
            out.append(st)
        if toks[i].low == "ra":
            out.append(_Anchor("go", i, i))  # "Em ra cửa hàng một chút" (ちょっとコンビニ行ってくる)
        if toks[i].low in ("đi", "về"):
            out.append(_Anchor("go", i, i))  # who leaves (行ってきます: "Cháu đi học đây ạ")
            nxt = toks[i + 1].low if i + 1 < len(toks) and toks[i + 1].adjacent else ""
            prv = toks[i - 1].low if i > 0 and toks[i].adjacent else ""
            if toks[i].low == "về" and (not nxt or nxt in _HOME_NEXT or prv in ("đã", "vừa", "mới")):
                out.append(_Anchor("home", i, i))  # who is home (ただいま / おかえり: "Con về rồi đây", "Anh về rồi à")
            j = _leave_end(toks, i)
            if j is not None:
                out.append(_Anchor("leave", i, j))
        two = (toks[i].low, toks[i + 1].low) if i + 1 < len(toks) and toks[i + 1].adjacent else None
        if two == ("bỏ", "qua") and not (i + 2 < len(toks) and toks[i + 2].adjacent and toks[i + 2].low == "cho"):
            two = None  # "bỏ qua câu này" is "skip"; only "bỏ qua cho em" is "forgive"
        if two in _ANCHOR_WORDS:
            out.append(_Anchor(_ANCHOR_WORDS[two], i, i + 1))
            i += 2
            continue
        cls = _ANCHOR_WORDS.get((toks[i].low,))
        prv = toks[i - 1].low if i > 0 and toks[i].adjacent else ""
        nxt = toks[i + 1].low if i + 1 < len(toks) and toks[i + 1].adjacent else ""
        if cls == "help" and toks[i].low == "hộ" and (prv in _HO_NOT_BEFORE or nxt in _HO_NOT_AFTER):
            cls = None
        elif cls == "ask" and nxt in _NHO_NOT_AFTER:
            cls = None
        elif cls == "let" and not (_sentence_start(toks, i) or _after_interjections(toks, i)):
            cls = None  # "để" inside a clause: "in order to", "để quên"
        if cls:
            out.append(_Anchor(cls, i, i))
        i += 1
    return out


_PRE_ADV_MORE = {"state": frozenset({"bị", "thấy", "cảm", "hơi", "đỡ", "khá"}),  # "Anh có bị thương không?"
                 "go": frozenset({"phép"})}  # "Em xin phép về trước"


def _adverb(toks: list[_Tok], j: int, a: _Anchor) -> bool:
    """toks[j] may stand between a subject and the anchor: not "làm cho" (causative: "làm cho anh vui"),
    and not "cần" / "được" before "giúp" (their subject is the one helped: "Anh cần giúp gì không?")."""
    w = toks[j].low
    if w in _PRE_ADV_MORE.get(a.cls, ()):
        return True
    if w not in _PRE_ADV or (a.cls == "give" and w == "làm"):
        return False
    return not (a.cls == "help" and w in ("cần", "được"))


def anchor_classes(translation: str) -> list[str]:
    """The anchor classes of a translation in order (apology, thanks, forgive, please, help, ask, give,
    let, will, leave)."""
    return [a.cls for a in _anchors(_tokens(_nfc(translation)))]


def _skip_focus(toks: list[_Tok], j: int) -> int:
    """j at "người" of "<subject> (mới) là người (phải) <anchor>": the index of the focus phrase's first
    word ("mới" / "là"), else -1."""
    if toks[j].low != "người" or j < 2 or not toks[j].adjacent or toks[j - 1].low != "là":
        return -1
    j -= 1
    if toks[j].adjacent and toks[j - 1].low == "mới":
        j -= 1
    return j


def _subject(toks: list[_Tok], a: _Anchor, uses: dict[int, object]) -> int | None:
    """Token index of the pronoun directly before the anchor (adverbs in between allowed, and the focus
    "Anh mới là người phải xin lỗi"); not a possessor ("mẹ ủi áo của con giúp con": con owns the shirt; a
    state word's subject may be the owner of a body part, _owner: "Vết thương của em có sao không?")."""
    j = a.first
    for _ in range(7):
        if j == 0 or not toks[j].adjacent:
            return None
        j -= 1
        if j in uses:
            return None if a.cls != "state" and _possessor(toks, j) else j
        k = _skip_focus(toks, j)
        if k > 0:
            j = k
            continue
        if not _adverb(toks, j, a):
            return None
    return None


_THIRD_SUBJECT = frozenset({"ấy", "ta", "nọ", "hắn", "nó", "họ", "ổng", "bả", "ảnh"})
_NOT_SUBJECT = frozenset({"vì", "là", "thì", "và", "nhưng", "rồi", "sao", "tại", "nên", "rằng", "hay", "hoặc", "mà",
                          "nếu", "khi", "để", "cho", "giúp", "nhờ", "thấy", "nhìn", "nào", "đi", "vậy", "thế", "thôi",
                          "ơi", "à", "ạ", "nhé", "nha", "chứ", "này", "đó", "đấy", "ừ", "dạ", "vâng", "lúc", "nữa"})
# A pronoun after these is told / made to do what follows ("Mẹ bảo anh phải xin lỗi em"): reported, not
# the speaker's act toward the listener. Directly before an anchor they, and "là" / "rằng", report it
# ("nhắn là xin lỗi em"). Not "nghĩ là em sẽ xin lỗi": who would apologise is still the source's agent.
_REPORT = frozenset({"bảo", "bắt", "nhắn", "dặn", "khuyên", "muốn", "mong", "kêu", "biểu", "nhắc", "giục", "ép",
                     "xúi", "cấm"})
_THAT = frozenset({"là", "rằng"})
_CONDITIONAL = frozenset({"nếu", "khi", "lúc", "hễ"})


def _other_subject(toks: list[_Tok], a: _Anchor, uses: dict[int, object]) -> bool:
    """The anchor has a subject that is no pronoun of the pair ("nhân viên sẽ giúp anh", "Kenji đã xin
    lỗi anh", "anh ấy xin lỗi em") or reports the act ("nhắn là xin lỗi em"): its object is not judged."""
    j, adverbs = a.first, 0
    for _ in range(5):
        if j == 0 or not toks[j].adjacent:
            return False
        j -= 1
        if j in uses:
            return False
        w = toks[j].low
        if _adverb(toks, j, a):
            adverbs += 1
            continue
        if w in _THIRD_SUBJECT or w in _REPORT or w in _THAT or (_is_upper(toks[j]) and not _sentence_start(toks, j)):
            return True
        return adverbs > 0 and w not in _NOT_SUBJECT
    return False


def _object(toks: list[_Tok], a: _Anchor, uses: dict[int, object]) -> int | None:
    """Token index of the pronoun directly after the anchor."""
    skip = _OBJ_SKIP.get(a.cls, set())
    j = a.last
    for _ in range(3):
        j += 1
        if j >= len(toks) or not toks[j].adjacent:
            return None
        if j in uses:
            return j
        if toks[j].low not in skip:
            return None
    return None


def _sentence_index(toks: list[_Tok]) -> list[int]:
    """The sentence (0, 1, ...) each word of a translation is in."""
    out, n = [], 0
    for i, t in enumerate(toks):
        if i and _SENT_END.search(t.gap):
            n += 1
        out.append(n)
    return out


_MEAN_TAIL = frozenset({"vậy", "chứ", "thế", "hả", "à", "nhỉ", "cơ", "ạ", "nữa"})


def _what_do_you_mean(toks: list[_Tok], text: str, uses: dict[int, object]) -> list[int]:
    """Pronouns of "Ý <pronoun> là sao / gì?" (what do you mean?): the listener, whatever the source."""
    out = []
    for i in range(len(toks) - 3):
        if toks[i].low != "ý" or i + 1 not in uses or not all(toks[k].adjacent for k in (i + 1, i + 2, i + 3)) \
                or toks[i + 2].low != "là" or toks[i + 3].low not in ("sao", "gì"):
            continue
        j = i + 4
        while j < len(toks) and toks[j].adjacent and toks[j].low in _MEAN_TAIL:
            j += 1
        tail = toks[j].gap if j < len(toks) else text[toks[j - 1].end:]
        if tail.lstrip().startswith("?"):
            out.append(i + 1)
    return out


def _clause(toks: list[_Tok], i: int) -> list[_Tok]:
    """The words of the clause around toks[i] (between punctuation)."""
    s = i
    while s > 0 and toks[s].adjacent:
        s -= 1
    e = i
    while e + 1 < len(toks) and toks[e + 1].adjacent:
        e += 1
    return toks[s:e + 1]


# What a slot of each anchor must be, from the source frames: (subject role, object role); None in a
# position = not judged. A check is off when the frames that bear on it disagree about who acts.
_DESCRIBE = {
    ("apology", SELF): "the speaker apologises", ("apology", LISTENER): "the listener is asked to apologise",
    ("thanks", SELF): "the speaker thanks", ("thanks", LISTENER): "the listener is asked to thank",
    ("forgive", SELF): "the speaker forgives", ("forgive", LISTENER): "the listener is asked to forgive",
    ("request", LISTENER): "the listener is asked to act", ("offer", SELF): "the speaker offers / will act",
    ("volition", SELF): "the speaker will act",
    ("state", LISTENER): "a question about the listener", ("state", SELF): "the speaker's own state",
    ("home", SELF): "the speaker is home", ("home", LISTENER): "the listener is home",
    ("go", SELF): "the speaker leaves", ("go", LISTENER): "the listener leaves",
    ("eat", SELF): "the speaker eats", ("goodnight", LISTENER): "good night to the listener",
    ("favour", LISTENER): "the listener does it for the speaker", ("favour", SELF): "the speaker does it for the listener",
}
# Anchors of the greeting / state frames: judged only where a sentence has one of them with a pronoun
# slot ("Em đi đây, anh cũng đi đi" is two people leaving).
_ONE_USE = frozenset({"state", "home", "go", "invite", "wish", "sleep", "welcome", "excuse"})
_SUBJECT_ONLY = frozenset({"will", "leave", "go", "home", "state", "sleep"})


def _firm(frames: list[Frame]) -> Frame:
    """The frame an expectation is reported under: a firm one when there is one (Frame.tentative)."""
    return next((f for f in frames if not f.tentative), frames[0])


def _merge(frames: list[Frame]) -> tuple[str, str | None, Frame] | None:
    agents = {f.agent for f in frames}
    if len(agents) != 1:
        return None
    patients = {f.patient for f in frames if f.patient}
    return agents.pop(), (patients.pop() if len(patients) == 1 else None), _firm(frames)


def _frame_expectations(frames: list[Frame],
                        dirs: frozenset[str] = frozenset()) -> dict[str, tuple[str | None, str | None, Frame]]:
    """What the anchors of a line (or of one source sentence) must be, from its frames; `dirs`: who gives in
    its benefactive cues, frames or not (_ben_directions): a help / give / favour slot is not judged when a
    benefactive goes the other way (the thanks clause of 手伝ってくれてありがとう、今度は私が手伝ってあげるね, the
    condition of 手伝ってくれたら、ジュースおごってあげる)."""
    by: dict[str, list[Frame]] = {}
    for f in frames:
        by.setdefault(f.kind, []).append(f)
    req, offer = by.get("request", []), by.get("offer", []) + by.get("volition", [])
    out: dict[str, tuple[str | None, str | None, Frame]] = {}
    for kind in ("apology", "thanks"):
        m = _merge(by.get(kind, []))
        if m:
            out[kind] = m
    forgive = by.get("forgive", []) + [Frame("forgive", LISTENER, SELF, f.cue, f.start, f.end)
                                       for f in by.get("apology", []) if f.agent == SELF]
    m = _merge(forgive)
    if m:
        out["forgive"] = m
    # "giúp" only from a request FOR the speaker (〜てくれる, 手伝って, "help me") or an offer: a bare
    # imperative (待ってて, "Please wait") says nothing about who helps whom elsewhere in the line
    m = _merge([f for f in req if f.patient] + by.get("offer", []))
    if m and not dirs - {m[0]}:
        out["help"] = m
    if req and not offer:
        out["ask"] = (SELF, LISTENER, req[0])  # "Em nhờ anh": the requester is the subject
        out["please"] = (LISTENER, LISTENER, req[0])
        explicit = [f for f in req if f.patient == SELF]
        if explicit and not dirs - {LISTENER}:
            out["give"] = (LISTENER, SELF, explicit[0])  # "Anh cho em mượn": for / to the speaker
    if offer and not req:
        out["let"] = (None, SELF, _firm(offer))  # "Để em làm": the speaker
        out["leave"] = (SELF, None, _firm(offer))
        if not by.get("listener_ref"):
            out["will"] = (SELF, None, _firm(offer))
        if all(f.patient == LISTENER for f in offer) and not dirs - {SELF}:
            # 買ってあげる: "Chị mua kem cho em": for the listener; not with 任せて / let me ("Để đó cho tôi" is
            # "leave it to me")
            out["give"] = (SELF, LISTENER, _firm(offer))
    # who does an act for whom (benefactives and requests for the speaker): the one who does it is the subject
    # of "<giver> … cho / giúp / hộ / thay <the one it is for>" and the agent of "<for> được <giver> …"
    # (_favour_slots); off when the acts of the line disagree about who acts (an offer next to 〜てくれた)
    ben = by.get("favour", []) + [f for f in req if f.patient == SELF]
    acting = ben + by.get("offer", []) + by.get("volition", [])
    if ben and len({f.agent for f in acting}) == 1 and len({f.patient for f in ben}) == 1 \
            and not dirs - {ben[0].agent}:
        f = _firm(ben)
        out["favour"] = (f.agent, f.patient, f)
        if by.get("favour") and (f.agent, f.patient) == (LISTENER, SELF):
            # 来てくれたんだ: "Cảm ơn anh đã đến"; もらう: "Em nhờ anh…", "Nhờ anh mà…" (cues: asked again only)
            out.setdefault("thanks", (SELF, LISTENER, by["favour"][0]))
            out.setdefault("ask", (SELF, LISTENER, by["favour"][0]))
    # whose state a question / statement is about, per state word ("state:mệt"): 疲れてない？ -> "Anh có mệt không?"
    states: dict[str, list[Frame]] = {}
    for f in by.get("state", []):
        for key in _state_keys(f.cue):
            states.setdefault(key, []).append(f)
    for key, fs in states.items():
        m = _merge(fs)
        if m:
            out[f"state:{key}"] = (m[0], None, m[2])
    m = _merge(by.get("home", []))
    if m:  # ただいま: "Con về rồi đây"; おかえり: "Anh về rồi à", "Mừng anh về nhà"
        out["home"] = (m[0], None, m[2])
        if m[0] == SELF:
            out.setdefault("leave", (SELF, None, m[2]))
        else:
            out["welcome"] = (None, LISTENER, m[2])
    m = _merge(by.get("go", []))
    if m and (not req or m[0] == LISTENER):  # 行ってきます: "Cháu đi học đây ạ"; お先に失礼します: "Em xin phép về trước"
        out["go"] = (m[0], None, m[2])
        if m[0] == SELF:
            out.setdefault("leave", (SELF, None, m[2]))
            out["excuse"] = (SELF, LISTENER, m[2])
    if by.get("eat") and not req:  # いただきます: "Con mời mẹ ăn cơm"
        out["invite"] = (SELF, LISTENER, by["eat"][0])
    if by.get("goodnight"):  # おやすみ: "Chúc anh ngủ ngon", "Em ngủ ngon nhé", "Anh đi ngủ đây"
        out["wish"] = (None, LISTENER, by["goodnight"][0])
        out["sleep"] = (LISTENER, None, by["goodnight"][0])
        out.setdefault("leave", (SELF, None, by["goodnight"][0]))
    return out


_REF_TAG = re.compile(r"\{([SLX]):([^{}]+)\}")
_REF_ROLES = {"S": SELF, "L": LISTENER, "X": UNKNOWN}
_NEXT_SKIP = frozenset({"cũng", "đã", "sẽ", "đang", "vừa", "mới", "vẫn", "còn", "không", "chưa", "rất", "thật", "sự",
                        "có", "thể", "phải", "chỉ", "lại", "luôn", "cứ", "hãy", "đừng", "nhất", "định", "chắc", "chắn",
                        "lỡ", "trót", "từng", "sắp", "toàn"})
# Verbs and prepositions whose object a following pronoun is ("giúp bà lấy", "bảo em đi", "với anh").
_OBJECT_BEFORE = frozenset({"giúp", "giùm", "hộ", "cho", "để", "nhờ", "bảo", "mời", "rủ", "gọi", "đưa", "dẫn", "kêu",
                            "bắt", "cùng", "với", "thấy", "nghe", "muốn", "ơn", "lỗi", "nói", "hỏi", "xin", "thương",
                            "mắng", "đợi", "chờ", "cứu", "làm", "khiến", "cấm", "dạy", "nhắc", "giục", "hứa", "tha",
                            "thứ", "theo", "vì", "tại", "do", "bằng", "như", "là", "của", "trả", "tặng", "gửi", "phạt",
                            "đánh", "giận", "yêu", "quý", "nhớ", "biết", "tin", "lo", "sợ", "mong", "kể"})
_KEY_STOP = frozenset({"ơi", "ạ", "à", "nhé", "nha", "nhỉ", "là", "mà", "và", "với", "ấy", "này", "đấy", "đó", "thế",
                       "vậy", "rồi", "đi", "chứ", "hả", "sao", "nào", "hay", "hoặc", "của", "được", "nữa",
                       "thì", "ơn", "lỗi"})


def parse_ref(ref: str) -> tuple[str, list[tuple[int, int, str]]]:
    """A role-tagged reference ("{S:Em} xin lỗi {L:anh} nhé", X = not a pronoun use) -> (plain
    text, [(start, end, role)]) with spans in the plain text."""
    plain: list[str] = []
    tags: list[tuple[int, int, str]] = []
    pos = 0
    n = 0
    for m in _REF_TAG.finditer(_nfc(ref)):
        plain.append(_nfc(ref)[pos:m.start()])
        n += m.start() - pos
        word = m.group(2)
        tags.append((n, n + len(word), _REF_ROLES[m.group(1)]))
        plain.append(word)
        n += len(word)
        pos = m.end()
    plain.append(_nfc(ref)[pos:])
    return "".join(plain), tags


def _context_keys(toks: list[_Tok], i: int) -> list[tuple[str, str]]:
    """The verb a pronoun is the subject of ("Cháu bắt được" -> bắt). Not for a possessor ("tên của
    anh", "cho ông tôi xem": my grandpa) or an object that is also the next verb's subject ("cháu giúp
    bà lấy": bà is helped, the reference's "cháu lấy giúp bà" is the same thing)."""
    if i > 0 and toks[i].adjacent and (toks[i - 1].low in _KIN or toks[i - 1].low in _BASE
                                       or toks[i - 1].low in _OBJECT_BEFORE):
        return []
    j = i + 1
    while j < len(toks) and toks[j].adjacent and toks[j].low in _NEXT_SKIP:
        j += 1
    if j < len(toks) and toks[j].adjacent and toks[j].low not in _KEY_STOP and toks[j].low not in _BASE:
        return [("next", toks[j].low)]
    return []


def ref_disagreements(ref: str, own: Pair, other: Pair) -> list[str]:
    """Where analyze() disagrees with a reference's tags (a tagged word it does not see as that role,
    or a pronoun use with a known role it finds untagged): a check of the test data and the analyser."""
    plain, tags = parse_ref(ref)
    uses = {(u.start, u.end): u for u in analyze(plain, own, other)}
    out = []
    for s, e, role in tags:
        u = uses.pop((s, e), None)
        if role == UNKNOWN:
            if u is not None and u.role != UNKNOWN:
                out.append(f"{plain[s:e]!r}@{s}: tagged X, analysed {u.role}")
        elif u is None:
            out.append(f"{plain[s:e]!r}@{s}: tagged {role}, not seen as a pronoun use")
        elif u.role != role:
            out.append(f"{plain[s:e]!r}@{s}: tagged {role}, analysed {u.role}")
    out += [f"{u.word!r}@{u.start}: untagged, analysed {u.role}" for u in uses.values() if u.role != UNKNOWN]
    return out


_PRONOUN_WORDS = _BASE | _KIN | _PAIR_ONLY


def _title(word: str) -> bool:
    """A pair word that is a title or a name written with a capital ("Sensei", "Aris"), not a pronoun."""
    return word[:1].isupper() and word.lower() not in _PRONOUN_WORDS


def _as_written(*pairs: Pair) -> dict[str, str]:
    """Lower case -> the pair word as given ("sensei" -> "Sensei")."""
    items = [w for p in pairs for w in ([p] if isinstance(p, str) else list(p or ()))]
    return {w.strip().lower(): w.strip() for w in items if w and w.strip()}


def _cap(word: str | None, like: str, start: bool = False) -> str | None:
    """`word` capitalised like `like`, the word it replaces. A title or name used as a pair word ("Sensei" for
    the teacher, Aris calling herself "Aris") keeps its capital, and a pronoun that replaces one is capitalised
    only at the start of a sentence (`start`): "Sensei cảm ơn em" from a student -> "Em cảm ơn Sensei"."""
    if not word or _title(word):
        return word
    word = word.lower()
    return word[:1].upper() + word[1:] if (start if _title(like) else like[:1].isupper()) else word


def _question_sentences(text: str, toks: list[_Tok], sent_of: list[int]) -> set[int]:
    """The sentences of a translation that end with a question mark."""
    out = set()
    for i, t in enumerate(toks):
        if i + 1 < len(toks) and sent_of[i + 1] == sent_of[i]:
            continue
        after = toks[i + 1].gap if i + 1 < len(toks) else text[t.end:]
        if "?" in after or "？" in after:
            out.add(sent_of[i])
    return out


# --- explicit source pronouns and the slots they map to ---------------------------------------------
#
# A source pronoun with a clear grammatical role (お前は…じゃ, 私のプリン, お前を, "I'll protect you") names
# who is the subject and who the object / possessor of its sentence. The translation keeps the
# subjects and the objects of a simple sentence, so when a sentence (or the whole line) has as many
# subject (object) pronouns as the source, each must be the same person: "お前はわしの自慢の弟子じゃ"
# -> "Con là đệ tử ... của ta", not "Ta là đệ tử ... của con". Only where the structure carries over:
# no reported, quoted or conditional sentences, no request or "can I" offer (the translation adds "giúp
# em" or turns it into "Anh có cần em giúp không?"), no causative ("make you"), no clause moved to the
# front ("I'll call you when you get home" -> "Khi em về thì anh gọi"), no plural or reflexive; a
# Japanese topic of the listener only with a predicate it is the subject of (お前はわしの弟子じゃ, not
# お前は許さない "I won't forgive you"), が not before 好き / 欲しい / できる (an object there).

_ROLE_WORDS_JA = (  # a kinship or role word the speaker may call themself or the listener by
    (r"お?兄(?:ちゃん|さん|貴|上)|おにいちゃん|にいちゃん|にいに", ("anh",)),
    (r"お?姉(?:ちゃん|さん|貴|上)|おねえちゃん|ねえちゃん|ねえね", ("chị",)),
    (r"お?母(?:さん|ちゃん|上)|おかあさん|かあさん|ママ|お袋", ("mẹ", "má")),
    (r"お?父(?:さん|ちゃん|上)|おとうさん|とうさん|パパ|親父", ("bố", "ba", "cha")),
    (r"お?(?:ばあ|婆|祖母)(?:ちゃん|さん)", ("bà",)),
    (r"お?(?:じい|爺|祖父)(?:ちゃん|さん)", ("ông",)),
    (r"先生", ("thầy", "cô")),
    (r"師匠", ("thầy", "sư phụ")),
    (r"姫様", ("công chúa",)),
)
# not after a name or の (山田先生, ケンジのお母さん: someone else's), not a plural (先生方, お兄ちゃんたち)
_ROLE_RX = re.compile(r"(?<![の一-鿿゠-ヿ])(?:" + "|".join(f"(?P<r{i}>{rx})" for i, (rx, _) in enumerate(_ROLE_WORDS_JA))
                      + r")(?!たち|達|ら|方|がた|同士)")
_JA_PRONOUN_RX = re.compile(rf"(?P<s>{_JA_SELF})|(?P<o>{_JA_OTHER})")
_JA_PLURAL_AFTER = re.compile(r"たち|達|ら(?!れ)|ども|共|等|[一-鿿]")  # 私たち, 僕ら; 私立, 君主, 俺様: compounds
_KANA_PRONOUN_OK_BEFORE = "もはがをにとのねよさてで"  # わし / おれ inside a word (こわし, かわした) are none
_JA_PARTICLE = re.compile(r"(?:自身)?(?P<lim>だけ|ばかり|ばっか|さえ|すら|しか)?"
                          r"(?P<p>のこと|のため|のせい|のもの|の|を|には|にも|に|へ|とは|とも|と|から|より|まで|が|は|も|こそ|ってば"
                          r"|って|だって|なんか|なんて|じゃ|だ|です)?")
_JA_OBJ_PARTICLES = frozenset({"のこと", "のため", "のせい", "を", "には", "にも", "に", "へ", "とは", "とも", "と",
                               "より", "まで", "じゃ", "だ", "です"})
# から names where an act comes from, which has no one slot in the translation: the one who does it (部長には私から
# 話しておく -> "chị sẽ nói với trưởng phòng giúp em"), the giver (私からのプレゼント -> "quà cháu tặng bà") or the one
# left (私から離れないで -> "đừng rời xa em"): its sentence is not judged
_JA_SOURCE_PARTICLES = frozenset({"から"})
# が marks the object of these (お前が好き, 私が欲しいの, お前が心配だ: "Anh thích em"), either with できる / 分かる
# / 見える (お前ができる, お前が分かってない), and translations turn their sentences round: not judged. いい / よかった
# only in the pronoun's own clause (not いいのよ、先生がやっておくから), not 分かりやすい (easy to understand).
_JA_EMOTIVE = re.compile(r"好き|嫌い|欲し|ほし|怖|こわ|心配(?!す|し)|必要|大事|大切|苦手|得意|羨まし|うらやまし|恋し|憎い"
                         r"|会いた|あいた|要る|いら(?:ない|ねえ)")
_JA_EMOTIVE_CLAUSE = re.compile(r"いい|良い|よかった")
_JA_STATIVE = re.compile(r"(?:分か|わか)(?!りやす|りにく)|でき|出来|見え|聞こえ|知って|知ら")
_JA_EXIST = re.compile(r"(?:い|居|あ|有)(?:る|た|ない|ません|ます|なかった|て|れ)")
_JA_FINAL_PARTICLES = re.compile(r"(?:よ|ね|な|わ|の|ん|ぞ|ぜ|さ|か|から|けど|もん|って|し|じゃん|でしょう|でしょ|です|ー|〜|～|っ)*")
_JA_PRED_END = re.compile(r"(?:[うくすつぬふむゆるぐぶずづたてでだないえれせけろよねなぞぜわさのかんもしがっー〜]|から|けど)$")
_JA_COPULA_END = re.compile(r"(?:だ|です|じゃ|じゃん|だろ|だろう|でしょ|でしょう|なの|なんだ|なんです|である|だった|でした|だな)"
                            r"(?:よ|ね|な|ぞ|ぜ|わ|から|けど|って|か|もん)*$")
_JA_ADJ_END = re.compile(r"(?:(?<![なた])い|(?<!な)かった|くない|くなかった)(?:よ|ね|な|ぞ|ぜ|わ|から|けど|の|か|ん|んだ)*$")
_JA_IMP_END = re.compile(r"(?:[ろけせれてで]|(?<!ね)え|な|なさい|ください|下さい|ちょうだい)(?:よ|ね|って|ってば)?$")
_JA_PASSIVE = re.compile(r"[らさまかわばた]れ(?:る|た|て|ない|ます|ました|ちゃ)")
_JA_SUBORD_END = re.compile(r"(?:から|ので|けど|けれど|けれども|し|のに|て|で|ながら|たり|ば|と|たら|なら|ても|でも|が|まま)$")
# a verb ending a time clause (俺が戻るまで, 帰る前に, いるうちに, 寝てる間に, 歩きながら): who does it is no
# subject of the sentence (お前は俺が戻るまでここにいろ: お前 waits, 俺 comes back). Not a noun + まで (家まで送る).
_JA_TIME_CLAUSE = re.compile(r"(?:[うくすつぬふむゆるぐぶずづ]|ない|た)(?:まで|前に|うちに|間|あいだ)|ながら")
# sentences whose pronouns do not carry over one to one
_JA_REF_SKIP = re.compile(
    r"[「」『』]|(?:って|(?<![っん])(?<!わざ)と)(?:でも|は|も)?(?:言|い[っうわえ]|伝|聞|話|頼|書|思|考)"  # not ちゃんと聞け
    r"|ってさ(?=[。！!？?…\s]|$)"
    r"|って(?=[。！!？?…\s]|$)"
    r"|そうだ|らしい|たら|だら|れば|ければ|(?<!よう)なら(?!ない|ず|ん)|(?<!と)ても|としたら"  # reported, conditional
    r"|ましょうか|(?:お|よ)うか|てあげようか|てやろうか"  # an offer as a question: "Anh có cần em ... không?"
    r"|て(?:くれ|もら|いただ|ちょうだい)|[てで]?(?:ください|下さい)|お願い|頼む|頼みます|自分")  # requests, reflexive
_EN_REF = re.compile(r"(?<![\w'])(?:i'm|i'll|i'd|i've|i|me|my|mine|myself|we're|we'll|we've|we'd|we|us|our|ours"
                     r"|ourselves|you're|you'll|you've|you'd|you|your|yours|yourself)(?![\w'])", re.IGNORECASE)
_EN_SUBJ_FORMS = frozenset({"i", "i'm", "i'll", "i'd", "i've", "we", "we're", "we'll", "we've", "we'd", "you're",
                            "you'll", "you've", "you'd"})
_EN_SUBJ_LEAD = frozenset({"and", "but", "or", "so", "because", "if", "when", "while", "that", "then", "did", "do",
                           "does", "are", "were", "will", "would", "can", "could", "should", "have", "has", "had",
                           "may", "might", "must", "shall", "don't", "didn't", "won't", "can't", "wouldn't", "couldn't",
                           "shouldn't", "haven't", "aren't", "weren't", "hasn't", "why", "what", "how", "where", "who",
                           "which", "whether", "as", "now", "also", "ever", "never", "really", "just", "finally",
                           "still", "already", "hey", "oh", "well", "yes", "no", "okay", "ok", "sure", "maybe",
                           "perhaps"})
_EN_FIXED = re.compile(r"\b(?:excuse|pardon|bless|mind)\s+(?:me|you)\b"  # fixed phrases, tag questions, fillers
                       r"|,\s*(?:\w+n't|do|does|did|are|is|will|would|can|could|have|has|were|was)\s+(?:you|i|we)"
                       r"\s*(?=[?!.]|$)"
                       r"|(?:^|(?<=,))\s*(?:you know|you see|i mean|i guess|i suppose)\s*(?=,|[.!?]|$)"
                       # a generic "you" (anyone): "Mình đâu biết…", "Thắng thua là chuyện thường"
                       r"|\byou\s+(?:never\s+know|never\s+can\s+tell|only\s+live\s+once|win\s+some|lose\s+some"
                       r"|can't\s+win\s+them\s+all|can\s+never\s+be\s+too)\b", re.IGNORECASE)
_EN_ME_TOO = re.compile(r"\s+(?:too|neither|either)\s*(?=[.!?,…]|$)", re.IGNORECASE)  # "Me too!": I (feel / do) too
_EN_REF_SKIP = re.compile(
    r"[\"“”]|\b(?:said|says|told|tells|asked|asks|heard|hears)\b|\b(?:if|unless|whether|in case|suppose)\b"
    r"|\b(?:can|may|shall|should)\s+(?:i|we)\b|\b(?:do|would)\s+you\s+(?:want|like)\s+(?:me|us)\b|\bwant\s+me\s+to\b"
    r"|\blet\s+(?:me|us)\b|\blet's\b|\b(?:can|could|would|will)\s+you\b|\bplease\b|\bhelp\s+me\b"
    r"|\b(?:make|makes|made|let|lets|have|has|had|get|got|gets|see|saw|watch|hear|show|showed|teach|taught|take|took"
    r"|bring|brought|send|sent)\s+(?:me|you|us)\s+(?!to\b|for\b|with\b|at\b|in\b|on\b|out\b|up\b)[a-z]"
    r"|\byou\s+(?:guys|all|two|both)\b|\byourselves\b|\by'all\b"
    r"|\b(?:you|i|me|we|us)\s+(?:and|or|&)\s+(?:you|i|me|we|us)\b"  # "You and I" -> "Anh và em"
    r"|\bby\s+(?:you|me|us)\b"  # a passive: "I was saved by you" -> "Anh đã cứu em"
    # reflexive: "think about yourself" -> "nghĩ cho mình", "your own mess" -> "đống bừa bộn của mình"
    r"|\b(?:myself|yourself|ourselves)\b|\b(?:my|your|our)\s+own\b", re.IGNORECASE)
# a clause in the middle of the sentence that Vietnamese moves to the front ("..., khi em về thì ..."); before
# / after / until only with a clause after them ("before you go", not "before me", "after class")
_EN_MEDIAL = re.compile(r"(?<=\w)[,\s]+(?:(?:when|whenever|while|once|as soon as)\b"
                        r"|(?:before|after|until|till)\s+(?:(?:i|you|he|she|we|they|it|the|a|an|my|your|his|her|our"
                        r"|their|this|that|everyone|everybody|someone|somebody)\b|\w+ing\b|i'|you'|we'|they'))",
                        re.IGNORECASE)
_VI_SUBJ_LEAD = frozenset({"thì", "mà", "nhưng", "và", "còn", "nên", "rồi", "vậy", "thế", "thôi", "sao", "chính", "hay",
                           "hoặc", "chứ", "giờ", "mai", "nay", "xưa", "nãy", "này", "đó", "đấy", "kia", "ơi", "à", "ạ",
                           "nhé", "nha", "ừ", "dạ", "vâng", "ơ", "ê", "ôi", "á", "chắc", "lẽ", "hả", "nhỉ", "cơ", "ủa"})
_VI_SUBJ_LEAD2 = frozenset({("thật", "ra"), ("hóa", "ra"), ("cuối", "cùng"), ("hôm", "qua"), ("lần", "sau"),
                            ("lần", "trước"), ("tuần", "trước"), ("tuần", "sau"), ("năm", "ngoái"), ("năm", "sau"),
                            ("chẳng", "phải"), ("không", "phải"), ("có", "phải"), ("tại", "sao"), ("vì", "sao"),
                            ("làm", "sao"), ("vì", "vậy"), ("cho", "nên"), ("bây", "giờ"), ("lúc", "đó"), ("khi", "đó"),
                            ("hồi", "đó"), ("sau", "đó"), ("trước", "đây"), ("dạo", "này"), ("thật", "sự"),
                            ("tối", "qua"), ("đêm", "qua"), ("sáng", "qua"), ("trưa", "qua"), ("chiều", "qua"),
                            ("lát", "nữa"), ("chút", "nữa"), ("tí", "nữa")})
_VI_CLAUSE_LEAD = frozenset({"khi", "lúc", "nếu", "hễ", "rằng"})  # a pronoun after these heads a clause of its own
# Vietnamese turns the source's subject into an agent, a cause or the second party of a converse verb:
# "Anh được em cứu" / "Em bị anh đánh" (You saved me / You hit me), "Nhờ anh mà em…", "Em hết hồn vì anh",
# "Tại anh mà…", "do em", "Tao thua mày" (You beat me), "Tao chịu mày": who is the subject no longer follows
_VI_AGENT_BEFORE = frozenset({"được", "bị", "do", "bởi", "nhờ", "vì", "tại"})
_VI_CONVERSE = frozenset({"thua", "chịu"})
_VI_CAUSE_BEFORE = frozenset({"do", "nhờ", "vì", "tại"})  # these keep the slots when the source has its cause too:
_SRC_CAUSE = re.compile(r"せい|おかげ|ため|によ|\bbecause\b|\bthanks\s+to\b|\bdue\s+to\b|\bowing\s+to\b"
                        r"|\bfor\s+(?:you|me|us)\b", re.IGNORECASE)  # 僕のせいで -> "Tại em mà…", "for you" -> "Vì anh"
_VI_GROUP_LEAD = frozenset({"chúng", "bọn", "tụi", "các"})
_VI_REF_SKIP_WORDS = frozenset({"nếu", "hễ", "dặn", "nhắn"})
_VI_REF_SKIP_PAIRS = frozenset({("giá", "mà"), ("giả", "sử"), ("lỡ", "mà"), ("nghe", "nói"), ("nói", "là"),
                                ("nói", "rằng"), ("bảo", "là"), ("kể", "là")})
_BAO_NOT_REPORT = frozenset({"vệ", "đảm", "quản", "hiểm", "tàng", "trì", "hành", "lưu", "mật", "bối", "vật", "toàn",
                             "hộ", "dưỡng", "trọng", "an"})
_QUOTE_CHARS = re.compile(r"[\"“”‘’'«»「」『』]")


# RoleIssue.anchor of these checks; "owner": a source possessor without "của" in the translation ("tay mẹ")
_SLOT_NAMES = {"subj": "subject", "poss": "possessor", "obj": "object", "owner": "owner"}


@dataclass(frozen=True)
class _Ref:
    role: str  # SELF | LISTENER
    func: str  # "subj" | "poss" | "obj" | "?" (the sentence is not judged)
    start: int
    end: int
    cue: str


def _role_words(text: str, own_w: tuple[str, ...], other_w: tuple[str, ...]) -> list[_Ref]:
    """Kinship / role words of a Japanese source that stand for one of the pair: お姉ちゃん from the
    older sister who says "chị" is the speaker, 先生 from a student who says "cô" to her the listener."""
    out = []
    for m in _ROLE_RX.finditer(text):
        i = next(int(k[1:]) for k, v in m.groupdict().items() if v)
        vi = _ROLE_WORDS_JA[i][1]
        in_own, in_other = any(w in own_w for w in vi), any(w in other_w for w in vi)
        if in_own != in_other:
            out.append(_Ref(SELF if in_own else LISTENER, "", m.start(), m.end(), m.group(0)))
    return out


def _ja_func(text: str, role: str, p: str | None, lim: str | None, after: int) -> str | None:
    """The slot of a Japanese pronoun from its particle `p` (text from `after` on): None (bare: a
    vocative or a dropped particle), "subj", "poss" (の), "obj" or "?" (not clear)."""
    if not p:
        return "?" if lim else None
    rest = re.match(r"[^。！？!?…\n]*", text[after:]).group(0)  # to the end of the sentence
    sent = _sentence_at(text, max(0, after - 1))
    clause = re.match(r"[^、。！？!?…\n]*", text[after:]).group(0)
    own_clause = text[max(text.rfind(c, 0, after) for c in "、。！？!?…\n") + 1:after] + clause
    if _JA_EMOTIVE.search(sent) or _JA_STATIVE.search(sent) or _JA_EMOTIVE_CLAUSE.search(own_clause):
        return "?"  # 好き, 大事, 分かる: often turned round ("Anh là người em thích", "Với mẹ, con là quan trọng nhất")
    if p in ("の", "のもの"):
        return "poss"  # 私のプリン: "bánh pudding của em"
    if p in _JA_OBJ_PARTICLES:
        return "obj"
    if p in ("なんか", "なんて") or p in _JA_SOURCE_PARTICLES:
        return "?"
    question = text[after + len(rest):after + len(rest) + 1] in ("？", "?")
    if text[after + len(clause):after + len(clause) + 1] in ("、", ",") and _JA_SUBORD_END.search(clause):
        return "?"  # 私がついてるから、…: the subject of a reason / condition, the rest may have another
    if p == "が":
        if _JA_EXIST.match(text, after) or _JA_TIME_CLAUSE.search(rest):
            return "?"  # お前がいる: "Có em ở đây"; 俺が戻るまで: a time clause's subject
        # not お前が作ったケーキ / カレー / 選んだかばん (a relative clause; a final ー is no predicate's)
        past = re.search(r"[ただ](?![いくらりろ])(?P<after>[^ただ]*)$", clause)
        if past and not _JA_FINAL_PARTICLES.fullmatch(past.group("after")):
            return "?"
        return "subj" if _JA_PRED_END.search(re.sub(r"[ー〜～]+$", "", clause)) else "?"
    if role == SELF:
        return "subj"
    # without a name the speaker calls at the end (お前は本当に頑張ったな、トビ)
    pred = re.sub(r"、[゠-ヿ一-鿿]{1,8}(?:くん|ちゃん|さん|君|様|殿)?$", "", rest).rstrip("ー〜～っ ")
    mine = re.match(rf"[^、。！？!?…\n]*?{_JA_SELF}が", text[after:])
    if p == "は" and mine:
        # お前は俺が守る / 家まで送る: the topic is who I protect. Not when 俺が is in a time clause or the
        # sentence tells the listener to do something (お前は俺が戻るまでここにいろ: お前 stays, 俺 comes back)
        if _JA_TIME_CLAUSE.search(text, after + mine.end(), after + len(rest)) or _JA_IMP_END.search(pred):
            return "?"
        return "obj"
    if question or _JA_PASSIVE.search(rest) or _JA_COPULA_END.search(pred) or _JA_ADJ_END.search(pred) \
            or _JA_IMP_END.search(pred):
        return "subj"
    return "?"  # お前は許さない: the topic is the object


def _source_refs(text: str, ja: bool, own_w: tuple[str, ...], other_w: tuple[str, ...]) -> list[_Ref]:
    """The explicit pronouns of the source with their role and slot (in text order)."""
    out: list[_Ref] = []
    if ja:
        found = [(m.start(), m.end(), SELF if m.group("s") else LISTENER, m.group(0))
                 for m in _JA_PRONOUN_RX.finditer(text)]
        found += [(r.start, r.end, r.role, r.cue) for r in _role_words(text, own_w, other_w)]
        for s, e, role, cue in sorted(found):
            if any(s < r.end and r.start < e for r in out):
                continue
            if _JA_PLURAL_AFTER.match(text, e):
                out.append(_Ref(role, "?", s, e, cue))  # 私たち: a group
                continue
            if re.fullmatch(r"[぀-ゟ]+", cue) and s > 0 and "぀" <= text[s - 1] <= "ゟ" \
                    and text[s - 1] not in _KANA_PRONOUN_OK_BEFORE:
                continue
            m = _JA_PARTICLE.match(text, e)
            func = _ja_func(text, role, m.group("p"), m.group("lim"), m.end())
            if func:
                out.append(_Ref(role, func, s, e, cue))
        return out
    fixed = [m.span() for m in _EN_FIXED.finditer(text)]
    for m in _EN_REF.finditer(text):
        s, e = m.span()
        if any(a <= s < b for a, b in fixed):
            continue
        w = m.group(0).lower()
        role = LISTENER if w.startswith("you") else SELF
        if w in _EN_SUBJ_FORMS or (w == "me" and _EN_ME_TOO.match(text, e)
                                   and re.search(r"(?:^|[.!?,;:—])[\s\"“'‘]*$", text[max(0, s - 12):s])) \
                or (w == "you" and re.search(r"(?:^|[.!?,;:—])\s*same\s+to\s+$", text[max(0, s - 16):s], re.IGNORECASE)):
            func = "subj"  # "Me too." -> "Em cũng vậy", "Same to you" -> "Mày cũng thế"
        elif w in ("my", "mine", "your", "yours", "our", "ours"):
            func = "poss"
        elif w == "you":
            before = text[max(0, s - 80):s]  # the word before (a bounded look back: long lines stay linear)
            prev = re.search(r"([\w']+)[^\w']*$", before)
            gap = before[prev.end(1):] if prev else ""
            first = not prev or re.search(r"[.!?,;:—]", gap)
            func = "subj" if first or prev.group(1).lower() in _EN_SUBJ_LEAD else "obj"
        else:
            func = "obj"
        out.append(_Ref(role, func, s, e, m.group(0)))
    return out


def _vi_func(toks: list[_Tok], i: int) -> str | None:
    """The slot of the pronoun toks[i] in its Vietnamese clause: "subj" (first in its clause, or after a
    conjunction / time word: "Mai anh sẽ quay lại"), "let" ("Để em làm"), "obj" (after a verb, a
    preposition or a noun: "bảo vệ anh", "của em", "tay cháu"), "?" (a topic "Em thì…", the subject of an
    embedded clause: "để anh đưa", "khi anh về", "Đây là em làm": the sentence is not judged) or None
    ("Anh, em xin lỗi": an address)."""
    t = toks[i]
    nxt = toks[i + 1] if i + 1 < len(toks) and toks[i + 1].adjacent else None
    if nxt is not None and nxt.low == "thì":
        return "?"  # a topic ("Em thì để anh đưa về": em is taken home)
    if not t.adjacent:
        if i + 1 < len(toks) and "," in toks[i + 1].gap and not _SENT_END.search(toks[i + 1].gap):
            return None
        return "subj"
    prv = toks[i - 1].low
    if prv == "của":
        return "poss"
    if prv == "để":
        # "Để em làm"; inside a clause the subject of what follows, whoever the source's subject is ("Em sẽ
        # để anh đưa em đi" for お前は俺が送っていく), except one made to ("để tôi phải đợi": keep you waiting)
        if _sentence_start(toks, i - 1) or _after_interjections(toks, i - 1):
            return "let"
        return "obj" if nxt is not None and nxt.low == "phải" else "?"
    if prv in _VI_CLAUSE_LEAD:
        return "?"  # the subject of a time / condition clause ("cho đến khi anh quay lại")
    if prv in _VI_SUBJ_LEAD or (i >= 2 and toks[i - 1].adjacent and (toks[i - 2].low, prv) in _VI_SUBJ_LEAD2):
        return "subj"
    if prv == "trông" and _sentence_start(toks, i - 1):
        return "subj"  # "Trông em mệt quá" (you look tired; not "Nhìn anh này": look at me)
    if prv == "là" and nxt is not None and nxt.low not in _FINAL_PARTICLES and nxt.low not in _DEMONSTRATIVE_AFTER:
        return "?"  # "Đây là em làm": the subject of a clause after "là", not "Là em đấy" (it's me)
    return "obj"


_PERSON_AFTER = frozenset({*_THIRD_AFTER, "gái", "trai", "ruột", "họ", "dâu", "rể", "cả", "út", "hai", "lớn", "nhỏ",
                           "bé", "nội", "ngoại", "giáo", "chủ", "hàng", "này", "đấy", "đó", "kia"})


def _person_mention(toks: list[_Tok], i: int, uses: dict[int, PronounUse], words: frozenset[str]) -> bool:
    """toks[i] is a kinship word for someone other than the pair's two (anh ấy, em gái, mẹ em, chúng em,
    anh Kenji): a source pronoun may have become it, so the slots no longer line up."""
    t = toks[i]
    if i in uses or not (t.low in _KIN or t.low in _BASE or t.low in words):
        return False
    nxt = toks[i + 1] if i + 1 < len(toks) and toks[i + 1].adjacent else None
    prv = toks[i - 1] if i > 0 and t.adjacent else None
    if nxt is not None and (nxt.low in _PERSON_AFTER or nxt.low in _KIN or (_is_upper(nxt) and nxt.low not in words)):
        return True
    if t.low == "mình" and prv is not None and prv.low in ("một", "tự"):
        return False  # "một mình" (alone), "tự mình" (by oneself): no one
    return prv is not None and (prv.low in _KIN or prv.low in _VI_GROUP_LEAD or prv.low in _PLURAL_BEFORE)


def _vi_skip(toks: list[_Tok]) -> bool:
    """A conditional or reported Vietnamese sentence ("Nếu anh…", "Mẹ dặn…", "nói là…")."""
    for j, t in enumerate(toks):
        nxt = toks[j + 1].low if j + 1 < len(toks) and toks[j + 1].adjacent else ""
        if t.low in _VI_REF_SKIP_WORDS or (t.low, nxt) in _VI_REF_SKIP_PAIRS:
            return True
        if (t.low == "bảo" and nxt not in _BAO_NOT_REPORT) or (t.low == "kể" and nxt != "cả"):
            return True
    return False


def _vi_restructured(toks: list[_Tok], idx: list[int], uses: dict[int, PronounUse], source: str) -> bool:
    """The sentence made the source's subject an agent, a cause or the partner of a converse verb ("Anh
    được em cứu", "Em hết hồn vì anh", "Nhờ mẹ mà…", "Tao thua mày"): its slots do not line up. A cause
    the source names as well (僕のせいで -> "Tại em mà…", "for you" -> "Vì anh") keeps them."""
    cause = bool(_SRC_CAUSE.search(source))
    for i in idx:
        if toks[i].low in _VI_CONVERSE:
            return True
        prv = toks[i - 1].low if i > 0 and toks[i].adjacent else ""
        if i in uses and prv in _VI_AGENT_BEFORE and not (cause and prv in _VI_CAUSE_BEFORE):
            return True
    return False


def _vi_group(toks: list[_Tok], own_w: tuple[str, ...], other_w: tuple[str, ...]) -> bool:
    """A plural or group form ("chúng tôi", "bọn em", "anh em mình", "chị em mình"): an English "we"
    became it."""
    words = (*own_w, *other_w)
    for j, t in enumerate(toks):
        nxt = toks[j + 1] if j + 1 < len(toks) and toks[j + 1].adjacent else None
        if nxt is not None and t.low in _VI_GROUP_LEAD and (nxt.low in _BASE or nxt.low in ("ta", "mình")):
            return True
        if nxt is not None and t.low in _KIN and nxt.low in (*_KIN, "mình"):
            return True
        if t.low in ("mình", "ta") and t.low not in words:
            return True
    return False


def _pronoun_slots(source: str, src_lang: str, vi_text: str, toks: list[_Tok], uses: dict[int, PronounUse],
                   sent_of: list[int], aligned: bool, own_w: tuple[str, ...],
                   other_w: tuple[str, ...]) -> list[tuple[int, str, str, str]]:
    """(token index, needed role, slot, source cue) for the pronouns of the translation whose slot an
    explicit source pronoun fills with the other person (see the section comment)."""
    text = _source_text(source, src_lang)
    ja = _is_ja(text, src_lang)
    refs = _source_refs(text, ja, own_w, other_w)
    if not refs:
        return []
    words = frozenset((*own_w, *other_w))
    quote_pos = _vi_quotes(vi_text)  # a quote in a sentence: another speaker's words, not judged
    sents = _source_sentences(text)
    starts = [a for a, _ in sents]
    n_units = len(sents) if aligned else 1

    def unit_of_src(pos: int) -> int:
        return max(0, bisect_right(starts, pos) - 1) if aligned else 0

    src_units: list[list[_Ref]] = [[] for _ in range(n_units)]
    for r in refs:
        src_units[unit_of_src(r.start)].append(r)
    vi_units: list[list[int]] = [[] for _ in range(n_units)]
    for i in range(len(toks)):
        vi_units[sent_of[i] if aligned else 0].append(i)
    out = []
    for k in range(n_units):
        src_refs = src_units[k]
        if not src_refs or any(r.func == "?" for r in src_refs):
            continue
        a, b = (sents[k] if aligned else (0, len(text)))
        stext = text[a:b]
        if (_JA_REF_SKIP if ja else _EN_REF_SKIP).search(stext) or (not ja and _EN_MEDIAL.search(stext)):
            continue
        if ja and _JA_PASSIVE.search(stext) and "に" in stext:
            continue  # 私は先生に褒められた -> "Cô đã khen em": a passive with its agent is often made active
        idx = vi_units[k]
        if not idx:
            continue
        vtoks = toks[idx[0]:idx[-1] + 1]
        lo = toks[idx[0]].start - len(toks[idx[0]].gap)
        hi = toks[idx[-1] + 1].start if idx[-1] + 1 < len(toks) else len(vi_text)
        if _vi_skip(vtoks) or any(lo <= q < hi for q in quote_pos) or _vi_restructured(toks, idx, uses, stext):
            continue
        if any(uses[i].role == UNKNOWN for i in idx if i in uses):
            continue  # someone outside the pair ("chị" between anh and em)
        tail = _address_tail(toks, idx)
        idx = [i for i in idx if i not in tail]  # "…, con trai.": an address
        we = [r for r in src_refs if r.cue.lower().startswith(("we", "us", "our"))]
        group = not ja and bool(we) and _vi_group(vtoks, own_w, other_w)
        if we and (group or not any(r.role == LISTENER for r in src_refs)):
            # "We're sorry" -> "Chúng tôi xin lỗi"; a "we" with no "you" apart from it may include the listener
            # ("We'll be late!" -> "Con sẽ muộn mất"): only "We'll pay you back" is clearly the speaker's side
            src_refs = [r for r in src_refs if r not in we]
        if not group and any(_person_mention(toks, i, uses, words) for i in idx):
            continue  # "Em làm cho anh ấy", "cà ri của em gái tôi": who the source names is someone else here
        # possessors apart from other objects: "お兄ちゃんの料理" may become "anh nấu cho em" (you cook for me)
        slots: dict[str, list[int]] = {"subj": [], "poss": [], "obj": []}
        unclear = False
        for i in idx:
            if i in uses and not uses[i].vocative:
                f = _vi_func(toks, i)
                unclear |= f == "?"
                if f in slots:
                    slots[f].append(i)
        if unclear or (not ja and len(src_refs) != sum(len(v) for v in slots.values())):
            continue  # English names every subject: a translation with more pronouns was restructured
            # ("Are you looking for…?" -> "Em có thể giúp anh tìm…?")
        want_of = {slot: [r for r in src_refs if r.func == slot] for slot in ("subj", "poss", "obj")}
        checks = [(slot, want_of[slot], slots[slot]) for slot in ("subj", "poss", "obj")]
        if want_of["poss"] and not want_of["obj"] and not slots["poss"] and slots["obj"] \
                and all(toks[i - 1].low not in _OBJECT_BEFORE for i in slots["obj"]):
            checks.append(("owner", want_of["poss"], slots["obj"]))  # your hand -> "tay mẹ": no "của"
        for slot, want, got in checks:
            if not want or len(want) != len(got):
                continue
            if ja and len({r.role for r in want}) > 1:
                continue  # Japanese word order differs: only one person in this slot
            out += [(i, r.role, slot, r.cue) for r, i in zip(want, got)]
    return out


_ADDRESS_AFTER = frozenset({"trai", "gái", "yêu", "ngoan", "à", "ạ", "ơi", "nhé", "nha"})


def _address_tail(toks: list[_Tok], idx: list[int]) -> set[int]:
    """The words of a final ", con trai." / ", em yêu!" / ", cô Mori." of a sentence: an address, no person
    its slots name."""
    j = next((i for i in reversed(idx) if "," in toks[i].gap and not _SENT_END.search(toks[i].gap)), None)
    tail = [i for i in idx if j is not None and i >= j]
    if j is None or j == idx[0] or len(tail) != 2:
        return set()
    first, second = toks[tail[0]].low, toks[tail[1]]
    named = _is_upper(second) and second.adjacent  # a title and a name
    return set(tail) if (first in _KIN or first in _BASE) and (second.low in _ADDRESS_AFTER or named) else set()


def _vi_quotes(text: str) -> list[int]:
    """Positions of the quote marks of a translation, without one pair around all of it (the line itself)."""
    pos = [m.start() for m in _QUOTE_CHARS.finditer(text)]
    if len(pos) == 2 and pos[0] == len(text) - len(text.lstrip()) and not text[pos[1] + 1:].strip(" .!?…。！？"):
        return []
    return pos


def _outside_pair(uses: dict[int, PronounUse], own_w: tuple[str, ...], other_w: tuple[str, ...],
                  with_ref: bool) -> bool:
    """"Chị ơi, …" toward a stranger, "Để chị dán băng cho em" from a sister who says "em" to her brother:
    the line is not between the pair's two people (with a reference, its tags say who is who)."""
    return any(u.word.lower() not in (*own_w, *other_w)
               and (u.vocative or (u.role == UNKNOWN and u.word.lower() in _KIN and not with_ref))
               for u in uses.values())


# --- who does it for whom in the translation ----------------------------------------------------------
#
# A benefactive frame (or a request for the speaker) says who does the act and for whom; Vietnamese puts the
# one it is for after "cho / giúp / giùm / dùm / hộ / thay / tặng" and the one who does it first in the clause
# ("Em làm cơm hộp cho anh à?", "Hôm qua chị đã làm thay ca cho em"), or turns it round: "<for> được <giver>
# <verb>" ("Em lúc nào cũng được cô giúp đỡ"). Not "cho phép / cho nên / cho đến / cho dù / cho rằng", the
# causative "làm cho anh vui", "thay đổi / thay vì", "ủng hộ / hộ chiếu"; no clause subject past another person,
# a name, a report, a condition, a passive, a coordination or a verb of thinking ("Tôi cảm ơn anh đã làm … cho
# tôi": tôi thanks, anh did it); nothing in a conditional or reported clause, a quote of the translation, a
# reflexive ("tự lo cho mày"), and nothing in a sentence whose own acts go both ways ("Em làm cho anh, anh
# giúp em nhé": which one renders the source's act is not clear).

_BEN_MARK = frozenset({"cho", "giúp", "giùm", "dùm", "hộ", "thay", "tặng"})
_BEN_OBJ_SKIP = frozenset({"đỡ", "cho"})  # "giúp đỡ em", "làm thay cho em"
# not "for" either: purpose and result ("chép cho kịp", "làm cho xong", "cho vui")
_CHO_NOT_AFTER = frozenset({"phép", "nên", "đến", "tới", "dù", "rằng", "là", "qua", "hay", "kịp", "xong", "vui", "đẹp", "chắc",
                            "nhanh", "kỹ", "kĩ", "đỡ", "thoáng", "sạch", "đủ", "tiện", "đã", "đều", "gọn", "chuẩn"})
# a converse verb's subject is the one who receives ("Em mượn vở…" for ノート貸してくれる？, "con xin thêm…"): no giver
_CONVERSE = frozenset({"mượn", "xin", "vay", "nhận", "thuê"})
# "nhận … từ <giver>" (receive from): not "nhận ra / thấy / xét / lỗi / lời …" (notice, admit, accept)
_NHAN_NOT_AFTER = frozenset({"ra", "thấy", "xét", "lỗi", "lời", "biết", "định", "diện", "thức", "lấy", "lệnh", "việc", "nuôi",
                             "làm", "phần", "tội"})
# after "làm cho <pronoun>": the end of the clause or these make it "do it for you", not the causative
# "làm cho anh vui" ("Mẹ sẽ làm cho con đây", "làm cho em một cái bánh")
_FOR_AFTER = frozenset({*_FINAL_PARTICLES, *_Q_PARTICLES, "đây", "nè", "này", "luôn", "ngay", "liền", "trước", "sau",
                        "xong", "nữa", "hết", "một", "cái", "món", "bữa", "ít", "chút", "tí", "mấy", "vài"})
_THAY_NOT_AFTER = frozenset({"đổi", "thế", "vì", "lòng", "áo", "quần", "đồ"})
_BEN_BLOCK = frozenset({*_REPORT, *_CONDITIONAL, *_THAT, *_CONVERSE, "để", "nhờ", "thì", "mà", "được", "bị", "do", "bởi",
                        "vì", "tại", "ai", "và", "với", "cùng", "hay", "hoặc", "cảm", "cám", "lỗi", "tạ", "thấy", "nghĩ",
                        "tưởng", "biết", "nghe", "tin", "khiến", "cho", "giúp", "tự"})


def _ben_marker(toks: list[_Tok], m: int) -> bool:
    """toks[m] puts the one an act is for after it ("cho em", "giúp anh", "làm thay chị")."""
    w = toks[m].low
    if w not in _BEN_MARK:
        return False
    nxt = toks[m + 1].low if m + 1 < len(toks) and toks[m + 1].adjacent else ""
    prv = toks[m - 1].low if m > 0 and toks[m].adjacent else ""
    if w == "cho":
        return nxt not in _CHO_NOT_AFTER
    if w == "hộ":
        return not (prv in _HO_NOT_BEFORE or nxt in _HO_NOT_AFTER)
    if w == "thay":
        return nxt not in _THAY_NOT_AFTER and prv != "đổi"
    return True


def _ben_object(toks: list[_Tok], m: int, uses: dict[int, PronounUse]) -> int | None:
    """The pronoun directly after a marker: the one the act is for. Not the causee of "làm cho anh vui"."""
    j = m + 1
    if toks[m].low != "cho" and j < len(toks) and toks[j].adjacent and toks[j].low in _BEN_OBJ_SKIP:
        j += 1
    if not (j < len(toks) and toks[j].adjacent and j in uses):
        return None
    if toks[m].low == "cho" and m > 0 and toks[m].adjacent and toks[m - 1].low == "làm" \
            and not _relative_subject(toks, m - 2, uses):
        k = j + 1  # "làm cho anh vui": causative; "làm cho anh à / nhé / đây / một cái / phải không": for you
        tag = toks[k].low == "phải" and k + 1 < len(toks) and toks[k + 1].low in ("không", "chứ", "hông") \
            if k < len(toks) else False
        if k < len(toks) and toks[k].adjacent and toks[k].low not in _FOR_AFTER and not tag:
            return None
    return j


def _relative_subject(toks: list[_Tok], i: int, uses: dict[int, PronounUse]) -> bool:
    """toks[i] is a pronoun right after a noun, the subject of a relative clause ("Hộp cơm anh làm cho em ngon
    lắm": the lunch you made for me; not the causative "Em làm cho anh vui")."""
    return i > 0 and i in uses and toks[i].adjacent and _vi_func(toks, i) == "obj" \
        and toks[i - 1].low not in _OBJECT_BEFORE and toks[i - 1].low not in _BASE and toks[i - 1].low not in _KIN


def _possessor(toks: list[_Tok], j: int) -> bool:
    """toks[j] owns the noun before it ("áo sơ mi của con", "em trai chị"): no subject of what follows."""
    if j == 0 or not toks[j].adjacent:
        return False
    prv = toks[j - 1].low
    return prv == "của" or (j >= 2 and toks[j - 1].adjacent and toks[j - 2].low in _KIN
                            and prv in _NOT_AFTER.get(toks[j - 2].low, ()))


def _ends_clause(toks: list[_Tok], m: int) -> bool:
    """Only final particles after toks[m] in its clause ("mẹ nấu cho nhé", "để anh làm giúp")."""
    j = m + 1
    while j < len(toks) and toks[j].adjacent and toks[j].low in _FINAL_PARTICLES:
        j += 1
    return j >= len(toks) or not toks[j].adjacent


# (not khi / lúc: "Lúc về em sẽ mua kem cho chị" is a time, and a when-clause of the source's act renders it:
# 手伝ってくれた時 -> "khi anh giúp em"; one before the subject is still no slot, check_line)
_COND_LEAD = frozenset({"nếu", "hễ"})
_COND_PAIRS = frozenset({("giá", "mà"), ("giả", "sử"), ("lỡ", "mà"), ("nhỡ", "mà"), ("miễn", "là"), ("giả", "như")})
_COND_NOT_AFTER = frozenset({"nào", "đó", "ấy", "này", "nãy", "nay", "trước", "xưa"})


def _condition_flags(toks: list[_Tok], uses: dict[int, PronounUse]) -> list[bool]:
    """Per word of a translation: it is in the condition of its clause - after nếu / hễ / giá mà / giả sử /
    lỡ mà with no "thì" between ("Em sẽ mời anh nếu anh giúp em"), before a "thì" ("Anh giúp em thì em bao
    anh"), or after "<pronoun> mà" with "là" after it ("Anh mà giúp em là em bao anh"). One pass per clause."""
    flags = [False] * len(toks)
    starts = [i for i in range(len(toks)) if i == 0 or not toks[i].adjacent] + [len(toks)]
    for s, e in zip(starts, starts[1:]):
        thi_after, la_after = [False] * (e - s), [False] * (e - s)
        thi = la = False
        for j in range(e - 1, s - 1, -1):
            thi_after[j - s], la_after[j - s] = thi, la
            thi |= toks[j].low == "thì"
            la |= toks[j].low == "là"
        cond = ma = False
        for j in range(s, e):
            flags[j] = thi_after[j - s] or cond or (ma and la_after[j - s])
            w = toks[j].low
            nxt = toks[j + 1].low if j + 1 < len(toks) and toks[j + 1].adjacent else ""
            if w == "thì":
                cond = ma = False  # "Nếu anh mệt thì em giúp anh": the main clause after it
            elif (w in _COND_LEAD and nxt not in _COND_NOT_AFTER) or (w, nxt) in _COND_PAIRS:
                cond = True
            elif w == "mà" and j - 1 in uses:
                ma = True
    return flags


def _receive(toks: list[_Tok], m: int, uses: dict[int, PronounUse]) -> int | None:
    """"<for> được <giver> <verb>" ("Em được cô giúp đỡ", "Em lúc nào cũng được cô giúp"): the giver's index."""
    j, k = m + 1, m + 2
    if toks[m].low != "được" or not (j < len(toks) and toks[j].adjacent and j in uses) \
            or not (k < len(toks) and toks[k].adjacent) or toks[k].low in _KEY_STOP or toks[k].low in _BASE \
            or toks[k].low in _FINAL_PARTICLES:
        return None
    prv = toks[m - 1].low if m > 0 and toks[m].adjacent else ""
    return j if (m - 1) in uses or (prv in _PRE_ADV and prv != "làm") else None


def _ben_subject(toks: list[_Tok], m: int, slot_uses: dict[int, PronounUse], uses: dict[int, PronounUse],
                 words: frozenset[str]) -> int | None:
    """The subject of the clause toks[m] is in: its first pronoun, in a subject position (_vi_func), with
    no other pronoun between it and toks[m] and nothing that gives the rest of the clause another subject
    ("Em sẽ nấu món cà ri mà chị thích cho chị": chị likes it, em cooks)."""
    s = m
    while s > 0 and toks[s].adjacent:
        s -= 1
    cand = next((j for j in range(s, m) if j in slot_uses), None)
    if cand is None or _vi_func(toks, cand) != "subj":
        return None
    for j in range(cand + 1, m):
        t = toks[j]
        nxt = toks[j + 1].low if j + 1 < len(toks) and toks[j + 1].adjacent else ""
        if t.low in ("lúc", "khi") and nxt == "nào":
            continue  # "lúc nào cũng" (always), not "when"
        if j in slot_uses or t.low in _BEN_BLOCK or (_is_upper(t) and not _sentence_start(toks, j)) \
                or _person_mention(toks, j, uses, words):
            return None
    return cand


def _receive_from(toks: list[_Tok], m: int, uses: dict[int, PronounUse]) -> int | None:
    """"<for> nhận … từ <giver>" ("Em lúc nào cũng chỉ nhận sự giúp đỡ từ anh"): the giver's index."""
    nxt = toks[m + 1].low if m + 1 < len(toks) and toks[m + 1].adjacent else ""
    if toks[m].low != "nhận" or nxt in _NHAN_NOT_AFTER:
        return None
    j = m + 1
    while j + 1 < len(toks) and toks[j].adjacent:
        if toks[j].low == "từ" and toks[j + 1].adjacent and j + 1 in uses:
            return j + 1
        j += 1
    return None


def _favour_slots(toks: list[_Tok], uses: dict[int, PronounUse], unit_of: list,
                  units: dict[object, tuple[str, str, Frame]], words: frozenset[str],
                  quote_pos: list[int]) -> list[tuple[int, str, str, Frame]]:
    """(token index, needed role, "giver" | "for", frame) for each pronoun of the translation in a slot of who
    does an act for whom, where `units` (unit -> (giver, the one it is for, frame); `unit_of`: the unit of each
    token, _Expect.act_key: a sentence, the line when the sentences do not align, or a clause of either) has a
    benefactive expectation (see the section comment)."""
    slot_uses = {i: u for i, u in uses.items() if not u.vocative}
    by_unit: dict[object, list[tuple[int | None, int | None, bool]]] = {}
    in_condition = _condition_flags(toks, slot_uses)
    skip_clause: dict[int, bool] = {}
    for m in range(len(toks)):
        unit = unit_of[m]
        if unit not in units:
            continue
        recv = toks[m].low in ("được", "nhận")
        if toks[m].low == "được":
            obj = _receive(toks, m, slot_uses)
            if obj is None:
                continue
        elif toks[m].low == "nhận":
            obj = _receive_from(toks, m, slot_uses)
            if obj is None:
                continue
        elif _ben_marker(toks, m):
            obj = _ben_object(toks, m, slot_uses)
        else:
            continue
        s = m
        while s > 0 and toks[s].adjacent:
            s -= 1
        if s not in skip_clause:  # once per clause: reported, conditional, reflexive or quoted
            clause = _clause(toks, m)
            lo, hi = clause[0].start - len(clause[0].gap), clause[-1].end + 1
            skip_clause[s] = _vi_skip(clause) or any(t.low == "tự" for t in clause) \
                or any(lo <= q <= hi for q in quote_pos)
        if in_condition[m] or skip_clause[s]:
            continue
        # "cho" with no one after it says for whom only at the end of its clause ("mẹ nấu cho nhé"): not "cho hộp
        # cơm", "cho buổi dã ngoại" (for a thing), whose clause subject need not be anyone's giver
        subj = None if obj is None and toks[m].low == "cho" and not _ends_clause(toks, m) \
            else _ben_subject(toks, m, slot_uses, uses, words)
        if subj is not None or obj is not None:
            by_unit.setdefault(unit, []).append((subj, obj, recv))
    out = []
    for unit, found in by_unit.items():
        giver, ben, frame = units[unit]
        roles = [((o, s) if recv else (s, o)) for s, o, recv in found]  # (giver index, for index)
        givers = {slot_uses[g].role for g, _ in roles if g is not None} - {UNKNOWN}
        fors = {slot_uses[b].role for _, b in roles if b is not None} - {UNKNOWN}
        if len(givers) > 1 or len(fors) > 1:
            continue  # the translation's acts go both ways
        for g, b in roles:
            if g is not None:
                out.append((g, giver, "giver", frame))
            if b is not None:
                out.append((b, ben, "for", frame))
    return out


_CUE_PARTICLE = re.compile(r".*[をがはにもへとの]")
_SCRIPT = (re.compile(r"[一-鿿々]"), re.compile(r"[゠-ヿ]"), re.compile(r"[぀-ゟ]"))


def _script(ch: str) -> int:
    return next((n for n, rx in enumerate(_SCRIPT) if rx.match(ch)), -1)


def _act_cue(text: str, f: Frame) -> str:
    """The source words of a benefactive or request frame for the note: the verb before a request's て
    (入れてくれる), starting at a word (after a particle or where the script changes: 送ってくれた, not
    ちゃんが送ってくれた / ュースおごってあげる); an English request to the end of its clause ("Can you cover for
    me", not "Can you")."""
    if not _is_ja(text, "auto"):
        rest = re.match(r"[^,.!?;:…]*", text[f.end:]).group(0)
        if f.kind == "favour" or not rest[:1].isspace():
            return f.cue.strip()
        return " ".join([f.cue.strip(), *rest.split()[:6]])
    start = f.start if f.kind == "favour" else f.start - len(re.search(r"[^\s、。！？!?…「」『』()（）]{0,6}$",
                                                                       text[:f.start]).group(0))
    end = f.end
    if re.match(r"[一-鿿]", text[end - 1:end]):  # 私が手伝 -> 私が手伝う: the word's ending
        end += len(re.match(r"[぀-ゖー〜]{0,8}", text[end:]).group(0))
    cue_start = start
    if start > 0 and not re.match(r"[\s、。！？!?…「」『』()（）]", text[start - 1]):  # cut inside the text before
        verb = text.index("て", start) if "て" in text[start:end] else end  # the verb stem ends at the first て
        lead = text[start:verb]
        m = _CUE_PARTICLE.match(lead)
        if m and m.end() < len(lead):
            cue_start = start + m.end()  # after the last particle: お前の分も|買ってきてやった
        elif _script(text[start - 1]) == _script(text[start]):
            # inside a word: from where the script changes (じゃがいも|入れてくれる, ジュース|おごってあげる)
            k = next((n for n in range(1, len(lead)) if _script(lead[n]) != _script(lead[n - 1])), None)
            cue_start = start + k if k else start
    return text[cue_start:end].strip()


# --- which clause of the translation renders which act -------------------------------------------------
#
# A source sentence with more than one predicate clause (迎えに来てくれてありがとう、荷物持つね: the speaker's own act
# follows the thanks; 宿題手伝ったら、アイスおごってあげる: a condition before the offer; 手伝ってくれてありがとう、今度は
# 私が手伝ってあげるね: favours both ways) has acts the translation renders with the same "giúp" / "cho" ("Cảm ơn anh
# đã đến đón em, để em xách đồ giúp anh nhé"). The act anchors (help, give, ask, please) and the favour slots of
# such a sentence are judged per clause, each Vietnamese clause by the frames of the source clause at its place,
# when both sides have as many clauses (names, vocatives, topics, interjections, thanks or relief alone not
# counted on the source side; a relative clause on a noun topic and a cause with おかげで go with the clause after
# them: 君が貸してくれたノート、すごく役に立った; vocatives and names alone not counted on the Vietnamese side), the
# thanks of both sides are in the same places and no Vietnamese clause after the first opens with a connective
# that may have moved it ("…, vì / nếu / khi …"); those slots are only asked again (basis "cue"). Otherwise the
# act anchors and favour slots of the sentence are not judged.

_SRC_CLAUSE = re.compile(r"[^、,，]+")
_JA_INTERJ = re.compile(r"(?:はい|うん|ええ|いいえ|ああ|おう|よし|さあ|じゃあ|まあ|ほら|ねえ|ねぇ|ねー|あのね|あのさ|あの|おい|もう"
                        r"|ちょっと|えっと|そう|そっか|へえ|ふーん|えっ|え|あっ|あ|なあ|ほんと|本当|マジ)[ー〜～っ！!？?…。\s]*$")
# a clause that is a noun phrase (お弁当、, 僕のシャツ、, これ、, 一緒に): it ends in a kanji / katakana noun, a demonstrative
# or a particle. Anything else counts as a predicate (counting one too many only leaves a line unjudged).
_JA_NOUN_CLAUSE = re.compile(r"(?:[一-鿿゠-ヿ々ー]|これ|それ|あれ|ここ|そこ|あそこ|[のをがはにへともや])$")
_GRATEFUL_LEAD = re.compile(r"(?:本当に|ほんとに|ほんとうに|すごく|とても|マジで|めっちゃ|いつも|心から)?")
_EN_TRIVIAL = re.compile(r"^\W*(?:(?:oh|hey|well|so|okay|ok|yes|yeah|no|wow|um|uh|look|listen|please|sorry|thanks"
                         r"|thank you|thanks a lot|thank you so much|really|man|dude|sis|bro|mom|dad|grandma|grandpa"
                         r"|sir|ma'am)\W*)+$|^\W*[A-Z][\w'-]*\W*$", re.IGNORECASE)
_SRC_THANKS = re.compile(r"ありがと|有難|有り難|感謝|サンキュ|助か|\bthank", re.IGNORECASE)  # 助かった: "Cảm ơn …" too
_VI_INTERJ = frozenset({"ơi", "à", "ạ", "nhé", "nha", "này", "nè", "ê", "ơ", "ôi", "dạ", "vâng", "ừ", "ừm", "thưa", "chà",
                        "trời", "hả", "ủa", "kìa"})
_VI_THANKS = frozenset({("cảm", "ơn"), ("cám", "ơn"), ("biết", "ơn"), ("cảm", "tạ"), ("đa", "tạ")})
_CLAUSE_GAP = re.compile(r"[,;:.!?…。！？、]")


def _ja_predicate_clause(clause: str) -> bool:
    """A Japanese clause with a predicate of its own: not a name, a role word, a topic noun (お弁当、), an
    interjection (ねえ、), thanks / relief / an apology alone (本当に助かりました)."""
    body = re.sub(r"^[\s「『(（]+", "", clause)
    bare = body.rstrip("ー〜～っ！!？?…。 　」』")
    named = _FAV_NAMED_VOC.match(bare + "、")
    return not (not bare or _JA_INTERJ.match(body) or _ROLE_RX.fullmatch(bare) or (named and named.end() >= len(bare))
                or _JA_NOUN_CLAUSE.search(bare) or _FAV_GRATEFUL.match(body, _GRATEFUL_LEAD.match(body).end()))


def _src_clause_kinds(sentence: str, ja: bool) -> list[tuple[int, int, bool]]:
    """(start, end, a predicate of its own) of each clause of a source sentence (between 、 / ,)."""
    out = []
    for m in _SRC_CLAUSE.finditer(sentence):
        c = m.group(0).strip(" 　")
        if not re.search(r"\w", c):
            continue
        pred = _ja_predicate_clause(c) if ja else len(re.findall(r"[\w']+", c)) >= 2 and not _EN_TRIVIAL.match(c)
        out.append((m.start(), m.end(), pred))
    return out


def _vi_clauses(toks: list[_Tok], idx: list[int], uses: dict[int, PronounUse]) -> list[list[int]]:
    """The clauses (token indices) of a unit of the translation that are more than a vocative, an interjection or
    a name ("Mẹ ơi", "Vâng", "Sera")."""
    out: list[list[int]] = []
    for i in idx:
        if not out or (i != idx[0] and _CLAUSE_GAP.search(toks[i].gap)):
            out.append([])
        out[-1].append(i)
    return [c for c in out if not all((i in uses and uses[i].vocative) or toks[i].low in _VI_INTERJ
                                      or (_is_upper(toks[i]) and i not in uses) for i in c)]


def _vi_has_thanks(toks: list[_Tok], clause: list[int]) -> bool:
    return any((toks[i].low, toks[i + 1].low) in _VI_THANKS for i in clause[:-1] if toks[i + 1].adjacent)


_ACT_KINDS = frozenset({"favour", "request", "offer", "volition"})
_ACT_CLASSES = frozenset({"help", "give", "ask", "please", "favour"})  # judged per clause (see the section comment)
_WITH_NEXT = re.compile(r"(?:おかげで|お陰で|おかげ|お陰|せいで)$|\bthanks\s+to\b", re.IGNORECASE)  # cause -> result
_VI_MOVED = frozenset({"vì", "bởi", "tại", "do", "nếu", "hễ", "khi", "lúc", "mặc", "dù", "giá"})


def _src_groups(sentence: str, ja: bool, frames: list[Frame], offset: int) -> list[tuple[int, int]]:
    """The spans (in `sentence`) of the clauses of a source sentence that carry an act or a predicate of their own,
    each with the uncounted clauses after it (the first from the sentence start; see the section comment)."""
    kinds = _src_clause_kinds(sentence, ja)
    acts = [any(f.kind in _ACT_KINDS and offset + s <= f.start < offset + e for f in frames) for s, e, _ in kinds]
    merged: list[int] = []
    joined: set[int] = set()
    for n in [n for n, (_, _, pred) in enumerate(kinds) if pred]:
        prev = merged[-1] if merged else None
        if prev is not None and prev not in joined and acts[prev] \
                and _WITH_NEXT.search(sentence[kinds[prev][0]:kinds[prev][1]].strip(" 　")):
            joined.add(prev)  # a cause with おかげで: one act with the one clause after it
            continue
        merged.append(n)
    starts = []
    for j, n in enumerate(merged):
        k = n  # with the clauses before it that are a noun phrase with an act (お前が作ってくれた弁当、うまかった)
        while k > (merged[j - 1] if j else -1) + 1 and not kinds[k - 1][2] and acts[k - 1]:
            k -= 1
        starts.append(0 if j == 0 else kinds[k][0])
    return [(s, e) for s, e in zip(starts, starts[1:] + [len(sentence)])] or [(0, len(sentence))]


@dataclass
class _Expect:
    """What the frames of a line expect, per source sentence when source and translation align (and, for the
    act anchors and favour slots, per clause: split_acts)."""
    frames: list[Frame]
    text: str  # the source as the frames index it (_source_text)
    sent_of: list[int]
    aligned: bool
    line: dict[str, tuple[str | None, str | None, Frame]]
    per_sent: list[dict[str, tuple[str | None, str | None, Frame]]]
    n_src: int
    ja: bool = True
    act_key: list = None  # per token of the translation: the unit its act anchors and favour slots are judged in
    act_exp: dict = None  # unit -> its expectations of the act classes
    split: frozenset = frozenset()  # the units of a sentence judged per clause
    act_base: dict = None  # a clause unit -> the expectations of its whole sentence

    def by_clause(self, i: int, cls_key: str) -> bool:
        """The act anchor at token i has an expectation of its own clause that its sentence does not share (a
        slot judged that way is only asked again: basis "cue")."""
        key = self.act_key[i]
        return key in self.split and self.act_exp[key].get(cls_key) != self.act_base[key].get(cls_key)

    def split_acts(self, toks: list[_Tok], uses: dict[int, PronounUse]) -> None:
        """Set act_key / act_exp (see the section comment): a unit is (sentence, clause) in a sentence judged per
        clause, (sentence, None) otherwise; one with more acts that cannot be lined up expects nothing."""
        self.act_key, self.act_exp, self.act_base, split = [], {}, {}, set()
        sents = _source_sentences(self.text)
        units = sorted({self.sent_of[i] if self.aligned else 0 for i in range(len(toks))})
        key_at: dict[int, object] = {}
        for u in units:
            base = self.per_sent[u] if self.aligned else self.line
            self.act_exp[(u, None)] = base
            if not self.aligned and self.n_src > 1:
                continue  # the old line-wide judgement (no favour slots there: favour_units)
            a, b = sents[u] if self.aligned else (sents[0] if sents else (0, len(self.text)))
            groups = _src_groups(self.text[a:b], self.ja, self.frames, a)
            if len(groups) <= 1:
                continue
            idx = [i for i in range(len(toks)) if (self.sent_of[i] if self.aligned else 0) == u]
            vi = _vi_clauses(toks, idx, uses)
            thanks = [bool(_SRC_THANKS.search(self.text[a + s:a + e])) for s, e in groups]
            moved = any(toks[c[0]].low in _VI_MOVED or (toks[c[0]].low in ("trước", "sau") and len(c) > 1
                                                          and toks[c[1]].low == "khi") for c in vi[1:])
            if len(vi) != len(groups) or thanks != [_vi_has_thanks(toks, c) for c in vi] or moved:
                self.act_exp[(u, None)] = {k: v for k, v in base.items() if k not in _ACT_CLASSES}
                continue
            self.act_exp[(u, None)] = {}  # a vocative or a name of its own: no act there
            for k, ((s, e), c) in enumerate(zip(groups, vi)):
                fs = [f for f in self.frames if a + s <= f.start < a + e]
                self.act_exp[(u, k)] = _frame_expectations(fs, _ben_directions(self.text[a + s:a + e], self.ja))
                self.act_base[(u, k)] = base
                split.add((u, k))
                key_at.update({i: (u, k) for i in c})
        self.act_key = [key_at.get(i, (self.sent_of[i] if self.aligned else 0, None)) for i in range(len(toks))]
        self.split = frozenset(split)

    def act_expect(self, i: int, cls_key: str) -> tuple[str | None, str | None, Frame] | None:
        """The expectation of an act class (_ACT_CLASSES) for the anchor at token i (after split_acts)."""
        return self.act_exp.get(self.act_key[i], {}).get(cls_key)

    def favour_units(self) -> dict[object, tuple[str, str, Frame]]:
        """unit -> (giver, the one it is for, frame) (after split_acts); the line as one unit only when the source
        is one sentence (else which source sentence a translated one renders is not known)."""
        if not self.aligned and self.n_src > 1:
            return {}
        return {key: e["favour"] for key, e in self.act_exp.items() if "favour" in e}


def _expect(source: str, src_lang: str, own_w: tuple[str, ...], other_w: tuple[str, ...], toks: list[_Tok]) -> _Expect:
    frames = source_frames(source, src_lang, own_w, other_w)
    text = _source_text(source, src_lang)
    ja = _is_ja(text, src_lang)
    src_sents = _source_sentences(text)
    sent_of = _sentence_index(toks)
    aligned = len(src_sents) > 1 and len(src_sents) == (sent_of[-1] + 1 if sent_of else 0)
    per_sent: list[dict] = []
    if aligned:
        starts = [s0 for s0, _ in src_sents]
        groups: list[list[Frame]] = [[] for _ in src_sents]
        for f in frames:
            k = bisect_right(starts, f.start) - 1
            if k >= 0 and f.start < src_sents[k][1]:
                groups[k].append(f)
        per_sent = [_frame_expectations(fs, _ben_directions(text[s0:s1], ja)) for fs, (s0, s1) in zip(groups, src_sents)]
    return _Expect(frames, text, sent_of, aligned, _frame_expectations(frames, _ben_directions(text, ja)), per_sent,
                   len(src_sents), ja)


def judged_slots(source: str, translation: str, own: Pair, other: Pair, src_lang: str, *,
                 spans: bool = False) -> list[tuple]:
    """(word, the role its slot needs, slot) for each pronoun of `translation` in a subject / possessor /
    object / owner slot that an explicit source pronoun names, and (slot "favour") in a slot of who does
    a benefactive act for whom ("Em làm cơm hộp cho anh à?"), right or wrong: what check_line's "pronoun"
    and "favour" issues are judged on. Empty when no such slot is judged (the sentence is restructured,
    reported, conditional, has someone outside the pair, ...): no issue there is not the same as right.
    With `spans`, (start, end, word, role, slot), spans in the NFC translation."""
    own_w, other_w = _words(own), _words(other)
    text = _nfc(translation)
    toks = _tokens(text, (*own_w, *other_w))
    uses = _analyze(toks, own_w, other_w)
    if _outside_pair(uses, own_w, other_w, False):
        return []
    ex = _expect(source, src_lang, own_w, other_w, toks)
    found = [(i, role, _SLOT_NAMES[slot])
             for i, role, slot, _ in _pronoun_slots(source, src_lang, text, toks, uses, ex.sent_of, ex.aligned, own_w,
                                                    other_w)]
    ex.split_acts(toks, uses)
    units = ex.favour_units()
    if units:
        found += [(i, role, "favour")
                  for i, role, _, _ in _favour_slots(toks, uses, ex.act_key, units, frozenset((*own_w, *other_w)),
                                                     _vi_quotes(text))]
    return [((uses[i].start, uses[i].end) if spans else ()) + (uses[i].word, role, slot) for i, role, slot in found]


def check_line(source: str, translation: str, own: Pair, other: Pair, src_lang: str, *,
               expected: str | None = None) -> list[RoleIssue]:
    """Pronouns of `translation` in a role the line does not allow: in the subject / object slot of an
    anchor whose source frame says who acts (an apology by the speaker needs the speaker's word before
    "xin lỗi"), a vocative with the speaker's own word ("Em ơi" toward an "anh"), and with `expected`
    (a role-tagged reference, see parse_ref) the reference's anchor slots and subject-verb contexts.
    Empty = nothing wrong found (which is not the same as right)."""
    own_w, other_w = _words(own), _words(other)
    written = _as_written(own, other)
    text = _nfc(translation)
    toks = _tokens(text, (*own_w, *other_w))
    uses = _analyze(toks, own_w, other_w)
    slot_uses = {i: u for i, u in uses.items() if not u.vocative}
    issues: dict[tuple[int, int], RoleIssue] = {}

    def need(i: int, role: str, why: str, basis: str, anchor: str, found: str | None = None,
             act: tuple[str, str, str, str] | None = None) -> None:
        u = uses[i]
        found = found or u.role  # a vocative's role is positional: pass the word's own role
        if found == role or found == UNKNOWN or (u.start, u.end) in issues:
            return
        kind = SELF_AS_LISTENER if role == SELF else LISTENER_AS_SELF
        pool = own_w if role == SELF else other_w
        want = _cap(written.get(pool[0], pool[0]), u.word, _sentence_start(toks, i)) if pool else None
        who = "the speaker" if role == SELF else "the listener"
        issues[(u.start, u.end)] = RoleIssue(kind, u.start, u.end, u.word, want,
                                             f"{why}: '{u.word}' must be {who}" + (f" ('{want}')" if want else ""),
                                             basis, anchor, act)

    def act_of(fe: tuple[str | None, str | None, Frame]) -> tuple[str, str, str, str] | None:
        """Who does it for whom, for the note: a benefactive or a request for the speaker / an offer for the
        listener with both people known."""
        giver, ben, f = fe
        if not (giver and ben and giver != ben and (f.kind in ("favour", "request") or f.kind == "offer")):
            return None
        return giver, ben, _act_cue(ex.text, f), "request" if f.kind == "request" else "favour"

    if _outside_pair(uses, own_w, other_w, bool(expected)):
        return []

    for i, u in uses.items():
        if u.vocative and u.word.lower() in own_w and u.word.lower() not in other_w:
            need(i, LISTENER, "a vocative addresses the listener", "vocative", "vocative", found=SELF)
    for i in _what_do_you_mean(toks, text, slot_uses):
        need(i, LISTENER, "'Ý … là sao?' asks what the listener means", "phrase", "meaning")

    # Source and translation with as many sentences: each frame bears only on its own sentence
    # ("手伝ってくれる？私も手伝うから。": who helps differs per sentence). Else the whole line.
    ex = _expect(source, src_lang, own_w, other_w, toks)
    ex.split_acts(toks, uses)
    sent_of, aligned, exp_by, frame_exp = ex.sent_of, ex.aligned, ex.per_sent, ex.line
    ref_slots: dict[str, list[tuple[str | None, str | None]]] = {}  # anchor class -> (subject, object) per use
    ref_ctx: dict[tuple[str, str], str] = {}
    if expected:
        plain, tags = parse_ref(expected)
        rtoks = _tokens(plain, (*own_w, *other_w))
        tagged = {i: role for i, t in enumerate(rtoks) for s, e, role in tags if s <= t.start < e and role != UNKNOWN}
        for a in _anchors(rtoks):
            if a.cls == "give" or a.cls in _ONE_USE:
                continue  # "cho", "đi" are everywhere and move around: too loose to align
            sj, oj = _subject(rtoks, a, tagged), _object(rtoks, a, tagged)
            ref_slots.setdefault(a.cls, []).append((tagged.get(sj) if sj is not None else None,
                                                    tagged.get(oj) if oj is not None else None))
        counts: dict[tuple[str, str], list[str]] = {}
        for i, role in tagged.items():
            for key in _context_keys(rtoks, i):
                counts.setdefault(key, []).append(role)
        ref_ctx = {key: roles[0] for key, roles in counts.items() if len(roles) == 1}

    anchors = _anchors(toks)
    will = [a for a in anchors if a.cls == "will" and _subject(toks, a, slot_uses) is not None]
    n_cls: dict[str, int] = {}
    one_use: dict[tuple[str, str, int], int] = {}
    for a in anchors:
        n_cls[a.cls] = n_cls.get(a.cls, 0) + 1
        if a.cls in _ONE_USE and (_subject(toks, a, slot_uses) is not None or _object(toks, a, slot_uses) is not None):
            ok = (a.cls, a.key, sent_of[a.first] if aligned else 0)
            one_use[ok] = one_use.get(ok, 0) + 1
    questions = _question_sentences(text, toks, sent_of)
    in_condition = _condition_flags(toks, slot_uses)
    seen_t: dict[str, int] = {}
    # "sẽ" last: a pronoun that is the subject of both "sẽ" and "giúp" ("Anh sẽ giúp") is reported under
    # the firmer anchor, which may be repaired / asked again ("sẽ" is only reported)
    for a in sorted(anchors, key=lambda x: x.cls == "will"):
        k = seen_t[a.cls] = seen_t.get(a.cls, -1) + 1
        r_subj = r_obj = None
        ref_uses = ref_slots.get(a.cls, [])
        if ref_uses and len(ref_uses) == n_cls[a.cls]:
            r_subj, r_obj = ref_uses[k]  # as many uses as the reference: aligned in order
        elif ref_uses:  # else only what every use in the reference agrees on
            subj = {s for s, _ in ref_uses if s}
            obj = {o for _, o in ref_uses if o}
            r_subj = subj.pop() if len(subj) == 1 else None
            r_obj = obj.pop() if len(obj) == 1 else None
        cls_key = f"state:{a.key}" if a.cls == "state" else a.cls
        # the act anchors by the frames of their own clause where a sentence has acts in several (split_acts)
        fe = ex.act_expect(a.first, cls_key) if a.cls in _ACT_CLASSES else \
            (exp_by[sent_of[a.first]] if aligned else frame_exp).get(cls_key)
        f_subj, f_obj, frame = fe if fe else (None, None, None)
        if a.cls in _ONE_USE and one_use.get((a.cls, a.key, sent_of[a.first] if aligned else 0), 0) > 1:
            f_subj = f_obj = None  # "Em đi đây, anh cũng đi đi": two people leave
        if a.cls == "state" and f_subj and (f_subj == LISTENER) != (sent_of[a.first] in questions):
            f_subj = None  # 大丈夫？ bears on the question ("Anh có sao không?"), お腹すいた！ on a statement
        # per sentence only (the whole line disagrees: "Why should I apologize? You apologize!"): firm
        # ("frame") when every use of this act has its subject in view, else "sentence" (reported, not
        # rewritten: a fix of one half could contradict the other)
        line_fe = frame_exp.get(cls_key)
        basis = "frame" if (not aligned or (line_fe and line_fe[:2] == (f_subj, f_obj))
                            or all(_subject(toks, b, slot_uses) is not None for b in anchors if b.cls == a.cls)) \
            else "sentence"
        if basis == "frame" and frame is not None and (frame.tentative or (a.cls in _ACT_CLASSES
                                                                           and ex.by_clause(a.first, cls_key))):
            basis = "cue"  # ごちそうさま, 任せなさい, 先生がやる, a clause's act: asked again, not rewritten
        if a.cls == "will" and sum(not aligned or sent_of[w.first] == sent_of[a.first] for w in will) != 1:
            f_subj = None  # two subjects of "sẽ" in one sentence: one may well be the listener
        if a.cls == "give":
            clause = _clause(toks, a.first)
            if any(t.low == "tự" for t in clause):
                f_subj = f_obj = None  # "tự lo cho mày": reflexive
        if in_condition[a.first]:
            f_subj = f_obj = None  # "Em sẽ mời anh nếu anh giúp em", "Anh giúp em thì …": a condition's act
        cue = (_act_cue(ex.text, frame) if frame.kind == "favour" else frame.cue) if frame else ""
        why = f"{_DESCRIBE.get((frame.kind, frame.agent), frame.kind)} ({cue})" if frame else ""
        word = " ".join(t.text for t in toks[a.first:a.last + 1])
        sj, oj = _subject(toks, a, slot_uses), _object(toks, a, slot_uses)
        if sj is not None and sj > 0 and toks[sj].adjacent:
            lead = toks[sj - 1].low
            if lead in _REPORT or lead in _CONDITIONAL:
                f_subj = f_obj = None  # "Mẹ bảo anh phải xin lỗi em", "Nếu anh giúp em": not this line's act
        elif sj is None and _other_subject(toks, a, slot_uses):
            f_obj = None  # "nhân viên sẽ giúp anh": the object is the third person's business
        if sj is not None and a.cls == "state" and any(t.low in ("đừng", "hãy", "chớ") for t in toks[sj + 1:a.first]):
            f_subj = None  # "Con đừng lo": an imperative, not the speaker's own worry
        if sj is not None and a.cls == "state" and f_subj == SELF and any(t.low == "sẽ" for t in toks[sj + 1:a.first]):
            f_subj = None  # うん、大丈夫 -> "Ừ, anh sẽ ổn thôi": reassuring the listener, not the speaker's state
        if sj is not None and a.cls == "state" and _owner(toks, sj):
            f_subj = None  # "Hộp cơm của em có ổn không?": the owner of a thing (not "Chân anh có sao không?")
        if sj is not None and a.cls != "let":
            # "giúp em cho": em is helped, not the subject of the final "cho" (from the teacher: "để cô xách
            # giúp em cho"); "Anh đưa em đi chơi đây": em is taken along, who leaves is anh
            lead = toks[sj - 1].low if sj > 0 and toks[sj].adjacent else ""
            embedded = lead in _SUBORDINATE and a.cls in ("help", "will", "give") \
                or (lead in _SUBORDINATE or lead in _CARRY) and a.cls in ("go", "leave", "home")
            if r_subj:
                need(sj, r_subj, f"the reference has the {r_subj} before '{word}'", "ref", a.cls)
            elif f_subj and not embedded:
                need(sj, f_subj, f"{why}: subject of '{word}'", basis, a.cls,
                     act=act_of(fe) if a.cls in ("give", "help") else None)
        if oj is not None and a.cls not in _SUBJECT_ONLY:
            if r_obj:
                need(oj, r_obj, f"the reference has the {r_obj} after '{word}'", "ref", a.cls)
            elif f_obj:
                need(oj, f_obj, f"{why}: object of '{word}'", basis, a.cls,
                     act=act_of(fe) if a.cls in ("give", "help") else None)
    for i, role, slot, cue in _pronoun_slots(source, src_lang, text, toks, uses, sent_of, aligned, own_w, other_w):
        span = (uses[i].start, uses[i].end)
        prev = issues.get(span)
        if prev is not None and prev.anchor == "will" and slot == "subj" \
                and prev.kind == (SELF_AS_LISTENER if role == SELF else LISTENER_AS_SELF):
            # the subject of "sẽ" that an explicit source pronoun names as well (私がやる -> "Anh sẽ làm" from
            # Sera): reported under the pronoun, the firmer basis ("sẽ" alone stays a report)
            del issues[span]
        who = "the speaker" if role == SELF else "the listener"
        what = _SLOT_NAMES[slot]
        need(i, role, f"{cue} in the source is {who}, the {what}", "pronoun", what)
    if ref_ctx:
        counts_t: dict[tuple[str, str], list[int]] = {}
        for i in slot_uses:
            for key in _context_keys(toks, i):
                counts_t.setdefault(key, []).append(i)
        for key, idx in counts_t.items():
            if len(idx) == 1 and key in ref_ctx:
                where = f"before '{key[1]}'" if key[0] == "next" else f"after '{key[1]}'"
                need(idx[0], ref_ctx[key], f"the reference has the {ref_ctx[key]} {where}", "ref", "context")
    # who does an act for whom: the giver first in the clause of "cho / giúp / thay …", the one it is for after it
    # (after the reference's checks, which then keep the spans they judge: the bench's "ref only" judge stays apart)
    units = ex.favour_units()
    if units:
        for i, role, which, f in _favour_slots(toks, uses, ex.act_key, units, frozenset((*own_w, *other_w)),
                                               _vi_quotes(text)):
            what = "who does it" if which == "giver" else "whom it is done for"
            need(i, role, f"{_DESCRIBE.get(('favour', f.agent), f.kind)} ({_act_cue(ex.text, f)}): {what}",
                 "cue" if f.tentative or ex.by_clause(i, "favour") else "frame", "favour",
                 act=act_of((f.agent, f.patient, f)))
    return sorted(issues.values(), key=lambda x: x.start)
