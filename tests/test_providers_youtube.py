import io
import json
from pathlib import Path

from harvest.config import Settings
from harvest.providers.base import Canonical, SourceMetadata
from harvest.providers.youtube import YouTubeProvider

FIX = Path(__file__).parent / "fixtures" / "youtube"


def _info(name):
    return json.load(io.open(FIX / f"{name}.info.json", encoding="utf-8"))


def _canonical(vid="dQw4w9WgXcQ"):
    return Canonical("youtube.com", vid, 1, f"https://youtu.be/{vid}")


def test_matches_youtube_hosts():
    p = YouTubeProvider()
    assert p.matches("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert p.matches("https://youtu.be/dQw4w9WgXcQ")
    assert not p.matches("https://www.bilibili.com/video/BV1")


def test_resolve_extracts_11_char_id_part_always_1():
    p = YouTubeProvider()
    c = p.resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5s")
    assert c.platform == "youtube.com" and c.id == "dQw4w9WgXcQ" and c.part == 1
    assert p.resolve("https://youtu.be/dQw4w9WgXcQ").id == "dQw4w9WgXcQ"


def test_auth_opts_has_no_referer_header():
    p = YouTubeProvider()
    opts = p.auth_opts(Settings())
    assert "Referer" not in opts["http_headers"]


def test_auth_opts_omits_browser_cookies_by_default():
    # Issue #1: YouTube is cookie-free by default — a logged-in browser session breaks yt-dlp's
    # default format selection ("Requested format is not available"). Public videos must extract.
    p = YouTubeProvider()
    opts = p.auth_opts(Settings())
    assert "cookiesfrombrowser" not in opts


def test_auth_opts_attaches_browser_cookies_when_opted_in():
    # Gated content is still reachable via the explicit opt-in (HARVEST_YT_COOKIES).
    p = YouTubeProvider()
    opts = p.auth_opts(Settings(youtube_cookies=True))
    assert "cookiesfrombrowser" in opts


def test_metadata_from_info_maps_fields_and_utc_published_at():
    p = YouTubeProvider()
    meta = p._metadata_from_info(_info("dQw4w9WgXcQ"))
    assert isinstance(meta, SourceMetadata)
    assert meta.platform == "youtube.com"
    assert meta.id == "dQw4w9WgXcQ"
    assert meta.uploader == "Rick Astley"
    assert meta.uploader_id == "UCuAXFkgsw1L7xaCfnd5JJOw"   # channel_id, NOT @handle
    assert meta.published_at == "2009-10-25T06:57:33Z"       # timestamp 1256453853 -> UTC ...Z
    assert meta.parts == 1
    assert meta.part_durations_s == [meta.duration_s]
    assert meta.thumbnail_url == "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"
    assert meta.view_count == 1700000000
    assert meta.like_count == 18000000
    # bilibili-only stats stay null on YouTube (do not invent values)
    assert meta.coin_count is None
    assert meta.favorite_count is None
    assert meta.share_count is None
    assert meta.reply_count is None
    assert meta.danmaku_count is None


def test_metadata_from_info_missing_stat_fields_are_none():
    p = YouTubeProvider()
    meta = p._metadata_from_info(_info("kJQP7kiw5Fk"))  # fixture has no view_count/like_count/thumbnail
    assert meta.thumbnail_url is None
    assert meta.view_count is None
    assert meta.like_count is None


def test_published_at_falls_back_to_upload_date_midnight_utc():
    p = YouTubeProvider()
    assert p._published_at({"id": "x", "upload_date": "20240628"}) == "2024-06-28T00:00:00Z"


def test_published_at_none_when_no_date_fields():
    assert YouTubeProvider()._published_at({"id": "x"}) is None


def _meta_for(info):
    return YouTubeProvider()._metadata_from_info(info)


def test_fetch_subtitle_reuses_exact_language_human_track():
    p = YouTubeProvider()
    info = _info("dQw4w9WgXcQ")  # language "en", subtitles has "en" with a vtt entry
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi\n"
    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: vtt)
    assert got is not None and got.accepted is True
    assert got.source == "human-sub" and got.language == "en"
    assert got.segments[0].text == "hi" and got.quality_gate is None


def test_fetch_subtitle_none_when_language_unknown_goes_to_whisper():
    # kJQP7kiw5Fk: language None though an es track exists -> unknown -> Whisper (None).
    p = YouTubeProvider()
    info = _info("kJQP7kiw5Fk")
    got = p.fetch_subtitle(_canonical("kJQP7kiw5Fk"), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: "")
    assert got is None


def test_fetch_subtitle_pinned_lang_overrides_and_reuses_track():
    # --lang es pins Despacito's es track despite info["language"] being None.
    p = YouTubeProvider()
    info = _info("kJQP7kiw5Fk")
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhola\n"
    got = p.fetch_subtitle(_canonical("kJQP7kiw5Fk"), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: vtt, pinned_lang="es")
    assert got.accepted is True and got.source == "human-sub"
    assert got.language == "es" and got.segments[0].text == "hola"


def test_fetch_subtitle_reuses_regional_variant_human_track():
    # info["language"] "de" but the only human German track is keyed "de-DE" (a clean region tag).
    # Tier 1 must still find it as human-sub rather than skipping to auto/whisper.
    p = YouTubeProvider()
    info = _auto_info("de", {"de-orig": [{"ext": "srt", "url": "u"}]},
                      subtitles={"de-DE": [{"ext": "vtt", "url": "h"}]})

    def _fetch(url, settings):
        assert url == "h", "must fetch the human de-DE track, not the auto track"
        return "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhallo\n"

    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info), info=info, fetch_url=_fetch)
    assert got.accepted and got.source == "human-sub" and got.language == "de-DE"
    assert got.segments[0].text == "hallo"


def test_fetch_subtitle_ignores_community_translation_human_key():
    # Despacito shape: bare "en" target, but the only en-* human keys are hash-suffixed community
    # TRANSLATIONS. Tier 1 must NOT reuse a translation as human-sub -> falls through (here to None
    # since there is no en auto track).
    p = YouTubeProvider()
    info = _auto_info("en", {}, subtitles={
        "es": [{"ext": "vtt", "url": "s"}],
        "en-eEY6OEpapPo": [{"ext": "vtt", "url": "t1"}],
        "en-US-njLgzgtehjs": [{"ext": "vtt", "url": "t2"}],
    })
    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: "")
    assert got is None


def _auto_info(language, automatic_captions, *, subtitles=None, duration=300):
    # Minimal inline info dict for the auto-caption path (fixtures are vtt-only, no -orig/srt).
    return {
        "id": "vvvvvvvvvvv",
        "duration": duration,
        "language": language,
        "subtitles": subtitles or {},
        "automatic_captions": automatic_captions,
    }


_AUTO_SRT = (
    "1\n00:00:15,000 --> 00:00:20,000\n>> Yeah, we're good, let's get started here.\n\n"
    "2\n00:01:00,000 --> 00:01:05,000\nOkay, so today we're going to talk about a few things.\n\n"
    "3\n00:02:00,000 --> 00:02:05,000\nThis part covers the main topic in some real detail today.\n\n"
    "4\n00:03:00,000 --> 00:03:05,000\nNow let's move onto the next major section of this talk.\n\n"
    "5\n00:04:58,000 --> 00:04:59,000\nOkay, folks, that is a wrap on the whole thing.\n"
)


def test_fetch_subtitle_uses_auto_caption_when_no_human_track():
    # language en, no human sub, en-orig auto track present -> auto-sub accepted.
    p = YouTubeProvider()
    info = _auto_info("en", {"en-orig": [{"ext": "srt", "url": "u"}]})
    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: _AUTO_SRT)
    assert got is not None and got.accepted is True
    assert got.source == "auto-sub" and got.language == "en"
    assert "auto-caption" in got.source_reason and got.quality_gate is None


def test_fetch_subtitle_human_track_wins_over_auto():
    # Both a human en track and an en-orig auto track exist -> human-sub, auto never fetched.
    p = YouTubeProvider()
    info = _auto_info("en", {"en-orig": [{"ext": "srt", "url": "u"}]},
                      subtitles={"en": [{"ext": "vtt", "url": "h"}]})

    def _fetch(url, settings):
        assert url == "h", "auto track must not be fetched when a human track exists"
        return "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi\n"

    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info), info=info, fetch_url=_fetch)
    assert got.accepted and got.source == "human-sub" and got.language == "en"


def test_fetch_subtitle_unknown_lang_uses_sole_orig_auto():
    # language None, sole en-orig auto track -> auto-sub via the -orig detection branch.
    p = YouTubeProvider()
    info = _auto_info(None, {"en-orig": [{"ext": "srt", "url": "u"}], "es": [{"ext": "srt", "url": "x"}]})
    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: _AUTO_SRT)
    assert got.accepted and got.source == "auto-sub" and got.language == "en"


def test_fetch_subtitle_rejects_auto_failing_the_net():
    # A single-cue truncated auto track fails presence/coverage -> rejected outcome (-> Whisper).
    p = YouTubeProvider()
    info = _auto_info("en", {"en-orig": [{"ext": "srt", "url": "u"}]}, duration=6000)
    tiny = "1\n00:00:00,000 --> 00:00:02,000\nHello.\n"
    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: tiny)
    assert got is not None and got.accepted is False
    assert "auto-sub rejected" in got.source_reason


def test_fetch_subtitle_none_when_no_human_and_no_auto():
    # Known lang, but neither a human track nor any auto track -> None (-> Whisper).
    p = YouTubeProvider()
    info = _auto_info("ko", {})
    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: "")
    assert got is None


# --- issue #5: fail-loud guard against a degraded extraction ------------------------------------


import types  # noqa: E402

import pytest  # noqa: E402

import harvest.providers.youtube as youtube_mod  # noqa: E402


def _fake_yt_dlp(monkeypatch, info):
    class _FakeYDL:
        def __init__(self, opts):
            ...

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            return info

    monkeypatch.setattr(youtube_mod, "yt_dlp", types.SimpleNamespace(YoutubeDL=_FakeYDL))


def test_extract_info_raises_on_degraded_response(monkeypatch):
    # A blocked/degraded YouTube response (placeholder title, no duration) must fail loud instead
    # of flowing into a corrupt bundle. Missing `duration` is the signal.
    _fake_yt_dlp(monkeypatch, {"title": "recommended", "id": "F8X9_Dp3ZUk"})
    with pytest.raises(RuntimeError) as ei:
        YouTubeProvider()._extract_info(_canonical("F8X9_Dp3ZUk"), Settings())
    assert "F8X9_Dp3ZUk" in str(ei.value)


def test_extract_info_returns_healthy_response(monkeypatch):
    healthy = {"title": "Real Lecture", "duration": 3895, "id": "F8X9_Dp3ZUk"}
    _fake_yt_dlp(monkeypatch, healthy)
    assert YouTubeProvider()._extract_info(_canonical("F8X9_Dp3ZUk"), Settings()) == healthy
