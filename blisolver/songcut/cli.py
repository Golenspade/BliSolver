"""CLI adapter; importing the main CLI does not load optional numerical/API packages."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .models import Manifest
from .state import redact


def add_parser(sub):
    parser = sub.add_parser("songcut", help="制作歌切: audio production and draft live lyrics")
    parser.add_argument("source", nargs="?", help="local media, BV/AV, or supported video URL")
    parser.add_argument("--manifest", help="JSON batch manifest; paths resolve relative to it")
    parser.add_argument("--id", default=None, help="safe clip ID for a single source")
    parser.add_argument("--title", default=None, help="song title, also used by optional lyric lookup")
    parser.add_argument("--part", type=int, default=None, help="1-based source part")
    parser.add_argument("--start", type=float, default=None, help="source start in seconds")
    parser.add_argument("--end", type=float, default=None, help="exclusive source end in seconds")
    parser.add_argument("--lang", default=None, help="ASR language hint; otherwise auto-detect")
    parser.add_argument("--backend", choices=["none", "whisper", "dashscope"], default=None)
    parser.add_argument("--normalization", choices=["preserve", "dynamic"], default=None)
    parser.add_argument("--reference-audio", action="append", default=None,
                        help="existing recording to compare; repeatable")
    parser.add_argument("--reference-lyrics", default=None, help="local UTF-8 reference lyrics")
    parser.add_argument("--lookup-lyrics", action="store_true", default=None,
                        help="opt in to external LRCLIB reference search")
    parser.add_argument("--budget-cny", type=float, default=None,
                        help="positive estimated cloud spend limit; required for dashscope")
    parser.add_argument("--upload", choices=["s3", "temporary"], default=None,
                        help="private object storage, or explicit development-only temporary upload")
    parser.add_argument("--workers", type=int, default=None, help="parallel clips, maximum 7")
    parser.add_argument("--out", default=None, help="stable output root; reuse it to resume")
    parser.add_argument("--plan", action="store_true", help="validate and print plan without I/O or API calls")
    parser.add_argument("--json", action="store_true", help="one JSON result (also the default)")
    return parser


def resolve_paths(data: dict, base: Path) -> dict:
    def local(value):
        path = Path(value).expanduser()
        return str((base / path).resolve() if not path.is_absolute() else path.resolve())
    for clip in data.get("clips", []):
        source = clip.get("source", "")
        if not re.match(r"^(?:https?://|BV[A-Za-z0-9]+$|av\d+$)", source):
            clip["source"] = local(source)
        if clip.get("reference_lyrics"):
            clip["reference_lyrics"] = local(clip["reference_lyrics"])
    data["reference_audio"] = [local(p) for p in data.get("reference_audio", [])]
    return data


def manifest_from_args(args) -> Manifest:
    if bool(args.source) == bool(args.manifest):
        raise ValueError("provide exactly one source or --manifest")
    single = {"id": args.id, "title": args.title, "part": args.part, "start": args.start,
              "end": args.end, "language": args.lang, "reference_lyrics": args.reference_lyrics}
    if args.manifest:
        if any(value is not None for value in single.values()):
            raise ValueError("single-clip flags cannot be combined with --manifest")
        path = Path(args.manifest).expanduser().resolve()
        data = resolve_paths(json.loads(path.read_text(encoding="utf-8")), path.parent)
    else:
        clip = {"id": "songcut", "source": args.source,
                **{key: value for key, value in single.items() if value is not None}}
        data = resolve_paths({"clips": [clip]}, Path.cwd())
    for name in ("backend", "workers"):
        if getattr(args, name) is not None:
            data[name] = getattr(args, name)
    if args.normalization is not None:
        data.setdefault("audio", {})["mode"] = args.normalization
    if args.reference_audio is not None:
        data["reference_audio"] = [str(Path(p).expanduser().resolve()) for p in args.reference_audio]
    if args.lookup_lyrics is not None:
        data["reference_lookup"] = args.lookup_lyrics
    for name in ("budget_cny", "upload"):
        if getattr(args, name) is not None:
            data.setdefault("cloud", {})[name] = getattr(args, name)
    return Manifest.model_validate(data)


def main(args) -> int:
    try:
        manifest = manifest_from_args(args)
        if args.plan:
            result = {"status": "planned", "manifest": manifest.model_dump(),
                      "network_calls": 0, "paid_calls": 0,
                      "stages": ["acquire", "cut", "recording_dedup", "master", "asr",
                                 "short_windows", "presence", "reference", "live_lyrics", "qc"],
                      "note": "No media was probed. Availability and cost remain unverified."}
        else:
            from ..config import Settings
            from .pipeline import run
            settings = Settings.load()
            out = Path(args.out) if args.out else settings.out_dir / "songcut"
            result = run(manifest, out, settings)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result["status"] in {"planned", "complete"} else 1
    except Exception as exc:  # noqa: BLE001 - CLI must return a sanitized JSON error envelope
        # Pydantic validation messages can echo unknown input credentials. Exclude input values.
        from pydantic import ValidationError
        error = exc.errors(include_input=False, include_url=False) if isinstance(exc, ValidationError) else str(exc)
        if isinstance(error, list):
            error = [{"location": list(row["loc"]), "message": row["msg"]} for row in error]
        print(json.dumps({"status": "failed", "error": redact(error)}, ensure_ascii=False))
        print("[songcut] failed; see JSON result", file=sys.stderr)
        return 1
