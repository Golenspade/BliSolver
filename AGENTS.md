# Agent Instructions

## Scope

- BliSolver acquires and normalizes bilibili.com and YouTube videos into Atlas bundles.
- Keep acquisition deterministic. Summarization, entity extraction, and other interpretation belong downstream in Atlas.

## Operating This Repository

For runtime diagnosis, video probing/ingestion, or bundle inspection, first read
[the ingestion Skill](skills/blisolver-video-ingestion/SKILL.md), then only the reference for the
current stage. Do not preload all references. `.agents/skills/blisolver-video-ingestion` links to
that same canonical skill; do not maintain a second copy. Source-only maintenance uses the source
map below and does not require loading the entire operating workflow.

## Sources of Truth

When sources disagree, trust current source and tests; then `PROTOCOL.md`; then `SPEC.md` and the root `README.md` where they agree with code; then `CONTEXT.md`. Dated files under `docs/` are history and rationale only.

Use `skills/blisolver-video-ingestion/references/source-map.md` to locate the implementing source. Do not turn historical plans into current requirements.

## Architecture and Invariants

- Platform branching stops at `blisolver/providers/`; shared stages consume normalized provider types.
- A bundle has one authoritative transcript. Burned-in OCR is a parallel timeline, never a replacement.
- Provenance is load-bearing: preserve source, delivered language, model, quality gate, and per-cue tags.
- A per-part failure must not abort sibling parts.
- stdout is a machine channel for JSON or JSON-RPC; progress and diagnostics go to stderr.
- Take output paths from the CLI result envelope. Never reconstruct a bundle directory from an ID.

## Development Environment

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e ".[mcp,frames,vision,dev]"
```

| Task | Command |
|---|---|
| Offline preflight | `.venv/bin/python -m blisolver.cli doctor --json` |
| One test file | `.venv/bin/python -m pytest -q tests/test_name.py` |
| Default offline suite | `.venv/bin/python -m pytest -q` |
| Plugin/MCP/Skill contract | `.venv/bin/python -m pytest -q tests/test_agent_plugin.py tests/test_mcp.py tests/test_packaging.py tests/test_skill_docs.py` |
| Lint touched paths | `.venv/bin/ruff check <paths>` |

The wrapper integration tests require the repository-local `.venv`. `tests/test_python_floor.py` separately enforces the declared Python 3.11 floor.

## Validation and Cost Boundaries

- The default test suite is offline; live tests are marked `live` and require explicit intent plus network access.
- Run `doctor` before a first media operation in an environment, then use `probe` to estimate work cheaply.
- Do not run a real `ingest`, download media, use provider credentials, or invoke external models merely to validate an unrelated change.

## Agent Plugin, Skill, and MCP Constraints

- The repository root is the Agent Plugins root: keep `plugin.json`, `mcp.json`, `bin/`, `blisolver/`, and `skills/` co-located.
- Keep the Agent Plugins schema versions in `plugin.json` and `mcp.json` identical. Agent Plugins discovers skills at `skills/<name>/SKILL.md`; the `.agents/skills/` alias serves
  project-skill discovery in hosts that support that convention.
- Keep a stdio MCP `command` as one executable token. Persist generated state under `${PLUGIN_DATA}` and never put credentials in package configuration.
- The server uses Python SDK 2.x (`mcp>=2,<3`) and `MCPServer`. It serves modern MCP `2026-07-28` and retains SDK-provided compatibility with legacy `2025-11-25` clients.
- The transport remains stdio, so do not add the HTTP-only `stateless_http` option. Modern protocol dispatch is stateless, while BliSolver application state remains explicit: callers pass `job_id`, and generated job records live under `${PLUGIN_DATA}` rather than an MCP session.
- SDK 2.x runs synchronous tool handlers in worker threads. Keep required cross-call state persisted and make any process-local optimization safe for concurrent access; `_PROCS` must never become the only job record.
- Do not call roots, sampling, or MCP protocol logging; introduce MRTR/`input_required` flows; or enable/declare the Tasks extension unless BliSolver implements and tests that behavior deliberately.
- Keep `bin/blisolver-mcp` and the Skill runtime resolver behavior aligned; their regression tests must change together.

## Security, State, and Existing Baselines

- Never commit `.env`, cookies, `SESSDATA`, API keys, downloaded media, transcripts, caches, or bundles; never print secret values or whole environment dumps. Generated state belongs in `BLISOLVER_DATA_DIR`, `PLUGIN_DATA`, `cache/`, or `out/`.
- Repo-wide Ruff currently has known findings. Do not auto-fix unrelated files or claim the whole lint baseline is clean; avoid adding findings in touched code.
- Some prose still contains stale faster-whisper, optional-extra, or setup claims. Verify behavior in source and tests before repeating it, and keep any correction narrowly scoped.
