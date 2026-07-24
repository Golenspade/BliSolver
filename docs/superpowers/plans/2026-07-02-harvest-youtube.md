# harvest — YouTube Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename `bili_tool`→`harvest`, generalize the bundle schema to the PROTOCOL 1.0 multi-source contract, introduce a per-source `Provider` seam, and add a YouTube provider — so `harvest ingest/probe` works for both bilibili.com and youtube.com.

**Architecture:** A `Provider` protocol (selected by URL) owns *only* platform-specific acquisition and produces a normalized `SourceMetadata`; everything downstream (`merge`, `probe`, transcript decision) reads normalized outputs and never branches on platform. The existing, tested bilibili logic is wrapped behind the interface (adapter, not rewrite); YouTube is native yt-dlp with an exact-key caption rule.

**Tech Stack:** Python 3.11, pydantic v2, yt-dlp (Python API, >=2026.6.9), faster-whisper (`large-v3`, CUDA), pytest >=8. Run tests with `./.venv/Scripts/python.exe -m pytest -q`.

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from SPEC.md / PROTOCOL.md.

- **Package name is `harvest`.** After Task 1 there is no `bili_tool` module and no `bili-tool` string in code, `pyproject.toml`, or test imports. Console script: `harvest = "harvest.cli:main"`.
- **`SCHEMA_VERSION = "1.0"`** (a fresh contract, not a bili-tool patch). Never re-introduce `"1.1"`.
- **`platform` in `{"bilibili.com", "bilibili.tv", "youtube.com"}`** — `bilibili.tv` stays a resolve-only, probe/ingest-unsupported placeholder (deferred).
- **`uploader_id: str | None`** everywhere. The integer `uploader_mid` field is **removed** from `ProbeResult` and `Bundle`. bilibili emits `uploader_id=str(mid)`; YouTube emits `info["channel_id"]` (`UC...`).
- **Provenance `TranscriptSource` in `{"human-sub", "auto-sub", "whisper"}`** (`"ai-zh"` is renamed to `"auto-sub"`). Authority order documented for Atlas: **`human-sub` > `whisper` > `auto-sub`**. Language is a separate axis on `Transcript.language`.
- **`published_at` is per-source tz:** bilibili `+08:00` (CST); YouTube `Z` (UTC).
- **YouTube caption rule is EXACT-key-match only** (SPEC §6): `target_lang = --lang if pinned else info["language"] if truthy else None`; `None -> Whisper`; `subtitles[target_lang]` exists -> reuse (`human-sub`, language=target_lang); else Whisper. **Never consult `automatic_captions`. No fuzzy/primary-subtag match. No quality gate on the YouTube path.**
- **Tests are offline by default.** Inject openers / use trimmed fixtures; never hit the network in the default suite. Exactly one opt-in `@pytest.mark.live` smoke test, excluded from the default run.
- **Never commit real secrets** (SESSDATA, cookies, tokens).

---

## Task 0: File structure (target)

This is the map the tasks below build toward — not a step to execute, but the decomposition contract.

```
harvest/
├── cli.py            # verb dispatch; select_provider(url); --lang; per-part orchestration
├── config.py         # Settings; HARVEST_* env; per-provider auth
├── schema.py         # pydantic Bundle/ProbeResult/Transcript (PROTOCOL 1.0)
├── providers/
│   ├── __init__.py
│   ├── base.py       # Provider protocol, SourceMetadata, Canonical, register()/select_provider()
│   ├── bilibili.py   # BilibiliProvider — adapter over resolve/player_api/subtitles bilibili path
│   └── youtube.py    # YouTubeProvider — native yt-dlp metadata + exact-key captions
├── resolve.py        # bilibili URL resolution (kept; Canonical re-exported from base)
├── player_api.py     # bilibili player-API metadata/subtitles (kept; used by bilibili provider)
├── subtitles.py      # shared yt-dlp subtitle plumbing + parsers (parse_bcc/parse_srt/parse_vtt)
├── quality.py        # bilibili quality gate (bilibili-only)
├── transcribe.py     # faster-whisper (shared, lang-aware)
├── probe.py · merge.py · frames.py · vision.py · cache.py · parts.py
```

**Decision — `Canonical` lives in `providers/base.py`.** It is the shared vocabulary of the provider seam (every `Provider` method consumes/produces it). `resolve.py` will import it from `base` and re-export it for backward compatibility, so existing `from harvest.resolve import Canonical` call sites keep working with zero churn. Justification: putting the seam's core type in the seam's module avoids `resolve.py` (a bilibili-specific module) owning a now-cross-platform type, while the re-export keeps the Task-1 rename mechanical.

---

## Task 1: Rename package `bili_tool` -> `harvest`

Mechanical, no behavior change. The clean foundation everything else builds on. This is one reviewable unit because it must land atomically — a half-renamed tree does not import.

**Files:**
- Rename: `bili_tool/` -> `harvest/` (via `git mv`)
- Modify: every `.py` under `harvest/` and `tests/` (import lines + `prog=` string)
- Modify: `pyproject.toml` (`[project].name`, `[project.scripts]`, `[tool.hatch.build.targets.wheel].packages`)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: importable package `harvest` with identical public API; console entry `harvest.cli:main`. All later tasks import from `harvest.*`.

- [ ] **Step 1: Move the package directory (preserves git history)**

```bash
git mv bili_tool harvest
```

- [ ] **Step 2: Run the suite to see it fail (imports now broken)**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: collection errors / `ModuleNotFoundError: No module named 'bili_tool'` — this is the failing state we fix next.

- [ ] **Step 3: Confirm the package has no absolute self-imports**

Package modules already use relative imports (`from .config import ...`, `from . import __version__`), so they need no change. Verify:
Run: `grep -rn "bili_tool" harvest/` — expected: no output. Fix any absolute `bili_tool` reference if one appears.

- [ ] **Step 4: Rewrite the CLI `prog` string**

In `harvest/cli.py`, `parse_args`:
old: `p = argparse.ArgumentParser(prog="bili-tool", description=__doc__)`
new: `p = argparse.ArgumentParser(prog="harvest", description=__doc__)`

- [ ] **Step 5: Rewrite every test import**

In every file under `tests/`, replace `bili_tool` with `harvest` on import lines (e.g. `from bili_tool.config import Settings` -> `from harvest.config import Settings`; `from bili_tool import cli` -> `from harvest import cli`; also inside function bodies, e.g. `from bili_tool.schema import ...`). The `from tests.test_player_api import _FakeOpener, _view_url` cross-import in `tests/test_probe.py` is unaffected.

- [ ] **Step 6: Update `pyproject.toml`**

```toml
[project]
name = "harvest"

[project.scripts]
harvest = "harvest.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["harvest"]
```

- [ ] **Step 7: Run the full suite green**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (95 tests). Then:
Run: `grep -rn "bili_tool" harvest/ tests/ pyproject.toml` — expected: no output.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: rename package bili_tool -> harvest

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

> Note: the git remote still points at `bili-tool.git`; leave the remote as-is (out of scope).

---

## Task 2: Generalize the schema to PROTOCOL 1.0

Turn `schema.py` into the multi-source 1.0 contract and fix every producer/consumer that breaks, keeping the suite green. One unit: the field changes and their call-site fixups are inseparable (the suite is red until all land).

**Files:**
- Modify: `harvest/schema.py`
- Modify: `harvest/player_api.py` (`part_segments` language-key comment)
- Modify: `harvest/subtitles.py` (`_pick_track`/`_acquire` provenance labels, `SubtitleResult` comment)
- Modify: `harvest/probe.py` (`uploader_mid=` -> `uploader_id=`)
- Modify: `harvest/merge.py` (`uploader_mid` build + markdown header)
- Test: `tests/test_probe.py`, `tests/test_cli.py`, `tests/test_merge.py`, `tests/test_subtitles.py`

**Interfaces:**
- Consumes: `harvest` package (Task 1).
- Produces:
  - `SCHEMA_VERSION = "1.0"`
  - `Platform = Literal["bilibili.com", "bilibili.tv", "youtube.com"]`
  - `TranscriptSource = Literal["human-sub", "auto-sub", "whisper"]`
  - `ProbeResult` / `Bundle` with `uploader_id: str | None` (no `uploader_mid`)
  - `Transcript.language: str | None = None`

- [ ] **Step 1: Write failing schema-contract tests**

Add to `tests/test_probe.py`:

```python
def test_schema_version_is_1_0():
    from harvest.schema import SCHEMA_VERSION
    assert SCHEMA_VERSION == "1.0"


def test_probe_result_uses_uploader_id_string():
    from harvest.schema import ProbeResult
    r = ProbeResult(platform="youtube.com", id="x", uploader_id="UCabc", parts=1)
    assert r.uploader_id == "UCabc"
    assert not hasattr(r, "uploader_mid")
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_probe.py::test_schema_version_is_1_0 tests/test_probe.py::test_probe_result_uses_uploader_id_string -q`
Expected: FAIL (`SCHEMA_VERSION == "1.1"`; `youtube.com` not a valid Platform; `uploader_id` unknown field).

- [ ] **Step 3: Edit `harvest/schema.py`**

```python
SCHEMA_VERSION = "1.0"

Platform = Literal["bilibili.com", "bilibili.tv", "youtube.com"]
TranscriptSource = Literal["human-sub", "auto-sub", "whisper"]
```

In `Transcript`, change `language: str = "zh"` to `language: str | None = None`.
In `ProbeResult`, replace `uploader_mid: int | None = None` with `uploader_id: str | None = None`.
In `Bundle`, replace `uploader_mid: int | None = None` with `uploader_id: str | None = None`.
Update the module docstring's `1.1 added ...` note to describe the fresh 1.0 contract.

- [ ] **Step 4: Fix bilibili provenance labels in `subtitles.py`**

Keep the *track/language key* `"ai-zh"` in `_ZH_KEYS` (bilibili's wire key), but change every **provenance label**:
- In `_pick_track`, the auto branch: `return "auto-sub", key, auto[key]` (was `"ai-zh"`).
- In `_acquire`, the player-API fallback: `return "auto-sub", lang, segments` (was `"ai-zh"`).
- `SubtitleResult.source` comment `# "human-sub" | "ai-zh" | None` -> `# "human-sub" | "auto-sub" | None`.

In `harvest/player_api.py`, `part_segments` returns `(pick.get("lan") or "ai-zh"), segments`. The first element is the **language key**, not provenance — leave `"ai-zh"` and add a comment: `# language key (provenance set to "auto-sub" by subtitles._acquire)`.

- [ ] **Step 5: Fix `probe.py`**

`uploader_mid=view.owner_mid` -> `uploader_id=str(view.owner_mid) if view.owner_mid is not None else None`.

- [ ] **Step 6: Fix `merge.py` + test expectations**

`build_bundle`: `uploader_mid = view.owner_mid if view else None` -> `uploader_id = str(view.owner_mid) if (view and view.owner_mid is not None) else None`; pass `uploader_id=uploader_id` to `Bundle(...)`.
`render_markdown`: replace the `uploader_mid` header line with `f"uploader_id: {bundle.uploader_id or ''}",`.
Update `tests/test_probe.py`, `tests/test_cli.py` (`fake_probe`), `tests/test_merge.py`: every `uploader_mid=<int>` becomes `uploader_id="<str>"`; `result.uploader_mid == 7` becomes `result.uploader_id == "7"`. Grep `tests/` for any `"ai-zh"` **source** assertion (none expected).

- [ ] **Step 7: Run the full suite green**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: PASS.
Run: `grep -rn "uploader_mid" harvest/ tests/` — expected: no output.
Run: `grep -rn '"ai-zh"' harvest/` — expected: only the language-key occurrences in `_ZH_KEYS` and `player_api.part_segments`, none as a provenance label.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: generalize bundle schema to PROTOCOL 1.0 (uploader_id, auto-sub, youtube.com)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Provider seam — protocol, SourceMetadata, registry

Pure definitions plus registry dispatch. No provider registers yet (Tasks 4/5 do). Split from the providers so the registry contract is independently reviewable with stubs.

**Files:**
- Create: `harvest/providers/__init__.py`
- Create: `harvest/providers/base.py`
- Modify: `harvest/resolve.py` (import + re-export `Canonical` from `base`)
- Test: `tests/test_providers_base.py`

**Interfaces:**
- Consumes: `harvest.schema.Platform`, `harvest.schema.Segment`.
- Produces:
  - `@dataclass(frozen=True) class Canonical`: `platform: Platform`, `id: str`, `part: int`, `url: str`.
  - `@dataclass class SourceMetadata`: `platform`, `id`, `title`, `uploader`, `uploader_id`, `description`, `duration_s`, `published_at`, `parts`, `part_durations_s`.
  - `@dataclass class SubtitleOutcome`: `accepted: bool`, `source: str|None`, `source_reason: str`, `language: str|None`, `segments: list[Segment]`, `quality_gate: QualityGate|None=None`. A rejected outcome (accepted=False) still carries reason + gate so the Whisper fallback records why.
  - `class Provider(Protocol)`: `matches(url)->bool`, `resolve(url)->Canonical`, `auth_opts(settings)->dict`, `fetch_metadata(canonical, settings)->SourceMetadata`, `enumerate_parts(canonical, settings)->int`, `fetch_subtitle(canonical, settings, meta, *, pinned_lang=None)->SubtitleOutcome | None` (None = no subtitle track → plain Whisper).
  - `register(provider)->None`, `select_provider(url)->Provider` (raises `ValueError` if none match).

- [ ] **Step 1: Write failing registry tests**

`tests/test_providers_base.py`:

```python
import pytest

from harvest.providers import base
from harvest.providers.base import Canonical, SourceMetadata, register, select_provider


class _StubA:
    def matches(self, url): return "a.example" in url
    def resolve(self, url): return Canonical("bilibili.com", "A", 1, url)
    def auth_opts(self, settings): return {}
    def fetch_metadata(self, canonical, settings): ...
    def enumerate_parts(self, canonical, settings): return 1
    def fetch_subtitle(self, canonical, settings, meta, *, pinned_lang=None): return None


class _StubB(_StubA):
    def matches(self, url): return "b.example" in url


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    monkeypatch.setattr(base, "_REGISTRY", [])


def test_select_provider_dispatches_by_matches():
    register(_StubA()); register(_StubB())
    assert isinstance(select_provider("http://a.example/x"), _StubA)
    assert isinstance(select_provider("http://b.example/x"), _StubB)


def test_select_provider_raises_when_none_match():
    register(_StubA())
    with pytest.raises(ValueError):
        select_provider("http://c.example/x")


def test_source_metadata_holds_normalized_fields():
    m = SourceMetadata(
        platform="youtube.com", id="v", title="T", uploader="U", uploader_id="UCx",
        description="d", duration_s=10, published_at="2024-01-01T00:00:00Z",
        parts=1, part_durations_s=[10],
    )
    assert m.uploader_id == "UCx" and m.parts == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_providers_base.py -q`
Expected: FAIL (`No module named 'harvest.providers'`).

- [ ] **Step 3: Create `harvest/providers/__init__.py`**

```python
"""Per-source provider seam (SPEC §4.1)."""
```

- [ ] **Step 4: Create `harvest/providers/base.py`**

```python
"""Provider seam (SPEC §4.1): URL-selected, platform-specific acquisition only.

A Provider is selected by URL and owns resolve/auth/metadata/parts/subtitle for one source.
Everything downstream (merge, probe, transcript decision) reads normalized SourceMetadata and
never branches on platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..schema import Platform, QualityGate, Segment


@dataclass(frozen=True)
class Canonical:
    """{platform, id, part, url}: the atomic, cached identity unit (SPEC §5)."""

    platform: Platform
    id: str
    part: int
    url: str


@dataclass
class SourceMetadata:
    """Normalized per-video metadata every provider produces. Replaces ViewData-vs-info
    branching: merge/probe read only this shape."""

    platform: Platform
    id: str
    title: str | None
    uploader: str | None
    uploader_id: str | None
    description: str | None
    duration_s: int | None
    published_at: str | None            # ISO 8601, per-source tz (SPEC §8)
    parts: int
    part_durations_s: list[int | None]


@dataclass
class SubtitleOutcome:
    """Result of a provider's subtitle acquisition + trust decision. A *rejected* outcome still
    carries its reason (and, for bilibili, the failed quality_gate) so the Whisper fallback can
    record WHY it fell back in the bundle header. `fetch_subtitle` returning None means "no
    subtitle track at all" -> plain Whisper, no gate."""

    accepted: bool                       # True -> use segments; False -> fall back to Whisper
    source: str | None                   # "human-sub" | "auto-sub" when accepted, else None
    source_reason: str                   # why accepted OR why rejected (flows into whisper reason)
    language: str | None
    segments: list[Segment]              # empty when rejected
    quality_gate: QualityGate | None = None


@runtime_checkable
class Provider(Protocol):
    def matches(self, url: str) -> bool: ...
    def resolve(self, url: str) -> Canonical: ...
    def auth_opts(self, settings) -> dict: ...
    def fetch_metadata(self, canonical: Canonical, settings) -> SourceMetadata: ...
    def enumerate_parts(self, canonical: Canonical, settings) -> int: ...
    def fetch_subtitle(
        self, canonical: Canonical, settings, meta: SourceMetadata,
        *, pinned_lang: str | None = None,
    ) -> SubtitleOutcome | None: ...


_REGISTRY: list[Provider] = []


def register(provider: Provider) -> None:
    _REGISTRY.append(provider)


def select_provider(url: str) -> Provider:
    for p in _REGISTRY:
        if p.matches(url):
            return p
    raise ValueError(f"no provider matches URL: {url}")
```

- [ ] **Step 5: Re-export `Canonical` from `resolve.py`**

In `harvest/resolve.py`, delete the local `@dataclass(frozen=True) class Canonical` definition. Add:

```python
from .providers.base import Canonical  # re-exported for backward-compatible import paths
```

Keep `from .schema import Platform` (used by the `platform: Platform` local annotation inside `resolve()`). Verify `from harvest.resolve import Canonical` still works.

> Import-cycle check: `providers/base` imports only `..schema` (no `resolve`); `resolve` imports `providers.base`. No cycle.

- [ ] **Step 6: Run tests green**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_providers_base.py tests/test_resolve.py -q`
Expected: PASS. Then full suite: `./.venv/Scripts/python.exe -m pytest -q` -> PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add provider seam (Provider protocol, SourceMetadata, registry)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: BilibiliProvider — adapter over existing bilibili logic

Wrap the existing, tested bilibili code behind the `Provider` interface. Move/adapter, not a rewrite: `resolve.py`, `player_api.py`, and `subtitles.py`'s bilibili path stay and are called by the provider. One unit: the adapter is only meaningful once all methods delegate correctly, and existing bilibili tests must stay green.

**Files:**
- Create: `harvest/providers/bilibili.py`
- Test: `tests/test_providers_bilibili.py`

**Interfaces:**
- Consumes: `resolve()` from `harvest.resolve`; `fetch_view`, `ViewError`, `published_at_iso` from `harvest.player_api`; `part_url` from `harvest.parts`; `extract_info`, `fetch_subtitle_segments`, `ydl_opts`, `probe` (as `subtitle_probe`) from `harvest.subtitles`; `evaluate`, `describe_failure` from `harvest.quality`; `Canonical`, `SourceMetadata`, `SubtitleOutcome`, `register` from `harvest.providers.base`.
- Produces: `class BilibiliProvider` implementing `Provider`; `fetch_subtitle` returns a `SubtitleOutcome` after running the FULL bilibili trust decision (probe tier-1/tier-2 #6357 + quality gate) internally; import registers `BilibiliProvider()`.

- [ ] **Step 1: Write failing provider tests (offline, opener-injected)**

`tests/test_providers_bilibili.py`:

```python
from harvest.config import Settings
from harvest.providers.base import Canonical, SourceMetadata
from harvest.providers.bilibili import BilibiliProvider
from tests.test_player_api import _FakeOpener, _view_url


def _canonical(part=1):
    return Canonical("bilibili.com", "BV1", part, f"https://b/video/BV1?p={part}")


def test_matches_bilibili_com_and_short_link():
    p = BilibiliProvider()
    assert p.matches("https://www.bilibili.com/video/BV1")
    assert p.matches("https://b23.tv/abc")
    assert not p.matches("https://www.youtube.com/watch?v=x")


def test_resolve_delegates_to_resolve():
    p = BilibiliProvider()
    c = p.resolve("https://www.bilibili.com/video/BV1xx411x7xx?p=2")
    assert c.platform == "bilibili.com" and c.id == "BV1xx411x7xx" and c.part == 2


def test_fetch_metadata_maps_view_to_source_metadata():
    p = BilibiliProvider()
    canonical = _canonical()
    payload = {"code": 0, "data": {
        "aid": 42, "cid": 100, "title": "My Video", "desc": "D", "duration": 600,
        "pubdate": 1719561600, "owner": {"mid": 7, "name": "Up"},
        "pages": [{"page": 1, "cid": 100, "part": "P1", "duration": 300},
                  {"page": 2, "cid": 200, "part": "P2", "duration": 300}]}}
    opener = _FakeOpener({_view_url(canonical): payload})
    meta = p.fetch_metadata(canonical, Settings(), opener=opener)
    assert isinstance(meta, SourceMetadata)
    assert meta.platform == "bilibili.com"
    assert meta.uploader == "Up"
    assert meta.uploader_id == "7"
    assert meta.published_at == "2024-06-28T16:00:00+08:00"
    assert meta.parts == 2
    assert meta.part_durations_s == [300, 300]


def test_enumerate_parts_counts_view_pages():
    p = BilibiliProvider()
    canonical = _canonical()
    payload = {"code": 0, "data": {"aid": 1, "cid": 5, "title": "S", "desc": "",
        "duration": 120, "owner": {"mid": 1, "name": "U"}, "pages": []}}
    opener = _FakeOpener({_view_url(canonical): payload})
    assert p.enumerate_parts(canonical, Settings(), opener=opener) == 1


# --- fetch_subtitle: the full trust decision, relocated from cli.decide_transcript ---
# Monkeypatch the provider's collaborators so these stay offline and pin the OUTCOME contract.

def test_fetch_subtitle_accepts_when_gate_passes(monkeypatch):
    from harvest.providers import bilibili as biliprov
    from harvest.schema import QualityGate, Segment
    from harvest.subtitles import SubtitleResult

    p = BilibiliProvider()
    segs = [Segment(start=0.0, end=1.0, text="你好")]
    monkeypatch.setattr(biliprov, "extract_info", lambda url, s: {"duration": 100})
    monkeypatch.setattr(p, "_view", lambda c, s, **k: None)
    monkeypatch.setattr(biliprov, "subtitle_probe",
        lambda info, c, s, **k: SubtitleResult(True, "auto-sub", "ai-zh", segments=segs, reason="ok"))
    monkeypatch.setattr(biliprov, "evaluate",
        lambda seg, dur, q: QualityGate(passed=True, punct_density=1.0, dup_ratio=0.0, nonzh_ratio=0.0, cps=5.0))
    out = p.fetch_subtitle(_canonical(), Settings(), None)
    assert out.accepted is True and out.source == "auto-sub"
    assert out.language == "zh" and out.quality_gate.passed and out.segments == segs


def test_fetch_subtitle_rejects_when_gate_fails_and_carries_gate(monkeypatch):
    from harvest.providers import bilibili as biliprov
    from harvest.schema import QualityGate, Segment
    from harvest.subtitles import SubtitleResult

    p = BilibiliProvider()
    failed = QualityGate(passed=False, punct_density=0.0, dup_ratio=0.9, nonzh_ratio=0.0, cps=1.0)
    monkeypatch.setattr(biliprov, "extract_info", lambda url, s: {"duration": 100})
    monkeypatch.setattr(p, "_view", lambda c, s, **k: None)
    monkeypatch.setattr(biliprov, "subtitle_probe",
        lambda info, c, s, **k: SubtitleResult(True, "auto-sub", "ai-zh",
            segments=[Segment(start=0.0, end=1.0, text="x")], reason="ok"))
    monkeypatch.setattr(biliprov, "evaluate", lambda seg, dur, q: failed)
    out = p.fetch_subtitle(_canonical(), Settings(), None)
    assert out.accepted is False and out.source is None
    assert out.quality_gate is failed and "rejected" in out.source_reason


def test_fetch_subtitle_rejected_when_probe_not_found_no_gate(monkeypatch):
    from harvest.providers import bilibili as biliprov
    from harvest.subtitles import SubtitleResult

    p = BilibiliProvider()
    monkeypatch.setattr(biliprov, "extract_info", lambda url, s: {"duration": 100})
    monkeypatch.setattr(p, "_view", lambda c, s, **k: None)
    monkeypatch.setattr(biliprov, "subtitle_probe",
        lambda info, c, s, **k: SubtitleResult(False, None, None,
            reason="failed part-match assertion (#6357, identical to part 1)"))
    out = p.fetch_subtitle(_canonical(), Settings(), None)
    assert out.accepted is False and out.quality_gate is None
    assert "#6357" in out.source_reason
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_providers_bilibili.py -q`
Expected: FAIL (`No module named 'harvest.providers.bilibili'`).

- [ ] **Step 3: Implement `harvest/providers/bilibili.py`**

```python
"""BilibiliProvider: existing bilibili acquisition behind the Provider interface. Adapter.

Owns the FULL bilibili subtitle trust decision (probe tier-1 duration + tier-2 #6357, then the
quality gate), so the CLI never branches on platform (SPEC §4.1, §6). A move of the proven
cli.decide_transcript bilibili flow into the seam — not a rewrite."""

from __future__ import annotations

from urllib.parse import urlparse

from ..config import Settings
from ..parts import part_url
from ..player_api import ViewData, ViewError, fetch_view, published_at_iso
from ..quality import describe_failure, evaluate
from ..resolve import resolve as _resolve
from ..subtitles import extract_info, fetch_subtitle_segments, ydl_opts
from ..subtitles import probe as subtitle_probe
from .base import Canonical, SourceMetadata, SubtitleOutcome, register


class BilibiliProvider:
    def matches(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return host.endswith("bilibili.com") or host.endswith("bilibili.tv") or host.endswith("b23.tv")

    def resolve(self, url: str) -> Canonical:
        return _resolve(url)

    def auth_opts(self, settings: Settings) -> dict:
        return ydl_opts(settings)

    def _view(self, canonical, settings, *, opener=None) -> ViewData | None:
        try:
            return fetch_view(canonical, settings, opener=opener)
        except ViewError:
            return None

    def fetch_metadata(self, canonical, settings, *, opener=None) -> SourceMetadata:
        view = fetch_view(canonical, settings, opener=opener)
        return SourceMetadata(
            platform="bilibili.com",
            id=canonical.id,
            title=view.title,
            uploader=view.owner_name,
            uploader_id=str(view.owner_mid) if view.owner_mid is not None else None,
            description=view.desc,
            duration_s=view.duration,
            published_at=published_at_iso(view.pubdate),
            parts=max(len(view.pages), 1),
            part_durations_s=[pg.duration for pg in view.pages],
        )

    def enumerate_parts(self, canonical, settings, *, opener=None) -> int:
        view = self._view(canonical, settings, opener=opener)
        return max(len(view.pages), 1) if view else 1

    def _part1_segments(self, canonical, settings):
        """#6357 tier-2 input: part 1's subtitle, fetched only for part>1. Best-effort — a
        failure here just skips tier-2, never aborts (relocated from cli._part1_segments)."""
        if canonical.part <= 1:
            return None
        try:
            p1_url = part_url(canonical.url, 1)
            p1 = Canonical(canonical.platform, canonical.id, 1, p1_url)
            p1_info = extract_info(p1_url, settings)
            return fetch_subtitle_segments(p1_info, p1, settings)
        except Exception:  # noqa: BLE001 - tier-2 is an optional guard, never fatal
            return None

    def fetch_subtitle(self, canonical, settings, meta, *, pinned_lang=None, opener=None):
        """Full bilibili trust decision → SubtitleOutcome. `pinned_lang` is unused (bilibili is
        zh-only; --lang only affects the Whisper fallback language in the CLI). A rejected outcome
        still carries source_reason (+ the failed quality_gate) so the bundle records why."""
        info = extract_info(canonical.url, settings)
        view = self._view(canonical, settings, opener=opener)
        sub = subtitle_probe(
            info, canonical, settings,
            part1_segments=self._part1_segments(canonical, settings),
            view=view,
        )
        if not sub.found:
            return SubtitleOutcome(
                accepted=False, source=None,
                source_reason=f"no usable subtitle ({sub.reason})",
                language=None, segments=[],
            )
        gate = evaluate(sub.segments, float(info.get("duration") or 0), settings.quality)
        if gate.passed:
            return SubtitleOutcome(
                accepted=True, source=sub.source,
                source_reason=f"{sub.source} (quality-gate: passed)",
                language="zh", segments=sub.segments, quality_gate=gate,
            )
        return SubtitleOutcome(
            accepted=False, source=None,
            source_reason=f"subtitle rejected ({describe_failure(gate, settings.quality)})",
            language=None, segments=[], quality_gate=gate,
        )


register(BilibiliProvider())
```

> `*, opener=None` is an additive test seam; production calls omit it. `fetch_subtitle` reuses the
> exact proven flow (`subtitle_probe` → `evaluate`) that lived in `cli.decide_transcript` — moved
> here so the CLI is platform-agnostic. `sub.source` is already `"human-sub"`/`"auto-sub"` after
> Task 2's rename. **Trade-off (conscious):** `fetch_metadata` and `fetch_subtitle` each fetch the
> player-API view, so a bilibili part now does 2 lightweight no-media view calls (was 1). This is
> the honest cost of not leaking `ViewData` back through the seam into the CLI; the view call is a
> cheap metadata GET. Do not "optimize" it by threading `ViewData` through `SourceMetadata` — that
> re-couples the CLI to a bilibili type.

- [ ] **Step 4: Run provider tests green**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_providers_bilibili.py -q`
Expected: PASS.

- [ ] **Step 5: Run full suite green**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add BilibiliProvider adapter over existing bilibili acquisition

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `parse_vtt` WebVTT parser

Small, independently testable. Needed by Task 6. Lands beside `parse_bcc`/`parse_srt`.

**Files:**
- Modify: `harvest/subtitles.py`
- Test: `tests/test_subtitles.py`

**Interfaces:**
- Consumes: `harvest.schema.Segment`.
- Produces: `parse_vtt(text: str) -> list[Segment]` in `harvest.subtitles`.

- [ ] **Step 1: Write failing test**

Add to `tests/test_subtitles.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_subtitles.py -k parse_vtt -q`
Expected: FAIL (`cannot import name 'parse_vtt'`).

- [ ] **Step 3: Implement `parse_vtt` in `harvest/subtitles.py`**

Add near `parse_srt`. It reuses the existing `_SRT_TIME` regex — WebVTT `.`-separated millis are matched by its `[,.]` alternative. A cue block is the blank-line-delimited block that contains a timing line; header/`NOTE` blocks have no timing line and are skipped:

```python
def parse_vtt(text: str) -> list[Segment]:
    """WebVTT (YouTube timed-text). Blank-line-delimited cue blocks; a cue block has a timing
    line (optional cue-id line above it). WEBVTT header and NOTE blocks lack a timing line and
    are skipped. Text lines after the timing line are joined with spaces."""
    out: list[Segment] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        m = _SRT_TIME.search(block)
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        lines = block.split("\n")
        ti = next((i for i, ln in enumerate(lines) if "-->" in ln), 0)
        body = " ".join(ln.strip() for ln in lines[ti + 1:] if ln.strip())
        out.append(Segment(start=start, end=end, text=body))
    return out
```

- [ ] **Step 4: Run tests green**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_subtitles.py -k parse_vtt -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add parse_vtt WebVTT subtitle parser

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: YouTubeProvider

Native yt-dlp metadata + the exact-key caption rule (SPEC §6). All tests drive off the captured `tests/fixtures/youtube/*.info.json` fixtures + a mocked track fetch; the default suite never hits the network.

**Files:**
- Create: `harvest/providers/youtube.py`
- Test: `tests/test_providers_youtube.py`

**Interfaces:**
- Consumes: `Canonical`, `SourceMetadata`, `SubtitleOutcome`, `register` from `harvest.providers.base`; `parse_vtt`, `ydl_opts` from `harvest.subtitles`.
- Produces: `class YouTubeProvider` implementing `Provider`; import registers a singleton. Helpers: `_video_id(url)`, `_published_at(info)`, `_metadata_from_info(info)`, `_target_lang(info, pinned)`, `fetch_subtitle(..., *, pinned_lang=None, info=None, fetch_url=None) -> SubtitleOutcome | None` (accepted `human-sub` outcome on an exact-key hit, else `None`).

**Fixture facts (verified against `dQw4w9WgXcQ.info.json`):** `info` carries `id`, `title`, `description`, `duration`, `channel` (display name), `channel_id` (`UC...`), `timestamp` (epoch `1256453853`), `upload_date` (`20091025`), `language` (`"en"`), and `subtitles[lang] = [{"ext","name","url"}, ...]` with a `vtt` entry. yt-dlp's own `info["uploader_id"]` is the mutable `@handle` (`@RickAstleyYT`) — **do not use it**; use `channel_id`. Other fixtures: `kJQP7kiw5Fk` language `None` with `es`/`en-US-...` tracks; `9bZkp7q19f0` language `ko` with empty `subtitles`; `aqz-KE-bpKQ` language `None`, empty `subtitles`.

- [ ] **Step 1: Write failing metadata + id tests (fixture-driven)**

`tests/test_providers_youtube.py`:

```python
import io
import json
from pathlib import Path

from harvest.config import Settings
from harvest.providers.base import Canonical, SourceMetadata
from harvest.providers.youtube import YouTubeProvider

FIX = Path(__file__).parent / "fixtures" / "youtube"


def _info(name):
    return json.load(io.open(FIX / f"{name}.info.json", encoding="utf-8"))


def _canonical(vid="dQw4w9WgXcQ"):
    return Canonical("youtube.com", vid, 1, f"https://youtu.be/{vid}")


def test_matches_youtube_hosts():
    p = YouTubeProvider()
    assert p.matches("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert p.matches("https://youtu.be/dQw4w9WgXcQ")
    assert not p.matches("https://www.bilibili.com/video/BV1")


def test_resolve_extracts_11_char_id_part_always_1():
    p = YouTubeProvider()
    c = p.resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5s")
    assert c.platform == "youtube.com" and c.id == "dQw4w9WgXcQ" and c.part == 1
    assert p.resolve("https://youtu.be/dQw4w9WgXcQ").id == "dQw4w9WgXcQ"


def test_metadata_from_info_maps_fields_and_utc_published_at():
    p = YouTubeProvider()
    meta = p._metadata_from_info(_info("dQw4w9WgXcQ"))
    assert isinstance(meta, SourceMetadata)
    assert meta.platform == "youtube.com"
    assert meta.id == "dQw4w9WgXcQ"
    assert meta.uploader == "Rick Astley"
    assert meta.uploader_id == "UCuAXFkgsw1L7xaCfnd5JJOw"   # channel_id, NOT @handle
    assert meta.published_at == "2009-10-25T06:57:33Z"       # timestamp 1256453853 -> UTC ...Z
    assert meta.parts == 1
    assert meta.part_durations_s == [meta.duration_s]


def test_published_at_falls_back_to_upload_date_midnight_utc():
    p = YouTubeProvider()
    assert p._published_at({"id": "x", "upload_date": "20240628"}) == "2024-06-28T00:00:00Z"


def test_published_at_none_when_no_date_fields():
    assert YouTubeProvider()._published_at({"id": "x"}) is None
```

- [ ] **Step 2: Write failing caption-rule tests (fixture + mocked fetch)**

Append to `tests/test_providers_youtube.py`. `fetch_subtitle` takes an injectable `fetch_url` seam so the default suite never downloads:

```python
def _meta_for(info):
    return YouTubeProvider()._metadata_from_info(info)


def test_fetch_subtitle_reuses_exact_language_human_track():
    p = YouTubeProvider()
    info = _info("dQw4w9WgXcQ")  # language "en", subtitles has "en" with a vtt entry
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi\n"
    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: vtt)
    assert got is not None and got.accepted is True
    assert got.source == "human-sub" and got.language == "en"
    assert got.segments[0].text == "hi" and got.quality_gate is None


def test_fetch_subtitle_none_when_language_unknown_goes_to_whisper():
    # kJQP7kiw5Fk: language None though an es track exists -> unknown -> Whisper (None).
    p = YouTubeProvider()
    info = _info("kJQP7kiw5Fk")
    got = p.fetch_subtitle(_canonical("kJQP7kiw5Fk"), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: "")
    assert got is None


def test_fetch_subtitle_pinned_lang_overrides_and_reuses_track():
    # --lang es pins Despacito's es track despite info["language"] being None.
    p = YouTubeProvider()
    info = _info("kJQP7kiw5Fk")
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhola\n"
    got = p.fetch_subtitle(_canonical("kJQP7kiw5Fk"), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: vtt, pinned_lang="es")
    assert got.accepted is True and got.source == "human-sub"
    assert got.language == "es" and got.segments[0].text == "hola"


def test_fetch_subtitle_none_when_known_lang_has_no_exact_track():
    # 9bZkp7q19f0: language "ko" but subtitles empty -> no exact track -> Whisper (None).
    p = YouTubeProvider()
    info = _info("9bZkp7q19f0")
    got = p.fetch_subtitle(_canonical("9bZkp7q19f0"), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: "")
    assert got is None


def test_fetch_subtitle_never_consults_automatic_captions():
    # Rick Astley has automatic_captions["en"]; strip subtitles and confirm no fallback.
    p = YouTubeProvider()
    info = dict(_info("dQw4w9WgXcQ"))
    info["subtitles"] = {}
    got = p.fetch_subtitle(_canonical(), Settings(), _meta_for(info),
                           info=info, fetch_url=lambda url, settings: "SHOULD NOT BE CALLED")
    assert got is None
```

- [ ] **Step 3: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_providers_youtube.py -q`
Expected: FAIL (`No module named 'harvest.providers.youtube'`).

---
- [ ] **Step 4: Implement `harvest/providers/youtube.py`**

```python
"""YouTubeProvider (SPEC §6): native yt-dlp metadata + exact-key human-caption reuse.

Caption rule: target_lang = pinned --lang, else info["language"] if truthy, else None.
None -> Whisper. Known L with an exact subtitles[L] human track -> reuse (human-sub, language L).
Otherwise -> Whisper. automatic_captions is NEVER consulted. No quality gate."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import yt_dlp

from ..config import Settings
from ..subtitles import parse_vtt, ydl_opts
from .base import Canonical, SourceMetadata, SubtitleOutcome, register

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.endswith("youtu.be"):
        cand = parsed.path.lstrip("/").split("/")[0]
        return cand if _ID.match(cand) else None
    v = parse_qs(parsed.query).get("v", [""])[0]
    if _ID.match(v):
        return v
    segs = [s for s in parsed.path.split("/") if s]
    for i, s in enumerate(segs):
        if s in ("shorts", "embed", "v") and i + 1 < len(segs) and _ID.match(segs[i + 1]):
            return segs[i + 1]
    return None


class YouTubeProvider:
    def matches(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return host.endswith("youtube.com") or host.endswith("youtu.be")

    def resolve(self, url: str) -> Canonical:
        vid = _video_id(url)
        if not vid:
            raise ValueError(f"unrecognized YouTube video id in URL: {url}")
        return Canonical("youtube.com", vid, 1, f"https://www.youtube.com/watch?v={vid}")

    def auth_opts(self, settings: Settings) -> dict:
        # YouTube cookies are optional; a configured browser profile unlocks gated content.
        return ydl_opts(settings)

    def _published_at(self, info: dict) -> str | None:
        ts = info.get("timestamp")
        if ts:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ud = info.get("upload_date")
        if ud and len(str(ud)) == 8:
            ud = str(ud)
            return f"{ud[0:4]}-{ud[4:6]}-{ud[6:8]}T00:00:00Z"
        return None

    def _metadata_from_info(self, info: dict) -> SourceMetadata:
        dur = info.get("duration")
        dur_i = int(dur) if dur else None
        return SourceMetadata(
            platform="youtube.com",
            id=info.get("id"),
            title=info.get("title"),
            uploader=info.get("channel") or info.get("uploader"),
            uploader_id=info.get("channel_id"),   # UC..., NOT the mutable @handle
            description=info.get("description"),
            duration_s=dur_i,
            published_at=self._published_at(info),
            parts=1,
            part_durations_s=[dur_i],
        )

    def _extract_info(self, canonical: Canonical, settings: Settings) -> dict:
        with yt_dlp.YoutubeDL(ydl_opts(settings)) as ydl:
            return ydl.extract_info(canonical.url, download=False)

    def fetch_metadata(self, canonical, settings, *, info=None) -> SourceMetadata:
        info = info if info is not None else self._extract_info(canonical, settings)
        return self._metadata_from_info(info)

    def enumerate_parts(self, canonical, settings) -> int:
        return 1

    def _target_lang(self, info: dict, pinned: str | None) -> str | None:
        if pinned:
            return pinned
        return info.get("language") or None

    def _fetch_url(self, url: str, settings: Settings) -> str:
        with yt_dlp.YoutubeDL(ydl_opts(settings)) as ydl:
            return ydl.urlopen(url).read().decode("utf-8", "replace")

    def fetch_subtitle(
        self, canonical, settings, meta, *, pinned_lang=None, info=None, fetch_url=None,
    ) -> SubtitleOutcome | None:
        if info is None:
            info = self._extract_info(canonical, settings)
        target = self._target_lang(info, pinned_lang)
        if target is None:
            return None                                     # unknown language -> Whisper (no gate)
        tracks = (info.get("subtitles") or {}).get(target)  # exact key only; never automatic_captions
        if not tracks:
            return None                                     # no exact human track -> Whisper
        vtt = next((t for t in tracks if t.get("ext") == "vtt"), None) or tracks[0]
        fetch = fetch_url or self._fetch_url
        raw = fetch(vtt["url"], settings)
        segments = parse_vtt(raw)
        if not segments:
            return None
        return SubtitleOutcome(
            accepted=True, source="human-sub",
            source_reason=f"human-sub (exact-key match: {target})",
            language=target, segments=segments, quality_gate=None,
        )


register(YouTubeProvider())
```

- [ ] **Step 5: Run YouTube tests green**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_providers_youtube.py -q`
Expected: PASS.

- [ ] **Step 6: Run full suite green**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add YouTubeProvider with exact-key human-caption rule

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Whisper language param

Remove the hard-pinned `language="zh"`; thread a resolved language through so bilibili defaults to `zh`, YouTube to `None` (auto-detect), and `--lang` overrides both.

**Files:**
- Modify: `harvest/transcribe.py`
- Test: `tests/test_transcribe.py` (new; test the param-plumbing seam, not a real CUDA decode)

**Interfaces:**
- Produces: `transcribe(audio_path, *, robust=False, model=WHISPER_MODEL, lang: str | None = None) -> list[Segment]` — `lang=None` means faster-whisper auto-detect.

- [ ] **Step 1: Write failing test (param passed to the model)**

`transcribe` imports `faster_whisper` lazily inside the function, so inject a fake via `sys.modules`:

```python
import sys
import types
from pathlib import Path


def _install_fake_whisper(monkeypatch, recorder):
    class _Seg:
        def __init__(self, start, end, text):
            self.start, self.end, self.text = start, end, text

    class _FakeModel:
        def __init__(self, *a, **k): ...
        def transcribe(self, audio, **kwargs):
            recorder["language"] = kwargs.get("language")
            recorder["condition_on_previous_text"] = kwargs.get("condition_on_previous_text")
            return [_Seg(0.0, 1.0, " hi ")], None

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = _FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)


def test_transcribe_defaults_language_to_none(monkeypatch):
    from harvest import transcribe as T
    monkeypatch.setattr(T, "_register_cuda_dlls", lambda: None)
    rec = {}
    _install_fake_whisper(monkeypatch, rec)
    segs = T.transcribe(Path("x.m4a"))
    assert rec["language"] is None
    assert segs[0].text == "hi"


def test_transcribe_threads_explicit_lang(monkeypatch):
    from harvest import transcribe as T
    monkeypatch.setattr(T, "_register_cuda_dlls", lambda: None)
    rec = {}
    _install_fake_whisper(monkeypatch, rec)
    T.transcribe(Path("x.m4a"), lang="zh")
    assert rec["language"] == "zh"
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_transcribe.py -q`
Expected: FAIL (`transcribe() got an unexpected keyword argument 'lang'`).

- [ ] **Step 3: Edit `harvest/transcribe.py`**

```python
def transcribe(
    audio_path: Path, *, robust: bool = False, model: str = WHISPER_MODEL, lang: str | None = None
) -> list[Segment]:
    _register_cuda_dlls()
    from faster_whisper import WhisperModel

    wm = WhisperModel(model, device="cuda", compute_type="float16")
    segments, _info = wm.transcribe(
        str(audio_path),
        language=lang,                       # None => faster-whisper auto-detect
        vad_filter=True,
        word_timestamps=True,
        condition_on_previous_text=not robust,
    )
    return [
        Segment(start=round(s.start, 3), end=round(s.end, 3), text=s.text.strip())
        for s in segments
    ]
```

Update the module docstring line `large-v3 on CUDA, language="zh", ...` -> `large-v3 on CUDA, language configurable (None => auto-detect), ...`.

- [ ] **Step 4: Run tests green**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_transcribe.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: make whisper transcription language configurable (default auto-detect)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

> `cli._whisper` still calls `transcribe(audio, robust=...)` (no `lang`), so behavior is unchanged until Task 9 threads the resolved language + `--lang` into it.

---

## Task 8: Config / env rename to HARVEST_*

Rename env keys `BILI_*` -> `HARVEST_*`, keep per-provider auth, keep `REFERER` bilibili-scoped. Update `.env.example`.

**Files:**
- Modify: `harvest/config.py`
- Modify: `.env.example`
- Test: `tests/test_config.py` (new)

**Interfaces:**
- Produces: `Settings.load()` reads `HARVEST_COOKIES_BROWSER`, `HARVEST_COOKIES_PROFILE`, `HARVEST_CACHE_DIR`, `HARVEST_OUT_DIR`; `SESSDATA` unchanged. `REFERER` stays a bilibili constant in `config.py`.

- [ ] **Step 1: Write failing test**

`tests/test_config.py`:

```python
from pathlib import Path

from harvest.config import Settings


def test_load_reads_harvest_env_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("HARVEST_COOKIES_BROWSER", "chrome")
    monkeypatch.setenv("HARVEST_COOKIES_PROFILE", "Default")
    monkeypatch.setenv("HARVEST_CACHE_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("HARVEST_OUT_DIR", str(tmp_path / "o"))
    monkeypatch.setenv("BILI_COOKIES_BROWSER", "firefox")  # old key must be ignored
    s = Settings.load()
    assert s.cookies_browser == "chrome"
    assert s.cookies_profile == "Default"
    assert s.cache_dir == Path(tmp_path / "c")
    assert s.out_dir == Path(tmp_path / "o")
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_config.py -q`
Expected: FAIL (`s.cookies_browser == "firefox"`, old key still read).

- [ ] **Step 3: Edit `harvest/config.py` `Settings.load()`**

```python
cookies_browser=os.environ.get("HARVEST_COOKIES_BROWSER", cls.cookies_browser),
cookies_profile=os.environ.get("HARVEST_COOKIES_PROFILE", ""),
```
```python
if os.environ.get("HARVEST_CACHE_DIR"):
    s.cache_dir = Path(os.environ["HARVEST_CACHE_DIR"])
if os.environ.get("HARVEST_OUT_DIR"):
    s.out_dir = Path(os.environ["HARVEST_OUT_DIR"])
```

Leave `SESSDATA`, `LMSTUDIO_*`, and `REFERER = "https://www.bilibili.com"` unchanged.

- [ ] **Step 4: Update `.env.example`**

Replace the whole file:

```dotenv
# harvest configuration — copy to `.env` and fill in. NEVER commit the real `.env`.

# --- Vision: LM Studio (OpenAI-compatible endpoint) ---
LMSTUDIO_BASE_URL=http://localhost:1234/v1
LMSTUDIO_API_KEY=
LMSTUDIO_VISION_MODEL=

# --- bilibili auth (D9) ---
# Default: yt-dlp reads cookies live from your logged-in browser (HARVEST_COOKIES_* below).
# Fallback (headless / no browser): paste a SESSDATA cookie value here.
# SESSDATA=

# --- Cookies-from-browser (D9 default; also unlocks age-gated YouTube when set) ---
# On Windows, Firefox is the reliable choice (Chromium browsers DPAPI-encrypt the cookie DB).
HARVEST_COOKIES_BROWSER=firefox
HARVEST_COOKIES_PROFILE=

# --- Optional overrides (otherwise sensible defaults in config.py) ---
# HARVEST_CACHE_DIR=cache
# HARVEST_OUT_DIR=out
# FFMPEG_PATH=
```

- [ ] **Step 5: Run tests green**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_config.py -q`
Expected: PASS. Then full suite -> PASS.
Run: `grep -rn "BILI_" harvest/ .env.example` — expected: no output.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: rename env prefix BILI_* -> HARVEST_*

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: CLI + probe integration via provider dispatch

Wire the provider seam into `cli.py` and `probe.py`: dispatch by `select_provider(url)`, add `--lang`, and route BOTH sources through the provider's `fetch_subtitle` (which returns a `SubtitleOutcome` — YouTube's exact-key rule, bilibili's gate/#6357 now live inside their providers), so `decide_transcript` is fully platform-agnostic. Generalize `probe`/`build_bundle` to consume `SourceMetadata`.

**Files:**
- Modify: `harvest/__init__.py` (import providers so they register)
- Modify: `harvest/probe.py`
- Modify: `harvest/cli.py`
- Modify: `harvest/merge.py` (`build_bundle` consumes `SourceMetadata`)
- Test: `tests/test_probe.py`, `tests/test_cli.py`, `tests/test_merge.py`

**Interfaces:**
- Consumes: `select_provider` from `harvest.providers.base`; provider `fetch_metadata`/`fetch_subtitle`; `SourceMetadata`; `transcribe(..., lang=...)` (Task 7).
- Produces: `harvest probe <url>` works for youtube.com; `harvest ingest <url> --lang CODE` works; `decide_transcript(canonical, meta, settings, args)`; `build_bundle(canonical, meta, transcript, frames, settings, *, vision_model=None)`.

- [ ] **Step 1: Ensure providers register on import**

In `harvest/__init__.py`, after the existing imports add (registration side effect):

```python
from .providers import bilibili as _bilibili  # noqa: F401,E402  (registers BilibiliProvider)
from .providers import youtube as _youtube    # noqa: F401,E402  (registers YouTubeProvider)
```

Add both to nothing else; they exist purely for the `register()` side effect so `select_provider` sees them whenever `harvest` is imported.

- [ ] **Step 2: Write failing probe test (YouTube path via provider)**

Add to `tests/test_probe.py`:

```python
def test_probe_youtube_delegates_to_provider(monkeypatch):
    from harvest import probe as probe_mod
    from harvest.providers.base import Canonical, SourceMetadata
    from harvest.schema import ProbeResult

    canonical = Canonical("youtube.com", "dQw4w9WgXcQ", 1, "https://youtu.be/dQw4w9WgXcQ")

    class _FakeYT:
        def fetch_metadata(self, c, settings):
            return SourceMetadata(
                platform="youtube.com", id="dQw4w9WgXcQ", title="T", uploader="C",
                uploader_id="UCx", description="d", duration_s=100,
                published_at="2009-10-25T06:57:33Z", parts=1, part_durations_s=[100])

    monkeypatch.setattr(probe_mod, "select_provider", lambda url: _FakeYT())
    result = probe_mod.probe(canonical, Settings())
    assert isinstance(result, ProbeResult)
    assert result.platform == "youtube.com"
    assert result.uploader_id == "UCx"
    assert result.published_at.endswith("Z")
```

- [ ] **Step 3: Rewrite `harvest/probe.py`**

```python
"""Public pre-flight metadata probe. Delegates to the URL-selected provider's fetch_metadata
-> SourceMetadata, then maps that normalized shape onto the stable ProbeResult schema. No
platform branches (SPEC §4.1)."""

from __future__ import annotations

from .config import Settings
from .providers.base import Canonical, select_provider
from .schema import ProbeResult


def probe(canonical: Canonical, settings: Settings, *, opener=None) -> ProbeResult:
    if canonical.platform == "bilibili.tv":
        raise ValueError("probe is bilibili.com-only; bilibili.tv unsupported (deferred)")

    provider = select_provider(canonical.url)
    if opener is not None:
        meta = provider.fetch_metadata(canonical, settings, opener=opener)
    else:
        meta = provider.fetch_metadata(canonical, settings)
    return ProbeResult(
        platform=meta.platform,
        id=meta.id,
        title=meta.title,
        uploader=meta.uploader,
        uploader_id=meta.uploader_id,
        description=meta.description,
        duration_s=meta.duration_s,
        published_at=meta.published_at,
        parts=meta.parts,
        part_durations_s=meta.part_durations_s,
    )
```

> `opener=` is forwarded so the 5 existing opener-injected bilibili probe tests keep passing unchanged: they `resolve()` a bilibili canonical, `select_provider` returns `BilibiliProvider`, whose `fetch_metadata(..., opener=...)` uses the fake. Keep those tests as-is.

- [ ] **Step 4: Run probe tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_probe.py -q`
Expected: PASS (existing bilibili opener tests + new YouTube test).

- [ ] **Step 5: Commit the probe half**

```bash
git add -A
git commit -m "feat: dispatch probe via provider seam (SourceMetadata -> ProbeResult)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9 (continued): CLI ingest dispatch

- [ ] **Step 6: Add `--lang` and write failing parse tests**

Add to `tests/test_cli.py`:

```python
def test_ingest_accepts_lang_flag():
    args = parse_args(["ingest", "https://youtu.be/dQw4w9WgXcQ", "--lang", "es"])
    assert args.lang == "es"


def test_ingest_lang_defaults_to_none():
    args = parse_args(["ingest", "https://youtu.be/dQw4w9WgXcQ"])
    assert args.lang is None
```

Run: `./.venv/Scripts/python.exe -m pytest tests/test_cli.py -k lang -q`
Expected: FAIL (`Namespace has no attribute 'lang'`).

In `harvest/cli.py` `parse_args`, add to the `ingest` subparser:

```python
ingest.add_argument(
    "--lang", default=None,
    help="pin transcription language (default: zh for bilibili, auto-detect for YouTube)",
)
```

- [ ] **Step 7: Write failing dispatch tests (YouTube decide_transcript)**

Add to `tests/test_cli.py`:

```python
def test_decide_transcript_youtube_reuses_human_sub_no_quality_gate(monkeypatch):
    from harvest import cli
    from harvest.providers.base import Canonical, SourceMetadata, SubtitleOutcome
    from harvest.schema import Segment

    canonical = Canonical("youtube.com", "dQw4w9WgXcQ", 1, "https://youtu.be/dQw4w9WgXcQ")
    meta = SourceMetadata(platform="youtube.com", id="dQw4w9WgXcQ", title="T", uploader="C",
                          uploader_id="UCx", description="d", duration_s=100,
                          published_at="2009-10-25T06:57:33Z", parts=1, part_durations_s=[100])

    class _FakeYT:
        def fetch_subtitle(self, c, settings, m, *, pinned_lang=None):
            assert pinned_lang is None
            return SubtitleOutcome(
                accepted=True, source="human-sub",
                source_reason="human-sub (exact-key match: en)", language="en",
                segments=[Segment(start=0.0, end=1.0, text="hi")], quality_gate=None)

    monkeypatch.setattr(cli, "select_provider", lambda url: _FakeYT())
    args = parse_args(["ingest", "https://youtu.be/dQw4w9WgXcQ"])
    t = cli.decide_transcript(canonical, meta, _settings(), args)
    assert t.source == "human-sub"
    assert t.language == "en"
    assert t.quality_gate is None
    assert t.segments[0].text == "hi"


def test_decide_transcript_youtube_falls_back_to_whisper(monkeypatch):
    from harvest import cli
    from harvest.providers.base import Canonical, SourceMetadata

    canonical = Canonical("youtube.com", "x", 1, "https://youtu.be/x")
    meta = SourceMetadata(platform="youtube.com", id="x", title=None, uploader=None,
                          uploader_id=None, description=None, duration_s=10,
                          published_at=None, parts=1, part_durations_s=[10])

    class _FakeYT:
        def fetch_subtitle(self, c, settings, m, *, pinned_lang=None):
            return None

    monkeypatch.setattr(cli, "select_provider", lambda url: _FakeYT())
    captured = {}

    def fake_whisper(canonical, settings, args, *, reason, gate=None, lang=None):
        from harvest.schema import Transcript
        captured["lang"] = lang
        return Transcript(source="whisper", source_reason=reason, language=lang, segments=[])

    monkeypatch.setattr(cli, "_whisper", fake_whisper)
    args = parse_args(["ingest", "https://youtu.be/x"])
    t = cli.decide_transcript(canonical, meta, _settings(), args)
    assert t.source == "whisper"
    assert captured["lang"] is None


def test_decide_transcript_bilibili_accepted_outcome_keeps_gate(monkeypatch):
    # Agnostic path: provider returns an accepted outcome (gate passed) -> Transcript mirrors it.
    from harvest import cli
    from harvest.providers.base import Canonical, SourceMetadata, SubtitleOutcome
    from harvest.schema import QualityGate, Segment

    canonical = Canonical("bilibili.com", "BV1", 1, "https://www.bilibili.com/video/BV1")
    meta = SourceMetadata(platform="bilibili.com", id="BV1", title="T", uploader="U",
                          uploader_id="7", description="d", duration_s=100, published_at=None,
                          parts=1, part_durations_s=[100])
    gate = QualityGate(passed=True, punct_density=1.0, dup_ratio=0.0, nonzh_ratio=0.0, cps=5.0)

    class _FakeBili:
        def fetch_subtitle(self, c, settings, m, *, pinned_lang=None):
            return SubtitleOutcome(accepted=True, source="auto-sub",
                                   source_reason="auto-sub (quality-gate: passed)", language="zh",
                                   segments=[Segment(start=0.0, end=1.0, text="你好")], quality_gate=gate)

    monkeypatch.setattr(cli, "select_provider", lambda url: _FakeBili())
    t = cli.decide_transcript(canonical, meta, _settings(),
                              parse_args(["ingest", "https://www.bilibili.com/video/BV1"]))
    assert t.source == "auto-sub" and t.language == "zh" and t.quality_gate is gate


def test_decide_transcript_rejected_outcome_falls_back_to_whisper_with_gate(monkeypatch):
    # accepted=False must carry source_reason + failed gate into the Whisper transcript.
    from harvest import cli
    from harvest.providers.base import Canonical, SourceMetadata, SubtitleOutcome
    from harvest.schema import QualityGate

    canonical = Canonical("bilibili.com", "BV1", 1, "https://www.bilibili.com/video/BV1")
    meta = SourceMetadata(platform="bilibili.com", id="BV1", title="T", uploader="U",
                          uploader_id="7", description="d", duration_s=100, published_at=None,
                          parts=1, part_durations_s=[100])
    failed = QualityGate(passed=False, punct_density=0.0, dup_ratio=0.9, nonzh_ratio=0.0, cps=1.0)

    class _FakeBili:
        def fetch_subtitle(self, c, settings, m, *, pinned_lang=None):
            return SubtitleOutcome(accepted=False, source=None,
                                   source_reason="subtitle rejected (dup_ratio 0.90)", language=None,
                                   segments=[], quality_gate=failed)

    monkeypatch.setattr(cli, "select_provider", lambda url: _FakeBili())
    captured = {}

    def fake_whisper(canonical, settings, args, *, reason, gate=None, lang=None):
        from harvest.schema import Transcript
        captured.update(reason=reason, gate=gate, lang=lang)
        return Transcript(source="whisper", source_reason=reason, language=lang,
                          quality_gate=gate, segments=[])

    monkeypatch.setattr(cli, "_whisper", fake_whisper)
    t = cli.decide_transcript(canonical, meta, _settings(),
                              parse_args(["ingest", "https://www.bilibili.com/video/BV1"]))
    assert t.source == "whisper" and captured["gate"] is failed
    assert "rejected" in captured["reason"] and captured["lang"] == "zh"
```

Run: `./.venv/Scripts/python.exe -m pytest tests/test_cli.py -k "decide_transcript" -q`
Expected: FAIL (`decide_transcript` signature/behavior mismatch).

- [ ] **Step 8: Rewrite `decide_transcript` + `_whisper` in `cli.py` (fully platform-agnostic)**

The bilibili gated flow (probe + gate + #6357 + `_part1_segments`) now lives in `BilibiliProvider.fetch_subtitle` (Task 4). `decide_transcript` no longer branches on platform — it asks the selected provider for a `SubtitleOutcome` and builds the `Transcript`, preserving a rejected outcome's reason + gate on the Whisper fallback. Change the signature to `(canonical, meta, settings, args)`. New body:

```python
def decide_transcript(canonical, meta, settings, args):
    # The only place a platform name remains: choosing the Whisper-fallback default language.
    # This is a default, not a control-flow branch — acquisition is fully delegated below.
    default_lang = args.lang if args.lang else (
        "zh" if canonical.platform == "bilibili.com" else None
    )
    if args.force_whisper:
        return _whisper(canonical, settings, args,
                        reason="forced via --force-whisper", lang=default_lang)

    provider = select_provider(canonical.url)
    outcome = provider.fetch_subtitle(canonical, settings, meta, pinned_lang=args.lang)
    if outcome is None or not outcome.accepted:
        reason = outcome.source_reason if outcome else "no usable subtitle"
        gate = outcome.quality_gate if outcome else None
        return _whisper(canonical, settings, args, reason=reason, gate=gate, lang=default_lang)
    return Transcript(
        source=outcome.source, source_reason=outcome.source_reason,
        language=outcome.language, quality_gate=outcome.quality_gate,
        segments=outcome.segments,
    )
```

Change `_whisper` to `def _whisper(canonical, settings, args, *, reason, gate=None, lang=None)`, pass `lang=lang` into `transcribe(audio, robust=args.robust, lang=lang)`, and set `Transcript(language=lang, ...)` (was `language="zh"`). Keep the cache key unchanged.

Add `from .providers.base import select_provider` at the top of `cli.py`. **Delete** the now-unused `decide_transcript` bilibili helpers and their imports from `cli.py`: `_part1_segments` (moved to `BilibiliProvider`), and the `subtitle_probe`, `evaluate`, `describe_failure`, `extract_info`, `fetch_subtitle_segments`, `part_url` imports if nothing else in `cli.py` uses them (verify with a grep; `fetch_view`/`ViewError` are also removed unless still referenced). Any existing `test_cli.py` tests that asserted the old inline bilibili gate/#6357 behavior are superseded by the `BilibiliProvider` tests (Task 4) and the two `decide_transcript_*_outcome_*` tests above — delete the superseded ones.

Run: `./.venv/Scripts/python.exe -m pytest tests/test_cli.py -k "decide_transcript" -q`
Expected: PASS.

- [ ] **Step 9: Update `build_bundle` to consume `SourceMetadata`**

In `harvest/merge.py`:

```python
def build_bundle(canonical, meta, transcript, frames, settings, *, vision_model=None):
    return Bundle(
        platform=canonical.platform, id=canonical.id, part=canonical.part, url=canonical.url,
        title=meta.title, uploader=meta.uploader, uploader_id=meta.uploader_id,
        description=meta.description, duration_s=meta.duration_s, published_at=meta.published_at,
        fetched_at=iso_now(), transcript=transcript, frames=frames,
        meta=Meta(
            cookies_used=bool(settings.sessdata or settings.cookies_browser),
            referer_used=(canonical.platform == "bilibili.com"),
            vision_model=vision_model, tool_version=settings.tool_version,
        ),
    )
```

Remove the now-unused `from .player_api import ViewData, published_at_iso` import from `merge.py`. Update `tests/test_merge.py` `build_bundle` tests to pass a `SourceMetadata` (from `harvest.providers.base`) instead of `info=`/`view=`; assert `bundle.uploader_id` and `bundle.published_at` come from `meta`.

- [ ] **Step 10: Rewrite `process_part` and part-enumeration in `cli.py`**

`process_part`:

```python
def process_part(canonical, settings, args):
    provider = select_provider(canonical.url)
    meta = provider.fetch_metadata(canonical, settings)
    transcript = decide_transcript(canonical, meta, settings, args)

    frames = []
    frame_sources = {}
    vision_model = None
    if not args.no_vision:
        from .frames import download_video, extract_frames

        print(f"[{canonical.id} p{canonical.part}] preparing video + frames...")
        video = download_video(canonical, settings)
        frames, frame_sources = extract_frames(canonical, video, settings)
        print(f"[{canonical.id} p{canonical.part}] {len(frames)} frames after dedup")
        if frames:
            frames = _caption(canonical, frames, frame_sources, settings)
            vision_model = settings.lmstudio_vision_model

    bundle = build_bundle(canonical, meta, transcript, frames, settings, vision_model=vision_model)
    out = write_bundle(bundle, settings, frame_sources=frame_sources,
                       frame_images=not args.no_frame_images)
    n = len(transcript.segments)
    print(f"[{canonical.id} p{canonical.part}] {transcript.source}: "
          f"{n} segments, {len(frames)} frames -> {out}")
```

`_run_ingest` — replace the `platform == "bilibili.com"` view/info enumeration branch with:

```python
if canonical.platform == "bilibili.tv":
    print("error: ingest is bilibili.com-only; bilibili.tv unsupported (deferred)",
          file=sys.stderr)
    return 1
provider = select_provider(canonical.url)
total = provider.enumerate_parts(canonical, settings)
```

Remove now-unused imports (`ViewData`; `fetch_view` stays for `_bili_view`; `fetch_subtitle_segments` stays for `_part1_segments`). `probe()` keeps its own `.tv` `ValueError`, so `_run_probe` needs no change.

Update `tests/test_cli.py`:
- `test_ingest_enumerates_parts_from_view_pages` currently monkeypatches `cli.fetch_view`. Rewrite it to monkeypatch `cli.select_provider` returning a fake whose `enumerate_parts` returns 3; assert `seen_parts == [1, 2, 3]`.
- Replace `test_process_part_fetches_view_once_and_reuses_it_for_subtitle_path` (it pins the retired bilibili double-fetch) with the metadata-once test below.

```python
def test_process_part_fetches_metadata_once_and_shares_it(monkeypatch):
    from harvest import cli
    from harvest.providers.base import Canonical, SourceMetadata
    from harvest.schema import Bundle, Meta, Transcript

    canonical = Canonical("youtube.com", "x", 1, "https://youtu.be/x")
    meta = SourceMetadata(platform="youtube.com", id="x", title="t", uploader=None,
                          uploader_id=None, description=None, duration_s=1,
                          published_at=None, parts=1, part_durations_s=[1])
    calls = {"meta": 0}

    class _FakeP:
        def fetch_metadata(self, c, settings):
            calls["meta"] += 1
            return meta

    monkeypatch.setattr(cli, "select_provider", lambda url: _FakeP())
    seen = {}

    def fake_decide(canonical, m, settings, args):
        seen["decide_meta"] = m
        return Transcript(source="whisper", source_reason="x", language=None, segments=[])

    monkeypatch.setattr(cli, "decide_transcript", fake_decide)

    def fake_build(canonical, m, transcript, frames, settings, *, vision_model=None):
        seen["build_meta"] = m
        return Bundle(platform="youtube.com", id="x", part=1, url=canonical.url, title="t",
                      fetched_at="2026-07-02T00:00:00Z", transcript=transcript, frames=[],
                      meta=Meta(cookies_used=False, referer_used=False, tool_version="0"))

    monkeypatch.setattr(cli, "build_bundle", fake_build)
    monkeypatch.setattr(cli, "write_bundle", lambda *a, **k: "out/path")

    args = parse_args(["ingest", "https://youtu.be/x", "--no-vision"])
    cli.process_part(canonical, _settings(), args)
    assert calls["meta"] == 1
    assert seen["decide_meta"] is meta and seen["build_meta"] is meta
```

- [ ] **Step 11: Run the full suite green**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "feat: dispatch ingest via provider seam; add --lang; SourceMetadata bundle

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: Opt-in `@live` smoke test

One network test, excluded from the default run via a registered marker, hitting a single stable public YouTube id end-to-end at the metadata/subtitle level.

**Files:**
- Modify: `pyproject.toml` (register the `live` marker + default `-m "not live"`)
- Test: `tests/test_live_youtube.py`

**Interfaces:**
- Consumes: `YouTubeProvider`, `Settings`.
- Produces: `tests/test_live_youtube.py::test_live_youtube_metadata_and_subtitle` marked `@pytest.mark.live`, skipped by default.

- [ ] **Step 1: Register the marker and exclude it by default**

In `pyproject.toml` `[tool.pytest.ini_options]`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-m 'not live'"
markers = ["live: hits the network against a real public video (opt in with -m live)"]
```

- [ ] **Step 2: Write the live test**

`tests/test_live_youtube.py`:

```python
import pytest

from harvest.config import Settings
from harvest.providers.base import SourceMetadata
from harvest.providers.youtube import YouTubeProvider

# Big Buck Bunny — stable, public, license-clean; the drift canary.
_LIVE_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


@pytest.mark.live
def test_live_youtube_metadata_and_subtitle():
    p = YouTubeProvider()
    canonical = p.resolve(_LIVE_URL)
    assert canonical.id == "aqz-KE-bpKQ"

    meta = p.fetch_metadata(canonical, Settings())
    assert isinstance(meta, SourceMetadata)
    assert meta.platform == "youtube.com"
    assert meta.uploader_id and meta.uploader_id.startswith("UC")
    assert meta.duration_s and meta.duration_s > 0
    assert meta.parts == 1

    # Probe showed language None + empty subtitles -> Whisper path (None). Tolerate either.
    got = p.fetch_subtitle(canonical, Settings(), meta)
    assert got is None or (got.accepted and got.source == "human-sub" and isinstance(got.segments, list))
```

- [ ] **Step 3: Confirm it is excluded by default**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: PASS, with the live test deselected (1 deselected). It must NOT touch the network here.

- [ ] **Step 4: (Optional, manual) run the live test explicitly**

Run: `./.venv/Scripts/python.exe -m pytest -m live -q`
Expected: PASS when online (network-dependent; do not gate CI on it).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: add opt-in @live YouTube smoke test (excluded by default)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage.**
- SPEC §4.1 Provider seam / SourceMetadata -> Task 3; both providers -> Tasks 4, 6; downstream reads the normalized shape -> Task 9 (`build_bundle`, `probe` consume `SourceMetadata`). OK
- SPEC §6 bilibili transcript logic (gate + #6357) -> moved INTO `BilibiliProvider.fetch_subtitle` (Task 4), reusing the unchanged `subtitle_probe`/`evaluate`; `decide_transcript` is platform-agnostic (clean-seam decision). OK
- SPEC §6 YouTube exact-key rule, skip-auto, no gate -> Task 6 (`fetch_subtitle` returns `SubtitleOutcome(quality_gate=None)`). OK
- SPEC §6 field mapping (channel_id->uploader_id, timestamp/upload_date->published_at, vtt) -> Task 6 + Task 5. OK
- SPEC §6 language default (zh bilibili / None youtube, `--lang` override) -> Tasks 7 & 9. OK
- SPEC §7 offline default + one @live -> Task 10. OK
- SPEC §8 per-source tz -> bilibili `published_at_iso` (+08:00) kept; YouTube `_published_at` (UTC Z) Task 6. OK
- SPEC §8 auth per-provider `auth_opts` -> Tasks 4 & 6. OK
- PROTOCOL: SCHEMA_VERSION 1.0, platform + youtube.com, uploader_id str (mid removed), auto-sub rename, provenance authority -> Task 2. OK
- PROTOCOL: `.tv` unsupported message on probe -> Task 9 (`probe` guard preserved). OK
- Package/env rename -> Tasks 1 & 8. OK

**2. Placeholder scan.** No "TBD"/"add error handling"/"similar to Task N" — every code step carries real code; refactor steps give exact old->new signatures and the pinning test.

**3. Type consistency.** `Canonical` defined in Task 3, re-exported by `resolve` (Task 3 Step 5), consumed unchanged everywhere. `SourceMetadata` field names identical across Tasks 3/4/6/9/10. `fetch_subtitle(canonical, settings, meta, *, pinned_lang=None) -> SubtitleOutcome | None` consistent across Tasks 3 (protocol), 4 (bilibili, gate/#6357 inside), 6 (youtube), 9 (consumed agnostically), 10 (live). `SubtitleOutcome(accepted, source, source_reason, language, segments, quality_gate)` used identically in Tasks 3/4/6/9. `decide_transcript(canonical, meta, settings, args)` new signature consistent in Task 9 tests + impl; no platform branch except the `default_lang` default. `_whisper(..., *, reason, gate=None, lang=None)` consistent Task 9. `build_bundle(canonical, meta, transcript, frames, settings, *, vision_model=None)` consistent Task 9/10 + merge tests. `transcribe(..., lang=None)` consistent Tasks 7/9. `TranscriptSource` values (`human-sub`/`auto-sub`/`whisper`) consistent Tasks 2/4/6/9.
