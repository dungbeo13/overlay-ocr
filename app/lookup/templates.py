"""Hand-made profiles shipped with the app (profiles/templates/*.json), matched BEFORE any network: by the
exe (the template's `processes`), the Steam app id or the title (its name plus TEMPLATE_ALIASES)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import PROJECT_ROOT
from app.lookup.names import norm_title
from app.lookup.types import GameIdentity
from app.profiles import Profile, exe_matches, profile_from_dict

log = logging.getLogger(__name__)

TEMPLATES_DIR = PROJECT_ROOT / "profiles" / "templates"

# Other names a template's game goes by (file stem -> titles / Steam app ids). Matched by norm_title.
TEMPLATE_ALIASES: dict[str, dict[str, tuple]] = {
    "blue_archive": {
        "titles": ("Blue Archive", "ブルーアーカイブ", "ブルーアーカイブ -Blue Archive-", "ブルアカ", "블루 아카이브",
                   "蔚蓝档案", "碧藍檔案", "BlueArchive"),
        "steam": (3557620,),
    },
}


@dataclass
class TemplateMatch:
    path: Path
    profile: Profile
    reason: str  # "exe" | "steam" | "title"


def _load(path: Path) -> Profile | None:
    try:
        return profile_from_dict(json.loads(path.read_text(encoding="utf-8-sig")), path.stem)
    except (OSError, ValueError, RecursionError):
        log.warning("template %s is not a valid profile", path, exc_info=True)
        return None


def list_templates(directory: Path = TEMPLATES_DIR) -> list[tuple[Path, Profile]]:
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return []
    return [(p, prof) for p in paths if (prof := _load(p)) is not None]


def find_template(identity: GameIdentity | str, directory: Path = TEMPLATES_DIR) -> TemplateMatch | None:
    """The template for this game (exe first, then Steam id, then title), None when there is none."""
    ident = identity if isinstance(identity, GameIdentity) else GameIdentity(titles=[str(identity)])
    templates = list_templates(directory)
    if ident.exe_name and not ident.generic_exe:
        for path, prof in templates:
            if any(exe_matches(ident.exe_name, entry) for entry in prof.processes):
                return TemplateMatch(path, prof, "exe")
    for path, prof in templates:
        alias = TEMPLATE_ALIASES.get(path.stem, {})
        if ident.steam_appid and ident.steam_appid in alias.get("steam", ()):
            return TemplateMatch(path, prof, "steam")
    keys = {norm_title(t) for t in ident.titles} - {""}
    if ident.steam_name:
        keys.add(norm_title(ident.steam_name))
    for path, prof in templates:
        names = {norm_title(prof.name), *(norm_title(t) for t in TEMPLATE_ALIASES.get(path.stem, {}).get("titles", ()))}
        if keys & names:
            return TemplateMatch(path, prof, "title")
    return None
