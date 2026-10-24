"""Tests for the game-lookup UI: new-game detection, the tray entries, the consent / lookup / request / import /
SearXNG-install windows (app.ui.lookup_dialog), the Settings "Tra cứu game" tab and the App wiring (app.main).

Run: python -m unittest tests.test_lookup_ui -v

No network: every real httpx transport fails the test; lookups answer from the fixtures recorded from the real
APIs (tests/data/lookup/). Logic runs on Qt's offscreen platform in this process. Capture-exclusion readback on
real HWNDs and the PNG previews (bench/results/preview/lookup_*.png, settings_lookup.png) run in a child process
on the windows platform (WindowsPlatformSubprocessTests), skipped while an anti-cheat game owns the foreground or
with SCREEN_TRANSLATOR_SKIP_GUI_TESTS=1. Nothing is shown on screen and no OS input is synthesized.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx
from PySide6.QtCore import QCoreApplication, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QShowEvent
from PySide6.QtWidgets import QAbstractButton, QLabel

from app.config import PROJECT_ROOT, Config
from app.lookup.game_id import identify, identity_from_title
from app.lookup.http import FixtureTransport, HttpClient
from app.lookup.service import LookupService
from app.lookup.types import GameCandidate, GameIdentity, LookupReport, PatchLink, SourceStatus
from app.profiles import Profile
from app.translate.contextual import Character, Term
from tests import test_app as _ta
from tests import test_settings_ui as _su
from tests.test_settings_ui import NATIVE, PREVIEW_DIR, flush_deletes, on_windows_platform, pump, pump_until, qapp

DATA = PROJECT_ROOT / "tests" / "data" / "lookup"
OFFLINE = ("steam", "wikidata", "vndb", "wiki")
ENGLISH = {"save", "cancel", "default", "settings", "apply", "ok", "close", "browse", "reset", "open", "search",
           "lookup", "import", "request", "source", "sources", "status", "install", "stop", "start", "back"}
_patcher = None


def setUpModule():
    global _patcher

    def _no_network(self, request):
        raise AssertionError(f"unit tests must not use the network: {request.url}")
    _patcher = mock.patch.object(httpx.HTTPTransport, "handle_request", _no_network)
    _patcher.start()


def tearDownModule():
    _patcher.stop()


class FixtureFactory:
    """make_lookup_service stand-in: a LookupService answering from recorded fixtures; remembers every client."""

    def __init__(self, name: str = "steins_gate", sources=OFFLINE) -> None:
        self.name, self.sources = name, sources
        self.transports: list[FixtureTransport] = []

    def __call__(self) -> LookupService:
        t = FixtureTransport(DATA / self.name)
        self.transports.append(t)
        return LookupService(client=HttpClient(cache_dir=None, transport=t), sources=self.sources)

    def urls(self) -> list[str]:
        return [u for t in self.transports for u in t.seen]


class CountingFactory:
    """A LookupService whose transport counts requests and answers `answer(request)`."""

    def __init__(self, answer=None) -> None:
        self.requests: list[httpx.Request] = []
        self.lock = threading.Lock()
        self.answer = answer or (lambda r: httpx.Response(500, json={}))

    def __call__(self) -> LookupService:
        def handler(request):
            with self.lock:
                self.requests.append(request)
            return self.answer(request)
        return LookupService(client=HttpClient(cache_dir=None, transport=httpx.MockTransport(handler)), sources=OFFLINE)


_REPORT: LookupReport | None = None


def steins_report() -> LookupReport:
    global _REPORT
    if _REPORT is None:
        _REPORT = FixtureFactory()().run(identity_from_title("Steins;Gate", "STEINS;GATE.exe"))
    return copy.deepcopy(_REPORT)


def make_game(root: Path, folder: str, exe: str, markers=("UnityPlayer.dll",)) -> Path:
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / exe).write_bytes(b"MZ")
    for m in markers:
        if m.endswith("/"):
            (d / m.rstrip("/")).mkdir(exist_ok=True)
        else:
            (d / m).write_bytes(b"")
    return d / exe


def visible_words(widget) -> set[str]:
    out: set[str] = set()
    for w in widget.findChildren(QLabel) + widget.findChildren(QAbstractButton):
        out |= {x.strip(".…:()“”,;").lower() for x in w.text().split()}
    return out


# --- detection (files only) ---------------------------------------------------------------------------------

class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="st-lookup-ui-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))

    def test_non_games(self):
        from app.ui.lookup_dialog import is_non_game
        for exe in ("explorer.exe", "chrome.exe", "msedge.exe", "Discord.exe", "Code.exe", "claude.exe",
                    "pythonw.exe", "WindowsTerminal.exe", "steam.exe", "", "noext"):
            self.assertTrue(is_non_game(exe), exe)
        self.assertTrue(is_non_game("svchost.exe", r"C:\Windows\System32\svchost.exe"))
        self.assertFalse(is_non_game("STEINS;GATE.exe", r"D:\Games\STEINS;GATE\STEINS;GATE.exe"))

    def test_looks_like_new_game_reads_files_only(self):
        from app.ui.lookup_dialog import looks_like_new_game
        unity = make_game(self.tmp, "Lost Tale", "LostTale.exe")
        ident = identify(str(unity))
        self.assertEqual(ident.engine, "Unity")
        self.assertTrue(looks_like_new_game(ident, str(unity)))
        nw = make_game(self.tmp, "Sky Novel", "SkyNovel.exe", markers=("package.nw",))
        ident = identify(str(nw))
        self.assertTrue(looks_like_new_game(ident, str(nw)))
        self.assertEqual(ident.engine, "NW.js")
        launcher = make_game(self.tmp / "Epic Games", "Neon Run", "NeonRun.exe", markers=())
        self.assertTrue(looks_like_new_game(identify(str(launcher)), str(launcher)))
        plain = make_game(self.tmp / "Tools", "Editor", "Editor.exe", markers=())
        self.assertFalse(looks_like_new_game(identify(str(plain)), str(plain)))
        self.assertFalse(looks_like_new_game(GameIdentity(exe_name="Game.exe", engine="Unity"), ""))  # no title

    def test_guard_reports_the_full_path_locally(self):
        from app.ui.game_guard import GameGuard, process_image_path
        self.assertEqual(Path(process_image_path(os.getpid())).name.lower(), Path(sys.executable).name.lower())
        self.assertIsNone(process_image_path(0))
        g = GameGuard(Config(guard_mode="off"), reader=lambda: None, use_event_hook=False)
        self.addCleanup(g.deleteLater)
        seen: list[str] = []
        g.foreground_changed.connect(seen.append)
        g.evaluate(r"D:\Games\Lost Tale\LostTale.exe", 4321)
        self.assertEqual(seen, ["LostTale.exe"])  # the signal still carries the basename
        self.assertEqual(g.foreground_path, r"D:\Games\Lost Tale\LostTale.exe")
        g.evaluate("notepad.exe", 4322)
        self.assertEqual(g.foreground_path, "")


# --- tray ---------------------------------------------------------------------------------------------------

class TrayLookupTests(unittest.TestCase):
    def setUp(self):
        qapp()
        self.addCleanup(flush_deletes)
        from app.ui.tray import TrayController
        self.tray = TrayController(Config())
        self.addCleanup(self.tray.menu().deleteLater)
        self.addCleanup(self.tray.deleteLater)

    def test_entries_and_signals(self):
        texts = self.tray.action_texts()
        i = texts.index("Tra cứu game")
        self.assertEqual(texts[i + 1:i + 5], ["  Tra cứu game…", "  Tạo yêu cầu tra cứu cho Claude…",
                                              "  Nhập hồ sơ từ file…", "  Dán hồ sơ từ clipboard…"])
        sub = [a.text() for a in self.tray._profile_menu.actions()]
        self.assertEqual(sub[-2:], ["Tạo hồ sơ mới…", "Sửa thuật ngữ / nhân vật…"])  # unchanged
        got: list = []
        self.tray.lookup_game.connect(lambda k: got.append(("lookup", k)))
        self.tray.lookup_request.connect(lambda: got.append(("request",)))
        self.tray.import_profile.connect(lambda how: got.append(("import", how)))
        for a in (self.tray._lookup_action, self.tray._request_action, self.tray._import_file_action,
                  self.tray._import_clip_action):
            a.trigger()
        self.assertEqual(got, [("lookup", ""), ("request",), ("import", "file"), ("import", "clipboard")])
        self.assertIs(self.tray._lookup_exclusion._widget, self.tray._lookup_menu)  # capture-excluded

    def test_pending_games_and_balloon_token(self):
        self.tray.set_pending_games([("exe:steins;gate.exe", "Steins;Gate"), ("exe:r&d.exe", "R&D")])
        texts = self.tray.action_texts()
        self.assertIn("Tra cứu game (2 game mới)", texts)
        self.assertIn("  Game mới: Steins;Gate…", texts)
        self.assertIn("  Game mới: R&&D…", texts)
        got: list[str] = []
        self.tray.lookup_game.connect(got.append)
        self.tray._pending_actions[0].trigger()
        self.assertEqual(got, ["exe:steins;gate.exe"])
        self.tray.set_pending_games([])
        self.assertIn("Tra cứu game", self.tray.action_texts())
        self.assertFalse(any(t.startswith("  Game mới") for t in self.tray.action_texts()))
        clicked: list[str] = []
        self.tray.message_clicked.connect(clicked.append)
        with mock.patch.object(self.tray.tray_icon(), "showMessage"):
            self.tray.show_message("Game mới: X", "Bấm để tra cứu", token="lookup:exe:x.exe")
            self.tray.tray_icon().messageClicked.emit()  # what a click on the balloon does
            self.tray.show_message("Screen Translator", "khác")
            self.tray.tray_icon().messageClicked.emit()
        self.assertEqual(clicked, ["lookup:exe:x.exe", ""])


# --- windows (offscreen) ------------------------------------------------------------------------------------

class ConsentDialogTests(unittest.TestCase):
    def setUp(self):
        qapp()
        self.addCleanup(flush_deletes)

    def dialog(self, ident=None, sources=("steam", "wikidata", "vndb", "wiki", "web"), **kw):
        from app.ui.lookup_dialog import ConsentDialog
        ident = ident or GameIdentity(exe_name="STEINS;GATE.exe", titles=["STEINS;GATE", "Steins Gate"],
                                      steam_appid=412830, engine="Unity")
        d = ConsentDialog(ident, list(sources), **kw)
        self.addCleanup(d.deleteLater)
        return d

    def test_names_what_is_sent_and_where(self):
        d = self.dialog(web_target="SearXNG trên máy này (127.0.0.1)")
        self.assertIn("Game mới: STEINS;GATE", d.head_label.text())
        self.assertIn("Steam 412830", d.head_label.text())
        text = d.sent_label.text()
        for host in ("store.steampowered.com", "www.wikidata.org", "api.vndb.org", "Fandom", "SearXNG trên máy này"):
            self.assertIn(host, text)
        self.assertIn("“STEINS;GATE”, “Steins Gate”, mã Steam 412830", text)  # the id from the Steam files too
        self.assertEqual(d.titles(), ["STEINS;GATE", "Steins Gate"])
        d.title_edit.setText("Steins;Gate")  # the same name in another case: not sent twice
        self.assertEqual(d.titles(), ["Steins;Gate", "Steins Gate"])
        self.assertIn("“Steins;Gate”", d.sent_label.text())
        d.title_edit.setText("Steins;Gate Elite")  # a corrected title: only it (the app's guesses are dropped)
        self.assertEqual(d.titles(), ["Steins;Gate Elite"])
        self.assertNotIn("Steins Gate”", d.sent_label.text())
        self.assertEqual(d.no_button.text(), "Để sau")
        self.assertFalse(visible_words(d) & ENGLISH, visible_words(d) & ENGLISH)

    def test_answers(self):
        d = self.dialog()
        got: list = []
        d.lookup.connect(lambda t: got.append(("lookup", t)))
        d.declined.connect(lambda never: got.append(("declined", never)))
        d.request_instead.connect(lambda t: got.append(("request", t)))
        d.show()
        d.lookup_button.click()
        d.show()
        d.no_button.click()
        d.show()
        d.never_button.click()
        d.show()
        d.request_button.click()
        self.assertEqual(got, [("lookup", "STEINS;GATE"), ("declined", False), ("declined", True),
                               ("request", "STEINS;GATE")])

    def test_manual_and_no_web(self):
        d = self.dialog(GameIdentity(), sources=("steam", "web"), manual=True)
        self.assertTrue(d.never_button.isHidden())  # only for a game the app found
        self.assertFalse(d.lookup_button.isEnabled())  # no title yet
        self.assertEqual(d.sent_label.text(), "Gõ tên game ở trên.")
        d.title_edit.setText("  Clannad  ")
        self.assertTrue(d.lookup_button.isEnabled())
        self.assertEqual(d.titles(), ["Clannad"])
        self.assertIn("bỏ qua (chưa có SearXNG", d.sent_label.text())
        self.assertFalse(self.dialog(sources=()).lookup_button.isEnabled())


class LookupDialogTests(unittest.TestCase):
    def setUp(self):
        qapp()
        self.addCleanup(flush_deletes)
        self.applied: list[tuple[Profile, str]] = []

    def apply(self, profile: Profile, name: str) -> str:
        self.applied.append((profile, name))
        return f"Đã tạo hồ sơ “{name}”"

    def dialog(self, ident=None, factory=None, sources=OFFLINE, **kw):
        from app.ui.lookup_dialog import LookupDialog
        ident = ident or identity_from_title("Steins;Gate", "STEINS;GATE.exe")
        kw.setdefault("exists", lambda _n: None)
        d = LookupDialog(ident, make_service=factory or FixtureFactory(), sources=list(sources), apply=self.apply, **kw)
        self.addCleanup(d.deleteLater)
        return d

    def run_dialog(self, d) -> LookupReport:
        got: list[LookupReport] = []
        d.report_ready.connect(got.append)
        d.start()
        self.assertTrue(pump_until(lambda: bool(got), 20), "the lookup did not finish")
        return got[0]

    def test_worker_progress_draft_and_apply(self):
        factory = FixtureFactory()
        d = self.dialog(factory=factory, active_profile="default")
        statuses: list[str] = []
        d.source_tree.itemChanged.connect(lambda item, col: col == 1 and statuses.append(item.text(1)))
        report = self.run_dialog(d)
        self.assertEqual(factory.transports[0].missing, [])
        self.assertEqual((report.title, report.confidence), ("Steins;Gate", "high"))
        self.assertIn("đang tra…", statuses)  # progress reached the GUI thread while it ran
        rows = {d.source_tree.topLevelItem(i).text(0): d.source_tree.topLevelItem(i).text(1)
                for i in range(d.source_tree.topLevelItemCount())}
        self.assertEqual(rows, {"Steam": "xong", "Wikidata": "xong", "VNDB": "xong", "Wiki game": "xong"})
        self.assertIn("Hồ sơ nháp: 15 nhân vật, 22 thuật ngữ", d.summary_label.text())
        tree = d.source_tree  # every source row fits (no scroll bar hiding the last one)
        self.assertGreaterEqual(tree.height(), tree.header().sizeHint().height() + 4 * tree.fontMetrics().height())
        view = d.panel.view
        self.assertEqual(view.counts(), (15, 22))
        self.assertEqual(len(view.items_of("patches")), 2)
        self.assertFalse(d.panel.patch_label.isHidden())  # the "app never downloads" warning
        self.assertTrue(view.items_of("sources"))
        kurisu = next(i for i in view.items_of("chars") if i.text(0) == "牧瀬紅莉栖")
        self.assertEqual(kurisu.text(1), "Makise Kurisu")
        kurisu.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(view.counts(), (14, 22))
        self.assertEqual(d.panel.create_button.text(), "Tạo hồ sơ “Steins;Gate”")
        self.assertTrue(d.panel.merge_button.isHidden())  # never into "Mặc định": every other game uses it
        d.panel.create_button.click()
        [(profile, name)] = self.applied
        self.assertEqual(name, "Steins;Gate")
        self.assertNotIn("牧瀬紅莉栖", [c.name for c in profile.characters])  # unchecked: not saved
        self.assertNotIn("牧瀬紅莉栖", profile.lookup["review"]["characters"])
        self.assertEqual(len(profile.characters), 14)
        self.assertIn("STEINS;GATE.exe", profile.processes)
        self.assertIn("Đã tạo hồ sơ", d.panel.status_label.text())
        self.assertFalse(d.panel.edit_button.isHidden())
        for url in factory.urls():  # only the title went out
            self.assertNotIn(".exe", url.casefold())

    def test_nicknames_only_on_request(self):
        d = self.dialog()
        self.run_dialog(d)
        self.assertFalse(d.alias_check.isHidden())
        self.assertFalse(d.alias_check.isChecked())  # off: VNDB has no spoiler flag on them
        terms = {i.text(0) for i in d.panel.view.items_of("terms")}
        self.assertNotIn("オカリン", terms)
        d.panel.name_edit.setText("SG của tôi")
        d.alias_check.setChecked(True)
        terms = {i.text(0) for i in d.panel.view.items_of("terms")}
        self.assertIn("オカリン", terms)
        self.assertNotIn("ジョン・タイター", terms)  # a full name / true identity: never
        self.assertEqual(d.panel.name_edit.text(), "SG của tôi")  # the name the user typed stays
        self.assertIn("Hồ sơ nháp: 15 nhân vật, 29 thuật ngữ (trong đó 8 gợi ý", d.summary_label.text())
        self.assertEqual(d.draft.lookup["aliases"]["オカリン"], "岡部倫太郎")

    def test_a_failing_save_is_shown(self):
        def broken(_profile, _name):
            raise TypeError("odd file")
        d = self.dialog()
        self.run_dialog(d)
        d.panel._apply = broken
        d.panel.create_button.click()  # no exception escapes the Qt slot
        self.assertIn("Không lưu được (TypeError)", d.panel.status_label.text())

    def test_patch_links_open_only_on_click(self):
        d = self.dialog()
        with mock.patch("app.ui.lookup_dialog.QDesktopServices.openUrl", return_value=True) as opened:
            self.run_dialog(d)
            self.assertFalse(opened.called)
            labels = d.panel.view.link_labels()
            self.assertTrue(labels)
            url = labels[0].toolTip()
            labels[0].linkActivated.emit(url)  # the user clicks the link
            [(args, _)] = opened.call_args_list
            self.assertEqual(args[0].toString(), url)
            from app.ui.lookup_dialog import open_link
            self.assertFalse(open_link("file:///C:/Windows/notepad.exe"))  # only http(s)
            self.assertEqual(len(opened.call_args_list), 1)

    def test_existing_entries_are_shown_kept(self):
        mine = Profile("Steins;Gate", characters=[Character("牧瀬紅莉栖", "Kurisu", "nữ", "tôi", "trợ lý")],
                       terms=[Term("秋葉原", "Akiba", "place")], rejected=["オカリン"])
        d = self.dialog(exists=lambda n: mine if n.casefold() == "steins;gate" else None)
        self.run_dialog(d)
        kept = [i.text(0) for i in d.panel.view.items_of("chars") if i.text(2) == "đã có trong hồ sơ, giữ nguyên"]
        self.assertEqual(kept, ["牧瀬紅莉栖"])
        self.assertEqual(d.panel.view.counts()[0], 14)  # what would be added
        self.assertEqual(d.panel.create_button.text(), "Thêm vào hồ sơ “Steins;Gate”")
        from app.lookup.profile_builder import merge_into
        merged, stats = merge_into(mine, d.panel.view.selected_profile())
        self.assertIn("牧瀬紅莉栖", stats.kept_characters)  # the user's version stays
        self.assertEqual(next(c for c in merged.characters if c.name == "牧瀬紅莉栖").name_vi, "Kurisu")
        self.assertNotIn("オカリン", [t.source for t in merged.terms])  # a suggestion the user deleted

    def test_cancel_on_close(self):
        started, saw_cancel = threading.Event(), threading.Event()

        class Slow:
            def run(self, ident, sources, progress_cb=None, cancel_event=None):
                progress_cb(SourceStatus("steam"))
                started.set()
                if cancel_event.wait(10):
                    saw_cancel.set()
                return LookupReport(query=ident.title, identity=ident, cancelled=True)

            def close(self):
                pass

        d = self.dialog(factory=lambda: Slow())
        d.show()
        d.start()
        self.assertTrue(started.wait(5))
        self.assertTrue(d.is_running())
        d.close()  # closing the window cancels the run
        self.assertTrue(saw_cancel.wait(5))

    def test_template_without_network(self):
        factory = CountingFactory()
        d = self.dialog(identity_from_title("Blue Archive", "BlueArchive.exe"), factory=factory, sources=())
        report = self.run_dialog(d)
        self.assertEqual(factory.requests, [])
        self.assertTrue(report.template)
        self.assertEqual(d.panel.view.counts(), (107, 73))
        self.assertIn("Không gửi gì lên mạng", d.sent_label.text())
        self.assertIn("hồ sơ mẫu", d.summary_label.text().lower())

    def test_nothing_found(self):
        def empty(request):
            url = str(request.url)
            if "storesearch" in url:
                return httpx.Response(200, json={"total": 0, "items": []})
            if "wikidata" in url:
                return httpx.Response(200, json={"search": []})
            return httpx.Response(200, json={"results": [], "more": False})
        d = self.dialog(identity_from_title("Zzqx Unknown Game", "Zzqx.exe"), factory=CountingFactory(empty))
        report = self.run_dialog(d)
        self.assertFalse(report.found_anything)
        self.assertIn("Không tìm thấy dữ liệu nào", d.summary_label.text())
        self.assertIn("App vẫn dịch như bình thường", d.summary_label.text())
        self.assertFalse(d.panel.create_button.isEnabled())

    def test_unsure_match_offers_alternatives(self):
        titles: list[list[str]] = []

        class Fake:
            def run(self, ident, sources, progress_cb=None, cancel_event=None):
                titles.append(ident.query_titles())
                return LookupReport(query=ident.title, identity=ident, title="Steins;Gate 0", confidence="low",
                                    alternatives=[GameCandidate("vndb", "Steins;Gate", 0.8, description="2009")])

            def close(self):
                pass

        d = self.dialog(identity_from_title("steins gate", "SG.exe"), factory=lambda: Fake())
        self.run_dialog(d)
        self.assertFalse(d.alt_widget.isHidden())
        self.assertEqual(d.alt_combo.currentData(), "Steins;Gate")
        self.assertIsNone(d.draft)  # nothing to save until the right game is picked
        self.assertTrue(d.panel.isHidden())
        self.assertIn("Chưa chắc đúng game", d.summary_label.text())
        self.assertIn("Có phải bạn tìm: Steins;Gate?", d.summary_label.text())
        got: list = []
        d.report_ready.connect(got.append)
        d.alt_button.click()  # look the alternative up instead
        self.assertTrue(pump_until(lambda: bool(got), 5))
        self.assertEqual(titles, [["steins gate"], ["Steins;Gate"]])  # the picked title alone


class RequestImportTests(unittest.TestCase):
    def setUp(self):
        qapp()
        self.addCleanup(flush_deletes)
        self.tmp = Path(tempfile.mkdtemp(prefix="st-lookup-ui-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))

    def test_request_copy_and_save(self):
        from app.lookup.claude_request import build_request
        from app.ui.lookup_dialog import RequestDialog
        text = build_request(steins_report())
        d = RequestDialog(text, title="Steins;Gate")
        self.addCleanup(d.deleteLater)
        self.assertEqual(d.text(), text)
        d.copy()
        self.assertEqual(QGuiApplication.clipboard().text(), text)
        self.assertEqual(d.copy_button.text(), "Đã sao chép")
        out = self.tmp / "req.md"
        self.assertEqual(d.save_to_file(str(out)), str(out))
        self.assertEqual(out.read_text(encoding="utf-8"), text)
        with mock.patch("app.ui.lookup_dialog.pick_file", return_value="") as pick:
            self.assertEqual(d.save_to_file(), "")  # cancelled
        self.assertTrue(pick.call_args.kwargs["save"])
        self.assertIn("Steins;Gate - yeu cau tra cuu.md", pick.call_args.kwargs["name"])
        self.assertFalse(visible_words(d) & ENGLISH)

    def test_file_dialog_is_qt_vietnamese_and_keeps_other_texts(self):
        from app.ui import lookup_dialog as ld
        d = ld.make_file_dialog(None, save=True, title="Lưu yêu cầu tra cứu", name="a.md", filters=ld.MD_FILTER)
        self.addCleanup(d.deleteLater)
        self.assertTrue(d.testOption(d.Option.DontUseNativeDialog))  # a Qt window: capture-excluded before it shows
        self.assertIsNotNone(getattr(d, "_capture_exclusion", None))
        self.assertEqual(d.labelText(d.DialogLabel.Accept), "Lưu")
        self.assertEqual(d.labelText(d.DialogLabel.Reject), "Hủy")
        tr = QCoreApplication.translate
        self.assertEqual(tr("QFileDialog", "Back"), "Quay lại")
        self.assertEqual(tr("QFileSystemModel", "Name"), "Tên")
        self.assertEqual(tr("QPlatformTheme", "Cancel"), "Hủy")  # app.ui.qt_vi's texts still answer
        self.assertEqual(tr("SomethingElse", "Hello"), "Hello")  # never blanked
        with mock.patch("app.ui.lookup_dialog.QFileDialog.exec", return_value=0):
            self.assertEqual(ld.pick_file(None, title="Mở tệp hồ sơ"), "")  # cancelled

    def test_import_paste_file_and_errors(self):
        from app.ui.lookup_dialog import ImportDialog
        applied: list = []
        d = ImportDialog(apply=lambda p, n: applied.append((p, n)) or "ok", exists=lambda n: None,
                         active_profile="default", exe_name="SG.exe")
        self.addCleanup(d.deleteLater)
        self.assertFalse(d.parse_text("không có gì"))
        self.assertIn("Không tìm thấy hồ sơ JSON", d.info_label.text())
        answer = ("Đây là hồ sơ:\n```json\n" + json.dumps({
            "name": "Steins;Gate", "src_lang": "ja",
            "characters": [{"name": "牧瀬紅莉栖", "name_vi": "Makise Kurisu", "gender": "nữ"},
                           {"name": "Okabe", "name_vi": "Okabe Rintarou"}],
            "terms": [{"source": "秋葉原", "target": "Akihabara", "kind": "place"}],
            "lookup": {"patches": [{"title": "Việt hóa", "url": "https://example.org/sg"}]}}, ensure_ascii=False)
            + "\n```\nChúc vui!")
        QGuiApplication.clipboard().setText(answer)
        self.assertTrue(d.paste_clipboard())
        self.assertEqual(d.panel.view.counts(), (2, 1))
        self.assertIn("không có tên chữ Nhật", d.info_label.text())  # Okabe: the app cannot spot it in JA text
        self.assertIn("Gắn với game SG.exe", d.info_label.text())  # the binding is shown
        self.assertEqual(len(d.panel.view.items_of("web_patches")), 1)  # an assistant's link: unverified
        self.assertIn("SG.exe", d.draft.processes)
        d.panel.create_button.click()
        self.assertEqual(applied[0][1], "Steins;Gate")
        path = self.tmp / "sg.json"
        path.write_text(answer.split("```json\n")[1].split("```")[0], encoding="utf-8-sig")
        self.assertTrue(d.choose_file(str(path)))
        self.assertIn("Đã đọc sg.json", d.info_label.text())
        QGuiApplication.clipboard().setText("")
        self.assertFalse(d.paste_clipboard())
        self.assertIn("Clipboard đang trống", d.info_label.text())


class SearxngInstallDialogTests(unittest.TestCase):
    def setUp(self):
        qapp()
        self.addCleanup(flush_deletes)

    def test_runs_in_the_background_and_reports(self):
        from app.ui.lookup_dialog import SearxngInstallDialog, setup_command
        cmd = setup_command()
        # the app writes searxng_url; the check after the install searches nothing (no query goes out)
        self.assertEqual(cmd[1:], ["-u", "-m", "tools.setup_searxng", "--no-config", "--no-search"])
        self.assertEqual(Path(cmd[0]).name.lower(), "python.exe")
        stopped: list[bool] = []
        fake = [sys.executable, "-c", "print('Cài SearXNG'); print('[1/5] venv'); print('[2/5] mã nguồn')"]
        d = SearxngInstallDialog(command=fake, before_start=lambda: stopped.append(True))
        self.addCleanup(d.deleteLater)
        self.assertEqual(d.status_label.text(), "Chưa bắt đầu.")  # never starts by itself
        done: list = []
        d.install_finished.connect(lambda ok, port: done.append((ok, port)))
        with mock.patch("app.lookup.searxng.installed", return_value=True), \
                mock.patch("app.lookup.searxng.install_info", return_value={"port": 8890, "commit": "x"}):
            self.assertTrue(d.start())
            self.assertTrue(pump_until(lambda: bool(done), 20))
        self.assertEqual(stopped, [True])  # the app's SearXNG is stopped first
        self.assertEqual(done, [(True, 8890)])
        self.assertEqual(d.log_view.toPlainText().splitlines(), ["Cài SearXNG", "[1/5] venv", "[2/5] mã nguồn"])
        self.assertIn("Đã cài xong", d.status_label.text())
        self.assertEqual(d.start_button.text(), "Cài lại")

    def test_stop(self):
        from app.ui.lookup_dialog import SearxngInstallDialog
        d = SearxngInstallDialog(command=[sys.executable, "-c", "import time; print('[1/5] venv', flush=True); "
                                          "time.sleep(30)"])
        self.addCleanup(d.deleteLater)
        done: list = []
        d.install_finished.connect(lambda ok, port: done.append(ok))
        d.start()
        self.assertTrue(pump_until(lambda: "[1/5]" in d.log_view.toPlainText(), 15))
        d.ask_before_stop = False
        d.show()
        d.close()  # closing while it installs stops it
        self.assertTrue(pump_until(lambda: bool(done), 15))
        self.assertEqual(done, [False])
        self.assertIn("Đã dừng", d.status_label.text())


# --- Settings tab -----------------------------------------------------------------------------------------------

class SettingsLookupTabTests(_su._DialogFixture):
    def test_tab_round_trip_and_dirty(self):
        cfg = Config(lookup_ask_new_games=False, lookup_sources=["vndb", "custom", "steam"],
                     searxng_url="http://127.0.0.1:8890/", searxng_managed=False, searxng_public_fallback=False)
        d = self.dialog(cfg, searxng_status=lambda: ("Dùng SearXNG ở http://127.0.0.1:8890", False), lookup_games=3)
        d.set_tab("lookup")
        self.assertEqual(d.current_tab(), "lookup")
        self.assertEqual(d.tabs.tabText(d.tabs.currentIndex()), "Tra cứu game")
        self.assertFalse(d.lookup_ask_check.isChecked())
        self.assertEqual({k for k, b in d.lookup_source_checks.items() if b.isChecked()}, {"vndb", "steam"})
        self.assertEqual(d.searxng_url_edit.text(), "http://127.0.0.1:8890/")
        self.assertEqual(d.searxng_status_label.text(), "Dùng SearXNG ở http://127.0.0.1:8890")
        self.assertEqual(d.searxng_install_button.text(), "Cài SearXNG trên máy…")
        self.assertEqual(d.lookup_games_label.text(), "Đã hỏi về 3 game.")
        got, errors = d.collect()
        self.assertEqual(errors, [])
        self.assertEqual(_su.ui_fields(got), _su.ui_fields(cfg))  # unknown sources and the saved spelling are kept
        self.assertFalse(d.is_dirty())
        d.lookup_source_checks["web"].setChecked(True)
        d.lookup_source_checks["vndb"].setChecked(False)
        d.searxng_public_check.setChecked(True)
        self.assertTrue(d.is_dirty())
        self.assertTrue(d.save())
        saved = self.applied[-1]
        self.assertEqual(saved.lookup_sources, ["custom", "steam", "web"])
        self.assertTrue(saved.searxng_public_fallback)

    def test_bad_url_blocks_saving_on_the_tab(self):
        d = self.dialog(Config())
        d.searxng_url_edit.setText("127.0.0.1:8888")
        self.assertFalse(d.save())
        self.assertEqual(d.current_tab(), "lookup")
        self.assertIn("http://", d.status_label.text())
        self.assertEqual(self.applied, [])

    def test_actions_and_sync(self):
        d = self.dialog(Config(), lookup_games=2)
        got: list = []
        d.lookup_request_requested.connect(lambda: got.append("request"))
        d.import_profile_requested.connect(lambda how: got.append(how))
        d.searxng_install_requested.connect(lambda: got.append("install"))
        d.forget_games_requested.connect(lambda: got.append("forget"))
        for b in (d.lookup_request_button, d.import_file_button, d.import_clip_button, d.searxng_install_button,
                  d.forget_games_button):
            b.click()
        self.assertEqual(got, ["request", "file", "clipboard", "install", "forget"])
        d.set_lookup_info(0)
        self.assertFalse(d.forget_games_button.isEnabled())
        d.sync_field("searxng_url", "http://127.0.0.1:8899")  # filled in by the app after an install
        self.assertEqual(d.searxng_url_edit.text(), "http://127.0.0.1:8899")
        self.assertFalse(d.is_dirty())
        with mock.patch("app.lookup.searxng.probe", return_value=True) as probe:
            d.check_searxng()
            self.assertTrue(pump_until(lambda: "trả lời ở" in d.searxng_status_label.text(), 5))
        probe.assert_called_once_with("http://127.0.0.1:8899")
        self.assertEqual(d.searxng_status_label.text(), "SearXNG trả lời ở http://127.0.0.1:8899.")

    def test_defaults_and_vietnamese(self):
        d = self.dialog(Config(lookup_sources=["steam"]))
        d.reset_defaults()
        self.assertTrue(all(b.isChecked() for b in d.lookup_source_checks.values()))
        self.assertTrue(d.lookup_ask_check.isChecked())
        words = visible_words(d.tabs.widget(d._tab_index["lookup"]))
        self.assertFalse(words & (ENGLISH - {"search"}), words & ENGLISH)


# --- App wiring (fake pipeline, profiles in a temporary folder) -----------------------------------------------

class AppLookupTests(_ta.AppTestBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(flush_deletes)
        if self.app.guard is not None:  # the real foreground window must not take part
            self.app.guard.stop()
        pump(50)
        self.app._lookup_seen.clear()
        self.app._pending_games.clear()
        self.app.config.searxng_managed = False  # the SearXNG installed on this machine plays no part
        self.games = Path(self.tmp.name) / "Games"
        self.factory = FixtureFactory()
        self.app.make_lookup_service = self.factory
        p = mock.patch.object(self.app.tray, "show_message")
        self.shown = p.start()
        self.addCleanup(p.stop)

    def foreground(self, path: Path) -> None:
        """What GameGuard does when this exe comes to the front."""
        self.app.guard.foreground_path = str(path)
        self.app._on_foreground_changed(path.name)

    def balloons(self) -> list:
        return [c for c in self.shown.call_args_list if c.kwargs.get("token", "").startswith("lookup:")]

    def windows(self, kind: str) -> list:
        from app.ui import lookup_dialog as ld
        return [w for w in self.app.lookup_windows() if isinstance(w, getattr(ld, kind))]

    def state(self) -> dict:
        path = Path(self.tmp.name) / "lookup_state.json"
        return json.loads(path.read_text(encoding="utf-8"))["games"] if path.exists() else {}

    def test_new_game_is_offered_once_then_looked_up_and_saved(self):
        exe = make_game(self.games, "STEINS;GATE", "STEINS;GATE.exe")
        self.foreground(exe)
        self.assertTrue(pump_until(lambda: bool(self.balloons()), 10))
        [call] = self.balloons()
        self.assertEqual(call.args[0], "Game mới: STEINS;GATE")
        self.assertIn("Chưa gửi gì lên mạng", call.args[1])
        self.assertEqual(call.kwargs["token"], "lookup:exe:steins;gate.exe")
        self.assertEqual(self.state()["exe:steins;gate.exe"]["state"], "offered")  # not answered yet
        self.assertEqual(self.app.tray.pending_games(), [("exe:steins;gate.exe", "STEINS;GATE")])
        self.assertEqual(self.windows("ConsentDialog"), [])  # no window over the game
        self.assertEqual(self.factory.transports, [])  # nothing sent
        # the same game again (also after a restart: the state remembers it): no second balloon
        self.app._lookup_seen.clear()
        self.app._pending_games.clear()
        self.app._refresh_pending_games()
        self.foreground(exe)
        pump(300)
        self.assertEqual(len(self.balloons()), 1)
        # ... but a game never answered (its balloon may have been hidden by a full-screen game) is in the tray again
        self.assertEqual(self.app.restore_pending_games(), 1)
        self.assertEqual(self.app.tray.pending_games(), [("exe:steins;gate.exe", "STEINS;GATE")])
        # the user clicks the balloon: the consent window, still nothing sent
        self.app.tray.message_clicked.emit(call.kwargs["token"])
        [consent] = self.windows("ConsentDialog")
        self.assertEqual(self.state()["exe:steins;gate.exe"]["state"], "asked")  # seen: not listed again after a restart
        self.app.tray.message_clicked.emit(call.kwargs["token"])  # a second click: the same window comes forward
        self.assertEqual(self.windows("ConsentDialog"), [consent])
        self.assertEqual(self.factory.transports, [])
        consent.title_edit.setText("Steins;Gate")
        consent.lookup_button.click()
        [dlg] = self.windows("LookupDialog")
        self.assertTrue(pump_until(lambda: dlg.report is not None, 20))
        self.assertEqual(self.factory.transports[0].missing, [])
        for url in self.factory.urls():  # the title only: no path, folder or exe name
            self.assertNotIn(".exe", url.casefold())
            self.assertNotIn(Path(self.tmp.name).name.casefold(), url.casefold())
        dlg.panel.create_button.click()
        self.assertIn("Đã tạo hồ sơ “Steins;Gate”", dlg.panel.status_label.text())
        prof = self.store.get("Steins;Gate")
        self.assertEqual(prof.name, "Steins;Gate")
        self.assertEqual(prof.processes, ["STEINS;GATE.exe"])
        self.assertEqual(len(prof.characters), 15)
        self.assertEqual(prof.lookup["aliases"]["紅莉栖"], "牧瀬紅莉栖")  # the name box 紅莉栖 is her (pronouns)
        self.assertEqual(self.app.profile_name, "Steins;Gate")
        self.assertEqual(self.state()["exe:steins;gate.exe"]["state"], "done")
        self.assertEqual(self.state()["exe:steins;gate.exe"]["profile"], "Steins;Gate")
        self.assertEqual(self.app.tray.pending_games(), [])
        self.assertEqual(self.store.match_process("STEINS;GATE.exe"), "Steins;Gate")  # auto-switch from now on

    def test_merge_keeps_the_users_entries(self):
        mine = Profile("Steins;Gate", characters=[Character("牧瀬紅莉栖", "Kurisu-chan", "nữ", "tôi", "trợ lý")])
        self.store.save(mine)
        self.app.set_profile("Steins;Gate")
        dlg = self.app._start_lookup(identity_from_title("Steins;Gate", "SG.exe"))
        self.assertTrue(pump_until(lambda: dlg.report is not None, 20))
        self.assertEqual(dlg.panel.create_button.text(), "Thêm vào hồ sơ “Steins;Gate”")
        dlg.panel.create_button.click()
        prof = self.store.get("Steins;Gate")
        kurisu = next(c for c in prof.characters if c.name == "牧瀬紅莉栖")
        self.assertEqual((kurisu.name_vi, kurisu.self_pronoun, kurisu.notes), ("Kurisu-chan", "tôi", "trợ lý"))
        self.assertEqual(len(prof.characters), 15)
        self.assertIn("SG.exe", prof.processes)
        self.assertIn("giữ nguyên", dlg.panel.status_label.text())

    def test_never_into_default_and_engine_exes_with_their_folder(self):
        with self.assertRaises(ValueError):
            self.app._apply_lookup_draft(Profile("x", characters=[Character("セラ", "Sera")]), "default", exe="Sera.exe")
        self.assertEqual(self.store.get("default").processes, [])
        exe = make_game(self.games, "Summer Pockets", "SiglusEngine.exe", markers=("Gameexe.dat",))
        other = make_game(self.games, "Little Busters", "SiglusEngine.exe", markers=("Gameexe.dat",))
        ident = identify(str(exe))
        self.assertTrue(ident.generic_exe)
        draft = Profile("Summer Pockets", characters=[Character("鳴瀬しろは", "Naruse Shiroha", "nữ")])
        self.app._apply_lookup_draft(draft, "Summer Pockets", key=ident.key, exe=ident.exe_name, folder=ident.folder_name)
        self.assertEqual(self.store.get("Summer Pockets").processes, ["Summer Pockets/SiglusEngine.exe"])
        self.app.set_profile("default")
        self.foreground(other)  # another Key game: not switched to Summer Pockets
        self.assertEqual(self.app.profile_name, "default")
        self.foreground(exe)
        self.assertEqual(self.app.profile_name, "Summer Pockets")

    def test_request_and_import_are_not_tied_to_another_game(self):
        self.store.save(Profile("Blue Archive", processes=["BlueArchive.exe"],
                                characters=[Character("砂狼シロコ", "Sunaookami Shiroko", "nữ")]))
        self.app.set_profile("Blue Archive")
        req = self.app.open_lookup_request(identity_from_title("Steins;Gate", "STEINS;GATE.exe"))
        self.assertNotIn("砂狼シロコ", req.text())  # another game's glossary is not "what the app has"
        self.assertNotIn("App đã có sẵn", req.text())
        own = self.app.open_lookup_request(identity_from_title("Blue Archive", "BlueArchive.exe"))
        self.assertIn("砂狼シロコ", own.text())
        a = make_game(self.games, "Pending Game", "PendingGame.exe")
        self.app._pending_games["exe:pendinggame.exe"] = identify(str(a))
        imp = self.app.import_profile("paste")  # from the tray: no game is guessed
        self.assertEqual(imp.exe_name, "")
        imp.parse_text(json.dumps({"name": "Friend's BA", "characters": [{"name": "セリカ", "name_vi": "Serika"}]},
                                  ensure_ascii=False))
        imp.panel.create_button.click()
        self.assertEqual(self.store.get("Friend's BA").processes, [])
        self.assertIn("exe:pendinggame.exe", self.app._pending_games)  # still offered

    def test_non_games_settings_off_and_anti_cheat(self):
        self.foreground(Path(r"C:\Windows\explorer.exe"))
        self.app.config.blocked_processes = ["Shooter.exe"]
        self.app.config.guard_mode = "pause"
        self.foreground(make_game(self.games, "Shooter", "Shooter.exe"))
        self.app.config.lookup_ask_new_games = False
        self.foreground(make_game(self.games, "Quiet VN", "QuietVN.exe"))
        pump(400)
        self.assertEqual(self.balloons(), [])
        self.assertEqual(self.state(), {})

    def test_template_is_offered_without_consent_or_network(self):
        counting = CountingFactory()
        self.app.make_lookup_service = counting
        exe = make_game(self.games, "BlueArchive", "BlueArchive.exe")
        self.foreground(exe)
        self.assertTrue(pump_until(lambda: bool(self.balloons()), 10))
        self.assertIn("hồ sơ mẫu “Blue Archive”", self.balloons()[0].args[1])
        self.app.open_lookup("exe:bluearchive.exe")
        self.assertEqual(self.windows("ConsentDialog"), [])
        [dlg] = self.windows("LookupDialog")
        self.assertTrue(pump_until(lambda: dlg.report is not None, 10))
        self.assertEqual(counting.requests, [])
        self.assertEqual(dlg.panel.view.counts(), (107, 73))
        dlg.panel.create_button.click()
        self.assertEqual(len(self.store.get("Blue Archive").characters), 107)

    def test_nothing_found_and_declined(self):
        def empty(request):
            url = str(request.url)
            if "storesearch" in url:
                return httpx.Response(200, json={"total": 0, "items": []})
            if "wikidata" in url:
                return httpx.Response(200, json={"search": []})
            return httpx.Response(200, json={"results": [], "more": False})
        self.app.make_lookup_service = CountingFactory(empty)
        a = make_game(self.games, "Zzqx Lost", "ZzqxLost.exe")
        self.app._pending_games["exe:zzqxlost.exe"] = identify(str(a))
        dlg = self.app._start_lookup(self.app._pending_games["exe:zzqxlost.exe"])
        self.assertTrue(pump_until(lambda: dlg.report is not None, 20))
        self.assertEqual(self.state()["exe:zzqxlost.exe"]["state"], "empty")
        self.assertNotIn("exe:zzqxlost.exe", self.app._pending_games)
        self.assertEqual(self.store.names(), ["default"])  # nothing created: the app goes on as today
        b = make_game(self.games, "Other", "Other.exe")
        self.app._pending_games["exe:other.exe"] = identify(str(b))
        consent = self.app.open_lookup("exe:other.exe")
        consent.never_button.click()
        self.assertEqual(self.state()["exe:other.exe"]["state"], "declined")
        self.assertEqual(self.app.tray.pending_games(), [])

    def test_new_profile_offers_a_lookup(self):
        with mock.patch.object(self.app, "_ask_profile_name", return_value="Octopath"):
            self.app.new_profile()
        self.assertTrue(self.app._editor is not None and self.app._editor.isVisible())
        [consent] = self.windows("ConsentDialog")
        self.assertEqual(consent.title_edit.text(), "Octopath")
        self.assertTrue(consent.never_button.isHidden())
        self.assertEqual(self.factory.transports, [])
        consent.lookup_button.click()
        [dlg] = self.windows("LookupDialog")
        self.assertEqual(dlg.profile_name, "Octopath")
        self.assertTrue(pump_until(lambda: dlg.report is not None, 20))  # not in the fixtures: nothing found
        self.app.config.lookup_ask_new_games = False
        with mock.patch.object(self.app, "_ask_profile_name", return_value="Ys IX"):
            self.app.new_profile()
        self.assertEqual(self.windows("ConsentDialog"), [])

    def test_request_import_and_settings_actions(self):
        req = self.app.open_lookup_request(identity_from_title("Steins;Gate"))
        self.assertIn("Yêu cầu: hồ sơ thuật ngữ cho game “Steins;Gate”", req.text())
        req.import_button.click()
        [imp] = self.windows("ImportDialog")
        imp.parse_text(json.dumps({"name": "Steins;Gate", "characters": [
            {"name": "椎名まゆり", "name_vi": "Shiina Mayuri", "gender": "nữ"}]}, ensure_ascii=False))
        imp.panel.create_button.click()
        self.assertEqual([c.name for c in self.store.get("Steins;Gate").characters], ["椎名まゆり"])
        self.app.tray.lookup_request.emit()
        self.assertEqual(len(self.windows("RequestDialog")), 2)
        self.app.open_settings("lookup")
        settings = self.app._settings
        self.assertEqual(settings.current_tab(), "lookup")
        self.app.lookup_state().mark("exe:x.exe", "declined")
        settings.ask_before_discard = False
        settings.forget_games_button.setEnabled(True)
        settings.forget_games_button.click()
        self.assertEqual(self.app.lookup_state().items(), {})
        self.assertEqual(settings.lookup_games_label.text(), "Chưa hỏi về game nào.")

    def test_searxng_lifecycle(self):
        class FakeSearxng:
            def __init__(self):
                self.closed = threading.Event()
                self.stopped = 0

            def close(self):
                self.closed.set()

            def stop(self):
                self.stopped += 1

        first = FakeSearxng()
        self.app._searxng = first
        cfg = copy.deepcopy(self.app._persisted)
        cfg.searxng_url = "http://127.0.0.1:8891"
        self.app.apply_settings(cfg)
        self.assertTrue(first.closed.wait(5))  # the old service is stopped, a new one is made on next use
        self.assertIsNone(self.app._searxng)
        self.assertEqual(self.app.config.searxng_url, "http://127.0.0.1:8891")
        self.app._persisted.searxng_url = self.app.config.searxng_url = ""
        self.app._on_searxng_installed(True, 8892)
        self.assertEqual(self.saved_config()["searxng_url"], "http://127.0.0.1:8892")
        self.assertTrue(self.saved_config()["searxng_managed"])
        self.assertIn("Đã cài xong SearXNG", self.shown.call_args.args[1])
        last = FakeSearxng()
        self.app._searxng = last
        self.app._stop_searxng_now()
        self.assertEqual(last.stopped, 1)
        self.app.shutdown()
        self.assertTrue(last.closed.is_set())  # stopped on exit


# --- native (windows platform only) -----------------------------------------------------------------------------

def _lookup_windows_for_previews(tmp: Path):
    """The new windows in representative states (for the capture-exclusion check and the PNG previews)."""
    from app.lookup.claude_request import build_request
    from app.ui import lookup_dialog as ld
    from app.ui.settings_dialog import SettingsDialog

    ident = GameIdentity(exe_name="STEINS;GATE.exe", titles=["STEINS;GATE"], steam_appid=412830, engine="Unity")
    out: dict[str, object] = {}
    out["lookup_consent"] = ld.ConsentDialog(ident, ["steam", "wikidata", "vndb", "wiki", "web"],
                                             web_target="SearXNG trên máy này (127.0.0.1); SearXNG hỏi tiếp Google, "
                                                        "Bing, DuckDuckGo… bằng tên game")
    running = ld.LookupDialog(identity_from_title("Steins;Gate", "STEINS;GATE.exe"), make_service=FixtureFactory(),
                              sources=["steam", "wikidata", "vndb", "wiki", "web"], active_profile="default",
                              web_target="SearXNG trên máy này")
    running.head_label.setText("<b>Tra cứu: Steins;Gate</b> <span style='color:gray'>(STEINS;GATE.exe)</span>")
    running.sent_label.setText(f"Gửi tên game “Steins;Gate” tới: Steam, Wikidata, VNDB, Wiki game, Tìm web. {ld.NOT_SENT}")
    for st in (SourceStatus("steam", "ok", elapsed_s=0.8, requests=3, found={"games": 1}),
               SourceStatus("wikidata", "ok", elapsed_s=1.2, requests=4, found={"characters": 12}),
               SourceStatus("vndb", "running"), SourceStatus("wiki", "waiting"),
               SourceStatus("web", "error", elapsed_s=2.0, message="network error: ConnectError")):
        running._status_row(st)
    running.summary_label.setText("Đang tra cứu… (thường mất 2–8 giây; tối đa khoảng 30 giây)")
    out["lookup_running"] = running
    result = ld.LookupDialog(identity_from_title("Steins;Gate", "STEINS;GATE.exe"), make_service=FixtureFactory(),
                             sources=list(OFFLINE), active_profile="default", apply=lambda p, n: "")
    rep = steins_report()
    rep.patches.append(PatchLink("[PC] Steins;Gate Việt hóa - bản dịch hoàn chỉnh", "https://example.vn/steins-gate",
                                 "web", note="tìm web: example.vn — chưa kiểm chứng", verified=False))
    result.show_report(rep)
    out["lookup_result"] = result
    unsure = ld.LookupDialog(identity_from_title("Persona"), make_service=FixtureFactory(),
                             sources=["steam", "wikidata", "vndb", "wiki", "web"], active_profile="default",
                             web_target="SearXNG trên máy này")
    unsure.show_report(LookupReport(
        query="Persona", identity=identity_from_title("Persona"), title="Persona 6", confidence="medium",
        candidates=[GameCandidate("steam", "Persona 6", 0.93, {"steam": "2649560"}, "2026")],
        alternatives=[GameCandidate("wikidata", "Persona 5 Royal", 0.8, description="2019 video game"),
                      GameCandidate("vndb", "Persona 4 Golden", 0.78, description="2012, Atlus")],
        notes=["Chưa chắc đây đúng là “Persona 6”: chọn đúng game rồi bấm “Tra cứu game này”."],
        statuses=[SourceStatus("steam", "ok", elapsed_s=0.9, requests=3, found={"games": 1}),
                  SourceStatus("wikidata", "empty", elapsed_s=1.1, requests=2),
                  SourceStatus("vndb", "empty", elapsed_s=0.7, requests=1),
                  SourceStatus("wiki", "skipped", message="chờ bạn chọn đúng game"),
                  SourceStatus("web", "skipped", message="chờ bạn chọn đúng game")]))
    out["lookup_unsure"] = unsure
    template = ld.LookupDialog(identity_from_title("Blue Archive", "BlueArchive.exe"), make_service=FixtureFactory(),
                               sources=[], active_profile="default")
    template.show_report(LookupService(client=HttpClient(cache_dir=None, transport=httpx.MockTransport(
        lambda r: httpx.Response(500))), sources=()).run(identity_from_title("Blue Archive", "BlueArchive.exe"), []))
    out["lookup_template"] = template
    empty = ld.LookupDialog(identity_from_title("Zzqx", "Zzqx.exe"), make_service=FixtureFactory(), sources=list(OFFLINE))
    empty.show_report(LookupReport(query="Zzqx", identity=identity_from_title("Zzqx", "Zzqx.exe"), statuses=[
        SourceStatus(s, "empty", elapsed_s=0.4, requests=1) for s in OFFLINE]))
    out["lookup_empty"] = empty
    out["lookup_request"] = ld.RequestDialog(build_request(steins_report()), title="Steins;Gate")
    imp = ld.ImportDialog(active_profile="default", exists=lambda n: None)
    imp.text_edit.setPlainText('Đây là hồ sơ:\n```json\n{"name": "Steins;Gate", "characters": [...]}\n```')
    imp._show(result.draft.copy(), "Đã đọc hồ sơ từ đoạn dán.")
    out["lookup_import"] = imp
    inst = ld.SearxngInstallDialog(command=[sys.executable, "-c", "pass"], home=ld.SEARXNG_HOME_SHOWN)
    for line in ("Cài SearXNG 2026.10.24+ef0ec03da (commit ef0ec03da) vào C:\\Users\\…\\.screen-translator\\searxng",
                 "[1/5] venv", "[2/5] mã nguồn", "  tải github.com/searxng/searxng/archive/ef0ec03….zip (6,1 MB)",
                 "[3/5] settings.yml", "[4/5] thư viện"):
        inst._on_line(line)
    inst.status_label.setText("Đang cài… (lần đầu mất vài phút)")
    out["searxng_install"] = inst
    s = SettingsDialog(Config(), apply=lambda c: None, startup=_su.FakeStartup(), lookup_games=4,
                       searxng_status=lambda: ("Đã cài, đang tắt (tự bật khi cần tra cứu)", True))
    s.ask_before_discard = False
    s.set_tab("lookup")
    out["settings_lookup"] = s
    return out


class NativeLookupTests(unittest.TestCase):
    def setUp(self):
        if not NATIVE or not on_windows_platform():
            self.skipTest("needs the windows platform: run via WindowsPlatformSubprocessTests")
        qapp()
        self.addCleanup(flush_deletes)
        self.tmp = Path(tempfile.mkdtemp(prefix="st-lookup-native-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))

    def test_windows_and_popups_capture_excluded(self):
        from app.overlay.affinity import get_display_affinity
        from app.ui import lookup_dialog as ld
        wins = _lookup_windows_for_previews(self.tmp)
        for name, w in wins.items():
            self.addCleanup(w.deleteLater)
            self.assertEqual(get_display_affinity(int(w.winId())), 0x11, name)
        d = wins["lookup_result"]
        d._popups.install()
        popups = [d.alt_combo.view().window()]
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(QMessageBox.Icon.Question, "Dừng cài SearXNG?", "Dừng?", parent=d)
        for w in [*popups, box]:
            QCoreApplication.sendEvent(w, QShowEvent())  # synthetic: nothing appears
            self.assertEqual(get_display_affinity(int(w.winId())), 0x11, w)
        d._popups.remove()
        fd = ld.make_file_dialog(d, save=False, title="Mở tệp hồ sơ")
        self.addCleanup(fd.deleteLater)
        self.assertEqual(get_display_affinity(int(fd.winId())), 0x11)
        self.assertTrue(fd.testOption(fd.Option.DontUseNativeDialog))

    def test_render_previews(self):
        """Every new window as PNG in bench/results/preview/ (QWidget.grab; nothing is shown)."""
        from app.ui import lookup_dialog as ld
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        wins = _lookup_windows_for_previews(self.tmp)
        neutral = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Web")  # no user name in the preview
        fd = ld.make_file_dialog(None, save=False, title="Mở tệp hồ sơ", directory=neutral)
        fd.setSidebarUrls([QUrl("file:"), QUrl.fromLocalFile(neutral)])  # not the home folder (the user's name)
        fd.resize(720, 460)
        wins["lookup_file_dialog"] = fd
        for name, w in wins.items():
            self.addCleanup(w.deleteLater)
            w.layout().activate()
            pump(30)
            img = w.grab()
            self.assertGreaterEqual(img.width(), 500, name)
            self.assertTrue(img.save(str(PREVIEW_DIR / f"{name}.png")), name)
        print(f"\n    previews: {PREVIEW_DIR / 'lookup_result.png'} …", file=sys.stderr)


# --- child process ------------------------------------------------------------------------------------------------

@unittest.skipIf(sys.platform != "win32", "Windows only")
@unittest.skipIf(os.environ.get("SCREEN_TRANSLATOR_SKIP_GUI_TESTS") == "1", "SCREEN_TRANSLATOR_SKIP_GUI_TESTS=1")
class WindowsPlatformSubprocessTests(unittest.TestCase):
    """Runs the native class (and the window logic again) in a child process on the windows platform."""

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
        classes = ["NativeLookupTests", "ConsentDialogTests", "LookupDialogTests", "SettingsLookupTabTests"]
        cmd = [sys.executable, "-m", "unittest", "-v", *[f"tests.test_lookup_ui.{c}" for c in classes]]
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=300)
        out = proc.stdout + proc.stderr
        if os.environ.get("SCREEN_TRANSLATOR_VERBOSE_CHILD") == "1":
            print(out, file=sys.stderr)
        self.assertEqual(proc.returncode, 0, out[-6000:])
        self.assertNotIn("skipped", out, out[-3000:])


if __name__ == "__main__":
    unittest.main()
