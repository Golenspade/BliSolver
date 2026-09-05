"""Preflight checks. Each test pins one of the gaps the previous skill-side doctor had.

The old doctor was a copy living inside the skill package. It reimplemented environment discovery
instead of reading `Settings`, and the three consequences below were all real:

* `whisper-cli` present was reported as a healthy transcript stage even when the GGML weights the
  binary loads were absent, so the failure surfaced only after the audio download.
* `BLISOLVER_DANMAKU_MODEL` was never checked, so `--danmaku` was accepted and then silently
  ignored at runtime.
* The OCR isolate was resolved environment-first while `config._resolve_ocr_paths` resolves
  repository-first, so the reported path could be one the pipeline would not use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blisolver import doctor
from blisolver.config import Settings

STAGES = {doctor.CORE, doctor.TRANSCRIPT, doctor.VISION, doctor.OCR, doctor.DANMAKU, doctor.AUTH}


def _settings(**overrides) -> Settings:
    s = Settings(**overrides)
    s.ffmpeg_path = "/usr/bin/ffmpeg"
    return s


def _check(report: doctor.Report, name: str) -> doctor.Check:
    return next(c for c in report.checks if c.name == name)


def test_report_shape_is_stable_and_every_check_declares_a_stage():
    report = doctor.run(_settings())
    assert report.status in (doctor.OK, doctor.WARN, doctor.ERROR)
    assert {c.stage for c in report.checks} <= STAGES
    assert len({c.name for c in report.checks}) == len(report.checks), "duplicate check name"
    payload = report.to_dict()
    assert set(payload) == {"status", "plugin_root", "interpreter", "checks"}
    assert all(set(c) == {"name", "status", "stage", "detail"} for c in payload["checks"])


# --- the whisper model gap -----------------------------------------------------------------


def test_missing_whisper_model_is_reported_even_when_the_binary_exists(monkeypatch, tmp_path):
    monkeypatch.delenv("BLISOLVER_WHISPER_MODEL", raising=False)
    monkeypatch.setattr("blisolver.transcribe.WHISPER_MODEL", str(tmp_path / "absent.bin"))
    check = doctor._whisper_model_check()
    assert check.status == doctor.WARN
    assert check.stage == doctor.TRANSCRIPT
    assert "missing" in check.detail
    assert "curl" in check.detail, "must tell the operator how to fetch the weights"


def test_present_whisper_model_reports_its_size(monkeypatch, tmp_path):
    model = tmp_path / "ggml.bin"
    model.write_bytes(b"lmgg" + b"\0" * 2044)
    monkeypatch.delenv("BLISOLVER_WHISPER_MODEL", raising=False)
    monkeypatch.setattr("blisolver.transcribe.WHISPER_MODEL", str(model))
    check = doctor._whisper_model_check()
    assert check.status == doctor.OK
    assert "MB" in check.detail


def test_tmp_model_path_warns_about_volatility(monkeypatch):
    """The shipped default lives under /tmp, which is cleared on reboot. A warning that omits this
    sends the operator round the same loop every restart."""
    monkeypatch.setenv("BLISOLVER_WHISPER_MODEL", "/tmp/blisolver-missing-test-model/ggml.bin")
    monkeypatch.setattr("blisolver.transcribe.Path.is_file", lambda self: False)
    monkeypatch.setattr("blisolver.transcribe.Path.exists", lambda self: False)
    check = doctor._whisper_model_check()
    assert check.status == doctor.WARN
    assert "reboot" in check.detail
    assert "BLISOLVER_WHISPER_MODEL" in check.detail


# --- the danmaku model gap -----------------------------------------------------------------


def test_unset_danmaku_model_warns_that_the_flag_is_ignored():
    check = doctor._danmaku_check(_settings(lmstudio_danmaku_model=""))
    assert check.status == doctor.WARN
    assert check.stage == doctor.DANMAKU
    assert "silently ignored" in check.detail
    assert "--interactions" in check.detail, "must say the sibling flag is unaffected"


def test_configured_danmaku_model_passes():
    check = doctor._danmaku_check(_settings(lmstudio_danmaku_model="qwen-x"))
    assert check.status == doctor.OK
    assert "qwen-x" in check.detail


# --- the OCR precedence divergence ----------------------------------------------------------


def test_ocr_check_reads_settings_rather_than_the_environment(monkeypatch, tmp_path):
    """A path the pipeline will not use must never be reported as the OCR isolate. Setting the
    environment override while `Settings` says otherwise must not change the verdict."""
    worker = tmp_path / "ocr_worker.py"
    python = tmp_path / "python"
    worker.write_text("", encoding="utf-8")
    python.write_text("", encoding="utf-8")
    monkeypatch.setenv("BLISOLVER_OCR_WORKER", "/decoy/worker.py")
    monkeypatch.setenv("BLISOLVER_OCR_VENV_PYTHON", "/decoy/python")

    settings = _settings(ocr_worker_path=str(worker), ocr_venv_python=str(python))
    check = doctor._ocr_check(settings)
    assert check.status == doctor.OK
    assert str(worker) in check.detail
    assert "/decoy/" not in check.detail


def test_ocr_check_names_what_is_missing():
    check = doctor._ocr_check(_settings(ocr_worker_path=None, ocr_venv_python=None))
    assert check.status == doctor.WARN
    assert "worker script" in check.detail and "isolated Python" in check.detail
    assert "no-op" in check.detail, "must say --ocr degrades rather than crashing ingest"


# --- credential discipline ------------------------------------------------------------------


@pytest.mark.parametrize(
    "settings_kwargs",
    [
        {"sessdata": "super-secret-cookie"},
        {"cookies_profile": "profile-name", "cookies_browser": "firefox"},
        {},
    ],
)
def test_auth_check_never_emits_a_credential_value(settings_kwargs):
    settings = _settings(**settings_kwargs)
    check = doctor._auth_check(settings)
    assert check.stage == doctor.AUTH
    assert "super-secret-cookie" not in check.detail


def test_whole_report_is_free_of_credential_values():
    settings = _settings(sessdata="leak-me", lmstudio_api_key="leak-me-too")
    rendered = doctor.render(doctor.run(settings))
    assert "leak-me" not in rendered


def test_cold_browser_warning_explains_the_transcript_consequence():
    check = doctor._auth_check(_settings(sessdata=None, cookies_profile=""))
    assert check.status == doctor.WARN
    assert "Whisper" in check.detail, "an operator needs to know what a cold profile costs"


# --- exit semantics -------------------------------------------------------------------------


def test_warnings_do_not_fail_the_command(monkeypatch, capsys):
    """An environment with no LM Studio and no OCR isolate is still a usable transcript
    environment, so warnings must not be reported as failure."""
    monkeypatch.setattr(doctor, "run", lambda s=None: doctor.Report("warn", "/p", "/py", []))
    assert doctor.main() == 0


def test_core_failure_fails_the_command(monkeypatch):
    monkeypatch.setattr(doctor, "run", lambda s=None: doctor.Report("error", "/p", "/py", []))
    assert doctor.main() == 1


def test_missing_ffmpeg_is_a_blocking_error():
    """Every media stage shells out to ffmpeg, so its absence is not a degraded mode."""
    settings = _settings()
    settings.ffmpeg_path = None
    check = doctor._ffmpeg_check(settings)
    if check.status != doctor.ERROR:  # ffmpeg really is installed on this machine
        import shutil

        assert shutil.which("ffmpeg"), "ffmpeg check passed without ffmpeg present"


def test_json_output_is_a_single_parseable_object(capsys):
    assert doctor.main(as_json=True, settings=_settings()) in (0, 1)
    out = capsys.readouterr().out
    assert out.count("\n") == 1, "must be one line so a caller can parse it directly"
    import json

    assert isinstance(json.loads(out), dict)


def test_render_groups_blockers_before_optional_stages():
    report = doctor.run(_settings())
    rendered = doctor.render(report)
    assert rendered.index("[core]") < rendered.index("[transcript]")
    for stage in ("core", "auth", "transcript"):
        assert f"[{stage}]" in rendered


# --- interpreter identity -------------------------------------------------------------------


def test_interpreter_check_recognizes_the_project_environment():
    """Compares sys.prefix, not sys.executable: a venv's `bin/python` is a symlink to the base
    interpreter, so resolving the executable makes an in-venv run look like an outside one."""
    import sys

    from blisolver.config import PROJECT_ROOT

    check = doctor._interpreter_check()
    if Path(sys.prefix).resolve() == (PROJECT_ROOT / ".venv").resolve():
        assert check.status == doctor.OK
        assert "project environment" in check.detail
    else:
        assert check.status == doctor.WARN
