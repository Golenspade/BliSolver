# harvest — Danmaku `high_like` Enrichment (protobuf census switch)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or
> superpowers:executing-plans) to implement this plan task-by-task, each task strictly following
> superpowers:test-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Surface bilibili's **high-praise (高赞 / "high_like") danmaku** — the peer-elevated
comments the official client decorates with a 👍 and promotes on screen — as a first-class,
platform-sourced signal in the danmaku track. To get it, switch danmaku acquisition from the
server-**sampled XML endpoint** to the **protobuf census** (`x/v2/dm/web/seg.so`), which carries the
per-danmaku `attr` flag the XML lacks. This also yields a ~90–94%-complete census (vs the old
sample) as a free by-product.

**Builds on:** [2026-07-02-harvest-danmaku-metadata.md](2026-07-02-harvest-danmaku-metadata.md)
(the shipped danmaku track). This is an **additive** enrichment to that `1.0` contract, with **one
subtractive change** (removing `sampled`, justified below).

**Status:** Design locked via grilling session 2026-07-03, grounded on an **empirical spike against
6 live videos** (`scratch/spike_seg_attr.py`, `scratch/spike_color_pos_value.py`). The switch and
`attr` HighLike population are PROVEN on real data; schema/pipeline changes below are Task work
under TDD.

---

## What the spike proved (the base this design stands on)

Run against the 6 captured BV ids with harvest's existing cookies:

- ✅ **The plain web census `x/v2/dm/web/seg.so` works with the existing cookie opener — no WBI
  signing** (the SPEC §3 surface harvest deliberately avoids). Segment-paginated (`segment_index`
  1..N, 6-min segments); pagination terminates when a further segment yields no danmaku (observed as
  HTTP 304 / empty body past the video's end).
- ✅ **`DanmakuElem.attr` is populated and bit2 = `DMAttrHighLike` (高赞) fires on genuinely promoted
  danmaku.** Rates: 0–0.9% of danmaku (a handful per video) — the *elite* tier, sparse by nature.
  Sample hits were unmistakably peer-elevated (thoughtful analyses, sharp reactions), not noise.
- ✅ **The census is ~90–94% of `source_total`** (e.g. 3747/3977). The gap is deleted/shielded
  danmaku, not sampling — materially more complete than the XML sample.
- ✅ **Dependency-free protobuf decode** (stdlib varint reader, ~40 lines) is sufficient — no
  protobuf library needed, consistent with harvest's stdlib-only fetch layer.

**Deliberately dropped after the spike disproved their value (see Decisions):** raw like *counts*
(`thumbup/stats` endpoint), `color`, `position`, and `weight`.

### DanmakuElem protobuf fields (wire-format field numbers)

`DmSegMobileReply.elems` (field 1, repeated) → each `DanmakuElem`:
`1 id · 2 progress(ms) · 3 mode · 4 fontsize · 5 color · 6 midHash · 7 content · 8 ctime ·
9 weight · 11 pool · 12 idStr · 13 attr`. We read **`attr` (13)** for the HighLike bit and
**`progress` (2)** for content-time; `content` (7) for text. `DMAttrBit`: 0=Protect, 1=FromLive,
**2=HighLike**. Test: `(attr >> 2) & 1`.

---

## Design decisions (the calls that govern the code)

### The one signal: `high_like` (boolean), extracted before clustering
- **`high_like` is the ONE new per-danmaku signal.** It is bilibili's own promotion decision — a
  **platform-sourced fact, higher-trust than the LLM mirror** (unlike the clustered lines, it is not
  a model judgment). Surfaced as a boolean; we do NOT fetch raw like counts.
- **Raw like counts are rejected.** They require the per-danmaku `x/v2/dm/thumbup/stats` endpoint
  (N batched extra calls, rate-limit/block risk) — a hard dependency the mirror must never take. The
  boolean HighLike flag is the "similar quality" signal (the client's own 👍 promotion) and comes
  free in the census. (Grilling deal-breaker: acceptable to ship the flag, not the count.)
- **Extract-before-cluster (load-bearing).** A `high_like` danmaku is pulled out of the stream
  **verbatim, before** exact-dedup/clustering, and emitted as its own `count == 1` line carrying
  `high_like: true`. Rationale: a promoted danmaku's **exact wording IS its value**; if it were left
  in the pool it could be absorbed into a near-identical `×N` flood and its specific text lost (the
  common case — promoted comments are often the definitive version of a meme everyone is spamming).
  Ordinary danmaku cluster exactly as today.
- **The LLM stage is UNCHANGED.** It still receives only `[{text, count}]` and returns
  `[{text, count}]`; it never sees `high_like` or any metadata. Representative selection among the
  remaining *near-identical* variants stays the LLM's (low-stakes by construction — the high-stakes
  "which is the definitive danmaku" case is exactly what extraction removed). `weight` is NOT fed to
  the LLM (spike: `weight` is broad/non-selective, and injecting a number risks silent re-sorting
  that breaks the load-bearing chronological order).

### Chronological order across the three line types
- Within a window there are now three line kinds: **high_like** (individual, own content-time),
  **clustered `×N`** (positioned at the cluster's *first-occurrence* content-time), **singleton**
  (own content-time). The convention stays **"list order = content time"** (no per-line timestamp
  field). high_like lines are **interleaved by their content-time** into the same first-occurrence
  ordered stream. Implementation consequence: `_exact_dedup` must carry a first-occurrence
  content_ts alongside each entry so high_like lines can be merge-sorted into position.

### Dropped: color, position, weight, raw counts
- **Color & render position: dropped.** Spike disproved value: colored = 14–19% and top-pinned =
  13–18% of danmaku, but ~98.6% of colored danmaku are NOT high_like and the text is ordinary — they
  are **personal style, not a prominence signal**. The imagined referential use ("红字说得对") is rare
  in practice. Worse, surfacing them is **not free**: it forces a fragile "rejoin representative text
  → source danmaku" step to recover per-line color after the LLM clusters. Not worth it. The census
  still carries `color`/`mode`, so re-adding later is a localized change (YAGNI).
- **`weight`: dropped from the contract.** A broad anti-shield score (spike: `weight=10` was ~30% of
  danmaku), not a discriminating quality/like signal. Internal-only at most; not surfaced.

### Acquisition: full replacement of the XML path
- **The census REPLACES the XML endpoint** — `_parse_danmaku_xml`, `_decode`,
  `_API_DANMAKU_XML` are **deleted**. No fallback path retained (proven working with existing
  cookies; carrying two parsers/test-surfaces to guard an unobserved failure isn't worth it).
- **Graceful-empty on failure** (matches the existing `fetch_danmaku` stance): a fetch that can't
  resolve a cid or errors before any segment returns → `records=[]`. A fetch that errors
  *mid-pagination* returns what it got and **logs a warning**; the low `fetched_total` + early-ending
  windows tell the honest story.

### `sampled` removed; completeness expressed by the raw numbers
- **Remove `Danmaku.sampled`.** It was a proxy for the XML hard cap. Under a census its literal
  definition (`fetched_total < source_total`) fires on ~every video for the small deletion gap,
  making it meaningless; a threshold redefinition would bake an arbitrary judgment into the contract
  (against the mirror ethos). Keep **`source_total`** (exposes the deletion gap) and
  **`fetched_total`** (density ground-truth); "materially incomplete" is derivable from those plus
  window coverage vs `duration_s`.
- **Subtractive-change guard:** `sampled` is in the *shipped* schema. Before removing, confirm no
  consumer (hermes/atlas) reads `danmaku.sampled`. If unconsumed → `SCHEMA_VERSION` stays `"1.0"`.
  If something reads it → stop and revisit.

### Rendering (`bundle.md`)
- **👍 prefix** (U+1F44D) on each high_like line: `- 👍 「…」`.
- **Two independent per-window caps**, each with its own `﹢N more` overflow marker; `bundle.json`
  stays complete: **promoted budget `N = 20`** (👍 lines, rendered first) + **ordinary budget
  `M = 50`** (existing `DANMAKU_MD_CAP`). Total lines/window ≤ `N + M + markers` — bounded regardless
  of how viral the video is. Both are tunable module constants, not contract.

---

## Concrete schema delta (`harvest/schema.py`)

```python
class DanmakuLine(BaseModel):
    """A representative danmaku. `text` is VERBATIM. `count` = near-identical variants collapsed
    into this representative within the window (1 = singleton or an extracted promoted danmaku).
    Lines within a window are ordered CHRONOLOGICALLY by content time, never by count."""
    text:      str
    count:     int = 1
    high_like: bool = False   # NEW: bilibili 高赞 (attr bit2) — platform-promoted; extracted
                              # verbatim before clustering, never a cluster head. Higher-trust
                              # than the surrounding mirror (a platform fact, not an LLM judgment).


class Danmaku(BaseModel):
    """Crowd danmaku track — a faithful MIRROR of the audience stream (LOWER AUTHORITY than
    transcript), EXCEPT `DanmakuLine.high_like`, which is a platform-sourced fact. bilibili-only;
    present only when `--danmaku` ran on a supporting platform. bundle.json is the COMPLETE record;
    bundle.md caps per window (👍 promoted + ordinary, each with a '+N more' marker)."""
    source_total:  int | None = None     # bilibili's platform-reported total (stat.danmaku)
    fetched_total: int                   # census danmaku actually pulled (~90–94% of source_total)
    model:         str | None = None     # the LLM that produced the clustered mirror (provenance)
    windows:       list[DanmakuWindow] = Field(default_factory=list)
    # REMOVED: sampled  (obsolete XML-cap proxy; completeness derivable from source/fetched totals)
```

`DanmakuWindow` unchanged (`start`, `end`, `total`, `lines`).

## PROTOCOL.md deltas
- **`Danmaku` example:** drop the `sampled` line; change the `fetched_total` comment from
  "endpoint may sample" to "census; ~90–94% of source_total, gap = deleted/shielded danmaku."
- **`DanmakuLine`:** document `high_like` — bilibili 高赞, a platform-promoted flag; note it is
  **higher authority than the surrounding mirror** (a platform fact, not model output).
- **Rendering paragraph:** document the 👍 prefix and the **two-cap** scheme (promoted N=20 +
  ordinary M=50, each with its own `﹢N more — see bundle.json`).
- **Authority paragraph:** add the carve-out that `high_like` is the one danmaku field that is NOT
  crowd-opinion-to-be-doubted but a platform signal (still about reaction, not video facts).
- Remove the stray "endpoint may sample" framing anywhere it implies the danmaku track is a sample.

## SPEC.md deltas
- Danmaku is already shipped; adjust only wording that calls the danmaku track a "sample." Note the
  acquisition source is now the protobuf census (no WBI). No new SPEC section required.

## Global Constraints (apply to every task)
- **`SCHEMA_VERSION` stays `"1.0"`** *iff* the `sampled` removal is confirmed unconsumed (see guard).
  All other changes are additive.
- **Danmaku LLM stays a fenced mirror** — no decode/translate/summarize/typing; `high_like` is
  handled entirely mechanically, outside the LLM.
- **`bundle.json` danmaku is always complete;** only `bundle.md` rendering caps (two markers).
- **Tests offline by default** — inject openers/clients; capture a trimmed protobuf `seg.so` fixture
  (a few segments incl. ≥1 HighLike elem) from the spike. Any live fetch/LLM test is
  `@pytest.mark.live`, excluded by default. Run: `./.venv/Scripts/python.exe -m pytest -q`.
- **Cache** key must fold `high_like` into the danmaku fingerprint (`_fingerprint`) so a re-fetch
  that changes promotion status restages.

---

## Tasks

Each task ends with an independently testable deliverable, is TDD (failing test first), and is
committed on completion. Files touched are all under `harvest/`.

### Task 1 — Census acquisition replaces XML + `sampled` removal (fetch seam)
**Files:** modify `harvest/player_api.py`, `harvest/schema.py`, `harvest/danmaku.py`; add
`harvest/danmaku_proto.py` (decoder); modify `tests/test_player_api.py`; add fixture
`tests/fixtures/bilibili/seg_sample.bin`.
**Produces (signatures later tasks rely on):**
- `RawDanmaku(content_ts: float, text: str, high_like: bool)` — `high_like` new; color/mode NOT kept.
- `DanmakuFetch(source_total: int | None, fetched_total: int, records: list[RawDanmaku])` —
  `sampled` **removed**.
- `fetch_danmaku(canonical, settings, *, opener=None, view=None) -> DanmakuFetch` — signature
  unchanged; internals swapped to census.
- `decode_seg(body: bytes) -> list[RawDanmaku]` in `danmaku_proto.py` — parses one `seg.so`
  segment's `DmSegMobileReply`, reading `content`(7), `progress`(2, ms÷1000→`content_ts`),
  `attr`(13)→`high_like = bool((attr>>2)&1)`.
- `Danmaku` loses `sampled` (keeps `source_total`, `fetched_total`, `model`, `windows`).

- [ ] **`sampled`-removal guard FIRST:** grep the consumer(s) for `\.sampled` / `"sampled"`. If
      `danmaku.sampled` is read anywhere, STOP and revisit (may force a `SCHEMA_VERSION` bump).
      Proceed only if unconsumed.
- [ ] Add the **dependency-free protobuf decoder** (`danmaku_proto.py`): stdlib varint +
      length-delimited reader; `DmSegMobileReply.elems`(1, repeated) → `DanmakuElem`. Seed the code
      from the proven `scratch/spike_seg_attr.py`. Failing test first, on a captured `seg_sample.bin`
      fixture containing ≥1 HighLike elem, asserting text/content_ts/`high_like` for known elems.
- [ ] Rewrite `fetch_danmaku` to page `x/v2/dm/web/seg.so?type=1&oid={cid}&segment_index={n}` via
      the existing `_opener` (cookies+Referer), incrementing `n` until a segment yields no danmaku
      (empty body / non-protobuf JSON error body); on a mid-pagination `URLError`/HTTP error, **log a
      warning and return what was gathered** (partial). `source_total` still comes from
      `view.danmaku_count` (the `fetch_view` call already made — NOT from `seg.so`). **Delete**
      `_parse_danmaku_xml`, `_decode`, `_API_DANMAKU_XML`. Test with an injected opener stubbing 2
      segments + a terminating empty segment; assert `fetched_total`, ordering, partial-on-error.
- [ ] **Remove `sampled` atomically so the suite stays green:** drop it from `DanmakuFetch`
      (player_api.py) AND `Danmaku` (schema.py) AND the `Danmaku(...)` construction in
      `represent_danmaku` (danmaku.py:~307-313, delete the `sampled=fetch.sampled` arg). Update
      model/fetch tests for the absent field.
- [ ] **`@live` verify:** run against ≥1 long (multi-segment) real video and confirm the
      termination condition doesn't truncate early (segments fetched ≈ `ceil(duration/360s)`).

### Task 2 — high_like extraction + chronological interleave (representation stage)
**Files:** modify `harvest/schema.py`, `harvest/danmaku.py`; modify `tests/test_danmaku.py`.
**Consumes:** `RawDanmaku.high_like` (Task 1). **Produces:** `DanmakuLine.high_like: bool = False`
(new schema field), populated; `_fingerprint` folds `high_like`. LLM input/output contract
(`[{text,count}]`) UNCHANGED.

- [ ] Add `DanmakuLine.high_like: bool = False` to `schema.py` (its first user is this task); update
      the model test.
- [ ] In `represent_danmaku`'s per-window loop: **before** `_exact_dedup`, partition records into
      `high_like` vs ordinary. Ordinary → existing `_exact_dedup` → `_cluster_window` (unchanged).
      high_like → collapse byte-identical into `DanmakuLine(text=…, count=<n identical>,
      high_like=True)`. Failing test first: a window with a promoted danmaku whose text is identical
      to a flood keeps the promoted one as its own `high_like=True` line, flood clusters separately.
- [ ] Make `_exact_dedup` (and the high_like path) carry each entry's **first-occurrence
      `content_ts`**; replace the index-based `_reorder_chronologically` ordering so the window's
      final `lines` (high_like + clustered + singleton) are merge-sorted by `content_ts`. Test the
      three-way interleave produces correct chronological order.
- [ ] Assert the LLM stub is called with payload containing **no** `high_like`/metadata key. Fold
      `high_like` into `_fingerprint` (Test: two fetches differing only in a `high_like` flag produce
      different keys). Commit.

### Task 3 — Render (👍 + two-cap) + docs
**Files:** modify `harvest/merge.py`, `PROTOCOL.md`, `SPEC.md`; modify `tests/test_merge.py` (or the
existing bundle round-trip test). **Consumes:** `DanmakuLine.high_like` (Task 2).

- [ ] `merge.py`: render `- 👍 「text」` for `high_like` lines (👍 = U+1F44D); implement the
      **two-cap** — promoted lines first, capped at `HIGH_LIKE_MD_CAP = 20`, then ordinary capped at
      the existing `DANMAKU_MD_CAP = 50`, **each** with its own `- ﹢N more — see bundle.json`.
      `bundle.json` stays complete. Failing test first: a window with >20 promoted + >50 ordinary
      yields both markers and the json carries all lines; a promoted line renders with the 👍 prefix.
- [ ] PROTOCOL.md + SPEC.md: apply the deltas above (rewrite the affected danmaku paragraphs fresh,
      not patched onto stale text). Commit.

---

## Empirical evidence (in `scratch/`, gitignored — keep as reference)
- `spike_seg_attr.py` — proven census fetch + dependency-free protobuf decode + attr HighLike report.
- `spike_color_pos_value.py` — the color/position value analysis that justified dropping them.
- (Prior) `dump_danmaku.py`, `_dump_BV*.json`, `_contract.md`, `_sonnet_*`, `_lmstudio_*` — the
  original danmaku-track probes.

## Deferred (not this batch)
- **Raw like counts** (`x/v2/dm/thumbup/stats`) — rejected (per-danmaku endpoint, rate-limit risk);
  the boolean HighLike flag supersedes the need.
- **Color / render position** — census carries them; re-add is localized if a real referential use
  case emerges.
