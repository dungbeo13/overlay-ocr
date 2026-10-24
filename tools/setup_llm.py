"""Install the local LLM tier: llama.cpp's llama-server (Windows x64, CUDA) and the GGUF models.

Everything is pinned (GitHub release tag, Hugging Face revision, byte size, SHA-256), so a reinstall
gets exactly the files bench/results/llm_setup.md measured. Before downloading, the pinned hashes
are cross-checked against the GitHub release asset digest / the Hugging Face LFS sha256 (a
mismatch aborts; an unreachable API only warns). Files go to config.LLM_HOME:

  LLAMA_DIR   llama-server.exe, ggml/CUDA DLLs and install.json (build + per-file SHA-256)
  MODELS_DIR  *.gguf

Run from the project root:
  python -m tools.setup_llm                          llama.cpp + every pinned model
  python -m tools.setup_llm --models light           llama.cpp + the light model only
  python -m tools.setup_llm --list                   what is pinned and what is installed
  python -m tools.setup_llm --verify                 re-hash everything installed (exit 1 on a mismatch)
  python -m tools.setup_llm --bench                  smoke test + quality probe of the installed models
                                                     -> bench/results/llm_setup.json / llm_setup.md
Options: --cuda 13|12|auto (CUDA build; 13 needs an r580+ driver), --force (re-download), --no-llama.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.config import LLAMA_DIR, LLM_HOME, MODELS_DIR
from app.llm.models import DEFAULT_KIND

USER_AGENT = "screen-translator-setup/1"
GITHUB_REPO = "ggml-org/llama.cpp"
CREATE_NO_WINDOW = 0x08000000


class SetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    sha256: str
    url: str


@dataclass(frozen=True)
class LlamaBuild:
    tag: str  # nightly build tag the stable release points at
    release: str  # stable release name
    cuda: str
    assets: tuple[Asset, ...]  # extracted into the same directory, in order


@dataclass(frozen=True)
class ModelSpec:
    role: str  # "light" (while gaming) | "quality" (reading-heavy content)
    file: str  # file name in MODELS_DIR (config.llm_model)
    repo: str
    revision: str  # pinned Hugging Face commit
    size: int
    sha256: str
    license: str
    params: str
    notes: str
    vram_mib: int = 0  # measured GPU memory with -c 4096 -np 1 (bench/results/llm_setup.json)
    sampling: dict = field(default_factory=dict)  # upstream-recommended sampling

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{self.file}"


def _gh(tag: str, name: str) -> str:
    return f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{name}"


# llama.cpp v0.7.0 (2026-10-15) is the latest stable release; its binaries are those of build b11669
# (the release's nightly-tag.txt). CUDA 13.4 needs driver r580+ (621.01 here reports CUDA 13.5).
_TAG = "b11669"
LLAMA_BUILDS = {
    "13": LlamaBuild(_TAG, "v0.7.0", "13.4", (
        Asset(f"llama-{_TAG}-bin-win-cuda-13.4-x64.zip", 154582227,
              "3b92d5c064f5032e603e531d3ec8961f3bd6613e5dfad047399a92dec8e95dbc",
              _gh(_TAG, f"llama-{_TAG}-bin-win-cuda-13.4-x64.zip")),
        Asset("cudart-llama-bin-win-cuda-13.4-x64.zip", 423535356,
              "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668",
              _gh(_TAG, "cudart-llama-bin-win-cuda-13.4-x64.zip")),
    )),
    "12": LlamaBuild(_TAG, "v0.7.0", "12.4", (
        Asset(f"llama-{_TAG}-bin-win-cuda-12.4-x64.zip", 266582698,
              "c2da1f73ed176ae093ac5aceb978a7b167a16113f6d74c6789eeaee2135cb8dc",
              _gh(_TAG, f"llama-{_TAG}-bin-win-cuda-12.4-x64.zip")),
        Asset("cudart-llama-bin-win-cuda-12.4-x64.zip", 391443627,
              "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
              _gh(_TAG, "cudart-llama-bin-win-cuda-12.4-x64.zip")),
    )),
}
CUDA13_MIN_DRIVER = 580

_HY_SAMPLING = {"temperature": 0.7, "top_p": 0.6, "top_k": 20, "repetition_penalty": 1.05}
MODELS: dict[str, ModelSpec] = {
    "light": ModelSpec(
        role="light", file="Hy-MT2-1.8B-Q6_K.gguf", repo="tencent/Hy-MT2-1.8B-GGUF",
        revision="a0c709d9fac510f2c807aa3af52872340dc37a4a", size=1474785120,
        sha256="d98fe604dec1f28f58f80d7d560f7177e584d3b8e5835862687660e5ff97cb40",
        license="Apache-2.0", params="1.8B",
        notes="Tencent Hy-MT2 translation model (33 languages incl. ja/en/vi), arch hunyuan-dense, "
              "no thinking mode; Q6_K keeps more of the instruction following that Q4_K_M loses",
        vram_mib=2100, sampling=_HY_SAMPLING),
    "quality": ModelSpec(
        role="quality", file="Hy-MT2-7B-Q4_K_M.gguf", repo="tencent/Hy-MT2-7B-GGUF",
        revision="ab8472660ac61fac25f1af43fac2599d52a8a775", size=4624648896,
        sha256="9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b",
        license="Apache-2.0", params="7.5B",
        notes="Tencent Hy-MT2 7B translation model, arch hunyuan-dense, no thinking mode; follows "
              "character notes for pronouns, the light model mostly does not",
        vram_mib=5400, sampling=_HY_SAMPLING),
}
# config.llm_model == "" (auto): app.llm.models.resolve_model loads the quality model when the GPU has room for it
# (AUTO_QUALITY_FREE_MB free at load time), else the light one, the fallback marked [default] here; "light" /
# "quality" or a file name pick one (tray: Dịch bằng LLM > Mô hình).
DEFAULT_MODEL = MODELS[DEFAULT_KIND].file


# ---------------------------------------------------------------------------------------------
# download + verify


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(8 << 20):
            h.update(chunk)
    return h.hexdigest()


def _fmt_size(n: float) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.1f} MB"


def _client() -> httpx.Client:
    return httpx.Client(follow_redirects=True, headers={"User-Agent": USER_AGENT},
                        timeout=httpx.Timeout(30.0, read=120.0))


def download(url: str, dest: Path, size: int, sha256: str) -> str:
    """Fetch `url` to `dest` (resuming `dest`.part), check size + SHA-256, then rename into place.

    Returns "present" if `dest` already had the right content, else "downloaded".
    """
    if dest.exists() and dest.stat().st_size == size:
        if file_sha256(dest) == sha256:
            return "present"
        print(f"  {dest.name}: existing file has the wrong SHA-256, downloading again")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    failures = 0
    t0, last_print, start_have = time.perf_counter(), 0.0, have
    with _client() as client:
        while have < size:
            headers = {"Range": f"bytes={have}-"} if have else {}
            try:
                with client.stream("GET", url, headers=headers) as resp:
                    if resp.status_code == 200 and have:
                        have = 0  # the server ignored Range: start over
                    elif resp.status_code not in (200, 206):
                        raise SetupError(f"{url}: HTTP {resp.status_code}")
                    with open(part, "ab" if have else "wb") as fh:
                        for chunk in resp.iter_bytes(1 << 20):
                            fh.write(chunk)
                            have += len(chunk)
                            now = time.perf_counter()
                            if now - last_print > 2 or have >= size:
                                last_print = now
                                rate = (have - start_have) / max(now - t0, 1e-6)
                                print(f"\r  {dest.name}: {have * 100 / size:5.1f}% of {_fmt_size(size)} "
                                      f"({rate / 1e6:.0f} MB/s)   ", end="", flush=True)
                if have < size:
                    raise httpx.ReadError("connection closed early")
            except (httpx.TransportError, SetupError) as e:
                failures += 1
                if isinstance(e, SetupError) or failures > 8:
                    print()
                    raise SetupError(f"download failed for {dest.name}: {e}") from e
                print(f"\n  {dest.name}: {type(e).__name__}, resuming at {_fmt_size(have)} (retry {failures})")
                time.sleep(min(2 * failures, 10))
    print()
    if have != size:
        raise SetupError(f"{dest.name}: got {have} bytes, expected {size}")
    digest = file_sha256(part)
    if digest != sha256:
        part.unlink()
        raise SetupError(f"{dest.name}: SHA-256 {digest} does not match the pinned {sha256}")
    part.replace(dest)
    return "downloaded"


def hf_lfs_info(spec: ModelSpec) -> tuple[int, str] | None:
    """(size, sha256) of the model file at the pinned revision, per the Hugging Face API."""
    try:
        with _client() as client:
            resp = client.get(f"https://huggingface.co/api/models/{spec.repo}/tree/{spec.revision}")
            resp.raise_for_status()
            for entry in resp.json():
                if entry.get("path") == spec.file and entry.get("lfs"):
                    return int(entry["lfs"].get("size", entry.get("size", 0))), entry["lfs"]["oid"]
    except (httpx.HTTPError, ValueError, KeyError) as e:
        print(f"  warning: Hugging Face API unavailable ({type(e).__name__}); using the pinned hash only")
    return None


def github_digests(tag: str) -> dict[str, str] | None:
    """asset name -> sha256 for a llama.cpp release, per the GitHub API (`digest` field)."""
    try:
        with _client() as client:
            resp = client.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{tag}",
                              headers={"Accept": "application/vnd.github+json"})
            resp.raise_for_status()
            return {a["name"]: a["digest"].removeprefix("sha256:")
                    for a in resp.json().get("assets", []) if str(a.get("digest", "")).startswith("sha256:")}
    except (httpx.HTTPError, ValueError, KeyError) as e:
        print(f"  warning: GitHub API unavailable ({type(e).__name__}); using the pinned hashes only")
    return None


# ---------------------------------------------------------------------------------------------
# llama.cpp


def nvidia_driver_major() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW).stdout
        return int(out.strip().splitlines()[0].split(".")[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def pick_build(cuda: str) -> LlamaBuild:
    if cuda in LLAMA_BUILDS:
        return LLAMA_BUILDS[cuda]
    major = nvidia_driver_major()
    if major is None:
        raise SetupError("nvidia-smi not found: is an NVIDIA driver installed? (use --cuda 12|13 to force)")
    return LLAMA_BUILDS["13" if major >= CUDA13_MIN_DRIVER else "12"]


def _read_install_info() -> dict | None:
    try:
        return json.loads((LLAMA_DIR / "install.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Zip members with their common top-level folder (if any) stripped; rejects path traversal."""
    infos = [i for i in zf.infolist() if not i.is_dir()]
    parts = [Path(i.filename.replace("\\", "/")).parts for i in infos]
    for p in parts:
        if not p or p[0] in ("/", "\\") or ".." in p or ":" in p[0]:
            raise SetupError(f"unsafe path in {zf.filename}: {'/'.join(p)}")
    strip = 1 if parts and all(len(p) > 1 and p[0] == parts[0][0] for p in parts) else 0
    for info, p in zip(infos, parts):
        info.filename = "/".join(p[strip:])
    return infos


def verify_llama() -> tuple[bool, str]:
    """Re-hash the installed llama.cpp files against install.json."""
    info = _read_install_info()
    if not info:
        return False, f"not installed ({LLAMA_DIR / 'install.json'} missing)"
    bad = [rel for rel, digest in info.get("files", {}).items()
           if not (LLAMA_DIR / rel).is_file() or file_sha256(LLAMA_DIR / rel) != digest]
    if bad:
        return False, f"{len(bad)} file(s) missing or changed: {', '.join(bad[:5])}"
    if not (LLAMA_DIR / "llama-server.exe").is_file():
        return False, "llama-server.exe missing"
    return True, f"{info['release']} ({info['tag']}, CUDA {info['cuda']}), {len(info['files'])} files OK"


def server_version() -> str:
    """`llama-server --version` output (also proves the CUDA DLLs load and see the GPU)."""
    try:
        proc = subprocess.run([str(LLAMA_DIR / "llama-server.exe"), "--version"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=60,
                              cwd=LLAMA_DIR, creationflags=CREATE_NO_WINDOW)
        return (proc.stdout + proc.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return f"failed: {e}"


def install_llama(build: LlamaBuild, force: bool) -> str:
    info = _read_install_info()
    if not force and info and info.get("tag") == build.tag and info.get("cuda") == build.cuda:
        ok, msg = verify_llama()
        if ok:
            return f"present: {msg}"
        print(f"  installed llama.cpp is damaged ({msg}); reinstalling")
    online = github_digests(build.tag)
    for a in build.assets:
        if online is not None and online.get(a.name) != a.sha256:
            raise SetupError(f"{a.name}: GitHub digest {online.get(a.name)} differs from the pinned {a.sha256}")
    work = Path(tempfile.mkdtemp(prefix="st-llama-"))  # fresh, empty: the zips are untrusted until hashed
    staging = LLAMA_DIR.with_name(LLAMA_DIR.name + ".new")
    try:
        for a in build.assets:
            download(a.url, work / a.name, a.size, a.sha256)
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        for a in build.assets:
            with zipfile.ZipFile(work / a.name) as zf:
                for member in _safe_members(zf):
                    zf.extract(member, staging)
        files = {p.relative_to(staging).as_posix(): file_sha256(p)
                 for p in sorted(staging.rglob("*")) if p.is_file()}
        if "llama-server.exe" not in files:
            raise SetupError("llama-server.exe not found in the release zip")
        (staging / "install.json").write_text(json.dumps({
            "tag": build.tag, "release": build.release, "cuda": build.cuda,
            "assets": [{"name": a.name, "sha256": a.sha256} for a in build.assets],
            "installed": time.strftime("%Y-%m-%d %H:%M:%S"), "files": files,
        }, indent=2), encoding="utf-8")
        old = LLAMA_DIR.with_name(LLAMA_DIR.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        if LLAMA_DIR.exists():
            LLAMA_DIR.replace(old)
        staging.replace(LLAMA_DIR)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
    return f"installed {build.release} ({build.tag}, CUDA {build.cuda}) into {LLAMA_DIR}"


# ---------------------------------------------------------------------------------------------
# models


def install_model(spec: ModelSpec, force: bool) -> str:
    dest = MODELS_DIR / spec.file
    if force and dest.exists():
        dest.unlink()
    online = hf_lfs_info(spec)
    if online is not None and online != (spec.size, spec.sha256):
        raise SetupError(f"{spec.file}: Hugging Face reports size/sha256 {online}, pinned ({spec.size}, {spec.sha256})")
    state = download(spec.url, dest, spec.size, spec.sha256)
    return f"{state}: {dest} ({_fmt_size(spec.size)}, sha256 OK)"


def verify_model(spec: ModelSpec) -> tuple[bool, str]:
    path = MODELS_DIR / spec.file
    if not path.is_file():
        return False, "not installed"
    if path.stat().st_size != spec.size:
        return False, f"size {path.stat().st_size} != {spec.size}"
    digest = file_sha256(path)
    return (True, "sha256 OK") if digest == spec.sha256 else (False, f"sha256 {digest} != pinned {spec.sha256}")


# ---------------------------------------------------------------------------------------------
# llama-server runner (bench only; the app has its own manager)

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
_k32.CreateJobObjectW.restype = wintypes.HANDLE
_k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
_k32.SetInformationJobObject.restype = wintypes.BOOL
_k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
_k32.AssignProcessToJobObject.restype = wintypes.BOOL
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL
_ntdll = ctypes.WinDLL("ntdll")
_ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
_ntdll.NtResumeProcess.restype = ctypes.c_long

CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_TERMINATE, _PROCESS_SET_QUOTA, _PROCESS_SUSPEND_RESUME = 0x0001, 0x0100, 0x0800


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                    "OtherOperationCount", "ReadTransferCount",
                                                    "WriteTransferCount", "OtherTransferCount")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def gpu_memory_used_mib() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW).stdout
        return int(out.strip().splitlines()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


class LlamaServer:
    """llama-server on 127.0.0.1:<free port>, windowless, inside a kill-on-close Job Object.

    The process starts suspended and only runs once it is in the job, so it can never outlive this
    process (closing the job handle - also on a crash - kills it and frees its VRAM).
    """

    def __init__(self, model: Path, ctx: int = 4096, gpu_layers: int = 999, extra: list[str] | None = None,
                 log_path: Path | None = None):
        self.model = model
        self.port = free_port()
        self.args = [str(LLAMA_DIR / "llama-server.exe"), "-m", str(model), "--host", "127.0.0.1",
                     "--port", str(self.port), "-ngl", str(gpu_layers), "-c", str(ctx), "-np", "1",
                     "--no-webui", *(extra or [])]
        self.log_path = log_path or (LLM_HOME / "logs" / f"server-{model.stem}.log")
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None
        self._job = None
        self._log = None
        self.load_s = 0.0

    def start(self, timeout_s: float = 300.0) -> float:
        """Start and wait until /health is OK; returns the load time in seconds."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "wb")
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            raise SetupError(f"CreateJobObject failed ({ctypes.get_last_error()})")
        self._job = job
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _k32.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                            ctypes.byref(limits), ctypes.sizeof(limits)):
            raise SetupError(f"SetInformationJobObject failed ({ctypes.get_last_error()})")
        t0 = time.perf_counter()
        self.proc = subprocess.Popen(self.args, cwd=LLAMA_DIR, stdin=subprocess.DEVNULL, stdout=self._log,
                                     stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW | CREATE_SUSPENDED)
        handle = _k32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_SET_QUOTA | _PROCESS_SUSPEND_RESUME,
                                  False, self.proc.pid)
        try:
            if not handle or not _k32.AssignProcessToJobObject(job, handle):
                err = ctypes.get_last_error()
                self.proc.kill()
                raise SetupError(f"could not put llama-server into the job object ({err})")
            if _ntdll.NtResumeProcess(handle) != 0:
                self.proc.kill()
                raise SetupError("could not resume llama-server")
        finally:
            if handle:
                _k32.CloseHandle(handle)
        with httpx.Client(timeout=5.0) as client:
            while True:
                if self.proc.poll() is not None:
                    raise SetupError(f"llama-server exited with {self.proc.returncode}:\n{self.log_tail()}")
                try:
                    if client.get(self.base_url + "/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if time.perf_counter() - t0 > timeout_s:
                    raise SetupError(f"llama-server not ready after {timeout_s:.0f} s:\n{self.log_tail()}")
                time.sleep(0.05)
        self.load_s = time.perf_counter() - t0
        return self.load_s

    def log_tail(self, n: int = 30) -> str:
        try:
            return "\n".join(self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
        except OSError:
            return ""

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._job:
            _k32.CloseHandle(self._job)  # kills anything still in the job
            self._job = None
        if self._log:
            self._log.close()
            self._log = None

    def __enter__(self) -> "LlamaServer":
        try:
            self.start()
        except BaseException:
            self.stop()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


# ---------------------------------------------------------------------------------------------
# bench: smoke test + quality probe

LANG_NAMES = {"ja": "Japanese", "en": "English"}
PROBE_IDS = ["ja_dlg_01", "ja_dlg_02", "ja_dlg_03", "ja_dlg_04", "ja_dlg_05", "ja_dlg_07",
             "ja_ui_01", "ja_ui_05", "ja_ui_06",
             "en_dlg_01", "en_dlg_02", "en_dlg_03", "en_dlg_04", "en_ui_02", "en_ui_04", "en_ui_06"]
SAMPLING = {"temperature": 0, "repeat_penalty": 1.05}  # greedy: reproducible, cache-friendly
SIMPLE_SYSTEM = ("You are a professional translator of games, manga and subtitles. Translate the user's text "
                 "from {lang} into natural, idiomatic Vietnamese. Output only the translation.")
SCREEN_SYSTEM = (
    "You are a professional translator of games, manga and subtitles into Vietnamese. Translate every line of "
    "the screen into natural, idiomatic Vietnamese that fits the speaker. Always use the glossary renderings for "
    "names and terms. Choose Vietnamese pronouns and forms of address (anh/em, chị/em, cậu/tớ, tao/mày, ông/cháu, "
    "bà/cháu, tôi/ngài...) from who is speaking to whom, using the character notes, the speaker of each line and "
    "the recent dialogue. Keep the tone (polite, rude, childish, ...). Do not translate the speaker names into "
    "the lines and do not add explanations. Reply with JSON only: an object mapping each line id to its "
    "Vietnamese translation.")

# Short scenes whose correct Vietnamese pronouns depend on who speaks to whom.
DIALOGUES = [
    {"name": "ja_senpai", "lang": "ja",
     "expect": "Yuuto -> Mika: anh/em; Mika -> Yuuto: em + anh/tiền bối, polite (ạ)",
     "glossary": [("ユウト", "Yuuto", "person"), ("ミカ", "Mika", "person")],
     "characters": ["ユウト (Yuuto): male, 17, Mika's senior at school. Calls himself 'anh' and calls Mika 'em'.",
                    "ミカ (Mika): female, 16, Yuuto's junior. Calls herself 'em' and calls Yuuto 'anh' or 'tiền bối'."],
     "lines": [("ユウト", "おい、ミカ。また遅刻か？"), ("ミカ", "すみません、先輩！電車が止まっちゃって…"),
               ("ユウト", "しょうがないな。今日は俺が奢ってやるよ。"), ("ミカ", "えっ、本当ですか？先輩、大好きです！")]},
    {"name": "ja_rivals", "lang": "ja",
     "expect": "two rude rivals: tao/mày both ways",
     "glossary": [("ゴウ", "Gou", "person"), ("レン", "Ren", "person")],
     "characters": ["ゴウ (Gou): male delinquent, Ren's rival; rude. Calls himself 'tao' and calls Ren 'mày'.",
                    "レン (Ren): male delinquent, Gou's rival; rude. Calls himself 'tao' and calls Gou 'mày'."],
     "lines": [("ゴウ", "てめぇ、俺の縄張りで何してやがる！"), ("レン", "お前に関係ねえだろ。どけよ。"),
               ("ゴウ", "上等だ。かかってこいよ！")]},
    {"name": "en_captain", "lang": "en",
     "expect": "Leo -> Mira: em + chị/đội trưởng; Mira -> Leo: tôi/chị + cậu/em",
     "glossary": [("Mira", "Mira", "person"), ("Leo", "Leo", "person")],
     "characters": ["Mira: female captain, about 30, Leo's commander. Calls herself 'tôi' or 'chị' and calls Leo 'cậu' or 'em'.",
                    "Leo: male soldier, 19, under Mira's command. Calls himself 'em' and calls Mira 'chị' or 'đội trưởng'."],
     "lines": [("Leo", "Captain, the scouts are back. They found the enemy camp."),
               ("Mira", "Good work, Leo. Tell them to rest. We move at dawn."),
               ("Leo", "Yes, ma'am! And... thank you for trusting me."),
               ("Mira", "Don't thank me yet. Just come back alive.")]},
    {"name": "ja_grandma", "lang": "ja",
     "expect": "grandmother and grandchild: bà/cháu both ways; the context run also carries one history line",
     "glossary": [("ハルト", "Haruto", "person")],
     "characters": ["おばあちゃん (Bà): Haruto's grandmother, gentle. Calls herself 'bà' and calls Haruto 'cháu'.",
                    "ハルト (Haruto): boy, 8, her grandson. Calls himself 'cháu' and calls her 'bà'."],
     "history": [("おばあちゃん", "ハルト、ご飯できたよ。", "Haruto ơi, cơm chín rồi, vào ăn đi cháu.")],
     "lines": [("ハルト", "ありがとう、おばあちゃん！今行く！"), ("おばあちゃん", "手を洗ってからね。"),
               ("ハルト", "はーい！")]},
]

# Constrained-decoding canary: the value "zq-17" only appears if the schema is really enforced.
_CANARY_SCHEMA = {"type": "object", "properties": {"canary": {"const": "zq-17"}, "vi": {"type": "string"}},
                  "required": ["canary", "vi"], "additionalProperties": False}
JSON_VARIANTS = {
    "response_format json_schema (OpenAI shape)":
        {"response_format": {"type": "json_schema",
                             "json_schema": {"name": "t", "strict": True, "schema": _CANARY_SCHEMA}}},
    "response_format json_object + schema":
        {"response_format": {"type": "json_object", "schema": _CANARY_SCHEMA}},
    "response_format json_schema + schema (no json_schema wrapper)":
        {"response_format": {"type": "json_schema", "schema": _CANARY_SCHEMA}},
    "top-level json_schema": {"json_schema": _CANARY_SCHEMA},
    "top-level grammar (GBNF)":
        {"grammar": 'root ::= "{\\"canary\\": \\"zq-17\\", \\"vi\\": \\"" [^"]* "\\"}"'},
}
_NO_THINK_VARIANTS = {
    "chat_template_kwargs enable_thinking=false": {"chat_template_kwargs": {"enable_thinking": False}},
    "reasoning_format=none": {"reasoning_format": "none"},
    "reasoning_effort=none": {"reasoning_effort": "none"},
}


def screen_schema(n: int) -> dict:
    """{"1": str, ..., "n": str}, all required and in order: the model must answer every line once."""
    keys = [str(i) for i in range(1, n + 1)]
    return {"type": "object", "properties": {k: {"type": "string"} for k in keys}, "required": keys,
            "additionalProperties": False}


def screen_messages_system(scene: dict, with_context: bool) -> list[dict]:
    """Generic shape: rules in a system prompt, the screen as "id | speaker | text" lines."""
    parts = []
    if with_context:
        parts.append("Glossary:\n" + "\n".join(f"{s} -> {t} ({k})" for s, t, k in scene["glossary"]))
        parts.append("Characters:\n" + "\n".join(f"- {c}" for c in scene["characters"]))
        if scene.get("history"):
            parts.append("Recent dialogue (oldest first, source => Vietnamese):\n"
                         + "\n".join(f"{sp}: {src} => {vi}" for sp, src, vi in scene["history"]))
    parts.append("Screen (id | speaker | text):\n"
                 + "\n".join(f"{i} | {sp} | {tx}" for i, (sp, tx) in enumerate(scene["lines"], 1)))
    return [{"role": "system", "content": SCREEN_SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}]


def screen_messages_hymt(scene: dict, with_context: bool) -> list[dict]:
    """Hy-MT2's own instruction shapes (model card: background information, reference translations,
    structured data), user turn only: the screen is a JSON object id -> source text."""
    who = [f"Line {i} is spoken by {sp}." for i, (sp, _) in enumerate(scene["lines"], 1)]
    background = (["Characters:", *[f"- {c}" for c in scene["characters"]]] if with_context else [])
    if with_context and scene.get("history"):
        background += ["Recent dialogue (source => Vietnamese):",
                       *[f"{sp}: {src} => {vi}" for sp, src, vi in scene["history"]]]
    background += who
    parts = ["[Background Information]\n" + "\n".join(background)]
    if with_context:
        parts.append("Reference the following translations:\n"
                     + "\n".join(f"{s} translates to {t}" for s, t, _ in scene["glossary"]))
    parts.append("Please translate the values of the following JSON into Vietnamese, taking the provided background "
                 "information into consideration. Keep the keys unchanged. Choose Vietnamese pronouns and forms of "
                 "address from who is speaking to whom.")
    parts.append("[Source Text]\n" + json.dumps({str(i): tx for i, (_, tx) in enumerate(scene["lines"], 1)},
                                                ensure_ascii=False))
    return [{"role": "user", "content": "\n\n".join(parts)}]


SCREEN_FORMATS = {"system+lines": screen_messages_system, "hymt-native": screen_messages_hymt}


def line_issues(vi: str | None, speaker: str, scene: dict) -> list[str]:
    """Cheap automatic checks of one translated line (misalignment, echoed labels, leftovers)."""
    if not vi or not vi.strip():
        return ["missing"]
    v = vi.strip()
    names = {speaker, *(t for s, t, _ in scene["glossary"] if s == speaker)}
    issues = []
    if v in names:
        issues.append("speaker name instead of the line")
    elif any(v.startswith(n + sep) for n in names for sep in (":", " |", "|")) or re.match(r"\d+\s*\|", v):
        issues.append("speaker/id label copied")
    if any(0x3040 <= ord(ch) <= 0x30FF or 0x4E00 <= ord(ch) <= 0x9FFF for ch in v):
        issues.append("Japanese left in the output")
    if re.search(r"[\"“”]\s*[,}]\s*$|“\s*$", v):
        issues.append("JSON quote debris")
    return issues


def _chat(client: httpx.Client, base_url: str, body: dict) -> tuple[int, str | None, float, dict]:
    t0 = time.perf_counter()
    resp = client.post(base_url + "/v1/chat/completions", json=body)
    dt = time.perf_counter() - t0
    try:
        data = resp.json()
    except ValueError:
        return resp.status_code, resp.text[:300], dt, {}
    if resp.status_code != 200:
        return resp.status_code, json.dumps(data, ensure_ascii=False)[:300], dt, {}
    return 200, data["choices"][0]["message"].get("content"), dt, data.get("timings") or {}


def _median(values: list[float]) -> float:
    s = sorted(values)
    return s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2


def bench_speed(client: httpx.Client, base_url: str, runs: int = 3) -> dict:
    """Prompt-processing and generation tok/s on a ~1k-token prompt and 256 generated tokens."""
    para = ("ねえ、あたしのこと、ほんとに忘れちゃったの？ The eastern gate has fallen, what are your orders? "
            "雨が止むまで、ここで待とう。Don't worry about it, kid. ")
    prompt = para * 24
    samples = []
    for i in range(runs + 1):
        body = {"prompt": prompt + f" [{i}]", "n_predict": 256, "ignore_eos": True, "cache_prompt": False,
                "temperature": 0.7, "seed": i}
        t = client.post(base_url + "/completion", json=body).json()["timings"]
        if i:  # first run is warm-up
            samples.append(t)
    return {"prompt_tokens": samples[0]["prompt_n"], "gen_tokens": samples[0]["predicted_n"], "runs": runs,
            "pp_tok_s": round(_median([s["prompt_per_second"] for s in samples]), 1),
            "tg_tok_s": round(_median([s["predicted_per_second"] for s in samples]), 1)}


def bench_json(client: httpx.Client, base_url: str) -> dict:
    msgs = [{"role": "system", "content": "Translate the user's text into Vietnamese and answer as JSON "
                                          "with the translation in \"vi\"."},
            {"role": "user", "content": "セーブしますか？"}]
    out = {}
    for name, extra in JSON_VARIANTS.items():
        status, content, dt, _ = _chat(client, base_url, {"messages": msgs, "max_tokens": 80, **SAMPLING, **extra})
        try:
            parsed = json.loads(content or "")
            enforced = isinstance(parsed, dict) and parsed.get("canary") == "zq-17"
        except ValueError:
            enforced = False
        out[name] = {"status": status, "schema_enforced": enforced, "content": content, "ms": round(dt * 1000)}
    for name, extra in _NO_THINK_VARIANTS.items():
        status, content, dt, _ = _chat(client, base_url, {"messages": msgs[1:], "max_tokens": 60, **SAMPLING, **extra})
        out[name] = {"status": status, "accepted": status == 200, "content": content, "ms": round(dt * 1000)}
    return out


def load_probe_segments() -> list[dict]:
    """PROBE_IDS from the eval set, with Google's translation from the last eval_translate run (no new traffic)."""
    from bench.common import RESULTS_DIR  # noqa: PLC0415 - bench-only dependencies
    from bench.eval_translate import SEGMENTS_CSV, load_segments  # noqa: PLC0415
    segs = {s["id"]: s for s in load_segments(SEGMENTS_CSV)}
    gtx: dict[str, str] = {}
    sheet = RESULTS_DIR / "eval_translate.csv"
    if sheet.exists():
        import csv  # noqa: PLC0415
        with open(sheet, encoding="utf-8-sig", newline="") as fh:
            gtx = {row["id"]: row.get("gtx_vi", "") for row in csv.DictReader(fh)}
    return [{**segs[i], "gtx_vi": gtx.get(i, "")} for i in PROBE_IDS if i in segs]


def bench_probe(client: httpx.Client, base_url: str, segments: list[dict]) -> list[dict]:
    from bench.eval_translate import chrf  # noqa: PLC0415
    rows = []
    for seg in segments:
        msgs = [{"role": "system", "content": SIMPLE_SYSTEM.format(lang=LANG_NAMES[seg["lang"]])},
                {"role": "user", "content": seg["source"]}]
        status, content, dt, tm = _chat(client, base_url, {"messages": msgs, "max_tokens": 200, **SAMPLING})
        vi = (content or "").strip() if status == 200 else f"<error {status}: {content}>"
        ref = seg.get("reference_vi", "")
        rows.append({"id": seg["id"], "lang": seg["lang"], "category": seg["category"], "source": seg["source"],
                     "reference_vi": ref, "gtx_vi": seg.get("gtx_vi", ""), "vi": vi, "latency_ms": round(dt * 1000),
                     "prompt_tokens": tm.get("prompt_n"), "gen_tokens": tm.get("predicted_n"),
                     "chrf": round(chrf(vi, ref), 1) if ref else None,
                     "gtx_chrf": round(chrf(seg["gtx_vi"], ref), 1) if ref and seg.get("gtx_vi") else None})
    return rows


def bench_dialogues(client: httpx.Client, base_url: str) -> list[dict]:
    out = []
    for fmt, build in SCREEN_FORMATS.items():
        for scene in DIALOGUES:
            for with_context in (False, True):
                body = {"messages": build(scene, with_context), "max_tokens": 600, **SAMPLING,
                        "response_format": {"type": "json_schema", "json_schema": {
                            "name": "screen", "strict": True, "schema": screen_schema(len(scene["lines"]))}}}
                status, content, dt, tm = _chat(client, base_url, body)
                try:
                    parsed = json.loads(content or "")
                except ValueError:
                    parsed = None
                lines = []
                for i, (sp, tx) in enumerate(scene["lines"], 1):
                    vi = parsed.get(str(i)) if isinstance(parsed, dict) else None
                    lines.append({"speaker": sp, "source": tx, "vi": vi, "issues": line_issues(vi, sp, scene)})
                out.append({"format": fmt, "name": scene["name"], "with_context": with_context, "status": status,
                            "valid_json": isinstance(parsed, dict),
                            "clean_lines": sum(not ln["issues"] for ln in lines), "n_lines": len(lines),
                            "latency_ms": round(dt * 1000), "prompt_tokens": tm.get("prompt_n"),
                            "gen_tokens": tm.get("predicted_n"), "lines": lines,
                            "raw": None if isinstance(parsed, dict) else content})
    return out


def bench_model(spec: ModelSpec, segments: list[dict]) -> dict:
    path = MODELS_DIR / spec.file
    vram_before = gpu_memory_used_mib()
    server = LlamaServer(path)
    result: dict = {"file": spec.file, "repo": spec.repo, "revision": spec.revision, "size_bytes": spec.size,
                    "sha256": spec.sha256, "license": spec.license, "params": spec.params,
                    "command": " ".join(server.args).replace(f"--port {server.port}", "--port <free port>")}
    print(f"[{spec.role}] {spec.file}: starting llama-server on 127.0.0.1:{server.port}")
    with server, httpx.Client(timeout=180.0) as client:
        vram_loaded = gpu_memory_used_mib()
        props = client.get(server.base_url + "/props").json()
        result.update({"load_s": round(server.load_s, 2), "vram_before_mib": vram_before,
                       "vram_loaded_mib": vram_loaded,
                       "vram_delta_mib": (vram_loaded - vram_before) if None not in (vram_loaded, vram_before) else None,
                       "build_info": props.get("build_info"), "chat_template_caps": props.get("chat_template_caps")})
        print(f"  loaded in {server.load_s:.2f} s, VRAM {vram_before} -> {vram_loaded} MiB")
        result["speed"] = bench_speed(client, server.base_url)
        print(f"  pp {result['speed']['pp_tok_s']} tok/s, tg {result['speed']['tg_tok_s']} tok/s")
        result["json"] = bench_json(client, server.base_url)
        result["probe"] = bench_probe(client, server.base_url, segments)
        lat = [r["latency_ms"] for r in result["probe"]]
        print(f"  probe: {len(lat)} lines, latency p50 {_median(lat):.0f} ms, max {max(lat)} ms")
        result["dialogues"] = bench_dialogues(client, server.base_url)
        per_format = {}
        for fmt in SCREEN_FORMATS:
            runs = [d for d in result["dialogues"] if d["format"] == fmt]
            per_format[fmt] = {"requests": len(runs), "valid_json": sum(d["valid_json"] for d in runs),
                               "clean_lines": sum(d["clean_lines"] for d in runs),
                               "lines": sum(d["n_lines"] for d in runs),
                               "latency_p50_ms": _median([d["latency_ms"] for d in runs])}
            print(f"  screens [{fmt}]: {per_format[fmt]['valid_json']}/{len(runs)} valid JSON, "
                  f"{per_format[fmt]['clean_lines']}/{per_format[fmt]['lines']} clean lines, "
                  f"latency p50 {per_format[fmt]['latency_p50_ms']:.0f} ms")
        result["vram_after_bench_mib"] = gpu_memory_used_mib()
        buffer_lines = [ln.split(" I ", 1)[-1].strip() for ln in server.log_tail(2000).splitlines()
                        if "buffer size" in ln][:10]
    result["server_log_buffers"] = buffer_lines
    time.sleep(1.0)
    result["vram_after_stop_mib"] = gpu_memory_used_mib()
    chrfs = [r["chrf"] for r in result["probe"] if r["chrf"] is not None]
    result["summary"] = {"probe_latency_p50_ms": _median(lat), "probe_latency_max_ms": max(lat),
                         "probe_chrf_mean": round(sum(chrfs) / max(1, len(chrfs)), 1), "screens": per_format}
    return result


def _md_cell(text: str | None) -> str:
    return (text or "").replace("|", "\\|").replace("\n", "<br>")


def write_bench_markdown(path: Path, data: dict) -> None:
    models = data["models"]
    roles = list(models)
    L = [f"# Local LLM setup: measurements ({data['created']})", "",
         f"Machine: {data['machine'].get('gpu')}, Windows build {data['machine'].get('windows_build')}. "
         f"llama.cpp {data['llama_cpp']['release']} ({data['llama_cpp']['tag']}, CUDA {data['llama_cpp']['cuda']}).",
         "Sampling for every probe: temperature 0 (greedy), repeat_penalty 1.05. Load time is with the GGUF in the "
         "OS file cache (warm); VRAM is the whole-GPU delta from nvidia-smi (other apps may move it a little).", "",
         "## Summary", "",
         "| role | file | size | load | VRAM used | prompt tok/s | gen tok/s | 1 line p50 / max |"
         + "".join(f" screen [{f}] p50, clean lines |" for f in SCREEN_FORMATS),
         "|---|---|---|---|---|---|---|---|" + "---|" * len(SCREEN_FORMATS)]
    for role in roles:
        m = models[role]
        s = m["summary"]
        L.append(f"| {role} | {m['file']} | {_fmt_size(m['size_bytes'])} | {m['load_s']} s | "
                 f"+{m['vram_delta_mib']} MiB | {m['speed']['pp_tok_s']:.0f} | {m['speed']['tg_tok_s']:.0f} | "
                 f"{s['probe_latency_p50_ms']:.0f} / {s['probe_latency_max_ms']} ms |"
                 + "".join(f" {s['screens'][f]['latency_p50_ms']:.0f} ms, {s['screens'][f]['clean_lines']}/"
                           f"{s['screens'][f]['lines']} |" for f in SCREEN_FORMATS))
    L += ["", f"Speed: `/completion`, ~{models[roles[0]]['speed']['prompt_tokens'] if roles else '?'}-token prompt, "
          "256 generated tokens, median of 3 after a warm-up. Screens: 3-4 dialogue lines in one request, "
          "JSON constrained by `screen_schema`; a line is *clean* if the automatic checks (`line_issues`: "
          "missing, speaker label copied, Japanese left, JSON quote debris) find nothing - pronoun correctness "
          "is judged by reading the tables below.", "",
          "VRAM after the server stopped: " + ", ".join(
              f"{r} {models[r]['vram_after_stop_mib']} MiB (before load {models[r]['vram_before_mib']})" for r in roles)
          + ".", "",
          "Server command (port chosen at random among free ports):", "", "```",
          *[models[r]["command"] for r in roles], "```", "",
          "## JSON schema / grammar request fields (OpenAI-compatible /v1/chat/completions)", "",
          "| request field | " + " | ".join(roles) + " |", "|---|" + "---|" * len(roles)]
    for name in JSON_VARIANTS:
        L.append(f"| `{name}` | " + " | ".join(
            ("enforced" if models[r]["json"][name]["schema_enforced"] else
             f"NOT enforced: `{_md_cell(models[r]['json'][name]['content'])}`") for r in roles) + " |")
    for name in _NO_THINK_VARIANTS:
        L.append(f"| `{name}` | " + " | ".join(
            ("accepted" if models[r]["json"][name]["accepted"] else f"HTTP {models[r]['json'][name]['status']}")
            for r in roles) + " |")
    L += ["", "## Quality probe: single lines, simple system prompt", "",
          "| id | source | Google (gtx) | " + " | ".join(roles) + " |", "|---|---|---|" + "---|" * len(roles)]
    for i, seg in enumerate(models[roles[0]]["probe"] if roles else []):
        cells = []
        for r in roles:
            row = models[r]["probe"][i]
            extra = f" ({row['latency_ms']} ms" + (f", chrF {row['chrf']}" if row["chrf"] is not None else "") + ")"
            cells.append(_md_cell(row["vi"]) + extra)
        gtx = _md_cell(seg["gtx_vi"]) + (f" (chrF {seg['gtx_chrf']})" if seg["gtx_chrf"] is not None else "")
        L.append(f"| {seg['id']} | {_md_cell(seg['source'])} | {gtx} | " + " | ".join(cells) + " |")
    L += ["", "## Dialogue snippets: one JSON request per screen, pronouns depend on the speakers", "",
          "Each screen is sent once without context and once with the glossary + character notes, in two prompt "
          "shapes: `system+lines` (rules in `SCREEN_SYSTEM`, screen as `id | speaker | text`) and `hymt-native` "
          "(Hy-MT2's own background-information / reference-translation / JSON-values instructions, user turn "
          "only). Output constrained by `screen_schema`. Cells marked [!] failed an automatic check.", ""]
    for fmt in SCREEN_FORMATS:
        for scene in DIALOGUES:
            L += [f"### {scene['name']}, {fmt} (expected: {scene['expect']})", ""]
            runs = {(r, d["with_context"]): d for r in roles for d in models[r]["dialogues"]
                    if d["name"] == scene["name"] and d["format"] == fmt}
            heads = [f"{r} {'ctx' if c else 'no ctx'}" for r in roles for c in (False, True)]
            L += ["| speaker | source | " + " | ".join(heads) + " |", "|---|---|" + "---|" * len(heads)]
            for k, (sp, tx) in enumerate(scene["lines"]):
                cells = []
                for r in roles:
                    for c in (False, True):
                        line = runs[(r, c)]["lines"][k]
                        flag = f" [!] {', '.join(line['issues'])}" if line["issues"] else ""
                        cells.append(_md_cell(line["vi"]) + flag)
                L.append(f"| {sp} | {_md_cell(tx)} | " + " | ".join(cells) + " |")
            L.append("| latency | | " + " | ".join(f"{runs[(r, c)]['latency_ms']} ms"
                                                   for r in roles for c in (False, True)) + " |")
            L.append("")
    if data.get("notes"):
        L += ["## Notes", ""] + [f"- {n}" for n in data["notes"]] + [""]
    path.write_text("\n".join(L), encoding="utf-8")


def run_bench(roles: list[str]) -> int:
    from app.config import Config  # noqa: PLC0415
    from app.ui.game_guard import GameGuard, matches  # noqa: PLC0415
    exe = GameGuard.foreground_process_name()
    if matches(exe, Config.load().blocked_processes):
        print(f"a blocked game ({exe}) is in the foreground: not loading the GPU now, try again later")
        return 2
    ok, msg = verify_llama()
    if not ok:
        raise SetupError(f"llama.cpp: {msg} (run python -m tools.setup_llm first)")
    from bench.common import RESULTS_DIR, save_results  # noqa: PLC0415
    info = _read_install_info() or {}
    segments = load_probe_segments()
    models = {}
    for role in roles:
        spec = MODELS[role]
        if not (MODELS_DIR / spec.file).is_file():
            print(f"[{role}] {spec.file} not installed, skipped")
            continue
        models[role] = bench_model(spec, segments)
    data = {"llama_cpp": {"release": info.get("release"), "tag": info.get("tag"), "cuda": info.get("cuda"),
                          "version": server_version(), "assets": info.get("assets")},
            "sampling": SAMPLING, "simple_system_prompt": SIMPLE_SYSTEM, "screen_system_prompt": SCREEN_SYSTEM,
            "models": models, "notes": BENCH_NOTES}
    path = save_results("llm_setup", data)
    data = json.loads(path.read_text(encoding="utf-8"))
    md = RESULTS_DIR / "llm_setup.md"
    write_bench_markdown(md, data)
    print(f"wrote {path} and {md}")
    return 0


BENCH_NOTES: list[str] = [
    "llama.cpp's latest stable release is v0.7.0 (2026-10-15); it only carries nightly-tag.txt = b11669, so the "
    "binaries are build b11669's (CUDA 13.4 zip + cudart zip, SHA-256 checked against the GitHub API digest). "
    "This version's default server port is 9931; we always pass --host 127.0.0.1 --port <free port>.",
    "JSON schema: the server reads the schema from `response_format.json_schema.schema` (OpenAI shape), from "
    "`response_format.schema` with type json_object, or from a top-level `json_schema`; `grammar` (GBNF) also "
    "works. `{\"type\": \"json_schema\", \"schema\": ...}` (the shape in the server README) is silently treated as "
    "plain JSON mode.",
    "Thinking: Hy-MT2 has none (its chat template has no reasoning block; `chat_template_caps` shows no reasoning "
    "support). The thinking switches are accepted and ignored, so no --reasoning flag is needed for these models.",
    "Prompt shape matters per model: the 1.8B breaks the generic system-prompt + `id | speaker | text` shape "
    "(copies labels, shifts lines) but is clean with Hy-MT2's own model-card shape (user turn only, background "
    "information + reference translations + JSON values); the 7B is clean with the generic shape but in the "
    "native JSON shape sometimes ends Japanese lines with curly-quote debris (`“,` / `“}`) that has to be "
    "stripped.",
    "Pronouns: the 1.8B mostly ignores the character notes (anh chị, tôi/bạn); the 7B follows them in most lines "
    "(chị/em, tao/mày) but not all (俺 -> tao in a senpai scene in the generic shape; 先輩、大好きです -> "
    "'Anh yêu quý lắm!'). Without notes both models guess from the Japanese (先輩 -> senpai/chị/sếp).",
    "Not usable in mainline llama.cpp: Hy-MT2-1.8B 1.25-bit / 2-bit GGUFs (need Tencent's STQ kernels, "
    "PR #22836 still open, CUDA PR #23505 closed unmerged). Hy-MT2-7B Q6_K (6.2 GB) and Qwen3.5-9B Q4_K_M "
    "(5.7 GB) do not fit the 6.5 GB budget next to a light model.",
    "Load times are warm (GGUF in the OS file cache after download); a cold load from disk adds roughly "
    "file size / disk read speed. GPU contention with a running game was not measured.",
]


# ---------------------------------------------------------------------------------------------
# CLI


def cmd_list(cuda: str) -> int:
    build = pick_build(cuda) if cuda != "auto" or nvidia_driver_major() else LLAMA_BUILDS["13"]
    info = _read_install_info()
    print(f"LLM home: {LLM_HOME}")
    print(f"llama.cpp {build.release} ({build.tag}) CUDA {build.cuda}: "
          + ", ".join(f"{a.name} ({_fmt_size(a.size)})" for a in build.assets))
    print(f"  installed: {(info['release'] + ' ' + info['tag'] + ' CUDA ' + info['cuda']) if info else 'no'}")
    for role, spec in MODELS.items():
        path = MODELS_DIR / spec.file
        state = "installed" if path.is_file() and path.stat().st_size == spec.size else "not installed"
        default = " [default]" if spec.file == DEFAULT_MODEL else ""
        print(f"{role:8} {spec.file:28} {_fmt_size(spec.size):>8}  {spec.license:10} {state}{default}")
        print(f"         {spec.repo}@{spec.revision[:10]}  {spec.notes}")
    return 0


def cmd_verify() -> int:
    ok, msg = verify_llama()
    print(f"llama.cpp: {'OK' if ok else 'FAIL'} - {msg}")
    if ok:
        for line in server_version().splitlines():
            print(f"  {line}")
    all_ok = ok
    for role, spec in MODELS.items():
        mok, mmsg = verify_model(spec)
        if mmsg != "not installed":
            all_ok &= mok
        print(f"{role:8} {spec.file}: {'OK' if mok else '--' if mmsg == 'not installed' else 'FAIL'} - {mmsg}")
    return 0 if all_ok else 1


def cmd_install(roles: list[str], cuda: str, force: bool, with_llama: bool) -> int:
    LLM_HOME.mkdir(parents=True, exist_ok=True)
    results: list[str] = []
    if with_llama:
        build = pick_build(cuda)
        print(f"llama.cpp {build.release} ({build.tag}), CUDA {build.cuda}")
        results.append("llama.cpp: " + install_llama(build, force))
    for role in roles:
        spec = MODELS[role]
        print(f"{role}: {spec.file} from {spec.repo}")
        results.append(f"{role}: " + install_model(spec, force))
    print()
    for line in results:
        print(line)
    if with_llama:
        print("llama-server --version:")
        for line in server_version().splitlines():
            print(f"  {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--models", default=",".join(MODELS),
                    help=f"comma-separated roles to install ({', '.join(MODELS)}); 'none' for none")
    ap.add_argument("--list", action="store_true", help="show pinned and installed files")
    ap.add_argument("--verify", action="store_true", help="re-hash installed files")
    ap.add_argument("--bench", action="store_true", help="smoke test + quality probe of installed models")
    ap.add_argument("--cuda", choices=["auto", "13", "12"], default="auto")
    ap.add_argument("--force", action="store_true", help="download again even if present")
    ap.add_argument("--no-llama", action="store_true", help="skip llama.cpp")
    args = ap.parse_args(argv)
    roles = [] if args.models.strip().lower() == "none" else [r.strip() for r in args.models.split(",") if r.strip()]
    unknown = [r for r in roles if r not in MODELS]
    if unknown:
        ap.error(f"unknown model role(s): {', '.join(unknown)} (known: {', '.join(MODELS)})")
    try:
        if args.list:
            return cmd_list(args.cuda)
        if args.verify:
            return cmd_verify()
        if args.bench:
            return run_bench(roles)
        return cmd_install(roles, args.cuda, args.force, not args.no_llama)
    except SetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted (partial downloads resume next time)", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
