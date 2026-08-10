"""whisper.cpp transcription backend (SPEC §5 step 3, §7) — Metal shim.

Replaces the original faster-whisper/CUDA implementation with a whisper-cli (whisper.cpp) shim so
the pipeline runs on Apple Silicon (Metal) instead of NVIDIA CUDA. The public `download_audio`
and `transcribe` signatures are UNCHANGED — a drop-in backend swap, not a rewrite.

Backend choice rationale (2026-07 refactor):
  * faster-whisper is CTranslate2, CUDA-first; CPU fallback is slow and there is no Metal path.
  * whisper.cpp ships a native Metal backend (verified on Apple M1) and a `whisper-cli` binary.
  * This shim shells out to `whisper-cli` and parses its SRT output back into `list[Segment]`.

Audio is downloaded once and cached (D6: audio depends only on the video). whisper.cpp wants a
16 kHz mono PCM WAV, so `transcribe` runs an ffmpeg downmix step on the cached container first.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import yt_dlp

from .cache import fs_key
from .config import REFERER, Settings
from .resolve import Canonical
from .schema import Segment
from .subtitles import parse_srt, ydl_opts

# Default whisper.cpp GGML model. Override with BLISOLVER_WHISPER_MODEL (path to a .bin).
# medium (~1.5 GB) is a good Chinese accuracy/speed tradeoff on M1; small for speed, large-v3 for quality.
WHISPER_MODEL = os.environ.get("BLISOLVER_WHISPER_MODEL", "/tmp/ggml-medium.bin")
# whisper-cli binary; override with BLISOLVER_WHISPER_CLI.
WHISPER_CLI = os.environ.get("BLISOLVER_WHISPER_CLI", shutil.which("whisper-cli") or "whisper-cli")


def download_audio(canonical: Canonical, settings: Settings) -> Path:
    """Download + cache bestaudio for the part. faster-whisper decodes the container directly."""
    key = fs_key(canonical.platform, canonical.id, canonical.part)
    audio_dir = settings.cache_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    existing = [
        p for p in audio_dir.glob(f"{key}.*")
        if p.suffix not in (".part", ".srt") and not p.name.endswith(".16k.wav")
    ]
    if existing:
        return existing[0]

    is_bilibili = canonical.platform == "bilibili.com"
    referer = REFERER if is_bilibili else None
    # YouTube media download hits the same cookie trap as extraction (issue #1): a logged-in
    # browser session breaks yt-dlp's format selection. Cookie-free unless opted in.
    browser_cookies = is_bilibili or settings.youtube_cookies
    # aria2c is a throttled-bilibili-CDN optimization (issue #3): on YouTube its parallel
    # connections get throttled to a crawl and it bypasses yt-dlp's n-signature handling, so
    # downloads stall or "succeed" without writing a file. Native downloader off bilibili.
    opts = ydl_opts(
        settings,
        skip_download=False,
        referer=referer,
        browser_cookies=browser_cookies,
        external_downloader=is_bilibili,
    )
    opts.update({"format": "bestaudio/best", "outtmpl": str(audio_dir / f"{key}.%(ext)s")})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(canonical.url, download=True)

    return _resolve_audio_path(info, audio_dir, key)


def _resolve_audio_path(info: dict, audio_dir: Path, key: str) -> Path:
    """Recover the downloaded audio file from yt-dlp's info dict, else the on-disk glob.

    yt-dlp's `requested_downloads[0]["filepath"]` is the happy path but is inconsistently present
    (issue #3), so we also try the top-level `filepath` and the entry's `_filename`/`filename`
    before globbing the cache. If nothing resolves to a real file the download silently produced
    none — fail loud with the expected pattern and yt-dlp's info keys instead of a bare
    StopIteration from an exhausted `next()`."""
    rd = (info.get("requested_downloads") or [{}])[0]
    keys = ("filepath", "_filename", "filename")
    for cand in (info.get("filepath"), *(rd.get(k) for k in keys)):
        if cand and Path(cand).exists():
            return Path(cand)
    matches = [
        p for p in audio_dir.glob(f"{key}.*")
        if p.suffix not in (".part", ".srt") and not p.name.endswith(".16k.wav")
    ]
    if matches:
        return matches[0]
    listing = sorted(p.name for p in audio_dir.iterdir()) if audio_dir.exists() else []
    raise RuntimeError(
        f"audio download completed but no file was found for {key!r}. "
        f"Expected {audio_dir / (key + '.*')} (excluding .part), "
        f"but audio dir contains: {listing}. "
        f"yt-dlp info keys: {sorted(info.keys())}; "
        f"requested_downloads[0] keys: {sorted(rd.keys())}."
    )


def _to_wav16k(audio_path: Path, settings: Settings) -> Path:
    """Downmix the cached audio container to a 16 kHz mono PCM WAV for whisper.cpp.

    whisper.cpp's mel frontend expects 16 kHz single-channel PCM; feeding it an m4a/opus container
    directly fails or silently resamples badly. We write the WAV next to the source in the cache
    dir so repeated transcribe calls reuse it (D6-style: the WAV depends only on the audio).
    """
    # Guard against double-transcoding: if the input is already a 16k wav (re-entry),
    # with_suffix would append another .16k.wav. Detect and reuse as-is.
    if audio_path.suffix == ".wav" and audio_path.name.endswith(".16k.wav"):
        return audio_path
    wav = audio_path.with_suffix(".16k.wav")
    if wav.exists():
        return wav
    ffmpeg = settings.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg, "-y", "-i", str(audio_path),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(wav),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return wav


def transcribe(
    audio_path: Path, *, robust: bool = False, model: str = WHISPER_MODEL, lang: str | None = None
) -> list[Segment]:
    """Run whisper.cpp on `audio_path` and return timestamped segments.

    Signature preserved from the faster-whisper original — a drop-in backend swap. `model` is now
    a path to a GGML .bin (default WHISPER_MODEL); `lang` is the ISO code or None for auto-detect;
    `robust` maps to whisper.cpp's `--no-context` (disable condition_on_previous_text to break
    repetition loops on degraded lectures, mirroring the original --robust semantics).
    """
    from .config import Settings as _S  # local import to avoid a module-cycle at import time
    settings = _S.load()
    wav = _to_wav16k(audio_path, settings)

    # whisper-cli writes SRT to <output-prefix>.srt; use a temp prefix in the cache dir.
    out_prefix = audio_path.with_suffix("")
    srt_path = Path(str(out_prefix) + ".srt")
    if srt_path.exists():
        srt_path.unlink()

    cmd = [WHISPER_CLI, "-m", model, "-f", str(wav), "-of", str(out_prefix), "-osrt"]
    if lang:
        cmd += ["-l", lang]
    # robust => disable condition_on_previous_text (whisper.cpp: --max-context 0)
    if robust:
        cmd += ["--max-context", "0"]
    # flash-attn is whisper.cpp's default and harmless on Metal; keep it on for speed.
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not srt_path.exists() or srt_path.stat().st_size == 0:
        return []
    segments = parse_srt(srt_path.read_text(encoding="utf-8"))
    # Provenance (schema 1.1): tag each ASR cue with its source so fuse.py/downstream can tell
    # whisper cues from CC/OCR cues without guessing. whisper.cpp doesn't expose per-segment
    # confidence, so confidence stays None (honest, not fabricated).
    for seg in segments:
        seg.source = "whisper"
    return segments
