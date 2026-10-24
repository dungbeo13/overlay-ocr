"""Game lookup: when a new game is chosen, look its community data up ONCE (after the user agrees) and turn
it into a glossary profile like profiles/templates/blue_archive.json.

    ident = identify(exe_path)                        # files only: version resource, Steam manifest, folder
    report = LookupService.from_config(cfg).run(ident, progress_cb=..., cancel_event=...)   # worker thread
    draft = build_profile(report)                      # Profile with lookup metadata ("review" marks)
    merged, stats = merge_into(store.get(name), draft)  # never overwrites the user's entries

Sources: local templates (no network), Steam store API, Wikidata, VNDB Kana API, game wikis (MediaWiki),
web hits through SearXNG (app.lookup.searxng). Only game titles are sent. Nothing is downloaded or
installed: Vietnamese patches are links. No Qt anywhere in this package.

Names are imported lazily, so `import app.lookup.searxng` does not load the rest.
"""
from __future__ import annotations

import importlib

_EXPORTS = {
    "identify": "game_id", "identity_from_title": "game_id",
    "LookupService": "service", "LookupState": "state", "find_template": "templates",
    "build_profile": "profile_builder", "merge_into": "profile_builder", "MergeStats": "profile_builder",
    "describe": "profile_builder", "pending_review": "profile_builder", "mark_reviewed": "profile_builder",
    "build_request": "claude_request", "parse_import": "claude_request", "import_file": "claude_request",
    "romanized_only": "claude_request",
    "GameIdentity": "types", "GameCandidate": "types", "CharacterCandidate": "types", "TermCandidate": "types",
    "PatchLink": "types", "SourceStatus": "types", "LookupReport": "types", "SOURCES": "types",
    "SOURCE_TITLES": "types",
}
__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'app.lookup' has no attribute {name!r}")
    value = getattr(importlib.import_module(f"app.lookup.{module}"), name)
    globals()[name] = value
    return value
