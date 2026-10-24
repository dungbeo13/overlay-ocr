"""Which game is this exe? Answered from FILES on disk only, never from the running process.

Read: the exe's version resource (ProductName / FileDescription / CompanyName, through version.dll, which
reads the file), Steam's steamapps/appmanifest_*.acf and libraryfolders.vdf, the folder name and engine
marker files next to the exe (existence checks). Nothing here opens, enumerates or touches a process.

Only `GameIdentity.titles` may later be sent online, after the user agrees; they are filtered so that a
user name, a machine name or a generic folder ("Desktop", "Games", "bin") never becomes one.
"""
from __future__ import annotations

import ctypes
import getpass
import logging
import os
import re
import socket
from ctypes import wintypes
from pathlib import Path

from app.lookup.names import clean_local_title, norm_title
from app.lookup.types import GameIdentity

log = logging.getLogger(__name__)

# Exe names that do not say which game they are: RPG Maker's Game.exe, NW.js, generic launchers, and the
# visual-novel engines whose exe is the engine's own (every Key game runs SiglusEngine.exe). Such an exe is keyed
# and matched together with its game folder ("Summer Pockets/SiglusEngine.exe"), never alone.
GENERIC_EXES = frozenset({
    "game.exe", "start.exe", "launcher.exe", "launch.exe", "play.exe", "run.exe", "app.exe", "main.exe", "nw.exe",
    "player.exe", "client.exe", "setup.exe", "bootstrap.exe", "rpg_rt.exe", "gameapp.exe", "unity.exe",
    "unitycrashhandler64.exe", "start_game.exe", "game_x64.exe", "game64.exe", "win64.exe", "x64.exe",
    "siglusengine.exe", "siglus.exe", "reallive.exe", "bgi.exe", "advhd.exe", "malie.exe", "cmvs32.exe", "cmvs64.exe",
    "tvpwin32.exe", "tvpwin64.exe", "krkr.exe", "krkrz.exe", "krkr64.exe", "nscr.exe", "nscripter.exe",
    "onscripter.exe", "onscripter-en.exe", "artemis.exe", "majiro.exe", "pal.exe", "yuris.exe", "cs2.exe",
    "catsystem2.exe", "ethornell.exe", "livemaker.exe", "tyranoscript.exe", "rpgvxace.exe", "nwjs.exe",
})
# Sub-folders a build puts the exe in: the game's name is the folder above (climbed through, at most 4).
_BUILD_DIRS = frozenset({
    "bin", "bin64", "bin32", "binaries", "win64", "win32", "x64", "x86", "windows", "windowsnoeditor", "release",
    "data", "app", "client", "launcher", "build", "builds", "dist", "system", "exe",
})
# Folders that hold games but are no game's name (never used, never climbed above: the folder above a "Games"
# folder may well be a personal one, "OneDrive - <company>" or "<name>'s documents").
_CONTAINER_DIRS = frozenset({
    "game", "games", "my games", "common", "steamapps", "steam", "steamlibrary", "program files",
    "program files (x86)", "programdata", "desktop", "downloads", "documents", "my documents", "users", "appdata",
    "local", "roaming", "temp", "tmp", "windowsapps", "public", "home", "onedrive", "epic games", "gog games",
    "gog galaxy", "xboxgames", "riot games", "ubisoft game launcher", "ea games", "origin games", "dmmgames", "itch",
    "ゲーム", "げーむ", "游戏", "遊戲", "遊戯", "게임", "trò chơi", "tro choi", "tải về", "tai ve",
})
_GENERIC_DIRS = _BUILD_DIRS | _CONTAINER_DIRS
# Cloud-sync roots: their folder name carries the account / company name ("OneDrive - Contoso Ltd").
_SYNC_ROOTS = ("onedrive", "dropbox", "google drive", "googledrive", "my drive", "icloud", "box sync", "megasync")
# Version-resource values that name an engine or a tool, not the game.
_GENERIC_PRODUCTS = frozenset({
    "unity", "unity player", "unityplayer", "unreal engine", "ue4", "ue5", "bootstrappackagedgame", "ren'py",
    "renpy", "python", "nw.js", "electron", "rpg maker", "rpgmaker", "game", "launcher", "microsoft windows",
    "microsoft® windows® operating system", "kirikiri", "kirikiri z", "tvp(kirikiri) z", "krkrz", "nscripter",
    "onscripter", "godot engine", "godot", "gamemaker", "game maker", "wolf rpg editor", "unknown", "application",
    "siglusengine", "siglus engine", "reallive", "bgi", "buriko general interpreter", "ethornell", "advhd", "malie",
    "artemis", "artemis engine", "majiro", "cmvs", "yu-ris", "yuris", "catsystem2", "tyranoscript", "livemaker",
    "visual novel maker", "unity engine",
})
_STEAM_DEFAULTS = (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam")


# --- version resource (version.dll reads the file's resources; no process involved) ---------------

def _version_strings(path: str) -> dict[str, str]:
    if os.name != "nt" or not path or not os.path.isfile(path):
        return {}
    try:
        ver = ctypes.WinDLL("version", use_last_error=True)
        ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        ver.GetFileVersionInfoW.restype = wintypes.BOOL
        ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wintypes.UINT)]
        ver.VerQueryValueW.restype = wintypes.BOOL
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return {}
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return {}
        ptr, length = ctypes.c_void_p(), wintypes.UINT()
        langs: list[tuple[int, int]] = []
        if ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)) and ptr:
            arr = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD * (length.value // 2))).contents
            langs = [(arr[i], arr[i + 1]) for i in range(0, len(arr) - 1, 2)]
        langs += [(0x0409, 0x04B0), (0x0409, 0x04E4), (0x0411, 0x04B0), (0x0000, 0x04B0)]
        out: dict[str, str] = {}
        for name in ("ProductName", "FileDescription", "CompanyName", "InternalName", "OriginalFilename"):
            for lang, cp in langs:
                block = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\{name}"
                if ver.VerQueryValueW(buf, block, ctypes.byref(ptr), ctypes.byref(length)) and ptr and length.value:
                    value = ctypes.wstring_at(ptr, length.value).rstrip("\x00").strip()
                    if value:
                        out[name] = value
                        break
        return out
    except (OSError, ValueError):
        log.debug("version resource of %s unreadable", path, exc_info=True)
        return {}


# --- Steam files ------------------------------------------------------------------------------

def parse_vdf(text: str) -> dict:
    """Valve's KeyValues text format ("key" "value" / "key" { ... }) as nested dicts."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text or "")
    root: dict = {}
    stack = [root]
    key: str | None = None
    for quoted, brace in tokens:
        if brace == "{":
            child: dict = {}
            if key is not None:
                stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            value = quoted.replace("\\\\", "\\").replace('\\"', '"')
            if key is None:
                key = value
            else:
                stack[-1][key] = value
                key = None
    return root


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def steam_libraries(steam_roots: tuple[str, ...] = _STEAM_DEFAULTS) -> list[Path]:
    """Library folders listed in Steam's libraryfolders.vdf (the default install places only)."""
    libs: list[Path] = []
    for root in steam_roots:
        data = parse_vdf(_read_text(Path(root) / "steamapps" / "libraryfolders.vdf"))
        folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
        for entry in folders.values() if isinstance(folders, dict) else []:
            p = entry.get("path") if isinstance(entry, dict) else entry if isinstance(entry, str) else None
            if p:
                libs.append(Path(p))
        if (Path(root) / "steamapps").is_dir():
            libs.append(Path(root))
    return list(dict.fromkeys(libs))


def _steam_app(exe: Path, steam_roots: tuple[str, ...]) -> tuple[int | None, str, bool]:
    """(app id, name, in a Steam library) from the appmanifest whose installdir holds `exe`."""
    parts = exe.parts  # (..., "steamapps", "common", <installdir>, ..., <exe>)
    lowered = [p.casefold() for p in parts]
    steamapps: Path | None = None
    install: str = ""
    for i in range(len(parts) - 3, 0, -1):
        if lowered[i] == "common" and lowered[i - 1] == "steamapps":
            steamapps, install = Path(*parts[:i]), parts[i + 1]
            break
    if steamapps is None:
        for lib in steam_libraries(steam_roots):
            common = lib / "steamapps" / "common"
            try:
                rel = exe.relative_to(common)
            except ValueError:
                continue
            steamapps, install = lib / "steamapps", rel.parts[0] if len(rel.parts) > 1 else ""
            break
    if steamapps is None or not install:
        return None, "", False
    try:
        manifests = sorted(steamapps.glob("appmanifest_*.acf"))
    except OSError:
        manifests = []
    for path in manifests:
        state = parse_vdf(_read_text(path)).get("AppState") or {}
        if isinstance(state, dict) and str(state.get("installdir", "")).casefold() == install.casefold():
            try:
                appid = int(state.get("appid", "0")) or None
            except ValueError:
                appid = None
            return appid, str(state.get("name", "")).strip(), True
    return None, "", True


# --- engine markers ---------------------------------------------------------------------------

def _engine(folder: Path, stem: str) -> str:
    def has(*names: str) -> bool:
        return any((folder / n).exists() for n in names)

    try:
        if has("UnityPlayer.dll", f"{stem}_Data", "GameAssembly.dll"):
            return "Unity"
        if has("renpy", "lib/py3-windows-x86_64", "lib/py2-windows-x86_64", "lib/windows-x86_64") \
                or (folder / "game").is_dir() and any((folder / "game").glob("*.rpa")):
            return "Ren'Py"
        if has("www/js/rpg_core.js", "js/rmmz_core.js", "www/js/rmmv_core.js", "js/rpg_core.js"):
            return "RPG Maker MV/MZ"
        if has("Game.rgss3a", "Game.rgss2a", "Game.rgssad", "RGSS301.dll", "RGSS104E.dll"):
            return "RPG Maker XP/VX/VX Ace"
        if has("RPG_RT.ldb", "RPG_RT.lmt"):
            return "RPG Maker 2000/2003"
        if any(folder.glob("*.xp3")):
            return "KiriKiri"
        if has("nscript.dat") or any(folder.glob("*.nsa")):
            return "NScripter"
        if any(folder.glob("*.ypf")):
            return "YU-RIS"
        if has("Data.wolf", "Data/BasicData"):
            return "WOLF RPG Editor"
        if has("SiglusEngine.exe", "Gameexe.dat", "Scene.pck"):
            return "SiglusEngine"
        if has("AdvHD.exe"):
            return "AdvHD"
        if has("data.win"):
            return "GameMaker"
        if any(folder.glob("*.pck")):
            return "Godot"
        if folder.name.casefold() == "win64" and folder.parent.name.casefold() == "binaries":
            return "Unreal"
        if has("Engine/Binaries") or (stem.endswith("-Shipping") and (folder.parent / "Engine").is_dir()):
            return "Unreal"
    except OSError:
        pass
    return ""


# --- titles -----------------------------------------------------------------------------------

def _private_words() -> set[str]:
    """User and machine names that must never be sent as a "title"."""
    words: set[str] = set()
    for get in (getpass.getuser, socket.gethostname, lambda: os.environ.get("USERNAME", ""),
                lambda: os.environ.get("COMPUTERNAME", ""), lambda: Path.home().name):
        try:
            w = (get() or "").strip()
        except Exception:  # getpass can raise on odd setups
            w = ""
        if len(w) >= 2:
            words.add(norm_title(w))
    return {w for w in words if w}


def _usable(title: str, private: set[str]) -> bool:
    key = norm_title(title)
    if len(key) < 2 or title.casefold() in _GENERIC_PRODUCTS or key in {norm_title(p) for p in _GENERIC_PRODUCTS}:
        return False
    if title.casefold() in _GENERIC_DIRS:
        return False
    return not any(p and (p == key or (len(p) >= 4 and p in key)) for p in private)


def _game_folder(exe: Path) -> Path:
    """The folder that names the game: the exe's own, or above bin / Binaries/Win64 / x64 ... (build sub-folders
    only: never above a "Games" / "Desktop" folder)."""
    folder = exe.parent
    for _ in range(4):
        if folder.name.casefold() in _BUILD_DIRS and folder.parent != folder:
            folder = folder.parent
        else:
            break
    return folder


def _personal_folder(folder: Path) -> bool:
    """A folder whose name may be personal: the user's profile folder or one right inside it ("C:\\Users\\<name>\\
    <name>'s stuff"), a cloud-sync root ("OneDrive - <company>", "Dropbox (<team>)"), or a drive root."""
    name = folder.name.casefold()
    if not name or folder.parent == folder or name in ("mega", "box"):
        return True
    if name.startswith(_SYNC_ROOTS):
        return True
    try:
        home = Path.home()
        return folder == home or folder.parent == home
    except (RuntimeError, OSError):
        return False


def identify(exe_path: str, *, steam_roots: tuple[str, ...] = _STEAM_DEFAULTS) -> GameIdentity:
    """What the files say about `exe_path` (a full path, or just an exe name: then only the name is used).
    Never raises."""
    raw = str(exe_path or "").strip().strip('"')
    exe = Path(raw)
    ident = GameIdentity(exe_name=exe.name, exe_path=raw if exe.is_absolute() else "")
    ident.generic_exe = exe.name.casefold() in GENERIC_EXES
    stem = exe.stem
    private = _private_words()
    candidates: list[str] = []
    try:
        if exe.is_absolute():
            folder = _game_folder(exe)
            ident.folder_name = folder.name if folder.name.casefold() not in _GENERIC_DIRS \
                and not _personal_folder(folder) else ""
            appid, name, in_lib = _steam_app(exe, steam_roots)
            ident.steam_appid, ident.steam_name, ident.in_steam_library = appid, name, in_lib
            if name:
                candidates.append(name)
                ident.hints.append("steam-manifest")
            elif in_lib:
                ident.hints.append("steam-library")
            info = _version_strings(raw)
            ident.product_name = info.get("ProductName", "")
            ident.file_description = info.get("FileDescription", "")
            ident.company = info.get("CompanyName", "")
            ident.engine = _engine(exe.parent, stem) or (_engine(folder, stem) if folder != exe.parent else "")
            if ident.engine:
                ident.hints.append(f"engine:{ident.engine}")
                if norm_title(stem) in (norm_title(ident.engine), norm_title(ident.engine.split()[0])):
                    ident.generic_exe = True  # the engine's own exe (SiglusEngine.exe, AdvHD.exe): no game name
            for value in (ident.product_name, ident.file_description):
                cleaned = clean_local_title(value)
                if cleaned and cleaned.casefold() != (ident.company or "").casefold():
                    candidates.append(cleaned)
            if ident.folder_name:
                candidates.append(clean_local_title(ident.folder_name))
    except OSError:
        log.debug("identify %s: file checks failed", raw, exc_info=True)
    if not ident.generic_exe:
        candidates.append(clean_local_title(stem))
    seen: set[str] = set()
    for t in candidates:
        key = norm_title(t)
        if t and key not in seen and _usable(t, private):
            seen.add(key)
            ident.titles.append(t)
    return ident


def identity_from_title(title: str, exe_name: str = "") -> GameIdentity:
    """An identity for a title the user typed (no files involved)."""
    ident = GameIdentity(exe_name=os.path.basename(exe_name or ""), titles=[" ".join(title.split())])
    ident.generic_exe = ident.exe_name.casefold() in GENERIC_EXES
    return ident


def identity_from_key(key: str, title: str = "", exe_name: str = "") -> GameIdentity:
    """The identity a LookupState entry stands for (GameIdentity.key round-trips): a game offered before a restart."""
    ident = GameIdentity(exe_name=os.path.basename(exe_name or ""), titles=[" ".join(title.split())] if title else [])
    rest = key[4:] if key.startswith("exe:") else ""
    if "/" in rest:
        folder, exe = rest.rsplit("/", 1)
        ident.folder_name, ident.generic_exe = folder, True
        ident.exe_name = ident.exe_name or exe
    elif rest:
        ident.exe_name = ident.exe_name or rest
    elif key.startswith("steam:") and key[6:].isdigit():
        ident.steam_appid = int(key[6:])
    return ident
