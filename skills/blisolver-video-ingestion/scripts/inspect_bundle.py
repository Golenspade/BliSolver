"""Summarize a local blisolver bundle without exposing its text bodies."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import bundle_json_path, safe_child_path


def _read_bundle(value: str) -> tuple[Path, Path, dict]:
    bundle_dir, json_path = bundle_json_path(value)
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {json_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"bundle root must be an object: {json_path}")
    return bundle_dir, json_path, payload


def summarize(value: str) -> dict:
    bundle_dir, json_path, data = _read_bundle(value)
    transcript = data.get("transcript") or {}
    ocr = data.get("ocr") or []
    frames = data.get("frames") or []
    danmaku = data.get("danmaku")
    interactions = data.get("interactions")

    missing_images = 0
    with_image_path = 0
    with_caption = 0
    with_ocr = 0
    for frame in frames if isinstance(frames, list) else []:
        if not isinstance(frame, dict):
            missing_images += 1
            continue
        if frame.get("caption"):
            with_caption += 1
        if frame.get("ocr"):
            with_ocr += 1
        path = frame.get("path")
        if path is None:
            continue
        with_image_path += 1
        if not isinstance(path, str) or safe_child_path(bundle_dir, path) is None:
            missing_images += 1
        else:
            resolved = safe_child_path(bundle_dir, path)
            if resolved is None or not resolved.is_file():
                missing_images += 1

    if isinstance(danmaku, dict):
        windows = danmaku.get("windows") or []
        line_count = sum(
            len(window.get("lines") or [])
            for window in windows
            if isinstance(window, dict)
        )
        danmaku_summary = {
            "windows": len(windows),
            "lines": line_count,
            "fetched_total": danmaku.get("fetched_total"),
        }
    else:
        danmaku_summary = None

    if isinstance(interactions, dict):
        interactions_summary = {
            "votes": len(interactions.get("votes") or []),
            "grades": len(interactions.get("grades") or []),
        }
    else:
        interactions_summary = None

    return {
        "bundle_path": str(bundle_dir),
        "schema_version": data.get("schema_version"),
        "identity": {
            key: data.get(key)
            for key in ("platform", "id", "part", "url", "title", "uploader", "original_language", "available_subtitles")
        },
        "transcript": {
            "source": transcript.get("source"),
            "language": transcript.get("language"),
            "segments": len(transcript.get("segments") or []),
        },
        "ocr": {"segments": len(ocr) if isinstance(ocr, list) else 0},
        "frames": {
            "count": len(frames) if isinstance(frames, list) else 0,
            "with_caption": with_caption,
            "with_ocr": with_ocr,
            "with_image_path": with_image_path,
            "missing_images": missing_images,
        },
        "danmaku": danmaku_summary,
        "interactions": interactions_summary,
        "artifacts": {
            "bundle_json": str(json_path),
            "bundle_md": (bundle_dir / "bundle.md").is_file(),
            "frames_directory": (bundle_dir / "frames").is_dir(),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize a local bundle without printing transcript or danmaku bodies."
    )
    parser.add_argument("bundle", help="bundle directory or bundle.json path")
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = summarize(args.bundle)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
