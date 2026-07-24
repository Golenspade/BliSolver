# render_markdown Output Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `bundle.md`'s frontmatter always-valid YAML and its untrusted body text inert against markdown-structure forging, and nest transcript windows under a `## Transcript` section.

**Architecture:** All changes live in `render_markdown()` in `harvest/merge.py` plus a one-line dependency add. Frontmatter is serialized with PyYAML instead of bare f-strings; untrusted free-text fields pass through a line-by-line `_neutralize()` escaper; transcript chunks gain a `## Transcript` parent and drop from `## [mm:ss]` to `### [mm:ss]`. `write_bundle()` and `bundle.json` are untouched.

**Tech Stack:** Python 3.11+, PyYAML 6, pydantic, pytest.

## Global Constraints

- Target file for all code changes: `harvest/merge.py`. Test file: `tests/test_merge.py`.
- `requires-python = ">=3.11"`; do not use newer syntax.
- `bundle.json` output and `write_bundle()` behavior must not change.
- Preserve the existing frontmatter field order exactly: platform, id, part, url, title, uploader, uploader_id, thumbnail_url, duration, published_at, fetched_at, transcript_source, vision_model, tool_version.
- Preserve the early-return behavior: when there is no transcript and no frames, render stops after the transcript placeholder (danmaku is intentionally dropped in that path).
- Run tests with: `python -m pytest tests/test_merge.py -q`
- Spec: `docs/superpowers/specs/2026-07-04-render-markdown-injection-design.md`

---

### Task 1: Frontmatter via PyYAML (Bug A)

**Files:**
- Modify: `pyproject.toml` (add `pyyaml` to `dependencies`)
- Modify: `harvest/merge.py` (imports + frontmatter block in `render_markdown`)
- Test: `tests/test_merge.py`

**Interfaces:**
- Consumes: existing `render_markdown(bundle, settings) -> str`.
- Produces: a `_frontmatter(md: str) -> dict` test helper (used by later tasks) that parses the `---`…`---` header via `yaml.safe_load`. Frontmatter is now valid YAML for any field content; scalars that would re-parse as non-strings (timestamps, `NN:NN` durations, numeric-string ids, `?`, empty) come back quoted in the raw text but `yaml.safe_load` returns the original Python strings. `part` remains an int.

- [ ] **Step 1: Write the failing regression tests + parse helper**

Add to `tests/test_merge.py` (top-level, near the other helpers):

```python
import yaml


def _frontmatter(md):
    """Parse the --- ... --- YAML header of a rendered bundle.md into a dict."""
    lines = md.splitlines()
    assert lines[0] == "---"
    close = lines.index("---", 1)
    return yaml.safe_load("\n".join(lines[1:close]))


def _bundle(**overrides):
    """A minimal renderable bundle; override any field by keyword."""
    fields = dict(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    fields.update(overrides)
    return Bundle(**fields)


def test_frontmatter_title_with_colon_space_roundtrips():
    md = render_markdown(_bundle(title="Rust: The Book"), _settings())
    assert _frontmatter(md)["title"] == "Rust: The Book"


def test_frontmatter_leading_indicator_values_roundtrip():
    md = render_markdown(_bundle(title="[weird", uploader="& anchor"), _settings())
    fm = _frontmatter(md)
    assert fm["title"] == "[weird"
    assert fm["uploader"] == "& anchor"


def test_frontmatter_part_stays_int_and_fields_ordered():
    md = render_markdown(_bundle(uploader_id="42", part=1), _settings())
    fm = _frontmatter(md)
    assert fm["part"] == 1  # int, not "1"
    assert fm["uploader_id"] == "42"  # str preserved
    keys = list(fm.keys())
    assert keys.index("uploader") + 1 == keys.index("uploader_id")
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_merge.py::test_frontmatter_title_with_colon_space_roundtrips -q`
Expected: FAIL — current output is `title: Rust: The Book`, so `yaml.safe_load` raises a `ScannerError`/`ComposerError` (mapping-value error) inside `_frontmatter`.

- [ ] **Step 3: Implement PyYAML frontmatter**

In `pyproject.toml`, add `pyyaml` to `dependencies`:

```toml
dependencies = [
    "yt-dlp>=2026.6.9",
    "pydantic>=2.13",
    "python-dotenv>=1.2",
    "pyyaml>=6",
]
```

In `harvest/merge.py`, add the import after the stdlib imports (line ~13):

```python
import yaml
```

Replace the frontmatter list construction at the start of `render_markdown` (the `lines = [ "---", f"platform: ...", ... "---", "", f"# {bundle.title or bundle.id}", "" ]` block) with:

```python
    t = bundle.transcript
    dur = _mmss(bundle.duration_s) if bundle.duration_s else "?"
    front = {
        "platform": bundle.platform,
        "id": bundle.id,
        "part": bundle.part,
        "url": bundle.url,
        "title": bundle.title or "",
        "uploader": bundle.uploader or "",
        "uploader_id": bundle.uploader_id or "",
        "thumbnail_url": bundle.thumbnail_url or "",
        "duration": dur,
        "published_at": bundle.published_at or "",
        "fetched_at": bundle.fetched_at,
        "transcript_source": f"{t.source} ({t.source_reason})",
        "vision_model": bundle.meta.vision_model or "none",
        "tool_version": bundle.meta.tool_version,
    }
    fm = yaml.safe_dump(
        front, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).strip()
    lines = [
        "---",
        fm,
        "---",
        "",
        f"# {bundle.title or bundle.id}",
        "",
    ]
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python -m pytest tests/test_merge.py -k frontmatter -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Rewrite the existing frontmatter assertions to parse instead of byte-match**

Several existing tests assert exact frontmatter lines that PyYAML now quotes. Rewrite each to use `_frontmatter`:

In `test_render_markdown_emits_thumbnail_url_in_header`, replace the assertion body:
```python
    md = render_markdown(bundle, _settings())
    assert _frontmatter(md)["thumbnail_url"] == "http://x/thumb.jpg"
```

In `test_render_markdown_thumbnail_url_empty_when_none`:
```python
    md = render_markdown(bundle, _settings())
    assert _frontmatter(md)["thumbnail_url"] == ""
```

In `test_render_markdown_emits_published_at_after_duration`, replace the `lines`/index assertions:
```python
    md = render_markdown(bundle, _settings())
    fm = _frontmatter(md)
    assert fm["published_at"] == "2024-06-28T16:00:00+08:00"
    keys = list(fm.keys())
    assert keys.index("duration") + 1 == keys.index("published_at")
```

In `test_render_markdown_published_at_empty_when_none`:
```python
    md = render_markdown(bundle, _settings())
    assert _frontmatter(md)["published_at"] == ""
```

In `test_render_markdown_emits_uploader_id_and_description_section`, replace only the two frontmatter assertions (`assert "uploader_id: 42" in md` and the `uploader:`/`uploader_id:` adjacency block) with:
```python
    fm = _frontmatter(md)
    assert fm["uploader_id"] == "42"
    keys = list(fm.keys())
    assert keys.index("uploader") + 1 == keys.index("uploader_id")
```
Leave that test's body assertions (`## Description`, the H1/desc/transcript ordering) unchanged for now — Task 2 updates the transcript part.

In `test_render_markdown_omits_description_section_when_none`, replace `assert "uploader_id: " in md`:
```python
    assert _frontmatter(md)["uploader_id"] == ""
```

In `test_render_markdown_description_with_literal_dashes_and_hash_line_is_safe`, replace the exact-list frontmatter assertion (the `assert frontmatter == [ ... ]` block, including the `lines[0] == "---"` / `close_idx` lines) with:
```python
    fm = _frontmatter(md)
    assert fm["title"] == "My Title"
    assert fm["uploader_id"] == "42"
    assert fm["transcript_source"] == "whisper (test)"
```
Leave the rest of that test (the description-body assertions) unchanged for now — Task 3 strengthens it.

- [ ] **Step 6: Run the full merge suite**

Run: `python -m pytest tests/test_merge.py -q`
Expected: PASS (all tests green).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml harvest/merge.py tests/test_merge.py
git commit -m "fix(merge): emit bundle.md frontmatter via PyYAML (#11 Bug A)"
```

---

### Task 2: Transcript section nesting

**Files:**
- Modify: `harvest/merge.py` (`render_markdown` transcript block)
- Test: `tests/test_merge.py`

**Interfaces:**
- Consumes: `render_markdown` from Task 1.
- Produces: output where `## Description` / `## Transcript` / `## Danmaku` are H2 peers and transcript windows are `### [mm:ss]` (H3), mirroring danmaku windows. The no-transcript placeholder renders under a `## Transcript` heading.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_merge.py`:

```python
def test_transcript_section_nests_windows_under_h2():
    bundle = _bundle(
        transcript=Transcript(
            source="whisper", source_reason="test",
            segments=[_seg(0, 5, "hello"), _seg(80, 85, "later")],
        ),
    )
    md = render_markdown(bundle, _settings())
    lines = md.splitlines()
    assert "## Transcript" in lines
    # Windows are H3, not H2.
    assert any(l.startswith("### [") for l in lines)
    assert not any(l.startswith("## [") for l in lines)
    # Ordering: H1 < ## Transcript < first ### window.
    h1 = next(i for i, l in enumerate(lines) if l.startswith("# "))
    tr = lines.index("## Transcript")
    win = next(i for i, l in enumerate(lines) if l.startswith("### ["))
    assert h1 < tr < win


# `_bundle` and `_settings` come from the helpers added/defined in Task 1 and the existing suite.


def test_no_transcript_placeholder_under_transcript_heading():
    bundle = _bundle(
        transcript=Transcript(source="whisper", source_reason="pending", segments=[]),
        frames=[],
    )
    md = render_markdown(bundle, _settings())
    lines = md.splitlines()
    assert "## Transcript" in lines
    ph = next(i for i, l in enumerate(lines) if "no transcript yet" in l)
    assert lines.index("## Transcript") < ph
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_merge.py::test_transcript_section_nests_windows_under_h2 -q`
Expected: FAIL — `"## Transcript"` is not in the output and windows are still `## [`.

- [ ] **Step 3: Implement the restructure**

In `harvest/merge.py`, in `render_markdown`, replace the transcript block. Change from:

```python
    if not t.segments and not bundle.frames:
        lines.append("_(no transcript yet — Whisper pending)_")
        return "\n".join(lines) + "\n"

    for ch in chunk(
        t.segments, bundle.frames, window_s=settings.chunk_window_s, duration_s=bundle.duration_s
    ):
        lines.append(f"## [{_mmss(ch.start)}]")
```

to:

```python
    lines.append("## Transcript")
    lines.append("")

    if not t.segments and not bundle.frames:
        lines.append("_(no transcript yet — Whisper pending)_")
        return "\n".join(lines) + "\n"

    for ch in chunk(
        t.segments, bundle.frames, window_s=settings.chunk_window_s, duration_s=bundle.duration_s
    ):
        lines.append(f"### [{_mmss(ch.start)}]")
```

(Leave the rest of the loop body — frame OCR/caption, joined text — unchanged.)

- [ ] **Step 4: Update the one existing test that references the old `## [` transcript header**

In `test_render_markdown_emits_uploader_id_and_description_section`, change:
```python
    transcript_idx = next(i for i, l in enumerate(lines) if l.startswith("## ["))
```
to:
```python
    transcript_idx = next(i for i, l in enumerate(lines) if l == "## Transcript")
```

- [ ] **Step 5: Run the full merge suite**

Run: `python -m pytest tests/test_merge.py -q`
Expected: PASS (all green, including the two new transcript tests).

- [ ] **Step 6: Commit**

```bash
git add harvest/merge.py tests/test_merge.py
git commit -m "feat(merge): nest transcript windows under ## Transcript (#11)"
```

---

### Task 3: Neutralize untrusted body text (Bug B)

**Files:**
- Modify: `harvest/merge.py` (`_neutralize` helper + apply to body fields)
- Test: `tests/test_merge.py`

**Interfaces:**
- Consumes: `render_markdown` from Tasks 1–2.
- Produces: a module-level `_neutralize(text: str) -> str` that backslash-escapes any line whose first non-whitespace content is a `#`-run, a `---`/`***`/`___` thematic break, or a ```` ``` ````/`~~~` code fence. Applied to the H1 title, description, joined transcript text, frame `ocr`/`caption`, and each danmaku line's `text`. Forged headings/rules/fences become inert literal text.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_merge.py`:

```python
def test_description_cannot_forge_sections():
    description = (
        "Intro line.\n"
        "## [00:00]\n"
        "fake transcript segment\n"
        "## Danmaku\n"
        "### [00:00] (999 danmaku)\n"
        "- fabricated crowd line\n"
        "---\n"
        "```python\n"
        "code\n"
        "```"
    )
    bundle = _bundle(description=description)
    md = render_markdown(bundle, _settings())
    lines = md.splitlines()

    # The only real H2 sections are the tool-produced ones (no danmaku on this bundle).
    assert [l for l in lines if l.startswith("## ")] == ["## Description", "## Transcript"]
    # Forged markers are escaped and inert.
    assert "\\## [00:00]" in md
    assert "\\## Danmaku" in md
    assert "\\### [00:00] (999 danmaku)" in md
    assert "\\---" in md
    assert "\\```python" in md
    # Text is still legible.
    assert "fake transcript segment" in md
    assert "Intro line." in md


def test_neutralize_leaves_ordinary_lines_untouched():
    assert _neutralize("plain line\nsecond line") == "plain line\nsecond line"
    assert _neutralize("a colon: here and a - dash") == "a colon: here and a - dash"


def test_danmaku_line_with_embedded_newline_cannot_forge_section():
    dm = Danmaku(
        source_total=None, fetched_total=1, model=None,
        windows=[DanmakuWindow(
            start=0.0, end=75.0, total=1,
            lines=[DanmakuLine(text="nice\n## Danmaku", count=1)],
        )],
    )
    bundle = _bundle_with_danmaku(dm)
    md = render_markdown(bundle, _settings())
    # Exactly one real ## Danmaku section header; the embedded one is escaped.
    assert md.count("\n## Danmaku") == 1
    assert "\\## Danmaku" in md
```

Add the `_neutralize` import to the existing merge import line at the top of the test file:
```python
from harvest.merge import (
    HIGH_LIKE_MD_CAP,
    _neutralize,
    build_bundle,
    chunk,
    chunk_boundaries,
    render_markdown,
    write_bundle,
)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_merge.py -k "forge or neutralize" -q`
Expected: FAIL — `_neutralize` is not importable / forged markers are unescaped.

- [ ] **Step 3: Implement `_neutralize` and apply it**

In `harvest/merge.py`, add `import re` to the stdlib imports (line ~9) and add the helper below `_mmss`:

```python
# Lines whose first non-whitespace content forges harvest's section grammar: a #-run heading,
# a ---/***/___ thematic break, or a ```/~~~ code fence. bundle.md delimits sections with these
# markers rather than fenced blocks, so untrusted body text that leads with one could manufacture
# a fake ## Transcript / ## Danmaku section or close the frontmatter. We backslash-escape the
# marker so it renders as literal text and no longer matches a structural ^## / ^--- scan.
_STRUCTURAL_LINE = re.compile(r"^(\s*)(#{1,6}|-{3,}|\*{3,}|_{3,}|`{3,}|~{3,})")


def _neutralize(text: str) -> str:
    """Backslash-escape line-leading structural markdown markers, line by line (so a marker after
    an embedded newline is caught too). Ordinary prose lines pass through unchanged."""
    out = []
    for line in text.split("\n"):
        m = _STRUCTURAL_LINE.match(line)
        if m:
            i = len(m.group(1))
            line = line[:i] + "\\" + line[i:]
        out.append(line)
    return "\n".join(out)
```

Apply it in `render_markdown` at each untrusted-field interpolation:

- H1 line (from Task 1's `lines = [...]`):
```python
        f"# {_neutralize(bundle.title or bundle.id)}",
```
- Description body:
```python
        lines.append(_neutralize(bundle.description))
```
- Frame OCR / caption:
```python
            if fr.ocr:
                lines.append(f"**slide (OCR):** {_neutralize(fr.ocr)}")
            if fr.caption:
                lines.append(f"**slide (figure):** {_neutralize(fr.caption)}")
```
- Joined transcript text:
```python
        text = _neutralize("".join(s.text for s in ch.segments).strip())
```
- Danmaku lines (both promoted and ordinary):
```python
            for ln in promoted[:HIGH_LIKE_MD_CAP]:
                suffix = "" if ln.count == 1 else f" ×{ln.count}"
                lines.append(f"- \U0001F44D 「{_neutralize(ln.text)}」{suffix}")
```
```python
            for ln in ordinary[: settings.danmaku_md_cap]:
                suffix = "" if ln.count == 1 else f" ×{ln.count}"
                lines.append(f"- 「{_neutralize(ln.text)}」{suffix}")
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python -m pytest tests/test_merge.py -k "forge or neutralize" -q`
Expected: PASS.

- [ ] **Step 5: Strengthen the old safety test's body assertions**

In `test_render_markdown_description_with_literal_dashes_and_hash_line_is_safe`, the description is `"Intro line.\n---\n# Not a real heading\nTrailing line."`. Replace its body assertions (everything after the frontmatter parse from Task 1) with:
```python
    # The adversarial --- and # lines are escaped so they cannot forge structure, but the text
    # is still present and legible.
    assert "\\---" in md
    assert "\\# Not a real heading" in md
    assert "Intro line." in md
    assert "Trailing line." in md
    # The only real H2 sections are tool-produced.
    assert [l for l in md.splitlines() if l.startswith("## ")] == ["## Description", "## Transcript"]
```

- [ ] **Step 6: Run the full merge suite**

Run: `python -m pytest tests/test_merge.py -q`
Expected: PASS (all green).

- [ ] **Step 7: Full test suite sanity check**

Run: `python -m pytest -q`
Expected: PASS (no regressions elsewhere).

- [ ] **Step 8: Commit**

```bash
git add harvest/merge.py tests/test_merge.py
git commit -m "fix(merge): neutralize untrusted body text in bundle.md (#11 Bug B)"
```

---

## Notes for the implementer

- `bundle.json` and `write_bundle()` are deliberately untouched — the full, unescaped record lives there; only the human/ingestion-facing `bundle.md` is hardened.
- The `_neutralize` regex intentionally over-escapes a few benign line-leading cases (e.g. a line starting with `#hashtag`). That is safe: it renders identically in a markdown viewer and only ever adds a single backslash.
- If `python -m pytest` reports PyYAML missing in a clean env, install the dev+runtime deps: `pip install -e ".[dev]"`.
