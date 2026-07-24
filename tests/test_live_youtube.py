import pytest

from harvest.config import Settings
from harvest.providers.base import SourceMetadata
from harvest.providers.youtube import YouTubeProvider

# Big Buck Bunny — stable, public, license-clean; the drift canary.
_LIVE_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


@pytest.mark.live
def test_live_youtube_metadata_and_subtitle():
    p = YouTubeProvider()
    canonical = p.resolve(_LIVE_URL)
    assert canonical.id == "aqz-KE-bpKQ"

    meta = p.fetch_metadata(canonical, Settings())
    assert isinstance(meta, SourceMetadata)
    assert meta.platform == "youtube.com"
    assert meta.uploader_id and meta.uploader_id.startswith("UC")
    assert meta.duration_s and meta.duration_s > 0
    assert meta.parts == 1

    # Probe showed language None + empty subtitles -> Whisper path (None). Tolerate either.
    got = p.fetch_subtitle(canonical, Settings(), meta)
    assert got is None or (got.accepted and got.source == "human-sub" and isinstance(got.segments, list))


# A public lecture with NO human captions but an English auto-caption — exercises the auto-sub tier.
_LIVE_AUTO_URL = "https://www.youtube.com/watch?v=-QFHIoCo-Ko"


@pytest.mark.live
def test_live_youtube_auto_caption_accepted():
    p = YouTubeProvider()
    canonical = p.resolve(_LIVE_AUTO_URL)
    meta = p.fetch_metadata(canonical, Settings())
    got = p.fetch_subtitle(canonical, Settings(), meta)
    assert got is not None and got.accepted is True
    assert got.source == "auto-sub" and got.language == "en"
    # De-rolling worked: no rolling-duplicate explosion, no leftover <c> word-timing tags.
    assert len(got.segments) < meta.duration_s          # far fewer cues than seconds
    assert all("<c>" not in s.text for s in got.segments)
