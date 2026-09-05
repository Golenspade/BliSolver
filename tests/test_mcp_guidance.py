"""Offline discovery and lazy guidance over both supported MCP protocol versions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp.client import Client

from blisolver import doctor
from blisolver.config import Settings
from blisolver.mcp import guidance
from blisolver.mcp.server import build_server


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, cache_dir=tmp_path / "cache", out_dir=tmp_path / "out")


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_discover_and_read_authoritative_guidance(tmp_path: Path, mode: str) -> None:
    root = guidance.PROJECT_ROOT / "skills" / "blisolver-video-ingestion"
    files = [root / "SKILL.md", *sorted((root / "references").glob("*.md"))]

    async def inspect() -> None:
        async with Client(build_server(_settings(tmp_path)), mode=mode) as client:
            assert client.instructions == guidance.SERVER_INSTRUCTIONS
            assert guidance.GUIDANCE_URI in client.instructions
            assert client.server_capabilities.resources is not None
            listed = (await client.list_resources()).resources
            assert len(listed) == len(files) + 1
            assert (await client.list_resource_templates()).resource_templates == []
            for path in files:
                uri = f"blisolver://guidance/{path.relative_to(root).as_posix()}"
                metadata = next(resource for resource in listed if str(resource.uri) == uri)
                assert metadata.mime_type == "text/markdown"
                assert len(metadata.description) < 150
                result = await client.read_resource(uri)
                assert len(result.contents) == 1
                assert result.contents[0].text == path.read_text(encoding="utf-8")

    asyncio.run(inspect())


def test_listing_never_reads_markdown_bodies(tmp_path: Path, monkeypatch) -> None:
    def unexpected_read(*args, **kwargs):
        raise AssertionError("resource body eagerly read")

    monkeypatch.setattr(guidance, "_read_markdown", unexpected_read)

    async def inspect() -> None:
        async with Client(build_server(_settings(tmp_path))) as client:
            assert (await client.list_resources()).resources
            assert (await client.list_tools()).tools

    asyncio.run(inspect())


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("uri", [
    "blisolver://guidance/../../.env",
    "blisolver://guidance/references/%2e%2e/%2e%2e/.env",
    "blisolver://guidance/references/source-map.md?path=.env",
    "blisolver://guidance/scripts/ingest.py",
    "file:///etc/passwd",
])
def test_unregistered_paths_are_rejected(tmp_path: Path, mode: str, uri: str) -> None:
    async def inspect() -> None:
        async with Client(build_server(_settings(tmp_path)), mode=mode) as client:
            with pytest.raises(Exception, match="[Rr]esource"):
                await client.read_resource(uri)

    asyncio.run(inspect())


def test_symlink_cannot_expose_files_outside_skill(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "skills" / "blisolver-video-ingestion"
    refs = root / "references"
    refs.mkdir(parents=True)
    (root / "SKILL.md").write_text("entry", encoding="utf-8")
    reference = refs / "safe.md"
    reference.write_text("safe", encoding="utf-8")
    outside = tmp_path / "private.md"
    outside.write_text("secret-must-not-leak", encoding="utf-8")
    (refs / "escape.md").symlink_to(outside)
    monkeypatch.setattr(guidance, "PROJECT_ROOT", tmp_path)
    server = build_server(_settings(tmp_path))
    reference.unlink()
    reference.symlink_to(outside)

    async def inspect() -> None:
        async with Client(server) as client:
            uris = [str(resource.uri) for resource in (await client.list_resources()).resources]
            assert "blisolver://guidance/references/escape.md" not in uris
            with pytest.raises(Exception, match="[Rr]esource"):
                await client.read_resource("blisolver://guidance/references/safe.md")

    asyncio.run(inspect())


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_missing_wheel_guidance_degrades_without_breaking_tools(
    tmp_path: Path, monkeypatch, mode: str,
) -> None:
    monkeypatch.setattr(guidance, "PROJECT_ROOT", tmp_path)

    async def inspect() -> None:
        async with Client(build_server(_settings(tmp_path)), mode=mode) as client:
            assert len((await client.list_tools()).tools) == 5
            result = await client.read_resource(guidance.GUIDANCE_URI)
            assert "complete BliSolver repository/plugin checkout" in result.contents[0].text
            assert "Python wheel alone" in result.contents[0].text
            assert len((await client.list_resources()).resources) == 2

    asyncio.run(inspect())


def test_skill_directory_symlink_cannot_escape_project(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    (project / "skills").mkdir(parents=True)
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "SKILL.md").write_text("secret-must-not-leak", encoding="utf-8")
    (project / "skills" / "blisolver-video-ingestion").symlink_to(outside)
    monkeypatch.setattr(guidance, "PROJECT_ROOT", project)

    async def inspect() -> None:
        async with Client(build_server(_settings(tmp_path))) as client:
            result = await client.read_resource(guidance.GUIDANCE_URI)
            assert result.contents[0].text == guidance._MISSING_GUIDANCE
            assert len((await client.list_tools()).tools) == 5

    asyncio.run(inspect())


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_doctor_reuses_current_settings_without_disclosing_secrets(
    tmp_path: Path, capsys, mode: str,
) -> None:
    settings = _settings(tmp_path)
    secrets = ["test-sessdata-secret", "test-api-key-secret", "test-cookie-profile-secret"]
    settings.sessdata, settings.lmstudio_api_key, settings.cookies_profile = secrets
    expected = doctor.run(settings).to_dict()

    async def inspect() -> None:
        async with Client(build_server(settings), mode=mode) as client:
            result = await client.read_resource(guidance.DOCTOR_URI)
            assert result.contents[0].mime_type == "application/json"
            assert json.loads(result.contents[0].text) == expected
            for secret in secrets:
                assert secret not in result.contents[0].text
            assert not list(tmp_path.rglob(".blisolver-write-probe"))

    asyncio.run(inspect())
    captured = capsys.readouterr()
    for secret in secrets:
        assert secret not in captured.out + captured.err


def test_raw_modern_stdio_exposes_entry_without_prior_discovery(tmp_path: Path) -> None:
    # Reuse only the byte-level transport helper, never the SDK client's initialization.
    from tests.test_mcp_protocol import EXPECTED_TOOL_ORDER, _raw_stdio_exchange, _request

    responses = asyncio.run(_raw_stdio_exchange(tmp_path, [
        _request(1, "tools/list"),
        _request(2, "resources/read", uri=guidance.GUIDANCE_URI),
        _request(3, "server/discover"),
        _request(4, "resources/list"),
    ]))
    tools = responses[0]["result"]["tools"]
    assert [tool["name"] for tool in tools] == EXPECTED_TOOL_ORDER
    for tool in tools[:2]:
        assert guidance.GUIDANCE_URI in tool["description"]
    root = guidance.PROJECT_ROOT / "skills" / "blisolver-video-ingestion"
    assert responses[1]["result"]["contents"][0]["text"] == (root / "SKILL.md").read_text()
    assert responses[2]["result"]["instructions"] == guidance.SERVER_INSTRUCTIONS
    assert responses[2]["result"]["capabilities"]["resources"] is not None
    assert guidance.GUIDANCE_URI in [
        entry["uri"] for entry in responses[3]["result"]["resources"]
    ]
