from harvest.resolve import Canonical, resolve


def test_com_bv_default_part_is_1():
    c = resolve("https://www.bilibili.com/video/BV1xx411x7xx")
    assert c == Canonical(
        platform="bilibili.com",
        id="BV1xx411x7xx",
        part=1,
        url="https://www.bilibili.com/video/BV1xx411x7xx",
    )


def test_com_part_query_detected():
    c = resolve("https://www.bilibili.com/video/BV1xx411x7xx?p=3")
    assert c.part == 3
    assert c.id == "BV1xx411x7xx"
    assert c.url == "https://www.bilibili.com/video/BV1xx411x7xx?p=3"


def test_com_av_id_preserved():
    c = resolve("https://www.bilibili.com/video/av170001")
    assert c.platform == "bilibili.com"
    assert c.id == "av170001"
    assert c.part == 1


def test_tracking_params_stripped_but_part_kept():
    c = resolve("https://www.bilibili.com/video/BV1xx411x7xx?p=2&spm_id_from=333.999&vd_source=abc")
    assert c.id == "BV1xx411x7xx"
    assert c.part == 2
    assert c.url == "https://www.bilibili.com/video/BV1xx411x7xx?p=2"


def test_mobile_host_normalized_to_com():
    c = resolve("https://m.bilibili.com/video/BV1xx411x7xx")
    assert c.platform == "bilibili.com"
    assert c.id == "BV1xx411x7xx"


def test_tv_platform_and_numeric_id():
    c = resolve("https://www.bilibili.tv/en/video/4789031742737920")
    assert c.platform == "bilibili.tv"
    assert c.id == "4789031742737920"
    assert c.part == 1


def test_b23_short_link_is_expanded():
    def fake_expander(url: str) -> str:
        assert url == "https://b23.tv/abc123"
        return "https://www.bilibili.com/video/BV1xx411x7xx?p=5"

    c = resolve("https://b23.tv/abc123", expander=fake_expander)
    assert c.platform == "bilibili.com"
    assert c.id == "BV1xx411x7xx"
    assert c.part == 5


def test_non_bilibili_url_rejected():
    import pytest

    with pytest.raises(ValueError):
        resolve("https://example.com/watch?v=abc")


def test_padded_title_prefix_sanitized():
    raw = "【力工？喜欢梭哈？坏了！】 https://www.bilibili.com/video/BV1x2T463E7L/?share_source=copy_web&vd_source=e1c23ae5b8eaaf371f354162dfdd6378"
    c = resolve(raw)
    assert c.platform == "bilibili.com"
    assert c.id == "BV1x2T463E7L"
    assert c.part == 1
    assert c.url == "https://www.bilibili.com/video/BV1x2T463E7L"


def test_iframe_html_snippet_sanitized():
    raw = '<iframe src="//player.bilibili.com/player.html?isOutside=true&aid=116855775694254&bvid=BV1x2T463E7L&cid=39621102157&p=1" scrolling="no" border="0" frameborder="no" framespacing="0" allowfullscreen="true"></iframe>'
    c = resolve(raw)
    assert c.platform == "bilibili.com"
    assert c.id == "BV1x2T463E7L"
    assert c.part == 1
    assert c.url == "https://www.bilibili.com/video/BV1x2T463E7L"


def test_player_embed_url_with_part():
    raw = "//player.bilibili.com/player.html?bvid=BV1x2T463E7L&p=3"
    c = resolve(raw)
    assert c.platform == "bilibili.com"
    assert c.id == "BV1x2T463E7L"
    assert c.part == 3
    assert c.url == "https://www.bilibili.com/video/BV1x2T463E7L?p=3"


def test_complex_spm_tracking_query():
    raw = "https://www.bilibili.com/video/BV1x2T463E7L/?spm_id_from=333.1007.tianma.1-2-2.click&vd_source=3ace871988d2f560287739374159bd38"
    c = resolve(raw)
    assert c.platform == "bilibili.com"
    assert c.id == "BV1x2T463E7L"
    assert c.part == 1
    assert c.url == "https://www.bilibili.com/video/BV1x2T463E7L"


def test_select_provider_with_messy_input():
    from harvest.providers.base import select_provider
    from harvest.providers.bilibili import BilibiliProvider

    raw = "【分享视频】 https://www.bilibili.com/video/BV1x2T463E7L?p=2"
    provider = select_provider(raw)
    assert isinstance(provider, BilibiliProvider)

