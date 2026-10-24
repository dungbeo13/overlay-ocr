"""First-run guide: six short steps in one small, capture-excluded dialog.

Shown once by itself (Config.first_run_done is set when the user finishes or skips it, not when the app
quits with the guide still open), and again from the tray ("Hướng dẫn nhanh…") or the Settings window. Every
step can be skipped; Enter always means "Tiếp" (only the step buttons take it when they have the focus). The guide changes nothing itself: picking a language or profile, creating one,
selecting the region and opening Settings are signals the App carries out on the running app, and the
App reports back (region chosen, profiles, LLM status) so the steps show the real state.
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import fields
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.config import PROJECT_ROOT, Config, Hotkeys
from app.overlay.affinity import CaptureExclusion, apply_to_widget
from app.ui.glossary_editor import _PopupExcluder, _signals_blocked
from app.ui.hotkeys import format_combo

STEP_TITLES = ("Chào bạn!", "Game của bạn dùng tiếng gì?", "Hồ sơ cho từng game", "Chọn vùng dịch",
               "Phím tắt", "Dịch bằng LLM (tùy chọn)")
HOTKEY_TEXT = {
    "select_region": "Chọn vùng dịch",
    "toggle_pause": "Tạm dừng / tiếp tục",
    "toggle_overlay": "Ẩn / hiện bản dịch (để xem chữ gốc)",
    "switch_lang": "Đổi ngôn ngữ nguồn Nhật ↔ Anh",
    "quit": "Thoát",
}
DEFAULT_PROFILE = "default"
DEFAULT_PROFILE_LABEL = "Mặc định"

WELCOME = (
    "<p><b>Screen Translator</b> đọc chữ trong một vùng màn hình bạn chọn (khung thoại visual novel, phụ đề, "
    "Blue Archive…) rồi hiện bản dịch tiếng Việt đè lên đúng chỗ đó. Bản dịch và các cửa sổ của app không hiện "
    "trong ảnh chụp màn hình, video quay hay khi stream — chỉ bạn thấy.</p>"
    "<p><b>Anti-cheat:</b> app chỉ chụp màn hình như phần mềm quay video, không đọc bộ nhớ và không can thiệp vào "
    "game. Khi bạn đang chơi một game có anti-cheat trong danh sách (Valorant, Genshin…), app tự tạm dừng. Danh "
    "sách sửa trong Cài đặt → Anti-cheat.</p>"
    "<p>App chạy ẩn dưới khay hệ thống, biểu tượng <b>文</b> ở góc phải thanh tác vụ (có thể nằm trong mũi tên ^). "
    "Bấm vào biểu tượng đó để mở menu với mọi lệnh.</p>")
LANG_TEXT = ("Chọn ngôn ngữ gốc của game. Ở chế độ tiếng Nhật, đoạn chỉ có chữ Latin (menu, phụ đề tiếng Anh) vẫn "
             "được dịch như tiếng Anh. Đổi lại bất cứ lúc nào bằng phím tắt hoặc menu khay.")
PROFILE_TEXT = ("Hồ sơ giữ tên nhân vật, thuật ngữ và cách xưng hô (anh/em, cậu/tớ, Sensei…) của một game, để LLM dịch "
                "tên riêng cố định và xưng hô đúng vai. Mỗi game nên có một hồ sơ.<br><br>Hồ sơ <b>tự bật</b> khi "
                "bạn mở game đó, nếu tên tệp .exe của game nằm trong hồ sơ (Sửa thuật ngữ / nhân vật… → thẻ “Văn "
                "phong & game”).")
REGION_TEXT = ("Mở game và để khung thoại hiện trên màn hình, rồi bấm nút dưới đây và kéo chuột quanh khung chữ "
               "(khung thoại, dải phụ đề). Vùng nhỏ thì dịch nhanh và ít nhầm hơn. Esc để hủy. Chưa mở game thì bỏ "
               "qua bước này và chọn sau bằng phím tắt “Chọn vùng dịch”. Vùng được lưu lại.")
TRAY_TEXT = "Muốn đổi phím, mở Cài đặt → Phím tắt. Phím tắt dùng được cả khi đang ở trong game."
LLM_INTRO = ("Không có LLM, app dịch bằng Google: nhanh nhưng hay sai tên riêng và xưng hô. Với LLM chạy trên GPU của "
             "bạn, bản Google hiện trước rồi được thay bằng bản dịch giữ đúng tên và xưng hô.")


def should_show(first_run_done: bool, *, automated: bool = False, forced: bool = False) -> bool:
    """Show the guide at startup: forced (--first-run), else once, never in an automated run (--exit-after)."""
    return forced or (not first_run_done and not automated)


def python_console_exe() -> Path:
    """python.exe of this venv (the app may run under pythonw.exe), for the commands the guide shows."""
    exe = Path(sys.executable)
    cand = exe.with_name("python.exe")
    return cand if cand.is_file() else exe


def setup_command() -> str:
    return f'cd "{PROJECT_ROOT}"; & "{python_console_exe()}" -m tools.setup_llm'


def llm_summary(llm_mode: str, status: str, *, models: Sequence[str] | None = None,
                server_found: bool | None = None) -> tuple[str, str]:
    """(state, Vietnamese text) for the LLM step: state is "off", "missing", "loading", "ready", "error"
    or "installed" (model on disk, loaded when text needs it)."""
    if models is None or server_found is None:
        from app.llm.models import find_llama_server, list_models

        models = [p.name for p in list_models()] if models is None else models
        server_found = find_llama_server() is not None if server_found is None else server_found
    status = " ".join((status or "").split())
    if llm_mode == "off":
        return "off", "Đang tắt. Bật ở Cài đặt → Dịch, hoặc menu khay → Dịch bằng LLM."
    if not models or not server_found:
        what = "model và llama.cpp" if not models and not server_found else "model" if not models else "llama.cpp"
        return "missing", (f"Chưa cài {what}: app đang dịch bằng Google. Cài một lần (tải vài GB) bằng lệnh dưới đây "
                           "trong PowerShell, xong mở lại app:")
    from app.llm.models import model_label

    names = ", ".join(dict.fromkeys(model_label(m) for m in models))
    low = status.lower()
    if "sẵn sàng" in low:
        ready = status.removeprefix("LLM: ")
        return "ready", f"{ready[:1].upper()}{ready[1:]}. Bản dịch LLM thay bản Google sau khoảng nửa giây."
    if "đang tải" in low:
        return "loading", "Đang tải model vào GPU (vài giây)… Trong lúc chờ, app dịch bằng Google."
    if "lỗi" in low or "chưa có" in low:
        return "error", f"{status}. Xem nhật ký trong thư mục dữ liệu; cài lại bằng lệnh dưới đây."
    return "installed", f"Đã cài ({names}). Model được tải lên GPU khi cần và tự giải phóng VRAM khi không dùng."


def _body(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    return label


class FirstRunGuide(QDialog):
    """See the module docstring. Non-modal; `dismissed(completed)` when the user closes it."""

    set_lang = Signal(str)
    set_profile = Signal(str)
    new_profile = Signal()
    edit_glossary = Signal()
    select_region = Signal()
    open_settings = Signal()
    dismissed = Signal(bool)  # True: went through to "Xong"; False: skipped or closed

    def __init__(self, config: Config, *, profiles: Sequence[str] = (), active_profile: str = DEFAULT_PROFILE,
                 llm_status: str = "", llm_models: Sequence[str] | None = None, llm_server: bool | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._llm_mode = config.llm_mode
        self._llm_models = llm_models
        self._llm_server = llm_server
        self._quiet = False  # closing without dismissed() (the app quits)
        self._selecting = False
        self.setWindowTitle("Hướng dẫn nhanh — Screen Translator")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(560, 430)

        root = QVBoxLayout(self)
        self.step_label = QLabel()
        self.step_label.setEnabled(False)
        root.addWidget(self.step_label)
        self.title_label = QLabel()
        tf = QFont(self.title_label.font())
        tf.setPointSizeF(tf.pointSizeF() + 4)
        tf.setWeight(QFont.Weight.DemiBold)
        self.title_label.setFont(tf)
        root.addWidget(self.title_label)
        self.pages = QStackedWidget()
        for build in (self._build_welcome, self._build_lang, self._build_profile, self._build_region,
                      self._build_hotkeys, self._build_llm):
            self.pages.addWidget(build())
        root.addWidget(self.pages, 1)

        nav = QHBoxLayout()
        self.skip_button = QPushButton("Bỏ qua hướng dẫn")
        self.skip_button.setToolTip("Đóng hướng dẫn. Mở lại bất cứ lúc nào: menu khay → Hướng dẫn nhanh…")
        self.skip_button.clicked.connect(self.skip)
        nav.addWidget(self.skip_button)
        nav.addStretch(1)
        self.back_button = QPushButton("Quay lại")
        self.back_button.clicked.connect(self.back)
        self.next_button = QPushButton("Tiếp")
        self.next_button.clicked.connect(self.next)
        nav.addWidget(self.back_button)
        nav.addWidget(self.next_button)
        root.addLayout(nav)

        self.set_lang_checked(config.src_lang)
        self.set_profiles(list(profiles) or [active_profile], active_profile)
        self.set_region(config.region)
        self.set_hotkeys(config.hotkeys)
        self.set_llm_status(llm_status)
        # Enter means "Tiếp" wherever the focus is (a dialog's focused push button would take it otherwise:
        # Enter on the first screen skipped the whole guide): only the step buttons take Enter when focused.
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
        self.next_button.setDefault(True)
        self.go(0)
        self._popups = _PopupExcluder(self)
        self._exclusion: CaptureExclusion = apply_to_widget(self)

    # --- pages ---------------------------------------------------------------------------------

    def _build_welcome(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(_body(WELCOME))
        lay.addStretch(1)
        return page

    def _build_lang(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.lang_group = QButtonGroup(page)
        self.lang_buttons: dict[str, QRadioButton] = {}
        for code, label in (("ja", "Tiếng Nhật (visual novel, Blue Archive bản Nhật…)"), ("en", "Tiếng Anh")):
            b = QRadioButton(label)
            b.toggled.connect(lambda on, c=code: on and self.set_lang.emit(c))
            self.lang_group.addButton(b)
            self.lang_buttons[code] = b
            lay.addWidget(b)
        lay.addSpacing(8)
        lay.addWidget(_body(LANG_TEXT))
        lay.addStretch(1)
        return page

    def _build_profile(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(_body(PROFILE_TEXT))
        lay.addSpacing(6)
        row = QHBoxLayout()
        row.addWidget(QLabel("Hồ sơ đang dùng:"))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(200)
        self.profile_combo.activated.connect(self._on_profile_chosen)
        row.addWidget(self.profile_combo, 1)
        lay.addLayout(row)
        buttons = QHBoxLayout()
        self.new_profile_button = QPushButton("Tạo hồ sơ mới…")
        self.new_profile_button.clicked.connect(self.new_profile)
        self.edit_profile_button = QPushButton("Sửa thuật ngữ / nhân vật…")
        self.edit_profile_button.clicked.connect(self.edit_glossary)
        buttons.addWidget(self.new_profile_button)
        buttons.addWidget(self.edit_profile_button)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        lay.addStretch(1)
        return page

    def _build_region(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(_body(REGION_TEXT))
        lay.addSpacing(6)
        row = QHBoxLayout()
        self.region_button = QPushButton("Chọn vùng ngay")
        self.region_button.clicked.connect(self._on_select_region)
        row.addWidget(self.region_button)
        row.addStretch(1)
        lay.addLayout(row)
        self.region_label = QLabel()
        self.region_label.setWordWrap(True)
        lay.addWidget(self.region_label)
        lay.addStretch(1)
        return page

    def _build_hotkeys(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(16)
        self.hotkey_labels: dict[str, QLabel] = {}
        for row, f in enumerate(fields(Hotkeys)):
            key = QLabel()
            kf = QFont(key.font())
            kf.setWeight(QFont.Weight.DemiBold)
            key.setFont(kf)
            grid.addWidget(key, row, 0)
            grid.addWidget(QLabel(HOTKEY_TEXT.get(f.name, f.name)), row, 1)
            self.hotkey_labels[f.name] = key
        lay.addLayout(grid)
        lay.addSpacing(8)
        lay.addWidget(_body(TRAY_TEXT))
        row = QHBoxLayout()
        self.settings_button = QPushButton("Mở Cài đặt…")
        self.settings_button.clicked.connect(self.open_settings)
        row.addWidget(self.settings_button)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        return page

    def _build_llm(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(_body(LLM_INTRO))
        lay.addSpacing(6)
        self.llm_label = QLabel()
        self.llm_label.setWordWrap(True)
        lay.addWidget(self.llm_label)
        self.command_label = QLabel(setup_command())
        self.command_label.setWordWrap(True)
        self.command_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.command_label.setStyleSheet("QLabel { font-family: Consolas, 'Cascadia Mono', monospace; "
                                         "background: rgba(127, 127, 127, 40); padding: 6px; border-radius: 4px; }")
        lay.addWidget(self.command_label)
        row = QHBoxLayout()
        self.copy_button = QPushButton("Sao chép lệnh")
        self.copy_button.clicked.connect(self._copy_command)
        row.addWidget(self.copy_button)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        return page

    # --- navigation ------------------------------------------------------------------------------

    def step(self) -> int:
        return self.pages.currentIndex()

    def step_count(self) -> int:
        return self.pages.count()

    def go(self, index: int) -> None:
        index = max(0, min(self.pages.count() - 1, index))
        self.pages.setCurrentIndex(index)
        self.step_label.setText(f"Bước {index + 1}/{self.pages.count()}")
        self.title_label.setText(STEP_TITLES[index])
        self.back_button.setEnabled(index > 0)
        last = index == self.pages.count() - 1
        self.next_button.setText("Xong" if last else "Tiếp")
        self.skip_button.setVisible(not last)
        self.next_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def next(self) -> None:
        if self.step() >= self.pages.count() - 1:
            self._dismiss(True)
            self.accept()
        else:
            self.go(self.step() + 1)

    def back(self) -> None:
        self.go(self.step() - 1)

    def skip(self) -> None:
        self._dismiss(False)
        self.reject()

    def close_quietly(self) -> None:
        """Close without counting it as seen (the app quits while the guide is open)."""
        self._quiet = True
        self.close()

    # --- state from the app ----------------------------------------------------------------------

    def set_lang_checked(self, lang: str) -> None:
        b = self.lang_buttons.get(lang)
        if b is not None and not b.isChecked():
            with _signals_blocked(b):
                b.setChecked(True)

    def set_profiles(self, names: Sequence[str], active: str) -> None:
        names = list(dict.fromkeys(n for n in names if n))
        if active and active not in names:
            names.append(active)
        with _signals_blocked(self.profile_combo):
            self.profile_combo.clear()
            for name in names:
                self.profile_combo.addItem(DEFAULT_PROFILE_LABEL if name.casefold() == DEFAULT_PROFILE else name, name)
            self.profile_combo.setCurrentIndex(max(0, self.profile_combo.findData(active)))

    def set_region(self, region) -> None:
        if region and len(region) == 4 and region[2] > 0 and region[3] > 0:
            _left, _top, w, h = (int(v) for v in region)
            self.region_label.setText(f"✓ Đã chọn vùng ({w} × {h} px). Bấm “Tiếp”, hoặc chọn lại.")
            self.region_button.setText("Chọn lại vùng")
        else:
            self.region_label.setText("Chưa chọn vùng: app chưa dịch gì cho tới khi bạn chọn. Có thể bỏ qua và "
                                      "chọn sau.")
            self.region_button.setText("Chọn vùng ngay")

    def region_finished(self, region) -> None:
        """The region selector closed (`region` [l, t, w, h], or None when cancelled): show the guide again."""
        if region is not None:
            self.set_region(region)
        if self._selecting:
            self._selecting = False
            self.show()
            self.raise_()
            self.activateWindow()
            self.next_button.setFocus(Qt.FocusReason.OtherFocusReason)  # Enter: on to the next step

    def set_hotkeys(self, hotkeys: Hotkeys) -> None:
        for name, label in self.hotkey_labels.items():
            combo = getattr(hotkeys, name, "")
            label.setText(format_combo(combo) if combo else "(không có)")

    def set_llm_status(self, status: str) -> None:
        state, text = llm_summary(self._llm_mode, status, models=self._llm_models, server_found=self._llm_server)
        self.llm_state = state
        self.llm_label.setText(text)
        show_cmd = state in ("missing", "error")
        self.command_label.setVisible(show_cmd)
        self.copy_button.setVisible(show_cmd)

    def set_llm_mode(self, mode: str) -> None:
        self._llm_mode = mode

    def exclusion(self) -> CaptureExclusion:
        return self._exclusion

    # --- internals -------------------------------------------------------------------------------

    def _on_profile_chosen(self, index: int) -> None:
        name = self.profile_combo.itemData(index)
        if name:
            self.set_profile.emit(str(name))

    def _on_select_region(self) -> None:
        self._selecting = True
        self.hide()  # out of the way of the dimmed screen
        self.select_region.emit()

    def _copy_command(self) -> None:
        QGuiApplication.clipboard().setText(setup_command())
        self.copy_button.setText("Đã sao chép")

    def _dismiss(self, completed: bool) -> None:
        if not self._quiet:
            self._quiet = True  # once
            self.dismissed.emit(completed)

    def showEvent(self, event) -> None:
        self._popups.install()
        super().showEvent(event)
        self.next_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._popups.remove()

    def reject(self) -> None:
        self._dismiss(False)  # Esc / the title bar's close button count as "skip"
        super().reject()

    def closeEvent(self, event) -> None:
        self._dismiss(False)
        super().closeEvent(event)
