from blisolver.config import QualityThresholds
from blisolver.quality import evaluate
from blisolver.schema import Segment


def _segs(texts, step=4.0):
    return [
        Segment(start=i * step, end=(i + 1) * step, text=text)
        for i, text in enumerate(texts)
    ]


def test_clean_subtitle_passes():
    # Well-punctuated, varied, all-CJK, sane pacing.
    texts = [
        "大家好，今天我们来讲深度学习。",
        "首先，什么是神经网络？",
        "它由很多层组成，每一层都有权重。",
        "我们通过反向传播来训练它。",
    ]
    gate = evaluate(_segs(texts), duration_s=16.0, thresholds=QualityThresholds())
    assert gate.passed is True


def test_low_punctuation_density_trips():
    texts = ["大家好今天我们来讲深度学习" * 1 for _ in range(4)]
    # vary text so dup_ratio doesn't also trip; only punctuation is the problem
    texts = ["啊啊啊啊啊啊啊啊啊啊啊啊一", "二三四五六七八九十甲乙丙丁戊",
             "天地玄黄宇宙洪荒日月盈昃", "辰宿列张寒来暑往秋收冬藏"]
    gate = evaluate(_segs(texts), duration_s=16.0, thresholds=QualityThresholds())
    assert gate.passed is False
    assert gate.punct_density < QualityThresholds().punct_density_min


def test_high_duplication_ratio_trips():
    texts = ["这个这个这个。"] * 10  # heavy repetition
    gate = evaluate(_segs(texts), duration_s=40.0, thresholds=QualityThresholds())
    assert gate.passed is False
    assert gate.dup_ratio > QualityThresholds().dup_ratio_max


def test_high_non_cjk_ratio_trips():
    texts = ["asdf qwer zxcv hjkl, foo bar baz qux.",
             "lorem ipsum dolor sit amet, consectetur.",
             "the quick brown fox jumps over lazy dog.",
             "abcdefg hijklmn opqrst uvwxyz 1234567."]
    gate = evaluate(_segs(texts), duration_s=16.0, thresholds=QualityThresholds())
    assert gate.passed is False
    assert gate.nonzh_ratio > QualityThresholds().nonzh_ratio_max


def test_implausible_chars_per_second_trips():
    # A few chars stretched over a very long duration -> cps below floor.
    gate = evaluate(_segs(["你好。", "世界。"]), duration_s=600.0,
                    thresholds=QualityThresholds())
    assert gate.passed is False
    assert gate.cps < QualityThresholds().cps_min


# --- Phase F: source-aware punct_density (auto-sub skips it) -------------------------

def test_auto_sub_skips_punct_density_trip_but_still_reports_it():
    # ai-zh captions are punctuation-less by construction; punct_density alone must NOT reject
    # an auto-sub track (verified across Phase F sampling: every good AND bad ai-zh scored
    # 0.0-0.011 at the 0.04 floor, all wrongly rejected). The metric is still REPORTED so the
    # gate stays transparent.
    from blisolver.schema import Segment
    segs = [Segment(start=0, end=1, text="今天看看代码"), Segment(start=1, end=2, text="然后说了什么")]
    # No punctuation -> punct_density=0.0, below the 0.04 floor. human-sub rejects; auto-sub passes.
    human = evaluate(segs, duration_s=10.0, thresholds=QualityThresholds(), source="human-sub")
    auto = evaluate(segs, duration_s=10.0, thresholds=QualityThresholds(), source="auto-sub")
    assert human.passed is False              # human: punct_density trips
    assert auto.passed is True                # auto-sub: punct_density skipped, other metrics pass
    assert human.punct_density == auto.punct_density == 0.0  # value still reported in both


def test_auto_sub_bad_quality_still_rejected_without_punct():
    # A BAD ai-zh (looping music symbols) still trips via dup_ratio/nonzh_ratio/cps even with
    # punct_density skipped — so skipping punct for auto-sub never lets bad AI through. Verified
    # on BV17FKJ6iER6 ai-zh (dup 0.88, nonzh 0.46, cps 0.76).
    from blisolver.schema import Segment
    bad = [Segment(start=i*1.0, end=i*1.0+1.0, text="♪ 音乐 ♪") for i in range(10)]
    auto = evaluate(bad, duration_s=100.0, thresholds=QualityThresholds(), source="auto-sub")
    assert auto.passed is False
    assert auto.dup_ratio > QualityThresholds().dup_ratio_max


def test_describe_failure_source_aware():
    from blisolver.quality import describe_failure
    from blisolver.schema import QualityGate
    g = QualityGate(passed=False, punct_density=0.0, dup_ratio=0.0, nonzh_ratio=0.0, cps=5.0)
    t = QualityThresholds()
    # human-sub: punct_density 0.0 < 0.04 is a reported failure cause.
    assert "punct_density" in describe_failure(g, t, source="human-sub")
    # auto-sub: punct_density is NOT a failure cause (skipped), and nothing else tripped -> passed.
    assert "punct_density" not in describe_failure(g, t, source="auto-sub")
    assert describe_failure(g, t, source="auto-sub") == "passed"
