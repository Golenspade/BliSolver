"""A failed discovery must never masquerade as a successfully empty subtitle list."""

import asyncio
import json
from urllib.error import HTTPError

import pytest
from mcp.client import Client
from yt_dlp.utils import DownloadError

from blisolver.config import Settings
from blisolver.mcp.server import build_server
from blisolver.probe import probe
from blisolver.providers import bilibili
from blisolver.providers.base import select_provider
from blisolver.providers.diagnostics import subtitle_discovery_warning
from blisolver.resolve import extract_url, resolve
from tests.test_player_api import _FakeOpener, _view_url


@pytest.mark.parametrize("raw", ["BV1LBt662EVa", "  BV1LBt662EVa\n"])
def test_bare_bv_routes_and_resolves_without_guessing(raw):
    provider = select_provider(raw)
    canonical = provider.resolve(raw)
    assert canonical.id == "BV1LBt662EVa"
    assert canonical.url == "https://www.bilibili.com/video/BV1LBt662EVa"
    assert canonical.part == 1


@pytest.mark.parametrize("raw", ["BV1", "BV1LBt662EVaJUNK", "text BV1LBt662EVa", "not-a-url"])
def test_incomplete_or_embedded_bare_id_is_not_guessed(raw):
    assert extract_url(raw) == raw
    with pytest.raises(ValueError):
        select_provider(raw)


def test_http_412_preserves_metadata_but_explicitly_marks_subtitle_failure(monkeypatch):
    canonical = resolve("BV1LBt662EVa")
    def fail(*args):
        raise DownloadError("private-url?SESSDATA=secret HTTP Error 412: Precondition Failed")
    monkeypatch.setattr(bilibili, "extract_info", fail)
    opener = _FakeOpener({_view_url(canonical): {
        "code": 0, "data": {"title": "Observed title", "duration": 223, "pages": []},
    }})
    result = probe(canonical, Settings(), opener=opener)
    assert result.title == "Observed title"
    assert result.status == "partial"
    assert result.subtitle_status == "error"
    assert result.available_subtitles == []
    warning = result.warnings[0]
    assert warning.code == "subtitle_access_blocked"
    assert warning.http_status == 412
    assert warning.retryable
    assert "secret" not in result.model_dump_json()


@pytest.mark.parametrize("tracks,expected", [({}, "none"), ({"ai-zh": []}, "available")])
def test_successfully_empty_discovery_is_distinct_from_failure(monkeypatch, tracks, expected):
    monkeypatch.setattr(bilibili, "extract_info", lambda *args: {"subtitles": tracks})
    canonical = resolve("BV1LBt662EVa")
    opener = _FakeOpener({_view_url(canonical): {"code": 0, "data": {"pages": []}}})
    result = probe(canonical, Settings(), opener=opener)
    assert result.status == "ok"
    assert result.subtitle_status == expected
    assert not result.warnings


def test_nested_exception_and_unknown_error_are_safe():
    inner = HTTPError("https://example.com/?token=secret", 429, "secret", {}, None)
    outer = DownloadError("sensitive diagnostic", exc_info=(HTTPError, inner, None))
    warning = subtitle_discovery_warning(outer)
    assert warning.http_status == 429 and warning.retryable
    assert "secret" not in warning.model_dump_json()
    unknown = subtitle_discovery_warning(RuntimeError("cookie=secret"))
    assert unknown.code == "subtitle_discovery_failed" and unknown.http_status is None
    assert "secret" not in unknown.model_dump_json()
    assert subtitle_discovery_warning(TimeoutError()).code == "subtitle_timeout"


def test_mcp_partial_probe_keeps_diagnostics_in_structured_and_text(monkeypatch, tmp_path):
    from blisolver.mcp import server
    from blisolver.schema import ProbeResult
    warning = subtitle_discovery_warning(HTTPError("u", 412, "blocked", {}, None))
    expected = ProbeResult(platform="bilibili.com", id="BV1LBt662EVa", parts=1,
                           status="partial", subtitle_status="error", warnings=[warning])
    monkeypatch.setattr(server, "_probe", lambda *args: expected)
    async def run():
        settings = Settings(data_dir=tmp_path, cache_dir=tmp_path / "cache", out_dir=tmp_path / "out")
        async with Client(build_server(settings)) as client:
            result = await client.call_tool("probe_video", {"url": "BV1LBt662EVa"})
            assert result.structured_content == expected.model_dump()
            assert json.loads(result.content[0].text) == expected.model_dump()
    asyncio.run(run())
