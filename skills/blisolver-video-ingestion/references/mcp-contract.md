# MCP contract

Verified against `blisolver/mcp/server.py` and `mcp.json`. This surface had no reference at all
before, while `source-map.md` pointed readers at the source file — which is why the job lifecycle
below was invisible to anyone using the skill.

## When to use MCP instead of the CLI

The CLI blocks for the whole run; a Whisper fallback on a long video takes tens of minutes. MCP
starts the ingest in a subprocess and hands back a `job_id` immediately, then lets you poll. Use MCP
when you cannot hold a synchronous call open, and the CLI when you can.

Both drive the same pipeline. MCP does not change provider behavior, stage semantics, or the bundle
contract.

## How a client launches it

`mcp.json` at the plugin root declares one stdio server:

```json
{
  "mcpServers": {
    "blisolver": {
      "type": "stdio",
      "command": "./bin/blisolver-mcp",
      "cwd": "${PLUGIN_ROOT}",
      "env": { "BLISOLVER_DATA_DIR": "${PLUGIN_DATA}" }
    }
  }
}
```

Agent Plugins 1.0.0 §7.2.1 requires `command` to be one executable token and performs no placeholder
expansion in it, so the interpreter cannot be named in the manifest. `bin/blisolver-mcp` is that
token: it resolves a Python that owns BliSolver's dependencies, then execs `blisolver.cli mcp`. Its
resolution order matches `scripts/_runtime.py` exactly — `$BLISOLVER_PYTHON`,
`$PLUGIN_DATA/venv/bin/python`, `$PLUGIN_ROOT/.venv/bin/python`, then `blisolver` on PATH.

Every diagnostic from the launcher goes to stderr. stdout carries JSON-RPC and nothing else.

To run the server by hand for debugging, invoke `bin/blisolver-mcp` directly; it derives
`PLUGIN_ROOT` from its own location when a client has not supplied it.

## Tools

Five tools. One is synchronous and cheap; the rest are a start-then-poll pair set.

### `probe_video(url) -> ProbeResult`

Synchronous, no media. Same payload as `blisolver probe`. Use it to estimate cost before committing.

### `extract_transcript(url, mode="auto") -> {job_id, status, canonical_id, part, mode}`

Starts an ingest and returns immediately with `status: "running"`. `mode` selects flags:

| mode | flags added | meaning |
|---|---|---|
| `auto` | none | the default acquisition chain: caption if trustworthy, else Whisper |
| `force_whisper` | `--force-whisper` | skip subtitle reuse, always run local ASR |
| `force_ocr` | `--ocr --force-ocr` | additionally run the burned-in subtitle track, skipping pre-detection |

An unknown mode raises rather than silently running the default.

Every job runs with `--no-vision --no-frame-images --json`. Frame captioning is deliberately not
part of the async surface; `get_visual_context` returns whatever the chosen mode produced, which
means frames are empty unless a mode that generates them was used.

### `get_transcript(job_id) -> {status, ...}`

Poll until `status` is `done`. On `done`, carries the picked transcript: `source`, `source_reason`,
`language`, `model`, `quality_gate`, and `segments` with per-cue provenance.

### `get_timeline(job_id) -> {status, ...}`

On `done`, the multi-source view: `timeline` (the authoritative picked cues), `ocr_track` (the
independent burned-in timeline), `sources_present`, `n_transcript`, `n_ocr`, plus the transcript
source and reason. This is the shape to reason over for authority ranking.

### `get_visual_context(job_id) -> {status, ...}`

On `done`, `frames` (timestamp, phash, caption, sparse OCR) and `ocr` (the burned-in track).

## Job lifecycle

Status is inferred, with no separate notification channel. Precedence, most reliable first:

1. **A parseable result envelope exists** — the run finished. `done` with the bundle path taken from
   the envelope, or `failed` carrying the part's error.
2. **The in-memory `Popen` handle polls as alive** — `running`.
3. **The recorded pid is alive** — `running`. This is the fallback after a server restart.
4. **Neither** — `failed`, with the tail of the job log as the reason.

`extract_transcript` spawns `ingest --json` with stdout redirected to
`<cache>/mcp-jobs/<job_id>.result.json` and stderr to `<job_id>.log`. Because the child writes the
envelope only after the whole run completes, a parseable envelope is a reliable completion signal.
A truncated or half-written file parses as absent, so a partial write is never mistaken for
completion.

**The job store never reconstructs the bundle path.** It reads it from the envelope. The delivery
directory is named after the sanitized video title, so `out/<id>-p<part>/` does not exist for any
titled video; a store that derived it reported `running` forever and then `failed`.

An unknown `job_id` returns `{"status": "unknown", "error": ...}` rather than raising.

## Environment and credentials

The server subprocess inherits its environment from the client. Provider credentials —
`SESSDATA`, `BLISOLVER_COOKIES_BROWSER`, `LMSTUDIO_API_KEY` — must already be present there, or in
the `.env` the application loads. They are deliberately **not** in `mcp.json`: Agent Plugins §9.2
states that configured `env` values are visible package data and must not carry secrets, and
`mcp.json` is committed to the repository.

Consequence worth knowing: a client that sanitizes the ambient environment will start a server with
no bilibili session, and every bilibili video will degrade to Whisper. Diagnose with
`blisolver doctor`, whose `provider-auth` check reports presence without printing values.

## Failure isolation

Agent Plugins §7.2.2 requires a server that fails to start or connect not to invalidate the rest of
the plugin. A conformant client should therefore still discover and expose the Skill when the MCP
server is unavailable. Running `scripts/blisolver_cli.py` still requires a usable BliSolver Python
environment, because the Skill and MCP surfaces intentionally share that runtime.
