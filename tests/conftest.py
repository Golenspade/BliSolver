"""Shared synthetic media for offline songcut tests. No downloaded recordings or API keys."""

import shutil
import wave

import pytest


@pytest.fixture
def songcut_wav(tmp_path):
    np = pytest.importorskip("numpy")
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is not installed")

    def write(name="source.wav", seconds=14, seed=17, samples=None, rate=48000):
        if samples is None:
            rng = np.random.default_rng(seed)
            t = np.arange(round(seconds*rate))/rate
            envelope = np.where(t < seconds*.5, .045, .30)
            samples = envelope * (np.sin(2*np.pi*(440*t+7*t*t))*.7 +
                                   rng.normal(size=len(t))*.1)
        path = tmp_path / name
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes((np.clip(samples, -.99, .99)*32767).astype("<i2").tobytes())
        return path
    return write
