from harvest.config import AutoSubNet
from harvest.schema import Segment
from harvest.providers.youtube_autosub import (
    _is_clean_bcp47,
    clean_srt_segments,
    pick_auto_key,
    pick_human_key,
    structural_net,
)


def test_is_clean_bcp47_accepts_bare_language():
    assert _is_clean_bcp47("en") is True


def test_is_clean_bcp47_accepts_region_and_script():
    assert _is_clean_bcp47("de-DE") is True       # 2-alpha region
    assert _is_clean_bcp47("zh-Hant") is True      # 4-alpha script
    assert _is_clean_bcp47("es-419") is True       # 3-digit region
    assert _is_clean_bcp47("zh-Hant-CN") is True   # script + region


def test_is_clean_bcp47_rejects_hash_suffixed_keys():
    # yt-dlp's community-translation / multi-track disambiguation keys — a trailing segment that
    # is neither a valid region nor script subtag. These must never be treated as a clean tag.
    assert _is_clean_bcp47("en-US-njLgzgtehjs") is False
    assert _is_clean_bcp47("en-eEY6OEpapPo") is False


def test_pick_human_key_exact_wins():
    subs = {"en": [{"ext": "vtt"}], "de-DE": [{"ext": "vtt"}]}
    assert pick_human_key(subs, "en") == "en"


def test_pick_human_key_regional_target_matches_bare():
    # info["language"] "en-US" but the human track is keyed "en".
    assert pick_human_key({"en": [{"ext": "vtt"}]}, "en-US") == "en"


def test_pick_human_key_bare_target_matches_regional():
    # info["language"] "de" but the only human German track is keyed "de-DE".
    assert pick_human_key({"de-DE": [{"ext": "vtt"}], "en": [{"ext": "vtt"}]}, "de") == "de-DE"


def test_pick_human_key_matches_script_variant():
    assert pick_human_key({"zh-Hans": [{"ext": "vtt"}]}, "zh-Hant") == "zh-Hans"


def test_pick_human_key_ignores_community_translation_keys():
    # Despacito shape: original is es; the en-* keys are community English TRANSLATIONS with a
    # hash suffix. A bare "en" target must NOT reuse a translation as the original -> None.
    subs = {"es": [{"ext": "vtt"}], "en-eEY6OEpapPo": [{"ext": "vtt"}],
            "en-US-njLgzgtehjs": [{"ext": "vtt"}]}
    assert pick_human_key(subs, "en") is None


def test_pick_human_key_no_same_base_returns_none():
    assert pick_human_key({"fr": [{"ext": "vtt"}], "es": [{"ext": "vtt"}]}, "de") is None


def test_pick_human_key_prefers_fuller_then_shorter():
    # Target "en-US": a key starting with the full target wins over a bare same-base key.
    subs = {"en": [{"ext": "vtt"}], "en-US": [{"ext": "vtt"}]}
    assert pick_human_key(subs, "en-US") == "en-US"


def test_pick_auto_key_prefers_orig_over_plain_for_known_lang():
    auto = {"en": [{"ext": "srt"}], "en-orig": [{"ext": "srt"}]}
    assert pick_auto_key(auto, "en") == "en-orig"


def test_pick_auto_key_falls_back_to_plain_when_no_orig():
    auto = {"ko": [{"ext": "srt"}]}
    assert pick_auto_key(auto, "ko") == "ko"


def test_pick_auto_key_known_lang_absent_returns_none():
    auto = {"fr": [{"ext": "srt"}], "fr-orig": [{"ext": "srt"}]}
    assert pick_auto_key(auto, "de") is None


def test_pick_auto_key_unknown_lang_uses_sole_orig():
    auto = {"en-orig": [{"ext": "srt"}], "es": [{"ext": "srt"}], "de": [{"ext": "srt"}]}
    assert pick_auto_key(auto, None) == "en-orig"


def test_pick_auto_key_unknown_lang_rejects_zero_orig():
    assert pick_auto_key({"en": [{"ext": "srt"}]}, None) is None


def test_pick_auto_key_unknown_lang_rejects_multiple_orig():
    auto = {"en-orig": [{"ext": "srt"}], "zh-orig": [{"ext": "srt"}]}
    assert pick_auto_key(auto, None) is None


def test_pick_auto_key_empty_dict_returns_none():
    assert pick_auto_key({}, "en") is None
    assert pick_auto_key({}, None) is None


def test_pick_auto_key_regional_target_matches_base_orig():
    # yt-dlp's info["language"] is often a regional BCP-47 tag ("en-US") while the caption keys are
    # the bare subtag ("en"/"en-orig"). This is the -QFHIoCo-Ko failure: en-US matched nothing.
    auto = {"en": [{"ext": "srt"}], "en-orig": [{"ext": "srt"}], "es": [{"ext": "srt"}]}
    assert pick_auto_key(auto, "en-US") == "en-orig"


def test_pick_auto_key_regional_target_single_audio_uses_base_asr():
    # Single-audio video: no *-orig keys at all, so the bare base-subtag key IS the original ASR
    # (the ~200 other keys are machine translations). A regional target should still reuse it.
    auto = {"en": [{"ext": "srt"}], "es": [{"ext": "srt"}], "fr": [{"ext": "srt"}]}
    assert pick_auto_key(auto, "en-US") == "en"


def test_pick_auto_key_regional_target_wont_grab_translation():
    # Original audio is Spanish (es-orig); "en" here is a machine translation. A regional en-US
    # target must NOT grab that translated track -> None (-> Whisper). Original-audio-safe.
    auto = {"es-orig": [{"ext": "srt"}], "en": [{"ext": "srt"}], "fr": [{"ext": "srt"}]}
    assert pick_auto_key(auto, "en-US") is None


def test_pick_auto_key_exact_regional_key_still_wins():
    # If yt-dlp actually keys the track by the regional tag, exact match must still take priority.
    auto = {"en-US-orig": [{"ext": "srt"}], "en-orig": [{"ext": "srt"}]}
    assert pick_auto_key(auto, "en-US") == "en-US-orig"


def test_pick_auto_key_chinese_region_target_matches_script_orig():
    # Regional Mandarin target (zh-CN, not present as an exact key) with the original audio keyed by
    # SCRIPT (zh-Hans-orig). Same base 'zh' + the -orig marker means it IS the original audio ->
    # reuse it despite the region/script tag mismatch. The Mandarin analogue of the en-US failure.
    auto = {"zh-Hans-orig": [{"ext": "srt"}], "zh-Hans": [{"ext": "srt"}],
            "zh-Hant": [{"ext": "srt"}], "en": [{"ext": "srt"}]}
    assert pick_auto_key(auto, "zh-CN") == "zh-Hans-orig"


def test_pick_auto_key_chinese_prefers_matching_script_orig():
    # When both scripts have an original-audio track, prefer the one matching the target's script.
    auto = {"zh-Hans-orig": [{"ext": "srt"}], "zh-Hant-orig": [{"ext": "srt"}]}
    # zh-TW: base zh, neither -orig startswith it -> shortest/stable pick.
    assert pick_auto_key(auto, "zh-TW") == "zh-Hans-orig"
    # zh-Hant: startswith zh-Hant -> the matching-script original wins.
    assert pick_auto_key(auto, "zh-Hant") == "zh-Hant-orig"


def test_pick_auto_key_cantonese_wont_grab_zh_translation():
    # Cantonese shape: original is yue-orig; zh-Hans/zh-Hant are TRANSLATIONS (no -orig).
    # A zh-* target must not steal the Cantonese original nor a zh translation -> None.
    auto = {"yue-orig": [{"ext": "srt"}], "zh-Hans": [{"ext": "srt"}], "zh-Hant": [{"ext": "srt"}]}
    assert pick_auto_key(auto, "zh-CN") is None


def test_pick_auto_key_chinese_single_audio_uses_same_base_asr():
    # Single-audio Mandarin: no -orig keys, ASR under a script-tagged key. Regional/script target
    # still reuses the same-language ASR rather than falling to Whisper.
    auto = {"zh-Hans": [{"ext": "srt"}], "en": [{"ext": "srt"}], "ja": [{"ext": "srt"}]}
    assert pick_auto_key(auto, "zh-CN") == "zh-Hans"


_SRT = (
    "1\n00:00:15,000 --> 00:00:18,760\n>> Yeah, we're good.\n\n"
    "2\n00:00:17,040 --> 00:00:20,440\nOkay, folks.\n\n"
    "3\n00:00:07,205 --> 00:00:09,225\n[music]\n"
)


def test_clean_srt_segments_strips_leading_speaker_marker():
    segs = clean_srt_segments(_SRT)
    assert segs[0].text == "Yeah, we're good."   # ">> " stripped


def test_clean_srt_segments_keeps_music_cue():
    segs = clean_srt_segments(_SRT)
    assert any(s.text == "[music]" for s in segs)


def test_clean_srt_segments_preserves_timing():
    segs = clean_srt_segments(_SRT)
    assert segs[0].start == 15.0 and segs[0].end == 18.76


def _segs(n, *, start=0.0, step=2.0, text="hello there friend"):
    return [Segment(start=start + i * step, end=start + i * step + step, text=text) for i in range(n)]


def test_structural_net_passes_healthy_track():
    net = AutoSubNet()
    segs = _segs(60)  # 120s of cues, dense text
    passed, reason = structural_net(segs, 120.0, net)
    assert passed is True and "passed" in reason


def test_structural_net_rejects_too_few_cues():
    passed, reason = structural_net(_segs(3), 120.0, AutoSubNet())
    assert passed is False and "cues" in reason


def test_structural_net_rejects_truncated_coverage():
    # 60 cues ending at ~120s, but the video is 400s -> ratio 0.30 < 0.70
    passed, reason = structural_net(_segs(60), 400.0, AutoSubNet())
    assert passed is False and "coverage" in reason


def test_structural_net_rejects_empty_but_covered():
    # Five cues spanning the whole duration but almost no text -> cps below floor
    # (>= min_cues so presence passes and coverage is full; only the cps check rejects)
    segs = [Segment(start=0.0, end=1.0, text="[music]"), Segment(start=59.0, end=60.0, text="[music]"), Segment(start=119.0, end=120.0, text="[music]"), Segment(start=179.0, end=180.0, text="[music]"), Segment(start=299.0, end=300.0, text="[music]")]
    passed, reason = structural_net(segs, 300.0, AutoSubNet())
    assert passed is False and "chars-per-second" in reason


def test_structural_net_no_duration_skips_coverage_and_cps():
    # duration 0/unknown: only the presence check applies
    passed, reason = structural_net(_segs(60), 0.0, AutoSubNet())
    assert passed is True
