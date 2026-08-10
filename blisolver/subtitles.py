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

# Original-language zh keys we accept, in preference order. Split by SOURCE TYPE because the
# quality gate is source-aware (Phase F: punct_density is skipped for auto-sub — ASR captions
# are punctuation-less by construction, so the metric only flags source-TYPE, not quality).
# bilibili delivers ai-zh inside the `subtitles` field (not `automatic_captions` like YouTube),
# so the field is NOT a reliable source signal — the key name is. `ai-*` is ALWAYS auto-sub.
_HUMAN_ZH_KEYS = ("zh-Hans", "zh-CN", "zh")   # human-authored original-language zh
_AUTO_ZH_KEYS = ("ai-zh",)                     # ASR-transcribed zh (bilibili ai-zh)
# Kept for back-compat with anything that imported the combined tuple; new code uses the split.
_ZH_KEYS = _HUMAN_ZH_KEYS + _AUTO_ZH_KEYS

# Redaction markers seen in bilibili's server-side-censored AI Chinese tracks: `**` is the literal
# substitution, `XX` and `和X` are the placeholder forms observed on real videos. A match rejects
# the track and moves down the candidate list (see `_acquire`).
#
# This heuristic is textual and can fire on innocent content — `**` appears in Markdown-ish
# speech, and `和X` is ordinary in a maths lecture ("X 和 Y"). A false positive is not silent: the
# rejection is recorded in the returned `Acquisition.rejected` and surfaces in the bundle's
# `source_reason`, so a reader can see that a Chinese track was dropped and on what evidence.
_CENSORSHIP_MARKERS = re.compile(r"\*\*|XX|和[Xx]")


def track_language(track_key: str | None) -> str | None:
    """Map a yt-dlp subtitle track key to the language it is actually in.

    bilibili's ASR tracks are keyed `ai-<lang>`; the `ai-` prefix records *how* the track was
    produced, not what language it holds. Reporting the raw key as a language would put `ai-en`
    into a language field, and reporting a constant would be worse still — the bilibili provider
    used to hardcode `"zh"`, so a video whose Chinese track was rejected in favour of the English
    one was recorded as Chinese while carrying English text.
    """
    if not track_key:
        return None
    return track_key[3:] if track_key.startswith("ai-") else track_key


# A caption track key is either `ai-<language>` or a bare language code. Anything else is not a
# caption: bilibili's extractor also exposes the scrolling-comment overlay under the key `danmaku`,
# which has no transcript meaning and is acquired through `--danmaku` instead.
_LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,8})*$")


def classify_track(track_key: str) -> str | None:
    """Return the provenance of a subtitle track key, or None when it is not a caption at all.

    Used to describe *availability*. The previous rule was "anything not prefixed `ai-` is
    human-sub", which labelled bilibili's `danmaku` overlay as a human-authored caption — the
    highest authority tier — so a consumer reading `available_subtitles` was told a human Chinese
    caption existed when none did.
    """
    if track_key.startswith("ai-"):
        return "auto-sub" if _LANGUAGE_CODE.match(track_key[3:]) else None
    return "human-sub" if _LANGUAGE_CODE.match(track_key) else None


@dataclass
class Acquisition:
    """A chosen subtitle track plus the tracks that were passed over to reach it.

    `rejected` is what makes a cross-language fallback auditable: each entry is
    `(track_key, why)` for a candidate that was fetched and then discarded. An empty tuple means
    the first candidate was accepted.
    """

    source: str
    lang: str
    segments: list[Segment]
    rejected: tuple[tuple[str, str], ...] = ()

    @property
    def is_language_proxy(self) -> bool:
        """True when a Chinese track was rejected and a different language stands in for it."""
        return bool(self.rejected) and track_language(self.lang) != "zh"

    def describe_fallback(self) -> str:
        """One clause naming the detour, for the bundle's transcript `source_reason`."""
        if not self.rejected:
            return ""
        skipped = ", ".join(f"{key} ({why})" for key, why in self.rejected)
        if self.is_language_proxy:
            return (
                f"language proxy: original-language track unusable [{skipped}]; "
                f"delivered {self.lang} instead"
            )
        return f"fell back past [{skipped}] to {self.lang}"


@dataclass
class SubtitleResult:
    found: bool
    source: str | None  # "human-sub" | "auto-sub" | None
    lang: str | None
    segments: list[Segment] = field(default_factory=list)
    reason: str = ""  # human-readable, flows into the D2 bundle.md header
    last_cue_end: float | None = None
    # Tracks fetched and discarded before `lang` was accepted, as (track_key, why). Empty when the
    # first candidate was taken. Carries the cross-language fallback fact to the provider so it can
    # be recorded rather than lost.
    rejected: tuple[tuple[str, str], ...] = ()


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
    so YouTube callers opt out unless the user opts in via BLISOLVER_YT_COOKIES. bilibili keeps
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


def _pick_tracks(info: dict) -> list[tuple[str, str, list]]:
    """Return a priority-ordered list of (source_label, lang_key, formats) tracks.

    Human-CC outranks ASR auto-sub. bilibili delivers ai-zh inside the `subtitles` field (not
    `automatic_captions`). The list includes foreign AI subs (ai-en, ai-ja) as fallbacks
    for censorship bypass."""
    human = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    
    candidates = []
    
    # 1. Human-CC outranks everything
    for key in _HUMAN_ZH_KEYS:
        if key in human:
            candidates.append(("human-sub", key, human[key]))
            
    # 2. Chinese AI-generated subtitles (prioritized for normal non-sensitive videos)
    for key in _AUTO_ZH_KEYS:
        if key in human:                  # bilibili: ai-zh lives in `subtitles`
            candidates.append(("auto-sub", key, human[key]))
        if key in auto:                    # YouTube: ai-zh lives in `automatic_captions`
            candidates.append(("auto-sub", key, auto[key]))
            
    # 3. Foreign language AI fallbacks (en > ja > others)
    # Used as a safety net if the Chinese tracks above trigger the censorship regex.
    fallback_langs = [
        "ai-en", "en",
        "ai-ja", "ja",
        "ai-es", "es",
        "ai-ar", "ar",
        "ai-pt", "pt",
        "ai-ko", "ko",
        "ai-th", "th",
        "ai-id", "id",
        "ai-vi", "vi"
    ]
    for fallback_lang in fallback_langs:
        if fallback_lang in human:
            candidates.append(("auto-sub", fallback_lang, human[fallback_lang]))
        elif fallback_lang in auto:
            candidates.append(("auto-sub", fallback_lang, auto[fallback_lang]))
            
    return candidates



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
) -> Acquisition | None:
    """Walk the candidate tracks and return the first usable one, recording what was skipped.

    Rejections used to be printed and then discarded. Two consequences: the bundle carried no
    record that a cross-language substitution had happened, and the message went to stdout — the
    channel `probe` and `ingest --json` reserve for machine-readable output. Both are fixed by
    returning the rejections to the caller instead of narrating them.
    """
    candidates = _pick_tracks(info)
    if not candidates:
        return None

    rejected: list[tuple[str, str]] = []
    for source, lang, formats in candidates:
        segments = _fetch(formats, settings)

        if lang in _AUTO_ZH_KEYS or lang in _HUMAN_ZH_KEYS:
            marker = _CENSORSHIP_MARKERS.search("".join(s.text for s in segments))
            if marker:
                rejected.append((lang, f"censorship marker {marker.group(0)!r}"))
                continue

        return Acquisition(
            source=source, lang=lang, segments=segments, rejected=tuple(rejected)
        )

    return None


def fetch_subtitle_segments(
    info: dict, canonical: Canonical, settings: Settings, *, view=None
) -> list[Segment] | None:
    """Best original-zh segments for an already-extracted part (no media), via yt-dlp's track
    list. Used to pull *part 1's* subtitle for the D4 tier-2 identity check. Returns None when no
    usable zh track exists — i.e. nothing to compare against."""
    acq = _acquire(info, canonical, settings, _segments_from_track, view=view)
    if acq is None:
        return None
    return acq.segments or None


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
        # Precise about which of the two situations occurred. The old wording said "no
        # original-language subtitle available" for both, which became misleading once the
        # candidate list grew past Chinese: a run that fetched several tracks and rejected each of
        # them reported the same thing as a video with no tracks at all.
        tracks = _pick_tracks(info)
        if not tracks:
            return SubtitleResult(False, None, None, reason="no subtitle track offered")
        offered = ", ".join(lang for _, lang, _ in tracks)
        return SubtitleResult(
            False, None, None,
            reason=f"every offered subtitle track was rejected (offered: {offered})",
        )

    source, lang, segments = acq.source, acq.lang, acq.segments
    fallback_note = acq.describe_fallback()
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
    # `source` here is "human-sub"/"auto-sub" from _pick_tracks.
    for seg in segments:
        seg.source = source
        if seg.confidence is None:
            seg.confidence = 1.0

    reason = f"{source} ({lang})"
    if fallback_note:
        reason = f"{reason}; {fallback_note}"
    return SubtitleResult(
        True, source, lang, segments=segments, reason=reason, last_cue_end=last_end,
        rejected=acq.rejected,
    )
