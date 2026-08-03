# Harvest Portable Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved portable `harvest-video-ingestion` Agent Skill with concise instructions, current-contract references, safe runtime wrappers, and local bundle inspection/validation tools.

**Status:** Implemented and verified.

**Architecture:** Create the publishable artifact under `skill/harvest-video-ingestion/`. `SKILL.md` contains only trigger guidance and the shortest safe operating workflow; detailed current facts live in directly linked `references/` files. `scripts/` contains thin Python wrappers that discover either a BliSolver checkout or an installed `harvest` command, plus local-only bundle utilities; it does not vendor the application.

**Tech Stack:** Agent Skills `SKILL.md` format, Python 3.11+ standard library for wrappers, current BliSolver `harvest` CLI/schema for delegation and validation, pytest subprocess/unit tests, `skills-ref validate` when available.

## Global Constraints

- Published skill name is exactly `harvest-video-ingestion`; the directory and `SKILL.md` frontmatter name must match.
- Current bundle schema is exactly `1.1`; `Segment.source`/`confidence` and `Bundle.ocr` must be documented and validated.
- Supported operational platforms are `bilibili.com` and YouTube; `bilibili.tv` is deferred and must remain an explicit failure.
- The current transcription backend is `whisper-cli`/whisper.cpp; do not describe faster-whisper/CUDA as the current implementation where source code disagrees.
- No skill script may vendor or modify `harvest/`, `.venv/`, `.ocr-venv/`, `cache/`, `out/`, models, browser profiles, cookies, `.env`, or the pre-existing untracked `scripts/download_video.py`.
- No script may use `shell=True`, print secret values, or perform network calls from `doctor.py`, `inspect_bundle.py`, or `validate_bundle.py`.
- `probe.py` must keep successful stdout as one JSON object and send diagnostics to stderr; `validate_bundle.py` exits 0 valid, 1 invalid, 2 usage/runtime failure.
- Existing tests must remain green; the baseline is `343 passed, 4 deselected`, before adding skill tests.

---

## File map

Create the following files:

- `skill/harvest-video-ingestion/SKILL.md` — portable skill manifest, trigger rules, decision tree, command table, and reference-loading map.
- `skill/harvest-video-ingestion/LICENSE.txt` — exact MIT license text copied from the repository `LICENSE`.
- `skill/harvest-video-ingestion/references/architecture.md` — current component map and URL-to-bundle flow.
- `skill/harvest-video-ingestion/references/current-contract.md` — CLI, schema 1.1, output layout, provenance, and optional-track contract.
- `skill/harvest-video-ingestion/references/provider-guide.md` — provider selection, bilibili/YouTube differences, auth, subtitle tiers, and deferred `.tv` behavior.
- `skill/harvest-video-ingestion/references/pipeline-stages.md` — cache keys, whisper.cpp, frames, vision/projector check, OCR isolate, fusion, danmaku, interactions, and MCP.
- `skill/harvest-video-ingestion/references/operational-runbook.md` — setup, preflight, safe invocations, failure recovery, and validation.
- `skill/harvest-video-ingestion/references/domain-glossary.md` — Atlas/harvest terms and authority rules.
- `skill/harvest-video-ingestion/references/source-map.md` — map each operational claim to current source/test files and mark historical docs.
- `skill/harvest-video-ingestion/scripts/_common.py` — runtime discovery and subprocess helpers shared by wrappers.
- `skill/harvest-video-ingestion/scripts/doctor.py` — no-network dependency/configuration report.
- `skill/harvest-video-ingestion/scripts/probe.py` — JSON-safe `harvest probe` adapter.
- `skill/harvest-video-ingestion/scripts/ingest.py` — explicit current-flag `harvest ingest` adapter and dry-run mode.
- `skill/harvest-video-ingestion/scripts/inspect_bundle.py` — local compact bundle summary.
- `skill/harvest-video-ingestion/scripts/validate_bundle.py` — schema/path/artifact validator.
- `tests/test_portable_skill.py` — deterministic tests for package shape and every wrapper behavior.

Do not modify existing application files unless a test exposes an unavoidable compatibility defect in the wrapper boundary.

---

### Task 1: Establish failing tests for the skill package and script contracts

**Files:**
- Create: `tests/test_portable_skill.py`
- Read-only fixtures: `harvest/schema.py`, `pyproject.toml`, `LICENSE`

**Interfaces:**
- Tests invoke scripts as real subprocesses through `sys.executable`, so the package must work when copied outside the checkout.
- Temporary bundle fixtures use the current JSON shape: required `platform`, `id`, `part`, `url`, `fetched_at`, `transcript`, and `meta`; optional `frames`, `ocr`, `danmaku`, and `interactions` exercise the inspector and validator.

- [ ] **Step 1: Write the failing tests**

Add a test module with these helpers and cases:

```python
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skill" / "harvest-video-ingestion"
SCRIPTS = SKILL / "scripts"


def run_script(name: str, *args: str, cwd: Path = REPO, env: dict[str, str] | None = None):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        cwd=cwd,
        env=merged,
        capture_output=True,
        text=True,
    )


def write_bundle(
    root: Path, *, frame_path: str | None = "frames/000.png", create_frame: bool = True
) -> Path:
    bundle_dir = root / "bundle"
    bundle_dir.mkdir(parents=True)
    if frame_path and create_frame and not frame_path.startswith("../"):
        frame = bundle_dir / frame_path
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"png-fixture")
    payload = {
        "schema_version": "1.1",
        "platform": "youtube.com",
        "id": "fixture-id",
        "part": 1,
        "url": "https://www.youtube.com/watch?v=fixture-id",
        "fetched_at": "2026-07-31T00:00:00Z",
        "transcript": {
            "source": "whisper",
            "source_reason": "fixture",
            "language": "en",
            "model": "fixture-model",
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello", "source": "whisper"}],
        },
        "ocr": [{"start": 0.0, "end": 1.0, "text": "HELLO", "source": "ocr"}],
        "frames": [{"ts": 0.0, "path": frame_path, "phash": "00", "caption": "slide", "ocr": "HELLO"}],
        "danmaku": {"fetched_total": 2, "windows": [{"start": 0, "end": 15, "total": 2, "lines": [{"text": "hi"}]}]},
        "interactions": {"votes": [{"question": "Q", "options": [], "total_count": 0}], "grades": [{"avg_score": 8.0, "count": 2}]},
        "meta": {"cookies_used": False, "referer_used": False, "tool_version": "0.1.0"},
    }
    (bundle_dir / "bundle.json").write_text(json.dumps(payload), encoding="utf-8")
    (bundle_dir / "bundle.md").write_text("# fixture\n", encoding="utf-8")
    return bundle_dir


def test_manifest_lists_valid_skill_files():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "name: harvest-video-ingestion" in text
    assert "references/current-contract.md" in text
    assert "scripts/validate_bundle.py" in text
    for path in [
        "LICENSE.txt",
        "references/architecture.md",
        "references/current-contract.md",
        "references/provider-guide.md",
        "references/pipeline-stages.md",
        "references/operational-runbook.md",
        "references/domain-glossary.md",
        "references/source-map.md",
    ]:
        assert (SKILL / path).is_file(), path


def test_all_user_scripts_support_help():
    for path in sorted(SCRIPTS.glob("*.py")):
        if path.name == "_common.py":
            continue
        result = run_script(path.name, "--help")
        assert result.returncode == 0, (path.name, result.stderr)
        assert "usage" in result.stdout.lower()


def test_ingest_dry_run_forwards_current_flags(tmp_path):
    result = run_script(
        "ingest.py", "https://example.invalid/video", "--project-root", str(REPO), "--dry-run",
        "--part", "2", "--force-whisper", "--lang", "zh", "--robust", "--no-vision",
        "--dedup-threshold", "7", "--out", str(tmp_path / "out"), "--no-frame-images",
        "--danmaku", "--interactions", "--ocr", "--force-ocr",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["cwd"] == str(REPO)
    assert payload["command"][-2:] == ["--ocr", "--force-ocr"]
    assert payload["command"][0:3] == [sys.executable, "-m", "harvest.cli"]
    assert "--force-whisper" in payload["command"]
    assert "--no-frame-images" in payload["command"]


def test_probe_keeps_child_json_on_stdout(tmp_path):
    fake = tmp_path / "harvest"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "print('diagnostic', file=sys.stderr)\n"
        "print(json.dumps({'schema_version': '1.1', 'id': 'fixture'}))\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {"PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "HARVEST_PROJECT_ROOT": ""}
    result = run_script("probe.py", "https://example.invalid/video", cwd=tmp_path, env=env)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"schema_version": "1.1", "id": "fixture"}
    assert "diagnostic" in result.stderr


def test_inspect_bundle_returns_compact_counts(tmp_path):
    bundle = write_bundle(tmp_path)
    result = run_script("inspect_bundle.py", str(bundle))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["identity"]["id"] == "fixture-id"
    assert payload["transcript"]["segments"] == 1
    assert payload["ocr"]["segments"] == 1
    assert payload["frames"] == {"count": 1, "with_caption": 1, "with_ocr": 1, "with_image_path": 1, "missing_images": 0}
    assert payload["danmaku"] == {"windows": 1, "lines": 1, "fetched_total": 2}
    assert payload["interactions"] == {"votes": 1, "grades": 1}


def test_validate_bundle_accepts_and_rejects_artifacts(tmp_path):
    valid = write_bundle(tmp_path / "valid")
    accepted = run_script("validate_bundle.py", str(valid), "--project-root", str(REPO))
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert json.loads(accepted.stdout)["valid"] is True

    broken = write_bundle(
        tmp_path / "broken", frame_path="frames/missing.png", create_frame=False
    )
    rejected = run_script("validate_bundle.py", str(broken), "--project-root", str(REPO))
    assert rejected.returncode == 1
    report = json.loads(rejected.stdout)
    assert report["valid"] is False
    assert any("missing" in error.lower() for error in report["errors"])


def test_doctor_json_never_contains_secret_values():
    result = run_script(
        "doctor.py", "--project-root", str(REPO), "--json",
        env={"SESSDATA": "secret-sessdata", "LMSTUDIO_API_KEY": "secret-api-key"},
    )
    assert result.returncode in (0, 1)
    assert "secret-sessdata" not in result.stdout + result.stderr
    assert "secret-api-key" not in result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert isinstance(report["checks"], list)
```

- [ ] **Step 2: Run the focused tests to verify they fail for missing artifact files**

Run:

```bash
./.venv/bin/python -m pytest -q tests/test_portable_skill.py
```

Expected: collection or assertion failures because `skill/harvest-video-ingestion/` and its scripts do not yet exist. Do not weaken the tests to make this first run pass.

- [ ] **Step 3: Confirm the baseline suite is otherwise unchanged**

Run:

```bash
./.venv/bin/python -m pytest -q
```

Expected: the pre-existing suite remains green apart from the new intentionally failing skill tests.

---

### Task 2: Implement runtime discovery and the dependency doctor

**Files:**
- Create: `skill/harvest-video-ingestion/scripts/_common.py`
- Create: `skill/harvest-video-ingestion/scripts/doctor.py`
- Test: `tests/test_portable_skill.py::test_doctor_json_never_contains_secret_values`

**Interfaces:**
- `_common.resolve_project_root(explicit: str | None) -> Path | None`
- `_common.resolve_runtime(explicit: str | None) -> Runtime`
- `_common.Runtime.command: tuple[str, ...]`, `.cwd: Path | None`, `.env: dict[str, str]`, `.kind: str`
- `doctor.py` emits `{status, project_root, checks}` where each check is `{name, status, detail}` and status is `ok`, `warn`, or `error`.

- [ ] **Step 1: Implement the minimal runtime resolver**

Use this exact discovery order and no shell command construction:

```python
@dataclass(frozen=True)
class Runtime:
    command: tuple[str, ...]
    cwd: Path | None
    env: dict[str, str]
    kind: str


def resolve_project_root(explicit: str | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if os.environ.get("HARVEST_PROJECT_ROOT"):
        candidates.append(Path(os.environ["HARVEST_PROJECT_ROOT"]).expanduser())
    here = Path.cwd().resolve()
    candidates.extend([here, *here.parents])
    for candidate in candidates:
        candidate = candidate.resolve()
        if (candidate / "harvest" / "__init__.py").is_file() and (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def resolve_runtime(explicit: str | None = None) -> Runtime:
    root = resolve_project_root(explicit)
    env = os.environ.copy()
    if root:
        old = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(root) if not old else os.pathsep.join((str(root), old))
        return Runtime((sys.executable, "-m", "harvest.cli"), root, env, "checkout")
    executable = shutil.which("harvest")
    if executable:
        return Runtime((executable,), None, env, "installed")
    raise RuntimeError("could not find a BliSolver checkout or an installed harvest command")
```

- [ ] **Step 2: Implement doctor checks without importing secrets or making HTTP calls**

Check exactly these names: `python`, `harvest`, `ffmpeg`, `javascript-runtime`, `whisper-cli`, `vision-config`, `ocr-isolate`, and `provider-auth`. Use `shutil.which` and environment-variable presence only. Never include values of `SESSDATA`, `LMSTUDIO_API_KEY`, `HARVEST_*_PROFILE`, or any token in a detail string. Treat Python/harvest as errors, and stage-specific missing tools/configuration as warnings. `--json` prints one compact JSON object; the non-JSON mode prints one human line per check.

- [ ] **Step 3: Run the focused doctor test**

Run:

```bash
./.venv/bin/python -m pytest -q tests/test_portable_skill.py::test_doctor_json_never_contains_secret_values
```

Expected: PASS, with no secret literal in stdout or stderr.

---

### Task 3: Implement probe and ingest adapters

**Files:**
- Create: `skill/harvest-video-ingestion/scripts/probe.py`
- Create: `skill/harvest-video-ingestion/scripts/ingest.py`
- Test: `tests/test_portable_skill.py::test_probe_keeps_child_json_on_stdout`
- Test: `tests/test_portable_skill.py::test_ingest_dry_run_forwards_current_flags`

**Interfaces:**
- `probe.py` accepts `URL` and optional `--project-root`; successful stdout is exactly one parsed/re-serialized JSON object.
- `ingest.py` accepts `URL`, optional `--project-root`, `--dry-run`, and the current flags: `--part`, `--all-parts`, `--force-whisper`, `--lang`, `--robust`, `--no-vision`, `--dedup-threshold`, `--scene-threshold`, `--out`, `--no-frame-images`, `--danmaku`, `--interactions`, `--ocr`, `--force-ocr`.
- Both invoke `resolve_runtime()` and `subprocess.run` with a list, `shell=False`, inherited environment, and the resolved checkout cwd.

- [ ] **Step 1: Implement `probe.py` JSON discipline**

Capture the child process with `text=True`. On success, parse `stdout` with `json.loads`; if parsing fails, write `error: harvest probe returned non-JSON stdout` and the bounded child output to stderr and return 1. On child failure, forward child stderr and return its nonzero code. On success, print `json.dumps(payload, ensure_ascii=False, separators=(",", ":"))` and forward child stderr unchanged.

- [ ] **Step 2: Implement explicit ingest flag forwarding**

Build the child command in this order:

```text
<runtime.command> ingest URL
--part N --all-parts --force-whisper --lang CODE --robust --no-vision
--dedup-threshold N --scene-threshold N --out DIR --no-frame-images
--danmaku --interactions --ocr --force-ocr
```

Omit flags whose argparse values are unset. `--dry-run` must not invoke the child; it prints `{"command": [...], "cwd": "...", "runtime": "checkout|installed"}` and returns 0. Normal mode forwards child stdout/stderr and returns the exact child return code.

- [ ] **Step 3: Run the two focused tests**

Run:

```bash
./.venv/bin/python -m pytest -q \
  tests/test_portable_skill.py::test_probe_keeps_child_json_on_stdout \
  tests/test_portable_skill.py::test_ingest_dry_run_forwards_current_flags
```

Expected: PASS. The probe test must execute only its temporary fake `harvest` executable; it must not contact a real provider.

---

### Task 4: Implement local bundle inspection and validation

**Files:**
- Create: `skill/harvest-video-ingestion/scripts/inspect_bundle.py`
- Create: `skill/harvest-video-ingestion/scripts/validate_bundle.py`
- Test: `tests/test_portable_skill.py::test_inspect_bundle_returns_compact_counts`
- Test: `tests/test_portable_skill.py::test_validate_bundle_accepts_and_rejects_artifacts`

**Interfaces:**
- Both accept either a bundle directory or a direct `bundle.json` path; a directory resolves to `<directory>/bundle.json`.
- `inspect_bundle.py` prints a JSON object with keys `bundle_path`, `schema_version`, `identity`, `transcript`, `ocr`, `frames`, `danmaku`, `interactions`, and `artifacts`.
- `validate_bundle.py` prints `{valid, bundle_path, schema_version, errors, warnings}` and exits 0/1/2 as defined in the design.

- [ ] **Step 1: Implement safe bundle path resolution**

Reject missing paths, non-JSON files, and directories without `bundle.json` with a usage/runtime exit 2. Load UTF-8 JSON only; do not modify any input file.

- [ ] **Step 2: Implement the compact inspector**

Count without exposing bodies:

- transcript source, language, and segment count;
- OCR segment count;
- frame count, frames with caption, frames with OCR, frames with a non-null image path, and missing referenced image count;
- danmaku window count, line count, and fetched total;
- interaction vote and grade counts.

When checking a frame path, resolve it relative to the bundle directory and count paths outside the directory as missing/suspicious rather than reading them.

- [ ] **Step 3: Implement current-schema validation**

With `--project-root`, prepend the checkout to `sys.path` and import `harvest.schema.Bundle` and `SCHEMA_VERSION`. Validate the parsed object through `Bundle.model_validate`. Add explicit errors when:

1. `schema_version != SCHEMA_VERSION` (currently `1.1`);
2. `bundle.md` is missing;
3. a non-null frame path is absolute, escapes the bundle directory, or points to a missing file.

Return all errors in one report. A valid Pydantic object with a missing frame file is invalid; a `null` frame path is valid for `--no-frame-images` bundles.

- [ ] **Step 4: Run the focused inspector/validator tests**

Run:

```bash
./.venv/bin/python -m pytest -q \
  tests/test_portable_skill.py::test_inspect_bundle_returns_compact_counts \
  tests/test_portable_skill.py::test_validate_bundle_accepts_and_rejects_artifacts
```

Expected: PASS for the valid temporary bundle and exit 1 with a `missing` error for the broken bundle.

---

### Task 5: Write the portable skill manifest and reference set

**Files:**
- Create: `skill/harvest-video-ingestion/SKILL.md`
- Create: `skill/harvest-video-ingestion/LICENSE.txt`
- Create: `skill/harvest-video-ingestion/references/architecture.md`
- Create: `skill/harvest-video-ingestion/references/current-contract.md`
- Create: `skill/harvest-video-ingestion/references/provider-guide.md`
- Create: `skill/harvest-video-ingestion/references/pipeline-stages.md`
- Create: `skill/harvest-video-ingestion/references/operational-runbook.md`
- Create: `skill/harvest-video-ingestion/references/domain-glossary.md`
- Create: `skill/harvest-video-ingestion/references/source-map.md`
- Test: `tests/test_portable_skill.py::test_manifest_lists_valid_skill_files`

**Interfaces:**
- `SKILL.md` frontmatter must contain exactly `name: harvest-video-ingestion`, a trigger-oriented `description`, `license: MIT`, a compatibility statement, and string metadata for project/schema/platforms.
- All reference links are relative and directly linked from `SKILL.md`; no reference requires a second undocumented hop.

- [ ] **Step 1: Write the manifest frontmatter and concise operating body**

Use this frontmatter:

```yaml
---
name: harvest-video-ingestion
description: Operate the BliSolver harvest pipeline for bilibili.com and YouTube videos, diagnose its runtime, run probe or ingest, and inspect or validate Atlas bundle outputs. Use when an Agent needs video acquisition, caption-versus-Whisper decisions, frame/vision/OCR processing, danmaku or interaction provenance, provider troubleshooting, or schema-1.1 bundle handling; do not use it for downstream summarization or entity extraction.
license: MIT
compatibility: Requires Python 3.11+ and either a BliSolver checkout or an installed harvest command; media stages additionally require their documented external tools and services.
metadata:
  project: BliSolver
  bundle-schema: "1.1"
  platforms: "bilibili.com, youtube.com"
---
```

The body must include: the current-truth precedence rule; the URL → provider → subtitle/Whisper → frames/vision → optional OCR/danmaku/interactions → bundle flow; the command table; the rule to run `doctor.py` before expensive work; the `bilibili.tv` warning; the authority order `human-sub > whisper > auto-sub`; and a reference-loading table matching the seven files.

- [ ] **Step 2: Fill references with current facts**

Use these required headings:

- `architecture.md`: “Purpose”, “Current module map”, “Execution flow”, “MCP boundary”, “What is not harvest’s job”.
- `current-contract.md`: “CLI verbs”, “Bundle schema 1.1”, “Transcript provenance”, “Optional tracks”, “Output layout”, “Stable versus volatile fields”.
- `provider-guide.md`: “Provider selection”, “bilibili.com”, “YouTube”, “Subtitle decision”, “Authentication”, “Deferred bilibili.tv”.
- `pipeline-stages.md`: “Cache identity”, “Audio and whisper.cpp”, “Frames and phash”, “Vision projector check”, “Hard-subtitle OCR”, “Fusion”, “Danmaku”, “Interactions”.
- `operational-runbook.md`: “Preflight”, “Cheap probe”, “Ingest recipes”, “Bundle QA”, “Failure matrix”, “Secret handling”.
- `domain-glossary.md`: “Atlas”, “bundle.md”, “bundle.json”, “human-sub/auto-sub/whisper”, “soft subtitles/hardsubs”, “danmaku/interactions”, “authority”.
- `source-map.md`: “Canonical current sources”, “Tests as executable truth”, “Historical documents”, “Known stale statements”.

Each reference must distinguish current implementation from historical plans. Specifically mention `harvest/transcribe.py`’s `whisper-cli` backend, schema 1.1, optional OCR, `harvest mcp`, and the `.tv` guard.

- [ ] **Step 3: Copy the license without modification**

Run:

```bash
cp LICENSE skill/harvest-video-ingestion/LICENSE.txt
```

Then verify the files are byte-identical:

```bash
cmp LICENSE skill/harvest-video-ingestion/LICENSE.txt
```

- [ ] **Step 4: Run the manifest test**

Run:

```bash
./.venv/bin/python -m pytest -q tests/test_portable_skill.py::test_manifest_lists_valid_skill_files
```

Expected: PASS, with every reference path present.

---

### Task 6: Validate, review, and document the finished artifact

**Files:**
- Modify only if validation exposes a concrete documentation or wrapper defect: files under `skill/harvest-video-ingestion/` or `tests/test_portable_skill.py`.
- Do not stage or modify: `scripts/download_video.py`.

**Interfaces:**
- All five user-facing scripts respond to `--help`.
- The artifact can be copied as a directory to `.agents/skills/harvest-video-ingestion/` without changing its internal relative links.

- [ ] **Step 1: Run the complete skill test file**

Run:

```bash
./.venv/bin/python -m pytest -q tests/test_portable_skill.py
```

Expected: all new tests pass.

- [ ] **Step 2: Run the project suite**

Run:

```bash
./.venv/bin/python -m pytest -q
```

Expected: all existing tests plus the new skill tests pass; no live tests run by default.

- [ ] **Step 3: Validate skill format**

If available, run:

```bash
skills-ref validate skill/harvest-video-ingestion
```

Expected: no validation errors. If `skills-ref` is unavailable, record that limitation and still run the manifest/link checks and all script tests.

- [ ] **Step 4: Perform offline smoke checks**

Run:

```bash
for script in doctor.py probe.py ingest.py inspect_bundle.py validate_bundle.py; do
  ./.venv/bin/python skill/harvest-video-ingestion/scripts/$script --help >/dev/null
 done
./.venv/bin/python skill/harvest-video-ingestion/scripts/doctor.py --project-root . --json
./.venv/bin/python skill/harvest-video-ingestion/scripts/ingest.py \
  https://example.invalid/video --project-root . --dry-run --no-vision
./.venv/bin/python skill/harvest-video-ingestion/scripts/inspect_bundle.py out/BV111o6BAEg4-p1
./.venv/bin/python skill/harvest-video-ingestion/scripts/validate_bundle.py \
  out/BV111o6BAEg4-p1 --project-root .
```

Expected: help succeeds; doctor emits JSON without secret values; ingest dry-run emits a JSON command and does not contact the network; inspection and validation succeed for the existing schema-1.1 sample if its referenced artifacts are present.

- [ ] **Step 5: Check repository cleanliness and review the diff**

Run:

```bash
git diff --check
git status --short
git diff --stat
git diff -- skill/harvest-video-ingestion tests/test_portable_skill.py
```

Expected: only the new skill, its tests, and the two approved design/plan documents appear; the pre-existing `?? scripts/download_video.py` remains unmodified and unstaged.

- [ ] **Step 6: Request a code review before reporting completion**

Provide the reviewer with the design document, implementation plan, test output, and the complete new skill tree. Ask specifically for checks on portability outside the checkout, secret leakage, subprocess safety, schema 1.1 accuracy, historical-document labeling, and Agent Skills frontmatter/link validity. Fix all Critical and Important findings, then rerun Steps 1–5.

## Self-review checklist

- [ ] Every requirement in the approved design has a corresponding task.
- [ ] The skill does not claim that the current backend is faster-whisper/CUDA.
- [ ] `bilibili.tv` remains explicitly unsupported.
- [ ] `doctor`, inspection, and validation do not make network calls.
- [ ] `probe` stdout remains parseable JSON.
- [ ] Bundle path traversal and missing frame images are reported.
- [ ] Secrets never appear in doctor output or test output.
- [ ] References use direct, relative links and are all present.
- [ ] Existing user work remains untouched.
