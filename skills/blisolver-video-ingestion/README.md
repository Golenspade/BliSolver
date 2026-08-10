<div align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=timeGradient&height=250&section=header&text=BliSolver&fontSize=90&animation=fadeIn&fontAlignY=38&desc=Portable%20Agent%20Skill%20for%20Video%20Ingestion&descAlignY=55&descAlign=50" alt="Skill Banner">
  <p><strong>Portable Agent Skill for operating the BliSolver <code>blisolver</code> video-ingestion pipeline.</strong></p>
  <p>
    <a href="README.md">English</a> | <a href="README_zh.md">简体中文</a>
  </p>
</div>

---

The skill teaches compatible coding agents how to:

- 🔍 **Probe and ingest** public bilibili.com and YouTube videos
- 📝 **Choose** provider captions or local Whisper transcription
- 🩺 **Diagnose** ffmpeg, JavaScript runtime, whisper.cpp, LM Studio, OCR, and auth setup
- 🔬 **Inspect and validate** Atlas-compatible schema 1.1 bundles
- 🧠 **Understand** transcript, visual, OCR, danmaku, and interaction provenance

> **Note:** BliSolver is an ingestion front-door, not a summarizer or entity extractor.

## 📦 Install

### Recommended: Install for all supported agents

```bash
npx skills add alttina/bilibili-get-content \
  --global \
  --agent '*' \
  --yes
```

**Install for one agent instead:**

```bash
npx skills add alttina/bilibili-get-content \
  --global \
  --agent codex \
  --yes
```

Replace `codex` with `claude-code`, `cursor`, `github-copilot`, or another supported agent. For a project-local installation, omit `--global`.

A one-line copy/paste version is in [`for_agents_download.md`](for_agents_download.md).

## 🚀 Use

After installation, ask an agent for example:

> *"Probe this bilibili URL, then ingest it without vision and validate the resulting bundle."*

The skill's public wrappers are:

| Script | Purpose |
|---|---|
| `scripts/doctor.py` | Offline runtime and dependency report |
| `scripts/probe.py` | JSON-safe `blisolver probe` wrapper |
| `scripts/ingest.py` | Safe `blisolver ingest` wrapper with dry-run support |
| `scripts/inspect_bundle.py` | Local bundle summary |
| `scripts/validate_bundle.py` | Schema 1.1 and artifact validation |

The wrappers locate a BliSolver checkout via `--project-root`, `BLISOLVER_PROJECT_ROOT`, an ancestor checkout, or an installed `blisolver` command. They do not contain the blisolver application itself.

## ⚙️ Runtime requirements

The skill package is portable, but media processing depends on the target environment:

- Python 3.11+;
- an installed BliSolver checkout or `blisolver` command;
- ffmpeg for audio/video stages;
- `whisper-cli` and a GGML model for local transcription fallback;
- deno or node for reliable YouTube extraction;
- LM Studio with a configured vision model and projector for frame vision;
- the isolated `.ocr-venv` worker only when hard-subtitle OCR is requested.

Provider credentials belong in the target environment or browser profile. Never put cookies, API keys, or `SESSDATA` in command arguments, URLs, README files, or logs.

## 🛠️ Model Setup (Local Inference)

BliSolver runs AI models locally to ensure privacy and avoid API costs. You must set up the following models and sandboxes before ingesting videos:

**1. Whisper ASR Model (Audio-to-Text)**  
Required for generating transcripts when official subtitles are unavailable. Download the GGML model (~1.5GB) to the default path:
```bash
curl -sL -o /tmp/ggml-medium.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin
```
*(You can override the default path by setting the `BLISOLVER_WHISPER_MODEL` environment variable).*

**2. OCR Sandbox (Hard-subtitle Extraction)**  
Required only if you use the `--ocr` flag to extract burned-in subtitles. To avoid dependency pollution, the OCR engine must be installed in a dedicated isolated virtual environment at the project root:
```bash
uv venv .ocr-venv
.ocr-venv/bin/pip install rapidocr-onnxruntime opencv-python
```

## 🔄 Update and remove

**Update installed skills:**
```bash
npx skills update blisolver-video-ingestion
```

**Remove the installed skill:**
```bash
npx skills remove blisolver-video-ingestion
```

## ⚠️ Current limitations

- `bilibili.tv` is deferred and intentionally rejected.
- The current ASR backend is `whisper-cli`/whisper.cpp; older faster-whisper/CUDA references are historical where they conflict with the current source.
- Vision, OCR, danmaku, and command-danmaku are optional stages with separate external requirements.

## 📂 Repository layout

```text
SKILL.md                    # required Agent Skills manifest and instructions
scripts/                    # public wrappers plus one internal helper
references/                 # progressive-disclosure operational documentation
LICENSE.txt                # MIT license
README.md                  # human-facing installation guide
for_agents_download.md     # copy/paste installation command
```

## 📜 Standards & License

- [Agent Skills specification](https://agentskills.io/specification)
- [`npx skills` installer](https://github.com/vercel-labs/skills)
- **MIT License.** See [`LICENSE.txt`](LICENSE.txt).
