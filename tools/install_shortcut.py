"""Create or remove the shortcuts that open Screen Translator without a console window.

    & $py -m tools.install_shortcut               # Desktop + Start Menu
    & $py -m tools.install_shortcut --startup     # also start with Windows (Startup folder)
    & $py -m tools.install_shortcut --remove      # remove Desktop + Start Menu (add --startup for that one too)
    & $py -m tools.install_shortcut --check       # show where the existing shortcuts point
    & $py -m tools.install_shortcut --redraw-icon # also draw assets\\app.ico again from the tray icon

Every shortcut runs <venv>\\Scripts\\pythonw.exe "<project>\\run_app.pyw" with the project folder as its
working directory and assets\\app.ico (the tray icon, drawn at 16-256 px; only drawn when the file is missing
or with --redraw-icon) as its icon, and carries the
app's AppUserModelID (app.winutil.APP_USER_MODEL_ID, which the app also sets on itself), so the taskbar
groups the app's windows as "Screen Translator" with that icon. Running the tool again rewrites the same
files (nothing changes when they are already right); every written shortcut is read back and an error is
raised when Windows stored something else (e.g. a path cut at 260 characters). The shortcuts point at THIS
project folder and venv: after moving either, run the tool again ("Tạo lại shortcut.cmd" in the project
folder does that with the venv's Python).

The Settings window's "Khởi động cùng Windows" box uses set_startup() / startup_enabled() from here.
Folders come from SHGetKnownFolderPath (a Desktop redirected to OneDrive is found); --desktop-dir,
--menu-dir and --startup-dir replace them (tests write into a temporary folder).
"""
from __future__ import annotations

import argparse
import ctypes
import os
import struct
import sys
import uuid
from ctypes import wintypes
from dataclasses import dataclass, fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:  # run as a file or from another folder: app.* must import
    sys.path.insert(0, str(PROJECT_ROOT))

from app.winutil import APP_USER_MODEL_ID  # noqa: E402

ENTRY = PROJECT_ROOT / "run_app.pyw"
ICON_PATH = PROJECT_ROOT / "assets" / "app.ico"
SHORTCUT_NAME = "Screen Translator"
DESCRIPTION = "Screen Translator: dịch chữ trên màn hình (Nhật / Anh → Việt)"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
LOCATIONS = ("desktop", "menu", "startup")
LABELS = {"desktop": "Desktop", "menu": "Start Menu", "startup": "Startup (khởi động cùng Windows)"}
_FOLDER_IDS = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",  # FOLDERID_Desktop
    "menu": "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}",  # FOLDERID_Programs (Start Menu\Programs)
    "startup": "{B97D20BB-F46A-4C97-BA10-5E3608430854}",  # FOLDERID_Startup
}
_SLGP_RAWPATH = 0x4
_STGM_READ = 0
_SW_SHOWNORMAL = 1


# --- folders and paths ----------------------------------------------------------------------------

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def known_folder(location: str) -> Path:
    """The user's Desktop / Start Menu Programs / Startup folder."""
    u = uuid.UUID(_FOLDER_IDS[location])
    guid = _GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD, wintypes.HANDLE,
                                             ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    out = ctypes.c_void_p()
    hr = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
    try:
        if hr != 0 or not out.value:
            raise OSError(f"SHGetKnownFolderPath({location}) failed: 0x{hr & 0xFFFFFFFF:08X}")
        return Path(ctypes.wstring_at(out.value))
    finally:
        if out.value:
            ole32.CoTaskMemFree(out)


def shortcut_path(location: str, folder: Path | None = None) -> Path:
    return Path(folder or known_folder(location)) / f"{SHORTCUT_NAME}.lnk"


def pythonw_path(python: str | Path | None = None) -> Path:
    """pythonw.exe next to `python` (default: this interpreter, i.e. the venv's)."""
    exe = Path(python or sys.executable)
    cand = exe if exe.name.lower() == "pythonw.exe" else exe.with_name("pythonw.exe")
    if not cand.is_file():
        raise FileNotFoundError(f"không tìm thấy pythonw.exe cạnh {exe}")
    return cand


@dataclass(frozen=True)
class ShortcutInfo:
    target: str
    arguments: str
    working_dir: str
    icon: str
    icon_index: int
    description: str
    app_id: str = ""  # System.AppUserModel.ID

    def runs_this_app(self) -> bool:
        """Points at this project's run_app.pyw through a pythonw.exe."""
        return Path(self.target).name.lower() == "pythonw.exe" \
            and os.path.normcase(str(ENTRY)) in os.path.normcase(self.arguments)


def wanted(python: str | Path | None = None, icon: Path | None = None) -> ShortcutInfo:
    return ShortcutInfo(target=str(pythonw_path(python)), arguments=f'"{ENTRY}"', working_dir=str(PROJECT_ROOT),
                        icon=str(icon or ICON_PATH), icon_index=0, description=DESCRIPTION, app_id=APP_USER_MODEL_ID)


# --- .lnk files (IShellLinkW via comtypes) -----------------------------------------------------------

_PKEY_APP_USER_MODEL_ID = ("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}", 5)
_VT_LPWSTR = 31
_propsys = None  # (IPropertyStore, PROPERTYKEY, PROPVARIANT), defined on first use (comtypes)


def _property_store_types():
    """IPropertyStore (propsys.h) for comtypes, which does not ship it: the shortcut's AppUserModelID."""
    global _propsys
    if _propsys is None:
        from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

        class PROPERTYKEY(ctypes.Structure):
            _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

        class PROPVARIANT(ctypes.Structure):  # vt + 3 reserved words + the value union (24 bytes on x64)
            _fields_ = [("vt", ctypes.c_ushort), ("reserved1", ctypes.c_ushort), ("reserved2", ctypes.c_ushort),
                        ("reserved3", ctypes.c_ushort), ("value", ctypes.c_void_p), ("value2", ctypes.c_void_p)]

        class IPropertyStore(IUnknown):
            _iid_ = GUID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
            _methods_ = [
                COMMETHOD([], HRESULT, "GetCount", (["out"], ctypes.POINTER(wintypes.DWORD), "cProps")),
                COMMETHOD([], HRESULT, "GetAt", (["in"], wintypes.DWORD, "iProp"),
                          (["out"], ctypes.POINTER(PROPERTYKEY), "pkey")),
                COMMETHOD([], HRESULT, "GetValue", (["in"], ctypes.POINTER(PROPERTYKEY), "key"),
                          (["out"], ctypes.POINTER(PROPVARIANT), "pv")),
                COMMETHOD([], HRESULT, "SetValue", (["in"], ctypes.POINTER(PROPERTYKEY), "key"),
                          (["in"], ctypes.POINTER(PROPVARIANT), "propvar")),
                COMMETHOD([], HRESULT, "Commit"),
            ]

        _propsys = (IPropertyStore, PROPERTYKEY, PROPVARIANT)
    return _propsys


def _app_id_key():
    from comtypes import GUID

    _store, key_type, _pv = _property_store_types()
    return key_type(GUID(_PKEY_APP_USER_MODEL_ID[0]), _PKEY_APP_USER_MODEL_ID[1])


def _set_app_id(link, app_id: str) -> None:
    store_type, _key, pv_type = _property_store_types()
    store = link.QueryInterface(store_type)
    text = ctypes.create_unicode_buffer(app_id)
    pv = pv_type()
    pv.vt = _VT_LPWSTR
    pv.value = ctypes.cast(text, ctypes.c_void_p).value
    store.SetValue(ctypes.byref(_app_id_key()), ctypes.byref(pv))  # copied by the store
    store.Commit()


def _get_app_id(link) -> str:
    from _ctypes import COMError

    store_type, _key, _pv = _property_store_types()
    try:
        store = link.QueryInterface(store_type)
        pv = store.GetValue(ctypes.byref(_app_id_key()))
    except (COMError, OSError, ValueError):
        return ""
    try:
        return ctypes.wstring_at(pv.value) if pv.vt == _VT_LPWSTR and pv.value else ""
    finally:
        ctypes.windll.ole32.PropVariantClear(ctypes.byref(pv))


def _link():
    from comtypes.client import CreateObject
    from comtypes.persist import IPersistFile
    from comtypes.shelllink import IShellLinkW, ShellLink

    link = CreateObject(ShellLink, interface=IShellLinkW)
    return link, link.QueryInterface(IPersistFile)


def _com_error(exc: Exception, what: str, path: Path) -> OSError:
    hr = exc.args[0] if exc.args else 0
    text = exc.args[1] if len(exc.args) > 1 else str(exc)
    hint = " (đường dẫn dài quá 260 ký tự?)" if len(str(path)) >= 260 else ""
    return OSError(f"{what} {path}: {text} (0x{hr & 0xFFFFFFFF:08X}){hint}")


def write_shortcut(path: Path, info: ShortcutInfo) -> None:
    from _ctypes import COMError

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        link, persist = _link()
        link.SetPath(info.target)
        link.SetArguments(info.arguments)
        link.SetWorkingDirectory(info.working_dir)
        link.SetIconLocation(info.icon, info.icon_index)
        link.SetDescription(info.description)
        link.ShowCmd = _SW_SHOWNORMAL
        if info.app_id:
            _set_app_id(link, info.app_id)
        persist.Save(str(path), True)
    except COMError as e:
        raise _com_error(e, "không ghi được", path) from None


def read_shortcut(path: Path) -> ShortcutInfo:
    from _ctypes import COMError

    try:
        link, persist = _link()
        persist.Load(str(path), _STGM_READ)
        icon, index = link.GetIconLocation()
        return ShortcutInfo(target=link.GetPath(_SLGP_RAWPATH), arguments=link.GetArguments(),
                            working_dir=link.GetWorkingDirectory(), icon=icon, icon_index=int(index),
                            description=link.GetDescription(), app_id=_get_app_id(link))
    except COMError as e:
        raise _com_error(e, "không đọc được", path) from None


# --- icon ----------------------------------------------------------------------------------------------

_qt_app = None  # keeps the QGuiApplication made for drawing the icon alive


def ico_bytes(images: list[tuple[int, bytes]]) -> bytes:
    """An .ico file holding PNG images [(size, png bytes)] (Windows Vista+ reads PNG entries)."""
    out = [struct.pack("<HHH", 0, 1, len(images))]
    offset = 6 + 16 * len(images)
    blobs = []
    for size, png in images:
        side = 0 if size >= 256 else size  # 0 means 256
        out.append(struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset))
        offset += len(png)
        blobs.append(png)
    return b"".join(out + blobs)


def icon_images(sizes=ICO_SIZES) -> list[tuple[int, bytes]]:
    """The tray icon (app.ui.tray.icon_pixmap, "running" colour) as PNGs. Needs Qt's windows
    platform for the 文 glyph; a QGuiApplication is created when there is none."""
    global _qt_app
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.instance() is None:
        _qt_app = QGuiApplication([sys.argv[0] if sys.argv else "install_shortcut"])
    from app.ui.tray import icon_pixmap

    out = []
    for size in sizes:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        if not icon_pixmap(size, "running").toImage().save(buf, "PNG"):
            raise OSError(f"could not encode the {size} px icon")
        out.append((size, bytes(buf.data())))
        buf.close()
    return out


def write_icon(path: Path | None = None) -> bool:
    """Draw the .ico (default ICON_PATH); False when the file already has exactly these bytes."""
    path = Path(path or ICON_PATH)
    data = ico_bytes(icon_images())
    try:
        if path.read_bytes() == data:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return True


# --- install / remove ----------------------------------------------------------------------------------

def install(location: str, *, folder: Path | None = None, python: str | Path | None = None,
            icon: Path | None = None) -> tuple[Path, str]:
    """Create or update one shortcut: (its path, "created" | "updated" | "unchanged"). The written shortcut
    is read back: OSError when Windows stored something else (IShellLink silently cuts a working directory
    longer than 259 characters), so "Khởi động cùng Windows" reports it instead of keeping a broken link."""
    path = shortcut_path(location, folder)
    icon = Path(icon or ICON_PATH)
    if not icon.is_file():
        write_icon(icon)
    want = wanted(python, icon)
    existed = path.is_file()
    before = None
    if existed:
        try:
            before = read_shortcut(path)
        except OSError:
            before = None  # unreadable: rewrite it
    if before == want:
        return path, "unchanged"
    write_shortcut(path, want)
    got = read_shortcut(path)
    if got != want:
        bad = [f.name for f in fields(ShortcutInfo) if getattr(got, f.name) != getattr(want, f.name)]
        hint = " (đường dẫn dài quá 260 ký tự?)" if any(len(str(getattr(want, n))) >= 259 for n in bad) else ""
        raise OSError(f"shortcut {path} đọc lại không khớp ({', '.join(bad)}){hint}")
    return path, "updated" if existed else "created"


def remove(location: str, *, folder: Path | None = None) -> bool:
    """Delete one shortcut (only ours: a file of that name that runs something else stays)."""
    path = shortcut_path(location, folder)
    if not path.is_file():
        return False
    try:
        if not read_shortcut(path).runs_this_app():
            return False
    except OSError:
        pass  # unreadable .lnk with our name: ours from a broken write
    path.unlink()
    return True


def is_installed(location: str, *, folder: Path | None = None) -> bool:
    path = shortcut_path(location, folder)
    if not path.is_file():
        return False
    try:
        return read_shortcut(path).runs_this_app()
    except OSError:
        return False


def _an_app_shortcut(path: Path) -> bool:
    """A shortcut of this app, from this project folder or one it was moved from."""
    try:
        info = read_shortcut(path) if path.is_file() else None
    except OSError:
        return False
    return info is not None and Path(info.target).name.lower() == "pythonw.exe" \
        and "run_app.pyw" in info.arguments.lower()


def startup_enabled(folder: Path | None = None) -> bool:
    """A shortcut in the Startup folder starts this app with Windows."""
    return is_installed("startup", folder=folder)


def set_startup(enabled: bool, folder: Path | None = None, python: str | Path | None = None) -> None:
    """Add or remove the Startup shortcut (raises OSError when the folder cannot be written)."""
    if enabled:
        install("startup", folder=folder, python=python)
    else:
        remove("startup", folder=folder)


# --- command line ----------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.install_shortcut",
                                 description="Tạo / gỡ shortcut mở Screen Translator không cần terminal.")
    ap.add_argument("--startup", action="store_true", help="cả shortcut trong thư mục Startup (khởi động cùng Windows)")
    ap.add_argument("--no-desktop", action="store_true", help="không tạo / gỡ shortcut ngoài Desktop")
    ap.add_argument("--no-menu", action="store_true", help="không tạo / gỡ shortcut trong Start Menu")
    ap.add_argument("--remove", action="store_true", help="gỡ các shortcut thay vì tạo")
    ap.add_argument("--check", action="store_true", help="chỉ in các shortcut đang có trỏ tới đâu")
    ap.add_argument("--python", metavar="EXE", help="python.exe / pythonw.exe của venv (mặc định: Python đang chạy)")
    ap.add_argument("--icon", type=Path, default=ICON_PATH, metavar="ICO", help=f"file icon (mặc định {ICON_PATH})")
    ap.add_argument("--redraw-icon", action="store_true",
                    help="vẽ lại file icon từ icon khay (mặc định chỉ vẽ khi chưa có file)")
    for loc in LOCATIONS:
        ap.add_argument(f"--{loc}-dir", type=Path, metavar="DIR", help=f"thư mục thay cho {LABELS[loc]} (để thử)")
    args = ap.parse_args(argv)
    folders = {loc: getattr(args, f"{loc}_dir") for loc in LOCATIONS}
    if args.check:
        chosen = list(LOCATIONS)
    else:
        chosen = [loc for loc in LOCATIONS if (loc == "desktop" and not args.no_desktop)
                  or (loc == "menu" and not args.no_menu) or (loc == "startup" and args.startup)]
        if not args.remove and "startup" not in chosen:
            try:  # "start with Windows" was on (maybe for the old folder): keep it, pointing here
                if _an_app_shortcut(shortcut_path("startup", folders["startup"])):
                    chosen.append("startup")
            except OSError:
                pass
    if not chosen:
        ap.error("không còn shortcut nào để làm (đã bỏ cả Desktop lẫn Start Menu)")
    rc = 0
    if not args.check and not args.remove:
        # assets/app.ico is committed: drawn only when missing (or asked), so a clone's working tree stays clean
        # (the drawing depends on the fonts and Qt version of the machine)
        if args.redraw_icon or not args.icon.is_file():
            changed = write_icon(args.icon)
            print(f"Icon: {args.icon} ({'đã vẽ' if changed else 'không đổi'})")
        else:
            print(f"Icon: {args.icon} (đã có; --redraw-icon để vẽ lại)")
    for loc in chosen:
        try:
            path = shortcut_path(loc, folders[loc])
            if args.check:
                if not path.is_file():
                    print(f"{LABELS[loc]}: chưa có ({path})")
                    continue
                info = read_shortcut(path)
                print(f"{LABELS[loc]}: {path}\n  chạy: {info.target} {info.arguments}\n  thư mục: {info.working_dir}"
                      f"\n  icon: {info.icon},{info.icon_index}\n  AppUserModelID: {info.app_id or '(không có)'}"
                      + ("" if info.runs_this_app() else "\n  (không trỏ tới app trong thư mục này)"))
            elif args.remove:
                print(f"{LABELS[loc]}: {'đã gỡ' if remove(loc, folder=folders[loc]) else 'không có'} {path}")
            else:
                path, status = install(loc, folder=folders[loc], python=args.python, icon=args.icon)
                word = {"created": "đã tạo", "updated": "đã cập nhật", "unchanged": "đã có, không đổi"}[status]
                info = read_shortcut(path)  # read back what Windows stored
                ok = info == wanted(args.python, args.icon)
                print(f"{LABELS[loc]}: {word} {path}" + ("" if ok else "  (đọc lại không khớp!)"))
                rc |= 0 if ok else 1
        except OSError as e:
            print(f"{LABELS[loc]}: lỗi: {e}", file=sys.stderr)
            rc = 1
    if not args.check and not args.remove:
        print(f"Shortcut trỏ tới thư mục {PROJECT_ROOT}; chuyển thư mục hoặc venv thì chạy lại lệnh này "
              "(hoặc bấm đúp “Tạo lại shortcut.cmd” trong thư mục app).")
    return rc


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):  # Vietnamese text into a pipe with a legacy code page
        if _stream is not None and not _stream.isatty() and hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
