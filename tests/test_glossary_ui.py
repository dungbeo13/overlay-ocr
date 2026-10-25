"""Tests for app.ui.glossary_editor (with its pronoun picker, app.ui.pronoun_picker, and the notes
helper app.address_notes), the tray's profile / LLM menus and GameGuard.foreground_changed.

Run: python -m unittest tests.test_glossary_ui -v

The logic tests run on Qt's offscreen platform in this process. NativeEditorTests (capture-exclusion
readback on real HWNDs, real fonts for the preview PNG) run in a child process on the windows platform
(WindowsPlatformSubprocessTests), together with the other classes. Nothing is activated there: HWNDs are
created hidden, popups get synthetic Show events, one tool window is shown off-screen without
activation, and the preview is rendered with QWidget.grab() into bench/results/preview/. The child is
skipped while an anti-cheat game owns the foreground, or with SCREEN_TRANSLATOR_SKIP_GUI_TESTS=1.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from ctypes import wintypes
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt
from PySide6.QtGui import QGuiApplication, QShortcut, QShowEvent
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QStyleOptionViewItem, QWidget

from app.address_notes import (
    AddressNotes,
    AddressRule,
    category_understood,
    join_word,
    listener_problem,
    notes_own,
    pair_infos,
    parse_notes,
    render_notes,
    rule_problems,
    rule_target,
    self_problem,
    split_word,
    suggest_call,
    suggest_self,
    word_problem,
)
from app.config import Config
from app.profiles import DEFAULT_PROFILE, Profile, ProfileStore, normalize_source, profile_from_dict
from app.translate.contextual import Character, Term, TranslationContext
from app.translate.llm_prompt import address_pair

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PREVIEW_DIR = PROJECT_ROOT / "bench" / "results" / "preview"
NATIVE = os.environ.get("SCREEN_TRANSLATOR_NATIVE_TESTS") == "1"

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetForegroundWindow.restype = wintypes.HWND


def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([sys.argv[0]])
    from app import qt_gc

    qt_gc.install(app)  # as in app.main: worker threads never run a gc collection (Qt wrappers in the garbage)
    return app


def on_windows_platform() -> bool:
    qapp()
    return QGuiApplication.platformName() == "windows"


def pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        time.sleep(0.003)


def flush_deletes() -> None:
    """Run pending deleteLater()s now: outside a running event loop processEvents() leaves them, and
    widgets with capture-exclusion filters must not survive until interpreter shutdown."""
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    pump(10)


def sample_profile(name: str = "Hải đăng & Phù thủy") -> Profile:
    return Profile(
        name=name,
        processes=["RPGGame.exe", "launcher.exe"],
        src_lang="ja",
        style="Giọng phiêu lưu kỳ ảo, lời thoại tự nhiên; giữ tên riêng theo phiên âm Latin; không dùng hậu tố -san.",
        terms=[
            Term("セラ", "Sera", "person", "nữ chính"),
            Term("レン", "Ren", "person"),
            Term("魔導書", "ma đạo thư", "item"),
            Term("聖騎士団", "Thánh Kỵ Sĩ Đoàn", "org"),
            Term("星詠み", "Tinh Vịnh", "skill", "kỹ năng tiên tri của Sera"),
            Term("エルデン王国", "Vương quốc Elden", "place", auto=True),
            Term("黒き森", "Rừng Đen", "place", auto=True),
            Term("灯台守", "người gác hải đăng", "term", auto=True),
        ],
        characters=[
            Character("セラ", "Sera", "nữ", "em", "17 tuổi, em gái nuôi của Ren; gọi Ren là 'anh', xưng 'em'; "
                                                   "với người lạ xưng 'tôi'"),
            Character("レン", "Ren", "nam", "tôi", "gọi Sera là 'em', xưng 'anh'; với thầy Garo xưng 'con'"),
            Character("ガロ", "Garo", "nam", "ta", "thầy của Ren, giọng già dặn; gọi Ren là 'con', xưng 'ta'"),
        ],
    )


def blue_archive_like() -> list[Character]:
    """The shapes a Blue Archive profile uses (research_notes/.../blue_archive.md): students "em" / "Sensei",
    Sensei "tôi", adults "tôi" / "ngài", an antagonist "ta" / "ngươi", asides on the word, a group the app
    does not read ("học sinh") and a name that is not a character (Noa)."""
    return [
        Character("先生", "Sensei", "", "tôi", "Người chơi, cố vấn của Schale. Gọi học sinh là 'em'."),
        Character("ホシノ", "Hoshino", "nữ", "em",
                  "Học sinh năm ba Abydos, uể oải; xưng 'em' (おじさん khi đùa); gọi Sensei là 'Sensei'"),
        Character("アロナ", "Arona", "nữ", "em", "AI của Máy tính bảng Shittim. Gọi Sensei là 'Sensei' (先生), xưng 'em'."),
        Character("ユウカ", "Yuuka", "nữ", "em",
                  "Thủ quỹ Millennium, nghiêm khắc chuyện tiền. Gọi Sensei là 'Sensei', xưng 'em'; gọi Noa là 'Noa'."),
        Character("黒服", "Hắc Phục", "nam", "tôi", "Kẻ bí ẩn của Gematria; gọi Sensei là 'ngài', xưng 'tôi'"),
        Character("ベアトリーチェ", "Beatrice", "nữ", "ta", "Kẻ thù; gọi Sensei là 'ngươi', xưng 'ta'"),
    ]


def note_fixtures() -> list[tuple[str, list[Character]]]:
    """Every character-notes shape in the repo: the bench scenes, this file's sample profile, the e2e demo
    profile (tools.e2e_check), bench/roles_llm.py's generated notes, the shapes of tests/test_llm.py and
    tests/test_profiles.py, Blue-Archive-like notes, and every profile in data/profiles/ and
    profiles/templates/ (the Blue Archive profile once it exists)."""
    out: list[tuple[str, list[Character]]] = []
    for path in sorted((PROJECT_ROOT / "bench" / "data" / "translate" / "scenarios").glob("*.json")):
        chars = json.loads(path.read_text(encoding="utf-8")).get("characters") or []
        if chars:
            out.append((path.name, [Character(c["name"], c.get("name_vi", ""), c.get("gender", ""),
                                              c.get("self_pronoun", ""), c.get("notes", "")) for c in chars]))
    out.append(("sample_profile", sample_profile().characters))
    from tools.e2e_check import E2E_CHARACTERS
    out.append(("e2e demo", [Character(*c) for c in E2E_CHARACTERS]))
    out.append(("bench.roles_llm", [Character("A", "A", "", "em", "gọi B là 'anh', xưng 'em'"),
                                    Character("B", "B", "", "anh", "gọi A là 'em', xưng 'anh'")]))
    out.append(("test_llm shop", [Character("Lily", "Lily", "nữ", "", "Xưng 'em', gọi khách là 'anh'."),
                                  Character("Carter", "Carter", "nam", "tôi")]))
    out.append(("test_llm kenji", [
        Character("セラ", "Sera", "nữ", "em", "Em gái của Ren. Gọi Ren là 'anh', xưng 'em'."),
        Character("レン", "Ren", "nam", "anh", "Anh trai của Sera. Gọi Sera là 'em', xưng 'anh'. "
                                               "Gọi Kenji là 'mày', xưng 'tao'."),
        Character("ケンジ", "Kenji", "nam", "tao", "Bạn của Ren. Gọi Ren là 'mày', xưng 'tao'.")]))
    out.append(("test_llm school", [
        Character("竜二", "Ryuuji", "nam", "tao", "Bạn thân của Kenta, gọi Kenta là 'mày'."),
        Character("健太", "Kenta", "nam", "tao"),
        Character("春香", "Haruka", "nữ", "em", "Gọi Souta là 'anh'."),
        Character("美咲", "Misaki", "nữ", "tớ", "Bạn cùng lớp của Haruka.")]))
    out.append(("test_profiles", [Character("セラ", "Sera", "nữ", "em", "gọi Ren là 'cậu', xưng 'tớ'"),
                                  Character("レン", "Ren", "nam", "tao", "gọi Sera là 'em'; với thầy xưng 'con'")]))
    out.append(("blue archive like", blue_archive_like()))
    out.append(("odd shapes", [
        Character("A", "Anh Hai", "nam", "", "thường gọi Bé là 'em'. 20 tuổi và gọi Út là 'em' (út cưng); "
                                             "calls Bé 'sis'. Xưng 'anh'. Xưng 'tao' khi giận"),
        Character("B", "Bé", "nữ", "em", "Với Anh Hai thì xưng 'em'; gọi Anh Hai là “anh” hoặc 'anh hai'"),
        Character("C", "Út", "nữ", "", "gọi mọi người là 'mọi người'（ít nói）, xưng 'em'")]))
    for folder in (PROJECT_ROOT / "data" / "profiles", PROJECT_ROOT / "profiles" / "templates"):
        for path in sorted(folder.rglob("*.json")) if folder.is_dir() else []:
            try:
                profile = profile_from_dict(json.loads(path.read_text(encoding="utf-8-sig")), path.stem)
            except ValueError:
                continue
            if profile.characters:
                out.append((str(path.relative_to(PROJECT_ROOT)), profile.characters))
    return out


_FORMAT_WORDS = {"gọi", "là", "xưng", "với", "và", "thì", "còn", "nhưng"}


def _content_words(text: str) -> set[str]:
    """The words of a note other than the notes format's own (gọi / là / xưng ...), casefolded."""
    return set(re.findall(r"[^\W_]+", text.casefold())) - _FORMAT_WORDS


def _pairs(chars) -> dict:
    """address_pair for every (speaker, listener or None) of `chars`."""
    ctx = TranslationContext(characters=tuple(chars))
    return {(s.name, l.name if l else None): address_pair(s.name, l.name if l else None, ctx)
            for s in chars for l in (*chars, None) if l is None or l.name != s.name}


class AddressNotesTests(unittest.TestCase):
    """app.address_notes: structured address <-> the notes text address_pair reads."""

    def test_every_note_shape_round_trips(self):
        """Regenerating the notes of every fixture keeps what address_pair makes of them for every
        speaker / listener, keeps every word of the note, and is a fixed point."""
        fixtures = note_fixtures()
        self.assertGreaterEqual(len(fixtures), 18)
        for name, chars in fixtures:
            regenerated = [replace(c, notes=render_notes(parse_notes(c.notes))) for c in chars]
            self.assertEqual(_pairs(regenerated), _pairs(chars), name)
            for before, after in zip(chars, regenerated):
                self.assertLessEqual(_content_words(before.notes), _content_words(after.notes), (name, before.notes))
                self.assertEqual(render_notes(parse_notes(after.notes)), after.notes, (name, after.notes))
                self.assertEqual(parse_notes(after.notes), parse_notes(before.notes), (name, before.notes))

    def test_parse_shapes(self):
        an = parse_notes("17 tuổi, em gái nuôi của Ren; gọi Ren là 'anh', xưng 'em'; với người lạ xưng 'tôi'")
        self.assertEqual(an, AddressNotes("17 tuổi, em gái nuôi của Ren", "", (
            AddressRule("Ren", "anh", "em"), AddressRule("người lạ", "", "tôi"))))
        an = parse_notes("Anh trai của Sera. Gọi Sera là 'em' (khi bực: 'mày'), xưng 'anh' (khi bực: 'tao').")
        self.assertEqual(an.rules, (AddressRule("Sera", "em (khi bực: 'mày')", "anh (khi bực: 'tao')"),))
        self.assertEqual(an.other, "Anh trai của Sera")
        an = parse_notes("22 tuổi, nhân viên. Xưng 'em', gọi khách là 'anh' hoặc 'quý khách'.")
        self.assertEqual((an.self_word, an.rules), ("em", (AddressRule("khách", "anh (hoặc 'quý khách')"),)))
        an = parse_notes("Xưng 'bà' (ばあちゃん), gọi Minato là 'cháu'.")
        self.assertEqual((an.self_word, an.rules, an.other), ("bà (ばあちゃん)", (AddressRule("Minato", "cháu"),), ""))
        self.assertEqual(parse_notes("Gọi ông Genzou là 'ông', xưng 'cháu'.").rules,
                         (AddressRule("ông Genzou", "ông", "cháu"),))
        self.assertEqual(parse_notes("Xưng 'cô', gọi Mio là 'em', gọi Shou là 'em'.").rules,
                         (AddressRule("Mio", "em"), AddressRule("Shou", "em")))
        # Not at the start of a segment, English, a second standalone "xưng": stay text (address_pair still reads them).
        an = parse_notes("thường gọi Bé là 'em'. 20 tuổi và gọi Út là 'em'; calls Bé 'sis'. Xưng 'anh'. Xưng 'tao' khi giận")
        self.assertEqual(an.rules, (AddressRule("Út", "em"),))
        self.assertEqual(an.self_word, "anh")
        self.assertEqual(an.other, "thường gọi Bé là 'em'. 20 tuổi; calls Bé 'sis'; Xưng 'tao' khi giận")
        # such leftovers keep their place around the rows (the one before them stays before them), so
        # address_pair still meets the same "xưng" / "gọi Bé là" first
        self.assertEqual(an.head, "thường gọi Bé là 'em'. 20 tuổi")
        self.assertEqual(render_notes(an), "thường gọi Bé là 'em'. 20 tuổi; xưng 'anh'; gọi Út là 'em'; calls Bé 'sis'; "
                                           "Xưng 'tao' khi giận")
        # Typographic quotes, "thì", full-width brackets.
        self.assertEqual(parse_notes("Với Ren thì xưng “em”; gọi Ren là ‘anh’（お兄ちゃん）").rules,
                         (AddressRule("Ren", "", "em"), AddressRule("Ren", "anh (お兄ちゃん)")))
        self.assertEqual(parse_notes(""), AddressNotes())
        self.assertEqual(parse_notes("Chỉ là mô tả, không có xưng hô."), AddressNotes("Chỉ là mô tả, không có xưng hô"))

    def test_source_order_is_kept(self):
        """Where address_pair depends on the order (its first "xưng", the first "gọi X là" per listener), a
        rendering keeps it, also after an edit of the free text only or a new row."""
        sensei, ren = Character("先生", "Sensei", "", "tôi"), Character("レン", "Ren", "nam", "anh")
        mio = Character("ミオ", "Mio", "nữ", "", "Hay nũng nịu gọi Sensei là 'ngài', xưng 'tôi'; gọi Ren là 'anh', xưng 'em'")
        an = parse_notes(mio.notes)
        self.assertEqual(an.head, "Hay nũng nịu gọi Sensei là 'ngài', xưng 'tôi'")
        edited = render_notes(replace(an, other=an.other + " (vui tính)"))
        self.assertEqual(edited, "Hay nũng nịu gọi Sensei là 'ngài', xưng 'tôi' (vui tính); gọi Ren là 'anh', xưng 'em'")
        before = _pairs([mio, sensei, ren])
        self.assertEqual(_pairs([replace(mio, notes=edited), sensei, ren]), before)
        added = render_notes(replace(an, rules=(*an.rules, AddressRule("Zed", "bạn"))))
        self.assertEqual({k: v for k, v in _pairs([replace(mio, notes=added), sensei, ren]).items()}, before)
        # a standalone "xưng" after a row's own word stays after it
        an = parse_notes("gọi Ren là 'anh', xưng 'em'; xưng 'tôi' khi lễ phép")
        self.assertTrue(an.self_after)
        self.assertEqual(notes_own(render_notes(an)), "em")
        an = parse_notes("gọi Ren là 'anh', xưng 'em'; xưng 'tôi' với người lạ.")  # a row for strangers
        self.assertEqual(an.rules, (AddressRule("Ren", "anh", "em"), AddressRule("người lạ", "", "tôi")))
        self.assertEqual(notes_own(render_notes(an)), "em")
        # line breaks: a rule on its own line is a rule, and "xưng" on the next line is not the row's own word
        an = parse_notes("17 tuổi\ngọi Ren là 'anh'\nxưng 'em'")
        self.assertEqual((an.rules, an.self_word, an.other), ((AddressRule("Ren", "anh"),), "em", "17 tuổi"))
        an = parse_notes("Red Winter\nXưng 'em', gọi Ren là 'anh' (お兄ちゃん)\nXưng 'tôi', gọi khách là 'anh'")
        self.assertEqual(an.rules, (AddressRule("Ren", "anh (お兄ちゃん)"), AddressRule("khách", "anh")))
        self.assertEqual(notes_own(render_notes(an)), "em")
        # a rule written inside brackets leaves no "(; )" behind
        an = parse_notes("Hay cười (gọi Mio là 'em').")
        self.assertEqual((an.other, an.rules), ("Hay cười", (AddressRule("Mio", "em"),)))
        self.assertNotIn("(", render_notes(an))
        self.assertEqual(parse_notes("Hay cười（gọi Mio là 'em'）").other, "Hay cười")

    def test_random_recombinations_keep_the_pairs(self):
        """Random recombinations of the repo's note segments (with ; . , và, spaces and line breaks between
        them): rendering, editing the free text and adding a row never move what address_pair gives, and
        parse_notes is a fixed point."""
        import random
        fixtures = note_fixtures()
        segs = list(dict.fromkeys(s.strip() for _n, chars in fixtures for c in chars
                                  for s in re.split(r"[;.]", c.notes) if s.strip()))
        known = {}
        for _n, chars in fixtures:
            for c in chars:
                known.setdefault((c.name_vi or c.name).casefold(), c)
        rnd = random.Random(20261025)
        for _trial in range(600):
            text = "".join(rnd.choice(segs) + rnd.choice(["; ", ". ", ", ", " và ", " ", "\n"])
                           for _ in range(rnd.randint(1, 4))).strip()
            sp = Character("話者", "Speaker", rnd.choice(["nam", "nữ", ""]), rnd.choice(["", "em", "tôi"]), text)
            others = [c for k, c in known.items() if (k in text.casefold() or c.name in text) and c.name != sp.name][:12]
            an = parse_notes(text)
            want = _pairs([sp, *others])
            out = render_notes(an)
            self.assertEqual(_pairs([replace(sp, notes=out), *others]), want, (text, out))
            self.assertEqual(render_notes(parse_notes(out)), out, text)
            self.assertLessEqual(_content_words(text), _content_words(out), text)
            if an.other:
                out = render_notes(replace(an, other=an.other + " (vui tính)"))
                self.assertEqual(_pairs([replace(sp, notes=out), *others]), want, (text, out))
            out = render_notes(replace(an, rules=(*an.rules, AddressRule("Zed", "bạn"))))
            got = _pairs([replace(sp, notes=out), *others, Character("ゼド", "Zed", "", "")])
            self.assertEqual({k: v for k, v in got.items() if k in want}, want, (text, out))

    def test_unquoted_phrases(self):
        from app.address_notes import convert_unquoted, unquoted_phrases
        an = parse_notes("thầy của Ren; Gọi Ren là con, xưng ta")
        self.assertEqual(an.rules, ())  # no quotes: address_pair reads nothing...
        self.assertEqual(unquoted_phrases(an.other), ["Gọi Ren là con, xưng ta"])  # ...the editor says so
        got, own = convert_unquoted(an, "")
        self.assertEqual((got.rules, got.other, own), ((AddressRule("Ren", "con", "ta"),), "thầy của Ren", ""))
        ren = Character("レン", "Ren", "nam", "em")
        garo = Character("ガロ", "Garo", "nam", own, render_notes(got))  # no Tự xưng: the row's "xưng 'ta'"
        self.assertEqual(address_pair("ガロ", "レン", TranslationContext(characters=(garo, ren))), ("ta", "con", True))
        self.assertEqual(unquoted_phrases("Lễ phép, xưng hô lịch sự. Gọi Ren là anh trai."), [])
        self.assertEqual(unquoted_phrases("gọi Aris là Aris; xưng em"), ["gọi Aris là Aris", "xưng em"])
        self.assertEqual(unquoted_phrases("gọi Ren là 'anh'"), [])  # quoted: a real rule
        got, own = convert_unquoted(AddressNotes(other="nhút nhát, xưng em"), "tôi")
        self.assertEqual((got.other, got.self_word, own), ("nhút nhát", "em", "tôi"))  # Tự xưng is kept

    def test_rename_listener(self):
        from app.address_notes import rename_listener
        notes = "Em gái của Ren. Gọi Ren là 'anh', xưng 'em'; với Ren thì xưng 'em'; calls Ren 'bro'; Renji là bạn"
        self.assertEqual(rename_listener(notes, "Ren", "Len"),
                         "Em gái của Ren. Gọi Len là 'anh', xưng 'em'; với Len thì xưng 'em'; calls Len 'bro'; "
                         "Renji là bạn")  # only the rules' listener; the description stays
        self.assertEqual(rename_listener("gọi ông Genzou là 'ông'", "Genzou", "Gen"), "gọi ông Gen là 'ông'")
        self.assertEqual(rename_listener(notes, "Ren", "Ren"), notes)

    def test_render(self):
        an = AddressNotes("17 tuổi.", "em (ばあちゃん)", (
            AddressRule("Ren", "anh (お兄ちゃん)", "em"), AddressRule("khách", "quý khách"),
            AddressRule("Garo", "", "con"), AddressRule("", "", ""),  # blank: dropped
            AddressRule("Kenji", ""),  # no word: left out (the editor reports it)
            AddressRule("Bé", "anh hoặc chị")))  # not one word: left out
        self.assertEqual(render_notes(an), "17 tuổi; xưng 'em' (ばあちゃん); gọi Ren là 'anh' (お兄ちゃん), xưng 'em'; "
                                           "gọi khách là 'quý khách'; với Garo xưng 'con'")
        self.assertEqual(render_notes(AddressNotes()), "")
        self.assertEqual(split_word("anh (hoặc 'tiền bối')"), ("anh", "hoặc 'tiền bối'"))
        self.assertEqual(split_word("  em  "), ("em", ""))
        self.assertEqual(join_word("em", "khi bực: 'mày'"), "em (khi bực: 'mày')")

    def _speaker_pair(self, speaker: Character, listener: str | None, others: list[Character]):
        an = parse_notes(speaker.notes)
        chars = [replace(speaker, notes=render_notes(an)), *others]
        return address_pair(speaker.name, listener, TranslationContext(characters=tuple(chars)))

    def test_structured_rules_give_the_intended_pairs(self):
        """Rows -> notes -> address_pair: the pair the user picked, for the common pairs."""
        def rows(self_pronoun: str, *rules: AddressRule, self_word: str = "", other: str = "") -> tuple[str, str]:
            return self_pronoun, render_notes(AddressNotes(other, self_word, rules))

        sensei = Character("先生", "Sensei", "", "tôi")
        hoshino = Character("ホシノ", "Hoshino", "nữ", "em")
        cases = [
            # (speaker, its rows, listener, others, expected)
            ("ホシノ", rows("em", AddressRule("Sensei", "Sensei")), "先生", [sensei], ("em", "sensei", True)),
            ("先生", rows("tôi", AddressRule("Hoshino", "em")), "ホシノ", [hoshino], ("tôi", "em", True)),
            ("竜二", rows("tao", AddressRule("Kenta", "mày")), "健太", [Character("健太", "Kenta", "nam", "tao")],
             ("tao", "mày", True)),
            ("湊", rows("cháu", AddressRule("Chiyo", "bà (おばあちゃん)")), "千代", [Character("千代", "Chiyo", "nữ", "bà")],
             ("cháu", "bà", True)),
            ("美緒", rows("em", AddressRule("cô Mori", "cô")), "森先生", [Character("森先生", "Mori", "nữ", "cô")],
             ("em", "cô", True)),
            ("翔", rows("em", AddressRule("Garo", "thầy")), "ガロ", [Character("ガロ", "Garo", "nam", "ta")],
             ("em", "thầy", True)),
            ("Lily", rows("em", AddressRule("khách", "anh")), None, [], ("em", "anh", True)),
            ("Lily", rows("em", AddressRule("khách", "anh")), "Carter", [Character("Carter", "Carter", "nam", "tôi")],
             ("em", "anh", True)),  # a category rule covers a character the notes do not name
            ("Mark", rows("tôi", AddressRule("mọi người", "quý khách")), None, [], ("tôi", "quý khách", True)),
            # Tự xưng empty: the row's own word (the first "xưng" of the notes) is the speaker's
            ("セラ", rows("", AddressRule("Ren", "anh", "em")), "レン", [Character("レン", "Ren", "nam", "anh")],
             ("em", "anh", True)),
            ("Lily", rows("", AddressRule("khách", "anh"), self_word="em"), None, [], ("em", "anh", True)),
            # derived from the listener's own word when no row names it
            ("竜二", rows("tao"), "健太", [Character("健太", "Kenta", "nam", "tao")], ("tao", "mày", True)),
        ]
        for speaker_name, (self_pronoun, notes), listener, others, expected in cases:
            speaker = Character(speaker_name, speaker_name, "", self_pronoun, notes)
            own, other, defined = self._speaker_pair(speaker, listener, others)
            # address_pair decides the case of a title ("Sensei" / "sensei"); the word is the user's
            self.assertEqual((own, other.casefold() if other else other, defined), expected, (speaker_name, notes))

    def test_what_the_notes_format_cannot_express(self):
        """Limits of address_pair's notes format (the editor's preview says so)."""
        garo = Character("ガロ", "Garo", "nam", "ta")
        self.assertIs(rule_target("Garo", [garo]), garo)
        self.assertIs(rule_target("ông Garo", [garo]), garo)
        # a title other than an honorific address_pair strips does not name the character; rule_target agrees
        # with address_pair whatever its honorific list is
        s = Character("レン", "Ren", "nam", "em", render_notes(AddressNotes(rules=(AddressRule("thầy Garo", "thầy"),))))
        matched = self._speaker_pair(s, "ガロ", [garo])[2]
        self.assertEqual(rule_target("thầy Garo", [garo]) is garo, matched)
        # a per-listener own word is not used when the character has a Tự xưng (one own word for everyone)
        kenji = Character("ケンジ", "Kenji", "nam", "tao")
        s = Character("レン", "Ren", "nam", "anh", render_notes(AddressNotes(rules=(AddressRule("Kenji", "mày", "tao"),))))
        self.assertEqual(self._speaker_pair(s, "ケンジ", [kenji]), ("anh", "mày", True))
        # only some group nouns are rules for anyone; any other listener that is not a character is only a note
        self.assertTrue(all(category_understood(c) for c in ("khách", "người lạ", "mọi người", "người khác")))
        self.assertFalse(category_understood("lớp trưởng"))
        self.assertFalse(category_understood("Kenji"))
        s = Character("Lily", "Lily", "nữ", "em", render_notes(AddressNotes(rules=(AddressRule("lớp trưởng", "cậu"),))))
        self.assertEqual(self._speaker_pair(s, None, []), (None, None, False))
        # two groups with different words cancel each other
        s = Character("Lily", "Lily", "nữ", "em", render_notes(AddressNotes(rules=(
            AddressRule("khách", "anh"), AddressRule("người lạ", "bạn")))))
        self.assertEqual(self._speaker_pair(s, None, []), (None, None, False))
        self.assertEqual(notes_own("gọi Ren là 'anh', xưng 'Em'"), "em")
        self.assertIsNone(notes_own("17 tuổi"))

    def test_validation(self):
        self.assertIsNone(word_problem(""))
        self.assertIsNone(word_problem("anh (お兄ちゃん)"))
        self.assertIsNone(word_problem("bổn cô nương"))
        for bad, part in (("anh hoặc chị", "mỗi ô chỉ một từ"), ("anh/chị", "mỗi ô chỉ một từ"),
                          ("'anh'", "dấu nháy"), ("(お兄ちゃん)", "thiếu từ"), ("anh (x", "dấu ngoặc"),
                          ("a" * 21, "dài quá")):
            self.assertIn(part, word_problem(bad) or "", bad)
        self.assertIsNone(listener_problem("ông Genzou"))
        self.assertIn("dấu câu", listener_problem("Ren, Sera"))
        self.assertIsNone(self_problem("em"))
        self.assertIn("một từ", self_problem("em/tớ"))
        an = AddressNotes(rules=(
            AddressRule("Ren", "anh"), AddressRule("", "", ""), AddressRule("Kenji", ""), AddressRule("", "em"),
            AddressRule("ren", "em"), AddressRule("Sera", "em"), AddressRule("Garo", "", "con"),
            AddressRule("Garo", "thầy")))
        problems = rule_problems(an, Character("セラ", "Sera"))
        self.assertEqual([r for r, _m in problems], [2, 3, 4, 5])
        text = " | ".join(m for _r, m in problems)
        for part in ("chưa có từ nào", "chưa chọn người nghe", "hai dòng", "chính nhân vật này"):
            self.assertIn(part, text)

    def test_suggestions(self):
        self.assertEqual(suggest_self("nữ", "30 tuổi, giáo viên chủ nhiệm"), "cô")
        self.assertEqual(suggest_self("nam", "giáo viên"), "thầy")
        self.assertEqual(suggest_self("nữ", "72 tuổi, bà nội của Minato"), "bà")
        self.assertEqual(suggest_self("nam", "9 tuổi, cháu nội của bà Chiyo"), "cháu")
        self.assertEqual(suggest_self("nam", "17 tuổi, đàn anh của Haruka, ở hội học sinh"), "anh")
        self.assertEqual(suggest_self("nữ", "Học sinh năm ba Abydos"), "em")
        self.assertEqual(suggest_self("", ""), "tôi")
        ren, sera = Character("レン", "Ren", "nam", "anh"), Character("セラ", "Sera", "nữ", "")
        self.assertEqual(suggest_call("em", ren), "anh")  # the listener's own word
        self.assertEqual(suggest_call("em", sera), "chị")  # from the speaker's word and the gender
        self.assertEqual(suggest_call("tao", Character("健太", "Kenta", "nam", "tao")), "mày")
        self.assertEqual(suggest_call("tớ", Character("x", "X", "")), "cậu")
        self.assertEqual(suggest_call("cháu", Character("x", "X", "nữ")), "bà")
        self.assertEqual(suggest_call("anh", sera), "em")
        self.assertEqual(suggest_call("tôi", None, "khách"), "quý khách")
        self.assertEqual(suggest_call("em", None, "Sensei"), "Sensei")
        self.assertEqual(suggest_call("tôi", Character("x", "X", "")), "bạn")
        self.assertEqual(suggest_call("", Character("x", "X", "")), "")
        # an elder / teacher by its notes or own word gets that title; no peer word for someone whose own word
        # gives no pair with the speaker's (a classmate "tớ" toward Sera "em" or Ren "tôi")
        self.assertEqual(suggest_call("tớ", Character("ガロ", "Garo", "nam", "ta", "thầy của Ren, giọng già dặn")), "thầy")
        self.assertEqual(suggest_call("em", Character("森", "Mori", "nữ", "", "cô giáo chủ nhiệm")), "cô")
        self.assertEqual(suggest_call("cháu", Character("x", "Ông Ba", "nam", "ông")), "ông")
        self.assertEqual(suggest_call("tớ", Character("セラ", "Sera", "nữ", "em")), "")
        self.assertEqual(suggest_call("tớ", Character("レン", "Ren", "nam", "tôi")), "")

    def test_pair_infos(self):
        chars = [Character("Lily", "Lily", "nữ", "em", "gọi khách là 'anh'; gọi Mark là 'chú'"),
                 Character("Mark", "Mark", "nam", "tôi"), Character("Carter", "Carter", "nam", "tôi"),
                 Character("Ben", "Ben", "nam", "anh"), Character("Tom", "Tom", "nam", "em")]
        got = {(i.listener.name if i.listener else None): (i.own, i.other, i.defined, i.source)
               for i in pair_infos(chars[0], chars)}
        self.assertEqual(got, {"Mark": ("em", "chú", True, "rule"), "Carter": ("em", "anh", True, "category"),
                               "Ben": ("em", "anh", True, "derived"),  # from Ben's own "anh"
                               "Tom": ("em", "anh", True, "category"),  # "em … em" is no pair address_pair derives
                               None: ("em", "anh", True, "category")})
        self.assertEqual(pair_infos(Character("", ""), chars), [])


class _EditorFixture(unittest.TestCase):
    def setUp(self):
        qapp()
        # Cleanups run last-in first-out: this runs after every deleteLater a test registers, so no
        # capture-excluded widget outlives its test (or reaches interpreter shutdown).
        self.addCleanup(flush_deletes)
        self.dir = Path(tempfile.mkdtemp(prefix="st-glossary-ui-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.store = ProfileStore(self.dir, rescan_s=0.0)
        self.profile = sample_profile()
        self.store.save(self.profile)
        self.editors = []
        self.addCleanup(lambda: [ed.deleteLater() for ed in self.editors])

    def editor(self, name: str | None = None):
        from app.ui.glossary_editor import GlossaryEditor
        ed = GlossaryEditor(self.store, name or self.profile.name)
        ed.ask_before_discard = False
        self.editors.append(ed)
        return ed


# --- editor logic (offscreen OK) --------------------------------------------------------------------

class EditorTests(_EditorFixture):
    def test_loads_profile(self):
        from app.ui.glossary_editor import _AUTO_BG, _ROLE_KEY, T_KIND, T_SOURCE, T_TARGET
        ed = self.editor()
        self.assertIn(self.profile.name, ed.windowTitle())
        self.assertEqual([ed.tabs.tabText(i).replace("&&", "&") for i in range(ed.tabs.count())],
                         ["Thuật ngữ", "Nhân vật", "Văn phong & game"])  # "&&": a literal '&', not a mnemonic
        t = ed.terms_table
        self.assertEqual([t.horizontalHeaderItem(i).text() for i in range(4)], ["Gốc", "Tiếng Việt", "Loại", "Ghi chú"])
        self.assertEqual(t.rowCount(), 8)
        self.assertEqual((t.item(0, T_SOURCE).text(), t.item(0, T_TARGET).text()), ("セラ", "Sera"))
        self.assertEqual(t.item(0, T_KIND).text(), "Nhân vật")  # kinds shown in Vietnamese
        self.assertEqual(t.item(0, T_KIND).data(_ROLE_KEY), "person")
        self.assertEqual([ed.is_auto(r) for r in range(8)], [False] * 5 + [True] * 3)
        for r in range(8):
            button = ed.confirm_button(r)
            if ed.is_auto(r):
                self.assertEqual(button.text(), "Xác nhận")
                self.assertEqual(t.item(r, T_SOURCE).background().color(), _AUTO_BG)  # visibly marked
            else:
                self.assertIsNone(button)
        c = ed.characters_table
        self.assertEqual([c.horizontalHeaderItem(i).text() for i in range(5)],
                         ["Tên gốc", "Tên tiếng Việt", "Giới tính", "Tự xưng", "Ghi chú"])
        self.assertEqual(c.rowCount(), 3)
        self.assertEqual(c.item(0, 2).text(), "Nữ")
        self.assertIn("gọi Ren là 'anh', xưng 'em'", ed.character_hint.text())
        self.assertEqual(ed.style_edit.toPlainText(), self.profile.style)
        self.assertEqual(ed.processes(), ["RPGGame.exe", "launcher.exe"])
        self.assertEqual(ed.lang_combo.currentData(), "ja")
        self.assertEqual((ed.save_button.text(), ed.cancel_button.text()), ("Lưu", "Hủy"))
        self.assertEqual(ed.pick_window_button.text(), "Thêm cửa sổ đang mở…")
        self.assertIn("3 gợi ý chưa xác nhận", ed.status_label.text())
        self.assertFalse(ed.is_dirty())

    def test_confirm_auto_row(self):
        ed = self.editor()
        ed.confirm_button(5).click()  # programmatic click on our own widget, not OS input
        self.assertFalse(ed.is_auto(5))
        self.assertIsNone(ed.confirm_button(5))
        self.assertTrue(ed.is_dirty())
        self.assertTrue(ed.save())
        stored = {t.source: t.auto for t in self.store.get(self.profile.name).terms}
        self.assertEqual(stored["エルデン王国"], False)
        self.assertEqual(stored["黒き森"], True)
        ed.confirm_all()
        self.assertTrue(ed.save())
        self.assertEqual([t.auto for t in self.store.get(self.profile.name).terms], [False] * 8)
        self.assertFalse(ed.confirm_all_button.isEnabled())

    def test_editing_a_suggestion_confirms_it(self):
        from app.ui.glossary_editor import T_TARGET
        ed = self.editor()
        ed.terms_table.item(6, T_TARGET).setText("Hắc Lâm")
        self.assertFalse(ed.is_auto(6))
        self.assertTrue(ed.save())
        t = next(t for t in self.store.get(self.profile.name).terms if t.source == "黒き森")
        self.assertEqual((t.target, t.auto), ("Hắc Lâm", False))

    def test_save_round_trip(self):
        from app.ui.glossary_editor import _ROLE_KEY, C_GENDER, C_NAME, C_NAME_VI, C_NOTES, C_PRONOUN, T_KIND, \
            T_NOTE, T_SOURCE, T_TARGET
        ed = self.editor()
        saved = []
        ed.saved.connect(saved.append)
        row = ed.add_term_row()
        t = ed.terms_table
        t.item(row, T_SOURCE).setText("  竜の涙 ")
        t.item(row, T_TARGET).setText("Lệ Rồng")
        t.item(row, T_KIND).setData(_ROLE_KEY, "item")
        t.item(row, T_NOTE).setText("vật phẩm hiếm")
        r = ed.add_character_row()
        c = ed.characters_table
        c.item(r, C_NAME).setText("ミナ")
        c.item(r, C_NAME_VI).setText("Mina")
        c.item(r, C_GENDER).setData(_ROLE_KEY, "nữ")
        c.item(r, C_PRONOUN).setData(_ROLE_KEY, "tớ")
        c.item(r, C_NOTES).setText("bạn thân của Sera; gọi Sera là 'cậu', xưng 'tớ'")
        ed.style_edit.setPlainText("Giọng nhẹ nhàng.")
        ed.lang_combo.setCurrentIndex(ed.lang_combo.findData("en"))
        self.assertEqual(ed.add_processes(["C:\\Games\\New Game\\NewGame.exe", "newgame.EXE", "rpggame.exe", " "]), 1)
        self.assertTrue(ed.is_dirty())
        self.assertTrue(ed.save())
        self.assertFalse(ed.is_dirty())
        self.assertEqual(saved, [self.profile.name])
        got = ProfileStore(self.dir).get(self.profile.name)
        self.assertEqual(got.terms[-1], Term("竜の涙", "Lệ Rồng", "item", "vật phẩm hiếm"))
        self.assertEqual(got.characters[-1], Character("ミナ", "Mina", "nữ", "tớ", "bạn thân của Sera; gọi Sera là 'cậu', xưng 'tớ'"))
        self.assertEqual(got.style, "Giọng nhẹ nhàng.")
        self.assertEqual(got.src_lang, "en")
        self.assertEqual(got.processes, ["RPGGame.exe", "launcher.exe", "NewGame.exe"])
        self.assertEqual(got.terms[:8], self.profile.terms)  # untouched rows unchanged
        self.assertEqual(ed.saved_profile(), got)

    def test_validation(self):
        from app.ui.glossary_editor import _ERROR_BG, C_NAME, T_SOURCE, T_TARGET
        ed = self.editor()
        t = ed.terms_table
        empty = ed.add_term_row()
        t.item(empty, T_TARGET).setText("không có gốc")  # dropped: no source
        dup = ed.add_term_row()
        t.item(dup, T_SOURCE).setText("ｾﾗ")  # half-width: the same source as row 0
        t.item(dup, T_TARGET).setText("Sela")
        missing = ed.add_term_row()
        t.item(missing, T_SOURCE).setText("海賊王")
        cdup = ed.add_character_row()
        ed.characters_table.item(cdup, C_NAME).setText("ガロ")
        self.assertFalse(ed.save())
        _profile, errors = ed.collect()
        text = " ".join(errors)
        self.assertIn("“ｾﾗ” bị trùng (dòng 1 và 10)", text)
        self.assertIn("“海賊王” (dòng 11) chưa có bản dịch", text)
        self.assertIn("Nhân vật “ガロ” bị trùng", text)
        self.assertEqual(t.item(dup, T_SOURCE).background().color(), _ERROR_BG)
        self.assertEqual(t.item(0, T_SOURCE).background().color(), _ERROR_BG)
        self.assertIn("bị trùng", ed.status_label.text())
        self.assertEqual(len(self.store.get(self.profile.name).terms), 8)  # nothing written
        ed.delete_term_rows([dup])
        t.item(missing - 1, T_TARGET).setText("Vua Hải Tặc")
        ed.delete_character_rows([cdup])
        self.assertTrue(ed.save(), ed.status_label.text())
        got = self.store.get(self.profile.name)
        self.assertEqual([x.source for x in got.terms][-1], "海賊王")
        self.assertNotIn("không có gốc", [x.target for x in got.terms])
        self.assertNotEqual(t.item(0, T_SOURCE).background().color(), _ERROR_BG)

    def test_deleted_suggestion_is_not_proposed_again(self):
        ed = self.editor()
        ed.delete_term_rows([7])  # 灯台守 (auto)
        self.assertTrue(ed.save())
        got = self.store.get(self.profile.name)
        self.assertNotIn("灯台守", [t.source for t in got.terms])
        self.assertIn(normalize_source("灯台守"), got.rejected)
        self.assertEqual(self.store.add_terms(got.name, [Term("灯台守", "người giữ đèn", "term")]), 0)
        # the user adds it back by hand: it leaves the rejected list
        ed2 = self.editor()
        from app.ui.glossary_editor import T_SOURCE, T_TARGET
        r = ed2.add_term_row()
        ed2.terms_table.item(r, T_SOURCE).setText("灯台守")
        ed2.terms_table.item(r, T_TARGET).setText("người gác đèn")
        self.assertTrue(ed2.save())
        self.assertNotIn(normalize_source("灯台守"), self.store.get(got.name).rejected)

    def test_auto_terms_added_while_open_are_kept(self):
        ed = self.editor()
        ed.delete_term_rows([6])  # 黒き森: deleted by the user
        self.assertEqual(self.store.add_terms(self.profile.name, [Term("ガイア", "Gaia", "place")]), 1)
        ed.style_edit.setPlainText("Mới.")
        self.assertTrue(ed.save())
        got = self.store.get(self.profile.name)
        sources = [t.source for t in got.terms]
        self.assertIn("ガイア", sources)  # the LLM's new term survived the editor's save
        self.assertNotIn("黒き森", sources)
        self.assertEqual(got.style, "Mới.")

    def test_suggestion_duplicating_a_character_is_dropped(self):
        from app.ui.glossary_editor import C_NAME, C_NAME_VI
        ed = self.editor()
        r = ed.add_character_row()
        ed.characters_table.item(r, C_NAME).setText("灯台守")
        ed.characters_table.item(r, C_NAME_VI).setText("Người gác đèn")
        self.assertTrue(ed.save())
        got = self.store.get(self.profile.name)
        self.assertNotIn("灯台守", [t.source for t in got.terms])
        self.assertIn("灯台守", [c.name for c in got.characters])

    def test_filter(self):
        ed = self.editor()
        ed.filter_edit.setText("SERA")
        hidden = [ed.terms_table.isRowHidden(r) for r in range(8)]
        self.assertEqual(hidden, [False, True, True, True, False, True, True, True])  # name or note match
        ed.add_term_row(edit=True)  # adding clears the filter
        self.assertEqual(ed.filter_edit.text(), "")
        self.assertFalse(any(ed.terms_table.isRowHidden(r) for r in range(9)))

    def test_missing_profile_edits_default(self):
        ed = self.editor("không tồn tại")
        self.assertIn("Mặc định", ed.windowTitle())  # the built-in "default" profile, in Vietnamese
        self.assertEqual(ed.terms_table.rowCount(), 0)
        ed.style_edit.setPlainText("x")
        self.assertTrue(ed.save())
        self.assertEqual(self.store.get(DEFAULT_PROFILE).style, "x")

    def test_window_picker(self):
        from app.ui.glossary_editor import WindowPicker
        p = WindowPicker(["A.exe"], windows=[("a.exe", "Alpha"), ("b.exe", "Bravo — " + "x" * 80)])
        self.addCleanup(p.deleteLater)
        self.assertEqual(p.list.count(), 2)
        self.assertIn("(đã có)", p.list.item(0).text())
        self.assertFalse(p.list.item(0).flags() & Qt.ItemFlag.ItemIsEnabled)
        self.assertTrue(p.list.item(1).text().endswith("…"))
        self.assertEqual(p.selected(), [])
        p.list.item(1).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(p.selected(), ["b.exe"])
        empty = WindowPicker([], windows=[])
        self.addCleanup(empty.deleteLater)
        self.assertEqual(empty.selected(), [])

    def test_cancel(self):
        ed = self.editor()
        ed.style_edit.setPlainText("không lưu")
        ed.reject()
        self.assertEqual(self.store.get(self.profile.name).style, self.profile.style)

    def test_character_without_source_name_is_not_dropped(self):
        """A player who cannot type セラ fills only "Tên tiếng Việt": saving must say so, not drop the row."""
        from app.ui.glossary_editor import _ERROR_BG, C_NAME, C_NAME_VI, C_PRONOUN
        ed = self.editor()
        r = ed.add_character_row()
        c = ed.characters_table
        c.item(r, C_NAME_VI).setText("Mina")
        c.item(r, C_PRONOUN).setText("tớ")
        self.assertFalse(ed.save())
        self.assertIn("Nhân vật dòng 4 (Mina) chưa có “Tên gốc”", ed.status_label.text())
        self.assertEqual(c.item(r, C_NAME_VI).background().color(), _ERROR_BG)
        self.assertEqual(c.currentRow(), r)
        self.assertEqual(len(self.store.get(self.profile.name).characters), 3)  # nothing written
        self.assertIn("ví dụ セラ", c.horizontalHeaderItem(C_NAME).toolTip())
        c.item(r, C_NAME).setText("ミナ")
        self.assertTrue(ed.save(), ed.status_label.text())
        self.assertEqual(self.store.get(self.profile.name).characters[-1].name_vi, "Mina")
        empty = ed.add_character_row()  # an untouched new row is just dropped
        self.assertTrue(ed.save())
        self.assertEqual(len(self.store.get(self.profile.name).characters), 4)
        self.assertGreater(empty, 0)

    def test_renaming_a_character_renames_it_in_the_others_rules(self):
        from app.ui.glossary_editor import C_NAME_VI, C_NOTES
        ed = self.editor()
        c = ed.characters_table
        c.setCurrentCell(1, 0)  # Ren
        c.item(1, C_NAME_VI).setText("Len")
        self.assertIn("Đã đổi “Ren” thành “Len” trong cách xưng hô của 2 nhân vật khác", ed.status_label.text())
        self.assertIn("gọi Len là 'anh', xưng 'em'", c.item(0, C_NOTES).text())  # Sera's rule
        self.assertIn("gọi Len là 'con'", c.item(2, C_NOTES).text())  # Garo's
        self.assertIn("thầy của Ren", c.item(2, C_NOTES).text())  # description untouched
        self.assertTrue(ed.save(), ed.status_label.text())
        ctx = self.store.get(self.profile.name).to_context()
        self.assertEqual(address_pair("セラ", "レン", ctx), ("em", "anh", True))
        self.assertEqual(address_pair("ガロ", "レン", ctx), ("ta", "con", True))
        c.setCurrentCell(0, 0)
        self.assertIn(("Len", "anh", "em"), [tuple(ed.pronoun_picker.rules_table.item(r, k).text() for k in range(3))
                                             for r in range(ed.pronoun_picker.rules_table.rowCount())])

    def test_character_filter(self):
        from app.ui.glossary_editor import C_NOTES
        ed = self.editor()
        ed.char_filter_edit.setText("garo")
        self.assertEqual([ed.characters_table.isRowHidden(r) for r in range(3)], [True, False, False])  # name, notes
        ed.add_character_row(edit=True)  # adding clears the filter
        self.assertEqual(ed.char_filter_edit.text(), "")
        self.assertFalse(any(ed.characters_table.isRowHidden(r) for r in range(4)))
        t = ed.characters_table
        self.assertFalse(t.wordWrap())  # one line per character (80 in Blue Archive), the notes in the tooltip
        self.assertEqual(t.item(0, C_NOTES).toolTip(), self.profile.characters[0].notes)

    def test_old_self_pronoun_does_not_block_other_edits(self):
        self.store.save(Profile("Cũ", characters=[Character("セラ", "Sera", "nữ", "em/tớ", "gọi Ren là 'anh'"),
                                                  Character("レン", "Ren", "nam", "anh")]))
        ed = self.editor("Cũ")
        ed.style_edit.setPlainText("Giọng nhẹ nhàng.")
        self.assertTrue(ed.save(), ed.status_label.text())  # Sera was not edited: her "em/tớ" stays as it was
        self.assertEqual(self.store.get("Cũ").characters[0].self_pronoun, "em/tớ")
        ed.pronoun_picker.self_combo.setEditText("em/cậu")  # edited here: now it must be one word
        self.assertFalse(ed.save())

    def test_discard_question_is_vietnamese(self):
        from PySide6.QtCore import QTimer
        ed = self.editor()
        ed.ask_before_discard = True
        ed.style_edit.setPlainText("x")
        seen: list = []

        def answer():
            for w in QApplication.topLevelWidgets():
                if isinstance(w, QMessageBox) and w.isVisible():
                    seen.append(sorted(b.text() for b in w.buttons()))
                    next(b for b in w.buttons() if b.text() == "Bỏ thay đổi").click()
                    return
            QTimer.singleShot(20, answer)

        QTimer.singleShot(20, answer)
        ed.reject()
        self.assertEqual(seen, [["Bỏ thay đổi", "Tiếp tục sửa"]])
        self.assertEqual(ed.result(), 0)
        ed.ask_before_discard = False


# --- pronoun picker in the editor (offscreen OK) ----------------------------------------------------

def _expected_line(speaker: Character, listener_label: str, pair: tuple) -> str:
    """The start of the preview line for address_pair's `pair` (a suffix may follow)."""
    own, other, defined = pair
    sp = speaker.name_vi or speaker.name
    if not defined:
        return f"{sp} → {listener_label}: chưa cố định"
    if own:
        return f"{sp} → {listener_label}: {sp} xưng '{own}', gọi '{other}'"
    return f"{sp} → {listener_label}: {sp} chưa có Tự xưng, gọi '{other}'"


class PronounPickerTests(_EditorFixture):
    """The "Xưng hô" panel of the Nhân vật tab (app.ui.pronoun_picker), driven through its widgets and
    signals (no OS input)."""

    def cells(self, picker) -> list[tuple[str, str, str]]:
        t = picker.rules_table
        return [tuple(t.item(r, c).text() for c in range(3)) for r in range(t.rowCount())]

    def open(self, name: str, characters: list[Character]):
        self.store.save(Profile(name, characters=list(characters)))
        return self.editor(name)

    def test_loads_the_selected_character(self):
        from app.ui.glossary_editor import C_NOTES
        ed = self.editor()
        pk = ed.pronoun_picker
        self.assertEqual(ed.characters_table.currentRow(), 0)
        self.assertEqual(ed.pronoun_group.title(), "Xưng hô của Sera (セラ)")
        self.assertEqual([pk.rules_table.horizontalHeaderItem(i).text() for i in range(3)],
                         ["Người nghe", "Gọi là", "Tự xưng riêng (gợi ý cho LLM)"])
        self.assertEqual(pk.self_pronoun(), "em")
        self.assertEqual(self.cells(pk), [("Ren", "anh", "em"), ("người lạ", "", "tôi")])
        self.assertEqual(pk.other_edit.text(), "17 tuổi, em gái nuôi của Ren")
        lines = pk.preview_lines()
        self.assertEqual(lines[:3], ["Sera → Ren: Sera xưng 'em', gọi 'anh'",
                                     "Sera → Garo: chưa cố định — LLM tự chọn theo ghi chú",
                                     "Sera → người khác / chưa rõ ai: chưa cố định — LLM tự chọn theo ghi chú"])
        self.assertIn("Với người lạ: tự xưng 'tôi' chỉ gửi cho LLM trong ghi chú", lines[3])
        self.assertEqual(pk.notes(), ed.characters_table.item(0, C_NOTES).text())
        self.assertIn("gọi Ren là &#x27;anh&#x27;, xưng &#x27;em&#x27;", pk.notes_label.text())
        self.assertEqual(pk.problems(), [])
        ed.characters_table.setCurrentCell(1, 0)  # Ren: Tự xưng "tôi" but "xưng 'anh'" toward Sera in the notes
        self.assertEqual(ed.pronoun_group.title(), "Xưng hô của Ren (レン)")
        self.assertEqual(self.cells(pk), [("Sera", "em", "anh"), ("thầy Garo", "", "con")])
        text = "\n".join(pk.preview_lines())
        self.assertIn("Ren → Sera: Ren xưng 'tôi', gọi 'em'", text)
        self.assertIn("Với Sera, app vẫn dùng tự xưng 'tôi'", text)
        self.assertIn("Với thầy Garo: tự xưng 'con' chỉ gửi cho LLM", text)
        self.assertFalse(ed.is_dirty())  # looking is not editing

    def test_unchanged_profiles_save_identically(self):
        """Every notes shape in the repo: open, visit every character, save -> the same characters."""
        for name, chars in note_fixtures():
            with self.subTest(fixture=name):
                ed = self.open(f"Hồ sơ {name}", chars)
                stored = self.store.get(f"Hồ sơ {name}").characters
                for row in range(ed.characters_table.rowCount()):
                    ed.characters_table.setCurrentCell(row, 0)
                self.assertFalse(ed.is_dirty())
                profile, errors = ed.collect()
                self.assertEqual(errors, [])
                self.assertEqual(profile.characters, stored)
                self.assertTrue(ed.save())
                self.assertEqual(self.store.get(f"Hồ sơ {name}").characters, stored)

    def test_preview_is_address_pair(self):
        """For every fixture and every speaker, each preview line is what address_pair gives (with many
        characters, the ones without a rule share one line per pair)."""
        for name, chars in note_fixtures():
            ed = self.open(f"Xem {name}", chars)
            stored = self.store.get(f"Xem {name}").characters
            ctx = TranslationContext(characters=tuple(stored))
            rows = range(len(stored)) if len(stored) <= 12 else (0, 1, 2, 3, len(stored) - 1)
            for row in rows:
                speaker = stored[row]
                ed.characters_table.setCurrentCell(row, 0)
                lines = ed.pronoun_picker.preview_lines()
                grouped = {}
                for line in lines:
                    m = re.match(r".+? → (\d+) nhân vật khác \(.*?\): (.*)$", line)
                    if m:
                        grouped[m.group(2)] = int(m.group(1))
                in_groups = 0
                for listener in stored:
                    if listener.name == speaker.name:
                        continue
                    want = _expected_line(speaker, listener.name_vi or listener.name,
                                          address_pair(speaker.name, listener.name, ctx))
                    if not any(line.startswith(want) for line in lines):
                        pair = want.split(": ", 1)[1]
                        self.assertTrue(any(g.startswith(pair) for g in grouped), (name, want, lines))
                        in_groups += 1
                self.assertEqual(in_groups, sum(grouped.values()), (name, lines))
                if len(stored) <= 7:
                    self.assertEqual(grouped, {})  # few characters: one line each
                want = _expected_line(speaker, "người khác / chưa rõ ai", address_pair(speaker.name, None, ctx))
                self.assertTrue(any(line.startswith(want) for line in lines), (name, want, lines))

    def test_structured_edit_writes_the_notes(self):
        from app.ui.glossary_editor import C_NOTES, C_PRONOUN
        from app.ui.pronoun_picker import L_CALL, L_LISTENER
        ed = self.editor()
        pk = ed.pronoun_picker
        t = pk.rules_table
        t.item(0, L_CALL).setText("anh (お兄ちゃん)")
        row = pk.add_rule()  # "Thêm người nghe": the first character without a row, a suggested word
        self.assertEqual(self.cells(pk)[row], ("Garo", "thầy", ""))  # Garo is "thầy của Ren" in his notes
        t.item(row, L_CALL).setText("thầy")
        blank = pk.add_rule()  # every other character has a row now: an empty one (dropped when saving)
        self.assertEqual(self.cells(pk)[blank], ("", "", ""))
        pk.other_edit.setText("17 tuổi, em gái nuôi của Ren, nói nhỏ nhẹ")
        want = ("17 tuổi, em gái nuôi của Ren, nói nhỏ nhẹ; gọi Ren là 'anh' (お兄ちゃん), xưng 'em'; "
                "với người lạ xưng 'tôi'; gọi Garo là 'thầy'")
        self.assertEqual(ed.characters_table.item(0, C_NOTES).text(), want)
        self.assertTrue(ed.is_dirty())
        self.assertIn("Sera → Garo: Sera xưng 'em', gọi 'thầy'", pk.preview_lines())
        pk.self_combo.setEditText("tớ")
        self.assertEqual(ed.characters_table.item(0, C_PRONOUN).text(), "tớ")
        self.assertTrue(ed.save(), ed.status_label.text())
        got = self.store.get(self.profile.name)
        self.assertEqual((got.characters[0].self_pronoun, got.characters[0].notes), ("tớ", want))
        self.assertEqual(got.characters[1:], self.profile.characters[1:])  # the others untouched
        ctx = got.to_context()
        self.assertEqual(address_pair("セラ", "ガロ", ctx), ("tớ", "thầy", True))
        self.assertEqual(address_pair("セラ", "レン", ctx), ("tớ", "anh", True))
        # undoing the edits gives the loaded text back, verbatim
        pk.delete_rules([row, blank])
        pk.other_edit.setText("17 tuổi, em gái nuôi của Ren")
        t.item(0, L_CALL).setText("anh")
        self.assertEqual(ed.characters_table.item(0, C_NOTES).text(), self.profile.characters[0].notes)
        self.assertEqual(t.item(0, L_LISTENER).text(), "Ren")

    def test_hand_edits_in_the_table_are_read_back(self):
        from app.ui.glossary_editor import _ROLE_KEY, C_NOTES, C_PRONOUN
        ed = self.editor()
        pk = ed.pronoun_picker
        typed = "Bạn thân của Ren. Gọi Ren là 'cậu', xưng 'tớ'."
        ed.characters_table.item(0, C_NOTES).setText(typed)
        self.assertEqual(self.cells(pk), [("Ren", "cậu", "tớ")])
        self.assertEqual(pk.other_edit.text(), "Bạn thân của Ren")
        ed.characters_table.item(0, C_PRONOUN).setData(_ROLE_KEY, "tớ")
        self.assertEqual(pk.self_pronoun(), "tớ")
        self.assertTrue(ed.save())
        self.assertEqual(self.store.get(self.profile.name).characters[0].notes, typed)  # as typed, not rewritten

    def test_standalone_xung_follows_tu_xung(self):
        from app.ui.glossary_editor import C_NOTES, C_PRONOUN
        ed = self.open("Tiệm sách", [Character("Lily", "Lily", "nữ", "", "Nhân viên. Xưng 'em', gọi khách là 'anh'."),
                                     Character("Carter", "Carter", "nam", "tôi")])
        pk = ed.pronoun_picker
        self.assertEqual(pk.self_combo.lineEdit().placeholderText(), "theo ghi chú: em")
        self.assertIn("Lily → Carter: Lily xưng 'em', gọi 'anh' (quy tắc chung)", pk.preview_lines())
        pk.self_combo.setEditText("tôi")
        self.assertEqual(ed.characters_table.item(0, C_NOTES).text(), "Nhân viên; xưng 'tôi'; gọi khách là 'anh'")
        self.assertEqual(ed.characters_table.item(0, C_PRONOUN).text(), "tôi")
        self.assertIn("Lily → người khác / chưa rõ ai: Lily xưng 'tôi', gọi 'anh' (quy tắc chung)", pk.preview_lines())

    def test_validation_and_drafts(self):
        from app.ui.pronoun_picker import _ERROR_BG, L_CALL, L_OWN
        ed = self.editor()
        pk = ed.pronoun_picker
        t = pk.rules_table
        row = pk.add_rule()
        t.item(row, L_CALL).setText("")  # a listener without a word
        self.assertEqual(pk.problems(), ["Dòng 3: “Garo”: chưa có từ nào — điền ô “Gọi là”"])
        self.assertEqual(t.item(row, 0).background().color(), _ERROR_BG)
        self.assertEqual(pk.notes(), self.profile.characters[0].notes)  # not written: the notes stay as they were
        ed.characters_table.setCurrentCell(1, 0)
        ed.characters_table.setCurrentCell(0, 0)
        self.assertEqual(self.cells(pk)[row], ("Garo", "", ""))  # the incomplete row survives switching
        ed.characters_table.setCurrentCell(2, 0)
        self.assertFalse(ed.save())
        self.assertIn("Nhân vật “Sera”: dòng xưng hô 3: “Garo”: chưa có từ nào", ed.status_label.text())
        self.assertEqual(ed.characters_table.currentRow(), 0)  # shown where it is wrong
        t.item(row, L_CALL).setText("anh hoặc thầy")
        self.assertIn("mỗi ô chỉ một từ", pk.problems()[0])
        self.assertFalse(ed.save())
        t.item(row, L_CALL).setText("thầy (hoặc 'sư phụ')")
        t.item(row, L_OWN).setText("con")
        self.assertEqual(pk.problems(), [])
        pk.self_combo.setEditText("em/tớ")
        self.assertFalse(ed.save())
        self.assertIn("Tự xưng “em/tớ”", ed.status_label.text())
        pk.self_combo.setEditText("em")
        self.assertTrue(ed.save(), ed.status_label.text())
        self.assertTrue(self.store.get(self.profile.name).characters[0].notes.endswith(
            "gọi Garo là 'thầy' (hoặc 'sư phụ'), xưng 'con'"))
        pk.delete_rules([0, 1, 2])
        self.assertEqual(self.cells(pk), [])
        self.assertTrue(pk.empty_label.isVisibleTo(pk))
        self.assertTrue(ed.save())
        self.assertEqual(self.store.get(self.profile.name).characters[0].notes, "17 tuổi, em gái nuôi của Ren")

    def test_new_character_suggestions(self):
        from app.ui.glossary_editor import _ROLE_KEY, C_GENDER, C_NAME, C_NAME_VI, C_PRONOUN
        from app.ui.pronoun_picker import L_CALL, L_LISTENER
        ed = self.editor()
        pk = ed.pronoun_picker
        r = ed.add_character_row(edit=True)
        self.assertEqual(ed.characters_table.currentRow(), r)
        self.assertEqual(pk.preview_lines(), ["Nhập “Tên gốc” của nhân vật ở bảng trên để xem trước."])
        c = ed.characters_table
        c.item(r, C_NAME).setText("ミナ")
        c.item(r, C_NAME_VI).setText("Mina")
        c.item(r, C_GENDER).setData(_ROLE_KEY, "nữ")
        self.assertEqual(ed.pronoun_group.title(), "Xưng hô của Mina (ミナ)")
        # no Tự xưng yet: toward Sera the word comes from Sera's own "em"
        self.assertEqual(pk.preview_lines()[0], "Mina → Sera: Mina chưa có Tự xưng, gọi 'em' (suy ra từ Tự xưng của Sera)")
        pk.other_edit.setText("bạn cùng lớp của Sera")
        self.assertEqual(pk.self_combo.lineEdit().placeholderText(), "gợi ý: tớ")
        pk.suggest()  # Tự xưng from the role; a row only for the listeners a word can be guessed for
        self.assertEqual(pk.self_pronoun(), "tớ")
        self.assertEqual(c.item(r, C_PRONOUN).text(), "tớ")
        # Garo is "thầy của Ren" (his notes): "thầy". Sera ("em") and Ren ("tôi") give no pair with "tớ": no
        # peer word is guessed for them (it would become a fixed pair at once)
        self.assertEqual(self.cells(pk), [("Garo", "thầy", "")])
        t = pk.rules_table
        t.item(0, L_LISTENER).setText("khách")  # a suggested word follows the listener...
        self.assertEqual(self.cells(pk)[0], ("khách", "quý khách", ""))
        r1 = pk.add_rule()  # the first character without a row, no word guessed
        self.assertEqual(self.cells(pk)[r1], ("Sera", "", ""))
        t.item(r1, L_CALL).setText("cậu")
        t.item(r1, L_LISTENER).setText("Garo")  # ...a word the user chose does not
        self.assertEqual(self.cells(pk)[r1], ("Garo", "cậu", ""))
        r2 = pk.add_rule()
        t.item(r2, L_LISTENER).setText("Garo")
        t.item(r2, L_CALL).setText("anh")
        self.assertEqual(pk.problems(), ["Dòng 3: “Garo” có hai dòng “Gọi là” (dòng 2 và 3); app chỉ dùng dòng đầu"])
        self.assertFalse(ed.save())
        pk.delete_rules([r2])
        self.assertTrue(ed.save(), ed.status_label.text())
        mina = self.store.get(self.profile.name).characters[-1]
        self.assertEqual((mina.name, mina.self_pronoun, mina.notes),
                         ("ミナ", "tớ", "bạn cùng lớp của Sera; gọi khách là 'quý khách'; gọi Garo là 'cậu'"))

    def test_listener_choices_delegates_and_keys(self):
        from app.ui.pronoun_picker import L_LISTENER
        ed = self.editor()
        pk = ed.pronoun_picker
        choices = pk._listener_choices()
        texts = [text for text, _tip in choices]
        self.assertEqual(texts, ["Ren", "Garo", "người lạ", "khách", "mọi người", "người khác", "học sinh", "Sensei"])
        tips = dict(choices)
        self.assertEqual(tips["Ren"], "Nhân vật: レン (Ren)")
        self.assertIn("mọi người nghe chưa có dòng riêng", tips["khách"])
        self.assertIn(tips["Sensei"], ("Nhóm: dùng cho mọi người nghe chưa có dòng riêng.",
                                       "Chỉ gửi cho LLM trong ghi chú; app chưa dùng làm cặp xưng hô cố định."))
        t = pk.rules_table
        index = t.model().index(1, L_LISTENER)
        delegate = t.itemDelegateForColumn(L_LISTENER)
        editor = delegate.createEditor(t.viewport(), QStyleOptionViewItem(), index)
        self.addCleanup(editor.deleteLater)
        self.assertTrue(editor.isEditable())
        self.assertEqual([editor.itemText(i) for i in range(editor.count())], texts)
        delegate.setEditorData(editor, index)
        self.assertEqual(editor.currentText(), "người lạ")
        editor.setEditText("  khách ")
        delegate.setModelData(editor, t.model(), index)
        self.assertEqual(self.cells(pk)[1], ("khách", "", "tôi"))  # an own word: no word suggested over it
        keys = {sc.key().toString(): sc for sc in t.findChildren(QShortcut)}
        self.assertEqual(set(keys), {"Del", "Ins"})
        keys["Ins"].activated.emit()
        self.assertEqual(t.rowCount(), 3)
        t.selectRow(2)
        keys["Del"].activated.emit()
        self.assertEqual(t.rowCount(), 2)
        self.assertFalse(t.tabKeyNavigation())  # Tab leaves the table
        buddies = {lab.text(): lab.buddy() for lab in pk.findChildren(QLabel) if lab.buddy() is not None}
        self.assertIs(buddies["&Tự xưng:"], pk.self_combo)
        self.assertIs(buddies["Ghi chú &khác:"], pk.other_edit)
        self.assertEqual(pk.add_rule_button.text(), "Thêm người &nghe")
        for w in (pk.self_combo, pk.suggest_button, pk.add_rule_button, pk.delete_rule_button, pk.other_edit):
            self.assertTrue(w.toolTip(), w)

    def test_remarks(self):
        from app.ui.pronoun_picker import L_CALL, L_LISTENER
        ed = self.editor()
        pk = ed.pronoun_picker
        ed.characters_table.setCurrentCell(1, 0)  # Ren
        t = pk.rules_table
        t.item(1, L_CALL).setText("thầy")  # "gọi thầy Garo là 'thầy'"
        text = "\n".join(pk.preview_lines())
        ctx = TranslationContext(characters=(pk.character(), *[c for c in self.profile.characters if c.name != "レン"]))
        if address_pair("レン", "ガロ", ctx)[2]:  # (depends on the titles address_pair strips)
            self.assertIn("Ren → Garo: Ren xưng 'tôi', gọi 'thầy'", text)
        else:
            self.assertIn("“thầy Garo”: app không nhận ra đây là Garo", text)
        for listener, word in (("khách", "anh"), ("người lạ", "bạn"), ("Kenji", "mày"), ("Sera", "cưng")):
            r = pk.add_rule()
            t.item(r, L_LISTENER).setText(listener)
            t.item(r, L_CALL).setText(word)
        pk.other_edit.setText("thường gọi Sera là 'nhóc'")
        text = "\n".join(pk.preview_lines())
        self.assertIn("Các nhóm dùng từ khác nhau ('anh', 'bạn')", text)
        self.assertIn("“Kenji”: không phải nhân vật trong hồ sơ", text)
        self.assertIn("“Ghi chú khác” còn câu xưng hô app vẫn đọc: gọi Sera là 'nhóc'", text)
        self.assertIn("Với Sera, app dùng 'em', không phải 'cưng'", text)  # two "Gọi là" rows for Sera
        self.assertTrue(any("có hai dòng" in p for p in pk.problems()))

    def test_unquoted_notes_are_turned_into_rows(self):
        """Notes typed the old way but without quotes ("Gọi Ren là con, xưng ta") give no pair: the picker
        says so and "Chuyển vào bảng" makes rows of them."""
        from app.ui.glossary_editor import C_NOTES
        ed = self.editor()
        pk = ed.pronoun_picker
        c = ed.characters_table
        c.setCurrentCell(2, 0)  # Garo
        c.item(2, C_NOTES).setText("thầy của Ren; Gọi Ren là con, xưng ta")  # typed by hand in the table
        self.assertEqual(self.cells(pk), [])
        self.assertFalse(pk.convert_button.isHidden())
        text = "\n".join(pk.preview_lines())
        self.assertIn("thiếu dấu nháy nên app không đọc được: “Gọi Ren là con, xưng ta”", text)
        self.assertIn("Garo → Ren: chưa cố định", text)
        pk.convert_button.click()  # our own button, clicked programmatically
        self.assertEqual(self.cells(pk), [("Ren", "con", "ta")])
        self.assertEqual(pk.other_edit.text(), "thầy của Ren")
        self.assertTrue(pk.convert_button.isHidden())
        self.assertIn("Garo → Ren: Garo xưng 'ta', gọi 'con'", pk.preview_lines())
        self.assertTrue(ed.save(), ed.status_label.text())
        self.assertEqual(address_pair("ガロ", "レン", self.store.get(self.profile.name).to_context()), ("ta", "con", True))

    def test_own_word_equal_to_tu_xung_is_shown_blank(self):
        from app.ui.pronoun_picker import L_OWN
        ed = self.editor()
        pk = ed.pronoun_picker
        t = pk.rules_table  # Sera (Tự xưng "em"): ("Ren", "anh", "em"), ("người lạ", "", "tôi")
        delegate = t.itemDelegateForColumn(L_OWN)

        def shown(row: int) -> str:
            opt = QStyleOptionViewItem()
            delegate.initStyleOption(opt, t.model().index(row, L_OWN))
            return opt.text

        self.assertEqual((shown(0), shown(1)), ("", "tôi"))  # "em" says nothing more than Tự xưng
        self.assertEqual(t.item(0, L_OWN).text(), "em")  # kept: the notes stay verbatim
        self.assertIn("Giống “Tự xưng”", t.item(0, L_OWN).toolTip())
        pk.self_combo.setEditText("tớ")
        self.assertEqual(shown(0), "em")
        self.assertIn("gợi ý cho LLM", t.horizontalHeaderItem(L_OWN).text())

    def test_many_characters_are_summarised(self):
        students = [Character(f"生徒{i}", f"Học sinh {i}", "nữ", "em", "Gọi Sensei là 'Sensei', xưng 'em'.")
                    for i in range(8)]
        chars = [*blue_archive_like(), *students]
        ed = self.open("Blue Archive (thử)", chars)
        pk = ed.pronoun_picker
        self.assertEqual(ed.pronoun_group.title(), "Xưng hô của Sensei (先生)")
        lines = pk.preview_lines()
        ctx = TranslationContext(characters=tuple(self.store.get("Blue Archive (thử)").characters))
        # one line for the 13 others (the same pair toward each), not 13 lines
        grouped = [line for line in lines if " nhân vật khác (" in line]
        self.assertEqual(len(grouped), 1, lines)
        label = "13 nhân vật khác (Hoshino, Arona, Yuuka, …)"
        self.assertTrue(grouped[0].startswith(_expected_line(chars[0], label, address_pair("先生", "生徒0", ctx))), grouped)
        self.assertLess(len(lines), 6)
        ed.characters_table.setCurrentCell(1, 0)  # Hoshino: one line per character she names, the rest summarised
        lines = pk.preview_lines()
        want = _expected_line(chars[1], "Sensei", address_pair("ホシノ", "先生", ctx))
        self.assertTrue(any(line.startswith(want) for line in lines), (want, lines))


# --- tray menus (offscreen OK) ----------------------------------------------------------------------

class TrayProfileMenuTests(unittest.TestCase):
    def setUp(self):
        qapp()
        from app.ui.tray import TrayController
        self.addCleanup(flush_deletes)
        self.tray = self.make(TrayController, Config(profile="Game A", llm_mode="only"))

    def make(self, cls, config: Config):
        tray = cls(config)
        for m in (tray.menu(), tray._lang_menu, tray._guard_menu, tray._profile_menu, tray._llm_menu):
            self.addCleanup(m.deleteLater)  # the tray's menus have no parent
        self.addCleanup(tray.deleteLater)
        return tray

    def test_menus_present(self):
        texts = self.tray.action_texts()
        for label in ("Hồ sơ: Game A", "  Game A", "  Tạo hồ sơ mới…", "  Sửa thuật ngữ / nhân vật…",
                      "Dịch bằng LLM: chỉ LLM", "  Tắt", "  Google trước, LLM tinh chỉnh", "  Chỉ LLM",
                      "  Mô hình: tự động (7B nếu đủ VRAM)", "  Mô hình: Nhẹ 1.8B (~2 GB VRAM)",
                      "  Mô hình: Chất lượng 7B (~5,3 GB VRAM)", "LLM: chưa chạy"):
            self.assertIn(label, texts)
        self.assertTrue(self.tray._llm_actions["only"].isChecked())
        self.assertTrue(self.tray._model_actions["auto"].isChecked())  # llm_model "" = auto
        self.tray.set_llm_model_checked("Hy-MT2-1.8B-Q6_K.gguf")
        self.assertTrue(self.tray._model_actions["light"].isChecked())
        self.tray.set_llm_model_checked("Other.gguf")
        self.assertFalse(any(a.isChecked() for a in self.tray._model_actions.values()))
        self.assertFalse(self.tray._llm_status_action.isEnabled())  # a status line, not an action
        # the old entries are all still there
        for label in ("Ngôn ngữ nguồn\tCtrl+Alt+L", "Chế độ anti-cheat", "Mở thư mục dữ liệu", "Thoát\tCtrl+Alt+Q"):
            self.assertIn(label, texts)
        from app.ui.tray import TrayController
        self.assertIn("LLM: tắt", self.make(TrayController, Config(llm_mode="off")).action_texts())

    def test_set_profiles(self):
        tray = self.tray
        tray.set_profiles(["default", "Game A", "R&D", "Hồ sơ rất rất rất dài " + "x" * 50], "R&D")
        texts = tray.action_texts()
        self.assertIn("Hồ sơ: R&&D", texts)  # '&' is shown, not taken as a mnemonic
        self.assertIn("  Mặc định", texts)  # the built-in "default" profile
        self.assertTrue(any(t.startswith("  Hồ sơ rất") and t.endswith("…") for t in texts))
        self.assertTrue(tray._profile_actions["R&D"].isChecked())
        self.assertFalse(tray._profile_actions["Game A"].isChecked())
        self.assertEqual(tray.active_profile(), "R&D")
        sub = [a.text() for a in tray._profile_menu.actions()]
        self.assertEqual(sub[-2:], ["Tạo hồ sơ mới…", "Sửa thuật ngữ / nhân vật…"])  # fixed actions stay last
        self.assertTrue(tray._profile_menu.actions()[-3].isSeparator())
        before = dict(tray._profile_actions)
        tray.set_profiles(["default", "Game A", "R&D", "Hồ sơ rất rất rất dài " + "x" * 50], "Game A")
        self.assertEqual(tray._profile_actions, before)  # same list: actions reused, only the check moves
        self.assertTrue(tray._profile_actions["Game A"].isChecked())
        tray.set_profiles(["default"], "Mới")  # an active name missing from the list is added
        self.assertEqual(list(tray._profile_actions), ["default", "Mới"])
        self.assertIn("Hồ sơ: Mới", tray.action_texts())

    def test_signals(self):
        got = []
        tray = self.tray
        tray.set_profile.connect(lambda n: got.append(("profile", n)))
        tray.new_profile.connect(lambda: got.append(("new",)))
        tray.edit_glossary.connect(lambda: got.append(("edit",)))
        tray.set_llm_mode.connect(lambda m: got.append(("llm", m)))
        tray.set_profiles(["default", "Game A"], "default")  # programmatic: no signal
        tray.set_llm_mode_checked("off")
        tray._profile_actions["Game A"].trigger()
        tray._new_profile_action.trigger()
        tray._edit_glossary_action.trigger()
        tray._llm_actions["refine"].trigger()
        self.assertEqual(got, [("profile", "Game A"), ("new",), ("edit",), ("llm", "refine")])
        self.assertTrue(tray._llm_actions["refine"].isChecked())

    def test_llm_status(self):
        tray = self.tray
        tray.set_llm_status("LLM: sẵn sàng (Qwen3-8B-Q4_K_M.gguf)")
        self.assertIn("LLM: sẵn sàng (Qwen3-8B-Q4_K_M.gguf)", tray.action_texts())
        tray.set_llm_status("đang tải model…")
        self.assertIn("LLM: đang tải model…", tray.action_texts())
        long = "LLM: lỗi " + "không khởi động được llama-server: " * 5
        tray.set_llm_status(long)
        shown = tray._llm_status_action.text()
        self.assertTrue(shown.endswith("…") and len(shown) <= 70, shown)
        self.assertEqual(tray.llm_status(), " ".join(long.split()))  # full text in the tooltip

    def test_menus_capture_excluded(self):
        tray = self.tray
        self.assertEqual([e._widget for e in tray._exclusions], [tray.menu(), tray._lang_menu, tray._guard_menu])
        self.assertEqual([e._widget for e in tray._more_exclusions], [tray._profile_menu, tray._llm_menu])
        if on_windows_platform():
            from app.overlay.affinity import get_display_affinity
            for m in (tray._profile_menu, tray._llm_menu):
                self.assertEqual(get_display_affinity(int(m.winId())), 0x11)


# --- foreground_changed (offscreen OK) ------------------------------------------------------------

class ForegroundSignalTests(unittest.TestCase):
    def setUp(self):
        qapp()
        from app.ui.game_guard import GameGuard
        self.cfg = Config(guard_mode="pause", blocked_processes=["Game.exe"])
        self.fg: tuple[int, str | None] | None = None
        self.guard = GameGuard(self.cfg, reader=lambda: self.fg, use_event_hook=False)
        self.seen: list[str] = []
        self.changes: list[tuple[bool, str]] = []
        self.guard.foreground_changed.connect(self.seen.append)
        self.guard.changed.connect(lambda g, e: self.changes.append((g, e)))

    def test_emitted_on_change_only(self):
        g = self.guard
        g.evaluate("notepad.exe", 100)
        g.evaluate("notepad.exe", 100)  # same exe: once
        g.evaluate("C:\\Games\\Game.exe", 200)  # basename only
        g.evaluate("GAME.EXE", 201)  # same exe, other case: not a change
        g.evaluate("python.exe", os.getpid())  # our own tray menu / editor: never reported
        g.evaluate(None, 300)  # unknown name: nothing
        g.evaluate("explorer.exe", 400)
        self.assertEqual(self.seen, ["notepad.exe", "Game.exe", "explorer.exe"])
        # the guard's own signal is unchanged (it reports the exe exactly as read)
        self.assertEqual(self.changes, [(True, "Game.exe"), (True, "GAME.EXE"), (False, "")])

    def test_all_modes_and_check_path(self):
        self.cfg.guard_mode = "off"
        self.fg = (10, "a.exe")
        self.guard.check()
        self.cfg.guard_mode = "warn"
        self.fg = (11, "b.exe")
        self.guard.check()
        self.fg = None  # no foreground window: nothing
        self.guard.check()
        self.fg = (os.getpid(), None)  # our own window (what _read_foreground returns for it)
        self.guard.check()
        self.assertEqual(self.seen, ["a.exe", "b.exe"])

    def test_event_hook_handler_triggers_a_check(self):
        """Calls the WinEvent callback directly, as Windows would on a focus change (none happens)."""
        from app.ui.game_guard import GameGuard
        guard = GameGuard(self.cfg, reader=lambda: self.fg, use_event_hook=True)
        seen: list[str] = []
        guard.foreground_changed.connect(seen.append)
        guard._install_hook()
        try:
            if guard._hook_proc is None:
                self.skipTest("SetWinEventHook unavailable")
            self.fg = (77, "Editor.exe")
            guard._hook_proc(None, 3, None, 0, 0, 0, 0)  # EVENT_SYSTEM_FOREGROUND
            self.assertEqual(seen, [])  # the check is queued to the GUI thread, not run inside the hook
            pump(50)
            self.assertEqual(seen, ["Editor.exe"])
        finally:
            guard._remove_hook()


# --- native (windows platform only) ---------------------------------------------------------------

class NativeEditorTests(_EditorFixture):
    def setUp(self):
        if not NATIVE or not on_windows_platform():
            self.skipTest("needs the windows platform: run via WindowsPlatformSubprocessTests")
        super().setUp()

    def test_dialog_and_popups_capture_excluded(self):
        from app.overlay.affinity import get_display_affinity
        from app.ui.glossary_editor import WindowPicker
        ed = self.editor()
        self.assertEqual(get_display_affinity(int(ed.winId())), 0x11)
        self.assertTrue(ed.exclusion().last_result.excluded)
        ed._popups.install()  # what showEvent does
        # popups get a synthetic Show (a real one would grab input / appear on screen)
        combo_popup = ed.lang_combo.view().window()
        self.assertTrue(combo_popup.isWindow())
        tip = QLabel("tooltip", None, Qt.WindowType.ToolTip)
        box = QMessageBox(QMessageBox.Icon.Question, "Bỏ thay đổi?", "Đóng mà không lưu?", parent=ed)
        foreign = QWidget()  # an unrelated window of ours: not touched by the editor's filter
        self.addCleanup(tip.deleteLater)
        self.addCleanup(foreign.deleteLater)
        for w in (combo_popup, tip, box, foreign):
            QCoreApplication.sendEvent(w, QShowEvent())
        for w in (combo_popup, tip, box):
            self.assertEqual(get_display_affinity(int(w.winId())), 0x11, w)
        self.assertEqual(get_display_affinity(int(foreign.winId())), 0)
        self.assertEqual(ed.popup_exclusions(), 3)
        picker = WindowPicker([], ed, windows=[("a.exe", "A")])
        self.assertEqual(get_display_affinity(int(picker.winId())), 0x11)
        # a real show (off-screen, not activated) is excluded before it is visible
        fg_before = _user32.GetForegroundWindow()
        child = QWidget(ed, Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        child.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        child.setGeometry(-4000, -4000, 120, 60)
        child.show()
        pump(80)
        self.assertEqual(get_display_affinity(int(child.winId())), 0x11)
        self.assertEqual(child._capture_exclusion.history[0][0], "apply_to_widget")
        self.assertEqual(_user32.GetForegroundWindow(), fg_before)
        child.hide()
        ed._popups.remove()
        late = QLabel("later", None, Qt.WindowType.ToolTip)
        self.addCleanup(late.deleteLater)
        QCoreApplication.sendEvent(late, QShowEvent())
        self.assertIsNone(getattr(late, "_capture_exclusion", None))  # filter gone with the dialog hidden

    def test_pronoun_picker_popups_capture_excluded(self):
        """The pickers' combo lists (Tự xưng and the table's cell editors) are excluded like the rest."""
        from app.overlay.affinity import get_display_affinity
        from app.ui.pronoun_picker import L_LISTENER
        ed = self.editor()
        ed._popups.install()  # what showEvent does
        self.addCleanup(ed._popups.remove)
        pk = ed.pronoun_picker
        t = pk.rules_table
        cell_editor = t.itemDelegateForColumn(L_LISTENER).createEditor(
            t.viewport(), QStyleOptionViewItem(), t.model().index(0, L_LISTENER))
        self.addCleanup(cell_editor.deleteLater)
        popups = [pk.self_combo.view().window(), cell_editor.view().window()]
        for w in popups:
            self.assertTrue(w.isWindow())
            QCoreApplication.sendEvent(w, QShowEvent())  # synthetic: a real popup would grab input
        for w in popups:
            self.assertEqual(get_display_affinity(int(w.winId())), 0x11, w)
        self.assertEqual(ed.popup_exclusions(), 2)

    def test_visible_window_processes(self):
        from app.ui.glossary_editor import visible_window_processes
        found = visible_window_processes()
        self.assertIsInstance(found, list)
        for exe, title in found:
            self.assertIsInstance(exe, str)
            self.assertIsInstance(title, str)
            self.assertTrue(exe)
        self.assertEqual([e.lower() for e, _ in found], sorted(e.lower() for e, _ in found))
        self.assertEqual(len({e.lower() for e, _ in found}), len(found))  # one entry per program

    def _grab(self, ed, name: str) -> None:
        ed.layout().activate()
        pump(30)
        img = ed.grab()
        self.assertFalse(img.isNull())
        self.assertGreaterEqual(img.width(), 900)
        path = PREVIEW_DIR / f"{name}.png"
        self.assertTrue(img.save(str(path)), path)

    def test_render_preview(self):
        ed = self.editor()
        ed.resize(1000, 700)
        ed.terms_table.selectRow(1)
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        for i, suffix in enumerate(("", "_characters", "_style")):
            ed.tabs.setCurrentIndex(i)
            self._grab(ed, f"glossary_editor{suffix}")
        # the pronoun picker: Ren (a per-listener own word the app does not use, a title it does not read)
        ed.tabs.setCurrentIndex(1)
        ed.characters_table.setCurrentCell(1, 0)
        self._grab(ed, "glossary_editor_characters_ren")
        # an incomplete row being edited, with its error
        ed.characters_table.setCurrentCell(0, 0)
        from app.ui.pronoun_picker import L_CALL
        row = ed.pronoun_picker.add_rule()
        ed.pronoun_picker.rules_table.item(row, L_CALL).setText("")
        ed.save()
        self._grab(ed, "glossary_editor_characters_error")
        # a Blue-Archive-sized profile: the preview summarises the characters without a rule
        students = [Character(f"生徒{i}", n, "nữ", "em", "Gọi Sensei là 'Sensei', xưng 'em'.")
                    for i, n in enumerate(("Shiroko", "Nonomi", "Serika", "Ayane", "Aru", "Mutsuki", "Kayoko"))]
        self.store.save(Profile("Blue Archive (xem thử)", characters=[*blue_archive_like(), *students]))
        many = self.editor("Blue Archive (xem thử)")
        many.resize(1000, 700)
        many.tabs.setCurrentIndex(1)
        many.characters_table.setCurrentCell(1, 0)
        self._grab(many, "glossary_editor_characters_many")
        from app.ui.glossary_editor import WindowPicker
        picker = WindowPicker(["RPGGame.exe"], ed, windows=[
            ("chrome.exe", "Hướng dẫn chơi — Google Chrome"), ("Code.exe", "glossary_editor.py — screen-translator"),
            ("RPGGame.exe", "Hải đăng & Phù thủy"), ("VisualNovel.exe", "ゆめのかけら Ver.1.02")])
        picker.list.item(3).setCheckState(Qt.CheckState.Checked)
        picker.layout().activate()
        self.assertTrue(picker.grab().save(str(PREVIEW_DIR / "glossary_editor_picker.png")))
        print(f"\n    preview: {PREVIEW_DIR / 'glossary_editor.png'}", file=sys.stderr)


# --- child process ------------------------------------------------------------------------------------

@unittest.skipIf(sys.platform != "win32", "Windows only")
@unittest.skipIf(os.environ.get("SCREEN_TRANSLATOR_SKIP_GUI_TESTS") == "1", "SCREEN_TRANSLATOR_SKIP_GUI_TESTS=1")
class WindowsPlatformSubprocessTests(unittest.TestCase):
    """Runs the test classes again in a child process that uses Qt's windows platform."""

    def test_windows_platform(self):
        if NATIVE:
            self.skipTest("already running on the windows platform")
        from app.ui.game_guard import GameGuard, matches
        fg = GameGuard.foreground_process_name()
        if matches(fg, Config.load().blocked_processes):
            self.skipTest(f"an anti-cheat game ({fg}) is in the foreground")
        env = dict(os.environ)
        env.pop("QT_SCALE_FACTOR", None)
        env.update(QT_QPA_PLATFORM="windows", SCREEN_TRANSLATOR_NATIVE_TESTS="1", PYTHONIOENCODING="utf-8")
        classes = ["NativeEditorTests", "EditorTests", "PronounPickerTests", "TrayProfileMenuTests", "ForegroundSignalTests"]
        cmd = [sys.executable, "-m", "unittest", "-v", *[f"tests.test_glossary_ui.{c}" for c in classes]]
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=180)
        out = proc.stdout + proc.stderr
        if os.environ.get("SCREEN_TRANSLATOR_VERBOSE_CHILD") == "1":
            print(out, file=sys.stderr)
        self.assertEqual(proc.returncode, 0, out[-6000:])
        self.assertNotIn("skipped", out, out[-3000:])


if __name__ == "__main__":
    unittest.main()
