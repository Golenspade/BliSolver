from harvest.vision import _parse


def test_parse_splits_ocr_and_description():
    text = "OCR:\n大家好 AI Agent\nDESCRIPTION:\nA dark slide with a title."
    ocr, caption = _parse(text)
    assert ocr == "大家好 AI Agent"
    assert caption == "A dark slide with a title."


def test_parse_none_values_become_null():
    text = "OCR:\nNONE\nDESCRIPTION:\nNONE"
    ocr, caption = _parse(text)
    assert ocr is None
    assert caption is None


def test_parse_without_description_marker_is_caption_only():
    text = "A plain talking-head frame, no slide text."
    ocr, caption = _parse(text)
    assert ocr is None
    assert caption == "A plain talking-head frame, no slide text."


def test_parse_ocr_present_description_none():
    text = "OCR:\n01 / 69\nDESCRIPTION:\nNONE"
    ocr, caption = _parse(text)
    assert ocr == "01 / 69"
    assert caption is None
