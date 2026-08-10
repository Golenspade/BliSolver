---
name: blisolver-video-ingestion
description: Operate the BliSolver ingestion pipeline for bilibili.com and YouTube videos - diagnose the runtime, probe metadata cheaply, run an ingest, and inspect or validate the resulting Atlas bundle. Use when an Agent needs video acquisition, caption-versus-Whisper decisions, frame/vision/OCR context, danmaku or interaction provenance, provider troubleshooting, or schema-1.1 bundle handling. Do not use it for downstream summarization or entity extraction.
license: MIT. LICENSE.txt has complete terms.
compatibility: Requires Python 3.11+ and a Python environment holding BliSolver's dependencies. Media stages additionally require ffmpeg, whisper.cpp with a GGML model, a JavaScript runtime for YouTube, LM Studio for vision, and an isolated OCR environment.
metadata:
  project: BliSolver
  bundle-schema: "1.1"
  platforms: "bilibili.com, youtube.com"
  plugin-format: "Agent Plugins 1.0.0"
---

# BliSolver video ingestion

BliSolver acquires a video, picks a trustworthy transcript, optionally extracts visual/OCR context
and bilibili engagement tracks, and writes an Atlas-consumable bundle. It is an acquisition and
normalization boundary, not the Atlas summarizer: do not ask it to summarize, extract entities, or
promote danmaku into facts.

This skill ships inside the BliSolver plugin, beside the application it drives. `blisolver/` is
three directories up from `scripts/`, so the instructions below describe code you can read.

## Establish the runtime first

Every command runs through one wrapper that resolves a Python holding BliSolver's dependencies.
Do not invoke `python -m blisolver.cli` yourself and do not assume the interpreter you happen to
have is the right one — a bare `python3` typically cannot import the package.

```bash
SCRIPTS="$(dirname "$(dirname "$0")")/scripts"   # or the absolute path to this skill's scripts/
python3 "$SCRIPTS/blisolver_cli.py" doctor
```

`blisolver_cli.py` forwards every argument after its own to the CLI untouched, so any flag the
application accepts works here. Add `--show-command` to print the resolved child command as JSON
and exit without running it — do this before a job that costs minutes.

Interpreter resolution order: `$BLISOLVER_PYTHON`, `$PLUGIN_DATA/venv/bin/python`,
`<plugin-root>/.venv/bin/python`, then an installed `blisolver` on PATH. When none exists the
wrapper exits 2 and prints how to create one.

## Diagnose before spending

`doctor` is offline and cheap. Run it before any first ingest in an unfamiliar environment.

```bash
python3 "$SCRIPTS/blisolver_cli.py" doctor --json
```

Each check names the `stage` it gates, so a warning tells you what is unavailable rather than
whether "something" is wrong:

| stage | checks | what a warning costs you |
|---|---|---|
| `core` | python, interpreter, plugin-manifest, ffmpeg, aria2c, javascript-runtime, cache-dir, out-dir | ffmpeg missing is fatal for every media stage; no JS runtime degrades YouTube extraction |
| `auth` | provider-auth | a cold browser profile degrades every bilibili video to Whisper |
| `transcript` | whisper-cli, whisper-model | the GGML weights are checked separately from the binary; a missing model fails *after* the audio download |
| `vision` | vision-model | frame captioning cannot run; pass `--no-vision` |
| `ocr` | ocr-isolate | `--ocr` degrades to a no-op |
| `danmaku` | danmaku-model | `--danmaku` is accepted and then silently ignored |

Exit code is 0 when nothing is blocking (warnings included) and 1 only when a `core` check failed.
Credentials are reported as configured or not; values are never printed.

## Check the URL cheaply

Only `bilibili.com` and YouTube are supported. `bilibili.tv` is deferred and rejected with an
explicit error.

```bash
python3 "$SCRIPTS/blisolver_cli.py" probe 'https://...'
```

`probe` prints one `ProbeResult` JSON object on stdout and sends errors to stderr with exit 1. It
fetches metadata only — no media. Use `duration_s`, `parts`, `original_language`, and
`available_subtitles` to decide whether an ingest is worth its cost and which acquisition path to
expect.

## Ingest deliberately

```bash
python3 "$SCRIPTS/blisolver_cli.py" ingest 'https://...' --json
```

`--json` puts one result envelope on stdout; progress always goes to stderr. **Read the envelope
rather than guessing where the bundle landed** — see "Finding the output" below.

| Need | Flag |
|---|---|
| select a bilibili part | `--part N` |
| process every part | `--all-parts` |
| skip subtitle reuse, force local ASR | `--force-whisper` |
| break ASR repetition loops | `--robust` |
| skip frames and captioning | `--no-vision` |
| tune near-duplicate frame collapse | `--dedup-threshold N` |
| keep frame metadata but omit PNGs | `--no-frame-images` |
| choose the output root | `--out PATH` |
| burned-in subtitle OCR | `--ocr`, plus `--force-ocr` when detection misses a track you know exists |
| mirror bilibili danmaku | `--danmaku` |
| bilibili vote/grade widgets | `--interactions` |
| set the ASR language | `--lang CODE` (read the caveat below) |

`--scene-threshold` is retained as a deprecated, ignored flag; it warns and does nothing.

### `--lang` is a transcription lever, not an output-language request

This is the most misused flag in the pipeline. What it actually does:

* It becomes whisper-cli's `-l`, i.e. *the language the recognizer expects to hear*. Passing
  `--lang en` for Mandarin audio asks the recognizer to hear English in Chinese speech, which
  produces garbage. It does not translate.
* On **bilibili** it does not choose a subtitle track at all — the candidate order is fixed. The
  CLI prints a note saying so. It applies only if the run falls back to Whisper.
* On **YouTube** it does select a human caption track in that language, and that track is labelled
  `human-sub`, the highest authority tier. Asking for a language the video was not spoken in
  therefore stamps a translation with top confidence.

Defaults are `zh` for bilibili and auto-detect for YouTube. Leave it unset unless you have a
specific reason. If you need content in a different language than the video, that is a translation
step downstream of this pipeline, not a flag here.

## Finding the output

A successful part writes `bundle.json`, `bundle.md`, and optionally `frames/` into a directory
named:

```text
<out-root>/<sanitized title> [<id>-p<part>]/
```

The title prefix means **the directory is not derivable from the video id**. Take the paths from
the `--json` envelope:

```json
{
  "schema_version": "1.1",
  "platform": "bilibili.com",
  "id": "BV1...",
  "ok": true,
  "parts": [
    {
      "part": 1, "ok": true,
      "bundle_dir": "/abs/path/<title> [BV1...-p1]",
      "bundle_json": "/abs/.../bundle.json",
      "bundle_md": "/abs/.../bundle.md",
      "frames_dir": null,
      "transcript_source": "auto-sub",
      "transcript_language": "zh",
      "segments": 412, "frames": 0,
      "ocr_cues": null, "danmaku_windows": null, "interactions": null
    }
  ]
}
```

`parts` lists every attempted part; a failed one carries `error` and no paths. An optional track
reports `null` when it was not requested and a count when it was.

Generated state lives under the plugin's data directory when a client provides one
(`BLISOLVER_DATA_DIR`, else `PLUGIN_DATA`, else the repository), so bundles survive plugin updates.

## Inspect and validate

```bash
python3 "$SCRIPTS/inspect_bundle.py"  <bundle-dir> --pretty
python3 "$SCRIPTS/validate_bundle.py" <bundle-dir>
```

`inspect_bundle.py` reports counts and identity without printing transcript or danmaku bodies.
`validate_bundle.py` checks the bundle against the live Pydantic contract, requires `bundle.md`,
and rejects frame paths that escape the bundle directory; it exits 1 on an invalid bundle.

`bundle.md` is the primary Atlas reading surface; `bundle.json` is the complete precise record —
Markdown may cap danmaku lines per window, JSON never does.

## Bundled scripts

| File | Status | Purpose |
|---|---|---|
| `scripts/blisolver_cli.py` | public | Pass-through to the CLI with a resolved interpreter |
| `scripts/inspect_bundle.py` | public | Local bundle summary, no text bodies |
| `scripts/validate_bundle.py` | public | Schema and artifact validation |
| `scripts/_runtime.py` | internal | Plugin-root and interpreter resolution; do not call directly |

The plugin also exposes an MCP server for asynchronous work; see `references/mcp-contract.md`.
The plugin root has its own `scripts/` directory, unrelated to this one; the `ocr_worker.py` there is
a runtime dependency of `--ocr`, not an entry point.

## Authority and safety rules

* Transcript authority is `human-sub > whisper > auto-sub`. Acquisition may prefer a caption for
  cost; `--force-whisper` is the override. Provenance stays visible in `Transcript.source_reason`.
* A delivered transcript may not be in the video's original language. bilibili's Chinese ASR track
  is sometimes returned redacted, and the pipeline then falls through to a foreign-language track.
  When that happens `source_reason` says `language proxy` and names both the rejected track and the
  marker that triggered rejection. **Read `source_reason`** — it is the direct signal.
  `transcript.language` and `bundle.md`'s `transcript_language` tell you what you actually received.
  `original_language` is a weaker cross-check: for bilibili it is a platform default of `zh`, not a
  measurement, so it is wrong on an English-language bilibili upload.
* OCR is an independent burned-in-subtitle timeline, not a replacement for the picked transcript.
  `Frame.ocr` is sparse slide/UI text and a different thing again.
* Danmaku and interactions are lower-authority audience signals. A danmaku author flag is an
  unverified hash hint; a Vote question is uploader framing, not a claim the video makes.
* Never put `SESSDATA`, `LMSTUDIO_API_KEY`, or cookies on a command line, and never echo an
  environment dump. The wrappers pass the environment to the child and never print it.
* The wrappers use argument arrays, never a shell string. Keep it that way when extending them.

## Load references as needed

| Task | Read |
|---|---|
| component map and data flow | `references/architecture.md` |
| CLI verbs, flags, schema, output contract | `references/cli-contract.md` |
| MCP tools, modes, job lifecycle | `references/mcp-contract.md` |
| provider, auth, and subtitle decisions | `references/provider-guide.md` |
| stage behavior and caching | `references/pipeline-stages.md` |
| setup, recovery, and QA | `references/operational-runbook.md` |
| terminology and authority | `references/domain-glossary.md` |
| where to verify a claim in source | `references/source-map.md` |

When documents disagree, trust in this order: current source and tests; then `PROTOCOL.md`,
`SPEC.md`, `README.md` where they agree with the code; then `CONTEXT.md`; then dated phase plans
and design documents, which are history rather than contract.
