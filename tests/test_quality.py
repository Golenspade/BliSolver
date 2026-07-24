from harvest.config import QualityThresholds
from harvest.quality import evaluate
from harvest.schema import Segment


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
