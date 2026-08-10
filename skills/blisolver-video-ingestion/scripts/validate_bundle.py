"""Validate a local blisolver bundle against the current schema and artifact layout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _runtime import bundle_json_path, plugin_root, safe_child_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate bundle.json, bundle.md, the current schema, and frame artifacts."
    )
    parser.add_argument("bundle", help="bundle directory or bundle.json path")
    parser.add_argument("--plugin-root", help="plugin root to import the schema from")
    return parser


def _load_schema(explicit_root: str | None):
    """Import the live Pydantic contract from the application in this package.

    The expected schema version is read from `blisolver.schema.SCHEMA_VERSION` rather than pinned
    here, so a schema bump cannot leave this validator asserting a stale version number.

    A directly-executed script puts its own directory on `sys.path[0]`, which would let any sibling
    module shadow the `blisolver` package. That entry is dropped before the import rather than
    trusted, so the validator always reads the application and never a same-named neighbour.
    """
    root = plugin_root(explicit_root)
    here = str(Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if p not in ("", ".", here)]
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from blisolver.schema import SCHEMA_VERSION, Bundle

    return Bundle, SCHEMA_VERSION


def validate(value: str, explicit_root: str | None = None) -> dict:
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
    Bundle, expected_schema = _load_schema(explicit_root)
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
        report = validate(args.bundle, args.plugin_root)
    except (FileNotFoundError, OSError, ImportError, ModuleNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
