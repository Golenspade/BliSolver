"""Explicit, non-interactive adapter for ``blisolver ingest``."""

from __future__ import annotations

import argparse
import json
import sys

from _common import extract_url, resolve_runtime, run_command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run blisolver ingest with explicit pipeline flags."
    )
    parser.add_argument("url", help="bilibili.com or YouTube URL")
    parser.add_argument("--project-root", help="BliSolver checkout to use")
    parser.add_argument("--dry-run", action="store_true", help="print the child command without running it")
    parser.add_argument("--part", type=int, help="1-based part index")
    parser.add_argument("--all-parts", action="store_true", help="process every available part")
    parser.add_argument("--force-whisper", action="store_true", help="skip subtitle reuse")
    parser.add_argument("--lang", help="transcription language override")
    parser.add_argument("--robust", action="store_true", help="disable previous-text context")
    parser.add_argument("--no-vision", action="store_true", help="skip frame captioning")
    parser.add_argument("--dedup-threshold", type=int, help="phash hamming threshold")
    parser.add_argument("--scene-threshold", type=float, help="deprecated blisolver compatibility flag")
    parser.add_argument("--out", help="output root")
    parser.add_argument("--no-frame-images", action="store_true", help="omit delivered PNGs")
    parser.add_argument("--danmaku", action="store_true", help="fetch bilibili danmaku")
    parser.add_argument("--interactions", action="store_true", help="fetch bilibili interactions")
    parser.add_argument("--ocr", action="store_true", help="run optional hard-subtitle OCR")
    parser.add_argument("--force-ocr", action="store_true", help="skip hard-subtitle pre-detection")
    return parser


def _flag(command: list[str], flag: str, value: object | None) -> None:
    if value is not None:
        command.extend((flag, str(value)))


def _build_command(runtime, args: argparse.Namespace) -> list[str]:
    url = extract_url(args.url)
    command = [*runtime.command, "ingest", url]
    _flag(command, "--part", args.part)
    if args.all_parts:
        command.append("--all-parts")
    if args.force_whisper:
        command.append("--force-whisper")
    _flag(command, "--lang", args.lang)
    if args.robust:
        command.append("--robust")
    if args.no_vision:
        command.append("--no-vision")
    _flag(command, "--dedup-threshold", args.dedup_threshold)
    _flag(command, "--scene-threshold", args.scene_threshold)
    _flag(command, "--out", args.out)
    if args.no_frame_images:
        command.append("--no-frame-images")
    if args.danmaku:
        command.append("--danmaku")
    if args.interactions:
        command.append("--interactions")
    if args.ocr:
        command.append("--ocr")
    if args.force_ocr:
        command.append("--force-ocr")
    return command


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        runtime = resolve_runtime(args.project_root)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    command = _build_command(runtime, args)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "command": command,
                    "cwd": str(runtime.cwd) if runtime.cwd else None,
                    "runtime": runtime.kind,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0

    result = run_command(command, cwd=runtime.cwd, env=runtime.env)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
