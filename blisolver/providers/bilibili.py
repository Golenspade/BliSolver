"""BilibiliProvider: existing bilibili acquisition behind the Provider interface. Adapter.

Owns the FULL bilibili subtitle trust decision (probe tier-1 duration + tier-2 #6357, then the
quality gate), so the CLI never branches on platform (SPEC §4.1, §6). A move of the proven
cli.decide_transcript bilibili flow into the seam — not a rewrite."""

from __future__ import annotations

from urllib.parse import urlparse

from ..config import Settings
from ..interactions import fetch_interactions
from ..parts import part_url
from ..player_api import (
    DanmakuFetch,
    ViewData,
    ViewError,
    fetch_danmaku,
    fetch_view,
    published_at_iso,
)
from ..quality import describe_failure, evaluate
from ..resolve import resolve as _resolve
from ..subtitles import extract_info, fetch_subtitle_segments, track_language, ydl_opts
from ..subtitles import probe as subtitle_probe
from .base import Canonical, SourceMetadata, SubtitleOutcome, register


class BilibiliProvider:
    def matches(self, url: str) -> bool:
        from ..resolve import extract_url
        clean_url = extract_url(url)
        host = urlparse(clean_url).netloc.lower()
        return (
            host.endswith("bilibili.com")
            or host.endswith("bilibili.tv")
            or host.endswith("b23.tv")
            or host.endswith("player.bilibili.com")
        )

    def resolve(self, url: str) -> Canonical:
        return _resolve(url)

    def auth_opts(self, settings: Settings) -> dict:
        return ydl_opts(settings)

    def _view(self, canonical, settings, *, opener=None) -> ViewData | None:
        try:
            return fetch_view(canonical, settings, opener=opener)
        except ViewError:
            return None

    def fetch_metadata(self, canonical, settings, *, opener=None) -> SourceMetadata:
        view = fetch_view(canonical, settings, opener=opener)
        subs = []
        try:
            info = extract_info(canonical.url, settings)
            sub_dict = info.get("subtitles") or {}
            for k in sub_dict:
                source = "auto-sub" if k.startswith("ai-") else "human-sub"
                subs.append({"code": k, "source": source, "title": k})
        except Exception:
            pass

        return SourceMetadata(
            platform=canonical.platform,
            id=canonical.id,
            title=view.title,
            uploader=view.owner_name,
            uploader_id=str(view.owner_mid) if view.owner_mid is not None else None,
            description=view.desc,
            duration_s=view.duration,
            published_at=published_at_iso(view.pubdate),
            parts=max(len(view.pages), 1),
            part_durations_s=[pg.duration for pg in view.pages],
            thumbnail_url=view.pic,
            original_language="zh" if canonical.platform == "bilibili.com" else None,
            available_subtitles=subs,
            view_count=view.view_count,
            like_count=view.like_count,
            coin_count=view.coin_count,
            favorite_count=view.favorite_count,
            share_count=view.share_count,
            reply_count=view.reply_count,
            danmaku_count=view.danmaku_count,
        )

    def enumerate_parts(self, canonical, settings, *, opener=None) -> int:
        view = self._view(canonical, settings, opener=opener)
        return max(len(view.pages), 1) if view else 1

    def fetch_danmaku(self, canonical, settings, *, opener=None, view=None) -> DanmakuFetch:
        """Bilibili-only, opt-in acquisition capability -- deliberately NOT on the shared
        `Provider` Protocol (YouTube has no equivalent). Thin delegation to
        `player_api.fetch_danmaku`; see there for the not-found/empty-result convention."""
        return fetch_danmaku(canonical, settings, opener=opener, view=view)

    def fetch_interactions(self, canonical, settings, *, opener=None, view=None):
        """Command-danmaku (投票/评分) acquisition. Thin passthrough to
        `interactions.fetch_interactions`; see there for the empty-result-never-raises convention."""
        return fetch_interactions(canonical, settings, opener=opener, view=view)

    def _part1_segments(self, canonical, settings):
        """#6357 tier-2 input: part 1's subtitle, fetched only for part>1. Best-effort — a
        failure here just skips tier-2, never aborts (relocated from cli._part1_segments)."""
        if canonical.part <= 1:
            return None
        try:
            p1_url = part_url(canonical.url, 1)
            p1 = Canonical(canonical.platform, canonical.id, 1, p1_url)
            p1_info = extract_info(p1_url, settings)
            return fetch_subtitle_segments(p1_info, p1, settings)
        except Exception:  # noqa: BLE001 - tier-2 is an optional guard, never fatal
            return None

    def fetch_subtitle(self, canonical, settings, meta, *, pinned_lang=None, opener=None):
        """Full bilibili trust decision → SubtitleOutcome.

        `pinned_lang` is not honoured for track selection here: the candidate order is fixed by
        `subtitles._pick_tracks` (human zh, then ai-zh, then the foreign ASR fallbacks). The CLI
        warns when `--lang` is passed for a bilibili URL so the flag is not silently swallowed.

        A rejected outcome still carries source_reason (+ the failed quality_gate) so the bundle
        records why. An accepted outcome reports the language it actually delivered: this used to be
        hardcoded to "zh", which meant a video whose Chinese track was rejected in favour of the
        English ASR track was recorded as Chinese while carrying English text. `Bundle
        .original_language` stays the video's spoken language, so a consumer can now detect the
        substitution by comparing the two.
        """
        info = extract_info(canonical.url, settings)
        view = self._view(canonical, settings, opener=opener)
        sub = subtitle_probe(
            info, canonical, settings,
            part1_segments=self._part1_segments(canonical, settings),
            view=view,
        )
        if not sub.found:
            return SubtitleOutcome(
                accepted=False, source=None,
                source_reason=f"no usable subtitle ({sub.reason})",
                language=None, segments=[],
            )
        gate = evaluate(sub.segments, float(info.get("duration") or 0), settings.quality,
                       source=sub.source, lang_key=sub.lang)
        if gate.passed:
            # sub.reason already names the delivered track and any cross-language detour.
            return SubtitleOutcome(
                accepted=True, source=sub.source,
                source_reason=f"{sub.reason} (quality-gate: passed)",
                language=track_language(sub.lang), segments=sub.segments, quality_gate=gate,
            )
        return SubtitleOutcome(
            accepted=False, source=None,
            source_reason=f"subtitle rejected ({describe_failure(gate, settings.quality, source=sub.source)})",
            language=None, segments=[], quality_gate=gate,
        )


register(BilibiliProvider())
