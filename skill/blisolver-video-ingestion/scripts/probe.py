"""JSON-safe adapter for the blisolver probe command."""

from __future__ import annotations

import argparse
import json
import sys

from _common import extract_url, resolve_runtime, run_command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run blisolver probe and keep stdout safe for a JSON parser."
    )
    parser.add_argument("url", help="bilibili.com or YouTube URL")
    parser.add_argument("--project-root", help="BliSolver checkout to use")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    url = extract_url(args.url)
    try:
        runtime = resolve_runtime(args.project_root)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = run_command(
        (*runtime.command, "probe", url),
        cwd=runtime.cwd,
        env=runtime.env,
        capture_output=True,
    )
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
        return result.returncode

    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        print("error: blisolver probe returned non-JSON stdout", file=sys.stderr)
        if result.stdout:
            print(result.stdout[:4000], file=sys.stderr, end="" if result.stdout.endswith("\n") else "\n")
        return 1
    if not isinstance(payload, dict):
        print("error: blisolver probe stdout is not a JSON object", file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
