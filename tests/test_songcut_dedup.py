import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from blisolver.songcut.dedup import RATE, compare, fingerprint


def recording(seconds=70, seed=42):
    rng = np.random.default_rng(seed)
    # Broadband, temporally varying recording; not a deterministic test-tone identity shortcut.
    noise = rng.normal(size=RATE*seconds)
    return noise * (.2+.1*np.sin(np.arange(len(noise))/RATE))


def test_same_recording_gain_offset_and_internal_edit():
    reference = recording()
    gain_offset = reference[3*RATE:63*RATE]*.4
    result = compare(gain_offset, reference)
    assert result["decision"] == "same_recording_high_fit"
    assert result["piecewise_offsets"][0]["offset"] == pytest.approx(3, abs=.002)
    edited = np.r_[reference[:30*RATE], reference[36*RATE:]]
    result = compare(edited, reference)
    assert result["decision"] == "same_recording_high_fit"
    assert len(result["piecewise_offsets"]) == 2
    assert result["identity_proven"] is False


def test_same_backing_different_vocal_not_duplicate():
    backing = recording()
    a = backing + recording(seed=5)*2
    b = backing + recording(seed=6)*2
    assert compare(a, b)["decision"] == "not_established"


def test_tones_and_short_audio_cannot_prove_identity():
    t = np.arange(16*RATE)/RATE
    tone = np.sin(2*np.pi*440*t)
    assert compare(tone, tone)["decision"] == "not_established"
    short = recording()[:3*RATE]
    assert compare(short, short)["reason"] == "too_short"


def test_fingerprint_keeps_long_recording_time_width():
    hashes, times = fingerprint(recording(seconds=8))
    assert len(hashes) > 0
    assert times.dtype == np.uint32
