"""The `ingest` machine-readable contract, and where generated state is written.

Two problems are pinned here.

The first is that the delivery directory is named after the sanitized video title
(`merge.write_bundle`), so it is *not* derivable from `{platform, id, part}`. Every consumer that
tried to derive it was reading a path that does not exist: the MCP job store polled
`out/<id>-p<part>/bundle.json` forever, and the skill documented the same dead path for its bundle
QA commands. The fix is that the producer reports where it wrote, on stdout, as JSON — which only
works if progress output is not also on stdout.

The second is placement. Agent Plugins §9.1 reserves `PLUGIN_DATA` for state that must survive a
plugin update; `PLUGIN_ROOT` holds package contents and may be replaced wholesale. Bundles written
under the plugin root would be lost on update.
"""

from __future__ import annotations

import json

import pytest

from blisolver.cli import parse_args
from blisolver.config import Settings


@pytest.fixture
def stub_pipeline(monkeypatch, tmp_path):
    """Replace every network/media stage so a full ingest runs offline through the real writer.

    `write_bundle` is deliberately NOT stubbed: the point is to observe the directory name the
    application actually chooses, title prefix included.
    """
    from blisolver import cli
    from blisolver.providers.base import Canonical, SourceMetadata
    from blisolver.schema import Segment, Transcript

    title = "为什么孩子不上班父母会觉得天塌了一样？"
    meta = SourceMetadata(
        platform="bilibili.com", id="BV1demo", title=title, uploader="up",
        uploader_id="1", description=None, duration_s=60, published_at=None,
        parts=1, part_durations_s=[60],
    )

    class _FakeProvider:
        def resolve(self, url):
            return Canonical("bilibili.com", "BV1demo", 1, url)

        def matches(self, url):
            return True

        def fetch_metadata(self, c, settings):
            return meta

        def enumerate_parts(self, c, settings):
            return 1

    monkeypatch.setattr(cli, "select_provider", lambda url: _FakeProvider())
    monkeypatch.setattr(
        cli, "decide_transcript",
        lambda canonical, m, settings, args: Transcript(
            source="human-sub", source_reason="stubbed", language="zh",
            segments=[Segment(start=0.0, end=1.0, text="你好", source="human-sub")],
        ),
    )
    settings = Settings()
    settings.out_dir = tmp_path / "out"
    settings.cache_dir = tmp_path / "cache"
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: settings))
    return settings, title


def _run(argv: list[str]) -> int:
    from blisolver.cli import _run_ingest

    return _run_ingest(parse_args(argv))


# --- the machine-readable envelope ----------------------------------------------------------


def test_ingest_json_reports_the_directory_it_actually_wrote(stub_pipeline, capsys):
    settings, title = stub_pipeline
    code = _run(["ingest", "https://www.bilibili.com/video/BV1demo", "--no-vision", "--json"])
    assert code == 0

    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert envelope["ok"] is True
    (part,) = envelope["parts"]
    assert part["ok"] is True and part["part"] == 1

    from pathlib import Path

    bundle_dir = Path(part["bundle_dir"])
    assert bundle_dir.is_dir(), "reported directory must exist"
    assert (bundle_dir / "bundle.json").is_file()
    assert Path(part["bundle_json"]).is_file()
    assert Path(part["bundle_md"]).is_file()

    # The reported name carries the title, which is exactly why it cannot be reconstructed from
    # the identity triple.
    assert title in bundle_dir.name
    assert bundle_dir.name != "BV1demo-p1"
    assert bundle_dir != settings.out_dir / "BV1demo-p1"


def test_progress_output_never_contaminates_stdout(stub_pipeline, capsys):
    """stdout must be parseable as one JSON object with no preamble."""
    _run(["ingest", "https://www.bilibili.com/video/BV1demo", "--no-vision", "--json"])
    captured = capsys.readouterr()
    assert captured.out.strip().count("\n") == 0, "stdout must be a single line"
    json.loads(captured.out)
    assert "human-sub" in captured.err, "progress belongs on stderr"


def test_ingest_without_json_writes_nothing_to_stdout(stub_pipeline, capsys):
    """The default remains a human-facing run; adding --json is what opts into the contract."""
    _run(["ingest", "https://www.bilibili.com/video/BV1demo", "--no-vision"])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip()


def test_envelope_carries_counts_and_provenance(stub_pipeline, capsys):
    _run(["ingest", "https://www.bilibili.com/video/BV1demo", "--no-vision", "--json"])
    (part,) = json.loads(capsys.readouterr().out)["parts"]
    assert part["transcript_source"] == "human-sub"
    assert part["transcript_language"] == "zh"
    assert part["segments"] == 1
    assert part["frames"] == 0
    # Optional tracks that did not run report null rather than zero, preserving the
    # "not requested" versus "requested and empty" distinction the bundle schema makes.
    assert part["ocr_cues"] is None
    assert part["danmaku_windows"] is None
    assert part["interactions"] is None


def test_failed_part_is_reported_with_an_error_and_no_paths(stub_pipeline, monkeypatch, capsys):
    from blisolver import cli

    def boom(canonical, settings, args):
        raise RuntimeError("whisper-cli not found")

    monkeypatch.setattr(cli, "process_part", boom)
    code = _run(["ingest", "https://www.bilibili.com/video/BV1demo", "--no-vision", "--json"])
    assert code == 1
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is False
    (part,) = envelope["parts"]
    assert part["ok"] is False
    assert "whisper-cli not found" in part["error"]
    assert "bundle_json" not in part


def test_no_frame_images_reports_no_frames_directory(stub_pipeline, capsys):
    _run([
        "ingest", "https://www.bilibili.com/video/BV1demo",
        "--no-vision", "--no-frame-images", "--json",
    ])
    (part,) = json.loads(capsys.readouterr().out)["parts"]
    assert part["frames_dir"] is None


# --- data directory placement (§9.1) --------------------------------------------------------


def _load_with(monkeypatch, **env) -> Settings:
    for key in (
        "BLISOLVER_DATA_DIR", "PLUGIN_DATA", "BLISOLVER_CACHE_DIR", "BLISOLVER_OUT_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings.load()


def test_defaults_to_the_repository_for_a_developer_checkout(monkeypatch):
    from blisolver.config import PROJECT_ROOT

    settings = _load_with(monkeypatch)
    assert settings.cache_dir == PROJECT_ROOT / "cache"
    assert settings.out_dir == PROJECT_ROOT / "out"


def test_plugin_data_relocates_generated_state(monkeypatch, tmp_path):
    """A conformant client supplies PLUGIN_DATA; bundles and caches must follow it so a plugin
    update that replaces package contents cannot destroy them."""
    settings = _load_with(monkeypatch, PLUGIN_DATA=str(tmp_path / "pdata"))
    assert settings.cache_dir == tmp_path / "pdata" / "cache"
    assert settings.out_dir == tmp_path / "pdata" / "out"


def test_explicit_data_dir_outranks_plugin_data(monkeypatch, tmp_path):
    settings = _load_with(
        monkeypatch,
        PLUGIN_DATA=str(tmp_path / "pdata"),
        BLISOLVER_DATA_DIR=str(tmp_path / "explicit"),
    )
    assert settings.cache_dir == tmp_path / "explicit" / "cache"
    assert settings.out_dir == tmp_path / "explicit" / "out"


def test_per_directory_overrides_outrank_every_root(monkeypatch, tmp_path):
    settings = _load_with(
        monkeypatch,
        PLUGIN_DATA=str(tmp_path / "pdata"),
        BLISOLVER_DATA_DIR=str(tmp_path / "explicit"),
        BLISOLVER_CACHE_DIR=str(tmp_path / "c"),
        BLISOLVER_OUT_DIR=str(tmp_path / "o"),
    )
    assert settings.cache_dir == tmp_path / "c"
    assert settings.out_dir == tmp_path / "o"


# --- OCR override precedence ----------------------------------------------------------------


def test_ocr_environment_override_is_honoured(monkeypatch, tmp_path):
    """The override used to lose to the bundled worker whenever that file existed, contradicting
    the documented precedence. An operator pointing at a different worker must get it."""
    worker = tmp_path / "custom_worker.py"
    python = tmp_path / "custom_python"
    worker.write_text("", encoding="utf-8")
    python.write_text("", encoding="utf-8")
    monkeypatch.setenv("BLISOLVER_OCR_WORKER", str(worker))
    monkeypatch.setenv("BLISOLVER_OCR_VENV_PYTHON", str(python))
    settings = Settings.load()
    assert settings.ocr_worker_path == str(worker)
    assert settings.ocr_venv_python == str(python)


def test_ocr_falls_back_to_the_bundled_worker(monkeypatch):
    from blisolver.config import PROJECT_ROOT

    monkeypatch.delenv("BLISOLVER_OCR_WORKER", raising=False)
    monkeypatch.delenv("BLISOLVER_OCR_VENV_PYTHON", raising=False)
    settings = Settings.load()
    expected = PROJECT_ROOT / "scripts" / "ocr_worker.py"
    assert settings.ocr_worker_path == (str(expected) if expected.exists() else None)



# --- the producer/consumer seam -------------------------------------------------------------


def test_mcp_job_store_resolves_a_real_envelope_from_the_real_cli(stub_pipeline, capsys, tmp_path):
    """Cross the seam in one process: the envelope the CLI actually emits must be the envelope the
    MCP job store can read.

    Without this, the two halves can each pass their own tests while disagreeing about the shape —
    which is precisely how the store ended up polling a path the writer never produced.
    """
    import time
    from pathlib import Path

    from blisolver.mcp.server import JobRecord, job_status

    settings, title = stub_pipeline
    _run(["ingest", "https://www.bilibili.com/video/BV1demo", "--no-vision", "--json"])
    envelope_text = capsys.readouterr().out

    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    result_path = jobs / "job.result.json"
    result_path.write_text(envelope_text, encoding="utf-8")

    rec = JobRecord(
        job_id="job", url="u", canonical_id="BV1demo", part=1, mode="auto",
        pid=None, started_at=time.time(),
        result_path=str(result_path), log_path=str(jobs / "job.log"),
    )
    status = job_status(settings, rec)
    assert status.status == "done"
    assert status.bundle_path is not None
    assert Path(status.bundle_path).is_file()
    assert title in Path(status.bundle_path).parent.name
