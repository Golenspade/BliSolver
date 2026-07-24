# Danmaku MD Cap → Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lower the per-window danmaku render cap from 50 to 15 and promote it from a hardcoded module constant to a configurable `Settings.danmaku_md_cap` (env `HARVEST_DANMAKU_MD_CAP`).

**Architecture:** The cap only ever trimmed `bundle.md` (the primary Atlas-ingestion surface); `bundle.json` stays complete and uncapped, so this touches no schema contract (`PROTOCOL.md` 1.0 is keyed to `bundle.json`). The value moves into `Settings` beside the sibling `danmaku_window_s`/`HARVEST_DANMAKU_WINDOW_S` knob; `render_markdown` already receives `settings`, so wiring is local. `HIGH_LIKE_MD_CAP` stays a hardcoded constant — a confirmed non-lever (~1 promoted line/window; never bites).

**Tech Stack:** Python 3.11, dataclasses, pytest, `python-dotenv`.

## Global Constraints

- The `## Danmaku` section cap applies to `bundle.md` ONLY. `bundle.json` MUST remain complete and uncapped (regression-guarded by `test_bundle_json_roundtrip_carries_complete_uncapped_danmaku`).
- Default cap value: `15`. Env override: `HARVEST_DANMAKU_MD_CAP`.
- Mirror the existing `danmaku_window_s` config idiom exactly (field default + `int(os.environ.get(...))` in `Settings.load()` + a config test).
- Do NOT edit dated historical artifacts under `.superpowers/sdd/` or `docs/superpowers/plans/2026-07-03-*.md` — they record what was built at the time; rewriting them falsifies the record. Live cascade only.
- `HIGH_LIKE_MD_CAP = 20` is out of scope — leave it exactly as-is.

---

### Task 1: Add `danmaku_md_cap` to Settings (config field + env override)

**Files:**
- Modify: `harvest/config.py:137` (add field after `danmaku_window_s`) and `harvest/config.py:155-157` (add env parse in `load()`)
- Modify: `.env.example:11` (add sibling env line)
- Test: `tests/test_config.py` (add after `test_danmaku_window_s_default_is_15_and_env_overridable`)

**Interfaces:**
- Produces: `Settings.danmaku_md_cap: int` (default `15`), populated from `HARVEST_DANMAKU_MD_CAP` in `Settings.load()`. Consumed by `render_markdown` in Task 2.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py` (mirrors the `danmaku_window_s` test directly above it):

```python
def test_danmaku_md_cap_default_is_15_and_env_overridable(monkeypatch):
    monkeypatch.delenv("HARVEST_DANMAKU_MD_CAP", raising=False)
    assert Settings.load().danmaku_md_cap == 15
    monkeypatch.setenv("HARVEST_DANMAKU_MD_CAP", "25")
    assert Settings.load().danmaku_md_cap == 25
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::test_danmaku_md_cap_default_is_15_and_env_overridable -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'danmaku_md_cap'`

- [ ] **Step 3: Add the field**

In `harvest/config.py`, immediately after the `danmaku_window_s: float = 15.0` line (currently line 137), add:

```python
    # Per-window cap on ORDINARY danmaku lines rendered in bundle.md (the primary Atlas
    # ingestion surface); bundle.json always carries the complete, uncapped set. A deliberate
    # gestalt-sample dial, NOT a pathological ceiling: Atlas Gate 2 needs the crowd's per-beat
    # gestalt, not exhaustiveness. 15 nearly halves the section (empirical: ~3.2k→~1.8k lines
    # over a 3.7k-danmaku demo, 2026-07) while keeping ~15 distinct reactions per hot 15s beat.
    danmaku_md_cap: int = 15
```

- [ ] **Step 4: Wire the env override**

In `harvest/config.py`, inside the `cls(...)` call in `load()`, immediately after the `danmaku_window_s=float(...)` block (currently lines 155-157), add:

```python
            danmaku_md_cap=int(
                os.environ.get("HARVEST_DANMAKU_MD_CAP", cls.danmaku_md_cap)
            ),
```

- [ ] **Step 5: Document the env var**

In `.env.example`, after line 11 (`# HARVEST_DANMAKU_WINDOW_S=15`), add:

```
# HARVEST_DANMAKU_MD_CAP=15
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (new test + existing `danmaku_window_s` test both green)

- [ ] **Step 7: Commit**

```bash
git add harvest/config.py tests/test_config.py .env.example
git commit -m "feat: add configurable danmaku_md_cap (default 15, HARVEST_DANMAKU_MD_CAP)"
```

---

### Task 2: Wire render_markdown to `settings.danmaku_md_cap`; remove the constant

**Files:**
- Modify: `harvest/merge.py:19-21` (remove `DANMAKU_MD_CAP` constant + its comment), `harvest/merge.py:184` and `harvest/merge.py:187` (read from settings)
- Modify: `tests/test_merge.py:5` (drop the import) and the four tests that referenced the constant (lines ~467, ~483 unaffected, ~494, ~535)

**Interfaces:**
- Consumes: `Settings.danmaku_md_cap` (Task 1).
- Produces: `render_markdown(bundle, settings)` unchanged in signature; ordinary-line cap now sourced from `settings.danmaku_md_cap`. `HIGH_LIKE_MD_CAP` remains an importable module constant.

- [ ] **Step 1: Update the failing tests first (they pin the new behavior)**

The four tests currently import `DANMAKU_MD_CAP` from `harvest.merge`. Rewrite them to read the cap from `settings` so they stay correct at any default.

In `tests/test_merge.py`, change the import block (lines 4-12) to drop `DANMAKU_MD_CAP,` (keep `HIGH_LIKE_MD_CAP`):

```python
from harvest.merge import (
    HIGH_LIKE_MD_CAP,
    build_bundle,
    chunk,
    chunk_boundaries,
    render_markdown,
    write_bundle,
)
```

Rewrite `test_render_markdown_danmaku_caps_lines_with_overflow_marker` (lines 467-480):

```python
def test_render_markdown_danmaku_caps_lines_with_overflow_marker():
    settings = _settings()
    cap = settings.danmaku_md_cap
    lines = [DanmakuLine(text=f"line{i}", count=1) for i in range(cap + 7)]
    dm = Danmaku(
        source_total=None, fetched_total=len(lines), model=None,
        windows=[DanmakuWindow(start=0.0, end=75.0, total=len(lines), lines=lines)],
    )
    bundle = _bundle_with_danmaku(dm)
    md = render_markdown(bundle, settings)

    for i in range(cap):
        assert f"「line{i}」" in md
    for i in range(cap, cap + 7):
        assert f"「line{i}」" not in md
    assert "﹢7 more — see bundle.json" in md
```

In `test_render_markdown_danmaku_two_cap_promoted_first_with_own_overflow_markers` (lines 494-532), add `cap = settings.danmaku_md_cap` right after `settings.out_dir = tmp_path / "out"` (line 510), then replace every `DANMAKU_MD_CAP` in that test body with `cap` (the `range(DANMAKU_MD_CAP + 9)` on line 501 must move below the `settings`/`cap` definition — build `ordinary` after `cap` is defined, or bind `cap = Settings().danmaku_md_cap` at the top of the test before constructing lines). Concretely, restructure the top of the test:

```python
def test_render_markdown_danmaku_two_cap_promoted_first_with_own_overflow_markers(tmp_path):
    settings = Settings()
    settings.out_dir = tmp_path / "out"
    cap = settings.danmaku_md_cap
    promoted = [
        DanmakuLine(text=f"promo{i}", count=1, high_like=True)
        for i in range(HIGH_LIKE_MD_CAP + 5)
    ]
    ordinary = [
        DanmakuLine(text=f"ord{i}", count=1, high_like=False)
        for i in range(cap + 9)
    ]
    lines = promoted + ordinary
    dm = Danmaku(
        source_total=None, fetched_total=len(lines), model=None,
        windows=[DanmakuWindow(start=0.0, end=75.0, total=len(lines), lines=lines)],
    )
    bundle = _bundle_with_danmaku(dm)

    md = render_markdown(bundle, settings)

    for i in range(HIGH_LIKE_MD_CAP):
        assert f"- \U0001F44D 「promo{i}」" in md
    for i in range(HIGH_LIKE_MD_CAP, HIGH_LIKE_MD_CAP + 5):
        assert f"promo{i}" not in md
    for i in range(cap):
        assert f"- 「ord{i}」" in md
    for i in range(cap, cap + 9):
        assert f"ord{i}" not in md
    assert "﹢5 more — see bundle.json" in md
    assert "﹢9 more — see bundle.json" in md
    assert md.index("promo0") < md.index("ord0")

    out = write_bundle(bundle, settings, frame_sources={}, frame_images=False)
    data = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert len(data["danmaku"]["windows"][0]["lines"]) == len(lines)  # uncapped in json
```

In `test_bundle_json_roundtrip_carries_complete_uncapped_danmaku` (lines 535-553), bind `cap` and replace `DANMAKU_MD_CAP + 10`:

```python
def test_bundle_json_roundtrip_carries_complete_uncapped_danmaku(tmp_path):
    cap = Settings().danmaku_md_cap
    lines = [DanmakuLine(text=f"line{i}", count=1) for i in range(cap + 10)]
    dm = Danmaku(
        source_total=200, fetched_total=len(lines), model="m1",
        windows=[DanmakuWindow(start=0.0, end=75.0, total=len(lines), lines=lines)],
    )
    bundle = _bundle_with_danmaku(dm)
    settings = Settings()
    settings.out_dir = tmp_path / "out"

    out = write_bundle(bundle, settings, frame_sources={}, frame_images=False)

    data = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert len(data["danmaku"]["windows"][0]["lines"]) == cap + 10  # uncapped

    roundtripped = Bundle.model_validate_json((out / "bundle.json").read_text(encoding="utf-8"))
    assert len(roundtripped.danmaku.windows[0].lines) == cap + 10
    assert roundtripped.danmaku.source_total == 200
    assert roundtripped.danmaku.model == "m1"
```

(`test_render_markdown_danmaku_under_cap_has_no_overflow_marker` at lines 483-491 uses a hardcoded `range(3)` and no constant — leave it unchanged; 3 < 15 so it still asserts "no overflow marker".)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_merge.py -v`
Expected: FAIL at import — `ImportError: cannot import name 'DANMAKU_MD_CAP' from 'harvest.merge'`

- [ ] **Step 3: Update merge.py — remove the constant, read from settings**

In `harvest/merge.py`, replace the constant block (lines 19-24) so only `HIGH_LIKE_MD_CAP` remains, with a pointer comment:

```python
# The ordinary per-window danmaku cap for bundle.md lives in Settings.danmaku_md_cap
# (env HARVEST_DANMAKU_MD_CAP) -- a tunable gestalt-sample dial. bundle.json is always complete.
# Separate cap for platform-promoted (high_like) lines, rendered as their own group ahead of
# ordinary lines with their own overflow marker. Not a contract knob -- a rendering constant.
HIGH_LIKE_MD_CAP = 20
```

In `render_markdown`, change line 184 from `for ln in ordinary[:DANMAKU_MD_CAP]:` to:

```python
            for ln in ordinary[: settings.danmaku_md_cap]:
```

and change line 187 from `ordinary_overflow = len(ordinary) - DANMAKU_MD_CAP` to:

```python
            ordinary_overflow = len(ordinary) - settings.danmaku_md_cap
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_merge.py -v`
Expected: PASS (all render/roundtrip danmaku tests green)

- [ ] **Step 5: Commit**

```bash
git add harvest/merge.py tests/test_merge.py
git commit -m "refactor: render danmaku cap from settings.danmaku_md_cap; drop constant"
```

---

### Task 3: Doc cascade — update the live "caps at 50" assertion

**Files:**
- Modify: `C:/Users/2phite/AppData/Local/hermes/skills/research/video-transcript-ingestion/SKILL.md:148`

**Interfaces:**
- No code. This is a factual-reference correction: a stale literal is wrong, not history to preserve. No pressure-test applies (no behavior-shaping guidance; the writing-skills Iron Law targets discipline skills, not fact fixes).

- [ ] **Step 1: Rewrite the stale line to cite the config source of truth**

In `video-transcript-ingestion/SKILL.md`, replace line 148:

```
- `bundle.md`'s `## Danmaku` section caps at 50 lines/window (`﹢N more` marker); `bundle.json`
  is the complete record. "Requested, found nothing" = populated with `fetched_total: 0`;
```

with:

```
- `bundle.md`'s `## Danmaku` section caps ordinary lines per window at `danmaku_md_cap`
  (default 15; override `HARVEST_DANMAKU_MD_CAP`), with a `﹢N more` marker; `bundle.json` is
  the complete, uncapped record. "Requested, found nothing" = populated with `fetched_total: 0`;
```

- [ ] **Step 2: Verify no stale LIVE reference to "50 lines/window" remains**

Run (from `c:/Users/2phite/GitHub/harvest`):

```bash
grep -rn "caps at 50\|50 lines/window" --include=*.md --include=*.py . "C:/Users/2phite/AppData/Local/hermes/skills"
```

Expected: no output. (Historical `.superpowers/sdd/*` and `docs/.../2026-07-03-*.md` mention `DANMAKU_MD_CAP = 50` as a record of prior work — those are intentionally left; the grep above targets the live prose phrasing only and should return nothing.)

- [ ] **Step 3: Commit**

```bash
git add "C:/Users/2phite/AppData/Local/hermes/skills/research/video-transcript-ingestion/SKILL.md"
git commit -m "docs: point danmaku cap note at configurable danmaku_md_cap default"
```

(If the skills dir is a separate git repo from harvest, run this commit inside that repo instead.)

---

### Task 4: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest -q`
Expected: all tests pass (config, merge, cli, danmaku, providers).

- [ ] **Step 2 (behavioral spot-check, optional but recommended): confirm the cap actually bites at 15**

Run:

```bash
python -c "from harvest.config import Settings; from harvest.merge import render_markdown; from harvest.schema import Bundle, Danmaku, DanmakuWindow, DanmakuLine, Transcript; \
w=DanmakuWindow(start=0.0,end=15.0,total=40,lines=[DanmakuLine(text=f'l{i}',count=1) for i in range(40)]); \
b=Bundle(platform='bilibili.com',id='BV1',part=1,url='u',fetched_at='2026-07-04T00:00:00Z',transcript=Transcript(source='whisper',source_reason='t',segments=[]),frames=[],danmaku=Danmaku(source_total=None,fetched_total=40,model=None,windows=[w])); \
md=render_markdown(b,Settings()); print('l14 in md:', 'l14' in md, '| l15 in md:', 'l15' in md, '| overflow:', '﹢25 more' in md)"
```

Expected: `l14 in md: True | l15 in md: False | overflow: ﹢25 more True`

---

## Self-Review

**Spec coverage:**
- Cap value 50→15 ✓ (Task 1 default, Task 2 wiring, Task 4 spot-check).
- Promote to config knob ✓ (Task 1 field + env + `.env.example`).
- `HIGH_LIKE_MD_CAP` untouched ✓ (kept as constant in Task 2).
- `bundle.json` stays uncapped ✓ (roundtrip test preserved in Task 2).
- Doc cascade, live only ✓ (Task 3; historical artifacts excluded per Global Constraints).
- No contract change ✓ (`PROTOCOL.md` keyed to `bundle.json`; not touched).

**Placeholder scan:** none — every code/edit step shows exact content.

**Type consistency:** `danmaku_md_cap: int` defined in Task 1, consumed as `settings.danmaku_md_cap` (int slice bound) in Task 2, asserted as int in tests. `HIGH_LIKE_MD_CAP` import retained in test_merge.py. Consistent.
