"""Tests for app.lookup (game lookup -> draft glossary profile). No network: the HTTP layer answers from
fixtures recorded from the real APIs on 2026-10-25 (tests/data/lookup/, written by
`python -m tools.lookup_game TITLE --record DIR`, trimmed to the fields the parsers read), and every real
transport is blocked for the whole module.

Run: python -m unittest tests.test_lookup -v
"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

from app.config import PROJECT_ROOT, Config
from app.lookup import claude_request, game_id, names, profile_builder, state, templates
from app.lookup.http import (FixtureRecorder, FixtureTransport, HttpClient, LookupCancelled, LookupHttpError,
                             USER_AGENT, canonical_url, request_key)
from app.lookup.service import LookupService, merge_characters, merge_patches
from app.lookup.sources import mediawiki, steam, vndb, web, wikidata
from app.lookup.types import CharacterCandidate, GameIdentity, PatchLink
from app.profiles import Profile, ProfileStore, profile_from_dict, profile_to_json
from app.translate.contextual import Character, Term

DATA = PROJECT_ROOT / "tests" / "data" / "lookup"
OFFLINE = ("steam", "wikidata", "vndb", "wiki")
_patcher = None


def setUpModule():
    """Any request that is not answered by a test transport fails loudly."""
    global _patcher

    def _no_network(self, request):
        raise AssertionError(f"unit tests must not use the network: {request.url}")
    _patcher = mock.patch.object(httpx.HTTPTransport, "handle_request", _no_network)
    _patcher.start()


def tearDownModule():
    _patcher.stop()


def fixture_service(name: str, **kw) -> tuple[LookupService, FixtureTransport]:
    transport = FixtureTransport(DATA / name)
    client = HttpClient(cache_dir=None, transport=transport)
    kw.setdefault("sources", OFFLINE)
    return LookupService(client=client, **kw), transport


class _Handler:
    """httpx.MockTransport handler recording requests; `answer(request)` -> httpx.Response."""

    def __init__(self, answer):
        self.answer = answer
        self.requests: list[httpx.Request] = []
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests.append(request)
        return self.answer(request)


# --- names -------------------------------------------------------------------------------------

class NamesTests(unittest.TestCase):
    def test_title_keys(self):
        self.assertEqual(names.norm_title("STEINS;GATE"), names.norm_title("Steins;Gate"))
        self.assertEqual(names.norm_title("Doki Doki Literature Club!"), names.norm_title("doki doki literature club"))
        self.assertEqual(names.title_similarity("Steins;Gate", "STEINS;GATE"), 1.0)
        game = names.title_similarity("Doki Doki Literature Club", "Doki Doki Literature Club!")
        ost = names.title_similarity("Doki Doki Literature Club", "Doki Doki Literature Club Plus! Soundtrack")
        self.assertGreater(game, ost)

    def test_clean_local_title(self):
        self.assertEqual(names.clean_local_title("BlueArchive"), "Blue Archive")
        self.assertEqual(names.clean_local_title("DeadByDaylight-Win64-Shipping.exe"), "Dead By Daylight")
        self.assertEqual(names.clean_local_title("My_Game v1.2.3"), "My Game")
        self.assertEqual(names.clean_local_title("STEINS;GATE"), "STEINS;GATE")
        self.assertEqual(names.clean_local_title("x"), "")

    def test_name_helpers(self):
        self.assertEqual(names.pair_parts("牧瀬 紅莉栖", "Makise Kurisu"), [("牧瀬", "Makise"), ("紅莉栖", "Kurisu")])
        self.assertEqual(names.pair_parts("フェイリス・ニャンニャン", "Faris NyanNyan"),
                         [("フェイリス", "Faris"), ("ニャンニャン", "NyanNyan")])
        self.assertEqual(names.pair_parts("セイバー", "Saber"), [])
        self.assertEqual(names.fix_long_vowels("Okabe Rintarō, Yūka"), "Okabe Rintarou, Yuuka")
        self.assertEqual(names.latin_key("Rintaro Okabe"), names.latin_key("Okabe Rintarou"))
        self.assertEqual(names.compact("牧瀬 紅莉栖"), "牧瀬紅莉栖")


# --- files: game identity ------------------------------------------------------------------------

class GameIdTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="st-lookup-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_parse_vdf(self):
        data = game_id.parse_vdf('"AppState"\n{\n\t"appid"\t\t"412830"\n\t"name"\t\t"STEINS;GATE"\n'
                                 '\t"UserConfig"\n\t{\n\t\t"language"\t\t"english"\n\t}\n}\n')
        self.assertEqual(data["AppState"]["appid"], "412830")
        self.assertEqual(data["AppState"]["UserConfig"]["language"], "english")

    def _steam_game(self, install: str, exe: str, appid: str, name: str) -> Path:
        apps = self.dir / "Lib" / "steamapps"
        game = apps / "common" / install
        game.mkdir(parents=True)
        (apps / f"appmanifest_{appid}.acf").write_text(
            f'"AppState"\n{{\n "appid" "{appid}"\n "name" "{name}"\n "installdir" "{install}"\n}}\n', encoding="utf-8")
        path = game / exe
        path.write_bytes(b"MZ")  # not a real PE: no version resource
        return path

    def test_steam_manifest_and_engine(self):
        exe = self._steam_game("STEINS;GATE", "STEINS;GATE.exe", "412830", "STEINS;GATE")
        (exe.parent / "UnityPlayer.dll").write_bytes(b"")
        ident = game_id.identify(str(exe), steam_roots=())
        self.assertEqual(ident.steam_appid, 412830)
        self.assertEqual(ident.steam_name, "STEINS;GATE")
        self.assertEqual(ident.engine, "Unity")
        self.assertEqual(ident.titles[0], "STEINS;GATE")
        self.assertTrue(ident.looks_like_game)
        self.assertEqual(ident.key, "exe:steins;gate.exe")

    def test_unreal_binaries_folder_and_generic_exe(self):
        exe = self.dir / "Games" / "Some Game" / "SomeGame" / "Binaries" / "Win64" / "SomeGame-Win64-Shipping.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"MZ")
        ident = game_id.identify(str(exe), steam_roots=())
        self.assertEqual(ident.engine, "Unreal")
        self.assertEqual(ident.folder_name, "SomeGame")
        self.assertIn("Some Game", ident.titles)
        rpg = self.dir / "RPG Quest" / "Game.exe"
        rpg.parent.mkdir(parents=True)
        rpg.write_bytes(b"MZ")
        (rpg.parent / "Game.rgss3a").write_bytes(b"")
        ident = game_id.identify(str(rpg), steam_roots=())
        self.assertTrue(ident.generic_exe)
        self.assertEqual(ident.engine, "RPG Maker XP/VX/VX Ace")
        self.assertEqual(ident.titles, ["RPG Quest"])  # never "Game"
        self.assertEqual(ident.key, "exe:rpg quest/game.exe")

    def test_private_names_are_never_titles(self):
        exe = self.dir / "alice" / "Desktop" / "alice.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"MZ")
        with mock.patch.object(game_id, "_private_words", return_value={"alice", "mypc"}):
            ident = game_id.identify(str(exe), steam_roots=())
        self.assertEqual(ident.titles, [])
        self.assertFalse(ident.looks_like_game)

    def _exe(self, *parts: str) -> Path:
        exe = self.dir.joinpath(*parts)
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"MZ")
        return exe

    def test_personal_folders_never_become_titles(self):
        """The folder name is a title only when it is the game's own folder: never a folder above "Games" /
        "Desktop", a cloud-sync root or a folder right inside the user's profile."""
        with mock.patch.object(game_id, "_private_words", return_value=set()):
            for parts, want in (
                    (("OneDrive - Contoso Ltd", "Desktop", "Games", "Shooter.exe"), ["Shooter"]),
                    (("OneDrive - Nguyen Van An", "Games", "Game", "Game.exe"), []),
                    (("Tai lieu cua Lan", "Games", "Shooter2.exe"), ["Shooter2"]),
                    (("Dropbox (Team)", "Quest.exe"), ["Quest"]),
                    (("Games", "ゲーム", "MyCoolGame.exe"), ["My Cool Game"]),
                    (("Lib", "Trò chơi", "Tiny.exe"), ["Tiny"])):
                ident = game_id.identify(str(self._exe(*parts)), steam_roots=())
                self.assertEqual(ident.titles, want, parts)
            home = self.dir / "home"
            exe = self._exe("home", "Do cua Lan", "Puzzle.exe")
            with mock.patch.object(game_id.Path, "home", return_value=home):
                ident = game_id.identify(str(exe), steam_roots=())
            self.assertEqual((ident.folder_name, ident.titles), ("", ["Puzzle"]))

    def test_engine_exes_are_generic_and_keyed_with_their_folder(self):
        a = self._exe("DLsite", "Summer Pockets", "SiglusEngine.exe")
        (a.parent / "Gameexe.dat").write_bytes(b"")
        b = self._exe("DLsite", "Little Busters", "SiglusEngine.exe")
        (b.parent / "Gameexe.dat").write_bytes(b"")
        ia, ib = game_id.identify(str(a), steam_roots=()), game_id.identify(str(b), steam_roots=())
        self.assertTrue(ia.generic_exe)
        self.assertEqual(ia.titles, ["Summer Pockets"])  # never "Siglus Engine"
        self.assertEqual((ia.key, ib.key), ("exe:summer pockets/siglusengine.exe", "exe:little busters/siglusengine.exe"))
        back = game_id.identity_from_key(ia.key, ia.title, ia.exe_name)
        self.assertEqual((back.key, back.exe_name, back.titles), (ia.key, "SiglusEngine.exe", ["Summer Pockets"]))
        self.assertEqual(game_id.identity_from_key("exe:sg.exe", "Steins;Gate", "SG.exe").key, "exe:sg.exe")

    def test_exe_name_only_and_missing_file(self):
        ident = game_id.identify("BlueArchive.exe")
        self.assertEqual(ident.titles, ["Blue Archive"])
        self.assertEqual(ident.exe_path, "")
        ident = game_id.identify(str(self.dir / "nope" / "Missing.exe"), steam_roots=())
        self.assertEqual(ident.exe_name, "Missing.exe")


# --- HTTP layer --------------------------------------------------------------------------------

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="st-lookup-http-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_key_ignores_param_order_and_encoding(self):
        a = request_key("GET", "https://x.org/api.php?b=2&a=1")
        b = request_key("GET", "https://X.org/api.php", {"a": "1", "b": 2})
        self.assertEqual(a, b)
        self.assertEqual(canonical_url("https://x.org/p", {"q": "Steins;Gate"}), "https://x.org/p?q=Steins%3BGate")

    def test_user_agent_cache_and_counts(self):
        handler = _Handler(lambda r: httpx.Response(200, json={"ok": True}))
        client = HttpClient(cache_dir=self.dir, transport=httpx.MockTransport(handler))
        s = client.session("steam")
        self.assertEqual(s.get_json("https://store.steampowered.com/api/x", {"a": 1}), {"ok": True})
        self.assertEqual(s.get_json("https://store.steampowered.com/api/x", {"a": 1}), {"ok": True})
        self.assertEqual(len(handler.requests), 1)  # the second answer came from the disk cache
        self.assertEqual(s.counts(), (1, 1))
        ua = handler.requests[0].headers["User-Agent"]
        self.assertIn("github.com/dungbeo13/overlay-ocr", ua)
        self.assertEqual(ua, USER_AGENT)

    def test_errors_deadline_and_cancel(self):
        handler = _Handler(lambda r: httpx.Response(503, text="busy"))
        client = HttpClient(cache_dir=None, transport=httpx.MockTransport(handler))
        with self.assertRaises(LookupHttpError) as ctx:
            client.session("vndb").post_json("https://api.vndb.org/kana/vn", {"a": 1})
        self.assertEqual(ctx.exception.status, 503)
        with self.assertRaises(LookupCancelled):
            client.session("vndb", deadline=time.monotonic() - 1).get_json("https://api.vndb.org/kana/x")
        ev = threading.Event()
        ev.set()
        with self.assertRaises(LookupCancelled):
            client.session("vndb", cancel=ev).get_json("https://api.vndb.org/kana/y")
        self.assertEqual(len(handler.requests), 1)
        not_json = HttpClient(cache_dir=None, transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")))
        with self.assertRaises(LookupHttpError):
            not_json.session("wiki").get_json("https://x.fandom.com/api.php")

    def test_no_cookies_are_kept(self):
        handler = _Handler(lambda r: httpx.Response(200, json={"ok": 1}, headers={"Set-Cookie": "Geo=VN; Path=/"}))
        client = HttpClient(cache_dir=None, transport=httpx.MockTransport(handler))
        s = client.session("wiki")
        s.get_json("https://x.fandom.com/api.php", {"a": 1})
        s.get_json("https://x.fandom.com/api.php", {"a": 2})
        self.assertEqual(len(handler.requests), 2)
        self.assertNotIn("cookie", {k.lower() for k in handler.requests[1].headers})

    def test_api_errors_with_http_200_are_errors_and_not_cached(self):
        answers = [httpx.Response(200, json={"error": {"code": "maxlag", "info": "lagged"}}),
                   httpx.Response(200, json={"entities": {"Q1": {"id": "Q1"}}})]
        handler = _Handler(lambda r: answers.pop(0))
        client = HttpClient(cache_dir=self.dir, transport=httpx.MockTransport(handler))
        s = client.session("wikidata")
        with self.assertRaises(LookupHttpError) as ctx:
            s.get_json("https://www.wikidata.org/w/api.php", {"ids": "Q1"})
        self.assertIn("maxlag", str(ctx.exception))
        self.assertEqual(list(self.dir.glob("*.json")), [])  # never cached
        self.assertEqual(s.get_json("https://www.wikidata.org/w/api.php", {"ids": "Q1"}),
                         {"entities": {"Q1": {"id": "Q1"}}})  # asked again
        self.assertEqual(len(handler.requests), 2)

    def test_recorder_and_fixture_transport_round_trip(self):
        live = HttpClient(cache_dir=None, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"n": 1})),
                          recorder=FixtureRecorder(self.dir, trim=lambda url, data: {**data, "trimmed": True}))
        live.session("vndb").post_json("https://api.vndb.org/kana/vn", {"filters": ["search", "=", "x"]})
        live.session("wd").get_json("https://www.wikidata.org/w/api.php", {"action": "wbgetentities", "ids": "Q1|Q2"})
        self.assertEqual(len(list(self.dir.glob("*.json"))), 2)
        transport = FixtureTransport(self.dir)
        replay = HttpClient(cache_dir=None, transport=transport)
        self.assertEqual(replay.session("vndb").post_json("https://api.vndb.org/kana/vn",
                                                          {"filters": ["search", "=", "x"]}), {"n": 1, "trimmed": True})
        self.assertEqual(replay.session("wd").get_json("https://www.wikidata.org/w/api.php?ids=Q1%7CQ2&action=wbgetentities"),
                         {"n": 1, "trimmed": True})
        with self.assertRaises(LookupHttpError):
            replay.session("wd").get_json("https://www.wikidata.org/w/api.php", {"action": "other"})
        self.assertEqual(len(transport.missing), 1)


# --- source parsers ------------------------------------------------------------------------------

class SourceParserTests(unittest.TestCase):
    def test_steam_languages(self):
        langs, audio = steam.parse_languages("English, Japanese<strong>*</strong>, Vietnamese<br><strong>*</strong>"
                                             "languages with full audio support")
        self.assertEqual(langs, ["English", "Japanese", "Vietnamese"])
        self.assertEqual(audio, ["Japanese"])
        self.assertTrue(steam.SteamInfo(1, languages=langs).vietnamese)

    def test_vndb_aliases_pair_original_with_reading(self):
        pairs = vndb._aliases(["Assistant", "クリス", "Chris", "助手", "Joshu", "まゆしぃ"])
        self.assertEqual(pairs, [("", "Assistant"), ("クリス", "Chris"), ("助手", "Joshu"), ("まゆしぃ", "")])

    def test_vndb_characters_skip_spoilers_and_use_apparent_sex(self):
        data = {"results": [
            {"id": "c1", "name": "Makise Kurisu", "original": "牧瀬 紅莉栖", "aliases": [], "sex": ["f", "f"],
             "vns": [{"id": "v1", "role": "primary", "spoiler": 0}]},
            {"id": "c2", "name": "Hidden One", "original": "隠し", "aliases": [], "sex": ["m", "m"],
             "vns": [{"id": "v1", "role": "side", "spoiler": 2}]},
            {"id": "c3", "name": "Ruka", "original": "るか", "aliases": [], "sex": ["f", "m"],
             "vns": [{"id": "v1", "role": "main", "spoiler": 0}]},
        ]}
        s = mock.Mock()
        s.post_json.return_value = data
        chars = vndb.characters(s, "v1")
        self.assertEqual([c.original for c in chars], ["るか", "牧瀬 紅莉栖"])  # main first, the spoiler one gone
        self.assertEqual(chars[0].gender, "nữ")  # the apparent sex, not the spoiler one
        self.assertEqual(chars[1].parts, [("牧瀬", "Makise"), ("紅莉栖", "Kurisu")])

    def test_wiki_lead_parsing(self):
        page = mediawiki.parse_lead("Faris NyanNyan", "{{Character\n|name = Faris NyanNyan\n"
                                    "|japanese = フェイリス・<br>ニャンニャン\n|romaji = Feirisu<br>NyanNyan\n"
                                    "|gender = Female<ref>x</ref>\n}}")
        self.assertEqual(page.japanese, "フェイリス・ニャンニャン")
        self.assertEqual(page.romaji, "Feirisu NyanNyan")
        self.assertEqual(page.gender, "nữ")
        page = mediawiki.parse_lead("Kurisu", "{{Nihongo|'''Kurisu Makise'''|牧瀬 紅莉栖|Makise Kurisu}} is a girl")
        self.assertEqual((page.japanese, page.romaji), ("牧瀬 紅莉栖", "Makise Kurisu"))
        page = mediawiki.parse_lead("X", "|kanji = [[岡部 倫太郎]]<br>(Okabe)\n|Rōmaji = Okabe Rintarō\n|sex = Male")
        self.assertEqual((page.japanese, page.romaji, page.gender), ("岡部 倫太郎", "Okabe Rintarou", "nam"))

    def test_fixture_trim_keeps_only_what_the_parser_reads(self):
        """`tools.lookup_game --record`: a wiki lead keeps the infobox and {{Nihongo}}, never the prose or a
        {{Quote}}; search snippets are cut short. The committed fixtures are trimmed the same way."""
        from tools.lookup_game import SNIPPET_CHARS, trim_fixture
        lead = ("{{Tabs\n|tab1 = Kurisu\n}}\n{{Character\n|japanese = 牧瀬 紅莉栖\n|gender = Female\n}}"
                "{{Quote|A line from the game.|Chapter 1}}\n{{Quote|Another line.}}\n"
                "{{Nihongo|'''Kurisu Makise'''|牧瀬 紅莉栖|Makise Kurisu}} is a genius girl who ...")
        data = {"query": {"pages": {"1": {"title": "Kurisu", "revisions": [{"slots": {"main": {"*": lead}}}]}}}}
        trim_fixture("https://x.fandom.com/api.php?action=query", data)
        text = data["query"]["pages"]["1"]["revisions"][0]["slots"]["main"]["*"]
        self.assertEqual(text, "{{Tabs\n|tab1 = Kurisu\n}}\n{{Character\n|japanese = 牧瀬 紅莉栖\n|gender = Female\n}}\n"
                               "{{Nihongo|'''Kurisu Makise'''|牧瀬 紅莉栖|Makise Kurisu}}")
        self.assertEqual(mediawiki.parse_lead("Kurisu", text), mediawiki.parse_lead("Kurisu", lead))
        hits = trim_fixture("http://127.0.0.1:8888/search?q=x",
                            {"results": [{"url": "https://a.example/", "title": "A", "content": "x" * 300}]})
        self.assertLessEqual(len(hits["results"][0]["content"]), SNIPPET_CHARS)
        for path in DATA.glob("*/fandom_*.json"):
            self.assertNotRegex(path.read_text(encoding="utf-8"), r"(?i)\{\{\s*quote\b", path.name)

    def test_wiki_discovery_helpers(self):
        wid, arts = mediawiki.pick_fandom_wiki("Steins;Gate", [], ["steins-gate:Kurisu_Makise|hero:Kurisu_Makise",
                                                                    "villains:Moeka_Kiryuu", "steins-gate:Rintaro_Okabe"])
        self.assertEqual((wid, arts), ("steins-gate", ["Kurisu Makise", "Rintaro Okabe"]))
        self.assertEqual(mediawiki.pick_fandom_wiki("Doki Doki Literature Club!", [], ["yandere:Monika_(DDLC)"]), ("", []))
        self.assertEqual(mediawiki.fandom_api("es.doblaje"), "https://doblaje.fandom.com/es/api.php")
        self.assertEqual(mediawiki.fandom_api("bad/../id"), "")
        self.assertEqual(mediawiki.api_from_url("https://megamitensei.fandom.com/wiki/Morgana"),
                         ("https://megamitensei.fandom.com/api.php", "Morgana"))
        self.assertEqual(mediawiki.api_from_url("https://example.com/wiki/X"), ("", ""))
        self.assertEqual(mediawiki.guess_fandom_ids("Danganronpa: Trigger Happy Havoc")[0], "danganronpa")
        site = mediawiki.WikiSite(api="a", name="Danganronpa Wiki")
        self.assertTrue(mediawiki.site_matches("Danganronpa: Trigger Happy Havoc", site))
        self.assertFalse(mediawiki.site_matches("Steins;Gate", site))
        self.assertEqual(mediawiki.franchise_words("Danganronpa: Trigger Happy Havoc", site), ["trigger", "happy", "havoc"])
        self.assertEqual(mediawiki.franchise_words("Steins;Gate", mediawiki.WikiSite(api="a", name="Steins;Gate Wiki")), [])
        # a web hit's wiki of another series, a series wiki for one of its games: not the game's wiki
        self.assertFalse(mediawiki.site_matches("Ever17", mediawiki.WikiSite(api="a", name="Memories Off Wiki")))
        self.assertFalse(mediawiki.site_matches("Persona 5 Royal", mediawiki.WikiSite(api="a", name="Megami Tensei Wiki")))
        two = mediawiki.WikiPage("Angel and Julian", japanese="アンジェラ")
        self.assertIsNone(mediawiki._character(two, "ja", site))  # a page about two characters

    def test_wikidata_entity(self):
        entity = {"id": "Q1", "labels": {"ja": {"value": "テスト"}, "en": {"value": "Test"}},
                  "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q7889"}}}}],
                             "P495": [{"mainsnak": {"datavalue": {"value": {"id": "Q865"}}}},
                                      {"mainsnak": {"datavalue": {"value": {"id": "Q17"}}}}],
                             "P1733": [{"mainsnak": {"datavalue": {"value": "123"}}, "rank": "deprecated"}]}}
        g = wikidata.game_from_entity(entity)
        self.assertTrue(g.is_game)
        self.assertEqual(g.country_language, "ja")  # Japan wins over a co-developer's country
        self.assertEqual(g.steam, [])  # deprecated claims are ignored
        zwj = wikidata._labels({"labels": {"vi": {"value": "Nhà Lữ Hành‍"}}})
        self.assertEqual(zwj, {"vi": "Nhà Lữ Hành"})
        # Wikidata keeps some English names only as the multilingual label (Genshin Impact: ja "原神", mul only)
        mul = wikidata._labels({"labels": {"ja": {"value": "原神"}, "mul": {"value": "Genshin Impact"}}})
        self.assertEqual(mul, {"ja": "原神", "en": "Genshin Impact"})

    def test_wikidata_title_match_must_be_a_game(self):
        """"Persona" matched Q5 "human" (a title search hit that is no game): never the game."""
        def answer(request):
            q = dict(request.url.params)
            if q.get("action") == "wbsearchentities":
                return httpx.Response(200, json={"search": [
                    {"id": "Q5", "label": "persona", "description": "common name of Homo sapiens"},
                    {"id": "Q99", "label": "Persona", "description": ""}]})
            return httpx.Response(200, json={"entities": {"Q99": {
                "id": "Q99", "labels": {"en": {"value": "Persona"}}, "descriptions": {"en": {"value": "Jungian term"}},
                "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q1"}}}}]}}}})
        handler = _Handler(answer)
        s = HttpClient(cache_dir=None, transport=httpx.MockTransport(handler)).session("wikidata")
        self.assertIsNone(wikidata.lookup(s, ["Persona"]))
        searched = [r for r in handler.requests if r.url.params.get("action") == "wbsearchentities"]
        self.assertEqual(len(searched), 1)
        got = [r.url.params.get("ids") for r in handler.requests if r.url.params.get("action") == "wbgetentities"]
        self.assertEqual(got, ["Q99"])  # Q5 was skipped by its description, Q99 by its claims


# --- templates -----------------------------------------------------------------------------------

class TemplateTests(unittest.TestCase):
    def test_blue_archive_by_exe_title_and_steam_id(self):
        for ident in (GameIdentity(exe_name="BlueArchive.exe"), GameIdentity(titles=["ブルーアーカイブ"]),
                      GameIdentity(titles=["Blue Archive"]), GameIdentity(steam_appid=3557620, titles=["x"])):
            match = templates.find_template(ident)
            self.assertIsNotNone(match, ident)
            self.assertEqual(match.profile.name, "Blue Archive")
            self.assertEqual(match.path.name, "blue_archive.json")
        self.assertIsNone(templates.find_template(GameIdentity(exe_name="Game.exe", generic_exe=True, titles=["Foo"])))
        self.assertIsNone(templates.find_template("Steins;Gate"))


# --- the service with recorded fixtures ------------------------------------------------------------

class ServiceFixtureTests(unittest.TestCase):
    def test_steins_gate(self):
        svc, transport = fixture_service("steins_gate")
        seen: list[tuple[str, str]] = []
        report = svc.run("Steins;Gate", progress_cb=lambda st: seen.append((st.name, st.state)))
        self.assertEqual(transport.missing, [])
        self.assertEqual(report.title, "Steins;Gate")
        self.assertEqual(report.src_lang, "ja")
        self.assertEqual(report.confidence, "high")
        self.assertEqual({k: report.ids[k] for k in ("steam", "vndb", "wikidata")},
                         {"steam": "412830", "vndb": "v2002", "wikidata": "Q482494"})
        self.assertEqual(report.ids.get("fandom"), "steins-gate")
        self.assertIs(report.vi_official, False)
        self.assertIn("STEINS;GATE 0", [a.title for a in report.alternatives])
        kurisu = next(c for c in report.characters if c.original == "牧瀬 紅莉栖")
        self.assertEqual((kurisu.romanized, kurisu.gender, kurisu.role), ("Makise Kurisu", "nữ", "primary"))
        self.assertTrue({"vndb", "wikidata", "wiki"} <= set(kurisu.sources))
        self.assertEqual(report.characters[0].original, "岡部 倫太郎")  # the protagonist first
        self.assertLessEqual(len(report.characters), 60)
        # Faris: Wikidata / the wiki call her フェイリス・ニャンニャン, VNDB by her real name: one character
        faris = [c for c in report.characters if "フェイリス・ニャンニャン" in {o for o, _ in c.aliases} | {c.original}]
        self.assertEqual(len(faris), 1)
        # Vietnamese releases from VNDB: links only, community patches
        vndb_patches = [p for p in report.patches if p.source == "vndb"]
        self.assertEqual(len(vndb_patches), 2)
        self.assertTrue(all(not p.official and p.info_url.startswith("https://vndb.org/r") for p in vndb_patches))
        self.assertIn("hako.re", vndb_patches[0].url)
        self.assertEqual({s.name: s.state for s in report.statuses if s.name in OFFLINE},
                         {"steam": "ok", "wikidata": "ok", "vndb": "ok", "wiki": "ok"})
        self.assertEqual(report.status("web").state, "skipped")
        self.assertIn(("vndb", "running"), seen)
        self.assertLessEqual(sum(s.requests for s in report.statuses), 20)  # a small number of requests
        sources = {a["source"] for a in report.attribution}
        self.assertTrue({"Steam", "Wikidata", "VNDB", "Steins;Gate Wiki"} <= sources)
        json.dumps(report.to_dict(), ensure_ascii=False)  # JSON-safe

    def test_only_titles_are_sent(self):
        svc, transport = fixture_service("steins_gate")
        ident = GameIdentity(exe_name="STEINS;GATE.exe", exe_path=r"C:\Users\alice\Games\STEINS;GATE\STEINS;GATE.exe",
                             titles=["Steins;Gate"], folder_name="STEINS;GATE")
        report = svc.run(ident)
        self.assertTrue(report.title)
        for url in transport.seen:
            self.assertNotIn("alice", url)
            self.assertNotIn("Users", url)
            self.assertNotIn(".exe", url.casefold())
        self.assertEqual(report.to_dict()["identity"]["exe_path"], "")

    def test_ddlc_english_game(self):
        svc, transport = fixture_service("ddlc")
        report = svc.run("Doki Doki Literature Club")
        self.assertEqual(transport.missing, [])
        self.assertEqual(report.title, "Doki Doki Literature Club!")
        self.assertEqual(report.src_lang, "en")
        self.assertEqual(report.ids["vndb"], "v21905")
        self.assertEqual(sorted(c.original for c in report.characters), ["Monika", "Natsuki", "Sayori", "Yuri"])
        self.assertTrue(all(not names.is_cjk(o) for c in report.characters for o, _ in c.aliases))
        self.assertEqual(len([p for p in report.patches if "hoshivsub.com" in p.url]), 1)  # http + https: one page

    def test_blue_archive_resolves_to_the_template_without_network(self):
        handler = _Handler(lambda r: httpx.Response(500))
        svc = LookupService(client=HttpClient(cache_dir=None, transport=httpx.MockTransport(handler)))
        for ident in ("Blue Archive", GameIdentity(exe_name="BlueArchive.exe", titles=["Blue Archive"])):
            report = svc.run(ident)
            self.assertTrue(report.template.endswith("blue_archive.json"))
            self.assertEqual(report.title, "Blue Archive")
            self.assertEqual(report.status("template").state, "ok")
        self.assertEqual(handler.requests, [])
        prof = profile_builder.build_profile(report)
        self.assertEqual(prof.name, "Blue Archive")
        self.assertGreaterEqual(len(prof.characters), 100)
        self.assertEqual(prof.lookup["template"], "blue_archive.json")

    def test_a_failing_source_leaves_the_others(self):
        fixtures = FixtureTransport(DATA / "steins_gate")

        def answer(request):
            if "vndb" in request.url.host:
                return httpx.Response(503, json={})
            return fixtures.handle_request(request)
        svc = LookupService(client=HttpClient(cache_dir=None, transport=httpx.MockTransport(answer)), sources=OFFLINE)
        report = svc.run("Steins;Gate")
        self.assertEqual(report.status("vndb").state, "error")
        self.assertIn("503", report.status("vndb").message)
        self.assertEqual(report.status("wikidata").state, "ok")
        self.assertEqual(report.title, "Steins;Gate")
        self.assertTrue(any(c.original == "牧瀬紅莉栖" for c in report.characters))  # from Wikidata
        self.assertEqual([p for p in report.patches if p.source == "vndb"], [])

    def test_budget_and_cancel(self):
        def slow(request):
            time.sleep(1.5)
            return httpx.Response(200, json={})
        svc = LookupService(client=HttpClient(cache_dir=None, transport=httpx.MockTransport(slow)), budget_s=1.0,
                            sources=OFFLINE)
        t0 = time.monotonic()
        report = svc.run("Some Game")
        self.assertLess(time.monotonic() - t0, 2.5)
        self.assertEqual({s.state for s in report.statuses if s.name in ("steam", "wikidata", "vndb")}, {"timeout"})
        self.assertFalse(report.found_anything)
        handler = _Handler(lambda r: httpx.Response(200, json={}))
        svc = LookupService(client=HttpClient(cache_dir=None, transport=httpx.MockTransport(handler)), sources=OFFLINE)
        ev = threading.Event()
        ev.set()
        report = svc.run("Some Game", cancel_event=ev)
        self.assertTrue(report.cancelled)
        self.assertEqual(handler.requests, [])

    def test_cancel_during_a_slow_request_returns_at_once(self):
        """Cancel (closing the window, quitting) does not wait for requests already sent: the jobs run on daemon
        threads, so nothing holds the run (or the app's exit) until the servers answer."""
        def slow(request):
            time.sleep(3.0)
            return httpx.Response(200, json={})
        svc = LookupService(client=HttpClient(cache_dir=None, transport=httpx.MockTransport(slow)), sources=OFFLINE)
        ev = threading.Event()
        threading.Timer(0.3, ev.set).start()
        t0 = time.monotonic()
        report = svc.run("Some Game", cancel_event=ev)
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertTrue(report.cancelled)
        self.assertEqual({s.state for s in report.statuses if s.name in ("steam", "wikidata", "vndb")}, {"cancelled"})
        workers = [t for t in threading.enumerate() if t.name.startswith("lookup-")]
        self.assertTrue(workers and all(t.daemon for t in workers))

    def test_web_sends_nothing_after_a_cancel(self):
        from app.lookup.service import _Run
        from app.lookup.types import LookupReport
        asked: list[str] = []
        ev = threading.Event()

        def search(query, language):
            asked.append(query)
            ev.set()  # the user cancels while the first search runs
            return [web_hit("Steins;Gate Việt hóa", "https://example.vn/sg")]
        svc = LookupService(client=HttpClient(cache_dir=None), web_search=search)
        rep = LookupReport(query="Steins;Gate", identity=GameIdentity(titles=["Steins;Gate"]), title="Steins;Gate")
        r = _Run(svc, rep, ("web", "wiki"), None, ev)
        with self.assertRaises(LookupCancelled):
            svc._web(r, r.session("web"))
        self.assertEqual(asked, ["Steins;Gate Việt hóa"])  # not the wiki search after it
        with self.assertRaises(LookupCancelled):
            svc._web(r, r.session("web"))  # cancelled before: SearXNG is not even started
        self.assertEqual(len(asked), 1)
        # an official Vietnamese version and a known wiki: nothing to search for (Genshin Impact)
        official = LookupReport(query="Genshin Impact", identity=GameIdentity(titles=["Genshin Impact"]),
                                title="Genshin Impact", vi_official=True)
        r = _Run(svc, official, ("web", "wiki"), None, None)
        r.found.wd = wikidata.WikidataGame(qid="Q1", fandom=["genshin-impact"])
        self.assertEqual(svc._web(r, r.session("web"))[0], {})
        self.assertEqual(len(asked), 1)

    def test_an_unsure_match_builds_nothing_until_the_user_picks(self):
        """No source confirms another and no title is exact: the best match is kept, a source that found another game
        becomes an alternative, and no details, web search or wiki are fetched for a derived title."""
        from app.lookup.service import _Run
        from app.lookup.types import LookupReport
        svc = LookupService(client=HttpClient(cache_dir=None, transport=httpx.MockTransport(lambda r: httpx.Response(500))))
        rep = LookupReport(query="Persona", identity=GameIdentity(titles=["Persona"]))
        r = _Run(svc, rep, ("steam", "wikidata", "vndb"), None, None)
        r.found.steam = steam.SteamInfo(appid=2649560, name="Persona 6", score=0.93)
        r.found.wd = wikidata.WikidataGame(qid="Q5", labels={"en": "human"}, score=0.8)
        svc._choose(r)
        self.assertEqual((rep.title, rep.confidence), ("Persona 6", "medium"))
        self.assertIsNone(r.found.wd)  # it named another thing: lends no title or id
        self.assertNotIn("wikidata", rep.ids)
        self.assertIn("human", [a.title for a in rep.alternatives])
        self.assertTrue(any("Chưa chắc" in n for n in rep.notes))
        self.assertIn("Có phải bạn tìm", " ".join(profile_builder.describe(rep)))
        # Wikidata's item found BY Steam's id points back at that id: no confirmation of the title match
        rep = LookupReport(query="Persona", identity=GameIdentity(titles=["Persona"]))
        r = _Run(svc, rep, ("steam", "wikidata", "vndb"), None, None)
        r.found.steam = steam.SteamInfo(appid=2649560, name="Persona 6", score=0.93)
        r.found.wd = wikidata.WikidataGame(qid="Q125405366", labels={"en": "Persona 6"}, steam=["2649560"], score=1.0)
        r.derived["wikidata"] = "steam"
        svc._choose(r)
        self.assertEqual((rep.title, rep.confidence), ("Persona 6", "medium"))  # not "high": wait for the user's pick

    def test_vndb_spoiler_characters_are_not_added_back(self):
        """Wikidata's character list has no spoiler flag: a character VNDB hides as a spoiler is dropped, and
        when VNDB knows the cast the other sources only add details to it."""
        from app.lookup.service import _Run
        from app.lookup.types import LookupReport
        svc = LookupService(client=HttpClient(cache_dir=None))
        rep = LookupReport(query="Summer Pockets", identity=GameIdentity(titles=["Summer Pockets"]), title="Summer Pockets",
                           src_lang="ja", confidence="high")
        r = _Run(svc, rep, OFFLINE, None, None)
        rep.characters = [CharacterCandidate("鷹原 うみ", "Takahara Umi", sources=["vndb"], ids={"vndb": "c1"},
                                             aliases=[("七海", "Nanami"), ("うーみん", "Uumin")]),
                          CharacterCandidate("空門 蒼", "Sorakado Ao", sources=["vndb"], ids={"vndb": "c2"})]
        r.spoilers = [CharacterCandidate("七海", "Nanami", sources=["vndb"], ids={"vndb": "c9"})]
        r.wd_chars = [CharacterCandidate("七海", "Nanami", sources=["wikidata"]),
                      CharacterCandidate("空門藍", "Ai Sorakado", sources=["wikidata"]),
                      CharacterCandidate("空門蒼", "Ao Sorakado", gender="nữ", sources=["wikidata"])]
        svc._merge(r)
        chars = {c.original: c for c in rep.characters}
        self.assertEqual(set(chars), {"鷹原 うみ", "空門 蒼"})  # no Nanami, no Wikidata-only Ai
        self.assertEqual(chars["空門 蒼"].gender, "nữ")  # enriched
        self.assertEqual(chars["鷹原 うみ"].aliases, [("うーみん", "Uumin")])  # the alias naming a spoiler is gone

    def test_nothing_found(self):
        def empty(request):
            if "storesearch" in request.url.path:
                return httpx.Response(200, json={"total": 0, "items": []})
            if "vndb" in request.url.host:
                return httpx.Response(200, json={"results": [], "more": False})
            return httpx.Response(200, json={"search": [], "query": {"search": []}})
        svc = LookupService(client=HttpClient(cache_dir=None, transport=httpx.MockTransport(empty)), sources=OFFLINE)
        report = svc.run("Qwzx Unknown Game")
        self.assertEqual(report.title, "")
        self.assertFalse(report.found_anything)
        self.assertEqual({s.state for s in report.statuses if s.name in ("steam", "wikidata", "vndb")}, {"empty"})
        self.assertIn("Không tìm thấy", " ".join(profile_builder.describe(report)))

    def test_web_hits_from_searxng(self):
        recorded = json.loads((DATA / "web_hits.json").read_text(encoding="utf-8"))["queries"]
        asked: list[tuple[str, str]] = []

        def search(query, language):
            asked.append((query, language))
            return [web_hit(**h) for h in recorded.get(f"{query}|{language}", [])]
        patches = web.vietnamese_patches(search, "Persona 5 Royal")
        self.assertTrue(patches)
        self.assertTrue(all(p.source == "web" and not p.verified for p in patches))
        self.assertTrue(all("persona" in (p.title + p.url).casefold() for p in patches))
        self.assertFalse(any("youtube.com" in p.url for p in patches))
        self.assertIn("https://megamitensei.fandom.com/api.php", web.discover_wikis(search, "Persona 5 Royal"))
        self.assertEqual(asked[0], ("Persona 5 Royal Việt hóa", "vi"))
        self.assertLessEqual(len(patches), web.MAX_PATCH_HITS)
        self.assertEqual(len({web.url_key(p.url) for p in patches}), len(patches))  # voz page 1 and page 2: one
        self.assertTrue(all(p.note.startswith("tìm web: ") for p in patches))

    def test_web_patch_filter(self):
        """Only a page whose title says it is about a Vietnamese patch of THIS game (live hits of 2026-10-25)."""
        ok = web.patch_hit
        self.assertTrue(ok("Persona 5 Royal", "[PC/SWITCH] Persona 5 Royal Việt hóa | VOZ", "https://voz.vn/t/p5r.970306/"))
        self.assertTrue(ok("Steins;Gate", "Steins;Gate Việt hóa hoàn tất", "https://hako.re/x"))
        for title, name, url in (
                ("Summer Pockets", "Tải về game Summer Pockets + Việt Hóa miễn phí", "https://linkneverdie.net/x"),
                ("Fate/stay night", "[ Fshare | Mega | Drive ] Fate/stay night Việt Hóa", "https://viethoapc.blogspot.com/x"),
                ("NEKOPARA Vol. 1", "[18+ Việt Hóa] Nekopara Vol.1", "https://mod18.com/x"),
                ("Steins;Gate", "Tải bản Việt Hóa game Steins;Gate", "https://thinkmay.net/x"),
                ("Ever17", "The Visual Novel Database – Wikipedia tiếng Việt", "https://vi.wikipedia.org/wiki/x"),
                ("Fate/stay night", "Fate/stay night vietsub", "https://animevn.net/x"),
                ("Higurashi When They Cry", "SILENT HILL f Việt Hóa: when they cry", "https://x.vn/shf"),
                ("Steins;Gate", "STEINS;GATE RE:BOOT Việt hóa", "https://x.vn/reboot"),
                ("Steins;Gate", "Steins;Gate 0 Việt hóa", "https://x.vn/zero"),
                ("NEKOPARA Vol. 1", "Nekopara Vol. 4 Việt hóa", "https://x.vn/vol4"),
                ("Doki Doki Literature Club", "Doki Doki Literature Club Việt hóa", "https://dammesangtao.com/store/x"),
                ("Genshin Impact", "Font Việt hóa Genshin Impact", "https://inviva.vn/x")):
            self.assertFalse(ok(title, name, url), name)
        self.assertEqual(web.url_key("http://www.baka-tsuki.org/project/index.php?title=Clannad%7EVietnamese%7E"),
                         web.url_key("https://baka-tsuki.org/project/index.php?title=Clannad~Vietnamese~"))
        self.assertEqual(web.url_key("https://voz.vn/t/x.1/page-2"), web.url_key("https://voz.vn/t/x.1/"))

    def test_public_searxng_only_when_none_is_set_up(self):
        """The consent window names the SearXNG that is set up: a title never goes to a public one instead."""
        from app.lookup import searxng
        dead = mock.Mock(url="", managed=True)
        dead.ensure_running.return_value = None
        dead.is_installed.return_value = True
        with mock.patch.object(searxng, "PUBLIC_INSTANCES", ["https://public.example"]), \
                mock.patch.object(searxng, "probe", return_value=True):
            self.assertEqual(web.resolve_base_url(service=dead, public_fallback=True)[0], "")
            self.assertEqual(web.resolve_base_url(url="", public_fallback=True), ("https://public.example", "public"))
            self.assertEqual(web.resolve_base_url(url="http://192.0.2.10:8080", public_fallback=True),
                             ("http://192.0.2.10:8080", "external"))
            missing = mock.Mock(url="", managed=True)
            missing.ensure_running.return_value = None
            missing.is_installed.return_value = False
            self.assertEqual(web.resolve_base_url(service=missing, public_fallback=True)[1], "public")


def web_hit(title: str, url: str, content: str = "", engine: str = ""):
    return mock.Mock(title=title, url=url, content=content, engine=engine)


class MergeTests(unittest.TestCase):
    def test_merge_by_alias_and_script_preference(self):
        vn = CharacterCandidate("秋葉 留未穂", "Akiha Rumiho", aliases=[("", "Faris NyanNyan"), ("フェイリス", "Feyris")],
                                sources=["vndb"], role="primary", native=True)
        wd = CharacterCandidate("フェイリス・ニャンニャン", "Faris Nyannyan", gender="nữ", sources=["wikidata"])
        out = merge_characters([vn], [wd])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].original, "秋葉 留未穂")
        self.assertEqual(out[0].gender, "nữ")
        self.assertIn(("フェイリス・ニャンニャン", "Faris Nyannyan"), out[0].aliases)
        en_vndb = CharacterCandidate("Monika", "Monika", sources=["vndb"])
        en_wd = CharacterCandidate("モニカ", "Monika", gender="nữ", sources=["wikidata"])
        self.assertEqual(merge_characters([en_vndb], [en_wd], prefer_cjk=False)[0].original, "Monika")
        self.assertEqual(merge_characters([en_vndb], [en_wd], prefer_cjk=True)[0].original, "モニカ")

    def test_enrich_only_does_not_add(self):
        base = [CharacterCandidate("牧瀬 紅莉栖", "Makise Kurisu", sources=["vndb"])]
        wiki = [CharacterCandidate("牧瀬紅莉栖", "Makise Kurisu", gender="nữ", sources=["wiki"]),
                CharacterCandidate("比屋定真帆", "Hiyajo Maho", sources=["wiki"])]
        out = merge_characters(base, enrich=wiki)
        self.assertEqual([c.original for c in out], ["牧瀬 紅莉栖"])
        self.assertEqual(out[0].gender, "nữ")

    def test_patches_dedupe(self):
        ps = merge_patches([PatchLink("a", "http://x.vn/p/", "web", verified=False), PatchLink("a", "https://www.x.vn/p", "vndb"),
                            PatchLink("b", "https://y.vn", "vndb", official=True)])
        self.assertEqual([p.url for p in ps], ["https://y.vn", "http://x.vn/p/"])

    def test_title_words_do_not_make_two_characters(self):
        vn = CharacterCandidate("ドクター中鉢", "Nakabachi", sources=["vndb"])
        wd = CharacterCandidate("中鉢博士", "Doctor Nakabachi", sources=["wikidata"])
        self.assertEqual(len(merge_characters([vn], [wd])), 1)
        self.assertEqual(len(merge_characters([CharacterCandidate("中鉢", "Nakabachi")],
                                              [CharacterCandidate("中島", "Nakajima")])), 2)


# --- draft profile ------------------------------------------------------------------------------

class BuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        svc, _ = fixture_service("steins_gate")
        cls.sg = svc.run(GameIdentity(exe_name="STEINS;GATE.exe", titles=["Steins;Gate"]))
        svc, _ = fixture_service("ddlc")
        cls.ddlc = svc.run("Doki Doki Literature Club")

    def test_steins_gate_draft_follows_the_template_conventions(self):
        prof = profile_builder.build_profile(self.sg)
        self.assertEqual(prof.name, "Steins;Gate")
        self.assertEqual(prof.processes, ["STEINS;GATE.exe"])
        self.assertEqual(prof.src_lang, "ja")
        chars = {c.name: c for c in prof.characters}
        kurisu = chars["牧瀬紅莉栖"]
        self.assertEqual((kurisu.name_vi, kurisu.gender, kurisu.self_pronoun), ("Makise Kurisu", "nữ", ""))
        self.assertEqual(kurisu.notes, "Nhân vật chính.")
        self.assertEqual(chars["岡部倫太郎"].name_vi, "Okabe Rintarou")
        self.assertTrue(all(c.self_pronoun == "" for c in prof.characters))
        terms = {t.source: t for t in prof.terms}
        self.assertEqual((terms["牧瀬"].target, terms["牧瀬"].kind, terms["牧瀬"].auto), ("Makise", "person", False))
        self.assertEqual(terms["紅莉栖"].target, "Kurisu")
        self.assertNotIn("至", terms)  # one kanji: also an ordinary word
        self.assertTrue(terms["るか"].auto)  # two kana: also inside するか, only a suggestion
        self.assertFalse(terms["まゆり"].auto)  # the name box Mayuri's lines show
        self.assertFalse(any(t.note == "biệt danh" for t in prof.terms))  # nicknames only on request (spoilers)
        self.assertIn("Akiha Yukitaka", terms["秋葉"].note)  # a family name two characters share
        nick = {t.source: t for t in profile_builder.build_profile(self.sg, include_aliases=True).terms}
        self.assertTrue(nick["オカリン"].auto)
        self.assertEqual(nick["ダル"].target, "Daru")
        self.assertNotIn("Okabe", nick["オカリン"].note)  # a nickname never says whose
        for spoiler in ("ジョン・タイター", "バレル・タイター", "牧瀬章一", "鳳凰院凶真"):
            self.assertNotIn(spoiler, nick)  # full names / true identities, a name sharing Kurisu's family name
        self.assertNotIn("助手", nick)  # a common word as an alias
        meta = prof.lookup
        self.assertEqual(meta["ids"]["vndb"], "v2002")
        self.assertEqual(len(meta["patches"]), 2)
        self.assertNotIn("秋葉", meta["aliases"])
        self.assertEqual(meta["aliases"]["紅莉栖"], "牧瀬紅莉栖")
        self.assertEqual(set(meta["review"]["characters"]), set(chars))
        self.assertEqual(set(meta["review"]["terms"]), set(terms))
        pending_c, pending_t = profile_builder.pending_review(prof)
        self.assertEqual(len(pending_c), len(prof.characters))
        reviewed = profile_builder.mark_reviewed(prof, characters=["牧瀬紅莉栖"], terms=["牧瀬"])
        self.assertEqual(len(profile_builder.pending_review(reviewed)[0]), len(prof.characters) - 1)
        self.assertEqual(profile_builder.pending_review(profile_builder.mark_reviewed(prof, everything=True)),
                         (set(), set()))
        given = profile_builder.build_profile(self.sg, name_order="given_first")
        self.assertEqual({c.name: c.name_vi for c in given.characters}["牧瀬紅莉栖"], "Kurisu Makise")

    def test_english_game_draft(self):
        prof = profile_builder.build_profile(self.ddlc)
        self.assertEqual(prof.src_lang, "en")
        self.assertEqual({(c.name, c.name_vi, c.gender) for c in prof.characters},
                         {("Monika", "Monika", "nữ"), ("Sayori", "Sayori", "nữ"), ("Yuri", "Yuri", "nữ"),
                          ("Natsuki", "Natsuki", "nữ")})
        self.assertFalse(any(names.is_cjk(t.source) for t in prof.terms))

    def test_draft_round_trips_through_the_store(self):
        prof = profile_builder.build_profile(self.sg)
        tmp = Path(tempfile.mkdtemp(prefix="st-lookup-store-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        store = ProfileStore(tmp, rescan_s=0.0)
        store.save(prof)
        loaded = store.get("Steins;Gate")
        self.assertEqual(loaded.lookup, prof.lookup)
        self.assertEqual(loaded.characters, prof.characters)
        self.assertEqual(loaded.terms, prof.terms)
        # An editor that does not know about `lookup` saves a Profile without it: the metadata stays.
        edited = Profile(name=loaded.name, processes=loaded.processes, src_lang=loaded.src_lang, style="giọng teen",
                         terms=loaded.terms, characters=loaded.characters)
        store.save(edited)
        self.assertEqual(store.get("Steins;Gate").lookup, prof.lookup)
        self.assertEqual(store.get("Steins;Gate").style, "giọng teen")

    def test_merge_into_keeps_the_users_entries(self):
        draft = profile_builder.build_profile(self.sg)
        mine = Profile(name="Steins;Gate", processes=["sg.exe"], src_lang=None, style="giữ -san",
                       terms=[Term("牧瀬", "Makise-san", "person", "của tôi")],
                       characters=[Character("牧瀬紅莉栖", "Kurisu", "nữ", "tôi", "trợ lý")],
                       rejected=["オカリン"])
        merged, stats = profile_builder.merge_into(mine, draft)
        self.assertEqual(merged.characters[0], mine.characters[0])
        self.assertEqual(next(t for t in merged.terms if t.source == "牧瀬"), mine.terms[0])
        self.assertNotIn("オカリン", [t.source for t in merged.terms])  # the user deleted it once
        self.assertIn("岡部倫太郎", stats.added_characters)
        self.assertIn("牧瀬紅莉栖", stats.kept_characters)
        self.assertEqual(merged.style, "giữ -san")
        self.assertIsNone(merged.src_lang)  # None on an existing profile: "keep the app's language setting"
        self.assertEqual(profile_builder.merge_into(Profile("Steins;Gate"), draft, new=True)[0].src_lang, "ja")
        self.assertEqual(merged.processes, ["sg.exe", "STEINS;GATE.exe"])
        self.assertNotIn("牧瀬紅莉栖", merged.lookup["review"]["characters"])  # not added, not to review
        self.assertIn("岡部倫太郎", merged.lookup["review"]["characters"])
        again, stats2 = profile_builder.merge_into(merged, draft)
        self.assertEqual(stats2.added, 0)
        self.assertEqual(len(again.terms), len(merged.terms))

    def test_the_default_profile_never_gets_a_games_exe_or_language(self):
        draft = profile_builder.build_profile(self.sg)
        merged, _ = profile_builder.merge_into(Profile("default"), draft, new=True)
        self.assertEqual((merged.processes, merged.src_lang), ([], None))

    def test_merge_survives_odd_lookup_blocks(self):
        odd = Profile("X", characters=[Character("セラ", "Sera")],
                      lookup={"aliases": ["Okarin"], "ids": ["vndb", "v1"], "sources": "https://vndb.org/v1",
                              "patches": {"url": "x"}, "review": "all"})
        merged, stats = profile_builder.merge_into(Profile("X", lookup={"ids": "bad", "review": []}), odd)
        self.assertEqual(stats.added_characters, ["セラ"])
        self.assertNotIn("aliases", merged.lookup)
        self.assertEqual(merged.lookup.get("sources"), None)

    def test_names_and_title_rules(self):
        from app.lookup.types import GameCandidate, LookupReport
        boy = CharacterCandidate("少年", "Shounen", sources=["vndb"])
        self.assertEqual(profile_builder.display_name(boy), "Cậu bé")
        doc = CharacterCandidate("中鉢博士", "Doctor Nakabachi", sources=["vndb"])
        self.assertEqual(profile_builder.display_name(doc), "Tiến sĩ Nakabachi")
        trav = CharacterCandidate("旅人", "Traveler", label_vi="Nhà Lữ Hành", sources=["wikidata"])
        self.assertEqual(profile_builder.display_name(trav, prefer_vi=True), "Nhà Lữ Hành")  # official Vietnamese
        self.assertEqual(profile_builder.display_name(trav), "Traveler")
        rep = LookupReport(query="Ever17", title="Ever 17: The Out of Infinity",
                           candidates=[GameCandidate("vndb", "Ever17 -the out of infinity-", 0.8)])
        self.assertEqual(profile_builder.profile_title(rep), "Ever17")
        rep = LookupReport(query="P5R", title="Persona 5 Royal", candidates=[GameCandidate("steam", "Persona 5 Royal", 1)])
        self.assertEqual(profile_builder.profile_title(rep), "Persona 5 Royal")

    def test_nickname_filter(self):
        chars = [CharacterCandidate("衛宮 士郎", "Emiya Shirou", parts=[("衛宮", "Emiya"), ("士郎", "Shirou")]),
                 CharacterCandidate("アーチャー", "Archer", aliases=[("エミヤ", "Emiya"), ("アーチャーさん", "Archer-san")]),
                 CharacterCandidate("鷹原 うみ", "Takahara Umi", aliases=[("うーみん", "Uumin"), ("鷹原羽未", "Takahara Umi")]),
                 CharacterCandidate("倉成 武", "Kuranari Takeshi", aliases=[("ショウネン", "Shounen")]),
                 CharacterCandidate("少年", "Shounen", aliases=[("ショウネン", "Shounen")])]
        safe = profile_builder.nickname_aliases(chars)
        self.assertEqual(safe.get(1), [("アーチャーさん", "Archer-san")])  # エミヤ romanizes to Shirou's family name
        self.assertEqual(safe.get(2), [("うーみん", "Uumin")])  # a full kanji name: dropped
        self.assertNotIn(3, safe)  # an alias two characters share
        self.assertNotIn(4, safe)

    def test_derive_aliases_links_name_parts(self):
        prof = Profile("SG", characters=[Character("牧瀬紅莉栖", "Makise Kurisu"), Character("椎名まゆり", "Shiina Mayuri")],
                       terms=[Term("紅莉栖", "Kurisu", "person"), Term("牧瀬", "Makise", "person"),
                              Term("秋葉原", "Akihabara", "place"), Term("まゆしぃ", "Mayushii", "person", auto=True)])
        self.assertEqual(profile_builder.derive_aliases(prof), {"紅莉栖": "牧瀬紅莉栖", "牧瀬": "牧瀬紅莉栖"})


# --- profiles.py: lookup metadata ------------------------------------------------------------------

class ProfileLookupFieldTests(unittest.TestCase):
    def test_round_trip_and_version(self):
        p = Profile(name="G", terms=[Term("ア", "A")], characters=[Character("セラ", "Sera", "nữ")],
                    lookup={"ids": {"vndb": "v1"}, "patches": [{"title": "t", "url": "https://x"}], "vi_official": False})
        text = profile_to_json(p)
        self.assertIn('"lookup"', text)
        back = profile_from_dict(json.loads(text))
        self.assertEqual(back.lookup, p.lookup)
        self.assertEqual(back.version(), Profile(name="G", terms=p.terms, characters=p.characters).version())
        c = p.copy()
        c.lookup["ids"]["vndb"] = "v2"
        self.assertEqual(p.lookup["ids"]["vndb"], "v1")  # copies are deep

    def test_profiles_without_lookup_are_written_as_before(self):
        p = Profile(name="G")
        self.assertNotIn("lookup", profile_to_json(p))
        self.assertIsNone(profile_from_dict({"name": "G", "lookup": "not an object"}).lookup)
        template = json.loads((PROJECT_ROOT / "profiles" / "templates" / "blue_archive.json").read_text(encoding="utf-8"))
        self.assertIsNone(profile_from_dict(template).lookup)

    def test_name_part_links_reach_the_prompt(self):
        """A name box showing only 紅莉栖 is the character 牧瀬紅莉栖: her gender reaches the pronoun rules."""
        from app.translate import llm_prompt
        from app.translate.contextual import ScreenItem
        p = Profile(name="SG", characters=[Character("牧瀬紅莉栖", "Makise Kurisu", "nữ")],
                    terms=[Term("紅莉栖", "Kurisu", "person")],
                    lookup={"aliases": {"紅莉栖": "牧瀬紅莉栖", "岡部": "岡部倫太郎"}})  # Okabe is no character here
        ctx = p.to_context()
        self.assertEqual(ctx.aliases, (("紅莉栖", "牧瀬紅莉栖"),))
        names = llm_prompt._Names(ctx)
        self.assertEqual(names.chars[llm_prompt._key("紅莉栖")].gender, "nữ")
        self.assertEqual(names.to_vi("紅莉栖"), "Kurisu")  # the part keeps its own rendering
        items = (ScreenItem(1, "紅莉栖", role_hint="name"), ScreenItem(2, "何よ", role_hint="dialog"))
        self.assertEqual(llm_prompt.screen_speakers(items, ctx)[2], "牧瀬紅莉栖")
        plain = Profile(name="SG", characters=p.characters, terms=p.terms)
        self.assertNotEqual(p.version(), plain.version())  # the links change speakers: part of the cache key
        self.assertEqual(plain.version(), Profile(name="SG", characters=p.characters, terms=p.terms, lookup={"x": 1}).version())

    def test_folder_qualified_process_entries(self):
        from app.profiles import exe_matches
        entry = "Summer Pockets/SiglusEngine.exe"
        self.assertTrue(exe_matches(r"D:\DLsite\Summer Pockets\SiglusEngine.exe", entry))
        self.assertFalse(exe_matches(r"D:\DLsite\Little Busters\SiglusEngine.exe", entry))
        self.assertFalse(exe_matches("SiglusEngine.exe", entry))  # a bare name cannot tell the games apart
        self.assertTrue(exe_matches(r"D:\Games\STEINS;GATE\STEINS;GATE.exe", "STEINS;GATE.exe"))


# --- Claude request / import ------------------------------------------------------------------------

class ClaudeRequestTests(unittest.TestCase):
    def test_request_lists_the_format_and_what_is_known(self):
        svc, _ = fixture_service("steins_gate")
        report = svc.run(GameIdentity(exe_name="STEINS;GATE.exe", titles=["Steins;Gate"]))
        draft = profile_builder.build_profile(report)
        text = claude_request.build_request(report, profile=draft)
        self.assertIn("Steins;Gate", text)
        self.assertIn("`STEINS;GATE.exe`", text)
        self.assertIn("```json", text)
        self.assertIn("牧瀬紅莉栖", text)  # what the app already has
        self.assertIn("spoiler", text)
        self.assertIn('"self_pronoun": ""', text)
        plain = claude_request.build_request("Some Game", src_lang="en")
        self.assertIn("tiếng Anh", plain)
        self.assertNotIn("App đã có sẵn", plain)

    def test_parse_import_accepts_chat_answers(self):
        body = {"name": "Game X", "src_lang": "ja", "terms": [{"source": "学園", "target": "Học viện", "kind": "place"}],
                "characters": [{"name": "セラ", "name_vi": "Sera", "gender": "nữ"}],
                "lookup": {"sources": ["https://vndb.org/v1"],
                           "patches": [{"title": "VH", "url": "https://vh.vn/x"}, {"title": "bad", "url": "file:///c:/x"}]}}
        raw = json.dumps(body, ensure_ascii=False)
        chat = f"Đây là hồ sơ:\n\n```json\n{json.dumps(body, ensure_ascii=False, indent=2)}\n```\nChúc chơi vui!"
        trailing = raw.replace('"gender": "nữ"}', '"gender": "nữ",}')
        wrapped = json.dumps({"profile": body}, ensure_ascii=False)
        for text in (raw, chat, trailing, wrapped, "Kết quả: " + raw + " (hết)"):
            prof = claude_request.parse_import(text)
            self.assertEqual(prof.name, "Game X")
            self.assertEqual([c.name for c in prof.characters], ["セラ"])
            self.assertEqual(prof.terms[0].target, "Học viện")
            self.assertEqual([p["url"] for p in prof.lookup["patches"]], ["https://vh.vn/x"])
            self.assertFalse(prof.lookup["patches"][0]["verified"])
            self.assertIn("セラ", prof.lookup["review"]["characters"])
            self.assertEqual(prof.lookup["sources"][-1], "assistant")
        for bad in ("", "không có gì", '{"name": "x"}', '{"terms": [], "characters": []}'):
            with self.assertRaises(ValueError):
                claude_request.parse_import(bad)

    def test_parse_import_odd_prose_and_lookup_shapes(self):
        body = {"name": "Steins;Gate", "src_lang": "ja",
                "characters": [{"name": "牧瀬紅莉栖", "name_vi": "Makise Kurisu", "gender": "nữ"}],
                "terms": [{"source": "紅莉栖", "target": "Kurisu", "kind": "person"}],
                "lookup": {"aliases": ["Okarin"], "ids": ["vndb", "v2002"], "sources": "https://vndb.org/v2002",
                           "patches": [{"title": 1, "url": "https://vh.vn/x", "official": "yes"}], "vi_official": "no"}}
        raw = json.dumps(body, ensure_ascii=False)
        for text in (f'Hồ sơ cho "Steins;Gate (bản 2009): {raw}', f"tên 'Okarin\" là biệt danh. {raw}"):
            prof = claude_request.parse_import(text)
            meta = prof.lookup
            self.assertEqual(meta["sources"], ["assistant"])  # a string is not split into letters
            self.assertNotIn("ids", meta)
            self.assertNotIn("vi_official", meta)
            self.assertEqual(meta["patches"], [{"url": "https://vh.vn/x", "source": "assistant", "verified": False}])
            self.assertEqual(meta["aliases"], {"紅莉栖": "牧瀬紅莉栖"})  # derived from the "person" term
            merged, stats = profile_builder.merge_into(Profile("Steins;Gate"), prof)  # never raises
            self.assertEqual(stats.added_characters, ["牧瀬紅莉栖"])

    def test_request_lists_no_suggestions(self):
        prof = Profile("SG", characters=[Character("牧瀬紅莉栖", "Makise Kurisu")],
                       terms=[Term("牧瀬", "Makise", "person"), Term("ジョン・タイター", "John Titor", "person", auto=True)])
        text = claude_request.build_request("Steins;Gate", profile=prof)
        self.assertIn("Thuật ngữ 牧瀬 → Makise", text)
        self.assertNotIn("ジョン・タイター", text)  # a suggestion (maybe a spoiler) is not "what the app has"

    def test_import_file_with_bom(self):
        tmp = Path(tempfile.mkdtemp(prefix="st-lookup-imp-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / "ho-so.json"
        path.write_bytes("\ufeff".encode("utf-8") + json.dumps({"terms": {"セラ": "Sera"}}, ensure_ascii=False).encode("utf-8"))
        prof = claude_request.import_file(path)
        self.assertEqual(prof.name, "ho-so")
        self.assertEqual(prof.terms[0].source, "セラ")
        self.assertEqual(claude_request.romanized_only(Profile("x", src_lang="ja", characters=[Character("Sera", "Sera")])),
                         ["Sera"])


# --- state / config ---------------------------------------------------------------------------------

class StateTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="st-lookup-state-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_asked_once_per_game(self):
        path = self.dir / "lookup_state.json"
        st = state.LookupState(path)
        self.assertTrue(st.should_ask("exe:sg.exe"))
        st.mark("exe:sg.exe", "asked", title="Steins;Gate", exe="sg.exe")
        self.assertFalse(st.should_ask("exe:sg.exe"))
        st.mark("exe:sg.exe", "done", profile="Steins;Gate")
        again = state.LookupState(path)  # persisted
        g = again.get("exe:sg.exe")
        self.assertEqual((g.state, g.title, g.profile), ("done", "Steins;Gate", "Steins;Gate"))
        again.mark("exe:other.exe", "failed")
        self.assertFalse(again.should_ask("exe:other.exe"))
        self.assertTrue(again.should_ask("exe:other.exe", now=time.time() + state.RETRY_FAILED_S + 1))
        again.forget("exe:sg.exe")
        self.assertTrue(again.should_ask("exe:sg.exe"))
        with self.assertRaises(ValueError):
            again.mark("x", "maybe")

    def test_offered_games_are_listed_again(self):
        path = self.dir / "lookup_state.json"
        st = state.LookupState(path)
        st.mark("exe:a.exe", "offered", title="A", exe="A.exe")
        st.mark("exe:b.exe", "offered", title="B", exe="B.exe")
        st.mark("exe:b.exe", "asked")  # the user opened the question
        self.assertFalse(st.should_ask("exe:a.exe"))  # no second balloon
        again = state.LookupState(path)
        self.assertEqual([(k, g.title) for k, g in again.offered()], [("exe:a.exe", "A")])

    def test_broken_file_is_kept_aside(self):
        path = self.dir / "lookup_state.json"
        path.write_text("{not json", encoding="utf-8")
        st = state.LookupState(path)
        self.assertTrue(st.should_ask("exe:a.exe"))
        self.assertTrue((self.dir / "lookup_state.json.bad").exists())
        st.mark("exe:a.exe", "declined")
        self.assertEqual(state.LookupState(path).get("exe:a.exe").state, "declined")


class ConfigTests(unittest.TestCase):
    def test_lookup_fields(self):
        cfg = Config()
        self.assertTrue(cfg.lookup_ask_new_games)
        self.assertEqual(cfg.lookup_sources, ["steam", "wikidata", "vndb", "wiki", "web"])
        self.assertEqual((cfg.searxng_url, cfg.searxng_managed, cfg.searxng_public_fallback), ("", True, False))
        tmp = Path(tempfile.mkdtemp(prefix="st-lookup-cfg-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / "config.json"
        path.write_text(json.dumps({"lookup_sources": ["vndb"], "searxng_url": "http://127.0.0.1:8888"}), encoding="utf-8")
        cfg = Config.load(path)
        self.assertEqual(cfg.lookup_sources, ["vndb"])
        svc = LookupService.from_config(cfg, client=HttpClient(cache_dir=None))
        self.assertEqual(svc.sources, ("vndb",))
        self.assertEqual(svc.searxng_url, "http://127.0.0.1:8888")


if __name__ == "__main__":
    unittest.main()
