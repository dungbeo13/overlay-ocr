"""System tray icon and menu (Vietnamese labels, hotkeys shown next to the actions).

A left click on the icon opens the menu too (after the double-click interval: a double click selects the
region instead), like the right click Windows users expect from other tray apps."""
from __future__ import annotations

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QCursor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from app import __version__
from app.config import Config, Hotkeys
from app.llm.models import model_kind
from app.overlay.affinity import apply_to_widget
from app.overlay.render import lang_pair, status_text
from app.pipeline.types import OverlayState

from .hotkeys import format_combo

APP_NAME = "Screen Translator"
APP_TITLE = f"{APP_NAME} {__version__}"  # the tooltip's first line, and the status line until the first state
LANG_LABELS = {"ja": "Tiếng Nhật", "en": "Tiếng Anh"}
GUARD_LABELS = {"pause": "Tạm dừng", "warn": "Chỉ cảnh báo", "off": "Tắt"}
LLM_LABELS = {"off": "Tắt", "refine": "Google trước, LLM tinh chỉnh", "only": "Chỉ LLM"}
LLM_SHORT = {"off": "tắt", "refine": "tinh chỉnh", "only": "chỉ LLM"}
MODEL_LABELS = {"auto": "Mô hình: tự động (7B nếu đủ VRAM)", "light": "Mô hình: Nhẹ 1.8B (~2 GB VRAM)",
                "quality": "Mô hình: Chất lượng 7B (~5,3 GB VRAM)"}
DEFAULT_PROFILE = "default"
DEFAULT_PROFILE_LABEL = "Mặc định"
LOOKUP_MENU = "Tra cứu game"
_NAME_MAX = 40  # profile names longer than this are elided in the menu
_LLM_STATUS_MAX = 70
_ICON_COLORS = {
    "running": QColor(25, 118, 210),
    "idle": QColor(84, 110, 122),
    "paused": QColor(97, 97, 97),
    "guarded": QColor(239, 108, 0),
    "error": QColor(211, 47, 47),
}
_TOOLTIP_MAX = 120  # Windows truncates tray tooltips at 127 characters


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64)


def icon_pixmap(size: int, status: str = "running") -> QPixmap:
    """One size of the tray icon: coloured rounded square, white 文 and (from 24 px) a "VI" badge.
    Also what tools/install_shortcut.py writes into the shortcuts' .ico (up to 256 px)."""
    color = _ICON_COLORS.get(status, _ICON_COLORS["idle"])
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), size * 0.22, size * 0.22)
    glyph = QFont("Yu Gothic UI")
    glyph.setPixelSize(max(8, round(size * (0.78 if size < 24 else 0.62))))
    glyph.setWeight(QFont.Weight.Bold)
    p.setFont(glyph)
    p.setPen(QColor(255, 255, 255))
    if size < 24:
        p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "文")
    else:
        p.drawText(QRectF(0, 0, size * 0.72, size * 0.72), Qt.AlignmentFlag.AlignCenter, "文")
        badge = QFont("Segoe UI")
        badge.setPixelSize(round(size * 0.34))
        badge.setWeight(QFont.Weight.Black)
        p.setFont(badge)
        p.drawText(QRectF(size * 0.38, size * 0.54, size * 0.6, size * 0.44), Qt.AlignmentFlag.AlignCenter, "VI")
    p.end()
    return pm


def make_icon(status: str = "running") -> QIcon:
    """Tray icon drawn with QPainter (icon_pixmap at every tray size)."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(icon_pixmap(size, status))
    return icon


class TrayController(QObject):
    """Tray icon + context menu. Emits intent signals; the app does the work and calls update_*."""

    select_region = Signal()
    toggle_pause = Signal()
    toggle_overlay = Signal()
    set_lang = Signal(str)  # "ja" | "en"
    set_guard_mode = Signal(str)  # "pause" | "warn" | "off"
    open_data_dir = Signal()
    quit = Signal()
    set_profile = Signal(str)  # profile display name
    new_profile = Signal()
    edit_glossary = Signal()
    set_llm_mode = Signal(str)  # "off" | "refine" | "only"
    set_llm_model = Signal(str)  # "auto" | "light" | "quality"
    open_settings = Signal()
    open_guide = Signal()
    lookup_game = Signal(str)  # key of a new game found in front (set_pending_games), "" = "Tra cứu game…"
    lookup_request = Signal()  # "Tạo yêu cầu tra cứu cho Claude…"
    import_profile = Signal(str)  # "file" | "clipboard"
    message_clicked = Signal(str)  # the user clicked the last balloon: the `token` it was shown with

    def __init__(self, config: Config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._hotkeys = config.hotkeys
        self._icons = {name: make_icon(name) for name in _ICON_COLORS}
        self._paused_by_user = False
        self._text_visible = True

        self._menu = QMenu()
        self._status_action = QAction(APP_TITLE, self._menu)
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)
        self._menu.addSeparator()

        self._select_action = QAction(self._menu)
        self._select_action.triggered.connect(self.select_region)
        self._pause_action = QAction(self._menu)
        self._pause_action.triggered.connect(self.toggle_pause)
        self._overlay_action = QAction(self._menu)
        self._overlay_action.triggered.connect(self.toggle_overlay)
        for a in (self._select_action, self._pause_action, self._overlay_action):
            self._menu.addAction(a)

        self._lang_menu = self._menu.addMenu("Ngôn ngữ nguồn")
        self._lang_group = QActionGroup(self._menu)
        self._lang_actions: dict[str, QAction] = {}
        for code, label in LANG_LABELS.items():
            a = QAction(label, self._lang_menu, checkable=True)
            a.triggered.connect(lambda _checked=False, c=code: self.set_lang.emit(c))
            self._lang_group.addAction(a)
            self._lang_menu.addAction(a)
            self._lang_actions[code] = a

        # Glossary profile: the checkable list is rebuilt by set_profiles() above the separator.
        self._active_profile = config.profile
        self._profile_names: list[str] = []
        self._profile_menu = self._menu.addMenu("Hồ sơ")
        self._profile_group = QActionGroup(self._menu)
        self._profile_actions: dict[str, QAction] = {}
        self._profile_sep = self._profile_menu.addSeparator()
        self._new_profile_action = QAction("Tạo hồ sơ mới…", self._profile_menu)
        self._new_profile_action.triggered.connect(self.new_profile)
        self._edit_glossary_action = QAction("Sửa thuật ngữ / nhân vật…", self._profile_menu)
        self._edit_glossary_action.triggered.connect(self.edit_glossary)
        self._profile_menu.addAction(self._new_profile_action)
        self._profile_menu.addAction(self._edit_glossary_action)

        # Game lookup (app.lookup): new games found in front are listed above the fixed entries.
        self._pending_games: list[tuple[str, str]] = []
        self._pending_actions: list[QAction] = []
        self._lookup_menu = self._menu.addMenu(LOOKUP_MENU)
        self._lookup_sep = self._lookup_menu.addSeparator()
        self._lookup_action = QAction("Tra cứu game…", self._lookup_menu)
        self._lookup_action.setToolTip("Tra tên nhân vật, thuật ngữ và bản Việt hóa của game đang chơi hoặc một tên "
                                       "game bạn gõ (hỏi trước khi gửi tên game lên mạng)")
        self._lookup_action.triggered.connect(lambda: self.lookup_game.emit(""))
        self._request_action = QAction("Tạo yêu cầu tra cứu cho Claude…", self._lookup_menu)
        self._request_action.triggered.connect(self.lookup_request)
        self._import_file_action = QAction("Nhập hồ sơ từ file…", self._lookup_menu)
        self._import_file_action.triggered.connect(lambda: self.import_profile.emit("file"))
        self._import_clip_action = QAction("Dán hồ sơ từ clipboard…", self._lookup_menu)
        self._import_clip_action.triggered.connect(lambda: self.import_profile.emit("clipboard"))
        for a in (self._lookup_action, self._request_action, self._import_file_action, self._import_clip_action):
            self._lookup_menu.addAction(a)
        self._lookup_sep.setVisible(False)

        self._llm_menu = self._menu.addMenu("Dịch bằng LLM")
        self._llm_group = QActionGroup(self._menu)
        self._llm_actions: dict[str, QAction] = {}
        for mode, label in LLM_LABELS.items():
            a = QAction(label, self._llm_menu, checkable=True)
            a.triggered.connect(lambda _checked=False, m=mode: self.set_llm_mode.emit(m))
            self._llm_group.addAction(a)
            self._llm_menu.addAction(a)
            self._llm_actions[mode] = a
        self._llm_menu.addSeparator()
        self._model_group = QActionGroup(self._menu)
        self._model_actions: dict[str, QAction] = {}
        for kind, label in MODEL_LABELS.items():
            a = QAction(label, self._llm_menu, checkable=True)
            a.triggered.connect(lambda _checked=False, k=kind: self.set_llm_model.emit(k))
            self._model_group.addAction(a)
            self._llm_menu.addAction(a)
            self._model_actions[kind] = a
        self._llm_status_action = QAction(self._menu)
        self._llm_status_action.setEnabled(False)
        self._menu.addAction(self._llm_status_action)

        self._guard_menu = self._menu.addMenu("Chế độ anti-cheat")
        self._guard_group = QActionGroup(self._menu)
        self._guard_actions: dict[str, QAction] = {}
        for mode, label in GUARD_LABELS.items():
            a = QAction(label, self._guard_menu, checkable=True)
            a.triggered.connect(lambda _checked=False, m=mode: self.set_guard_mode.emit(m))
            self._guard_group.addAction(a)
            self._guard_menu.addAction(a)
            self._guard_actions[mode] = a

        self._menu.addSeparator()
        self._settings_action = QAction("Cài đặt…", self._menu)
        self._settings_action.triggered.connect(self.open_settings)
        self._menu.addAction(self._settings_action)
        self._guide_action = QAction("Hướng dẫn nhanh…", self._menu)
        self._guide_action.triggered.connect(self.open_guide)
        self._menu.addAction(self._guide_action)
        self._data_action = QAction("Mở thư mục dữ liệu", self._menu)
        self._data_action.triggered.connect(self.open_data_dir)
        self._menu.addAction(self._data_action)
        self._quit_action = QAction(self._menu)
        self._quit_action.triggered.connect(self.quit)
        self._menu.addAction(self._quit_action)
        # The menus are our own top-level windows: if one opens over the capture region, its
        # Vietnamese labels must not be OCR'd (and sent to the translator).
        self._exclusions = [apply_to_widget(m) for m in (self._menu, self._lang_menu, self._guard_menu)]
        self._more_exclusions = [apply_to_widget(m) for m in (self._profile_menu, self._llm_menu)]
        self._lookup_exclusion = apply_to_widget(self._lookup_menu)

        self._tray = QSystemTrayIcon(self._icons["idle"], self)
        self._tray.setContextMenu(self._menu)
        self._tray.setToolTip(APP_TITLE)
        self._tray.activated.connect(self._on_activated)
        self._message_token = ""
        self._tray.messageClicked.connect(lambda: self.message_clicked.emit(self._message_token))
        self._click_timer = QTimer(self)  # a single left click opens the menu, unless a double click follows
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self.popup_menu)

        self.set_hotkeys(config.hotkeys)
        self.set_lang_checked(config.src_lang)
        self.set_guard_checked(config.guard_mode)
        self.set_profiles([config.profile], config.profile)
        self.set_llm_mode_checked(config.llm_mode)
        self.set_llm_model_checked(config.llm_model)
        self.set_llm_status("LLM: tắt" if config.llm_mode == "off" else "LLM: chưa chạy")

    # --- public API ------------------------------------------------------------------------

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    def is_available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def menu(self) -> QMenu:
        return self._menu

    def tray_icon(self) -> QSystemTrayIcon:
        return self._tray

    def set_hotkeys(self, hotkeys: Hotkeys) -> None:
        """Refresh the shortcut column (global hotkeys are not Qt shortcuts, so it is just text)."""
        self._hotkeys = hotkeys
        self._select_action.setText(self._label("Chọn vùng dịch", hotkeys.select_region))
        self._lang_menu.setTitle(self._label("Ngôn ngữ nguồn", hotkeys.switch_lang))
        self._quit_action.setText(self._label("Thoát", hotkeys.quit))
        self._refresh_toggles()

    def update_status(self, state: OverlayState, *, paused_by_user: bool, text_visible: bool) -> None:
        self._paused_by_user = paused_by_user
        self._text_visible = text_visible
        status = "paused" if paused_by_user and state.status not in ("error", "guarded") else state.status
        self._tray.setIcon(self._icons.get(status, self._icons["idle"]))
        shown = state if not paused_by_user or state.status in ("paused", "error", "guarded") \
            else OverlayState(seq=state.seq, region=state.region, status="paused", src_lang=state.src_lang,
                              tgt_lang=state.tgt_lang)
        line = status_text(shown, text_visible=text_visible)
        if state.region is None and state.status == "idle" and not paused_by_user:
            # The pipeline's hint names the configured hotkey; say only what really works.
            key = self._hotkeys.select_region
            line = f"Chưa chọn vùng — nhấn {format_combo(key)}" if key else "Chưa chọn vùng — dùng menu Chọn vùng dịch"
        self._status_action.setText(f"{lang_pair(state)} · {line}" if line != lang_pair(state) else line)
        tip = f"{APP_TITLE} · {lang_pair(state)}\n{line}"
        self._tray.setToolTip(tip if len(tip) <= _TOOLTIP_MAX else tip[:_TOOLTIP_MAX - 1] + "…")
        self.set_lang_checked(state.src_lang)
        self._refresh_toggles()

    def set_lang_checked(self, lang: str) -> None:
        a = self._lang_actions.get(lang)
        if a is not None and not a.isChecked():
            a.setChecked(True)  # setChecked does not emit `triggered`, so no feedback loop

    def set_guard_checked(self, mode: str) -> None:
        a = self._guard_actions.get(mode)
        if a is not None and not a.isChecked():
            a.setChecked(True)

    def set_profiles(self, names: list[str], active: str) -> None:
        """Rebuild the profile list (checkable, `active` checked) and the submenu title."""
        names = list(dict.fromkeys(n for n in names if n))
        if active and active not in names:
            names.append(active)
        if names != self._profile_names:
            for a in self._profile_actions.values():
                self._profile_group.removeAction(a)
                self._profile_menu.removeAction(a)
                a.deleteLater()
            self._profile_actions = {}
            for name in names:
                a = QAction(self._menu_text(name), self._profile_menu, checkable=True)
                a.triggered.connect(lambda _checked=False, n=name: self.set_profile.emit(n))
                self._profile_group.addAction(a)
                self._profile_menu.insertAction(self._profile_sep, a)
                self._profile_actions[name] = a
            self._profile_names = names
        self._active_profile = active
        a = self._profile_actions.get(active)
        if a is not None and not a.isChecked():
            a.setChecked(True)
        self._profile_menu.setTitle(f"Hồ sơ: {self._menu_text(active)}")

    def active_profile(self) -> str:
        return self._active_profile

    def set_llm_mode_checked(self, mode: str) -> None:
        a = self._llm_actions.get(mode)
        if a is not None and not a.isChecked():
            a.setChecked(True)
        if mode in LLM_SHORT:
            self._llm_menu.setTitle(f"Dịch bằng LLM: {LLM_SHORT[mode]}")

    def set_llm_model_checked(self, llm_model: str) -> None:
        """config.llm_model: "" / "auto", "light", "quality" or a GGUF file name (its kind, if any)."""
        value = (llm_model or "").strip().lower()
        kind = "auto" if value in ("", "auto") else value if value in MODEL_LABELS else model_kind(value)
        a = self._model_actions.get(kind or "")
        if a is not None:
            if not a.isChecked():
                a.setChecked(True)
        else:  # another model file: none of the three
            self._model_group.setExclusive(False)
            for x in self._model_actions.values():
                x.setChecked(False)
            self._model_group.setExclusive(True)

    def set_llm_status(self, text: str) -> None:
        """The disabled status line under the LLM submenu ("LLM: sẵn sàng (<model>)", ...)."""
        text = " ".join((text or "").split())
        if not text.startswith("LLM"):
            text = f"LLM: {text}" if text else "LLM"
        shown = text if len(text) <= _LLM_STATUS_MAX else text[:_LLM_STATUS_MAX - 1] + "…"
        self._llm_status_action.setText(shown.replace("&", "&&"))
        self._llm_status_action.setToolTip(text)
        self._menu.setToolTipsVisible(len(shown) < len(text))

    def llm_status(self) -> str:
        return self._llm_status_action.toolTip()

    def popup_menu(self) -> None:
        """Open the menu without a click (the app was launched again while running): over the tray
        icon when Windows reports where it is, else at the mouse pointer."""
        geo = self._tray.geometry()
        pos = geo.topLeft() if geo.isValid() and not geo.isEmpty() else QCursor.pos()
        self._menu.popup(pos)  # flipped / moved by Qt to stay on the screen
        self._menu.raise_()
        self._menu.activateWindow()  # so that a click elsewhere closes it

    def show_message(self, title: str, text: str, *, error: bool = False, msecs: int = 5000, token: str = "") -> None:
        """A balloon; a click on it emits message_clicked(token) ("" for an ordinary message)."""
        icon = QSystemTrayIcon.MessageIcon.Warning if error else QSystemTrayIcon.MessageIcon.Information
        self._message_token = token
        self._tray.showMessage(title, text, icon, msecs)

    def set_pending_games(self, games: list[tuple[str, str]]) -> None:
        """New games found in front this run, as (key, title): one "Game mới: <title>…" entry each at the top of
        the "Tra cứu game" submenu (its title counts them)."""
        games = list(dict.fromkeys((k, t) for k, t in games if k))
        if games == self._pending_games:
            return
        for a in self._pending_actions:
            self._lookup_menu.removeAction(a)
            a.deleteLater()
        self._pending_actions = []
        for key, title in games:
            a = QAction(f"Game mới: {self._menu_text(title or key)}…", self._lookup_menu)
            a.triggered.connect(lambda _checked=False, k=key: self.lookup_game.emit(k))
            self._lookup_menu.insertAction(self._lookup_sep, a)
            self._pending_actions.append(a)
        self._pending_games = games
        self._lookup_sep.setVisible(bool(games))
        self._lookup_menu.setTitle(f"{LOOKUP_MENU} ({len(games)} game mới)" if games else LOOKUP_MENU)

    def pending_games(self) -> list[tuple[str, str]]:
        return list(self._pending_games)

    def tooltip(self) -> str:
        return self._tray.toolTip()

    def action_texts(self) -> list[str]:
        """Visible menu labels, submenus included (for tests and logs)."""
        out = []
        for a in self._menu.actions():
            if a.isSeparator():
                continue
            out.append(a.text())
            if a.menu() is not None:
                out.extend("  " + s.text() for s in a.menu().actions() if not s.isSeparator())
        return out

    # --- internals -------------------------------------------------------------------------

    @staticmethod
    def _label(text: str, combo: str) -> str:
        return f"{text}\t{format_combo(combo)}" if combo else text

    @staticmethod
    def _menu_text(name: str) -> str:
        """A profile name as menu text: elided, '&' not taken as a mnemonic, no tab column; the
        built-in "default" profile shows as "Mặc định"."""
        name = " ".join(name.split()) or "?"
        if name.casefold() == DEFAULT_PROFILE:
            return DEFAULT_PROFILE_LABEL
        if len(name) > _NAME_MAX:
            name = name[:_NAME_MAX - 1] + "…"
        return name.replace("&", "&&")

    def _refresh_toggles(self) -> None:
        hk = self._hotkeys
        self._pause_action.setText(self._label("Tiếp tục" if self._paused_by_user else "Tạm dừng", hk.toggle_pause))
        self._overlay_action.setText(
            self._label("Ẩn bản dịch" if self._text_visible else "Hiện bản dịch", hk.toggle_overlay))

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # left click
            self._click_timer.start(QApplication.doubleClickInterval() + 50)
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._click_timer.stop()
            self.select_region.emit()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.toggle_pause.emit()
