"""Multi-source fusion (Phase D, SPEC §5 multi-source).

Two distinct jobs, both driven by real end-to-end evidence (not the §5.3 sketch):

1. ASR hallucination detection + cross-source verification (the high-frequency real pain
   point). On BV1LD7U65Ew2, whisper produced 46/69 segments of "冰淇淋-6 颗" (67% repetition)
   while the burned-in OCR track carries the actual content in the same time windows. The §5.2
   contract keeps the picked `transcript` as the authoritative single timeline and `ocr` as an
   independent track, so fuse does NOT rewrite the transcript body — it emits a diagnosis that
   tells downstream readers "this ASR transcript is hallucinated; the OCR track is the truth in
   the affected windows" via `source_reason` + a diagnostics list.

2. Same-source selection (§5.3 original scope, rare in practice — the degradation chain picks
   ONE of CC/AI/ASR, so multiple speech sources only co-occur when human-sub AND auto-sub both
   pass the gate). When `candidates` is supplied, overlap-aware text-similarity selection runs;
   without candidates the entry point is a no-op pass-through so existing single-source ingests
   are unaffected.

Provenance tagging (schema 1.1 `Segment.source/confidence`) happens at the DATA SOURCES
(transcribe.py -> "whisper", subtitles.probe -> "human-sub"/"auto-sub" + conf 1.0), NOT here —
fuse reads those tags; it does not guess them.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .schema import Segment, Transcript

# --- calibration (start guesses; tune on more videos, Phase F) ---------------------

# A track whose single most-repeated normalized text covers > this fraction of cues is a
# hallucination signal. 0.40 is generous: BV1LD7U65Ew2's 46/69=0.67 trips it; a healthy lecture
# repeats connectives ("所以"/"然后") far below this.
HALLUC_REPEAT_RATIO = 0.40
# Adjacent-run threshold: the same text appearing >= this many times in a row is a strong signal
# (whisper repetition loops). 5 catches real loops; healthy narration rarely repeats 5x adjacently.
HALLUC_ADJACENT_RUN = 5
# Text-similarity threshold for cross-source verification: when the ASR cue and the time-overlapping
# OCR cue share < this much text, the ASR content is NOT corroborated by the screen text.
CROSS_DISAGREE_SIM = 0.30


@dataclass
class FusionResult:
    """Outcome of a fusion pass. The transcript body is UNCHANGED (§5.2 contract: the picked
    single timeline stays the authoritative record); fusion only annotates provenance-aware
    diagnostics onto `source_reason` + `diagnostics` so downstream readers can weigh authority."""

    transcript: Transcript
    ocr: list[Segment] | None = None
    diagnostics: list[str] = field(default_factory=list)
    # Structured signals for callers/tests:
    hallucination: bool = False
    repeat_ratio: float = 0.0
    top_repeated_text: str | None = None
    cross_verified_windows: int = 0  # ASR windows where OCR disagreed (confirms hallucination)


def _normalize(text: str) -> str:
    """Lowercase + strip whitespace/punctuation for the comparison surface. Preserves CJK.
    Mirrors the worker's dedup normalizer so fuse and OCR agree on 'same cue'."""
    import re
    text = re.sub(r"[\s\u3000]+", "", text).strip().strip("，。！？、!?.,;:：""''\"'…—-()（）*")
    return text.lower()


def _similarity(a: str, b: str) -> float:
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _overlapping_ocr(seg: Segment, ocr: list[Segment]) -> list[Segment]:
    """OCR cues whose [start,end) intersects [seg.start, seg.end)."""
    return [o for o in ocr if o.end > seg.start and o.start < seg.end]


def detect_hallucination(transcript: Transcript) -> tuple[bool, float, str | None]:
    """Flag an ASR transcript whose cues repeat one text dominantly (whisper repetition loop).

    Returns (is_hallucination, repeat_ratio, top_text). A track with <3 cues can't statistically
    trip this (return False). Two signals, either suffices:
      * dominant text covers > HALLUC_REPEAT_RATIO of cues (global repetition), OR
      * one text appears >= HALLUC_ADJACENT_RUN times in a row (loop signature).
    """
    segs = transcript.segments
    if len(segs) < 3:
        return False, 0.0, None
    normed = [_normalize(s.text) for s in segs]
    counts = Counter(normed)
    top_text, top_n = counts.most_common(1)[0]
    ratio = top_n / len(segs)
    if not top_text:
        return False, 0.0, None
    # Adjacent-run check: longest run of identical consecutive cues.
    longest_run = run = 1
    for i in range(1, len(normed)):
        if normed[i] == normed[i - 1] and normed[i]:
            run += 1
            longest_run = max(longest_run, run)
        else:
            run = 1
    is_hall = ratio >= HALLUC_REPEAT_RATIO or longest_run >= HALLUC_ADJACENT_RUN
    return is_hall, round(ratio, 3), top_text or None


def cross_verify_with_ocr(
    transcript: Transcript, ocr: list[Segment] | None
) -> int:
    """Count ASR windows where the time-overlapping OCR carries materially different text.

    A hallucinated ASR repeating one fragment scores low similarity against the real, varied OCR
    content in the same window — so each such window is a corroborating signal. Returns the count
    of disagreeing windows (0 when there's no OCR or no disagreement)."""
    if not ocr:
        return 0
    disagree = 0
    for seg in transcript.segments:
        overlap = _overlapping_ocr(seg, ocr)
        if not overlap:
            continue
        # Best (max) similarity across overlapping OCR cues: if even the closest OCR cue shares
        # almost nothing with the ASR text, the ASR content is uncorroborated here.
        best = max((_similarity(seg.text, o.text) for o in overlap), default=0.0)
        if best < CROSS_DISAGREE_SIM:
            disagree += 1
    return disagree


def select_same_source(
    primary: Transcript, candidates: list[Transcript]
) -> Transcript:
    """§5.3 same-source selection: when multiple speech sources pass the gate, pick the better
    cue per time window by text-similarity-then-confidence.

    Algorithm (overlap-aware merge):
      1. Collect all cues from primary + candidates, each tagged with its source Transcript.
      2. Sort by start.
      3. Walk cues; when the next cue overlaps the current and text-similarity >= 0.80, keep the
         higher-confidence one (CC conf=1.0 beats ASR conf=None->0.0); else keep both.
      4. The result's segments carry their original per-cue `source`/`confidence` provenance.

    No real multi-source test video exists yet (the degradation chain picks one source), so this
    is exercised by unit tests with synthetic multi-source transcripts. With no candidates it
    returns `primary` unchanged.
    """
    if not candidates:
        return primary
    # Confidence fallback: None -> 0.0 for ranking (ASR without conf ranks below CC's 1.0).
    def _conf(s: Segment) -> float:
        return s.confidence if s.confidence is not None else 0.0

    all_segs: list[tuple[Segment, Transcript]] = (
        [(s, primary) for s in primary.segments]
        + [(s, c) for c in candidates for s in c.segments]
    )
    all_segs.sort(key=lambda ts: ts[0].start)

    merged: list[Segment] = []
    for seg, _t in all_segs:
        if merged:
            prev = merged[-1]
            overlaps = seg.start < prev.end
            similar = _similarity(prev.text, seg.text) >= 0.80
            if overlaps and similar:
                # Keep the higher-confidence cue; drop the other. Preserve the earlier start,
                # later end, so the merged cue spans the union window.
                keep, drop = (prev, seg) if _conf(prev) >= _conf(seg) else (seg, prev)
                keep.start = min(prev.start, seg.start)
                keep.end = max(prev.end, seg.end)
                merged[-1] = keep
                continue
        merged.append(seg)
    # New Transcript: provenance reason carries forward; segments now carry per-cue source tags.
    return Transcript(
        source=primary.source,
        source_reason=primary.source_reason,
        language=primary.language,
        model=primary.model,
        robust=primary.robust,
        quality_gate=primary.quality_gate,
        segments=merged,
    )


def fuse(
    transcript: Transcript,
    ocr: list[Segment] | None = None,
    *,
    candidates: list[Transcript] | None = None,
) -> FusionResult:
    """Annotate a multi-source fusion pass over `transcript` (+ optional OCR/candidates).

    Contract (§5.2): the returned `transcript` body is the picked authoritative single timeline.
    Fusion annotates `source_reason` with provenance-aware diagnostics (hallucination detection +
    cross-source verification) and surfaces structured signals on `FusionResult`. Same-source
    selection (`candidates`) rewrites the segment list only when multiple speech sources are
    actually supplied — the common single-source ingest is a pass-through.
    """
    diagnostics: list[str] = []

    # 1. Same-source selection (rare; no-op without candidates).
    picked = select_same_source(transcript, candidates or [])
    if candidates:
        diagnostics.append(
            f"same-source selection: merged {len(transcript.segments)}+{sum(len(c.segments) for c in candidates)}"
            f" cues -> {len(picked.segments)}"
        )

    # 2. ASR hallucination detection (only meaningful for whisper; CC is trusted by construction).
    is_hall, ratio, top_text = (False, 0.0, None)
    cross_windows = 0
    if picked.source == "whisper" and picked.segments:
        is_hall, ratio, top_text = detect_hallucination(picked)
        if is_hall:
            diagnostics.append(
                f"hallucination: ASR repeats '{(top_text or '')[:30]}' for {ratio:.0%} of cues"
            )
        # 3. Cross-source verification against OCR (when present).
        cross_windows = cross_verify_with_ocr(picked, ocr)
        if ocr and cross_windows:
            diagnostics.append(
                f"cross-source: {cross_windows} ASR windows disagree with overlapping OCR "
                f"(OCR is the corroborated truth in those windows)"
            )

    # 4. Annotate source_reason with fusion diagnostics (append, never replace the original).
    if diagnostics:
        note = " | fusion: " + "; ".join(diagnostics)
        if note not in (picked.source_reason or ""):
            picked = picked.model_copy(update={"source_reason": (picked.source_reason or "") + note})

    return FusionResult(
        transcript=picked,
        ocr=ocr,
        diagnostics=diagnostics,
        hallucination=is_hall,
        repeat_ratio=ratio,
        top_repeated_text=top_text,
        cross_verified_windows=cross_windows,
    )