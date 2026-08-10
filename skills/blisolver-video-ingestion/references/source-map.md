# Source map

The skill lives inside the plugin, beside the application. Paths below are relative to the plugin
root, which is three directories above `scripts/`. Every claim in this skill is checkable there.

## Where to verify a claim

| Claim | Source |
|---|---|
| CLI verbs, flags, stream discipline, `--json` envelope | `blisolver/cli.py` |
| preflight checks and their stage labels | `blisolver/doctor.py` |
| schema version and Pydantic shapes | `blisolver/schema.py` |
| data directory precedence, thresholds, env names | `blisolver/config.py`, `.env.example` |
| provider registry and the normalized seam | `blisolver/providers/base.py` |
| URL normalization (title prefixes, embeds, b23.tv) | `blisolver/resolve.py` |
| bilibili acquisition and auth | `blisolver/providers/bilibili.py`, `blisolver/player_api.py` |
| candidate track order, censorship fallback, `track_language` | `blisolver/subtitles.py` |
| quality gate, including its language adaptation | `blisolver/quality.py` |
| YouTube acquisition and caption tiers | `blisolver/providers/youtube.py`, `blisolver/providers/youtube_autosub.py` |
| ASR backend and its flags | `blisolver/transcribe.py` |
| frame extraction and phash dedup | `blisolver/frames.py` |
| LM Studio and projector verification | `blisolver/vision.py` |
| burned-in OCR | `blisolver/detect_hardsubs.py`, `blisolver/ocr.py`, `scripts/ocr_worker.py` |
| fusion diagnostics | `blisolver/fuse.py` |
| bundle rendering and the output directory name | `blisolver/merge.py` |
| MCP tools and job lifecycle | `blisolver/mcp/server.py` |
| how a client launches the MCP server | `mcp.json`, `bin/blisolver-mcp` |
| plugin identity and metadata | `plugin.json` |
| danmaku representation, interaction whitelist | `blisolver/danmaku.py`, `blisolver/interactions.py` |

## Tests as executable truth

Check a test before trusting prose. The offline suite is the fastest way to learn actual behavior.

| Question | Test |
|---|---|
| does the package satisfy Agent Plugins 1.0.0? | `tests/test_agent_plugin.py` |
| what do the skill's scripts guarantee? | `tests/test_portable_skill.py` |
| what does `ingest --json` promise, and where does state go? | `tests/test_ingest_contract.py` |
| what does the censorship fallback record? | `tests/test_censorship_fallback.py` |
| what does preflight actually check? | `tests/test_doctor.py` |
| MCP job status inference | `tests/test_mcp.py` |
| CLI parsing and the transcript decision | `tests/test_cli.py` |

Live-network tests are marked `live` and excluded by default.

## Documents, ranked

Current source and tests outrank all prose. Then `PROTOCOL.md` (the Atlas-facing contract),
`SPEC.md`, and the root `README.md` where they agree with the code. Then `CONTEXT.md`. Dated files
under `docs/superpowers/{plans,specs}/` and `docs/phase-*.md` explain history and decisions; they are
not runtime discovery sources.

## Known stale statements elsewhere in the repository

* `pyproject.toml` still declares a `transcribe` optional-dependency group containing faster-whisper
  and NVIDIA CUDA wheels. The implementation is whisper.cpp via `whisper-cli`. The group is vestigial.
* Older prose describes schema 1.0. Current bundles are 1.1, with per-cue provenance and
  `Bundle.ocr`.
* Historical bilibili-only descriptions predate the YouTube provider.
* `bilibili.tv` appears in the `Platform` type but is rejected at runtime as deferred.
* Documents written before the cross-language fallback describe acquisition as always
  original-language. It is not; see `references/provider-guide.md`.
