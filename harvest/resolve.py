"""URL resolution (SPEC §5 step 1, D12): expand b23.tv, detect platform/id/part.

Emits a canonical {platform, id, part, url}. The single-part {platform, id, part} triple is
the atomic identity unit (D12); --all-parts loops this resolver per part upstream.
"""

from __future__ import annotations

import re
import urllib.request
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .config import REFERER
from .providers.base import Canonical  # re-exported for backward-compatible import paths
from .schema import Platform

_COM_ID = re.compile(r"(BV[0-9A-Za-z]+|av\d+)", re.IGNORECASE)
_URL_RE = re.compile(r"(https?://[^\s\"'>]+|//[^\s\"'>]+)")


def extract_url(text: str) -> str:
    """Extract and sanitize a valid video URL from raw input text.

    Handles pasted text with Chinese title prefixes, <iframe...> HTML snippets,
    player.bilibili.com embed links, and extra tracking query parameters.
    """
    if not text:
        return ""

    match = _URL_RE.search(text)
    url = match.group(1) if match else text.strip()

    if url.startswith("//"):
        url = "https:" + url

    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if "player.bilibili.com" in host:
        qs = parse_qs(parsed.query)
        bvid = qs.get("bvid", [None])[0]
        part = qs.get("p", ["1"])[0]
        if bvid:
            canon = f"https://www.bilibili.com/video/{bvid}"
            if part and part != "1":
                canon += f"?p={part}"
            return canon

    return url


def _expand_b23(url: str) -> str:
    """Follow a b23.tv short link to its final bilibili URL (Referer required, SPEC §7)."""
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "Mozilla/5.0", "Referer": REFERER},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.geturl()


def resolve(url: str, expander: Callable[[str], str] | None = None) -> Canonical:
    url = extract_url(url)
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if host.endswith("b23.tv"):
        expand = expander or _expand_b23
        url = expand(url)
        parsed = urlparse(url)
        host = parsed.netloc.lower()

    if host.endswith("bilibili.com"):
        platform: Platform = "bilibili.com"
    elif host.endswith("bilibili.tv"):
        platform = "bilibili.tv"
    else:
        raise ValueError(f"not a bilibili URL: {url}")

    segments = [s for s in parsed.path.split("/") if s]
    if "video" not in segments:
        raise ValueError(f"no video id in URL path: {url}")
    vi = segments.index("video")
    raw = segments[vi + 1] if vi + 1 < len(segments) else ""

    part = int(parse_qs(parsed.query).get("p", ["1"])[0])

    if platform == "bilibili.com":
        m = _COM_ID.match(raw)
        if not m:
            raise ValueError(f"unrecognized bilibili.com video id: {raw!r}")
        vid = m.group(1)
        canon = f"https://www.bilibili.com/video/{vid}"
    else:  # bilibili.tv
        if not raw.isdigit():
            raise ValueError(f"unrecognized bilibili.tv video id: {raw!r}")
        vid = raw
        canon = f"https://www.bilibili.tv/en/video/{vid}"

    if part > 1:
        canon += f"?p={part}"

    return Canonical(platform=platform, id=vid, part=part, url=canon)
