# YouTube Auto-Caption Reuse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the YouTube provider reuse original-language auto-generated captions (`auto-sub`) in preference to local Whisper, gated by a language-agnostic structural validity net, so most videos skip the expensive GPU transcription.

**Architecture:** Extend `YouTubeProvider.fetch_subtitle` from a two-outcome path (human-sub | Whisper) to a three-tier one: **human-sub > auto-sub > whisper**. Human captions still win on an exact original-language key. When absent, pick the original-audio auto-caption track (preferring yt-dlp's `L-orig` key, or the sole `*-orig` key when the language is unknown), fetch its server-de-rolled `srt`, parse with the existing `parse_srt`, and accept it only if it clears a structural net (presence, duration-coverage, chars-per-second floor). Any miss or net failure falls back to Whisper, unchanged. The net and key-picking are pure functions in a new `youtube_autosub.py` module; the provider orchestrates.

**Tech Stack:** Python 3.11+, yt-dlp, pytest. No new dependencies.

## Global Constraints

- **Additive / contract-preserving:** `transcript.source` gains no new value — `"auto-sub"` already exists (bilibili path). Authority order documented in PROTOCOL.md/SPEC.md §8 is unchanged: `human-sub > whisper > auto-sub`.
- **No new dependencies.** No `langdetect`/`cld3` — language-ID checking is a documented non-goal.
- **Default posture is auto-by-default.** YouTube prefers the auto-caption over Whisper for cost; `--force-whisper` is the only override (it already short-circuits before `fetch_subtitle` in `cli.decide_transcript`, so no change is needed there).
- **The CJK `harvest/quality.py` gate is bilibili-only and MUST NOT be touched or imported by the YouTube path.** The YouTube net is separate and structural.
- **Acquisition format is `srt`** (YouTube's server-side de-rolled track), never `vtt`/`json3` (rolling, `<c>`-tagged, ~2× cue inflation). Human captions keep using `vtt`.
- **Fail toward Whisper on the structural axis:** absent track, wrong/ambiguous `-orig` set, truncated coverage, or empty-but-covered → Whisper.
- Thresholds live in config as calibratable defaults (like `QualityThresholds`), not literals in code.
- Tests are **offline** by default; inject caption text via the existing `fetch_url` parameter and build `info` dicts inline. One opt-in `@live` smoke test may hit the network (marked `@pytest.mark.live`, excluded from the default run).

---

### Task 1: `AutoSubNet` config thresholds

**Files:**
- Modify: `harvest/config.py` (add a dataclass next to `QualityThresholds`, and a `Settings` field)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `AutoSubNet` dataclass with fields `min_cues: int = 5`, `coverage_min: float = 0.70`, `coverage_max: float = 1.10`, `cps_min: float = 0.5`. `Settings` gains `youtube_auto: AutoSubNet` (default-constructed).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
from harvest.config import AutoSubNet, Settings


def test_settings_has_youtube_auto_net_defaults():
    s = Settings()
    assert isinstance(s.youtube_auto, AutoSubNet)
    assert s.youtube_auto.min_cues == 5
    assert s.youtube_auto.coverage_min == 0.70
    assert s.youtube_auto.coverage_max == 1.10
    assert s.youtube_auto.cps_min == 0.5


def test_two_settings_do_not_share_one_youtube_auto_instance():
    # default_factory, not a shared mutable default
    assert Settings().youtube_auto is not Settings().youtube_auto
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::test_settings_has_youtube_auto_net_defaults -v`
Expected: FAIL with `ImportError: cannot import name 'AutoSubNet'`

- [ ] **Step 3: Write minimal implementation**

In `harvest/config.py`, add this dataclass immediately after the `QualityThresholds` class:

```python
@dataclass
class AutoSubNet:
    """Structural validity net for YouTube auto-captions (SPEC §6). Language-agnostic — no
    per-language calibration, so unlike QualityThresholds it can't misfire across languages. Any
    single check failing falls the candidate back to Whisper. Starting guesses; calibrate later."""

    min_cues: int = 5          # a real track has more than a handful of cues
    coverage_min: float = 0.70  # last cue / duration, lower bound (truncation guard)
    coverage_max: float = 1.10  # last cue / duration, upper bound
    cps_min: float = 0.5        # chars/sec over the whole track; ~0 means music/silence, no speech
```

In the `Settings` dataclass, add this field directly below the existing `quality:` field:

```python
    # YouTube auto-caption structural net (SPEC §6) — separate from the CJK quality gate above
    youtube_auto: AutoSubNet = field(default_factory=AutoSubNet)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (both new tests, and all existing config tests)

- [ ] **Step 5: Commit**

```bash
git add harvest/config.py tests/test_config.py
git commit -m "feat(config): add AutoSubNet thresholds for YouTube auto-caption net"
```

---

### Task 2: `youtube_autosub.py` pure helpers

**Files:**
- Create: `harvest/providers/youtube_autosub.py`
- Test: `tests/test_youtube_autosub.py`

**Interfaces:**
- Consumes: `harvest.subtitles.parse_srt` (existing: `parse_srt(text: str) -> list[Segment]`), `harvest.schema.Segment`, `harvest.config.AutoSubNet` (Task 1).
- Produces:
  - `pick_auto_key(automatic_captions: dict, target: str | None) -> str | None` — the caption dict key to use, or None (→ Whisper).
  - `clean_srt_segments(raw: str) -> list[Segment]` — `parse_srt` + strip leading `>>` speaker markers, drop segments that become empty.
  - `structural_net(segments: list[Segment], duration_s: float, net: AutoSubNet) -> tuple[bool, str]` — `(passed, reason)`.

- [ ] **Step 1: Write the failing tests for `pick_auto_key`**

Create `tests/test_youtube_autosub.py`:

```python
from harvest.config import AutoSubNet
from harvest.providers.youtube_autosub import (
    clean_srt_segments,
    pick_auto_key,
    structural_net,
)


def test_pick_auto_key_prefers_orig_over_plain_for_known_lang():
    auto = {"en": [{"ext": "srt"}], "en-orig": [{"ext": "srt"}]}
    assert pick_auto_key(auto, "en") == "en-orig"


def test_pick_auto_key_falls_back_to_plain_when_no_orig():
    auto = {"ko": [{"ext": "srt"}]}
    assert pick_auto_key(auto, "ko") == "ko"


def test_pick_auto_key_known_lang_absent_returns_none():
    auto = {"fr": [{"ext": "srt"}], "fr-orig": [{"ext": "srt"}]}
    assert pick_auto_key(auto, "de") is None


def test_pick_auto_key_unknown_lang_uses_sole_orig():
    auto = {"en-orig": [{"ext": "srt"}], "es": [{"ext": "srt"}], "de": [{"ext": "srt"}]}
    assert pick_auto_key(auto, None) == "en-orig"


def test_pick_auto_key_unknown_lang_rejects_zero_orig():
    assert pick_auto_key({"en": [{"ext": "srt"}]}, None) is None


def test_pick_auto_key_unknown_lang_rejects_multiple_orig():
    auto = {"en-orig": [{"ext": "srt"}], "zh-orig": [{"ext": "srt"}]}
    assert pick_auto_key(auto, None) is None


def test_pick_auto_key_empty_dict_returns_none():
    assert pick_auto_key({}, "en") is None
    assert pick_auto_key({}, None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_youtube_autosub.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harvest.providers.youtube_autosub'`

- [ ] **Step 3: Implement `pick_auto_key`**

Create `harvest/providers/youtube_autosub.py`:

```python
"""YouTube auto-caption acquisition + structural validity net (SPEC §6).

Pure helpers, provider-orchestrated. The net is LANGUAGE-AGNOSTIC and structural (presence,
coverage, chars-per-second) — deliberately NOT the CJK `harvest/quality.py` gate, which can't be
calibrated across YouTube's ~150 language variants. Fail-toward-Whisper on any check.
"""

from __future__ import annotations

from ..config import AutoSubNet
from ..schema import Segment
from ..subtitles import parse_srt


def pick_auto_key(automatic_captions: dict, target: str | None) -> str | None:
    """Choose the original-audio auto-caption key, or None (-> Whisper).

    Known target L: prefer `L-orig` (yt-dlp's original-audio marker), then plain `L`. Neither -> None.
    Unknown target: use the sole `*-orig` key; 0 or >1 such keys is ambiguous -> None (don't guess)."""
    if target is not None:
        for key in (f"{target}-orig", target):
            if key in automatic_captions:
                return key
        return None
    origs = [k for k in automatic_captions if k.endswith("-orig")]
    return origs[0] if len(origs) == 1 else None
```

- [ ] **Step 4: Run tests to verify `pick_auto_key` passes**

Run: `python -m pytest tests/test_youtube_autosub.py -k pick_auto_key -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Write the failing tests for `clean_srt_segments`**

Append to `tests/test_youtube_autosub.py`:

```python
_SRT = (
    "1\n00:00:15,000 --> 00:00:18,760\n>> Yeah, we're good.\n\n"
    "2\n00:00:17,040 --> 00:00:20,440\nOkay, folks.\n\n"
    "3\n00:00:07,205 --> 00:00:09,225\n[music]\n"
)


def test_clean_srt_segments_strips_leading_speaker_marker():
    segs = clean_srt_segments(_SRT)
    assert segs[0].text == "Yeah, we're good."   # ">> " stripped


def test_clean_srt_segments_keeps_music_cue():
    segs = clean_srt_segments(_SRT)
    assert any(s.text == "[music]" for s in segs)


def test_clean_srt_segments_preserves_timing():
    segs = clean_srt_segments(_SRT)
    assert segs[0].start == 15.0 and segs[0].end == 18.76
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `python -m pytest tests/test_youtube_autosub.py -k clean_srt -v`
Expected: FAIL with `ImportError` / `cannot import name 'clean_srt_segments'`

- [ ] **Step 7: Implement `clean_srt_segments`**

Append to `harvest/providers/youtube_autosub.py`:

```python
def clean_srt_segments(raw: str) -> list[Segment]:
    """Parse YouTube's server-de-rolled SRT and strip its cosmetics. `parse_srt` handles the timing
    and comma-millisecond format; we only remove leading `>>` speaker-change markers (a documented
    auto-sub artifact). `[music]`/`[applause]` non-speech cues are kept — honest context."""
    out: list[Segment] = []
    for seg in parse_srt(raw):
        text = seg.text
        if text.startswith(">>"):
            text = text[2:].strip()
        if text:
            out.append(Segment(start=seg.start, end=seg.end, text=text))
    return out
```

- [ ] **Step 8: Run tests to verify `clean_srt_segments` passes**

Run: `python -m pytest tests/test_youtube_autosub.py -k clean_srt -v`
Expected: PASS (3 tests)

- [ ] **Step 9: Write the failing tests for `structural_net`**

Append to `tests/test_youtube_autosub.py`:

```python
def _segs(n, *, start=0.0, step=2.0, text="hello there friend"):
    return [Segment(start=start + i * step, end=start + i * step + step, text=text) for i in range(n)]


def test_structural_net_passes_healthy_track():
    net = AutoSubNet()
    segs = _segs(60)  # 120s of cues, dense text
    passed, reason = structural_net(segs, 120.0, net)
    assert passed is True and "passed" in reason


def test_structural_net_rejects_too_few_cues():
    passed, reason = structural_net(_segs(3), 120.0, AutoSubNet())
    assert passed is False and "cues" in reason


def test_structural_net_rejects_truncated_coverage():
    # 60 cues ending at ~120s, but the video is 400s -> ratio 0.30 < 0.70
    passed, reason = structural_net(_segs(60), 400.0, AutoSubNet())
    assert passed is False and "coverage" in reason


def test_structural_net_rejects_empty_but_covered():
    # Two cues spanning the whole duration but almost no text -> cps below floor
    segs = [Segment(start=0.0, end=1.0, text="[music]"), Segment(start=299.0, end=300.0, text="[music]")]
    passed, reason = structural_net(segs, 300.0, AutoSubNet())
    assert passed is False and "chars-per-second" in reason


def test_structural_net_no_duration_skips_coverage_and_cps():
    # duration 0/unknown: only the presence check applies
    passed, reason = structural_net(_segs(60), 0.0, AutoSubNet())
    assert passed is True
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `python -m pytest tests/test_youtube_autosub.py -k structural_net -v`
Expected: FAIL with `cannot import name 'structural_net'`

- [ ] **Step 11: Implement `structural_net`**

Append to `harvest/providers/youtube_autosub.py`:

```python
def structural_net(
    segments: list[Segment], duration_s: float, net: AutoSubNet
) -> tuple[bool, str]:
    """Language-agnostic pass/fail on an auto-caption candidate. Returns (passed, reason). Any single
    check failing -> reject (caller falls back to Whisper). Coverage and cps are skipped when the
    duration is unknown/zero (presence still applies)."""
    n = len(segments)
    if n < net.min_cues:
        return False, f"only {n} cues (< {net.min_cues})"

    if duration_s and duration_s > 0:
        last_end = max(s.end for s in segments)
        ratio = last_end / duration_s
        if not (net.coverage_min <= ratio <= net.coverage_max):
            return False, (
                f"coverage {ratio:.2f} outside {net.coverage_min}-{net.coverage_max} "
                f"(last cue {last_end:.0f}s vs {duration_s:.0f}s)"
            )
        chars = sum(len(s.text) for s in segments)
        cps = chars / duration_s
        if cps < net.cps_min:
            return False, f"chars-per-second {cps:.2f} < {net.cps_min} (near-empty/music track)"

    return True, "structural net: passed"
```

- [ ] **Step 12: Run the whole module's tests to verify they pass**

Run: `python -m pytest tests/test_youtube_autosub.py -v`
Expected: PASS (all 15 tests)

- [ ] **Step 13: Commit**

```bash
git add harvest/providers/youtube_autosub.py tests/test_youtube_autosub.py
git commit -m "feat(youtube): auto-caption key-pick, SRT cleaning, structural net helpers"
```

---

### Task 3: Wire `fetch_subtitle` three-tier decision

**Files:**
- Modify: `harvest/providers/youtube.py:124-145` (the `fetch_subtitle` method)
- Test: `tests/test_providers_youtube.py`

**Interfaces:**
- Consumes: `pick_auto_key`, `clean_srt_segments`, `structural_net` from `harvest.providers.youtube_autosub` (Task 2); `settings.youtube_auto` (Task 1); `SubtitleOutcome` (existing, `harvest/providers/base.py`); `meta.duration_s` (existing on `SourceMetadata`).
- Produces: `fetch_subtitle(...)` now returns, in order: an accepted `human-sub` `SubtitleOutcome`; else an accepted `auto-sub` `SubtitleOutcome`; else a **rejected** `SubtitleOutcome(accepted=False, source_reason="auto-sub rejected (…)")` when an auto track existed but failed the net; else `None` (no usable track). Rejected and `None` both route to Whisper in `cli.decide_transcript`.

- [ ] **Step 1: Update the stale "never consults" test and add auto-path tests**

In `tests/test_providers_youtube.py`, DELETE `test_fetch_subtitle_never_consults_automatic_captions` (lines ~138-145) and `test_fetch_subtitle_none_when_known_lang_has_no_exact_track` (lines ~129-135) — both encode the old skip-auto behavior. Replace them with:

```python
def _auto_info(language, automatic_captions, *, subtitles=None, duration=300):
    # Minimal inline info dict for the auto-caption path (fixtures are vtt-only, no -orig/srt).
    return {
        "id": "vvvvvvvvvvv",
        "duration": duration,
        "language": language,
        "subtitles": subtitles or {},
        "automatic_captions": automatic_captions,
    }


_AUTO_SRT = (
    "1\n00:00:15,000 --> 00:04:58,000\n>> Yeah, we're good.\n\n"
    "2\n00:04:58,000 --> 00:04:59,000\nOkay, folks, that is a wrap on the whole thing.\n"
)


def test_fetch_subtitle_uses_auto_caption_when_no_human_track():
    # language en, no human sub, en-orig auto track present -> auto-sub accepted.
    p = YouTubeProvider()
    info = _auto_info("en", {"en-orig": [{"ext": "srt", "url": "u"}]})
    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: _AUTO_SRT)
    assert got is not None and got.accepted is True
    assert got.source == "auto-sub" and got.language == "en"
    assert "auto-caption" in got.source_reason and got.quality_gate is None


def test_fetch_subtitle_human_track_wins_over_auto():
    # Both a human en track and an en-orig auto track exist -> human-sub, auto never fetched.
    p = YouTubeProvider()
    info = _auto_info("en", {"en-orig": [{"ext": "srt", "url": "u"}]},
                      subtitles={"en": [{"ext": "vtt", "url": "h"}]})

    def _fetch(url, settings):
        assert url == "h", "auto track must not be fetched when a human track exists"
        return "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi\n"

    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info), info=info, fetch_url=_fetch)
    assert got.accepted and got.source == "human-sub" and got.language == "en"


def test_fetch_subtitle_unknown_lang_uses_sole_orig_auto():
    # language None, sole en-orig auto track -> auto-sub via the -orig detection branch.
    p = YouTubeProvider()
    info = _auto_info(None, {"en-orig": [{"ext": "srt", "url": "u"}], "es": [{"ext": "srt", "url": "x"}]})
    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: _AUTO_SRT)
    assert got.accepted and got.source == "auto-sub" and got.language == "en"


def test_fetch_subtitle_rejects_auto_failing_the_net():
    # A single-cue truncated auto track fails presence/coverage -> rejected outcome (-> Whisper).
    p = YouTubeProvider()
    info = _auto_info("en", {"en-orig": [{"ext": "srt", "url": "u"}]}, duration=6000)
    tiny = "1\n00:00:00,000 --> 00:00:02,000\nHello.\n"
    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: tiny)
    assert got is not None and got.accepted is False
    assert "auto-sub rejected" in got.source_reason


def test_fetch_subtitle_none_when_no_human_and_no_auto():
    # Known lang, but neither a human track nor any auto track -> None (-> Whisper).
    p = YouTubeProvider()
    info = _auto_info("ko", {})
    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: "")
    assert got is None
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `python -m pytest tests/test_providers_youtube.py -k "auto or human_track_wins or no_human_and_no_auto" -v`
Expected: FAIL — the current `fetch_subtitle` returns `None` for the no-human cases (no auto path yet), so the `auto-sub` assertions fail.

- [ ] **Step 3: Rewrite `fetch_subtitle` to the three-tier decision**

In `harvest/providers/youtube.py`, add the import near the top (with the other `from ..` imports):

```python
from .youtube_autosub import clean_srt_segments, pick_auto_key, structural_net
```

Replace the entire `fetch_subtitle` method (currently lines ~124-145) with:

```python
    def fetch_subtitle(
        self, canonical, settings, meta, *, pinned_lang=None, info=None, fetch_url=None,
    ) -> SubtitleOutcome | None:
        # Tier order: human-sub (exact key) > auto-sub (original-audio ASR, gated) > Whisper.
        if info is None:
            info = self._extract_info(canonical, settings)
        fetch = fetch_url or self._fetch_url
        target = self._target_lang(info, pinned_lang)

        # Tier 1 — human captions on an exact original-language key. automatic_captions excluded here.
        if target is not None:
            tracks = (info.get("subtitles") or {}).get(target)
            if tracks:
                vtt = next((t for t in tracks if t.get("ext") == "vtt"), None) or tracks[0]
                segments = parse_vtt(fetch(vtt["url"], settings))
                if segments:
                    return SubtitleOutcome(
                        accepted=True, source="human-sub",
                        source_reason=f"human-sub (exact-key match: {target})",
                        language=target, segments=segments, quality_gate=None,
                    )

        # Tier 2 — original-language auto-caption, structural net decides accept vs Whisper.
        auto = info.get("automatic_captions") or {}
        key = pick_auto_key(auto, target)
        if key is None:
            return None                                     # no usable original auto track -> Whisper
        srt = next((t for t in (auto.get(key) or []) if t.get("ext") == "srt"), None)
        if srt is None:
            return None                                     # no de-rolled srt to parse -> Whisper
        segments = clean_srt_segments(fetch(srt["url"], settings))
        lang = key[:-5] if key.endswith("-orig") else key   # "en-orig" -> "en"
        passed, reason = structural_net(segments, float(meta.duration_s or 0), settings.youtube_auto)
        if not passed:
            return SubtitleOutcome(
                accepted=False, source=None,
                source_reason=f"auto-sub rejected ({reason})",
                language=None, segments=[],
            )
        return SubtitleOutcome(
            accepted=True, source="auto-sub",
            source_reason=f"auto-sub (youtube auto-caption: {key})",
            language=lang, segments=segments, quality_gate=None,
        )
```

Also update the module docstring at the top of `harvest/providers/youtube.py` (lines 1-5) to match the new behavior:

```python
"""YouTubeProvider (SPEC §6): native yt-dlp metadata + tiered caption reuse.

Transcript tier order: human-sub > auto-sub > whisper. target_lang = pinned --lang, else
info["language"], else None. Human `subtitles[target]` on an exact key -> human-sub. Else the
original-audio auto-caption (key `target-orig`/`target`, or the sole `*-orig` when target is unknown),
fetched as de-rolled SRT and accepted only if it clears the structural net (harvest/providers/
youtube_autosub.py). Anything else -> Whisper. --force-whisper (handled in cli) skips all of this."""
```

- [ ] **Step 4: Run the full YouTube provider suite to verify it passes**

Run: `python -m pytest tests/test_providers_youtube.py -v`
Expected: PASS — the new auto tests pass, and the retained tests still hold:
- `test_fetch_subtitle_reuses_exact_language_human_track` (human-sub still wins)
- `test_fetch_subtitle_none_when_language_unknown_goes_to_whisper` — `kJQP7kiw5Fk` has `automatic_captions == {}`, so `pick_auto_key({}, None)` is `None` → still `None`.
- `test_fetch_subtitle_pinned_lang_overrides_and_reuses_track` (human path via pinned `es`)

- [ ] **Step 5: Run the whole suite to confirm nothing else regressed**

Run: `python -m pytest -q -m "not live"`
Expected: PASS (all non-live tests)

- [ ] **Step 6: Commit**

```bash
git add harvest/providers/youtube.py tests/test_providers_youtube.py
git commit -m "feat(youtube): reuse original-language auto-captions with structural net"
```

---

### Task 4: Opt-in live smoke test for the auto-caption path

**Files:**
- Modify: `tests/test_live_youtube.py`
- Test: same file (the test IS the deliverable)

**Interfaces:**
- Consumes: `YouTubeProvider.fetch_subtitle`, `Settings`, real network (guarded by `@pytest.mark.live`).

- [ ] **Step 1: Add the live auto-caption assertion**

Append to `tests/test_live_youtube.py`:

```python
# A public lecture with NO human captions but an English auto-caption — exercises the auto-sub tier.
_LIVE_AUTO_URL = "https://www.youtube.com/watch?v=-QFHIoCo-Ko"


@pytest.mark.live
def test_live_youtube_auto_caption_accepted():
    p = YouTubeProvider()
    canonical = p.resolve(_LIVE_AUTO_URL)
    meta = p.fetch_metadata(canonical, Settings())
    got = p.fetch_subtitle(canonical, Settings(), meta)
    assert got is not None and got.accepted is True
    assert got.source == "auto-sub" and got.language == "en"
    # De-rolling worked: no rolling-duplicate explosion, no leftover <c> word-timing tags.
    assert len(got.segments) < meta.duration_s          # far fewer cues than seconds
    assert all("<c>" not in s.text for s in got.segments)
```

- [ ] **Step 2: Run the live test to verify it passes**

Run: `python -m pytest tests/test_live_youtube.py::test_live_youtube_auto_caption_accepted -v -m live`
Expected: PASS (requires network + yt-dlp reaching YouTube)

- [ ] **Step 3: Confirm the live marker excludes it from the default run**

Run: `python -m pytest tests/test_live_youtube.py -q -m "not live"`
Expected: no tests run (both live tests are deselected)

- [ ] **Step 4: Commit**

```bash
git add tests/test_live_youtube.py
git commit -m "test(youtube): opt-in live smoke test for auto-caption reuse"
```

---

## Self-Review

**Spec coverage** (SPEC.md §6 YouTube + §7):
- `human-sub > auto-sub > whisper` ordering → Task 3.
- Target resolution (`--lang` / `info["language"]` / unknown) → reuses existing `_target_lang`, exercised in Task 3.
- Known-`L` prefer `L-orig` then `L` → `pick_auto_key` (Task 2), wired Task 3.
- Unknown → sole `*-orig`, else Whisper → `pick_auto_key` (Task 2), tests in Tasks 2 & 3.
- `srt` acquisition + `parse_srt` + strip `>>`, keep `[music]` → `clean_srt_segments` (Task 2).
- Structural net (presence, coverage 0.70–1.10, cps floor), config thresholds → Task 1 + `structural_net` (Task 2), wired Task 3.
- Net failure and no-track both → Whisper → Task 3 (rejected outcome / `None`), consumed unchanged by `cli.decide_transcript`.
- `--force-whisper` override → already short-circuits in `cli.decide_transcript` before `fetch_subtitle`; no change (noted in Global Constraints).
- Provenance `auto-sub`, `quality_gate=None`, authority order unchanged → Task 3; PROTOCOL/SPEC docs already updated in the consolidate-docs stage.
- Deliberate limitations (no language-ID; `--lang` to non-original may yield MT) → documented in SPEC §6; no code owed.
- §7 fixtures: unit tests build info inline (fixtures are vtt-only, no `-orig`/`srt`); live smoke covers the real path (Task 4). No fixture regeneration required.

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code step shows complete code; every run step shows an exact command + expected result.

**Type consistency:** `pick_auto_key`/`clean_srt_segments`/`structural_net` signatures are identical between Task 2 (definition) and Task 3 (call site). `AutoSubNet` field names (`min_cues`, `coverage_min`, `coverage_max`, `cps_min`) are identical across Tasks 1, 2, 3. `SubtitleOutcome` construction matches the existing dataclass in `harvest/providers/base.py` (`accepted`, `source`, `source_reason`, `language`, `segments`, `quality_gate`). `meta.duration_s` matches `SourceMetadata`.
