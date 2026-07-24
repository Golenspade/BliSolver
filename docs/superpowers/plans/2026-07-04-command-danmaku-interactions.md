# Command Danmaku (`--interactions`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `--interactions` track that fetches bilibili command danmaku (互动弹幕) — 投票 votes (question + option tallies) and 评分 grades (0–10 average + rater count) — and surfaces them in `bundle.json`/`bundle.md`.

**Architecture:** A separate acquisition path from the danmaku census: fetch `x/v2/dm/web/view` (plain cookies, no WBI — spike-confirmed), decode `DmWebViewReply.commandDms` with a dependency-free protobuf reader, whitelist `#VOTE#`/`#GRADE#`, parse each kind's JSON `extra` payload into structured pydantic types, and render a `## Interactions` bundle section below the transcript. **No LLM** — the data is already structured, so it decodes straight to schema. Mirrors the existing danmaku stack (`danmaku_proto.py` + `player_api.fetch_danmaku` + `merge` render) but with no representation/clustering stage.

**Tech Stack:** Python 3.11, `pydantic` v2, stdlib `urllib`/`json`, `pytest`. No new dependencies.

## Global Constraints

- **Contract stays `SCHEMA_VERSION = "1.0"`** — all additions are additive optional fields (new `Bundle.interactions`, defaults `None`). Do NOT bump the version.
- **bilibili.com only.** `--interactions` on any other platform prints a warning and leaves `bundle.interactions` `null` (same shape as not passing the flag). Independent of `--danmaku`.
- **No WBI signing, ever** (SPEC §3). Acquisition reuses `player_api._opener(settings)` (the same browser cookies + Referer the census/player-API use).
- **No new runtime dependencies** — stdlib only for decode/fetch, matching `danmaku_proto.py`.
- **Verbatim uploader content.** A vote's `question` and each option's `text` are copied exactly from the API — never paraphrased, translated, or decoded.
- **Vocabulary follows bilibili's `#VOTE#`/`#GRADE#`** — types are `Vote`/`VoteOption`/`Grade`; the container fields are `votes`/`grades`. Never "poll"/"survey".
- **Empty-state = populated-but-empty.** When `--interactions` runs on bilibili and finds nothing, return `Interactions(votes=[], grades=[])`, not `None`. `None` means "not requested" or "unsupported platform" and is set at the CLI layer.
- Source-of-truth docs already written: `CONTEXT.md` (Vote/Grade/Rating danmaku), `SPEC.md §8`, `PROTOCOL.md` (Interactions shapes). The spike verdict is in `scratch/_command_dm_spike_notes.md`.

---

## File Structure

- **Create `harvest/interactions_proto.py`** — dependency-free protobuf decode of `DmWebViewReply.commandDms` → `list[RawCommandDm]`. Decode only, never encode (mirrors `danmaku_proto.py`).
- **Create `harvest/interactions.py`** — `build_interactions(raws)` (pure: whitelist + parse each `extra` JSON → schema) and `fetch_interactions(canonical, settings, ...)` (HTTP GET the view endpoint, decode, build). No LLM.
- **Modify `harvest/schema.py`** — add `VoteOption`, `Vote`, `Grade`, `Interactions`; add `Bundle.interactions: Interactions | None = None`.
- **Modify `harvest/merge.py`** — `build_bundle(...)` gains an `interactions` kwarg; `render_markdown` renders a `## Interactions` section.
- **Modify `harvest/providers/bilibili.py`** — thin `fetch_interactions` passthrough method.
- **Modify `harvest/cli.py`** — `--interactions` argparse flag + per-part wiring.

---

### Task 1: Schema types

**Files:**
- Modify: `harvest/schema.py` (add types after the `Danmaku` block, ~line 143; add field to `Bundle`, ~line 163)
- Test: `tests/test_interactions.py` (create)

**Interfaces:**
- Consumes: nothing (pydantic `BaseModel`).
- Produces:
  - `VoteOption(text: str, count: int = 0, write_in: bool = False)`
  - `Vote(question: str, options: list[VoteOption] = [], total_count: int = 0, ts: float | None = None)`
  - `Grade(avg_score: float, count: int = 0)` — `avg_score` on a 0–10 scale
  - `Interactions(votes: list[Vote] = [], grades: list[Grade] = [])`
  - `Bundle.interactions: Interactions | None = None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_interactions.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_interactions.py -v`
Expected: FAIL with `ImportError: cannot import name 'Interactions' from 'harvest.schema'`

- [ ] **Step 3: Write minimal implementation**

In `harvest/schema.py`, add after the `Danmaku` class (before `class Bundle`):

```python
class VoteOption(BaseModel):
    """One selectable option of a Vote (投票). `text` is the VERBATIM option label from the
    uploader; `count` is the crowd tally for it. `write_in` marks the free-text "其他/other"
    option (bilibili `has_self_def`) — its `text` is a prompt, not a real answer."""

    text: str
    count: int = 0
    write_in: bool = False


class Vote(BaseModel):
    """An on-screen vote (投票 / bilibili `#VOTE#`): an uploader-authored `question` plus discrete
    `options`, each with a running crowd tally. `total_count` is bilibili's own reported total (kept
    explicit, not summed). `ts` is the content-time (seconds) the widget is pinned to (null if the
    widget carries no timeline anchor). The question and option texts are VERBATIM uploader content;
    the question is VERIFIED framing (structural widget data, not a crc32 guess), but it is a
    question, never a claim the video asserts. Authority: BELOW transcript."""

    question: str
    options: list[VoteOption] = Field(default_factory=list)
    total_count: int = 0
    ts: float | None = None


class Grade(BaseModel):
    """A star grading (评分 / bilibili `#GRADE#`): a 1–5 star bar the server pre-aggregates. Its
    datum is `avg_score` on a **0–10 scale** (the 1–5 stars ×2) plus the rater `count`. It has NO
    framing question and NO per-option breakdown — just the mean and n. Not timeline-pinned.
    Authority: a crowd reception aggregate, strictly BELOW transcript (never a fact about content).
    NB: viewers' raw star clicks also post literal digit danmaku ("5"/"1") into the census, so with
    --danmaku on they ALSO appear in the danmaku mirror — the same act, surfaced twice by design."""

    avg_score: float  # 0–10 (1–5 stars ×2); a server-computed mean, NOT raw votes
    count: int = 0


class Interactions(BaseModel):
    """Command danmaku (互动弹幕) — the uploader's on-screen interactive widgets — a SEPARATE class
    from `danmaku`, on a separate acquisition path (`x/v2/dm/web/view`, no LLM). bilibili-only;
    present only when `--interactions` ran on a supporting platform (else `Bundle.interactions` is
    null). Populated-but-empty (`votes: []`, `grades: []`) means "requested, found nothing" —
    distinct from null ("not requested")."""

    votes: list[Vote] = Field(default_factory=list)
    grades: list[Grade] = Field(default_factory=list)
```

Then add to `class Bundle` (after the `danmaku: Danmaku | None = None` line):

```python
    interactions: Interactions | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_interactions.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add harvest/schema.py tests/test_interactions.py
git commit -m "feat(schema): add Vote/Grade/Interactions types, additive to 1.0"
```

---

### Task 2: Protobuf decode of `commandDms`

**Files:**
- Create: `harvest/interactions_proto.py`
- Test: `tests/test_interactions_proto.py` (create)

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `RawCommandDm(command: str, content: str, progress_ms: int | None, extra: str, id_str: str)` — frozen dataclass.
  - `decode_view(body: bytes) -> list[RawCommandDm]` — parses a `DmWebViewReply`; field 9 = repeated `CommandDm`. An empty body or a non-protobuf JSON error body (e.g. `{"code":-352}`) returns `[]` rather than raising.

- [ ] **Step 1: Write the failing test**

Create `tests/test_interactions_proto.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_interactions_proto.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harvest.interactions_proto'`

- [ ] **Step 3: Write minimal implementation**

Create `harvest/interactions_proto.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_interactions_proto.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add harvest/interactions_proto.py tests/test_interactions_proto.py
git commit -m "feat(interactions): decode DmWebViewReply.commandDms (dependency-free)"
```

---

### Task 3: Build + fetch interactions

**Files:**
- Create: `harvest/interactions.py`
- Test: `tests/test_interactions.py` (extend — created in Task 1)

**Interfaces:**
- Consumes: `RawCommandDm`, `decode_view` (Task 2); `Interactions`, `Vote`, `VoteOption`, `Grade` (Task 1); `player_api._opener`, `player_api.fetch_view`, `player_api.cid_for_part`, `player_api.ViewData`, `player_api.ViewError`; `resolve.Canonical`; `config.Settings`.
- Produces:
  - `build_interactions(raws: list[RawCommandDm]) -> Interactions` — pure; whitelist `#VOTE#`→`Vote`, `#GRADE#`→`Grade`, drop everything else; malformed `extra` JSON for one record is skipped, not fatal.
  - `fetch_interactions(canonical: Canonical, settings: Settings, *, opener=None, view: ViewData | None = None) -> Interactions` — always returns an `Interactions` (empty on any failure), never raises.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_interactions.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_interactions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harvest.interactions'`

- [ ] **Step 3: Write minimal implementation**

Create `harvest/interactions.py`:

```python
"""Command-danmaku (互动弹幕) acquisition: fetch `x/v2/dm/web/view`, decode `commandDms`, and build
the structured `Interactions` schema. Separate acquisition from the danmaku census (`seg.so`); plain
cookies, no WBI (SPEC §3) — the same `player_api._opener` the census and player-API use.

NO LLM: command danmaku are already structured, so this decodes straight to schema (no mirror, no
clustering). Only two `command` kinds carry a crowd signal and are whitelisted: `#VOTE#` (投票) and
`#GRADE#` (评分). Others (`#ATTENTION#` follow prompts, `#LINK#` cards) are dropped.

`build_interactions` is pure (raws -> schema); `fetch_interactions` owns the one HTTP GET and mirrors
`player_api.fetch_danmaku`'s "absence degrades to an empty result, never raises" convention."""

from __future__ import annotations

import json
import logging
from urllib.error import URLError

from .config import Settings
from .interactions_proto import RawCommandDm, decode_view
from .player_api import ViewData, ViewError, _opener, cid_for_part, fetch_view
from .resolve import Canonical
from .schema import Grade, Interactions, Vote, VoteOption

logger = logging.getLogger(__name__)

_API_VIEW_CMD = "https://api.bilibili.com/x/v2/dm/web/view?type=1&oid={cid}&pid={aid}"

_VOTE = "#VOTE#"
_GRADE = "#GRADE#"


def _parse_vote(extra: dict, progress_ms: int | None) -> Vote:
    options = [
        VoteOption(
            text=str(o.get("desc") or ""),
            count=int(o.get("cnt") or 0),
            write_in=bool(o.get("has_self_def", False)),
        )
        for o in (extra.get("options") or [])
    ]
    return Vote(
        question=str(extra.get("question") or ""),
        options=options,
        total_count=int(extra.get("cnt") or 0),
        ts=(progress_ms / 1000.0) if progress_ms is not None else None,
    )


def _parse_grade(extra: dict) -> Grade:
    return Grade(avg_score=float(extra.get("avg_score") or 0.0), count=int(extra.get("count") or 0))


def build_interactions(raws: list[RawCommandDm]) -> Interactions:
    """Whitelist `#VOTE#`/`#GRADE#` and parse each record's JSON `extra` into schema. A record whose
    `extra` is not valid JSON (or is the wrong shape) is skipped — one bad widget never aborts the
    rest. Pure and deterministic; no network."""
    votes: list[Vote] = []
    grades: list[Grade] = []
    for r in raws:
        if r.command not in (_VOTE, _GRADE):
            continue
        try:
            extra = json.loads(r.extra)
        except json.JSONDecodeError:
            continue
        if not isinstance(extra, dict):
            continue
        try:
            if r.command == _VOTE:
                votes.append(_parse_vote(extra, r.progress_ms))
            else:
                grades.append(_parse_grade(extra))
        except (TypeError, ValueError):
            continue
    return Interactions(votes=votes, grades=grades)


def fetch_interactions(
    canonical: Canonical, settings: Settings, *, opener=None, view: ViewData | None = None
) -> Interactions:
    """Fetch + decode the command danmaku for this part via `x/v2/dm/web/view`.

    Always returns an `Interactions`, never raises or returns `None`: when no cid/aid resolves for
    the part (or the view is unavailable/`ViewError`s, or the HTTP GET fails), returns an empty
    `Interactions()`. This mirrors `player_api.fetch_danmaku`'s graceful-absence stance. `opener`
    and `view` are injectable for tests / to share an already-fetched view."""
    op = opener or _opener(settings)

    if view is None:
        try:
            view = fetch_view(canonical, settings, opener=op)
        except ViewError:
            return Interactions()

    aid = view.aid
    cid = cid_for_part(view, canonical.part)
    if not (aid and cid):
        return Interactions()

    url = _API_VIEW_CMD.format(cid=cid, aid=aid)
    try:
        body = op.open(url, timeout=60).read()
    except URLError as exc:
        logger.warning("interactions view fetch failed for cid=%s: %s", cid, exc)
        return Interactions()
    return build_interactions(decode_view(body))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_interactions.py -v`
Expected: PASS (all Task 1 + Task 3 tests)

- [ ] **Step 5: Commit**

```bash
git add harvest/interactions.py tests/test_interactions.py
git commit -m "feat(interactions): fetch + build votes/grades from x/v2/dm/web/view"
```

---

### Task 4: Render + bundle plumbing

**Files:**
- Modify: `harvest/merge.py` (`build_bundle` ~line 114-140; `render_markdown` — add section after the danmaku block ~line 237; import `Interactions` ~line 20)
- Test: `tests/test_merge.py` (extend)

**Interfaces:**
- Consumes: `Interactions`, `Vote`, `VoteOption`, `Grade` (Task 1); existing `_neutralize`, `_mmss`.
- Produces: `build_bundle(..., interactions: Interactions | None = None)` sets `Bundle.interactions`; `render_markdown` emits a `## Interactions` section when `bundle.interactions` has any votes or grades.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_merge.py` (reuse its existing `_bundle`/`_settings` helpers; check their signatures at the top of the file and pass `interactions=` through if `_bundle` forwards kwargs to `build_bundle`, else construct a `Bundle` with `interactions=` directly):

```python
from harvest.schema import Interactions, Vote, VoteOption, Grade


def test_render_interactions_section():
    from harvest.merge import render_markdown
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
    bundle = _bundle()
    bundle.interactions = interactions
    md = render_markdown(bundle, _settings())
    assert "## Interactions" in md
    assert "9.9" in md and "178" in md          # grade summary
    assert "喜欢哪个版本？" in md                  # vote question, verbatim
    assert "[06:11]" in md                       # ts 371.3 -> mm:ss
    assert "只加黄葱" in md and "153" in md        # option + tally
    assert "443" in md                           # total


def test_render_no_interactions_section_when_none():
    from harvest.merge import render_markdown
    bundle = _bundle()
    bundle.interactions = None
    assert "## Interactions" not in render_markdown(bundle, _settings())


def test_render_no_interactions_section_when_empty():
    from harvest.merge import render_markdown
    bundle = _bundle()
    bundle.interactions = Interactions()  # requested, found nothing
    assert "## Interactions" not in render_markdown(bundle, _settings())


def test_build_bundle_threads_interactions():
    # extend the existing build_bundle test path: build_bundle accepts interactions= and sets it
    from harvest.merge import build_bundle
    interactions = Interactions(grades=[Grade(avg_score=8.0, count=5)])
    bundle = _build_bundle_under_test(interactions=interactions)  # see note below
    assert bundle.interactions == interactions
```

> Note on `_build_bundle_under_test`: `tests/test_merge.py` already has a `build_bundle` call in `test_build_bundle_consumes_source_metadata` (~line 191). Model the new test on that one — construct the same `Canonical`/`SourceMetadata`/`Transcript`/`Settings` it uses and pass `interactions=interactions` as a kwarg. Do not invent a helper that doesn't exist; inline the construction exactly as that test does.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_merge.py -k interactions -v`
Expected: FAIL — `build_bundle() got an unexpected keyword argument 'interactions'` / missing `## Interactions`.

- [ ] **Step 3: Write minimal implementation**

In `harvest/merge.py`:

1. Extend the schema import (line ~20):

```python
from .schema import Bundle, Danmaku, Frame, Interactions, Meta, Segment, Stats, Transcript
```

2. Add the `interactions` kwarg to `build_bundle` (signature + the `Bundle(...)` call):

```python
def build_bundle(
    canonical: Canonical,
    meta: SourceMetadata,
    transcript: Transcript,
    frames: list[Frame],
    settings: Settings,
    *,
    vision_model: str | None = None,
    danmaku: Danmaku | None = None,
    interactions: Interactions | None = None,
) -> Bundle:
```

and in the returned `Bundle(...)`, after `danmaku=danmaku,`:

```python
        interactions=interactions,
```

3. Add the render block in `render_markdown`, immediately AFTER the danmaku section's closing (after the `for w in dm.windows:` loop block, before `return "\n".join(lines)...`):

```python
    it = bundle.interactions
    if it and (it.votes or it.grades):
        lines.append("## Interactions")
        lines.append(
            "_uploader-initiated widgets (below transcript authority). "
            "grades = crowd 0–10 average; votes = uploader question + crowd tallies._"
        )
        lines.append("")
        # Grades first (video-level reception summary; no timeline anchor).
        for g in it.grades:
            lines.append(f"### 评分 (grade)")
            lines.append(f"- avg {g.avg_score:g} / 10 over {g.count} raters")
            lines.append("")
        # Votes next, ordered by timeline anchor (ts); unanchored votes (ts is None) sort last.
        for v in sorted(it.votes, key=lambda v: (v.ts is None, v.ts or 0.0)):
            head = "### "
            if v.ts is not None:
                head += f"[{_mmss(v.ts)}] "
            head += f"投票 (vote): {_neutralize(v.question)}"
            lines.append(head)
            for opt in v.options:
                marker = " (write-in)" if opt.write_in else ""
                lines.append(f"- {_neutralize(opt.text)}{marker} — {opt.count}")
            lines.append(f"_{v.total_count} votes total_")
            lines.append("")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_merge.py -k interactions -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all prior tests + the new ones)

- [ ] **Step 6: Commit**

```bash
git add harvest/merge.py tests/test_merge.py
git commit -m "feat(interactions): thread through build_bundle + render ## Interactions"
```

---

### Task 5: CLI flag + provider wiring

**Files:**
- Modify: `harvest/providers/bilibili.py` (add `fetch_interactions` method + import ~line 17, 72)
- Modify: `harvest/cli.py` (argparse flag ~line 65; wiring ~line 165-186; import ~line 16)
- Test: `tests/test_interactions.py` (extend — provider passthrough); manual CLI smoke.

**Interfaces:**
- Consumes: `interactions.fetch_interactions` (Task 3); `build_bundle(..., interactions=)` (Task 4).
- Produces: `BilibiliProvider.fetch_interactions(canonical, settings, *, opener=None, view=None) -> Interactions`; `harvest ingest --interactions` populates `bundle.interactions` on bilibili, leaves it `None` elsewhere.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_interactions.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_interactions.py::test_bilibili_provider_fetch_interactions_passthrough -v`
Expected: FAIL with `AttributeError: 'BilibiliProvider' object has no attribute 'fetch_interactions'`

- [ ] **Step 3: Write minimal implementation**

In `harvest/providers/bilibili.py`:

1. Extend the `player_api` import group (near line 17) to add nothing (fetch_interactions lives in `harvest.interactions`); instead add a new import:

```python
from ..interactions import fetch_interactions
```

2. Add the method after `fetch_danmaku` (~line 76):

```python
    def fetch_interactions(self, canonical, settings, *, opener=None, view=None):
        """Command-danmaku (投票/评分) acquisition. Thin passthrough to
        `interactions.fetch_interactions`; see there for the empty-result-never-raises convention."""
        return fetch_interactions(canonical, settings, opener=opener, view=view)
```

In `harvest/cli.py`:

3. Add the import (near line 16, beside `from .danmaku import represent_danmaku`):

```python
# (no import needed — provider.fetch_interactions is called via the provider seam)
```

4. Add the argparse flag (after the `--danmaku` flag, ~line 68):

```python
    ingest.add_argument(
        "--interactions", action="store_true",
        help="opt-in: fetch bilibili command-danmaku aggregates — 投票 votes + 评分 grades "
             "(bilibili only; independent of --danmaku)",
    )
```

5. Add the wiring after the `danmaku` block (after line ~181, before `bundle = build_bundle(`):

```python
    interactions = None
    if args.interactions:
        if not hasattr(provider, "fetch_interactions"):
            print(f"[{canonical.id} p{canonical.part}] --interactions ignored: "
                  f"not supported on {canonical.platform}")
        else:
            interactions = provider.fetch_interactions(canonical, settings)
```

6. Pass it into `build_bundle`:

```python
    bundle = build_bundle(
        canonical, meta, transcript, frames, settings,
        vision_model=vision_model, danmaku=danmaku, interactions=interactions,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_interactions.py -v`
Expected: PASS (including the provider passthrough test)

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (whole suite green)

- [ ] **Step 6: Live smoke test (manual, needs cookies)**

Run against the spike's known grade+vote video:

```bash
.venv/Scripts/python.exe -m harvest ingest "https://www.bilibili.com/video/BV1RnAuz6E29" --interactions --no-vision --force-whisper --out ./out 2>&1 | tail -20
```

Expected: `out/BV1RnAuz6E29-p1/bundle.json` has a non-null `interactions` with ≥1 grade (`avg_score` ~9.9) and ≥1 vote; `bundle.md` shows a `## Interactions` section. (If cookies/model unavailable, skip — the hermetic suite already covers the logic.)

- [ ] **Step 7: Commit**

```bash
git add harvest/cli.py harvest/providers/bilibili.py tests/test_interactions.py
git commit -m "feat(cli): wire --interactions opt-in (bilibili command danmaku)"
```

---

## Self-Review Notes

- **Spec coverage:** scope/whitelist (Task 3 `build_interactions`), acquisition no-WBI (Task 3 `fetch_interactions`), no-LLM (Task 3, no model dep), schema additive-1.0 (Task 1), authority/render below transcript (Task 4), empty-state populated-but-empty vs null (Tasks 3 + 5), separate `--interactions` flag (Task 5), both-flags-on documented-not-coded (docs only, correct — no code change). All PROTOCOL `Interactions` fields (`votes[].question/options[].text/count/write_in/total_count/ts`, `grades[].avg_score/count`) are produced in Task 3 and rendered in Task 4.
- **Vocabulary:** types `Vote`/`VoteOption`/`Grade`, container fields `votes`/`grades` — consistent across Tasks 1/3/4/5. No "poll".
- **Type consistency:** `RawCommandDm` fields (`command`, `content`, `progress_ms`, `extra`, `id_str`) identical in Tasks 2 & 3. `fetch_interactions` signature identical in Tasks 3 & 5. `build_bundle(..., interactions=)` identical in Tasks 4 & 5.
- **Deferred/out of scope:** no suppression of Rating danmaku from the census mirror (Q6 — leave both, documented in PROTOCOL). No README change (README does not list flags). No `stats`-based opt-in signal for interactions (none exists; unlike `danmaku_count`).
