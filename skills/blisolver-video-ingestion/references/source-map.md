# Source map

## Canonical current sources

Use these files when an operational claim needs verification in a live checkout:

| Claim | Current source |
|---|---|
| CLI verbs and flags | `blisolver/cli.py` |
| schema version and Pydantic shapes | `blisolver/schema.py` |
| runtime/env defaults | `blisolver/config.py`, `.env.example` |
| provider registry and normalized seam | `blisolver/providers/base.py` |
| bilibili acquisition | `blisolver/providers/bilibili.py`, `blisolver/player_api.py`, `blisolver/danmaku.py`, `blisolver/interactions.py` |
| YouTube acquisition and caption tiers | `blisolver/providers/youtube.py`, `blisolver/providers/youtube_autosub.py` |
| subtitle formats and parsing | `blisolver/subtitles.py` |
| current ASR backend | `blisolver/transcribe.py` |
| frame extraction and dedup | `blisolver/frames.py` |
| LM Studio/projector behavior | `blisolver/vision.py` |
| hard-subtitle OCR isolate | `blisolver/detect_hardsubs.py`, `blisolver/ocr.py`, `scripts/ocr_worker.py` |
| fusion diagnostics | `blisolver/fuse.py` |
| bundle rendering and writes | `blisolver/merge.py` |
| MCP behavior | `blisolver/mcp/server.py` |

## Tests as executable truth

The offline suite is the first place to check behavior before trusting prose. Relevant tests include
`tests/test_cli.py`, `test_probe.py`, `test_schema` coverage in merge/CLI tests, provider tests,
subtitle tests, transcription/vision/OCR tests, danmaku/interactions tests, and `test_mcp.py`.
Live-network tests are marked `live` and excluded by default.

## Historical documents

`README.md`, `SPEC.md`, and `PROTOCOL.md` remain important contract/design documents, but current
source wins when implementation has advanced. Phase plans under `docs/phase-*.md`, refactor plans,
and dated `docs/superpowers/{plans,specs}/` files explain decisions and history; they are not
runtime discovery sources.

## Known stale statements

- Older setup text describes faster-whisper/CUDA, but current `blisolver/transcribe.py` invokes
  `whisper-cli`/whisper.cpp.
- Early architecture notes may describe schema 1.0; current `blisolver/schema.py` and emitted bundles
  use schema 1.1 with per-cue provenance and `Bundle.ocr`.
- Historical bilibili-only descriptions predate the current YouTube provider and its caption policy.
- `bilibili.tv` may appear in type-level architecture, but current CLI control flow deliberately
  rejects it as deferred.
