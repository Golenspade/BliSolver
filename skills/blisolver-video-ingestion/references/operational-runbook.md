# Operational runbook

`$SCRIPTS` below is this skill's `scripts/` directory. Every command goes through the wrapper so the
child runs under an interpreter that owns BliSolver's dependencies.

## First contact with an environment

```bash
python3 "$SCRIPTS/blisolver_cli.py" doctor
```

Offline, no network, safe to run before anything expensive. Read the `stage` on each check to know
what a warning costs. Exit 0 means nothing is blocking; exit 1 means a `core` check failed.

If the wrapper itself exits 2, there is no usable Python yet. It prints the candidates it tried and
the command to create one:

```bash
uv venv <plugin-root>/.venv
<plugin-root>/.venv/bin/python -m pip install -e "<plugin-root>[mcp]"
```

`$BLISOLVER_PYTHON` overrides the search when the environment lives somewhere unusual.

## Stage prerequisites

| Stage | Needs | Set up with |
|---|---|---|
| spine, probe | Python 3.11+, the package dependencies | `pip install -e .` |
| any media stage | ffmpeg | system package manager |
| YouTube extraction | deno or node | either; deno is yt-dlp's preferred runtime |
| local ASR | `whisper-cli` plus a GGML model | see below |
| frame vision | LM Studio serving the configured model *and its projector* | `LMSTUDIO_VISION_MODEL` |
| burned-in OCR | an isolated environment | `uv venv .ocr-venv && .ocr-venv/bin/pip install rapidocr-onnxruntime opencv-python` |
| danmaku | `BLISOLVER_DANMAKU_MODEL` | LM Studio model id |

The ASR model is checked separately from the binary because having one says nothing about the other:

```bash
curl -sL -o /tmp/ggml-medium.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin
```

The shipped default path is under `/tmp`, which is cleared on reboot. Point
`BLISOLVER_WHISPER_MODEL` at a durable location if you intend to keep it. `doctor` warns about this
specifically.

Do not infer the ASR backend from `pyproject.toml`: the `transcribe` optional-dependency group still
lists faster-whisper and CUDA wheels, while the implementation shells out to whisper.cpp. Read
`blisolver/transcribe.py`.

## Cheap probe

```bash
python3 "$SCRIPTS/blisolver_cli.py" probe 'https://www.youtube.com/watch?v=...' > probe.json
```

Parse `probe.json` only; diagnostics are on stderr. Nonzero exit means there is no trustworthy
record. Null metadata fields in a successful probe are normal.

## Ingest recipes

Preview the child command before a costly job:

```bash
python3 "$SCRIPTS/blisolver_cli.py" --show-command ingest 'https://...' --ocr
```

Caption-first default, machine-readable result:

```bash
python3 "$SCRIPTS/blisolver_cli.py" ingest 'https://www.bilibili.com/video/BV...' --json
```

Force local ASR and skip visual services:

```bash
python3 "$SCRIPTS/blisolver_cli.py" ingest 'https://...' --force-whisper --no-vision --json
```

Burned-in OCR without frame vision:

```bash
python3 "$SCRIPTS/blisolver_cli.py" ingest 'https://...' --no-vision --ocr --json
```

bilibili audience tracks:

```bash
python3 "$SCRIPTS/blisolver_cli.py" ingest 'https://www.bilibili.com/video/BV...' \
  --danmaku --interactions --json
```

Use `--part N` for one part; use `--all-parts` only when the extra acquisition cost is intended.

## Bundle QA

Take the path from the ingest envelope rather than constructing it:

```bash
DIR=$(python3 "$SCRIPTS/blisolver_cli.py" ingest 'https://...' --json \
      | python -c 'import json,sys; print(json.load(sys.stdin)["parts"][0]["bundle_dir"])')

python3 "$SCRIPTS/inspect_bundle.py"  "$DIR" --pretty
python3 "$SCRIPTS/validate_bundle.py" "$DIR"
```

Validation covers the live Pydantic schema, the presence of `bundle.md`, frame-path containment, and
referenced image existence. Null frame paths are valid under `--no-frame-images`.

## Failure matrix

| Symptom | First action |
|---|---|
| wrapper exits 2, "no interpreter" | create the environment it names, or set `$BLISOLVER_PYTHON` |
| `ModuleNotFoundError` from a child | you bypassed the wrapper; go through `blisolver_cli.py` |
| `.tv` unsupported error | use a `bilibili.com` URL; `.tv` is deferred |
| YouTube missing title or subtitles | install deno or node; keep public extraction cookie-free first |
| bilibili 403/412, or no captions offered | verify the logged-in browser profile without printing it; preserve referer behavior |
| ASR fails after the audio download | `doctor` and read `whisper-model`; the weights are probably absent |
| vision projector check fails | bind the model's mmproj in LM Studio; do not bypass by trusting captions |
| `--ocr` did nothing | `doctor` and read `ocr-isolate`; `--force-ocr` only helps once the isolate exists |
| `--danmaku` did nothing | set `BLISOLVER_DANMAKU_MODEL`; the track is bilibili-only and opt-in |
| transcript is not in the video's language | expected on a redacted bilibili track; read `source_reason` for `language proxy` |
| bundle cannot be found | read `bundle_dir` from the envelope; the directory name carries the title |
| MCP job never reaches `done` | read `<cache>/mcp-jobs/<job_id>.log`; the result file appears only on completion |
| bundle invalid | read the JSON report and repair the producing run, not the bundle |

## Secret handling

Credentials belong in the environment or `.env`, never in a URL, an argument, a log, `mcp.json`, or a
test fixture. `mcp.json` is committed and Agent Plugins treats configured `env` values as visible
package data. `doctor` reports credentials as configured or absent and never prints a value; keep any
new check to that standard.
