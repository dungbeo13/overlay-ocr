"""Vietnamese for the few texts Qt itself draws in our windows.

PySide6 ships no qtbase_vi.qm, so without a translator the right-click menu of every text field says
"&Undo / Cu&t / &Copy / &Paste / Select All" and standard dialog buttons say "OK / Cancel / Close". install()
puts a small QTranslator in front that answers only for the Qt contexts below; everything else (our own
texts are Vietnamese already) passes through unchanged.
"""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QTranslator

_EDIT_MENU = {
    "&Undo": "&Hoàn tác",
    "&Redo": "&Làm lại",
    "Cu&t": "Cắ&t",
    "&Copy": "&Sao chép",
    "&Paste": "&Dán",
    "Delete": "Xóa",
    "Select All": "Chọn tất cả",
}
TEXTS: dict[str, dict[str, str]] = {
    "QLineEdit": dict(_EDIT_MENU),  # also the editable part of combo boxes and spin boxes
    "QWidgetTextControl": {**_EDIT_MENU, "Copy &Link Location": "Sao chép địa chỉ liên &kết"},  # multi-line fields
    "QAbstractSpinBox": {"&Step up": "&Tăng", "Step &down": "&Giảm"},
    "QPlatformTheme": {  # standard dialog buttons (QDialogButtonBox, QMessageBox, QInputDialog)
        "OK": "OK", "Save": "Lưu", "Save All": "Lưu tất cả", "Open": "Mở", "&Yes": "&Có", "Yes to &All": "Có tất cả",
        "&No": "&Không", "N&o to All": "Không tất cả", "Abort": "Hủy bỏ", "Retry": "Thử lại", "Ignore": "Bỏ qua",
        "Close": "Đóng", "Cancel": "Hủy", "Discard": "Bỏ thay đổi", "Help": "Trợ giúp", "Apply": "Áp dụng",
        "Reset": "Đặt lại", "Restore Defaults": "Khôi phục mặc định",
    },
    "QMessageBox": {"Show Details...": "Xem chi tiết…", "Hide Details...": "Ẩn chi tiết…", "OK": "OK"},
}


class VietnameseQtTranslator(QTranslator):
    """Answers TEXTS; any other text comes back as it is (the same as no translation)."""

    def translate(self, context, source_text, disambiguation=None, n=-1):  # noqa: D401 - Qt override
        return TEXTS.get(context or "", {}).get(source_text, source_text)

    def isEmpty(self) -> bool:
        return False


_installed: VietnameseQtTranslator | None = None


def install(app: QCoreApplication | None = None) -> VietnameseQtTranslator | None:
    """Install the translator once (it must stay referenced: Qt does not own it)."""
    global _installed
    app = app or QCoreApplication.instance()
    if app is None:
        return None
    if _installed is None:
        _installed = VietnameseQtTranslator(app)
        app.installTranslator(_installed)
    return _installed
