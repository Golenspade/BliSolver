"""MCP interface layer (Phase E, SPEC §6): exposes blisolver's verified CLI capabilities as MCP
tools so an Agent (Atlas, etc.) can drive the ingestion pipeline — turning the local CLI
monolith into an Agent-callable service.

Five tools mirror §6:

  * probe_video(url)            -> ProbeResult JSON (sync, cheap, no media)
  * extract_transcript(url, mode) -> job_id          (ASYNC: ingests in a subprocess)
  * get_transcript(job_id)      -> {status, source, segments, quality_gate}
  * get_timeline(job_id)        -> unified timeline + provenance + fusion diagnostics
  * get_visual_context(job_id)  -> frames + ocr track

Async model (minimal viable): extract_transcript spawns `blisolver ingest` as a non-blocking
subprocess and returns a job_id immediately (whisper can take tens of minutes); the get_* tools
poll by inferring status from the process liveness + the bundle file's existence (no separate
worker notification channel needed). The job record lives under cache/mcp-jobs/<job_id>.json.

The tool logic is split into pure helper functions (jobs_*) so it is unit-testable without an
MCP transport; the FastMCP wrappers are thin.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..config import PROJECT_ROOT, Settings
from ..probe import probe as _probe
from ..providers.base import select_provider

# --- job store -----------------------------------------------------------------------

_JOBS_DIRNAME = "mcp-jobs"

# In-memory Popen handles keyed by job_id, so job_status can poll() (which reaps finished
# children) instead of os.kill(pid,0) — the latter misreports zombies as alive, so a job that
# already finished stayed "running" forever. Lost on server restart; the bundle-exists check
# below covers that case (a finished job always wrote its bundle before exiting).
_PROCS: dict[str, subprocess.Popen] = {}


def _jobs_dir(settings: Settings) -> Path:
    d = settings.cache_dir / _JOBS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class JobRecord:
    job_id: str
    url: str
    canonical_id: str
    part: int
    mode: str
    pid: int | None
    started_at: float
    # Where `ingest --json` writes its result envelope. The job's bundle location is read from
    # there, never guessed. Guessing is what broke this store: `write_bundle` names the delivery
    # directory after the sanitized video title, so the old `out/<id>-p<part>/bundle.json`
    # reconstruction pointed at a path that never appeared for any titled video, leaving every
    # job stuck on "running" until it was declared failed.
    result_path: str
    log_path: str


def _job_path(settings: Settings, job_id: str) -> Path:
    return _jobs_dir(settings) / f"{job_id}.json"


def load_job(settings: Settings, job_id: str) -> JobRecord | None:
    p = _job_path(settings, job_id)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return JobRecord(**d)


def _save_job(settings: Settings, rec: JobRecord) -> None:
    _job_path(settings, rec.job_id).write_text(
        json.dumps(rec.__dict__, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _pid_alive(pid: int | None) -> bool:
    """True if the process is still running. Best-effort: signal 0 probes liveness without
    delivering a signal. ProcessLookupError = dead; PermissionError = exists but not ours
    (macOS reaps finished pids, so this branch staying alive is conservative-correct)."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists, just not ours


@dataclass
class JobStatus:
    """Inferred job status +, when done, the bundle path; when failed, the log tail."""

    status: str  # "running" | "done" | "failed" | "unknown"
    bundle_path: str | None = None
    error: str | None = None
    result: dict | None = None  # the full `ingest --json` envelope when the job finished


def read_result(rec: JobRecord) -> dict | None:
    """Parse the producer's result envelope, or None if it is absent or not yet complete.

    `ingest` writes this file only after the whole run finishes, so a parseable envelope is a
    reliable completion signal — the same role the bundle file used to play, without the guess.
    """
    path = Path(rec.result_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None  # still being written, or truncated by a crash
    return payload if isinstance(payload, dict) else None


def _bundle_from_result(result: dict, part: int) -> str | None:
    """Pick this job's part out of the envelope. Falls back to the sole successful part."""
    parts = [p for p in result.get("parts") or [] if isinstance(p, dict)]
    for entry in parts:
        if entry.get("part") == part and entry.get("ok") and entry.get("bundle_json"):
            return entry["bundle_json"]
    successful = [p for p in parts if p.get("ok") and p.get("bundle_json")]
    return successful[0]["bundle_json"] if len(successful) == 1 else None


def job_status(settings: Settings, rec: JobRecord) -> JobStatus:
    """Infer status from the producer's result envelope, then process liveness.

    Precedence (most reliable first):
      * a parseable result envelope -> the run finished; report done or failed from its contents
      * in-memory Popen .poll()     -> running (poll reaps; None == still running)
      * os.kill(pid, 0)             -> running (fallback after a server restart)
      * dead with no envelope       -> failed (tail the log for the why)
    """
    result = read_result(rec)
    if result is not None:
        bundle = _bundle_from_result(result, rec.part)
        if bundle and Path(bundle).exists():
            return JobStatus(status="done", bundle_path=bundle, result=result)
        # The run completed and reported failure, or reported a path that is gone.
        errors = [
            p.get("error") for p in result.get("parts") or [] if isinstance(p, dict) and p.get("error")
        ]
        return JobStatus(
            status="failed",
            error="; ".join(e for e in errors if e) or _log_tail(rec) or
            "ingest finished without producing a bundle for this part",
            result=result,
        )

    proc = _PROCS.get(rec.job_id)
    if proc is not None:
        if proc.poll() is None:
            return JobStatus(status="running")
        return JobStatus(
            status="failed",
            error=_log_tail(rec) or f"(ingest exited rc={proc.returncode} without a result file)",
        )
    # No in-memory handle (server restarted): fall back to a pid probe.
    if _pid_alive(rec.pid):
        return JobStatus(status="running")
    return JobStatus(
        status="failed", error=_log_tail(rec) or "(ingest died without writing a result file)"
    )


def _log_tail(rec: JobRecord, limit: int = 800) -> str:
    log = Path(rec.log_path)
    if not log.exists():
        return ""
    return log.read_text(encoding="utf-8", errors="replace")[-limit:]


# --- mode -> ingest flags ------------------------------------------------------------

_MODE_FLAGS = {
    "auto": [],                         # default degradation chain (CC -> AI -> whisper)
    "force_whisper": ["--force-whisper"],
    "force_ocr": ["--ocr", "--force-ocr"],  # run the burned-in OCR track too
}


def start_ingest_job(
    url: str, mode: str, settings: Settings
) -> JobRecord:
    """Resolve the URL, spawn a non-blocking `blisolver ingest --json` subprocess, return its record.

    `--json` is what makes the job observable: the child reports its own bundle locations on
    stdout, which is captured to a result file. Progress text goes to stderr and lands in the log,
    so the two streams never contaminate each other.

    The subprocess inherits this process's env, same as running the CLI by hand. --no-vision +
    --no-frame-images keep the async job lean (frames are a separate concern surfaced by
    get_visual_context on a job that ran --ocr).
    """
    if mode not in _MODE_FLAGS:
        raise ValueError(f"unknown mode {mode!r}; expected one of {list(_MODE_FLAGS)}")
    canonical = select_provider(url).resolve(url)
    job_id = uuid.uuid4().hex[:12]
    result_path = str(_jobs_dir(settings) / f"{job_id}.result.json")
    log_path = str(_jobs_dir(settings) / f"{job_id}.log")
    flags = _MODE_FLAGS[mode]
    cmd = [
        sys.executable, "-m", "blisolver.cli", "ingest", url,
        "--no-vision", "--no-frame-images", "--json", *flags,
    ]
    result_f = open(result_path, "w", encoding="utf-8")
    log_f = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd, stdout=result_f, stderr=log_f,
        cwd=str(PROJECT_ROOT), env=os.environ.copy(),
    )
    rec = JobRecord(
        job_id=job_id, url=url, canonical_id=canonical.id, part=canonical.part,
        mode=mode, pid=proc.pid, started_at=time.time(),
        result_path=result_path, log_path=log_path,
    )
    _save_job(settings, rec)
    _PROCS[job_id] = proc  # keep the handle so job_status can poll()/reap, not just os.kill
    return rec


# --- bundle readers (the get_* tools) -------------------------------------------------

def _read_bundle(bundle_path: str) -> dict:
    return json.loads(Path(bundle_path).read_text(encoding="utf-8"))


def get_transcript_payload(bundle: dict) -> dict:
    """{status-source, segments, quality_gate} — the picked authoritative transcript."""
    t = bundle.get("transcript") or {}
    return {
        "source": t.get("source"),
        "source_reason": t.get("source_reason"),
        "language": t.get("language"),
        "model": t.get("model"),
        "quality_gate": t.get("quality_gate"),
        "segments": t.get("segments") or [],
    }


def get_timeline_payload(bundle: dict) -> dict:
    """Unified timeline: the picked transcript cues (with per-cue source/confidence) on one
    timeline, plus the OCR track as a separate timeline, plus the fusion diagnostics lifted out
    of source_reason. This is the multi-source view an Agent reasons over."""
    t = bundle.get("transcript") or {}
    segs = t.get("segments") or []
    ocr = bundle.get("ocr") or []
    sources_present = sorted({s.get("source") for s in segs if s.get("source")} | {"ocr"} if ocr else
                              {s.get("source") for s in segs if s.get("source")})
    return {
        "transcript_source": t.get("source"),
        "transcript_reason": t.get("source_reason"),
        "timeline": segs,           # the authoritative picked track, per-cue provenance on each
        "ocr_track": ocr,           # independent burned-in timeline (empty unless --ocr ran)
        "sources_present": list(sources_present),
        "n_transcript": len(segs),
        "n_ocr": len(ocr),
    }


def get_visual_context_payload(bundle: dict) -> dict:
    """Frames (slide notes: phash/caption/ocr) + the burned-in OCR track. Empty unless the job
    ran --vision (frames) or --ocr/--force-ocr (ocr track)."""
    return {
        "frames": bundle.get("frames") or [],
        "ocr": bundle.get("ocr") or [],
        "n_frames": len(bundle.get("frames") or []),
        "n_ocr": len(bundle.get("ocr") or []),
    }


# --- MCP server (FastMCP wrappers) ---------------------------------------------------

def build_server(settings: Settings | None = None):
    """Build the FastMCP server with all five tools registered. `settings` injectable for tests;
    default loads from env/.env."""
    from mcp.server.fastmcp import FastMCP

    s = FastMCP("blisolver")
    _settings = settings or Settings.load()

    @s.tool()
    def probe_video(url: str) -> dict:
        """Cheap pre-flight metadata probe (no media). Returns ProbeResult JSON: title, uploader,
        duration, parts, stats — enough to estimate ingest cost before committing."""
        canonical = select_provider(url).resolve(url)
        return _probe(canonical, _settings).model_dump()

    @s.tool()
    def extract_transcript(url: str, mode: str = "auto") -> dict:
        """Start an async ingest job. Returns {job_id, status:'running'} immediately. Poll
        get_transcript(job_id) until status=='done'. mode: 'auto' (CC->AI->whisper),
        'force_whisper' (skip subs), 'force_ocr' (also run burned-in OCR)."""
        rec = start_ingest_job(url, mode, _settings)
        return {"job_id": rec.job_id, "status": "running", "canonical_id": rec.canonical_id,
                "part": rec.part, "mode": rec.mode}

    @s.tool()
    def get_transcript(job_id: str) -> dict:
        """Poll an ingest job. Returns {status, ...transcript} where status is running/done/failed.
        On done, carries the picked transcript (source, segments with per-cue provenance,
        quality_gate)."""
        rec = load_job(_settings, job_id)
        if rec is None:
            return {"status": "unknown", "error": f"no job {job_id}"}
        st = job_status(_settings, rec)
        if st.status != "done":
            return {"status": st.status, "error": st.error}
        return {"status": "done", **get_transcript_payload(_read_bundle(st.bundle_path))}

    @s.tool()
    def get_timeline(job_id: str) -> dict:
        """Unified multi-source timeline for a done job: the picked transcript cues (with
        per-cue source/confidence) + the independent OCR track + fusion diagnostics. The view an
        Agent reasons over for authority ranking."""
        rec = load_job(_settings, job_id)
        if rec is None:
            return {"status": "unknown", "error": f"no job {job_id}"}
        st = job_status(_settings, rec)
        if st.status != "done":
            return {"status": st.status, "error": st.error}
        return {"status": "done", **get_timeline_payload(_read_bundle(st.bundle_path))}

    @s.tool()
    def get_visual_context(job_id: str) -> dict:
        """Visual context for a done job: slide-note frames (phash/caption/ocr) + the burned-in
        OCR track. Empty unless the job ran --vision (frames) or --ocr/--force-ocr (ocr)."""
        rec = load_job(_settings, job_id)
        if rec is None:
            return {"status": "unknown", "error": f"no job {job_id}"}
        st = job_status(_settings, rec)
        if st.status != "done":
            return {"status": st.status, "error": st.error}
        return {"status": "done", **get_visual_context_payload(_read_bundle(st.bundle_path))}

    return s


def main() -> int:
    """Entry point for `blisolver mcp`: run the MCP server over stdio."""
    build_server().run()
    return 0
