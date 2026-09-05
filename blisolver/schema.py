"""The bundle interface contract (SPEC §6). Downstream Atlas depends on this shape — treat as a
stable API. bundle.md is the primary ingestion surface (D1); this JSON is the precise backing
record. Bump SCHEMA_VERSION on any breaking change to the field shape.

1.0 is the fresh multi-source contract: `uploader_id` (string, all sources) replaces the
bilibili-only integer `uploader_mid`; `Platform` includes `youtube.com`; `TranscriptSource`
renames bilibili's `"ai-zh"` provenance label to the source-neutral `"auto-sub"`, with language
now tracked separately on `Transcript.language`.

1.1 is additive provenance + hardsub OCR track (2026-07 multi-source refactor): `Segment`
gains optional `source`/`confidence` (null on legacy bundles, no consumer break); `Bundle`
gains optional `ocr` — an independent-timeline track for burned-in subtitles (source="ocr").
The single `transcript` field is unchanged and remains the authoritative picked transcript.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.1"

Platform = Literal["bilibili.com", "bilibili.tv", "youtube.com"]
TranscriptSource = Literal["human-sub", "auto-sub", "whisper"]
# Per-cue provenance for fused/multi-source segments. Mirrors TranscriptSource with the
# burned-in subtitle source "ocr" added; null on legacy 1.0 bundles (consumer-agnostic).
SegmentSource = Literal["human-sub", "auto-sub", "whisper", "ocr"]


class Segment(BaseModel):
    """A single timestamped transcript segment (whisper segment or subtitle cue).

    `source`/`confidence` (1.1) are optional per-cue provenance: which pipeline stage produced
    this cue ("human-sub"/"auto-sub"/"whisper"/"ocr") and a [0,1] quality score where available
    (CC defaults to 1.0; ASR/OCR may report lower). Both null on legacy 1.0 bundles, so old
    consumers that ignore them keep working unchanged.
    """

    start: float
    end: float
    text: str
    source: SegmentSource | None = None
    confidence: float | None = None


class QualityGate(BaseModel):
    """Quality-gate metrics + verdict for a subtitle source (SPEC §7, D5).

    Populated only when a subtitle track was evaluated; null when the source is whisper with no
    subtitle to gate (e.g. --force-whisper, or no sub returned).
    """

    passed: bool
    punct_density: float
    dup_ratio: float
    nonzh_ratio: float
    cps: float | None = None  # chars per second (length-vs-duration sanity, D5)


class Transcript(BaseModel):
    source: TranscriptSource  # provenance, load-bearing for downstream authority ranking (SPEC §6)
    source_reason: str  # human-readable decision, promoted into the bundle.md header (D2)
    language: str | None = None
    model: str | None = None  # whisper model id; null when source is a subtitle
    robust: bool = False  # condition_on_previous_text disabled? (SPEC §7)
    quality_gate: QualityGate | None = None
    segments: list[Segment] = Field(default_factory=list)


class Frame(BaseModel):
    ts: float
    path: str | None = None  # relative to the bundle dir; null when --no-frame-images (D8)
    phash: str
    caption: str | None = None
    ocr: str | None = None


class Meta(BaseModel):
    cookies_used: bool  # "cookies supplied", NOT "server honored them" (D11)
    referer_used: bool
    vision_model: str | None = None
    tool_version: str


class Stats(BaseModel):
    """Engagement metrics — a POINT-IN-TIME SNAPSHOT as of the enclosing record's `fetched_at`,
    NOT stable identity. All fields volatile (generally grow, but can be reset/hidden) and
    per-platform partial: bilibili fills all; YouTube fills view_count/like_count only.
    Null-tolerate every field; never compare across bundles without accounting for each
    record's `fetched_at`."""
    view_count:     int | None = None
    like_count:     int | None = None
    coin_count:     int | None = None   # bilibili 硬币; YouTube null
    favorite_count: int | None = None   # bilibili 收藏; YouTube null
    share_count:    int | None = None   # bilibili 分享; YouTube null
    reply_count:    int | None = None   # top-level comments (bilibili stat.reply); YT null
    danmaku_count:  int | None = None   # bilibili danmaku total (--danmaku opt-in signal); YT null


class SubtitleTrackInfo(BaseModel):
    """Metadata describing one available subtitle track on the source platform."""

    code: str
    source: SegmentSource
    title: str | None = None


SubtitleDiscoveryStatus = Literal["available", "none", "error", "unknown"]


class ProbeWarning(BaseModel):
    """Safe, actionable diagnostics for an incomplete metadata probe."""

    stage: Literal["subtitles"] = "subtitles"
    code: str
    message: str
    http_status: int | None = None
    retryable: bool = False


class ProbeResult(BaseModel):
    """Cheap pre-flight metadata (no transcript/frames): lets Atlas estimate workload before
    committing to the full pipeline."""

    schema_version: str = SCHEMA_VERSION
    platform: Platform
    id: str
    title: str | None = None
    uploader: str | None = None
    uploader_id: str | None = None
    description: str | None = None
    duration_s: int | None = None
    published_at: str | None = None  # ISO 8601, video's publish time (SPEC: bilibili pubdate)
    thumbnail_url: str | None = None  # intrinsic/descriptive, NOT part of `stats`
    fetched_at: str | None = None  # ISO 8601 UTC, e.g. "2026-06-28T12:00:00Z" -- when probe ran
    stats: Stats | None = None
    parts: int
    part_durations_s: list[int | None] = Field(default_factory=list)
    original_language: str | None = None
    available_subtitles: list[SubtitleTrackInfo] = Field(default_factory=list)
    status: Literal["ok", "partial"] = "ok"
    subtitle_status: SubtitleDiscoveryStatus = "unknown"
    warnings: list[ProbeWarning] = Field(default_factory=list)


class DanmakuLine(BaseModel):
    """A representative danmaku = one cluster head. `text` is VERBATIM (never paraphrased/translated/
    decoded). `count` = near-identical variants collapsed into this representative within the window
    (1 = singleton). Lines within a window are ordered CHRONOLOGICALLY by content time, never by
    count."""

    text: str
    count: int = 1
    high_like: bool = False  # bilibili 高赞 / platform-promoted -- extracted verbatim BEFORE
    # clustering (never absorbed into a flood's count), never LLM-decided (Task 2)
    author: Literal["owner", "staff"] | None = None  # SUSPECTED video author of this line: "owner"
    # (UP主) or "staff" (合作 co-author), crc32-matched off the poster hash (midHash) BEFORE
    # clustering; None = organic crowd. UNVERIFIED: midHash is a lossy 32-bit crc32 and bilibili
    # exposes no true-sender API, so a match may be a hash collision (empirically confirmed on real
    # videos). Treat as a weak hint, NOT authoritative author content (see PROTOCOL.md carve-out).


class DanmakuWindow(BaseModel):
    """Danmaku pinned to content-time window [start, end) seconds (aligned to bundle chunks).
    `total` = raw danmaku in the window BEFORE clustering — the density signal that survives even if
    `lines` is capped in bundle.md."""

    start: float
    end: float
    total: int
    lines: list[DanmakuLine] = Field(default_factory=list)


class Danmaku(BaseModel):
    """Crowd danmaku track — a faithful MIRROR of the audience stream, NOT interpreted content.
    LOWER AUTHORITY than `transcript`: crowd expression (jokes, memes, sarcasm, frequently 'wrong'
    claims); never treat as authoritative. bilibili-only; present only when `--danmaku` was requested
    on a supporting platform (else Bundle.danmaku is null). bundle.json is the COMPLETE record;
    bundle.md may cap per window with a '+N more' marker (read this JSON for the full set)."""

    source_total: int | None = None  # bilibili stat.danmaku: cumulative lifetime, not live pool
    fetched_total: int  # census pull = currently-live danmaku; ≤ source_total by nature
    model: str | None = None  # the LLM that produced this representation (provenance)
    windows: list[DanmakuWindow] = Field(default_factory=list)


class VoteOption(BaseModel):
    """One selectable option of a Vote (投票). `text` is the VERBATIM option label from the
    uploader; `count` is the crowd tally for it. `write_in` marks the free-text "其他/other"
    option (bilibili `has_self_def`) — its `text` is a prompt, not a real answer."""

    text: str
    count: int = 0
    write_in: bool = False


class Vote(BaseModel):
    """An on-screen vote (投票 / bilibili `#VOTE#`): an uploader-authored `question` plus discrete
    `options`, each with a running crowd tally. `total_count` is bilibili's own reported total (kept
    explicit, not summed). `ts` is the content-time (seconds) the widget is pinned to (null if the
    widget carries no timeline anchor). The question and option texts are VERBATIM uploader content;
    the question is VERIFIED framing (structural widget data, not a crc32 guess), but it is a
    question, never a claim the video asserts. Authority: BELOW transcript."""

    question: str
    options: list[VoteOption] = Field(default_factory=list)
    total_count: int = 0
    ts: float | None = None


class Grade(BaseModel):
    """A star grading (评分 / bilibili `#GRADE#`): a 1–5 star bar the server pre-aggregates. Its
    datum is `avg_score` on a **0–10 scale** (the 1–5 stars ×2) plus the rater `count`. It has NO
    framing question and NO per-option breakdown — just the mean and n. Not timeline-pinned.
    Authority: a crowd reception aggregate, strictly BELOW transcript (never a fact about content).
    NB: viewers' raw star clicks also post literal digit danmaku ("5"/"1") into the census, so with
    --danmaku on they ALSO appear in the danmaku mirror — the same act, surfaced twice by design."""

    avg_score: float  # 0–10 (1–5 stars ×2); a server-computed mean, NOT raw votes
    count: int = 0


class Interactions(BaseModel):
    """Command danmaku (互动弹幕) — the uploader's on-screen interactive widgets — a SEPARATE class
    from `danmaku`, on a separate acquisition path (`x/v2/dm/web/view`, no LLM). bilibili-only;
    present only when `--interactions` ran on a supporting platform (else `Bundle.interactions` is
    null). Populated-but-empty (`votes: []`, `grades: []`) means "requested, found nothing" —
    distinct from null ("not requested")."""

    votes: list[Vote] = Field(default_factory=list)
    grades: list[Grade] = Field(default_factory=list)


class Bundle(BaseModel):
    schema_version: str = SCHEMA_VERSION
    platform: Platform
    id: str
    part: int
    url: str
    title: str | None = None
    uploader: str | None = None
    uploader_id: str | None = None
    description: str | None = None
    duration_s: int | None = None
    published_at: str | None = None  # ISO 8601, video's publish time (SPEC: bilibili pubdate)
    thumbnail_url: str | None = None  # intrinsic/descriptive, NOT part of `stats`
    original_language: str | None = None
    available_subtitles: list[SubtitleTrackInfo] = Field(default_factory=list)
    fetched_at: str  # ISO 8601 UTC, e.g. "2026-06-28T12:00:00Z"
    stats: Stats | None = None
    transcript: Transcript
    # Hardsub OCR track (1.1): burned-in subtitles on an INDEPENDENT timeline from the picked
    # `transcript`. Present only when OCR ran and detected a hardsub track (else null). Per-cue
    # `Segment.source` is "ocr". This is distinct from `Frame.ocr` (slide/UI text, sparse).
    ocr: list[Segment] | None = None
    frames: list[Frame] = Field(default_factory=list)
    danmaku: Danmaku | None = None
    interactions: Interactions | None = None
    meta: Meta
