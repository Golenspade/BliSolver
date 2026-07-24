# Author Danmaku (UP主 / 合作) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flag each danmaku posted by a video author — the primary uploader (UP主) or a 合作 collaborator — as an elevated, higher-authority line, and realign `bundle.md` danmaku rendering to a single chronological pass with elevation pills (fixing a `high_like` ordering drift in the same stroke).

**Architecture:** The census stream harvest already pages under `--danmaku` carries a per-elem `midHash` (protobuf field 6, a crc32 of the poster's mid); the `view` response already carries the video's author mids (`owner.mid` + `staff[].mid`). We read `midHash` in the decoder, resolve it to an `author` role by crc32-matching against the author mids at fetch time (zero new network calls), carry `author` through the representation stage as an *elevated* signal alongside `high_like` (extracted verbatim before LLM clustering), and render every elevated line in place — never dropped, never reordered out of content-time.

**Tech Stack:** Python 3, pydantic (schema), stdlib `zlib` (crc32) + `dataclasses.replace`, dependency-free protobuf decoder (existing), pytest.

## Global Constraints

- **Stdlib only for the new logic** — `zlib` (crc32) is stdlib; no new third-party dependency. Matches harvest's no-heavy-deps ethos.
- **Zero new network calls** — `midHash` rides the census `seg.so` already fetched; author mids ride the `web-interface/view` response already fetched. No WBI-signed endpoints.
- **Additive schema only** — `DanmakuLine.author` is a new optional field defaulting `None`; `SCHEMA_VERSION` stays `"1.0"`. `bundle.json`'s existing shape is unchanged for existing consumers.
- **The LLM fence is unchanged** — the clustering call still receives only `[{text, count}]` and never sees `author` (or `high_like`, or `midHash`). Author lines are extracted *before* clustering, entirely mechanically.
- **Chronological order is load-bearing** — within a window, `bundle.json` `lines` and `bundle.md` rendering are both ordered by first-occurrence content time; never reorder by count or by elevation.
- **`author` values are exactly `"owner"` | `"staff"` | `None`** — `"owner"` = primary uploader (renders `UP主`), `"staff"` = 合作 co-author (renders `合作`), `None` = organic crowd.
- **Owner precedence** — a mid that is both `owner.mid` and present in `staff[]` classifies as `"owner"`, never `"staff"`.

---

### Task 1: Decoder reads `midHash` (protobuf field 6) into `RawDanmaku.mid_hash`

**Files:**
- Modify: `harvest/danmaku_proto.py` (add `mid_hash` field to `RawDanmaku`; read field 6 in `_parse_elem`)
- Test: `tests/test_danmaku_proto.py`

**Interfaces:**
- Produces: `RawDanmaku.mid_hash: str` — the poster's crc32 hash as bilibili's verbatim hex string, `""` when the elem carries no field 6.

- [ ] **Step 1: Extend the test-only encoder to optionally emit field 6, and add failing tests**

In `tests/test_danmaku_proto.py`, change `_encode_elem` to accept an optional `mid_hash` and emit it as field 6 (length-delimited), then add two tests. Replace the existing `_encode_elem` definition (lines 49-57) with:

```python
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
```

Add these tests at the end of the file:

```python
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
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_danmaku_proto.py::test_decode_seg_recovers_mid_hash_field6 tests/test_danmaku_proto.py::test_decode_seg_mid_hash_absent_defaults_to_empty_string -v`
Expected: FAIL — `RawDanmaku` has no `mid_hash` attribute (`TypeError`/`AttributeError`).

- [ ] **Step 3: Add the field and read it in the decoder**

In `harvest/danmaku_proto.py`, add `mid_hash` to the `RawDanmaku` dataclass (after `high_like`):

```python
@dataclass(frozen=True)
class RawDanmaku:
    content_ts: float
    text: str
    high_like: bool = False
    mid_hash: str = ""  # bilibili midHash (field 6): crc32 hex of the poster's mid; "" if absent
```

In `_parse_elem`, initialize `mid_hash` and read field 6 (add the `elif` alongside the existing field reads):

```python
def _parse_elem(buf: bytes) -> RawDanmaku | None:
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
```

- [ ] **Step 4: Run the full proto test file to verify pass + no regression**

Run: `python -m pytest tests/test_danmaku_proto.py -v`
Expected: PASS — the two new tests pass and the existing `test_decode_seg_recovers_known_text_content_ts_and_high_like` still passes (it never asserts `mid_hash`).

- [ ] **Step 5: Commit**

```bash
git add harvest/danmaku_proto.py tests/test_danmaku_proto.py
git commit -m "feat(danmaku): decode midHash (field 6) into RawDanmaku.mid_hash"
```

---

### Task 2: `ViewData` parses collaborator mids from `data.staff[]`

**Files:**
- Modify: `harvest/player_api.py` (`ViewData` gains `staff_mids`; `fetch_view` parses `data.staff`)
- Test: `tests/test_player_api.py`

**Interfaces:**
- Produces: `ViewData.staff_mids: list[int]` — every `mid` in the view's `staff[]` list (co-authors of a 合作 video), `[]` when the video has no staff.

- [ ] **Step 1: Write failing tests**

In `tests/test_player_api.py`, add:

```python
def test_fetch_view_parses_staff_mids():
    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 1, "cid": 1, "owner": {"mid": 7, "name": "U"},
            "staff": [
                {"mid": 7, "title": "UP主"},
                {"mid": 99, "title": "配音"},
                {"mid": 100, "title": "后期"},
            ],
            "pages": [],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})
    view = fetch_view(canonical, Settings(), opener=opener)
    assert view.staff_mids == [7, 99, 100]


def test_fetch_view_no_staff_key_is_empty_list():
    canonical = _canonical()
    payload = {"code": 0, "data": {"aid": 1, "cid": 1, "owner": {"mid": 7}, "pages": []}}
    opener = _FakeOpener({_view_url(canonical): payload})
    view = fetch_view(canonical, Settings(), opener=opener)
    assert view.staff_mids == []
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_player_api.py::test_fetch_view_parses_staff_mids tests/test_player_api.py::test_fetch_view_no_staff_key_is_empty_list -v`
Expected: FAIL — `ViewData` has no `staff_mids` attribute.

- [ ] **Step 3: Add the field and parse it**

In `harvest/player_api.py`, add to `ViewData` (after `owner_name`):

```python
    owner_mid: int | None = None
    owner_name: str | None = None
    staff_mids: list[int] = []  # 合作 co-author mids from data.staff[]; [] when solo
```

In `fetch_view`, after `owner = data.get("owner") or {}` (near line 150), parse staff:

```python
    owner = data.get("owner") or {}
    staff = data.get("staff") or []
    staff_mids = [s["mid"] for s in staff if isinstance(s, dict) and s.get("mid") is not None]
```

Pass it into the `ViewData(...)` constructor (add alongside `owner_mid=owner.get("mid")`):

```python
            owner_mid=owner.get("mid"),
            owner_name=owner.get("name"),
            staff_mids=staff_mids,
```

- [ ] **Step 4: Run to verify pass + no regression**

Run: `python -m pytest tests/test_player_api.py -v`
Expected: PASS — new tests pass; existing view tests (which have no `staff` key) still pass with `staff_mids == []`.

- [ ] **Step 5: Commit**

```bash
git add harvest/player_api.py tests/test_player_api.py
git commit -m "feat(danmaku): parse 合作 staff mids into ViewData.staff_mids"
```

---

### Task 3: `classify_authors` resolves `mid_hash` → `author`, wired into `fetch_danmaku`

**Files:**
- Modify: `harvest/danmaku_proto.py` (`RawDanmaku` gains `author`)
- Modify: `harvest/player_api.py` (`classify_authors` helper; `fetch_danmaku` applies it)
- Test: `tests/test_player_api.py`

**Interfaces:**
- Consumes: `RawDanmaku.mid_hash` (Task 1), `ViewData.owner_mid` + `ViewData.staff_mids` (Task 2).
- Produces:
  - `RawDanmaku.author: str | None` — `"owner"`, `"staff"`, or `None`.
  - `classify_authors(records: list[RawDanmaku], owner_mid: int | None, staff_mids: list[int]) -> list[RawDanmaku]` — returns records with `author` tagged (a new list; the input's frozen records are copied via `dataclasses.replace`). Returns the input list object unchanged when there are no author mids.

- [ ] **Step 1: Add `author` to `RawDanmaku`**

In `harvest/danmaku_proto.py`, extend the dataclass:

```python
@dataclass(frozen=True)
class RawDanmaku:
    content_ts: float
    text: str
    high_like: bool = False
    mid_hash: str = ""  # bilibili midHash (field 6): crc32 hex of the poster's mid; "" if absent
    author: str | None = None  # "owner"/"staff" once resolved vs the video's author mids; else None
```

- [ ] **Step 2: Write failing tests for `classify_authors`**

In `tests/test_player_api.py`, add near the danmaku section. Note the test computes the crc32 hex itself — the standard-CRC32 algorithm is the contract we assert against:

```python
import zlib
from dataclasses import replace as _replace  # noqa: F401  (kept explicit for clarity)


def _mid_hash(mid: int) -> str:
    return format(zlib.crc32(str(mid).encode("utf-8")) & 0xFFFFFFFF, "x")


def test_classify_authors_tags_owner_staff_and_leaves_crowd_and_hashless():
    from harvest.player_api import RawDanmaku, classify_authors
    records = [
        RawDanmaku(content_ts=1.0, text="owner note", mid_hash=_mid_hash(7)),
        RawDanmaku(content_ts=2.0, text="staff note", mid_hash=_mid_hash(99)),
        RawDanmaku(content_ts=3.0, text="crowd", mid_hash=_mid_hash(555)),
        RawDanmaku(content_ts=4.0, text="no hash", mid_hash=""),
    ]
    out = classify_authors(records, owner_mid=7, staff_mids=[7, 99])
    assert [r.author for r in out] == ["owner", "staff", None, None]
    # text/ts untouched
    assert [r.text for r in out] == ["owner note", "staff note", "crowd", "no hash"]


def test_classify_authors_owner_precedence_when_owner_also_in_staff():
    from harvest.player_api import RawDanmaku, classify_authors
    records = [RawDanmaku(content_ts=1.0, text="x", mid_hash=_mid_hash(7))]
    out = classify_authors(records, owner_mid=7, staff_mids=[7])
    assert out[0].author == "owner"


def test_classify_authors_no_author_mids_returns_input_unchanged():
    from harvest.player_api import RawDanmaku, classify_authors
    records = [RawDanmaku(content_ts=1.0, text="x", mid_hash=_mid_hash(7))]
    out = classify_authors(records, owner_mid=None, staff_mids=[])
    assert out is records  # no-op fast path, same list object


def test_classify_authors_tolerates_unparseable_mid_hash():
    from harvest.player_api import RawDanmaku, classify_authors
    records = [RawDanmaku(content_ts=1.0, text="x", mid_hash="not-hex")]
    out = classify_authors(records, owner_mid=7, staff_mids=[])
    assert out[0].author is None
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_player_api.py -k classify_authors -v`
Expected: FAIL — `classify_authors` does not exist (ImportError).

- [ ] **Step 4: Implement `classify_authors`**

In `harvest/player_api.py`, add `import zlib` at the top with the other stdlib imports, and add `replace` to the dataclasses import:

```python
from dataclasses import dataclass, replace
```

Add the helper just above `fetch_danmaku` (after the `DanmakuFetch` dataclass):

```python
def _mid_crc32(mid: int) -> int:
    """bilibili's danmaku poster hash is the standard CRC32 of the mid's decimal string."""
    return zlib.crc32(str(mid).encode("utf-8")) & 0xFFFFFFFF


def classify_authors(
    records: list[RawDanmaku], owner_mid: int | None, staff_mids: list[int]
) -> list[RawDanmaku]:
    """Tag each record's `author` by crc32-matching its `mid_hash` against the video's author mids:
    `owner_mid` -> "owner" (UP主), any `staff_mids` entry -> "staff" (合作). Owner wins on overlap.
    Records that match no author, or whose `mid_hash` is empty/unparseable, keep author=None.

    Pure and deterministic, no network. Compares integers (crc32 value vs int(mid_hash, 16)) so it
    is robust to leading-zero padding / case in bilibili's hex rendering of the hash. Returns the
    input list object unchanged when there are no author mids to match against.
    """
    role_by_hash: dict[int, str] = {}
    for mid in staff_mids:
        if mid is not None:
            role_by_hash[_mid_crc32(mid)] = "staff"
    if owner_mid is not None:
        role_by_hash[_mid_crc32(owner_mid)] = "owner"  # owner precedence over any staff overlap
    if not role_by_hash:
        return records
    out: list[RawDanmaku] = []
    for r in records:
        role: str | None = None
        if r.mid_hash:
            try:
                role = role_by_hash.get(int(r.mid_hash, 16))
            except ValueError:
                role = None
        out.append(replace(r, author=role) if role else r)
    return out
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_player_api.py -k classify_authors -v`
Expected: PASS (all four tests).

- [ ] **Step 6: Wire `classify_authors` into `fetch_danmaku` with a failing integration test**

Extend the test-only encoder in `tests/test_player_api.py` to carry a midHash, and add an integration test. Replace `_encode_elem` (lines 468-475) and `_encode_seg` (lines 478-484) with:

```python
def _encode_elem(*, progress_ms: int, content: str, attr: int = 0, mid_hash: str = "") -> bytes:
    buf = bytearray()
    buf += _encode_tag(2, 0) + _encode_varint(progress_ms)
    if mid_hash:
        h = mid_hash.encode("utf-8")
        buf += _encode_tag(6, 2) + _encode_varint(len(h)) + h
    data = content.encode("utf-8")
    buf += _encode_tag(7, 2) + _encode_varint(len(data)) + data
    if attr:
        buf += _encode_tag(13, 0) + _encode_varint(attr)
    return bytes(buf)


def _encode_seg(elems: list[tuple]) -> bytes:
    """Build a fake `DmSegMobileReply` body. Each elem is (progress_ms, content, attr) or
    (progress_ms, content, attr, mid_hash)."""
    out = bytearray()
    for elem in elems:
        ms, text, attr = elem[0], elem[1], elem[2]
        mid_hash = elem[3] if len(elem) > 3 else ""
        body = _encode_elem(progress_ms=ms, content=text, attr=attr, mid_hash=mid_hash)
        out += _encode_tag(1, 2) + _encode_varint(len(body)) + body
    return bytes(out)
```

Add the integration test:

```python
def test_fetch_danmaku_classifies_author_from_view_owner_and_staff():
    from harvest.player_api import fetch_danmaku

    canonical = _canonical(part=1)
    view_payload = {
        "code": 0,
        "data": {
            "aid": 42, "cid": 100, "title": "T", "desc": "d", "duration": 600,
            "owner": {"mid": 7, "name": "U"},
            "staff": [{"mid": 7, "title": "UP主"}, {"mid": 99, "title": "配音"}],
            "stat": {"danmaku": 3},
            "pages": [{"page": 1, "cid": 100, "part": "P1", "duration": 600}],
        },
    }
    seg1 = _encode_seg([
        (1000, "owner speaks", 0, _mid_hash(7)),
        (2000, "staff speaks", 0, _mid_hash(99)),
        (3000, "crowd speaks", 0, _mid_hash(555)),
    ])
    opener = _FakeOpener({
        _view_url(canonical): view_payload,
        _seg_url(100, 1): seg1,
        _seg_url(100, 2): b"",
    })

    result = fetch_danmaku(canonical, Settings(), opener=opener)

    assert [r.text for r in result.records] == ["owner speaks", "staff speaks", "crowd speaks"]
    assert [r.author for r in result.records] == ["owner", "staff", None]
```

- [ ] **Step 7: Run to verify failure**

Run: `python -m pytest tests/test_player_api.py::test_fetch_danmaku_classifies_author_from_view_owner_and_staff -v`
Expected: FAIL — `author` is `None` for every record (`fetch_danmaku` doesn't classify yet).

- [ ] **Step 8: Apply classification in `fetch_danmaku`**

In `harvest/player_api.py`, in `fetch_danmaku`, replace the final two lines:

```python
    records.sort(key=lambda r: r.content_ts)
    return DanmakuFetch(source_total=source_total, fetched_total=len(records), records=records)
```

with:

```python
    records.sort(key=lambda r: r.content_ts)
    if view is not None:
        records = classify_authors(records, view.owner_mid, view.staff_mids)
    return DanmakuFetch(source_total=source_total, fetched_total=len(records), records=records)
```

- [ ] **Step 9: Run to verify pass + no regression**

Run: `python -m pytest tests/test_player_api.py tests/test_danmaku_proto.py -v`
Expected: PASS — new integration test passes; the existing `test_fetch_danmaku_pages_segments_until_empty_and_orders_chronologically` still passes (its elems carry no `mid_hash`, so every `author` is `None`, and it never asserts `author`).

- [ ] **Step 10: Commit**

```bash
git add harvest/danmaku_proto.py harvest/player_api.py tests/test_player_api.py
git commit -m "feat(danmaku): classify UP主/合作 author danmaku via crc32(midHash)"
```

---

### Task 4: `DanmakuLine.author` schema field

**Files:**
- Modify: `harvest/schema.py` (`DanmakuLine` gains `author`)
- Test: `tests/test_danmaku.py`

**Interfaces:**
- Produces: `DanmakuLine.author: Literal["owner", "staff"] | None` — additive, defaults `None`. `SCHEMA_VERSION` stays `"1.0"`.

- [ ] **Step 1: Write a failing test**

In `tests/test_danmaku.py`, add:

```python
def test_danmakuline_author_field_defaults_none_and_accepts_roles():
    assert DanmakuLine(text="x").author is None
    assert DanmakuLine(text="x", author="owner").author == "owner"
    assert DanmakuLine(text="x", author="staff").author == "staff"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_danmaku.py::test_danmakuline_author_field_defaults_none_and_accepts_roles -v`
Expected: FAIL — pydantic ignores/rejects unknown `author` (`AttributeError` on `.author`).

- [ ] **Step 3: Add the field**

In `harvest/schema.py`, add to `DanmakuLine` (after `high_like`; `Literal` is already imported):

```python
    author: Literal["owner", "staff"] | None = None  # video author of this line: "owner" (UP主) or
    # "staff" (合作 co-author), crc32-matched off the poster hash BEFORE clustering; None = organic
    # crowd. Higher authority than the surrounding crowd mirror (see PROTOCOL.md authority carve-out).
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_danmaku.py::test_danmakuline_author_field_defaults_none_and_accepts_roles -v`
Expected: PASS.

- [ ] **Step 5: Confirm `SCHEMA_VERSION` unchanged**

Run: `python -c "from harvest.schema import SCHEMA_VERSION; assert SCHEMA_VERSION == '1.0', SCHEMA_VERSION; print('ok 1.0')"`
Expected: prints `ok 1.0` (the change is additive; no bump).

- [ ] **Step 6: Commit**

```bash
git add harvest/schema.py tests/test_danmaku.py
git commit -m "feat(schema): add DanmakuLine.author (owner/staff), additive to 1.0"
```

---

### Task 5: `represent_danmaku` treats `author` as elevated (extract-before-cluster)

**Files:**
- Modify: `harvest/danmaku.py` (`_dedup_elevated` helper; per-window partition; `_fingerprint`)
- Test: `tests/test_danmaku.py`

**Interfaces:**
- Consumes: `RawDanmaku.author` (Task 3), `DanmakuLine.author` (Task 4).
- Produces: `_dedup_elevated(records: list[RawDanmaku]) -> list[tuple[DanmakuLine, float]]` — collapses byte-identical elevated danmaku keyed on `(text, high_like, author)`, each becoming a `DanmakuLine` carrying its own flags + first-occurrence `content_ts`. `represent_danmaku` now extracts *any* elevated line (`high_like` **or** `author`) before clustering; the ordinary partition (LLM-clustered) is `not (high_like or author)`.

- [ ] **Step 1: Update the `_rd` test helper and write failing tests**

In `tests/test_danmaku.py`, extend the `_rd` helper (line 25) to take `author`:

```python
def _rd(ts, text, high_like=False, author=None):
    return RawDanmaku(content_ts=ts, text=text, high_like=high_like, author=author)
```

Add the import for `_dedup_elevated` to the existing `from harvest.danmaku import (...)` block, and add these tests:

```python
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


def test_represent_danmaku_extracts_author_lines_before_clustering():
    # The LLM stub clusters ONLY what it is handed; if an author line reached it, it would be
    # collapsed/echoed. Assert author + high_like lines bypass it and keep verbatim text + flags.
    settings = Settings()

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
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_danmaku.py::test_dedup_elevated_keys_on_text_highlike_author_and_counts tests/test_danmaku.py::test_represent_danmaku_extracts_author_lines_before_clustering -v`
Expected: FAIL — `_dedup_elevated` does not exist; `represent_danmaku` still partitions on `high_like` only, so author lines are clustered (seen by the stub) and lose their `author` flag.

- [ ] **Step 3: Add `_dedup_elevated` and repartition the window loop**

In `harvest/danmaku.py`, add the helper after `_exact_dedup` (near line 90):

```python
def _dedup_elevated(records: list[RawDanmaku]) -> list[tuple[DanmakuLine, float]]:
    """Collapse byte-identical ELEVATED danmaku (high_like and/or author), keyed on
    (text, high_like, author) so distinct elevation combos stay distinct, preserving
    first-occurrence content_ts. Each becomes a DanmakuLine carrying its own flags. Never
    LLM-clustered -- an elevated line's exact wording IS its value (a promoted meme, an
    uploader correction), so it is extracted verbatim before the ordinary flood is clustered."""
    counts: dict[tuple[str, bool, str | None], int] = {}
    first_ts: dict[tuple[str, bool, str | None], float] = {}
    order: list[tuple[str, bool, str | None]] = []
    for r in records:
        key = (r.text, r.high_like, r.author)
        if key not in counts:
            counts[key] = 0
            first_ts[key] = r.content_ts
            order.append(key)
        counts[key] += 1
    out: list[tuple[DanmakuLine, float]] = []
    for key in order:
        text, high_like, author = key
        out.append(
            (DanmakuLine(text=text, count=counts[key], high_like=high_like, author=author),
             first_ts[key])
        )
    return out
```

In `represent_danmaku`, replace the per-window partition/extract block (the current
`promoted_records`/`ordinary_records`/`promoted_entries`/`promoted_lines`/`combined` lines, ~338-354) with:

```python
        # Extract-before-cluster: any ELEVATED danmaku (high_like OR author) is pulled out BEFORE
        # the LLM sees the window, so its exact wording + flags are never absorbed into a flood
        # cluster. The LLM only ever sees the ordinary partition.
        elevated_records = [r for r in records if r.high_like or r.author is not None]
        ordinary_records = [r for r in records if not (r.high_like or r.author is not None)]

        ordinary_entries = _exact_dedup(ordinary_records)
        clustered = _cluster_window(
            client, model, settings.lmstudio_danmaku_max_tokens, ordinary_entries
        )

        elevated_lines = _dedup_elevated(elevated_records)

        # Mechanical merge-sort by first-occurrence content_ts -- the window's final chronological
        # order, interleaving elevated lines with the clustered ordinary ones.
        combined = sorted(clustered + elevated_lines, key=lambda pair: pair[1])
        lines = [line for line, _ts in combined]
        windows.append(DanmakuWindow(start=start, end=end, total=len(records), lines=lines))
```

Update `_fingerprint` (near line 278) to fold `author` in, so a re-fetch that changes author
detection restages:

```python
def _fingerprint(fetch: DanmakuFetch) -> str:
    # high_like + author folded in: a re-fetch that only changes elevation status (no text/ts
    # change) must restage, since it changes which lines get extracted verbatim vs clustered.
    blob = "".join(
        f"{r.content_ts}:{r.high_like}:{r.author}:{r.text}\x1f" for r in fetch.records
    )
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]
```

- [ ] **Step 4: Run to verify pass + no regression**

Run: `python -m pytest tests/test_danmaku.py -v`
Expected: PASS — new tests pass; existing high_like extraction tests still pass (`high_like` lines are still elevated, still extracted before clustering; `_dedup_elevated` yields the same `high_like=True, author=None` lines the old `promoted_lines` block did).

- [ ] **Step 5: Commit**

```bash
git add harvest/danmaku.py tests/test_danmaku.py
git commit -m "feat(danmaku): extract author lines before clustering as elevated"
```

---

### Task 6: `bundle.md` single-pass chronological rendering with elevation pills

**Files:**
- Modify: `harvest/merge.py` (delete `HIGH_LIKE_MD_CAP` + two-group render; add `_line_pills`; single chronological pass; extend provenance note)
- Test: `tests/test_merge.py` (drop the `HIGH_LIKE_MD_CAP` import + two-cap test; add single-pass tests)

**Interfaces:**
- Consumes: `DanmakuLine.high_like`, `DanmakuLine.author` (Tasks 4/5).
- Produces: `_line_pills(line: DanmakuLine) -> str` — the elevation-pill prefix (`""`, `"👍 "`, `"UP主 "`, `"合作 "`, or `"👍 UP主 "`/`"👍 合作 "`), trailing space when non-empty. `render_markdown` walks `w.lines` once in chronological order; elevated lines always print; only ordinary lines are capped by `settings.danmaku_md_cap`.

- [ ] **Step 1: Rewrite the danmaku render tests**

In `tests/test_merge.py`: remove `HIGH_LIKE_MD_CAP,` from the `from harvest.merge import (...)` block (line 7). Delete the entire `test_render_markdown_danmaku_two_cap_promoted_first_with_own_overflow_markers` function (lines ~563 to the end of that test, including its `write_bundle` tail). Add these tests:

```python
def test_render_markdown_danmaku_pills_owner_staff_highlike_and_both():
    dm = Danmaku(
        source_total=None, fetched_total=4, model=None,
        windows=[DanmakuWindow(start=0.0, end=75.0, total=4, lines=[
            DanmakuLine(text="up", count=1, author="owner"),
            DanmakuLine(text="co", count=1, author="staff"),
            DanmakuLine(text="hot", count=1, high_like=True),
            DanmakuLine(text="both", count=1, high_like=True, author="owner"),
        ])],
    )
    md = render_markdown(_bundle_with_danmaku(dm), _settings())
    assert "- UP主 「up」" in md
    assert "- 合作 「co」" in md
    assert "- \U0001F44D 「hot」" in md
    assert "- \U0001F44D UP主 「both」" in md


def test_render_markdown_danmaku_elevated_never_dropped_ordinary_capped():
    settings = _settings()
    cap = settings.danmaku_md_cap
    # Interleave: an elevated line, then cap+4 ordinary, then another elevated line.
    lines = (
        [DanmakuLine(text="owner note", count=1, author="owner")]
        + [DanmakuLine(text=f"ord{i}", count=1) for i in range(cap + 4)]
        + [DanmakuLine(text="hot", count=1, high_like=True)]
    )
    dm = Danmaku(
        source_total=None, fetched_total=len(lines), model=None,
        windows=[DanmakuWindow(start=0.0, end=75.0, total=len(lines), lines=lines)],
    )
    md = render_markdown(_bundle_with_danmaku(dm), settings)
    # Both elevated lines survive regardless of the ordinary cap.
    assert "- UP主 「owner note」" in md
    assert "- \U0001F44D 「hot」" in md
    # Ordinary capped at `cap`; the 4 beyond are dropped with a single overflow marker.
    for i in range(cap):
        assert f"「ord{i}」" in md
    for i in range(cap, cap + 4):
        assert f"「ord{i}」" not in md
    assert "﹢4 more — see bundle.json" in md


def test_render_markdown_danmaku_preserves_chronological_order_across_kinds():
    dm = Danmaku(
        source_total=None, fetched_total=3, model=None,
        windows=[DanmakuWindow(start=0.0, end=75.0, total=3, lines=[
            DanmakuLine(text="first ordinary", count=1),
            DanmakuLine(text="second is owner", count=1, author="owner"),
            DanmakuLine(text="third hot", count=1, high_like=True),
        ])],
    )
    md = render_markdown(_bundle_with_danmaku(dm), _settings())
    assert md.index("first ordinary") < md.index("second is owner") < md.index("third hot")


def test_render_markdown_danmaku_note_mentions_elevation_pills():
    dm = Danmaku(
        source_total=100, fetched_total=80, model="qwen-test",
        windows=[DanmakuWindow(start=0.0, end=75.0, total=1,
                               lines=[DanmakuLine(text="x", count=1)])],
    )
    md = render_markdown(_bundle_with_danmaku(dm), _settings())
    assert "Lines pilled" in md
    assert "\U0001F44D/UP主/合作" in md
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_merge.py -k danmaku -v`
Expected: FAIL — new pill/overflow assertions fail (current renderer groups promoted-first, uses two caps, emits no `UP主`/`合作` pills and no "Lines pilled" note). Import of `HIGH_LIKE_MD_CAP` is already removed so collection succeeds.

- [ ] **Step 3: Rewrite the renderer**

In `harvest/merge.py`, delete the `HIGH_LIKE_MD_CAP` constant and its comment block (lines 22-26). Add a pill helper above `render_markdown`:

```python
def _line_pills(line) -> str:
    """Elevation-pill prefix for a danmaku line: 👍 (high_like) then UP主/合作 (author), space-joined
    with a trailing space. Empty string for an ordinary crowd line."""
    pills: list[str] = []
    if line.high_like:
        pills.append("\U0001F44D")
    if line.author == "owner":
        pills.append("UP主")
    elif line.author == "staff":
        pills.append("合作")
    return (" ".join(pills) + " ") if pills else ""
```

Replace the danmaku rendering block in `render_markdown` (the current `if dm and dm.windows:` body that builds the note and the two promoted/ordinary loops, ~190-222) with:

```python
    dm = bundle.danmaku
    if dm and dm.windows:
        lines.append("## Danmaku")
        note = f"_crowd track (lower authority than transcript) — fetched {dm.fetched_total}"
        if dm.source_total is not None:
            note += f" of {dm.source_total}"
        if dm.model:
            note += f" · {dm.model}"
        note += (
            ". Lines pilled \U0001F44D/UP主/合作 rank above the crowd "
            "(platform / video-author signals)._"
        )
        lines.append(note)
        lines.append("")
        for w in dm.windows:
            if not w.lines:
                continue
            lines.append(f"### [{_mmss(w.start)}] ({w.total} danmaku)")
            # ONE chronological pass (w.lines is already content-time ordered). Elevated lines
            # (high_like or author) always render in place; only ordinary lines are capped.
            ordinary_shown = 0
            for ln in w.lines:
                if not (ln.high_like or ln.author is not None):
                    if ordinary_shown >= settings.danmaku_md_cap:
                        continue
                    ordinary_shown += 1
                suffix = "" if ln.count == 1 else f" ×{ln.count}"
                lines.append(f"- {_line_pills(ln)}「{_neutralize(ln.text)}」{suffix}")
            ordinary_total = sum(
                1 for ln in w.lines if not (ln.high_like or ln.author is not None)
            )
            ordinary_overflow = ordinary_total - settings.danmaku_md_cap
            if ordinary_overflow > 0:
                lines.append(f"- ﹢{ordinary_overflow} more — see bundle.json")
            lines.append("")
```

- [ ] **Step 4: Run to verify pass + full-suite regression**

Run: `python -m pytest tests/test_merge.py -v`
Expected: PASS — new danmaku tests pass; the existing `test_render_markdown_emits_danmaku_section_with_provenance_and_counts`, `test_render_markdown_danmaku_caps_lines_with_overflow_marker` (all-ordinary), and `test_render_markdown_danmaku_under_cap_has_no_overflow_marker` still pass.

Then run the whole suite:

Run: `python -m pytest -q`
Expected: PASS — no references to `HIGH_LIKE_MD_CAP` remain (grep to confirm: `git grep -n HIGH_LIKE_MD_CAP -- harvest tests` returns nothing).

- [ ] **Step 5: Commit**

```bash
git add harvest/merge.py tests/test_merge.py
git commit -m "feat(danmaku): single-pass chronological bundle.md render with elevation pills"
```

---

## Self-Review

**1. Spec coverage** (against CONTEXT.md / PROTOCOL.md / SPEC.md as consolidated in Stage 3):
- *Author danmaku detection, zero new network* → Tasks 1 (midHash), 2 (staff mids), 3 (crc32 classify). ✓
- *UP主 vs 合作, owner precedence* → Task 3 (`role_by_hash`, owner written last). ✓
- *`DanmakuLine.author` additive, 1.0 stays* → Task 4. ✓
- *Elevated = high_like OR author, extracted before clustering, LLM never sees it* → Task 5. ✓
- *Single-pass chronological render, pills 👍/UP主/合作 combinable, elevated never dropped, one ordinary cap, `HIGH_LIKE_MD_CAP` deleted* → Task 6. ✓
- *Section note flags pilled lines as higher-authority* → Task 6 (note clause). ✓
- *Rides `--danmaku`, no new flag* → no CLI change needed; `fetch_danmaku` is already the `--danmaku` path. ✓ (no task required — verified by absence of a new flag in `cli.py`).

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code + test step shows full content. ✓

**3. Type consistency:** `RawDanmaku.mid_hash: str` / `.author: str | None` (Tasks 1/3) consumed by `classify_authors` (Task 3) and `_dedup_elevated` (Task 5). `DanmakuLine.author: Literal["owner","staff"] | None` (Task 4) produced by `_dedup_elevated`, consumed by `_line_pills` (Task 6). `classify_authors(records, owner_mid, staff_mids)` signature matches the `fetch_danmaku` call site (Task 3 Step 8). `_line_pills` reads `.high_like`/`.author` — both exist on `DanmakuLine`. Consistent. ✓

## Spike (do first, before Task 3 lands on real data)

Not a code task — a ~15-min empirical confirmation, since detection correctness rests on two platform facts the offline tests assume:
1. **crc32 format** — pull one real `seg.so` segment (existing cookies) for a video whose UP主 is known to have posted a danmaku; confirm `int(elem.midHash, 16) == zlib.crc32(str(owner_mid).encode()) & 0xFFFFFFFF`. (Integer comparison already makes Task 3 robust to hex padding/case; the spike confirms the *algorithm* matches.)
2. **staff shape** — fetch `web-interface/view` for a known 合作 video; confirm `data.staff` is a list of objects each with an integer `mid`.

If either differs from the assumption, revisit Task 2/3 before executing them.
