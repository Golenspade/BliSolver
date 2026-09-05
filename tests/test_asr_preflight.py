"""ASR dependency failures must precede network, audio downloads, and conversion."""

import json
from pathlib import Path

import pytest

from blisolver import cli, doctor
from blisolver import transcribe as asr
from blisolver.config import Settings
from blisolver.providers.base import Canonical, SourceMetadata, SubtitleOutcome
from blisolver.schema import Segment

URL = "https://www.bilibili.com/video/BV1demo"
CANONICAL = Canonical("bilibili.com", "BV1demo", 1, URL)


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch, tmp_path):
    monkeypatch.delenv("BLISOLVER_WHISPER_CLI", raising=False)
    monkeypatch.delenv("BLISOLVER_WHISPER_MODEL", raising=False)
    monkeypatch.setattr(asr, "WHISPER_CLI", str(tmp_path / "missing-whisper-cli"))
    monkeypatch.setattr(asr, "WHISPER_MODEL", str(tmp_path / "missing-model.bin"))
    settings = Settings(cache_dir=tmp_path / "cache", out_dir=tmp_path / "out")
    monkeypatch.setattr(Settings, "load", lambda: settings)
    return settings


@pytest.fixture
def valid_runtime(monkeypatch, tmp_path):
    executable = tmp_path / "whisper-cli"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    model = tmp_path / "ggml-test.bin"
    # Only a header fixture; neither tensor integrity nor inference is tested here.
    model.write_bytes(b"lmgg" + b"\0" * 60)
    monkeypatch.setenv("BLISOLVER_WHISPER_CLI", str(executable))
    monkeypatch.setenv("BLISOLVER_WHISPER_MODEL", str(model))
    return executable, model


def forbidden(*args, **kwargs):
    pytest.fail("network/media work must not run")


def test_preflight_reports_both_missing_dependencies():
    with pytest.raises(asr.ASRPreflightError) as exc:
        asr.require_whisper_runtime()
    message = str(exc.value)
    assert "whisper-cli missing" in message
    assert "whisper-model missing" in message
    assert "BLISOLVER_WHISPER_CLI" in message
    assert "BLISOLVER_WHISPER_MODEL" in message


def test_nonexecutable_binary_is_rejected(valid_runtime):
    executable, _ = valid_runtime
    executable.chmod(0o600)
    with pytest.raises(asr.ASRPreflightError, match="whisper-cli not executable"):
        asr.require_whisper_runtime()


@pytest.mark.parametrize("contents", [b"", b"lmgg", b"lmgg" + b"\0" * 44, b"<html>" * 20])
def test_empty_truncated_or_wrong_model_rejected(valid_runtime, contents):
    _, model = valid_runtime
    model.write_bytes(contents)
    with pytest.raises(asr.ASRPreflightError, match="whisper-model"):
        asr.require_whisper_runtime()
    assert doctor._whisper_model_check().status == doctor.WARN


def test_directory_is_not_a_model(valid_runtime, monkeypatch, tmp_path):
    monkeypatch.setenv("BLISOLVER_WHISPER_MODEL", str(tmp_path))
    with pytest.raises(asr.ASRPreflightError, match="not a regular file"):
        asr.require_whisper_runtime()


def test_unreadable_model_is_reported(valid_runtime, monkeypatch):
    _, model = valid_runtime
    original_open = Path.open

    def deny_model(path, *args, **kwargs):
        if path == model:
            raise PermissionError(13, "Permission denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_model)
    with pytest.raises(asr.ASRPreflightError, match="unreadable.*Permission denied"):
        asr.require_whisper_runtime()


def test_doctor_and_runtime_use_the_same_diagnostics():
    with pytest.raises(asr.ASRPreflightError) as exc:
        asr.require_whisper_runtime()
    assert doctor._whisper_cli_check().detail in str(exc.value)
    assert doctor._whisper_model_check().detail in str(exc.value)


def test_valid_preflight_does_not_launch_a_process(valid_runtime, monkeypatch):
    executable, _ = valid_runtime
    monkeypatch.setattr(asr.subprocess, "run", forbidden)
    assert asr.require_whisper_runtime() == str(executable)
    assert doctor._whisper_cli_check().status == doctor.OK
    assert doctor._whisper_model_check().status == doctor.OK


def test_force_whisper_fails_before_even_resolving_the_url(monkeypatch, capsys):
    monkeypatch.setattr(cli, "select_provider", forbidden)
    monkeypatch.setattr(cli, "download_audio", forbidden)
    assert cli.main(["ingest", URL, "--force-whisper", "--json"]) == 1
    output = capsys.readouterr()
    assert output.out == ""  # No resolved identity or attempted parts yet.
    assert "Local ASR preflight failed" in output.err
    assert "BLISOLVER_WHISPER_MODEL" in output.err


def test_direct_forced_part_checks_before_metadata(monkeypatch, isolated_runtime):
    monkeypatch.setattr(cli, "select_provider", forbidden)
    with pytest.raises(asr.ASRPreflightError):
        cli.process_part(CANONICAL, isolated_runtime, cli.parse_args([
            "ingest", URL, "--force-whisper",
        ]))


def test_auto_fallback_checks_before_downloading(monkeypatch, isolated_runtime):
    class Provider:
        def fetch_subtitle(self, *args, **kwargs):
            return None

    monkeypatch.setattr(cli, "select_provider", lambda url: Provider())
    monkeypatch.setattr(cli, "download_audio", forbidden)
    with pytest.raises(asr.ASRPreflightError):
        cli.decide_transcript(
            CANONICAL, None, isolated_runtime, cli.parse_args(["ingest", URL]),
        )


def test_direct_transcribe_checks_before_conversion(monkeypatch, tmp_path):
    monkeypatch.setattr(asr, "_to_wav16k", forbidden)
    monkeypatch.setattr(asr.subprocess, "run", forbidden)
    with pytest.raises(asr.ASRPreflightError):
        asr.transcribe(tmp_path / "cached-audio.m4a")


def test_cached_auto_transcript_needs_no_asr_runtime(monkeypatch, isolated_runtime):
    monkeypatch.setattr(cli, "load_json", lambda *args: [{"start": 0, "end": 1, "text": "hi"}])
    monkeypatch.setattr(cli, "require_whisper_runtime", forbidden)
    monkeypatch.setattr(cli, "download_audio", forbidden)
    transcript = cli._whisper(
        CANONICAL, isolated_runtime, cli.parse_args(["ingest", URL]), reason="cached",
    )
    assert transcript.segments[0].text == "hi"


def test_late_dotenv_values_reach_validation_and_inference(valid_runtime, monkeypatch, tmp_path):
    executable, model = valid_runtime
    monkeypatch.delenv("BLISOLVER_WHISPER_CLI")
    monkeypatch.delenv("BLISOLVER_WHISPER_MODEL")

    def load_settings():
        monkeypatch.setenv("BLISOLVER_WHISPER_CLI", str(executable))
        monkeypatch.setenv("BLISOLVER_WHISPER_MODEL", str(model))
        return Settings()

    monkeypatch.setattr(Settings, "load", load_settings)
    monkeypatch.setattr(asr, "_to_wav16k", lambda audio, settings: audio)
    commands = []
    monkeypatch.setattr(asr.subprocess, "run", lambda cmd, **kwargs: commands.append(cmd))
    asr.transcribe(tmp_path / "audio.m4a")
    assert commands[0][0] == str(executable)
    assert commands[0][commands[0].index("-m") + 1] == str(model)


def test_auto_part_failure_preserves_subtitle_sibling_and_clean_json(monkeypatch, capsys):
    class Provider:
        def resolve(self, url):
            return CANONICAL

        def enumerate_parts(self, canonical, settings):
            return 2

        def fetch_metadata(self, canonical, settings):
            return SourceMetadata(
                platform="bilibili.com", id="BV1demo", title="Test", uploader=None,
                uploader_id=None, description=None, duration_s=2, published_at=None,
                parts=2, part_durations_s=[1, 1],
            )

        def fetch_subtitle(self, canonical, *args, **kwargs):
            if canonical.part == 1:
                return None
            return SubtitleOutcome(
                accepted=True, source="human-sub", source_reason="available", language="zh",
                segments=[Segment(start=0, end=1, text="你好", source="human-sub")],
            )

    monkeypatch.setattr(cli, "select_provider", lambda url: Provider())
    monkeypatch.setattr(cli, "download_audio", forbidden)
    result = cli.main([
        "ingest", URL, "--all-parts", "--no-vision", "--json", "--scene-threshold", "0.4",
    ])
    assert result == 1
    output = capsys.readouterr()
    envelope = json.loads(output.out)
    first, second = envelope["parts"]
    assert first["ok"] is False and "ASRPreflightError" in first["error"]
    assert second["ok"] is True and second["transcript_source"] == "human-sub"
    assert Path(second["bundle_json"]).is_file()
    assert "deprecated" in output.err
    assert "deprecated" not in output.out
