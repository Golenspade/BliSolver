"""A relocated checkout must expose the same skill and executable runtime entry."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = Path("skills/blisolver-video-ingestion")
ALIAS_PATH = Path(".agents/skills/blisolver-video-ingestion")


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    target = tmp_path / "fresh checkout with spaces"
    target.mkdir()
    for name in ("plugin.json", "mcp.json", "README.md", "README_zh.md", "AGENTS.md"):
        shutil.copy2(ROOT / name, target / name)
    for name in ("blisolver", "bin", "skills", ".agents"):
        shutil.copytree(
            ROOT / name, target / name, symlinks=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    return target


def _run(checkout: Path, entry: Path, *args: str, ready: bool = True):
    # A fresh clone contains no venv or credentials. Inject only the test interpreter when ready.
    env = {
        "PATH": "", "PYTHONDONTWRITEBYTECODE": "1",
        "BLISOLVER_DATA_DIR": str(checkout.parent / "generated"),
    }
    if ready:
        env["BLISOLVER_PYTHON"] = sys.executable
    return subprocess.run(
        [sys.executable, str(checkout / entry / "scripts/blisolver_cli.py"), *args],
        cwd=checkout.parent, env=env, capture_output=True, text=True, check=False,
    )


def test_project_and_plugin_discovery_share_one_skill_after_relocation(checkout: Path):
    alias = checkout / ALIAS_PATH
    canonical = checkout / SKILL_PATH
    assert alias.is_symlink()
    assert not Path(os.readlink(alias)).is_absolute()
    assert alias.resolve() == canonical.resolve()
    assert (alias / "SKILL.md").read_bytes() == (canonical / "SKILL.md").read_bytes()
    assert (alias / "scripts/_runtime.py").resolve().is_relative_to(checkout)


@pytest.mark.parametrize("entry", [SKILL_PATH, ALIAS_PATH])
def test_relocated_skill_runs_outside_checkout_with_exact_arguments(checkout: Path, entry: Path):
    # This URL is only an inert argument fixture: show-command performs no provider call.
    args = (
        "ingest", "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
        "--no-vision", "--no-frame-images", "--lang", "zh", "--json",
    )
    result = _run(checkout, entry, "--show-command", *args)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["cwd"] == str(checkout)
    assert payload["command"] == [sys.executable, "-m", "blisolver.cli", *args]
    assert not (checkout.parent / "generated").exists()

    help_result = _run(checkout, entry, "ingest", "--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "--no-vision" in help_result.stdout


def test_cold_clone_returns_setup_instructions_instead_of_guessing_python(checkout: Path):
    result = _run(checkout, ALIAS_PATH, "doctor", "--json", ready=False)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "no interpreter" in result.stderr
    assert "uv pip install" in result.stderr
    assert str(checkout) in result.stderr
    setup_line = next(line.strip() for line in result.stderr.splitlines() if "uv venv" in line)
    assert shlex.split(setup_line) == [
        "uv", "venv", str(checkout / ".venv"), "&&", "uv", "pip", "install", "--python",
        str(checkout / ".venv/bin/python"), "-e", f"{checkout}[mcp]",
    ]
    assert not (checkout / ".venv").exists()
