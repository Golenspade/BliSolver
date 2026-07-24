"""Phase E: MCP interface layer — job store, status inference, payload readers, mode->flags.

The MCP transport (FastMCP stdio) is exercised end-to-end separately; these tests pin the pure
logic that the tools delegate to, without spinning up a server or a real ingest subprocess.
"""
import json
import time
from pathlib import Path

import pytest

from harvest.config import Settings
from harvest.mcp import server as mcp_server
from harvest.mcp.server import (
    JobRecord,
    _MODE_FLAGS,
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

def _make_rec(tmp_path, *, pid=None, bundle_exists=False, bundle_path=None):
    settings = _settings(tmp_path)
    bp = bundle_path or str(settings.out_dir / "BV1-p1" / "bundle.json")
    rec = JobRecord(
        job_id="abc123", url="u", canonical_id="BV1", part=1, mode="auto",
        pid=pid, started_at=time.time(), bundle_path=bp,
        log_path=str(settings.cache_dir / "mcp-jobs" / "abc123.log"),
    )
    from harvest.mcp.server import _save_job
    _save_job(settings, rec)
    if bundle_exists:
        Path(bp).parent.mkdir(parents=True, exist_ok=True)
        Path(bp).write_text(json.dumps(_bundle_dict()), encoding="utf-8")
    return settings, rec


def test_job_status_running_when_pid_alive(monkeypatch, tmp_path):
    settings, rec = _make_rec(tmp_path, pid=99999, bundle_exists=False)
    # pid 99999 is almost certainly dead on the test host; force "alive" via the probe.
    monkeypatch.setattr("harvest.mcp.server._pid_alive", lambda pid: True)
    assert job_status(settings, rec).status == "running"


def test_job_status_done_when_pid_dead_and_bundle_present(tmp_path):
    settings, rec = _make_rec(tmp_path, pid=None, bundle_exists=True)
    st = job_status(settings, rec)
    assert st.status == "done"
    assert st.bundle_path == rec.bundle_path


def test_job_status_failed_when_pid_dead_and_no_bundle(tmp_path):
    settings, rec = _make_rec(tmp_path, pid=None, bundle_exists=False)
    # Write a log so the failed status surfaces a why.
    Path(rec.log_path).parent.mkdir(parents=True, exist_ok=True)
    Path(rec.log_path).write_text("boom: whisper-cli not found", encoding="utf-8")
    st = job_status(settings, rec)
    assert st.status == "failed"
    assert "whisper-cli" in (st.error or "")


def test_load_job_returns_none_for_unknown(tmp_path):
    assert load_job(_settings(tmp_path), "nope") is None


# --- start_ingest_job (subprocess spawn contract) ------------------------------------

def test_start_ingest_job_spawns_ingest_with_mode_flags(monkeypatch, tmp_path):
    """The spawned command must be `python -m harvest.cli ingest <url> --no-vision
    --no-frame-images [+mode flags]`, inherit env, and write a job record immediately."""
    settings = _settings(tmp_path)
    captured = {}

    class _FakeProc:
        pid = 4242

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["env_set"] = "HARVEST_COOKIES_BROWSER" in (kwargs.get("env") or {})
        # Popen opens the log file; the fake proc just needs to exist.
        return _FakeProc()

    monkeypatch.setattr("harvest.mcp.server.subprocess.Popen", fake_popen)
    monkeypatch.setenv("HARVEST_COOKIES_BROWSER", "chrome")  # env the MCP server would carry
    # Avoid opening a real log file handle leak: redirect to a path under tmp.
    monkeypatch.setattr("builtins.open", lambda f, *a, **k: _NullFile())

    rec = start_ingest_job("https://www.bilibili.com/video/BV1dSKJ6wEVz/", "force_ocr", settings)
    assert rec.pid == 4242
    assert rec.mode == "force_ocr"
    assert rec.canonical_id == "BV1dSKJ6wEVz"
    assert rec.part == 1
    cmd = captured["cmd"]
    assert cmd[1] == "-m" and cmd[2] == "harvest.cli"
    assert "ingest" in cmd and "https://www.bilibili.com/video/BV1dSKJ6wEVz/" in cmd
    assert "--no-vision" in cmd and "--no-frame-images" in cmd
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