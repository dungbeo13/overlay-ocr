"""Game lookup windows (app.lookup): the consent question, the lookup window, the request for Claude, the import
preview and the SearXNG install window, plus the files-only test that a new foreground exe looks like a game.

Every window here is non-modal and capture-excluded before it first shows (apply_to_widget), and so is
everything it opens: combo lists, tooltips, message boxes and the file dialog (_PopupExcluder; the file dialog is
Qt's own, not the native one, so it can be excluded too). Nothing is sent before the user presses "Tra cứu" in
ConsentDialog, and only the titles it shows are sent. LookupDialog runs LookupService.run on a worker thread;
progress and the report come back to the GUI thread through queued signals, and closing the window cancels the
run. Vietnamese patch links open in the browser only when the user clicks one: nothing is downloaded.
"""
from __future__ import annotations

import dataclasses
import html
import logging
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QStandardPaths, Qt, QTranslator, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config import PROJECT_ROOT
from app.lookup.types import SOURCES, GameIdentity, LookupReport, SourceStatus
from app.overlay.affinity import CaptureExclusion, apply_to_widget
from app.profiles import Profile, normalize_source
from app.ui.glossary_editor import GENDER_LABELS, KIND_LABELS, _PopupExcluder, ask

log = logging.getLogger(__name__)

SOURCE_LABELS = {"steam": "Steam", "wikidata": "Wikidata", "vndb": "VNDB", "wiki": "Wiki game", "web": "Tìm web",
                 "template": "Hồ sơ mẫu có sẵn", "files": "Tệp của game"}
SOURCE_SENT_TO = {  # where each source sends the title, and what it is asked for (the consent dialog)
    "steam": ("store.steampowered.com", "tên chính thức, tên tiếng Nhật, game có tiếng Việt chính thức không"),
    "wikidata": ("www.wikidata.org", "đúng game, tên nhân vật ja / en / vi"),
    "vndb": ("api.vndb.org", "visual novel: nhân vật, giới tính, bản tiếng Việt"),
    "wiki": ("wiki Fandom / wiki.gg của game", "tên nhân vật trong infobox, địa danh, tổ chức"),
}
STATE_LABELS = {"running": "đang tra…", "ok": "xong", "empty": "không có gì", "error": "lỗi", "timeout": "quá giờ",
                "skipped": "bỏ qua", "cancelled": "đã hủy", "waiting": "chờ"}
_STATE_COLORS = {"ok": QColor(27, 94, 32), "error": QColor(183, 28, 28), "timeout": QColor(191, 54, 12)}
FOUND_LABELS = {"characters": "nhân vật", "terms": "thuật ngữ", "patches": "trang Việt hóa", "wikis": "wiki"}
DRAFT_KIND_LABELS = {**KIND_LABELS, "person": "Tên người"}
PATCH_WARNING = ("App không bao giờ tải hay cài bản Việt hóa: bấm vào link để mở trang trong trình duyệt. Link từ tìm "
                 "kiếm web chưa được ai kiểm chứng; chỉ tải từ trang bạn tin.")
NOT_SENT = "Không gửi đường dẫn, tên máy, tên người dùng hay ảnh màn hình. Kết quả được lưu 14 ngày trong data/lookup_cache."
UNSURE_HINT = "Chưa chắc đúng game nên chưa dựng hồ sơ nháp. Chọn đúng game ở dưới rồi bấm “Tra cứu game này”."
ALIAS_OPTION = "Thêm biệt danh (có thể lộ nội dung truyện)"
ALIAS_TIP = ("VNDB không đánh dấu spoiler cho biệt danh. App chỉ lấy biệt danh ngắn (như オカリン) thuộc về một nhân vật, "
             "và thêm chúng thành gợi ý chờ bạn xác nhận.")
REQUEST_HINT = ("Dán đoạn dưới đây cho Claude (claude.ai) hoặc trợ lý khác. App không gửi gì: bạn tự sao chép. Khi có câu "
                "trả lời (một khối JSON), bấm “Nhập câu trả lời…” rồi dán vào.")
IMPORT_HINT = ("Dán cả câu trả lời của Claude (app tự tìm khối ```json), hoặc mở một tệp hồ sơ .json. Chưa có gì được lưu "
               "cho tới khi bạn bấm Tạo / Thêm.")
SEARXNG_INSTALL_INFO = (
    "App sẽ cài SearXNG (máy tìm kiếm mã nguồn mở) vào {home}, trong một môi trường Python riêng: tải khoảng 100 MB "
    "từ GitHub và PyPI, venv của app không đổi gì. Cài xong, app bật thử SearXNG một lần để kiểm tra (chỉ nghe trên "
    "127.0.0.1, không tìm gì) rồi tắt. Sau đó SearXNG chỉ chạy khi đang tra cứu một game và tự tắt sau 10 phút không "
    "dùng. Tiến trình hiện ở khung dưới; bạn dừng được bất cứ lúc nào.")
SEARXNG_HOME_SHOWN = r"%USERPROFILE%\.screen-translator\searxng"  # the path shown (no user name on screen / in previews)
JSON_FILTER = "Hồ sơ JSON (*.json);;Văn bản (*.txt *.md);;Mọi tệp (*)"
MD_FILTER = "Markdown (*.md);;Văn bản (*.txt);;Mọi tệp (*)"
_ROLE = Qt.ItemDataRole.UserRole


# --- new-game detection (files only) -----------------------------------------------------------------------

# Foreground apps that are never a game: the shell, browsers, chat, editors, launchers, our own interpreter.
NON_GAME_EXES = frozenset({
    "explorer.exe", "searchhost.exe", "searchapp.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "applicationframehost.exe", "textinputhost.exe", "lockapp.exe", "taskmgr.exe", "systemsettings.exe", "mmc.exe",
    "control.exe", "rundll32.exe", "dllhost.exe", "consent.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
    "windowsterminal.exe", "openconsole.exe", "conhost.exe", "wt.exe", "notepad.exe", "notepad++.exe", "mspaint.exe",
    "snippingtool.exe", "screenclippinghost.exe", "photos.exe", "calculatorapp.exe", "regedit.exe", "msiexec.exe",
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "opera_gx.exe", "brave.exe", "vivaldi.exe", "iexplore.exe",
    "coccoc.exe", "browser.exe", "arc.exe", "discord.exe", "discordptb.exe", "discordcanary.exe", "slack.exe",
    "teams.exe", "ms-teams.exe", "zoom.exe", "telegram.exe", "zalo.exe", "skype.exe", "whatsapp.exe", "messenger.exe",
    "signal.exe", "code.exe", "code - insiders.exe", "cursor.exe", "windsurf.exe", "devenv.exe", "pycharm64.exe",
    "idea64.exe", "rider64.exe", "clion64.exe", "webstorm64.exe", "sublime_text.exe", "zed.exe", "claude.exe",
    "chatgpt.exe", "obs64.exe", "obs32.exe", "spotify.exe", "vlc.exe", "mpc-hc64.exe", "mpc-be64.exe",
    "potplayermini64.exe", "potplayermini.exe", "mpv.exe", "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe",
    "galaxyclient.exe", "battle.net.exe", "eadesktop.exe", "origin.exe", "upc.exe", "ubisoftconnect.exe",
    "riotclientservices.exe", "riotclientux.exe", "hoyoplay.exe", "dmmgameplayer.exe", "python.exe", "pythonw.exe",
    "py.exe", "pyw.exe", "winword.exe", "excel.exe", "powerpnt.exe", "outlook.exe", "olk.exe", "onenote.exe",
    "acrord32.exe", "acrobat.exe", "sumatrapdf.exe", "7zfm.exe", "winrar.exe", "everything.exe", "nvidia app.exe",
    "nvcontainer.exe", "msiafterburner.exe", "rtss.exe", "lghub.exe", "armourycrate.exe", "steamvr.exe", "vrmonitor.exe",
    "screen translator.exe",
})
# Folder names of game stores / launchers: an exe below one is a game (most likely).
LAUNCHER_DIRS = frozenset({
    "steamapps", "steamlibrary", "epic games", "gog games", "gog galaxy", "riot games", "xboxgames", "ubisoft game launcher",
    "ea games", "origin games", "itch", "dmmgames", "dmm gameplayer", "hoyoplay", "games", "game", "my games",
})
_SYSTEM_PREFIXES = tuple(p.casefold() for p in (os.environ.get("SystemRoot", r"C:\Windows") + "\\",
                                                r"C:\Program Files\WindowsApps\Microsoft."))


def is_non_game(exe: str, path: str = "") -> bool:
    """A foreground exe that is never offered a lookup (shell, browsers, chat, IDEs, launchers, Windows itself)."""
    name = os.path.basename(str(exe or "").strip().strip('"').replace("/", "\\")).casefold()
    if not name or name in NON_GAME_EXES or not name.endswith(".exe"):
        return True
    p = str(path or "").casefold()
    return bool(p) and p.startswith(_SYSTEM_PREFIXES)


def _extra_markers(folder: Path) -> str:
    """Engine markers app.lookup.game_id does not check (existence checks only)."""
    try:
        if (folder / "package.nw").exists() or ((folder / "nw.exe").exists() and (folder / "resources.pak").exists()):
            return "NW.js"
        if (folder / "BGI.exe").exists() or (folder / "sysprg.arc").exists() or (folder / "BGI.gdb").exists():
            return "BGI"
        if (folder / "Engine" / "Binaries").is_dir() or (folder.parent / "Engine" / "Binaries").is_dir():
            return "Unreal"
    except OSError:
        pass
    return ""


def looks_like_new_game(ident: GameIdentity, path: str = "") -> bool:
    """`ident` (from app.lookup.game_id.identify, files only) is worth a "Game mới" offer: it has a title to send
    and its files say it is a game (Steam library or manifest, engine markers, a store / launcher folder)."""
    if not ident.titles:
        return False
    if ident.looks_like_game:
        return True
    p = Path(path) if path else None
    if p is None or not p.is_absolute():
        return False
    marker = _extra_markers(p.parent)
    if marker:
        ident.hints.append(f"engine:{marker}")
        ident.engine = ident.engine or marker
        return True
    parts = {x.casefold() for x in p.parent.parts}
    if parts & LAUNCHER_DIRS:
        ident.hints.append("launcher-folder")
        return True
    return False


# --- small helpers -----------------------------------------------------------------------------------------

def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setEnabled(False)  # the grey "secondary text" look the other windows use
    return label


def _short_url(url: str, limit: int = 60) -> str:
    text = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _link(url: str, text: str = "") -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(text or _short_url(url))}</a>'


def open_link(url: str) -> bool:
    """Open an http(s) page in the browser (the user clicked it). Nothing else is ever opened or downloaded."""
    url = str(url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        log.warning("not opening %r: only http(s) links", url)
        return False
    log.info("opening in the browser (user click): %s", url)
    return bool(QDesktopServices.openUrl(QUrl(url)))


def with_title(ident: GameIdentity, title: str) -> GameIdentity:
    """`ident` with the title the user confirmed or picked, so query_titles() sends exactly what
    ConsentDialog.titles() showed. The app's own first title confirmed (maybe in another case): the other titles
    from the game's files follow. A title the user corrected or picked: it alone (the others were the app's
    guesses, and one may come from a folder name)."""
    title = " ".join((title or "").split())
    if not title:
        return ident
    if ident.titles and ident.titles[0].casefold() == title.casefold():
        rest = [t for t in ident.titles if t.casefold() != title.casefold()]
        return dataclasses.replace(ident, titles=[title] + rest, hints=list(ident.hints))
    return dataclasses.replace(ident, titles=[title], hints=list(ident.hints))


def found_text(found: dict[str, int]) -> str:
    parts = [f"{v} {FOUND_LABELS[k]}" for k, v in found.items() if v and k in FOUND_LABELS]
    if not parts and found.get("games"):
        parts.append("tìm thấy game")
    return ", ".join(parts)


def status_message(st: SourceStatus) -> str:
    """A short Vietnamese note for a source's state (the service's messages are partly English)."""
    msg = (st.message or "").strip()
    low = msg.lower()
    if low.startswith("network error"):
        return "lỗi mạng (máy chủ không trả lời)"
    if low.startswith("timeout") or low.startswith("hết thời gian"):
        return "quá thời gian chờ"
    m = re.match(r"http (\d{3})", low)
    if m:
        return f"máy chủ báo lỗi {m.group(1)}"
    if low in ("not json", "response too large"):
        return "câu trả lời không đúng định dạng"
    if low.startswith("lỗi: "):
        return "lỗi khi đọc kết quả (xem nhật ký)"
    return msg


def sent_to_lines(sources: list[str], web_target: str = "") -> list[str]:
    """Where a lookup with `sources` sends the title (one Vietnamese line per source)."""
    out = []
    for s in sources:
        if s in SOURCE_SENT_TO:
            host, what = SOURCE_SENT_TO[s]
            out.append(f"{SOURCE_LABELS[s]} ({host}): {what}")
        elif s == "web":
            out.append(f"Tìm web: {web_target}" if web_target else "Tìm web: bỏ qua (chưa có SearXNG trên máy)")
    return out


class _Bridge(QObject):
    """Signals from a worker thread to the GUI thread. Parented to the application, never to a window, so a
    window closed meanwhile cannot leave the worker emitting on a deleted object."""

    progress = Signal(object)
    done = Signal(object, object)  # result | None, error text | None
    line = Signal(str)

    def __init__(self) -> None:
        super().__init__(QCoreApplication.instance())


class _ExcludedDialog(QDialog):
    """A non-modal window of ours: capture-excluded before it is shown, its popups too while it is visible."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setModal(False)
        self._popups = _PopupExcluder(self)
        self._exclusion: CaptureExclusion = apply_to_widget(self)

    def exclusion(self) -> CaptureExclusion:
        return self._exclusion

    def popup_exclusions(self) -> int:
        return self._popups.count

    def showEvent(self, event) -> None:
        self._popups.install()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._popups.remove()

    def bring_to_front(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()


# --- file dialog (Qt's own, so it can be capture-excluded; Vietnamese texts) ----------------------------------

FILE_DIALOG_TEXTS = {
    "QFileDialog": {
        "Back": "Quay lại", "Forward": "Tiến", "Parent Directory": "Thư mục cha", "Create New Folder": "Tạo thư mục mới",
        "List View": "Dạng danh sách", "Detail View": "Dạng chi tiết", "Sidebar": "Thanh bên", "Files": "Tệp",
        "New Folder": "Thư mục mới", "&Rename": "Đổi &tên", "&Delete": "&Xóa", "Show &hidden files": "Hiện tệp ẩ&n",
        "&New Folder": "Thư mục &mới", "&Open": "&Mở", "&Save": "&Lưu", "&Choose": "&Chọn", "Open": "Mở",
        "Save As": "Lưu thành", "All Files (*)": "Mọi tệp (*)", "Recent Places": "Thư mục gần đây",
        "My Computer": "Máy tính", "Computer": "Máy tính", "Directory:": "Thư mục:", "File Folder": "Thư mục",
        "Folder": "Thư mục", "Look in:": "Tìm trong:", "File &name:": "Tên &tệp:", "Files of type:": "Loại tệp:",
        "Go back": "Quay lại", "Go forward": "Tiến", "Go to the parent directory": "Lên thư mục cha",
        "Create a New Folder": "Tạo thư mục mới", "Change to list view mode": "Xem dạng danh sách",
        "Change to detail view mode": "Xem dạng chi tiết", "Alias": "Lối tắt", "Shortcut": "Lối tắt",
        "%1 already exists.\nDo you want to replace it?": "%1 đã có.\nGhi đè lên tệp đó?",
        "%1\nFile not found.\nPlease verify the correct file name was given.":
            "%1\nKhông thấy tệp.\nKiểm tra lại tên tệp.",
        "Are you sure you want to delete '%1'?": "Xóa '%1'?",
    },
    "QFileSystemModel": {"Name": "Tên", "Size": "Cỡ", "Type": "Loại", "Kind": "Loại", "Date Modified": "Ngày sửa",
                         "My Computer": "Máy tính", "Computer": "Máy tính", "File Folder": "Thư mục", "Folder": "Thư mục",
                         "Drive": "Ổ đĩa", "Unknown": "Không rõ"},
}


class _FileDialogTranslator(QTranslator):
    """Vietnamese for Qt's file dialog, plus app.ui.qt_vi's texts; anything else comes back unchanged. (Qt takes the
    first non-null answer of the newest translator, and an empty string from Python is not null: so this one must
    answer everything itself, exactly as qt_vi does.)"""

    def translate(self, context, source_text, disambiguation=None, n=-1):  # noqa: D401 - Qt override
        from app.ui.qt_vi import TEXTS

        ctx = context or ""
        return FILE_DIALOG_TEXTS.get(ctx, {}).get(source_text) or TEXTS.get(ctx, {}).get(source_text, source_text)

    def isEmpty(self) -> bool:
        return False


_file_translator: _FileDialogTranslator | None = None


def _install_file_dialog_texts() -> None:
    global _file_translator
    app = QCoreApplication.instance()
    if app is not None and _file_translator is None:
        _file_translator = _FileDialogTranslator(app)
        app.installTranslator(_file_translator)


def make_file_dialog(parent: QWidget | None, *, save: bool, title: str, name: str = "", filters: str = JSON_FILTER,
                     directory: str = "") -> QFileDialog:
    """Qt's (not the native) file dialog: a Qt window we can exclude from capture before it shows."""
    _install_file_dialog_texts()
    start = directory or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    dlg = QFileDialog(parent, title, os.path.join(start, name) if name else start, filters)
    dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave if save else QFileDialog.AcceptMode.AcceptOpen)
    dlg.setFileMode(QFileDialog.FileMode.AnyFile if save else QFileDialog.FileMode.ExistingFile)
    if save and name:
        dlg.selectFile(name)
    for label, text in ((QFileDialog.DialogLabel.LookIn, "Tìm trong:"), (QFileDialog.DialogLabel.FileName, "Tên tệp:"),
                        (QFileDialog.DialogLabel.FileType, "Loại tệp:"),
                        (QFileDialog.DialogLabel.Accept, "Lưu" if save else "Mở"),
                        (QFileDialog.DialogLabel.Reject, "Hủy")):
        dlg.setLabelText(label, text)
    dlg._capture_exclusion_keep = apply_to_widget(dlg)  # before it is shown
    return dlg


def pick_file(parent: QWidget | None, *, save: bool = False, title: str = "", name: str = "",
              filters: str = JSON_FILTER) -> str:
    """The chosen path, "" when cancelled (tests patch this)."""
    dlg = make_file_dialog(parent, save=save, title=title or ("Lưu thành tệp" if save else "Mở tệp"), name=name,
                           filters=filters)
    popups = _PopupExcluder(dlg)
    popups.install()
    try:
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        files = dlg.selectedFiles()
    finally:
        popups.remove()
        dlg.deleteLater()
    return files[0] if ok and files else ""


# --- consent ------------------------------------------------------------------------------------------------

class ConsentDialog(_ExcludedDialog):
    """Asked once per new game, before anything is sent: the titles that will be sent, the services, and
    Tra cứu / Để sau / Không hỏi lại game này."""

    lookup = Signal(str)  # the title to send (the user may have corrected it)
    declined = Signal(bool)  # True: "Không hỏi lại game này"
    request_instead = Signal(str)  # "Tạo yêu cầu cho Claude…" instead of looking it up online (no network)

    def __init__(self, identity: GameIdentity | None, sources: list[str], *, web_target: str = "",
                 manual: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.identity = identity or GameIdentity()
        self.sources = [s for s in sources if s in SOURCES]
        self.manual = manual
        self.web_target = web_target
        self.setWindowTitle("Tra cứu dữ liệu game — Screen Translator")
        self.resize(620, 400)
        lay = QVBoxLayout(self)
        ident = self.identity
        if manual:
            head = "<b>Tra cứu dữ liệu cộng đồng cho một game</b>"
        else:
            head = f"<b>Game mới: {html.escape(ident.title or ident.exe_name)}</b>"
        details = [x for x in (ident.exe_name, f"Steam {ident.steam_appid}" if ident.steam_appid else "", ident.engine)
                   if x]
        self.head_label = QLabel(head + (f" <span style='color:gray'>({html.escape(' · '.join(details))})</span>"
                                         if details else ""))
        self.head_label.setWordWrap(True)
        lay.addWidget(self.head_label)
        lay.addWidget(QLabel("App có thể tra tên nhân vật, thuật ngữ và bản Việt hóa của game này trên mạng một lần, "
                             "rồi dựng sẵn hồ sơ thuật ngữ (như hồ sơ Blue Archive). Chưa gửi gì cho tới khi bạn bấm "
                             "Tra cứu.", wordWrap=True))
        row = QHBoxLayout()
        row.addWidget(QLabel("Tên game sẽ gửi đi:"))
        self.title_edit = QLineEdit(ident.title)
        self.title_edit.setPlaceholderText("gõ tên game, ví dụ: Steins;Gate")
        self.title_edit.textChanged.connect(self._refresh)
        row.addWidget(self.title_edit, 1)
        lay.addLayout(row)
        self.sent_label = QLabel()
        self.sent_label.setWordWrap(True)
        self.sent_label.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.sent_label)
        lay.addWidget(_hint(NOT_SENT))
        lay.addStretch(1)
        buttons = QHBoxLayout()
        self.request_button = QPushButton("Tạo yêu cầu cho Claude…")
        self.request_button.setToolTip("Không lên mạng: app tạo một đoạn yêu cầu để bạn tự dán cho Claude")
        self.request_button.clicked.connect(self._on_request)
        buttons.addWidget(self.request_button)
        buttons.addStretch(1)
        self.lookup_button = QPushButton("Tra cứu")
        self.lookup_button.setDefault(True)
        self.lookup_button.clicked.connect(self._on_lookup)
        self.no_button = QPushButton("Để sau")
        self.no_button.setToolTip("Lần này không tra; game vẫn nằm trong mục “Tra cứu game” ở khay")
        self.no_button.clicked.connect(lambda: self._decline(False))
        self.never_button = QPushButton("Không hỏi lại game này")
        self.never_button.clicked.connect(lambda: self._decline(True))
        self.never_button.setVisible(not manual)
        for b in (self.lookup_button, self.no_button, self.never_button):
            buttons.addWidget(b)
        lay.addLayout(buttons)
        self._refresh()

    def titles(self) -> list[str]:
        """Exactly what a lookup started now sends (with_title(identity, edited).query_titles())."""
        first = " ".join(self.title_edit.text().split())
        return with_title(self.identity, first).query_titles() if first else []

    def _refresh(self) -> None:
        titles = self.titles()
        if not titles:
            self.sent_label.setText("Gõ tên game ở trên.")
            self.lookup_button.setEnabled(False)
            return
        parts = [f"{'các tên' if len(titles) > 1 else 'tên'} " + ", ".join(f"“{html.escape(t)}”" for t in titles)]
        steam = self.identity.steam_appid if any(s in self.sources for s in ("steam", "wikidata")) else None
        if steam:  # the id read from the game's Steam files goes out too
            parts.append(f"mã Steam {steam} (đọc từ tệp Steam của game)")
        lines = "".join(f"<li>{html.escape(x)}</li>" for x in sent_to_lines(self.sources, self.web_target))
        self.sent_label.setText(f"Sẽ gửi {', '.join(parts)} và tên chính thức mà các nguồn tìm thấy, tới:<ul "
                                f"style='margin-top:2px; margin-bottom:2px'>{lines or '<li>(chưa bật nguồn nào)</li>'}</ul>")
        self.lookup_button.setEnabled(bool(self.sources))

    def _on_lookup(self) -> None:
        titles = self.titles()
        if not titles:
            return
        self.lookup.emit(titles[0])
        self.accept()

    def _on_request(self) -> None:
        self.request_instead.emit(" ".join(self.title_edit.text().split()))
        self.accept()

    def _decline(self, never: bool) -> None:
        self.declined.emit(never)
        self.reject()


# --- the draft preview ----------------------------------------------------------------------------------------

class DraftView(QTreeWidget):
    """A draft profile grouped as Nhân vật / Thuật ngữ / Bản Việt hóa / Nguồn. Characters and terms have check
    boxes (entries the target profile already has are shown unchecked: they stay as the user has them)."""

    link_clicked = Signal(str)
    selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHeaderLabels(["Gốc", "Tiếng Việt / La-tinh", "Ghi chú"])
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        header = self.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.setColumnWidth(0, 210)
        self.setColumnWidth(1, 190)
        self._draft: Profile | None = None
        self._groups: dict[str, QTreeWidgetItem] = {}
        self.itemChanged.connect(lambda _item, col: col == 0 and self.selection_changed.emit())

    def set_draft(self, draft: Profile | None, existing: Profile | None = None) -> None:
        self.blockSignals(True)
        try:
            self.clear()
            self._groups = {}
            self._draft = draft.copy() if draft is not None else None
            if draft is None:
                return
            have_c = {normalize_source(c.name) for c in existing.characters} if existing else set()
            have_t = ({normalize_source(t.source) for t in existing.terms} | set(existing.rejected)) if existing else set()
            if draft.characters:
                g = self._group("chars", f"Nhân vật ({len(draft.characters)})", checkable=True)
                for i, c in enumerate(draft.characters):
                    note = "; ".join(x for x in (GENDER_LABELS.get(c.gender, c.gender) if c.gender else "", c.notes) if x)
                    self._row(g, ("char", i), [c.name, c.name_vi, note], normalize_source(c.name) in have_c | have_t)
            if draft.terms:
                g = self._group("terms", f"Thuật ngữ ({len(draft.terms)})", checkable=True)
                for i, t in enumerate(draft.terms):
                    note = "; ".join(x for x in (DRAFT_KIND_LABELS.get(t.kind, t.kind),
                                                 "gợi ý, chờ bạn xác nhận" if t.auto else "", t.note) if x)
                    self._row(g, ("term", i), [t.source, t.target, note], normalize_source(t.source) in have_t | have_c)
            meta = draft.lookup if isinstance(draft.lookup, dict) else {}
            patches = [p for p in meta.get("patches") if isinstance(p, dict) and isinstance(p.get("url"), str)
                       and p["url"]] if isinstance(meta.get("patches"), list) else []
            known = [p for p in patches if p.get("official") or p.get("verified")]  # VNDB: someone recorded them
            unverified = [p for p in patches if p not in known]  # web hits, an assistant's answer
            collapsed = []
            for key, rows, title in (("patches", known, f"Bản Việt hóa ({len(known)}) — chỉ là đường link"),
                                     ("web_patches", unverified, f"Kết quả tìm web, chưa kiểm chứng ({len(unverified)})")):
                if not rows:
                    continue
                g = self._group(key, title)
                for p in rows:
                    note = str(p.get("note") or "")
                    flag = "chính thức" if p.get("official") else "có trên VNDB" if p.get("verified") else \
                        "" if "chưa kiểm chứng" in note else "chưa kiểm chứng"
                    item = QTreeWidgetItem(g, [str(p.get("title") or ""), "", "; ".join(x for x in (flag, note) if x)])
                    item.setToolTip(0, str(p.get("title") or ""))
                    item.setToolTip(2, item.text(2))
                    self._link_widget(item, str(p["url"]))
                if key == "web_patches" and known:
                    collapsed.append(g)  # after the recorded ones, folded: unverified pages
            attribution = [a for a in meta.get("attribution") if isinstance(a, dict)] \
                if isinstance(meta.get("attribution"), list) else []
            if attribution:
                g = self._group("sources", f"Nguồn ({len(attribution)})")
                for a in attribution:
                    item = QTreeWidgetItem(g, [str(a.get("source") or ""), "", str(a.get("license") or "")])
                    if a.get("url"):
                        self._link_widget(item, str(a["url"]))
            self.expandAll()
            for g in collapsed:
                g.setExpanded(False)
        finally:
            self.blockSignals(False)
        self.selection_changed.emit()

    def _group(self, key: str, title: str, *, checkable: bool = False) -> QTreeWidgetItem:
        g = QTreeWidgetItem(self, [title])
        f = g.font(0)
        f.setBold(True)
        g.setFont(0, f)
        g.setFirstColumnSpanned(True)
        if checkable:
            g.setFlags(g.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
            g.setCheckState(0, Qt.CheckState.Checked)
        self._groups[key] = g
        return g

    def _row(self, group: QTreeWidgetItem, ref: tuple[str, int], texts: list[str], exists: bool) -> QTreeWidgetItem:
        item = QTreeWidgetItem(group, texts)
        item.setData(0, _ROLE, ref)
        for col, text in enumerate(texts):
            if text:
                item.setToolTip(col, text)
        if exists:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setText(2, "đã có trong hồ sơ, giữ nguyên")
            for col in range(3):
                item.setForeground(col, QColor(128, 128, 128))
            item.setData(0, _ROLE + 1, True)
        else:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
        return item

    def _link_widget(self, item: QTreeWidgetItem, url: str) -> None:
        label = QLabel(_link(url))
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse
                                      | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        label.setOpenExternalLinks(False)
        label.setToolTip(url)
        label.linkActivated.connect(self.link_clicked)
        self.setItemWidget(item, 1, label)

    # --- reading the choice ---

    def _checked(self, kind: str, *, existing: bool = False) -> list[int]:
        """Indices of the checked rows; with `existing`, also the rows the target already has (merge_into keeps
        the user's version of those, and counts them as kept)."""
        g = self._groups.get("chars" if kind == "char" else "terms")
        out = []
        for i in range(g.childCount() if g is not None else 0):
            child = g.child(i)
            have = bool(child.data(0, _ROLE + 1))
            if (have and existing) or (not have and child.checkState(0) == Qt.CheckState.Checked):
                out.append(int(child.data(0, _ROLE)[1]))
        return out

    def counts(self) -> tuple[int, int]:
        """(characters, terms) that would be added."""
        return len(self._checked("char")), len(self._checked("term"))

    def set_all(self, checked: bool) -> None:
        for key in ("chars", "terms"):
            g = self._groups.get(key)
            if g is not None:
                g.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def items_of(self, key: str) -> list[QTreeWidgetItem]:
        g = self._groups.get(key)
        return [g.child(i) for i in range(g.childCount())] if g is not None else []

    def link_labels(self) -> list[QLabel]:
        out = []
        for key in ("patches", "web_patches", "sources"):
            for item in self.items_of(key):
                w = self.itemWidget(item, 1)
                if isinstance(w, QLabel):
                    out.append(w)
        return out

    def selected_profile(self) -> Profile | None:
        """The draft with only the checked characters / terms (their review marks follow them). Rows shown as
        already in the profile stay in it: merging never changes the user's version of them."""
        if self._draft is None:
            return None
        prof = self._draft.copy()
        chars, terms = set(self._checked("char", existing=True)), set(self._checked("term", existing=True))
        prof.characters = [c for i, c in enumerate(prof.characters) if i in chars]
        prof.terms = [t for i, t in enumerate(prof.terms) if i in terms]
        meta = dict(prof.lookup or {})
        review = meta.get("review") or {}
        if review:
            keep_c = {c.name for c in prof.characters}
            keep_t = {t.source for t in prof.terms}
            review = {"characters": {k: v for k, v in (review.get("characters") or {}).items() if k in keep_c},
                      "terms": {k: v for k, v in (review.get("terms") or {}).items() if k in keep_t}}
            if review["characters"] or review["terms"]:
                meta["review"] = review
            else:
                meta.pop("review", None)
            prof.lookup = meta
        return prof


class DraftPanel(QWidget):
    """DraftView plus where to put it: "Tạo hồ sơ “<tên>”" (or "Thêm vào hồ sơ “<tên>”" when that name exists) and
    "Thêm vào hồ sơ đang dùng “X”". `apply(profile, name)` does the saving and returns the message to show (it raises
    ValueError / OSError with a Vietnamese message when it cannot)."""

    applied = Signal(str)  # the profile that was written
    edit_requested = Signal(str)

    def __init__(self, *, apply: Callable[[Profile, str], str] | None, exists: Callable[[str], Profile | None] | None,
                 active_profile: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._apply = apply
        self._exists = exists or (lambda _name: None)
        self.active_profile = active_profile
        self._saved_name = ""
        self._has_draft = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = DraftView()
        self.view.link_clicked.connect(open_link)
        self.view.selection_changed.connect(self._refresh)
        lay.addWidget(self.view, 1)
        self.patch_label = QLabel(PATCH_WARNING)
        self.patch_label.setWordWrap(True)
        self.patch_label.setStyleSheet("QLabel { color: #8a4b00; }")
        self.patch_label.setVisible(False)
        lay.addWidget(self.patch_label)
        row = QHBoxLayout()
        row.addWidget(QLabel("Tên hồ sơ:"))
        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(self._refresh)
        row.addWidget(self.name_edit, 1)
        self.all_button = QPushButton("Chọn hết")
        self.all_button.clicked.connect(lambda: self.view.set_all(True))
        self.none_button = QPushButton("Bỏ chọn hết")
        self.none_button.clicked.connect(lambda: self.view.set_all(False))
        row.addWidget(self.all_button)
        row.addWidget(self.none_button)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.create_button = QPushButton()
        self.create_button.clicked.connect(lambda: self._do(self.name_edit.text()))
        self.merge_button = QPushButton()
        self.merge_button.clicked.connect(lambda: self._do(self.active_profile))
        self.edit_button = QPushButton("Mở hồ sơ để xem lại…")
        self.edit_button.setVisible(False)
        self.edit_button.clicked.connect(lambda: self.edit_requested.emit(self._saved_name))
        row.addWidget(self.create_button)
        row.addWidget(self.merge_button)
        row.addWidget(self.edit_button)
        row.addStretch(1)
        lay.addLayout(row)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)
        self.set_draft(None)

    def set_draft(self, draft: Profile | None, *, name: str = "") -> None:
        name = " ".join((name or (draft.name if draft is not None else "")).split())
        existing = self._exists(name) if name else None
        if existing is None and self.active_profile and self.active_profile.casefold() != "default":
            existing = self._exists(self.active_profile)
        self.view.set_draft(draft, existing)
        has_patches = bool(draft is not None and (draft.lookup or {}).get("patches"))
        self.patch_label.setVisible(has_patches)
        self._has_draft = draft is not None
        for w in (self.name_edit, self.all_button, self.none_button):
            w.setEnabled(self._has_draft)
        self.name_edit.setText(name)
        self.edit_button.setVisible(False)
        self.set_status("")
        self._refresh()

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setStyleSheet("color: #b71c1c;" if error else "")
        self.status_label.setText(text)

    def _refresh(self) -> None:
        name = " ".join(self.name_edit.text().split())
        exists = bool(name) and self._exists(name) is not None
        self.create_button.setText(f"Thêm vào hồ sơ “{_elide(name)}”" if exists else
                                   f"Tạo hồ sơ “{_elide(name)}”" if name else "Tạo hồ sơ mới")
        self.create_button.setToolTip("Hồ sơ này đã có: chỉ thêm mục mới, mục bạn đã có giữ nguyên" if exists else
                                      "Tạo hồ sơ mới với các mục đã chọn và dùng nó ngay")
        active = self.active_profile
        # Never into the default profile: every game without a profile of its own uses it.
        show_merge = bool(active) and active.casefold() not in (name.casefold(), "default")
        self.merge_button.setVisible(show_merge)
        if show_merge:
            self.merge_button.setText(f"Thêm vào hồ sơ đang dùng “{_elide(active)}”")
            self.merge_button.setToolTip("Chỉ thêm mục mới; mục bạn đã có giữ nguyên")
        draft = getattr(self, "_has_draft", False)
        self.create_button.setEnabled(draft and bool(name))
        self.merge_button.setEnabled(draft)

    def _do(self, name: str) -> None:
        profile = self.view.selected_profile()
        name = " ".join((name or "").split())
        if profile is None or not name or self._apply is None:
            return
        try:
            message = self._apply(profile, name)
        except (ValueError, OSError) as e:
            log.warning("saving the looked-up profile failed", exc_info=True)
            self.set_status(f"Không lưu được: {e}", error=True)
            return
        except Exception as e:  # noqa: BLE001 - a bug in an odd imported file: shown, never lost in a Qt slot
            log.exception("saving the looked-up profile failed")
            self.set_status(f"Không lưu được ({type(e).__name__}); xem data/logs/app.log.", error=True)
            return
        self._saved_name = name
        self.set_status(message)
        self.edit_button.setVisible(True)
        self.applied.emit(name)
        self._refresh()


def _elide(text: str, limit: int = 32) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- the lookup window ------------------------------------------------------------------------------------------

class LookupDialog(_ExcludedDialog):
    """Runs one lookup on a worker thread and shows it: per-source progress, cancel, the draft (with check boxes),
    the alternatives when the match is unsure, patch links, and where to save it."""

    report_ready = Signal(object)  # LookupReport (GUI thread)
    request_claude = Signal(object)  # LookupReport | GameIdentity: "Tạo yêu cầu cho Claude…"
    import_requested = Signal()
    edit_requested = Signal(str)
    applied = Signal(str)

    def __init__(self, identity: GameIdentity, *, make_service: Callable[[], object], sources: list[str],
                 apply: Callable[[Profile, str], str] | None = None,
                 exists: Callable[[str], Profile | None] | None = None, active_profile: str = "",
                 web_target: str = "", build_draft: Callable[[LookupReport], Profile] | None = None,
                 profile_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.identity = identity
        self.sources = [s for s in sources if s in SOURCES]
        self._make_service = make_service
        self._build_draft = build_draft
        self.web_target = web_target
        self.profile_name = profile_name  # the profile name offered (e.g. the one just made by "Tạo hồ sơ mới")
        self.report: LookupReport | None = None
        self.draft: Profile | None = None
        self._cancel: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._rows: dict[str, QTreeWidgetItem] = {}
        self.setWindowTitle("Tra cứu game — Screen Translator")
        self.setSizeGripEnabled(True)
        self.resize(860, 780)
        lay = QVBoxLayout(self)
        self.head_label = QLabel()
        self.head_label.setWordWrap(True)
        lay.addWidget(self.head_label)
        self.sent_label = _hint("")
        lay.addWidget(self.sent_label)
        self.source_tree = QTreeWidget()
        self.source_tree.setColumnCount(4)
        self.source_tree.setHeaderLabels(["Nguồn", "Trạng thái", "Thời gian", "Kết quả"])
        self.source_tree.setRootIsDecorated(False)
        self.source_tree.setUniformRowHeights(True)
        self.source_tree.setColumnWidth(0, 150)
        self.source_tree.setColumnWidth(1, 100)
        self.source_tree.setColumnWidth(2, 70)
        self.source_tree.header().setStretchLastSection(True)
        self.source_tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.source_tree)
        self._fit_source_tree()
        row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(10)
        row.addWidget(self.progress, 1)
        self.cancel_button = QPushButton("Hủy tra cứu")
        self.cancel_button.clicked.connect(self.cancel)
        row.addWidget(self.cancel_button)
        lay.addLayout(row)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextFormat(Qt.TextFormat.RichText)
        self.summary_label.setOpenExternalLinks(False)
        lay.addWidget(self.summary_label)
        self.alt_widget = QWidget()
        alt = QHBoxLayout(self.alt_widget)
        alt.setContentsMargins(0, 0, 0, 0)
        alt.addWidget(QLabel("Không chắc đúng game? Chọn:"))
        self.alt_combo = QComboBox()
        alt.addWidget(self.alt_combo, 1)
        self.alt_button = QPushButton("Tra cứu game này")
        self.alt_button.clicked.connect(self._lookup_alternative)
        alt.addWidget(self.alt_button)
        self.alt_widget.setVisible(False)
        lay.addWidget(self.alt_widget)
        self.alias_check = QCheckBox(ALIAS_OPTION)  # off: VNDB has no spoiler flag on nicknames
        self.alias_check.setToolTip(ALIAS_TIP)
        self.alias_check.setVisible(False)
        self.alias_check.toggled.connect(self._rebuild_draft)
        lay.addWidget(self.alias_check)
        self.panel = DraftPanel(apply=apply, exists=exists, active_profile=active_profile)
        self.panel.edit_requested.connect(self.edit_requested)
        self.panel.applied.connect(self.applied)
        lay.addWidget(self.panel, 1)
        self._filler = QWidget()  # takes the room while there is no draft to show
        lay.addWidget(self._filler, 1)
        self._show_panel(False)
        bottom = QHBoxLayout()
        self.request_button = QPushButton("Tạo yêu cầu cho Claude…")
        self.request_button.clicked.connect(
            lambda: self.request_claude.emit(self.report if self.report is not None else self.identity))
        self.import_button = QPushButton("Nhập hồ sơ từ file…")
        self.import_button.clicked.connect(self.import_requested)
        bottom.addWidget(self.request_button)
        bottom.addWidget(self.import_button)
        bottom.addStretch(1)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.close)
        bottom.addWidget(self.close_button)
        lay.addLayout(bottom)
        self._set_header()

    def _show_panel(self, on: bool) -> None:
        self.panel.setVisible(on)
        self._filler.setVisible(not on)

    def _fit_source_tree(self) -> None:
        """Tall enough for every source row (5 at most), no scroll bar."""
        tree = self.source_tree
        n = max(tree.topLevelItemCount(), len(self.sources), 1)
        first = tree.topLevelItem(0)
        row = tree.visualItemRect(first).height() if first is not None else 0
        row = row or tree.fontMetrics().height() + 4  # before the first layout an item has no rectangle yet
        tree.setFixedHeight(tree.header().sizeHint().height() + n * row + 2 * tree.frameWidth() + 2)

    def _set_header(self) -> None:
        ident = self.identity
        name = ident.title or ident.exe_name or "?"
        self.head_label.setText(f"<b>Tra cứu: {html.escape(name)}</b>"
                                + (f" <span style='color:gray'>({html.escape(ident.exe_name)})</span>"
                                   if ident.exe_name and ident.exe_name != name else ""))
        if not self.sources:
            self.sent_label.setText("Không gửi gì lên mạng.")
            return
        titles = ", ".join(f"“{t}”" for t in ident.query_titles())
        where = ", ".join(SOURCE_LABELS[s] for s in self.sources if s != "web" or self.web_target)
        self.sent_label.setText(f"Gửi tên game {titles} tới: {where or 'không nguồn nào'}. {NOT_SENT}")

    # --- running ---

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, title: str | None = None) -> None:
        """Start the lookup (again, with `title` instead of the identity's first title)."""
        if self.is_running():
            return
        if title:
            self.identity = with_title(self.identity, title)
        ident = self.identity
        self.report = None
        self.draft = None
        self.panel.set_draft(None)
        self._show_panel(False)
        self.alt_widget.setVisible(False)
        self.alias_check.blockSignals(True)
        self.alias_check.setChecked(False)
        self.alias_check.blockSignals(False)
        self.alias_check.setVisible(False)
        self._set_header()
        log.info("game lookup %r: sending the title(s) %s to %s%s", ident.key, ident.query_titles(),
                 ", ".join(s for s in self.sources) or "nothing",
                 f" (web through {self.web_target})" if "web" in self.sources and self.web_target else "")
        self.source_tree.clear()
        self._rows = {}
        for s in self.sources:
            self._status_row(SourceStatus(s, state="waiting"))
        self.summary_label.setText("Đang tra cứu… (thường mất 2–8 giây; tối đa khoảng 30 giây)")
        self.progress.setVisible(True)
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        self.request_button.setEnabled(True)
        self._cancel = threading.Event()
        bridge = _Bridge()
        bridge.progress.connect(self._on_progress)
        bridge.done.connect(self._on_done)
        bridge.done.connect(bridge.deleteLater)
        self._bridge = bridge
        self._thread = threading.Thread(target=_lookup_worker, name="lookup-ui",
                                        args=(bridge, self._make_service, ident, list(self.sources), self._cancel),
                                        daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        if self._cancel is not None and not self._cancel.is_set():
            log.info("game lookup cancelled by the user")
            self._cancel.set()
            self.cancel_button.setEnabled(False)
            self.summary_label.setText("Đang hủy…")

    def show_report(self, report: LookupReport) -> None:
        """Show a finished report (also used by tests and for a template found before any network)."""
        self._on_done(report, None)

    def _status_row(self, st: SourceStatus) -> None:
        item = self._rows.get(st.name)
        if item is None:
            item = QTreeWidgetItem(self.source_tree, [SOURCE_LABELS.get(st.name, st.name)])
            self._rows[st.name] = item
            self._fit_source_tree()
        item.setText(1, STATE_LABELS.get(st.state, st.state))
        color = _STATE_COLORS.get(st.state)
        item.setForeground(1, color if color is not None else self.palette().text().color())
        item.setText(2, f"{st.elapsed_s:.1f} s" if st.elapsed_s else "")
        note = found_text(st.found) or status_message(st)
        if st.cached and not st.requests:
            note = (note + "; " if note else "") + "từ bộ nhớ đệm"
        item.setText(3, note)
        item.setToolTip(3, st.message or note)

    def _on_progress(self, st: SourceStatus) -> None:
        self._status_row(st)

    def _on_done(self, report: LookupReport | None, error: str | None) -> None:
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        self._thread = None
        if report is None:
            self.summary_label.setText(f"<span style='color:#b71c1c'>Tra cứu lỗi: {html.escape(error or '?')}</span>")
            return
        self.report = report
        for st in report.statuses:
            if st.state != "skipped" or st.name in self.sources:
                self._status_row(st)
        if report.cancelled:
            self.summary_label.setText("Đã hủy tra cứu. Chưa có gì được lưu.")
            self.report_ready.emit(report)
            return
        from app.lookup.profile_builder import describe, has_aliases

        lines = describe(report)
        if report.template:
            self.sent_label.setText("Không gửi gì lên mạng: đã có hồ sơ mẫu đi kèm app.")
        unsure = bool(report.title) and not report.template and report.confidence != "high"
        if not report.found_anything:
            lines = [f"Không tìm thấy dữ liệu nào về “{report.query or self.identity.title}”. App vẫn dịch như bình "
                     "thường. Bạn có thể tạo yêu cầu cho Claude, hoặc tự thêm nhân vật trong “Sửa thuật ngữ / nhân "
                     "vật…”."] + [x for x in report.notes if x not in lines]
            self.draft = None
            self.panel.set_draft(None)
        elif unsure:  # maybe another game: nothing to save until the user picks the right one
            lines = [x for x in lines if not x.startswith("Chưa chắc đây đúng là")] + [UNSURE_HINT]
            self.draft = None
            self.panel.set_draft(None)
            self._show_panel(False)
        else:
            self.alias_check.setVisible(self._build_draft is None and has_aliases(report))
            self._rebuild_draft()
            if self.draft is not None and report.template:
                lines.append(f"Hồ sơ mẫu có {len(self.draft.characters)} nhân vật và {len(self.draft.terms)} thuật ngữ, "
                             "làm tay theo quy ước dịch của app.")
            elif self.draft is not None and (self.draft.characters or self.draft.terms):
                at = next((i + 1 for i, x in enumerate(lines) if x.startswith("Tìm thấy:")), len(lines))
                lines.insert(at, self._draft_line())
        self.summary_label.setText("<br>".join(html.escape(x) for x in lines))
        seen: set[str] = set()
        alts = []
        if unsure:  # the best match first, then the other close ones
            for a in [c for c in report.candidates if c.title] + [a for a in report.alternatives if a.title]:
                if a.title.casefold() not in seen:
                    seen.add(a.title.casefold())
                    alts.append(a)
        self.alt_combo.clear()
        for a in alts[:6]:
            extra = " · ".join(x for x in (SOURCE_LABELS.get(a.source, a.source), a.description[:50]) if x)
            self.alt_combo.addItem(f"{a.title} ({extra})" if extra else a.title, a.title)
        self.alt_widget.setVisible(bool(alts))
        log.info("game lookup %r: %s in %.1f s; %s", self.identity.key, report.confidence or "nothing found",
                 report.elapsed_s, "; ".join(f"{s.name} {s.state} ({s.requests} requests, {s.cached} cached)"
                                             for s in report.statuses))
        self.report_ready.emit(report)

    def _draft_line(self) -> str:
        d = self.draft
        auto = sum(t.auto for t in d.terms)
        return (f"Hồ sơ nháp: {len(d.characters)} nhân vật, {len(d.terms)} thuật ngữ"
                + (f" (trong đó {auto} gợi ý chờ bạn xác nhận)" if auto else "")
                + ". Bỏ chọn mục bạn không muốn rồi bấm Tạo / Thêm.")

    def _rebuild_draft(self, *_args) -> None:
        """(Re)build the draft from the report: at first, and when "Thêm biệt danh" is toggled."""
        report = self.report
        if report is None:
            return
        from app.lookup.profile_builder import build_profile

        name = " ".join(self.panel.name_edit.text().split()) if self.draft is not None else ""
        try:
            self.draft = self._build_draft(report) if self._build_draft is not None else \
                build_profile(report, include_aliases=self.alias_check.isChecked())
        except Exception:  # a builder bug must not lose the report
            log.exception("building the draft profile failed")
            self.draft = None
        self.panel.set_draft(self.draft, name=name or self.profile_name or (
            self.draft.name if self.draft is not None else report.title))
        self._show_panel(self.draft is not None)
        if self.draft is not None and not self.alias_check.isHidden() and self.summary_label.text():
            text = self.summary_label.text()
            self.summary_label.setText(re.sub(r"Hồ sơ nháp: [^<]*", html.escape(self._draft_line()), text, count=1))

    def _lookup_alternative(self) -> None:
        title = self.alt_combo.currentData()
        if title:
            self.start(str(title))

    def closeEvent(self, event) -> None:
        self.cancel()
        super().closeEvent(event)

    def reject(self) -> None:
        self.cancel()
        super().reject()


def _lookup_worker(bridge: _Bridge, make_service, ident: GameIdentity, sources: list[str],
                   cancel: threading.Event) -> None:
    service = None
    try:
        service = make_service()

        def progress(st: SourceStatus) -> None:
            bridge.progress.emit(dataclasses.replace(st, found=dict(st.found)))

        report = service.run(ident, sources, progress_cb=progress, cancel_event=cancel)
        bridge.done.emit(report, None)
    except Exception as e:  # noqa: BLE001 - shown in the window
        log.exception("game lookup failed")
        bridge.done.emit(None, f"{type(e).__name__}: {e}")
    finally:
        if service is not None:
            try:
                service.close()
            except Exception:
                log.debug("closing the lookup service failed", exc_info=True)


# --- request for Claude ---------------------------------------------------------------------------------------

class RequestDialog(_ExcludedDialog):
    """The "Tạo yêu cầu tra cứu" prompt (app.lookup.claude_request.build_request): Sao chép / Lưu thành file /
    Nhập câu trả lời. Nothing is sent anywhere."""

    import_requested = Signal()

    def __init__(self, text: str, *, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.game_title = title
        self.setWindowTitle("Yêu cầu tra cứu cho Claude — Screen Translator")
        self.setSizeGripEnabled(True)
        self.resize(760, 600)
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>Yêu cầu tra cứu: {html.escape(title)}</b>" if title else "<b>Yêu cầu tra cứu</b>")
        lay.addWidget(head)
        lay.addWidget(QLabel(REQUEST_HINT, wordWrap=True))
        self.text_edit = QPlainTextEdit(text)
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text_edit.setStyleSheet("QPlainTextEdit { font-family: Consolas, 'Cascadia Mono', monospace; }")
        lay.addWidget(self.text_edit, 1)
        row = QHBoxLayout()
        self.copy_button = QPushButton("Sao chép")
        self.copy_button.setDefault(True)
        self.copy_button.clicked.connect(self.copy)
        self.save_button = QPushButton("Lưu thành file…")
        self.save_button.clicked.connect(self.save_to_file)
        self.import_button = QPushButton("Nhập câu trả lời…")
        self.import_button.clicked.connect(self.import_requested)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.close)
        for b in (self.copy_button, self.save_button, self.import_button):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.close_button)
        lay.addLayout(row)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)

    def text(self) -> str:
        return self.text_edit.toPlainText()

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text())
        self.copy_button.setText("Đã sao chép")
        self.status_label.setText("Đã sao chép. Dán vào khung chat của Claude.")

    def save_to_file(self, path: str = "") -> str:
        safe = re.sub(r'[\\/:*?"<>|]+', " ", self.game_title or "game").strip() or "game"
        path = path or pick_file(self, save=True, title="Lưu yêu cầu tra cứu", name=f"{safe} - yeu cau tra cuu.md",
                                 filters=MD_FILTER)
        if not path:
            return ""
        try:
            Path(path).write_text(self.text(), encoding="utf-8")
        except OSError as e:
            self.status_label.setText(f"Không lưu được: {e}")
            return ""
        self.status_label.setText(f"Đã lưu: {path}")
        log.info("lookup request saved to %s", Path(path).name)
        return path


# --- import ------------------------------------------------------------------------------------------------------

class ImportDialog(_ExcludedDialog):
    """"Nhập hồ sơ": pasted text (a chat answer with a ```json block, raw JSON) or a profile file -> preview of what
    will be added -> create or merge (app.lookup.claude_request.parse_import)."""

    edit_requested = Signal(str)
    applied = Signal(str)

    def __init__(self, *, apply: Callable[[Profile, str], str] | None = None,
                 exists: Callable[[str], Profile | None] | None = None, active_profile: str = "",
                 fallback_name: str = "", exe_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.fallback_name = fallback_name
        self.exe_name = exe_name
        self.draft: Profile | None = None
        self.setWindowTitle("Nhập hồ sơ — Screen Translator")
        self.setSizeGripEnabled(True)
        self.resize(780, 640)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>Nhập hồ sơ (câu trả lời của Claude hoặc tệp hồ sơ)</b>"))
        lay.addWidget(_hint(IMPORT_HINT))
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("Dán câu trả lời vào đây…")
        self.text_edit.setMaximumHeight(110)
        lay.addWidget(self.text_edit)
        row = QHBoxLayout()
        self.paste_button = QPushButton("Dán từ clipboard")
        self.paste_button.clicked.connect(self.paste_clipboard)
        self.file_button = QPushButton("Mở file…")
        self.file_button.clicked.connect(lambda: self.choose_file())
        self.parse_button = QPushButton("Đọc hồ sơ")
        self.parse_button.clicked.connect(lambda: self.parse_text(self.text_edit.toPlainText()))
        for b in (self.paste_button, self.file_button, self.parse_button):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        lay.addWidget(self.info_label)
        self.panel = DraftPanel(apply=apply, exists=exists, active_profile=active_profile)
        self.panel.edit_requested.connect(self.edit_requested)
        self.panel.applied.connect(self.applied)
        lay.addWidget(self.panel, 1)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.close)
        bottom.addWidget(self.close_button)
        lay.addLayout(bottom)

    def _set_info(self, text: str, *, error: bool = False) -> None:
        self.info_label.setStyleSheet("color: #b71c1c;" if error else "")
        self.info_label.setText(text)

    def paste_clipboard(self) -> bool:
        text = QGuiApplication.clipboard().text()
        if not text.strip():
            self._set_info("Clipboard đang trống: sao chép câu trả lời của Claude trước.", error=True)
            return False
        self.text_edit.setPlainText(text)
        return self.parse_text(text)

    def choose_file(self, path: str = "") -> bool:
        path = path or pick_file(self, title="Mở tệp hồ sơ", filters=JSON_FILTER)
        if not path:
            return False
        from app.lookup.claude_request import import_file

        try:
            prof = import_file(path)
        except (OSError, ValueError) as e:
            self._set_info(f"Không đọc được {Path(path).name}: {e}", error=True)
            return False
        log.info("profile imported from %s", Path(path).name)
        return self._show(prof, f"Đã đọc {Path(path).name}.")

    def parse_text(self, text: str) -> bool:
        from app.lookup.claude_request import parse_import

        try:
            prof = parse_import(text, fallback_name=self.fallback_name or "Game")
        except ValueError as e:
            self._set_info(str(e), error=True)
            return False
        return self._show(prof, "Đã đọc hồ sơ từ đoạn dán.")

    def _show(self, prof: Profile, head: str) -> bool:
        from app.lookup.claude_request import romanized_only
        from app.profiles import exe_matches

        if self.exe_name and not any(exe_matches(self.exe_name, p) for p in prof.processes):
            prof.processes.append(self.exe_name)
        self.draft = prof
        lines = [f"{head} Hồ sơ “{prof.name}”: {len(prof.characters)} nhân vật, {len(prof.terms)} thuật ngữ"
                 + (f", {len((prof.lookup or {}).get('patches') or [])} trang Việt hóa" if (prof.lookup or {}).get("patches")
                    else "") + "."]
        missing = romanized_only(prof)
        if missing:
            lines.append("Các nhân vật sau không có tên chữ Nhật nên app không nhận ra họ trong chữ tiếng Nhật: "
                         + ", ".join(missing[:8]) + (" …" if len(missing) > 8 else "") + ".")
        if self.exe_name:  # only when the import was started from that game's own window
            lines.append(f"Gắn với game {self.exe_name}: hồ sơ tự bật khi bạn mở game này.")
        self._set_info(" ".join(lines), error=False)
        self.panel.set_draft(prof, name=prof.name)
        return True


# --- SearXNG install --------------------------------------------------------------------------------------------

def setup_command() -> list[str]:
    """tools/setup_searxng.py with the venv's console python; data/config.json is left to the app (it is running).
    --no-search: the check after the install starts SearXNG on 127.0.0.1 and stops it without searching anything
    (a test search would go out to Google, Brave... with the user's IP, and the window does not ask for that)."""
    exe = Path(sys.executable)
    console = exe.with_name("python.exe")
    return [str(console if console.is_file() else exe), "-u", "-m", "tools.setup_searxng", "--no-config", "--no-search"]


class SearxngInstallDialog(_ExcludedDialog):
    """Installs SearXNG with tools/setup_searxng.py in the background (no console window) and shows its output
    live; never starts by itself: the user presses "Bắt đầu cài". The process runs in a kill-on-close job object,
    so quitting the app stops it."""

    install_finished = Signal(bool, int)  # ok, port

    def __init__(self, *, command: list[str] | None = None, before_start: Callable[[], None] | None = None,
                 home: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from app.lookup.searxng import SEARXNG_HOME

        self._command = list(command) if command else setup_command()
        self._before_start = before_start
        self._proc: subprocess.Popen | None = None
        self._job: int | None = None
        self._stopping = False
        self.exit_code: int | None = None
        self.ask_before_stop = True  # the app turns the question off while it quits
        self.setWindowTitle("Cài SearXNG — Screen Translator")
        self.setSizeGripEnabled(True)
        self.resize(720, 480)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>Cài SearXNG trên máy (tìm web cho phần tra cứu game)</b>"))
        from app.lookup.searxng import short_path

        shown = home or (SEARXNG_HOME_SHOWN if str(SEARXNG_HOME).casefold() == str(Path.home() / ".screen-translator" /
                                                                                    "searxng").casefold()
                         else short_path(str(SEARXNG_HOME)))
        lay.addWidget(QLabel(SEARXNG_INSTALL_INFO.format(home=shown), wordWrap=True))
        self.progress = QProgressBar()
        self.progress.setRange(0, 5)
        self.progress.setValue(0)
        self.progress.setFormat("bước %v / %m")
        lay.addWidget(self.progress)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        self.log_view.setStyleSheet("QPlainTextEdit { font-family: Consolas, 'Cascadia Mono', monospace; }")
        lay.addWidget(self.log_view, 1)
        self.status_label = QLabel("Chưa bắt đầu.")
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)
        row = QHBoxLayout()
        self.start_button = QPushButton("Bắt đầu cài")
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Dừng")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.close)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        row.addStretch(1)
        row.addWidget(self.close_button)
        lay.addLayout(row)

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        if self.is_running():
            return False
        from app.llm.server import CREATE_NO_WINDOW, CREATE_SUSPENDED, _assign_and_resume, _k32, _kill_on_close_job

        if self._before_start is not None:
            try:
                self._before_start()  # the app's own SearXNG must not hold the port while it is (re)installed
            except Exception:
                log.warning("stopping SearXNG before the install failed", exc_info=True)
        env = dict(os.environ)
        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.log_view.clear()
        self.progress.setValue(0)
        self.exit_code = None
        self._stopping = False
        from app.lookup.searxng import short_path

        log.info("SearXNG install started by the user: %s", short_path(subprocess.list2cmdline(self._command)))
        try:
            job = _kill_on_close_job()
            proc = subprocess.Popen(self._command, cwd=str(PROJECT_ROOT), env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    creationflags=CREATE_NO_WINDOW | CREATE_SUSPENDED)
            try:
                _assign_and_resume(job, proc.pid)
            except OSError:
                proc.kill()
                _k32.CloseHandle(job)
                raise
        except OSError as e:
            log.warning("SearXNG install could not start", exc_info=True)
            self.status_label.setText(f"Không chạy được trình cài: {e}")
            return False
        self._proc, self._job = proc, job
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status_label.setText("Đang cài… (lần đầu mất vài phút)")
        bridge = _Bridge()
        bridge.line.connect(self._on_line)
        bridge.done.connect(self._on_exit)
        bridge.done.connect(bridge.deleteLater)
        self._bridge = bridge
        threading.Thread(target=_read_process, args=(proc, bridge), name="searxng-setup", daemon=True).start()
        return True

    def stop(self) -> None:
        if not self.is_running():
            return
        from app.llm.server import _k32

        log.info("SearXNG install stopped by the user")
        self._stopping = True
        if self._job is not None:
            _k32.TerminateJobObject(self._job, 1)
        try:
            self._proc.kill()
        except OSError:
            pass

    def _on_line(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        m = re.match(r"\s*\[(\d)/(\d)\]", text)
        if m:
            self.progress.setMaximum(int(m.group(2)))
            self.progress.setValue(int(m.group(1)) - 1)

    def _on_exit(self, code, _error) -> None:
        from app.llm.server import _k32
        from app.lookup import searxng

        self.exit_code = int(code) if code is not None else -1
        if self._job is not None:
            _k32.CloseHandle(self._job)
        self._proc, self._job = None, None
        self.start_button.setEnabled(True)
        self.start_button.setText("Cài lại")
        self.stop_button.setEnabled(False)
        ok = self.exit_code == 0 and searxng.installed()
        port = int((searxng.install_info() or {}).get("port") or searxng.DEFAULT_PORT)
        if ok:
            self.progress.setValue(self.progress.maximum())
            self.status_label.setText(f"Đã cài xong. App dùng SearXNG ở http://127.0.0.1:{port} (tự bật khi tra cứu).")
        elif self._stopping:
            self.status_label.setText("Đã dừng. Bấm “Cài lại” để cài tiếp (phần đã tải được dùng lại).")
        else:
            self.status_label.setText(f"Cài chưa xong (mã lỗi {self.exit_code}): xem các dòng “lỗi” ở trên.")
        log.info("SearXNG install finished: exit code %s, installed=%s", self.exit_code, ok)
        self.install_finished.emit(ok, port)

    def _may_close(self) -> bool:
        """Closing while it installs stops the install (after a question, unless the app is quitting)."""
        if not self.is_running() or self._stopping:
            return True
        if self.ask_before_stop and not ask(self, "Dừng cài SearXNG?", "SearXNG đang được cài. Dừng lại và đóng cửa "
                                            "sổ?", "Dừng và đóng", "Tiếp tục cài"):
            return False
        self.stop()
        return True

    def closeEvent(self, event) -> None:
        if not self._may_close():
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self._may_close():
            super().reject()


def _read_process(proc: subprocess.Popen, bridge: _Bridge) -> None:
    try:
        for raw in iter(proc.stdout.readline, b""):
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            for part in text.split("\r"):
                if part.strip():
                    bridge.line.emit(part.rstrip())
    except (OSError, ValueError):
        pass
    finally:
        code = proc.wait()
        try:
            proc.stdout.close()
        except OSError:
            pass
        bridge.done.emit(code, None)


__all__ = ["ConsentDialog", "DraftPanel", "DraftView", "ImportDialog", "LookupDialog", "RequestDialog",
           "SearxngInstallDialog", "is_non_game", "looks_like_new_game", "open_link", "pick_file", "sent_to_lines"]
