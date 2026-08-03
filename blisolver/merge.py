"""Timeline alignment -> bundle.json + bundle.md (SPEC §5 step 5, §6, D1/D2/D3/D8).

bundle.md is the product (D1): a provenance header (D2) + slide/wall-clock chunks (D3), each
"what was on screen + the speech while it was up". bundle.json is the precise backing record.
"""

from __future__ import annotations

import re
import shutil
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .config import Settings
from .providers.base import Canonical, SourceMetadata
from .schema import Bundle, Danmaku, Frame, Interactions, Meta, Segment, Stats, SubtitleTrackInfo, Transcript

# The ordinary per-window danmaku cap for bundle.md lives in Settings.danmaku_md_cap
# (env BLISOLVER_DANMAKU_MD_CAP) -- a tunable gestalt-sample dial. bundle.json is always complete.


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


# Lines whose first non-whitespace content forges blisolver's section grammar: a #-run heading,
# a ---/***/___ thematic break, or a ```/~~~ code fence. bundle.md delimits sections with these
# markers rather than fenced blocks, so untrusted body text that leads with one could manufacture
# a fake ## Transcript / ## Danmaku section or close the frontmatter. We backslash-escape the
# marker so it renders as literal text and no longer matches a structural ^## / ^--- scan.
_STRUCTURAL_LINE = re.compile(r"^(\s*)(#{1,6}|-{3,}|\*{3,}|_{3,}|`{3,}|~{3,})")


def _neutralize(text: str) -> str:
    """Backslash-escape line-leading structural markdown markers, line by line (so a marker after
    an embedded newline is caught too). Ordinary prose lines pass through unchanged."""
    out = []
    for line in text.split("\n"):
        m = _STRUCTURAL_LINE.match(line)
        if m:
            i = len(m.group(1))
            line = line[:i] + "\\" + line[i:]
        out.append(line)
    return "\n".join(out)


def _line_pills(line) -> str:
    """Pill prefix for a danmaku line: 👍 (high_like) then UP主?/合作? (author), space-joined with a
    trailing space. Empty string for an ordinary crowd line. The author pills carry a trailing `?`
    deliberately: `author` is a crc32 poster-hash match, and bilibili does not expose the true
    sender, so the match is UNVERIFIED (may be a hash collision) — the `?` keeps a scanning reader
    from reading it as a confirmed UP主/合作 post. 👍 has no `?`: it is a reliable platform flag."""
    pills: list[str] = []
    if line.high_like:
        pills.append("\U0001F44D")
    if line.author == "owner":
        pills.append("UP主?")
    elif line.author == "staff":
        pills.append("合作?")
    return (" ".join(pills) + " ") if pills else ""


@dataclass
class Chunk:
    start: float
    frames: list[Frame]
    segments: list[Segment]


def chunk_boundaries(
    segments: list[Segment],
    frames: list[Frame],
    *,
    window_s: float,
    duration_s: float | None,
) -> list[float]:
    """D3 boundary computation, extracted so other stages (danmaku windowing, Task 4) can align
    to the SAME boundaries `chunk()` buckets against: deduped frame timestamps when frames exist,
    else a fixed wall-clock window covering `duration_s`."""
    if frames:
        return sorted({0.0, *(f.ts for f in frames)})
    end = duration_s or (max((s.end for s in segments), default=0.0))
    return [i * window_s for i in range(int(end // window_s) + 1)] or [0.0]


def chunk(
    segments: list[Segment],
    frames: list[Frame],
    *,
    window_s: float,
    duration_s: float | None,
) -> list[Chunk]:
    """D3: boundaries = deduped frame timestamps when present, else a fixed wall-clock window.
    Segments are assigned whole by their start timestamp; never split across a boundary."""
    boundaries = chunk_boundaries(segments, frames, window_s=window_s, duration_s=duration_s)

    chunks = [Chunk(start=b, frames=[], segments=[]) for b in boundaries]
    for f in frames:
        chunks[bisect_right(boundaries, f.ts) - 1].frames.append(f)
    for seg in segments:
        chunks[bisect_right(boundaries, seg.start) - 1].segments.append(seg)
    return [c for c in chunks if c.segments or c.frames]


def build_bundle(
    canonical: Canonical,
    meta: SourceMetadata,
    transcript: Transcript,
    frames: list[Frame],
    settings: Settings,
    *,
    vision_model: str | None = None,
    danmaku: Danmaku | None = None,
    interactions: Interactions | None = None,
    ocr: list[Segment] | None = None,
) -> Bundle:
    return Bundle(
        platform=canonical.platform, id=canonical.id, part=canonical.part, url=canonical.url,
        title=meta.title, uploader=meta.uploader, uploader_id=meta.uploader_id,
        description=meta.description, duration_s=meta.duration_s, published_at=meta.published_at,
        thumbnail_url=meta.thumbnail_url,
        original_language=meta.original_language,
        available_subtitles=[SubtitleTrackInfo(**s) if isinstance(s, dict) else s for s in meta.available_subtitles],
        stats=Stats(
            view_count=meta.view_count, like_count=meta.like_count, coin_count=meta.coin_count,
            favorite_count=meta.favorite_count, share_count=meta.share_count,
            reply_count=meta.reply_count, danmaku_count=meta.danmaku_count,
        ),
        fetched_at=iso_now(), transcript=transcript, frames=frames, danmaku=danmaku,
        interactions=interactions, ocr=ocr,
        meta=Meta(
            cookies_used=bool(settings.sessdata or settings.cookies_browser),
            referer_used=(canonical.platform == "bilibili.com"),
            vision_model=vision_model, tool_version=settings.tool_version,
        ),
    )


def render_markdown(bundle: Bundle, settings: Settings) -> str:
    t = bundle.transcript
    dur = _mmss(bundle.duration_s) if bundle.duration_s else "?"
    front = {
        "platform": bundle.platform,
        "id": bundle.id,
        "part": bundle.part,
        "url": bundle.url,
        "title": bundle.title or "",
        "uploader": bundle.uploader or "",
        "uploader_id": bundle.uploader_id or "",
        "thumbnail_url": bundle.thumbnail_url or "",
        "duration": dur,
        "published_at": bundle.published_at or "",
        "fetched_at": bundle.fetched_at,
        "original_language": bundle.original_language or "",
        "available_subtitles": [f"{s.code} ({s.source})" for s in bundle.available_subtitles],
        "transcript_source": f"{t.source} ({t.source_reason})",
        "vision_model": bundle.meta.vision_model or "none",
        "tool_version": bundle.meta.tool_version,
    }
    fm = yaml.safe_dump(
        front, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).strip()
    lines = [
        "---",
        fm,
        "---",
        "",
        f"# {_neutralize(bundle.title or bundle.id)}",
        "",
    ]

    if bundle.description:
        lines.append("## Description")
        lines.append("")
        lines.append(_neutralize(bundle.description))
        lines.append("")

    lines.append("## Transcript")
    lines.append("")

    has_downstream = bool(bundle.ocr) or bool(bundle.danmaku and bundle.danmaku.windows) or (
        bundle.interactions and (bundle.interactions.votes or bundle.interactions.grades)
    )
    if not t.segments and not bundle.frames:
        lines.append("_(no transcript yet — Whisper pending)_")
        lines.append("")
        if not has_downstream:
            return "\n".join(lines).rstrip() + "\n"
    else:
        for ch in chunk(
            t.segments, bundle.frames, window_s=settings.chunk_window_s, duration_s=bundle.duration_s
        ):
            lines.append(f"### [{_mmss(ch.start)}]")
            for fr in ch.frames:
                if fr.ocr:
                    lines.append(f"**slide (OCR):** {_neutralize(fr.ocr)}")
                if fr.caption:
                    lines.append(f"**slide (figure):** {_neutralize(fr.caption)}")
            if ch.frames:
                lines.append("")
            text = _neutralize("".join(s.text for s in ch.segments).strip())
            if text:
                lines.append(text)
            lines.append("")

    if bundle.ocr:
        lines.append("## Hard-subtitles (OCR)")
        lines.append(
            "_burned-in subtitle track on an independent timeline (lower authority than the "
            "picked transcript; per-cue confidence shown). Distinct from slide/Frame.ocr._"
        )
        lines.append("")
        for seg in bundle.ocr:
            conf = f" (conf {seg.confidence:.2f})" if seg.confidence is not None else ""
            lines.append(f"- [{_mmss(seg.start)}] {_neutralize(seg.text)}{conf}")
        lines.append("")

    dm = bundle.danmaku
    if dm and dm.windows:
        lines.append("## Danmaku")
        note = f"_crowd track (lower authority than transcript) — fetched {dm.fetched_total}"
        if dm.source_total is not None:
            note += f" of {dm.source_total} ever posted"
        if dm.model:
            note += f" · {dm.model}"
        note += (
            ". \U0001F44D = bilibili 高赞 (platform-promoted; reliable). "
            "UP主?/合作? = possibly from the video author, unverified — not authoritative._"
        )
        lines.append(note)
        lines.append("")
        for w in dm.windows:
            if not w.lines:
                continue
            lines.append(f"### [{_mmss(w.start)}] ({w.total} danmaku)")
            # ONE chronological pass (w.lines is already content-time ordered). Elevated lines
            # (high_like or author) always render in place; only ordinary lines are capped.
            ordinary_shown = 0
            for ln in w.lines:
                if not (ln.high_like or ln.author is not None):
                    if ordinary_shown >= settings.danmaku_md_cap:
                        continue
                    ordinary_shown += 1
                suffix = "" if ln.count == 1 else f" ×{ln.count}"
                lines.append(f"- {_line_pills(ln)}「{_neutralize(ln.text)}」{suffix}")
            ordinary_total = sum(
                1 for ln in w.lines if not (ln.high_like or ln.author is not None)
            )
            ordinary_overflow = ordinary_total - settings.danmaku_md_cap
            if ordinary_overflow > 0:
                lines.append(f"- ﹢{ordinary_overflow} more — see bundle.json")
            lines.append("")

    it = bundle.interactions
    if it and (it.votes or it.grades):
        lines.append("## Interactions")
        lines.append(
            "_uploader-initiated widgets (below transcript authority). "
            "grades = crowd 0–10 average; votes = uploader question + crowd tallies._"
        )
        lines.append("")
        # Grades first (video-level reception summary; no timeline anchor).
        for g in it.grades:
            lines.append("### 评分 (grade)")
            lines.append(f"- avg {g.avg_score:g} / 10 over {g.count} raters")
            lines.append("")
        # Votes next, ordered by timeline anchor (ts); unanchored votes (ts is None) sort last.
        for v in sorted(it.votes, key=lambda v: (v.ts is None, v.ts or 0.0)):
            head = "### "
            if v.ts is not None:
                head += f"[{_mmss(v.ts)}] "
            head += f"投票 (vote): {_neutralize(v.question)}"
            lines.append(head)
            for opt in v.options:
                marker = " (write-in)" if opt.write_in else ""
                lines.append(f"- {_neutralize(opt.text)}{marker} — {opt.count}")
            lines.append(f"_{v.total_count} votes total_")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')


def _sanitize_filename(name: str) -> str:
    """Clean a string for safe use in cross-platform directory names."""
    if not name:
        return ""
    cleaned = _INVALID_FILENAME_CHARS.sub(" ", name)
    cleaned = " ".join(cleaned.split())
    return cleaned.strip(" .")


def write_bundle(
    bundle: Bundle,
    settings: Settings,
    *,
    frame_sources: dict[str, Path] | None = None,
    frame_images: bool = True,
) -> Path:
    """Write the self-contained delivery dir out/<sanitized_title> [<id>-p<part>]/ (D8).

    frame_images=False (--no-frame-images, D8): omit PNGs from out/ and null each frame's `path`
    in bundle.json — the record keeps phash/ts/caption/ocr and the markdown keeps the caption
    text, so only the QA images are dropped.
    """
    clean_title = _sanitize_filename(bundle.title or "")
    dir_name = f"{clean_title} [{bundle.id}-p{bundle.part}]" if clean_title else f"{bundle.id}-p{bundle.part}"
    out = settings.out_dir / dir_name
    out.mkdir(parents=True, exist_ok=True)
    frames_dir = out / "frames"
    # Rebuild frames/ from scratch so the delivered dir matches bundle.json exactly (D8) and
    # stale PNGs from prior runs (e.g. a different dedup threshold, or a prior images-on run)
    # never linger.
    if frames_dir.exists():
        shutil.rmtree(frames_dir)

    if frame_images:
        frames_dir.mkdir(parents=True, exist_ok=True)
        if frame_sources:
            for fr in bundle.frames:
                if fr.path and fr.path in frame_sources:
                    shutil.copy2(frame_sources[fr.path], out / fr.path)
    else:
        for fr in bundle.frames:
            fr.path = None

    (out / "bundle.json").write_text(
        bundle.model_dump_json(indent=2), encoding="utf-8"
    )
    (out / "bundle.md").write_text(render_markdown(bundle, settings), encoding="utf-8")
    return out
