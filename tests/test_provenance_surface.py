"""What a reader can learn from a bundle without opening the code.

These tests came out of inspecting real output. One bundle in `out/` carried 30,872 characters of
machine-translated English under `original_language: zh`, and `bundle.md` — the surface Atlas
actually reads — contained **no field at all** stating what language the transcript was in. The
JSON lied; the Markdown simply omitted. A reader had nothing to contradict the assumption that
they were reading the original.

Three separate defects made that possible, and each is pinned below:

* `bundle.md` did not render `Transcript.language`.
* `available_subtitles` classified every key without an `ai-` prefix as `human-sub`, so bilibili's
  `danmaku` overlay was advertised as a human-authored caption — the top authority tier.
* `transcript_source` rendered the source twice, as `auto-sub (auto-sub (...))`, because provider
  reasons already begin with the source.
"""

from __future__ import annotations

import pytest
import yaml

from blisolver.config import Settings
from blisolver.merge import render_markdown
from blisolver.providers.base import Canonical, SourceMetadata
from blisolver.schema import Bundle, Meta, Segment, SubtitleTrackInfo, Transcript
from blisolver.subtitles import classify_track


def _bundle(**over) -> Bundle:
    transcript = over.pop("transcript", None) or Transcript(
        source="auto-sub", source_reason="auto-sub (ai-zh) (quality-gate: passed)",
        language="zh", segments=[Segment(start=0.0, end=1.0, text="你好", source="auto-sub")],
    )
    fields = dict(
        platform="bilibili.com", id="BV1demo", part=1,
        url="https://www.bilibili.com/video/BV1demo", title="标题",
        fetched_at="2026-08-10T00:00:00Z", transcript=transcript, frames=[],
        original_language="zh",
        meta=Meta(cookies_used=False, referer_used=True, tool_version="0.1.0"),
    )
    fields.update(over)
    return Bundle(**fields)


def _frontmatter(bundle: Bundle) -> dict:
    md = render_markdown(bundle, Settings())
    assert md.startswith("---\n")
    return yaml.safe_load(md.split("---", 2)[1])


# --- the delivered language must be on the page ---------------------------------------------


def test_frontmatter_states_the_transcript_language():
    fm = _frontmatter(_bundle())
    assert fm["transcript_language"] == "zh"


def test_a_language_proxy_is_visible_without_reading_the_json():
    """The exact shape of the real incident: Chinese video, English transcript.

    Both facts must be legible side by side in the frontmatter — the language the text is in, and
    the reason recording why it is not the original.
    """
    transcript = Transcript(
        source="auto-sub",
        source_reason=(
            "auto-sub (ai-en); language proxy: original-language track unusable "
            "[ai-zh (censorship marker '**')]; delivered ai-en instead (quality-gate: passed)"
        ),
        language="en",
        segments=[Segment(start=0.0, end=1.0, text="Hello", source="auto-sub")],
    )
    fm = _frontmatter(_bundle(transcript=transcript, original_language="zh"))

    assert fm["original_language"] == "zh"
    assert fm["transcript_language"] == "en"
    assert fm["transcript_language"] != fm["original_language"], "the discrepancy must be legible"
    assert "language proxy" in fm["transcript_source"]
    assert "ai-zh" in fm["transcript_source"], "the rejected track must be named"


def test_missing_language_renders_empty_rather_than_absent():
    """Whisper with no pinned language reports none. The key must still exist, so a reader learns
    'unknown' instead of finding nothing and inferring 'original'."""
    transcript = Transcript(
        source="whisper", source_reason="forced via --force-whisper", language=None,
        segments=[Segment(start=0.0, end=1.0, text="hi", source="whisper")],
    )
    fm = _frontmatter(_bundle(transcript=transcript))
    assert "transcript_language" in fm
    assert fm["transcript_language"] == ""


# --- transcript_source must not repeat itself -----------------------------------------------


def test_source_is_not_repeated_when_the_reason_already_names_it():
    fm = _frontmatter(_bundle())
    assert fm["transcript_source"] == "auto-sub (ai-zh) (quality-gate: passed)"
    assert not fm["transcript_source"].startswith("auto-sub (auto-sub")


def test_source_is_prefixed_when_the_reason_omits_it():
    """PROTOCOL.md defines this key as carrying the source plus the decision reason, so a reason
    that does not name the source must still yield both."""
    transcript = Transcript(
        source="whisper", source_reason="forced via --force-whisper", language="zh",
        segments=[Segment(start=0.0, end=1.0, text="hi", source="whisper")],
    )
    fm = _frontmatter(_bundle(transcript=transcript))
    assert fm["transcript_source"] == "whisper (forced via --force-whisper)"


# --- available_subtitles must describe captions only ----------------------------------------


@pytest.mark.parametrize(
    "key,expected",
    [
        ("ai-zh", "auto-sub"),
        ("ai-en", "auto-sub"),
        ("ai-ja", "auto-sub"),
        ("zh-Hans", "human-sub"),
        ("zh-CN", "human-sub"),
        ("zh", "human-sub"),
        ("en", "human-sub"),
        ("yue", "human-sub"),
        ("danmaku", None),
        ("ai-danmaku", None),
        ("", None),
    ],
)
def test_classify_track(key, expected):
    assert classify_track(key) == expected


def test_the_danmaku_overlay_is_never_advertised_as_a_caption():
    """bilibili exposes the scrolling-comment overlay under the key `danmaku`. Labelling it
    `human-sub` told a consumer a human Chinese caption existed when none did, and SKILL.md
    directs agents to read this field when choosing an acquisition strategy."""
    assert classify_track("danmaku") is None


def test_available_subtitles_excludes_non_caption_tracks(monkeypatch):
    from blisolver.providers import bilibili as biliprov
    from blisolver.providers.bilibili import BilibiliProvider

    class _View:
        title, owner_name, owner_mid, desc, duration = "标题", "up", 1, "", 100
        pubdate, pic, view_count, like_count, coin_count = 0, "", 1, 1, 1
        favorite_count, share_count, reply_count, danmaku_count = 1, 1, 1, 1
        pages = [type("P", (), {"duration": 100})()]

    monkeypatch.setattr(biliprov, "fetch_view", lambda c, s, opener=None: _View())
    monkeypatch.setattr(
        biliprov, "extract_info",
        lambda url, s: {"subtitles": {"danmaku": [], "ai-zh": [], "ai-en": [], "zh-Hans": []}},
    )
    meta = BilibiliProvider().fetch_metadata(
        Canonical("bilibili.com", "BV1demo", 1, "https://www.bilibili.com/video/BV1demo"),
        Settings(),
    )
    codes = {s["code"]: s["source"] for s in meta.available_subtitles}
    assert "danmaku" not in codes, "the danmaku overlay is not a caption"
    assert codes == {"ai-zh": "auto-sub", "ai-en": "auto-sub", "zh-Hans": "human-sub"}


def test_every_advertised_track_could_be_picked():
    """The invariant behind the classifier: if a track is advertised with a provenance, the picker
    must be willing to consider it. Otherwise the metadata promises an acquisition that cannot
    happen."""
    from blisolver.subtitles import _AUTO_ZH_KEYS, _HUMAN_ZH_KEYS, _pick_tracks

    offered = ["danmaku", "ai-zh", "ai-en", "ai-ja", "zh-Hans", "en", "not-a-language-key"]
    info = {"subtitles": {k: [{"ext": "json", "data": k}] for k in offered}}
    pickable = {lang for _, lang, _ in _pick_tracks(info)}
    advertised = {k for k in offered if classify_track(k) is not None}

    unpickable = sorted(advertised - pickable - set(_HUMAN_ZH_KEYS) - set(_AUTO_ZH_KEYS))
    assert not unpickable, f"advertised but never considered by the picker: {unpickable}"
    assert "danmaku" not in advertised and "danmaku" not in pickable


# --- original_language is an assumption, and says so ----------------------------------------


def test_original_language_is_documented_as_a_platform_assumption():
    """No detection happens for bilibili, so the field is a default. That is defensible; asserting
    it silently is not, because SKILL.md previously told readers to compare it against the
    transcript language as though both were observations.
    """
    import inspect

    from blisolver.providers.bilibili import BilibiliProvider

    source = inspect.getsource(BilibiliProvider.fetch_metadata)
    assert "ASSUMPTION" in source.upper(), (
        "the hardcoded original_language must carry an explicit note about what it is not"
    )
    assert "source_reason" in source, "the note must point at the signal that is reliable"
