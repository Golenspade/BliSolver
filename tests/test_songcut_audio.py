from pathlib import Path

import pytest

from blisolver.songcut.audio import AudioEngine
from blisolver.songcut.models import AudioOptions, ClipInput, Manifest
from blisolver.songcut.state import file_lock, sha256


def test_cut_range_preserve_dynamics_and_final_peak(songcut_wav, tmp_path):
    import numpy as np
    source = songcut_wav(seconds=24)
    before = sha256(source)
    engine = AudioEngine()
    cut = tmp_path / "cut.wav"
    assert engine.pcm(source, cut, 2, 22) == pytest.approx(20, abs=.001)
    receipt = engine.master(cut, tmp_path / "master", AudioOptions())
    assert receipt["mode"] == "constant_gain"
    assert receipt["output"]["input_tp"] <= -1.4
    assert receipt["decoded_duration"] == pytest.approx(20, abs=.1)
    original, final = engine.samples(cut), engine.samples(Path(receipt["audio"]))
    rms = lambda x: np.sqrt(np.mean(x*x))
    ratio = lambda x: rms(x[13*8000:17*8000])/rms(x[2*8000:6*8000])
    assert ratio(final)/ratio(original) == pytest.approx(1, rel=.1)
    assert sha256(source) == before


def test_master_cache_checks_artifact_hash(songcut_wav, tmp_path):
    engine = AudioEngine()
    source = songcut_wav()
    dest = tmp_path / "master"
    one = engine.master(source, dest, AudioOptions())
    target = Path(one["audio"])
    expected = sha256(target)
    target.write_bytes(b"corrupt")
    two = engine.master(source, dest, AudioOptions())
    assert sha256(target) == expected
    assert two["key"] == one["key"]
    three = engine.master(source, tmp_path / "other", AudioOptions(target_lufs=-20))
    assert three["key"] != one["key"]


def test_explicit_dynamic_mode_and_silence(songcut_wav, tmp_path):
    import numpy as np
    engine = AudioEngine()
    receipt = engine.master(songcut_wav(), tmp_path / "dynamic", AudioOptions(mode="dynamic"))
    assert receipt["mode"] == "dynamic"
    with pytest.raises(ValueError, match="silent"):
        engine.master(songcut_wav("silent.wav", samples=np.zeros(48000*4)),
                      tmp_path / "silent", AudioOptions())


def test_cut_past_eof_fails(songcut_wav, tmp_path):
    with pytest.raises(ValueError, match="exceeds"):
        AudioEngine().pcm(songcut_wav(seconds=4), tmp_path / "cut.wav", 2, 20)


@pytest.mark.parametrize("data", [
    {"id": "../escape", "source": "x"}, {"id": "ok", "source": "x", "start": float("nan")},
    {"id": "ok", "source": "x", "start": 3, "end": 2},
])
def test_invalid_clip_rejected(data):
    with pytest.raises(ValueError):
        ClipInput(**data)


def test_paid_manifest_requires_budget_and_unique_ids():
    clip = {"id": "x", "source": "x.wav"}
    with pytest.raises(ValueError, match="positive"):
        Manifest(clips=[clip], backend="dashscope")
    with pytest.raises(ValueError, match="unique"):
        Manifest(clips=[clip, clip])


def test_output_lock_is_exclusive_and_released(tmp_path):
    path = tmp_path / "run.lock"
    with file_lock(path), pytest.raises(RuntimeError, match="already"), file_lock(path):
        pytest.fail("lock unexpectedly acquired")
    with file_lock(path):
        assert path.exists()
