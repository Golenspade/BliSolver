import types
from pathlib import Path

import pytest

from blisolver.config import Settings
from blisolver.providers.base import Canonical
from blisolver.subtitles import ydl_opts


def _stub_whisper_cli(monkeypatch, tmp_path, *, lang=None, robust=False):
    """Wire the whisper-cli subprocess shim: skip the ffmpeg downmix, fake whisper-cli writing
    an SRT next to the audio, and capture the argv handed to subprocess.run. Returns the dict
    the captured command lands in."""
    from blisolver import transcribe as T

    audio = tmp_path / "x.m4a"
    audio.write_bytes(b"x")
    captured: dict = {"cmd": None}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        out_prefix = cmd[cmd.index("-of") + 1]
        Path(str(out_prefix) + ".srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n hi \n", encoding="utf-8"
        )
        return None

    monkeypatch.setattr(T.subprocess, "run", fake_run)
    monkeypatch.setattr(T, "_to_wav16k", lambda p, s: p)  # skip the real downmix
    monkeypatch.setattr(Settings, "load", lambda: Settings())
    monkeypatch.setattr(T, "WHISPER_CLI", "whisper-cli")
    return audio, captured


def test_transcribe_defaults_language_to_none(monkeypatch, tmp_path):
    # whisper-cli shim: default lang=None => NO "-l" flag (auto-detect); robust=False => no
    # "--no-context". SRT output is parsed back into Segments.
    from blisolver import transcribe as T
    audio, captured = _stub_whisper_cli(monkeypatch, tmp_path)
    segs = T.transcribe(audio, model="ggml.bin")
    cmd = captured["cmd"]
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == "ggml.bin"
    assert "-l" not in cmd            # default: language auto-detect, no flag
    assert "--no-context" not in cmd   # robust=False
    assert segs[0].text == "hi"


def test_transcribe_threads_explicit_lang(monkeypatch, tmp_path):
    # explicit lang="zh" => "-l zh" on the whisper-cli argv; robust=True => "--no-context".
    from blisolver import transcribe as T
    audio, captured = _stub_whisper_cli(monkeypatch, tmp_path)
    T.transcribe(audio, model="ggml.bin", lang="zh", robust=True)
    cmd = captured["cmd"]
    assert "-l" in cmd and cmd[cmd.index("-l") + 1] == "zh"
    assert "--no-context" in cmd   # robust=True


# --- issue #3: robust audio-file recovery + downloader scoping ---------------------------------


def test_ydl_opts_external_downloader_false_omits_aria2c():
    # issue #3: YouTube (external_downloader=False) must use yt-dlp's native downloader, not
    # aria2c, whose parallel connections YouTube throttles to a crawl and which bypasses the
    # n-signature handling. aria2c stays the default for bilibili's throttled CDN.
    s = Settings(aria2c_path="C:/aria2c.exe")
    opts = ydl_opts(s, external_downloader=False)
    assert "external_downloader" not in opts
    assert opts["http_chunk_size"] > 0


def test_ydl_opts_default_uses_aria2c_when_available():
    s = Settings(aria2c_path="C:/aria2c.exe")
    opts = ydl_opts(s)
    assert opts["external_downloader"] == {"default": "C:/aria2c.exe"}


class _FakeYDL:
    """Records the opts it was constructed with and runs a side effect on extract_info."""

    captured: dict = {}

    def __init__(self, opts):
        _FakeYDL.captured = opts
        self._info = opts.pop("_test_info", {})
        self._writes = opts.pop("_test_writes", None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download):
        if self._writes is not None:
            self._writes()
        return self._info


def _fake_ydl_factory(monkeypatch, info, writes=None):
    def make(opts):
        opts["_test_info"] = info
        opts["_test_writes"] = writes
        return _FakeYDL(opts)

    from blisolver import transcribe as T
    fake_mod = types.SimpleNamespace(YoutubeDL=make)
    monkeypatch.setattr(T, "yt_dlp", fake_mod)


def _canon(platform="youtube.com"):
    return Canonical(platform, "F8X9_Dp3ZUk", 1, "https://www.youtube.com/watch?v=F8X9_Dp3ZUk")


def test_download_audio_returns_filepath_from_requested_downloads(tmp_path, monkeypatch):
    from blisolver import transcribe as T
    s = Settings(cache_dir=tmp_path)
    audio = tmp_path / "audio"
    audio.mkdir(parents=True)
    key = "youtube.com_F8X9_Dp3ZUk_1"
    target = audio / f"{key}.webm"

    def writes():
        target.write_bytes(b"x")

    info = {"requested_downloads": [{"filepath": str(target)}]}
    _fake_ydl_factory(monkeypatch, info, writes)
    assert T.download_audio(_canon(), s) == target


def test_download_audio_recovers_via_glob_when_filepath_missing(tmp_path, monkeypatch):
    # The reported failure mode: requested_downloads[0] lacks a filepath key entirely, but the
    # file IS on disk. Recover it via the glob instead of raising a bare StopIteration.
    from blisolver import transcribe as T
    s = Settings(cache_dir=tmp_path)
    audio = tmp_path / "audio"
    audio.mkdir(parents=True)
    key = "youtube.com_F8X9_Dp3ZUk_1"
    target = audio / f"{key}.webm"

    def writes():
        target.write_bytes(b"x")

    info = {"requested_downloads": [{"asr": 44100, "format_id": "251"}]}  # no filepath
    _fake_ydl_factory(monkeypatch, info, writes)
    assert T.download_audio(_canon(), s) == target


def test_download_audio_raises_descriptive_error_when_no_file(tmp_path, monkeypatch):
    # issue #3: download "succeeded" (no exception) but wrote no file -> fail loud, not a bare
    # StopIteration. The message must name the expected pattern and the yt-dlp info keys.
    from blisolver import transcribe as T
    s = Settings(cache_dir=tmp_path)
    info = {"requested_downloads": [{"asr": 44100}], "id": "F8X9_Dp3ZUk"}
    _fake_ydl_factory(monkeypatch, info, writes=None)  # writes nothing
    with pytest.raises(RuntimeError) as ei:
        T.download_audio(_canon(), s)
    msg = str(ei.value)
    assert "youtube.com_F8X9_Dp3ZUk_1" in msg
    assert "requested_downloads" in msg


def test_download_audio_youtube_uses_native_downloader(tmp_path, monkeypatch):
    # issue #3 root-cause mitigation: the YouTube audio path must NOT hand the download to aria2c.
    from blisolver import transcribe as T
    s = Settings(cache_dir=tmp_path, aria2c_path="C:/aria2c.exe")
    audio = tmp_path / "audio"
    audio.mkdir(parents=True)
    target = audio / "youtube.com_F8X9_Dp3ZUk_1.webm"
    info = {"requested_downloads": [{"filepath": str(target)}]}
    _fake_ydl_factory(monkeypatch, info, lambda: target.write_bytes(b"x"))
    T.download_audio(_canon(), s)
    assert "external_downloader" not in _FakeYDL.captured


def test_download_audio_bilibili_keeps_aria2c(tmp_path, monkeypatch):
    from blisolver import transcribe as T
    s = Settings(cache_dir=tmp_path, aria2c_path="C:/aria2c.exe")
    audio = tmp_path / "audio"
    audio.mkdir(parents=True)
    target = audio / "bilibili.com_BV1_1.m4a"
    canon = Canonical("bilibili.com", "BV1", 1, "https://www.bilibili.com/video/BV1")
    info = {"requested_downloads": [{"filepath": str(target)}]}
    _fake_ydl_factory(monkeypatch, info, lambda: target.write_bytes(b"x"))
    T.download_audio(canon, s)
    assert _FakeYDL.captured["external_downloader"] == {"default": "C:/aria2c.exe"}
