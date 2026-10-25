"""Tests for the version number (app.__version__): one source of truth, printed by --version (console, run.ps1)
and shown in a box by the windowless entry, set on QApplication, named in the tray tooltip, the Settings window
and the User-Agents (lookup, SearXNG, tools.setup_llm); CHANGELOG.md and README.md name it, and every version
the README and docs/ spell out is the current one. The startup log line ("Screen Translator <version> starting")
is checked by the real run_app.pyw launch in tests.test_settings_ui (LauncherIntegrationTests).

Run: python -m unittest tests.test_version -v

Everything runs on Qt's offscreen platform; the `python -m app --version` / `run.ps1 --version` children exit
during argument parsing (before the single-instance lock, the log or any window), with a temporary data folder.
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtWidgets import QApplication

from app import __version__
from app.config import Config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPECTED = f"Screen Translator {__version__}"
# How the README and docs/ spell a version of the app out: "Phiên bản 1.2.3", "Screen Translator 1.2.3",
# "bản 1.2.3" (e.g. the test count in docs/phat-trien.md).
DOC_VERSION = re.compile(r"(?:Phiên bản|Screen Translator|\bbản)\s+(\d+\.\d+\.\d+)\b")


def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([sys.argv[0]])
    from app import qt_gc

    qt_gc.install(app)  # as in app.main: worker threads never run a gc collection (Qt wrappers in the garbage)
    return app


def load_run_app():
    path = PROJECT_ROOT / "run_app.pyw"
    loader = importlib.machinery.SourceFileLoader("run_app_version_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class VersionSourceTests(unittest.TestCase):
    def test_one_source_of_truth(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")
        from app.main import VERSION_TEXT
        self.assertEqual(VERSION_TEXT, EXPECTED)
        # No other module spells the number out: they all read app.__version__.
        literal = re.compile(rf"""["'/ ]{re.escape(__version__)}["' ]""")
        for folder in ("app", "tools"):
            for path in (PROJECT_ROOT / folder).rglob("*.py"):
                if path == PROJECT_ROOT / "app" / "__init__.py":
                    continue
                text = path.read_text(encoding="utf-8")
                self.assertIsNone(literal.search(text), f"{path.relative_to(PROJECT_ROOT)} hard-codes the version")

    def test_changelog_and_readme_name_it(self):
        changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(changelog, rf"(?m)^## \[{re.escape(__version__)}\] — \d{{4}}-\d{{2}}-\d{{2}}$")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"Phiên bản {__version__}", readme)
        self.assertIn("CHANGELOG.md", readme)

    def test_docs_name_the_current_version(self):
        """The examples in the README and docs/ (tooltip, Settings label, test count) follow a version bump."""
        found = 0
        for path in [PROJECT_ROOT / "README.md", *sorted((PROJECT_ROOT / "docs").glob("*.md"))]:
            for m in DOC_VERSION.finditer(path.read_text(encoding="utf-8")):
                found += 1
                self.assertEqual(m.group(1), __version__,
                                 f"{path.relative_to(PROJECT_ROOT)} still names version {m.group(1)}: {m.group(0)!r}")
        self.assertGreaterEqual(found, 3)  # README version line, tray tooltip example, Settings label

    def test_user_agents(self):
        from app.lookup.http import USER_AGENT
        from app.lookup.searxng import USER_AGENT as SEARXNG_UA
        from tools.setup_llm import USER_AGENT as SETUP_UA
        self.assertTrue(USER_AGENT.startswith(f"ScreenTranslator/{__version__} (+https://github.com/"), USER_AGENT)
        self.assertTrue(SEARXNG_UA.startswith(f"screen-translator/{__version__} "), SEARXNG_UA)
        self.assertEqual(SETUP_UA, f"screen-translator-setup/{__version__}")


class VersionCliTests(unittest.TestCase):
    def test_version_flag_prints_and_exits(self):
        from app.main import build_parser
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            build_parser().parse_args(["--version"])
        self.assertIn(cm.exception.code, (0, None))
        self.assertEqual(out.getvalue().strip(), EXPECTED)
        self.assertIn("--version", build_parser().format_help())

    def test_windowless_version_shows_a_box(self):
        from app import main as app_main
        real_out, real_err = sys.stdout, sys.stderr
        with mock.patch.object(app_main, "show_info_box") as info, \
                mock.patch.object(app_main, "show_error_box") as error, self.assertRaises(SystemExit) as cm:
            app_main._parse_args(["--version"], windowless=True)
        self.assertIn(cm.exception.code, (0, None))
        self.assertIs(sys.stdout, real_out)  # both streams are given back
        self.assertIs(sys.stderr, real_err)
        error.assert_not_called()
        info.assert_called_once()
        title, text = info.call_args.args
        self.assertEqual(title, "Screen Translator")
        self.assertTrue(text.startswith(EXPECTED), text)

    def test_windowless_help_and_bad_args_unchanged(self):
        from app import main as app_main
        with mock.patch.object(app_main, "show_info_box") as info, \
                mock.patch.object(app_main, "show_error_box") as error, mock.patch.object(app_main, "setup_logging"):
            with self.assertRaises(SystemExit):
                app_main._parse_args(["--help"], windowless=True)
            info.assert_not_called()  # --help prints to a stdout nobody sees, as before
            with self.assertRaises(SystemExit) as cm:
                app_main._parse_args(["--lang", "xx"], windowless=True)
            self.assertEqual(cm.exception.code, 2)
            info.assert_not_called()
            error.assert_called_once()

    def test_run_app_version_does_not_crash(self):
        """run_app.pyw (what the shortcut starts) with --version: the real app.main.main(windowless=True)
        shows the version box and returns 0 before taking the single-instance lock or opening the log."""
        q = qapp()
        before = (q.applicationName(), q.applicationVersion(), q.quitOnLastWindowClosed())

        def restore():
            q.setApplicationName(before[0])
            q.setApplicationVersion(before[1])
            q.setQuitOnLastWindowClosed(before[2])

        self.addCleanup(restore)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        import logging
        self.addCleanup(logging.captureWarnings, False)
        home = Path(tempfile.mkdtemp(prefix="st-version-"))
        self.addCleanup(shutil.rmtree, home, True)
        from app import main as app_main
        mod = load_run_app()
        with mock.patch.dict(os.environ, {"SCREEN_TRANSLATOR_HOME": str(home)}), \
                mock.patch.object(app_main, "set_app_user_model_id"), \
                mock.patch.object(app_main, "InstanceLock", side_effect=AssertionError("got past --version")) as lock, \
                mock.patch.object(app_main, "show_info_box") as info, \
                mock.patch.object(app_main, "show_error_box") as error:
            self.assertEqual(mod.run(["--version"]), 0)
        info.assert_called_once()
        self.assertTrue(info.call_args.args[1].startswith(EXPECTED))
        error.assert_not_called()
        lock.assert_not_called()  # it never got as far as the single-instance lock
        self.assertEqual(q.applicationVersion(), __version__)
        self.assertEqual(list(home.iterdir()), [])  # no log, no config written

    def _child_env(self) -> dict:
        home = Path(tempfile.mkdtemp(prefix="st-version-child-"))
        self.addCleanup(shutil.rmtree, home, True)
        env = {k: v for k, v in os.environ.items() if k not in ("SCREEN_TRANSLATOR_NATIVE_TESTS",)}
        env.update(QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8", SCREEN_TRANSLATOR_HOME=str(home / "data"),
                   SCREEN_TRANSLATOR_LLM_HOME=str(home / "llm"), SCREEN_TRANSLATOR_PYTHON=sys.executable)
        return env

    def test_python_m_app_version(self):
        proc = subprocess.run([sys.executable, "-m", "app", "--version"], cwd=PROJECT_ROOT, env=self._child_env(),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertEqual(proc.stdout.strip(), EXPECTED)

    @unittest.skipIf(sys.platform != "win32" or shutil.which("powershell") is None, "needs Windows PowerShell")
    def test_run_ps1_version(self):
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
               str(PROJECT_ROOT / "run.ps1"), "--version"]
        proc = subprocess.run(cmd, cwd=tempfile.gettempdir(), env=self._child_env(), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(proc.returncode, 0, (proc.stdout + proc.stderr)[-2000:])
        self.assertEqual(proc.stdout.strip(), EXPECTED)


class VersionInUiTests(unittest.TestCase):
    def setUp(self):
        qapp()

    def test_tray_tooltip_and_status_line(self):
        from app.capture.base import Region
        from app.pipeline.types import OverlayState
        from app.ui.tray import TrayController
        tray = TrayController(Config())
        for m in (tray.menu(), tray._lang_menu, tray._guard_menu, tray._profile_menu, tray._llm_menu,
                  tray._lookup_menu):
            self.addCleanup(m.deleteLater)
        self.addCleanup(tray.deleteLater)
        self.assertEqual(tray.tooltip(), EXPECTED)
        self.assertEqual(tray.action_texts()[0], EXPECTED)  # the status line until the first state arrives
        st = OverlayState(seq=1, region=Region(0, 0, 10, 10), status="running", pending=1, src_lang="ja")
        tray.update_status(st, paused_by_user=False, text_visible=True)
        self.assertTrue(tray.tooltip().startswith(f"{EXPECTED} · JA→VI\n"), tray.tooltip())

    def test_settings_general_tab_names_the_version(self):
        from app.ui.settings_dialog import VERSION_LABEL, SettingsDialog

        class Startup:
            def enabled(self):
                return False

            def set_enabled(self, on):
                pass

        d = SettingsDialog(Config(), startup=Startup(), llm_models=[], llm_server=False,
                           searxng_status=lambda: ("chưa cài", False))
        self.addCleanup(d.deleteLater)
        self.assertIn(f"Phiên bản {__version__}", VERSION_LABEL)
        self.assertEqual(d.version_label.text(), VERSION_LABEL)
        d.set_tab("general")
        general = d.tabs.widget(d.tabs.currentIndex())
        self.assertTrue(general.isAncestorOf(d.version_label))


if __name__ == "__main__":
    unittest.main()
