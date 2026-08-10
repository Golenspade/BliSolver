# CLI contract

Verified against `blisolver/cli.py`, `blisolver/schema.py`, and `blisolver/merge.py`. When this file
and the source disagree, the source wins and this file is a bug.

## Verbs

```text
blisolver ingest <url> [flags]
blisolver probe  <url>
blisolver doctor [--json]
blisolver mcp
```

There is no bare-URL form; a URL without a verb is an argument error.

Reach these through `scripts/blisolver_cli.py`, which resolves an interpreter that owns the
dependencies and then forwards arguments verbatim. The wrapper adds exactly two options of its own,
both of which must precede the verb: `--plugin-root PATH` and `--show-command`.

## Stream discipline

| Verb | stdout | stderr |
|---|---|---|
| `probe` | one `ProbeResult` JSON object | errors |
| `doctor` | one JSON object with `--json`, otherwise a grouped report | nothing |
| `ingest` | one result envelope with `--json`, otherwise **nothing** | all progress, warnings, notes |
| `mcp` | JSON-RPC only | server logs |

Progress used to share stdout with machine output. It no longer does, and that is load-bearing: it
is what makes `ingest --json` parseable and what keeps the MCP stdio transport clean.

## `ingest` flags

| Flag | Effect |
|---|---|
| `--part N` | 1-based part index; defaults to the part in the URL |
| `--all-parts` | enumerate and run every part, isolating per-part failure |
| `--force-whisper` | skip subtitle acquisition entirely, always run local ASR |
| `--robust` | disable ASR context carry-over (whisper.cpp `--max-context 0`) to break repetition loops |
| `--no-vision` | skip video download, frame sampling, and captioning |
| `--dedup-threshold N` | perceptual-hash Hamming distance for collapsing near-duplicate frames |
| `--scene-threshold F` | **deprecated and ignored**; warns and does nothing |
| `--out PATH` | output root; defaults to the resolved data directory |
| `--no-frame-images` | keep frame metadata in JSON, omit the PNGs, set each `Frame.path` to null |
| `--lang CODE` | ASR language, and on YouTube the caption track language. Not an output language |
| `--danmaku` | bilibili audience comment mirror; needs `BLISOLVER_DANMAKU_MODEL` or it is ignored |
| `--interactions` | bilibili vote/grade widgets; independent of `--danmaku`, needs no model |
| `--ocr` | detect and OCR burned-in subtitles into an independent track |
| `--force-ocr` | with `--ocr`, skip pre-detection and always run the dense sample |
| `--json` | emit the result envelope on stdout |

## `ingest --json` envelope

```json
{
  "schema_version": "1.1",
  "platform": "bilibili.com",
  "id": "BV1...",
  "ok": true,
  "parts": [
    {
      "part": 1,
      "ok": true,
      "platform": "bilibili.com",
      "id": "BV1...",
      "url": "https://www.bilibili.com/video/BV1...",
      "title": "...",
      "bundle_dir":  "/abs/.../<title> [BV1...-p1]",
      "bundle_json": "/abs/.../bundle.json",
      "bundle_md":   "/abs/.../bundle.md",
      "frames_dir":  "/abs/.../frames",
      "transcript_source": "auto-sub",
      "transcript_language": "zh",
      "segments": 412,
      "frames": 37,
      "ocr_cues": null,
      "danmaku_windows": null,
      "interactions": null
    }
  ]
}
```

Guarantees:

* `parts` lists every attempted part, successful or not, so a partial `--all-parts` run is
  reportable rather than ambiguous.
* A successful part carries absolute `bundle_dir`, `bundle_json`, `bundle_md`. A failed part carries
  `error` and no paths.
* `frames_dir` is null under `--no-frame-images`.
* An optional track reports `null` for "not requested" and a count for "requested"; that preserves
  the same distinction the bundle schema makes between a null track and a populated-but-empty one.
* `ok` at the top level is the conjunction of the parts.
* Exit code is 1 if any part failed. URL resolution failure exits 1 with an error on stderr and
  emits no envelope.

## Output layout

```text
<out-root>/<sanitized title> [<id>-p<part>]/
├── bundle.json
├── bundle.md
└── frames/            # absent under --no-frame-images
```

`merge.write_bundle` builds the directory name from the sanitized title plus the identity triple,
falling back to `<id>-p<part>` only when the title is empty or sanitizes away. **Do not reconstruct
this path.** Take it from the envelope, or from the MCP job result. Consumers that derived
`out/<id>-p<part>/` broke silently for every titled video.

## Data directory

Generated state resolves in this order:

1. `BLISOLVER_CACHE_DIR` / `BLISOLVER_OUT_DIR` — explicit, per-directory
2. `BLISOLVER_DATA_DIR/{cache,out}` — explicit, single root
3. `PLUGIN_DATA/{cache,out}` — supplied by a conformant Agent Plugins client
4. `<plugin-root>/{cache,out}` — developer checkout default

Agent Plugins 1.0.0 §9.1 reserves `PLUGIN_DATA` for state that must outlive a plugin update, which
is why bundles belong there rather than under the package. `mcp.json` sets `BLISOLVER_DATA_DIR` to
`${PLUGIN_DATA}` so the placement is visible in the manifest instead of being an ambient accident.

## Bundle schema 1.1

`blisolver/schema.py` is the contract; `SCHEMA_VERSION` is the version to compare against, and
`validate_bundle.py` reads it from there rather than pinning a literal.

`Bundle` carries identity and metadata plus:

* `transcript` — the single authoritative picked transcript. `source` is `human-sub`, `auto-sub`, or
  `whisper`; `source_reason` is the human-readable decision and is promoted into the `bundle.md`
  header; `language` is the language actually delivered; `quality_gate` is populated only when a
  subtitle track was evaluated.
* `ocr` — burned-in subtitle cues on an independent timeline, or null. Distinct from `Frame.ocr`.
* `frames` — timestamp, phash, optional caption/OCR, and a bundle-relative image path or null.
* `danmaku`, `interactions` — bilibili-only opt-in tracks, or null.
* `original_language`, `available_subtitles` — what the platform reported.
* `meta` — cookie/referer supply indicators, vision model, tool version.

`Segment.source` and `Segment.confidence` are per-cue provenance, both nullable on legacy 1.0
bundles. A transcript cue carries `human-sub`/`auto-sub`/`whisper`; a burned-in cue carries `ocr`.

### Reading language honestly

`transcript.language` is the language of the delivered text. `original_language` is what the
platform says was spoken. They can differ: bilibili's Chinese ASR track is sometimes returned
redacted, and acquisition then falls through to a foreign-language track. `source_reason` says
`language proxy` when that happened and names the rejected track and the marker that triggered it.

Compare the two fields before treating transcript text as the speaker's own words. `Transcript
.source` describes *how* the text was produced, never *what language it is in*.

## Optional track semantics

* `ocr: null` — not requested, not configured, or no burned-in track detected.
* `danmaku: null` — not requested, or the platform has no such concept. A populated `Danmaku` with
  zero fetched records means requested and found nothing.
* `interactions: null` — same distinction. An empty `Interactions` means the fetch ran and no
  whitelisted Vote/Grade records existed.

## Stable versus volatile metadata

Platform, id, title, uploader, duration, publication time, and part count are intrinsic. `stats` is
a point-in-time snapshot as of `fetched_at`; never compare counts across bundles without accounting
for their timestamps. Null metadata on a successful probe is normal, not a failure.

## Platform boundary

The `Platform` type still names `bilibili.tv` for architectural compatibility, but `probe` and
`ingest` both reject it with an explicit deferred-support error. Operationally: `bilibili.com` and
`youtube.com` only.
