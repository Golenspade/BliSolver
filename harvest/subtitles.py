"""yt-dlp subtitle probe (SPEC §5 step 2, §7, D4, D9, D11).

Drives yt-dlp's Python API with skip_download to surface original-language subtitles (bilibili
.com AI auto-captions land as a normal track). Never touches media here. The #6357 part-match
assertion (D4) guards against silently aligning the wrong part's text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import yt_dlp

from .config import REFERER, Settings
from .resolve import Canonical
from .schema import Segment

# Original-language zh keys we accept, in preference order. Human subs (info["subtitles"])
# outrank AI captions (info["automatic_captions"]); within each we prefer simplified zh.
_ZH_KEYS = ("zh-Hans", "zh-CN", "zh", "ai-zh")


@dataclass
class SubtitleResult:
    found: bool
    source: str | None  # "human-sub" | "auto-sub" | None
    lang: str | None
    segments: list[Segment] = field(default_factory=list)
    reason: str = ""  # human-readable, flows into the D2 bundle.md header
    last_cue_end: float | None = None


def ydl_opts(
    settings: Settings,
    *,
    skip_download: bool = True,
    referer: str | None = REFERER,
    browser_cookies: bool = True,
    external_downloader: bool = True,
) -> dict:
    """Common yt-dlp options: auth (D9), Referer (§7, scoped to bilibili by default — YouTube
    callers pass referer=None so bilibili's Referer is never sent on YouTube requests), ffmpeg
    location.

    `browser_cookies=False` omits the `cookiesfrombrowser` jar (issue #1): YouTube extraction
    breaks ("Requested format is not available") when a logged-in browser session is attached,
    so YouTube callers opt out unless the user opts in via HARVEST_YT_COOKIES. bilibili keeps
    the jar (its default, cookies effectively required).

    `external_downloader=False` keeps aria2c out of the media download (issue #3): aria2c's
    parallel connections are a throttled-bilibili-CDN optimization; YouTube throttles them to a
    crawl and aria2c bypasses yt-dlp's n-signature handling, so downloads stall or "succeed"
    without writing a file. Non-bilibili media callers pass False to use the native downloader."""
    headers: dict = {}
    if referer:
        headers["Referer"] = referer
    opts: dict = {
        "skip_download": skip_download,
        "quiet": True,
        "no_warnings": True,
        "http_headers": headers,
        # bilibili CDN can be slow/flaky; be patient and resume partials.
        "socket_timeout": 60,
        "retries": 10,
        "fragment_retries": 10,
        "continuedl": True,
    }
    if external_downloader and settings.aria2c_path:
        # aria2c saturates throttled bilibili CDNs with parallel connections + robust resume.
        opts["external_downloader"] = {"default": settings.aria2c_path}
        opts["external_downloader_args"] = {
            "aria2c": [
                "-x16", "-s16", "-k1M", "--retry-wait=2", "--max-tries=10",
                "--disable-ipv6=true",  # Akamai mirrors resolve to unreachable IPv6 on this box
            ]
        }
    else:
        # Native fallback: ranged chunks so a stall loses only one chunk, not the whole file.
        opts["http_chunk_size"] = 10 * 1024 * 1024
    if settings.sessdata:
        headers["Cookie"] = f"SESSDATA={settings.sessdata}"
    elif browser_cookies:
        profile = settings.cookies_profile or None
        opts["cookiesfrombrowser"] = (settings.cookies_browser, profile, None, None)
    if settings.ffmpeg_path:
        from pathlib import Path

        opts["ffmpeg_location"] = str(Path(settings.ffmpeg_path).parent)
    if settings.js_runtime:
        # A JS runtime lets yt-dlp use YouTube's real web player client (issue #5); without one,
        # extraction intermittently degrades to a stripped response. Harmless for bilibili — the
        # runtime is only exercised when a challenge actually needs solving.
        name, path = settings.js_runtime
        opts["js_runtimes"] = {name: {"path": path}}
    return opts


def extract_info(url: str, settings: Settings) -> dict:
    """Fetch yt-dlp info for a specific part URL (no media). Fails loud per D11 if the cookie
    source itself can't be read (yt-dlp raises a clear DownloadError).

    IMPORTANT: bilibili's extractor fetches subtitles LAZILY — it only populates
    `info["subtitles"]` when `writesubtitles`/`writeautomaticsub` are requested. Without these
    flags the subtitle list comes back `{}` even when tracks exist (verified on BV1dSKJ6wEVz:
    `--list-subs` shows zh+ai-zh, but plain extract_info returns {}). This is subtitle METADATA
    extraction only — `skip_download` stays True, so NO subtitle FILE is written to disk. The
    download path (transcribe/frames) uses ydl_opts() WITHOUT these flags, so it is unaffected."""
    opts = ydl_opts(settings)
    opts["writesubtitles"] = True
    opts["writeautomaticsub"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _pick_track(info: dict) -> tuple[str, str, list] | None:
    """Return (source_label, lang_key, formats) for the best original-zh track, or None."""
    human = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    for key in _ZH_KEYS:
        if key in human:
            return "human-sub", key, human[key]
    for key in _ZH_KEYS:
        if key in auto:
            return "auto-sub", key, auto[key]
    return None


def _download_track(formats: list, settings: Settings) -> tuple[str, str]:
    """Fetch a subtitle track's raw text. Returns (text, ext). Prefers json(bcc)/srt/vtt formats.

    Two delivery shapes: yt-dlp embeds the body inline as `data` (bilibili does this — no url
    to fetch), OR it provides a `url` to urlopen (YouTube timed-text). `data` wins when present;
    the url path falls back to a YoutubeDL.urlopen with the shared cookie jar."""
    ordered = sorted(formats, key=lambda f: 0 if f.get("ext") in ("json", "srt", "vtt") else 1)
    for f in ordered:
        ext = f.get("ext") or ""
        data = f.get("data")
        if data:
            return data, ext
        url = f.get("url")
        if not url:
            continue
        with yt_dlp.YoutubeDL(ydl_opts(settings)) as ydl:
            raw = ydl.urlopen(url).read().decode("utf-8", "replace")
        return raw, ext
    raise ValueError("subtitle track had no fetchable url or inline data")


def parse_bcc(text: str) -> list[Segment]:
    """bilibili bcc/json subtitle: {"body":[{"from":..,"to":..,"content":".."}]}."""
    data = json.loads(text)
    body = data.get("body", data) if isinstance(data, dict) else data
    out: list[Segment] = []
    for cue in body:
        out.append(
            Segment(
                start=float(cue["from"]),
                end=float(cue["to"]),
                text=str(cue.get("content", "")).strip(),
            )
        )
    return out


_SRT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def parse_srt(text: str) -> list[Segment]:
    out: list[Segment] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        m = _SRT_TIME.search(block)
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        lines = block.split("\n")
        ti = next((i for i, ln in enumerate(lines) if "-->" in ln), 0)
        body = " ".join(ln.strip() for ln in lines[ti + 1 :] if ln.strip())
        out.append(Segment(start=start, end=end, text=body))
    return out


def parse_vtt(text: str) -> list[Segment]:
    """WebVTT (YouTube timed-text). Blank-line-delimited cue blocks; a cue block has a timing
    line (optional cue-id line above it). WEBVTT header and NOTE blocks lack a timing line and
    are skipped. Text lines after the timing line are joined with spaces."""
    out: list[Segment] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        m = _SRT_TIME.search(block)
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        lines = block.split("\n")
        ti = next((i for i, ln in enumerate(lines) if "-->" in ln), 0)
        body = " ".join(ln.strip() for ln in lines[ti + 1:] if ln.strip())
        out.append(Segment(start=start, end=end, text=body))
    return out


def _segments_text(segments: list[Segment]) -> str:
    """Normalized concatenation of cue text — the comparison surface for the #6357 check."""
    return "".join(s.text.strip() for s in segments)


def is_part1_duplicate(
    part_segments: list[Segment],
    part1_segments: list[Segment],
    *,
    threshold: float = 0.90,
) -> bool:
    """D4 tier-2: is this part's subtitle the #6357 signature (part 1's text returned again)?

    Compares the concatenated cue text. Exact match or near-identical (>= threshold similarity)
    counts — yt-dlp's #6357 hands back part 1 verbatim, so trivial encoding drift still trips it.
    Genuinely-distinct parts of a course share little text and score near zero, so the margin is
    wide; the threshold only has to clear punctuation/whitespace drift, not real content overlap.
    """
    a = _segments_text(part_segments)
    b = _segments_text(part1_segments)
    if not a or not b:
        return False
    if a == b:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _segments_from_track(formats: list, settings: Settings) -> list[Segment]:
    raw, ext = _download_track(formats, settings)
    return parse_bcc(raw) if ext == "json" else parse_srt(raw)


def _acquire(
    info: dict, canonical: Canonical, settings: Settings, _fetch, *, view=None
) -> tuple[str, str, list[Segment]] | None:
    """Get (source, lang, segments) for the best original-zh track from yt-dlp's track list.

    yt-dlp (with `HARVEST_COOKIES_BROWSER=chrome`) now surfaces bilibili's AI subtitle tracks
    (`ai-zh`/...) directly in `automatic_captions`, so this is the sole acquisition path; the
    player-API subtitle手爬 fallback that used to live here was removed in the 2026-07 refactor
    (it was dead code on the cookie path). `view` is accepted for call-site compatibility but
    no longer drives any fallback fetch.
    """
    pick = _pick_track(info)
    if pick is None:
        return None
    source, lang, formats = pick
    return source, lang, _fetch(formats, settings)


def fetch_subtitle_segments(
    info: dict, canonical: Canonical, settings: Settings, *, view=None
) -> list[Segment] | None:
    """Best original-zh segments for an already-extracted part (no media), via yt-dlp's track
    list. Used to pull *part 1's* subtitle for the D4 tier-2 identity check. Returns None when no
    usable zh track exists — i.e. nothing to compare against."""
    acq = _acquire(info, canonical, settings, _segments_from_track, view=view)
    if acq is None:
        return None
    return acq[2] or None


def probe(
    info: dict,
    canonical: Canonical,
    settings: Settings,
    *,
    part1_segments: list[Segment] | None = None,
    view=None,
    _fetch=_segments_from_track,
) -> SubtitleResult:
    """Probe + D4 two-tier assertion. Tier-1: duration sanity (all parts). Tier-2: for part>1,
    reject if the text is identical to part 1 (the #6357 signature) — caller supplies
    `part1_segments`. single-part / part=1 can't hit #6357, so tier-2 is skipped there. `view` is
    accepted for call-site compatibility (it no longer drives a player-API fallback fetch)."""
    acq = _acquire(info, canonical, settings, _fetch, view=view)
    if acq is None:
        return SubtitleResult(False, None, None, reason="no original-language subtitle available")

    source, lang, segments = acq
    if not segments:
        return SubtitleResult(
            False, None, None, reason=f"subtitle track {lang!r} parsed to zero cues"
        )

    last_end = max(s.end for s in segments)
    duration = info.get("duration")
    if duration:
        ratio = last_end / float(duration)
        if not (0.70 <= ratio <= 1.10):  # D4 tier-1
            return SubtitleResult(
                False,
                None,
                None,
                segments=[],
                reason=(
                    f"subtitle rejected: duration sanity {ratio:.2f} outside 0.70-1.10 "
                    f"(last cue {last_end:.0f}s vs {duration:.0f}s)"
                ),
                last_cue_end=last_end,
            )

    if canonical.part > 1 and part1_segments and is_part1_duplicate(segments, part1_segments):
        return SubtitleResult(  # D4 tier-2
            False,
            None,
            None,
            segments=[],
            reason="subtitle rejected: failed part-match assertion (#6357, identical to part 1)",
            last_cue_end=last_end,
        )

    # Provenance (schema 1.1): tag each CC cue with its source + confidence=1.0 (CC is a
    # trusted track by construction; ASR/OCR report their own confidence where available).
    # `source` here is "human-sub"/"auto-sub" from _pick_track.
    for seg in segments:
        seg.source = source
        if seg.confidence is None:
            seg.confidence = 1.0

    return SubtitleResult(
        True, source, lang, segments=segments, reason=f"{source} ({lang})", last_cue_end=last_end
    )
