"""Tests for the command-danmaku (--interactions) stack: schema types, the pure protobuf
`extra`-payload build step, and the HTTP fetch. Fully offline/hermetic — the fetch tests drive
a fake opener, and the decode tests synthesize protobuf inline (no network, no cookies)."""

from __future__ import annotations

from harvest.schema import Bundle, Grade, Interactions, Vote, VoteOption


def test_interactions_types_roundtrip():
    interactions = Interactions(
        votes=[
            Vote(
                question="喜欢哪个版本？",
                options=[
                    VoteOption(text="只加黄葱", count=153),
                    VoteOption(text="其他，请补充", count=40, write_in=True),
                ],
                total_count=443,
                ts=371.3,
            )
        ],
        grades=[Grade(avg_score=9.9, count=178)],
    )
    dumped = interactions.model_dump()
    assert dumped["votes"][0]["question"] == "喜欢哪个版本？"
    assert dumped["votes"][0]["options"][1] == {"text": "其他，请补充", "count": 40, "write_in": True}
    assert dumped["votes"][0]["total_count"] == 443
    assert dumped["votes"][0]["ts"] == 371.3
    assert dumped["grades"][0] == {"avg_score": 9.9, "count": 178}
    # round-trips back into the model
    assert Interactions(**dumped) == interactions


def test_interactions_empty_defaults():
    empty = Interactions()
    assert empty.votes == []
    assert empty.grades == []


def test_bundle_interactions_defaults_none():
    # A Bundle built without interactions leaves the field null (schema stays 1.0, additive).
    assert "interactions" in Bundle.model_fields
    assert Bundle.model_fields["interactions"].default is None


from harvest.interactions import build_interactions, fetch_interactions
from harvest.interactions_proto import RawCommandDm
from harvest.config import Settings
from harvest.resolve import Canonical
from harvest.player_api import ViewData


_VOTE_EXTRA = (
    '{"vote_id":16341116,"question":"喜欢哪个版本？","cnt":443,"options":['
    '{"idx":1,"desc":"只加黄葱","cnt":153,"has_self_def":false},'
    '{"idx":2,"desc":"木耳莴笋冬笋","cnt":250,"has_self_def":false},'
    '{"idx":3,"desc":"其他，请补充","cnt":40,"has_self_def":true}]}'
)
_GRADE_EXTRA = '{"msg":"感谢大家支持！","grade_id":6816364,"count":178,"avg_score":9.9,"mid_score":0}'


def test_build_interactions_parses_vote_and_grade():
    raws = [
        RawCommandDm("#VOTE#", "投票弹幕", 371300, _VOTE_EXTRA, "111"),
        RawCommandDm("#GRADE#", "感谢", None, _GRADE_EXTRA, "222"),
    ]
    result = build_interactions(raws)
    assert len(result.votes) == 1
    v = result.votes[0]
    assert v.question == "喜欢哪个版本？"
    assert v.total_count == 443
    assert v.ts == 371.3
    assert v.options[0] == VoteOption(text="只加黄葱", count=153, write_in=False)
    assert v.options[2] == VoteOption(text="其他，请补充", count=40, write_in=True)
    assert result.grades == [Grade(avg_score=9.9, count=178)]


def test_build_interactions_drops_non_whitelisted_kinds():
    raws = [
        RawCommandDm("#ATTENTION#", "关注弹幕", 43200, '{"duration":5000}', "1"),
        RawCommandDm("#LINK#", "在线推理", 2850, '{"title":"x"}', "2"),
    ]
    result = build_interactions(raws)
    assert result.votes == []
    assert result.grades == []


def test_build_interactions_skips_malformed_extra():
    raws = [
        RawCommandDm("#VOTE#", "投票弹幕", 100, "not json {", "1"),
        RawCommandDm("#GRADE#", "g", None, _GRADE_EXTRA, "2"),
    ]
    result = build_interactions(raws)
    assert result.votes == []  # the bad vote is skipped, not fatal
    assert result.grades == [Grade(avg_score=9.9, count=178)]


def test_build_interactions_skips_wrong_shape_options_without_aborting_batch():
    raws = [
        RawCommandDm("#VOTE#", "v", 100, '{"question":"q","cnt":1,"options":"abc"}', "1"),
        RawCommandDm("#GRADE#", "g", None, _GRADE_EXTRA, "2"),
    ]
    result = build_interactions(raws)
    # the wrong-shape vote is tolerated: it lands with empty options (question/cnt still parse),
    # and the following grade still lands too — one bad widget never aborts the batch.
    assert result.votes == [Vote(question="q", options=[], total_count=1, ts=0.1)]
    assert result.grades == [Grade(avg_score=9.9, count=178)]


def test_build_interactions_vote_missing_ts_is_none():
    raws = [RawCommandDm("#VOTE#", "v", None, _VOTE_EXTRA, "1")]
    assert build_interactions(raws).votes[0].ts is None


class _FakeOpener:
    """Maps URL -> bytes payload (mirrors tests/test_player_api.py::_FakeOpener but bytes-valued
    for the protobuf endpoint)."""

    def __init__(self, responses: dict[str, bytes]):
        self.responses = responses
        self.requested_urls: list[str] = []

    def open(self, url: str, timeout: int = 60):
        self.requested_urls.append(url)
        import io
        return io.BytesIO(self.responses[url])


def _view() -> ViewData:
    return ViewData(aid=999, cid=888, duration=600)


def _cmd_url(cid: int, aid: int) -> str:
    return f"https://api.bilibili.com/x/v2/dm/web/view?type=1&oid={cid}&pid={aid}"


def _reply_bytes(*command_dms: bytes) -> bytes:
    # reuse the inline encoder from the proto test module
    from tests.test_interactions_proto import _ld
    return b"".join(_ld(9, dm) for dm in command_dms)


def test_fetch_interactions_end_to_end():
    from tests.test_interactions_proto import _command_dm
    canonical = Canonical(platform="bilibili.com", id="BV1x", part=1, url="u")
    body = _reply_bytes(
        _command_dm(command="#VOTE#", content="投票弹幕", progress_ms=371300,
                    extra=_VOTE_EXTRA, id_str="1"),
    )
    opener = _FakeOpener({_cmd_url(888, 999): body})
    result = fetch_interactions(canonical, Settings(), opener=opener, view=_view())
    assert [v.question for v in result.votes] == ["喜欢哪个版本？"]
    assert opener.requested_urls == [_cmd_url(888, 999)]


def test_fetch_interactions_no_cid_returns_empty():
    canonical = Canonical(platform="bilibili.com", id="BV1x", part=9, url="u")
    empty_view = ViewData(aid=None, cid=None, duration=600)
    result = fetch_interactions(canonical, Settings(), opener=_FakeOpener({}), view=empty_view)
    assert result == Interactions()


def test_bilibili_provider_fetch_interactions_passthrough():
    from harvest.providers.bilibili import BilibiliProvider
    from tests.test_interactions_proto import _command_dm

    canonical = Canonical(platform="bilibili.com", id="BV1x", part=1, url="u")
    body = b"".join(
        [__import__("tests.test_interactions_proto", fromlist=["_ld"])._ld(
            9, _command_dm(command="#GRADE#", content="g", progress_ms=None,
                           extra=_GRADE_EXTRA, id_str="1"))]
    )
    opener = _FakeOpener({_cmd_url(888, 999): body})
    result = BilibiliProvider().fetch_interactions(
        canonical, Settings(), opener=opener, view=_view()
    )
    assert result.grades == [Grade(avg_score=9.9, count=178)]
