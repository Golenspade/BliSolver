import json

import pytest

from harvest.config import Settings
from harvest.danmaku import (
    PROMPT_VERSION,
    _boundaries,
    _dedup_elevated,
    _exact_dedup,
    _merge_lines,
    _parse_response,
    _strip_think,
    represent_danmaku,
    window_records,
)
from harvest.player_api import DanmakuFetch, RawDanmaku
from harvest.providers.base import Canonical
from harvest.schema import Danmaku, DanmakuLine


def _canonical():
    return Canonical(platform="bilibili.com", id="BV1", part=1, url="https://b/video/BV1")


def _rd(ts, text, high_like=False, author=None):
    return RawDanmaku(content_ts=ts, text=text, high_like=high_like, author=author)


# ---------------------------------------------------------------------------
# Pure piece: exact-dedup pre-pass
# ---------------------------------------------------------------------------


def test_exact_dedup_collapses_byte_identical_and_sums_counts():
    records = [_rd(1.0, "hello"), _rd(2.0, "hello"), _rd(3.0, "world")]
    out = _exact_dedup(records)
    assert out == [("hello", 2, 1.0), ("world", 1, 3.0)]


def test_exact_dedup_preserves_first_occurrence_chronological_order():
    records = [_rd(1.0, "b"), _rd(2.0, "a"), _rd(3.0, "b"), _rd(4.0, "a")]
    out = _exact_dedup(records)
    assert [t for t, _, _ in out] == ["b", "a"]
    assert out == [("b", 2, 1.0), ("a", 2, 2.0)]


def test_exact_dedup_empty_input():
    assert _exact_dedup([]) == []


# ---------------------------------------------------------------------------
# Pure piece: windowing / bucketing
# ---------------------------------------------------------------------------


def test_boundaries_fixed_window_covers_duration():
    b = _boundaries(window_s=75.0, duration_s=200.0)
    assert b == [0.0, 75.0, 150.0]


def test_boundaries_no_duration_is_single_zero_window():
    assert _boundaries(window_s=75.0, duration_s=None) == [0.0]


def test_window_records_buckets_by_content_ts_and_omits_empty_windows():
    records = [_rd(1.0, "a"), _rd(80.0, "b"), _rd(81.0, "c")]
    boundaries = [0.0, 75.0, 150.0]
    windows = window_records(records, boundaries, duration_s=200.0)
    # window [0,75) has 1 record; window [75,150) has 2; window [150,200) empty -> omitted
    assert [(w[0], w[1]) for w in windows] == [(0.0, 75.0), (75.0, 150.0)]
    assert [len(w[2]) for w in windows] == [1, 2]


def test_window_records_last_window_end_uses_duration_when_given():
    records = [_rd(80.0, "b")]
    boundaries = [0.0, 75.0]
    windows = window_records(records, boundaries, duration_s=120.0)
    assert windows[0][0] == 75.0
    assert windows[0][1] == 120.0


def test_window_records_last_window_end_falls_back_to_window_span_without_duration():
    records = [_rd(80.0, "b")]
    boundaries = [0.0, 75.0]
    windows = window_records(records, boundaries, duration_s=None, window_s=75.0)
    assert windows[0][1] == 150.0


# ---------------------------------------------------------------------------
# Pure piece: <think> stripping
# ---------------------------------------------------------------------------


def test_strip_think_removes_leading_think_block():
    text = "<think>burning tokens here\nmulti-line</think>[{\"text\": \"x\", \"count\": 1}]"
    assert _strip_think(text) == '[{"text": "x", "count": 1}]'


def test_strip_think_is_noop_for_non_reasoning_response():
    text = '[{"text": "x", "count": 1}]'
    assert _strip_think(text) == text


def test_strip_think_handles_whitespace_after_block():
    text = '<think>x</think>\n\n  [{"text": "x", "count": 1}]'
    assert _strip_think(text) == '[{"text": "x", "count": 1}]'


# ---------------------------------------------------------------------------
# Pure piece: response parser (incl. CJK / think-stripping)
# ---------------------------------------------------------------------------


def test_parse_response_reads_json_array():
    text = '[{"text": "666", "count": 3}, {"text": "233", "count": 1}]'
    lines = _parse_response(text)
    assert lines == [DanmakuLine(text="666", count=3), DanmakuLine(text="233", count=1)]


def test_parse_response_handles_cjk_and_quotes():
    text = '[{"text": "「哈哈哈」笑死", "count": 5}]'
    lines = _parse_response(text)
    assert lines[0].text == "「哈哈哈」笑死"
    assert lines[0].count == 5


def test_parse_response_strips_leading_think_block_before_parsing():
    text = "<think>let me think about clustering...</think>\n" '[{"text": "牛", "count": 2}]'
    lines = _parse_response(text)
    assert lines == [DanmakuLine(text="牛", count=2)]


def test_parse_response_tolerates_surrounding_prose():
    text = 'Sure, here you go:\n[{"text": "ok", "count": 1}]\nHope that helps!'
    lines = _parse_response(text)
    assert lines == [DanmakuLine(text="ok", count=1)]


def test_parse_response_empty_array():
    assert _parse_response("[]") == []


def test_parse_response_degrades_gracefully_on_trailing_bracket_prose():
    # Trailing prose containing a literal "]" (e.g. a bilibili emote shortcode like "[doge]")
    # confuses the naive find("[")/rfind("]") array extraction: rfind grabs the "]" from the
    # emote instead of the JSON array's true close, producing an invalid JSON.loads payload.
    # This must degrade gracefully (no crash aborting the whole stage), not raise.
    text = '[{"text": "ok", "count": 1}] closing remark with emote [doge]'
    assert _parse_response(text) == []


# ---------------------------------------------------------------------------
# Pure piece: merging lines across sub-batches
# ---------------------------------------------------------------------------


def test_merge_lines_combines_identical_representative_text_summing_counts():
    lines = [
        (DanmakuLine(text="a", count=2), 1.0),
        (DanmakuLine(text="b", count=1), 2.0),
        (DanmakuLine(text="a", count=3), 3.0),
    ]
    merged = _merge_lines(lines)
    assert merged == [
        (DanmakuLine(text="a", count=5), 1.0),
        (DanmakuLine(text="b", count=1), 2.0),
    ]


def test_merge_lines_preserves_first_occurrence_order():
    lines = [(DanmakuLine(text="z", count=1), 5.0), (DanmakuLine(text="a", count=1), 6.0)]
    merged = _merge_lines(lines)
    assert [l.text for l, _ts in merged] == ["z", "a"]


# ---------------------------------------------------------------------------
# Stub LLM client for the fenced-call + cache tests
# ---------------------------------------------------------------------------


class _StubChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _StubResponse:
    def __init__(self, content):
        self.choices = [_StubChoice(content)]


class _StubCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("stub LLM client called more times than expected")
        return _StubResponse(self._responses.pop(0))


class _StubChat:
    def __init__(self, responses):
        self.completions = _StubCompletions(responses)


class _StubClient:
    def __init__(self, responses):
        self.chat = _StubChat(responses)


# ---------------------------------------------------------------------------
# represent_danmaku: end-to-end with an injected stub client
# ---------------------------------------------------------------------------


def _settings(tmp_path):
    s = Settings()
    s.cache_dir = tmp_path / "cache"
    s.lmstudio_danmaku_model = "stub-model"
    return s


def test_represent_danmaku_orders_lines_chronologically_not_count_descending(tmp_path):
    records = [
        _rd(1.0, "first thought"),
        _rd(2.0, "second thought"),
        _rd(2.5, "second thought"),
        _rd(2.6, "second thought"),
    ]
    fetch = DanmakuFetch(source_total=4, fetched_total=4, records=records)
    # The stub deliberately returns clusters in a NON-chronological (count-descending) order --
    # "second thought" (count 3) FIRST, "first thought" (count 1) SECOND -- exactly the ordering
    # bug the probe found (SPEC-locked: count-sort destroys the temporal signal). The prompt asks
    # the LLM for chronological order, but real model drift can violate it, so the code must
    # MECHANICALLY reorder by each line's first-occurrence position in the input entries rather
    # than trust the LLM's returned order. If the code just passed the response through, this
    # assertion would fail.
    resp = json.dumps([
        {"text": "second thought", "count": 3},
        {"text": "first thought", "count": 1},
    ])
    client = _StubClient([resp])
    settings = _settings(tmp_path)

    result = represent_danmaku(
        _canonical(), fetch, settings, boundaries=[0.0], duration_s=10.0, client=client
    )

    assert isinstance(result, Danmaku)
    assert len(result.windows) == 1
    lines = result.windows[0].lines
    assert [l.text for l in lines] == ["first thought", "second thought"]
    assert [l.count for l in lines] == [1, 3]
    assert result.windows[0].total == 4
    assert result.source_total == 4
    assert result.fetched_total == 4
    assert result.model == "stub-model"
    assert len(client.chat.completions.calls) == 1


def test_represent_danmaku_reorders_unmatched_representative_after_matched_ones(tmp_path):
    # A representative text that doesn't verbatim-match any input entry is already a prompt-rule
    # violation, but the mechanical reorder must degrade gracefully rather than crash: matched
    # lines are sorted by first-appearance in the input; unmatched lines are kept after them, in
    # the LLM's received order.
    records = [
        _rd(1.0, "alpha"),
        _rd(2.0, "beta"),
    ]
    fetch = DanmakuFetch(source_total=2, fetched_total=2, records=records)
    resp = json.dumps([
        {"text": "not verbatim", "count": 1},
        {"text": "beta", "count": 1},
        {"text": "alpha", "count": 1},
    ])
    client = _StubClient([resp])
    settings = _settings(tmp_path)

    result = represent_danmaku(
        _canonical(), fetch, settings, boundaries=[0.0], duration_s=10.0, client=client
    )

    lines = result.windows[0].lines
    assert [l.text for l in lines] == ["alpha", "beta", "not verbatim"]


def test_represent_danmaku_cache_hit_skips_llm_call(tmp_path):
    records = [_rd(1.0, "hi")]
    fetch = DanmakuFetch(source_total=1, fetched_total=1, records=records)
    resp = json.dumps([{"text": "hi", "count": 1}])
    settings = _settings(tmp_path)

    client1 = _StubClient([resp])
    first = represent_danmaku(
        _canonical(), fetch, settings, boundaries=[0.0], duration_s=10.0, client=client1
    )
    assert len(client1.chat.completions.calls) == 1

    client2 = _StubClient([])  # any call raises AssertionError
    second = represent_danmaku(
        _canonical(), fetch, settings, boundaries=[0.0], duration_s=10.0, client=client2
    )
    assert len(client2.chat.completions.calls) == 0
    assert second.model_dump() == first.model_dump()


def test_represent_danmaku_different_fetched_danmaku_invalidates_cache(tmp_path):
    settings = _settings(tmp_path)
    resp1 = json.dumps([{"text": "hi", "count": 1}])
    fetch1 = DanmakuFetch(
        source_total=1, fetched_total=1, records=[_rd(1.0, "hi")]
    )
    client1 = _StubClient([resp1])
    represent_danmaku(
        _canonical(), fetch1, settings, boundaries=[0.0], duration_s=10.0, client=client1
    )

    resp2 = json.dumps([{"text": "bye", "count": 1}])
    fetch2 = DanmakuFetch(
        source_total=1, fetched_total=1, records=[_rd(1.0, "bye")]
    )
    client2 = _StubClient([resp2])
    second = represent_danmaku(
        _canonical(), fetch2, settings, boundaries=[0.0], duration_s=10.0, client=client2
    )
    # Different fetched danmaku -> must NOT hit the stale cache entry -> LLM called again.
    assert len(client2.chat.completions.calls) == 1
    assert second.windows[0].lines[0].text == "bye"


def test_represent_danmaku_omits_empty_windows(tmp_path):
    settings = _settings(tmp_path)
    records = [_rd(80.0, "only here")]
    fetch = DanmakuFetch(source_total=1, fetched_total=1, records=records)
    resp = json.dumps([{"text": "only here", "count": 1}])
    client = _StubClient([resp])

    result = represent_danmaku(
        _canonical(), fetch, settings, boundaries=[0.0, 75.0, 150.0], duration_s=200.0,
        client=client,
    )
    assert len(result.windows) == 1
    assert result.windows[0].start == 75.0


def test_represent_danmaku_no_records_produces_no_windows_and_no_llm_call(tmp_path):
    settings = _settings(tmp_path)
    fetch = DanmakuFetch(source_total=0, fetched_total=0, records=[])
    client = _StubClient([])

    result = represent_danmaku(
        _canonical(), fetch, settings, boundaries=[0.0], duration_s=10.0, client=client
    )
    assert result.windows == []
    assert len(client.chat.completions.calls) == 0


def test_represent_danmaku_batches_large_windows_and_merges_results(tmp_path, monkeypatch):
    import harvest.danmaku as dm

    monkeypatch.setattr(dm, "_BATCH_CAP", 2)
    records = [_rd(float(i), f"msg{i}") for i in range(5)]  # 5 distinct entries, cap=2 -> 3 batches
    fetch = DanmakuFetch(source_total=5, fetched_total=5, records=records)
    settings = _settings(tmp_path)

    responses = [
        json.dumps([{"text": "msg0", "count": 1}, {"text": "msg1", "count": 1}]),
        json.dumps([{"text": "msg2", "count": 1}, {"text": "msg3", "count": 1}]),
        json.dumps([{"text": "msg4", "count": 1}]),
    ]
    client = _StubClient(responses)

    result = represent_danmaku(
        _canonical(), fetch, settings, boundaries=[0.0], duration_s=10.0, client=client
    )
    assert len(client.chat.completions.calls) == 3
    assert [l.text for l in result.windows[0].lines] == ["msg0", "msg1", "msg2", "msg3", "msg4"]
    assert result.windows[0].total == 5


def test_represent_danmaku_default_boundaries_windows_via_window_s(tmp_path):
    # Every other test passes explicit `boundaries=[...]`. This exercises the default branch:
    # window_s-only -> _boundaries()-derived boundaries + the window_s cache key. Task 4 relies on
    # this path, so it must be covered end-to-end with a stub client.
    records = [
        _rd(1.0, "early one"),
        _rd(80.0, "late one"),
        _rd(81.0, "late two"),
    ]
    fetch = DanmakuFetch(source_total=3, fetched_total=3, records=records)
    settings = _settings(tmp_path)
    resp1 = json.dumps([{"text": "early one", "count": 1}])
    resp2 = json.dumps([
        {"text": "late one", "count": 1},
        {"text": "late two", "count": 1},
    ])
    client = _StubClient([resp1, resp2])

    result = represent_danmaku(
        _canonical(), fetch, settings, window_s=75.0, duration_s=150.0, client=client
    )

    assert isinstance(result, Danmaku)
    assert len(client.chat.completions.calls) == 2
    assert [(w.start, w.end) for w in result.windows] == [(0.0, 75.0), (75.0, 150.0)]
    assert [w.total for w in result.windows] == [1, 2]
    assert [l.text for l in result.windows[0].lines] == ["early one"]
    assert [l.text for l in result.windows[1].lines] == ["late one", "late two"]
    assert result.source_total == 3
    assert result.fetched_total == 3
    assert result.model == "stub-model"


def test_danmaku_line_high_like_defaults_false():
    line = DanmakuLine(text="x", count=1)
    assert line.high_like is False
    promoted = DanmakuLine(text="x", count=1, high_like=True)
    assert promoted.high_like is True


def test_danmakuline_author_field_defaults_none_and_accepts_roles():
    assert DanmakuLine(text="x").author is None
    assert DanmakuLine(text="x", author="owner").author == "owner"
    assert DanmakuLine(text="x", author="staff").author == "staff"


def test_represent_danmaku_extracts_high_like_verbatim_before_clustering(tmp_path):
    # A promoted (high_like) danmaku whose text is byte-identical to an ordinary flood must
    # still appear as its OWN high_like=True line -- the flood clusters separately from it, and
    # the LLM never sees the promoted copy (extract-before-cluster).
    records = [
        _rd(1.0, "same text", high_like=True),
        _rd(2.0, "same text"),
        _rd(2.5, "same text"),
    ]
    fetch = DanmakuFetch(source_total=3, fetched_total=3, records=records)
    resp = json.dumps([{"text": "same text", "count": 2}])
    client = _StubClient([resp])
    settings = _settings(tmp_path)

    result = represent_danmaku(
        _canonical(), fetch, settings, boundaries=[0.0], duration_s=10.0, client=client
    )

    lines = result.windows[0].lines
    assert len(lines) == 2
    promoted_lines = [l for l in lines if l.high_like]
    ordinary_lines = [l for l in lines if not l.high_like]
    assert [l.text for l in promoted_lines] == ["same text"]
    assert [l.count for l in promoted_lines] == [1]
    assert [l.text for l in ordinary_lines] == ["same text"]
    assert [l.count for l in ordinary_lines] == [2]
    assert result.windows[0].total == 3
    # The LLM only ever saw the ordinary flood (2), never the promoted copy.
    call_content = client.chat.completions.calls[0]["messages"][0]["content"]
    call_payload = json.loads(call_content.split("Input:\n", 1)[1])
    assert call_payload == [{"text": "same text", "count": 2}]


def test_represent_danmaku_three_way_chronological_interleave(tmp_path):
    # promoted, clustered, and singleton lines must merge-sort by first-occurrence content_ts,
    # not by category or by the LLM's returned order.
    records = [
        _rd(1.0, "alpha"),  # ordinary singleton, ts=1.0
        _rd(2.0, "promoted one", high_like=True),  # promoted, ts=2.0
        _rd(3.0, "beta"),  # ordinary, part of flood
        _rd(3.5, "beta"),
        _rd(5.0, "gamma"),  # ordinary singleton, ts=5.0
    ]
    fetch = DanmakuFetch(source_total=5, fetched_total=5, records=records)
    # Stub deliberately returns clusters in a scrambled order to prove mechanical reordering.
    resp = json.dumps([
        {"text": "beta", "count": 2},
        {"text": "alpha", "count": 1},
        {"text": "gamma", "count": 1},
    ])
    client = _StubClient([resp])
    settings = _settings(tmp_path)

    result = represent_danmaku(
        _canonical(), fetch, settings, boundaries=[0.0], duration_s=10.0, client=client
    )

    lines = result.windows[0].lines
    assert [(l.text, l.high_like) for l in lines] == [
        ("alpha", False),
        ("promoted one", True),
        ("beta", False),
        ("gamma", False),
    ]
    assert result.windows[0].total == 5


def test_represent_danmaku_llm_payload_has_no_high_like_or_metadata_keys(tmp_path):
    records = [
        _rd(1.0, "promoted", high_like=True),
        _rd(2.0, "ordinary"),
    ]
    fetch = DanmakuFetch(source_total=2, fetched_total=2, records=records)
    resp = json.dumps([{"text": "ordinary", "count": 1}])
    client = _StubClient([resp])
    settings = _settings(tmp_path)

    represent_danmaku(
        _canonical(), fetch, settings, boundaries=[0.0], duration_s=10.0, client=client
    )

    assert len(client.chat.completions.calls) == 1
    content = client.chat.completions.calls[0]["messages"][0]["content"]
    payload_json = content.split("Input:\n", 1)[1]
    payload = json.loads(payload_json)
    for item in payload:
        assert set(item.keys()) == {"text", "count"}
        assert "high_like" not in item
        assert "content_ts" not in item


def test_fingerprint_differs_when_only_high_like_flag_differs(tmp_path):
    from harvest.danmaku import _fingerprint

    fetch_a = DanmakuFetch(
        source_total=1, fetched_total=1, records=[_rd(1.0, "hi", high_like=False)]
    )
    fetch_b = DanmakuFetch(
        source_total=1, fetched_total=1, records=[_rd(1.0, "hi", high_like=True)]
    )
    assert _fingerprint(fetch_a) != _fingerprint(fetch_b)


def test_represent_danmaku_cache_restages_when_high_like_flag_changes(tmp_path):
    settings = _settings(tmp_path)
    resp1 = json.dumps([{"text": "hi", "count": 1}])
    fetch1 = DanmakuFetch(
        source_total=1, fetched_total=1, records=[_rd(1.0, "hi", high_like=False)]
    )
    client1 = _StubClient([resp1])
    first = represent_danmaku(
        _canonical(), fetch1, settings, boundaries=[0.0], duration_s=10.0, client=client1
    )
    assert not first.windows[0].lines[0].high_like

    resp2 = json.dumps([{"text": "hi", "count": 0}])  # ordinary side sees nothing now
    fetch2 = DanmakuFetch(
        source_total=1, fetched_total=1, records=[_rd(1.0, "hi", high_like=True)]
    )
    client2 = _StubClient([])  # promoted-only window -> no LLM call needed
    second = represent_danmaku(
        _canonical(), fetch2, settings, boundaries=[0.0], duration_s=10.0, client=client2
    )
    assert second.windows[0].lines[0].high_like
    assert second.windows[0].lines[0].count == 1


def test_represent_danmaku_prompt_forbids_gaps_section_and_count_descending():
    # Static assertion on the prompt contract itself (corrections applied to the seed).
    from harvest.danmaku import DANMAKU_PROMPT

    assert "GAPS" not in DANMAKU_PROMPT
    assert "descending" not in DANMAKU_PROMPT.lower()
    assert "chronolog" in DANMAKU_PROMPT.lower()


def test_dedup_elevated_keys_on_text_highlike_author_and_counts():
    from harvest.danmaku import _dedup_elevated
    records = [
        _rd(1.0, "同", high_like=True),
        _rd(2.0, "同", high_like=True),          # same (text, flags) -> count 2
        _rd(3.0, "同", author="owner"),           # same text, different flags -> distinct line
        _rd(4.0, "改一下", author="staff"),
    ]
    out = _dedup_elevated(records)
    assert [(l.text, l.count, l.high_like, l.author) for l, _ in out] == [
        ("同", 2, True, None),
        ("同", 1, False, "owner"),
        ("改一下", 1, False, "staff"),
    ]
    assert [ts for _, ts in out] == [1.0, 3.0, 4.0]  # first-occurrence content_ts


def test_represent_danmaku_extracts_author_lines_before_clustering(tmp_path):
    # The LLM stub clusters ONLY what it is handed; if an author line reached it, it would be
    # collapsed/echoed. Assert author + high_like lines bypass it and keep verbatim text + flags.
    settings = Settings()
    settings.cache_dir = tmp_path / "cache"

    class _StubClient:
        def __init__(self):
            self.seen_texts = []

        class _Chat:
            def __init__(self, outer):
                self.completions = _StubClient._Completions(outer)

        class _Completions:
            def __init__(self, outer):
                self.outer = outer

            def create(self, *, model, messages, temperature, max_tokens):
                payload = messages[0]["content"]
                self.outer.seen_texts.append(payload)
                # Echo the input array back unchanged (a faithful no-op clusterer).
                start = payload.index("[")
                arr = payload[start:]
                return type("R", (), {"choices": [type("C", (), {"message": type(
                    "M", (), {"content": arr})()})()]})()

        def __init_subclass__(cls):  # pragma: no cover
            pass

    client = _StubClient()
    client.chat = _StubClient._Chat(client)

    fetch = DanmakuFetch(
        source_total=None, fetched_total=4,
        records=[
            _rd(1.0, "普通评论"),
            _rd(2.0, "这里我口误了", author="owner"),
            _rd(3.0, "配音补充", author="staff"),
            _rd(4.0, "神弹幕", high_like=True),
        ],
    )
    result = represent_danmaku(
        _canonical(), fetch, settings, window_s=75.0, duration_s=75.0, client=client
    )
    lines = result.windows[0].lines
    by_text = {l.text: l for l in lines}
    assert by_text["这里我口误了"].author == "owner"
    assert by_text["配音补充"].author == "staff"
    assert by_text["神弹幕"].high_like is True
    # The clusterer only ever saw the ordinary line, never an elevated one.
    joined = " ".join(client.seen_texts)
    assert "普通评论" in joined
    assert "这里我口误了" not in joined
    assert "配音补充" not in joined
    assert "神弹幕" not in joined
    # Chronological order preserved across kinds.
    assert [l.text for l in lines] == ["普通评论", "这里我口误了", "配音补充", "神弹幕"]


@pytest.mark.live
def test_live_danmaku_smoke():
    """Real LM Studio path: fenced clustering on a tiny hand-built batch. Excluded by default
    (-m 'not live'); run explicitly with `-m live` against a running LM Studio instance that has
    HARVEST_DANMAKU_MODEL loaded."""
    settings = Settings.load()
    assert settings.lmstudio_danmaku_model, "set HARVEST_DANMAKU_MODEL to run this smoke test"

    records = [
        _rd(1.0, "233"),
        _rd(1.5, "233"),
        _rd(2.0, "哈哈哈哈哈"),
        _rd(3.0, "666"),
    ]
    fetch = DanmakuFetch(source_total=4, fetched_total=4, records=records)
    result = represent_danmaku(_canonical(), fetch, settings, boundaries=[0.0], duration_s=10.0)

    assert result.windows
    assert result.windows[0].total == 4
    all_text = " ".join(l.text for l in result.windows[0].lines)
    assert all_text  # got SOME representation back, not empty/truncated (D7-style reasoning defense)
