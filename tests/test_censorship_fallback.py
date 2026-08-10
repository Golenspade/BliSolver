"""The cross-language subtitle fallback, and what the bundle records about it.

bilibili's AI Chinese track is sometimes returned with redactions in place of words. The pipeline
responds by walking down a candidate list — human zh, `ai-zh`, then foreign-language ASR tracks —
and taking the first track without redaction markers. That behaviour shipped with no tests at all,
and it had two provenance defects that only matter once the fallback actually fires:

* the bilibili provider hardcoded `language="zh"` on an accepted outcome, so a video delivered as
  English was recorded as Chinese;
* the rejection was written to stdout and then discarded, so nothing in the bundle said a
  substitution had happened — and stdout is the channel `probe` and `ingest --json` reserve for
  machine-readable output.

A substitution is a legitimate acquisition strategy. Recording it as though it never happened is
not: `human-sub > whisper > auto-sub` authority is meaningless to a consumer that cannot tell which
language it received.
"""

from __future__ import annotations

import pytest

from blisolver.config import Settings
from blisolver.providers.base import Canonical
from blisolver.schema import Segment
from blisolver.subtitles import (
    Acquisition,
    _acquire,
    _pick_tracks,
    probe as subtitle_probe,
    track_language,
)

CANONICAL = Canonical("bilibili.com", "BV1demo", 1, "https://www.bilibili.com/video/BV1demo")

CENSORED_ZH = [Segment(start=0.0, end=2.0, text="这里被**了，还有和X的问题")]
CLEAN_ZH = [Segment(start=0.0, end=2.0, text="这是一段完全正常的中文字幕内容")]
CLEAN_EN = [Segment(start=0.0, end=2.0, text="this is a perfectly ordinary english caption")]


def _info(**tracks) -> dict:
    """Build a yt-dlp info dict. bilibili delivers every track inside `subtitles`."""
    return {"duration": 2, "subtitles": {k: [{"ext": "json", "data": k}] for k in tracks}}


def _fetcher(mapping: dict[str, list[Segment]]):
    """Stand in for the network fetch: the track key travels in the format's `data` field."""

    def _fetch(formats, settings):
        key = formats[0]["data"]
        return list(mapping[key])

    return _fetch


# --- track_language -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,expected",
    [("ai-zh", "zh"), ("ai-en", "en"), ("ai-ja", "ja"), ("zh-Hans", "zh-Hans"),
     ("en", "en"), (None, None)],
)
def test_track_language_strips_the_production_prefix(key, expected):
    """`ai-` records how a track was produced, not what language it holds."""
    assert track_language(key) == expected


# --- candidate ordering ---------------------------------------------------------------------


def test_human_chinese_outranks_ai_chinese_which_outranks_foreign():
    tracks = _pick_tracks(_info(**{"ai-en": 1, "ai-zh": 1, "zh-Hans": 1}))
    assert [lang for _, lang, _ in tracks] == ["zh-Hans", "ai-zh", "ai-en"]
    assert [src for src, _, _ in tracks] == ["human-sub", "auto-sub", "auto-sub"]


# --- the fallback itself --------------------------------------------------------------------


def test_clean_chinese_track_is_taken_without_any_fallback():
    acq = _acquire(
        _info(**{"ai-zh": 1, "ai-en": 1}), CANONICAL, Settings(),
        _fetcher({"ai-zh": CLEAN_ZH, "ai-en": CLEAN_EN}),
    )
    assert acq.lang == "ai-zh"
    assert acq.rejected == ()
    assert acq.is_language_proxy is False
    assert acq.describe_fallback() == ""


def test_censored_chinese_falls_through_to_the_foreign_track():
    acq = _acquire(
        _info(**{"ai-zh": 1, "ai-en": 1}), CANONICAL, Settings(),
        _fetcher({"ai-zh": CENSORED_ZH, "ai-en": CLEAN_EN}),
    )
    assert acq.lang == "ai-en"
    assert acq.segments == CLEAN_EN
    assert [key for key, _ in acq.rejected] == ["ai-zh"]
    assert acq.is_language_proxy is True


def test_the_rejection_records_which_marker_fired():
    """A textual heuristic must show its evidence; `**` and `和X` are not proof of censorship."""
    acq = _acquire(
        _info(**{"ai-zh": 1, "ai-en": 1}), CANONICAL, Settings(),
        _fetcher({"ai-zh": CENSORED_ZH, "ai-en": CLEAN_EN}),
    )
    ((key, why),) = acq.rejected
    assert key == "ai-zh"
    assert "censorship marker" in why
    assert "**" in why


def test_fallback_is_not_a_language_proxy_when_it_lands_on_chinese():
    """ai-zh censored but a clean human zh track exists: a detour, not a language substitution."""
    acq = _acquire(
        _info(**{"ai-zh": 1, "zh-Hans": 1}), CANONICAL, Settings(),
        _fetcher({"zh-Hans": CENSORED_ZH, "ai-zh": CLEAN_ZH}),
    )
    assert acq.lang == "ai-zh"
    assert acq.is_language_proxy is False
    assert "fell back past" in acq.describe_fallback()


def test_every_track_censored_yields_no_acquisition():
    acq = _acquire(
        _info(**{"ai-zh": 1, "zh-Hans": 1}), CANONICAL, Settings(),
        _fetcher({"ai-zh": CENSORED_ZH, "zh-Hans": CENSORED_ZH}),
    )
    assert acq is None


def test_acquire_writes_nothing_to_stdout(capsys):
    """stdout is reserved for machine-readable output; the rejection travels in the return value."""
    _acquire(
        _info(**{"ai-zh": 1, "ai-en": 1}), CANONICAL, Settings(),
        _fetcher({"ai-zh": CENSORED_ZH, "ai-en": CLEAN_EN}),
    )
    assert capsys.readouterr().out == ""


# --- what reaches SubtitleResult ------------------------------------------------------------


def test_probe_reason_names_the_language_substitution():
    result = subtitle_probe(
        _info(**{"ai-zh": 1, "ai-en": 1}), CANONICAL, Settings(),
        _fetch=_fetcher({"ai-zh": CENSORED_ZH, "ai-en": CLEAN_EN}),
    )
    assert result.found is True
    assert result.lang == "ai-en"
    assert "language proxy" in result.reason
    assert "ai-zh" in result.reason
    assert result.rejected == (("ai-zh", result.rejected[0][1]),)


def test_probe_distinguishes_no_tracks_from_all_tracks_rejected():
    """The old wording reported both as "no original-language subtitle available"."""
    none_offered = subtitle_probe(
        {"duration": 2, "subtitles": {}}, CANONICAL, Settings(), _fetch=_fetcher({}),
    )
    assert none_offered.found is False
    assert none_offered.reason == "no subtitle track offered"

    all_rejected = subtitle_probe(
        _info(**{"ai-zh": 1}), CANONICAL, Settings(), _fetch=_fetcher({"ai-zh": CENSORED_ZH}),
    )
    assert all_rejected.found is False
    assert "every offered subtitle track was rejected" in all_rejected.reason
    assert "ai-zh" in all_rejected.reason


# --- what reaches the bundle ----------------------------------------------------------------


def _outcome_for(monkeypatch, tracks: dict[str, list[Segment]], order: list[str]):
    from blisolver.providers import bilibili as biliprov
    from blisolver.providers.bilibili import BilibiliProvider
    from blisolver.schema import QualityGate

    provider = BilibiliProvider()
    info = _info(**{k: 1 for k in order})
    monkeypatch.setattr(biliprov, "extract_info", lambda url, s: info)
    monkeypatch.setattr(provider, "_view", lambda c, s, **k: None)
    monkeypatch.setattr(provider, "_part1_segments", lambda c, s: None)
    monkeypatch.setattr(
        biliprov, "evaluate",
        lambda seg, dur, q, **kw: QualityGate(
            passed=True, punct_density=1.0, dup_ratio=0.0, nonzh_ratio=0.0, cps=5.0
        ),
    )
    monkeypatch.setattr(
        biliprov, "subtitle_probe",
        lambda i, c, s, **k: subtitle_probe(i, c, s, _fetch=_fetcher(tracks), **{
            kk: vv for kk, vv in k.items() if kk != "_fetch"
        }),
    )
    return provider.fetch_subtitle(CANONICAL, Settings(), None)


def test_accepted_outcome_reports_the_language_it_actually_delivered(monkeypatch):
    """The defect this pins: `language` was hardcoded to "zh", so an English delivery was recorded
    as Chinese. `Bundle.original_language` still says zh, so the two together expose the proxy."""
    outcome = _outcome_for(
        monkeypatch, {"ai-zh": CENSORED_ZH, "ai-en": CLEAN_EN}, ["ai-zh", "ai-en"]
    )
    assert outcome.accepted is True
    assert outcome.language == "en", "must not claim Chinese for an English track"
    assert outcome.segments == CLEAN_EN


def test_accepted_outcome_source_reason_carries_the_proxy_fact(monkeypatch):
    outcome = _outcome_for(
        monkeypatch, {"ai-zh": CENSORED_ZH, "ai-en": CLEAN_EN}, ["ai-zh", "ai-en"]
    )
    assert "language proxy" in outcome.source_reason
    assert "ai-zh" in outcome.source_reason
    assert "quality-gate: passed" in outcome.source_reason


def test_unproxied_chinese_still_reports_zh(monkeypatch):
    """The common path is unchanged: a clean ai-zh track is Chinese and says so plainly."""
    outcome = _outcome_for(monkeypatch, {"ai-zh": CLEAN_ZH}, ["ai-zh"])
    assert outcome.accepted is True
    assert outcome.language == "zh"
    assert "language proxy" not in outcome.source_reason



# --- --lang, which is a transcription lever and not an output-language request ---------------


def test_transcript_cache_key_includes_the_language(tmp_path, monkeypatch):
    """Two runs differing only in --lang must not share a cache entry.

    `lang` becomes whisper-cli's `-l` flag, so it changes the output. Leaving it out of the key
    meant the second run silently returned the first run's transcript, in the wrong language, with
    no indication the flag had been ignored.
    """
    from blisolver import cli
    from blisolver.cache import fs_key
    from blisolver.transcribe import WHISPER_MODEL

    def key_for(lang: str | None) -> str:
        return fs_key(
            "bilibili.com", "BV1", 1, stage="transcript", force_whisper=False,
            robust=False, model=WHISPER_MODEL, lang=lang or "auto",
        )

    assert key_for("zh") != key_for("en")
    assert key_for(None) != key_for("en")

    # And the key the implementation actually computes must vary with --lang.
    calls: list[str] = []
    monkeypatch.setattr(cli, "load_json", lambda cache, stage, key: calls.append(key) or None)
    monkeypatch.setattr(cli, "download_audio", lambda c, s: tmp_path / "a.m4a")
    monkeypatch.setattr(cli, "transcribe", lambda audio, robust=False, lang=None: [])
    monkeypatch.setattr(cli, "save_json", lambda *a, **k: None)

    settings = Settings()
    settings.cache_dir = tmp_path
    for lang in ("zh", "en"):
        args = cli.parse_args(["ingest", "https://www.bilibili.com/video/BV1", "--lang", lang])
        cli._whisper(CANONICAL, settings, args, reason="test", lang=lang)
    assert len(calls) == 2
    assert calls[0] != calls[1], "cache key did not change with --lang"


def test_lang_on_bilibili_warns_that_it_does_not_select_a_track(monkeypatch, capsys):
    """bilibili fixes its own candidate order, so --lang cannot pick a subtitle track there.
    Accepting the flag and quietly dropping it is what made the documented policy of aligning
    --lang to a conversation language look like it worked."""
    from blisolver import cli
    from blisolver.providers.base import SubtitleOutcome

    class _Provider:
        def fetch_subtitle(self, c, s, m, *, pinned_lang=None):
            return SubtitleOutcome(
                accepted=True, source="auto-sub", source_reason="auto-sub (ai-zh)",
                language="zh", segments=[Segment(start=0.0, end=1.0, text="你好")],
            )

    monkeypatch.setattr(cli, "select_provider", lambda url: _Provider())
    args = cli.parse_args([
        "ingest", "https://www.bilibili.com/video/BV1demo", "--lang", "en",
    ])
    cli.decide_transcript(CANONICAL, None, Settings(), args)
    err = capsys.readouterr().err
    assert "--lang en does not choose a bilibili subtitle track" in err


def test_no_warning_when_lang_is_not_passed(monkeypatch, capsys):
    from blisolver import cli
    from blisolver.providers.base import SubtitleOutcome

    class _Provider:
        def fetch_subtitle(self, c, s, m, *, pinned_lang=None):
            return SubtitleOutcome(
                accepted=True, source="auto-sub", source_reason="auto-sub (ai-zh)",
                language="zh", segments=[Segment(start=0.0, end=1.0, text="你好")],
            )

    monkeypatch.setattr(cli, "select_provider", lambda url: _Provider())
    args = cli.parse_args(["ingest", "https://www.bilibili.com/video/BV1demo"])
    cli.decide_transcript(CANONICAL, None, Settings(), args)
    assert "does not choose" not in capsys.readouterr().err
