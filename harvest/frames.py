"""Frame extraction + perceptual dedup (SPEC §5 step 4, D8).

ffmpeg + PySceneDetect content detector pick candidate frames (a scene cut ~= a slide change on
lectures); imagehash phash collapses near-duplicates BEFORE captioning — the main cost lever.
Deduped frames are copied into the bundle dir for QA (D8); raw extracts stay in cache.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

import yt_dlp

from .cache import fs_key
from .config import REFERER, Settings
from .schema import Frame
from .subtitles import ydl_opts


def hamming(phash_a: str, phash_b: str) -> int:
    """Bit difference between two equal-length hex phash strings."""
    return bin(int(phash_a, 16) ^ int(phash_b, 16)).count("1")


def dedup_phashes(
    items: Sequence[tuple[float, str]], threshold: int
) -> list[tuple[float, str]]:
    """Collapse near-duplicate frames (a stable slide) to one representative — the cost lever.

    Each candidate is compared against the last KEPT frame (not the last seen), so a slow visual
    drift still eventually trips a new keep instead of silently merging distinct slides.
    """
    kept: list[tuple[float, str]] = []
    for ts, ph in items:
        if not kept or hamming(kept[-1][1], ph) > threshold:
            kept.append((ts, ph))
    return kept


def download_video(canonical, settings: Settings) -> Path:
    """Download + cache a <=720p video stream: OCR-legible slides, far cheaper than 4K."""
    key = fs_key(canonical.platform, canonical.id, canonical.part)
    vdir = settings.cache_dir / "video"
    vdir.mkdir(parents=True, exist_ok=True)
    existing = [p for p in vdir.glob(f"{key}.*") if p.suffix != ".part"]
    if existing:
        return existing[0]

    is_bilibili = canonical.platform == "bilibili.com"
    referer = REFERER if is_bilibili else None
    # Cookie-free YouTube download (issue #1); bilibili keeps its jar. Opt in via HARVEST_YT_COOKIES.
    browser_cookies = is_bilibili or settings.youtube_cookies
    # external_downloader scoped to bilibili (issue #9, same rationale as #3): aria2c bypasses
    # yt-dlp's n-signature handling, so YouTube's video stream 403s. Native downloader for YouTube.
    opts = ydl_opts(
        settings, skip_download=False, referer=referer,
        browser_cookies=browser_cookies, external_downloader=is_bilibili,
    )
    opts.update(
        {
            # Video-only (no audio): frames don't need sound, and skipping the mux avoids the
            # corrupt-merge failure. Prefer H.264 for decode robustness, else any <=720 stream
            # (bilibili often serves AV1/HEVC, handled by the PyAV scenedetect backend).
            "format": "bv*[height<=720][vcodec~='avc1']/bv*[height<=720]/bv*",
            "outtmpl": str(vdir / f"{key}.%(ext)s"),
        }
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(canonical.url, download=True)
    rd = (info.get("requested_downloads") or [{}])[0]
    fp = rd.get("filepath")
    if fp:
        return Path(fp)
    return next(p for p in vdir.glob(f"{key}.*") if p.suffix != ".part")


def _bulk_sample(
    video_path: Path, interval: float, raw_dir: Path, ffmpeg: str
) -> list[tuple[float, Path]]:
    """Extract one frame every `interval` seconds in a single fast ffmpeg pass.

    Robust to continuous-shot slide recordings (no hard cuts) where scene detection finds nothing;
    the phash dedup downstream collapses the repeated samples to one frame per stable slide. ffmpeg
    decodes AV1/HEVC fine, so no codec gymnastics needed here.
    """
    files = sorted(raw_dir.glob("f_*.png"))
    if not files:  # cache the raw extraction; re-runs (e.g. re-captioning) skip ffmpeg
        pattern = str(raw_dir / "f_%06d.png")
        subprocess.run(
            [ffmpeg, "-y", "-i", str(video_path), "-vf", f"fps=1/{interval}",
             "-q:v", "2", pattern],
            check=True,
            capture_output=True,
        )
        files = sorted(raw_dir.glob("f_*.png"))
    return [(i * interval, f) for i, f in enumerate(files)]


def extract_frames(
    canonical, video_path: Path, settings: Settings
) -> tuple[list[Frame], dict[str, Path]]:
    """Return (kept Frames, {bundle_rel_path: cache_png_path}) for write_bundle to copy (D8).

    Mechanism: periodic sampling + perceptual dedup. A slide-deck screen-recording's frame is
    dominated by the slide region (the presenter cam is small), so full-frame phash tracks slide
    changes and collapses to ~one frame per slide.
    """
    import imagehash
    from PIL import Image

    if not settings.ffmpeg_path:
        raise RuntimeError("ffmpeg not found; required for frame extraction (see README)")

    key = fs_key(canonical.platform, canonical.id, canonical.part)
    raw_dir = settings.cache_dir / "frames" / key
    raw_dir.mkdir(parents=True, exist_ok=True)

    samples = _bulk_sample(video_path, settings.sample_interval_s, raw_dir, settings.ffmpeg_path)

    candidates: list[tuple[float, str, Path]] = []
    for ts, path in samples:
        ph = str(imagehash.phash(Image.open(path)))
        candidates.append((ts, ph, path))

    kept = dedup_phashes([(ts, ph) for ts, ph, _ in candidates], settings.phash_dedup_threshold)
    kept_ts = {ts for ts, _ in kept}

    frames: list[Frame] = []
    sources: dict[str, Path] = {}
    for ts, ph, path in candidates:
        if ts in kept_ts:
            rel = f"frames/{int(round(ts * 1000)):08d}.png"
            frames.append(Frame(ts=ts, path=rel, phash=ph))
            sources[rel] = path
    return frames, sources
