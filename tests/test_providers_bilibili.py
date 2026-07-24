from harvest.config import Settings
from harvest.providers.base import Canonical, SourceMetadata
from harvest.providers.bilibili import BilibiliProvider
from tests.test_player_api import _FakeOpener, _view_url


def _canonical(part=1):
    return Canonical("bilibili.com", "BV1", part, f"https://b/video/BV1?p={part}")


def test_matches_bilibili_com_and_short_link():
    p = BilibiliProvider()
    assert p.matches("https://www.bilibili.com/video/BV1")
    assert p.matches("https://b23.tv/abc")
    assert not p.matches("https://www.youtube.com/watch?v=x")


def test_auth_opts_still_carries_bilibili_referer():
    from harvest.config import REFERER

    p = BilibiliProvider()
    opts = p.auth_opts(Settings())
    assert opts["http_headers"]["Referer"] == REFERER


def test_resolve_delegates_to_resolve():
    p = BilibiliProvider()
    c = p.resolve("https://www.bilibili.com/video/BV1xx411x7xx?p=2")
    assert c.platform == "bilibili.com" and c.id == "BV1xx411x7xx" and c.part == 2


def test_fetch_metadata_maps_view_to_source_metadata():
    p = BilibiliProvider()
    canonical = _canonical()
    payload = {"code": 0, "data": {
        "aid": 42, "cid": 100, "title": "My Video", "desc": "D", "duration": 600,
        "pubdate": 1719561600, "owner": {"mid": 7, "name": "Up"},
        "pic": "http://i0.hdslb.com/bfs/archive/thumb.jpg",
        "stat": {"view": 1000, "danmaku": 50, "like": 200, "coin": 30,
                  "favorite": 40, "share": 10, "reply": 20},
        "pages": [{"page": 1, "cid": 100, "part": "P1", "duration": 300},
                  {"page": 2, "cid": 200, "part": "P2", "duration": 300}]}}
    opener = _FakeOpener({_view_url(canonical): payload})
    meta = p.fetch_metadata(canonical, Settings(), opener=opener)
    assert isinstance(meta, SourceMetadata)
    assert meta.platform == "bilibili.com"
    assert meta.uploader == "Up"
    assert meta.uploader_id == "7"
    assert meta.published_at == "2024-06-28T16:00:00+08:00"
    assert meta.parts == 2
    assert meta.part_durations_s == [300, 300]
    assert meta.thumbnail_url == "http://i0.hdslb.com/bfs/archive/thumb.jpg"
    assert meta.view_count == 1000
    assert meta.danmaku_count == 50
    assert meta.like_count == 200
    assert meta.coin_count == 30
    assert meta.favorite_count == 40
    assert meta.share_count == 10
    assert meta.reply_count == 20


def test_fetch_metadata_uses_canonical_platform_for_bilibili_tv():
    p = BilibiliProvider()
    canonical = Canonical("bilibili.tv", "BV1", 1, "https://b/video/BV1?p=1")
    payload = {"code": 0, "data": {
        "aid": 1, "cid": 5, "title": "S", "desc": "",
        "duration": 120, "owner": {"mid": 1, "name": "U"}, "pages": []}}
    opener = _FakeOpener({_view_url(canonical): payload})
    meta = p.fetch_metadata(canonical, Settings(), opener=opener)
    assert meta.platform == "bilibili.tv"


def test_enumerate_parts_counts_view_pages():
    p = BilibiliProvider()
    canonical = _canonical()
    payload = {"code": 0, "data": {"aid": 1, "cid": 5, "title": "S", "desc": "",
        "duration": 120, "owner": {"mid": 1, "name": "U"}, "pages": []}}
    opener = _FakeOpener({_view_url(canonical): payload})
    assert p.enumerate_parts(canonical, Settings(), opener=opener) == 1


# --- fetch_subtitle: the full trust decision, relocated from cli.decide_transcript ---
# Monkeypatch the provider's collaborators so these stay offline and pin the OUTCOME contract.

def test_fetch_subtitle_accepts_when_gate_passes(monkeypatch):
    from harvest.providers import bilibili as biliprov
    from harvest.schema import QualityGate, Segment
    from harvest.subtitles import SubtitleResult

    p = BilibiliProvider()
    segs = [Segment(start=0.0, end=1.0, text="你好")]
    monkeypatch.setattr(biliprov, "extract_info", lambda url, s: {"duration": 100})
    monkeypatch.setattr(p, "_view", lambda c, s, **k: None)
    monkeypatch.setattr(biliprov, "subtitle_probe",
        lambda info, c, s, **k: SubtitleResult(True, "auto-sub", "ai-zh", segments=segs, reason="ok"))
    monkeypatch.setattr(biliprov, "evaluate",
        lambda seg, dur, q: QualityGate(passed=True, punct_density=1.0, dup_ratio=0.0, nonzh_ratio=0.0, cps=5.0))
    out = p.fetch_subtitle(_canonical(), Settings(), None)
    assert out.accepted is True and out.source == "auto-sub"
    assert out.language == "zh" and out.quality_gate.passed and out.segments == segs


def test_fetch_subtitle_rejects_when_gate_fails_and_carries_gate(monkeypatch):
    from harvest.providers import bilibili as biliprov
    from harvest.schema import QualityGate, Segment
    from harvest.subtitles import SubtitleResult

    p = BilibiliProvider()
    failed = QualityGate(passed=False, punct_density=0.0, dup_ratio=0.9, nonzh_ratio=0.0, cps=1.0)
    monkeypatch.setattr(biliprov, "extract_info", lambda url, s: {"duration": 100})
    monkeypatch.setattr(p, "_view", lambda c, s, **k: None)
    monkeypatch.setattr(biliprov, "subtitle_probe",
        lambda info, c, s, **k: SubtitleResult(True, "auto-sub", "ai-zh",
            segments=[Segment(start=0.0, end=1.0, text="x")], reason="ok"))
    monkeypatch.setattr(biliprov, "evaluate", lambda seg, dur, q: failed)
    out = p.fetch_subtitle(_canonical(), Settings(), None)
    assert out.accepted is False and out.source is None
    assert out.quality_gate is failed and "rejected" in out.source_reason


def test_fetch_danmaku_delegates_to_player_api(monkeypatch):
    """BilibiliProvider.fetch_danmaku is a thin delegation to player_api.fetch_danmaku."""
    from harvest.player_api import DanmakuFetch
    from harvest.providers import bilibili as biliprov

    p = BilibiliProvider()
    canonical = _canonical()
    sentinel = DanmakuFetch(source_total=5, fetched_total=5, records=[])
    captured = {}

    def _fake_fetch_danmaku(c, s, *, opener=None, view=None):
        captured["canonical"] = c
        captured["opener"] = opener
        captured["view"] = view
        return sentinel

    monkeypatch.setattr(biliprov, "fetch_danmaku", _fake_fetch_danmaku)
    result = p.fetch_danmaku(canonical, Settings(), opener="fake-opener", view="fake-view")

    assert result is sentinel
    assert captured["canonical"] is canonical
    assert captured["opener"] == "fake-opener"
    assert captured["view"] == "fake-view"


def test_fetch_subtitle_rejected_when_probe_not_found_no_gate(monkeypatch):
    from harvest.providers import bilibili as biliprov
    from harvest.subtitles import SubtitleResult

    p = BilibiliProvider()
    monkeypatch.setattr(biliprov, "extract_info", lambda url, s: {"duration": 100})
    monkeypatch.setattr(p, "_view", lambda c, s, **k: None)
    monkeypatch.setattr(biliprov, "subtitle_probe",
        lambda info, c, s, **k: SubtitleResult(False, None, None,
            reason="failed part-match assertion (#6357, identical to part 1)"))
    out = p.fetch_subtitle(_canonical(), Settings(), None)
    assert out.accepted is False and out.quality_gate is None
    assert "#6357" in out.source_reason
