"""Provider seam (SPEC §4.1): URL-selected, platform-specific acquisition only.

A Provider is selected by URL and owns resolve/auth/metadata/parts/subtitle for one source.
Everything downstream (merge, probe, transcript decision) reads normalized SourceMetadata and
never branches on platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..schema import Platform, QualityGate, Segment


@dataclass(frozen=True)
class Canonical:
    """{platform, id, part, url}: the atomic, cached identity unit (SPEC §5)."""

    platform: Platform
    id: str
    part: int
    url: str


@dataclass
class SourceMetadata:
    """Normalized per-video metadata every provider produces. Replaces ViewData-vs-info
    branching: merge/probe read only this shape.

    `thumbnail_url` + the stat fields (Task 1) are additive and optional: bilibili fills all of
    them from the already-fetched view API response; YouTube fills only `view_count`/`like_count`/
    `thumbnail_url` and leaves the bilibili-only stats (`coin_count`, `favorite_count`,
    `share_count`, `reply_count`, `danmaku_count`) null."""

    platform: Platform
    id: str
    title: str | None
    uploader: str | None
    uploader_id: str | None
    description: str | None
    duration_s: int | None
    published_at: str | None            # ISO 8601, per-source tz (SPEC §8)
    parts: int
    part_durations_s: list[int | None]
    thumbnail_url: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    coin_count: int | None = None       # bilibili-only
    favorite_count: int | None = None   # bilibili-only
    share_count: int | None = None      # bilibili-only
    reply_count: int | None = None      # bilibili-only
    danmaku_count: int | None = None    # bilibili-only


@dataclass
class SubtitleOutcome:
    """Result of a provider's subtitle acquisition + trust decision. A *rejected* outcome still
    carries its reason (and, for bilibili, the failed quality_gate) so the Whisper fallback can
    record WHY it fell back in the bundle header. `fetch_subtitle` returning None means "no
    subtitle track at all" -> plain Whisper, no gate."""

    accepted: bool                       # True -> use segments; False -> fall back to Whisper
    source: str | None                   # "human-sub" | "auto-sub" when accepted, else None
    source_reason: str                   # why accepted OR why rejected (flows into whisper reason)
    language: str | None
    segments: list[Segment]              # empty when rejected
    quality_gate: QualityGate | None = None


@runtime_checkable
class Provider(Protocol):
    def matches(self, url: str) -> bool: ...
    def resolve(self, url: str) -> Canonical: ...
    def auth_opts(self, settings) -> dict: ...
    def fetch_metadata(self, canonical: Canonical, settings) -> SourceMetadata: ...
    def enumerate_parts(self, canonical: Canonical, settings) -> int: ...
    def fetch_subtitle(
        self, canonical: Canonical, settings, meta: SourceMetadata,
        *, pinned_lang: str | None = None,
    ) -> SubtitleOutcome | None: ...


_REGISTRY: list[Provider] = []


def register(provider: Provider) -> None:
    _REGISTRY.append(provider)


def select_provider(url: str) -> Provider:
    for p in _REGISTRY:
        if p.matches(url):
            return p
    raise ValueError(f"no provider matches URL: {url}")
