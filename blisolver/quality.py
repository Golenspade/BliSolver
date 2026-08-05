"""Subtitle quality gate (SPEC §7, D5).

Policy: fail-toward-Whisper. Any ONE of {punct_density, dup_ratio, nonzh_ratio, cps} tripping its
config threshold rejects the sub -> Whisper. Not a weighted score. Thresholds are calibratable
guesses in config.py, tuned on a real degraded lecture (build step 3).
"""

from __future__ import annotations

from .config import QualityThresholds
from .schema import QualityGate, Segment

_PUNCT = set("。，、！？；：「」『』（）《》〈〉【】—…·.,!?;:\"'()[]{}<>-")


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def evaluate(
    segments: list[Segment],
    duration_s: float,
    thresholds: QualityThresholds,
    *,
    source: str | None = None,
    lang_key: str | None = None,
) -> QualityGate:
    """Quality gate. Any ONE tripping metric rejects the sub -> Whisper. Not a weighted score.

    `source` ("human-sub"/"auto-sub") scopes which metrics apply: punct_density is only
    meaningful for human-CC (ASR/auto-sub captions lack punctuation by default — measuring it
    on them flags source-TYPE not quality; across Phase F sampling every good AND bad ai-zh
    scored 0.0-0.011, all wrongly rejected at 0.04). For auto-sub we skip punct_density and rely
    on dup_ratio/nonzh_ratio/cps, which DO separate good AI (content-correct) from bad
    (looping/music-symbol) — verified on 9 real videos. The metric is still REPORTED so the
    gate stays transparent; it just doesn't trip the verdict for auto-sub."""
    text = "".join(s.text for s in segments)
    total = len(text)

    punct = sum(1 for c in text if c in _PUNCT)
    punct_density = punct / total if total else 0.0

    n = len(segments)
    uniq = len({s.text for s in segments})
    dup_ratio = (n - uniq) / n if n else 0.0

    letters = [c for c in text if not c.isspace()]
    is_foreign = lang_key and not any(zh in lang_key for zh in ("zh", "zh-CN", "zh-TW", "zh-Hans", "zh-Hant"))
    if is_foreign:
        nonzh_ratio = 0.0
    else:
        nonzh = [c for c in letters if not _is_cjk(c) and c not in _PUNCT and not c.isdigit()]
        nonzh_ratio = len(nonzh) / len(letters) if letters else 0.0

    cps = total / duration_s if duration_s and duration_s > 0 else None

    punct_trips = punct_density < thresholds.punct_density_min
    if source == "auto-sub":
        # ASR captions are punctuation-less by construction; the metric is reported but doesn't
        # trip the verdict (it can't tell good AI from bad — both score ~0).
        punct_trips = False
    
    cps_trips = cps is not None and not (thresholds.cps_min <= cps <= thresholds.cps_max)
    if is_foreign and cps is not None:
        # English CPS is natively much higher than CJK (letters vs characters)
        cps_trips = cps > 40.0 # arbitrary high bound for English
        
    tripped = (
        punct_trips
        or dup_ratio > thresholds.dup_ratio_max
        or nonzh_ratio > thresholds.nonzh_ratio_max
        or cps_trips
    )

    return QualityGate(
        passed=not tripped,
        punct_density=round(punct_density, 4),
        dup_ratio=round(dup_ratio, 4),
        nonzh_ratio=round(nonzh_ratio, 4),
        cps=round(cps, 4) if cps is not None else None,
    )


def describe_failure(gate: QualityGate, t: QualityThresholds, *, source: str | None = None) -> str:
    """Human-readable list of which metric(s) tripped, for the D2 bundle.md header.
    Mirrors evaluate()'s source scoping: punct_density is not a trip cause for auto-sub."""
    fails = []
    if source != "auto-sub" and gate.punct_density < t.punct_density_min:
        fails.append(f"punct_density {gate.punct_density} < {t.punct_density_min}")
    if gate.dup_ratio > t.dup_ratio_max:
        fails.append(f"dup_ratio {gate.dup_ratio} > {t.dup_ratio_max}")
    if gate.nonzh_ratio > t.nonzh_ratio_max:
        fails.append(f"nonzh_ratio {gate.nonzh_ratio} > {t.nonzh_ratio_max}")
    if gate.cps is not None and (gate.cps < t.cps_min or gate.cps > t.cps_max):
        fails.append(f"cps {gate.cps} outside {t.cps_min}-{t.cps_max}")
    return ", ".join(fails) if fails else "passed"
