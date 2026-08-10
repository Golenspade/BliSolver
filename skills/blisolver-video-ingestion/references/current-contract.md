# Current contract

## CLI verbs

The current CLI is:

```text
blisolver ingest <url> [flags]
blisolver probe  <url>
blisolver mcp
```

There is no bare-URL form. `probe` takes only a URL and prints one JSON `ProbeResult` line on
stdout; errors are `error: ...` on stderr with exit code 1. The portable `probe.py` adapter preserves
that machine-safe behavior.

`ingest` supports `--part`, `--all-parts`, `--force-whisper`, `--lang`, `--robust`, `--no-vision`,
`--dedup-threshold`, `--out`, `--no-frame-images`, `--danmaku`, `--interactions`, `--ocr`, and
`--force-ocr`. `--scene-threshold` is retained by the current CLI as a deprecated, ignored
compatibility flag.

## Bundle schema 1.1

`blisolver/schema.py` defines the current Pydantic contract and `SCHEMA_VERSION = "1.1"`.
`ProbeResult` carries best-effort metadata and a point-in-time `stats` snapshot. `Bundle` carries
that metadata plus:

- `platform`, `id`, `part`, `url`, and timestamps;
- `transcript`, whose `source` is `human-sub`, `auto-sub`, or `whisper`;
- `frames`, each with timestamp, phash, optional caption/OCR, and an optional bundle-relative image
  path;
- optional `danmaku` and `interactions` tracks;
- optional `ocr`, a list of `Segment`s on an independent burned-in-subtitle timeline;
- `meta` with cookie/referer supply indicators, vision model, and tool version.

Schema 1.1 added optional `Segment.source` and `Segment.confidence`. A transcript cue can carry
`human-sub`, `auto-sub`, or `whisper`; a burned-in OCR cue carries `ocr`. Legacy 1.0 cues can leave
these fields null. `Bundle.ocr` is distinct from sparse `Frame.ocr` slide text.

## Transcript provenance

The production method is separate from language:

```text
human-sub > whisper > auto-sub
```

That is the downstream authority order. Acquisition may reuse a structurally valid auto-caption to
avoid an expensive local transcription; `--force-whisper` explicitly requests the local fallback.
`source_reason`, language, model, robust mode, and any quality gate are retained in the bundle.

## Optional tracks

- `ocr: null` means hard-subtitle OCR was not requested, not configured, or found no track. When
  present, it is independent from the picked transcript.
- `danmaku: null` means the opt-in track was not run or the platform does not support it. A populated
  `Danmaku` with zero fetched records means it was requested and found nothing.
- `interactions: null` has the same requested/not-supported meaning. An empty `Interactions` object
  means the structured command-danmaku fetch ran but produced no whitelisted Vote/Grade records.
- `--no-frame-images` keeps frame timestamps, phashes, captions, and OCR in JSON while setting image
  paths to null and omitting delivered PNGs.

## Output layout

Each successful atomic part is:

```text
out/<id>-p<part>/
├── bundle.json
├── bundle.md
└── frames/                 # omitted when --no-frame-images is used
```

`bundle.md` is the primary Atlas ingestion surface: provenance frontmatter, slide/wall-clock
chunks, transcript, and optional sections. `bundle.json` is the complete precise record. JSON keeps
all danmaku lines even when Markdown applies its ordinary-line cap.

## Stable versus volatile metadata

Identity and descriptive fields such as platform, id, title, uploader, duration, publication time,
and parts are intrinsic metadata. `stats` is an engagement snapshot at `fetched_at`; do not compare
counts across probes or bundles without accounting for their timestamps. Null metadata is normal for
a successful best-effort probe.

## Platform boundary

The schema type still names `bilibili.tv` for architectural compatibility, but current `probe` and
`ingest` reject it with an explicit deferred-support error. Operationally use `bilibili.com` or
`youtube.com` only.
