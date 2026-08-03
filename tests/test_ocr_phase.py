"""Phase C: hard-subtitle OCR stage — subprocess-shim contract + caching + provenance coverage.

The OCR engine itself lives in the isolated worker (scripts/ocr_worker.py, run in .ocr-venv) and
is exercised end-to-end on a real burned-in-sub video separately. These tests pin the BLISOLVER-side
contract: the one-line-JSON protocol, Segment provenance (source="ocr"/confidence), per-part +
per-knob caching, and graceful no-op when the OCR isolate is absent (so a missing .ocr-venv never
crashes ingest or pollutes cache).
"""
import json
from pathlib import Path

from blisolver.config import Settings
from blisolver.providers.base import Canonical
from blisolver.schema import Segment


def _settings(tmp_path, *, with_isolate=True):
    s = Settings(cache_dir=tmp_path)
    if with_isolate:
        s.ocr_worker_path = "/fake/ocr_worker.py"
        s.ocr_venv_python = "/fake/ocr_python"
    else:
        s.ocr_worker_path = None
        s.ocr_venv_python = None
    return s


def _canon():
    return Canonical("bilibili.com", "BV1LD7U65Ew2", 1, "https://www.bilibili.com/video/BV1LD7U65Ew2")


class _ProcResult:
    def __init__(self, stdout, stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patch_run(monkeypatch, captures, stdout):
    """Patch subprocess.run to capture the worker argv + stdin request and return `stdout`.

    The shims send a single JSON line on stdin and expect a single JSON line on stdout; the worker's
    own real behavior is out of scope here (covered by the end-to-end run), so we replay a canned
    response and assert the request reached the worker intact.
    """
    def fake_run(cmd, input=None, **kwargs):
        captures["cmd"] = cmd
        captures["request"] = json.loads(input) if input else None
        return _ProcResult(stdout)
    monkeypatch.setattr("blisolver.ocr.subprocess.run", fake_run)
    monkeypatch.setattr("blisolver.detect_hardsubs.subprocess.run", fake_run)


# --- detect_hardsubs shim -----------------------------------------------------------

def test_detect_hardsubs_parses_worker_response_and_caches(monkeypatch, tmp_path):
    from blisolver.detect_hardsubs import detect_hardsubs

    s = _settings(tmp_path)
    cap = {}
    _patch_run(monkeypatch, cap, json.dumps({
        "ok": True, "has_hardsubs": True, "confidence": 0.991,
        "sampled": 8, "positive": 8, "reason": "density=1.00 avg_conf=0.99",
    }))
    result = detect_hardsubs(_canon(), Path("/video.mp4"), s)

    # The request reached the worker with the right mode + the video path under the "video" key.
    assert cap["cmd"] == ["/fake/ocr_python", "/fake/ocr_worker.py"]
    assert cap["request"]["mode"] == "detect"
    assert cap["request"]["video"] == "/video.mp4"
    assert cap["request"]["band_top"] == s.ocr.band_top
    # Verdict propagated as a structured result.
    assert result.has_hardsubs is True and result.confidence == 0.991
    assert result.sampled == 8 and result.positive == 8
    # Cached under cache/ocr-detect/: a second call MUST NOT shell out again.
    cap.pop("cmd", None)
    detect_hardsubs(_canon(), Path("/video.mp4"), s)
    assert "cmd" not in cap, "detect cache hit should not re-shell-out"


def test_detect_hardsubs_returns_absent_and_does_not_cache_on_isolate_missing(tmp_path):
    from blisolver.detect_hardsubs import detect_hardsubs

    s = _settings(tmp_path, with_isolate=False)
    result = detect_hardsubs(_canon(), Path("/video.mp4"), s)
    # Absent isolate → absent verdict (sampled=0), NOT a clean negative, and never cached.
    assert result.absent is True
    assert result.has_hardsubs is False
    assert result.sampled == 0
    # load_json always mkdirs the stage dir; the real contract is "no cached verdict file".
    assert not list((tmp_path / "ocr-detect").glob("*.json"))


def test_detect_hardsubs_negative_verdict_is_cached(tmp_path, monkeypatch):
    from blisolver.detect_hardsubs import detect_hardsubs

    s = _settings(tmp_path)
    cap = {}
    _patch_run(monkeypatch, cap, json.dumps({
        "ok": True, "has_hardsubs": False, "confidence": 0.0,
        "sampled": 6, "positive": 1, "reason": "density=0.17",
    }))
    detect_hardsubs(_canon(), Path("/v.mp4"), s)
    # The negative result is still cached (don't re-shell-out on a known-clean-negative part).
    cap.pop("cmd", None)
    detect_hardsubs(_canon(), Path("/v.mp4"), s)
    assert "cmd" not in cap


# --- ocr shim -----------------------------------------------------------------------

def test_ocr_subtitle_returns_segments_with_provenance_and_caches(monkeypatch, tmp_path):
    from blisolver.ocr import ocr_subtitle

    s = _settings(tmp_path)
    cap = {}
    raw_segs = [
        {"start": 0.0, "end": 2.0, "text": "今天也是日常看看GitHub", "confidence": 0.994},
        {"start": 2.0, "end": 5.0, "text": "AstrBot PR 神人", "confidence": 0.97},
    ]
    _patch_run(monkeypatch, cap, json.dumps({
        "ok": True, "segments": raw_segs, "frames": 522, "ocr_calls": 142,
    }))
    segs = ocr_subtitle(_canon(), Path("/v.mp4"), s)

    assert cap["cmd"] == ["/fake/ocr_python", "/fake/ocr_worker.py"]
    req = cap["request"]
    assert req["mode"] == "ocr" and req["video"] == "/v.mp4"
    assert req["fps"] == s.ocr.fps and req["band_top"] == s.ocr.band_top
    assert req["diff_threshold"] == s.ocr.diff_threshold

    # Each returned Segment carries the burned-in provenance (source="ocr") + confidence.
    assert [seg.source for seg in segs] == ["ocr", "ocr"]
    assert round(segs[0].confidence, 3) == 0.994
    assert segs[0].start == 0.0 and segs[1].end == 5.0
    assert segs[1].text == "AstrBot PR 神人"

    # Cache hit: a second call does not shell out and returns identical content.
    cap.pop("cmd", None)
    segs2 = ocr_subtitle(_canon(), Path("/v.mp4"), s)
    assert "cmd" not in cap
    assert [s2.text for s2 in segs2] == [s.text for s in segs]


def test_ocr_subtitle_isolate_absent_is_noop_and_does_not_cache(tmp_path):
    from blisolver.ocr import ocr_subtitle

    s = _settings(tmp_path, with_isolate=False)
    assert ocr_subtitle(_canon(), Path("/v.mp4"), s) == []
    # Absent isolate must not poison the cache (set up the venv later must un-block OCR).
    assert not list((tmp_path / "ocr").glob("*.json"))


def test_ocr_subtitle_worker_failure_returns_empty_and_does_not_cache(monkeypatch, tmp_path):
    from blisolver.ocr import ocr_subtitle

    s = _settings(tmp_path)
    cap = {}
    _patch_run(monkeypatch, cap, json.dumps({"ok": False, "error": "boom"}))
    assert ocr_subtitle(_canon(), Path("/v.mp4"), s) == []
    # A worker failure is never cached, so the next call retries.
    cap.pop("cmd", None)
    ocr_subtitle(_canon(), Path("/v.mp4"), s)
    assert "cmd" in cap


# --- legacy schema parity -----------------------------------------------------------

def test_ocr_segment_identical_shape_to_subtitle_segment():
    # The OCR track reuses the SAME Segment shape as the picked transcript, so fuse.py (Phase D)
    # and downstream consumers see one cue type regardless of source. source="ocr" is the only new
    # value exercised here; the addition itself isSchema 1.1 (test_probe.py covers the default).
    s = Segment(start=0.0, end=2.0, text="x", source="ocr", confidence=0.9)
    assert s.source == "ocr" and s.confidence == 0.9
    # And a legacy-style cue (no provenance) co-exists in the same list.
    s0 = Segment(start=0.0, end=1.0, text="legacy")
    assert s0.source is None


def test_render_markdown_emits_ocr_section_when_track_present():
    # bundle.md is the primary ingest surface; the OCR track must surface there under a distinct
    # heading with per-cue confidence + start time, so downstream readers can see hard-subs without
    # parsing bundle.json. Picked transcript (none here) and frames co-exist unaffected.
    from blisolver.merge import render_markdown
    from blisolver.schema import Bundle, Meta, Transcript

    bundle = Bundle(
        platform="bilibili.com", id="BV1", part=1, url="u",
        title="T", fetched_at="2026-07-24T05:41:05Z",
        transcript=Transcript(source="whisper", source_reason="none", segments=[]),
        ocr=[
            Segment(start=0.0, end=2.0, text="今天也看看GitHub", source="ocr", confidence=0.99),
            Segment(start=12.0, end=15.5, text="PR 神人", source="ocr", confidence=0.97),
        ],
        meta=Meta(cookies_used=True, referer_used=True, tool_version="0"),
    )
    md = render_markdown(bundle, _settings(Path("/tmp")))
    assert "## Hard-subtitles (OCR)" in md
    assert "今天也看看GitHub (conf 0.99)" in md
    assert "PR 神人" in md and "conf 0.97)" in md
    # The OCR section sits AFTER the transcript section and before any danmaku section.
    assert md.index("## Transcript") < md.index("## Hard-subtitles (OCR)")


def test_render_markdown_omits_ocr_section_when_track_absent():
    from blisolver.merge import render_markdown
    from blisolver.schema import Bundle, Meta, Transcript

    bundle = Bundle(
        platform="bilibili.com", id="BV1", part=1, url="u",
        title="T", fetched_at="2026-07-24T05:41:05Z",
        transcript=Transcript(source="whisper", source_reason="none", segments=[]),
        meta=Meta(cookies_used=True, referer_used=True, tool_version="0"),
    )
    md = render_markdown(bundle, _settings(Path("/tmp")))
    assert "## Hard-subtitles" not in md