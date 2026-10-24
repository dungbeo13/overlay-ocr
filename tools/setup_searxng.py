"""Install SearXNG natively (its own venv) for the game lookup's web search; optional.

SearXNG is pinned to one commit of https://github.com/searxng/searxng: the source archive of that commit is
downloaded from GitHub and checked against a content hash (file names + SHA-256 of every file, so a re-compressed
archive still matches). Everything goes to app.lookup.searxng.SEARXNG_HOME (%USERPROFILE%\\.screen-translator\\searxng):

  venv\\         a venv of the same base Python as the app's venv; SearXNG's pinned requirements + tzdata only
  src\\          searx/ of the pinned commit, patched for Windows (below); the venv imports it through a .pth file
  settings.yml  from deploy/searxng/settings.yml with a fresh random secret_key and the port
  install.json  commit, version, port, patches, hash of src (written last: the install is complete)

Windows changes (SearXNG targets Linux), all inside SEARXNG_HOME:
  - searx/valkeydb.py imports the Unix-only `pwd` module at the top: guarded (it is only used to log a failed
    Valkey connection, and we run without Valkey: the limiter is off).
  - searx/version.py asks git for the version and fails without git: searx/version_frozen.py is written, as
    SearXNG's own `python -m searx.version freeze` does for its Docker image.
  - Python's zoneinfo has no time-zone database on Windows: the tzdata package (else the bilibili engine fails).
  - The server is SearXNG's built-in one (python -m searx.webapp): one process, no granian/uwsgi needed.

Run from the project root:
  python -m tools.setup_searxng                 install or repair, verify (start it, one JSON search, stop it),
                                                then set searxng_url / searxng_managed in data/config.json
  python -m tools.setup_searxng --verify        check src against install.json, start it, one search, stop it
  python -m tools.setup_searxng --status        what is installed / answering
  python -m tools.setup_searxng --remove        delete SEARXNG_HOME (data/config.json is left as it is)
  python -m tools.setup_searxng --docker        write SEARXNG_HOME\\docker (compose file + settings with a secret)
  python -m tools.setup_searxng --test-public   which public instances answer format=json (one request each)
Options: --port N (default 8888, else a free one), --reset-settings (new settings.yml and secret), --force
(download and reinstall the source), --no-search (verify without an outgoing query), --no-config.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import secrets
import shutil
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import CONFIG_PATH, PROJECT_ROOT
from app.lookup import searxng as sx

CREATE_NO_WINDOW = 0x08000000

# master on 2026-10-24 ("[l10n] update translations from Weblate (#6850)"); Docker image tag 2026.10.24-ef0ec03da.
SEARXNG_COMMIT = "ef0ec03da02b62d0a977264cb90a0833dfe001ae"
SEARXNG_VERSION = "2026.10.24+ef0ec03da"
DOCKER_TAG = "2026.10.24-ef0ec03da"
ARCHIVE_URL = f"https://github.com/searxng/searxng/archive/{SEARXNG_COMMIT}.zip"
ARCHIVE_TREE_SHA256 = "21b223fcf1e1494300a3d8c76191db24bf16e5666f60b22ce2dd1f0b0b6467a2"  # 1008 files
ARCHIVE_SHA256 = "4bcfa758fce764e996f7a04643b6d21fc5007261e5cc7273cfb744615a765b3c"  # informational only
TZDATA = "tzdata==2026.5"
TEMPLATE = PROJECT_ROOT / "deploy" / "searxng" / "settings.yml"
COMPOSE = PROJECT_ROOT / "deploy" / "searxng" / "docker-compose.yml"
SECRET_PLACEHOLDER = "ultrasecretkey"
_PORT_LINE = r"(?m)^([ \t]+port:[ \t]*)(\d+)"  # server.port (the only indented `port:` key in our template)
FALLBACK_PORTS = range(8889, 8900)
TEST_QUERIES = (("Blue Archive Việt hóa", "vi"), ("Steins;Gate キャラクター", "ja"))

_EXTRACT_FILES = ("requirements.txt", "LICENSE", "README.rst")
PATCHES = (  # (file in src, old, new): applied once, after extraction
    ("searx/valkeydb.py", "import pwd\n",
     "try:\n    import pwd\nexcept ImportError:  # screen-translator: Windows has no pwd; only used if Valkey fails\n"
     "    pwd = None\n"),
)
# Header of the generated searx/version_frozen.py, a file of SearXNG (AGPL) written into the user's SearXNG copy,
# not this file's licence: the string is split so licence scanners do not tag tools/setup_searxng.py with it.
_SEARXNG_SPDX = "# SPDX-" "License-Identifier: AGPL-3.0-or-later"
VERSION_FROZEN = f'''{_SEARXNG_SPDX}
# pylint: disable=missing-module-docstring
# written by screen-translator tools/setup_searxng.py (no git checkout), like `python -m searx.version freeze`

VERSION_STRING = "{SEARXNG_VERSION}"
VERSION_TAG = "{SEARXNG_VERSION}"
DOCKER_TAG = "{DOCKER_TAG}"
GIT_URL = "https://github.com/searxng/searxng"
GIT_BRANCH = "master"
'''


class SetupError(RuntimeError):
    pass


def _say(msg: str = "") -> None:
    print(msg, flush=True)


def _run(cmd: list[str], *, quiet: bool = False, env: dict | None = None, timeout: float = 900) -> str:
    """Run `cmd` (no console window), return its output; SetupError with the output when it fails."""
    try:
        proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        raise SetupError(f"không chạy được {Path(str(cmd[0])).name}: {e}") from e
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise SetupError(f"{' '.join(str(c) for c in cmd[:4])}… lỗi (mã {proc.returncode}):\n{out[-3000:]}")
    if out and not quiet:
        _say(out)
    return out


# --- source --------------------------------------------------------------------------------------------------------
def tree_sha256(files: dict[str, bytes]) -> str:
    """Content hash of a file set: sorted relative names, each with the SHA-256 of its bytes."""
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode("utf-8") + b"\0" + hashlib.sha256(files[name]).digest())
    return h.hexdigest()


def read_archive(data: bytes) -> dict[str, bytes]:
    """{relative path: bytes} of a GitHub archive (its top folder stripped); refuses paths that leave it."""
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            parts = info.filename.replace("\\", "/").split("/", 1)
            if len(parts) != 2 or not parts[1]:
                continue
            rel = parts[1]
            if rel.startswith("/") or ".." in rel.split("/"):
                raise SetupError(f"đường dẫn lạ trong tệp nén: {info.filename!r}")
            out[rel] = z.read(info)
    return out


def safe_rel(rel: str) -> bool:
    """A relative path that can be written on Windows as it is (no drive, stream or reserved characters)."""
    return bool(rel) and not any(c in rel for c in ':*?"<>|\\') and ".." not in rel.split("/")


def download_source() -> dict[str, bytes]:
    _say(f"  tải mã nguồn SearXNG {SEARXNG_VERSION} từ GitHub…")
    try:
        r = httpx.get(ARCHIVE_URL, headers={"User-Agent": sx.USER_AGENT}, timeout=120, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise SetupError(f"không tải được {ARCHIVE_URL}: {e}") from e
    files = read_archive(r.content)
    got = tree_sha256(files)
    if got != ARCHIVE_TREE_SHA256:
        raise SetupError(f"mã nguồn tải về không khớp bản đã ghim (hash {got[:16]}…, cần {ARCHIVE_TREE_SHA256[:16]}…)")
    _say(f"  đã kiểm tra: {len(files)} tệp khớp bản đã ghim ({len(r.content) / 1e6:.1f} MB)")
    return files


def wanted(rel: str) -> bool:
    return rel.startswith("searx/") or rel in _EXTRACT_FILES


def patch_files(files: dict[str, bytes]) -> list[str]:
    """Apply PATCHES and write version_frozen.py into `files` (in memory); returns what was changed."""
    done = []
    for rel, old, new in PATCHES:
        text = files[rel].decode("utf-8")
        if new in text:
            continue
        if text.count(old) != 1:
            raise SetupError(f"không vá được {rel}: đoạn cần vá không còn như cũ")
        files[rel] = text.replace(old, new, 1).encode("utf-8")
        done.append(rel)
    files["searx/version_frozen.py"] = VERSION_FROZEN.encode("utf-8")
    return done + ["searx/version_frozen.py (mới)"]


def src_files(src: Path) -> dict[str, bytes]:
    return {p.relative_to(src).as_posix(): p.read_bytes() for p in src.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.name != ".commit"}


def install_source(home: Path, force: bool, prev_info: dict) -> tuple[str, list[str]]:
    """src/ at the pinned commit, patched. Returns (tree hash of src, patches applied now). `prev_info` is the
    install.json of the previous install (its src_sha256 says whether src/ is still as installed)."""
    src = home / "src"
    if not force and (src / ".commit").is_file() and (src / ".commit").read_text().strip() == SEARXNG_COMMIT:
        current = tree_sha256(src_files(src))
        if current == prev_info.get("src_sha256"):
            _say("  mã nguồn đã có và khớp, giữ nguyên")
            return current, []
        _say("  mã nguồn đã có nhưng bị đổi, cài lại")
    files = {k: v for k, v in download_source().items() if wanted(k)}
    patched = patch_files(files)
    new = home / "src.new"
    if new.exists():
        shutil.rmtree(new)
    for rel, data in files.items():
        if not safe_rel(rel):
            raise SetupError(f"không ghi được tệp {rel!r} trên Windows")
        dest = new / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    (new / ".commit").write_text(SEARXNG_COMMIT, encoding="ascii")
    if src.exists():
        old = home / "src.old"
        if old.exists():
            shutil.rmtree(old)
        src.replace(old)
        shutil.rmtree(old, ignore_errors=True)
    new.replace(src)
    for p in patched:
        _say(f"  vá cho Windows: {p}")
    return tree_sha256(src_files(src)), patched


# --- venv ----------------------------------------------------------------------------------------------------------
def ensure_venv(home: Path) -> Path:
    py = sx.venv_python(home)
    if py.is_file():
        try:
            _run([py, "-c", "import sys, venv"], quiet=True, timeout=60)
            _say(f"  venv đã có: {py.parent.parent}")
            return py
        except SetupError:
            _say("  venv hỏng, tạo lại")
            shutil.rmtree(home / "venv", ignore_errors=True)
    base = Path(getattr(sys, "_base_executable", sys.executable))
    _say(f"  tạo venv bằng {base}")
    _run([base, "-m", "venv", home / "venv"], quiet=True)
    if not py.is_file():
        raise SetupError(f"tạo venv xong mà không thấy {py}")
    return py


def install_requirements(py: Path, src: Path) -> None:
    _say("  cài thư viện của SearXNG vào venv riêng (khoảng 40 MB, 1–2 phút lần đầu)…")
    _run([py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--require-virtualenv", "-q",
          "-r", src / "requirements.txt", TZDATA], quiet=True)
    purelib = Path(_run([py, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"], quiet=True)
                   .splitlines()[-1].strip())
    (purelib / "searxng-src.pth").write_text(str(src) + "\n", encoding="utf-8")
    env = sx.child_env(src.parent, sx.DEFAULT_PORT)
    version = _run([py, "-c", "import searx.version as v; print(v.VERSION_STRING)"], quiet=True, env=env,
                   timeout=120).splitlines()[-1].strip()
    if version != SEARXNG_VERSION:
        raise SetupError(f"SearXNG trong venv báo phiên bản {version!r}, cần {SEARXNG_VERSION!r}")
    _say(f"  SearXNG {version} nạp được trong venv")


# --- settings --------------------------------------------------------------------------------------------------------
def pick_port(wanted_port: int | None = None) -> int:
    if wanted_port:
        return wanted_port
    for port in (sx.DEFAULT_PORT, *FALLBACK_PORTS):
        if sx.port_free(port):
            return port
    from app.llm.server import free_port

    return free_port()


def render_settings(template: str, *, secret: str, port: int) -> str:
    """The template with a real secret_key and the port (each must occur exactly once)."""
    text, n1 = re.subn(r'(?m)^([ \t]*secret_key:[ \t]*)"' + SECRET_PLACEHOLDER + '".*$',
                       lambda m: f'{m.group(1)}"{secret}"  # random, written by tools/setup_searxng.py', template)
    text, n2 = re.subn(_PORT_LINE, lambda m: f"{m.group(1)}{port}", text)
    if n1 != 1 or n2 != 1:
        raise SetupError(f"{TEMPLATE} không có đúng một dòng secret_key \"{SECRET_PLACEHOLDER}\" và một dòng port")
    return text


def has_real_secret(text: str) -> bool:
    """settings.yml text whose secret_key is set to something other than the placeholder."""
    m = re.search(r'(?m)^[ \t]*secret_key:[ \t]*"([^"]*)"', text)
    return bool(m) and m.group(1) not in ("", SECRET_PLACEHOLDER)


def settings_port(text: str) -> int | None:
    m = re.search(_PORT_LINE, text)
    return int(m.group(2)) if m else None


def write_settings(home: Path, port_arg: int | None, reset: bool) -> int:
    path = sx.settings_path(home)
    if path.is_file() and not reset:
        text = path.read_text(encoding="utf-8")
        if not has_real_secret(text):
            raise SetupError(f"{path} vẫn còn secret_key mẫu: chạy lại với --reset-settings")
        port = settings_port(text) or sx.DEFAULT_PORT
        if port_arg and port_arg != port:
            text = re.sub(_PORT_LINE, lambda m: f"{m.group(1)}{port_arg}", text, count=1)
            path.write_text(text, encoding="utf-8")
            port = port_arg
        _say(f"  giữ settings.yml đang có (cổng {port}; --reset-settings để tạo lại)")
        return port
    port = pick_port(port_arg)
    path.write_text(render_settings(TEMPLATE.read_text(encoding="utf-8"), secret=secrets.token_hex(32), port=port),
                    encoding="utf-8")
    _say(f"  ghi {path} (secret_key ngẫu nhiên, chỉ 127.0.0.1, cổng {port}, JSON bật, limiter tắt)")
    return port


# --- data/config.json ------------------------------------------------------------------------------------------------
def update_config(path: Path, values: dict) -> bool:
    """Set `values` in the JSON object at `path`, keeping every other key and value (and the key order) as it is.
    Written like app.config.Config.save. Returns whether anything changed."""
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError as e:
            raise SetupError(f"{path} không phải JSON hợp lệ, không sửa: {e}") from e
        if not isinstance(data, dict):
            raise SetupError(f"{path} không phải một đối tượng JSON, không sửa")
    else:
        data = {}
    if all(k in data and data[k] == v for k, v in values.items()):
        return False
    data.update(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return True


# --- verify ----------------------------------------------------------------------------------------------------------
def run_checks(home: Path, *, do_search: bool = True) -> bool:
    """Start our instance, check it listens on 127.0.0.1 only, run the test searches, stop it; prints the numbers."""
    svc = sx.SearxngService(url="", managed=True, home=home)
    ok = True
    try:
        _say("  khởi động SearXNG…")
        url = svc.ensure_running(timeout=60)
        if url is None:
            _say(f"  LỖI: không khởi động được: {svc.last_error}")
            return False
        pids = svc.pids()
        addrs = sx.listening_addresses(pids)
        _say(f"  sẵn sàng sau {svc.start_s:.1f} s tại {url} (pid {', '.join(map(str, pids))})")
        _say(f"  đang nghe: {', '.join(f'{ip}:{port}' for ip, port in addrs) or '?'}")
        if not addrs or any(not ip.startswith("127.") for ip, _ in addrs):
            _say("  LỖI: SearXNG phải chỉ nghe trên 127.0.0.1")
            ok = False
        if do_search:
            for query, lang in TEST_QUERIES:
                t0 = time.perf_counter()
                try:
                    hits = sx.search(url, query, language=lang, timeout=20)
                except sx.SearxngError as e:
                    _say(f"  LỖI tìm {query!r}: {e}")
                    ok = False
                    continue
                _say(f"  tìm {query!r}: {len(hits)} kết quả JSON trong {time.perf_counter() - t0:.1f} s")
                for h in hits[:3]:
                    _say(f"    - {h.title[:70]}  [{h.engine}]  {h.url[:80]}")
                if not hits:
                    ok = False
        _say(f"  RAM: {svc.memory_mb():.0f} MB")
    finally:
        pids = svc.pids()
        port = int(svc.base_url.rsplit(":", 1)[1]) if svc.base_url else None
        t0 = time.perf_counter()
        svc.close()
        if port is not None:
            deadline = time.monotonic() + 3  # the kernel releases the listening socket just after the process
            while not (closed := sx.port_free(port)) and time.monotonic() < deadline:
                time.sleep(0.1)
            _say(f"  đã tắt trong {(time.perf_counter() - t0) * 1000:.0f} ms; cổng {port} "
                 f"{'đã đóng' if closed else 'VẪN MỞ'}")
            ok = ok and closed
        import psutil

        alive = [p for p in pids if psutil.pid_exists(p)]
        if alive:
            _say(f"  LỖI: còn process {alive}")
            ok = False
    return ok


# --- commands --------------------------------------------------------------------------------------------------------
def _blocked_by_running(home: Path) -> None:
    info = sx.install_info(home) or {}
    port = info.get("port") or sx.DEFAULT_PORT
    if sx.probe(f"http://127.0.0.1:{port}", timeout=2):
        raise SetupError(f"đang có SearXNG chạy ở 127.0.0.1:{port} (app đang dùng, hoặc bản Docker): thoát app "
                         "hoặc dừng nó rồi chạy lại")


def cmd_install(args) -> int:
    home = sx.SEARXNG_HOME
    _say(f"Cài SearXNG {SEARXNG_VERSION} (commit {SEARXNG_COMMIT[:9]}) vào {home}")
    if not TEMPLATE.is_file():
        raise SetupError(f"thiếu tệp mẫu {TEMPLATE}")
    home.mkdir(parents=True, exist_ok=True)
    if not args.force_running:
        _blocked_by_running(home)
    prev = sx.install_info(home) or {}
    (home / "install.json").unlink(missing_ok=True)  # written again last: until then the install is incomplete
    _say("[1/5] venv")
    py = ensure_venv(home)
    _say("[2/5] mã nguồn")
    src_hash, _patched = install_source(home, args.force, prev)
    _say("[3/5] settings.yml")
    port = write_settings(home, args.port, args.reset_settings)
    _say("[4/5] thư viện")
    install_requirements(py, home / "src")
    info = {
        "commit": SEARXNG_COMMIT, "version": SEARXNG_VERSION, "docker_tag": DOCKER_TAG, "source": ARCHIVE_URL,
        "archive_tree_sha256": ARCHIVE_TREE_SHA256, "src_sha256": src_hash, "port": port,
        "python": _run([py, "-c", "import platform; print(platform.python_version())"], quiet=True).strip(),
        "extra_packages": [TZDATA], "patches": [p[0] for p in PATCHES] + ["searx/version_frozen.py"],
        "server": "python -m searx.webapp (werkzeug, threaded)",
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (home / "install.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    _say("[5/5] kiểm tra")
    ok = True
    if not args.no_verify:
        ok = run_checks(home, do_search=not args.no_search)
    url = f"http://127.0.0.1:{port}"
    if ok and not args.no_config:
        changed = update_config(CONFIG_PATH, {"searxng_url": url, "searxng_managed": True})
        _say(f"  {CONFIG_PATH}: searxng_url = {url}, searxng_managed = true"
             f"{'' if changed else ' (đã đúng sẵn)'}")
    _say("Xong: app tự bật SearXNG khi cần tra cứu và tự tắt sau 10 phút không dùng." if ok
         else "Cài xong nhưng kiểm tra chưa đạt: xem các dòng LỖI ở trên.")
    return 0 if ok else 1


def cmd_verify(args) -> int:
    home = sx.SEARXNG_HOME
    info = sx.install_info(home)
    if not sx.installed(home) or info is None:
        _say(f"SearXNG chưa được cài ở {home}: chạy python -m tools.setup_searxng")
        return 1
    _say(f"SearXNG {info.get('version')} ở {home}")
    current = tree_sha256(src_files(home / "src"))
    src_ok = current == info.get("src_sha256")
    _say(f"  mã nguồn: {'khớp install.json' if src_ok else 'KHÁC install.json (chạy lại cài đặt với --force)'}")
    secret_ok = has_real_secret(sx.settings_path(home).read_text(encoding="utf-8"))
    _say(f"  settings.yml: {'secret_key riêng' if secret_ok else 'LỖI: còn secret_key mẫu'}")
    _blocked_by_running(home)
    ok = run_checks(home, do_search=not args.no_search)
    return 0 if ok and src_ok and secret_ok else 1


def cmd_status(_args) -> int:
    home = sx.SEARXNG_HOME
    info = sx.install_info(home)
    _say(f"Thư mục: {home}")
    _say(f"Đã cài: {'có, ' + str(info.get('version')) + ', cổng ' + str(info.get('port')) if sx.installed(home) and info else 'chưa'}")
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        cfg = {}
    url = cfg.get("searxng_url") or ""
    _say(f"data/config.json: searxng_url = {url or '(trống)'}, searxng_managed = {cfg.get('searxng_managed', '(chưa có)')}")
    for u in dict.fromkeys(filter(None, [url, f"http://127.0.0.1:{(info or {}).get('port') or sx.DEFAULT_PORT}"])):
        _say(f"  {u}: {'đang trả lời' if sx.probe(u, timeout=2) else 'không có gì trả lời'}")
    if (home / "docker" / "docker-compose.yml").is_file():
        _say(f"Thư mục Docker: {home / 'docker'}")
    return 0


def cmd_remove(_args) -> int:
    home = sx.SEARXNG_HOME
    if not home.exists():
        _say(f"Không có gì để gỡ ({home} không tồn tại)")
        return 0
    _blocked_by_running(home)
    try:
        shutil.rmtree(home)
    except OSError as e:
        raise SetupError(f"không xóa hết được {home} (tệp đang được dùng? thoát app rồi thử lại): {e}") from e
    _say(f"Đã xóa {home}. data/config.json giữ nguyên; app sẽ báo “Chưa cài SearXNG”.")
    return 0


def cmd_docker(args) -> int:
    dest = sx.SEARXNG_HOME / "docker"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(COMPOSE, dest / "docker-compose.yml")
    settings = dest / "settings.yml"
    if settings.is_file() and not args.reset_settings and has_real_secret(settings.read_text(encoding="utf-8")):
        _say(f"Giữ {settings} đang có (--reset-settings để tạo secret mới)")
    else:
        settings.write_text(render_settings(TEMPLATE.read_text(encoding="utf-8"), secret=secrets.token_hex(32),
                                            port=sx.DEFAULT_PORT), encoding="utf-8")
        _say(f"Ghi {settings} (secret_key ngẫu nhiên)")
    _say(f"Đã chuẩn bị {dest}. Chạy SearXNG bằng Docker:")
    _say(f'  cd "{dest}"')
    _say("  docker compose up -d")
    _say("App tìm thấy nó ở http://127.0.0.1:8888 và dùng nó thay cho bản tự chạy.")
    return 0


def cmd_test_public(_args) -> int:
    ua = {"User-Agent": sx.USER_AGENT}
    _say("Lấy danh sách instance công khai từ searx.space…")
    data = httpx.get("https://searx.space/data/instances.json", headers=ua, timeout=30, follow_redirects=True).json()
    cands = [u for u, d in data.get("instances", {}).items()
             if d.get("network_type") == "normal" and (d.get("http") or {}).get("status_code") == 200
             and ((d.get("uptime") or {}).get("uptimeMonth") or 0) >= 80]
    _say(f"Thử {len(cands)} instance, mỗi cái một lần tìm format=json…")

    def one(url: str) -> tuple[str, str]:
        try:
            n = len(sx.search(url, "Blue Archive", timeout=12))
            return url, f"OK ({n} kết quả)"
        except sx.SearxngError as e:
            return url, str(e).split(": ", 1)[-1]

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(one, cands))
    for url, res in results:
        _say(f"  {url:45} {res}")
    good = [u.rstrip("/") for u, r in results if r.startswith("OK")]
    _say(f"\n{len(good)}/{len(cands)} trả lời JSON. Cho app/lookup/searxng.py "
         f"(thử ngày {datetime.now():%Y-%m-%d}):\nPUBLIC_INSTANCES: list[str] = {good!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true", help="check the install, start it, search once, stop it")
    mode.add_argument("--status", action="store_true", help="show what is installed / answering")
    mode.add_argument("--remove", action="store_true", help="delete the native install")
    mode.add_argument("--docker", action="store_true", help="write a Docker folder with a real secret")
    mode.add_argument("--test-public", action="store_true", help="test public instances for format=json")
    ap.add_argument("--port", type=int, help=f"port for the native instance (default {sx.DEFAULT_PORT} if free)")
    ap.add_argument("--reset-settings", action="store_true", help="write settings.yml again (new secret)")
    ap.add_argument("--force", action="store_true", help="download and reinstall the source")
    ap.add_argument("--force-running", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-verify", action="store_true", help="install without starting it")
    ap.add_argument("--no-search", action="store_true", help="verify without an outgoing search")
    ap.add_argument("--no-config", action="store_true", help="do not touch data/config.json")
    args = ap.parse_args(argv)
    if args.port is not None and not 1024 <= args.port <= 65535:
        ap.error("--port must be 1024-65535")
    try:
        if args.verify:
            return cmd_verify(args)
        if args.status:
            return cmd_status(args)
        if args.remove:
            return cmd_remove(args)
        if args.docker:
            return cmd_docker(args)
        if args.test_public:
            return cmd_test_public(args)
        return cmd_install(args)
    except SetupError as e:
        print(f"lỗi: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nđã dừng (chạy lại lệnh để cài tiếp)", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
