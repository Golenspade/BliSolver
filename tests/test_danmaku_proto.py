"""Tests for `harvest.danmaku_proto.decode_seg` -- the dependency-free protobuf decoder for
bilibili's danmaku census endpoint (`x/v2/dm/web/seg.so`, `DmSegMobileReply`).

The fixture (`tests/fixtures/bilibili/seg_sample.bin`) is SYNTHESIZED here via a tiny inline
protobuf ENCODER (mirrors the wire format: tag = (field<<3)|wiretype as a varint, then a varint
or length-delimited payload) rather than captured live off a real video -- a live capture needs
network + cookies and yields an opaque blob we can't precisely control. Synthesizing keeps this
test fully offline/hermetic while letting us assert exact known values, including a HighLike
(attr bit2) elem. The encoder is TEST-ONLY: shipped code (`harvest/danmaku_proto.py`) only ever
decodes, never encodes.
"""

from __future__ import annotations

from pathlib import Path

from harvest.danmaku_proto import RawDanmaku, decode_seg

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "bilibili" / "seg_sample.bin"


# --- minimal protobuf wire-format ENCODER (test-only; mirrors DanmakuElem / DmSegMobileReply) ---


def _encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _encode_tag(field: int, wiretype: int) -> bytes:
    return _encode_varint((field << 3) | wiretype)


def _encode_varint_field(field: int, value: int) -> bytes:
    return _encode_tag(field, 0) + _encode_varint(value)


def _encode_ld_field(field: int, data: bytes) -> bytes:
    return _encode_tag(field, 2) + _encode_varint(len(data)) + data


def _encode_elem(
    *, progress_ms: int, content: str, attr: int | None, mid_hash: str | None = None
) -> bytes:
    """One `DanmakuElem` body: `progress`(2, varint) + `content`(7, length-delimited)
    [+ `midHash`(6, length-delimited) when given] [+ `attr`(13, varint) when not None]."""
    buf = bytearray()
    buf += _encode_varint_field(2, progress_ms)
    if mid_hash is not None:
        buf += _encode_ld_field(6, mid_hash.encode("utf-8"))
    buf += _encode_ld_field(7, content.encode("utf-8"))
    if attr is not None:
        buf += _encode_varint_field(13, attr)
    return bytes(buf)


def _encode_seg(elem_bodies: list[bytes]) -> bytes:
    """`DmSegMobileReply`: field 1 = repeated `DanmakuElem` (length-delimited)."""
    return b"".join(_encode_ld_field(1, body) for body in elem_bodies)


# --- the known elements the fixture encodes, and what `decode_seg` must recover from them ---
# (progress_ms, content, attr) -- attr=None models an elem with the attr field entirely absent.

_KNOWN_ELEMS: list[tuple[int, str, int | None]] = [
    (1000, "hello world", None),        # ordinary: no attr field at all -> high_like False
    (2500, "弹幕测试", 4),   # 弹幕测试: attr=4 (0b100) -> HighLike bit2 set
    (2500, "普通弹幕", 0),   # 普通弹幕: attr present but zero -> not HighLike
    (500000, "迟到的评论", 5),  # 迟到的评论: attr=5 (0b101, HighLike + Protect)
]


def _build_fixture_bytes() -> bytes:
    return _encode_seg([
        _encode_elem(progress_ms=ms, content=text, attr=attr)
        for ms, text, attr in _KNOWN_ELEMS
    ])


# Regenerate the committed fixture from the encoder at collection time, so the on-disk bytes and
# the encoder can never drift out of lockstep (and the file stays a real, readable git artifact
# like the existing `sample_danmaku.xml` fixture, not a hand-maintained blob).
_FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
_FIXTURE_PATH.write_bytes(_build_fixture_bytes())


def test_decode_seg_recovers_known_text_content_ts_and_high_like():
    body = _FIXTURE_PATH.read_bytes()
    records = decode_seg(body)

    assert len(records) == len(_KNOWN_ELEMS)
    assert all(isinstance(r, RawDanmaku) for r in records)

    for record, (ms, text, _attr) in zip(records, _KNOWN_ELEMS):
        assert record.text == text
        assert record.content_ts == ms / 1000.0

    # HighLike = attr bit2. attr=4 and attr=5 both have it set; None/0 do not.
    assert [r.high_like for r in records] == [False, True, False, True]


def test_decode_seg_empty_body_returns_empty_list():
    assert decode_seg(b"") == []


def test_decode_seg_non_protobuf_json_error_body_returns_empty_list():
    """A JSON error body (e.g. `{"code":-352}`) has no field-1 length-delimited elements at the
    byte offsets protobuf expects -- `decode_seg` must degrade to an empty list, not raise, so
    `fetch_danmaku` can treat it as a pagination-terminating signal."""
    assert decode_seg(b'{"code":-352,"message":"risk control"}') == []


def test_decode_seg_recovers_mid_hash_field6():
    # A DanmakuElem carrying midHash(6) -> RawDanmaku.mid_hash holds it verbatim.
    body = _encode_seg([
        _encode_elem(progress_ms=1000, content="hi", attr=None, mid_hash="e6c5b7f0"),
    ])
    records = decode_seg(body)
    assert len(records) == 1
    assert records[0].mid_hash == "e6c5b7f0"


def test_decode_seg_mid_hash_absent_defaults_to_empty_string():
    body = _encode_seg([_encode_elem(progress_ms=1000, content="hi", attr=None)])
    assert decode_seg(body)[0].mid_hash == ""
