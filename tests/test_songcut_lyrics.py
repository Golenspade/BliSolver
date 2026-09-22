import pytest

from blisolver.songcut.lyrics import (
    curate,
    lrc,
    merge_window,
    parse_model_json,
    parse_words,
    short_windows,
    validate_curation,
)
from blisolver.songcut.models import Word


def words():
    return [Word(start=i*2, end=i*2+1, text=text, source="test")
            for i, text in enumerate(["你好", "再见", "你好", "再见"])]


def test_repeat_chorus_and_omitted_words_remain_timed():
    result = validate_curation({"rows": [[0, 1, "你好再见", "singing"],
                                         [2, 2, "你好", "singing"]]}, words())
    assert [row["start"] for row in result["lines"]] == [0, 4, 6]
    assert result["lines"][-1]["kind"] == "uncertain"
    assert "omitted_words_recovered" in result["issues"]
    assert lrc(result["lines"]).startswith("[00:00.00]你好再见")
    assert result["reviewed"] is False


@pytest.mark.parametrize("rows", [
    [[0, 2, "x", "singing"], [2, 3, "x", "singing"]],
    [[0, 9, "x", "singing"]], [[False, 1, "x", "singing"]],
    [[2, 3, "x", "singing"], [0, 1, "x", "singing"]],
])
def test_invalid_index_partitions_fail(rows):
    with pytest.raises(ValueError):
        validate_curation({"rows": rows}, words())


def test_expanded_standard_lyrics_and_lrc_injection_reverted():
    result = validate_curation({"rows": [[0, 0, "未唱出的完整标准歌词"*5, "singing"],
                                         [1, 1, "[01:30]", "singing"]]}, words())
    assert result["lines"][0]["text"] == "你好"
    assert result["lines"][1]["text"] == "再见"


def test_short_window_core_and_conflict_evidence():
    window = {"core_start": 0, "core_end": 3}
    alternate = [Word(start=0, end=1, text="不同", source="flash")]
    merged, issues = merge_window(words(), alternate, window)
    assert merged == words()
    assert issues == ["short_window_conflict"]
    merged, _ = merge_window([], alternate, window)
    assert merged == alternate
    planned = short_windows([], 125)
    assert planned[0]["core_start"] == 0
    assert planned[-1]["core_end"] == 125
    assert max(w["end"]-w["start"] for w in planned) <= 60


def test_no_word_timestamps_does_not_invent_lyrics():
    parsed, issues = parse_words({"transcripts": [{"sentences": [{"text": "ignored"}]}]}, 50, "asr")
    assert parsed == [] and "no_word_timestamps" in issues
    assert curate(parsed)["lines"] == []


def test_invalid_times_rejected_and_window_offset_added():
    raw = {"output": {"sentence": {"words": [
        {"begin_time": 1000, "end_time": 2000, "text": "hello"},
        {"begin_time": -1000, "end_time": 2000, "text": "wrong"},
        {"begin_time": 2000, "end_time": 100000, "text": "past"}]}}}
    parsed, issues = parse_words(raw, 5, "flash", offset=60)
    assert len(parsed) == 1 and parsed[0].start == 61
    assert issues


def test_truncated_model_json_rejected():
    with pytest.raises(ValueError, match="truncated"):
        parse_model_json({"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]})
