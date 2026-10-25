# Third-party data in tests/data/lookup

The files in this folder are answers of public APIs, recorded on 2026-10-25 (`python -m tools.lookup_game TITLE --record DIR`
and a local SearXNG instance) and trimmed to what the parsers read. They are kept only as offline test fixtures. They are
**not** covered by the MIT licence of this project's own code (`LICENSE` at the repository root): each file keeps the
licence or status of its source.

| Files | Source | Licence / status |
|---|---|---|
| `steins_gate/fandom_*.json`, `ddlc/fandom_*.json` | Wikitext of the lead sections of pages on [steins-gate.fandom.com](https://steins-gate.fandom.com) and [doki-doki-literature-club.fandom.com](https://doki-doki-literature-club.fandom.com). The exact API request (wiki and page titles) is in each file's `request.url`. | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Authors: the contributors listed in each page's history on that wiki. **Modified:** cut to the infobox parameters and `{{Nihongo}}` templates (lead prose, quotes and other templates removed) by `trim_fixture` in `tools/lookup_game.py`. These excerpts stay under CC BY-SA 3.0. |
| `*/vndb_*.json` | [vndb.org](https://vndb.org) (The Visual Novel Database, Kana API) | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/); data © the vndb.org contributors |
| `*/wikidata_*.json` | [www.wikidata.org](https://www.wikidata.org) | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `*/steam_*.json` | store.steampowered.com (storesearch, appdetails) | Valve's store data (names, IDs, languages, developer / publisher, release date), kept for the tests only; no rights are granted by this repository |
| `searxng_search_*.json`, `web_hits.json` | Search results from a local SearXNG instance (Google CSE, DuckDuckGo, Brave) | Titles and URLs of public web pages; the page snippets (`content`) are cut to 60 characters |
| `searxng_config.json` | The `/config` answer of that local SearXNG instance | Lists of engine and category names |

Game titles, character names and trademarks belong to their respective owners. This project is not affiliated with them.
