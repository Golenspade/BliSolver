# harvest — Danmaku + Metadata Enrichment Design & Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or
> superpowers:executing-plans) to implement this plan task-by-task, each task strictly following
> superpowers:test-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add two coupled, **additive** capabilities to the `1.0` contract — (1) **engagement/
metadata enrichment** (thumbnail + view/like/coin/favorite/share/reply/danmaku counts) surfaced by
`probe` and `ingest`; (2) a **bilibili danmaku** track (`--danmaku` opt-in) that faithfully mirrors
the audience comment stream into the bundle. A third feature (**AV remux for collection**) is
**deferred** to a separate `collect` verb and is out of scope here.

**Why coupled:** the danmaku `--danmaku` opt-in is only *usable* if the caller (hermes) can first
see `stats.danmaku_count` (and other engagement signals) from a cheap `probe`. Metadata is the
prerequisite; ship it first.

**Status:** Design locked via grilling session 2026-07-02 (+ empirical probe on 6 blind bilibili
videos, sonnet "ceiling" vs local-qwen "floor"). Schema below is drafted but NOT yet in `schema.py`
— that is Task work under TDD. Contract stays **`SCHEMA_VERSION = "1.0"`** (all additive; hermes has
not yet consumed `1.0`, so it learns the expanded shape directly — no migration).

---

## Design decisions (the calls that govern the code)

### Danmaku philosophy (the load-bearing constraints)
- **Danmaku is a first-class payload, not lecture-enrichment.** The memes / sarcasm / collective
  mood ARE the value; for some videos danmaku > the audio content. harvest stays general-purpose —
  ingestion focus (is danmaku the point for *this* video?) is the caller's/hermes's call, not
  harvest's.
- **Narrow, hard-quarantined interpretive exception (SPEC §1/§8).** harvest is otherwise
  non-interpretive; the danmaku stage is the *one* place an LLM is applied to produce output. It is
  fenced: a separate, explicitly-crowd-sourced, **LOWER-authority** track, never fused into
  `transcript`. Requires an explicit SPEC carve-out.
- **The LLM MIRRORS, it does not DECODE.** It must never explain/translate/label what a comment or
  meme means (no hope of correctly reading latest memes — proven necessary by the probe). It only:
  (a) **exact-dedup** (mechanical pre-pass), (b) **cluster near-identical** variants and (c) select
  **representative VERBATIM** samples with counts. "Which samples are representative" is a *sampling*
  judgment (accepted); it is never a *meaning* judgment.
- **Chronological order within a window — NOT count-descending.** (Empirical: count-sort destroyed
  the temporal signal; every ceiling agent flagged structure-loss as the #1 gap.) Order carries the
  crowd's real-time progression (mutation chains, pile-ons, escalation). Representing *structure*
  (threads/factions/mutations) is hermes's job to INFER from the ordered mirror — harvest never
  marks it. Reply-thread reconstruction is out of scope (no parent-id exists; would be decode).
- **Windowing:** danmaku is chunked on **fixed content-time windows aligned to the existing bundle
  chunks** (`merge.py::chunk`). Density imbalance between windows is **signal, not noise** (a burst
  window vs a quiet window shows where the crowd reacted) — never normalize it away; quiet stretches
  read as timeline gaps. Dynamic count-balanced batching for LLM calls is an internal
  processing detail, invisible to the contract; results reassemble into the fixed windows.
- **Volume/compression (option b):** compression exists only where there's redundancy. Floods
  cluster well; a diverse-substantive crowd (e.g. a debate) has nothing to compress and its large
  section IS the faithful payload — do NOT force-drop distinct content. Therefore: **`bundle.json`
  always carries the COMPLETE faithful danmaku (uncapped).** `bundle.md` carries the same but applies
  a **pathological-size safety-valve cap per window** (top-K representatives + a `﹢N more — see
  bundle.json` overflow marker). Normal volumes: md is complete. Consequence: danmaku-as-payload
  ingestion means hermes reads `bundle.json`'s danmaku for the full set.

### Metadata / stats
- **`stats` is a volatile snapshot @ `fetched_at`** — grouped into one nested object so the grouping
  itself signals time-sensitivity. All fields nullable; per-platform partial (bilibili fills all
  from the view API `data.stat.*` — verified free, same call already made; YouTube fills
  view/like/thumbnail only). `thumbnail_url` is intrinsic (descriptive), kept at top level, NOT in
  `stats`.

### Model routing (feasibility, proven by the floor probe)
- The configured `lmstudio_vision_model` (`qwen/qwen3.6-27b`) is a **reasoning** model → unsuitable
  as-is: at `max_tokens=4096` it returns EMPTY on any window >~30 danmaku (burns the budget in
  `<think>`; `finish=length`, 0 content). Fix = **reasoning disabled** (qwen3 `/no_think`) and/or a
  **non-reasoning model** (`gemma-4-12b-it` is already loaded) + adequate budget. Danmaku needs a
  **per-stage model selector** distinct from the vision VL model (the predicted SPEC §4.3
  consequence). New `.env`: `HARVEST_DANMAKU_MODEL`, `HARVEST_DANMAKU_MAX_TOKENS` (default ~8192).

---

## Concrete schema (target `harvest/schema.py`)

```python
class Stats(BaseModel):
    """Engagement metrics — a POINT-IN-TIME SNAPSHOT as of the enclosing record's `fetched_at`,
    NOT stable identity. All fields volatile (generally grow, but can be reset/hidden) and
    per-platform partial: bilibili fills all; YouTube fills view_count/like_count only. Null-tolerate
    every field; never compare across bundles without accounting for each record's `fetched_at`."""
    view_count:     int | None = None
    like_count:     int | None = None
    coin_count:     int | None = None   # bilibili 硬币; YouTube null
    favorite_count: int | None = None   # bilibili 收藏; YouTube null
    share_count:    int | None = None   # bilibili 分享; YouTube null
    reply_count:    int | None = None   # top-level comments (bilibili stat.reply); YouTube null
    danmaku_count:  int | None = None   # bilibili total danmaku — the --danmaku opt-in signal; YT null


class DanmakuLine(BaseModel):
    """A representative danmaku = one cluster head. `text` is VERBATIM (never paraphrased/translated/
    decoded). `count` = near-identical variants collapsed into this representative within the window
    (1 = singleton). Lines within a window are ordered CHRONOLOGICALLY by content time, never by
    count."""
    text:  str
    count: int = 1


class DanmakuWindow(BaseModel):
    """Danmaku pinned to content-time window [start, end) seconds (aligned to bundle chunks).
    `total` = raw danmaku in the window BEFORE clustering — the density signal that survives even if
    `lines` is capped in bundle.md."""
    start: float
    end:   float
    total: int
    lines: list[DanmakuLine] = Field(default_factory=list)


class Danmaku(BaseModel):
    """Crowd danmaku track — a faithful MIRROR of the audience stream, NOT interpreted content.
    LOWER AUTHORITY than `transcript`: crowd expression (jokes, memes, sarcasm, frequently 'wrong'
    claims); never treat as authoritative. bilibili-only; present only when `--danmaku` was requested
    on a supporting platform (else Bundle.danmaku is null). bundle.json is the COMPLETE record;
    bundle.md may cap per window with a '+N more' marker (read this JSON for the full set)."""
    source_total:  int | None = None    # platform-reported total (stat.danmaku)
    fetched_total: int                  # how many actually pulled (endpoint may sample)
    sampled:       bool                 # fetched_total < source_total → a sample, not a census
    model:         str | None = None    # the LLM that produced this representation (provenance)
    windows:       list[DanmakuWindow] = Field(default_factory=list)


# ProbeResult gains: thumbnail_url (intrinsic), fetched_at (it had none), stats
# Bundle      gains: thumbnail_url, stats, danmaku: Danmaku | None = None
```

## PROTOCOL.md additions
- Document `Stats`, `Danmaku*` shapes in the ingest/probe sections.
- New subsection **"Stable vs volatile fields"**: intrinsic fields (`platform, id, title, uploader,
  uploader_id, description, duration_s, published_at, thumbnail_url, parts`) are stable; the `stats`
  object is a snapshot @ `fetched_at` (counts grow but can reset/hide; never compare across bundles
  without `fetched_at`). `stats.danmaku_count` is the `--danmaku` opt-in signal.
- Document danmaku **authority**: a crowd-sourced track strictly BELOW `transcript`; never
  authoritative; `bundle.json` is the complete record when danmaku is the payload.

## Global Constraints (apply to every task)
- **`SCHEMA_VERSION` stays `"1.0"`.** All additions are additive optional fields. Do not bump.
- **Additive only** — no existing field changes shape/removes. YouTube nulls bilibili-only stats.
- **Danmaku LLM stays a fenced mirror** — no decode/translate/summarize/typing in code or prompts.
- **`bundle.json` danmaku is always complete;** only `bundle.md` rendering caps (with overflow marker).
- **Tests offline by default** — inject openers, use trimmed fixtures (capture from `scratch/_dump_*`);
  never hit network/LM Studio in the default suite. Any live danmaku/LLM test is `@pytest.mark.live`,
  excluded by default. Run: `./.venv/Scripts/python.exe -m pytest -q`.
- **Cache** the danmaku stage by identity + stage-param-hash (model, prompt version, window params,
  fetched danmaku fingerprint) per SPEC §5 — changing `--danmaku` knobs must not restage unrelated
  work nor silently return a stale result.

---

## Tasks

### Task 1 — Metadata/stats enrichment (the danmaku prerequisite)
- [ ] Add `Stats` model; add `thumbnail_url`, `fetched_at`, `stats` to `ProbeResult`; add
      `thumbnail_url`, `stats` to `Bundle`. (schema.py)
- [ ] Extend `SourceMetadata` (providers/base.py) with `thumbnail_url` + the stat fields.
- [ ] **bilibili:** map `data.stat.{view,danmaku,like,coin,favorite,share,reply}` + `data.pic` in
      `ViewData`/`fetch_view` → `SourceMetadata`. (player_api.py, providers/bilibili.py)
- [ ] **YouTube:** map `info["view_count"]`, `info["like_count"]`, `info["thumbnail"]` → the shared
      fields; leave bilibili-only stats null. (providers/youtube.py)
- [ ] `probe.py`: populate `fetched_at` + `stats` + `thumbnail_url`. `merge.py`: carry them into
      `Bundle`; render `thumbnail_url` in the bundle.md header.
- [ ] PROTOCOL.md: add the fields + the "Stable vs volatile fields" note. Tests updated for the new
      shape (offline fixtures).

### Task 2 — Danmaku acquisition (provider seam)
- [ ] Promote `scratch/dump_danmaku.py` into a real provider capability: bilibili `fetch_danmaku`
      returning raw `(content_ts, text)` records (+ `source_total` from `stat.danmaku`). Reuse
      `_opener` (cookies+Referer) + `cid_for_part`. Handle deflate/gzip decode.
- [ ] **Scope lever (decide at build):** XML endpoint `comment.bilibili.com/{cid}.xml` (server-
      sampled, simple — MVP) vs protobuf `seg.so` (full census, density-faithful). Schema's
      `sampled`/`fetched_total`/`source_total` supports either; start XML, set `sampled` correctly.
- [ ] Offline tests from captured `scratch/_dump_*.json` fixtures.

### Task 3 — Danmaku representation stage (the fenced LLM mirror)
- [ ] Mechanical **exact-dedup** pre-pass (deterministic: collapse byte-identical → text+count).
- [ ] **Window** by fixed content-time aligned to bundle chunks; **dynamic count-batching** for LLM
      calls under the hood; reassemble into fixed windows.
- [ ] LLM call via a **danmaku-specific model selector** (reasoning-off; `HARVEST_DANMAKU_MODEL`,
      `HARVEST_DANMAKU_MAX_TOKENS`). Prompt = the mirror contract (`scratch/_contract.md` is the
      validated seed): verbatim, cluster near-dups, representative selection, **chronological order**,
      no decode. Produce `DanmakuWindow`/`DanmakuLine`.
- [ ] Cache per Global Constraints. Offline unit tests with a stub LLM client; one `@live` smoke test.

### Task 4 — Pipeline + CLI + render integration
- [ ] `--danmaku` flag (default OFF). On YouTube: warn + no-op (Bundle.danmaku stays null).
- [ ] Wire the danmaku stage into `process_part` (opt-in), co-located with the transcript/frame
      chunks. Populate `Bundle.danmaku`.
- [ ] `merge.py` render: emit danmaku per chunk in bundle.md with the **pathological-size cap +
      overflow marker**; bundle.json stays complete. bundle.md/json round-trip tests.

---

## Empirical evidence (in `scratch/`, gitignored — keep as reference)
- `dump_danmaku.py` — working dumper (auth-reusing, deflate-aware).
- `_dump_BV*.json` / `_flat_BV*.txt` — 6 blind bilibili videos (sparse↔flood↔debate spread).
- `_contract.md` — the validated candidate output contract (mirror rules) — seed for the Task 3 prompt.
- `_sonnet_BV*.md` — ceiling outputs + GAPS (structure-loss findings → chronological-order decision).
- `_lmstudio_BV*.md` + `run_lmstudio_danmaku.py` — floor outputs (reasoning-exhaustion finding →
  model-routing decision).

## Deferred (not this batch)
- **AV remux for collection** — a distinct `harvest collect` verb (SPEC §8 grammar); does not touch
  the `1.0` bundle contract.
- **Protobuf `seg.so` full-census danmaku** — if XML sampling proves insufficient (schema already
  ready via `sampled`).
- **`post_unix` (real-world danmaku post time)** — descoped from the contract (no compelling use case
  found); the meme-evolution-over-months angle didn't justify an equal slot to comment text.
