"""Editor for one glossary profile: terms, characters, style and the apps that select it.

Vietnamese UI. The dialog and every window it opens (combo-box lists, tooltips, message boxes, the
window picker) are excluded from screen capture, so the editor can sit over the capture region
without its own text being OCR'd and translated.

In the "Nhân vật" tab the selected character's address is edited with pickers (app.ui.pronoun_picker:
Tự xưng, one row per listener, the rest of the notes) instead of typing the notes format; the notes column
is written from them and stays editable by hand (a hand edit is read back into the pickers). A character's
notes are kept verbatim until its address actually changes (app.address_notes). Renaming a character also
renames it in the other characters' address rules, so their pairs keep working.
"""
from __future__ import annotations

import ctypes
import logging
import os
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass, replace

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.address_notes import SELF_WORDS, AddressNotes, rename_listener, rule_problems, self_problem
from app.overlay.affinity import CaptureExclusion, apply_to_widget
from app.profiles import REJECTED_CAP, Profile, ProfileStore, normalize_source
from app.translate.contextual import Character, Term
from app.ui.pronoun_picker import PronounPicker, display_name

log = logging.getLogger(__name__)

KIND_LABELS = {"person": "Nhân vật", "place": "Địa danh", "item": "Vật phẩm", "skill": "Kỹ năng",
               "org": "Tổ chức", "title": "Danh hiệu", "term": "Thuật ngữ"}
GENDER_LABELS = {"": "Không rõ", "nam": "Nam", "nữ": "Nữ"}
_GENDER_ALIASES = {"male": "nam", "m": "nam", "female": "nữ", "f": "nữ", "nu": "nữ", "nữ": "nữ", "nam": "nam"}
PRONOUNS = list(SELF_WORDS)  # the Tự xưng list, the same as the pronoun picker's
LANG_CHOICES = [(None, "Theo cài đặt chung"), ("ja", "Tiếng Nhật"), ("en", "Tiếng Anh")]
NAME_TIP = ("Tên đúng như chữ trong game (ví dụ セラ), để app nhận ra nhân vật trong khung thoại. Bắt buộc: "
            "dòng không có Tên gốc không lưu được.")

TERM_HEADERS = ["Gốc", "Tiếng Việt", "Loại", "Ghi chú", "Trạng thái"]
CHAR_HEADERS = ["Tên gốc", "Tên tiếng Việt", "Giới tính", "Tự xưng", "Ghi chú"]
T_SOURCE, T_TARGET, T_KIND, T_NOTE, T_STATE = range(5)
C_NAME, C_NAME_VI, C_GENDER, C_PRONOUN, C_NOTES = range(5)

_ROLE_KEY = Qt.ItemDataRole.UserRole  # kind / gender key behind the Vietnamese label
_ROLE_AUTO = Qt.ItemDataRole.UserRole + 1  # on the source item: proposed by the LLM, not confirmed
_ROLE_ORIG = Qt.ItemDataRole.UserRole + 2  # on the source item: normalised source as proposed
_ROLE_DRAFT = Qt.ItemDataRole.UserRole + 3  # on the notes item: the pickers' rows for that text (_Draft)
_ROLE_PREV = Qt.ItemDataRole.UserRole + 4  # on the name items: the name before the last edit (renames)

_AUTO_BG, _AUTO_FG = QColor(255, 241, 196), QColor(66, 48, 0)
_ERROR_BG, _ERROR_FG = QColor(255, 205, 210), QColor(110, 0, 0)

AUTO_TIP = "Gợi ý do LLM tự thêm, chưa xác nhận. Bấm “Xác nhận” để giữ cố định, sửa nếu sai, hoặc xóa."
CHAR_HINT = ("Chọn một nhân vật để đặt cách xưng hô ở khung “Xưng hô”. Cột “Ghi chú” được viết từ đó (ví dụ: "
             "gọi Ren là 'anh', xưng 'em'); di chuột lên ô để xem cả ghi chú.")
TERM_HINT = ("Tên riêng và thuật ngữ luôn được dịch đúng như cột “Tiếng Việt”. Dòng nền vàng là gợi ý LLM tự "
             "thêm: bấm “Xác nhận” để giữ, sửa nếu sai (sửa xong là đã xác nhận), xóa nếu không muốn — gợi ý "
             "đã xóa sẽ không được thêm lại.")
STYLE_PLACEHOLDER = ("Ví dụ: giọng kiếm hiệp cổ trang; giữ hậu tố -san, -sama; lời thoại ngắn gọn, tự nhiên; "
                     "gọi nhân vật chính là 'cậu'.")


# --- visible windows (for "Thêm cửa sổ đang mở…") -------------------------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetWindow.restype = wintypes.HWND
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
try:
    _dwmapi = ctypes.WinDLL("dwmapi")
    _dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    _dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
except OSError:  # pragma: no cover
    _dwmapi = None

_GW_OWNER = 4
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x80
_DWMWA_CLOAKED = 14
_TH32CS_SNAPPROCESS = 0x2
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.Process32FirstW.restype = wintypes.BOOL
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.Process32NextW.restype = wintypes.BOOL


def _process_names() -> dict[int, str]:
    """pid -> exe name from a Toolhelp snapshot (opens no process)."""
    snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE_VALUE:
        return {}
    out: dict[int, str] = {}
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out[int(entry.th32ProcessID)] = entry.szExeFile
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snap)
    return out


def _cloaked(hwnd: int) -> bool:
    if _dwmapi is None:
        return False
    value = wintypes.DWORD(0)
    hr = _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return hr == 0 and value.value != 0


def visible_window_processes() -> list[tuple[str, str]]:
    """(exe name, one window title) for the visible top-level windows of other processes, sorted.

    Window-manager information only: EnumWindows for the windows, a Toolhelp snapshot for the
    names. No process is opened and no window handle is kept.
    """
    own = os.getpid()
    titles: dict[int, str] = {}

    def _visit(hwnd, _lparam) -> bool:
        try:
            if not _user32.IsWindowVisible(hwnd) or _user32.GetWindow(hwnd, _GW_OWNER):
                return True
            if _user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW or _cloaked(hwnd):
                return True
            n = _user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            pid = wintypes.DWORD(0)
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value and pid.value != own and pid.value not in titles:
                buf = ctypes.create_unicode_buffer(n + 1)
                _user32.GetWindowTextW(hwnd, buf, n + 1)
                titles[int(pid.value)] = buf.value
        except Exception:  # never let an exception cross the ctypes callback
            log.debug("EnumWindows callback failed", exc_info=True)
        return True

    _user32.EnumWindows(_WNDENUMPROC(_visit), 0)
    names = _process_names()
    out: dict[str, tuple[str, str]] = {}
    for pid, title in titles.items():
        exe = names.get(pid)
        if exe and exe.lower() not in out:
            out[exe.lower()] = (exe, title)
    return sorted(out.values(), key=lambda it: it[0].lower())


# --- helpers ----------------------------------------------------------------------------------------

class _PopupExcluder(QObject):
    """App-wide event filter, installed while `root` is visible: windows opened from it (combo
    lists, tooltips, message boxes, pickers) are capture-excluded on their first Show, before they
    become visible."""

    def __init__(self, root: QWidget) -> None:
        super().__init__(root)
        self._root = root
        self.count = 0
        self.installed = False

    def install(self) -> None:
        app = QApplication.instance()
        if app is not None and not self.installed:
            app.installEventFilter(self)
            self.installed = True

    def remove(self) -> None:
        app = QApplication.instance()
        if app is not None and self.installed:
            app.removeEventFilter(self)
        self.installed = False

    def _belongs(self, w: QWidget) -> bool:
        p = w.parentWidget()
        while p is not None:
            if p is self._root:
                return True
            p = p.parentWidget()
        return False

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow() \
                and obj is not self._root and getattr(obj, "_capture_exclusion", None) is None:
            popup = obj.windowType() in (Qt.WindowType.ToolTip, Qt.WindowType.Popup)
            if popup or self._belongs(obj):
                apply_to_widget(obj)
                self.count += 1
        return False


@contextmanager
def _signals_blocked(obj: QObject):
    old = obj.blockSignals(True)
    try:
        yield
    finally:
        obj.blockSignals(old)


def ask(parent: QWidget, title: str, text: str, yes: str, no: str, *, destructive: bool = True) -> bool:
    """A modal question with Vietnamese buttons (Qt's standard ones would say "Discard" / "Cancel"); the
    safe answer `no` is the default and Esc. Capture-excluded: a popup of `parent` (_PopupExcluder) and
    excluded here too, for a parent that has no filter installed."""
    box = QMessageBox(QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.NoButton, parent)
    yes_button = box.addButton(yes, QMessageBox.ButtonRole.DestructiveRole if destructive
                               else QMessageBox.ButtonRole.AcceptRole)
    no_button = box.addButton(no, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(no_button)
    box.setEscapeButton(no_button)
    exclusion = apply_to_widget(box)  # noqa: F841 - must live as long as the box
    box.exec()
    clicked = box.clickedButton()
    box.deleteLater()
    return clicked is yes_button


def ask_discard(parent: QWidget) -> bool:
    """"Bỏ thay đổi?" before closing a window with unsaved changes; True: close without saving."""
    return ask(parent, "Bỏ thay đổi?", "Các thay đổi chưa được lưu. Đóng mà không lưu?", "Bỏ thay đổi",
               "Tiếp tục sửa")


@dataclass(frozen=True)
class _Draft:
    """The pronoun picker's rows (incomplete ones included) behind a notes cell's `text`."""

    text: str
    structure: AddressNotes


class _ChoiceDelegate(QStyledItemDelegate):
    """Combo-box editor; the cell shows the label and keeps the key in _ROLE_KEY (editable: free text)."""

    def __init__(self, choices: dict[str, str], parent: QObject, *, editable: bool = False) -> None:
        super().__init__(parent)
        self._choices = choices
        self._editable = editable

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.setEditable(self._editable)
        for key, label in self._choices.items():
            combo.addItem(label, key)
        return combo

    def setEditorData(self, editor: QComboBox, index) -> None:
        key = index.data(_ROLE_KEY)
        key = "" if key is None else str(key)
        if self._editable:
            editor.setEditText(key)
            return
        i = editor.findData(key)
        if i < 0 and key:
            editor.addItem(key, key)  # a kind the LLM invented: keep it selectable
            i = editor.count() - 1
        editor.setCurrentIndex(max(i, 0))

    def setModelData(self, editor: QComboBox, model, index) -> None:
        if self._editable:
            key = " ".join(editor.currentText().split())
            label = key
        else:
            key = editor.currentData()
            key = "" if key is None else str(key)
            label = self._choices.get(key, key)
        model.setData(index, key, _ROLE_KEY)
        model.setData(index, label, Qt.ItemDataRole.DisplayRole)


def _text(table: QTableWidget, row: int, col: int) -> str:
    item = table.item(row, col)
    return " ".join(item.text().split()) if item is not None else ""


def _key_of(table: QTableWidget, row: int, col: int) -> str:
    item = table.item(row, col)
    value = item.data(_ROLE_KEY) if item is not None else None
    return "" if value is None else str(value)


def _clean_exe(text: str) -> str:
    return os.path.basename(text.strip().strip('"').replace("/", "\\")).strip()


def _make_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
                          | QAbstractItemView.EditTrigger.AnyKeyPressed | QAbstractItemView.EditTrigger.SelectedClicked)
    table.setWordWrap(False)
    table.verticalHeader().setDefaultSectionSize(30)
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return table


# --- window picker ----------------------------------------------------------------------------------

class WindowPicker(QDialog):
    """Checkable list of the programs that have a visible window; returns their exe names."""

    def __init__(self, existing: list[str], parent: QWidget | None = None,
                 windows: list[tuple[str, str]] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Thêm cửa sổ đang mở")
        self.resize(520, 420)
        have = {e.lower() for e in existing}
        lay = QVBoxLayout(self)
        label = QLabel("Chọn game (chương trình) sẽ tự bật hồ sơ này khi bạn đang dùng cửa sổ của nó:")
        label.setWordWrap(True)
        lay.addWidget(label)
        self.list = QListWidget()
        for exe, title in windows if windows is not None else visible_window_processes():
            short = title if len(title) <= 60 else title[:59] + "…"
            item = QListWidgetItem(f"{exe}   —   {short}" if short else exe)
            item.setData(Qt.ItemDataRole.UserRole, exe)
            item.setToolTip(title)
            if exe.lower() in have:
                item.setText(f"{item.text()}   (đã có)")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
            self.list.addItem(item)
        if self.list.count() == 0:
            self.list.addItem(QListWidgetItem("(không tìm thấy cửa sổ nào)"))
        lay.addWidget(self.list)
        buttons = QDialogButtonBox()
        buttons.addButton("Thêm", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Hủy", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._exclusion = apply_to_widget(self)

    def selected(self) -> list[str]:
        out = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsEnabled and item.checkState() == Qt.CheckState.Checked:
                exe = item.data(Qt.ItemDataRole.UserRole)
                if exe:
                    out.append(exe)
        return out


# --- editor -----------------------------------------------------------------------------------------

class GlossaryEditor(QDialog):
    """Edit one profile; Lưu writes it to the store (merging auto terms the LLM added meanwhile)."""

    saved = Signal(str)  # profile name, after a successful save

    def __init__(self, store: ProfileStore, profile_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        profile = store.get(profile_name)
        self._name = profile.name
        self._loaded = profile
        self._loaded_sources = {normalize_source(t.source) for t in profile.terms}
        self._rejected = list(profile.rejected)
        self._saved: Profile | None = None
        self._dirty = False
        self._syncing = False  # the pronoun picker is writing into the characters table
        self.ask_before_discard = True  # tests turn the "discard changes?" question off

        shown = "Mặc định" if self._name.casefold() == "default" else self._name
        self.setWindowTitle(f"Thuật ngữ & nhân vật — {shown}")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setSizeGripEnabled(True)
        screen = self.screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        if avail is not None and avail.width() > 0:  # small screens (1366×768, 150 %): Lưu stays above the taskbar
            self.resize(min(1000, avail.width() - 40), min(700, avail.height() - 60))
        else:
            self.resize(1000, 700)

        root = QVBoxLayout(self)
        title = QLabel(f"Hồ sơ: <b>{_html(shown)}</b>")
        tf = QFont(title.font())
        tf.setPointSizeF(tf.pointSizeF() + 2)
        title.setFont(tf)
        root.addWidget(title)
        sub = QLabel("Tên riêng và thuật ngữ được giữ cố định khi dịch bằng LLM; thông tin nhân vật giúp chọn "
                     "đúng cách xưng hô (anh/em, cậu/tớ, tao/mày…).")
        sub.setWordWrap(True)
        sub.setEnabled(False)
        root.addWidget(sub)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_terms_tab(), "Thuật ngữ")
        self.tabs.addTab(self._build_characters_tab(), "Nhân vật")
        self.tabs.addTab(self._build_style_tab(), "Văn phong && game")  # "&&": a literal '&'
        root.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        bottom.addWidget(self.status_label, 1)
        self.buttons = QDialogButtonBox()
        self.save_button = self.buttons.addButton("Lưu", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = self.buttons.addButton("Hủy", QDialogButtonBox.ButtonRole.RejectRole)
        self.save_button.setDefault(True)
        self.buttons.accepted.connect(self._on_save_clicked)
        self.buttons.rejected.connect(self.reject)
        bottom.addWidget(self.buttons)
        root.addLayout(bottom)

        self._load(profile)
        # Before anything is shown: this dialog and all its popups never reach the OCR.
        self._popups = _PopupExcluder(self)
        self._exclusion: CaptureExclusion = apply_to_widget(self)

    # --- construction ------------------------------------------------------------------------

    def _build_terms_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Tìm thuật ngữ…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        bar.addWidget(self.filter_edit, 1)
        self.add_term_button = QPushButton("Thêm dòng")
        self.add_term_button.clicked.connect(lambda: self.add_term_row(edit=True))
        self.delete_term_button = QPushButton("Xóa dòng đã chọn")
        self.delete_term_button.clicked.connect(lambda: self.delete_term_rows(self._selected_rows(self.terms_table)))
        self.confirm_all_button = QPushButton("Xác nhận tất cả gợi ý")
        self.confirm_all_button.clicked.connect(self.confirm_all)
        for b in (self.add_term_button, self.delete_term_button, self.confirm_all_button):
            bar.addWidget(b)
        lay.addLayout(bar)

        self.terms_table = t = _make_table(TERM_HEADERS)
        self._delegates = [_ChoiceDelegate(KIND_LABELS, t)]
        t.setItemDelegateForColumn(T_KIND, self._delegates[0])
        h = t.horizontalHeader()
        for col, width in ((T_SOURCE, 190), (T_TARGET, 210), (T_KIND, 110)):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            t.setColumnWidth(col, width)
        h.setSectionResizeMode(T_NOTE, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(T_STATE, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(T_STATE, 104)
        t.itemChanged.connect(self._on_term_changed)
        lay.addWidget(t, 1)
        hint = QLabel(TERM_HINT)
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return page

    def _build_characters_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.char_filter_edit = QLineEdit()
        self.char_filter_edit.setPlaceholderText("Tìm nhân vật…")
        self.char_filter_edit.setClearButtonEnabled(True)
        self.char_filter_edit.textChanged.connect(self._apply_char_filter)
        bar.addWidget(self.char_filter_edit, 1)
        self.add_char_button = QPushButton("Thêm nhân vật")
        self.add_char_button.clicked.connect(lambda: self.add_character_row(edit=True))
        self.delete_char_button = QPushButton("Xóa dòng đã chọn")
        self.delete_char_button.clicked.connect(
            lambda: self.delete_character_rows(self._selected_rows(self.characters_table)))
        bar.addWidget(self.add_char_button)
        bar.addWidget(self.delete_char_button)
        lay.addLayout(bar)

        self.characters_table = t = _make_table(CHAR_HEADERS)
        self._delegates += [_ChoiceDelegate(GENDER_LABELS, t), _ChoiceDelegate({p: p for p in PRONOUNS}, t, editable=True)]
        t.setItemDelegateForColumn(C_GENDER, self._delegates[1])
        t.setItemDelegateForColumn(C_PRONOUN, self._delegates[2])
        h = t.horizontalHeader()
        for col, width in ((C_NAME, 150), (C_NAME_VI, 160), (C_GENDER, 90), (C_PRONOUN, 100)):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            t.setColumnWidth(col, width)
        h.setSectionResizeMode(C_NOTES, QHeaderView.ResizeMode.Stretch)
        t.horizontalHeaderItem(C_NAME).setToolTip(NAME_TIP)
        t.horizontalHeaderItem(C_PRONOUN).setToolTip("Cách nhân vật tự gọi mình (tôi, tớ, em, ta…).")
        t.horizontalHeaderItem(C_NOTES).setToolTip("Được viết từ khung “Xưng hô” bên dưới (di chuột lên ô để xem "
                                                    "cả ghi chú); vẫn sửa tay được.")
        # One line per character (a profile may have 80): long notes are elided, the whole text is the
        # cell's tooltip and the "Xưng hô" panel shows it.
        t.setWordWrap(False)
        t.setTextElideMode(Qt.TextElideMode.ElideRight)
        t.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        t.setMinimumHeight(90)
        t.itemChanged.connect(self._on_character_item_changed)
        t.currentCellChanged.connect(self._on_current_character_changed)

        self.pronoun_group = QGroupBox("Xưng hô")
        group_lay = QVBoxLayout(self.pronoun_group)
        self.pronoun_picker = PronounPicker()
        self.pronoun_picker.changed.connect(self._on_picker_changed)
        group_lay.addWidget(self.pronoun_picker)
        self.character_splitter = splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(t)
        splitter.addWidget(self.pronoun_group)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([260, 360])
        lay.addWidget(splitter, 1)
        self.character_hint = QLabel(CHAR_HINT)
        self.character_hint.setWordWrap(True)
        self.character_hint.setEnabled(False)
        lay.addWidget(self.character_hint)
        return page

    def _build_style_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel("Văn phong (gửi kèm cho LLM mỗi lần dịch):"))
        self.style_edit = QPlainTextEdit()
        self.style_edit.setPlaceholderText(STYLE_PLACEHOLDER)
        self.style_edit.setTabChangesFocus(True)
        self.style_edit.setMaximumHeight(130)
        self.style_edit.textChanged.connect(self._on_changed)
        lay.addWidget(self.style_edit)

        row = QHBoxLayout()
        row.addWidget(QLabel("Ngôn ngữ nguồn:"))
        self.lang_combo = QComboBox()
        for code, label in LANG_CHOICES:
            self.lang_combo.addItem(label, code)
        self.lang_combo.currentIndexChanged.connect(self._on_changed)
        row.addWidget(self.lang_combo)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addSpacing(8)
        lay.addWidget(QLabel("Tự chọn hồ sơ này khi bạn đang dùng cửa sổ của một trong các game (tệp .exe):"))
        body = QHBoxLayout()
        self.process_list = QListWidget()
        self.process_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        body.addWidget(self.process_list, 1)
        side = QVBoxLayout()
        self.pick_window_button = QPushButton("Thêm cửa sổ đang mở…")
        self.pick_window_button.clicked.connect(self.pick_windows)
        self.remove_process_button = QPushButton("Xóa")
        self.remove_process_button.clicked.connect(self.remove_selected_processes)
        side.addWidget(self.pick_window_button)
        side.addWidget(self.remove_process_button)
        side.addStretch(1)
        body.addLayout(side)
        lay.addLayout(body, 1)
        manual = QHBoxLayout()
        self.process_edit = QLineEdit()
        self.process_edit.setPlaceholderText("hoặc gõ tên tệp, ví dụ: game.exe")
        self.process_edit.returnPressed.connect(self._add_typed_process)
        self.add_process_button = QPushButton("Thêm")
        self.add_process_button.clicked.connect(self._add_typed_process)
        manual.addWidget(self.process_edit, 1)
        manual.addWidget(self.add_process_button)
        lay.addLayout(manual)
        note = QLabel("Chỉ đọc tên tệp .exe của cửa sổ; app không đọc hay can thiệp vào chương trình khác.")
        note.setWordWrap(True)
        note.setEnabled(False)
        lay.addWidget(note)
        return page

    def _load(self, profile: Profile) -> None:
        for term in profile.terms:
            self.add_term_row(term)
        for ch in profile.characters:
            self.add_character_row(ch)
        with _signals_blocked(self.style_edit):
            self.style_edit.setPlainText(profile.style)
        i = self.lang_combo.findData(profile.src_lang)
        if i < 0 and profile.src_lang:
            self.lang_combo.addItem(profile.src_lang, profile.src_lang)
            i = self.lang_combo.count() - 1
        with _signals_blocked(self.lang_combo):
            self.lang_combo.setCurrentIndex(max(i, 0))
        self.add_processes(profile.processes, mark_dirty=False)
        if self.characters_table.rowCount():
            self.characters_table.setCurrentCell(0, C_NAME)  # the pickers show the first character
        self._load_picker(self.characters_table.currentRow())  # (no signal if row 0 was current already)
        self._dirty = False
        self._update_summary()

    # --- terms -------------------------------------------------------------------------------

    def add_term_row(self, term: Term | None = None, *, edit: bool = False) -> int:
        t = term or Term("", "")
        table = self.terms_table
        if edit and self.filter_edit.text():
            self.filter_edit.clear()
        row = table.rowCount()
        with _signals_blocked(table):
            table.insertRow(row)
            src = QTableWidgetItem(t.source)
            src.setData(_ROLE_AUTO, bool(t.auto))
            src.setData(_ROLE_ORIG, normalize_source(t.source) if t.auto else "")
            table.setItem(row, T_SOURCE, src)
            table.setItem(row, T_TARGET, QTableWidgetItem(t.target))
            kind = QTableWidgetItem(KIND_LABELS.get(t.kind, t.kind))
            kind.setData(_ROLE_KEY, t.kind or "term")
            table.setItem(row, T_KIND, kind)
            table.setItem(row, T_NOTE, QTableWidgetItem(t.note))
            state = QTableWidgetItem("")
            state.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(row, T_STATE, state)
        self._style_term_row(row)
        if term is None:
            self._mark_dirty()
        if edit:
            table.scrollToItem(src)
            table.setCurrentCell(row, T_SOURCE)
            table.editItem(src)
        return row

    def is_auto(self, row: int) -> bool:
        item = self.terms_table.item(row, T_SOURCE)
        return bool(item is not None and item.data(_ROLE_AUTO))

    def confirm_button(self, row: int) -> QPushButton | None:
        w = self.terms_table.cellWidget(row, T_STATE)
        return w.findChild(QPushButton) if w is not None else None

    def confirm_row(self, row: int) -> None:
        item = self.terms_table.item(row, T_SOURCE)
        if item is None or not item.data(_ROLE_AUTO):
            return
        with _signals_blocked(self.terms_table):
            item.setData(_ROLE_AUTO, False)
        self._style_term_row(row)
        self._mark_dirty()

    def confirm_all(self) -> None:
        for row in range(self.terms_table.rowCount()):
            self.confirm_row(row)

    def delete_term_rows(self, rows: list[int]) -> None:
        for row in sorted(set(rows), reverse=True):
            item = self.terms_table.item(row, T_SOURCE)
            if item is not None and item.data(_ROLE_AUTO):
                orig = item.data(_ROLE_ORIG) or normalize_source(item.text())
                if orig and orig not in self._rejected:
                    self._rejected.append(orig)  # do not let the LLM propose it again
            self.terms_table.removeRow(row)
        if rows:
            self._mark_dirty()

    def _style_term_row(self, row: int, error: bool = False) -> None:
        table = self.terms_table
        auto = self.is_auto(row)
        with _signals_blocked(table):
            for col in range(table.columnCount()):
                item = table.item(row, col)
                if item is None:
                    continue
                if error:
                    item.setBackground(_ERROR_BG)
                    item.setForeground(_ERROR_FG)
                elif auto:
                    item.setBackground(_AUTO_BG)
                    item.setForeground(_AUTO_FG)
                else:
                    item.setData(Qt.ItemDataRole.BackgroundRole, None)
                    item.setData(Qt.ItemDataRole.ForegroundRole, None)
                item.setToolTip(AUTO_TIP if auto else (item.text() if col == T_NOTE else ""))
        if auto and table.cellWidget(row, T_STATE) is None:
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(6, 2, 6, 2)
            button = QPushButton("Xác nhận")
            button.setToolTip(AUTO_TIP)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            src_item = table.item(row, T_SOURCE)
            button.clicked.connect(lambda _checked=False, it=src_item: self.confirm_row(table.row(it)))
            hl.addWidget(button)
            table.setCellWidget(row, T_STATE, holder)
        elif not auto and table.cellWidget(row, T_STATE) is not None:
            table.removeCellWidget(row, T_STATE)

    def _on_term_changed(self, item: QTableWidgetItem) -> None:
        if item.column() in (T_SOURCE, T_TARGET) and self.is_auto(item.row()):
            self.confirm_row(item.row())  # the user corrected a suggestion: it is theirs now
        self._mark_dirty()

    def _apply_filter(self, text: str) -> None:
        needle = normalize_source(text)
        table = self.terms_table
        for row in range(table.rowCount()):
            hay = " ".join(normalize_source(_text(table, row, c)) for c in (T_SOURCE, T_TARGET, T_NOTE))
            table.setRowHidden(row, bool(needle) and needle not in hay)

    # --- characters --------------------------------------------------------------------------

    def add_character_row(self, ch: Character | None = None, *, edit: bool = False) -> int:
        c = ch or Character("", "")
        table = self.characters_table
        row = table.rowCount()
        if edit and self.char_filter_edit.text():
            self.char_filter_edit.clear()
        with _signals_blocked(table):
            table.insertRow(row)
            for col, value in ((C_NAME, c.name), (C_NAME_VI, c.name_vi)):
                item = QTableWidgetItem(value)
                item.setData(_ROLE_PREV, " ".join(value.split()))
                table.setItem(row, col, item)
            g = _GENDER_ALIASES.get(c.gender.strip().casefold(), c.gender.strip())
            gender = QTableWidgetItem(GENDER_LABELS.get(g, g))
            gender.setData(_ROLE_KEY, g)
            table.setItem(row, C_GENDER, gender)
            pron = QTableWidgetItem(c.self_pronoun)
            pron.setData(_ROLE_KEY, c.self_pronoun)
            table.setItem(row, C_PRONOUN, pron)
            notes = QTableWidgetItem(c.notes)
            notes.setToolTip(c.notes)
            table.setItem(row, C_NOTES, notes)
        if ch is None:
            self._mark_dirty()
        if edit:
            self.tabs.setCurrentIndex(1)
            table.scrollToItem(table.item(row, C_NAME))
            table.setCurrentCell(row, C_NAME)
            table.editItem(table.item(row, C_NAME))
        elif ch is None:
            self._refresh_picker_context()  # a new listener to choose
        return row

    def delete_character_rows(self, rows: list[int]) -> None:
        for row in sorted(set(rows), reverse=True):
            self.characters_table.removeRow(row)
        if rows:
            self._mark_dirty()
            # the current row may now be another character (its rows being edited are in its _Draft)
            self._load_picker(self.characters_table.currentRow())

    def _row_character(self, row: int) -> Character:
        """The character of table row `row` as it stands (notes not collapsed: the picker compares them)."""
        t = self.characters_table
        notes = t.item(row, C_NOTES)
        return Character(name=_text(t, row, C_NAME), name_vi=_text(t, row, C_NAME_VI), gender=_key_of(t, row, C_GENDER),
                         self_pronoun=" ".join((_key_of(t, row, C_PRONOUN) or _text(t, row, C_PRONOUN)).split()),
                         notes=notes.text() if notes is not None else "")

    def _other_characters(self, row: int) -> list[Character]:
        t = self.characters_table
        return [self._row_character(r) for r in range(t.rowCount()) if r != row and _text(t, r, C_NAME)]

    def _load_picker(self, row: int) -> None:
        """Show table row `row` in the pronoun picker (-1: none)."""
        t = self.characters_table
        if not 0 <= row < t.rowCount():
            self.pronoun_group.setTitle("Xưng hô")
            self.pronoun_picker.clear_speaker()
            return
        ch = self._row_character(row)
        draft = t.item(row, C_NOTES).data(_ROLE_DRAFT) if t.item(row, C_NOTES) is not None else None
        structure = draft.structure if isinstance(draft, _Draft) and draft.text == ch.notes else None
        self.pronoun_picker.load(ch, self._other_characters(row), structure)
        self._update_picker_title(row)

    def _update_picker_title(self, row: int) -> None:
        t = self.characters_table
        name, name_vi = _text(t, row, C_NAME), _text(t, row, C_NAME_VI)
        if not name and not name_vi:
            self.pronoun_group.setTitle(f"Xưng hô — nhân vật dòng {row + 1}")
        elif name_vi and name and name_vi != name:
            self.pronoun_group.setTitle(f"Xưng hô của {name_vi} ({name})")
        else:
            self.pronoun_group.setTitle(f"Xưng hô của {name_vi or name}")

    def _refresh_picker_context(self) -> None:
        """Names / genders / the list of characters changed: refresh the picker's choices and preview."""
        row = self.characters_table.currentRow()
        if row < 0:
            return
        self.pronoun_picker.set_context(self._row_character(row), self._other_characters(row))
        self._update_picker_title(row)

    def _on_current_character_changed(self, row: int, _col: int, prev_row: int, _prev_col: int) -> None:
        if row != prev_row:
            self._load_picker(row)

    def _on_character_item_changed(self, item: QTableWidgetItem) -> None:
        if self._syncing:
            return
        self._mark_dirty()
        t = self.characters_table
        if item.column() == C_NOTES:
            with _signals_blocked(t):
                item.setToolTip(item.text())
        renamed = self._rename_in_rules(item) if item.column() in (C_NAME, C_NAME_VI) else None
        if renamed and t.currentRow() in renamed[2]:
            self._load_picker(t.currentRow())  # the character shown names the renamed one: show the new notes
        elif item.row() != t.currentRow():
            self._refresh_picker_context()
        elif item.column() == C_NOTES:
            self._load_picker(item.row())  # typed by hand: read the notes back into the pickers
        elif item.column() == C_PRONOUN:
            self.pronoun_picker.set_self_pronoun(_key_of(t, item.row(), C_PRONOUN) or _text(t, item.row(), C_PRONOUN))
        else:
            self._refresh_picker_context()
        if renamed:
            old, new, rows = renamed
            self._set_status(f"Đã đổi “{old}” thành “{new}” trong cách xưng hô của {len(rows)} nhân vật khác.")

    def _rename_in_rules(self, item: QTableWidgetItem) -> tuple[str, str, set[int]] | None:
        """A character's name or Vietnamese name changed: the other characters' rules that named it by the
        old name ("gọi Ren là 'anh'") now name it by the new one, else address_pair would no longer pair
        them. (old, new, rows whose notes changed), or None when no notes changed."""
        t = self.characters_table
        row = item.row()
        old = " ".join(str(item.data(_ROLE_PREV) or "").split())
        new = _text(t, row, item.column())
        with _signals_blocked(t):
            item.setData(_ROLE_PREV, new)
        names = {n.casefold() for n in (_text(t, row, C_NAME), _text(t, row, C_NAME_VI)) if n}
        target = new or _text(t, row, C_NAME_VI if item.column() == C_NAME else C_NAME)
        if not old or old.casefold() in names or not target:
            return None
        changed: set[int] = set()
        with _signals_blocked(t):
            for r in range(t.rowCount()):
                notes_item = t.item(r, C_NOTES)
                if r == row or notes_item is None:
                    continue
                before = notes_item.text()
                after = rename_listener(before, old, target)
                draft = notes_item.data(_ROLE_DRAFT)
                if isinstance(draft, _Draft) and draft.text == before:  # rows being edited, incomplete ones too
                    rules = tuple(replace(rule, listener=target) if rule.listener.casefold() == old.casefold()
                                  else rule for rule in draft.structure.rules)
                    if rules != draft.structure.rules or after != before:
                        notes_item.setData(_ROLE_DRAFT, _Draft(after, replace(draft.structure, rules=rules)))
                        changed.add(r)
                if after != before:
                    notes_item.setText(after)
                    notes_item.setToolTip(after)
                    changed.add(r)
        if not changed:
            return None
        log.info("character renamed %r -> %r in %d other characters' notes", old, target, len(changed))
        return old, target, changed

    def _apply_char_filter(self, text: str) -> None:
        needle = normalize_source(text)
        t = self.characters_table
        first = -1
        for row in range(t.rowCount()):
            hay = " ".join(normalize_source(_text(t, row, c)) for c in (C_NAME, C_NAME_VI, C_NOTES))
            hidden = bool(needle) and needle not in hay
            t.setRowHidden(row, hidden)
            if not hidden and first < 0:
                first = row
        if first >= 0 and (t.currentRow() < 0 or t.isRowHidden(t.currentRow())):
            t.setCurrentCell(first, C_NAME)  # the "Xưng hô" panel shows a character the list shows

    def _on_picker_changed(self) -> None:
        """The user edited the pickers: write the notes and Tự xưng of the current character."""
        t = self.characters_table
        row = t.currentRow()
        if row < 0:
            return
        picker = self.pronoun_picker
        notes, pron = picker.notes(), picker.self_pronoun()
        self._syncing = True
        try:
            with _signals_blocked(t):
                item = t.item(row, C_NOTES)
                item.setText(notes)
                item.setToolTip(notes)
                item.setData(_ROLE_DRAFT, _Draft(notes, picker.structure()))
                p = t.item(row, C_PRONOUN)
                p.setText(pron)
                p.setData(_ROLE_KEY, pron)
        finally:
            self._syncing = False
        self._mark_dirty()

    # --- processes ---------------------------------------------------------------------------

    def processes(self) -> list[str]:
        return [self.process_list.item(i).text() for i in range(self.process_list.count())]

    def add_processes(self, names, *, mark_dirty: bool = True) -> int:
        have = {p.lower() for p in self.processes()}
        added = 0
        for name in names:
            exe = _clean_exe(name)
            if exe and exe.lower() not in have:
                have.add(exe.lower())
                self.process_list.addItem(exe)
                added += 1
        if added and mark_dirty:
            self._mark_dirty()
        return added

    def remove_selected_processes(self) -> None:
        rows = sorted((self.process_list.row(i) for i in self.process_list.selectedItems()), reverse=True)
        for row in rows:
            self.process_list.takeItem(row)
        if rows:
            self._mark_dirty()

    def pick_windows(self) -> None:
        picker = WindowPicker(self.processes(), self)
        if picker.exec() == QDialog.DialogCode.Accepted:
            self.add_processes(picker.selected())
        picker.deleteLater()

    def _add_typed_process(self) -> None:
        if self.add_processes([self.process_edit.text()]):
            self.process_edit.clear()

    # --- validation and saving ---------------------------------------------------------------

    def collect(self) -> tuple[Profile, list[str]]:
        """The edited profile and the validation errors (empty: OK to save). Rows without a source
        are dropped; offending rows are highlighted."""
        errors: list[str] = []
        chars: list[Character] = []
        char_rows: dict[str, int] = {}
        bad_chars: set[int] = set()
        ct = self.characters_table
        loaded_self = {normalize_source(c.name): " ".join(c.self_pronoun.split()) for c in self._loaded.characters}
        for row in range(ct.rowCount()):
            name = _text(ct, row, C_NAME)
            norm = normalize_source(name)
            if not norm:
                notes_item = ct.item(row, C_NOTES)
                draft = notes_item.data(_ROLE_DRAFT) if notes_item is not None else None
                filled = [_text(ct, row, c) for c in (C_NAME_VI, C_PRONOUN, C_NOTES)]
                if any(filled) or (isinstance(draft, _Draft) and any(r.listener for r in draft.structure.rules)):
                    # a character with only a Vietnamese name would be dropped silently, with its pronoun rows
                    bad_chars.add(row)
                    shown = f" ({filled[0]})" if filled[0] else ""
                    errors.append(f"Nhân vật dòng {row + 1}{shown} chưa có “Tên gốc” — ghi đúng như chữ trong game, "
                                  "ví dụ セラ.")
                continue
            if norm in char_rows:
                bad_chars.update((row, char_rows[norm]))
                errors.append(f"Nhân vật “{name}” bị trùng (dòng {char_rows[norm] + 1} và {row + 1}).")
                continue
            char_rows[norm] = row
            ch = Character(name=name, name_vi=_text(ct, row, C_NAME_VI), gender=_key_of(ct, row, C_GENDER),
                           self_pronoun=" ".join((_key_of(ct, row, C_PRONOUN) or _text(ct, row, C_PRONOUN)).split()),
                           notes=_text(ct, row, C_NOTES))
            chars.append(ch)
            # the pickers' rows: a listener without a word, a word that is not one word, ... cannot be saved;
            # a Tự xưng only when it was edited here (an old profile's "em/tớ" does not block other edits)
            problems = [self_problem(ch.self_pronoun) if loaded_self.get(norm) != ch.self_pronoun else None]
            notes_item = ct.item(row, C_NOTES)
            draft = notes_item.data(_ROLE_DRAFT) if notes_item is not None else None
            if isinstance(draft, _Draft) and draft.text == notes_item.text():
                problems += [f"dòng xưng hô {r + 1}: {m}" for r, m in rule_problems(draft.structure, ch)]
            problems = [p for p in problems if p]
            if problems:
                bad_chars.add(row)
                errors.append(f"Nhân vật “{display_name(ch)}”: " + "; ".join(problems) + ".")
        with _signals_blocked(ct):
            for row in range(ct.rowCount()):
                for col in range(ct.columnCount()):
                    item = ct.item(row, col)
                    if item is not None:
                        item.setData(Qt.ItemDataRole.BackgroundRole, _ERROR_BG if row in bad_chars else None)
                        item.setData(Qt.ItemDataRole.ForegroundRole, _ERROR_FG if row in bad_chars else None)

        terms: list[Term] = []
        term_rows: dict[str, int] = {}
        bad_terms: set[int] = set()
        tt = self.terms_table
        for row in range(tt.rowCount()):
            source = _text(tt, row, T_SOURCE)
            norm = normalize_source(source)
            if not norm:
                continue
            auto = self.is_auto(row)
            if auto and norm in char_rows:
                continue  # a suggestion that duplicates a character: the character wins
            if norm in term_rows:
                bad_terms.update((row, term_rows[norm]))
                errors.append(f"Thuật ngữ “{source}” bị trùng (dòng {term_rows[norm] + 1} và {row + 1}).")
                continue
            term_rows[norm] = row
            target = _text(tt, row, T_TARGET)
            if not target:
                bad_terms.add(row)
                errors.append(f"Thuật ngữ “{source}” (dòng {row + 1}) chưa có bản dịch tiếng Việt.")
                continue
            terms.append(Term(source=source, target=target, kind=_key_of(tt, row, T_KIND) or "term",
                              note=_text(tt, row, T_NOTE), auto=auto))
        for row in range(tt.rowCount()):
            self._style_term_row(row, error=row in bad_terms)

        present = set(term_rows) | set(char_rows)
        rejected = [r for r in dict.fromkeys(self._rejected) if r not in present][-REJECTED_CAP:]
        profile = Profile(name=self._name, processes=self.processes(), src_lang=self.lang_combo.currentData(),
                          style=self.style_edit.toPlainText().strip(), terms=terms, characters=chars,
                          rejected=rejected)
        if bad_terms:
            self.tabs.setCurrentIndex(0)
            tt.scrollToItem(tt.item(min(bad_terms), T_SOURCE))
        elif bad_chars:
            self.tabs.setCurrentIndex(1)
            if self.char_filter_edit.text():
                self.char_filter_edit.clear()  # the rows to fix must be visible
            if ct.currentRow() not in bad_chars:
                ct.setCurrentCell(min(bad_chars), C_NAME)  # the pickers show what is wrong
        return profile, errors

    def save(self) -> bool:
        """Validate and write; False (with the reason in the status line) when nothing was saved."""
        profile, errors = self.collect()
        if errors:
            more = f" (và {len(errors) - 3} lỗi khác)" if len(errors) > 3 else ""
            self._set_status(" ".join(errors[:3]) + more, error=True)
            return False
        profile = self._merge_concurrent(profile)
        try:
            self._store.save(profile)
        except (OSError, ValueError) as e:
            log.exception("saving profile %s failed", self._name)
            self._set_status(f"Không lưu được: {e}", error=True)
            return False
        self._saved = profile
        self._loaded_sources = {normalize_source(t.source) for t in profile.terms}
        self._rejected = list(profile.rejected)
        self._dirty = False
        self._set_status("Đã lưu.")
        self.saved.emit(profile.name)
        return True

    def saved_profile(self) -> Profile | None:
        return self._saved

    def exclusion(self) -> CaptureExclusion:
        return self._exclusion

    def popup_exclusions(self) -> int:
        """How many popup windows were capture-excluded by the app-wide filter (tests)."""
        return self._popups.count

    def is_dirty(self) -> bool:
        return self._dirty

    def _merge_concurrent(self, profile: Profile) -> Profile:
        """Keep auto terms the LLM added to the store while the editor was open."""
        current = self._store.get(self._name)
        if current.name != self._name:
            return profile
        have = {normalize_source(t.source) for t in profile.terms}
        have |= {normalize_source(c.name) for c in profile.characters} | set(profile.rejected)
        extra = [t for t in current.terms if t.auto and normalize_source(t.source) not in self._loaded_sources
                 and normalize_source(t.source) not in have]
        if extra:
            profile.terms = [*profile.terms, *extra]
        return profile

    # --- misc --------------------------------------------------------------------------------

    def _on_save_clicked(self) -> None:
        if self.save():
            self.accept()

    def showEvent(self, event) -> None:
        self._popups.install()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._popups.remove()

    def reject(self) -> None:
        if self._dirty and self.ask_before_discard and not ask_discard(self):
            return
        super().reject()

    def _on_changed(self, *_args) -> None:
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._update_summary()

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setStyleSheet("color: #b71c1c;" if error else "")
        self.status_label.setText(text)

    def _update_summary(self) -> None:
        n = self.terms_table.rowCount()
        auto = sum(1 for r in range(n) if self.is_auto(r))
        chars = self.characters_table.rowCount()
        parts = [f"{n} thuật ngữ"]
        if auto:
            parts.append(f"{auto} gợi ý chưa xác nhận")
        parts.append(f"{chars} nhân vật")
        if self._dirty:
            parts.append("chưa lưu")
        self.confirm_all_button.setEnabled(auto > 0)
        self._set_status(" · ".join(parts))

    @staticmethod
    def _selected_rows(table: QTableWidget) -> list[int]:
        return sorted({i.row() for i in table.selectedIndexes()})


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
