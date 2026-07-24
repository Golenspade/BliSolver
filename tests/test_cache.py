from harvest.cache import stage_key


def test_base_key_with_no_params():
    assert stage_key("bilibili.com", "BV1xx411x7xx", 1) == "bilibili.com:BV1xx411x7xx:1"


def test_params_append_a_short_hash_after_base():
    k = stage_key("bilibili.com", "BV1", 1, force_whisper=True, robust=False)
    base, sep, h = k.partition("#")
    assert base == "bilibili.com:BV1:1"
    assert sep == "#"
    assert h and len(h) <= 16


def test_different_params_produce_different_keys():
    a = stage_key("bilibili.com", "BV1", 1, force_whisper=True)
    b = stage_key("bilibili.com", "BV1", 1, force_whisper=False)
    assert a != b


def test_param_order_does_not_matter():
    a = stage_key("bilibili.com", "BV1", 1, scene_threshold=27.0, phash=5)
    b = stage_key("bilibili.com", "BV1", 1, phash=5, scene_threshold=27.0)
    assert a == b


def test_base_identity_unchanged_when_params_added():
    # D6: changing a stage param must not invalidate the whole video's cache.
    base = stage_key("bilibili.com", "BV1", 2)
    keyed = stage_key("bilibili.com", "BV1", 2, scene_threshold=30.0)
    assert keyed.startswith(base + "#")
