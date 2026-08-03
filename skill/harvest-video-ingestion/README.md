# bilibili-get-content

Portable Agent Skill for operating the BliSolver `harvest` video-ingestion pipeline.

The skill teaches compatible coding agents how to:

- probe and ingest public bilibili.com and YouTube videos;
- choose provider captions or local Whisper transcription;
- diagnose ffmpeg, JavaScript runtime, whisper.cpp, LM Studio, OCR, and auth setup;
- inspect and validate Atlas-compatible schema 1.1 bundles;
- understand transcript, visual, OCR, danmaku, and interaction provenance.

Harvest is an ingestion front-door, not a summarizer or entity extractor.

## Install

### Recommended: install for all supported agents

```bash
npx skills add alttina/bilibili-get-content \
  --skill harvest-video-ingestion \
  --global \
  --agent '*' \
  --yes
```

Install for one agent instead:

```bash
npx skills add alttina/bilibili-get-content \
  --skill harvest-video-ingestion \
  --global \
  --agent codex \
  --yes
```

Replace `codex` with `claude-code`, `cursor`, `github-copilot`, or another supported agent.
For a project-local installation, omit `--global`.

A one-line copy/paste version is in [`for_agents_download.md`](for_agents_download.md).

## Easy install

```text
Install the `harvest-video-ingestion` skill from `https://github.com/alttina/bilibili-get-content` for all supported agents using `npx skills add https://github.com/alttina/bilibili-get-content --skill harvest-video-ingestion --global --agent '*' --yes`; then verify with `npx skills list` and confirm that `harvest-video-ingestion` is listed.
```

## Use

After installation, ask an agent for example:

```text
Probe this bilibili URL, then ingest it without vision and validate the resulting bundle.
```

The skill's public wrappers are:

| Script | Purpose |
|---|---|
| `scripts/doctor.py` | Offline runtime and dependency report |
| `scripts/probe.py` | JSON-safe `harvest probe` wrapper |
| `scripts/ingest.py` | Safe `harvest ingest` wrapper with dry-run support |
| `scripts/inspect_bundle.py` | Local bundle summary |
| `scripts/validate_bundle.py` | Schema 1.1 and artifact validation |

The wrappers locate a BliSolver checkout via `--project-root`, `HARVEST_PROJECT_ROOT`, an ancestor
checkout, or an installed `harvest` command. They do not contain the harvest application itself.

## Runtime requirements

The skill package is portable, but media processing depends on the target environment:

- Python 3.11+;
- an installed BliSolver checkout or `harvest` command;
- ffmpeg for audio/video stages;
- `whisper-cli` and a GGML model for local transcription fallback;
- deno or node for reliable YouTube extraction;
- LM Studio with a configured vision model and projector for frame vision;
- the isolated `.ocr-venv` worker only when hard-subtitle OCR is requested.

Provider credentials belong in the target environment or browser profile. Never put cookies, API
keys, or `SESSDATA` in command arguments, URLs, README files, or logs.

## Update and remove

Update installed skills with:

```bash
npx skills update harvest-video-ingestion
```

Use the skills CLI's remove command to remove the installed skill:

```bash
npx skills remove harvest-video-ingestion
```

## Current limitations

- `bilibili.tv` is deferred and intentionally rejected.
- The current ASR backend is `whisper-cli`/whisper.cpp; older faster-whisper/CUDA references are
  historical where they conflict with the current source.
- Vision, OCR, danmaku, and command-danmaku are optional stages with separate external requirements.

## Repository layout

```text
SKILL.md                    # required Agent Skills manifest and instructions
scripts/                    # public wrappers plus one internal helper
references/                 # progressive-disclosure operational documentation
LICENSE.txt                # MIT license
README.md                  # human-facing installation guide
for_agents_download.md     # copy/paste installation command
```

## Standards

- [Agent Skills specification](https://agentskills.io/specification)
- [`npx skills` installer](https://github.com/vercel-labs/skills)

## License

MIT. See [`LICENSE.txt`](LICENSE.txt).
