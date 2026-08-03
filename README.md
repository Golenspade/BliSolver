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
  - **bilibili:** A logged-in **Firefox** profile (default), or a `SESSDATA` fallback.
  - **YouTube:** None for public videos; optionally a browser profile to unlock age-gated/bot-checked content.

## 📦 Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows

pip install -e .                   # core deps
pip install -e ".[transcribe]"     # + faster-whisper (or whisper.cpp on Mac)
pip install -e ".[frames,vision]"  # + frame extraction & captioning

cp .env.example .env               # configure your environment
```

> **Note:** Configure `.env` with your LM Studio endpoint/token/model, and per-source auth variables. Never commit your `.env` file.

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
