import types

from harvest.config import Settings
from harvest.frames import dedup_phashes, hamming
from harvest.providers.base import Canonical


def test_hamming_identical_is_zero():
    assert hamming("ffff0000ffff0000", "ffff0000ffff0000") == 0


def test_hamming_counts_differing_bits():
    # 0x0 vs 0xf differ in 4 bits; rest identical.
    assert hamming("0000000000000000", "000000000000000f") == 4


def test_dedup_collapses_near_duplicate_run():
    # Three near-identical frames (same slide) then a clearly different one.
    items = [
        (0.0, "0000000000000000"),
        (4.0, "0000000000000001"),  # 1 bit off -> same slide
        (8.0, "0000000000000003"),  # 2 bits off the first kept -> still same slide
        (12.0, "ffffffffffffffff"),  # totally different -> new slide
    ]
    kept = dedup_phashes(items, threshold=5)
    assert [ts for ts, _ in kept] == [0.0, 12.0]


def test_dedup_keeps_distinct_slides():
    items = [
        (0.0, "0000000000000000"),
        (4.0, "ffffffffffffffff"),
        (8.0, "0f0f0f0f0f0f0f0f"),
    ]
    kept = dedup_phashes(items, threshold=5)
    assert len(kept) == 3


def test_dedup_compares_against_last_kept_not_last_seen():
    # Gradual drift: each step is within threshold of its predecessor, but cumulatively far.
    # Comparing against the last KEPT frame must eventually trigger a new keep.
    items = [
        (0.0, "0000000000000000"),
        (4.0, "0000000000000003"),   # 2 from kept[0] -> drop
        (8.0, "000000000000000f"),   # 4 from kept[0] -> drop
        (12.0, "00000000000000ff"),  # 8 from kept[0] -> keep
    ]
    kept = dedup_phashes(items, threshold=5)
    assert [ts for ts, _ in kept] == [0.0, 12.0]


def test_dedup_empty():
    assert dedup_phashes([], threshold=5) == []


# --- issue #9: video download must not hand YouTube to aria2c (same class as #3) ---------------


class _FakeYDL:
    """Records the opts it was constructed with and writes the target file on extract_info."""

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
    from harvest import frames as F

    def make(opts):
        opts["_test_info"] = info
        opts["_test_writes"] = writes
        return _FakeYDL(opts)

    monkeypatch.setattr(F, "yt_dlp", types.SimpleNamespace(YoutubeDL=make))


def _prep(tmp_path, key):
    vdir = tmp_path / "video"
    vdir.mkdir(parents=True)
    return vdir / f"{key}.mp4"


def test_download_video_youtube_uses_native_downloader(tmp_path, monkeypatch):
    from harvest import frames as F

    s = Settings(cache_dir=tmp_path, aria2c_path="C:/aria2c.exe")
    target = _prep(tmp_path, "youtube.com_F8X9_Dp3ZUk_1")
    canon = Canonical("youtube.com", "F8X9_Dp3ZUk", 1,
                      "https://www.youtube.com/watch?v=F8X9_Dp3ZUk")
    info = {"requested_downloads": [{"filepath": str(target)}]}
    _fake_ydl_factory(monkeypatch, info, lambda: target.write_bytes(b"x"))
    F.download_video(canon, s)
    assert "external_downloader" not in _FakeYDL.captured


def test_download_video_bilibili_keeps_aria2c(tmp_path, monkeypatch):
    from harvest import frames as F

    s = Settings(cache_dir=tmp_path, aria2c_path="C:/aria2c.exe")
    target = _prep(tmp_path, "bilibili.com_BV1_1")
    canon = Canonical("bilibili.com", "BV1", 1, "https://www.bilibili.com/video/BV1")
    info = {"requested_downloads": [{"filepath": str(target)}]}
    _fake_ydl_factory(monkeypatch, info, lambda: target.write_bytes(b"x"))
    F.download_video(canon, s)
    assert _FakeYDL.captured["external_downloader"] == {"default": "C:/aria2c.exe"}
