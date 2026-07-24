"""Tests for `harvest.interactions_proto.decode_view` — the dependency-free protobuf decoder for
bilibili's command-danmaku view endpoint (`x/v2/dm/web/view`, `DmWebViewReply.commandDms`).

Fixtures are SYNTHESIZED inline via a tiny test-only protobuf encoder (mirrors the wire format:
tag = (field<<3)|wiretype as a varint, then a varint or length-delimited payload). A live capture
needs network + cookies and yields an opaque blob we can't control; synthesizing keeps this fully
offline while asserting exact known values. Shipped code only ever DECODES."""

from __future__ import annotations

from harvest.interactions_proto import RawCommandDm, decode_view


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field: int, wt: int) -> bytes:
    return _varint((field << 3) | wt)


def _ld(field: int, payload: bytes) -> bytes:  # length-delimited (wire type 2)
    return _tag(field, 2) + _varint(len(payload)) + payload


def _vint(field: int, value: int) -> bytes:  # varint (wire type 0)
    return _tag(field, 0) + _varint(value)


def _command_dm(*, command: str, content: str, progress_ms: int | None, extra: str,
                id_str: str) -> bytes:
    body = b""
    body += _vint(1, 12345)  # id (ignored by decoder, present on the wire)
    body += _ld(4, command.encode())
    body += _ld(5, content.encode())
    if progress_ms is not None:
        body += _vint(6, progress_ms)
    body += _ld(9, extra.encode())
    body += _ld(10, id_str.encode())
    return body


def test_decode_view_extracts_command_dms():
    reply = (
        _ld(2, b"some text")  # DmWebViewReply.text (field 2) — must be skipped
        + _ld(9, _command_dm(command="#VOTE#", content="投票弹幕", progress_ms=371300,
                             extra='{"question":"x"}', id_str="111"))
        + _ld(9, _command_dm(command="#GRADE#", content="感谢", progress_ms=None,
                             extra='{"avg_score":9.9}', id_str="222"))
    )
    out = decode_view(reply)
    assert out == [
        RawCommandDm(command="#VOTE#", content="投票弹幕", progress_ms=371300,
                     extra='{"question":"x"}', id_str="111"),
        RawCommandDm(command="#GRADE#", content="感谢", progress_ms=None,
                     extra='{"avg_score":9.9}', id_str="222"),
    ]


def test_decode_view_empty_body_is_empty_list():
    assert decode_view(b"") == []


def test_decode_view_json_error_body_is_empty_list():
    # bilibili returns a JSON error (not protobuf) in some states; must degrade to [] not raise.
    assert decode_view(b'{"code":-352,"message":"error"}') == []


def test_decode_view_no_command_dms_is_empty_list():
    reply = _ld(2, b"text only, no field 9")
    assert decode_view(reply) == []
