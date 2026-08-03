# Architecture

## Purpose

`blisolver` is the ingestion front-door for Atlas. It starts with a supported video URL and ends with
a self-contained `out/<id>-p<part>/` delivery directory. BliSolver produces a timeline-aligned
original-language transcript and visual notes; downstream Atlas performs interpretation,
summarization, and entity extraction.

## Current module map

- `blisolver/cli.py` — parses `ingest`, `probe`, and `mcp`; orchestrates each selected part.
- `blisolver/providers/base.py` — `Canonical`, normalized `SourceMetadata`, `SubtitleOutcome`,
  provider protocol, and registry.
- `blisolver/providers/bilibili.py` — bilibili URL resolution, metadata, subtitles, danmaku, and
  command-danmaku interactions.
- `blisolver/providers/youtube.py` — YouTube yt-dlp metadata and original-language caption selection.
- `blisolver/subtitles.py` — yt-dlp options and BCC/SRT/VTT subtitle parsers.
- `blisolver/probe.py` — maps normalized provider metadata to `ProbeResult`.
- `blisolver/transcribe.py` — audio download/cache plus the current `whisper-cli`/whisper.cpp SRT
  transcription shim.
- `blisolver/frames.py` — video download/cache, periodic ffmpeg sampling, and phash deduplication.
- `blisolver/vision.py` — LM Studio OpenAI-compatible image captioning and projector verification.
- `blisolver/detect_hardsubs.py`, `blisolver/ocr.py`, `scripts/ocr_worker.py` — optional isolated
  burned-in subtitle OCR.
- `blisolver/fuse.py` — adds transcript/OCR cross-verification diagnostics.
- `blisolver/danmaku.py` — fixed-window faithful danmaku representation when explicitly requested.
- `blisolver/interactions.py` — structured Vote/Grade decoding without an LLM.
- `blisolver/merge.py` — timeline chunking, bundle construction, Markdown rendering, and delivery.
- `blisolver/schema.py` — Pydantic schema 1.1, the machine-facing bundle contract.
- `blisolver/mcp/server.py` — MCP tools around probe, asynchronous ingest, transcript, timeline, and
  visual-context reads.

## Execution flow

```text
URL
  -> provider registry / Canonical(platform, id, part)
  -> normalized metadata and part enumeration
  -> subtitle decision: human-sub / auto-sub / Whisper
  -> optional audio download and whisper.cpp
  -> optional video download -> periodic frames -> phash dedup -> LM Studio vision
  -> optional hard-subtitle detection/OCR on its own timeline
  -> optional bilibili danmaku and/or command-danmaku interactions
  -> fusion diagnostics
  -> bundle.json + bundle.md + optional frames/
```

A bilibili multi-part URL is decomposed into isolated single-part runs. `--all-parts` continues after
an individual part failure and reports the failed part at the end. YouTube v1 uses part 1.

## Seams and caching

The provider owns source-specific acquisition and authentication. Downstream stages consume
normalized metadata and never need platform-specific subtitle/API shapes. Heavy local artifacts stay
in the modular monolith because the pipeline is local-file and GPU bound; LM Studio is the one
intentionally external service boundary.

Stage caches are keyed by video identity plus stage parameters. Flags such as `--force-whisper`,
`--robust`, language, OCR knobs, and frame dedup settings must not silently reuse an incompatible
result.

## MCP boundary

`blisolver mcp` runs the stdio MCP server. The server exposes cheap `probe_video`, asynchronous
`extract_transcript`, and polling reads for transcript, unified timeline, and visual context. It
reuses the same CLI/pipeline semantics; it is an Agent-facing transport, not a second ingestion
implementation.

## What blisolver is not

BliSolver should not summarize a lecture, infer entities, classify audience sentiment, or turn
engagement counts into claims about video content. Those judgments belong to Atlas, with the bundle's
provenance and authority signals available as input.
