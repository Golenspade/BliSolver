---
name: blisolver-video-ingestion
description: Acquire bilibili.com or YouTube video transcripts and optional visual/OCR tracks with BliSolver. Use for runtime checks, probing, ingestion, provider troubleshooting, and Atlas bundle validation; interpretation belongs downstream.
license: MIT. LICENSE.txt has complete terms.
compatibility: Requires the complete BliSolver checkout/plugin and Python 3.11+ with its dependencies. ASR needs ffmpeg, whisper-cli and GGML weights; vision and OCR have separate optional prerequisites. MCP clients may read guidance and runtime status through resources.
metadata:
  project: BliSolver
  bundle-schema: "1.1"
  platforms: "bilibili.com, youtube.com"
  plugin-format: "Agent Plugins 1.0.0"
---

# BliSolver video ingestion

Acquire and normalize video into an Atlas bundle. Keep one authoritative transcript with its
provenance; summarization, translation, and entity extraction belong downstream.

## Choose an entry and read only its next reference

This file is the shared workflow. Do not preload every reference or script source.

| Current stage or need | Read before acting |
|---|---|
| Local CLI: first operation, flags, language, output fields | [CLI contract](references/cli-contract.md) |
| MCP: first tool call, modes, polling and errors | [MCP contract](references/mcp-contract.md) |
| Missing runtime, doctor warning, installation or recovery | [Operational runbook](references/operational-runbook.md) |
| Auth, caption selection, rejected or wrong-language subtitles | [Provider guide](references/provider-guide.md) |
| Requested vision/OCR/danmaku, or cache behavior | [Pipeline stages](references/pipeline-stages.md) |
| Bundle QA and handoff | [CLI contract](references/cli-contract.md) (envelope and schema sections) |
| Architecture work | [Architecture](references/architecture.md) |
| An ambiguous domain term | [Domain glossary](references/domain-glossary.md) |
| Source changes or a contract disagreement | [Source map](references/source-map.md) |
| Host cannot discover this skill; plugin/clone setup | [Agent discovery](references/agent-discovery.md) |

For MCP-only access, read this file at `blisolver://guidance/SKILL.md` using `resources/read`.
The reference URI is `blisolver://guidance/` followed by its path above, for example
`blisolver://guidance/references/mcp-contract.md`. These are MCP resource identifiers, not shell
paths or web URLs. Read only the selected resource. Scripts require a local complete checkout;
use MCP tools when the client has no shell. If the host exposes neither resources nor local files,
have it expose the guidance or attach the skill before proceeding; do not invent a read-skill tool.

## Local runtime anchor

In a clone, run the following **from the repository root containing README.md and plugin.json**:

```bash
SCRIPTS="$(pwd -P)/skills/blisolver-video-ingestion/scripts"
python3 "$SCRIPTS/blisolver_cli.py" doctor --json
```

If activated from another directory, set `SCRIPTS` to `scripts/` beside the actual loaded
`SKILL.md` using its supplied absolute location. Do not derive it from the shell's `$0`, assume
PATH has `blisolver`, or copy this skill away from its application. The wrapper resolves the
correct interpreter independently of the caller's working directory; setup failures name the fix.

## Acquisition sequence

1. **Preflight:** run the doctor command above, or read `blisolver://runtime/doctor.json` through
   MCP. It is offline; directory/writability checks may create local cache/output directories.
   Check required stages, not just the exit code or overall status. Missing whisper-cli/weights
   means ASR fallback is unavailable; resolve that before a run that may need it.
2. **Input:** use the user's or an observed video's URL/BV ID. Never invent a video ID, language,
   part number, model, or credential. If the target is missing, ask for it. A user-supplied bare
   BV ID may be used directly. Probe reports available parts; follow the selected URL's part
   unless the user requests another or all parts. `bilibili.tv` is unsupported.
3. **Probe:** use CLI `probe` or MCP `probe_video` for metadata, without downloading media.
   Inspect duration, parts and subtitle candidates. Candidates do not prove quality-gate acceptance.
4. **Run:** for text only, CLI uses `--no-vision --no-frame-images --json`; MCP uses
   `extract_transcript` with `mode="auto"`. Preview CLI arguments with `--show-command` before a
   costly run; that only displays the command, it does not validate the URL or model. Force ASR
   only when requested or caption quality/language requires it, after runtime readiness.
   `--lang` is the spoken-language setting, never the conversation or desired translation language;
   read the selected interface's language limitations before overriding it.
5. **Collect:** CLI paths come only from successful `parts[]` entries in the JSON envelope.
   For MCP, preserve the returned `job_id`, poll with the documented delay, and stop on terminal
   errors. Do not reconstruct paths, fabricate handles, or start a duplicate job while polling.
6. **Verify:** check nonempty cues, delivered `language`, `source_reason`, quality gate and cue
   provenance. A `language proxy` is not verified original-language text; see the provider guide.
   Bilibili's `original_language=zh` is a default, not a measurement. Local schema validation
   checks structure, not recognition accuracy. The local inspector is a count/identity summary;
   read `transcript` in the returned `bundle_json` for provenance and actual cues. Report failures
   per part and hand off actual output.

## Local helpers and essential boundaries

| Script (relative to this skill) | Use |
|---|---|
| `scripts/blisolver_cli.py` | CLI pass-through; `--show-command` precedes the verb |
| `scripts/inspect_bundle.py` | Summary without text bodies: pass the returned bundle directory |
| `scripts/validate_bundle.py` | Validate that directory against the live schema and artifacts |
| `scripts/_runtime.py` | Internal resolver; do not invoke directly |

OCR is a parallel burned-in timeline, never the authoritative transcript. Danmaku/interactions
are audience signals. Keep optional tracks off unless needed. Never print secret values, pass
cookies/API keys on command lines, or place credentials in package configuration. stdout is JSON
or JSON-RPC; diagnostics go to stderr. Current source/tests outrank prose; dated `docs/` are history.
