# harvest

The **ingestion front-door** for the **Atlas** knowledge base. Given a video URL from a supported
source (**bilibili.com** or **YouTube**), harvest produces a timeline-aligned, self-contained
**bundle**:

- an **original-language transcript** (reuse trustworthy captions, else faster-whisper), and
- **per-frame visual notes** (OCR + figure/slide captions via a local vision model).

It starts at a URL and ends at `out/<id>-p<part>/` (`bundle.md` + `bundle.json` + `frames/`). It does
**not** summarize or extract entities — that lives downstream in Atlas. See [SPEC.md](SPEC.md) for the
design and [PROTOCOL.md](PROTOCOL.md) for the Atlas-facing contract.

## Why a dedicated tool

Source acquisition is the hard part, and it differs per platform. bilibili hard-walls general agents
(`bilibili.com/video/…` returns HTTP 412; stream/subtitle URLs 403 without a `Referer`; most content
needs a logged-in cookie) and doesn't surface its AI captions to yt-dlp. YouTube is friendlier but
still needs careful original-language caption handling. harvest encapsulates each source behind a
**provider** and turns the result into one clean bundle.

## Prerequisites

- **Python 3.11**
- **ffmpeg** on PATH (or auto-detected from a winget install) — used by yt-dlp and the frame stage.
- **A JavaScript runtime** (**deno** recommended, or node) for **YouTube** — yt-dlp needs one to
  drive YouTube's real web player client; without it extraction intermittently degrades to a
  stripped/blocked response (wrong title, no subtitles). Auto-detected from PATH, a `deno.land`
  install (`~/.deno`, via `irm https://deno.land/install.ps1 | iex`), or winget. Note: the
  separate `nsig` challenge solver (EJS) is optional and not required — audio, subtitles, and
  metadata come through without it. Not needed for bilibili.
- An **NVIDIA GPU** (CUDA) for faster-whisper. CPU works but is slow.
- **LM Studio** running with a VL model **and its mmproj projector loaded** (verified at runtime via
  a nonce-OCR probe) for the vision stage.
- **Auth (per source):**
  - **bilibili:** a logged-in **Firefox** profile (default), or a `SESSDATA` fallback.
  - **YouTube:** none for public videos; optionally a browser profile to unlock age-gated/bot-checked
    content.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -e .                   # spine deps
pip install -e ".[transcribe]"     # + faster-whisper
pip install -e ".[frames,vision]"  # + frame extraction & captioning

cp .env.example .env               # then fill in LM Studio + auth vars
```

Configure `.env`: LM Studio endpoint/token/model, and per-source auth (bilibili Firefox profile or
`SESSDATA`; optional YouTube profile). The real `.env` is gitignored — never commit it.

## Usage

```bash
harvest ingest <url> [--part N] [--all-parts] [--force-whisper] [--lang CODE] [--robust]
                     [--no-vision] [--dedup-threshold N] [--out DIR] [--no-frame-images]
harvest probe  <url>
```

| flag | effect |
|------|--------|
| `--part N` / `--all-parts` | one part (default: from URL) / loop every part (bilibili) |
| `--force-whisper` | skip caption reuse, always transcribe |
| `--lang CODE` | pin transcription language (default: `zh` bilibili, auto-detect YouTube) |
| `--robust` | disable `condition_on_previous_text` (repetition-loop lectures) |
| `--no-vision` | skip frame captioning |
| `--dedup-threshold N` | phash hamming distance to collapse near-duplicate frames (default 10) |
| `--no-frame-images` | omit PNGs from `out/` (caption text still recorded) |

`probe` takes only a URL and prints a single-line JSON `ProbeResult` to stdout (title, uploader,
duration, parts) so a caller can estimate workload before an `ingest` run. See
[PROTOCOL.md](PROTOCOL.md) for the exact contract.

### Transcript source, by platform

- **bilibili:** prefer human/AI captions gated for quality (`#6357` part-match + quality gate); fall
  back to Whisper. Auto-caption reuse is labeled `auto-sub`.
- **YouTube:** `human-sub > auto-sub > whisper`. Prefer human captions on the original-language key —
  exact, else a clean region/script variant like `de-DE`/`zh-Hant`, never a hash-suffixed
  community-translation key (`human-sub`); else the original-language auto-caption (`auto-sub`) when it
  clears a structural validity net (presence, duration-coverage, chars-per-second floor); else Whisper.
  Auto-caption reuse is a cost optimization — `--force-whisper` overrides to always transcribe.

## Output

`out/<id>-p<part>/` — `bundle.md` (the product Atlas ingests: provenance header + slide-chunked
transcript/visual notes), `bundle.json` (precise backing record, contract in PROTOCOL.md), `frames/`
(QA PNGs).
