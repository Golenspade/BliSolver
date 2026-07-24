import pytest

from harvest.providers import base
from harvest.providers.base import Canonical, SourceMetadata, register, select_provider


class _StubA:
    def matches(self, url): return "a.example" in url
    def resolve(self, url): return Canonical("bilibili.com", "A", 1, url)
    def auth_opts(self, settings): return {}
    def fetch_metadata(self, canonical, settings): ...
    def enumerate_parts(self, canonical, settings): return 1
    def fetch_subtitle(self, canonical, settings, meta, *, pinned_lang=None): return None


class _StubB(_StubA):
    def matches(self, url): return "b.example" in url


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    monkeypatch.setattr(base, "_REGISTRY", [])


def test_select_provider_dispatches_by_matches():
    register(_StubA()); register(_StubB())
    assert isinstance(select_provider("http://a.example/x"), _StubA)
    assert isinstance(select_provider("http://b.example/x"), _StubB)


def test_select_provider_raises_when_none_match():
    register(_StubA())
    with pytest.raises(ValueError):
        select_provider("http://c.example/x")


def test_source_metadata_holds_normalized_fields():
    m = SourceMetadata(
        platform="youtube.com", id="v", title="T", uploader="U", uploader_id="UCx",
        description="d", duration_s=10, published_at="2024-01-01T00:00:00Z",
        parts=1, part_durations_s=[10],
    )
    assert m.uploader_id == "UCx" and m.parts == 1


def test_source_metadata_thumbnail_and_stats_default_to_none():
    """New additive fields (Task 1) must be optional so existing construction sites keep working."""
    m = SourceMetadata(
        platform="youtube.com", id="v", title="T", uploader="U", uploader_id="UCx",
        description="d", duration_s=10, published_at="2024-01-01T00:00:00Z",
        parts=1, part_durations_s=[10],
    )
    assert m.thumbnail_url is None
    assert m.view_count is None
    assert m.like_count is None
    assert m.coin_count is None
    assert m.favorite_count is None
    assert m.share_count is None
    assert m.reply_count is None
    assert m.danmaku_count is None


def test_source_metadata_accepts_thumbnail_and_stats():
    m = SourceMetadata(
        platform="bilibili.com", id="BV1", title="T", uploader="U", uploader_id="7",
        description="d", duration_s=10, published_at="2024-01-01T00:00:00+08:00",
        parts=1, part_durations_s=[10],
        thumbnail_url="http://x/thumb.jpg", view_count=1, like_count=2, coin_count=3,
        favorite_count=4, share_count=5, reply_count=6, danmaku_count=7,
    )
    assert m.thumbnail_url == "http://x/thumb.jpg"
    assert (m.view_count, m.like_count, m.coin_count, m.favorite_count,
            m.share_count, m.reply_count, m.danmaku_count) == (1, 2, 3, 4, 5, 6, 7)
