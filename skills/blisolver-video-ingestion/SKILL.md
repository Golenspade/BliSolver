---
name: blisolver-video-ingestion
description: Operate the BliSolver blisolver pipeline for bilibili.com and YouTube videos, diagnose its runtime, run probe or ingest, and inspect or validate Atlas bundle outputs. Use when an Agent needs video acquisition, caption-versus-Whisper decisions, frame/vision/OCR processing, danmaku or interaction provenance, provider troubleshooting, or schema-1.1 bundle handling; do not use it for downstream summarization or entity extraction.
license: MIT
compatibility: Requires Python 3.11+ and either a BliSolver checkout or an installed blisolver command; media stages additionally require their documented external tools and services.
metadata:
  project: BliSolver
  bundle-schema: "1.1"
  platforms: "bilibili.com, youtube.com"
---

# BliSolver video ingestion

Use this skill to operate the **BliSolver/blisolver** ingestion front-door. BliSolver acquires a video,
chooses a trustworthy original-language transcript, optionally extracts visual/OCR context and
bilibili engagement tracks, and writes an Atlas-consumable bundle. It is not the Atlas summarizer:
do not ask blisolver to summarize, extract entities, or promote danmaku into facts.

## Current truth

When documents disagree, use this order:

1. current source code and tests;
2. `PROTOCOL.md`, `SPEC.md`, and `README.md` where they agree with the code;
3. `CONTEXT.md`;
4. historical phase plans and design documents.

The current contract is schema **1.1**. It includes per-segment `source`/`confidence` and an
independent `Bundle.ocr` track. The current ASR implementation is `whisper-cli`/whisper.cpp; older
faster-whisper/CUDA descriptions are historical where they conflict with `blisolver/transcribe.py`.
`bilibili.tv` is deferred and unsupported.

## Standard workflow

Set `SKILL_ROOT` to the directory containing this `SKILL.md` (for example,
`~/.agents/skills/blisolver-video-ingestion`) and use that absolute path when invoking scripts.

1. **Locate the runtime.** Run the bundled doctor before an expensive operation:

   ```bash
   python "$SKILL_ROOT/scripts/doctor.py" --project-root /path/to/BliSolver --json
   ```

   The wrappers also discover `BLISOLVER_PROJECT_ROOT`, an ancestor checkout, or an installed
   `blisolver` command. Never put cookies or API keys on a command line.

2. **Check the URL.** Only `bilibili.com` and YouTube are supported. For a cheap metadata check:

   ```bash
   python "$SKILL_ROOT/scripts/probe.py" 'https://...' --project-root /path/to/BliSolver
   ```

   `probe.py` keeps stdout as one JSON object and sends diagnostics to stderr. A `.tv` URL must be
   stopped with the explicit deferred-platform error.

3. **Ingest deliberately.** Use the adapter for safe argument forwarding:

   ```bash
   python "$SKILL_ROOT/scripts/ingest.py" 'https://...' --project-root /path/to/BliSolver
   ```

   Before running a costly job, inspect the command with `--dry-run`. Common controls are:

   | Need | Flag |
   |---|---|
   | select a bilibili part | `--part N` |
   | process all bilibili parts | `--all-parts` |
   | force local ASR | `--force-whisper` |
   | pin language | `--lang CODE` (defaults to user's conversation language) |
   | avoid repetition loops | `--robust` |
   | skip frames/vision | `--no-vision` |
   | omit delivered PNGs | `--no-frame-images` |
   | enable burned-in subtitle OCR | `--ocr` (and `--force-ocr` when detection misses) |
   | mirror bilibili danmaku | `--danmaku` |
   | capture command-danmaku votes/grades | `--interactions` |

   **User-Language Alignment & Probe Decisioning:**
   - Always align `--lang CODE` to the language of the user in the active conversation (e.g. `--lang en` for an English conversation, `--lang zh` for Chinese).
   - `probe.py` returns `original_language` (the video's spoken language) and `available_subtitles` (all available subtitle tracks on the platform). Use this metadata to decide whether to fetch a platform track or trigger `--force-whisper --lang <original_language>`.

4. **Inspect and validate the result.** A successful part produces `out/<id>-p<part>/` with
   `bundle.json`, `bundle.md`, and optionally `frames/`:

   ```bash
   python "$SKILL_ROOT/scripts/inspect_bundle.py" /path/to/out/<id>-p<part>
   python "$SKILL_ROOT/scripts/validate_bundle.py" /path/to/out/<id>-p<part> \
       --project-root /path/to/BliSolver
   ```

   `bundle.md` is the primary Atlas reading surface; `bundle.json` is the precise backing record.
   A `null` optional track means it was not requested or not supported. An empty populated track
   means it was requested but found no records.

## Bundled script inventory

The portable skill contains five public entry points and one internal helper:

| File | Status | Purpose |
|---|---|---|
| `scripts/doctor.py` | public | Offline runtime/dependency/configuration report |
| `scripts/probe.py` | public | JSON-safe wrapper around `blisolver probe` |
| `scripts/ingest.py` | public | Safe flag-forwarding wrapper around `blisolver ingest` |
| `scripts/inspect_bundle.py` | public | Local compact bundle summary |
| `scripts/validate_bundle.py` | public | Schema 1.1, path, and artifact validation |
| `scripts/_common.py` | internal | Runtime discovery and safe path/subprocess helpers; do not call directly |

These are the only scripts shipped by this portable skill. The BliSolver checkout also contains
project-local helpers such as `scripts/ocr_worker.py`; that OCR worker is a runtime dependency for
`--ocr`, not a skill entry point. `scripts/make_paragraphs.py` and the pre-existing
`scripts/download_video.py` are not part of the portable skill API.

## Authority and safety rules

- Transcript authority is `human-sub > whisper > auto-sub`. Acquisition may choose auto-sub over
  Whisper for cost, but provenance remains visible and `--force-whisper` is the override.
- OCR is an independent burned-in subtitle timeline, not a replacement for the picked transcript.
- Danmaku and interactions are lower-authority audience/reception signals. A danmaku author flag is
  an unverified hash hint; a Vote question is structural uploader framing, not a content fact.
- Never expose `SESSDATA`, `LMSTUDIO_API_KEY`, browser cookies, or full environment dumps.
- Do not use `shell=True` or interpolate user URLs into shell strings. The bundled wrappers use
  argument arrays and preserve child exit codes.

## Load references as needed

| Task | Read |
|---|---|
| component map and data flow | `references/architecture.md` |
| exact CLI/schema/output contract | `references/current-contract.md` |
| provider, auth, and subtitle decisions | `references/provider-guide.md` |
| stage behavior and caching | `references/pipeline-stages.md` |
| setup, recovery, and QA | `references/operational-runbook.md` |
| terminology and authority | `references/domain-glossary.md` |
| source/test locations and stale docs | `references/source-map.md` |

The skill package intentionally does not vendor the `blisolver/` application, environments, models,
media, caches, outputs, or secrets. Install/copy the directory as a skill, then point its scripts at
the target BliSolver checkout or installed CLI.
