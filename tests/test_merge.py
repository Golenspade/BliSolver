import json

import yaml

from harvest.config import Settings
from harvest.merge import (
    _neutralize,
    build_bundle,
    chunk,
    chunk_boundaries,
    render_markdown,
    write_bundle,
)
from harvest.providers.base import Canonical, SourceMetadata
from harvest.schema import (
    Bundle,
    Danmaku,
    DanmakuLine,
    DanmakuWindow,
    Frame,
    Grade,
    Interactions,
    Meta,
    Segment,
    Transcript,
    Vote,
    VoteOption,
)


def _seg(start, end, text="x"):
    return Segment(start=start, end=end, text=text)


def _frontmatter(md):
    """Parse the --- ... --- YAML header of a rendered bundle.md into a dict."""
    lines = md.splitlines()
    assert lines[0] == "---"
    close = lines.index("---", 1)
    return yaml.safe_load("\n".join(lines[1:close]))


def _bundle(**overrides):
    """A minimal renderable bundle; override any field by keyword."""
    fields = dict(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    fields.update(overrides)
    return Bundle(**fields)


def test_frontmatter_title_with_colon_space_roundtrips():
    md = render_markdown(_bundle(title="Rust: The Book"), _settings())
    assert _frontmatter(md)["title"] == "Rust: The Book"


def test_frontmatter_leading_indicator_values_roundtrip():
    md = render_markdown(_bundle(title="[weird", uploader="& anchor"), _settings())
    fm = _frontmatter(md)
    assert fm["title"] == "[weird"
    assert fm["uploader"] == "& anchor"


def test_frontmatter_part_stays_int_and_fields_ordered():
    md = render_markdown(_bundle(uploader_id="42", part=1), _settings())
    fm = _frontmatter(md)
    assert fm["part"] == 1  # int, not "1"
    assert fm["uploader_id"] == "42"  # str preserved
    keys = list(fm.keys())
    assert keys.index("uploader") + 1 == keys.index("uploader_id")


def _canonical():
    return Canonical(
        platform="bilibili.com", id="BV1", part=1, url="https://b/video/BV1"
    )


def _transcript():
    return Transcript(source="whisper", source_reason="test", segments=[])


def _settings():
    s = Settings()
    s.tool_version = "t"
    return s


def _bundle_with_frame(rel_path):
    return Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[Frame(ts=2.0, path=rel_path, phash="abc", ocr="SLIDE TEXT", caption="a chart")],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )


def test_no_frame_images_omits_png_nulls_path_keeps_caption(tmp_path):
    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG fake")
    bundle = _bundle_with_frame("frames/00002000.png")
    settings = Settings()
    settings.out_dir = tmp_path / "out"

    out = write_bundle(
        bundle, settings,
        frame_sources={"frames/00002000.png": src},
        frame_images=False,
    )

    assert not (out / "frames").exists()  # D8: no PNGs shipped
    data = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["path"] is None  # path nulled
    assert data["frames"][0]["phash"] == "abc"  # but record retained
    md = (out / "bundle.md").read_text(encoding="utf-8")
    assert "SLIDE TEXT" in md and "a chart" in md  # caption text still in the product


def test_default_ships_png_and_keeps_path(tmp_path):
    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG fake")
    bundle = _bundle_with_frame("frames/00002000.png")
    settings = Settings()
    settings.out_dir = tmp_path / "out"

    out = write_bundle(bundle, settings, frame_sources={"frames/00002000.png": src})

    assert (out / "frames" / "00002000.png").exists()
    data = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["path"] == "frames/00002000.png"


def test_wallclock_chunking_buckets_by_window():
    segs = [_seg(0, 5), _seg(10, 15), _seg(80, 85), _seg(160, 165)]
    chunks = chunk(segs, [], window_s=75.0, duration_s=200.0)
    # boundaries at 0,75,150 -> segments grouped [0,10],[80],[160]
    assert [c.start for c in chunks] == [0.0, 75.0, 150.0]
    assert [len(c.segments) for c in chunks] == [2, 1, 1]


def test_segment_assigned_whole_by_start_never_split():
    # A segment straddling a boundary belongs entirely to the chunk of its start.
    segs = [_seg(70, 80)]  # starts in [0,75)
    chunks = chunk(segs, [], window_s=75.0, duration_s=150.0)
    assert len(chunks) == 1
    assert chunks[0].start == 0.0
    assert chunks[0].segments[0].start == 70


def test_frame_boundaries_used_when_frames_present():
    frames = [Frame(ts=12.0, phash="a"), Frame(ts=30.0, phash="b")]
    segs = [_seg(0, 5), _seg(20, 25), _seg(40, 45)]
    chunks = chunk(segs, frames, window_s=75.0, duration_s=60.0)
    # boundaries {0,12,30}: seg@0 -> chunk0; seg@20 -> chunk12; seg@40 -> chunk30
    assert [c.start for c in chunks] == [0.0, 12.0, 30.0]
    assert [s.segments and s.segments[0].start for s in chunks] == [0, 20, 40]
    # frame goes into the chunk it opens
    assert chunks[1].frames[0].ts == 12.0


def test_empty_chunks_dropped():
    frames = [Frame(ts=100.0, phash="a")]  # boundary with nothing before it but seg later
    segs = [_seg(105, 110)]
    chunks = chunk(segs, frames, window_s=75.0, duration_s=120.0)
    assert all(c.segments or c.frames for c in chunks)


def test_chunk_boundaries_extraction_leaves_chunk_output_identical_fixed_window():
    # Regression guard: chunk() must produce byte-identical output before/after the
    # chunk_boundaries() extraction, for the fixed wall-clock fallback branch (no frames).
    segs = [_seg(0, 5), _seg(10, 15), _seg(80, 85), _seg(160, 165)]
    boundaries = chunk_boundaries(segs, [], window_s=75.0, duration_s=200.0)
    assert boundaries == [0.0, 75.0, 150.0]
    chunks = chunk(segs, [], window_s=75.0, duration_s=200.0)
    assert [c.start for c in chunks] == boundaries[: len(chunks)]
    assert [c.start for c in chunks] == [0.0, 75.0, 150.0]
    assert [len(c.segments) for c in chunks] == [2, 1, 1]


def test_chunk_boundaries_extraction_leaves_chunk_output_identical_frames_present():
    # Same regression guard for the frames-present branch.
    frames = [Frame(ts=12.0, phash="a"), Frame(ts=30.0, phash="b")]
    segs = [_seg(0, 5), _seg(20, 25), _seg(40, 45)]
    boundaries = chunk_boundaries(segs, frames, window_s=75.0, duration_s=60.0)
    assert boundaries == [0.0, 12.0, 30.0]
    chunks = chunk(segs, frames, window_s=75.0, duration_s=60.0)
    assert [c.start for c in chunks] == [0.0, 12.0, 30.0]
    assert chunks[1].frames[0].ts == 12.0


def test_build_bundle_consumes_source_metadata():
    meta = SourceMetadata(
        platform="bilibili.com", id="BV1", title="View Title", uploader="View Owner",
        uploader_id="999", description="View description.", duration_s=123,
        published_at="2024-06-28T16:00:00+08:00", parts=1, part_durations_s=[123],
        thumbnail_url="http://x/thumb.jpg", view_count=1000, like_count=200,
        coin_count=30, favorite_count=40, share_count=10, reply_count=20, danmaku_count=50,
    )
    bundle = build_bundle(_canonical(), meta, _transcript(), [], _settings())
    assert bundle.title == "View Title"
    assert bundle.uploader == "View Owner"
    assert bundle.duration_s == 123
    assert bundle.uploader_id == "999"
    assert bundle.description == "View description."
    assert bundle.published_at == "2024-06-28T16:00:00+08:00"
    assert bundle.thumbnail_url == "http://x/thumb.jpg"
    assert bundle.stats.view_count == 1000
    assert bundle.stats.like_count == 200
    assert bundle.stats.coin_count == 30
    assert bundle.stats.favorite_count == 40
    assert bundle.stats.share_count == 10
    assert bundle.stats.reply_count == 20
    assert bundle.stats.danmaku_count == 50


def test_build_bundle_with_no_stats_or_thumbnail_leaves_them_none():
    meta = SourceMetadata(
        platform="youtube.com", id="x", title="yt title", uploader="yt uploader",
        uploader_id=None, description="yt description", duration_s=456,
        published_at=None, parts=1, part_durations_s=[456],
    )
    bundle = build_bundle(_canonical(), meta, _transcript(), [], _settings())
    assert bundle.thumbnail_url is None
    # stats is still populated (view_count/like_count fields exist even if all None) --
    # mirrors probe's Stats(...) construction so Bundle and ProbeResult behave consistently.
    assert bundle.stats is not None
    assert bundle.stats.view_count is None
    assert bundle.stats.danmaku_count is None


def test_build_bundle_with_missing_metadata_fields_is_none():
    meta = SourceMetadata(
        platform="youtube.com", id="x", title="yt title", uploader="yt uploader",
        uploader_id=None, description="yt description", duration_s=456,
        published_at=None, parts=1, part_durations_s=[456],
    )
    bundle = build_bundle(_canonical(), meta, _transcript(), [], _settings())
    assert bundle.title == "yt title"
    assert bundle.uploader == "yt uploader"
    assert bundle.duration_s == 456
    assert bundle.uploader_id is None
    assert bundle.description == "yt description"
    assert bundle.published_at is None


def test_render_markdown_emits_thumbnail_url_in_header():
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        thumbnail_url="http://x/thumb.jpg",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    assert _frontmatter(md)["thumbnail_url"] == "http://x/thumb.jpg"


def test_render_markdown_thumbnail_url_empty_when_none():
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    assert _frontmatter(md)["thumbnail_url"] == ""


def test_render_markdown_emits_uploader_id_and_description_section():
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        uploader_id="42",
        description="Line one.\n\nLine two with a URL: https://example.com",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    fm = _frontmatter(md)
    assert fm["uploader_id"] == "42"
    keys = list(fm.keys())
    assert keys.index("uploader") + 1 == keys.index("uploader_id")
    lines = md.splitlines()
    assert "## Description" in md
    assert "Line one." in md
    assert "Line two with a URL: https://example.com" in md
    # Description section appears after the H1 and before the transcript body.
    h1_idx = next(i for i, l in enumerate(lines) if l.startswith("# "))
    desc_idx = next(i for i, l in enumerate(lines) if l == "## Description")
    transcript_idx = next(i for i, l in enumerate(lines) if l == "## Transcript")
    assert h1_idx < desc_idx < transcript_idx


def test_render_markdown_emits_published_at_after_duration():
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        published_at="2024-06-28T16:00:00+08:00",
        duration_s=60,
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    fm = _frontmatter(md)
    assert fm["published_at"] == "2024-06-28T16:00:00+08:00"
    keys = list(fm.keys())
    assert keys.index("duration") + 1 == keys.index("published_at")


def test_render_markdown_published_at_empty_when_none():
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    assert _frontmatter(md)["published_at"] == ""


def test_render_markdown_omits_description_section_when_none():
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    assert "## Description" not in md
    assert _frontmatter(md)["uploader_id"] == ""  # still emitted, empty


def test_render_markdown_description_with_literal_dashes_and_hash_line_is_safe():
    """A description containing a line that is exactly `---` (a YAML/HR delimiter) and a line
    starting with `#` (looks like a heading) must not corrupt the frontmatter block or be
    misread as a new section. The frontmatter stays exactly the intended scalar lines, and the
    description text appears verbatim, in full, inside the ## Description body."""
    description = "Intro line.\n---\n# Not a real heading\nTrailing line."
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        uploader_id="42",
        description=description,
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())
    lines = md.splitlines()

    # Frontmatter: a parser reads from the first `---` to the *next* `---` it meets. That
    # block must be exactly the intended scalar lines -- the adversarial `---` inside the
    # description (which necessarily comes later, after the H1 and `## Description` heading)
    # must not be mistaken for the close fence, and nothing from the description must leak
    # into it.
    fm = _frontmatter(md)
    assert fm["title"] == "My Title"
    assert fm["uploader_id"] == "42"
    assert fm["transcript_source"] == "whisper (test)"

    # The description text appears verbatim (each of its lines, in order) inside the
    # ## Description section, after the H1.
    h1_idx = next(i for i, l in enumerate(lines) if l.startswith("# "))
    desc_idx = next(i for i, l in enumerate(lines) if l == "## Description")
    assert h1_idx < desc_idx

    # The adversarial --- and # lines are escaped so they cannot forge structure, but the text
    # is still present and legible.
    assert "\\---" in md
    assert "\\# Not a real heading" in md
    assert "Intro line." in md
    assert "Trailing line." in md
    # The only real H2 sections are tool-produced.
    assert [l for l in md.splitlines() if l.startswith("## ")] == ["## Description", "## Transcript"]


def test_bundle_json_roundtrips_new_fields(tmp_path):
    from harvest.schema import Stats

    bundle = _bundle_with_frame(None)
    bundle.uploader_id = "123"
    bundle.description = "desc text"
    bundle.thumbnail_url = "http://x/thumb.jpg"
    bundle.stats = Stats(view_count=1000, danmaku_count=50)
    settings = Settings()
    settings.out_dir = tmp_path / "out"

    out = write_bundle(bundle, settings, frame_sources={}, frame_images=False)

    data = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert data["uploader_id"] == "123"
    assert data["description"] == "desc text"
    assert data["thumbnail_url"] == "http://x/thumb.jpg"
    assert data["stats"]["view_count"] == 1000
    assert data["stats"]["danmaku_count"] == 50


def _bundle_with_danmaku(danmaku):
    return Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        title="My Title",
        uploader="My Uploader",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[],
        danmaku=danmaku,
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )


def test_transcript_section_nests_windows_under_h2():
    bundle = _bundle(
        transcript=Transcript(
            source="whisper", source_reason="test",
            segments=[_seg(0, 5, "hello"), _seg(80, 85, "later")],
        ),
    )
    md = render_markdown(bundle, _settings())
    lines = md.splitlines()
    assert "## Transcript" in lines
    # Windows are H3, not H2.
    assert any(l.startswith("### [") for l in lines)
    assert not any(l.startswith("## [") for l in lines)
    # Ordering: H1 < ## Transcript < first ### window.
    h1 = next(i for i, l in enumerate(lines) if l.startswith("# "))
    tr = lines.index("## Transcript")
    win = next(i for i, l in enumerate(lines) if l.startswith("### ["))
    assert h1 < tr < win


def test_no_transcript_placeholder_under_transcript_heading():
    bundle = _bundle(
        transcript=Transcript(source="whisper", source_reason="pending", segments=[]),
        frames=[],
    )
    md = render_markdown(bundle, _settings())
    lines = md.splitlines()
    assert "## Transcript" in lines
    ph = next(i for i, l in enumerate(lines) if "no transcript yet" in l)
    assert lines.index("## Transcript") < ph


def test_render_markdown_omits_danmaku_section_when_none():
    bundle = _bundle_with_danmaku(None)
    md = render_markdown(bundle, _settings())
    assert "## Danmaku" not in md


def test_render_markdown_omits_danmaku_section_when_no_windows():
    bundle = _bundle_with_danmaku(
        Danmaku(source_total=0, fetched_total=0, model=None, windows=[])
    )
    md = render_markdown(bundle, _settings())
    assert "## Danmaku" not in md


def test_render_markdown_emits_danmaku_section_with_provenance_and_counts():
    dm = Danmaku(
        source_total=100,
        fetched_total=80,
        model="qwen-test",
        windows=[
            DanmakuWindow(
                start=0.0, end=75.0, total=5,
                lines=[
                    DanmakuLine(text="草", count=3),
                    DanmakuLine(text="singleton comment", count=1),
                ],
            ),
        ],
    )
    bundle = _bundle_with_danmaku(dm)
    md = render_markdown(bundle, _settings())

    assert "## Danmaku" in md
    # Provenance line conveys: lower authority, fetched/source totals, model.
    assert "lower authority than transcript" in md
    assert "fetched 80" in md
    assert "of 100" in md
    assert "qwen-test" in md
    # Per-window header uses mm:ss + total (raw, pre-clustering) count.
    assert "### [00:00] (5 danmaku)" in md
    # count > 1 -> ×count shown; count == 1 -> omitted.
    assert "「草」 ×3" in md
    assert "「singleton comment」" in md
    assert "「singleton comment」 ×1" not in md


def test_render_markdown_danmaku_caps_lines_with_overflow_marker():
    settings = _settings()
    cap = settings.danmaku_md_cap
    lines = [DanmakuLine(text=f"line{i}", count=1) for i in range(cap + 7)]
    dm = Danmaku(
        source_total=None, fetched_total=len(lines), model=None,
        windows=[DanmakuWindow(start=0.0, end=75.0, total=len(lines), lines=lines)],
    )
    bundle = _bundle_with_danmaku(dm)
    md = render_markdown(bundle, settings)

    for i in range(cap):
        assert f"「line{i}」" in md
    for i in range(cap, cap + 7):
        assert f"「line{i}」" not in md
    assert "﹢7 more — see bundle.json" in md


def test_render_markdown_danmaku_under_cap_has_no_overflow_marker():
    lines = [DanmakuLine(text=f"line{i}", count=1) for i in range(3)]
    dm = Danmaku(
        source_total=None, fetched_total=3, model=None,
        windows=[DanmakuWindow(start=0.0, end=75.0, total=3, lines=lines)],
    )
    bundle = _bundle_with_danmaku(dm)
    md = render_markdown(bundle, _settings())
    assert "more — see bundle.json" not in md


def test_render_markdown_danmaku_pills_owner_staff_highlike_and_both():
    dm = Danmaku(
        source_total=None, fetched_total=4, model=None,
        windows=[DanmakuWindow(start=0.0, end=75.0, total=4, lines=[
            DanmakuLine(text="up", count=1, author="owner"),
            DanmakuLine(text="co", count=1, author="staff"),
            DanmakuLine(text="hot", count=1, high_like=True),
            DanmakuLine(text="both", count=1, high_like=True, author="owner"),
        ])],
    )
    md = render_markdown(_bundle_with_danmaku(dm), _settings())
    # Author pills carry a trailing `?` (unverified crc32 hash match); 👍 does not (platform flag).
    assert "- UP主? 「up」" in md
    assert "- 合作? 「co」" in md
    assert "- \U0001F44D 「hot」" in md
    assert "- \U0001F44D UP主? 「both」" in md


def test_render_markdown_danmaku_elevated_never_dropped_ordinary_capped():
    settings = _settings()
    cap = settings.danmaku_md_cap
    # Interleave: an elevated line, then cap+4 ordinary, then another elevated line.
    lines = (
        [DanmakuLine(text="owner note", count=1, author="owner")]
        + [DanmakuLine(text=f"ord{i}", count=1) for i in range(cap + 4)]
        + [DanmakuLine(text="hot", count=1, high_like=True)]
    )
    dm = Danmaku(
        source_total=None, fetched_total=len(lines), model=None,
        windows=[DanmakuWindow(start=0.0, end=75.0, total=len(lines), lines=lines)],
    )
    md = render_markdown(_bundle_with_danmaku(dm), settings)
    # Both elevated lines survive regardless of the ordinary cap.
    assert "- UP主? 「owner note」" in md
    assert "- \U0001F44D 「hot」" in md
    # Ordinary capped at `cap`; the 4 beyond are dropped with a single overflow marker.
    for i in range(cap):
        assert f"「ord{i}」" in md
    for i in range(cap, cap + 4):
        assert f"「ord{i}」" not in md
    assert "﹢4 more — see bundle.json" in md


def test_render_markdown_danmaku_preserves_chronological_order_across_kinds():
    dm = Danmaku(
        source_total=None, fetched_total=3, model=None,
        windows=[DanmakuWindow(start=0.0, end=75.0, total=3, lines=[
            DanmakuLine(text="first ordinary", count=1),
            DanmakuLine(text="second is owner", count=1, author="owner"),
            DanmakuLine(text="third hot", count=1, high_like=True),
        ])],
    )
    md = render_markdown(_bundle_with_danmaku(dm), _settings())
    assert md.index("first ordinary") < md.index("second is owner") < md.index("third hot")


def test_render_markdown_danmaku_note_explains_pills_and_author_caveat():
    dm = Danmaku(
        source_total=100, fetched_total=80, model="qwen-test",
        windows=[DanmakuWindow(start=0.0, end=75.0, total=1,
                               lines=[DanmakuLine(text="x", count=1)])],
    )
    md = render_markdown(_bundle_with_danmaku(dm), _settings())
    # Note names both pill kinds and calibrates the author match as unverified / not authoritative.
    assert "\U0001F44D = bilibili 高赞" in md
    assert "UP主?/合作?" in md
    assert "unverified — not authoritative" in md


def test_description_cannot_forge_sections():
    description = (
        "Intro line.\n"
        "## [00:00]\n"
        "fake transcript segment\n"
        "## Danmaku\n"
        "### [00:00] (999 danmaku)\n"
        "- fabricated crowd line\n"
        "---\n"
        "```python\n"
        "code\n"
        "```"
    )
    bundle = _bundle(description=description)
    md = render_markdown(bundle, _settings())
    lines = md.splitlines()

    # The only real H2 sections are the tool-produced ones (no danmaku on this bundle).
    assert [l for l in lines if l.startswith("## ")] == ["## Description", "## Transcript"]
    # Forged markers are escaped and inert.
    assert "\\## [00:00]" in md
    assert "\\## Danmaku" in md
    assert "\\### [00:00] (999 danmaku)" in md
    assert "\\---" in md
    assert "\\```python" in md
    # Text is still legible.
    assert "fake transcript segment" in md
    assert "Intro line." in md


def test_frame_ocr_and_caption_cannot_forge_sections():
    bundle = Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[Frame(ts=2.0, path=None, phash="abc", ocr="## Danmaku", caption="### fake")],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )
    md = render_markdown(bundle, _settings())

    # The only real H2 section is the tool-produced Transcript heading (no description,
    # no danmaku on this bundle) -- the forged headings inside frame fields never surface.
    assert [l for l in md.splitlines() if l.startswith("## ")] == ["## Transcript"]
    # Forged markers are escaped right after the slide-label prefix, and inert.
    assert "**slide (OCR):** \\## Danmaku" in md
    assert "**slide (figure):** \\### fake" in md
    # Text is still legible.
    assert "Danmaku" in md
    assert "fake" in md


def test_neutralize_leaves_ordinary_lines_untouched():
    assert _neutralize("plain line\nsecond line") == "plain line\nsecond line"
    assert _neutralize("a colon: here and a - dash") == "a colon: here and a - dash"


def test_danmaku_line_with_embedded_newline_cannot_forge_section():
    dm = Danmaku(
        source_total=None, fetched_total=1, model=None,
        windows=[DanmakuWindow(
            start=0.0, end=75.0, total=1,
            lines=[DanmakuLine(text="nice\n## Danmaku", count=1)],
        )],
    )
    bundle = _bundle_with_danmaku(dm)
    md = render_markdown(bundle, _settings())
    # Exactly one real ## Danmaku section header; the embedded one is escaped.
    assert md.count("\n## Danmaku") == 1
    assert "\\## Danmaku" in md


def test_bundle_json_roundtrip_carries_complete_uncapped_danmaku(tmp_path):
    cap = Settings().danmaku_md_cap
    lines = [DanmakuLine(text=f"line{i}", count=1) for i in range(cap + 10)]
    dm = Danmaku(
        source_total=200, fetched_total=len(lines), model="m1",
        windows=[DanmakuWindow(start=0.0, end=75.0, total=len(lines), lines=lines)],
    )
    bundle = _bundle_with_danmaku(dm)
    settings = Settings()
    settings.out_dir = tmp_path / "out"

    out = write_bundle(bundle, settings, frame_sources={}, frame_images=False)

    data = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert len(data["danmaku"]["windows"][0]["lines"]) == cap + 10  # uncapped

    roundtripped = Bundle.model_validate_json((out / "bundle.json").read_text(encoding="utf-8"))
    assert len(roundtripped.danmaku.windows[0].lines) == cap + 10
    assert roundtripped.danmaku.source_total == 200
    assert roundtripped.danmaku.model == "m1"

    md = (out / "bundle.md").read_text(encoding="utf-8")
    assert "﹢10 more — see bundle.json" in md  # bundle.md stays capped


def test_render_interactions_section():
    from harvest.merge import render_markdown
    interactions = Interactions(
        votes=[
            Vote(
                question="喜欢哪个版本？",
                options=[
                    VoteOption(text="只加黄葱", count=153),
                    VoteOption(text="其他，请补充", count=40, write_in=True),
                ],
                total_count=443,
                ts=371.3,
            )
        ],
        grades=[Grade(avg_score=9.9, count=178)],
    )
    bundle = _bundle()
    bundle.interactions = interactions
    md = render_markdown(bundle, _settings())
    assert "## Interactions" in md
    assert "9.9" in md and "178" in md          # grade summary
    assert "喜欢哪个版本？" in md                  # vote question, verbatim
    assert "[06:11]" in md                       # ts 371.3 -> mm:ss
    assert "只加黄葱" in md and "153" in md        # option + tally
    assert "443" in md                           # total


def test_render_no_interactions_section_when_none():
    from harvest.merge import render_markdown
    bundle = _bundle()
    bundle.interactions = None
    assert "## Interactions" not in render_markdown(bundle, _settings())


def test_render_no_interactions_section_when_empty():
    from harvest.merge import render_markdown
    bundle = _bundle()
    bundle.interactions = Interactions()  # requested, found nothing
    assert "## Interactions" not in render_markdown(bundle, _settings())


def test_build_bundle_threads_interactions():
    # extend the existing build_bundle test path: build_bundle accepts interactions= and sets it
    from harvest.merge import build_bundle
    meta = SourceMetadata(
        platform="bilibili.com", id="BV1", title="View Title", uploader="View Owner",
        uploader_id="999", description="View description.", duration_s=123,
        published_at="2024-06-28T16:00:00+08:00", parts=1, part_durations_s=[123],
    )
    interactions = Interactions(grades=[Grade(avg_score=8.0, count=5)])
    bundle = build_bundle(
        _canonical(), meta, _transcript(), [], _settings(), interactions=interactions
    )
    assert bundle.interactions == interactions
