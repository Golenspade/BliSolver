<div align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=timeGradient&height=250&section=header&text=BliSolver&fontSize=90&animation=fadeIn&fontAlignY=38&desc=The%20Ingestion%20Front-Door%20for%20Atlas&descAlignY=55&descAlign=50" alt="BliSolver Banner">
  <br>
  <p>
    <a href="README.md">English</a> | <a href="README_zh.md">简体中文</a>
  </p>
</div>

---

**BliSolver** is a robust video ingestion tool for **bilibili.com** and **YouTube** (inspired by the BliSolver pipeline). It produces a timeline-aligned, self-contained **bundle** from a given video URL:

- 📝 **Original-language transcript** (Reuses trustworthy platform captions, or falls back to faster-whisper)
- 🖼️ **Per-frame visual notes** (OCR + figure/slide captions via a local vision model)

It starts at a URL and ends at `out/<sanitized_title> [<id>-p<part>]/` containing `bundle.md`, `bundle.json`, and `frames/`. It does **not** summarize or extract entities — that lives downstream in Atlas.

See [SPEC.md](SPEC.md) for the design and [PROTOCOL.md](PROTOCOL.md) for the Atlas-facing contract.

## 🌟 Why a Dedicated Tool?

Source acquisition is the hard part, and it differs per platform:
- **Bilibili** hard-walls general agents (`bilibili.com/video/…` returns HTTP 412; stream/subtitle URLs 403 without a `Referer`; most content needs a logged-in cookie) and doesn't surface its AI captions to yt-dlp.
- **YouTube** is friendlier but still needs careful original-language caption handling. 

BliSolver encapsulates each source behind a **provider** and turns the result into one clean bundle.

## 🛠️ Prerequisites

- **Python 3.11**
- **ffmpeg** on PATH (used by yt-dlp and the frame stage).
- **A JavaScript runtime** (**deno** recommended, or node) for **YouTube** — yt-dlp needs one to drive YouTube's real web player client. Auto-detected from PATH or standard install locations.
- **NVIDIA GPU** or **Apple Silicon** (Metal) for fast whisper transcription.
- **LM Studio** running with a VL model and its mmproj loaded for the vision stage.
- **Auth (per source):**
  - **bilibili:** A logged-in **Chrome** profile (default; override with `BLISOLVER_COOKIES_BROWSER`), or a `SESSDATA` fallback.
  - **YouTube:** None for public videos; optionally a browser profile to unlock age-gated/bot-checked content.

## 📦 Setup

Either interpreter path works. `uv venv` does **not** install `pip` into the environment, so use
`uv pip` rather than `<venv>/bin/pip` when you take the uv route.

```bash
git clone https://github.com/alttina/BliSolver.git && cd BliSolver

# with uv
uv venv .venv
uv pip install --python .venv/bin/python -e ".[mcp,frames,vision]"

# or with the standard library
python3 -m venv .venv
.venv/bin/pip install -e ".[mcp,frames,vision]"

cp .env.example .env
.venv/bin/blisolver doctor          # verify what is ready before spending on media
```

There is no `transcribe` extra: local ASR runs whisper.cpp through the external `whisper-cli`
binary plus a GGML model file, neither of which is a Python package. `doctor` checks the binary and
the weights separately, and prints the command to fetch the weights if they are missing.

> **Note:** Configure `.env` with your LM Studio endpoint/model and per-source auth. Never commit your `.env`.

## 🔌 Use as an Agent Plugin

This repository *is* an [Agent Plugin](https://agent-plugins.org) — `plugin.json` sits at the root
beside the application, so a compatible client can load the skill and the MCP server directly:

```text
BliSolver/
├── plugin.json      # Agent Plugins 1.0.0 manifest
├── mcp.json         # one stdio MCP server
├── bin/blisolver-mcp
├── blisolver/       # the application
└── skills/
    └── blisolver-video-ingestion/   # SKILL.md, references/, scripts/
```

**Installation is client-defined.** The Agent Plugins specification standardizes the directory
shape and deliberately leaves distribution, installation, and enablement to each client, so there
is no universal install command. Clone this repository, then point your client at the directory —
see [Compatible Clients](https://agent-plugins.org/compatible-clients) for per-client setup
(Cursor, GitHub Copilot, Hermes Agent, Kiro, VS Code at the time of writing).

Two things are worth knowing before you wire it up:

- **The MCP server needs an interpreter that has the dependencies.** `mcp.json` runs
  `./bin/blisolver-mcp`, which resolves `$BLISOLVER_PYTHON`, then `$PLUGIN_DATA/venv/bin/python`,
  then `<plugin-root>/.venv/bin/python`, then an installed `blisolver`. Run the Setup above first,
  or the server exits with the command to create one.
- **Credentials are not in the manifest.** The specification treats configured `env` values as
  visible package data, and `mcp.json` is committed, so `SESSDATA` and `LMSTUDIO_API_KEY` must come
  from the environment or `.env`. A client that sanitizes the environment will start a server with
  no bilibili session, and every bilibili video will fall back to Whisper.

`mcp.json` sets `BLISOLVER_DATA_DIR` to `${PLUGIN_DATA}`, so caches and bundles land in the
client-managed data directory and survive a plugin update.

## 🚀 Usage

```bash
blisolver ingest <url> [options]
blisolver probe  <url>
```

### Options

| Flag | Description |
|------|-------------|
| `--part N` | Process a specific part index (1-based) |
| `--all-parts` | Process every available part (bilibili) |
| `--force-whisper` | Skip caption reuse, always transcribe |
| `--lang CODE` | Pin transcription language (defaults to agent/user conversational language) |
| `--robust` | Disable `condition_on_previous_text` (good for repetition-loop lectures) |
| `--no-vision` | Skip frame captioning |
| `--dedup-threshold N` | pHash hamming distance to collapse near-duplicate frames (default: 10) |
| `--no-frame-images` | Omit PNGs from `out/` (caption text still recorded) |
| `--ocr` / `--force-ocr` | Run optional hard-subtitle OCR |
| `--danmaku` | Fetch bilibili danmaku track |
| `--interactions` | Fetch bilibili command interactions (votes/grades) |

### 🔍 Probe

`probe` takes only a URL and prints a single-line JSON `ProbeResult` to stdout (including title, uploader, duration, parts, `original_language`, and `available_subtitles`) so a caller can estimate workload before an `ingest` run.

## 📖 Output format

Outputs are written to: `out/<sanitized_title> [<id>-p<part>]/`

- `bundle.md`: The main product Atlas ingests (provenance header + slide-chunked transcript/visual notes)
- `bundle.json`: Precise backing record (contract in PROTOCOL.md)
- `frames/`: Directory containing QA PNGs

### Transcript Source Strategy

- **bilibili:** Prefers human/AI captions gated for quality; falls back to Whisper. Auto-caption reuse is labeled `auto-sub`.
- **YouTube:** `human-sub > auto-sub > whisper`. Prefers human captions on the original-language key.
