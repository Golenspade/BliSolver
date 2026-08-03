# Source map

## Canonical current sources

Use these files when an operational claim needs verification in a live checkout:

| Claim | Current source |
|---|---|
| CLI verbs and flags | `harvest/cli.py` |
| schema version and Pydantic shapes | `harvest/schema.py` |
| runtime/env defaults | `harvest/config.py`, `.env.example` |
| provider registry and normalized seam | `harvest/providers/base.py` |
| bilibili acquisition | `harvest/providers/bilibili.py`, `harvest/player_api.py`, `harvest/danmaku.py`, `harvest/interactions.py` |
| YouTube acquisition and caption tiers | `harvest/providers/youtube.py`, `harvest/providers/youtube_autosub.py` |
| subtitle formats and parsing | `harvest/subtitles.py` |
| current ASR backend | `harvest/transcribe.py` |
| frame extraction and dedup | `harvest/frames.py` |
| LM Studio/projector behavior | `harvest/vision.py` |
| hard-subtitle OCR isolate | `harvest/detect_hardsubs.py`, `harvest/ocr.py`, `scripts/ocr_worker.py` |
| fusion diagnostics | `harvest/fuse.py` |
| bundle rendering and writes | `harvest/merge.py` |
| MCP behavior | `harvest/mcp/server.py` |

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

- Older setup text describes faster-whisper/CUDA, but current `harvest/transcribe.py` invokes
  `whisper-cli`/whisper.cpp.
- Early architecture notes may describe schema 1.0; current `harvest/schema.py` and emitted bundles
  use schema 1.1 with per-cue provenance and `Bundle.ocr`.
- Historical bilibili-only descriptions predate the current YouTube provider and its caption policy.
- `bilibili.tv` may appear in type-level architecture, but current CLI control flow deliberately
  rejects it as deferred.
