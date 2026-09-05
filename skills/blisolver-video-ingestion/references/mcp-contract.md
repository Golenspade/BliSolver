# MCP contract

Verified against `blisolver/mcp/server.py`, `blisolver/mcp/guidance.py`, and `mcp.json`.

## Read guidance and preflight before tools

The server's short instructions (modern discovery or legacy initialization) and probe/start tool
descriptions point to `blisolver://guidance/SKILL.md`. Use `resources/read` to read it, then only the
reference needed for the current stage; this contract's URI is
`blisolver://guidance/references/mcp-contract.md`. `resources/list` exposes metadata, not all bodies.
These are core MCP resources, not an implementation of the draft Skills extension.

Read `blisolver://runtime/doctor.json` for the server's offline prerequisites. It can create local
cache/output directories and briefly write/delete a writability probe. It does not download media,
call external models or extract browser cookies. A warning about whisper-cli/weights means ASR
fallback is unavailable, even if the report's overall status is not an error. Read the operational
runbook when setup is needed. If the host hides resources, use the local Skill files and CLI doctor,
or have the host expose/attach these resources; do not invent a doctor tool.

## Minimal transcript workflow

1. Complete preflight, then call `probe_video` with `{"url": "<actual URL or BV ID>"}`. Replace
   the placeholder with the user's or an observed value. Inspect duration, parts and subtitles.
2. Call `extract_transcript` with that same `url` and `mode: "auto"` for caption-first text.
   Caption availability does not prove acceptance; auto can download audio and invoke Whisper.
3. Preserve the exact returned `job_id`. Call `get_transcript` with `{"job_id": "<returned handle>"}`.
   While running, wait about 5 seconds between polls, increasing to 15–30 seconds for long runs;
   honor any rate-limit retry delay. This delay is a usage recommendation, not a protocol rule.
4. On done, inspect `segments`, `language`, `source_reason` and `quality_gate`. On failed, unknown
   or expired, stop polling and use the reported error. Never invent a handle or restart implicitly.

The start tool accepts only `url` and `mode`. A Bilibili URL may select a part through its `p` query;
there is no `part`, `lang`, `all_parts`, or `output` argument. Use the CLI when language, all-parts,
vision or output controls are needed. In particular the Bilibili ASR default is `zh`, and YouTube
omits whisper-cli's language argument; do not infer actual audio language from either default.
MCP has no subtitle-only mode. Missing ASR prerequisites can still block a caption-first job.

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

## Protocol versions and state boundary

The server is built with the official Python SDK 2.x `MCPServer`. On the same stdio transport it
serves modern MCP `2026-07-28` self-describing requests (with optional capability discovery through
`server/discover`) and retains SDK-provided legacy MCP `2025-11-25` compatibility through
`initialize`. `stateless_http` is an HTTP transport option, not an MCP `2026-07-28` switch, so it
does not belong in this stdio server.

Modern protocol dispatch does not carry a hidden server session. BliSolver's ingest lifecycle is
application state instead: `extract_transcript` returns a `job_id`, every polling call sends that
handle explicitly, and job records, logs, and result envelopes persist under `${PLUGIN_DATA}` via
`BLISOLVER_DATA_DIR`. Restart recovery therefore depends on those persisted records and the
explicit handle, not on an MCP session.

This server does not call roots, sampling, or MCP protocol logging, and it does not introduce
MRTR/`input_required` flows. It also does not enable or declare the Tasks extension: the existing
`job_id` contract is a BliSolver tool-level API, not an MCP Task.

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

`mode` is advertised as an enum in `inputSchema`. Every start creates a new opaque UUIDv4 handle
(32 hexadecimal characters); existing short handles remain readable. Handles expire **7 days
after creation**, including records from earlier versions. Expiry prevents MCP polling; it neither
deletes bundles/caches nor cancels an already running ingest. A caller needing an expired job's
result can read its existing bundle locally or start a new job (which may reuse stage caches).

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

An unknown `job_id` returns `{"status": "unknown", "error": ...}`. Unknown, failed, and expired
jobs set **`isError: true`** on the MCP result; running and done jobs set it to false. Polling
responses carry the same payload in `structuredContent` and a serialized JSON `TextContent` block.
Invalid handles and modes are tool execution errors, not filesystem lookups or fallback modes.

The handle schema accepts 1–64 ASCII letters, digits, underscores, or hyphens. The job store also
validates handles before any lookup and rejects record symlinks escaping its directory. Records
are published atomically; in-memory process lookup/polling is locked for SDK worker threads.
The ingest child receives the server's explicit data/cache/output directories, so injected Settings
and inherited environment variables cannot direct the child into another store.

## Admission limits and trust boundary

Each running server instance uses thread-safe, rolling 60-second budgets: **20 probes**, **4 job
starts**, and **120 polls shared across the three get tools**. Exhaustion returns a tool execution
error with a retry delay before any provider call or subprocess launch. Budgets span client
connections but reset when the server is recreated; these are local admission controls, not a
cross-process queue or a cap on concurrently running ingest jobs.

Annotations distinguish a read-only network probe, local read-only polling, and non-idempotent
ingest that can download media, invoke models, and replace generated cache/bundle files. They are
hints for clients, not an authorization mechanism. Stdio runs with the launching OS user's file
permissions; this is a single-user local service, not an authenticated multi-tenant remote API.

## Specification audit (2026-09-05)

The official [latest specification](https://modelcontextprotocol.io/specification/latest) resolves
to **2026-07-28**. The [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
and [tool contract](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) are the
upstream references; handle lifetime/entropy guidance is non-normative design guidance, while
request/result fields and tool error signaling are protocol behavior.

`tests/test_mcp_protocol.py` checks raw stdio JSON-RPC without an SDK client: no handshake or
discovery prerequisite, `server/discover`, `resultType: "complete"`, list cache hints,
unsupported-version error `-32022`, error results, and stable tool ordering after requests carrying
different versions. It also checks modern/legacy tool calls, persisted jobs through a fresh server,
structured/text payload parity, tool annotations, validation, and admission limits. The SDK owns
wire encoding and version compatibility; the application does not duplicate that implementation.

## Environment and credentials

The server subprocess inherits its environment from the client. Provider credentials —
`SESSDATA`, `BLISOLVER_COOKIES_BROWSER`, `LMSTUDIO_API_KEY` — must already be present there, or in
the `.env` the application loads. They are deliberately **not** in `mcp.json`: Agent Plugins §9.2
states that configured `env` values are visible package data and must not carry secrets, and
`mcp.json` is committed to the repository.

Consequence worth knowing: a client that sanitizes the ambient environment will start a server with
no explicit bilibili session. Captions requiring login may be unavailable; when captions fail,
Whisper fallback still requires a working local ASR runtime. Diagnose with
`blisolver doctor`, whose `provider-auth` check reports presence without printing values.

## Failure isolation

Agent Plugins §7.2.2 requires a server that fails to start or connect not to invalidate the rest of
the plugin. A conformant client should therefore still discover and expose the Skill when the MCP
server is unavailable. Running `scripts/blisolver_cli.py` still requires a usable BliSolver Python
environment, because the Skill and MCP surfaces intentionally share that runtime.
