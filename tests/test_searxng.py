"""SearXNG client and local-instance manager (app/lookup/searxng.py), and tools/setup_searxng.py helpers.

No network: search() / probe() talk to fake servers on 127.0.0.1 or a mocked client fed with trimmed answers
recorded from the real local instance (tests/data/lookup/searxng_*.json, 2026-10-25). SearxngService runs a
fake server script that reads SEARXNG_PORT / SEARXNG_BIND_ADDRESS from its environment, as SearXNG does.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import httpx
import psutil

from app.config import PROJECT_ROOT
from app.llm.server import free_port
from app.lookup import searxng as sx
from tools import setup_searxng as setup

DATA = Path(__file__).parent / "data" / "lookup"
HAVE_YAML = importlib.util.find_spec("yaml") is not None


def _fixture(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def _gone(pids: list[int], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(psutil.pid_exists(p) for p in pids):
            return True
        time.sleep(0.05)
    return False


def _port_closes(port: int, timeout: float = 3.0) -> bool:
    """The kernel releases a killed server's listening socket a moment after the process is gone."""
    deadline = time.monotonic() + timeout
    while not sx.port_free(port):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def _response(status: int, *, json_body=None, text: str = "", ctype: str = "") -> httpx.Response:
    req = httpx.Request("GET", "http://127.0.0.1:9/search")
    if json_body is not None:
        return httpx.Response(status, json=json_body, request=req)
    return httpx.Response(status, text=text, headers={"content-type": ctype or "text/html"}, request=req)


# --- a fake SearXNG inside this process (for search() / probe() over real HTTP) ------------------------------------
class _FakeHandler(BaseHTTPRequestHandler):
    routes: dict = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        parts = urlsplit(self.path)
        self.server.seen.append((parts.path, parse_qs(parts.query), dict(self.headers)))
        status, ctype, body = self.server.routes.get(parts.path, (404, "text/plain", b"not found"))
        if callable(body):
            status, ctype, body = body(parse_qs(parts.query))
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(test: unittest.TestCase, routes: dict) -> tuple[str, ThreadingHTTPServer]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHandler)
    srv.routes, srv.seen = routes, []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    test.addCleanup(srv.server_close)
    test.addCleanup(srv.shutdown)
    return f"http://127.0.0.1:{srv.server_address[1]}", srv


def _json(obj) -> tuple[int, str, bytes]:
    return 200, "application/json", json.dumps(obj).encode("utf-8")


def _searxng_routes(search_body=None) -> dict:
    return {
        "/healthz": (200, "text/plain", b"OK"),
        "/config": _json(_fixture("searxng_config.json")),
        "/search": search_body or _json(_fixture("searxng_search_blue_archive_viet_hoa.json")),
    }


class TestSearch(unittest.TestCase):
    def test_parses_a_recorded_answer(self):
        client = mock.Mock()
        client.get.return_value = _response(200, json_body=_fixture("searxng_search_blue_archive_viet_hoa.json"))
        hits = sx.search("http://127.0.0.1:8888/", "Blue Archive Việt hóa", language="vi", client=client)
        self.assertEqual(len(hits), 6)
        first = hits[0]
        self.assertIsInstance(first, sx.SearchHit)
        self.assertEqual(first.url, "https://www.lagofast.com/vi/blog/play-blue-archive-in-vietnamese/")
        self.assertEqual(first.title, "Cách dịch trò chơi Blue Archive sang tiếng Việt - LagoFast")
        self.assertEqual(first.engine, "google cse")
        self.assertTrue(first.content.startswith("LagoFast giúp bạn"))
        self.assertTrue(any("reddit.com/r/BlueArchive" in h.url for h in hits))
        (url,), kw = client.get.call_args
        self.assertEqual(url, "http://127.0.0.1:8888/search")
        self.assertEqual(kw["params"], {"q": "Blue Archive Việt hóa", "format": "json", "language": "vi"})
        self.assertEqual(kw["headers"]["Accept"], "application/json")
        self.assertIn("github.com/dungbeo13/overlay-ocr", kw["headers"]["User-Agent"])
        client.close.assert_not_called()  # the caller's client is the caller's

    def test_japanese_answer_and_partial_engine_failure(self):
        client = mock.Mock()
        client.get.return_value = _response(200, json_body=_fixture("searxng_search_steins_gate_ja.json"))
        with self.assertLogs("app.lookup.searxng", "INFO") as logs:
            hits = sx.search("http://127.0.0.1:8888", "Steins;Gate キャラクター", language="ja", client=client)
        self.assertTrue(hits)
        self.assertTrue(any("STEINS;GATE" in h.title for h in hits))
        self.assertIn("brave", "\n".join(logs.output))  # an engine without an answer is only logged

    def test_dedupes_and_filters(self):
        body = {"results": [
            {"url": "https://a.example/x", "title": "  A \n title ", "content": "c\t1", "engines": ["bing"]},
            {"url": "https://a.example/x", "title": "duplicate"},
            {"url": "javascript:alert(1)", "title": "bad"},
            {"url": "ftp://files.example/", "title": "not http"},
            {"title": "no url"},
            "not a dict",
            {"url": "http://b.example/", "engine": "duckduckgo"},
        ]}
        client = mock.Mock()
        client.get.return_value = _response(200, json_body=body)
        hits = sx.search("http://127.0.0.1:1", "q", client=client)
        self.assertEqual(hits, [sx.SearchHit("A title", "https://a.example/x", "c 1", "bing"),
                                sx.SearchHit("http://b.example/", "http://b.example/", "", "duckduckgo")])
        self.assertEqual(client.get.call_args.kwargs["params"]["language"], "all")

    def test_errors(self):
        cases = [
            (_response(403, text="Forbidden"), sx.SearxngRefused, "JSON output disabled"),
            (_response(429, text="Too Many Requests"), sx.SearxngRefused, "rate-limited"),
            (_response(500, text="boom"), sx.SearxngError, "HTTP 500"),
            (_response(200, text="<html>challenge</html>"), sx.SearxngError, "not JSON"),
            (_response(200, json_body={"answers": []}), sx.SearxngError, "without a results list"),
            (_response(200, json_body=[1, 2]), sx.SearxngError, "without a results list"),
            (_response(200, json_body={"results": [], "unresponsive_engines": [["google", "CAPTCHA"], ["brave"]]}),
             sx.SearxngError, "no engine answered: google (CAPTCHA), brave"),
        ]
        for resp, exc, text in cases:
            with self.subTest(status=resp.status_code, text=text):
                client = mock.Mock()
                client.get.return_value = resp
                with self.assertRaises(exc) as cm:
                    sx.search("http://127.0.0.1:1", "q", client=client)
                self.assertIn(text, str(cm.exception))
                self.assertEqual(cm.exception.status, resp.status_code)
        with self.assertRaises(sx.SearxngError) as cm:
            sx.search("http://127.0.0.1:1", "q", client=mock.Mock(get=mock.Mock(side_effect=httpx.ConnectError("x"))))
        self.assertNotIsInstance(cm.exception, sx.SearxngRefused)
        self.assertIn("ConnectError", str(cm.exception))
        self.assertEqual(sx.search("http://127.0.0.1:1", "q", client=mock.Mock(
            get=mock.Mock(return_value=_response(200, json_body={"results": []})))), [])
        with self.assertRaises(ValueError):
            sx.search("file:///etc/passwd", "q", client=mock.Mock())

    def test_over_http_and_probe(self):
        base, srv = _serve(self, _searxng_routes())
        self.assertTrue(sx.probe(base))
        hits = sx.search(base, "Blue Archive Việt hóa", language="vi", timeout=5)
        self.assertEqual(len(hits), 6)
        path, query, headers = srv.seen[-1]
        self.assertEqual(path, "/search")
        self.assertEqual(query["format"], ["json"])
        self.assertEqual(query["q"], ["Blue Archive Việt hóa"])
        self.assertTrue(headers["User-Agent"].startswith("screen-translator/"))
        self.assertEqual({p for p, _, _ in srv.seen}, {"/healthz", "/config", "/search"})

    def test_probe_rejects_other_servers(self):
        base, _ = _serve(self, {"/healthz": (200, "text/plain", b"OK"), "/config": (200, "text/html", b"<html>")})
        self.assertFalse(sx.probe(base))
        base2, _ = _serve(self, {"/healthz": (200, "text/plain", b"OK"), "/config": _json({"name": "something"})})
        self.assertFalse(sx.probe(base2))
        base3, _ = _serve(self, {})  # 404 everywhere
        self.assertFalse(sx.probe(base3))
        self.assertFalse(sx.probe(""))
        self.assertFalse(sx.probe("not a url"))
        t0 = time.monotonic()
        self.assertFalse(sx.probe(f"http://127.0.0.1:{free_port()}"))  # nothing listens
        self.assertLess(time.monotonic() - t0, 1.5)  # no ~2 s refused-connect wait on Windows

    def test_refused_over_http(self):
        base, _ = _serve(self, _searxng_routes((403, "text/html", b"<h1>Forbidden</h1>")))
        with self.assertRaises(sx.SearxngRefused):
            sx.search(base, "q", timeout=5)


class TestHelpers(unittest.TestCase):
    def test_loopback(self):
        for url in ("http://127.0.0.1:8888", "http://localhost:1", "http://127.0.0.2", "http://[::1]:8080/"):
            self.assertTrue(sx._is_loopback(url), url)
        for url in ("https://searx.example", "http://10.0.0.5:8888", "http://0.0.0.0:8888", "http://[::]:1"):
            self.assertFalse(sx._is_loopback(url), url)

    def test_child_env(self):
        home = Path(tempfile.gettempdir()) / "sx-home"
        leaked = {"SEARXNG_SECRET": "", "SEARXNG_BIND_ADDRESS": "0.0.0.0", "PYTHONPATH": "C:/elsewhere",
                  "PYTHONHOME": "C:/py", "VIRTUAL_ENV": "C:/venv", "__PYVENV_LAUNCHER__": "C:/x/python.exe",
                  "SEARXNG_LIMITER": "true", "KEEP_ME": "1"}
        with mock.patch.dict(os.environ, leaked):
            env = sx.child_env(home, 4321)
        for k in ("SEARXNG_SECRET", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
            self.assertNotIn(k, env)
        self.assertEqual(env["SEARXNG_BIND_ADDRESS"], "127.0.0.1")
        self.assertEqual(env["SEARXNG_PORT"], "4321")
        self.assertEqual(env["SEARXNG_SETTINGS_PATH"], str(home / "settings.yml"))
        for k in ("SEARXNG_LIMITER", "SEARXNG_PUBLIC_INSTANCE", "SEARXNG_IMAGE_PROXY", "SEARXNG_DEBUG"):
            self.assertEqual(env[k], "false")
        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(env["KEEP_ME"], "1")
        self.assertIn("PATH", {k.upper() for k in env})

    def test_installed(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            self.assertFalse(sx.installed(home))
            for rel in ("venv/Scripts/python.exe", "settings.yml", "src/searx/webapp.py"):
                (home / rel).parent.mkdir(parents=True, exist_ok=True)
                (home / rel).write_text("x")
            self.assertFalse(sx.installed(home))  # no install.json: the install did not finish
            (home / "install.json").write_text(json.dumps({"commit": "abc", "port": 8890}))
            self.assertTrue(sx.installed(home))
            self.assertEqual(sx.SearxngService(home=home)._preferred_port(), 8890)
            self.assertEqual(sx.SearxngService("http://127.0.0.1:9999", home=home)._preferred_port(), 9999)
            self.assertEqual(sx.SearxngService("https://searx.example", home=home)._preferred_port(), 8890)
            (home / "install.json").write_text("{broken")
            self.assertFalse(sx.installed(home))

    def test_public_instances_and_texts(self):
        for url in sx.PUBLIC_INSTANCES:
            self.assertTrue(url.startswith("https://") and not url.endswith("/"), url)
        self.assertEqual(set(sx.STATUS_TEXT), {"missing", "off", "starting", "running", "external", "error"})
        self.assertIn("tools.setup_searxng", sx.setup_command())

    def test_port_free(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            self.assertFalse(sx.port_free(s.getsockname()[1]))
        self.assertTrue(sx.port_free(free_port()))


# --- SearxngService with a fake SearXNG process -------------------------------------------------------------------
FAKE_SEARXNG = textwrap.dedent('''
    """Fake SearXNG: binds SEARXNG_BIND_ADDRESS:SEARXNG_PORT like searx.webapp; /healthz, /config, /search."""
    import argparse, json, os, sys, time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit

    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--exit", type=int)
    ap.add_argument("--env-dump")
    a = ap.parse_args()
    if a.env_dump:
        with open(a.env_dump, "w", encoding="utf-8") as fh:
            json.dump(dict(os.environ), fh)
    host, port = os.environ["SEARXNG_BIND_ADDRESS"], int(os.environ["SEARXNG_PORT"])
    print(f"fake searxng starting on {host}:{port}", flush=True)
    if a.exit is not None:
        print("fake fatal: settings.yml is broken", flush=True)
        sys.exit(a.exit)
    t0 = time.monotonic()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlsplit(self.path)
            if u.path == "/healthz":
                ready = time.monotonic() - t0 >= a.delay
                self.reply(200 if ready else 503, "OK" if ready else "starting")
            elif u.path == "/config":
                self.reply(200, {"instance_name": "fake", "version": "0", "engines": []})
            elif u.path == "/search":
                q = parse_qs(u.query)
                if q.get("format") != ["json"]:
                    self.reply(403, {})
                else:
                    self.reply(200, {"query": q["q"][0], "results": [
                        {"url": "https://example.org/" + str(port), "title": "hit for " + q["q"][0],
                         "engine": "fake"}]})
            else:
                self.reply(404, {})

    ThreadingHTTPServer((host, port), H).serve_forever()
''')

_CRASH_OWNER = textwrap.dedent('''
    import sys, time
    from pathlib import Path
    from app.lookup import searxng as sx
    fake, home, log = sys.argv[1:4]
    svc = sx.SearxngService(home=Path(home), log_path=Path(log), command=[sys.executable, fake])
    url = svc.ensure_running(30)
    print(url, *svc.pids(), flush=True)
    time.sleep(60)
''')


class TestService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="test-searxng-")
        self.addCleanup(self._rmtree)
        self.home = Path(self.tmp.name)
        self.fake = self.home / "fake_searxng.py"
        self.fake.write_text(FAKE_SEARXNG, encoding="utf-8")
        self.log = self.home / "searxng.log"
        self.port = free_port()  # never the user's 8888
        (self.home / "install.json").write_text(json.dumps({"commit": "test", "port": self.port}))

    def _rmtree(self):
        for _ in range(50):
            try:
                self.tmp.cleanup()
                return
            except PermissionError:  # a just-exited process's log can stay locked for a moment
                time.sleep(0.1)

    def service(self, *args: str, **kw) -> sx.SearxngService:
        kw.setdefault("home", self.home)
        kw.setdefault("log_path", self.log)
        svc = sx.SearxngService(command=[sys.executable, str(self.fake), *args], **kw)
        self.addCleanup(svc.close)
        return svc

    def test_start_search_stop_restart(self):
        dump = self.home / "env.json"
        svc = self.service("--delay", "0.3", "--env-dump", str(dump))
        self.assertEqual(svc.status(), "off")
        with mock.patch.dict(os.environ, {"SEARXNG_SECRET": "", "SEARXNG_BIND_ADDRESS": "0.0.0.0"}):
            t0 = time.monotonic()
            url = svc.ensure_running(30)
        self.assertEqual(url, f"http://127.0.0.1:{self.port}")  # install.json's port
        self.assertGreaterEqual(time.monotonic() - t0, 0.3)  # waited for /healthz 200, not just the port
        self.assertEqual(svc.status(), "running")
        self.assertEqual(svc.status_text(), sx.STATUS_TEXT["running"])
        env = json.loads(dump.read_text(encoding="utf-8"))
        self.assertEqual(env["SEARXNG_BIND_ADDRESS"], "127.0.0.1")
        self.assertEqual(env["SEARXNG_PORT"], str(self.port))
        self.assertNotIn("SEARXNG_SECRET", env)
        pids = svc.pids()
        self.assertGreaterEqual(len(pids), 2)  # venv launcher + interpreter
        addrs = sx.listening_addresses(pids)
        self.assertEqual({ip for ip, _ in addrs}, {"127.0.0.1"})
        self.assertEqual({p for _, p in addrs}, {self.port})
        self.assertGreater(svc.memory_mb(), 1)
        hits = svc.search("Steins;Gate")
        self.assertEqual(hits, [sx.SearchHit("hit for Steins;Gate", f"https://example.org/{self.port}", "", "fake")])
        self.assertEqual(svc.ensure_running(), url)  # idempotent: same process
        self.assertEqual(svc.pids(), pids)
        self.assertIn("fake searxng starting on 127.0.0.1", svc.log_tail())
        svc.stop()
        self.assertTrue(_gone(pids), "SearXNG processes still alive after stop()")
        self.assertEqual(svc.status(), "off")
        self.assertIsNone(svc.base_url)
        self.assertTrue(_port_closes(self.port))
        svc.stop()  # idempotent
        self.assertEqual(svc.ensure_running(30), url)  # starts again on demand
        self.assertNotEqual(svc.pids(), pids)
        self.assertTrue((self.log.with_name(self.log.name + ".1")).is_file())  # previous log kept

    def test_reuses_a_running_instance(self):
        ours = self.service()
        url = ours.ensure_running(30)
        other = sx.SearxngService(url=url + "/", managed=True, home=self.home / "nothing-installed")
        self.addCleanup(other.close)
        self.assertEqual(other.ensure_running(), url)
        self.assertEqual(other.status(), "external")
        self.assertEqual(other.pids(), [])  # no second server
        self.assertEqual(len(other.search("x")), 1)
        other.stop()  # not ours: left running
        self.assertEqual(ours.status(), "running")
        self.assertTrue(sx.probe(url))
        # an unmanaged service whose URL does not answer
        lonely = sx.SearxngService(url=f"http://127.0.0.1:{free_port()}", managed=False, home=self.home)
        self.assertIsNone(lonely.ensure_running())
        self.assertEqual(lonely.status(), "off")
        self.assertIn("Không có SearXNG", lonely.status_text())
        with self.assertRaises(sx.SearxngError):
            lonely.search("x")

    def test_missing_install(self):
        svc = sx.SearxngService(home=self.home / "empty")
        self.assertEqual(svc.status(), "missing")
        self.assertIsNone(svc.ensure_running())
        self.assertEqual(svc.status(), "missing")
        self.assertEqual(sx.SearxngService(managed=False, home=self.home).status(), "missing")

    def test_exit_while_starting(self):
        svc = self.service("--exit", "3")
        with self.assertLogs("app.lookup.searxng", "WARNING"):
            self.assertIsNone(svc.ensure_running(30))
        self.assertEqual(svc.status(), "error")
        self.assertIn("exited with code 3", svc.last_error)
        self.assertIn("fake fatal: settings.yml is broken", svc.last_error)
        self.assertIsNone(svc.pid)

    def test_timeout_and_stop_aborts_start(self):
        svc = self.service("--delay", "60")
        with self.assertLogs("app.lookup.searxng", "INFO") as logs:
            self.assertIsNone(svc.ensure_running(timeout=1))
            self.assertIn("not ready after", svc.last_error)
            self.assertIsNone(svc.pid)
            result = []
            th = threading.Thread(target=lambda: result.append(svc.ensure_running(30)))
            th.start()
            deadline = time.monotonic() + 10
            while svc.pid is None and time.monotonic() < deadline:
                time.sleep(0.02)
            pids = svc.pids()
            t0 = time.monotonic()
            svc.stop()
            th.join(10)
        self.assertIn("INFO:app.lookup.searxng:SearXNG start stopped", "\n".join(logs.output))
        self.assertIn("stop() was called", "\n".join(logs.output))
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual(result, [None])
        self.assertTrue(_gone(pids))
        self.assertEqual(svc.status(), "off")  # a deliberate stop is no "could not start"
        self.assertEqual(svc.last_error, "")

    def test_idle_stop(self):
        svc = self.service(idle_stop_s=0.4)
        svc.ensure_running(30)
        pids = svc.pids()
        deadline = time.monotonic() + 10
        while svc.status() == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(svc.status(), "off")
        self.assertTrue(_gone(pids))
        self.assertIsNotNone(svc.ensure_running(30))  # back on demand

    def test_close_refuses_later_starts(self):
        svc = self.service()
        svc.ensure_running(30)
        pids = svc.pids()
        svc.close()
        self.assertTrue(_gone(pids))
        self.assertIsNone(svc.ensure_running())

    def test_busy_port_uses_another(self):
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", self.port))
            blocker.listen(1)
            svc = self.service()
            url = svc.ensure_running(30)
            self.assertIsNotNone(url)
            self.assertNotEqual(url, f"http://127.0.0.1:{self.port}")
            self.assertTrue(url.startswith("http://127.0.0.1:"))

    def test_non_loopback_listener_is_stopped(self):
        svc = self.service()
        with mock.patch.object(sx, "listening_addresses", return_value=[("0.0.0.0", self.port)]), \
                self.assertLogs("app.lookup.searxng", "WARNING"):
            self.assertIsNone(svc.ensure_running(30))
        self.assertIn("non-loopback", svc.last_error)
        self.assertIsNone(svc.pid)
        self.assertTrue(_port_closes(self.port))

    def test_owner_crash_kills_server(self):
        p = subprocess.Popen([sys.executable, "-c", _CRASH_OWNER, str(self.fake), str(self.home), str(self.log)],
                             cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE, text=True,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        line = p.stdout.readline().split()
        self.assertTrue(line and line[0].startswith("http://127.0.0.1:"), line)
        pids = [int(x) for x in line[1:]]
        self.assertTrue(pids)
        p.kill()  # TerminateProcess: no cleanup code runs in the owner
        p.wait(10)
        p.stdout.close()
        self.assertTrue(_gone(pids), "the job object did not kill SearXNG with its owner")


# --- tools/setup_searxng.py -----------------------------------------------------------------------------------------
def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


_VALKEYDB = b"import os\nimport pwd\nimport logging\n\nimport valkey\n"


class TestSetup(unittest.TestCase):
    def test_tree_hash(self):
        a = {"b.txt": b"2", "a/x.py": b"1"}
        self.assertEqual(setup.tree_sha256(a), setup.tree_sha256(dict(reversed(list(a.items())))))
        self.assertNotEqual(setup.tree_sha256(a), setup.tree_sha256({**a, "b.txt": b"3"}))
        self.assertNotEqual(setup.tree_sha256(a), setup.tree_sha256({"c.txt": b"2", "a/x.py": b"1"}))

    def test_read_archive(self):
        files = setup.read_archive(_zip({"searxng-abc/searx/a.py": b"A", "searxng-abc/requirements.txt": b"R",
                                         "searxng-abc/utils/x.conf:socket": b"S"}))
        self.assertEqual(files, {"searx/a.py": b"A", "requirements.txt": b"R", "utils/x.conf:socket": b"S"})
        self.assertTrue(setup.wanted("searx/a.py") and setup.wanted("requirements.txt"))
        self.assertFalse(setup.wanted("utils/x.conf:socket") or setup.wanted("docs/index.rst"))
        with self.assertRaises(setup.SetupError):
            setup.read_archive(_zip({"searxng-abc/../evil.py": b"x"}))
        self.assertTrue(setup.safe_rel("searx/static/themes/simple/sxng-core.min.js"))
        for bad in ("utils/x.conf:socket", "../x", "a/../../x", "", "a\\b", "C:/x"):
            self.assertFalse(setup.safe_rel(bad), bad)

    def test_patches(self):
        files = {"searx/valkeydb.py": _VALKEYDB}
        done = setup.patch_files(files)
        self.assertIn("searx/valkeydb.py", done)
        text = files["searx/valkeydb.py"].decode()
        self.assertIn("except ImportError:", text)
        self.assertEqual(text.count("import pwd"), 1)
        compile(text, "valkeydb.py", "exec")
        frozen = files["searx/version_frozen.py"].decode()
        self.assertIn(f'VERSION_STRING = "{setup.SEARXNG_VERSION}"', frozen)
        compile(frozen, "version_frozen.py", "exec")
        self.assertNotIn("searx/valkeydb.py", setup.patch_files(files))  # already patched: unchanged
        with self.assertRaises(setup.SetupError):
            setup.patch_files({"searx/valkeydb.py": b"import os\n"})  # upstream changed: do not guess

    def test_install_source(self):
        archive = {"searx/valkeydb.py": _VALKEYDB, "searx/webapp.py": b"app = None\n", "requirements.txt": b"x==1\n",
                   "docs/conf.py": b"", "utils/a.conf:socket": b""}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(setup, "download_source", side_effect=lambda: dict(archive)) as dl, \
                mock.patch.object(setup, "_say"):
            home = Path(d)
            h1, patched = setup.install_source(home, False, {})
            self.assertEqual(dl.call_count, 1)
            src = home / "src"
            self.assertEqual((src / ".commit").read_text(), setup.SEARXNG_COMMIT)
            self.assertIn(b"except ImportError", (src / "searx" / "valkeydb.py").read_bytes())
            self.assertTrue((src / "searx" / "version_frozen.py").is_file())
            self.assertFalse((src / "docs").exists())
            self.assertEqual(h1, setup.tree_sha256(setup.src_files(src)))
            self.assertTrue(patched)
            h2, patched2 = setup.install_source(home, False, {"src_sha256": h1})  # unchanged: kept
            self.assertEqual((h2, patched2, dl.call_count), (h1, [], 1))
            (src / "searx" / "webapp.py").write_text("tampered")
            h3, _ = setup.install_source(home, False, {"src_sha256": h1})  # changed: reinstalled
            self.assertEqual((h3, dl.call_count), (h1, 2))
            self.assertFalse((home / "src.new").exists() or (home / "src.old").exists())

    def test_repo_template_and_rendering(self):
        template = setup.TEMPLATE.read_text(encoding="utf-8")
        self.assertFalse(setup.has_real_secret(template), "the repo must only carry the placeholder secret")
        text = setup.render_settings(template, secret="f" * 64, port=8890)
        self.assertTrue(setup.has_real_secret(text))
        self.assertEqual(setup.settings_port(text), 8890)
        self.assertEqual(setup.settings_port(template), 8888)
        with self.assertRaises(setup.SetupError):
            setup.render_settings(template.replace('"ultrasecretkey"', '"x"'), secret="s", port=1)
        if not HAVE_YAML:
            self.skipTest("PyYAML not installed")
        import yaml

        cfg = yaml.safe_load(text)
        server = cfg["server"]
        self.assertEqual(server["bind_address"], "127.0.0.1")
        self.assertEqual(server["port"], 8890)
        self.assertEqual(server["secret_key"], "f" * 64)
        self.assertIs(server["limiter"], False)
        self.assertIs(server["public_instance"], False)
        self.assertIs(server["image_proxy"], False)
        self.assertIn("json", cfg["search"]["formats"])
        self.assertIs(cfg["valkey"]["url"], False)
        self.assertEqual(cfg["use_default_settings"]["engines"]["remove"], ["ahmia", "torch"])
        self.assertEqual(yaml.safe_load(template)["server"]["secret_key"], "ultrasecretkey")

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed")
    def test_docker_compose_is_local_only(self):
        import yaml

        compose = yaml.safe_load(setup.COMPOSE.read_text(encoding="utf-8"))
        (name, service), = compose["services"].items()
        self.assertEqual(service["ports"], ["127.0.0.1:8888:8080"])
        self.assertTrue(service["image"].endswith(":" + setup.DOCKER_TAG))
        self.assertIn("./settings.yml:/etc/searxng/settings.yml:ro", service["volumes"])
        self.assertNotIn("environment", service)  # no SEARXNG_SECRET="" that would override the file's key

    def test_update_config_preserves_everything(self):
        original = {"src_lang": "ja", "region": None, "poll_ms": 150, "blocked_processes": ["a.exe", "b.exe"],
                    "hotkeys": {"quit": "ctrl+alt+q"}, "unknown_future_key": {"x": [1, 2.5, "ư"]},
                    "searxng_managed": False}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")
            self.assertTrue(setup.update_config(path, {"searxng_url": "http://127.0.0.1:8888",
                                                       "searxng_managed": True}))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(list(data), list(original) + ["searxng_url"])
            for k, v in original.items():
                if k != "searxng_managed":
                    self.assertEqual(data[k], v)
            self.assertIs(data["searxng_managed"], True)
            self.assertIn('"unknown_future_key"', path.read_text(encoding="utf-8"))
            before = path.read_bytes()
            self.assertFalse(setup.update_config(path, {"searxng_url": "http://127.0.0.1:8888"}))
            self.assertEqual(path.read_bytes(), before)
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(setup.SetupError):
                setup.update_config(path, {"searxng_url": "x"})
            self.assertEqual(path.read_text(encoding="utf-8"), "{not json")
            path.write_text("[1]", encoding="utf-8")
            with self.assertRaises(setup.SetupError):
                setup.update_config(path, {"searxng_url": "x"})
            fresh = Path(d) / "sub" / "config.json"
            self.assertTrue(setup.update_config(fresh, {"searxng_url": "u"}))
            self.assertEqual(json.loads(fresh.read_text(encoding="utf-8")), {"searxng_url": "u"})

    def test_pick_port(self):
        self.assertEqual(setup.pick_port(9123), 9123)
        port = setup.pick_port()
        self.assertTrue(port == sx.DEFAULT_PORT or port in setup.FALLBACK_PORTS or port > 1024)


if __name__ == "__main__":
    unittest.main()
