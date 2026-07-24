"""Dependency-free protobuf decoder for bilibili's danmaku CENSUS endpoint
(`x/v2/dm/web/seg.so`, `DmSegMobileReply`). Stdlib only, matching harvest's no-heavy-deps ethos.

This is the acquisition-time replacement for the old server-sampled XML endpoint: the census
returns every danmaku in a segment (not a server-side sample), and each `DanmakuElem` carries an
`attr` bitfield we didn't have before -- specifically bit2, HighLike (高赞), a later task's input
signal. Seeded from the proven feasibility spike (`scratch/spike_seg_attr.py`), decoded fields are:
`content`(7), `progress`(2), `attr`(13), and `midHash`(6) for author role resolution. color(5)/
mode(3)/weight(9) are read by the spike but deliberately NOT carried into `RawDanmaku` (descoped,
see task brief).
"""

from __future__ import annotations

from dataclasses import dataclass

# DMAttrBit (bilibili's attr bitfield): 0=Protect, 1=FromLive, 2=HighLike.
_HIGH_LIKE_BIT = 2


@dataclass(frozen=True)
class RawDanmaku:
    """One `DanmakuElem`, stripped to the fields the acquisition contract keeps: `content_ts`
    (seconds into the video the comment is pinned to, from `progress` ms), its `text`,
    `high_like` (attr bit2), `mid_hash` (from field 6, crc32 hex of the poster's mid), and
    `author` (once resolved vs the video's author mids; else None). `mode`/color/weight and the
    rest of the elem are deliberately dropped (descoped, see task brief)."""

    content_ts: float
    text: str
    high_like: bool = False
    mid_hash: str = ""  # bilibili midHash (field 6): crc32 hex of poster's mid; "" if absent
    author: str | None = None  # "owner"/"staff" once resolved vs the video's author mids; else None


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
    """Yield `(field_number, wire_type, value)` for a protobuf message body. `value` is an int
    for wire type 0 (varint), raw `bytes` for wire type 2 (length-delimited) or wire types 1/5
    (fixed64/fixed32, unused by the fields we read but must still be skipped correctly)."""
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


def _parse_elem(buf: bytes) -> RawDanmaku | None:
    """Parse one `DanmakuElem` body into a `RawDanmaku`. Elements with no `content`(7) are
    dropped -- there's nothing to mirror without text."""
    content: str | None = None
    progress_ms = 0
    attr = 0
    mid_hash = ""
    for field, wt, val in _fields(buf):
        if field == 2 and wt == 0:
            progress_ms = val
        elif field == 6 and wt == 2:
            mid_hash = val.decode("utf-8", "replace")
        elif field == 7 and wt == 2:
            content = val.decode("utf-8", "replace")
        elif field == 13 and wt == 0:
            attr = val
    if content is None:
        return None
    return RawDanmaku(
        content_ts=progress_ms / 1000.0,
        text=content,
        high_like=bool((attr >> _HIGH_LIKE_BIT) & 1),
        mid_hash=mid_hash,
    )


def decode_seg(body: bytes) -> list[RawDanmaku]:
    """Parse one `seg.so` segment response (`DmSegMobileReply`): field 1 = repeated
    `DanmakuElem` (length-delimited). Elements without text are skipped. An empty body or a
    non-protobuf JSON error body (e.g. `{"code":-352}`, which `_fields` can't walk as valid wire
    format) both yield an empty list rather than raising -- `fetch_danmaku` treats an empty
    result as "no more segments", the pagination-terminating signal."""
    out: list[RawDanmaku] = []
    try:
        for field, wt, val in _fields(body):
            if field == 1 and wt == 2:
                elem = _parse_elem(val)
                if elem is not None:
                    out.append(elem)
    except (ValueError, IndexError):
        return []
    return out
