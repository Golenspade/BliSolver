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
    segments: list[Segment], duration_s: float, thresholds: QualityThresholds
) -> QualityGate:
    text = "".join(s.text for s in segments)
    total = len(text)

    punct = sum(1 for c in text if c in _PUNCT)
    punct_density = punct / total if total else 0.0

    n = len(segments)
    uniq = len({s.text for s in segments})
    dup_ratio = (n - uniq) / n if n else 0.0

    letters = [c for c in text if not c.isspace()]
    nonzh = [c for c in letters if not _is_cjk(c) and c not in _PUNCT and not c.isdigit()]
    nonzh_ratio = len(nonzh) / len(letters) if letters else 0.0

    cps = total / duration_s if duration_s and duration_s > 0 else None

    tripped = (
        punct_density < thresholds.punct_density_min
        or dup_ratio > thresholds.dup_ratio_max
        or nonzh_ratio > thresholds.nonzh_ratio_max
        or (cps is not None and (cps < thresholds.cps_min or cps > thresholds.cps_max))
    )

    return QualityGate(
        passed=not tripped,
        punct_density=round(punct_density, 4),
        dup_ratio=round(dup_ratio, 4),
        nonzh_ratio=round(nonzh_ratio, 4),
        cps=round(cps, 4) if cps is not None else None,
    )


def describe_failure(gate: QualityGate, t: QualityThresholds) -> str:
    """Human-readable list of which metric(s) tripped, for the D2 bundle.md header."""
    fails = []
    if gate.punct_density < t.punct_density_min:
        fails.append(f"punct_density {gate.punct_density} < {t.punct_density_min}")
    if gate.dup_ratio > t.dup_ratio_max:
        fails.append(f"dup_ratio {gate.dup_ratio} > {t.dup_ratio_max}")
    if gate.nonzh_ratio > t.nonzh_ratio_max:
        fails.append(f"nonzh_ratio {gate.nonzh_ratio} > {t.nonzh_ratio_max}")
    if gate.cps is not None and (gate.cps < t.cps_min or gate.cps > t.cps_max):
        fails.append(f"cps {gate.cps} outside {t.cps_min}-{t.cps_max}")
    return ", ".join(fails) if fails else "passed"
