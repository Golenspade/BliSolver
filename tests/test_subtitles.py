from harvest.config import REFERER, Settings
from harvest.resolve import Canonical
from harvest.schema import Segment
from harvest.subtitles import SubtitleResult, is_part1_duplicate, probe, ydl_opts


def _segs(texts):
    return [Segment(start=i * 4.0, end=(i + 1) * 4.0, text=t) for i, t in enumerate(texts)]


def test_ydl_opts_default_includes_bilibili_referer():
    opts = ydl_opts(Settings())
    assert opts["http_headers"]["Referer"] == REFERER


def test_ydl_opts_referer_none_omits_referer_header():
    opts = ydl_opts(Settings(), referer=None)
    assert "Referer" not in opts["http_headers"]


def test_ydl_opts_default_attaches_browser_cookie_jar():
    # Default (no SESSDATA): bilibili's cookie jar fallback is still attached.
    opts = ydl_opts(Settings())
    assert "cookiesfrombrowser" in opts


def test_ydl_opts_browser_cookies_false_omits_cookie_jar():
    # Regression (issue #1): a caller (YouTube) must be able to produce opts with NO browser
    # cookie jar, so yt-dlp's default format selection isn't broken by a logged-in session.
    opts = ydl_opts(Settings(), browser_cookies=False)
    assert "cookiesfrombrowser" not in opts


def test_ydl_opts_wires_detected_js_runtime():
    # issue #5: a detected JS runtime is handed to yt-dlp so it uses YouTube's real web player
    # client instead of degrading to a stripped/blocked response.
    s = Settings()
    s.js_runtime = ("deno", "C:/Users/x/.deno/bin/deno.exe")
    opts = ydl_opts(s)
    assert opts["js_runtimes"] == {"deno": {"path": "C:/Users/x/.deno/bin/deno.exe"}}


def test_ydl_opts_omits_js_runtimes_when_none():
    s = Settings()
    s.js_runtime = None
    assert "js_runtimes" not in ydl_opts(s)


def test_is_part1_duplicate_identical_text():
    a = _segs(["第一句。", "第二句。", "第三句。"])
    b = _segs(["第一句。", "第二句。", "第三句。"])
    assert is_part1_duplicate(a, b) is True


def test_is_part1_duplicate_distinct_text():
    a = _segs(["这是第二部分的内容。", "完全不同的讲解。"])
    b = _segs(["这是第一部分的开场。", "另一段话。"])
    assert is_part1_duplicate(a, b) is False


def test_is_part1_duplicate_near_identical_counts_as_dup():
    # #6357 returns part 1's text verbatim; allow trivial drift (one cue differs).
    a = _segs(["大家好，欢迎来到本课程。", "今天我们讲第一章。", "谢谢观看。"])
    b = _segs(["大家好，欢迎来到本课程。", "今天我们讲第一章。", "谢谢观看！"])
    assert is_part1_duplicate(a, b) is True


def test_is_part1_duplicate_empty_part1_is_not_dup():
    a = _segs(["有内容。"])
    assert is_part1_duplicate(a, []) is False


def test_probe_rejects_part_gt1_when_identical_to_part1():
    # tier-1 (duration sanity) passes; tier-2 (#6357) must reject.
    segs = _segs(["第一句。", "第二句。", "第三句。"])
    info = {
        "duration": 12.0,
        "subtitles": {"zh-Hans": [{"ext": "json", "url": "http://x/sub.json"}]},
    }
    canonical = Canonical("bilibili.com", "BV1", 3, "https://b/video/BV1?p=3")
    result = probe(
        info, canonical, _Settings(), part1_segments=segs, _fetch=lambda *_: segs
    )
    assert result.found is False
    assert "6357" in result.reason


def test_probe_accepts_part_gt1_when_text_differs_from_part1():
    segs_p3 = _segs(["这是第三部分。", "独特的内容。"])
    segs_p1 = _segs(["这是第一部分。", "开场白。"])
    info = {
        "duration": 8.0,
        "subtitles": {"zh-Hans": [{"ext": "json", "url": "http://x/sub.json"}]},
    }
    canonical = Canonical("bilibili.com", "BV1", 3, "https://b/video/BV1?p=3")
    result = probe(
        info, canonical, _Settings(), part1_segments=segs_p1, _fetch=lambda *_: segs_p3
    )
    assert isinstance(result, SubtitleResult)
    assert result.found is True


def test_parse_vtt_basic_cues():
    from harvest.subtitles import parse_vtt
    vtt = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:02.500\nHello world\n\n"
        "00:00:02.500 --> 00:00:05.000\nSecond line\nwrapped\n"
    )
    segs = parse_vtt(vtt)
    assert len(segs) == 2
    assert segs[0].start == 0.0 and segs[0].end == 2.5 and segs[0].text == "Hello world"
    assert segs[1].start == 2.5 and segs[1].end == 5.0 and segs[1].text == "Second line wrapped"


def test_parse_vtt_ignores_header_notes_and_cue_ids():
    from harvest.subtitles import parse_vtt
    vtt = (
        "WEBVTT - Kind: captions\n\n"
        "NOTE this is a comment\n\n"
        "cue-1\n00:00:01.000 --> 00:00:02.000\nOnly text\n"
    )
    segs = parse_vtt(vtt)
    assert len(segs) == 1 and segs[0].text == "Only text" and segs[0].start == 1.0


class _Settings:
    """Minimal stand-in; probe must not touch the network when _fetch is injected."""
