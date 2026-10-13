# Live acquisition pipeline

The collector normalizes the pinned blivedm upstream models into LiveEvent records.
A single bounded ingress queue feeds one journal writer. Only that writer dequeues
collection events, flushes each JSONL line, and adds it to the summary accumulator.
Each stream consumer has its own journal cursor, starts at the beginning, and drains
the journal after collection stops. Slow consumers do not compete with persistence
or hold a separate growing in-memory queue. Consumers can replay a completed session.
This fixes the original competing-consumer design without replacing the pipeline.

The ingress queue has a finite capacity. Overflow is counted and fails the session
explicitly; it is not silently presented as complete capture. Per-event flush is not
fsync, and neither the source protocol nor this client guarantees crash-proof or
exactly-once delivery. Summaries still retain session events in memory; very long
broadcasts should eventually use incremental aggregation with a journal rebuild.

The CLI generates a distinct output directory for every session. An explicit output
directory containing events.jsonl is rejected without modifying existing artifacts;
resume is not implemented. API/collector errors and cancellation close resources,
drain accepted events and write partial bundles while propagating the original error.
Transport failures yield unknown status; only an explicit offline result ends a run.
Status is checked even when a duration limit exists. WebSocket reconnect behavior
remains supplied by pinned blivedm; offline replay does not prove real reconnects.

Guard records use actual upstream unit price and quantity; source-2 giveaway notices
are omitted because they repeat the source-0 paid toast. Moderator and guard identity
come from admin and privilege_type. No deduplication of arbitrary reconnect replays is
claimed: not every upstream event has a stable identity.

Install live test dependencies with `uv pip install -e '.[dev,live]'`. Without the
optional live extra, the live-specific tests skip and the default suite can collect.
`tests/test_live_replay.py` covers upstream dataclasses, Unicode consumer content,
concurrent fast/slow readers, journal and summaries, notification accounting, unknown
status versus true end, output reuse refusal, overflow and startup/poll error cleanup.
These are offline replay tests; no production credentials or external live traffic
are used. SafetyGuard provides cooperative naming checks, not transport isolation or
account/TOS guarantees.

## Repair validation (2026-10-13)

Original PR head: `cc63fa5754d502091594ff723130a75473c9197b`.

- Fresh documented non-live development installation succeeded; default collection:
  706 selected tests, four external live tests deselected, live-specific modules skipped.
- Fresh installation including `live` succeeded with the immutable blivedm revision.
- Source distribution and wheel build succeeded.
- Final live + replay + plugin/MCP/packaging/skill contract selection: **136 passed**.
- Full offline suite after implementation changes: **727 passed, 7 failed, 4 deselected**.
  The subsequently added cancellation regression also passes in the final selection.
- Untouched main (`7a975310bc1120a973fedd47671a5234b6940705`) reproduced exactly
  the same seven failures in the same fresh dependency environment: **40 passed,
  7 failed** across MCP preflight/protocol tests. These concern SDK masking of tool
  error details and are outside this PR's changes.
- Ruff on live modules and both live test files passes; cli.py retains its two
  pre-existing exception-handler findings. `git diff --check` passes.
- No real live traffic, media downloads, production credentials or external model calls.
- No GitHub check runs were present on the original head. Default-suite failures
  remain open, so this repair does not assert a wholly green suite or merge readiness.

Replay includes cancellation, collector startup failure, secondary cleanup failure
without masking the original exception, and post-stop consumer drain. Partial bundles
expose `capture_complete=false`; ingress overflow also exposes `dropped_event_count`.

## Independent Codex review follow-up

The reviewer reproduced two additional defects against the first repair:

1. A consumer racing with delayed room status detection saw an empty journal and
   exited before collection started. Readers now wait through startup and only finish
   after the explicit `_finished` state is set following producer shutdown and drain.
2. The pinned upstream client catches fatal network-task exceptions and calls
   `on_client_stopped`; these were invisible to the session. The handler now records
   fatal termination, and run/stop propagate it and mark the bundle incomplete.
   Expected healthy producer shutdown is distinguished from fatal termination.

New regressions start the consumer before `run`, hold room status detection, then
verify it receives the later event; inject a fatal task into the actual pinned
BLiveClient wrapper without opening a connection; enqueue the final event during
producer shutdown; and overflow a capacity-one queue then check the delivered
journal, dropped count, failed outcome and partial bundle. Independent reviewer
reran all live tests: **33 passed** (one upstream aiohttp deprecation warning).
Plugin/MCP/packaging/skill contract tests: **107 passed**. Source and wheel rebuild
and touched-code Ruff pass.

The seven unrelated main-baseline failures have these precise assertions:

- `test_forced_asr_fails_before_resolve_files_or_subprocess[binary-auto]` and
  `[binary-legacy]`: expect `whisper-cli missing` in tool error text.
- The same test's `[model-auto]` and `[model-legacy]`: expect
  `whisper-model missing` in tool error text.
- The same test's `[both-auto]` and `[both-legacy]`: first expect
  `whisper-cli missing` (and then also the model diagnostic).
- `test_invalid_mode_and_start_rate_limit_do_not_launch_work`: expects
  `retry after` in the rejected fifth call's error text.

All receive generic `Error executing tool extract_transcript` instead: existing
handlers raise ordinary ValueError/runtime errors, which installed MCP 2.3.0 wraps
as UnexpectedToolError and masks. is_error rejection still occurs. The live CLI,
collector and session do not call extract_transcript or modify its handlers, ASR
preflight, call budget, MCP SDK dependency or these tests. The identical untouched
main reproduction establishes these as unrelated existing diagnostic defects.
They are not silently counted as passing and do not require unrelated repairs in
this live acquisition PR.

Final applicable full offline run: **732 passed, 11 deselected, one upstream
warning**. The 11 deselections comprise the seven explicitly documented main
baseline failures above and four opt-in external live tests. No other offline
tests were excluded. This supports the independent no-blocking-findings review;
it does not claim the unchanged main diagnostic failures have been fixed.
