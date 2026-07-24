"""YouTubeProvider (SPEC §6): native yt-dlp metadata + tiered caption reuse.

Transcript tier order: human-sub > auto-sub > whisper. target_lang = pinned --lang, else
info["language"], else None. A human `subtitles` track for `target` -> human-sub: exact key, else a
clean BCP-47 region/script variant (never a hash-suffixed community-translation key; see
youtube_autosub.pick_human_key). Else the original-audio auto-caption (key `target-orig`/`target`, or
the sole `*-orig` when target is unknown), fetched as de-rolled SRT and accepted only if it clears the
structural net. Anything else -> Whisper. --force-whisper (handled in cli) skips all of this."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import yt_dlp

from ..config import Settings
from ..subtitles import parse_vtt, ydl_opts
from .base import Canonical, SourceMetadata, SubtitleOutcome, register
from .youtube_autosub import (
    clean_srt_segments,
    pick_auto_key,
    pick_human_key,
    structural_net,
)

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.endswith("youtu.be"):
        cand = parsed.path.lstrip("/").split("/")[0]
        return cand if _ID.match(cand) else None
    v = parse_qs(parsed.query).get("v", [""])[0]
    if _ID.match(v):
        return v
    segs = [s for s in parsed.path.split("/") if s]
    for i, s in enumerate(segs):
        if s in ("shorts", "embed", "v") and i + 1 < len(segs) and _ID.match(segs[i + 1]):
            return segs[i + 1]
    return None


class YouTubeProvider:
    def matches(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return host.endswith("youtube.com") or host.endswith("youtu.be")

    def resolve(self, url: str) -> Canonical:
        vid = _video_id(url)
        if not vid:
            raise ValueError(f"unrecognized YouTube video id in URL: {url}")
        return Canonical("youtube.com", vid, 1, f"https://www.youtube.com/watch?v={vid}")

    def _ydl_opts(self, settings: Settings, **kw) -> dict:
        # referer=None: the default Referer is a bilibili URL and must never reach YouTube.
        # browser_cookies opt-in (issue #1): a logged-in Firefox session breaks yt-dlp's default
        # format selection, so YouTube is cookie-free unless HARVEST_YT_COOKIES is set.
        return ydl_opts(settings, referer=None, browser_cookies=settings.youtube_cookies, **kw)

    def auth_opts(self, settings: Settings) -> dict:
        return self._ydl_opts(settings)

    def _published_at(self, info: dict) -> str | None:
        ts = info.get("timestamp")
        if ts:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ud = info.get("upload_date")
        if ud and len(str(ud)) == 8:
            ud = str(ud)
            return f"{ud[0:4]}-{ud[4:6]}-{ud[6:8]}T00:00:00Z"
        return None

    def _metadata_from_info(self, info: dict) -> SourceMetadata:
        dur = info.get("duration")
        dur_i = int(dur) if dur else None
        return SourceMetadata(
            platform="youtube.com",
            id=info.get("id"),
            title=info.get("title"),
            uploader=info.get("channel") or info.get("uploader"),
            uploader_id=info.get("channel_id"),   # UC..., NOT the mutable @handle
            description=info.get("description"),
            duration_s=dur_i,
            published_at=self._published_at(info),
            parts=1,
            part_durations_s=[dur_i],
            thumbnail_url=info.get("thumbnail"),
            view_count=info.get("view_count"),
            like_count=info.get("like_count"),
        )

    def _extract_info(self, canonical: Canonical, settings: Settings) -> dict:
        with yt_dlp.YoutubeDL(self._ydl_opts(settings)) as ydl:
            info = ydl.extract_info(canonical.url, download=False)
        self._guard_degraded(info, canonical, settings)
        return info

    @staticmethod
    def _guard_degraded(info: dict, canonical: Canonical, settings: Settings) -> None:
        """Fail loud on a degraded/blocked YouTube response (issue #5) instead of letting a corrupt
        bundle get written. Every real video reports a duration; a stripped response (bot check /
        no working JS runtime, seen as a placeholder title like "recommended") omits it."""
        if info.get("duration"):
            return
        runtime = settings.js_runtime[0] if settings.js_runtime else "none"
        raise RuntimeError(
            f"degraded YouTube extraction for {canonical.id!r}: no duration in the response "
            f"(title={info.get('title')!r}). yt-dlp likely got a bot-check/stripped page because "
            f"its YouTube JS challenge could not be solved (js_runtime={runtime}). "
            f"Install deno (https://deno.land) so yt-dlp can use the real web player client."
        )

    def fetch_metadata(self, canonical, settings, *, info=None) -> SourceMetadata:
        info = info if info is not None else self._extract_info(canonical, settings)
        return self._metadata_from_info(info)

    def enumerate_parts(self, canonical, settings) -> int:
        return 1

    def _target_lang(self, info: dict, pinned: str | None) -> str | None:
        if pinned:
            return pinned
        return info.get("language") or None

    def _fetch_url(self, url: str, settings: Settings) -> str:
        with yt_dlp.YoutubeDL(self._ydl_opts(settings)) as ydl:
            return ydl.urlopen(url).read().decode("utf-8", "replace")

    def fetch_subtitle(
        self, canonical, settings, meta, *, pinned_lang=None, info=None, fetch_url=None,
    ) -> SubtitleOutcome | None:
        # Tier order: human-sub (exact key) > auto-sub (original-audio ASR, gated) > Whisper.
        if info is None:
            info = self._extract_info(canonical, settings)
        fetch = fetch_url or self._fetch_url
        target = self._target_lang(info, pinned_lang)

        # Tier 1 — human captions on the original-language key (exact, else a clean region/script
        # variant; never a hash-suffixed community-translation key). automatic_captions excluded.
        if target is not None:
            subtitles = info.get("subtitles") or {}
            key = pick_human_key(subtitles, target)
            if key is not None:
                tracks = subtitles[key]
                vtt = next((t for t in tracks if t.get("ext") == "vtt"), None) or tracks[0]
                segments = parse_vtt(fetch(vtt["url"], settings))
                if segments:
                    match = "exact-key match" if key == target else "base-subtag match"
                    return SubtitleOutcome(
                        accepted=True, source="human-sub",
                        source_reason=f"human-sub ({match}: {key})",
                        language=key, segments=segments, quality_gate=None,
                    )

        # Tier 2 — original-language auto-caption, structural net decides accept vs Whisper.
        auto = info.get("automatic_captions") or {}
        key = pick_auto_key(auto, target)
        if key is None:
            return None                                     # no usable original auto track -> Whisper
        srt = next((t for t in (auto.get(key) or []) if t.get("ext") == "srt"), None)
        if srt is None:
            return None                                     # no de-rolled srt to parse -> Whisper
        segments = clean_srt_segments(fetch(srt["url"], settings))
        lang = key[:-5] if key.endswith("-orig") else key   # "en-orig" -> "en"
        passed, reason = structural_net(segments, float(meta.duration_s or 0), settings.youtube_auto)
        if not passed:
            return SubtitleOutcome(
                accepted=False, source=None,
                source_reason=f"auto-sub rejected ({reason})",
                language=None, segments=[],
            )
        return SubtitleOutcome(
            accepted=True, source="auto-sub",
            source_reason=f"auto-sub (youtube auto-caption: {key})",
            language=lang, segments=segments, quality_gate=None,
        )


register(YouTubeProvider())
