"""Forced ASR fails before URL resolution or job creation, independent of host models."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.client import Client

from blisolver.config import Settings
from blisolver.mcp import server
from blisolver.resolve import Canonical


@pytest.fixture
def local_runtime(tmp_path: Path, monkeypatch):
    model = tmp_path / "test-header.bin"
    # A minimal header accepted by preflight; never passed to an inference process.
    model.write_bytes(b"lmgg" + b"\0" * 45)
    monkeypatch.setenv("BLISOLVER_WHISPER_CLI", sys.executable)
    monkeypatch.setenv("BLISOLVER_WHISPER_MODEL", str(model))
    monkeypatch.setattr(server, "_PROCS", {})
    return model


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "state", cache_dir=tmp_path / "cache", out_dir=tmp_path / "out",
    )


@pytest.mark.parametrize("protocol_mode", ["auto", "legacy"])
@pytest.mark.parametrize("missing", ["binary", "model", "both"])
def test_forced_asr_fails_before_resolve_files_or_subprocess(
    tmp_path: Path, monkeypatch, local_runtime, protocol_mode: str, missing: str,
) -> None:
    if missing in {"binary", "both"}:
        monkeypatch.setenv("BLISOLVER_WHISPER_CLI", str(tmp_path / "absent-cli"))
    if missing in {"model", "both"}:
        monkeypatch.setenv("BLISOLVER_WHISPER_MODEL", str(tmp_path / "absent-model"))

    def forbidden(*args, **kwargs):
        raise AssertionError("resolution, job writes, and subprocesses must not run")

    monkeypatch.setattr(server, "select_provider", forbidden)
    monkeypatch.setattr(server, "_jobs_dir", forbidden)
    monkeypatch.setattr(server.subprocess, "Popen", forbidden)
    settings = _settings(tmp_path)

    async def call() -> None:
        async with Client(server.build_server(settings), mode=protocol_mode) as client:
            result = await client.call_tool(
                "extract_transcript", {"url": "https://b23.tv/test", "mode": "force_whisper"},
            )
            assert result.is_error is True
            message = "\n".join(content.text for content in result.content)
            if missing in {"binary", "both"}:
                assert "whisper-cli missing" in message
            if missing in {"model", "both"}:
                assert "whisper-model missing" in message
            assert "job_id" not in message

    asyncio.run(call())
    assert not settings.cache_dir.exists()
    assert not settings.out_dir.exists()
    assert not settings.data_dir.exists()
    assert server._PROCS == {}


@pytest.mark.parametrize("mode", ["auto", "force_ocr", "force_whisper"])
def test_only_forced_asr_checks_runtime_before_starting(
    tmp_path: Path, monkeypatch, local_runtime, mode: str,
) -> None:
    events = []
    original_preflight = server.require_whisper_runtime

    def preflight():
        assert mode == "force_whisper", "subtitle-first modes must not require ASR"
        events.append("preflight")
        return original_preflight()

    def resolve(url):
        events.append("resolve")
        return Canonical("bilibili.com", "BV1dSKJ6wEVz", 1, url)

    def popen(cmd, **kwargs):
        events.append("spawn")
        assert ("--force-whisper" in cmd) == (mode == "force_whisper")
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(server, "require_whisper_runtime", preflight)
    monkeypatch.setattr(server, "select_provider", lambda url: SimpleNamespace(resolve=resolve))
    monkeypatch.setattr(server.subprocess, "Popen", popen)
    if mode != "force_whisper":
        # A subtitle-first job must remain available even when both ASR dependencies are absent.
        monkeypatch.setenv("BLISOLVER_WHISPER_CLI", str(tmp_path / "absent-cli"))
        monkeypatch.setenv("BLISOLVER_WHISPER_MODEL", str(tmp_path / "absent-model"))
    settings = _settings(tmp_path)
    record = server.start_ingest_job("https://b23.tv/test", mode, settings)
    assert events == (["preflight"] if mode == "force_whisper" else []) + ["resolve", "spawn"]
    assert server.load_job(settings, record.job_id) == record
