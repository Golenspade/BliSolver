"""Hard-subtitle pre-detection (Phase C, SPEC §5 step 4-bis): a cheap probe that decides whether
a video has burned-in subtitles BEFORE committing to the expensive dense-sample OCR pass.

Architecture (isolation model, same as ``transcribe.py``→``whisper-cli``): the probe shells out to
``scripts/ocr_worker.py`` running in the isolated ``.ocr-venv`` (rapidocr-onnxruntime + opencv, never
imported by harvest's main Python). Communication is a one-line-JSON stdin → one-line-JSON stdout
protocol; worker diagnostics go to stderr so this shim parses stdout as a single JSON object.

Caching: a probe is cheap (a handful of frames) but a cold RapidOCR model load dominates its cost,
so the verdict is cached under ``cache/ocr-detect/<video-key>.json`` keyed by the part identity plus
the detect knob set — re-ingests of the same part skip the probe.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .cache import fs_key, load_json, save_json
from .config import Settings


@dataclass(frozen=True)
class HardsubResult:
    """Verdict of the hard-subtitle pre-detection probe."""

    has_hardsubs: bool
    confidence: float
    sampled: int
    positive: int
    reason: str = ""

    @property
    def absent(self) -> bool:
        """True when no real sampling happened (worker/venv missing, launch failure, or a worker
        error) — distinct from a clean negative where probes ran and found nothing. Lets the caller
        emit a one-line diagnostic instead of treating an unset verdict as a definitive "no"."""
        return self.sampled == 0


def _detect_request(settings: Settings) -> dict:
    return {
        "mode": "detect",
        "detect_interval": settings.ocr.detect_interval,
        "band_top": settings.ocr.band_top,
        "band_bottom": settings.ocr.band_bottom,
        "min_conf": settings.ocr.min_conf,
    }


def _run_worker(req: dict, settings: Settings) -> dict:
    """Shell out to the OCR worker; return its parsed JSON response (or an {ok:false,...} envelope
    on a missing worker/venv or subprocess failure). Never raises — a missing OCR isolate is a
    no-op, not an ingest-killing error."""
    worker = settings.ocr_worker_path
    python = settings.ocr_venv_python
    if not worker or not python:
        return {"ok": False, "error": "OCR worker or venv python not configured (Phase C isolate absent)"}
    try:
        proc = subprocess.run(
            [python, worker],
            input=json.dumps(req, ensure_ascii=False),
            capture_output=True, text=True, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"could not launch OCR worker: {type(exc).__name__}: {exc}"}
    # The worker writes exactly one JSON line to stdout; stderr carries diagnostics.
    out = (proc.stdout or "").strip()
    if not out:
        return {
            "ok": False,
            "error": f"OCR worker returned no stdout (rc={proc.returncode}; stderr: "
                     f"{proc.stderr.strip()[:400]})",
        }
    return json.loads(out)


def detect_hardsubs(
    canonical, video_path: Path, settings: Settings
) -> HardsubResult:
    """Return the hard-subtitle verdict for `video_path`, cached per part identity + detect knobs.

    Caching rationale: a probe re-ingest of the same part repeats the same handful of OCR calls;
    the verdict depends only on the video + the band/detect interval, so we memoize it under
    ``cache/ocr-detect/``. Cache misses shell out once.
    """
    key = fs_key(
        canonical.platform, canonical.id, canonical.part,
        stage="ocr-detect", band=(settings.ocr.band_top, settings.ocr.band_bottom),
        interval=settings.ocr.detect_interval,
    )
    cached = load_json(settings.cache_dir, "ocr-detect", key)
    if cached is not None and _cached_matches(cached):
        # Skip the cache-schema sentinel (_v) so it doesn't collide with the dataclass init.
        knowable = {k: v for k, v in cached.items() if k != "_v"}
        return HardsubResult(**knowable)

    req = _detect_request(settings)
    req["video"] = str(video_path)
    resp = _run_worker(req, settings)
    if not resp.get("ok"):
        reason = resp.get("error", "detect failed")
        # Absent isolate / failure → absent verdict, never cache (so a later setup unblocks it).
        return HardsubResult(False, 0.0, 0, 0, reason=reason)

    result = HardsubResult(
        has_hardsubs=bool(resp.get("has_hardsubs")),
        confidence=float(resp.get("confidence", 0.0)),
        sampled=int(resp.get("sampled", 0)),
        positive=int(resp.get("positive", 0)),
        reason=str(resp.get("reason", "")),
    )
    save_json(
        settings.cache_dir, "ocr-detect", key,
        result.__dict__ | {"_v": 1},
    )
    return result


def _cached_matches(cached: dict) -> bool:
    return cached.get("_v") == 1 and all(
        k in cached for k in ("has_hardsubs", "confidence", "sampled", "positive")
    )