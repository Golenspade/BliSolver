"""Validate a local blisolver bundle against the current schema and artifact layout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import bundle_json_path, resolve_project_root, safe_child_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate bundle.json, bundle.md, schema 1.1, and frame artifacts."
    )
    parser.add_argument("bundle", help="bundle directory or bundle.json path")
    parser.add_argument("--project-root", help="BliSolver checkout to use for schema validation")
    return parser


def _load_schema(project_root: str | None):
    root = resolve_project_root(project_root)
    if root:
        root_text = str(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
    from blisolver.schema import Bundle, SCHEMA_VERSION

    return Bundle, SCHEMA_VERSION


def validate(value: str, project_root: str | None = None) -> dict:
    bundle_dir, json_path = bundle_json_path(value)
    report = {
        "valid": True,
        "bundle_path": str(bundle_dir),
        "schema_version": None,
        "errors": [],
        "warnings": [],
    }

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report["valid"] = False
        report["errors"].append(f"invalid JSON: {exc}")
        return report
    if not isinstance(data, dict):
        report["valid"] = False
        report["errors"].append("bundle root must be a JSON object")
        return report

    report["schema_version"] = data.get("schema_version")
    Bundle, expected_schema = _load_schema(project_root)
    try:
        Bundle.model_validate(data)
    except Exception as exc:  # Pydantic's ValidationError varies across supported versions.
        report["valid"] = False
        report["errors"].append(f"schema validation failed: {exc}")

    if data.get("schema_version") != expected_schema:
        report["valid"] = False
        report["errors"].append(
            f"schema_version is {data.get('schema_version')!r}; expected {expected_schema!r}"
        )

    if not (bundle_dir / "bundle.md").is_file():
        report["valid"] = False
        report["errors"].append("bundle.md is missing")

    frames = data.get("frames") or []
    if not isinstance(frames, list):
        report["valid"] = False
        report["errors"].append("frames must be an array")
        frames = []
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            report["valid"] = False
            report["errors"].append(f"frame {index} must be an object")
            continue
        path = frame.get("path")
        if path is None:
            continue
        if not isinstance(path, str):
            report["valid"] = False
            report["errors"].append(f"frame {index} path must be a string or null")
            continue
        resolved = safe_child_path(bundle_dir, path)
        if resolved is None:
            report["valid"] = False
            report["errors"].append(f"frame {index} path points outside bundle directory: {path}")
        elif not resolved.is_file():
            report["valid"] = False
            report["errors"].append(f"frame {index} image is missing: {path}")

    return report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = validate(args.bundle, args.project_root)
    except (FileNotFoundError, OSError, ImportError, ModuleNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
