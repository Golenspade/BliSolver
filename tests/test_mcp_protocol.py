"""Offline protocol tests for the MCP 2026-07-28 server boundary.

These tests exercise discovery, tool listing, and a pure local tool call. They do not resolve a
video URL, access the network, download media, or invoke an external model.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest
from mcp import StdioServerParameters, stdio_client
from mcp.client import Client
from mcp.types import TextContent

from blisolver.config import Settings
from blisolver.mcp.server import JOB_TTL_S, JobRecord, _save_job, build_server

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
@pytest.mark.parametrize("tool", EXPECTED_TOOL_ORDER[2:])
def test_tool_call_works_in_modern_and_legacy_modes(
    tmp_path: Path, mode: str, expected_protocol_version: str, tool: str
) -> None:
    async def call_missing_job() -> tuple[str, dict]:
        async with Client(build_server(_settings(tmp_path)), mode=mode) as client:
            result = await client.call_tool(tool, {"job_id": "missing-job"})
            assert result.is_error is True
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, TextContent)
            assert result.structured_content == json.loads(content.text)
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


def test_tools_describe_side_effects_and_mode_constraints(tmp_path: Path) -> None:
    async def inspect():
        async with Client(build_server(_settings(tmp_path))) as client:
            return {t.name: t for t in (await client.list_tools()).tools}

    tools = asyncio.run(inspect())
    start = tools["extract_transcript"]
    assert start.input_schema["properties"]["mode"]["enum"] == [
        "auto", "force_whisper", "force_ocr",
    ]
    assert start.annotations.read_only_hint is False
    assert start.annotations.destructive_hint is True
    assert start.annotations.idempotent_hint is False
    assert start.annotations.open_world_hint is True
    assert "7 days" in start.description
    for name in EXPECTED_TOOL_ORDER[2:]:
        assert tools[name].annotations.read_only_hint is True
        assert tools[name].annotations.open_world_hint is False
        assert tools[name].input_schema["properties"]["job_id"]["maxLength"] == 64


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("tool", EXPECTED_TOOL_ORDER[2:])
@pytest.mark.parametrize("state", ["running", "done", "failed", "expired"])
def test_persisted_jobs_are_readable_without_the_creating_server(
    tmp_path: Path, mode: str, tool: str, state: str, monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    jobs = settings.cache_dir / "mcp-jobs"
    rec = JobRecord(
        job_id="legacy123abc4", url="unused", canonical_id="BV1", part=1, mode="auto",
        pid=12345, started_at=time.time() - (JOB_TTL_S + 1 if state == "expired" else 0),
        result_path=str(jobs / "legacy123abc4.result.json"),
        log_path=str(jobs / "legacy123abc4.log"),
    )
    _save_job(settings, rec)
    bundle = settings.out_dir / "title chosen by producer" / "bundle.json"
    bundle.parent.mkdir(parents=True)
    bundle.write_text(json.dumps({
        "transcript": {"source": "whisper", "language": "zh", "segments": []},
        "frames": [], "ocr": [],
    }), encoding="utf-8")
    if state in {"done", "expired"}:
        Path(rec.result_path).write_text(json.dumps({
            "ok": True, "parts": [{"part": 1, "ok": True, "bundle_json": str(bundle)}],
        }), encoding="utf-8")
    monkeypatch.setattr("blisolver.mcp.server._PROCS", {})
    monkeypatch.setattr("blisolver.mcp.server._pid_alive", lambda _: state == "running")

    async def poll():
        # A fresh server has only the persistent store and the explicit handle.
        async with Client(build_server(settings), mode=mode) as client:
            return await client.call_tool(tool, {"job_id": rec.job_id})

    result = asyncio.run(poll())
    assert result.is_error is (state in {"failed", "expired"})
    assert result.structured_content["status"] == state
    assert json.loads(result.content[0].text) == result.structured_content
    assert bundle.exists(), "expiry must never delete the output"


def test_invalid_mode_and_start_rate_limit_do_not_launch_work(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    calls = []

    def start(url, mode, settings):
        calls.append(url)
        return SimpleNamespace(job_id="a" * 32, canonical_id="BV1", part=1, mode=mode)

    monkeypatch.setattr("blisolver.mcp.server.start_ingest_job", start)

    async def exercise():
        server = build_server(_settings(tmp_path))
        async with Client(server) as client:
            invalid = await client.call_tool("extract_transcript", {"url": "unused", "mode": "bad"})
            assert invalid.is_error is True
            assert calls == []
            for _ in range(4):
                result = await client.call_tool("extract_transcript", {"url": "unused"})
                assert result.is_error is False
        # A new protocol connection cannot reset the server's budget.
        async with Client(server) as client:
            limited = await client.call_tool("extract_transcript", {"url": "unused"})
            assert limited.is_error is True
            assert "retry after" in limited.content[0].text

    asyncio.run(exercise())
    assert len(calls) == 4


async def _raw_stdio_exchange(tmp_path: Path, requests: list[dict]) -> list[dict]:
    """Inspect actual JSON-RPC bytes without SDK client normalization or initialization."""
    with (tmp_path / "wire-stderr.log").open("w+", encoding="utf-8") as errlog:
        proc = await asyncio.create_subprocess_exec(
            str(PLUGIN_ROOT / "bin" / "blisolver-mcp"),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=errlog,
            cwd=PLUGIN_ROOT,
            env={**os.environ, "BLISOLVER_PYTHON": sys.executable,
                 "PLUGIN_ROOT": str(PLUGIN_ROOT), "PLUGIN_DATA": str(tmp_path),
                 "BLISOLVER_DATA_DIR": str(tmp_path)},
        )
        try:
            async with asyncio.timeout(10):
                responses = []
                for request in requests:
                    proc.stdin.write((json.dumps(request) + "\n").encode())
                    await proc.stdin.drain()
                    response = json.loads(await proc.stdout.readline())
                    assert response["id"] == request["id"], response
                    responses.append(response)
                proc.stdin.close()
                await proc.wait()
                assert proc.returncode == 0
                return responses
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()


def _request(number: int, method: str, *, version=MODERN_PROTOCOL_VERSION, **params) -> dict:
    return {
        "jsonrpc": "2.0", "id": number, "method": method,
        "params": {**params, "_meta": {
            "io.modelcontextprotocol/protocolVersion": version,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "wire-test", "version": "1"},
        }},
    }


def test_modern_stdio_wire_is_stateless_and_reports_complete_results(tmp_path: Path) -> None:
    responses = asyncio.run(_raw_stdio_exchange(tmp_path, [
        _request(1, "tools/list"),  # discovery is optional; there is no initialize handshake
        _request(2, "server/discover"),
        _request(3, "tools/list", version="2099-01-01"),
        _request(4, "tools/call", name="get_transcript", arguments={"job_id": "missing"}),
        _request(5, "tools/call", name="get_transcript", arguments={"job_id": "../escape"}),
        _request(6, "tools/list"),
    ]))
    listed = responses[0]["result"]
    assert listed["resultType"] == "complete"
    assert listed["ttlMs"] >= 0
    assert listed["cacheScope"] in {"public", "private"}
    assert listed["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "blisolver"
    assert [t["name"] for t in listed["tools"]] == EXPECTED_TOOL_ORDER
    assert responses[1]["result"]["resultType"] == "complete"
    assert responses[2]["error"]["code"] == -32022
    for response in responses[3:5]:
        assert response["result"]["resultType"] == "complete"
        assert response["result"]["isError"] is True
    assert responses[3]["result"]["structuredContent"]["status"] == "unknown"
    assert responses[5]["result"]["tools"] == listed["tools"]
    capabilities = responses[1]["result"]["capabilities"]
    assert not capabilities.get("extensions")
    assert not capabilities.get("logging")
