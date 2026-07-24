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
