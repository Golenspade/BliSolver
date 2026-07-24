"""Dependency-free protobuf decoder for bilibili's command-danmaku view endpoint
(`x/v2/dm/web/view`, `DmWebViewReply`). Stdlib only, matching `danmaku_proto.py`'s ethos.

Command danmaku (互动弹幕) are the uploader's on-screen interactive widgets — a separate class from
the scrolling census. They arrive as `DmWebViewReply.commandDms` (field 9, repeated `CommandDm`).
Each `CommandDm` carries: `command`(4) the kind tag ("#VOTE#"/"#GRADE#"/…), `content`(5) a short
label, `progress`(6) a timeline anchor in ms, `extra`(9) a JSON string with the actual payload, and
`idStr`(10). We keep exactly those; every other field is skipped. This module DECODES only."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RawCommandDm:
    """One `CommandDm`, stripped to the fields we keep. `extra` is the raw JSON string (parsed
    downstream by `interactions.build_interactions`, per `command` kind). `progress_ms` is None when
    the widget carries no timeline anchor (grades typically do not)."""

    command: str
    content: str
    progress_ms: int | None
    extra: str
    id_str: str


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _fields(buf: bytes):
    """Yield `(field_number, wire_type, value)` for a protobuf message body. `value` is an int for
    wire type 0 (varint), raw `bytes` for wire type 2 (length-delimited), and raw `bytes` for wire
    types 1/5 (fixed64/32, unused here but skipped correctly)."""
    pos, n = 0, len(buf)
    while pos < n:
        tag, pos = _varint(buf, pos)
        field, wt = tag >> 3, tag & 7
        if wt == 0:
            val, pos = _varint(buf, pos)
        elif wt == 2:
            ln, pos = _varint(buf, pos)
            val, pos = buf[pos:pos + ln], pos + ln
        elif wt == 1:
            val, pos = buf[pos:pos + 8], pos + 8
        elif wt == 5:
            val, pos = buf[pos:pos + 4], pos + 4
        else:
            raise ValueError(f"unknown wire type {wt} for field {field}")
        yield field, wt, val


def _parse_command_dm(buf: bytes) -> RawCommandDm:
    command = content = extra = id_str = ""
    progress_ms: int | None = None
    for field, wt, val in _fields(buf):
        if field == 4 and wt == 2:
            command = val.decode("utf-8", "replace")
        elif field == 5 and wt == 2:
            content = val.decode("utf-8", "replace")
        elif field == 6 and wt == 0:
            progress_ms = val
        elif field == 9 and wt == 2:
            extra = val.decode("utf-8", "replace")
        elif field == 10 and wt == 2:
            id_str = val.decode("utf-8", "replace")
    return RawCommandDm(command=command, content=content, progress_ms=progress_ms,
                        extra=extra, id_str=id_str)


def decode_view(body: bytes) -> list[RawCommandDm]:
    """Parse a `DmWebViewReply`: field 9 = repeated `CommandDm` (length-delimited). An empty body or
    a non-protobuf JSON error body (e.g. `{"code":-352}`, which `_fields` cannot walk as valid wire
    format) both yield `[]` rather than raising — the caller treats that as "no command danmaku"."""
    out: list[RawCommandDm] = []
    try:
        for field, wt, val in _fields(body):
            if field == 9 and wt == 2:
                out.append(_parse_command_dm(val))
    except (ValueError, IndexError):
        return []
    return out
