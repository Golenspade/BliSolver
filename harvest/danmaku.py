"""Danmaku representation stage: turns raw danmaku records (Task 2's `fetch_danmaku`) into a
faithful `Danmaku` mirror via a tightly-fenced LLM call (SPEC danmaku build, Task 3).

This is the ONE place in `harvest` an LLM produces output. The fence is the whole point: mirror,
never decode/translate/sentiment/topic-label; every quoted string is verbatim; lines within a
window are ordered CHRONOLOGICALLY by content time (never by count). A `high_like` (bilibili
高赞 / platform-promoted) danmaku is extracted verbatim BEFORE clustering, entirely mechanically
-- the LLM never sees it and never decides promotion. The pipeline is:

    raw records --[window by content-time]--> per-window records
               --[partition]--> high_like records | ordinary records
    ordinary   --[exact-dedup, deterministic, no LLM]--> (text, count, content_ts) entries
               --[fenced LLM cluster call, batched: {text,count} only]--> DanmakuLine clusters
    high_like  --[exact-dedup, deterministic, no LLM]--> DanmakuLine(high_like=True) singletons
               --[merge-sort both by first-occurrence content_ts]--> DanmakuWindow -> Danmaku

Structured like `harvest/vision.py`: reuses its `_client(settings)` (OpenAI-compatible LM Studio
endpoint), follows `cli.py::_caption`'s all-or-nothing stage-cache pattern, and follows
`merge.py::chunk`'s `bisect_right` bucketing pattern for windowing.
"""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_right

from .cache import fs_key, load_json, save_json
from .config import Settings
from .player_api import DanmakuFetch, RawDanmaku
from .providers.base import Canonical
from .schema import Danmaku, DanmakuLine, DanmakuWindow
from .vision import _client

PROMPT_VERSION = "1"

# Dynamic count-batching: a per-call cap on deduped entries handed to the LLM in one request.
# Internal optimization detail, not a contract knob -- no CLI/env override.
_BATCH_CAP = 200

_THINK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.IGNORECASE | re.DOTALL)

DANMAKU_PROMPT = (
    "You process a batch of bilibili danmaku (scrolling audience comments), already deduplicated "
    "for byte-identical repeats and given to you in chronological content-time order. Produce a "
    "FAITHFUL MIRROR of what the crowd said -- you do NOT interpret, summarize, translate, or "
    "explain it.\n\n"
    "Absolute rules (violating any one = failure):\n"
    "1. Mirror, never decode. Never explain what a comment or meme means. Never translate. Never "
    "label sentiment or topic -- writing things like \"the crowd is mocking X\" is FORBIDDEN.\n"
    "2. Verbatim only. Every quoted string is an EXACT danmaku from the input. Never paraphrase, "
    "normalize, or clean up text.\n"
    "3. Representative selection, not exhaustive. Cluster the same crowd-thought together, but "
    "preserve distinct minority/singleton voices -- rarity is not a reason to drop distinct "
    "content.\n"
    "4. Cluster near-identical danmaku into ONE line with a summed count. \"Near-identical\" means "
    "the same crowd-thought with trivial variation (punctuation, emoji, character variants of one "
    "phrase). Quote the single most representative verbatim form for the cluster.\n"
    "5. Order the output lines CHRONOLOGICALLY -- the same order the input entries first appear "
    "in. NEVER reorder by count.\n\n"
    "Input: a JSON array of {\"text\": <verbatim danmaku>, \"count\": <exact-duplicate count>} "
    "objects, already in chronological order.\n"
    "Output: ONLY a JSON array of {\"text\": <verbatim representative>, \"count\": <summed count>} "
    "objects, in the same chronological order the clusters first appear. No prose, no markdown "
    "fences, no commentary -- JSON only."
)


# ---------------------------------------------------------------------------
# Pure piece: exact-dedup pre-pass (deterministic, no LLM)
# ---------------------------------------------------------------------------


def _exact_dedup(records: list[RawDanmaku]) -> list[tuple[str, int, float]]:
    """Collapse byte-identical `text` into `(text, count, first_occurrence_content_ts)`,
    preserving first-occurrence chronological order (`records` is assumed already
    content_ts-sorted, per `fetch_danmaku`). Used for BOTH the ordinary flood (feeds the LLM
    clusterer) and the high_like extraction path (collapsed mechanically, no LLM) -- each entry's
    `content_ts` is the merge-sort key that reassembles a window's final chronological order."""
    counts: dict[str, int] = {}
    first_ts: dict[str, float] = {}
    order: list[str] = []
    for r in records:
        if r.text not in counts:
            counts[r.text] = 0
            first_ts[r.text] = r.content_ts
            order.append(r.text)
        counts[r.text] += 1
    return [(text, counts[text], first_ts[text]) for text in order]


def _dedup_elevated(records: list[RawDanmaku]) -> list[tuple[DanmakuLine, float]]:
    """Collapse byte-identical ELEVATED danmaku (high_like and/or author), keyed on
    (text, high_like, author) so distinct elevation combos stay distinct, preserving
    first-occurrence content_ts. Each becomes a DanmakuLine carrying its own flags. Never
    LLM-clustered -- an elevated line's exact wording IS its value (a promoted meme, an
    uploader correction), so it is extracted verbatim before the ordinary flood is clustered."""
    counts: dict[tuple[str, bool, str | None], int] = {}
    first_ts: dict[tuple[str, bool, str | None], float] = {}
    order: list[tuple[str, bool, str | None]] = []
    for r in records:
        key = (r.text, r.high_like, r.author)
        if key not in counts:
            counts[key] = 0
            first_ts[key] = r.content_ts
            order.append(key)
        counts[key] += 1
    out: list[tuple[DanmakuLine, float]] = []
    for key in order:
        text, high_like, author = key
        out.append(
            (DanmakuLine(text=text, count=counts[key], high_like=high_like, author=author),
             first_ts[key])
        )
    return out


# ---------------------------------------------------------------------------
# Pure piece: windowing (content-time, aligned to bundle chunks)
# ---------------------------------------------------------------------------


def _boundaries(*, window_s: float, duration_s: float | None) -> list[float]:
    """Fixed wall-clock window boundaries covering `duration_s` (mirrors merge.chunk's fallback
    branch). Used when the caller doesn't supply explicit boundaries."""
    end = duration_s or 0.0
    return [i * window_s for i in range(int(end // window_s) + 1)] or [0.0]


def window_records(
    records: list[RawDanmaku],
    boundaries: list[float],
    *,
    duration_s: float | None = None,
    window_s: float | None = None,
) -> list[tuple[float, float, list[RawDanmaku]]]:
    """Bucket `records` into windows by `content_ts` via `bisect_right` (merge.chunk's pattern).
    Returns `(start, end, records_in_window)` tuples; windows with zero danmaku are omitted.
    `end` for the last window is `duration_s` when given, else `boundaries[-1] + window_s`."""
    buckets: list[list[RawDanmaku]] = [[] for _ in boundaries]
    for r in records:
        idx = min(bisect_right(boundaries, r.content_ts) - 1, len(boundaries) - 1)
        idx = max(idx, 0)
        buckets[idx].append(r)

    out: list[tuple[float, float, list[RawDanmaku]]] = []
    for i, start in enumerate(boundaries):
        if i + 1 < len(boundaries):
            end = boundaries[i + 1]
        elif duration_s is not None:
            end = duration_s
        else:
            end = start + (window_s if window_s is not None else 0.0)
        if buckets[i]:
            out.append((start, end, buckets[i]))
    return out


# ---------------------------------------------------------------------------
# Pure piece: reasoning-model defense (strip a leading <think> block)
# ---------------------------------------------------------------------------


def _strip_think(text: str) -> str:
    """Defensively strip a leading `<think>...</think>` block (a reasoning model burning its
    token budget there before the real answer). No-op for non-reasoning models/responses."""
    return _THINK_RE.sub("", text, count=1).strip()


# ---------------------------------------------------------------------------
# Pure piece: response parser (JSON array of {text, count})
# ---------------------------------------------------------------------------


def _parse_response(text: str) -> list[DanmakuLine]:
    """Parse the LLM's response into `DanmakuLine`s. Strips a leading `<think>` block first, then
    extracts the first `[...]` JSON array in the (possibly prose-wrapped) response -- robust to
    models that add a preamble/postscript despite being told not to."""
    cleaned = _strip_think(text)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        # Trailing prose can contain a literal "]" (e.g. a bilibili emote shortcode like
        # "[doge]" in a model's closing remark), which confuses the naive find/rfind array
        # extraction and produces invalid JSON. Degrade gracefully -- an empty batch result --
        # rather than let an uncaught exception abort the whole stage.
        return []
    return [DanmakuLine(text=item["text"], count=int(item.get("count", 1))) for item in payload]


# ---------------------------------------------------------------------------
# Pure piece: merge cluster lines (across sub-batches) -- combine identical
# representative text, summing counts, preserving first-occurrence order.
# ---------------------------------------------------------------------------


def _merge_lines(lines: list[tuple[DanmakuLine, float]]) -> list[tuple[DanmakuLine, float]]:
    """Merge `(DanmakuLine, content_ts)` pairs across sub-batches: combine identical
    representative text (summing counts), keeping the EARLIEST content_ts seen for that text as
    its position in the window's final chronological merge-sort."""
    counts: dict[str, int] = {}
    ts: dict[str, float] = {}
    order: list[str] = []
    for line, line_ts in lines:
        if line.text not in counts:
            counts[line.text] = 0
            ts[line.text] = line_ts
            order.append(line.text)
        counts[line.text] += line.count
        ts[line.text] = min(ts[line.text], line_ts)
    return [(DanmakuLine(text=text, count=counts[text]), ts[text]) for text in order]


# ---------------------------------------------------------------------------
# Pure piece: mechanical chronological reorder (do NOT trust the LLM for order)
# ---------------------------------------------------------------------------


def _reorder_chronologically(
    lines: list[DanmakuLine], entries: list[tuple[str, int, float]]
) -> list[tuple[DanmakuLine, float]]:
    """Reorder `lines` (a batch's parsed LLM response) by each line's `text` FIRST-OCCURRENCE
    position in `entries` (the deduped, chronologically-ordered batch input), and pair each line
    with that entry's first-occurrence `content_ts`. Chronological order within a window is a
    LOAD-BEARING locked constraint (the probe found count-sort destroyed the temporal signal), so
    it is enforced mechanically here rather than trusted from the prompt text alone -- real model
    drift could otherwise silently reintroduce that exact regression. The returned `content_ts`
    also doubles as the merge-sort key the caller uses to interleave high_like lines.

    Representative text is contractually verbatim, so it should match an input entry; a line
    whose text does NOT match any entry (already a verbatim-rule violation) is kept after the
    matched ones, in the order the LLM returned it -- with a fallback content_ts of the batch's
    last entry, so it still sorts after everything the batch actually saw -- so a fence violation
    degrades gracefully instead of crashing.
    """
    order = {text: (i, ts) for i, (text, _count, ts) in enumerate(entries)}
    matched = sorted(
        (line for line in lines if line.text in order), key=lambda line: order[line.text][0]
    )
    unmatched = [line for line in lines if line.text not in order]
    fallback_ts = entries[-1][2] if entries else 0.0
    return [(line, order[line.text][1]) for line in matched] + [
        (line, fallback_ts) for line in unmatched
    ]


# ---------------------------------------------------------------------------
# The fenced LLM cluster call
# ---------------------------------------------------------------------------


def _cluster_batch(
    client, model: str, max_tokens: int, entries: list[tuple[str, int, float]]
) -> list[tuple[DanmakuLine, float]]:
    # The LLM stays a fenced mirror: payload is ONLY {text, count} -- content_ts (and, by
    # construction, high_like -- promoted danmaku never reach this function) never crosses the
    # fence.
    payload = json.dumps(
        [{"text": t, "count": c} for t, c, _ts in entries], ensure_ascii=False
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": DANMAKU_PROMPT + "\n\nInput:\n" + payload},
        ],
        temperature=0,
        max_tokens=max_tokens,
    )
    content = (resp.choices[0].message.content or "").strip()
    lines = _parse_response(content)
    return _reorder_chronologically(lines, entries)


def _cluster_window(
    client, model: str, max_tokens: int, entries: list[tuple[str, int, float]]
) -> list[tuple[DanmakuLine, float]]:
    """Dynamic count-batching: split `entries` into consecutive sub-batches of at most
    `_BATCH_CAP`, call the LLM per batch, then merge (invisible outside this function -- the
    caller sees one clustered result for the whole window). Each returned line carries its
    first-occurrence `content_ts`, the merge-sort key `represent_danmaku` interleaves against
    high_like lines."""
    if not entries:
        return []
    batches = [entries[i : i + _BATCH_CAP] for i in range(0, len(entries), _BATCH_CAP)]
    lines: list[tuple[DanmakuLine, float]] = []
    for batch in batches:
        lines.extend(_cluster_batch(client, model, max_tokens, batch))
    return _merge_lines(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _fingerprint(fetch: DanmakuFetch) -> str:
    # high_like + author folded in: a re-fetch that only changes elevation status (no text/ts
    # change) must restage, since it changes which lines get extracted verbatim vs clustered.
    blob = "".join(
        f"{r.content_ts}:{r.high_like}:{r.author}:{r.text}\x1f" for r in fetch.records
    )
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]


def represent_danmaku(
    canonical: Canonical,
    fetch: DanmakuFetch,
    settings: Settings,
    *,
    window_s: float = 15.0,
    duration_s: float | None = None,
    boundaries: list[float] | None = None,
    client=None,
) -> Danmaku:
    """Turn a `DanmakuFetch` into a `Danmaku` mirror: window -> exact-dedup -> fenced LLM cluster
    call -> reassemble. All-or-nothing stage cache keyed on identity + params + a fingerprint of
    the fetched danmaku (mirrors `cli.py::_caption`'s `frameset` fingerprint pattern).

    Danmaku is windowed on its own fixed `window_s` cadence (default 15s), deliberately decoupled
    from the frame/transcript chunk boundaries — the crowd's pace is unrelated to slide cuts.
    `boundaries` remains an escape hatch for a caller that wants explicit window starts; absent it
    (the normal path), fixed `window_s`-wide boundaries covering `duration_s` are derived.
    `client` is injectable for offline testing (a stub with `.chat.completions.create(...)`);
    defaults to the real LM Studio client via `vision._client(settings)`.
    """
    model = settings.lmstudio_danmaku_model
    resolved_boundaries = (
        sorted(boundaries) if boundaries is not None else _boundaries(
            window_s=window_s, duration_s=duration_s
        )
    )

    key_params: dict = {
        "stage": "danmaku",
        "model": model,
        "prompt": PROMPT_VERSION,
        "dmset": _fingerprint(fetch),
    }
    if boundaries is not None:
        key_params["boundaries"] = hashlib.sha1(
            json.dumps(resolved_boundaries).encode()
        ).hexdigest()[:10]
    else:
        key_params["window_s"] = window_s

    key = fs_key(canonical.platform, canonical.id, canonical.part, **key_params)
    cached = load_json(settings.cache_dir, "danmaku", key)
    if cached is not None:
        return Danmaku(**cached)

    if client is None:
        client = _client(settings)

    windows: list[DanmakuWindow] = []
    for start, end, records in window_records(
        fetch.records, resolved_boundaries, duration_s=duration_s, window_s=window_s
    ):
        # Extract-before-cluster: any ELEVATED danmaku (high_like OR author) is pulled out BEFORE
        # the LLM sees the window, so its exact wording + flags are never absorbed into a flood
        # cluster. The LLM only ever sees the ordinary partition.
        elevated_records = [r for r in records if r.high_like or r.author is not None]
        ordinary_records = [r for r in records if not (r.high_like or r.author is not None)]

        ordinary_entries = _exact_dedup(ordinary_records)
        clustered = _cluster_window(
            client, model, settings.lmstudio_danmaku_max_tokens, ordinary_entries
        )

        elevated_lines = _dedup_elevated(elevated_records)

        # Mechanical merge-sort by first-occurrence content_ts -- the window's final chronological
        # order, interleaving elevated lines with the clustered ordinary ones.
        combined = sorted(clustered + elevated_lines, key=lambda pair: pair[1])
        lines = [line for line, _ts in combined]
        windows.append(DanmakuWindow(start=start, end=end, total=len(records), lines=lines))

    result = Danmaku(
        source_total=fetch.source_total,
        fetched_total=fetch.fetched_total,
        model=model,
        windows=windows,
    )
    save_json(settings.cache_dir, "danmaku", key, result.model_dump())
    return result
