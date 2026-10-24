"""Tests for app.translate.pronoun_roles: who each Vietnamese pronoun refers to, source speech-act
frames and the role checks, on hand-written Vietnamese (correct and wrong) for many speaker pairs,
plus the role-tagged references of the bench scenes.

Run: python -m unittest tests.test_pronoun_roles -v
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.translate.pronoun_roles import (
    LISTENER,
    SELF,
    UNKNOWN,
    analyze,
    check_line,
    judged_slots,
    parse_ref,
    ref_disagreements,
    source_frames,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = PROJECT_ROOT / "bench" / "data" / "translate" / "scenarios"

SERA = ("em", "anh")  # younger sister -> older brother
REN = (("anh", "tao"), ("em", "mày"))  # older brother -> younger sister (tao/mày when annoyed)
YUTO = ("em", "chị")  # younger brother -> older sister
AOI = ("chị", "em")
MINATO = ("cháu", "bà")  # grandson -> grandmother
CHIYO = ("bà", "cháu")
STUDENT = ("em", "thầy")
TEACHER = ("cô", "em")
FRIEND = ("tớ", "cậu")
RUDE = ("tao", "mày")
CLERK = ("tôi", "anh")  # shopkeeper -> customer
CUSTOMER = ("em", "anh")
MOM = ("mẹ", "con")
CHILD = ("con", "mẹ")
MASTER = ("ta", "con")  # an old master -> disciple
DISCIPLE = ("con", "thầy")
KNIGHT = ("thần", "công chúa")
PAL = ("mình", "bạn")
SERA_LINE = "ごめんね。一緒に探してくれる？"


def roles(text: str, own, other) -> list[tuple[str, str, bool]]:
    return [(u.word, u.role, u.vocative) for u in analyze(text, own, other)]


def issues(source: str, translation: str, pair, lang: str = "ja", expected: str | None = None) -> list[tuple[str, str]]:
    return [(i.kind, i.found) for i in check_line(source, translation, *pair, lang, expected=expected)]


def frames(source: str, lang: str = "ja") -> list[tuple[str, str | None, str | None]]:
    """The speech-act frames of a source line (not explicit pronouns, not benefactives: favours())."""
    return [(f.kind, f.agent, f.patient) for f in source_frames(source, lang)
            if not f.kind.endswith("_ref") and f.kind != "favour"]


def favours(source: str, pair=(None, None), lang: str = "ja") -> list[tuple[str | None, str | None]]:
    """(the one who does it, the one it is for) of each benefactive frame of a source line."""
    return [(f.agent, f.patient) for f in source_frames(source, lang, *pair) if f.kind == "favour"]


def wrong(source: str, translation: str, pair, lang: str = "ja") -> list[str]:
    """The words check_line finds in the wrong role (lower case, sorted)."""
    return sorted(i.found.lower() for i in check_line(source, translation, *pair, lang))


class CaseTable:
    """subTest over (source, translation, pair, language, the words in the wrong role)."""

    def check_table(self, table) -> None:
        for source, translation, pair, lang, want in table:
            with self.subTest(source=source, translation=translation):
                self.assertEqual(wrong(source, translation, pair, lang), sorted(want))


class AnalyzeTests(unittest.TestCase):
    def test_roles_from_the_pair_and_inherent_person(self):
        self.assertEqual(roles("Anh xin lỗi nhé. Anh giúp em tìm cùng được không?", *SERA),
                         [("Anh", LISTENER, False), ("Anh", LISTENER, False), ("em", SELF, False)])
        self.assertEqual(roles("Em xin lỗi anh.", *REN), [("Em", LISTENER, False), ("anh", SELF, False)])
        # outside the pair: tôi/tao/tớ are first person, mày/bạn second, kinship words unknown
        self.assertEqual(roles("Tôi hỏi bạn, chị đi đâu? Cậu biết không?", *SERA),
                         [("Tôi", SELF, False), ("bạn", LISTENER, False), ("chị", UNKNOWN, False),
                          ("Cậu", UNKNOWN, False)])
        self.assertEqual(roles("Tao bảo mày rồi.", *REN), [("Tao", SELF, False), ("mày", LISTENER, False)])
        # both calling each other "anh": only the direction decides
        self.assertEqual(roles("Anh cứ đợi, tôi gọi anh sau.", *CLERK),
                         [("Anh", LISTENER, False), ("tôi", SELF, False), ("anh", LISTENER, False)])
        self.assertEqual(roles("Tiền bối giúp em nhé.", "em", ("anh", "tiền bối")),
                         [("Tiền bối", LISTENER, False), ("em", SELF, False)])
        self.assertEqual(roles("Anh", None, None), [("Anh", UNKNOWN, False)])

    def test_third_person_kinship_nouns_groups_and_compounds_are_not_pronoun_uses(self):
        for text in ("Anh ấy đi rồi.", "Cô ta nói dối.", "Ông ta là ai?", "Em ấy còn nhỏ.", "Anh trai đâu?",
                     "Em gái của Ren.", "Chị gái đâu?", "Hai anh em cãi nhau.", "Ông bà ngoại khỏe không?",
                     "Cô giáo dạy toán.", "Chúng em về đây.", "Các anh đi trước đi.", "Bọn tao thắng rồi.",
                     "Mọi người ơi!", "Phải tôi luyện thêm.", "Mày mò cả đêm.", "Nhíu lông mày.", "Phong thái tao nhã.",
                     "Cảm thấy cô đơn.", "Gặp anh Kenji chưa?", "Cảm ơn ông Carter.", "Gặp Tuấn Anh chưa?",
                     "Học tiếng Anh.", "Bạn bè đâu?", "Đúng là đứa cháu ngoan.", "Anh này nói gì vậy?",
                     "Bà cháu mình đi chợ.", "Đi một mình.", "Một anh lạ mặt.", "Hiệu sách Ánh Trăng."):
            with self.subTest(text=text):
                self.assertEqual(roles(text, ("em", "cháu", "tôi", "tao", "mình", "chị"),
                                       ("anh", "bà", "cô", "ông", "mày", "bạn")), [])
        self.assertEqual(roles("Con mèo của con.", "con", "mẹ"), [("con", SELF, False)])  # classifier vs pronoun
        self.assertEqual(roles("Đây, trả mày này.", *RUDE), [("mày", LISTENER, False)])  # final particle
        self.assertEqual(roles("Minato đến thăm bà đấy à.", *CHIYO), [("bà", SELF, False)])

    def test_vocatives_address_the_listener(self):
        self.assertEqual(roles("Anh ơi, em xin lỗi.", *SERA), [("Anh", LISTENER, True), ("em", SELF, False)])
        self.assertEqual(roles("Thưa cô, em quên vở.", "em", "cô"), [("cô", LISTENER, True), ("em", SELF, False)])
        self.assertEqual(roles("Tự làm đi, mày.", *RUDE), [("mày", LISTENER, True)])
        self.assertEqual(roles("Ngủ ngon nhé, em ạ!", *AOI), [("em", LISTENER, True)])
        self.assertEqual(roles("Này anh, đợi em với!", *SERA), [("anh", LISTENER, True), ("em", SELF, False)])
        self.assertEqual(roles("Tiền bối ơi!", "em", ("anh", "tiền bối")), [("Tiền bối", LISTENER, True)])
        # "Em ơi" from the sister: positionally the listener (check_line reports the word)
        self.assertEqual(roles("Em ơi!", *SERA), [("Em", LISTENER, True)])
        # an answer "Em." after a question is not a vocative
        self.assertEqual(roles("Ai làm vỡ? Em.", *SERA), [("Em", SELF, False)])


class FrameTests(unittest.TestCase):
    def test_japanese_cues(self):
        self.assertEqual(frames(SERA_LINE), [("apology", SELF, LISTENER), ("request", LISTENER, SELF)])
        self.assertEqual(frames("ごめんなさい…。"), [("apology", SELF, LISTENER)])  # not the imperative なさい
        self.assertEqual(frames("ちゃんと謝って。"), [("apology", LISTENER, SELF)])
        self.assertEqual(frames("謝れ。"), [("apology", LISTENER, SELF)])
        self.assertEqual(frames("謝らないで。"), [("apology", LISTENER, SELF)])
        self.assertEqual(frames("ごめんって言ってよ"), [("apology", LISTENER, SELF)])  # one demand, its て included
        self.assertEqual(frames("感謝してるよ。"), [("thanks", SELF, LISTENER)])  # 感謝 is not 謝
        self.assertEqual(frames("ありがとうって言え。"), [("thanks", LISTENER, SELF)])
        self.assertEqual(frames("許して。"), [("forgive", LISTENER, SELF)])
        self.assertEqual(frames("今回は許してやる。"), [("forgive", SELF, LISTENER), ("offer", SELF, LISTENER)])
        self.assertEqual(frames("取ってくれるかい？"), [("request", LISTENER, SELF)])
        self.assertEqual(frames("教えていただけませんか？"), [("request", LISTENER, SELF)])
        self.assertEqual(frames("これください。"), [("request", LISTENER, SELF)])
        self.assertEqual(frames("見て見て！"), [("request", LISTENER, None)])  # an imperative: for whom unknown
        self.assertEqual(frames("持ってあげる。"), [("offer", SELF, LISTENER)])
        self.assertEqual(frames("任せて！"), [("offer", SELF, None)])  # not an imperative request
        self.assertEqual(frames("俺が払う。"), [("volition", SELF, None)])
        self.assertEqual(frames("鍵なら拾っといたぞ。"), [("offer", SELF, None)])
        self.assertEqual(frames("僕が悪かったです。"), [("apology", SELF, LISTENER)])  # not "I will"
        # not frames: past / conditional くれる, quotative って, 〜なくて, "your fault", a question 俺が？
        for text in ("来てくれたね。", "手伝ってくれたら嬉しい。", "後で返すって。", "分からなくて。", "お前が悪い。",
                     "俺が？", "優しいね。", "そうか。"):
            with self.subTest(text=text):
                self.assertEqual(frames(text), [])
        self.assertEqual(frames("妹に優しくしてあげて。"), [("request", LISTENER, None)])  # あげて asks, not offers

    def test_reported_and_quoted_acts_are_no_frames(self):
        for text in ("母さんが、お前に謝れってさ。", "ケンジくんに謝れって言われた。", "ケンジくんが、ごめんって伝えてって。",
                     "ケンジくん、お兄ちゃんにごめんって言った？", "ケンジがさ、お前にごめんって言ってたぞ。",
                     "お兄ちゃんが『ごめん』って言えば済む話でしょ。", "お母さんが、お兄ちゃんにありがとうって言ってたよ。",
                     "ケンジくん、私に謝ってくれたよ。", "お兄ちゃんに『ありがとう』って言いたかったの。"):
            with self.subTest(text=text):
                self.assertEqual(frames(text), [])
        for text in ("Did he say sorry to you?", "Mom told me to say sorry to you.", 'All you have to do is say "sorry".',
                     "Mom told me to thank you.", "Kenji said sorry to you?"):
            with self.subTest(text=text):
                self.assertEqual(frames(text, "en"), [])

    def test_more_cues(self):
        self.assertEqual(frames("もう、ごめんってば。"), [("apology", SELF, LISTENER)])  # emphatic, not quoted
        self.assertEqual(frames("行ってきます！"), [("offer", SELF, None), ("go", SELF, None)])
        self.assertEqual(frames("何か手伝おうか？"), [("offer", SELF, None)])
        self.assertEqual(frames("お兄ちゃんも手伝う？"), [])  # asks whether the listener will
        self.assertEqual(frames("手伝って！"), [("request", LISTENER, SELF)])  # help me
        self.assertEqual(frames("なんで私が謝らなきゃいけないの？お兄ちゃんが謝ってよ！"),
                         [("apology", SELF, None), ("apology", LISTENER, SELF)])
        self.assertEqual(frames("手伝ってくれる？私も手伝うから。"), [("request", LISTENER, SELF), ("offer", SELF, None)])
        for text in ("お疲れ！", "おかえりなさい！", "おやすみなさい。"):  # greetings, not imperatives
            with self.subTest(text=text):
                self.assertEqual([f for f in frames(text) if f[0] == "request"], [])
        self.assertEqual(frames("お疲れ！"), [])
        # 私が only in a main clause ("I'll do it"), not in a condition, a reason or a relative clause
        for text in ("私が卒業したら、お兄ちゃん寂しくなるね。", "私がついてるから、大丈夫だよ。", "私がいないと、困るでしょ？",
                     "私が作ったケーキ、きっと気に入るよ。", "僕が言わなきゃ、姉ちゃんは気づかないよ。"):
            with self.subTest(text=text):
                self.assertEqual(frames(text), [])
        self.assertEqual(frames("うん、僕がやるよ。"), [("volition", SELF, None)])
        self.assertEqual(frames("Why should I apologize? You apologize!", "en"),
                         [("apology", SELF, LISTENER), ("apology", LISTENER, SELF)])
        self.assertEqual(frames("Please wait here.", "en"), [("request", LISTENER, None)])  # not "for me"

    def test_explicit_pronouns(self):
        kinds = [(f.kind, f.cue) for f in source_frames("俺のせいだ。お前は悪くない。健太君も。", "ja")
                 if f.kind.endswith("_ref")]
        self.assertEqual(kinds, [("self_ref", "俺"), ("listener_ref", "お前")])  # 健太君: a name suffix
        kinds = [(f.kind, f.cue) for f in source_frames("I’ll call you, sir.", "en") if f.kind.endswith("_ref")]
        self.assertEqual(kinds, [("self_ref", "I'll"), ("listener_ref", "you"), ("listener_ref", "sir")])

    def test_english_cues(self):
        self.assertEqual(frames("I'm so sorry, sir.", "en"), [("apology", SELF, LISTENER)])
        self.assertEqual(frames("Sorry for the trouble.", "en"), [("apology", SELF, LISTENER)])
        self.assertEqual(frames("You should apologize to her.", "en"), [("apology", LISTENER, SELF)])
        self.assertEqual(frames("Say sorry.", "en"), [("apology", LISTENER, SELF)])
        self.assertEqual(frames("Thank you, you've been very helpful.", "en"), [("thanks", SELF, LISTENER)])
        self.assertEqual(frames("Could you take a look at it?", "en"), [("request", LISTENER, SELF)])
        self.assertEqual(frames("Would you like me to reserve a copy?", "en"), [("offer", SELF, LISTENER)])
        self.assertEqual(frames("Let me check.", "en"), [("offer", SELF, None)])
        for text in ("I feel sorry for him.", "Thanks to you, we won.", "I can't do it.", "Would you like some tea?"):
            with self.subTest(text=text):
                self.assertEqual(frames(text, "en"), [])


class CheckLineTests(unittest.TestCase):
    def test_the_sera_line(self):
        self.assertEqual(issues(SERA_LINE, "Anh xin lỗi nhé. Anh giúp em tìm cùng được không?", SERA),
                         [("self_as_listener", "Anh")])
        issue = check_line(SERA_LINE, "Anh xin lỗi nhé. Anh giúp em tìm cùng được không?", *SERA, "ja")[0]
        self.assertEqual((issue.start, issue.end, issue.expected_word, issue.basis), (0, 3, "Em", "frame"))
        for ok in ("Em xin lỗi nhé. Anh giúp em tìm cùng được không?", "Xin lỗi anh nhé. Anh tìm cùng em được không?",
                   "Anh ơi, em xin lỗi. Tìm cùng em nhé?", "Em xin lỗi anh. Anh giúp em với nhé?",
                   "Xin lỗi nhé. Mình cùng tìm được không?"):
            with self.subTest(ok=ok):
                self.assertEqual(issues(SERA_LINE, ok, SERA), [])
        self.assertEqual(issues(SERA_LINE, "Xin lỗi em nhé.", SERA), [("listener_as_self", "em")])
        self.assertEqual(issues(SERA_LINE, "Em xin lỗi nhé. Em giúp anh tìm được không?", SERA),
                         [("listener_as_self", "Em"), ("self_as_listener", "anh")])

    def test_demanded_apology_is_the_listeners(self):
        self.assertEqual(issues("ちゃんと謝って。", "Anh xin lỗi em đi!", SERA), [])
        self.assertEqual(issues("ちゃんと謝って。", "Em xin lỗi anh đi!", SERA),
                         [("listener_as_self", "Em"), ("self_as_listener", "anh")])
        self.assertEqual(issues("謝ってよ。", "Anh nợ em một lời xin lỗi đấy.", SERA), [])  # a noun phrase
        self.assertEqual(issues("謝れよ。", "Mày xin lỗi tao đi!", REN), [])
        self.assertEqual(issues("謝れよ。", "Tao xin lỗi mày.", REN), [("listener_as_self", "Tao"),
                                                                     ("self_as_listener", "mày")])
        self.assertEqual(issues("謝りなさい。", "Em xin lỗi bạn ấy đi.", TEACHER), [])  # bạn ấy: third person
        self.assertEqual(issues("謝りなさい。", "Cô xin lỗi em đi.", TEACHER), [("listener_as_self", "Cô"),
                                                                         ("self_as_listener", "em")])

    def test_brother_to_sister(self):
        self.assertEqual(issues("悪いな、言いすぎた。", "Anh xin lỗi, anh nói hơi quá.", REN), [])
        self.assertEqual(issues("悪いな、言いすぎた。", "Em xin lỗi, anh nói hơi quá.", REN), [("self_as_listener", "Em")])
        self.assertEqual(issues("仕方ないな。俺が探してやる。", "Thôi được, để anh tìm cho.", REN), [])
        self.assertEqual(issues("仕方ないな。俺が探してやる。", "Thôi được, để em tìm cho.", REN), [("self_as_listener", "em")])
        self.assertEqual(issues("仕方ないな。俺が探してやる。", "Anh sẽ tìm giúp em.", REN), [])
        self.assertEqual(issues("仕方ないな。俺が探してやる。", "Em sẽ tìm giúp anh.", REN),
                         [("self_as_listener", "Em"), ("listener_as_self", "anh")])

    def test_younger_and_older_sibling(self):
        self.assertEqual(issues("姉ちゃん、ごめん！", "Chị ơi, em xin lỗi!", YUTO), [])
        self.assertEqual(issues("姉ちゃん、ごめん！", "Chị ơi, chị xin lỗi!", YUTO), [("self_as_listener", "chị")])
        self.assertEqual(issues("荷物、持ってあげる。", "Để em xách cho chị.", YUTO), [])
        self.assertEqual(issues("荷物、持ってあげる。", "Để chị xách cho em.", YUTO),
                         [("self_as_listener", "chị"), ("listener_as_self", "em")])  # cho: for the listener
        # an offer to the listener: who it is for ("cho ...") is the listener
        self.assertEqual(issues("帰りにアイス買ってあげる。", "Về nhà chị sẽ mua kem cho em.", AOI), [])
        self.assertEqual(issues("帰りにアイス買ってあげる。", "Về nhà chị sẽ mua kem cho chị.", AOI),
                         [("listener_as_self", "chị")])
        self.assertEqual(issues("帰りにアイス買ってあげる。", "Về nhà em sẽ mua kem cho chị.", AOI),
                         [("self_as_listener", "em"), ("listener_as_self", "chị")])
        # 任せて (leave it to me) next to the offer: "Để đó cho tôi" is right, "cho" is not judged
        self.assertEqual(issues("任せて！カレー、作ってあげる。", "Để đó cho tôi! Tôi sẽ nấu cà ri.", ("tôi", "chị")), [])
        self.assertEqual(issues("晩ご飯、作ってくれる？", "Em nấu cơm cho chị được không?", AOI), [])
        # who cooks for whom: "Chị" is the subject of the clause with "cho em" (benefactive slots, since round 6)
        self.assertEqual(issues("晩ご飯、作ってくれる？", "Chị nấu cơm cho em được không?", AOI),
                         [("listener_as_self", "Chị"), ("self_as_listener", "em")])
        self.assertEqual(issues("ありがとう。", "Cảm ơn em.", AOI), [])
        self.assertEqual(issues("ありがとう。", "Cảm ơn chị.", AOI), [("listener_as_self", "chị")])

    def test_grandchild_and_grandmother(self):
        self.assertEqual(issues("ごめんなさい、おばあちゃん！", "Cháu xin lỗi bà!", MINATO), [])
        self.assertEqual(issues("ごめんなさい、おばあちゃん！", "Xin lỗi bà ạ!", MINATO), [])
        self.assertEqual(issues("ごめんなさい、おばあちゃん！", "Bà xin lỗi cháu!", MINATO),
                         [("self_as_listener", "Bà"), ("listener_as_self", "cháu")])
        self.assertEqual(issues("湊、ありがとう。", "Cảm ơn cháu nhé, Minato.", CHIYO), [])
        self.assertEqual(issues("湊、ありがとう。", "Cảm ơn bà nhé.", CHIYO), [("listener_as_self", "bà")])
        self.assertEqual(issues("お皿を取ってくれるかい？", "Cháu lấy giúp bà cái đĩa được không?", CHIYO), [])
        self.assertEqual(issues("お皿を取ってくれるかい？", "Bà lấy giúp cháu cái đĩa được không?", CHIYO),
                         [("listener_as_self", "Bà"), ("self_as_listener", "cháu")])  # who fetches it for whom
        self.assertEqual(issues("じゃあ、お願いしようかね。", "Vậy bà nhờ cháu nhé.", CHIYO), [])
        self.assertEqual(issues("じゃあ、お願いしようかね。", "Vậy cháu nhờ bà nhé.", CHIYO),
                         [("self_as_listener", "cháu"), ("listener_as_self", "bà")])

    def test_conflicting_frames_switch_the_check_off(self):
        # 僕がやる (the speaker acts) + 座ってて (the listener acts): who "để" points to is not judged
        src = "片付けは僕がやるから、おばあちゃんは座ってて。"
        self.assertEqual(issues(src, "Để cháu dọn cho, bà cứ ngồi đi.", MINATO), [])
        self.assertEqual(issues(src, "Để bà dọn cho, cháu cứ ngồi đi.", MINATO), [])
        # a "sẽ" with a listener in the source ("you") or twice in the line is not judged; the explicit
        # source pronouns (You can / I'll / you) still show the swap, as the subject and the object
        found = check_line("You can wait, or I'll call you.", "Anh đợi nhé, hoặc anh sẽ gọi cho tôi.", *CLERK, "en")
        self.assertEqual([(i.found, i.basis, i.anchor) for i in found],
                         [("anh", "pronoun", "subject"), ("tôi", "pronoun", "object")])
        self.assertEqual(issues("You can wait, or I'll call you.", "Anh đợi nhé, hoặc tôi sẽ gọi cho anh.", CLERK, "en"),
                         [])
        self.assertEqual(issues("私がやる。きっと驚くよ。", "Em sẽ làm. Anh sẽ bất ngờ cho xem.", SERA), [])
        self.assertEqual(issues("私がやる。", "Anh sẽ làm.", SERA), [("self_as_listener", "Anh")])
        # the subject of "sẽ" that 私が names as well is reported under the pronoun (asked again); "sẽ" with no
        # explicit source pronoun stays the "will" anchor (only reported)
        self.assertEqual([(i.anchor, i.basis) for i in check_line("私がやる。", "Anh sẽ làm.", *SERA, "ja")],
                         [("subject", "pronoun")])
        self.assertEqual([(i.anchor, i.basis) for i in check_line("任せて！", "Anh sẽ lo hết!", *SERA, "ja")],
                         [("will", "frame")])

    def test_student_teacher_friends_and_customers(self):
        self.assertEqual(issues("先生、すみません。", "Thưa thầy, em xin lỗi.", STUDENT), [])
        self.assertEqual(issues("先生、すみません。", "Thưa thầy, thầy xin lỗi.", STUDENT), [("self_as_listener", "thầy")])
        self.assertEqual(issues("先生、すみません。", "Thưa em, em xin lỗi.", STUDENT), [("listener_as_self", "em")])
        self.assertEqual(issues("手伝ってくれてありがとう。また頼むね！", "Cảm ơn cậu đã giúp tớ. Lần sau lại nhờ cậu nhé!",
                                FRIEND), [])
        self.assertEqual(issues("手伝ってくれてありがとう。また頼むね！", "Cảm ơn tớ đã giúp cậu.", FRIEND),
                         [("listener_as_self", "tớ"), ("self_as_listener", "cậu")])
        self.assertEqual(issues("わりい、俺が悪かった。", "Xin lỗi mày, tao sai rồi.", RUDE), [])
        self.assertEqual(issues("わりい、俺が悪かった。", "Mày xin lỗi, tao sai rồi.", RUDE), [("self_as_listener", "Mày")])
        self.assertEqual(issues("I'm sorry, the display is cracked.", "Tôi xin lỗi, màn hình bị nứt rồi.", CLERK, "en"), [])
        self.assertEqual(issues("I'm sorry, the display is cracked.", "Anh xin lỗi, màn hình bị nứt rồi.", CLERK, "en"),
                         [("self_as_listener", "Anh")])
        self.assertEqual(issues("Could you write your name on this form?", "Anh ghi tên vào phiếu này giúp tôi nhé.",
                                CLERK, "en"), [])
        self.assertEqual(issues("Could you write your name on this form?", "Tôi ghi tên vào phiếu này giúp anh nhé.",
                                CLERK, "en"), [("listener_as_self", "Tôi"), ("self_as_listener", "anh")])
        self.assertEqual(issues("Let me check the connector.", "Để tôi kiểm tra đầu nối.", CLERK, "en"), [])
        self.assertEqual(issues("Let me check the connector.", "Để anh kiểm tra đầu nối.", CLERK, "en"),
                         [("self_as_listener", "anh")])
        self.assertEqual(issues("Thank you so much!", "Em cảm ơn anh nhiều lắm!", CUSTOMER, "en"), [])
        self.assertEqual(issues("Thank you so much!", "Anh cảm ơn em nhiều lắm!", CUSTOMER, "en"),
                         [("self_as_listener", "Anh"), ("listener_as_self", "em")])
        self.assertEqual(issues("Could you take a look at it?", "Anh xem giúp em được không ạ?", CUSTOMER, "en"), [])

    def test_lines_without_a_judgeable_pronoun_are_fine(self):
        for src, tr, pair in (("灯台の明かりが消えた。", "Đèn hải đăng đã tắt.", SERA),
                              ("ごめん、ケンジのせいなんだ。", "Em xin lỗi, tại anh ấy đấy.", SERA),
                              ("ごめん、私たちが悪かった。", "Bọn em xin lỗi anh.", SERA),
                              ("ごめん、お兄ちゃんのせいじゃないよ。", "Xin lỗi, không phải lỗi của anh đâu.", SERA),
                              ("ありがとう、ケンジ。", "Cảm ơn anh Kenji nhé.", SERA),
                              ("お兄ちゃん、どこ？", "Anh đâu rồi?", SERA),
                              ("ありがとうございます！", "Cảm ơn quý khách ạ!", ("em", ("anh", "quý khách"))),
                              ("", "", SERA), ("ごめんね。", "Anh xin lỗi nhé.", (None, None))):
            with self.subTest(tr=tr):
                self.assertEqual(issues(src, tr, pair), [])

    def test_each_sentence_has_its_own_act(self):
        src = "なんで私が謝らなきゃいけないの？お兄ちゃんが謝ってよ！"
        self.assertEqual(issues(src, "Sao em lại phải xin lỗi chứ? Anh mới là người phải xin lỗi chứ!", SERA), [])
        self.assertEqual(issues(src, "Sao anh lại phải xin lỗi chứ? Anh mới là người phải xin lỗi chứ!", SERA),
                         [("self_as_listener", "anh")])
        en = "Why should I apologize? You apologize!"
        self.assertEqual(issues(en, "Sao anh phải xin lỗi chứ? Em xin lỗi anh đi!", REN, "en"), [])
        found = check_line(en, "Sao em phải xin lỗi chứ? Anh mới là người phải xin lỗi chứ!", *REN, "en")
        self.assertEqual([(i.found, i.basis) for i in found], [("em", "frame"), ("Anh", "frame")])  # both in view
        # only one half in view: reported as "sentence" (not rewritten: a fix of one half could contradict)
        found = check_line(en, "Sao em phải xin lỗi chứ? Anh nợ em một lời xin lỗi!", *REN, "en")
        self.assertEqual([(i.found, i.basis) for i in found], [("em", "sentence")])
        req = "手伝ってくれる？私も手伝うから。"
        self.assertEqual(issues(req, "Anh giúp em được không? Em cũng sẽ giúp nữa mà.", SERA), [])
        self.assertEqual(issues(req, "Anh giúp em được không? Anh cũng sẽ giúp nữa mà.", SERA), [("self_as_listener", "Anh")])
        # a request in one sentence says nothing about "cho" in the next
        self.assertEqual(issues("これ持っててくれる？お兄ちゃんのために作ったの。",
                                "Anh cầm giúp em cái này nhé? Em làm cho anh đấy.", SERA), [])

    def test_what_the_reviewers_saw_flagged_on_correct_lines(self):
        for src, tr, pair, lang in (
                ("母さんが、お前に謝れってさ。", "Mẹ bảo anh phải xin lỗi em đấy.", REN, "ja"),
                ("ケンジくんが、ごめんって伝えてって。", "Kenji nhờ em gửi lời xin lỗi tới anh.", SERA, "ja"),
                ("ケンジくん、お兄ちゃんにごめんって言った？", "Kenji đã xin lỗi anh chưa?", SERA, "ja"),
                ("Did he say sorry to you?", "Hắn có xin lỗi em không?", REN, "en"),
                ("手伝ってくれたら、晩ご飯作ってあげる。", "Nếu anh giúp em thì em sẽ nấu cơm tối cho anh.", SERA, "ja"),
                ("私がいないと、お兄ちゃんは何もできないんだから。", "Không có em, anh sẽ chẳng làm được gì đâu.", SERA, "ja"),
                ("I can fix it by tomorrow. It will cost fifty dollars.",
                 "Tôi có thể sửa xong trước ngày mai. Anh sẽ mất năm mươi đô.", CLERK, "en"),
                ("Please wait here, our staff will help you.", "Xin anh chờ ở đây nhé, nhân viên sẽ giúp anh.", CLERK, "en"),
                ("Can I help you?", "Anh cần giúp gì không?", CLERK, "en"),
                ("待ってて！すぐ手伝うから！", "Chờ một chút nhé! Em sẽ giúp anh ngay thôi!", SERA, "ja"),
                ("お疲れ！手伝うよ。", "Vất vả rồi! Em giúp cho.", SERA, "ja"),
                ("ごめん、この問題は飛ばすね。", "Xin lỗi, em bỏ qua câu hỏi này nhé.", SERA, "ja"),
                # Ren asks a stranger ("chị ơi"): the pair with Sera says nothing about this line
                ("すみません、写真撮ってもらえますか？", "Chị ơi, chị chụp giúp em tấm ảnh được không ạ?", REN, "ja")):
            with self.subTest(tr=tr):
                self.assertEqual(issues(src, tr, pair, lang), [])
        # still caught: the apology, "đi đây", the offer to help, "bỏ qua cho"
        self.assertEqual(issues("怒らないでよ。ごめんってば。", "Đừng giận mà. Anh xin lỗi mà.", SERA), [("self_as_listener", "Anh")])
        self.assertEqual(issues("行ってきます！", "Anh đi đây!", SERA), [("self_as_listener", "Anh")])
        self.assertEqual(issues("何か手伝おうか？", "Có cần em giúp gì không?", REN), [("self_as_listener", "em")])
        # "nghĩ là ..." is no report of someone else's act: the one who would apologise is the speaker (7B)
        self.assertEqual(issues("俺が謝るとでも思ったか？", "Anh nghĩ là em sẽ xin lỗi à?", REN), [("self_as_listener", "em")])
        self.assertEqual(issues("俺が謝るとでも思ったか？", "Em tưởng anh sẽ xin lỗi à?", REN), [])
        self.assertEqual(issues("ごめん、許して。", "Xin lỗi, em bỏ qua cho anh nhé.", SERA),
                         [("listener_as_self", "em"), ("self_as_listener", "anh")])

    def test_what_do_you_mean_asks_the_listener(self):
        src = "意外とって何だよ。姉ちゃんこそ、いつも僕に怒ってばっかりじゃん。"
        self.assertEqual(issues(src, "Ý em là sao? Chị mới là người lúc nào cũng giận em suốt mà.", YUTO),
                         [("listener_as_self", "em")])
        self.assertEqual(issues(src, "Ý chị là sao? Chị mới là người lúc nào cũng giận em suốt mà.", YUTO), [])
        self.assertEqual(issues("つまり…", "Ý em là, anh nên nghỉ ngơi đi.", SERA), [])  # "what I mean is": the speaker

    def test_vocative_with_the_speakers_own_word(self):
        self.assertEqual(issues("お兄ちゃん、どこ？", "Em ơi, anh đâu rồi?", SERA), [("listener_as_self", "Em")])
        self.assertEqual(check_line("お兄ちゃん、どこ？", "Em ơi, anh đâu rồi?", *SERA, "ja")[0].basis, "vocative")

    def test_reference_checks(self):
        ref = "{S:Em} xin lỗi nhé. {L:Anh} giúp {S:em} tìm cùng được không?"
        self.assertEqual(parse_ref(ref), ("Em xin lỗi nhé. Anh giúp em tìm cùng được không?",
                                          [(0, 2, SELF), (16, 19, LISTENER), (25, 27, SELF)]))
        self.assertEqual(ref_disagreements(ref, *SERA), [])
        self.assertEqual(ref_disagreements("{L:Em} xin lỗi.", *SERA), ["'Em'@0: tagged listener, analysed self"])
        # an anchor the frames do not cover ("bắt được" has none) is checked against the reference's
        # subject-verb context; the source alone says nothing about it
        src, ref2 = "見て見て！カブトムシつかまえたの！", "{L:Ông} ơi, nhìn này! {S:Cháu} bắt được bọ cánh cứng rồi!"
        wrong = "Ông ơi, nhìn này! Ông đã bắt được châu chấu rồi!"
        self.assertEqual(issues(src, wrong, ("cháu", "ông")), [])
        self.assertEqual(issues(src, wrong, ("cháu", "ông"), expected=ref2), [("self_as_listener", "Ông")])
        self.assertEqual(check_line(src, wrong, "cháu", "ông", "ja", expected=ref2)[0].basis, "ref")
        self.assertEqual(issues(src, "Ông ơi, xem này! Cháu bắt được bọ cánh cứng!", ("cháu", "ông"), expected=ref2), [])
        # real model outputs: who lent whom, who broke what (adverbs such as "lỡ" are skipped)
        self.assertEqual(issues("先週貸した金、まだ返してもらってねえぞ。", "Tiền tuần trước mày cho tao vay, tao chưa lấy lại.",
                                RUDE, expected="Tiền tuần trước {S:tao} cho {L:mày} vay, {L:mày} còn chưa trả."),
                         [("self_as_listener", "mày")])
        self.assertEqual(issues("姉ちゃん、ごめん！ヘッドホン、壊しちゃった…。", "Chị ơi, xin lỗi! Chị đã làm hỏng tai nghe của chị rồi.",
                                YUTO, expected="{L:Chị} ơi, {S:em} xin lỗi! {S:Em} lỡ làm hỏng tai nghe của {L:chị} rồi…"),
                         [("self_as_listener", "Chị")])
        # not subjects: an object that is also the next verb's subject, a possessor
        self.assertEqual(issues("お皿を取ってくれるかい？", "Vậy thì cháu giúp bà lấy cái đĩa được không?", CHIYO,
                                expected="Vậy {L:cháu} lấy giúp {S:bà} cái đĩa được không?"), [])
        self.assertEqual(issues("でも、おじいちゃんに見せたかったの。", "Nhưng tôi muốn cho ông tôi xem.", ("cháu", "ông"),
                                expected="Nhưng {S:cháu} muốn cho {L:ông} xem mà."), [])
        self.assertEqual(issues("May I have your name, please?", "Xin hãy cho em biết tên của anh, được không?",
                                CUSTOMER, "en", expected="Được phục vụ {L:anh} là niềm vui của {S:em} ạ!"), [])

    def test_scenario_references_are_consistent(self):
        """Every ref of the bench scenes: the analyser sees each tagged word in its role, and the
        checker finds nothing wrong in the reference itself (it is correct Vietnamese)."""
        n = 0
        for path in sorted(SCENARIOS.glob("*.json")):
            sc = json.loads(path.read_text(encoding="utf-8"))
            chars = [c["name"] for c in sc.get("characters", [])]
            for i, line in enumerate(sc.get("lines", []), 1):
                if "ref" not in line:
                    continue
                n += 1
                listener = line.get("to") or next(c for c in chars if c != line["speaker"])
                exp = sc["pronouns"][f"{line['speaker']}>{listener}"]
                plain, _ = parse_ref(line["ref"])
                with self.subTest(scene=sc["name"], line=i):
                    self.assertIn(sc.get("split"), ("tune", "test", "probe"))
                    self.assertEqual(ref_disagreements(line["ref"], exp["self"], exp["other"]), [])
                    self.assertEqual(check_line(line["text"], plain, exp["self"], exp["other"], sc["lang"],
                                                expected=line["ref"]), [])
        self.assertGreaterEqual(n, 80)


class ListenerStateTests(CaseTable, unittest.TestCase):
    """Questions about the listener (大丈夫？ -> the listener "có sao không") and plain statements of the
    speaker's own state (お腹すいた！ -> the speaker "đói")."""

    def test_frames(self):
        for text in ("大丈夫？", "大丈夫か？", "怪我はないかい？", "お怪我はございませんか", "元気だった？", "疲れてない？",
                     "寒くない？", "お腹すいてない？", "どうしたの？", "何してるの？", "お兄ちゃん、大丈夫？", "足、大丈夫？"):
            with self.subTest(text=text):
                self.assertEqual(frames(text), [("state", LISTENER, None)])
        for text in ("Are you okay?", "Hey, you okay?", "Are you hurt?", "Did you eat?", "What are you doing?",
                     "What's wrong?"):
            with self.subTest(text=text):
                self.assertEqual(frames(text, "en"), [("state", LISTENER, None)])
        for text in ("お腹すいた！", "怒ってないよ。", "本当にうれしいよ。", "疲れた〜", "心配したんだよ。"):
            with self.subTest(text=text):
                self.assertEqual(frames(text), [("state", SELF, None)])
        # not: a statement (大丈夫だよ may reassure the listener: "Anh sẽ ổn thôi"), wondering (かな), a third
        # person's state, a sentence with the speaker's own pronoun, a quoted question, shared or guessed
        # feelings (うれしいね, 疲れたでしょ asks about the listener), a request (心配しないで)
        for text in ("大丈夫だよ。", "大丈夫かな…", "ケンジは大丈夫？", "私の顔、大丈夫？", "「大丈夫？」って聞いたのに。",
                     "うれしいね。", "心配しないで。", "元気出して。", "What's wrong with this machine?"):
            with self.subTest(text=text):
                self.assertNotIn(("state", SELF, None), frames(text, "en" if text.isascii() else "ja"))
                self.assertNotIn(("state", LISTENER, None), frames(text, "en" if text.isascii() else "ja"))
        self.assertEqual(frames("疲れたでしょ？"), [("state", LISTENER, None)])
        self.assertEqual(frames("怒ってない？"), [("state", LISTENER, None)])

    def test_the_listener_is_asked_about(self):
        self.check_table([
            ("大丈夫？", "Anh có sao không?", SERA, "ja", []),
            ("大丈夫？", "Em có sao không?", SERA, "ja", ["em"]),
            ("お兄ちゃん、大丈夫？怪我してない？", "Anh ơi, em có sao không? Em có bị thương không?", SERA, "ja", ["em", "em"]),
            ("お兄ちゃん、大丈夫？怪我してない？", "Anh ơi, anh có sao không? Anh có bị thương không?", SERA, "ja", []),
            ("大丈夫か？", "Em không sao chứ?", REN, "ja", []),
            ("大丈夫か？", "Anh không sao chứ?", REN, "ja", ["anh"]),
            ("怪我はないかい？", "Cháu không sao chứ?", CHIYO, "ja", []),
            ("怪我はないかい？", "Bà không sao chứ?", CHIYO, "ja", ["bà"]),
            ("姫様、お怪我はございませんか？", "Công chúa có bị thương không ạ?", KNIGHT, "ja", []),
            ("姫様、お怪我はございませんか？", "Công chúa, thần có bị thương không ạ?", KNIGHT, "ja", ["thần"]),
            ("元気だった？", "Dạo này em có khỏe không?", AOI, "ja", []),
            ("元気だった？", "Dạo này chị có khỏe không?", AOI, "ja", ["chị"]),
            ("疲れてない？", "Con có mệt không?", MOM, "ja", []),
            ("疲れてない？", "Mẹ có mệt không?", MOM, "ja", ["mẹ"]),
            ("寒くない？", "Em có lạnh không?", YUTO, "ja", ["em"]),
            ("お腹すいてない？", "Bà có đói không?", CHIYO, "ja", ["bà"]),
            ("お腹すいたでしょ？", "Chắc con đói rồi nhỉ?", MOM, "ja", []),
            ("どうしたの？", "Anh sao vậy?", SERA, "ja", []),
            ("どうしたの？", "Em sao vậy?", SERA, "ja", ["em"]),
            ("どうしたの？", "Có chuyện gì vậy?", SERA, "ja", []),
            ("何してるの？", "Em đang làm gì vậy?", AOI, "ja", []),
            ("何してるの？", "Chị đang làm gì vậy?", AOI, "ja", ["chị"]),
            ("ご飯、もう食べた？", "Mẹ ăn cơm chưa?", MOM, "ja", ["mẹ"]),
            ("Are you okay?", "Anh có sao không?", CLERK, "en", []),
            ("Are you okay?", "Tôi có sao không?", CLERK, "en", ["tôi"]),
            ("Are you hurt?", "Tớ có bị thương không?", FRIEND, "en", ["tớ"]),
            ("Did you eat?", "Mẹ ăn chưa?", MOM, "en", ["mẹ"]),
            ("What are you doing?", "Mày đang làm gì đấy?", RUDE, "en", []),
            ("What are you doing?", "Tao đang làm gì đấy?", RUDE, "en", ["tao"]),
            ("Hey, you okay? That was a nasty hit.", "Ê, mày ổn không đấy? Cú vừa rồi đau phết đấy.", RUDE, "en", []),
        ])

    def test_near_misses(self):
        self.check_table([
            ("大丈夫だよ。", "Em không sao.", SERA, "ja", []),  # the speaker's answer about herself
            ("大丈夫だよ。", "Anh sẽ ổn thôi.", SERA, "ja", []),  # or reassurance
            ("私がついてるから、大丈夫だよ。", "Có em ở đây rồi, anh sẽ ổn thôi.", SERA, "ja", []),
            ("大丈夫かな…", "Không biết em có ổn không…", SERA, "ja", []),
            ("ケンジは大丈夫？", "Anh ấy có sao không?", SERA, "ja", []),
            ("私の顔、大丈夫？", "Mặt em có sao không?", SERA, "ja", []),
            ("大丈夫？私は平気だけど。", "Anh có sao không? Em thì không sao.", SERA, "ja", []),
            ("「大丈夫？」って聞いたのに。", "Em đã hỏi anh có sao không mà.", SERA, "ja", []),
            ("怪我してない？", "Anh không bị thương chứ? Em lo lắm.", SERA, "ja", []),
            ("寒くない？", "Trời lạnh không anh?", SERA, "ja", []),
            ("大丈夫？", "Có sao không anh?", SERA, "ja", []),
            ("怒ってないよ。だから心配しないで。", "Mẹ không giận đâu, con đừng lo.", MOM, "ja", []),
            ("心配しないで。", "Con đừng lo.", MOM, "ja", []),
            ("うれしいね。", "Vui quá nhỉ.", TEACHER, "ja", []),
            ("あの子、大丈夫？", "Em có sao không?", REN, "ja", []),  # a third person
            ("あいつ、何してるんだ？", "Anh đang làm gì vậy?", SERA, "ja", []),
            ("おばあちゃんは大丈夫？", "Bà có sao không?", MINATO, "ja", []),
            ("疲れたでしょう。少し休んで。", "Chắc em mệt rồi. Nghỉ một chút đi.", SERA, "ja", []),  # a guess, no question
            ("大丈夫？", "Anh có sao không? Em lo quá.", SERA, "ja", []),
            ("Are you hungry? I'm starving.", "Anh có đói không? Em đói lắm rồi.", SERA, "en", []),
        ])
        self.check_table([  # still caught
            ("お母さん、大丈夫？", "Con có sao không?", CHILD, "ja", ["con"]),
            ("大丈夫？痛くない？", "Bà có sao không? Có đau không?", CHIYO, "ja", ["bà"]),
            ("どうしたの？泣いてるの？", "Mẹ sao thế? Mẹ khóc à?", MOM, "ja", ["mẹ", "mẹ"]),
            ("Are you hungry? I'm starving.", "Em có đói không? Anh đói lắm rồi.", SERA, "en", ["anh", "em"]),
            ("How are you?", "Em có khỏe không?", SERA, "en", ["em"]),
        ])

    def test_the_speakers_own_state(self):
        self.check_table([
            ("お腹すいた！", "Con đói quá!", CHILD, "ja", []),
            ("お腹すいた！", "Mẹ đói quá!", CHILD, "ja", ["mẹ"]),
            ("ただいま！お母さん、お腹すいた！", "Con về rồi đây! Mẹ ơi, con đói quá!", CHILD, "ja", []),
            ("怒ってないよ。ただ心配だったの", "Mẹ không giận đâu. Chỉ là mẹ lo thôi.", MOM, "ja", []),
            ("怒ってないよ。ただ心配だったの", "Con không giận đâu. Chỉ là con lo thôi.", MOM, "ja", ["con", "con"]),
            ("怒ってない？", "Con không giận mẹ chứ?", MOM, "ja", []),
            ("心配したんだよ。", "Em lo cho chị lắm đấy.", AOI, "ja", ["em"]),
            ("よく頑張ったね。本当にうれしいよ", "Em đã cố gắng rất nhiều. Cô thật sự rất vui.", TEACHER, "ja", []),
            ("よく頑張ったね。本当にうれしいよ", "Em đã cố gắng rất nhiều. Em thật sự rất vui.", TEACHER, "ja", ["em"]),
            ("疲れた〜", "Mệt quá đi mất.", SERA, "ja", []),
            ("疲れた〜", "Anh mệt quá.", SERA, "ja", ["anh"]),
        ])

    def test_review_round_near_misses(self):
        """Right lines the reviewers saw asked again: a question quoted with a particle before the closing
        bracket, the owner of a thing (not of a body part) before a state word, the speaker's own state in
        English, someone outside the pair, a reassurance after うん."""
        self.check_table([
            ("お兄ちゃんが『大丈夫か？』って聞いてくれたの。", "Anh đã hỏi “em có sao không?” mà.", SERA, "ja", []),
            ("お兄ちゃん、昨日『何してるの？』って聞いたよね。", "Hôm qua anh hỏi em đang làm gì, đúng không?", SERA, "ja", []),
            ("お兄ちゃんが『怪我はないかい？』って聞いてきた。", "Anh hỏi em có bị thương không.", SERA, "ja", []),
            ("大丈夫か？って聞いたのに。", "Anh đã hỏi em có sao không mà.", REN, "ja", []),
            ("お弁当、大丈夫だった？", "Hộp cơm của em có ổn không?", SERA, "ja", []),
            ("この服、大丈夫？", "Bộ đồ này của em ổn không?", SERA, "ja", []),
            ("スマホ、どうしたの？", "Điện thoại của em bị sao vậy?", SERA, "ja", []),
            ("Are you okay with my cooking?", "Món ăn của em có ổn không?", SERA, "en", []),
            ("What's wrong with me today?", "Hôm nay em bị làm sao vậy nhỉ?", SERA, "en", []),
            ("What's wrong with my phone?", "Máy em bị sao vậy?", SERA, "en", []),
            ("Are you okay with me coming?", "Em đi cùng có được không?", SERA, "en", []),
            ("大丈夫？お姉ちゃんが絆創膏貼ってあげるね。", "Em có sao không? Để chị dán băng cá nhân cho em nhé.", SERA, "ja",
             []),  # Sera talks to a child ("chị"): not to Ren
            ("うん、大丈夫だよ。お兄ちゃんならできる。", "Ừ, anh sẽ ổn thôi. Anh làm được mà.", SERA, "ja", []),
            ("寒いね。", "Lạnh nhỉ.", SERA, "ja", []),
            ("怖い…", "Anh đáng sợ quá…", SERA, "ja", []),
            ("あの子、寝てる？", "Bé ngủ chưa?", MOM, "ja", []),
            ("怒ってないよ。ただ心配だったの", "Con không giận đâu. Chỉ là con lo thôi.", MOM, "ja", ["con", "con"]),
        ])
        self.check_table([  # still caught, and what this round adds
            ("足、大丈夫？", "Chân em có sao không?", SERA, "ja", ["em"]),
            ("怪我、大丈夫？", "Vết thương của em có sao không?", SERA, "ja", ["em"]),
            ("What's wrong with you?", "Em bị sao vậy?", SERA, "en", ["em"]),
            ("大丈夫？", "Có sao không em?", SERA, "ja", ["em"]),  # an address after the final question word
            ("ご飯食べた？", "Ăn cơm chưa mẹ?", MOM, "ja", ["mẹ"]),
            ("うん、大丈夫。心配しないで。", "Ừ, anh không sao. Em đừng lo.", SERA, "ja", ["anh"]),
            ("はい、大丈夫です。", "Dạ, cô không sao ạ.", ("em", "cô"), "ja", ["cô"]),
            ("寒い…", "Anh lạnh quá…", SERA, "ja", ["anh"]),
            ("心配したんだからね！", "Anh lo cho em lắm đấy!", SERA, "ja", ["anh"]),
            ("どこか痛いの？", "Cô đau ở đâu à?", TEACHER, "ja", ["cô"]),
            ("お前、熱あるんじゃないか？", "Anh bị sốt rồi phải không?", REN, "ja", ["anh"]),
            ("ちゃんと寝てる？", "Bà có ngủ đủ giấc không?", CHIYO, "ja", ["bà"]),
            ("Did you sleep at all last night?", "Tối qua em có ngủ được chút nào không?", SERA, "en", ["em"]),
            ("「大丈夫？」", "“Em có sao không?”", SERA, "ja", ["em"]),  # brackets around the whole line
            ("セラ：大丈夫？", "Em có sao không?", SERA, "ja", ["em"]),  # a speaker label
        ])
        # a kinship pronoun outside the pair ("cậu") stops the source-only checks, not a reference's (the 7B)
        self.assertEqual(issues("母さんが、お前に謝れってさ。", "Mẹ bảo em phải xin lỗi cậu đấy.", REN, "ja"), [])
        self.assertEqual(issues("母さんが、お前に謝れってさ。", "Mẹ bảo em phải xin lỗi cậu đấy.", REN, "ja",
                                expected="Mẹ bảo {S:anh} phải xin lỗi {L:em} đấy."), [("self_as_listener", "em")])

    def test_real_outputs(self):
        # the 1.8B, roles_grandma_ja: Chiyo asks Minato whether he is hurt
        self.assertEqual(wrong("怪我はないかい？お皿なんかより、湊のほうが大事だよ。",
                               "Bà không sao chứ? So với đĩa, việc của Minato quan trọng hơn nhiều.", CHIYO), ["bà"])
        # the 1.8B: the teacher's 先生、うれしかったよ read as a vocative (both words wrong)
        self.assertEqual(wrong("ええ、読んだわよ。とてもよく書けていたわ。先生、うれしかったよ。",
                               "Ồ, cô đã đọc rồi. Bài viết của em rất hay. Cô ơi, em rất vui.", TEACHER), ["cô", "em"])


class GreetingTests(CaseTable, unittest.TestCase):
    """ただいま / 行ってきます / いただきます / お先に失礼します are the speaker's, おかえり / 行ってらっしゃい the
    listener's; おやすみ wishes the listener good night."""

    def test_frames(self):
        self.assertEqual(frames("ただいま！"), [("home", SELF, None)])
        self.assertEqual(frames("師匠、ただいま戻りました。"), [("home", SELF, None)])
        self.assertEqual(frames("おかえりなさい。"), [("home", LISTENER, None)])
        self.assertEqual(frames("行ってらっしゃい。"), [("go", LISTENER, None)])
        self.assertEqual(frames("お先に失礼します。"), [("go", SELF, None), ("apology", SELF, LISTENER)])
        self.assertEqual(frames("いただきます！"), [("eat", SELF, None)])
        self.assertEqual(frames("ごちそうさまでした。"), [("thanks", SELF, LISTENER)])
        self.assertEqual(frames("おやすみ。"), [("goodnight", LISTENER, None)])
        self.assertEqual(frames("I'm home!", "en"), [("home", SELF, None)])
        self.assertEqual(frames("Welcome home.", "en"), [("home", LISTENER, None)])
        # quoted or reported, "right away" (ただいま参ります), someone else leaving, "a day off"
        for text in ("お母さんが「ただいま」って言ってた。", "お母さんがただいまって。", "ただいま参ります。", "お兄ちゃんが行ってくるって。",
                     "今日はお休みだよ。"):
            with self.subTest(text=text):
                self.assertEqual([f for f in frames(text) if f[0] in ("home", "go", "goodnight")], [])

    def test_greetings(self):
        self.check_table([
            ("ただいま！", "Con về rồi đây!", CHILD, "ja", []),
            ("ただいま！", "Mẹ về rồi đây!", CHILD, "ja", ["mẹ"]),
            ("ただいま！お母さん、お腹すいた！", "Mẹ về rồi đây! Mẹ ơi, con đói quá!", CHILD, "ja", ["mẹ"]),
            ("師匠、ただいま戻りました。", "Thầy ơi, con về rồi ạ.", DISCIPLE, "ja", []),
            ("師匠、ただいま戻りました。", "Thầy ơi, thầy về rồi đây ạ.", DISCIPLE, "ja", ["thầy"]),
            ("ただいま、おばあちゃん。お土産買ってきたよ", "Cháu về rồi đây bà ơi. Cháu có mua quà cho bà nè.", MINATO, "ja", []),
            ("おかえり。", "Cháu về rồi à.", CHIYO, "ja", []),  # the listener came home
            ("おかえり。", "Bà về rồi à.", CHIYO, "ja", ["bà"]),
            ("おかえり！", "Mừng cháu về nhà!", CHIYO, "ja", []),
            ("おかえり！", "Mừng bà về nhà!", CHIYO, "ja", ["bà"]),
            ("おかえりなさい。", "Anh về rồi à.", SERA, "ja", []),
            ("おかえり、遅かったね。", "Em về rồi à, sao muộn thế?", SERA, "ja", ["em"]),
            ("おばあちゃん、学校行ってきます！", "Bà ơi, cháu đi học đây ạ!", MINATO, "ja", []),
            ("おばあちゃん、学校行ってきます！", "Bà ơi, bà đi học đây ạ!", MINATO, "ja", ["bà"]),  # words between đi / đây
            ("行ってくるね。", "Mẹ đi làm đây.", MOM, "ja", []),  # the mother leaves
            ("行ってくるね。", "Con đi làm đây.", MOM, "ja", ["con"]),
            ("じゃあ、行ってくるね。", "Vậy em đi nhé.", REN, "ja", ["em"]),
            ("行ってらっしゃい、気をつけてね。", "Anh đi cẩn thận nhé.", SERA, "ja", []),
            ("行ってらっしゃい、気をつけてね。", "Em đi cẩn thận nhé.", SERA, "ja", ["em"]),
            ("いただきます！", "Con mời mẹ ăn cơm!", CHILD, "ja", []),
            ("いただきます！", "Mẹ mời con ăn cơm!", CHILD, "ja", ["con", "mẹ"]),
            ("ごちそうさまでした。", "Cảm ơn anh vì bữa ăn.", CUSTOMER, "ja", []),
            ("ごちそうさまでした。", "Cảm ơn em vì bữa ăn.", CUSTOMER, "ja", ["em"]),
            ("お先に失礼します。", "Em xin phép về trước ạ.", CUSTOMER, "ja", []),
            ("お先に失礼します。", "Anh xin phép về trước ạ.", CUSTOMER, "ja", ["anh"]),
            ("おやすみ。", "Chúc anh ngủ ngon.", SERA, "ja", []),
            ("おやすみ。", "Chúc em ngủ ngon.", SERA, "ja", ["em"]),
            ("おやすみ。", "Em ngủ ngon nhé.", SERA, "ja", ["em"]),
            ("おやすみ。", "Em đi ngủ đây.", SERA, "ja", []),
            ("おやすみ。", "Anh đi ngủ đây.", SERA, "ja", ["anh"]),
            ("おやすみ。", "Ngủ ngon nhé, em.", REN, "ja", []),
            ("I'm home!", "Mẹ về rồi đây!", CHILD, "en", ["mẹ"]),
            ("I'm home. Sera, did you finish your homework?", "Anh về rồi đây. Sera, em làm xong bài tập chưa?", REN,
             "en", []),
            ("Welcome home.", "Mẹ về rồi à.", MOM, "en", ["mẹ"]),
        ])

    def test_near_misses(self):
        self.check_table([
            ("お母さんが「ただいま」って言ってた。", "Mẹ bảo mẹ về rồi đây.", CHILD, "ja", []),  # quoted
            ("ただいま参ります。", "Tôi đến ngay đây ạ.", CLERK, "ja", []),
            ("お兄ちゃんが行ってくるって。", "Anh bảo anh đi đây.", SERA, "ja", []),
            ("行ってきます。お兄ちゃんも早く行きなよ。", "Em đi đây. Anh cũng đi nhanh lên nhé.", SERA, "ja", []),
            ("行ってきます！", "Em đi đây, anh cũng đi đi.", SERA, "ja", []),  # two people leave: not judged
            ("行ってきます！", "Con đi đây, mẹ đi làm vui vẻ nhé.", CHILD, "ja", []),
            ("おかえり。", "Anh về rồi à? Em đợi mãi.", SERA, "ja", []),
            ("いただきます。", "Mời cả nhà ăn cơm.", CHILD, "ja", []),
            ("おやすみ。", "Mẹ đi ngủ đây, con cũng ngủ sớm đi nhé.", MOM, "ja", []),
            ("今日はお休みだよ。", "Hôm nay con được nghỉ.", MOM, "ja", []),
            ("ただいま。お母さんは？", "Con về rồi. Mẹ đâu ạ?", ("con", "bố"), "ja", []),
            ("Thank you, Master. It's good to be home.", "Cảm ơn thầy. Được về nhà thật tốt.", DISCIPLE, "en", []),
            ("「行ってきます」も言わずに出ていった。", "Em đi mà không nói gì.", SERA, "ja", []),  # quoted
            ("お兄ちゃん、ただいまって言った？", "Anh đã nói 'em về rồi' chưa?", SERA, "ja", []),
            ("ただいまの一言もないの？", "Về nhà mà không nói một câu à?", SERA, "ja", []),
            ("行ってきます。", "Thưa mẹ con đi học ạ.", CHILD, "ja", []),
            ("おやすみなさい、お母さん。", "Con chúc mẹ ngủ ngon.", CHILD, "ja", []),
        ])
        self.check_table([  # still caught
            ("お母さん、おかえり！", "Con về rồi à!", CHILD, "ja", ["con"]),
            ("おやすみなさい、お母さん。", "Mẹ đi ngủ đây.", CHILD, "ja", ["mẹ"]),
            ("I'm off to school!", "Mẹ đi học đây!", CHILD, "en", ["mẹ"]),
        ])

    def test_review_round(self):
        # 行ってくる？ asks whether the LISTENER goes; who is taken along ("đưa em đi") is not who leaves; a
        # quoted 行ってくる is someone's words
        self.check_table([
            ("コンビニ行ってくる？", "Anh đi cửa hàng tiện lợi à?", SERA, "ja", []),
            ("行ってくる？", "Anh đi à?", SERA, "ja", []),
            ("お兄ちゃん、コンビニ行ってくる？アイス買ってきて", "Anh ơi, anh đi cửa hàng tiện lợi à? Mua kem cho em nhé", SERA,
             "ja", []),
            ("行ってきます", "Anh đưa em đi chơi đây", REN, "ja", []),
            ("お兄ちゃん、『行ってくる』って出かけたまま帰ってこない。", "Anh bảo “anh đi đây” rồi đi mãi chưa về.", SERA, "ja", []),
            ("行ってきます。", "Thật ra em đi đây.", SERA, "ja", []),
            ("ちょっとコンビニ行ってくる。", "Anh ra cửa hàng tiện lợi một chút.", REN, "ja", []),
        ])
        self.check_table([
            ("ちょっとコンビニ行ってくる。", "Em ra cửa hàng tiện lợi một chút.", REN, "ja", ["em"]),  # "ra": goes out
            ("行ってくるね", "Anh đi đây", SERA, "ja", ["anh"]),
        ])
        self.assertEqual([f for f in frames("行ってくる？") if f[0] == "go"], [])
        self.assertEqual([f for f in frames("I'm off?", "en") if f[0] == "go"], [])


class SourcePronounTests(CaseTable, unittest.TestCase):
    """An explicit source pronoun with a clear slot names who the subject / possessor / object of its
    sentence is ("I'll protect you", お前は私の自慢の弟子じゃ, 私のプリン)."""

    def test_slots(self):
        self.check_table([
            ("Don't worry, I'll protect you.", "Đừng lo, anh sẽ bảo vệ em.", REN, "en", []),
            ("Don't worry, I'll protect you.", "Đừng lo, em sẽ bảo vệ anh.", REN, "en", ["anh", "em"]),
            ("I'll wait for you at the station.", "Cậu sẽ đợi tớ ở nhà ga nhé.", FRIEND, "en", ["cậu", "tớ"]),
            ("You don't have to bow to me, Garron.", "Ngươi không cần phải cúi đầu trước ta đâu, Garron.",
             ("ta", "ngươi"), "en", []),
            ("You don't have to bow to me, Garron.", "Ta không cần phải cúi đầu trước ngươi đâu, Garron.",
             ("ta", "ngươi"), "en", ["ngươi", "ta"]),
            ("You forgot my birthday again, didn't you?", "Anh lại quên sinh nhật của em rồi, đúng không?", SERA, "en",
             []),
            ("You forgot my birthday again, didn't you?", "Anh lại quên sinh nhật của anh nữa rồi, đúng không?", SERA,
             "en", ["anh"]),
            ("Did you lend my umbrella to Tom? I told you I need it tomorrow.",
             "Con cho Tom mượn ô của con à? Mẹ đã bảo con là mẹ cần nó vào ngày mai mà.", MOM, "en", ["con"]),
            ("I'm proud of you.", "Cháu tự hào về bà.", CHIYO, "en", ["bà", "cháu"]),
            ("Don't worry about me.", "Đừng lo cho anh.", SERA, "en", ["anh"]),
            ("Sorry to keep you waiting.", "Xin lỗi vì đã để tôi phải đợi.", CLERK, "en", ["tôi"]),
            ("We'll come back tomorrow.", "Mai em sẽ quay lại.", CUSTOMER, "en", []),
            # a "we" with no "you" apart from it may include the listener ("We'll be late!" -> "Con sẽ muộn
            # mất"): not judged; with "you" it is the speaker's side
            ("We'll come back tomorrow.", "Mai anh sẽ quay lại.", CUSTOMER, "en", []),
            ("We'll pay you back.", "Anh sẽ trả lại em.", CUSTOMER, "en", ["anh", "em"]),
            ("We'll come back tomorrow.", "Mai bọn em sẽ quay lại.", CUSTOMER, "en", []),
            ("お前はわしの自慢の弟子じゃ", "Con là đệ tử đáng tự hào của ta.", MASTER, "ja", []),
            ("泣くな、トビ。お前はわしの自慢の弟子じゃ", "Đừng khóc, Tobi. Ta là đệ tử đáng tự hào của con.", MASTER, "ja",
             ["con", "ta"]),
            ("お前を倒して、村を取り戻す！", "Ta sẽ đánh bại ngươi và giành lại ngôi làng!", ("ta", "ngươi"), "ja", []),
            ("お前を倒して、村を取り戻す！", "Ngươi sẽ đánh bại ta và giành lại ngôi làng!", ("ta", "ngươi"), "ja", ["ta"]),
            ("お兄ちゃん、私のプリン食べたでしょ！", "Anh ơi, anh ăn bánh pudding của em rồi phải không!", SERA, "ja", []),
            ("お兄ちゃん、私のプリン食べたでしょ！", "Anh ơi, em đã ăn bánh pudding của anh phải không!", SERA, "ja", ["anh"]),
            ("ごめん、お兄ちゃんのプリン食べちゃった", "Xin lỗi, anh đã ăn mất bánh pudding của em rồi.", SERA, "ja", ["em"]),
            ("お前が壊したんだろ、俺のゲーム機", "Em làm hỏng máy chơi game của em rồi chứ gì.", REN, "ja", ["em"]),
            ("お前こそ、俺の言うことちゃんと聞けよ", "Chính em mới phải nghe lời anh đấy.", REN, "ja", []),
            ("心配すんな、俺が何とかする", "Đừng lo, em sẽ lo liệu mọi chuyện.", REN, "ja", ["em"]),  # 何とか: no condition
            ("お前のせいだ！", "Tại tao đấy!", RUDE, "ja", ["tao"]),
            ("お前のこと、信じてる。", "Em tin anh.", REN, "ja", ["anh"]),
            ("あなたは優しいね。", "Em tốt bụng thật.", SERA, "ja", ["em"]),
            ("僕も行く！", "Chị cũng đi!", YUTO, "ja", ["chị"]),
            ("お兄ちゃんが悪いんだよ！", "Em sai rồi!", SERA, "ja", ["em"]),
            ("先生はね、嬉しいのよ。", "Em vui lắm.", TEACHER, "ja", ["em"]),
            ("お母さんが作ってあげる。", "Con làm cho mẹ nhé.", MOM, "ja", ["con", "mẹ"]),
        ])

    def test_role_words_of_the_speaker(self):
        # the teacher, the mother, the older sister call themselves by their role (the reviewers' false alarm)
        self.assertEqual(wrong("重いでしょ、先生が持ってあげる", "Nặng lắm đúng không, để cô xách giúp em cho.", TEACHER), [])
        # the swapped line: who is helped is the student ("để" after a comma is not judged)
        self.assertEqual(wrong("重いでしょ、先生が持ってあげる", "Nặng lắm đúng không, để em xách giúp cô cho.", TEACHER), ["cô"])
        # 先生が like 私が: "I'll do it" from the teacher, not from a student (who calls her 先生)
        self.assertEqual(frames_with_pair("先生がやるよ。", TEACHER), [("volition", SELF, None)])
        self.assertEqual(frames_with_pair("先生がやるよ。", STUDENT), [])
        self.assertEqual(wrong("先生がやるよ。", "Cô sẽ làm.", TEACHER), [])
        self.assertEqual(wrong("先生がやるよ。", "Em sẽ làm.", TEACHER), ["em"])
        self.assertEqual(wrong("お姉ちゃんがやるから、見てて。", "Để chị làm, em xem nhé.", AOI), [])
        self.assertEqual(wrong("お姉ちゃんに任せなさい。", "Cứ để chị lo.", AOI), [])
        self.assertEqual(wrong("お姉ちゃんに任せなさい。", "Cứ để em lo.", AOI), ["em"])
        self.assertEqual(wrong("お母さんが作ってあげる。", "Mẹ làm cho con nhé.", MOM), [])
        # someone else's: a name or の before, a plural
        for text in ("山田先生が持ってあげる。", "ケンジのお母さんが作ってあげる。", "先生方が来る。"):
            with self.subTest(text=text):
                self.assertNotIn(("volition", SELF, None), frames_with_pair(text, TEACHER if "先生" in text else MOM))

    def test_near_misses(self):
        self.check_table([
            ("I'll call you when you get home.", "Khi nào anh về đến nhà thì em sẽ gọi cho anh.", SERA, "en", []),
            ("Can I help you?", "Anh cần giúp gì không?", CLERK, "en", []),
            ("Let me carry your bag.", "Để em xách túi cho anh.", SERA, "en", []),
            ("Could you write your name on this form?", "Anh ghi tên vào phiếu này giúp tôi nhé.", CLERK, "en", []),
            ("Your father would be proud.", "Bố anh sẽ tự hào lắm.", SERA, "en", []),
            ("Thanks for waiting for me.", "Cảm ơn bạn đã đợi mình.", PAL, "en", []),
            ("If you need me, call me.", "Nếu anh cần tôi thì cứ gọi.", CLERK, "en", []),
            ("I'll make you regret this.", "Mày sẽ phải hối hận.", RUDE, "en", []),
            ("Me? I'm fine.", "Em á? Em không sao.", SERA, "en", []),
            # someone else's thanks passed on (a fresh 1.8B answer the role re-ask was asked for)
            ("Grandma, the lady next door asked me to tell you thanks.",
             "Bà ơi, người phụ nữ bên cạnh đã nhờ cháu nói với bà cảm ơn.", ("cháu", "bà"), "en", []),
            ("We're sorry, we're closed today.", "Xin lỗi anh, hôm nay tiệm chúng tôi đóng cửa.", CLERK, "en", []),
            ("We apologize for the wait.", "Chúng tôi xin lỗi vì đã để anh phải đợi.", CLERK, "en", []),
            ("Because I love you, I'll wait.", "Vì anh yêu em nên anh sẽ đợi.", REN, "en", []),
            ("Leave me alone!", "Để em yên!", SERA, "en", []),
            ("Is that your sister?", "Đó là em gái anh à?", SERA, "en", []),
            ("お前は許さない。", "Tao không tha cho mày đâu.", RUDE, "ja", []),  # the topic is the object
            ("君は僕が守る。", "Anh sẽ bảo vệ em.", REN, "ja", []),
            ("お前が好きだ。", "Anh thích em.", REN, "ja", []),
            ("あなたのことが好き。", "Anh là người em thích nhất.", SERA, "ja", []),  # emotive: turned round
            ("お母さんはね、あなたのことが一番大事なの。", "Với mẹ, con là quan trọng nhất.", MOM, "ja", []),
            ("私がついてるから、大丈夫だよ。", "Có em ở đây rồi, anh sẽ ổn thôi.", SERA, "ja", []),  # a reason clause
            ("私は疲れたから、行ってきて。", "Vì em mệt nên anh đi đi.", SERA, "ja", []),
            ("お前が作ったケーキ、美味しいよ。", "Anh thấy bánh em làm ngon lắm.", REN, "ja", []),  # a relative clause
            ("お前がいるから、大丈夫だ。", "Có em ở đây nên anh không sao.", REN, "ja", []),
            ("お前が来るなら、俺も行く。", "Nếu em đến thì anh cũng đi.", REN, "ja", []),  # a condition
            ("母さんが、お前に謝れってさ。", "Mẹ bảo anh phải xin lỗi em đấy.", REN, "ja", []),  # reported
            ("お前、昨日『お兄ちゃん大好き』って言ってたじゃん", "Hôm qua em còn bảo “em thương anh nhất” cơ mà.", REN, "ja",
             []),  # quoted
            ("私たち、文化祭でお化け屋敷をやることにしました", "Chúng em quyết định làm nhà ma cho lễ hội văn hóa ạ.", STUDENT,
             "ja", []),  # a group
            ("てめえ、俺に勝てると思ってんのか？", "Mày tưởng mày thắng được tao à?", RUDE, "ja", []),
            ("私だけ怒られた。", "Chỉ có em bị mắng.", SERA, "ja", []),
        ])

    def test_review_round(self):
        """The reviewers' second round: quoted offers, "bạn" = friend, relative clauses ending in ー or a
        noun, coordinated subjects, passives, time words before the subject, someone outside the pair."""
        self.check_table([
            ("お兄ちゃんが『俺に任せろ』って言ったんじゃん。", "Chính anh đã nói “cứ để anh lo” mà.", SERA, "ja", []),
            ("お兄ちゃんが『手伝ってあげる』って言ったじゃん。", "Chính anh nói “để anh giúp em” mà.", SERA, "ja", []),
            ("お兄ちゃんが『俺がやる』って言ったんだよ。", "Chính anh nói “để anh làm” mà.", SERA, "ja", []),
            ("お前が『私がやる』って言ったんだろ。", "Chính em nói “để em làm” mà.", REN, "ja", []),
            ("僕の友達の車だ", "Đây là xe của bạn anh", REN, "ja", []),
            ("これは僕の友達のだよ", "Cái này là của bạn anh đấy", REN, "ja", []),
            ("私の友達の家に行くの", "Em đến nhà của bạn em", SERA, "ja", []),
            ("俺のダチの店だ", "Đây là quán của bạn tao", RUDE, "ja", []),
            ("お兄ちゃんが作ったカレー、また食べたいな。", "Em lại muốn ăn món cà ri anh nấu.", SERA, "ja", []),
            ("私が焼いたクッキー、どうだった？", "Anh thấy bánh quy em nướng thế nào?", SERA, "ja", []),
            ("お兄ちゃんが選んだかばん、かわいいね。", "Em thấy cái túi anh chọn dễ thương lắm.", SERA, "ja", []),
            ("You and I are a team.", "Anh và em là một đội mà.", REN, "en", []),
            ("You and I are the same", "Em và anh giống nhau", SERA, "en", []),
            ("We're going to the park tomorrow!", "Mai con được đi công viên chơi đấy!", MOM, "en", []),
            ("Hurry up, we'll be late!", "Nhanh lên, con sẽ muộn học đấy!", MOM, "en", []),
            ("I was saved by you.", "Anh đã cứu em.", SERA, "en", []),
            ("私は先生に褒められた。", "Cô đã khen em.", ("em", "cô"), "ja", []),
            ("昨日の夜、お前に電話したんだぞ。", "Tối qua anh gọi rồi mà.", REN, "ja", []),
            ("あとでお前にかけ直す。", "Lát nữa anh gọi lại.", REN, "ja", []),
            ("お兄ちゃんの料理、おいしい！", "Anh nấu cho em ngon quá!", SERA, "ja", []),
            ("いいのよ、先生がやっておくから。", "Không sao đâu, cô làm nốt cho.", TEACHER, "ja", []),
            ("「お前は私の自慢の弟子じゃ」", "\"Con là đệ tử đáng tự hào của ta\"", MASTER, "ja", []),
            ("お前は俺が家まで送る。", "Anh sẽ đưa em về tận nhà.", REN, "ja", []),
            ("I'm so proud of you, son.", "Bố rất tự hào về con, con trai.", ("bố", "con"), "en", []),
            ("Look at me.", "Nhìn anh này.", REN, "en", []),
        ])
        self.check_table([  # caught now
            ("「お前は私の自慢の弟子じゃ」", "\"Ta là đệ tử đáng tự hào của con\"", MASTER, "ja", ["con", "ta"]),
            ("お前は俺が家まで送る。", "Em sẽ đưa anh về tận nhà.", REN, "ja", ["anh", "em"]),  # the topic is the object
            ("お前は本当に頑張ったな、トビ。", "Ta thật sự đã rất cố gắng, Tobi.", MASTER, "ja", ["ta"]),
            ("I'm so proud of you, son.", "Con rất tự hào về bố, con trai.", ("bố", "con"), "en", ["bố", "con"]),
            ("Did you sleep at all last night?", "Tối qua em có ngủ được chút nào không?", SERA, "en", ["em"]),
            ("You look tired.", "Trông em mệt quá.", SERA, "en", ["em"]),
            ("What happened to your hand?", "Tay mẹ bị sao thế?", MOM, "en", ["mẹ"]),  # a possessor without "của"
            ("お姉ちゃんの言うこと、聞きなさい。", "Chị phải nghe lời em.", AOI, "ja", ["em"]),
            ("いいのよ、先生がやっておくから。", "Không sao đâu, em làm nốt cho.", TEACHER, "ja", ["em"]),
            ("先生の授業、すごく分かりやすいです。", "Giờ học của em rất dễ hiểu ạ.", ("em", "cô"), "ja", ["em"]),
        ])
        # an address at the end and a reported owner are no slots: "tay mẹ" for "your hand" is only reported
        found = check_line("What happened to your hand?", "Tay mẹ bị sao thế?", *MOM, "en")
        self.assertEqual([(i.anchor, i.basis) for i in found], [("owner", "pronoun")])

    def test_review_round_4(self):
        """The reviewers' adversarial set r3: right lines the slot check misread (they were asked again with a
        note that told the model to swap them) and real errors it missed."""
        self.check_table([  # right: not judged (or judged right)
            # 俺が in a time clause, お前 told to stay: not "the topic is the object"
            ("お前は俺が戻るまでここにいろ", "Em cứ ở yên đây cho đến khi anh quay lại.", REN, "ja", []),
            ("お前は俺が戻るまでここにいろ", "Ở yên đây đợi anh về.", REN, "ja", []),
            ("お前は俺が呼ぶまで出てくるなよ", "Em đừng ra ngoài cho đến khi anh gọi nhé.", REN, "ja", []),
            ("あなたは私が戻るまで待ってて", "Anh đợi đến khi em quay lại nhé.", SERA, "ja", []),
            ("俺が戻るまで待ってて", "Em đợi đến khi anh về nhé.", REN, "ja", []),
            # a Vietnamese topic, "để <pronoun>" inside a clause
            ("お前は俺が送っていく", "Em thì để anh đưa về.", REN, "ja", []),
            ("お前は俺が送っていく", "Em sẽ để anh đưa em đi.", REN, "ja", []),
            # the source's subject made an agent, a cause or a converse verb's partner
            ("You saved me.", "Nhờ anh mà em mới được cứu đấy.", SERA, "en", []),
            ("You saved me.", "Em được anh cứu đấy.", SERA, "en", []),
            ("You hit me!", "Em bị anh đánh đấy!", SERA, "en", []),
            ("You scared me!", "Em hết hồn vì anh đấy!", SERA, "en", []),
            ("You raised me well, Mom.", "Con nên người được là nhờ mẹ cả.", CHILD, "en", []),
            ("You beat me fair and square.", "Tao thua mày tâm phục khẩu phục.", RUDE, "en", []),
            ("You beat me again.", "Tao lại thua mày rồi.", RUDE, "en", []),
            ("お前が俺を救ったんだ", "Anh được em cứu một mạng đấy.", REN, "ja", []),
            # reflexive and generic "you"
            ("You only ever think about yourself!", "Bạn lúc nào cũng chỉ biết nghĩ cho mình thôi!", PAL, "en", []),
            ("Clean up your own mess!", "Tự dọn đống bừa bộn của mình đi!", PAL, "en", []),
            ("You never know what tomorrow brings.", "Mình đâu biết ngày mai sẽ ra sao.", PAL, "en", []),
            # a cause the source names too keeps the slots: right as it is
            ("I got scolded because of you.", "Tại anh mà em bị mắng đấy.", SERA, "en", []),
            ("Me too! I missed you so much!", "Em cũng vậy! Em nhớ anh nhiều lắm!", SERA, "en", []),
            ("You too! Don't work too hard.", "Anh cũng vậy nhé! Đừng làm việc quá sức.", SERA, "en", []),
            # still: an agentless passive keeps its subject, お前は俺が守る the topic as object
            ("私は先週告白されたの", "Tuần trước em được tỏ tình đấy.", SERA, "ja", []),
            ("お前は俺が送っていく", "Anh sẽ đưa em về.", REN, "ja", []),
        ])
        self.check_table([  # wrong: caught
            ("私は先週告白されたの", "Tuần trước anh được tỏ tình đấy.", SERA, "ja", ["anh"]),
            ("お前は俺が送っていく", "Em sẽ đưa anh về.", REN, "ja", ["anh", "em"]),
            ("僕のせいで、お姉ちゃんが怒られた。", "Tại chị mà em bị mắng.", YUTO, "ja", ["chị", "em"]),
            ("I got scolded because of you.", "Tại em mà anh bị mắng đấy.", SERA, "en", ["anh", "em"]),
            # "before me", "after class": prepositions, no clause moved to the front
            ("You will kneel before me.", "Ta sẽ quỳ trước mặt ngươi.", ("ta", "ngươi"), "en", ["ngươi", "ta"]),
            ("I'll wait for you after class.", "Bạn sẽ đợi mình sau giờ học nhé.", PAL, "en", ["bạn", "mình"]),
            # "một mình" (alone), a final ", cô Mori" (an address), "Ms." (no sentence end)
            ("Don't leave me alone.", "Đừng bỏ anh lại một mình.", SERA, "en", ["anh"]),
            ("I'll never forget you, Ms. Mori.", "Cô sẽ không bao giờ quên em đâu, cô Mori.", ("em", "cô"), "en",
             ["cô", "em"]),
            # "Me too" / "Same to you": the speaker / the listener does it too
            ("Me too. I was worried about you.", "Em cũng thế. Anh lo cho em lắm.", REN, "en", ["em"]),
            ("Same to you, idiot.", "Tao cũng thế, đồ ngốc.", RUDE, "en", ["tao"]),
        ])
        # a clause after before / until / when is still moved: not judged
        self.assertEqual(wrong("I'll call you before you leave.", "Trước khi anh đi, em sẽ gọi cho anh.", SERA, "en"), [])

    def test_judged_slots(self):
        """What the slot check compares, right or wrong; nothing for a sentence it does not judge."""
        self.assertEqual(judged_slots("Don't worry, I'll protect you.", "Đừng lo, anh sẽ bảo vệ em.", *REN, "en"),
                         [("anh", SELF, "subject"), ("em", LISTENER, "object")])
        self.assertEqual(judged_slots("Don't worry, I'll protect you.", "Đừng lo, em sẽ bảo vệ anh.", *REN, "en"),
                         [("em", SELF, "subject"), ("anh", LISTENER, "object")])
        self.assertEqual(judged_slots("お前は俺が送っていく", "Em sẽ là anh gửi cho", *REN, "ja"), [])
        self.assertEqual(judged_slots("You saved me.", "Em được anh cứu đấy.", *SERA, "en"), [])

    def test_real_outputs_that_are_right(self):
        """Right lines of the stored and fresh real outputs that an earlier version of the slot check
        flagged: a restructured question, a third person, a cleft, a possessive turned into a clause."""
        self.check_table([
            ("Welcome to Moonlight Books! Are you looking for anything in particular?",
             "Chào mừng anh đến với Hiệu sách Ánh Trăng! Em có thể giúp anh tìm kiếm món đồ cụ thể nào không ạ?",
             ("em", ("anh", "quý khách")), "en", []),
            ("任せて！姉ちゃんの好きなカレー、作ってあげる。", "Để đó cho tôi! Tôi sẽ nấu món cà ri yêu thích của em gái tôi.",
             YUTO, "ja", []),
            ("本当？お兄ちゃんの料理、久しぶりだね。", "Thật sao? Đã lâu rồi anh mới nấu ăn cho em.", SERA, "ja", []),
            ("Why should I apologize? You apologize!", "Sao anh phải xin lỗi chứ? Em xin lỗi anh đi!", REN, "en", []),
        ])
        # wrong in meaning (anh ấy: a third person), but "em" is no wrong role: nothing is reported for it
        self.assertEqual(wrong("これ持っててくれる？お兄ちゃんのために作ったの。",
                               "Anh có thể mang cái này cho em được không? Đây là em làm cho anh ấy đấy.", SERA), [])


KAITO = ("anh", "em")  # older brother -> younger sister


class BenefactiveTests(CaseTable, unittest.TestCase):
    """Who did the favour for whom (〜てくれた / 〜てくれる / 〜てもらう / 〜てあげる, "you made this for me",
    "I'll cook for you"): the one who does it is the subject of the clause with "cho / giúp / thay <the one it
    is for>", or the agent of "<for> được <giver>"."""

    def test_frames(self):
        for source, pair, lang, want in (
                ("弁当作ってくれたのか。ありがとな、セラ。", KAITO, "ja", [(LISTENER, SELF)]),  # a question + thanks
                ("これ、お前が作ってくれたのか？", KAITO, "ja", [(LISTENER, SELF)]),
                ("お兄ちゃん、迎えに来てくれたの？", SERA, "ja", [(LISTENER, SELF)]),  # the listener called
                ("お母さん、いつもお弁当作ってくれてありがとう。", CHILD, "ja", [(LISTENER, SELF)]),
                ("昨日は残業を代わってくださって、本当に助かりました。", ("em", "chị"), "ja", [(LISTENER, SELF)]),
                ("お前がくれたお守り、まだ持ってるよ。", KAITO, "ja", [(LISTENER, SELF)]),  # main verb くれる
                ("手伝ってもらって悪いな。", KAITO, "ja", [(LISTENER, SELF)]),  # もらう + an apology
                ("先生にはいつも助けてもらってばかりです。", STUDENT, "ja", [(LISTENER, SELF)]),  # the に-agent
                ("お越しいただき、ありがとうございます。", CLERK, "ja", [(LISTENER, SELF)]),
                ("今度、クッキー焼いてあげるね。", FRIEND, "ja", [(SELF, LISTENER)]),
                ("お母さんが作ってあげるから、待ってて。", MOM, "ja", [(SELF, LISTENER)]),  # a role word for herself
                ("お前の宿題、俺が手伝ってやるよ。", KAITO, "ja", [(SELF, LISTENER)]),
                ("この花、お前にやるよ", KAITO, "ja", [(SELF, LISTENER)]),  # main verb やる
                ("肩たたいてあげようか？", CHILD, "ja", [(SELF, LISTENER)]),  # an offer as a question
                ("You made this for me? Thank you!", SERA, "en", [(LISTENER, SELF)]),
                ("Grandma, did you knit this for me?", MINATO, "en", [(LISTENER, SELF)]),
                ("Thanks for helping me with the move.", KAITO, "en", [(LISTENER, SELF)]),
                ("I'll cook your favourite for you tonight.", MOM, "en", [(SELF, LISTENER)])):
            with self.subTest(source=source):
                self.assertEqual(favours(source, pair, lang), want)
        # no benefactive frame: a third person or a role word outside the pair gives, the one it is for is
        # someone else, a condition, a quote or report, a statement with no giver named, もらう / あげる asked
        # about the listener, 〜てあげて (the listener for someone), English with a third person, "we", a report
        for source, pair, lang in (
                ("ケンジくんが宿題手伝ってくれたの。", SERA, "ja"), ("彼ね、毎日私にお弁当作ってくれるの。", SERA, "ja"),
                ("母さんが作ってくれた弁当、美味しかった。", FRIEND, "ja"), ("ケンジくん、私に謝ってくれたよ。", SERA, "ja"),
                ("誰が作ってくれたの？", SERA, "ja"), ("妹を助けてくれてありがとう。", KAITO, "ja"),
                ("雨が止んでくれた。", SERA, "ja"), ("来てくれると嬉しいな。", SERA, "ja"),
                ("ようこそお越しくださいました。", CLERK, "ja"),  # no thanks or question: whose visit is not clear
                ("お父さんが『買ってやる』って言ってたよ。", CHILD, "ja"),
                ("手伝ってくれって言われた。", SERA, "ja"), ("来てくれたね。", SERA, "ja"),
                ("手伝ってもらった？", SERA, "ja"), ("先輩に手伝ってもらったんだ。", SERA, "ja"),
                ("ケーキ、作ってあげたの？", SERA, "ja"), ("妹に優しくしてあげて。", KAITO, "ja"),
                ("お兄ちゃんが手伝ってくれないなら、自分でやるからいいもん", SERA, "ja"),
                ("My mom made this for me.", SERA, "en"), ("He did it for me.", SERA, "en"),
                ("Mom said you made this for me.", SERA, "en"), ("We baked this cake for you!", MINATO, "en"),
                ("If you do this for me, I'll owe you.", SERA, "en")):
            with self.subTest(source=source):
                self.assertEqual(favours(source, pair, lang), [])
        # the condition is no frame, the offer after it is
        self.assertEqual(favours("手伝ってくれたら、ケーキ作ってあげる。", SERA), [(SELF, LISTENER)])
        (f,) = [f for f in source_frames("弁当作ってくれたのか。", "ja", *KAITO) if f.kind == "favour"]
        self.assertEqual((f.cue, f.tentative), ("弁当作ってくれた", True))  # asked again, never rewritten
        # an honorific statement (お越しくださいました) is no request any more ("please come" still is)
        self.assertEqual(frames("ようこそお越しくださいました。"), [])
        self.assertEqual(frames("またお越しくださいませ。"), [("request", LISTENER, SELF)])

    def test_who_does_it_for_whom(self):
        self.check_table([
            # held-out D: the 7B made the brother the one who made the lunch
            ("弁当作ってくれたのか。ありがとな、セラ。", "Em làm cơm hộp cho anh à? Cảm ơn em nhé, Sera.", KAITO, "ja", []),
            ("弁当作ってくれたのか。ありがとな、セラ。", "Anh làm bữa trưa cho em à? Cảm ơn em nhiều nhé, Sera.", KAITO, "ja",
             ["anh", "em"]),
            ("これ、お前が作ってくれたのか？", "Cái này em làm cho anh à?", KAITO, "ja", []),
            ("これ、お前が作ってくれたのか？", "Cái này anh làm cho em à?", KAITO, "ja", ["anh", "em"]),
            ("お母さん、いつもお弁当作ってくれてありがとう。", "Mẹ ơi, cảm ơn mẹ vì ngày nào cũng làm cơm hộp cho con.",
             CHILD, "ja", []),
            ("お母さん、いつもお弁当作ってくれてありがとう。", "Mẹ ơi, cảm ơn mẹ vì ngày nào con cũng làm cơm hộp cho mẹ.",
             CHILD, "ja", ["mẹ"]),
            ("昨日は残業を代わってくださって、本当に助かりました。", "Hôm qua chị đã làm thêm giờ thay em, thật sự giúp em nhiều lắm ạ.",
             ("em", "chị"), "ja", []),
            ("昨日は残業を代わってくださって、本当に助かりました。", "Hôm qua em đã làm thêm giờ thay chị, thật sự đã giúp ích rất nhiều.",
             ("em", "chị"), "ja", ["chị", "em"]),
            ("ノート貸してくれて助かったよ。", "May mà cậu cho tớ mượn vở, đỡ quá.", FRIEND, "ja", []),
            ("ノート貸してくれて助かったよ。", "May mà tớ cho cậu mượn vở, đỡ quá.", FRIEND, "ja", ["cậu", "tớ"]),
            ("手伝ってもらって悪いな。", "Cảm ơn em đã giúp anh nhé, phiền em quá.", KAITO, "ja", []),
            ("手伝ってもらって悪いな。", "Cảm ơn anh đã giúp em nhé, phiền anh quá.", KAITO, "ja", ["anh", "em"]),
            ("先生にはいつも助けてもらってばかりです。", "Em lúc nào cũng được cô giúp đỡ ạ.", ("em", "cô"), "ja", []),
            ("先生にはいつも助けてもらってばかりです。", "Cô lúc nào cũng được em giúp đỡ ạ.", ("em", "cô"), "ja", ["cô", "em"]),
            ("今度、クッキー焼いてあげるね。", "Lần sau tớ nướng bánh quy cho cậu nhé.", FRIEND, "ja", []),
            ("今度、クッキー焼いてあげるね。", "Lần sau cậu nướng bánh quy cho tớ nhé.", FRIEND, "ja", ["cậu", "tớ"]),
            ("お母さんが作ってあげるから、待ってて。", "Mẹ làm cho con, con đợi chút nhé.", MOM, "ja", []),
            ("お母さんが作ってあげるから、待ってて。", "Con làm cho mẹ, mẹ đợi chút nhé.", MOM, "ja", ["con", "mẹ"]),
            ("お母さんが作ってあげるから、待ってて。", "Mẹ sẽ làm cho con đây, con đợi một chút nhé.", MOM, "ja", []),
            ("お母さんが作ってあげるから、待ってて。", "Con sẽ làm cho mẹ đây, con đợi một chút nhé.", MOM, "ja", ["con", "mẹ"]),
            ("この花、お前にやるよ", "Bông hoa này anh tặng em đấy.", KAITO, "ja", []),
            ("この花、お前にやるよ", "Bông hoa này em tặng anh đấy.", KAITO, "ja", ["anh", "em"]),
            # a request for the speaker: who is asked to do it is the subject of the clause with "giúp / cho …"
            ("お兄ちゃん、この箱運んでくれる？", "Anh ơi, anh bê giúp em cái hộp này được không?", SERA, "ja", []),
            ("お兄ちゃん、この箱運んでくれる？", "Anh ơi, em bê giúp anh cái hộp này được không?", SERA, "ja", ["anh", "em"]),
            ("お母さん、明日の遠足のお弁当、唐揚げ入れてくれる?",
             "Mẹ ơi, bữa trưa đi dã ngoại ngày mai mẹ cho thêm gà chiên vào giúp con nhé?", CHILD, "ja", []),
            ("You made this for me? Thank you!", "Anh làm cái này cho em à? Cảm ơn anh!", SERA, "en", []),
            ("You made this for me? Thank you!", "Em làm cái này cho anh à? Cảm ơn anh!", SERA, "en", ["anh", "em"]),
            ("Thanks for helping me with the move.", "Cảm ơn em đã giúp anh chuyển nhà.", KAITO, "en", []),
            ("Thanks for helping me with the move.", "Cảm ơn em đã giúp em chuyển nhà.", KAITO, "en", ["em"]),
            ("I'm really happy for you.", "Anh thật sự mừng cho em.", KAITO, "en", []),
            ("I'm really happy for you.", "Em thật sự mừng cho anh.", KAITO, "en", ["anh", "em"]),
        ])
        found = check_line("弁当作ってくれたのか。ありがとな、セラ。", "Anh làm bữa trưa cho em à? Cảm ơn em nhé, Sera.",
                           *KAITO, "ja")
        self.assertEqual([(i.found, i.anchor, i.basis, i.act) for i in found],
                         [("Anh", "favour", "cue", (LISTENER, SELF, "弁当作ってくれた", "favour")),
                          ("em", "favour", "cue", (LISTENER, SELF, "弁当作ってくれた", "favour"))])
        # a request's "cho" slot says who does it for whom too (for the note), its verb included
        (i,) = check_line("お母さん、明日の遠足のお弁当、唐揚げ入れてくれる?",
                          "Mẹ ơi, bữa trưa đi dã ngoại ngày mai con có thể cho thêm gà chiên vào được không ạ?", *CHILD, "ja")
        self.assertEqual((i.found, i.anchor, i.basis, i.act), ("con", "give", "frame",
                                                               (LISTENER, SELF, "唐揚げ入れてくれる", "request")))

    def test_near_misses(self):
        self.check_table([
            # someone else gives, or it is for someone else: not judged
            ("ケンジくんが宿題手伝ってくれたの。", "Kenji đã giúp anh làm bài tập đấy.", SERA, "ja", []),
            ("彼ね、毎日私にお弁当作ってくれるの。", "Anh ấy ngày nào cũng làm cơm hộp cho em đấy.", SERA, "ja", []),
            ("母さんが作ってくれた弁当、美味しかった。", "Hộp cơm mẹ làm cho cậu ngon lắm.", FRIEND, "ja", []),
            # a condition, a quote, a report
            ("手伝ってくれたら、ケーキ作ってあげる。", "Nếu anh giúp em thì em sẽ làm bánh cho anh.", SERA, "ja", []),
            ("お父さんが『買ってやる』って言ってたよ。", "Bố bảo bố sẽ mua cho con đấy.", CHILD, "ja", []),
            ("ケンジに手伝ってくれって言われた。", "Kenji nhờ em giúp anh ấy.", SERA, "ja", []),
            ("Mom said you made this for me.", "Mẹ bảo anh làm cái này cho em.", SERA, "en", []),
            # "cho" as "let", a final particle, in a noun phrase; "cho phép", the causative "làm cho … vui"
            ("お兄ちゃんのゲーム、ちょっとやらせてくれない？", "Anh cho em chơi game của anh một chút được không?", SERA, "ja",
             []),
            ("仕方ないな、やってやるよ。", "Thôi được rồi, để anh làm cho.", KAITO, "ja", []),
            ("お前がくれたお守り、まだ持ってるよ。", "Lá bùa em tặng anh, anh vẫn giữ đấy.", KAITO, "ja", []),
            ("行かせてくれる？", "Anh cho phép em đi nhé?", SERA, "ja", []),
            ("笑わせてあげる。", "Để em làm cho anh cười nhé.", SERA, "ja", []),
            # someone else in the clause before the act; the translation's acts go both ways; a reflexive
            ("Thank you for covering my shift yesterday.", "Tôi cảm ơn anh hôm qua đã làm thay ca cho tôi.",
             ("tôi", "anh"), "en", []),
            ("手伝ってくれてありがとう。", "Cảm ơn anh đã giúp em, lần sau em sẽ làm cho anh nhé.", SERA, "ja", []),
            ("手伝ってくれてありがとう。", "Cảm ơn anh, anh tự lo cho anh cũng được mà.", SERA, "ja", []),
            # a relative clause between the subject and "cho" has a subject of its own (the 7B, round 6 audit)
            ("任せて！姉ちゃんの好きなカレー、作ってあげる。", "Để em lo nhé! Em sẽ nấu món cà ri mà chị thích cho chị.", YUTO,
             "ja", []),
            # two source sentences the translation runs together: which one a clause renders is not known
            ("ようこそお越しくださいました。ありがとうございます。", "Cảm ơn quý khách đã đến, em sẽ chuẩn bị phòng cho quý khách.",
             ("em", "quý khách"), "ja", []),
            # a passive with the giver as its agent is the benefactive turned round: judged, and right here
            ("お前が助けてくれたんだな。", "Anh được em cứu một mạng đấy.", KAITO, "ja", []),
        ])

    def test_judged_slots(self):
        src = "弁当作ってくれたのか。ありがとな、セラ。"
        self.assertEqual(judged_slots(src, "Em làm cơm hộp cho anh à? Cảm ơn em nhé, Sera.", *KAITO, "ja"),
                         [("Em", LISTENER, "favour"), ("anh", SELF, "favour")])
        self.assertEqual(judged_slots(src, "Em làm cơm hộp cho anh à? Cảm ơn em nhé, Sera.", *KAITO, "ja", spans=True),
                         [(0, 2, "Em", LISTENER, "favour"), (19, 22, "anh", SELF, "favour")])
        self.assertEqual(judged_slots(src, "Em làm bữa trưa này vì anh à? Cảm ơn em nhé, Sera.", *KAITO, "ja"), [])

    def test_review_round_7_both_ways_and_conditions(self):
        """Right lines the round-6 rules flagged (and the older help / give anchors rewrote into the reversed
        meaning): favours both ways in one sentence, a condition, the speaker's own act after the thanks."""
        self.check_table([
            # favours both ways (r4 review): each clause by its own source clause; the thanks clause is no offer's
            ("手伝ってくれてありがとう、今度は私が手伝ってあげるね。", "Cảm ơn anh đã giúp em, lần sau em sẽ giúp anh nhé.", SERA, "ja",
             []),
            ("手伝ってくれて助かったよ、今度は俺が手伝ってやる。", "Cảm ơn em đã giúp anh, lần sau anh sẽ giúp lại em.", KAITO, "ja",
             []),
            ("手伝ってくれて助かったよ、今度は俺が手伝ってやる。", "Cảm ơn bạn đã giúp đỡ, lần này anh sẽ giúp bạn.", KAITO, "ja",
             []),  # the 1.8B: was rewritten "Cảm ơn anh đã giúp đỡ" (Kaito thanking himself)
            ("手伝ってくれて助かったよ、今度は俺が手伝ってやる。", "Cảm ơn bạn đã giúp đỡ, lần này em sẽ giúp bạn thôi.", SERA, "ja",
             []),
            ("作ってくれてありがとう、お返しに何か作ってあげる。", "Cảm ơn anh đã nấu cho em, em sẽ nấu gì đó cho anh để đáp lễ.",
             SERA, "ja", []),
            ("Thanks for helping me, I'll help you next time.", "Cảm ơn anh đã giúp em, lần sau em sẽ giúp anh.", SERA, "en",
             []),
            ("Thanks for the help, I'll make it up to you.", "Cảm ơn anh đã giúp em, em sẽ đền bù cho anh.", SERA, "en", []),
            # ... and the wrong ones are still caught, in their own clause
            ("手伝ってくれてありがとう、今度は私が手伝ってあげるね。", "Cảm ơn anh đã giúp em, lần sau anh sẽ giúp em nhé.", SERA, "ja",
             ["anh", "em"]),
            ("手伝ってくれて助かったよ、今度は俺が手伝ってやる。", "Cảm ơn anh đã giúp em, lần sau anh sẽ giúp lại em.", KAITO, "ja",
             ["em"]),
            # a condition: the act in it is not the line's offer
            ("宿題やってくれたら、アイスおごってあげるよ。", "Nếu anh làm bài tập giúp em, em sẽ mời anh ăn kem nhé.", SERA, "ja",
             []),
            ("黙っててくれたら、お菓子分けてあげる。", "Nếu anh im lặng cho em, em sẽ chia kẹo cho anh.", SERA, "ja", []),
            ("手伝ってくれたら、ジュースおごってあげる。", "Anh giúp em một tay, em sẽ mời anh uống nước nhé.", SERA, "ja", []),
            ("手伝ってくれたら、ジュースおごってあげる。", "Anh mà giúp em thì em bao anh chai nước.", SERA, "ja", []),
            ("手伝ってくれたら、ケーキ作ってあげる。", "Anh giúp em đi, em sẽ làm bánh cho anh.", SERA, "ja", []),
            ("宿題見てくれたら、お皿洗ってあげる。", "Anh xem bài tập giúp em thì em rửa bát cho anh.", SERA, "ja", []),
            ("宿題手伝ったら、アイスおごってあげる。", "Anh giúp em làm bài tập, em sẽ mời anh ăn kem.", SERA, "ja", []),
            ("手伝ってくれたら、ケーキ作ってあげる。", "Em sẽ làm bánh cho anh nếu anh giúp em.", SERA, "ja", []),
            # the speaker's own act after the thanks, in one source sentence
            ("迎えに来てくれてありがとう、荷物持つね。", "Cảm ơn anh đã đến đón em, để em xách đồ giúp anh nhé.", SERA, "ja", []),
            ("迎えに来てくれてありがとう、荷物持つね。", "Cảm ơn anh đã đến đón em. Em xách đồ giúp anh nhé.", SERA, "ja", []),
            ("ご飯作ってくれてありがとう、片付けはするね。", "Cảm ơn anh đã nấu cơm. Em sẽ dọn dẹp cho anh.", SERA, "ja", []),
            ("迎えに来てくれてありがとう、荷物持つね。", "Để em xách đồ giúp anh nhé, cảm ơn anh đã đến đón em.", SERA, "ja", []),
            # a possessor before "giúp / cho" is no subject (the shirt is the child's)
            ("お母さん、僕のシャツ、アイロンかけてくれる？", "Mẹ ơi, mẹ ủi áo sơ mi của con giúp con được không?", CHILD, "ja", []),
            ("先生、私の作文、見ていただけますか。", "Thưa cô, cô xem bài văn của em giúp em được không ạ?", ("em", "cô"), "ja", []),
            ("お兄ちゃん、私の自転車、直してくれない？", "Anh ơi, anh sửa xe đạp của em giúp em được không?", SERA, "ja", []),
            ("お前の分も買ってきてやったぞ。", "Anh mua cả phần của em cho em rồi đấy.", KAITO, "ja", []),
            ("明日、弟を駅まで迎えに行ってもらえる？", "Mai em ra ga đón em trai chị giúp chị được không?", AOI, "ja", []),
            # the particle で and an adverb in に are no benefactive (自分でやる: "I'll do it myself")
            ("自分でやるから、手伝わなくていいよ。", "Em tự làm được, anh không cần giúp em đâu.", SERA, "ja", []),
            ("後でやるよ。", "Lát nữa em làm, anh khỏi giúp em.", SERA, "ja", []),
            ("一緒にやろう！", "Mình cùng làm đi, anh giúp em một tay nhé!", SERA, "ja", []),
            ("自分でやるから大丈夫。", "Em tự làm được mà, anh không cần giúp đâu.", SERA, "ja", []),
            ("いいよ、自分でやる。", "Không sao, em tự làm. Anh không phải giúp đâu.", SERA, "ja", []),
            # "cho" of purpose or before a thing, a converse verb (mượn, xin: the one who receives)
            ("ノート貸してくれる？", "Em mượn vở cho kịp bài được không anh?", SERA, "ja", []),
            ("ノート貸してくれる？", "Em mượn vở được không anh? Em cần chép cho kịp.", SERA, "ja", []),
            ("お母さん、明日の遠足のお弁当、唐揚げ入れてくれる?",
             "Mẹ ơi, con xin thêm gà chiên cho hộp cơm đi dã ngoại ngày mai được không ạ?", CHILD, "ja", []),
            ("お母さん、明日の遠足のお弁当、唐揚げ入れてくれる?",
             "Mẹ ơi, con thích có gà chiên trong hộp cơm cho buổi dã ngoại ngày mai, mẹ làm nhé?", CHILD, "ja", []),
            # a time is no condition; a cause with おかげで and a relative clause on a topic are one act with what follows
            ("帰りにアイス買ってあげる。", "Lúc về em sẽ mua kem cho chị nhé.", AOI, "ja", ["chị", "em"]),
            ("師匠が教えてくれた技のおかげで、勝てました。", "Nhờ chiêu thức con đã dạy cho thầy mà con đã thắng.", DISCIPLE, "ja",
             ["thầy"]),
            ("君が貸してくれたノート、すごく役に立ったよ。", "Cuốn vở tớ cho cậu mượn giúp ích lắm đấy.", FRIEND, "ja", ["cậu"]),
        ])
        # nothing of these is rewritten: their slots are asked again, the old help anchor no longer judges them
        found = check_line("手伝ってくれてありがとう、今度は私が手伝ってあげるね。", "Cảm ơn anh đã giúp em, lần sau anh sẽ giúp em nhé.",
                           *SERA, "ja")
        self.assertTrue(all(i.basis == "cue" for i in found if i.anchor in ("help", "favour")))

    def test_review_round_7_frames(self):
        for source, pair, lang in (
                ("あんまり甘やかしてやるなよ。", KAITO, "ja"), ("無理してやるなよ。", KAITO, "ja"),  # prohibitive
                ("手伝ってくれてありがとう、ってケンジに言っといて。", SERA, "ja"),  # someone else's thanks passed on
                ("来てくれてありがとう、って伝えて。", SERA, "ja"),
                ("助けてくれたのはケンジくんだよ。ありがとね。", SERA, "ja"),  # the giver named after the cue
                ("作ってくれた人に感謝しなきゃね。ありがとう。", SERA, "ja"),
                ("自分でやる", SERA, "ja"), ("後でやる", SERA, "ja"), ("一人でやった", SERA, "ja"), ("一緒にやろう", SERA, "ja"),
                ("すぐにやる", SERA, "ja"), ("ついにやる", SERA, "ja"), ("手をあげた人", SERA, "ja"),
                ("I've been waiting for you", SERA, "en"), ("I'm too old for you", SERA, "en"),
                ("Did you ask for me?", SERA, "en")):
            with self.subTest(source=source):
                self.assertEqual(favours(source, pair, lang), [])
        self.assertEqual(frames("手伝ってくれてありがとう、ってケンジに言っといて。"), [("request", LISTENER, None)])
        self.assertEqual([k for k, *_ in frames_with_pair("自分でやる", SERA)], [])  # no offer either
        # a gift of the speaker's named by what follows, これ、あげる, a name called in a question or thanks
        for source, pair, want in (
                ("これ、あげる。", SERA, [(SELF, LISTENER)]), ("この前あげたマフラー、使ってる？", SERA, [(SELF, LISTENER)]),
                ("先輩、手伝ってくれてありがとうございます。", ("em", "chị"), [(LISTENER, SELF)]),
                ("たくみくん、私のために傘持ってきてくれたの？", SERA, [(LISTENER, SELF)]),
                ("ケンジくん、私に謝ってくれたよ。", SERA, []),  # a statement: Kenji may be someone else
                ("この前あげたマフラー、似合ってたね。", SERA, [])):  # not asked about: maybe someone else's
            with self.subTest(source=source):
                self.assertEqual(favours(source, pair), want)

    def test_review_round_7_recall(self):
        self.check_table([
            ("これ、あげる。", "Cái này anh cho em.", SERA, "ja", ["anh", "em"]),
            ("これ、あげる。", "Cái này em cho anh.", SERA, "ja", []),
            ("この前あげたマフラー、使ってる？", "Cái khăn anh tặng em hôm trước, anh có dùng không?", SERA, "ja", ["em"]),
            ("先輩、手伝ってくれてありがとうございます。", "Chị ơi, em cảm ơn vì đã giúp chị ạ.", ("em", "chị"), "ja", ["chị"]),
            ("先輩、手伝ってくれてありがとうございます。", "Chị ơi, em cảm ơn chị vì đã giúp em ạ.", ("em", "chị"), "ja", []),
            ("たくみくん、私のために傘持ってきてくれたの？", "Takumi, em mang ô đến cho anh à?", SERA, "ja", ["anh", "em"]),
            # "nhận … từ <giver>": the one who receives is the subject
            ("お前には助けてもらってばかりだな。", "Em lúc nào cũng chỉ nhận sự giúp đỡ từ anh thôi.", KAITO, "ja", ["anh", "em"]),
            ("お前には助けてもらってばかりだな。", "Anh lúc nào cũng chỉ nhận sự giúp đỡ từ em thôi.", KAITO, "ja", []),
            # "làm cho em" in a relative clause on a noun is "made for me", not the causative "làm cho em ngon"
            ("お前が作ってくれた弁当、うまかったよ。", "Hộp cơm anh làm cho em ngon lắm.", KAITO, "ja", ["em"]),
            ("お前が作ってくれた弁当、うまかったよ。", "Hộp cơm em làm cho anh ngon lắm.", KAITO, "ja", []),
        ])

    def test_note_cues_start_at_a_word(self):
        for source, pair, translation, cue in (
                ("おばあちゃんが送ってくれた野菜、みんなで食べたよ。", MINATO, "Rau cháu gửi cho bà, cả nhà ăn hết rồi ạ.", "送ってくれた"),
                ("お母さん、カレーにじゃがいも入れてくれる？", CHILD, "Mẹ ơi, con cho khoai tây vào cà ri được không?", "入れてくれる"),
                ("お兄ちゃんの分も買っておいてあげたよ。", SERA, "Anh mua sẵn cho em một phần rồi đấy.", "買っておいてあげた"),
                ("今日は好きなだけ遊ばせてあげる。", MOM, "Hôm nay con cho mẹ chơi thỏa thích.", "遊ばせてあげる"),
                ("Can you cover for me tomorrow?", FRIEND, "Mai tớ làm thay cậu được không?", "Can you cover for me tomorrow")):
            with self.subTest(source=source):
                acts = {i.act[2] for i in check_line(source, translation, *pair, "en" if source.isascii() else "ja")
                        if i.act}
                self.assertEqual(acts, {cue})


BA_STUDENT = ("em", "Sensei")  # a Blue Archive student -> the teacher, whom everyone calls "Sensei"
BA_ARIS = ("Aris", "Sensei")  # Aris calls herself by her name
BA_SENSEI = ("tôi", "em")  # the teacher -> a student
SAKI = ("chị", "em")  # a senior colleague -> her junior


class TitleAsPairWordTests(CaseTable, unittest.TestCase):
    """A title the listener is called by ("Sensei") or a name the speaker calls herself by ("Aris") works as a
    pair word like a pronoun (lines written for the Blue Archive profile, not taken from the game)."""

    def test_analyze(self):
        self.assertEqual(roles("Sensei ơi, em mang cơm hộp cho Sensei này", *BA_STUDENT),
                         [("Sensei", LISTENER, True), ("em", SELF, False), ("Sensei", LISTENER, False)])
        self.assertEqual(roles("Aris được gặp Sensei nên vui lắm ạ!", *BA_ARIS),
                         [("Aris", SELF, False), ("Sensei", LISTENER, False)])

    def test_right_lines_are_not_flagged(self):
        self.check_table([
            ("先生、おはようございます！", "Sensei, chào buổi sáng ạ!", BA_STUDENT, "ja", []),
            ("先生、お弁当作ってきたよ！", "Sensei ơi, em mang cơm hộp cho Sensei này!", BA_STUDENT, "ja", []),
            ("先生、ありがとうございます！", "Em cảm ơn Sensei ạ!", BA_STUDENT, "ja", []),
            ("ごめんなさい、先生。遅れちゃいました…", "Em xin lỗi Sensei. Em đến muộn mất rồi…", BA_STUDENT, "ja", []),
            ("先生、大丈夫？怪我してない？", "Sensei có sao không? Sensei có bị thương không?", BA_STUDENT, "ja", []),
            ("うへ～、おじさんはもう眠いよ～", "Uhe~ ông chú này buồn ngủ lắm rồi~", BA_STUDENT, "ja", []),
            ("先生、手伝ってくれる？", "Sensei giúp em một tay được không?", BA_STUDENT, "ja", []),
            ("私が先生を守ります。", "Em sẽ bảo vệ Sensei.", BA_STUDENT, "ja", []),
            ("先生、私のプリン食べたでしょ！", "Sensei ăn bánh pudding của em rồi đúng không!", BA_STUDENT, "ja", []),
            ("行ってきます、先生！", "Em đi đây, Sensei!", BA_STUDENT, "ja", []),
            ("先生、おかえりなさい！", "Sensei về rồi ạ!", BA_STUDENT, "ja", []),
            ("先生、このおじさんに任せておきなよ～", "Sensei cứ để ông chú này lo cho~", BA_STUDENT, "ja", []),
            ("先生、ただいま戻りました！", "Sensei, Aris về rồi ạ!", BA_ARIS, "ja", []),
            ("ありがとう、ユウカ。助かったよ。", "Cảm ơn em, Yuuka. Em giúp tôi nhiều lắm.", BA_SENSEI, "ja", []),
        ])

    def test_wrong_lines_are_caught(self):
        self.check_table([
            ("先生、お弁当作ってきてあげたよ！", "Sensei ơi, Sensei làm cơm hộp cho em này!", BA_STUDENT, "ja", ["em"]),
            ("先生、ありがとうございます！", "Sensei cảm ơn em ạ!", BA_STUDENT, "ja", ["em", "sensei"]),
            ("先生、大丈夫？", "Em có sao không?", BA_STUDENT, "ja", ["em"]),
            ("先生、手伝ってくれる？", "Em giúp Sensei một tay được không?", BA_STUDENT, "ja", ["em", "sensei"]),
            ("行ってきます、先生！", "Sensei đi đây!", BA_STUDENT, "ja", ["sensei"]),
            ("先生、私のプリン食べたでしょ！", "Em ăn bánh pudding của Sensei rồi đúng không!", BA_STUDENT, "ja", ["sensei"]),
            ("私が先生を守ります。", "Sensei sẽ bảo vệ em.", BA_STUDENT, "ja", ["sensei"]),
            ("先生、おかえりなさい！", "Sensei ơi, em về rồi!", BA_STUDENT, "ja", ["em"]),
            ("ごめん、ユウカ。", "Em xin lỗi, Yuuka.", BA_SENSEI, "ja", ["em"]),
        ])

    def test_expected_words_keep_the_titles_capital(self):
        found = check_line("先生、ありがとう！", "Sensei cảm ơn em!", *BA_STUDENT, "ja")
        self.assertEqual([(i.found, i.expected_word) for i in found], [("Sensei", "Em"), ("em", "Sensei")])
        found = check_line("先生、ありがとう！", "Vâng, Sensei cảm ơn em!", *BA_STUDENT, "ja")
        self.assertEqual([(i.found, i.expected_word) for i in found], [("Sensei", "em"), ("em", "Sensei")])
        found = check_line("お姉ちゃん、ごめんね。", "Chị xin lỗi nhé.", "em", "chị", "ja")  # pronouns as before
        self.assertEqual([(i.found, i.expected_word) for i in found], [("Chị", "Em")])


class SourceParticleTests(CaseTable, unittest.TestCase):
    def test_kara_names_no_object(self):
        """私から ("from me": the speaker who tells, gives or is left) has no one slot in the translation: held-out
        set E's false alarm on a right line (em = the junior it is done for, not 私)."""
        self.check_table([
            ("大丈夫、部長には私から話しておくから。", "Không sao đâu, tôi sẽ nói chuyện với trưởng phòng cho em.",
             SAKI, "ja", []),
            ("大丈夫、部長には私から話しておくから。", "Không sao đâu, chị sẽ nói với trưởng phòng giúp em.", SAKI, "ja", []),
            ("これは私からのプレゼント。", "Đây là quà cháu tặng bà.", MINATO, "ja", []),
            ("私から離れないで。", "Đừng rời xa em.", SERA, "ja", []),
        ])
        self.assertEqual(judged_slots("大丈夫、部長には私から話しておくから。",
                                      "Không sao đâu, tôi sẽ nói chuyện với trưởng phòng cho em.", *SAKI, "ja"), [])
        # the rest of the line is still judged: "sẽ" of the speaker's offer (話しておく) is reported
        self.assertEqual(wrong("大丈夫、部長には私から話しておくから。",
                               "Không sao đâu, em sẽ nói chuyện với trưởng phòng cho chị.", SAKI), ["em"])


def frames_with_pair(source: str, pair, lang: str = "ja") -> list[tuple[str, str | None, str | None]]:
    return [(f.kind, f.agent, f.patient) for f in source_frames(source, lang, *pair) if not f.kind.endswith("_ref")]


class E2eAddressTests(unittest.TestCase):
    """tools.e2e_check's forms-of-address check between the demo siblings is role-aware."""

    def test_sera_line_is_wrong_and_correct_versions_are_ok(self):
        from tools import e2e_check as e  # noqa: PLC0415 (pulls in cv2 / psutil)
        from tools.demo_text_window import JA_TALK  # noqa: PLC0415
        speaker, line = JA_TALK[2]
        self.assertEqual((speaker, line), ("セラ", SERA_LINE))
        self.assertEqual(e.address_of(speaker, "Anh xin lỗi nhé. Anh giúp em tìm cùng được không?", line), "wrong")
        self.assertEqual(len(e.address_roles(speaker, line, "Anh xin lỗi nhé. Anh giúp em tìm cùng được không?")), 1)
        for ok in ("Em xin lỗi nhé. Anh giúp em tìm cùng được không?", "Xin lỗi anh nhé. Anh giúp em tìm cùng được không?"):
            self.assertEqual(e.address_of(speaker, ok, line), "ok")
        self.assertEqual(e.address_of(speaker, "Anh xin lỗi nhé."), "ok")  # without the source: words only
        self.assertEqual(e.address_of("レン", "Tao đi trước, mày theo sau.", JA_TALK[3][1]), "ok")
        o = [{"scene": "ja_talk_3", "cycle": 0, "lines": list(JA_TALK[2]), "llm_eligible": True, "tier_seq": {},
              "tiers": {"セラ": {"llm": "Sera"},
                        line: {"llm": "Anh xin lỗi nhé. Anh giúp em tìm cùng được không?", "speaker": "セラ"}}}]
        r = e.llm_report(o)
        self.assertEqual((r["address_wrong"], r["address_role_wrong"], r["address_n"]), (1, 1, 1))
        self.assertEqual(len(r["pages"][0]["roles"]), 1)


if __name__ == "__main__":
    unittest.main()
