"""Command-danmaku (互动弹幕) acquisition: fetch `x/v2/dm/web/view`, decode `commandDms`, and build
the structured `Interactions` schema. Separate acquisition from the danmaku census (`seg.so`); plain
cookies, no WBI (SPEC §3) — the same `player_api._opener` the census and player-API use.

NO LLM: command danmaku are already structured, so this decodes straight to schema (no mirror, no
clustering). Only two `command` kinds carry a crowd signal and are whitelisted: `#VOTE#` (投票) and
`#GRADE#` (评分). Others (`#ATTENTION#` follow prompts, `#LINK#` cards) are dropped.

`build_interactions` is pure (raws -> schema); `fetch_interactions` owns the one HTTP GET and mirrors
`player_api.fetch_danmaku`'s "absence degrades to an empty result, never raises" convention."""

from __future__ import annotations

import json
import logging
from urllib.error import URLError

from .config import Settings
from .interactions_proto import RawCommandDm, decode_view
from .player_api import ViewData, ViewError, _opener, cid_for_part, fetch_view
from .resolve import Canonical
from .schema import Grade, Interactions, Vote, VoteOption

logger = logging.getLogger(__name__)

_API_VIEW_CMD = "https://api.bilibili.com/x/v2/dm/web/view?type=1&oid={cid}&pid={aid}"

_VOTE = "#VOTE#"
_GRADE = "#GRADE#"


def _parse_vote(extra: dict, progress_ms: int | None) -> Vote:
    options = [
        VoteOption(
            text=str(o.get("desc") or ""),
            count=int(o.get("cnt") or 0),
            write_in=bool(o.get("has_self_def", False)),
        )
        for o in (extra.get("options") or [])
        if isinstance(o, dict)
    ]
    return Vote(
        question=str(extra.get("question") or ""),
        options=options,
        total_count=int(extra.get("cnt") or 0),
        ts=(progress_ms / 1000.0) if progress_ms is not None else None,
    )


def _parse_grade(extra: dict) -> Grade:
    return Grade(avg_score=float(extra.get("avg_score") or 0.0), count=int(extra.get("count") or 0))


def build_interactions(raws: list[RawCommandDm]) -> Interactions:
    """Whitelist `#VOTE#`/`#GRADE#` and parse each record's JSON `extra` into schema. A record whose
    `extra` is not valid JSON (or is the wrong shape) is skipped — one bad widget never aborts the
    rest. Pure and deterministic; no network."""
    votes: list[Vote] = []
    grades: list[Grade] = []
    for r in raws:
        if r.command not in (_VOTE, _GRADE):
            continue
        try:
            extra = json.loads(r.extra)
        except json.JSONDecodeError:
            continue
        if not isinstance(extra, dict):
            continue
        try:
            if r.command == _VOTE:
                votes.append(_parse_vote(extra, r.progress_ms))
            else:
                grades.append(_parse_grade(extra))
        except (TypeError, ValueError):
            continue
    return Interactions(votes=votes, grades=grades)


def fetch_interactions(
    canonical: Canonical, settings: Settings, *, opener=None, view: ViewData | None = None
) -> Interactions:
    """Fetch + decode the command danmaku for this part via `x/v2/dm/web/view`.

    Always returns an `Interactions`, never raises or returns `None`: when no cid/aid resolves for
    the part (or the view is unavailable/`ViewError`s, or the HTTP GET fails), returns an empty
    `Interactions()`. This mirrors `player_api.fetch_danmaku`'s graceful-absence stance. `opener`
    and `view` are injectable for tests / to share an already-fetched view."""
    op = opener or _opener(settings)

    if view is None:
        try:
            view = fetch_view(canonical, settings, opener=op)
        except ViewError:
            return Interactions()

    aid = view.aid
    cid = cid_for_part(view, canonical.part)
    if not (aid and cid):
        return Interactions()

    url = _API_VIEW_CMD.format(cid=cid, aid=aid)
    try:
        body = op.open(url, timeout=60).read()
    except URLError as exc:
        logger.warning("interactions view fetch failed for cid=%s: %s", cid, exc)
        return Interactions()
    return build_interactions(decode_view(body))
