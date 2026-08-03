"""Burned-in subtitle OCR (Phase C, SPEC §5 step 4-bis): dense-sample the bottom band, run OCR
(rec+det) per changed frame, temporal-dedupe adjacent near-identical cues → a ``list[Segment]``
track on its own timeline (``source="ocr"``), populated into ``Bundle.ocr``.

Architecture (isolation model, same as ``transcribe.py``→``whisper-cli``): the heavy lifting runs in
``scripts/ocr_worker.py`` inside the isolated ``.ocr-venv`` (rapidocr-onnxruntime + opencv, never
imported by blisolver's main Python, so its native runtime never pollutes blisolver's dependency graph —
the Phase §4.4 risk). Communication is a one-line-JSON stdin → one-line-JSON stdout protocol;
worker diagnostics go to stderr so this shim parses stdout as a single JSON object.

Caching (D6-style): an OCR pass is expensive (minutes per video), and its output depends only on
the cached video file plus the OCR knob set, so it memoizes under ``cache/ocr/<video-key>.json``
keyed by those inputs — re-ingests reuse the track for free.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .cache import fs_key, load_json, save_json
from .config import Settings
from .schema import Segment


def _ocr_request(settings: Settings) -> dict:
    return {
        "mode": "ocr",
        "fps": settings.ocr.fps,
        "band_top": settings.ocr.band_top,
        "band_bottom": settings.ocr.band_bottom,
        "min_conf": settings.ocr.min_conf,
        "sim_threshold": settings.ocr.sim_threshold,
        "min_dur": settings.ocr.min_dur,
        "diff_threshold": settings.ocr.diff_threshold,
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
    out = (proc.stdout or "").strip()
    if not out:
        return {
            "ok": False,
            "error": f"OCR worker returned no stdout (rc={proc.returncode}; stderr: "
                     f"{proc.stderr.strip()[:400]})",
        }
    return json.loads(out)


def ocr_subtitle(
    canonical, video_path: Path, settings: Settings, *, timeout_s: int | None = 1800
) -> list[Segment]:
    """Return the burned-in subtitle track for `video_path`, cached per part identity + OCR knobs.

    The dense-sample + det+rec + temporal dedup run is the expensive stage of the pipeline (minutes
    per video), and its output depends only on the cached video + the fps/band/sim/min_conf knobs,
    so a memoize under ``cache/ocr/<key>.json`` lets re-ingests skip it.

    `timeout_s` caps the worker wall time; a slow run is a no-op (returns []) rather than hanging
    an ingest. 1800s default = ~7x the worst calibrated run (≈4-min video at 2fps ≈ 20min worst),
    so genuine slowness trips it but normal runs never do.
    """
    key = fs_key(
        canonical.platform, canonical.id, canonical.part,
        stage="ocr",
        fps=settings.ocr.fps, band=(settings.ocr.band_top, settings.ocr.band_bottom),
        sim=settings.ocr.sim_threshold, min_conf=settings.ocr.min_conf,
        min_dur=settings.ocr.min_dur, diff=settings.ocr.diff_threshold,
    )
    cached = load_json(settings.cache_dir, "ocr", key)
    if cached is not None and cached.get("_v") == 1:
        return [Segment(**s) for s in cached.get("segments", [])]

    req = _ocr_request(settings)
    req["video"] = str(video_path)
    resp = _run_worker(req, settings)
    if not resp.get("ok"):
        # Absent isolate / worker failure → empty track, never cache (so a later setup unblocks it).
        return []

    segs = [
        Segment(
            start=float(s["start"]),
            end=float(s["end"]),
            text=str(s["text"]),
            source="ocr",
            confidence=float(s.get("confidence", 0.0)) or None,
        )
        for s in resp.get("segments", [])
    ]
    save_json(
        settings.cache_dir, "ocr", key,
        {"_v": 1, "segments": [s.model_dump() for s in segs],
         "frames": resp.get("frames"), "ocr_calls": resp.get("ocr_calls")},
    )
    return segs