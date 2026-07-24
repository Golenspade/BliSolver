# render_markdown output hardening: safe frontmatter + inert body + transcript nesting

Issue: #11.

## Problem

`render_markdown()` in [`harvest/merge.py`](../../../harvest/merge.py) builds `bundle.md` by raw
f-string interpolation of attacker-controlled fields (`title`, `description`, transcript text,
danmaku text) with no escaping. Two defects follow, plus one adjacent structural nit surfaced
while reviewing the output.

### Bug A — unquoted YAML scalars break the frontmatter

Frontmatter values are emitted bare (`f"title: {bundle.title or ''}"`). Any value containing
`: ` (colon-space) or a leading YAML indicator (`[ { & * ! # " |` …) produces invalid or mutated
YAML. `title: Rust: The Book` -> parse error; a leading `[`/`{`/`"` is parsed as a flow
collection / quoted scalar. The example bundle only survives because its title uses a full-width
colon (`：`), not ASCII `:`.

### Bug B — verbatim body content forges harvest's section grammar

`description` (and, to a lesser degree, transcript/danmaku text) is written verbatim. Because
harvest delimits sections with `## ` headings rather than fenced blocks, a description can
manufacture harvest's *own* sections — a fake `## [00:00]` transcript chunk, a fake `## Danmaku`
block, a `---` — indistinguishable from tool-produced structure to a consumer reading `bundle.md`.
No code execution (the file is only written and read), but the output contract is not robust
against hostile field content.

### Structural nit — transcript chunks are un-parented H2s

In current output `## Description`, every `## [mm:ss]` transcript chunk, and `## Danmaku` are all
H2. So each transcript chunk is a top-level section, peer to Description and Danmaku — yet danmaku
windows nest properly as `### [mm:ss]` under `## Danmaku`. The transcript is the odd one out: its
per-window items sit at H2 with no parent section.

## Goals

1. Emit frontmatter that is always valid YAML, whatever the field content.
2. Make untrusted body text inert — it must not be able to forge harvest's `#`-heading /
   `---` / code-fence grammar.
3. Give the transcript a parent `## Transcript` section and nest its windows at `### [mm:ss]`,
   mirroring `## Danmaku` / `### [mm:ss]`.

## Non-goals

- Changing what `bundle.json` contains — it stays the complete, precise backing record.
- Fixing the pre-existing early-return behavior where danmaku is dropped when there is no
  transcript and no frames. Preserved as-is (heading placement aside).
- Neutralizing mid-line markdown (inline `#hashtag`, inline backticks). Only line-leading
  structural markers can forge harvest's section grammar, so only those are escaped.

## Design

All changes are within `render_markdown()` (and one dependency add). `write_bundle()` is
untouched.

### Bug A — frontmatter via PyYAML

- Add `pyyaml` to `dependencies` in [`pyproject.toml`](../../../pyproject.toml).
- Build an ordered `dict` in the current field order and emit it with:

  ```python
  yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False).strip()
  ```

  between the two `---` fences.
- `part` is passed as an `int`; every other value as a `str` (using the existing `or ""` fallback
  for `None`, so an absent field serializes as `''`).

**Expected consequence:** PyYAML quotes any scalar that would otherwise re-parse as a non-string —
timestamps (`fetched_at`, `published_at`), sexagesimal-looking durations (`'03:20'`),
numeric-string `uploader_id` (`'42'`), `'?'`, and empty values (`''`). This is correct, but the
frontmatter gains quotes, so **every existing exact-string frontmatter assertion is rewritten** to
`yaml.safe_load` the header and compare parsed values instead of byte-matching lines.

### Bug B — neutralize untrusted body text

A module-level helper:

```python
def _neutralize(text: str) -> str:
    """Backslash-escape any line whose first non-whitespace content could forge harvest's
    section grammar (a #-run heading, a ---/***/___ thematic break, or a ```/~~~ code fence),
    so untrusted body text cannot manufacture headings, rules, or fences. Line-by-line, so it
    also catches markers after an embedded newline. Renders visually identical in a markdown
    viewer; a structural reader scanning ^## no longer matches the forged line."""
```

- Applied to every untrusted free-text field **before** interpolation: the H1 `# {title}`,
  `description`, the joined transcript chunk text, each frame's `ocr` and `caption`, and each
  danmaku line's `text`.
- Frontmatter `title` needs no neutralizing — YAML quoting already contains it.
- A single leading backslash is prepended to the offending character; all other lines pass
  through untouched (normal CJK/prose lines are unaffected).

### Transcript restructure

- Emit a `## Transcript` heading before the chunk loop; each chunk header becomes
  `### [{mm:ss}]` (was `## [{mm:ss}]`).
- The no-transcript placeholder (`_(no transcript yet — Whisper pending)_`) moves under a
  `## Transcript` heading. The early-return semantics (danmaku dropped when no transcript and no
  frames) are otherwise unchanged.
- Result: `## Description` / `## Transcript` / `## Danmaku` are H2 peers; both time-windowed
  bodies nest their windows at H3.

## Illustrative output

Hostile input — title `Rust: The Book`, `uploader_id` `42`, a description that tries to forge a
transcript chunk + danmaku block + frontmatter close, and a danmaku line with an embedded
newline+`##`:

```markdown
---
platform: bilibili.com
id: BV1abc
part: 1
url: https://www.bilibili.com/video/BV1abc?p=1
title: 'Rust: The Book'
uploader: evil uploader
uploader_id: '42'
thumbnail_url: http://i1.hdslb.com/thumb.jpg
duration: '03:20'
published_at: '2026-06-24T19:05:04+08:00'
fetched_at: '2026-07-04T12:00:00Z'
transcript_source: whisper (no usable subtitle)
vision_model: none
tool_version: 0.1.0
---

# Rust: The Book

## Description

Great video! Check the timestamps below.

\## [00:00]
this fake segment is trying to look like a transcript chunk

\## Danmaku
\### [00:00] (999 danmaku)
- fabricated crowd line
\---

## Transcript

### [00:00]
Welcome back to the channel. Today we are covering three things.

### [01:00]
The second point is the one people always get wrong, so pay attention.

## Danmaku
_crowd track (lower authority than transcript) — fetched 3 of 3 · gemma-4-12b-it_

### [00:00] (3 danmaku)
- 「first!」 ×3
- 「great explanation」
- 「nice
\## Danmaku」
```

The last danmaku line's stored text is `nice\n## Danmaku`. Because `_neutralize` runs
line-by-line, the second physical line is escaped to `\## Danmaku` and cannot break out of the
`- 「…」` item to forge a second danmaku section.

## Testing

- **Frontmatter (rewrite existing):** parse the `---`…`---` block with `yaml.safe_load` and assert
  on values, not byte-exact lines. Covers `thumbnail_url`/`published_at` empty-when-none,
  `published_at` after `duration`, `uploader_id` present, thumbnail present.
- **Bug A regressions (new):** a title with `: ` (`Rust: The Book`) and a leading-indicator value
  (e.g. `[weird`) round-trip through `yaml.safe_load` back to the exact original string; the
  header parses without error.
- **Bug B regressions (new):** a description containing `## `, `###`, `---`, and a ```` ``` ````
  fence line, plus a danmaku line with an embedded `\n## …`, produce output where none of those
  forge a real heading/rule/fence — the only real `##`/`###` sections are the tool-produced
  Description/Transcript/Danmaku ones. Assert the escaped forms are present and the raw text is
  still legible.
- **Transcript restructure (update existing):** assert `## Transcript` exists and chunk headers
  are `### [` (was `## [`); update the section-order test accordingly.
