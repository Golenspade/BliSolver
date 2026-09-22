import asyncio
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from blisolver.config import Settings
from blisolver.mcp.server import build_server
from blisolver.songcut.mcp import poll, start


def test_new_tools_are_registered():
    tools = {t.name: t for t in asyncio.run(build_server(Settings()).list_tools())}
    assert {"make_songcut", "get_songcut", "extract_transcript"}.issubset(tools)
    assert tools["make_songcut"].annotations.open_world_hint is True
    assert tools["make_songcut"].annotations.read_only_hint is False
    assert tools["get_songcut"].annotations.open_world_hint is False
    assert tools["get_songcut"].input_schema["properties"]["job_id"]["maxLength"] == 32


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_songcut_missing_job_wire_contract(tmp_path, mode):
    from mcp.client import Client
    async def check():
        settings = Settings(cache_dir=tmp_path)
        async with Client(build_server(settings), mode=mode) as client:
            result = await client.call_tool("get_songcut", {"job_id": "a"*32})
            assert result.is_error
            assert result.structured_content["status"] == "unknown"
    asyncio.run(check())


def test_mcp_real_worker_persisted_result_after_server_restart(songcut_wav, tmp_path):
    from blisolver.songcut.mcp import _LOCK, _PROCS
    settings = Settings(data_dir=tmp_path, cache_dir=tmp_path / "cache", out_dir=tmp_path / "out")
    job = start({"clips": [{"id": "song", "source": str(songcut_wav())}]}, settings)
    deadline = time.monotonic()+40
    while time.monotonic() < deadline:
        result = poll(job["job_id"], settings)
        if result["status"] != "running":
            break
        time.sleep(.1)
    assert result["status"] == "complete", result
    with _LOCK:
        _PROCS.clear()
    persisted = poll(job["job_id"], settings)
    assert Path(persisted["clips"][0]["bundle_json"]).is_file()


def test_invalid_or_unknown_handle(tmp_path):
    settings = Settings(cache_dir=tmp_path)
    with pytest.raises(ValueError, match="invalid"):
        poll("../../escape", settings)
    assert poll("a"*32, settings)["status"] == "unknown"
