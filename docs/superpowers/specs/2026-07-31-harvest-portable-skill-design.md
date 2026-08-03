# BliSolver Portable Skill Design

**Status:** Implemented

**Date:** 2026-07-31

## Goal

Package the current BliSolver/`harvest` capability as a portable Agent Skill that teaches another Agent how to operate the video-ingestion pipeline, diagnose its environment, and consume or validate its Atlas bundles without copying the application source into the skill.

## Scope and boundary

The published artifact is a self-contained skill directory:

```text
harvest-video-ingestion/
├── SKILL.md
├── LICENSE.txt
├── references/
│   ├── architecture.md
│   ├── current-contract.md
│   ├── provider-guide.md
│   ├── pipeline-stages.md
│   ├── operational-runbook.md
│   ├── domain-glossary.md
│   └── source-map.md
└── scripts/
    ├── _common.py
    ├── doctor.py
    ├── probe.py
    ├── ingest.py
    ├── inspect_bundle.py
    └── validate_bundle.py
```

The skill does **not** vendor:

- the `harvest/` Python package;
- `.venv/`, `.ocr-venv/`, downloaded models, browser profiles, or cookies;
- `cache/`, `out/`, raw videos, frames, transcripts, or other generated artifacts;
- `.env` or any secret-bearing configuration;
- the untracked `scripts/download_video.py` helper.

The skill therefore remains small and portable while honestly documenting its runtime dependency on an installed `harvest` command or a BliSolver checkout.

## Current truth versus historical documents

The skill will state this precedence explicitly:

1. current source code and tests;
2. `PROTOCOL.md`, `SPEC.md`, and `README.md` when consistent with the code;
3. `CONTEXT.md`;
4. historical phase plans and prior design documents.

Important current facts to preserve:

- CLI verbs are `harvest ingest`, `harvest probe`, and `harvest mcp`.
- The current bundle schema is `1.1`.
- `Segment.source`/`Segment.confidence` and `Bundle.ocr` are part of schema 1.1.
- Supported ingestion platforms are `bilibili.com` and YouTube; `bilibili.tv` remains deferred and must fail loudly rather than be treated as supported.
- The current transcription implementation shells out to `whisper-cli`/whisper.cpp and parses SRT. Older README/spec text that describes faster-whisper/CUDA is historical wherever it conflicts with `harvest/transcribe.py` and current tests.
- OCR is an optional isolated worker in `scripts/ocr_worker.py` and `.ocr-venv`.
- Vision uses the LM Studio OpenAI-compatible endpoint and requires the projector nonce check before captioning.
- Danmaku and command-danmaku interactions are independent optional bilibili tracks.

## User-facing capability

The skill triggers when an Agent needs to:

- preflight or ingest a bilibili.com or YouTube URL;
- choose or explain caption reuse versus Whisper fallback;
- configure or diagnose ffmpeg, JavaScript runtime, whisper-cli, LM Studio, OCR, or provider authentication;
- inspect a produced `bundle.json`/`bundle.md` pair;
- validate schema, artifact paths, provenance, and optional tracks;
- understand the boundary between harvest acquisition and Atlas downstream interpretation.

It must not encourage harvest to summarize, extract entities, or treat danmaku/engagement as authoritative video facts; those are downstream or lower-authority signals.

## Script interfaces

All scripts are non-interactive and use argument lists rather than shell interpolation.

### `doctor.py`

```text
python scripts/doctor.py [--project-root PATH] [--json]
```

Reports a machine-readable check list without probing remote services or printing secret values. Checks include Python version, harvest import/CLI availability, ffmpeg, deno/node, whisper-cli, vision configuration, optional OCR isolate, and provider-auth configuration. Missing optional stage dependencies are warnings; missing core runtime pieces are errors.

### `probe.py`

```text
python scripts/probe.py URL [--project-root PATH]
```

Delegates to `harvest probe URL`, verifies that successful stdout is a single JSON object, preserves diagnostics on stderr, and returns the child exit code. It is safe to pipe stdout to a JSON parser.

### `ingest.py`

```text
python scripts/ingest.py URL [harvest-ingest-flags] [--project-root PATH] [--dry-run]
```

Exposes the current ingest flags (`--part`, `--all-parts`, `--force-whisper`, `--lang`, `--robust`, `--no-vision`, `--dedup-threshold`, `--out`, `--no-frame-images`, `--danmaku`, `--interactions`, `--ocr`, and `--force-ocr`). It delegates to the real CLI, preserves long-running progress output, and supports `--dry-run` for safe command inspection.

### `inspect_bundle.py`

```text
python scripts/inspect_bundle.py BUNDLE_OR_DIRECTORY [--pretty]
```

Reads only local files and emits a compact summary: identity, schema, transcript provenance and segment count, OCR count, frame/caption/image counts, danmaku windows/lines, interaction counts, and missing referenced images. It never prints transcript or danmaku bodies by default.

### `validate_bundle.py`

```text
python scripts/validate_bundle.py BUNDLE_OR_DIRECTORY [--project-root PATH]
```

Reads `bundle.json` and `bundle.md`, validates the JSON with the current `harvest.schema.Bundle` model, enforces schema 1.1, rejects frame path traversal, and reports missing referenced frame images. It emits a JSON result and exits 0 for valid bundles, 1 for invalid bundles, and 2 for usage/runtime failures.

## Runtime discovery

`_common.py` will resolve the runtime in this order:

1. explicit `--project-root`;
2. `HARVEST_PROJECT_ROOT`;
3. the current directory or an ancestor containing both `harvest/` and `pyproject.toml`;
4. an installed `harvest` executable on `PATH`.

For a source checkout, wrappers invoke `[sys.executable, "-m", "harvest.cli", ...]` with the checkout as cwd and on `PYTHONPATH`. For an installed deployment, they invoke the `harvest` executable. No wrapper assumes the skill itself is inside the BliSolver repository.

## Security and reliability rules

- Never log `SESSDATA`, API keys, cookie contents, browser profile paths containing secrets, or full environment dumps.
- Use `subprocess.run` argument arrays, never `shell=True`.
- Keep child stdout/stderr semantics explicit; `probe.py` must not contaminate JSON stdout.
- Treat URLs and bundle paths as untrusted input.
- Resolve frame references under the bundle directory and reject traversal outside it.
- Do not make network calls from `doctor.py`, `inspect_bundle.py`, or `validate_bundle.py`.
- Preserve upstream exit codes for `probe.py` and `ingest.py`; do not silently convert a failed ingest into success.
- Do not mutate existing bundles during inspection or validation.

## Progressive disclosure

`SKILL.md` stays short and operational. Detailed information is loaded on demand:

| Need | Reference |
|---|---|
| Understand components or data flow | `references/architecture.md` |
| Read the exact CLI/schema/provenance contract | `references/current-contract.md` |
| Select a provider or debug subtitles/auth | `references/provider-guide.md` |
| Understand transcript, frames, vision, OCR, fusion, danmaku, and interactions | `references/pipeline-stages.md` |
| Run setup, preflight, recovery, and validation safely | `references/operational-runbook.md` |
| Clarify Atlas/harvest terminology and authority | `references/domain-glossary.md` |
| Locate current facts in source files and tests | `references/source-map.md` |

References are directly linked from `SKILL.md`; they do not depend on a deep chain of references.

## Validation and acceptance criteria

The finished artifact must satisfy all of the following:

1. `SKILL.md` has valid frontmatter with `name: harvest-video-ingestion` and a trigger-oriented `description`.
2. The package passes `skills-ref validate` when that validator is installed.
3. Every referenced file exists and all links are relative to the skill root.
4. Each script supports `--help`; local-only scripts work without network access.
5. Script tests cover runtime discovery, probe JSON discipline, ingest dry-run flag forwarding, bundle summary, schema/path validation, and secret-safe doctor output.
6. The existing project suite remains green (`343 passed, 4 deselected` at the baseline; the final count may increase with skill tests).
7. The only pre-existing untracked file, `scripts/download_video.py`, remains untouched and unstaged.
