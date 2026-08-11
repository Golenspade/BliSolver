"""Phase E: MCP interface layer — job store, status inference, payload readers, mode->flags.

The MCP transport (MCPServer stdio) is exercised end-to-end separately; these tests pin the pure
logic that the tools delegate to, without spinning up a server or a real ingest subprocess.
"""
import json
import time
from pathlib import Path

import pytest

from blisolver.config import Settings
from blisolver.mcp import server as mcp_server
from blisolver.mcp.server import (
    _MODE_FLAGS,
    JobRecord,
    get_timeline_payload,
    get_transcript_payload,
    get_visual_context_payload,
    job_status,
    load_job,
    start_ingest_job,
)


@pytest.fixture(autouse=True)
def _clear_procs():
    """Isolate the module-level _PROCS dict across tests (in-memory Popen handles)."""
    mcp_server._PROCS.clear()
    yield
    mcp_server._PROCS.clear()


def _settings(tmp_path):
    return Settings(cache_dir=tmp_path, out_dir=tmp_path / "out")


def _bundle_dict():
    """A minimal 1.1 bundle with provenance-tagged transcript + ocr track."""
    return {
        "schema_version": "1.1",
        "transcript": {
            "source": "whisper",
            "source_reason": "no usable subtitle | fusion: hallucination: ASR repeats 'x' for 67%",
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "hi", "source": "whisper", "confidence": None},
            ],
        },
        "ocr": [
            {"start": 0.0, "end": 2.0, "text": "real content", "source": "ocr", "confidence": 0.99},
        ],
        "frames": [{"ts": 5.0, "phash": "ab", "caption": "a slide", "ocr": "slide text"}],
    }


# --- mode -> flags -------------------------------------------------------------------

def test_mode_flags_cover_three_modes():
    assert _MODE_FLAGS["auto"] == []
    assert _MODE_FLAGS["force_whisper"] == ["--force-whisper"]
    assert _MODE_FLAGS["force_ocr"] == ["--ocr", "--force-ocr"]


# --- payload readers -----------------------------------------------------------------

def test_get_transcript_payload_lifts_provenance():
    p = get_transcript_payload(_bundle_dict())
    assert p["source"] == "whisper"
    assert p["segments"][0]["source"] == "whisper"
    assert "fusion" in p["source_reason"]
    assert p["quality_gate"] is None  # bundle sample has none


def test_get_timeline_payload_unifies_sources_and_flags_ocr():
    p = get_timeline_payload(_bundle_dict())
    assert "ocr" in p["sources_present"]
    assert "whisper" in p["sources_present"]
    assert p["n_transcript"] == 1 and p["n_ocr"] == 1
    assert p["ocr_track"][0]["source"] == "ocr"
    # timeline carries per-cue provenance (the Agent authority signal)
    assert p["timeline"][0]["source"] == "whisper"


def test_get_timeline_payload_empty_ocr_does_not_claim_ocr_source():
    b = _bundle_dict()
    b["ocr"] = None
    p = get_timeline_payload(b)
    assert "ocr" not in p["sources_present"]
    assert p["n_ocr"] == 0


def test_get_visual_context_payload_returns_frames_and_ocr():
    p = get_visual_context_payload(_bundle_dict())
    assert p["n_frames"] == 1 and p["n_ocr"] == 1
    assert p["frames"][0]["caption"] == "a slide"
    assert p["ocr"][0]["source"] == "ocr"


# --- job store + status inference ----------------------------------------------------

# A title-prefixed delivery directory, which is what `write_bundle` actually produces. The old
# store reconstructed `out/<id>-p<part>/bundle.json` instead of reading the producer's report, so
# every titled video — that is, every real video — left its job stuck reporting "running" and then
# "failed". Using the real naming here makes that regression impossible to reintroduce silently.
TITLED_DIR = "为什么孩子不上班父母会觉得天塌了一样？ [BV1-p1]"


def _result_envelope(bundle_json: str | None, *, part: int = 1, error: str | None = None) -> dict:
    entry: dict = {"part": part, "ok": error is None}
    if error is not None:
        entry["error"] = error
    if bundle_json is not None:
        entry.update({
            "bundle_dir": str(Path(bundle_json).parent),
            "bundle_json": bundle_json,
            "bundle_md": str(Path(bundle_json).parent / "bundle.md"),
        })
    return {
        "schema_version": "1.1", "platform": "bilibili.com", "id": "BV1",
        "ok": error is None, "parts": [entry],
    }


def _make_rec(tmp_path, *, pid=None, result: dict | None = None, bundle_exists=False):
    """Build a persisted job record. `result` is the `ingest --json` envelope the child wrote."""
    settings = _settings(tmp_path)
    bundle_json = str(settings.out_dir / TITLED_DIR / "bundle.json")
    jobs = settings.cache_dir / "mcp-jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    rec = JobRecord(
        job_id="abc123", url="u", canonical_id="BV1", part=1, mode="auto",
        pid=pid, started_at=time.time(),
        result_path=str(jobs / "abc123.result.json"),
        log_path=str(jobs / "abc123.log"),
    )
    from blisolver.mcp.server import _save_job
    _save_job(settings, rec)
    if bundle_exists:
        Path(bundle_json).parent.mkdir(parents=True, exist_ok=True)
        Path(bundle_json).write_text(json.dumps(_bundle_dict()), encoding="utf-8")
    if result is not None:
        Path(rec.result_path).write_text(json.dumps(result), encoding="utf-8")
    return settings, rec, bundle_json


def test_job_status_running_when_no_result_yet_and_pid_alive(monkeypatch, tmp_path):
    settings, rec, _ = _make_rec(tmp_path, pid=99999)
    monkeypatch.setattr("blisolver.mcp.server._pid_alive", lambda pid: True)
    assert job_status(settings, rec).status == "running"


def test_job_status_done_reads_the_bundle_path_from_the_producer(tmp_path):
    """The store must learn the location from the envelope. A title-prefixed directory is not
    derivable from {platform, id, part}, so any reconstruction fails this test."""
    settings, rec, bundle_json = _make_rec(tmp_path, bundle_exists=True)
    Path(rec.result_path).write_text(json.dumps(_result_envelope(bundle_json)), encoding="utf-8")
    st = job_status(settings, rec)
    assert st.status == "done"
    assert st.bundle_path == bundle_json
    assert TITLED_DIR in st.bundle_path
    assert st.result is not None and st.result["ok"] is True


def test_job_status_failed_when_the_envelope_reports_a_part_error(tmp_path):
    settings, rec, _ = _make_rec(tmp_path)
    Path(rec.result_path).write_text(
        json.dumps(_result_envelope(None, error="RuntimeError: whisper-cli not found")),
        encoding="utf-8",
    )
    st = job_status(settings, rec)
    assert st.status == "failed"
    assert "whisper-cli" in (st.error or "")


def test_job_status_failed_when_process_died_without_a_result(tmp_path):
    settings, rec, _ = _make_rec(tmp_path, pid=None)
    Path(rec.log_path).write_text("boom: whisper-cli not found", encoding="utf-8")
    st = job_status(settings, rec)
    assert st.status == "failed"
    assert "whisper-cli" in (st.error or "")


def test_partial_result_file_is_not_mistaken_for_completion(tmp_path):
    """The child writes stdout progressively; a truncated envelope must not read as done."""
    settings, rec, _ = _make_rec(tmp_path, pid=1)
    Path(rec.result_path).write_text('{"schema_version": "1.1", "parts": [', encoding="utf-8")
    from blisolver.mcp.server import read_result
    assert read_result(rec) is None


def test_load_job_returns_none_for_unknown(tmp_path):
    assert load_job(_settings(tmp_path), "nope") is None


# --- start_ingest_job (subprocess spawn contract) ------------------------------------

def test_start_ingest_job_spawns_ingest_with_mode_flags(monkeypatch, tmp_path):
    """The spawned command must be `python -m blisolver.cli ingest <url> --no-vision
    --no-frame-images [+mode flags]`, inherit env, and write a job record immediately."""
    settings = _settings(tmp_path)
    captured = {}

    class _FakeProc:
        pid = 4242

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["env_set"] = "BLISOLVER_COOKIES_BROWSER" in (kwargs.get("env") or {})
        # Popen opens the log file; the fake proc just needs to exist.
        return _FakeProc()

    monkeypatch.setattr("blisolver.mcp.server.subprocess.Popen", fake_popen)
    monkeypatch.setenv("BLISOLVER_COOKIES_BROWSER", "chrome")  # env the MCP server would carry
    # Avoid opening a real log file handle leak: redirect to a path under tmp.
    monkeypatch.setattr("builtins.open", lambda f, *a, **k: _NullFile())

    rec = start_ingest_job("https://www.bilibili.com/video/BV1dSKJ6wEVz/", "force_ocr", settings)
    assert rec.pid == 4242
    assert rec.mode == "force_ocr"
    assert rec.canonical_id == "BV1dSKJ6wEVz"
    assert rec.part == 1
    cmd = captured["cmd"]
    assert cmd[1] == "-m" and cmd[2] == "blisolver.cli"
    assert "ingest" in cmd and "https://www.bilibili.com/video/BV1dSKJ6wEVz/" in cmd
    assert "--no-vision" in cmd and "--no-frame-images" in cmd
    assert "--json" in cmd, "the job store reads the child's own result envelope, not a guess"
    assert "--ocr" in cmd and "--force-ocr" in cmd  # force_ocr mode flags
    assert captured["env_set"] is True
    # Job record persisted + reloadable.
    reloaded = load_job(settings, rec.job_id)
    assert reloaded is not None and reloaded.job_id == rec.job_id


class _NullFile:
    """Stand-in for open(log_path, 'w') so the fake Popen path doesn't leak a real handle."""
    def write(self, *a, **k):
        return 0

    def close(self):
        pass


def test_start_ingest_job_rejects_unknown_mode(tmp_path):
    with pytest.raises(ValueError):
        start_ingest_job("https://www.bilibili.com/video/BV1dSKJ6wEVz/", "bogus", _settings(tmp_path))
