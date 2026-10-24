"""The "Xưng hô" panel of the glossary editor's "Nhân vật" tab: pickers instead of a notes format to type.

For the character selected in the table: its own word (Tự xưng), a small table of how it addresses each
listener (another character of the profile, or a group such as "khách" / "người lạ") with an optional own
word for that listener only, and the rest of its notes (age, role, voice). The panel writes the notes text
that app.translate.llm_prompt.address_pair reads (app.address_notes) and shows, computed by address_pair
itself, the pronoun pair the model will be given toward each listener, plus what the app cannot use as a
fixed pair (and only passes on as notes).

The panel has no window of its own: its combo-box lists and tooltips are popups of the editor dialog, which
capture-excludes them (glossary_editor._PopupExcluder).
"""
from __future__ import annotations

import html
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import replace

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.address_notes import (
    CALL_WORDS,
    CATEGORY_LISTENERS,
    EXTRA_LISTENERS,
    SELF_WORDS,
    AddressNotes,
    AddressRule,
    PairInfo,
    address_phrases,
    category_understood,
    complete_rules,
    convert_unquoted,
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
    unquoted_phrases,
)
from app.translate.contextual import Character

L_LISTENER, L_CALL, L_OWN = range(3)
RULE_HEADERS = ["Người nghe", "Gọi là", "Tự xưng riêng (gợi ý cho LLM)"]
RULE_TIPS = [
    "Người được nói với: một nhân vật trong hồ sơ, hoặc một nhóm như “khách”, “người lạ”, “mọi người” "
    "(dùng cho mọi người nghe chưa có dòng riêng).",
    "Từ nhân vật dùng để gọi người nghe: anh, em, chị, cậu, bạn, mày, Sensei… Muốn ghi thêm cách khác thì "
    "ghi trong ngoặc, ví dụ: anh (hoặc 'tiền bối') hay anh (お兄ちゃん).",
    "Thường để trống. Chỉ điền khi nhân vật tự xưng khác đi với riêng người này (ví dụ: với bạn thân xưng "
    "'tao'). App vẫn dùng “Tự xưng” ở trên cho mọi người nghe; từ này chỉ gửi cho LLM trong ghi chú.",
]
SELF_TIP = ("Cách nhân vật tự gọi mình khi nói (tôi, em, anh, tớ, ta…). App dùng từ này cho mọi người "
            "nghe khi có cặp xưng hô cố định.")
SUGGEST_TIP = ("Điền “Tự xưng” (nếu trống) và các ô “Gọi là” còn trống khi đoán được: theo giới tính, vai trò "
               "trong “Ghi chú khác” và cách người nghe tự xưng. Ô nào không đoán được thì để trống.")
OTHER_PLACEHOLDER = "tuổi, vai trò, giọng nói… ví dụ: 17 tuổi, em gái nuôi của Ren, nói nhỏ nhẹ"
CONVERT_TIP = "Biến các câu xưng hô gõ thiếu dấu nháy trong “Ghi chú khác” thành dòng trong bảng."
NO_CHARACTER = "Chưa chọn nhân vật — chọn một dòng ở bảng trên, hoặc bấm “Thêm nhân vật”."
NO_NAME = "Nhập “Tên gốc” của nhân vật ở bảng trên để xem trước."
EMPTY_RULES = "Chưa có dòng nào."
OTHERS_LABEL = "người khác / chưa rõ ai"
MANY_LISTENERS = 6  # more other characters than this: the ones without a rule are summarised in one line
NOTES_SHOWN = 140  # "Ghi chú lưu vào hồ sơ" shows this many characters (the whole text in its tooltip)

_ROLE_AUTO_CALL = Qt.ItemDataRole.UserRole + 10  # on a "Gọi là" item: a suggestion, redone when the listener changes
_ERROR_BG, _ERROR_FG = QColor(255, 205, 210), QColor(110, 0, 0)
_COLORS = {"pair": "", "free": "#6b6b6b", "note": "#7a5a00", "warn": "#b71c1c"}


@contextmanager
def _signals_blocked(obj: QObject):
    old = obj.blockSignals(True)
    try:
        yield
    finally:
        obj.blockSignals(old)


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def display_name(c: Character) -> str:
    return _clean(c.name_vi) or _clean(c.name)


class _WordDelegate(QStyledItemDelegate):
    """Editable combo-box editor: free text, with suggestions from `choices()` ((text, tooltip) pairs).
    Picking a suggestion with the mouse commits it at once."""

    def __init__(self, choices: Callable[[], list[tuple[str, str]]], parent: QObject) -> None:
        super().__init__(parent)
        self._choices = choices
        self._open: set[QComboBox] = set()

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.setMaxVisibleItems(16)
        for text, tip in self._choices():
            combo.addItem(text)
            if tip:
                combo.setItemData(combo.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self._open.add(combo)
        combo.activated.connect(lambda _i, c=combo: QTimer.singleShot(0, lambda: self._commit(c)))
        return combo

    def _commit(self, combo: QComboBox) -> None:
        if combo in self._open:  # not closed meanwhile by Enter / Tab / focus out
            self.commitData.emit(combo)
            self.closeEditor.emit(combo, QAbstractItemDelegate.EndEditHint.NoHint)

    def destroyEditor(self, editor, index) -> None:
        self._open.discard(editor)
        super().destroyEditor(editor, index)

    def setEditorData(self, editor: QComboBox, index) -> None:
        editor.setEditText(index.data(Qt.ItemDataRole.DisplayRole) or "")
        editor.lineEdit().selectAll()

    def setModelData(self, editor: QComboBox, model, index) -> None:
        model.setData(index, _clean(editor.currentText()), Qt.ItemDataRole.EditRole)


class _OwnDelegate(_WordDelegate):
    """The "Tự xưng riêng" column: a word equal to the character's Tự xưng is shown blank (it says nothing
    more; the notes keep it verbatim), so the column does not look like it must be filled."""

    def __init__(self, choices: Callable[[], list[tuple[str, str]]], same: Callable[[str], bool],
                 parent: QObject) -> None:
        super().__init__(choices, parent)
        self._same = same

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if option.text and self._same(option.text):
            option.text = ""


class _NotesEdit(QPlainTextEdit):
    """"Ghi chú khác": a few lines of plain text (long notes stay readable), with QLineEdit's text() /
    setText(); Tab moves on."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        lines = QFontMetrics(self.font()).lineSpacing()
        self.setFixedHeight(lines * 3 + 2 * int(self.document().documentMargin()) + 2 * self.frameWidth() + 2)

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:
        self.setPlainText(text)


class PronounPicker(QWidget):
    """Structured address editing for one character; `changed` after every user edit (then notes() and
    self_pronoun() hold the new values)."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._speaker = Character("", "")  # identity (name, name_vi, gender) of the character shown
        self._others: tuple[Character, ...] = ()
        self._self_word = ""  # a standalone "xưng 'x'" of the notes: follows Tự xưng
        self._last_self = ""
        self._loaded_text = ""  # the notes as loaded: kept verbatim while nothing meaningful changes
        self._loaded_render = ""
        self._order = AddressNotes()  # the loaded notes' source order (head, self_after, self_first)
        self._loading = False
        self._lines: list[tuple[str, str]] = []
        self._problems: list[str] = []
        self._build()
        self.clear_speaker()

    # --- construction --------------------------------------------------------------------------

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        left = QVBoxLayout()
        top = QHBoxLayout()
        self_label = QLabel("&Tự xưng:")
        self.self_combo = QComboBox()
        self.self_combo.setEditable(True)
        self.self_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.self_combo.addItems(list(SELF_WORDS))
        self.self_combo.setMinimumWidth(150)
        self.self_combo.setToolTip(SELF_TIP)
        self.self_combo.editTextChanged.connect(self._on_self_edited)
        self_label.setBuddy(self.self_combo)
        self_label.setToolTip(SELF_TIP)
        self.suggest_button = QPushButton("&Gợi ý")
        self.suggest_button.setToolTip(SUGGEST_TIP)
        self.suggest_button.clicked.connect(self.suggest)
        top.addWidget(self_label)
        top.addWidget(self.self_combo)
        top.addWidget(self.suggest_button)
        top.addStretch(1)
        left.addLayout(top)

        rules_label = QLabel("Cách gọi từng người nghe:")
        rules_label.setToolTip(RULE_TIPS[0])
        left.addWidget(rules_label)
        body = QHBoxLayout()
        self.rules_table = t = QTableWidget(0, len(RULE_HEADERS))
        t.setHorizontalHeaderLabels(RULE_HEADERS)
        for col, tip in enumerate(RULE_TIPS):
            t.horizontalHeaderItem(col).setToolTip(tip)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
                          | QAbstractItemView.EditTrigger.AnyKeyPressed | QAbstractItemView.EditTrigger.SelectedClicked)
        t.setTabKeyNavigation(False)  # Tab leaves the table (arrows move between cells; Tab while editing still moves on)
        t.setWordWrap(False)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(28)
        h = t.horizontalHeader()
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h.setSectionResizeMode(L_LISTENER, QHeaderView.ResizeMode.Stretch)
        h.setMinimumSectionSize(90)
        for col, width in ((L_CALL, 110), (L_OWN, 180)):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            t.setColumnWidth(col, width)
        t.setMinimumHeight(96)
        self._delegates = [_WordDelegate(self._listener_choices, t),
                           _WordDelegate(lambda: [(w, "") for w in CALL_WORDS], t),
                           _OwnDelegate(lambda: [(w, "") for w in SELF_WORDS], self._same_as_self, t)]
        for col, d in enumerate(self._delegates):
            t.setItemDelegateForColumn(col, d)
        t.itemChanged.connect(self._on_rule_item_changed)
        for key, slot in ((QKeySequence(Qt.Key.Key_Delete), self._delete_selected),
                          (QKeySequence(Qt.Key.Key_Insert), lambda: self.add_rule(edit=True))):
            sc = QShortcut(key, t)
            sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(slot)
        body.addWidget(t, 1)
        side = QVBoxLayout()
        self.add_rule_button = QPushButton("Thêm người &nghe")
        self.add_rule_button.setToolTip("Thêm một dòng (phím Insert khi đang ở bảng).")
        self.add_rule_button.clicked.connect(lambda: self.add_rule(edit=True))
        self.delete_rule_button = QPushButton("&Xóa dòng")
        self.delete_rule_button.setToolTip("Xóa các dòng đang chọn (phím Delete khi đang ở bảng).")
        self.delete_rule_button.clicked.connect(self._delete_selected)
        side.addWidget(self.add_rule_button)
        side.addWidget(self.delete_rule_button)
        self.empty_label = QLabel(EMPTY_RULES)  # beside the empty table (no extra line under it)
        self.empty_label.setEnabled(False)
        self.empty_label.setWordWrap(True)
        self.empty_label.setMaximumWidth(self.add_rule_button.sizeHint().width() + 10)
        side.addWidget(self.empty_label)
        side.addStretch(1)
        body.addLayout(side)
        left.addLayout(body, 1)

        other = QHBoxLayout()
        other_label = QLabel("Ghi chú &khác:")
        self.other_edit = _NotesEdit()
        self.other_edit.setPlaceholderText(OTHER_PLACEHOLDER)
        self.other_edit.setToolTip("Mọi điều khác về nhân vật (tuổi, vai trò, giọng nói). Gửi kèm cho LLM.")
        self.other_edit.textChanged.connect(lambda: None if self._loading else self._emit())
        other_label.setBuddy(self.other_edit)
        other.addWidget(other_label, 0, Qt.AlignmentFlag.AlignTop)
        other.addWidget(self.other_edit, 1)
        self.convert_button = QPushButton("Chuyển vào bảng")
        self.convert_button.setToolTip(CONVERT_TIP)
        self.convert_button.clicked.connect(self.convert_unquoted)
        self.convert_button.setVisible(False)
        other.addWidget(self.convert_button, 0, Qt.AlignmentFlag.AlignTop)
        left.addLayout(other)
        outer.addLayout(left, 3)

        right = QVBoxLayout()
        head = QLabel("<b>LLM sẽ được báo:</b>")
        head.setToolTip("Cặp xưng hô cố định app gửi kèm mỗi câu thoại, tính đúng như lúc dịch.")
        right.addWidget(head)
        self.preview_view = QTextBrowser()  # read-only, wraps to its width, scrolls only when needed
        self.preview_view.setOpenLinks(False)
        self.preview_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_view.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # no extra Tab stop
        self.preview_view.setMinimumHeight(80)
        self.preview_view.document().setDocumentMargin(6)
        right.addWidget(self.preview_view, 1)
        self.notes_label = QLabel()
        self.notes_label.setWordWrap(True)
        self.notes_label.setTextFormat(Qt.TextFormat.RichText)
        self.notes_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.notes_label.setToolTip("Đúng chữ sẽ lưu vào cột “Ghi chú” của hồ sơ.")
        right.addWidget(self.notes_label)
        self.problem_label = QLabel()
        self.problem_label.setWordWrap(True)
        self.problem_label.setStyleSheet("color: #b71c1c;")
        right.addWidget(self.problem_label)
        outer.addLayout(right, 2)

    # --- loading -------------------------------------------------------------------------------

    def load(self, speaker: Character, others: Sequence[Character], structure: AddressNotes | None = None) -> None:
        """Show `speaker` (its self_pronoun and notes); `structure`: rows being edited for these notes
        (incomplete ones included), else they are parsed from the notes."""
        an = structure if structure is not None else parse_notes(speaker.notes)
        self._loading = True
        try:
            self._speaker = speaker
            self._others = tuple(o for o in others if _clean(o.name))
            self._self_word = an.self_word
            self._last_self = _clean(speaker.self_pronoun)
            self._loaded_text = speaker.notes
            self._loaded_render = render_notes(an)
            self._order = AddressNotes(head=an.head, self_after=an.self_after, self_first=an.self_first)
            with _signals_blocked(self.self_combo):
                self.self_combo.setEditText(speaker.self_pronoun)
            with _signals_blocked(self.other_edit):
                self.other_edit.setText(an.other)
            t = self.rules_table
            with _signals_blocked(t):
                t.setRowCount(0)
            for rule in an.rules:
                self.add_rule(rule)
            self.setEnabled(True)
        finally:
            self._loading = False
        self._refresh()

    def set_context(self, speaker: Character, others: Sequence[Character]) -> None:
        """The table changed elsewhere: new name / Vietnamese name / gender of the character shown, or
        other characters. The rows and notes being edited stay."""
        self._speaker = replace(self._speaker, name=speaker.name, name_vi=speaker.name_vi, gender=speaker.gender)
        self._others = tuple(o for o in others if _clean(o.name))
        self._refresh()

    def clear_speaker(self, message: str = NO_CHARACTER) -> None:
        self._loading = True
        try:
            self._speaker = Character("", "")
            self._others = ()
            self._self_word = self._last_self = self._loaded_text = self._loaded_render = ""
            self._order = AddressNotes()
            with _signals_blocked(self.self_combo):
                self.self_combo.setEditText("")
            with _signals_blocked(self.other_edit):
                self.other_edit.clear()
            with _signals_blocked(self.rules_table):
                self.rules_table.setRowCount(0)
        finally:
            self._loading = False
        self.setEnabled(False)
        self.convert_button.setVisible(False)
        self._lines = [("free", message)]
        self._problems = []
        self._render_preview("")

    # --- values --------------------------------------------------------------------------------

    def self_pronoun(self) -> str:
        return _clean(self.self_combo.currentText())

    def structure(self) -> AddressNotes:
        t = self.rules_table
        rules = tuple(AddressRule(self._cell(r, L_LISTENER), self._cell(r, L_CALL), self._cell(r, L_OWN))
                      for r in range(t.rowCount()))
        return replace(self._order, other=_clean(self.other_edit.text()), self_word=self._self_word, rules=rules)

    def notes(self) -> str:
        """The notes to store: the loaded text verbatim while it says the same, else the rendering."""
        rendered = render_notes(self.structure())
        return self._loaded_text if rendered == self._loaded_render else rendered

    def character(self) -> Character:
        return replace(self._speaker, self_pronoun=self.self_pronoun(), notes=self.notes())

    def problems(self) -> list[str]:
        return list(self._problems)

    def preview_lines(self) -> list[str]:
        """The preview as plain text, one entry per line (tests, accessibility)."""
        return [text for _kind, text in self._lines]

    # --- editing -------------------------------------------------------------------------------

    def set_self_pronoun(self, text: str) -> None:
        """Change Tự xưng as the user would (the standalone "xưng" of the notes follows)."""
        self.self_combo.setEditText(text)

    def add_rule(self, rule: AddressRule | None = None, *, edit: bool = False, auto_call: bool = False) -> int:
        """Append a row. Without `rule` (the user's "Thêm người nghe"): the first other character that has
        no row yet, with a suggested "Gọi là" (redone if the listener is changed, like any `auto_call`)."""
        t = self.rules_table
        if rule is None:
            have: set[str] = set()
            for r in range(t.rowCount()):
                listener = self._cell(r, L_LISTENER)
                target = rule_target(listener, self._others) if listener else None
                have.add((display_name(target) if target is not None else listener).casefold())
            free = [o for o in self._others if display_name(o).casefold() not in have]
            target = free[0] if free else None
            call = suggest_call(self._effective_own(), target) if target is not None else ""
            rule = AddressRule(display_name(target) if target is not None else "", call)
            auto_call = bool(call)
        row = t.rowCount()
        with _signals_blocked(t):
            t.insertRow(row)
            for col, text in ((L_LISTENER, rule.listener), (L_CALL, rule.call), (L_OWN, rule.own)):
                item = QTableWidgetItem(text)
                if col == L_CALL:
                    item.setData(_ROLE_AUTO_CALL, auto_call)
                t.setItem(row, col, item)
        if not self._loading:
            self._emit()
        if edit:
            t.setFocus(Qt.FocusReason.OtherFocusReason)
            t.setCurrentCell(row, L_LISTENER)
            t.editItem(t.item(row, L_LISTENER))
        return row

    def delete_rules(self, rows: Sequence[int]) -> None:
        for row in sorted(set(rows), reverse=True):
            if 0 <= row < self.rules_table.rowCount():
                self.rules_table.removeRow(row)
        if rows:
            self._emit()

    def suggest(self) -> None:
        """Fill Tự xưng when empty, every empty "Gọi là", and (no rows yet, 1-3 other characters) one row
        per other character."""
        if not self.isEnabled():
            return
        if not self.self_pronoun():
            word = suggest_self(self._speaker.gender, self.other_edit.text())
            with _signals_blocked(self.self_combo):
                self.self_combo.setEditText(word)
            self._on_self_edited(word, emit=False)
        own = self._effective_own()
        t = self.rules_table
        with _signals_blocked(t):
            for r in range(t.rowCount()):
                listener = self._cell(r, L_LISTENER)
                if listener and not self._cell(r, L_CALL) and not self._cell(r, L_OWN):
                    word = suggest_call(own, rule_target(listener, self._others), listener)
                    t.item(r, L_CALL).setText(word)
                    t.item(r, L_CALL).setData(_ROLE_AUTO_CALL, bool(word))
        if not any(self._cell(r, L_LISTENER) for r in range(t.rowCount())) and 1 <= len(self._others) <= 3:
            with _signals_blocked(t):
                t.setRowCount(0)
            for o in self._others:  # only the listeners a word can be guessed for (an empty row blocks saving)
                word = suggest_call(own, o)
                if word:
                    self.add_rule(AddressRule(display_name(o), word), auto_call=True)
        self._emit()

    def convert_unquoted(self) -> None:
        """"Chuyển vào bảng": the address phrases typed without quotes in "Ghi chú khác" become rows (and Tự
        xưng, when it is empty)."""
        if not self.isEnabled():
            return
        before = self.structure()
        after, own = convert_unquoted(before, self.self_pronoun())
        if after == before and own == self.self_pronoun():
            return
        self._loading = True
        try:
            for rule in after.rules[len(before.rules):]:
                self.add_rule(rule)
            with _signals_blocked(self.other_edit):
                self.other_edit.setText(after.other)
            self._self_word = after.self_word
            self._order = replace(self._order, head=after.head)
            if own != self.self_pronoun():
                with _signals_blocked(self.self_combo):
                    self.self_combo.setEditText(own)
                self._last_self = own
        finally:
            self._loading = False
        self._emit()

    def _same_as_self(self, text: str) -> bool:
        """A "Tự xưng riêng" cell that is just the character's Tự xưng (shown blank)."""
        own = self.self_pronoun()
        return bool(own) and _clean(text).casefold() == own.casefold()

    # --- internals -----------------------------------------------------------------------------

    def _cell(self, row: int, col: int) -> str:
        item = self.rules_table.item(row, col)
        return _clean(item.text()) if item is not None else ""

    def _listener_choices(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen = {display_name(self._speaker).casefold(), _clean(self._speaker.name).casefold(), ""}
        for o in self._others:
            name = display_name(o)
            if name.casefold() not in seen:
                seen.add(name.casefold())
                out.append((name, f"Nhân vật: {o.name}" + (f" ({o.name_vi})" if o.name_vi and o.name_vi != o.name else "")))
        for group in (*CATEGORY_LISTENERS, *EXTRA_LISTENERS):
            if group.casefold() in seen:
                continue
            seen.add(group.casefold())
            if category_understood(group):
                out.append((group, "Nhóm: dùng cho mọi người nghe chưa có dòng riêng."))
            else:
                out.append((group, "Chỉ gửi cho LLM trong ghi chú; app chưa dùng làm cặp xưng hô cố định."))
        return out

    def _effective_own(self) -> str:
        return self.self_pronoun() or notes_own(render_notes(self.structure())) or ""

    def _delete_selected(self) -> None:
        self.delete_rules(sorted({i.row() for i in self.rules_table.selectedIndexes()}))

    def _on_self_edited(self, text: str, emit: bool = True) -> None:
        if self._loading:
            return
        new = _clean(text)
        if self._self_word and new:
            word, aside = split_word(self._self_word)
            if not self._last_self or word.casefold() == self._last_self.casefold():
                self._self_word = f"{new} ({aside})" if aside else new
        self._last_self = new
        if emit:
            self._emit()

    def _on_rule_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        t = self.rules_table
        row = item.row()
        if item.column() == L_CALL:
            with _signals_blocked(t):
                item.setData(_ROLE_AUTO_CALL, False)  # the user's word now
        elif item.column() == L_LISTENER:
            call = t.item(row, L_CALL)
            if call is not None and (call.data(_ROLE_AUTO_CALL) or not (self._cell(row, L_CALL) or self._cell(row, L_OWN))):
                listener = self._cell(row, L_LISTENER)
                word = suggest_call(self._effective_own(), rule_target(listener, self._others), listener) if listener else ""
                with _signals_blocked(t):
                    call.setText(word)
                    call.setData(_ROLE_AUTO_CALL, bool(word))
        self._emit()

    def _emit(self) -> None:
        self._refresh()
        self.changed.emit()

    def _refresh(self) -> None:
        an = self.structure()
        t = self.rules_table
        row_problems = rule_problems(an, self._speaker)
        bad = {r for r, _m in row_problems}
        with _signals_blocked(t):
            for r in range(t.rowCount()):
                msg = next((m for i, m in row_problems if i == r), "")
                for c in range(t.columnCount()):
                    item = t.item(r, c)
                    if item is None:
                        continue
                    item.setData(Qt.ItemDataRole.BackgroundRole, _ERROR_BG if r in bad else None)
                    item.setData(Qt.ItemDataRole.ForegroundRole, _ERROR_FG if r in bad else None)
                    tip = RULE_TIPS[c] if not item.text() else item.text()
                    if c == L_OWN and item.text() and self._same_as_self(item.text()):
                        tip = f"Giống “Tự xưng” ở trên ('{self.self_pronoun()}'): không cần điền."
                    item.setToolTip(msg or tip)
        t.viewport().update()  # the "Tự xưng riêng" cells equal to Tự xưng are drawn blank
        self.convert_button.setVisible(bool(unquoted_phrases(an.other)))
        problems = [f"Dòng {r + 1}: {m}" for r, m in row_problems]
        sp = self_problem(self.self_pronoun())
        if sp:
            problems.insert(0, sp)
        self._problems = problems
        self.empty_label.setVisible(t.rowCount() == 0)
        self.delete_rule_button.setEnabled(t.rowCount() > 0)
        notes = self.notes()
        own_from_notes = None if self.self_pronoun() else notes_own(notes)
        if own_from_notes:
            placeholder = f"theo ghi chú: {own_from_notes}"
        else:
            placeholder = f"gợi ý: {suggest_self(self._speaker.gender, an.other)}"
        self.self_combo.lineEdit().setPlaceholderText(placeholder)
        kept = split_word(self._self_word)[1] if self._self_word else ""
        self.self_combo.setToolTip(SELF_TIP + (f"\nGhi chú kèm từ này (được giữ): {kept}" if kept else ""))
        self._lines = self._preview(an, notes)
        self._render_preview(notes)

    def _render_preview(self, notes: str) -> None:
        rows = []
        for kind, text in self._lines:
            color = _COLORS.get(kind, "")
            style = "margin-bottom:3px;" + (f"color:{color};" if color else "")
            mark = "•" if kind in ("pair", "free") else "–"
            rows.append(f"<p style='margin-top:0; {style}'>{mark} {html.escape(text)}</p>")
        self.preview_view.setHtml("".join(rows))
        short = notes if len(notes) <= NOTES_SHOWN else notes[:NOTES_SHOWN - 1].rstrip() + "…"  # whole text: tooltip
        shown = html.escape(short) if notes else "<i>(trống)</i>"
        self.notes_label.setText(f"<span style='color:#6b6b6b'>Ghi chú lưu vào hồ sơ:</span> {shown}")
        self.notes_label.setToolTip(notes or "Đúng chữ sẽ lưu vào cột “Ghi chú” của hồ sơ.")
        self.problem_label.setText("\n".join(self._problems))
        self.problem_label.setVisible(bool(self._problems))

    def _preview(self, an: AddressNotes, notes: str) -> list[tuple[str, str]]:
        speaker = replace(self._speaker, self_pronoun=self.self_pronoun(), notes=notes)
        if not _clean(speaker.name):
            return [("free", NO_NAME)]
        sp = display_name(speaker)
        infos = pair_infos(speaker, self._others)
        by_name = {_clean(i.listener.name).casefold(): i for i in infos if i.listener is not None}
        rules = complete_rules(an)
        targets = {r: rule_target(r.listener, self._others) for r in rules}
        named = {_clean(c.name).casefold() for c in targets.values() if c is not None}
        lines: list[tuple[str, str]] = []
        many = len(by_name) > MANY_LISTENERS
        grouped: dict[tuple, list[str]] = {}
        for info in infos:
            if info.listener is None:
                continue
            key = _clean(info.listener.name).casefold()
            if many and info.source not in ("rule", "derived") and key not in named:
                grouped.setdefault((info.own, info.other, info.defined, info.source), []).append(
                    display_name(info.listener))
                continue
            lines.append(self._pair_line(sp, display_name(info.listener), info))
        for (own, other, defined, source), names in grouped.items():
            shown = ", ".join(names[:3]) + (", …" if len(names) > 3 else "")
            label = f"{len(names)} nhân vật khác ({shown})"
            lines.append(self._pair_line(sp, label, PairInfo(None, own, other, defined, source), derived_ok=False))
        lines.append(self._pair_line(sp, OTHERS_LABEL, infos[-1]))
        lines += self._remarks(an, rules, targets, by_name, infos[-1])
        return lines

    @staticmethod
    def _pair_line(sp: str, name: str, info: PairInfo, *, derived_ok: bool = True) -> tuple[str, str]:
        if not info.defined:
            return "free", f"{sp} → {name}: chưa cố định — LLM tự chọn theo ghi chú"
        if info.own:
            text = f"{sp} → {name}: {sp} xưng '{info.own}', gọi '{info.other}'"
        else:
            text = f"{sp} → {name}: {sp} chưa có Tự xưng, gọi '{info.other}'"
        if info.source == "derived" and derived_ok:
            text += f" (suy ra từ Tự xưng của {name})"
        elif info.source == "category":
            text += " (quy tắc chung)"
        return "pair", text

    def _remarks(self, an: AddressNotes, rules, targets, by_name, others_info: PairInfo) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        group_words: dict[str, str] = {}
        recased: dict[str, str] = {}
        for rule in rules:
            listener = _clean(rule.listener)
            call = split_word(rule.call)[0]
            own = split_word(rule.own)[0]
            target = targets[rule]
            if not call:  # "với Garo xưng 'con'": no pair without a word for the listener
                name = display_name(target) if target is not None else listener
                out.append(("note", f"Với {name}: tự xưng '{own.lower()}' chỉ gửi cho LLM trong ghi chú — "
                                    "thêm “Gọi là” để có cặp cố định."))
                continue
            if target is None:
                if not category_understood(listener):
                    near = next((o for o in self._others if listener.casefold().endswith(
                        (" " + display_name(o).casefold(), " " + _clean(o.name).casefold()))), None)
                    if near is not None:
                        out.append(("warn", f"“{listener}”: app không nhận ra đây là {display_name(near)} "
                                            f"(chỉ hiểu tên, hoặc ông / bà / anh / chị / cô / chú / bác / em / cậu "
                                            f"+ tên) — ghi “{display_name(near)}” để có cặp cố định."))
                    else:
                        out.append(("note", f"“{listener}”: không phải nhân vật trong hồ sơ hay nhóm app hiểu "
                                            "(khách, người lạ, mọi người…) — chỉ gửi cho LLM trong ghi chú."))
                    continue
                group_words.setdefault(call.casefold(), listener)
                info, name = others_info, listener
            else:
                info, name = by_name.get(_clean(target.name).casefold()), display_name(target)
                if info is None:
                    continue
                if info.defined and (info.other or "").casefold() != call.casefold():
                    out.append(("warn", f"Với {name}, app dùng '{info.other}', không phải '{call}' "
                                        "(một câu xưng hô khác trong ghi chú đứng trước)."))
            if info.defined and info.other and info.other != call and info.other.casefold() == call.casefold():
                recased.setdefault(call, info.other)
            if own and info.defined and info.own and info.own.casefold() != own.casefold():
                out.append(("note", f"Với {name}, app vẫn dùng tự xưng '{info.own}' (một Tự xưng cho mọi "
                                    f"người nghe); '{own}' chỉ gửi cho LLM trong ghi chú."))
        if len(group_words) > 1:
            words = ", ".join(f"'{w}'" for w in group_words)
            out.append(("warn", f"Các nhóm dùng từ khác nhau ({words}): app không dùng nhóm nào, LLM tự chọn."))
        bare = unquoted_phrases(an.other)
        if bare:
            out.append(("warn", "“Ghi chú khác” có câu xưng hô thiếu dấu nháy nên app không đọc được: "
                        + "; ".join(f"“{p}”" for p in bare) + " — bấm “Chuyển vào bảng”."))
        phrases = address_phrases(an.other, ignore=(self._speaker.name, self._speaker.name_vi))
        if phrases:
            out.append(("note", "“Ghi chú khác” còn câu xưng hô app vẫn đọc: " + "; ".join(phrases)
                        + " — nên chuyển vào bảng."))
        if recased:
            out.append(("note", "LLM nhận từ đã đổi chữ hoa / thường: "
                        + ", ".join(f"{a} → {b}" for a, b in recased.items()) + "."))
        return out
