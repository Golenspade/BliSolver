"""Offline protocol tests for the MCP 2026-07-28 server boundary.

These tests exercise discovery, tool listing, and a pure local tool call. They do not resolve a
video URL, access the network, download media, or invoke an external model.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import StdioServerParameters, stdio_client
from mcp.client import Client
from mcp.types import TextContent

from blisolver.config import Settings
from blisolver.mcp.server import build_server

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2025-11-25"
EXPECTED_TOOL_ORDER = [
    "probe_video",
    "extract_transcript",
    "get_transcript",
    "get_timeline",
    "get_visual_context",
]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        cache_dir=tmp_path / "cache",
        out_dir=tmp_path / "out",
    )


async def _inspect_in_memory_server(
    tmp_path: Path, *, mode: str = "auto"
) -> tuple[str, list[str]]:
    async with Client(build_server(_settings(tmp_path)), mode=mode) as client:
        tools = await client.list_tools()
        return client.protocol_version, [tool.name for tool in tools.tools]


def test_in_memory_client_auto_negotiates_mcp_2026_07_28(tmp_path: Path) -> None:
    protocol_version, _ = asyncio.run(_inspect_in_memory_server(tmp_path))

    assert protocol_version == MODERN_PROTOCOL_VERSION


def test_in_memory_client_legacy_mode_negotiates_mcp_2025_11_25(tmp_path: Path) -> None:
    protocol_version, tool_names = asyncio.run(_inspect_in_memory_server(tmp_path, mode="legacy"))

    assert protocol_version == LEGACY_PROTOCOL_VERSION
    assert tool_names == EXPECTED_TOOL_ORDER


def test_tools_list_preserves_server_registration_order(tmp_path: Path) -> None:
    _, tool_names = asyncio.run(_inspect_in_memory_server(tmp_path))

    assert tool_names == EXPECTED_TOOL_ORDER


def test_modern_tools_list_carries_cache_hints(tmp_path: Path) -> None:
    async def inspect() -> tuple[int | None, str | None]:
        async with Client(build_server(_settings(tmp_path))) as client:
            result = await client.list_tools()
            return result.ttl_ms, result.cache_scope

    ttl_ms, cache_scope = asyncio.run(inspect())

    assert ttl_ms is not None
    assert cache_scope is not None


@pytest.mark.parametrize(
    ("mode", "expected_protocol_version"),
    [("auto", MODERN_PROTOCOL_VERSION), ("legacy", LEGACY_PROTOCOL_VERSION)],
)
def test_tool_call_works_in_modern_and_legacy_modes(
    tmp_path: Path, mode: str, expected_protocol_version: str
) -> None:
    async def call_missing_job() -> tuple[str, dict]:
        async with Client(build_server(_settings(tmp_path)), mode=mode) as client:
            result = await client.call_tool("get_transcript", {"job_id": "missing-job"})
            assert result.is_error is False
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, TextContent)
            return client.protocol_version, json.loads(content.text)

    protocol_version, payload = asyncio.run(call_missing_job())

    assert protocol_version == expected_protocol_version
    assert payload == {"status": "unknown", "error": "no job missing-job"}


async def _inspect_stdio_server(tmp_path: Path, errlog) -> tuple[str, list[str]]:
    parameters = StdioServerParameters(
        command=str(PLUGIN_ROOT / "bin" / "blisolver-mcp"),
        env={
            "BLISOLVER_PYTHON": sys.executable,
            "PLUGIN_ROOT": str(PLUGIN_ROOT),
            "PLUGIN_DATA": str(tmp_path),
        },
        cwd=PLUGIN_ROOT,
    )
    async with asyncio.timeout(10):
        async with Client(
            stdio_client(parameters, errlog=errlog), read_timeout_seconds=5.0
        ) as client:
            tools = await client.list_tools()
            return client.protocol_version, [tool.name for tool in tools.tools]


def test_stdio_launcher_negotiates_modern_protocol_and_lists_tools_in_order(
    tmp_path: Path,
) -> None:
    stderr_path = tmp_path / "mcp-stderr.log"
    with stderr_path.open("w+", encoding="utf-8") as errlog:
        try:
            protocol_version, tool_names = asyncio.run(_inspect_stdio_server(tmp_path, errlog))
        except Exception as exc:
            errlog.flush()
            errlog.seek(0)
            exc.add_note(f"MCP server stderr:\n{errlog.read()}")
            raise

    assert protocol_version == MODERN_PROTOCOL_VERSION
    assert tool_names == EXPECTED_TOOL_ORDER
