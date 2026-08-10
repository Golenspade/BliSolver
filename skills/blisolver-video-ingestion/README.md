<div align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=timeGradient&height=250&section=header&text=BliSolver&fontSize=90&animation=fadeIn&fontAlignY=38&desc=Agent%20Skill%20for%20Video%20Ingestion&descAlignY=55&descAlign=50" alt="Skill Banner">
  <p><strong>The Agent Skill for operating the BliSolver <code>blisolver</code> video-ingestion pipeline.</strong></p>
  <p>
    <a href="README.md">English</a> | <a href="README_zh.md">简体中文</a>
  </p>
</div>

---

This directory is one skill inside the BliSolver [Agent Plugin](https://agent-plugins.org). It
teaches a compatible agent to:

- 🩺 **Diagnose** the runtime offline before spending on media — `blisolver doctor`
- 🔍 **Probe** bilibili.com and YouTube metadata cheaply, with no download
- 📝 **Ingest** a video and read back the exact paths it wrote
- 🧠 **Interpret** transcript, visual, OCR, danmaku, and interaction provenance
- 🔬 **Inspect and validate** schema-1.1 bundles

> **Note:** BliSolver is an ingestion front door, not a summarizer or entity extractor.

## 📦 How this is packaged

The repository root is the plugin root, so the skill sits beside the application it drives:

```text
<plugin-root>/
├── plugin.json                 # Agent Plugins 1.0.0 manifest
├── mcp.json                    # stdio MCP server declaration
├── bin/blisolver-mcp           # launcher that resolves a usable interpreter
├── blisolver/                  # the application this skill drives
└── skills/
    └── blisolver-video-ingestion/     ← you are here
        ├── SKILL.md
        ├── references/
        └── scripts/
```

A conformant Agent Plugins client reads `plugin.json`, discovers this skill from the fixed `skills/`
location, and reads `mcp.json` for the MCP server. To install, make the plugin directory available to
your client — clone the repository, or copy it into the directory your client loads plugins from. The
exact command is client-specific and is documented by your client, not here.

The skill package intentionally does not vendor Python environments, models, media, caches, or
secrets. It ships instructions and three scripts.

## 🚀 Use

Ask an agent something like:

> *"Probe this bilibili URL, then ingest it without vision and validate the resulting bundle."*

The public scripts:

| Script | Purpose |
|---|---|
| `scripts/blisolver_cli.py` | Runs the CLI under an interpreter that has BliSolver's dependencies, forwarding every argument verbatim |
| `scripts/inspect_bundle.py` | Bundle summary — counts and identity, never text bodies |
| `scripts/validate_bundle.py` | Validates a bundle against the live schema and its artifacts |

`scripts/blisolver_cli.py` is a pass-through rather than an adapter: it does not re-declare the CLI's
flags, so it cannot fall behind them. It resolves the interpreter in this order —
`$BLISOLVER_PYTHON`, `$PLUGIN_DATA/venv/bin/python`, `<plugin-root>/.venv/bin/python`, then an
installed `blisolver` on PATH — and never falls back to whichever `python3` happened to launch it.

```bash
S=skills/blisolver-video-ingestion/scripts

python3 "$S/blisolver_cli.py" doctor                       # offline preflight
python3 "$S/blisolver_cli.py" probe 'https://...'           # one JSON object on stdout
python3 "$S/blisolver_cli.py" --show-command ingest 'https://...'   # preview, do not run
python3 "$S/blisolver_cli.py" ingest 'https://...' --json   # run; read paths from the envelope
```

## ⚙️ Runtime requirements

The instructions are portable; the media stages depend on the target machine.

- Python 3.11+ and a Python environment holding BliSolver's dependencies
- ffmpeg — every media stage shells out to it
- `whisper-cli` **and** a GGML model — two independent things, both checked by `doctor`
- deno or node — yt-dlp needs one to drive YouTube's real web-player client
- LM Studio with a vision model *and its projector* — only for frame captioning
- an isolated `.ocr-venv` — only for burned-in subtitle OCR
- an LM Studio model id in `BLISOLVER_DANMAKU_MODEL` — only for `--danmaku`

Run `blisolver doctor` rather than guessing: each check names the pipeline stage it gates, so a
warning tells you what is unavailable instead of that something is wrong.

## 🛠️ Model setup

**Whisper weights** — needed when no trustworthy caption is available:

```bash
curl -sL -o /tmp/ggml-medium.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin
```

`/tmp` is cleared on reboot. Set `BLISOLVER_WHISPER_MODEL` to a durable path if you want to keep it.

**OCR sandbox** — only for `--ocr`, kept isolated so RapidOCR and OpenCV never enter the application
environment:

```bash
uv venv .ocr-venv
.ocr-venv/bin/pip install rapidocr-onnxruntime opencv-python
```

## 🔐 Credentials

Provider credentials belong in the environment or `.env`. Never put `SESSDATA`, `LMSTUDIO_API_KEY`,
or cookie text into a command line, a URL, a log, or `mcp.json` — Agent Plugins treats configured
`env` values as visible package data, and `mcp.json` is committed.

## ⚠️ Current limitations

- `bilibili.tv` is deferred and rejected with an explicit error.
- The ASR backend is whisper.cpp via `whisper-cli`. The `transcribe` optional-dependency group in
  `pyproject.toml` still lists faster-whisper and CUDA wheels; it is vestigial.
- A delivered transcript may not be in the video's original language. bilibili sometimes returns its
  Chinese ASR track redacted, and acquisition falls through to a foreign-language track. Compare
  `transcript.language` with `original_language` and read `source_reason` for `language proxy`.
- Vision, OCR, danmaku, and interactions are optional stages with separate external requirements.

## 📂 Layout

```text
SKILL.md                  # the Agent Skills manifest and instructions
scripts/                  # three public scripts plus one internal helper
references/               # progressive-disclosure documentation
LICENSE.txt               # MIT
README.md / README_zh.md  # this guide
```

## 📜 Standards and license

- [Agent Plugins specification](https://agent-plugins.org/specification)
- [Agent Skills specification](https://agentskills.io/specification)
- [Model Context Protocol](https://modelcontextprotocol.io/specification)
- **MIT License.** See [`LICENSE.txt`](LICENSE.txt).
