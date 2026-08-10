# Architecture

## Package shape

The repository root is the Agent Plugins plugin root, so the portable package and the application
are the same tree:

```text
<plugin-root>/
├── plugin.json                     # Agent Plugins manifest
├── mcp.json                        # one stdio MCP server
├── bin/blisolver-mcp               # interpreter-resolving launcher for that server
├── blisolver/                      # the application
├── skills/blisolver-video-ingestion/
│   ├── SKILL.md
│   ├── references/
│   └── scripts/
├── scripts/ocr_worker.py           # runtime dependency of --ocr, not an entry point
└── tests/
```

This co-location is deliberate. An earlier arrangement kept a copied skill package that
reimplemented runtime discovery and restated the CLI's flags; both copies drifted from the code they
described. A skill that sits beside the application and imports it cannot fall behind in the same
way, and `tests/test_agent_plugin.py` plus `tests/test_portable_skill.py` fail when it starts to.

## Entry points

| Surface | Path | Nature |
|---|---|---|
| CLI | `blisolver/cli.py` | `ingest`, `probe`, `doctor`, `mcp` |
| MCP | `blisolver/mcp/server.py` | five tools, async job store |
| skill wrapper | `skills/*/scripts/blisolver_cli.py` | interpreter resolution plus pass-through |

The two runtime surfaces share one interpreter-resolution chain, implemented once in
`bin/blisolver-mcp` and once in `scripts/_runtime.py`, with a test asserting they agree.

## Data flow

```text
URL
 └─ resolve.extract_url ─ providers.select_provider ─ provider.resolve ─→ Canonical{platform,id,part,url}
                                                          │
                            provider.fetch_metadata ──────┴─→ SourceMetadata (normalized)
                                                          │
   ┌──────────────────────────────────────────────────────┘
   │
   ├─ decide_transcript
   │    ├─ provider.fetch_subtitle → SubtitleOutcome  (candidate walk, quality gate)
   │    └─ fallback: transcribe.download_audio → whisper-cli → Segment[]
   │
   ├─ frames.download_video → frames.extract_frames → vision.caption_frames        [--no-vision skips]
   ├─ detect_hardsubs → ocr.ocr_subtitle (isolated worker)                         [--ocr]
   ├─ provider.fetch_danmaku → danmaku.represent_danmaku                           [--danmaku]
   ├─ provider.fetch_interactions                                                  [--interactions]
   │
   ├─ fuse (only when an OCR track exists): cross-verification + hallucination diagnostics
   │
   └─ merge.build_bundle → merge.write_bundle → "<title> [<id>-p<part>]/{bundle.json,bundle.md,frames/}"
```

`process_part` returns the artifact paths it wrote; `run_parts` collects them per part; `--json`
serializes the collection. Nothing downstream reconstructs a path.

## Component responsibilities

* `resolve.py` — URL normalization and canonical identity. The `{platform, id, part}` triple is the
  atomic cached unit.
* `providers/base.py` — the seam. `Provider` protocol, `SourceMetadata`, `SubtitleOutcome`. Shared
  stages never see a raw platform response.
* `providers/bilibili.py`, `providers/youtube.py` — per-source acquisition, auth, and trust
  decisions. The only places a platform is named.
* `subtitles.py` — yt-dlp options, track candidate ordering, the censorship fallback, and BCC/SRT/VTT
  parsers.
* `quality.py` — the source- and language-aware gate that decides whether a caption is trustworthy.
* `transcribe.py` — audio download/cache and the whisper.cpp shim.
* `frames.py`, `vision.py` — periodic sampling with perceptual-hash dedup, then LM Studio captioning
  behind a projector verification that hard-stops rather than hallucinating.
* `detect_hardsubs.py`, `ocr.py` — burned-in subtitle detection and recognition, executed in a
  separate environment so vision dependencies never enter the main one.
* `fuse.py` — annotates the transcript's reason with cross-verification and ASR-hallucination
  diagnostics. Never replaces the picked transcript.
* `danmaku.py`, `interactions.py` — audience mirror and uploader widgets, both lower authority.
* `merge.py` — bundle assembly, Markdown rendering, and the delivery directory.
* `schema.py` — the Pydantic contract Atlas depends on.
* `doctor.py` — offline preflight, reading the same `Settings` the pipeline reads.
* `config.py` — settings, thresholds, tool discovery, and data-directory placement.

Names in this list are relative to `blisolver/`. The plugin root also has a `scripts/` directory
holding `ocr_worker.py`, which is separate from this skill's `scripts/`.

## Invariants

* One authoritative transcript per bundle. OCR is a parallel timeline, never a substitute.
* Provenance is load-bearing, not decoration: source, language, model, gate, and per-cue tags all
  feed downstream authority ranking.
* Platform branching stops at the provider seam.
* A per-part failure never aborts sibling parts.
* stdout is a machine channel on every verb; progress is stderr.
* Generated state lives in the data directory, which a plugin client can point outside the package
  so it survives an update.
