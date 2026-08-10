"""Settings + secret loading (SPEC §11). Reads .env, applies defaults, auto-detects ffmpeg.

Calibratable thresholds (D3/D5/D6) live here as named, overridable defaults seeded with the
SPEC's documented *guesses* — tuned on real lectures in build steps 3-4, not law.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from . import __version__

TOOL_VERSION = __version__
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REFERER = "https://www.bilibili.com"


def find_ffmpeg() -> str | None:
    """Locate ffmpeg: PATH first, then the winget install location (no shell refresh needed)."""
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    env = os.environ.get("FFMPEG_PATH")
    if env and Path(env).exists():
        return env
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
        for exe in pkgs.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"):
            return str(exe)
    return None


def find_aria2c() -> str | None:
    """Locate aria2c (preferred downloader for throttled bilibili CDNs): PATH then winget."""
    on_path = shutil.which("aria2c")
    if on_path:
        return on_path
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
        for exe in pkgs.glob("aria2.aria2*/**/aria2c.exe"):
            return str(exe)
    return None


def _winget_glob(pattern: str) -> str | None:
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
    return next((str(exe) for exe in pkgs.glob(pattern)), None)


def find_js_runtime() -> tuple[str, str] | None:
    """Locate a JavaScript runtime for yt-dlp (issue #5). yt-dlp needs one to drive YouTube's real
    web player client; without it extraction intermittently degrades to a stripped/blocked
    response (placeholder title, no duration/subtitles). Returns (name, path) or None.

    deno is preferred (yt-dlp's default-priority runtime); node is a working fallback. Each is
    looked up on PATH first, then in its out-of-band install location, since neither the deno.land
    scripted installer nor winget necessarily touches this process's PATH."""
    exe = ".exe" if os.name == "nt" else ""

    deno = shutil.which("deno")
    if deno:
        return ("deno", deno)
    # deno.land installer: $DENO_INSTALL (default ~/.deno), binary at bin/deno[.exe].
    deno_root = os.environ.get("DENO_INSTALL") or str(Path.home() / ".deno")
    deno_exe = Path(deno_root) / "bin" / f"deno{exe}"
    if deno_exe.exists():
        return ("deno", str(deno_exe))
    winget_deno = _winget_glob("DenoLand.Deno*/**/deno.exe")
    if winget_deno:
        return ("deno", winget_deno)

    node = shutil.which("node")
    if node:
        return ("node", node)
    winget_node = _winget_glob("OpenJS.NodeJS*/**/node.exe")
    if winget_node:
        return ("node", winget_node)
    return None


@dataclass
class QualityThresholds:
    """D5 starting guesses — any single metric tripping falls back to Whisper. Calibrate step 3."""

    punct_density_min: float = 0.04  # punctuation marks per char
    dup_ratio_max: float = 0.30  # fraction of duplicated/near-repeated segments
    nonzh_ratio_max: float = 0.20  # fraction of non-CJK "garbage" chars
    cps_min: float = 1.0  # chars per second, lower bound
    cps_max: float = 8.0  # chars per second, upper bound


@dataclass
class AutoSubNet:
    """Structural validity net for YouTube auto-captions (SPEC §6). Language-agnostic — no
    per-language calibration, so unlike QualityThresholds it can't misfire across languages. Any
    single check failing falls the candidate back to Whisper. Starting guesses; calibrate later."""

    min_cues: int = 5          # a real track has more than a handful of cues
    coverage_min: float = 0.70  # last cue / duration, lower bound (truncation guard)
    coverage_max: float = 1.10  # last cue / duration, upper bound
    cps_min: float = 0.5        # chars/sec over the whole track; ~0 means music/silence, no speech


@dataclass
class OCRSettings:
    """Hard-subtitle OCR stage tuning (Phase C). The stage shells out to an isolated OCR worker
    (``scripts/ocr_worker.py``) running in ``.ocr-venv`` so rapidocr-onnxruntime/opencv never
    touches blisolver's main Python — same isolation model as the whisper-cli transcribe shim.
    All defaults calibrated end-to-end on a 261s burned-in-sub test video (BV1LD7U65Ew2)."""

    fps: float = 2.0           # dense sample cadence per second (subs display 2-4s; 2fps is enough)
    band_top: float = 0.78     # bottom-band crop top (fraction of frame height)
    band_bottom: float = 0.96  # bottom-band crop bottom
    min_conf: float = 0.50     # drop OCR lines below this recognition confidence
    sim_threshold: float = 0.80  # text similarity for joining consecutive samples into one cue
    min_dur: float = 0.30      # drop cues shorter than this (s)
    diff_threshold: float = 6.0  # mean-abs-gray diff gating OCR (skip frames where band unchanged)
    detect_interval: float = 30.0  # seconds between hardsub-detection probes


@dataclass
class Settings:
    # vision / LM Studio
    lmstudio_base_url: str = "http://localhost:1234/v1"
    lmstudio_api_key: str = ""
    lmstudio_vision_model: str = ""
    lmstudio_danmaku_model: str = ""
    lmstudio_danmaku_max_tokens: int = 8192

    # auth (D9)
    sessdata: str | None = None
    cookies_browser: str = "chrome"  # yt-dlp surfaces bilibili AI subs via chrome cookies (2026-07)
    cookies_profile: str = ""
    # YouTube is cookie-free by default (issue #1: a logged-in browser session breaks yt-dlp's
    # default format selection). Opt in with BLISOLVER_YT_COOKIES to unlock gated YouTube content.
    youtube_cookies: bool = False

    # hard-subtitle OCR (Phase C) — paths autodetected at load() to <repo>/scripts/ocr_worker.py
    # and <repo>/.ocr-venv/bin/python; None when absent (the OCR stage then no-ops with a clear
    # diagnostic instead of crashing ingest). Env-overridable via BLISOLVER_OCR_WORKER / BLISOLVER_OCR_VENV_PYTHON.
    ocr_worker_path: str | None = None
    ocr_venv_python: str | None = None
    ocr: OCRSettings = field(default_factory=OCRSettings)

    # paths
    # data_dir is the writable root for generated state. Defaults to the repository for a
    # developer checkout; a conformant Agent Plugins client points it at PLUGIN_DATA so bundles
    # and caches survive a plugin update (§9.1). See _resolve_data_dirs for precedence.
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT)
    cache_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "cache")
    out_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "out")
    ffmpeg_path: str | None = None
    aria2c_path: str | None = None
    js_runtime: tuple[str, str] | None = None  # (name, path) for yt-dlp, issue #5

    # frames (D3/D6) — defaults are guesses, tuned step 4
    sample_interval_s: float = 6.0  # periodic frame sampling cadence (slide-deck recordings)
    scene_threshold: float = 27.0  # PySceneDetect ContentDetector default (secondary signal)
    # hamming collapse; calibrated step 4 (within-slide jitter <10, slide cut >=16)
    phash_dedup_threshold: int = 10
    chunk_window_s: float = 60.0  # D3 wall-clock chunk fallback; 60s = minute-aligned buckets
    # Danmaku bucket width — deliberately narrower than chunk_window_s and INDEPENDENT of it.
    # 15s tracks the crowd's real pace: an empirical probe (6 videos, 5.3k danmaku) found one
    # coherent "beat" (a joke-phase, meme-mutation step, or claim+rebuttal) spans ~7-20s, so 15s
    # holds ~one beat without merging distinct ones (30s+ merges; 75s collapses floods to 1-2
    # mega-buckets). Sparse videos stay fragmented at any width — that's honest, not an artifact.
    danmaku_window_s: float = 15.0

    # Per-window cap on ORDINARY danmaku lines rendered in bundle.md (the primary Atlas
    # ingestion surface); bundle.json always carries the complete, uncapped set. A deliberate
    # gestalt-sample dial, NOT a pathological ceiling: Atlas Gate 2 needs the crowd's per-beat
    # gestalt, not exhaustiveness. 15 nearly halves the section (empirical: ~3.2k→~1.8k lines
    # over a 3.7k-danmaku demo, 2026-07) while keeping ~15 distinct reactions per hot 15s beat.
    danmaku_md_cap: int = 15

    # quality gate (D5)
    quality: QualityThresholds = field(default_factory=QualityThresholds)

    # YouTube auto-caption structural net (SPEC §6) — separate from the CJK quality gate above
    youtube_auto: AutoSubNet = field(default_factory=AutoSubNet)

    tool_version: str = TOOL_VERSION

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env")
        s = cls(
            lmstudio_base_url=os.environ.get("LMSTUDIO_BASE_URL", cls.lmstudio_base_url),
            lmstudio_api_key=os.environ.get("LMSTUDIO_API_KEY", ""),
            lmstudio_vision_model=os.environ.get("LMSTUDIO_VISION_MODEL", ""),
            lmstudio_danmaku_model=os.environ.get("BLISOLVER_DANMAKU_MODEL", ""),
            lmstudio_danmaku_max_tokens=int(
                os.environ.get("BLISOLVER_DANMAKU_MAX_TOKENS", cls.lmstudio_danmaku_max_tokens)
            ),
            danmaku_window_s=float(
                os.environ.get("BLISOLVER_DANMAKU_WINDOW_S", cls.danmaku_window_s)
            ),
            danmaku_md_cap=int(
                os.environ.get("BLISOLVER_DANMAKU_MD_CAP", cls.danmaku_md_cap)
            ),
            sessdata=os.environ.get("SESSDATA") or None,
            cookies_browser=os.environ.get("BLISOLVER_COOKIES_BROWSER", cls.cookies_browser),
            cookies_profile=os.environ.get("BLISOLVER_COOKIES_PROFILE", ""),
            youtube_cookies=os.environ.get("BLISOLVER_YT_COOKIES", "").strip().lower()
            in ("1", "true", "yes", "on"),
        )
        # cache/out placement is decided in one place (see _resolve_data_dirs) so the explicit
        # per-directory overrides, the single-root override, and PLUGIN_DATA cannot disagree.
        s = _resolve_data_dirs(s)
        s.ffmpeg_path = find_ffmpeg()
        s.aria2c_path = find_aria2c()
        s.js_runtime = find_js_runtime()
        s = _resolve_ocr_paths(s)
        return s


def _resolve_data_dirs(s: "Settings") -> "Settings":
    """Decide where cache/ and out/ live.

    Agent Plugins 1.0.0 §9.1 designates `PLUGIN_DATA` as the client-managed directory for exactly
    this kind of state: it is writable, dedicated to the installed plugin instance, and preserved
    across plugin updates. `PLUGIN_ROOT` is package contents and may be replaced wholesale on an
    update, so writing generated bundles there would lose them.

    Precedence, most specific first:
      1. BLISOLVER_CACHE_DIR / BLISOLVER_OUT_DIR — explicit, per-directory override
      2. BLISOLVER_DATA_DIR/{cache,out}          — explicit, single-root override
      3. PLUGIN_DATA/{cache,out}                 — supplied by a conformant plugin client
      4. <repo>/{cache,out}                      — developer checkout default

    A conformant client sets PLUGIN_DATA only for plugin subprocesses, so a hand-run CLI in a
    checkout keeps using the repository directories and nothing moves under a developer's feet.
    """
    base: Path | None = None
    if os.environ.get("BLISOLVER_DATA_DIR"):
        base = Path(os.environ["BLISOLVER_DATA_DIR"])
    elif os.environ.get("PLUGIN_DATA"):
        base = Path(os.environ["PLUGIN_DATA"])
    if base is not None:
        s.data_dir = base
        s.cache_dir = base / "cache"
        s.out_dir = base / "out"
    if os.environ.get("BLISOLVER_CACHE_DIR"):
        s.cache_dir = Path(os.environ["BLISOLVER_CACHE_DIR"])
    if os.environ.get("BLISOLVER_OUT_DIR"):
        s.out_dir = Path(os.environ["BLISOLVER_OUT_DIR"])
    return s


def _resolve_ocr_paths(s: "Settings") -> "Settings":
    """Auto-detect the OCR worker script + its isolated venv Python when unset.

    Environment overrides win, then the repository layout. The previous order was inverted relative
    to this docstring: it preferred `<repo>/scripts/ocr_worker.py` and consulted
    BLISOLVER_OCR_WORKER only when that file was absent, so an operator pointing the variable at a
    different worker was silently ignored whenever the bundled one happened to exist.

    Defaults: worker at ``<repo>/scripts/ocr_worker.py``; Python at
    ``<repo>/.ocr-venv/bin/python`` (a uv-managed Python 3.12 isolate). When neither an override nor
    the default resolves, the path stays None and the OCR stage no-ops with a clear diagnostic
    rather than crashing the whole ingest."""
    exe = ".exe" if os.name == "nt" else ""
    if not s.ocr_worker_path:
        env_worker = os.environ.get("BLISOLVER_OCR_WORKER")
        cand = PROJECT_ROOT / "scripts" / "ocr_worker.py"
        s.ocr_worker_path = env_worker or (str(cand) if cand.exists() else None)
    if not s.ocr_venv_python:
        env_python = os.environ.get("BLISOLVER_OCR_VENV_PYTHON")
        cand = PROJECT_ROOT / ".ocr-venv" / "bin" / f"python{exe}"
        s.ocr_venv_python = env_python or (str(cand) if cand.exists() else None)
    # OCR dials are env-overridable for tuning without code edits.
    s.ocr.fps = float(os.environ.get("BLISOLVER_OCR_FPS", s.ocr.fps))
    s.ocr.min_conf = float(os.environ.get("BLISOLVER_OCR_MIN_CONF", s.ocr.min_conf))
    s.ocr.sim_threshold = float(os.environ.get("BLISOLVER_OCR_SIM_THRESHOLD", s.ocr.sim_threshold))
    s.ocr.diff_threshold = float(os.environ.get("BLISOLVER_OCR_DIFF_THRESHOLD", s.ocr.diff_threshold))
    s.ocr.detect_interval = float(os.environ.get("BLISOLVER_OCR_DETECT_INTERVAL", s.ocr.detect_interval))
    return s
