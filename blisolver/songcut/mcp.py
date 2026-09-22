"""Persisted async songcut jobs, independent of ingest/Atlas result envelopes."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Annotated

from pydantic import Field

from ..config import Settings
from .cli import resolve_paths
from .models import Manifest
from .state import atomic_json, identity, read_json

_PROCS: dict[str, subprocess.Popen] = {}
_LOCK = threading.Lock()
SongcutJobId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$", max_length=32)]


def start(manifest: dict, settings: Settings, output: str | None = None) -> dict:
    from .pipeline import preflight
    model = Manifest.model_validate(resolve_paths(manifest, Path.cwd()))
    preflight(model, settings)
    job_id = uuid.uuid4().hex
    root = settings.cache_dir.resolve() / "songcut-jobs"
    root.mkdir(parents=True, exist_ok=True)
    manifest_file = root / f"{job_id}.manifest.json"
    atomic_json(manifest_file, model.model_dump())
    # A repeated manifest resumes the same output by default, rather than spending twice.
    logical = [{key: value for key, value in clip.model_dump().items()
                if key in {"id", "source", "part", "start", "end"}} for clip in model.clips]
    out = Path(output).expanduser().resolve() if output else (
        settings.out_dir.resolve() / "songcut" / identity(logical)[:20])
    result_path = root / f"{job_id}.result.json"
    log_path = root / f"{job_id}.log"
    record_path = root / f"{job_id}.json"
    record = {"job_id": job_id, "status": "starting", "output": str(out),
              "result_path": str(result_path), "log_path": str(log_path),
              "manifest_path": str(manifest_file), "pid": None}
    atomic_json(record_path, record)
    env = dict(os.environ)
    env["BLISOLVER_DATA_DIR"] = str(settings.data_dir.resolve())
    env["BLISOLVER_CACHE_DIR"] = str(settings.cache_dir.resolve())
    env["BLISOLVER_OUT_DIR"] = str(settings.out_dir.resolve())
    try:
        with result_path.open("w", encoding="utf-8") as result, log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "blisolver.cli", "songcut",
                                        "--manifest", str(manifest_file), "--out", str(out), "--json"],
                                       stdout=result, stderr=log, env=env,
                                       cwd=str(Path(__file__).resolve().parents[2]))
        with _LOCK:
            _PROCS[job_id] = process
        record.update(pid=process.pid, status="running")
        atomic_json(record_path, record)
    except Exception:
        atomic_json(record_path, {**record, "status": "failed"})
        raise
    return {"job_id": job_id, "status": "running", "output": str(out)}


def poll(job_id: str, settings: Settings) -> dict:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("invalid songcut job_id")
    root = (settings.cache_dir / "songcut-jobs").resolve()
    path = root / f"{job_id}.json"
    if path.resolve().parent != root:
        raise ValueError("job record is outside its store")
    record = read_json(path)
    if not record:
        return {"status": "unknown", "job_id": job_id}
    with _LOCK:
        process = _PROCS.get(job_id)
        if process and process.poll() is not None:
            _PROCS.pop(job_id, None)
    try:
        result = read_json(Path(record["result_path"]))
    except (ValueError, OSError):
        result = None
    if result:
        return {"job_id": job_id, **result}
    from ..mcp.server import _pid_alive
    if record.get("pid") and _pid_alive(record["pid"]):
        return {"status": "running", "job_id": job_id, "output": record["output"]}
    return {"status": "failed", "job_id": job_id, "output": record["output"],
            "error": "worker stopped without a result; rerun the same manifest/output to resume"}


def register(server, settings, start_budget, poll_budget, tool_result):
    from mcp.types import ToolAnnotations

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                            idempotent_hint=False, open_world_hint=True))
    def make_songcut(manifest: dict, output: str | None = None) -> dict:
        """Start 制作歌切 from a songcut manifest; may download audio and call paid APIs.
        Read blisolver://guidance/references/songcut.md. Poll get_songcut(job_id).
        Draft audio/lyrics artifacts are independent of Atlas. Reuse output for recovery."""
        start_budget.check()
        return start(manifest, settings, output)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    def get_songcut(job_id: SongcutJobId):
        """Read persisted songcut status/result: running/complete/partial/failed/unknown.
        Artifact paths come from the producer. Complete means processing, never human review."""
        poll_budget.check()
        return tool_result(poll(job_id, settings))
