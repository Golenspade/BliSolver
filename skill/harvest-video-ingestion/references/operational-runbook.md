# Operational runbook

## Preflight

Run from any directory with the skill scripts:

```bash
python skill/harvest-video-ingestion/scripts/doctor.py \
  --project-root /path/to/BliSolver --json
```

The doctor is offline. It checks Python, harvest discovery, ffmpeg, deno/node, whisper-cli, vision
configuration, optional OCR isolation, and provider-auth signals. Warnings mean a stage may be
unavailable; they do not expose the corresponding secret.

Required setup depends on the requested stage:

- Python 3.11+ and the BliSolver spine installation for probe/provider code;
- ffmpeg for audio/video processing;
- a working whisper-cli/model for Whisper fallback;
- deno or node for reliable YouTube yt-dlp extraction;
- LM Studio with the configured vision model and projector for vision;
- the isolated OCR environment only when `--ocr` is needed.

Install the project spine with the repository's normal `pip install -e .` flow. Do not infer the
current ASR backend from the historical faster-whisper optional extra; inspect `harvest/transcribe.py`
and the doctor result.

## Cheap probe

```bash
python skill/harvest-video-ingestion/scripts/probe.py \
  'https://www.youtube.com/watch?v=...' --project-root /path/to/BliSolver \
  > probe.json
```

Parse only `probe.json`. The command performs provider metadata acquisition but no media download.
A nonzero exit means there is no trustworthy probe record. Treat null metadata fields in a successful
probe as normal.

## Ingest recipes

Caption-first default:

```bash
python skill/harvest-video-ingestion/scripts/ingest.py \
  'https://www.bilibili.com/video/BV...' --project-root /path/to/BliSolver
```

Force local ASR and skip visual services:

```bash
python skill/harvest-video-ingestion/scripts/ingest.py \
  'https://...' --project-root /path/to/BliSolver \
  --force-whisper --no-vision --out /path/to/out
```

Enable burned-in OCR while keeping frame vision disabled:

```bash
python skill/harvest-video-ingestion/scripts/ingest.py \
  'https://...' --project-root /path/to/BliSolver \
  --no-vision --ocr
```

Run bilibili audience tracks independently:

```bash
python skill/harvest-video-ingestion/scripts/ingest.py \
  'https://www.bilibili.com/video/BV...' --project-root /path/to/BliSolver \
  --danmaku --interactions
```

Use `--dry-run` before any costly command. Use `--part N` for one bilibili part; use `--all-parts`
only when the extra acquisition cost is intended.

## Bundle QA

```bash
python skill/harvest-video-ingestion/scripts/inspect_bundle.py out/BV...-p1 --pretty
python skill/harvest-video-ingestion/scripts/validate_bundle.py \
  out/BV...-p1 --project-root /path/to/BliSolver
```

Validation checks Pydantic schema 1.1, `bundle.md`, frame path containment, and referenced image
existence. `--no-frame-images` is valid because frame paths may be null. Inspection reports counts,
not transcript/danmaku bodies.

## Failure matrix

| Symptom | First action |
|---|---|
| no checkout/CLI found | pass `--project-root` or set `HARVEST_PROJECT_ROOT` |
| `.tv` unsupported error | use a `bilibili.com` URL; `.tv` is deferred |
| YouTube missing title/subtitles | install/find deno or node; keep public extraction cookie-free first |
| bilibili 403/412 or no captions | verify logged-in browser/`SESSDATA` without printing it and preserve referer behavior |
| no Whisper fallback | check `whisper-cli`, `HARVEST_WHISPER_MODEL`, and ffmpeg |
| vision projector check fails | load/bind the model's mmproj in LM Studio; do not bypass by trusting captions |
| OCR skipped | create `.ocr-venv` worker setup or use `--force-ocr` only after setup is present |
| bundle invalid | inspect the JSON report; repair the producing run rather than editing the bundle silently |
| danmaku ignored | set `HARVEST_DANMAKU_MODEL`; remember the track is bilibili-only and opt-in |

## Secret handling

Use `.env`/environment and browser profiles according to the project configuration. Never put
`SESSDATA`, LM Studio tokens, or cookie text into URLs, arguments, logs, references, test fixtures, or
skill documentation. The portable wrappers pass the environment to the child process but never dump
it.
