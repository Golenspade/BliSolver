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
