"""Behavior of the skill's bundled scripts.

Packaging conformance lives in `tests/test_agent_plugin.py`. What this file guards is the thing
that actually broke before: a wrapper that looked healthy in isolation while being unable to run
the application it wrapped, or describing a CLI surface the application no longer had.

Two regression guards carry most of the weight here:

* `test_show_command_targets_the_project_environment` — the wrapper must not launch the pipeline
  with the interpreter that happens to be running it. Doing so turned a perfectly good checkout
  into `ModuleNotFoundError: No module named 'dotenv'` whenever an agent invoked the scripts with a
  bare `python3`.
* `test_passthrough_forwards_unknown_flags_verbatim` — the wrapper must not re-declare the CLI's
  flags. The old adapter rebuilt the child command from its own argparse namespace, so any flag it
  had not been taught about was silently dropped on the floor.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL = PLUGIN_ROOT / "skills" / "blisolver-video-ingestion"
SCRIPTS = SKILL / "scripts"
LAUNCHER = PLUGIN_ROOT / "bin" / "blisolver-mcp"
PROJECT_VENV_PYTHON = PLUGIN_ROOT / ".venv" / "bin" / "python"

PUBLIC_SCRIPTS = {"blisolver_cli.py", "inspect_bundle.py", "validate_bundle.py"}
INTERNAL_SCRIPTS = {"_runtime.py"}


def run_script(name: str, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None):
    """Run a bundled script the way an agent would: by path, with whatever python is at hand."""
    merged = os.environ.copy()
    merged.pop("PLUGIN_DATA", None)  # only an MCP client supplies this; keep runs deterministic
    if env:
        merged.update({k: v for k, v in env.items() if v is not None})
        for k, v in env.items():
            if v is None:
                merged.pop(k, None)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        cwd=str(cwd or PLUGIN_ROOT),
        env=merged,
        capture_output=True,
        text=True,
    )


def write_bundle(root: Path, *, frame_path: str | None = "frames/000.png", create_frame=True):
    """A minimal schema-valid bundle. `schema_version` is read from the application so this fixture
    cannot drift from the contract it is meant to satisfy."""
    sys.path.insert(0, str(PLUGIN_ROOT))
    from blisolver.schema import SCHEMA_VERSION

    bundle_dir = root / "bundle"
    bundle_dir.mkdir(parents=True)
    if frame_path and create_frame and not frame_path.startswith("../"):
        frame = bundle_dir / frame_path
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"png-fixture")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "platform": "youtube.com",
        "id": "fixture-id",
        "part": 1,
        "url": "https://www.youtube.com/watch?v=fixture-id",
        "fetched_at": "2026-08-10T00:00:00Z",
        "transcript": {
            "source": "whisper",
            "source_reason": "fixture",
            "language": "en",
            "model": "fixture-model",
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello", "source": "whisper"}],
        },
        "ocr": [{"start": 0.0, "end": 1.0, "text": "HELLO", "source": "ocr"}],
        "frames": [
            {"ts": 0.0, "path": frame_path, "phash": "00", "caption": "slide", "ocr": "HELLO"}
        ],
        "danmaku": {
            "fetched_total": 2,
            "windows": [{"start": 0, "end": 15, "total": 2, "lines": [{"text": "hi"}]}],
        },
        "interactions": {
            "votes": [{"question": "Q", "options": [], "total_count": 0}],
            "grades": [{"avg_score": 8.0, "count": 2}],
        },
        "meta": {"cookies_used": False, "referer_used": False, "tool_version": "0.1.0"},
    }
    (bundle_dir / "bundle.json").write_text(json.dumps(payload), encoding="utf-8")
    (bundle_dir / "bundle.md").write_text("# fixture\n", encoding="utf-8")
    return bundle_dir


# --- script inventory ----------------------------------------------------------------------


def test_script_inventory_is_exactly_the_documented_set():
    """Four scripts, one of them internal. The three superseded adapters (`doctor.py`, `probe.py`,
    `ingest.py`) are gone: doctor moved into the application as `blisolver doctor`, and the other
    two were flag mirrors replaced by pass-through."""
    present = {p.name for p in SCRIPTS.glob("*.py")}
    assert present == PUBLIC_SCRIPTS | INTERNAL_SCRIPTS


@pytest.mark.parametrize("name", sorted(PUBLIC_SCRIPTS))
def test_public_scripts_document_their_usage(name):
    result = run_script(name, "--help")
    assert result.returncode == 0, result.stderr
    assert "usage" in result.stdout.lower()


# --- runtime resolution (the A1 regression) ------------------------------------------------


def test_show_command_targets_the_project_environment():
    """The resolved child interpreter is the project environment, never the interpreter running
    the wrapper. This is the assertion the previous suite got backwards: it asserted
    `command[0] == sys.executable`, which is exactly the bug."""
    result = run_script("blisolver_cli.py", "--show-command", "doctor", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["command"][0] == str(PROJECT_VENV_PYTHON)
    assert payload["command"][1:3] == ["-m", "blisolver.cli"]
    assert payload["runtime"] == "project-venv"
    assert payload["cwd"] == str(PLUGIN_ROOT)


def test_wrapper_runs_the_pipeline_under_an_unrelated_interpreter():
    """End-to-end proof of the fix: invoke the wrapper with the system python, which has none of
    blisolver's dependencies, and the child still runs in the project environment.

    The load-bearing assertion is the child's own `interpreter` check, not a path comparison made
    from this process. `doctor._interpreter_check` compares `sys.prefix` against the `.venv` beside
    the package it was imported from, so the child answers "am I in the project environment?"
    using only paths it resolved itself. Comparing `report["interpreter"]` to a path built here
    instead breaks whenever the checkout or the environment sits behind a symlink, and resolving
    both sides collapses them onto the base interpreter, which would let any virtual environment
    pass.
    """
    system_python = Path("/usr/bin/python3")
    if not system_python.exists():
        pytest.skip("no /usr/bin/python3 on this platform")
    result = subprocess.run(
        [str(system_python), str(SCRIPTS / "blisolver_cli.py"), "doctor", "--json"],
        cwd=str(PLUGIN_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode in (0, 1), result.stderr
    report = json.loads(result.stdout)

    interpreter_check = next(c for c in report["checks"] if c["name"] == "interpreter")
    assert interpreter_check["status"] == "ok", (
        f"child did not run in the project environment: {interpreter_check['detail']}"
    )
    child = Path(report["interpreter"])
    assert child.resolve() != system_python.resolve(), (
        "the pipeline ran under the interpreter that launched the wrapper"
    )
    assert child.parts[-3:] == (".venv", "bin", "python"), (
        f"unexpected child interpreter shape: {child}"
    )


def test_explicit_interpreter_override_wins():
    result = run_script(
        "blisolver_cli.py", "--show-command", "doctor", env={"BLISOLVER_PYTHON": sys.executable}
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["command"][0] == sys.executable
    assert payload["runtime"] == "explicit"


def test_missing_interpreter_produces_actionable_guidance(tmp_path):
    """A plugin root with no environment must say how to create one, not raise an import error."""
    fake_root = tmp_path / "plugin"
    fake_root.mkdir()
    (fake_root / "plugin.json").write_text('{"name": "fake"}', encoding="utf-8")
    result = run_script(
        "blisolver_cli.py",
        "--plugin-root",
        str(fake_root),
        "doctor",
        env={"BLISOLVER_PYTHON": None, "PATH": "/nonexistent"},
    )
    assert result.returncode == 2
    assert "no interpreter" in result.stderr
    assert "uv venv" in result.stderr
    assert str(fake_root) in result.stderr


def test_plugin_root_is_derived_from_fixed_package_depth():
    """Agent Plugins §7.1 fixes skills at `skills/<name>/`, so a script's distance to the plugin
    root is a constant rather than something to search for."""
    sys.path.insert(0, str(SCRIPTS))
    import _runtime

    assert _runtime.PLUGIN_ROOT == PLUGIN_ROOT
    assert _runtime.plugin_root() == PLUGIN_ROOT


def test_launcher_and_wrapper_share_one_interpreter_chain():
    """`bin/blisolver-mcp` and `_runtime.py` must agree, or the MCP surface and the skill surface
    will disagree about whether the environment works."""
    launcher = LAUNCHER.read_text(encoding="utf-8")
    markers = ["BLISOLVER_PYTHON", "PLUGIN_DATA/venv/bin/python", ".venv/bin/python"]
    positions = [launcher.index(m) for m in markers]
    assert positions == sorted(positions), "launcher chain order differs from _runtime.py"

    sys.path.insert(0, str(SCRIPTS))
    import _runtime

    kinds = [
        kind
        for kind, _ in _runtime._interpreter_candidates(PLUGIN_ROOT)
    ]
    assert kinds[-1] == "project-venv"
    assert "explicit" not in kinds or kinds[0] == "explicit"


# --- pass-through (the flag-mirror regression) ---------------------------------------------


def test_passthrough_forwards_unknown_flags_verbatim():
    """The wrapper knows nothing about pipeline flags and must stay that way. A flag invented here
    has to arrive at the child untouched, in order."""
    invented = ["ingest", "https://example.invalid/v", "--a-flag-the-wrapper-never-heard-of", "7"]
    result = run_script("blisolver_cli.py", "--show-command", *invented)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["command"][3:] == invented


def test_passthrough_does_not_rewrite_urls():
    """URL normalization belongs to `blisolver/resolve.py`; the wrapper used to carry a second copy
    of it. Raw text, tracking parameters, and embed links all reach the application intact."""
    raw = "【标题】 https://www.bilibili.com/video/BV1x2T463E7L/?spm_id_from=333"
    result = run_script("blisolver_cli.py", "--show-command", "probe", raw)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["command"][-1] == raw


def test_wrapper_flags_stop_at_the_first_passthrough_token():
    """`--show-command` after the verb belongs to the child, not the wrapper."""
    result = run_script("blisolver_cli.py", "doctor", "--show-command")
    # Forwarded to the CLI, which rejects it; the wrapper must not have intercepted it.
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr or "error" in result.stderr.lower()


def test_wrapper_preserves_child_exit_code():
    result = run_script("blisolver_cli.py", "probe", "https://example.invalid/not-a-video")
    assert result.returncode == 1
    assert "error:" in result.stderr


# --- doctor, now a CLI verb ----------------------------------------------------------------


def test_doctor_reports_every_stage_gate():
    result = run_script("blisolver_cli.py", "doctor", "--json")
    assert result.returncode in (0, 1), result.stderr
    report = json.loads(result.stdout)
    names = {c["name"] for c in report["checks"]}
    # The three the previous doctor was missing entirely.
    assert {"whisper-model", "danmaku-model", "aria2c"} <= names
    assert {c["stage"] for c in report["checks"]} <= {
        "core", "transcript", "vision", "ocr", "danmaku", "auth"
    }


def test_doctor_never_prints_credential_values():
    secrets = {
        "SESSDATA": "secret-sessdata-value",
        "LMSTUDIO_API_KEY": "secret-apikey-value",
    }
    result = run_script("blisolver_cli.py", "doctor", "--json", env=secrets)
    combined = result.stdout + result.stderr
    for value in secrets.values():
        assert value not in combined
    assert "SESSDATA is configured" in result.stdout


# --- bundle QA -----------------------------------------------------------------------------


def test_inspect_bundle_returns_compact_counts(tmp_path):
    bundle = write_bundle(tmp_path)
    result = run_script("inspect_bundle.py", str(bundle))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["identity"]["id"] == "fixture-id"
    assert payload["transcript"]["segments"] == 1
    assert payload["ocr"]["segments"] == 1
    assert payload["frames"] == {
        "count": 1,
        "with_caption": 1,
        "with_ocr": 1,
        "with_image_path": 1,
        "missing_images": 0,
    }
    assert payload["danmaku"] == {"windows": 1, "lines": 1, "fetched_total": 2}
    assert payload["interactions"] == {"votes": 1, "grades": 1}


def test_validate_bundle_accepts_and_rejects_artifacts(tmp_path):
    valid = write_bundle(tmp_path / "valid")
    accepted = run_script("validate_bundle.py", str(valid))
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert json.loads(accepted.stdout)["valid"] is True

    broken = write_bundle(tmp_path / "broken", frame_path="frames/missing.png", create_frame=False)
    rejected = run_script("validate_bundle.py", str(broken))
    assert rejected.returncode == 1
    report = json.loads(rejected.stdout)
    assert report["valid"] is False
    assert any("missing" in e.lower() for e in report["errors"])


def test_validate_bundle_rejects_frame_path_traversal(tmp_path):
    bundle = write_bundle(tmp_path / "traversal", frame_path="../outside.png")
    result = run_script("validate_bundle.py", str(bundle))
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["valid"] is False
    assert any("outside" in e.lower() for e in report["errors"])


def test_validate_bundle_reads_the_live_schema_version(tmp_path):
    """The validator must import SCHEMA_VERSION from the application rather than pin a literal, so
    a schema bump cannot leave it asserting a version the pipeline no longer emits."""
    source = (SCRIPTS / "validate_bundle.py").read_text(encoding="utf-8")
    assert "SCHEMA_VERSION" in source
    assert not re.search(r'expected_schema\s*=\s*["\']\d+\.\d+', source)

    bundle = write_bundle(tmp_path)
    data = json.loads((bundle / "bundle.json").read_text())
    data["schema_version"] = "0.9"
    (bundle / "bundle.json").write_text(json.dumps(data), encoding="utf-8")
    result = run_script("validate_bundle.py", str(bundle))
    assert result.returncode == 1
    assert any("schema_version" in e for e in json.loads(result.stdout)["errors"])
