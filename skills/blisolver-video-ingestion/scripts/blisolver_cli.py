#!/usr/bin/env python3
"""Run the blisolver CLI with an interpreter that has its dependencies.

This is a pass-through, not an adapter. It resolves the plugin root and interpreter, then forwards
every remaining argument to `blisolver` untouched.

Why pass-through and not a flag mirror
--------------------------------------
The previous wrapper re-declared each CLI flag in its own argparse parser and rebuilt the child
command from the parsed namespace. That made the wrapper a second, hand-maintained copy of the
CLI's interface, so every flag added to `blisolver/cli.py` silently failed to reach the pipeline
until someone remembered to mirror it here. Forwarding the argument list verbatim removes the
possibility: there is nothing to keep in sync. Argument arrays are passed to `subprocess` directly,
so nothing is re-parsed by a shell either.

Usage:
  blisolver_cli.py [--plugin-root PATH] [--show-command] [--] <blisolver args...>

Wrapper options must come first. `--show-command` prints the resolved child command as JSON and
exits without running it — use it before a job that costs minutes of media processing.

Examples:
  blisolver_cli.py doctor --json
  blisolver_cli.py probe 'https://www.bilibili.com/video/BV...'
  blisolver_cli.py --show-command ingest 'https://...' --no-vision --ocr
  blisolver_cli.py ingest 'https://...' --part 2 --danmaku
"""

from __future__ import annotations

import sys

from _runtime import RuntimeError_, emit, fail, resolve_runtime, run_cli

WRAPPER_FLAGS = {"--plugin-root", "--show-command", "--"}


def _split_argv(argv: list[str]) -> tuple[str | None, bool, list[str]]:
    """Peel leading wrapper flags off the front; everything after belongs to the CLI.

    Stops at the first token that is not a wrapper flag so a passthrough argument that happens to
    share a name is never captured.
    """
    plugin_root: str | None = None
    show_command = False
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--":
            i += 1
            break
        if token == "--show-command":
            show_command = True
            i += 1
        elif token == "--plugin-root":
            if i + 1 >= len(argv):
                raise ValueError("--plugin-root requires a path")
            plugin_root = argv[i + 1]
            i += 2
        elif token.startswith("--plugin-root="):
            plugin_root = token.split("=", 1)[1]
            i += 1
        else:
            break
    return plugin_root, show_command, argv[i:]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("--help", "-h"):
        print(__doc__.strip())
        return 0

    try:
        plugin_root, show_command, forwarded = _split_argv(argv)
    except ValueError as exc:
        return fail(str(exc))

    if not forwarded:
        return fail("no blisolver arguments given; try: blisolver_cli.py doctor")

    try:
        runtime = resolve_runtime(plugin_root)
    except RuntimeError_ as exc:
        return fail(str(exc))

    if show_command:
        emit(
            {
                "command": [*runtime.command, *forwarded],
                "cwd": str(runtime.cwd),
                "runtime": runtime.kind,
                "interpreter": runtime.interpreter,
            }
        )
        return 0

    # Streamed, not captured: `ingest` runs for minutes and its progress output is the only signal
    # a caller has while it works. The child's exit code is preserved.
    return run_cli(runtime, forwarded).returncode


if __name__ == "__main__":
    raise SystemExit(main())
