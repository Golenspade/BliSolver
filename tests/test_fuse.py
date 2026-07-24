"""Phase D: multi-source fusion — ASR hallucination detection, OCR cross-verification,
same-source selection. Unit coverage; the end-to-end run on BV1LD7U65Ew2 (whisper 67%
repetition hallucination vs real OCR) is the real proof.
"""
from harvest.fuse import (
    HALLUC_REPEAT_RATIO,
    cross_verify_with_ocr,
    detect_hallucination,
    fuse,
)
from harvest.schema import Segment, Transcript


def _whisper_transcript(texts: list[str], dur: float = 1.0) -> Transcript:
    """A whisper transcript whose cues all start at i*1.0, end at i*1.0+dur."""
    segs = [Segment(start=i * 1.0, end=i * 1.0 + dur, text=t, source="whisper") for i, t in enumerate(texts)]
    return Transcript(source="whisper", source_reason="r", segments=segs)


def _ocr(segs: list[tuple[float, float, str]]) -> list[Segment]:
    return [Segment(start=s, end=e, text=t, source="ocr", confidence=0.95) for s, e, t in segs]


# --- hallucination detection ---------------------------------------------------------

def test_hallucination_flags_dominant_repeat():
    # BV1LD7U65Ew2 shape: 46/69 cues "冰淇淋-6 颗", 21 "5 颗", 2 distinct others.
    texts = ["冰淇淋-6 颗"] * 46 + ["5 颗"] * 21 + ["今天看看GitHub", "AstrBot PR"]
    is_hall, ratio, top = detect_hallucination(_whisper_transcript(texts))
    assert is_hall is True
    assert ratio >= 46 / 69 - 0.01
    assert "冰淇淋" in (top or "")


def test_hallucination_flags_adjacent_run_even_below_ratio():
    # 6 adjacent identical cues but only 6/15 = 0.40 total — exactly at ratio; the adjacent-run
    # signal (>=5) should still trip it (loops, not just dominant share).
    texts = ["重复"] * 6 + [f"内容{i}" for i in range(9)]
    is_hall, ratio, _ = detect_hallucination(_whisper_transcript(texts))
    assert is_hall is True  # adjacent run >= HALLUC_ADJACENT_RUN
    assert ratio == round(6 / 15, 3)


def test_hallucination_clean_transcript_not_flagged():
    # A real lecture repeats connectives but stays well under the threshold.
    texts = ["所以", "然后", "这里", "我们", "看到", "所以", "接下来", "这个", "然后", "它"]
    texts = texts * 3  # 30 cues, max repeat ~6, ratio 0.20 — below threshold, no adjacent run >=5
    is_hall, ratio, _ = detect_hallucination(_whisper_transcript(texts))
    assert is_hall is False
    assert ratio < HALLUC_REPEAT_RATIO


def test_hallucination_short_transcript_skipped():
    # <3 cues can't statistically trip; return False, not a false positive.
    assert detect_hallucination(_whisper_transcript(["a", "a"])) == (False, 0.0, None)


def test_hallucination_cc_transcript_never_checked_by_fuse():
    # CC is trusted by construction; fuse only runs hallucination detection on source="whisper".
    cc = Transcript(
        source="human-sub", source_reason="r",
        segments=[Segment(start=0, end=1, text="重复", source="human-sub", confidence=1.0)] * 50,
    )
    res = fuse(cc, ocr=None)
    assert res.hallucination is False  # not even checked for CC
    assert res.diagnostics == []


# --- cross-source verification -------------------------------------------------------

def test_cross_verify_counts_disagreeing_windows():
    # ASR repeats one fragment; OCR carries varied real content in the same windows.
    asr = _whisper_transcript(["冰淇淋-6 颗"] * 5)  # 0-5s
    ocr = _ocr([(0, 1, "今天看看GitHub"), (1, 2, "AstrBot PR"), (2, 3, "配置错误"),
                (3, 4, "另一个内容"), (4, 5, "更多内容")])
    n = cross_verify_with_ocr(asr, ocr)
    assert n == 5  # all 5 ASR windows disagree with overlapping OCR


def test_cross_verify_no_ocr_returns_zero():
    asr = _whisper_transcript(["x"] * 10)
    assert cross_verify_with_ocr(asr, None) == 0
    assert cross_verify_with_ocr(asr, []) == 0


def test_cross_verify_corroborated_asr_not_counted():
    # ASR and OCR agree -> 0 disagreeing windows.
    asr = _whisper_transcript(["今天看看GitHub", "AstrBot PR"])
    ocr = _ocr([(0, 1, "今天看看GitHub"), (1, 2, "AstrBot PR 神人")])
    assert cross_verify_with_ocr(asr, ocr) == 0


# --- fuse end-to-end (unit) ----------------------------------------------------------

def test_fuse_annotates_source_reason_with_diagnostics_on_hallucination():
    asr = _whisper_transcript(["冰淇淋-6 颗"] * 10)  # 0-10s, 100% repetition -> hallucination
    ocr = _ocr([(0, 2, "今天看看GitHub"), (2, 4, "AstrBot PR"), (4, 6, "配置"),
                (6, 8, "内容"), (8, 10, "更多")])
    res = fuse(asr, ocr)
    assert res.hallucination is True
    assert res.repeat_ratio == 1.0
    assert res.cross_verified_windows >= 5
    # source_reason carries the fusion note (append, original reason preserved).
    assert "fusion" in res.transcript.source_reason
    assert "hallucination" in res.transcript.source_reason
    assert "cross-source" in res.transcript.source_reason
    # The transcript body is UNCHANGED (§5.2 contract).
    assert [s.text for s in res.transcript.segments] == [s.text for s in asr.segments]


def test_fuse_clean_asr_with_ocr_only_emits_cross_corroboration_signal():
    # No hallucination; cross-verify runs but finds agreement -> no cross diagnostic.
    asr = _whisper_transcript(["今天看看GitHub", "AstrBot PR", "然后说了什么"])
    ocr = _ocr([(0, 1, "今天看看GitHub"), (1, 2, "AstrBot PR 神人"), (2, 3, "然后说了那个")])
    res = fuse(asr, ocr)
    assert res.hallucination is False
    assert res.cross_verified_windows == 0
    assert res.diagnostics == []  # clean: nothing to say


def test_fuse_without_ocr_skips_cross_verification():
    asr = _whisper_transcript(["冰淇淋-6 颗"] * 10)
    res = fuse(asr, ocr=None)
    assert res.hallucination is True
    assert res.cross_verified_windows == 0  # no OCR to verify against
    assert "cross-source" not in (res.transcript.source_reason or "")


# same-source selection (§5.3) was DROPPED after real multi-source testing showed its 0.80
# similarity threshold rejected 95% of overlapping cues (CC is long-merged, AI is short-
# chopped, granularity misaligned) — the quality gate already does source selection
# (gate-passing source wins); a second fuse-layer merge had negative ROI. See fuse.py
# module docstring for the full rationale and commit history for the removed code.
