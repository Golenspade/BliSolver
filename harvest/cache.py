"""Per-stage caching keyed by video-identity + stage-param-hash (SPEC §5, D6).

The bare `{platform}:{id}:{part}` key is correct only for stages depending solely on the video
(audio, subtitle-probe). Param-dependent stages append a short hash of their determining params so
that changing one flag (e.g. --dedup-threshold) never invalidates the whole video's cache.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def stage_key(platform: str, id: str, part: int, **params) -> str:
    base = f"{platform}:{id}:{part}"
    if not params:
        return base
    blob = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]
    return f"{base}#{digest}"


def fs_key(platform: str, id: str, part: int, **params) -> str:
    """stage_key rendered safe for use as a filename/dir component on Windows."""
    return stage_key(platform, id, part, **params).replace(":", "_").replace("#", "_")


def _stage_dir(cache_dir: Path, stage: str) -> Path:
    d = cache_dir / stage
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(cache_dir: Path, stage: str, key: str) -> Any | None:
    f = _stage_dir(cache_dir, stage) / f"{key}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return None


def save_json(cache_dir: Path, stage: str, key: str, data: Any) -> None:
    f = _stage_dir(cache_dir, stage) / f"{key}.json"
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
