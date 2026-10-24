"""Tests for app.profiles (glossary profiles on disk) and app.translate.memory (dialogue memory).

Run: python -m unittest tests.test_profiles -v
Everything runs in temporary directories; nothing touches data/profiles.
"""
from __future__ import annotations

import json
import random
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app.profiles import (
    DEFAULT_PROFILE,
    Profile,
    ProfileStore,
    junk_reason,
    normalize_source,
    profile_from_dict,
    profile_to_json,
    slugify,
)
from app.translate.contextual import Character, HistoryLine, Term, TranslationContext
from app.translate.memory import DialogueMemory, MemoryBank

ODD_NAMES = ["Hồ sơ: Đặc biệt / 原神 <test> & \"quotes\"", "CON", "原神", "   khoảng   trắng  ", "a" * 120,
             "Tiếng Việt có dấu — ỄỂỀẾỆ", "con"]


def sample_profile(name: str = "Truyện kiếm hiệp") -> Profile:
    return Profile(
        name=name,
        processes=["Game.exe", "C:\\Games\\Other\\launcher.exe"],
        src_lang="ja",
        style="Giọng kiếm hiệp cổ trang;\ngiữ hậu tố -san, -sama.",
        terms=[Term("セラ", "Sera", "person", "nữ chính"), Term("魔導書", "ma đạo thư", "item"),
               Term("エルデン", "Elden", "place", auto=True)],
        characters=[Character("セラ", "Sera", "nữ", "em", "gọi Ren là 'anh', xưng 'em'"),
                    Character("レン", "Ren", "nam", "tôi", "gọi Sera là 'em'; với thầy xưng 'con'")],
    )


class _TempDirTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="st-profiles-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def store(self, **kw) -> ProfileStore:
        kw.setdefault("rescan_s", 0.0)
        return ProfileStore(self.dir, **kw)


# --- round trip / files ---------------------------------------------------------------------------

class RoundTripTests(_TempDirTest):
    def test_round_trip_vietnamese_and_japanese(self):
        s = self.store()
        p = sample_profile()
        s.save(p)
        got = ProfileStore(self.dir).get(p.name)  # a fresh store reads it from disk
        self.assertEqual(got, p)
        self.assertEqual(got.version(), p.version())
        files = list(self.dir.glob("*.json"))
        self.assertEqual([f.name for f in files], ["truyen-kiem-hiep.json"])
        text = files[0].read_text(encoding="utf-8")
        self.assertIn("Giọng kiếm hiệp", text)  # stored as UTF-8 text, not \u escapes
        self.assertIn("セラ", text)
        term_lines = [ln for ln in text.splitlines() if '"source"' in ln]
        self.assertEqual(len(term_lines), 3)  # one term per line: easy to edit by hand
        self.assertIn('"auto": true', term_lines[2])
        self.assertEqual(json.loads(text)["name"], p.name)

    def test_odd_names_get_safe_unique_files(self):
        s = self.store()
        created = []
        for name in ODD_NAMES:
            prof = s.create(name)
            created.append(prof.name)
            self.assertTrue(prof.name.strip())
        self.assertEqual(created[3], "khoảng trắng")  # whitespace collapsed
        self.assertEqual(len(created[4]), 80)  # capped
        self.assertEqual(created[6], "CON")  # names compare case-insensitively: "con" is "CON"
        for f in self.dir.glob("*.json"):
            self.assertRegex(f.stem, r"^[a-z0-9-]+$")
            self.assertNotIn(f.stem.lower(), {"con", "prn", "aux", "nul"})
        fresh = ProfileStore(self.dir)
        names = fresh.names()
        self.assertEqual(names[0], DEFAULT_PROFILE)
        for name in dict.fromkeys(created):
            self.assertIn(name, names)
            self.assertEqual(fresh.get(name).name, name)
        self.assertEqual(len(names), 1 + len(set(c.casefold() for c in created)))

    def test_slugify(self):
        self.assertEqual(slugify("Hồ sơ Đặc biệt"), "ho-so-dac-biet")
        self.assertEqual(slugify("NUL"), "nul-1")
        self.assertRegex(slugify("原神"), r"^profile-[0-9a-f]{8}$")
        self.assertEqual(slugify("原神"), slugify("原神"))
        self.assertNotEqual(slugify("原神"), slugify("崩壊"))

    def test_same_slug_different_names(self):
        s = self.store()
        s.create("Ho so")
        s.create("Hồ sơ")
        self.assertEqual(sorted(f.name for f in self.dir.glob("*.json")), ["ho-so-2.json", "ho-so.json"])
        self.assertEqual(ProfileStore(self.dir).names(), [DEFAULT_PROFILE, "Ho so", "Hồ sơ"])

    def test_missing_profile_falls_back_to_default(self):
        s = self.store()
        p = s.get("does not exist")
        self.assertEqual(p.name, DEFAULT_PROFILE)
        self.assertEqual((p.terms, p.characters, p.processes), ([], [], []))
        self.assertEqual(s.names(), [DEFAULT_PROFILE])
        self.assertEqual(list(self.dir.glob("*")), [])  # reading never creates files
        stored = sample_profile(DEFAULT_PROFILE)
        s.save(stored)
        self.assertEqual(s.get("still missing"), stored)  # the stored default, not a fresh one

    def test_create_existing_and_bad_names(self):
        s = self.store()
        a = s.create("Game A")
        s.add_terms("Game A", [Term("セラ", "Sera", "person")])
        again = s.create("game a")
        self.assertEqual(again.name, "Game A")
        self.assertEqual(len(again.terms), 1)  # not overwritten
        for bad in ("", "   ", "\n\t"):
            with self.assertRaises(ValueError):
                s.create(bad)
        self.assertEqual(a.name, "Game A")

    def test_get_returns_copies(self):
        s = self.store()
        s.save(sample_profile())
        p = s.get("Truyện kiếm hiệp")
        p.terms.clear()
        p.processes.append("x.exe")
        self.assertEqual(len(s.get("Truyện kiếm hiệp").terms), 3)
        self.assertNotIn("x.exe", s.get("Truyện kiếm hiệp").processes)

    def test_delete(self):
        s = self.store()
        s.save(sample_profile())
        with self.assertRaises(ValueError):
            s.delete("default")
        with self.assertRaises(ValueError):
            s.delete("DEFAULT")
        s.delete("truyện kiếm hiệp")
        self.assertEqual(s.names(), [DEFAULT_PROFILE])
        self.assertEqual(s.get("Truyện kiếm hiệp").name, DEFAULT_PROFILE)
        self.assertTrue((self.dir / "truyen-kiem-hiep.json.deleted").exists())  # recoverable
        s.delete("never existed")  # no error

    def test_hand_edits_are_picked_up_and_shorthand_accepted(self):
        s = self.store()
        changes = []
        s.add_listener(changes.append)
        self.assertEqual(s.names(), [DEFAULT_PROFILE])
        (self.dir / "manual.json").write_text(json.dumps({
            "name": "Viết tay", "processes": ["rpg.exe"],
            "terms": {"勇者": "Dũng giả", "魔王": "Ma vương"},  # {source: target} shorthand
            "characters": [{"name": "アリス", "name_vi": "Alice", "gender": "nữ"}],
        }, ensure_ascii=False), encoding="utf-8")
        p = s.get("Viết tay")
        self.assertEqual([(t.source, t.target, t.auto) for t in p.terms], [("勇者", "Dũng giả", False),
                                                                            ("魔王", "Ma vương", False)])
        self.assertEqual(p.characters[0].name_vi, "Alice")
        self.assertIn("Viết tay", changes)  # external change reported
        self.assertEqual(s.match_process("RPG.EXE"), "Viết tay")
        # a later write keeps the hand-written content
        s.add_terms("Viết tay", [Term("エルフ", "Tiên tộc", "term")])
        data = json.loads((self.dir / "manual.json").read_text(encoding="utf-8"))
        self.assertEqual([t["source"] for t in data["terms"]], ["勇者", "魔王", "エルフ"])

    def test_bom_and_list_shorthand(self):
        raw = {"name": "BOM", "terms": [["剣", "Kiếm", "item"], ["盾", "Khiên"]]}
        (self.dir / "bom.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(raw, ensure_ascii=False).encode("utf-8"))
        p = self.store().get("BOM")
        self.assertEqual([(t.source, t.target, t.kind) for t in p.terms], [("剣", "Kiếm", "item"), ("盾", "Khiên", "term")])


class CorruptFileTests(_TempDirTest):
    def test_bad_files_never_raise(self):
        (self.dir / "broken.json").write_text('{"name": "Broken", "terms": [', encoding="utf-8")
        (self.dir / "list.json").write_text("[1, 2, 3]", encoding="utf-8")
        (self.dir / "binary.json").write_bytes(b"\xff\xfe\x00garbage\x81")
        (self.dir / "empty.json").write_bytes(b"")
        (self.dir / "partial.json").write_text(json.dumps({
            "name": "Partial", "processes": ["ok.exe", 5, None, ""], "src_lang": 7, "style": ["not", "text"],
            "terms": [{"source": "セラ", "target": "Sera"}, {"target": "no source"}, "junk", 42,
                      {"source": 5, "target": "x"}, {"source": "セラ", "target": "duplicate"},
                      {"source": "レン", "target": None, "kind": ["x"], "auto": "yes"}],
            "characters": [{"name": "セラ", "gender": 1}, {"name_vi": "nameless"}, None],
            "rejected": "nope",
        }, ensure_ascii=False), encoding="utf-8")
        s = self.store()
        with self.assertLogs("app.profiles", "WARNING") as logs:
            for name in ("Broken", "broken", "list", "binary", "empty"):
                self.assertEqual(s.get(name).name, DEFAULT_PROFILE, name)
        bad = "\n".join(logs.output)
        for f in ("broken.json", "list.json", "binary.json", "empty.json"):
            self.assertEqual(bad.count(f), 1, bad)  # logged once, not on every read
        self.assertEqual(s.names(), [DEFAULT_PROFILE, "Partial"])
        p = s.get("Partial")
        self.assertEqual(p.processes, ["ok.exe"])
        self.assertIsNone(p.src_lang)
        self.assertEqual(p.style, "")
        self.assertEqual([(t.source, t.target, t.auto) for t in p.terms], [("セラ", "Sera", False), ("レン", "", False)])
        self.assertEqual([c.name for c in p.characters], ["セラ"])
        self.assertEqual(p.rejected, [])
        self.assertIsNone(s.match_process("anything.exe"))

    def test_corrupt_file_is_moved_aside_not_overwritten(self):
        bad = self.dir / "default.json"
        bad.write_text("{ this is not json", encoding="utf-8")
        s = self.store()
        with self.assertLogs("app.profiles", "WARNING") as logs:
            self.assertEqual(s.get(DEFAULT_PROFILE).terms, [])
            self.assertEqual(s.add_terms(DEFAULT_PROFILE, [Term("セラ", "Sera", "person")]), 1)
        self.assertTrue(any("default.json.bad" in line for line in logs.output), logs.output)
        self.assertEqual((self.dir / "default.json.bad").read_text(encoding="utf-8"), "{ this is not json")
        self.assertEqual([t.source for t in ProfileStore(self.dir).get(DEFAULT_PROFILE).terms], ["セラ"])

    def test_write_failures(self):
        s = self.store()
        s.save(Profile("G", terms=[Term("セラ", "Sera", "person")]))
        before = (self.dir / "g.json").read_bytes()
        with mock.patch("app.profiles.os.replace", side_effect=OSError(28, "disk full")):
            with self.assertLogs("app.profiles", "WARNING"):
                self.assertEqual(s.add_terms("G", [Term("ガイア", "Gaia", "place")]), 0)  # logged, not raised
            with self.assertRaises(OSError):
                s.save(Profile("G"))  # the editor reports this one
        self.assertEqual((self.dir / "g.json").read_bytes(), before)
        self.assertEqual(list(self.dir.glob("*.tmp")), [])  # no temp file left behind
        self.assertEqual([t.source for t in s.get("G").terms], ["セラ"])
        self.assertEqual(s.add_terms("G", [Term("ガイア", "Gaia", "place")]), 1)  # works again afterwards
        blocked = self.dir / "not-a-dir"
        blocked.write_text("x", encoding="utf-8")
        with self.assertLogs("app.profiles", "WARNING"):
            self.assertEqual(ProfileStore(blocked).add_terms("G", [Term("ガイア", "Gaia", "place")]), 0)

    def test_a_file_that_becomes_unreadable_keeps_its_last_good_version(self):
        s = self.store()
        s.save(Profile("Game", processes=["game.exe"], terms=[Term("セラ", "Sera", "person")]))
        path = self.dir / "game.json"
        good = path.read_text(encoding="utf-8")
        changes = []
        s.add_listener(changes.append)
        path.write_text(good[: len(good) // 2], encoding="utf-8")  # a typo / an editor caught mid-save
        with self.assertLogs("app.profiles", "WARNING") as logs:
            p = s.get("Game")
        self.assertIn("last good version", "\n".join(logs.output))
        self.assertEqual((p.name, [t.source for t in p.terms]), ("Game", ["セラ"]))  # not "default"
        self.assertEqual(s.names(), [DEFAULT_PROFILE, "Game"])
        self.assertEqual(s.match_process("game.exe"), "Game")
        self.assertEqual(changes, [])  # nothing to react to
        self.assertEqual(s.add_terms("Game", [Term("ガイア", "Gaia", "place")]), 0)  # the user's file is left alone
        self.assertEqual(path.read_text(encoding="utf-8"), good[: len(good) // 2])
        fixed = json.loads(good)
        fixed["terms"].append({"source": "レン", "target": "Ren", "kind": "person"})
        path.write_text(json.dumps(fixed, ensure_ascii=False), encoding="utf-8")
        self.assertEqual([t.source for t in s.get("Game").terms], ["セラ", "レン"])
        self.assertEqual(changes, ["Game"])
        path.write_text("{ broken", encoding="utf-8")
        with self.assertLogs("app.profiles", "WARNING"):
            s.get("Game")
        s.save(Profile("Game", terms=[Term("剣", "Kiếm", "item")]))  # an explicit save: the broken text kept aside
        self.assertEqual((self.dir / "game.json.bad").read_text(encoding="utf-8"), "{ broken")
        self.assertEqual([t.source for t in ProfileStore(self.dir).get("Game").terms], ["剣"])

    def test_root_must_be_object(self):
        with self.assertRaises(ValueError):
            profile_from_dict([1, 2])
        self.assertEqual(profile_from_dict({}, "x").name, "x")


# --- version / context ----------------------------------------------------------------------------

class VersionTests(unittest.TestCase):
    def test_version_changes_with_content_only(self):
        p = sample_profile()
        v = p.version()
        self.assertRegex(v, r"^[0-9a-f]{12}$")
        self.assertEqual(sample_profile().version(), v)  # stable
        same = sample_profile()
        same.name, same.processes, same.src_lang = "Khác", ["x.exe"], "en"
        same.terms.reverse()
        same.characters.reverse()
        self.assertEqual(same.version(), v)  # name, apps, language and row order do not matter
        variants = []
        for change in ("style", "term_target", "term_add", "term_auto", "term_note", "char_notes", "char_pronoun"):
            q = sample_profile()
            if change == "style":
                q.style += " Thêm."
            elif change == "term_target":
                q.terms[0] = Term("セラ", "Sela", "person", "nữ chính")
            elif change == "term_add":
                q.terms.append(Term("剣", "kiếm", "item"))
            elif change == "term_auto":
                q.terms[2] = Term("エルデン", "Elden", "place", auto=False)  # confirming changes the prompt
            elif change == "term_note":
                q.terms[1] = Term("魔導書", "ma đạo thư", "item", "sách phép")
            elif change == "char_notes":
                q.characters[0] = Character("セラ", "Sera", "nữ", "em", "gọi Ren là 'cậu', xưng 'tớ'")
            else:
                q.characters[1] = Character("レン", "Ren", "nam", "tao", "gọi Sera là 'em'; với thầy xưng 'con'")
            variants.append(q.version())
            self.assertNotEqual(q.version(), v, change)
        self.assertEqual(len(set(variants)), len(variants))

    def test_to_context(self):
        p = sample_profile()
        p.terms.insert(0, Term("自動", "tự động", auto=True))
        hist = (HistoryLine("セラ", "行こう", "Đi thôi anh"), HistoryLine(None, "うん", "Ừ"))
        ctx = p.to_context(hist)
        self.assertIsInstance(ctx, TranslationContext)
        self.assertEqual(ctx.profile, p.name)
        self.assertEqual(ctx.version, p.version())
        self.assertEqual(ctx.history, hist)
        self.assertEqual(ctx.style, p.style.strip())
        self.assertEqual([t.source for t in ctx.terms], ["セラ", "魔導書", "自動", "エルデン"])  # user terms first
        self.assertEqual([t.auto for t in ctx.terms], [False, False, True, True])
        self.assertEqual(ctx.characters, tuple(p.characters))
        self.assertEqual(Profile("x").to_context().history, ())


# --- process matching -----------------------------------------------------------------------------

class MatchProcessTests(_TempDirTest):
    def test_match_process(self):
        s = self.store()
        s.save(Profile("Game A", processes=["GameA.exe", "C:/Games/launcher.exe"]))
        s.save(Profile("Game B", processes=["gameb"]))  # without .exe: also matches the stem
        s.save(Profile("Trống"))
        self.assertEqual(s.match_process("gamea.EXE"), "Game A")
        self.assertEqual(s.match_process("D:\\Steam\\GameA.exe"), "Game A")
        self.assertEqual(s.match_process("Launcher.exe"), "Game A")
        self.assertEqual(s.match_process("GameB.exe"), "Game B")
        self.assertEqual(s.match_process("gameb"), "Game B")
        self.assertIsNone(s.match_process("gamea.exe.bak"))
        self.assertIsNone(s.match_process("mygamea.exe"))
        self.assertIsNone(s.match_process("notepad.exe"))
        self.assertIsNone(s.match_process(""))
        self.assertIsNone(s.match_process(None))
        s.save(Profile(DEFAULT_PROFILE, processes=["gamea.exe"]))
        self.assertEqual(s.match_process("GameA.exe"), "Game A")  # a named profile beats "default"


# --- shipped templates ----------------------------------------------------------------------------

TEMPLATES = Path(__file__).resolve().parent.parent / "profiles" / "templates"


class TemplateTests(unittest.TestCase):
    """profiles/templates/*.json: shippable profiles in the app's own format (read only here)."""

    def test_blue_archive(self):
        from app.translate.llm_prompt import address_pair, glossary_rendering, is_romanized_name  # noqa: PLC0415
        path = TEMPLATES / "blue_archive.json"
        store = ProfileStore(TEMPLATES)
        self.assertEqual(store.path_of("Blue Archive"), path)
        p = store.get("Blue Archive")
        self.assertEqual(path.read_text(encoding="utf-8"), profile_to_json(p))  # written by the store: round-trips
        self.assertEqual(store.match_process("D:\\Steam\\steamapps\\common\\BlueArchive\\BlueArchive.exe"),
                         "Blue Archive")
        self.assertEqual((p.src_lang, p.rejected, p.auto_terms()), ("ja", [], []))  # nothing from a user's play
        ctx = p.to_context()
        adults = {"先生", "黒服"}
        students = [c for c in p.characters if c.name not in adults]
        self.assertGreaterEqual(len(students), 40)
        self.assertLessEqual(len(p.terms), 80)  # llm_prompt.MAX_STABLE_TERMS: every term stays in the cached prefix
        for c in p.characters:
            with self.subTest(character=c.name):
                self.assertTrue(is_romanized_name(c.name_vi), c.name_vi)
                self.assertEqual(glossary_rendering(c.name, ctx), c.name_vi)
        for c in students:  # every student (and Arona, Plana): their own word, and "Sensei" for the teacher
            with self.subTest(character=c.name):
                self.assertEqual(address_pair(c.name, "先生", ctx), (c.self_pronoun, "Sensei", True))
                self.assertEqual(address_pair("先生", c.name, ctx), ("tôi", "em", True))
        self.assertEqual(address_pair("ホシノ", "先生", ctx), ("em", "Sensei", True))
        self.assertEqual(address_pair("アリス", "先生", ctx), ("Aris", "Sensei", True))
        self.assertEqual(address_pair("黒服", "先生", ctx), ("tôi", "Sensei", True))
        self.assertEqual(address_pair("先生", "黒服", ctx), ("tôi", "ông", True))  # an adult: not "em"
        # Between students no pair: the listener guess may be the last speaker while the line is for Sensei, and
        # a rule such as "gọi Hoshino là 'Hoshino-senpai'" then put the senpai into a line to Sensei (review E0)
        self.assertEqual(address_pair("セリカ", "ホシノ", ctx), (None, None, False))
        self.assertEqual(address_pair("ミドリ", "モモイ", ctx), (None, None, False))
        for source, target in (("先生", "Sensei"), ("シャーレ", "Schale"), ("キヴォトス", "Kivotos"),
                               ("ゲーム開発部", "CLB Phát triển Game"), ("便利屋68", "Problem Solver 68"),
                               ("アビドス高等学校", "Trường Abydos"), ("Arisu", "Aris"), ("黒服", "Black Suit"),
                               ("シロコ*テラー", "Shiroko Terror")):
            self.assertEqual(glossary_rendering(source, ctx), target)


# --- auto terms -----------------------------------------------------------------------------------

class AddTermsTests(_TempDirTest):
    def setUp(self):
        super().setUp()
        self.s = self.store()
        self.s.save(Profile("G", terms=[Term("セラ", "Sera", "person"), Term("Ren", "Ren", "person")],
                            characters=[Character("レン", "Ren", "nam"), Character("アリス", "Alice", "nữ")]))

    def terms(self) -> list[Term]:
        return ProfileStore(self.dir).get("G").terms

    def test_user_terms_and_characters_are_never_overwritten(self):
        proposals = [
            Term("セラ", "Sela", "person"),  # same source as a user term
            Term("「セラ」", "Sela", "person"),  # quotes stripped
            Term("セラさん", "chị Sera", "person"),  # honorific suffix
            Term("ﾚﾝ", "Lên", "person"),  # half-width katakana of a character name
            Term("ＲＥＮ", "Rên", "person"),  # full-width Latin of a user term
            Term("Ren-san", "anh Ren", "person"),
            Term("アリス様", "tiểu thư Alice", "person"),
        ]
        self.assertEqual(self.s.add_terms("G", proposals), 0)
        self.assertEqual(self.terms(), [Term("セラ", "Sera", "person"), Term("Ren", "Ren", "person")])

    def test_junk_is_filtered(self):
        junk = {
            "empty source": Term("", "x"), "empty target": Term("剣士", ""), "blank": Term("  ", "  "),
            "too long": Term("あ" * 41, "dài"), "target too long": Term("剣聖", "x" * 61),
            "untranslated": Term("魔導書", "魔導書"), "kana left": Term("カイト", "Kaiト"),
            "pronoun ja": Term("お前", "mày"), "pronoun en": Term("You", "Bạn"), "kinship": Term("お兄ちゃん", "anh hai"),
            "lowercase common": Term("sword", "kiếm"), "greeting": Term("Hello", "Xin chào"),
            "pronoun target": Term("彼氏", "anh"), "single char": Term("剣", "kiếm"), "no letters": Term("100", "100"),
            "sentence": Term("Run!", "Chạy!"), "multi-line": Term("セラ\nレン", "x"), "phrase": Term("I Will Go To The Sea", "x"),
        }
        self.assertEqual(self.s.add_terms("G", junk.values()), 0)
        for label, t in junk.items():
            self.assertIsNotNone(junk_reason(t), label)
        good = [Term("エルデン", "Elden", "place"), Term("Excalibur", "Excalibur", "item"),
                Term("魔導書", "ma đạo thư", "item"), Term("Ｋａｉｔｏ", "Kaito", "person")]
        for t in good:
            self.assertIsNone(junk_reason(t), t)
        self.assertEqual(self.s.add_terms("G", good), 4)
        added = [t for t in self.terms() if t.auto]
        self.assertEqual([(t.source, t.target) for t in added],
                         [("エルデン", "Elden"), ("Excalibur", "Excalibur"), ("魔導書", "ma đạo thư"), ("Kaito", "Kaito")])

    def test_dedupe_and_first_rendering_kept(self):
        self.assertEqual(self.s.add_terms("G", [Term("エルデン", "Elden", "place"), Term("ｴﾙﾃﾞﾝ", "Eruden", "place"),
                                                Term("エルデン", "Elden", "place")]), 1)
        self.assertEqual(self.s.add_terms("G", [Term("エルデン", "Erden", "place")]), 0)
        auto = [t for t in self.terms() if t.auto]
        self.assertEqual([(t.source, t.target, t.auto) for t in auto], [("エルデン", "Elden", True)])
        self.assertEqual(self.s.add_terms("G", []), 0)
        self.assertEqual(self.s.add_terms("G", ["not a term", None]), 0)
        # Honorific variants of an auto term are the same name (東雲くん after 東雲, and back).
        self.assertEqual(self.s.add_terms("G", [Term("東雲", "Shinonome", "person")]), 1)
        self.assertEqual(self.s.add_terms("G", [Term("東雲くん", "Higashiuno", "person"),
                                                Term("小鳥遊さん", "Takanashi", "person"),
                                                Term("小鳥遊", "Tiểu Điểu Du", "person")]), 1)
        auto = [(t.source, t.target) for t in self.terms() if t.auto]
        self.assertEqual(auto[1:], [("東雲", "Shinonome"), ("小鳥遊さん", "Takanashi")])

    def test_cap_drops_least_recently_proposed_auto_terms(self):
        s = self.store(auto_cap=5)
        names = [f"Hero{c}" for c in "ABCDEFGH"]
        self.assertEqual(s.add_terms("G", [Term(n, n, "person") for n in names[:5]]), 5)
        s.add_terms("G", [Term("HeroA", "HeroA", "person")])  # HeroA proposed again: recently seen
        self.assertEqual(s.add_terms("G", [Term(n, n, "person") for n in names[5:]]), 3)
        auto = [t.source for t in ProfileStore(self.dir).get("G").terms if t.auto]
        self.assertEqual(sorted(auto), ["HeroA", "HeroE", "HeroF", "HeroG", "HeroH"])  # B, C, D dropped
        user = [t.source for t in ProfileStore(self.dir).get("G").terms if not t.auto]
        self.assertEqual(user, ["セラ", "Ren"])  # user terms never count against the cap
        # a fresh store has no proposal history: oldest in the file goes first
        s2 = self.store(auto_cap=5)
        self.assertEqual(s2.add_terms("G", [Term("HeroZ", "HeroZ", "person")]), 1)
        auto = [t.source for t in ProfileStore(self.dir).get("G").terms if t.auto]
        self.assertEqual(len(auto), 5)
        self.assertIn("HeroZ", auto)
        # one batch bigger than the cap keeps its newest entries
        s3 = self.store(auto_cap=3)
        batch = [Term(f"Mage{c}", f"Mage{c}", "person") for c in "ABCDEF"]
        self.assertEqual(s3.add_terms("G", batch), 3)
        self.assertEqual([t.source for t in ProfileStore(self.dir).get("G").terms if t.auto], ["MageD", "MageE", "MageF"])

    def test_rejected_terms_are_not_proposed_again(self):
        p = self.s.get("G")
        p.rejected = [normalize_source("エルデン")]
        self.s.save(p)
        self.assertEqual(self.s.add_terms("G", [Term("エルデン", "Elden", "place"), Term("ガイア", "Gaia", "place")]), 1)
        self.assertEqual([t.source for t in self.terms() if t.auto], ["ガイア"])

    def test_listener_and_unknown_profile(self):
        got = []
        self.s.add_listener(got.append)
        self.s.add_listener(got.append)  # registered once
        self.assertEqual(self.s.add_terms("G", [Term("ガイア", "Gaia", "place")]), 1)
        self.assertEqual(got, ["G"])
        self.s.add_terms("G", [Term("ガイア", "Gaia", "place")])  # nothing new: no notification
        self.assertEqual(got, ["G"])
        self.assertEqual(self.s.add_terms("nope", [Term("ガイア", "Gaia", "place")]), 1)  # -> default
        self.assertEqual(got, ["G", DEFAULT_PROFILE])
        self.s.save(self.s.get("G"))
        self.s.create("New")
        self.s.delete("New")
        self.assertEqual(got, ["G", DEFAULT_PROFILE, "G", "New", "New"])

        def boom(_name):
            raise RuntimeError("listener bug")

        self.s.add_listener(boom)
        with self.assertLogs("app.profiles", "ERROR"):
            self.s.create("Other")  # a failing listener is logged, not raised
        self.s.remove_listener(boom)

    def test_normalize_source(self):
        self.assertEqual(normalize_source("ｾﾗ"), normalize_source("セラ"))
        self.assertEqual(normalize_source("ＲＥＮ"), "ren")
        self.assertEqual(normalize_source("『セラ』"), "セラ")
        self.assertEqual(normalize_source("  Ren   Ito "), "ren ito")
        self.assertEqual(normalize_source("セ ラ"), "セラ")  # OCR space between kana


class ThreadSafetyTests(_TempDirTest):
    def test_many_threads(self):
        s = self.store(auto_cap=10_000)
        s.create("T")
        calls = []
        s.add_listener(calls.append)
        pool = [f"Name{i:03d}" for i in range(300)]
        results: list[int] = []
        errors: list[BaseException] = []
        lock = threading.Lock()
        barrier = threading.Barrier(16)

        def worker(seed: int) -> None:
            rnd = random.Random(seed)
            try:
                barrier.wait()
                for _ in range(15):
                    batch = [Term(n, n, "person") for n in rnd.sample(pool, 8)]
                    n = s.add_terms("T", batch)
                    s.get("T").to_context()
                    s.names()
                    s.match_process("x.exe")
                    with lock:
                        results.append(n)
            except BaseException as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        terms = ProfileStore(self.dir).get("T").terms
        sources = [t.source for t in terms]
        self.assertEqual(len(sources), len(set(sources)))  # no duplicates
        self.assertEqual(sum(results), len(sources))  # every add counted exactly once
        self.assertEqual(len(calls), sum(1 for n in results if n))
        json.loads((self.dir / "t.json").read_text(encoding="utf-8"))  # never a torn file
        self.assertEqual(list(self.dir.glob("*.tmp")), [])


# --- dialogue memory ------------------------------------------------------------------------------

class MemoryTests(unittest.TestCase):
    def test_add_recent_order_and_maxlen(self):
        m = DialogueMemory(maxlen=3)
        for i in range(5):
            m.add("セラ" if i % 2 else None, f"line {i}", f"dòng {i}")
        self.assertEqual(len(m), 3)
        self.assertEqual([h.source for h in m.recent(10)], ["line 2", "line 3", "line 4"])
        self.assertEqual([h.source for h in m.recent(2)], ["line 3", "line 4"])  # oldest first
        self.assertEqual(m.recent(0), ())
        self.assertEqual(m.recent(-1), ())
        self.assertIsInstance(m.recent(1)[0], HistoryLine)
        m.clear()
        self.assertEqual(m.recent(5), ())

    def test_repeats(self):
        m = DialogueMemory()
        m.add("セラ", "行こう", "Đi thôi")
        m.add("セラ", "行こう", "Đi thôi")  # exact repeat: ignored
        m.add(None, " 行こう ", "")  # same line seen again without new information
        self.assertEqual(m.recent(5), (HistoryLine("セラ", "行こう", "Đi thôi"),))
        m.add("セラ", "行こう", "Đi thôi anh")  # LLM refined the Google translation: replaced
        self.assertEqual(m.recent(5), (HistoryLine("セラ", "行こう", "Đi thôi anh"),))
        m.add("レン", "行こう", "Đi nào")  # another speaker says the same thing: a new line
        m.add(None, "", "nothing")  # empty source ignored
        self.assertEqual([(h.speaker, h.translation) for h in m.recent(5)], [("セラ", "Đi thôi anh"), ("レン", "Đi nào")])
        m.add(None, "はい", "Vâng")
        m.add("セラ", "はい", "Vâng ạ")  # speaker learned later: merged into the last line
        self.assertEqual(m.recent(1), (HistoryLine("セラ", "はい", "Vâng ạ"),))
        m.add(None, "行こう", "Đi")  # not the last line any more: a new entry
        self.assertEqual(len(m), 4)

    def test_a_pause_ends_the_conversation(self):
        now = [0.0]
        m = MemoryBank(clock=lambda: now[0]).get("G")
        m.add("健太", "昨日のバイトどうだった？", "Hôm qua làm thêm sao rồi?")
        now[0] = 5.0
        m.add("竜二", "最悪だった。", "Tệ lắm.")
        now[0] = 400.0  # a new scene, minutes later
        m.add("竜二", "すみません、駅はどこですか？", "Xin lỗi, nhà ga ở đâu ạ?")
        self.assertEqual([h.source for h in m.recent(12, max_gap_s=180)], ["すみません、駅はどこですか？"])
        self.assertEqual(len(m.recent(12)), 3)  # without a gap limit: everything
        now[0] = 700.0
        self.assertEqual(m.recent(12, max_gap_s=180), ())  # the last line itself is stale

    def test_threads_and_bank(self):
        m = DialogueMemory(maxlen=1000)

        def worker(k: int) -> None:
            for i in range(200):
                m.add(f"s{k}", f"{k}-{i}", "x")
                m.recent(12)

        threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(len(m), 1000)
        bank = MemoryBank(maxlen=5)
        a, b = bank.get("A"), bank.get("B")
        self.assertIs(bank.get("A"), a)
        self.assertIsNot(a, b)
        a.add(None, "x", "y")
        self.assertEqual(len(b), 0)
        bank.clear_all()
        self.assertEqual(len(a), 0)


if __name__ == "__main__":
    unittest.main()
