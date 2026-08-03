"""Multi-part orchestration (D12): enumerate parts and run the single-part pipeline per part
with per-part failure isolation.

The {platform, id, part} triple is the atomic identity unit (D12). A bilibili multi-part video
is one id with N parts; yt-dlp returns the whole set as a playlist when asked for the bare URL,
and a single part when asked for `?p=N`. So --all-parts = "count parts, then loop ?p=N".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .resolve import Canonical


def part_url(base_url: str, part: int) -> str:
    """Canonical per-part URL: set/override ?p=<part>, preserving every other query param.

    bilibili carries its id in the path (`/video/BVxxx`), so only `p=` matters there. YouTube
    carries its id in the query (`watch?v=<id>`), so we must NOT clobber the whole query —
    dropping `v=` yields `watch?p=1`, a video-less feed yt-dlp reports as title='recommended'
    with no duration (issue #7). Merge instead of replace; `p=` is harmless to YouTube."""
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query))
    query["p"] = str(part)
    return urlunparse(parsed._replace(query=urlencode(query)))


def select_parts(args, canonical: Canonical, *, total: int) -> list[int]:
    """Which 1-based parts to run: all of them, an explicit --part, or the URL's part."""
    if getattr(args, "all_parts", False):
        return list(range(1, total + 1))
    if getattr(args, "part", None) is not None:
        return [args.part]
    return [canonical.part]


@dataclass
class PartResult:
    part: int
    ok: bool
    error: str | None = None


def run_parts(
    canonical: Canonical,
    parts: list[int],
    *,
    settings,
    args,
    processor: Callable[[Canonical, object, object], None],
) -> list[PartResult]:
    """Run `processor` for each selected part, isolating failures so one bad part (private,
    region-locked, transient CDN error) never aborts the rest of the batch."""
    results: list[PartResult] = []
    for p in parts:
        per_part = Canonical(
            canonical.platform, canonical.id, p, part_url(canonical.url, p)
        )
        try:
            processor(per_part, settings, args)
            results.append(PartResult(p, True))
        except Exception as exc:  # noqa: BLE001 - isolation is the whole point
            results.append(PartResult(p, False, f"{type(exc).__name__}: {exc}"))
    return results
