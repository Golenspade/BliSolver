"""Declarations must match what the code actually uses.

Two defects motivated this file, both found by comparing a declaration against the imports:

* `pyproject.toml` declared a `transcribe` extra pulling faster-whisper and three NVIDIA CUDA
  wheels. Nothing has imported `faster_whisper` since local ASR moved to whisper.cpp, so
  `pip install .[transcribe]` downloaded hundreds of megabytes to satisfy no import.
* the `frames` extra declared `scenedetect`, also never imported — periodic sampling with
  perceptual-hash dedup replaced scene-cut detection, and only a stale comment still referred to it.

`.env.example` has the same failure mode in the other direction: a variable the code reads but the
template omits is a setting nobody knows to set, and a variable the template lists but nothing reads
is an instruction that does nothing. `BLISOLVER_WHISPER_MODEL` was in the first category — the
setting `doctor` tells you to fix, absent from the file you copy.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# Distribution name -> module name, for the cases where they differ.
IMPORT_NAME = {
    "pillow": "PIL",
    "python-dotenv": "dotenv",
    "pyyaml": "yaml",
    "yt-dlp": "yt_dlp",
    "faster-whisper": "faster_whisper",
}

# Declared for the test suite and tooling rather than imported by the application.
TOOLING_ONLY = {"pytest", "jsonschema", "ruff"}

ENV_NAMES = r"BLISOLVER_[A-Z_]+|LMSTUDIO_[A-Z_]+|SESSDATA|FFMPEG_PATH"
ENV_PATTERN = rf"\b({ENV_NAMES})\b"


def manifest() -> dict:
    return tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def declared_requirements() -> dict[str, list[str]]:
    """{group: [distribution names]} for the base dependencies and every extra."""
    project = manifest()["project"]
    groups = {"dependencies": project.get("dependencies", [])}
    groups.update(project.get("optional-dependencies", {}))
    return {
        name: [re.split(r"[<>=!~;\[ ]", spec, maxsplit=1)[0].strip().lower() for spec in specs]
        for name, specs in groups.items()
    }


def application_source() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8") for p in (PLUGIN_ROOT / "blisolver").rglob("*.py")
    )


def is_imported(distribution: str, source: str) -> bool:
    module = IMPORT_NAME.get(distribution, distribution.replace("-", "_"))
    return re.search(rf"^\s*(?:import|from)\s+{re.escape(module)}\b", source, re.M) is not None


@pytest.mark.parametrize(
    "group,distribution",
    [(g, d) for g, ds in declared_requirements().items() for d in ds if d not in TOOLING_ONLY],
    ids=lambda v: v,
)
def test_every_declared_dependency_is_imported(group: str, distribution: str):
    """A declared package the application never imports makes an install pay for nothing."""
    assert is_imported(distribution, application_source()), (
        f"pyproject declares {distribution!r} under [{group}] but nothing in blisolver/ imports it"
    )


def test_no_extra_declares_an_absent_backend():
    """Local ASR is an external binary, so no extra can install it. Declaring one implies it can."""
    extras = manifest()["project"].get("optional-dependencies", {})
    assert "transcribe" not in extras, (
        "whisper.cpp is an external binary plus a weights file; an extra cannot provide it, and the "
        "previous one installed CUDA wheels for an import that no longer exists"
    )


def test_env_example_documents_every_variable_the_code_reads():
    source = application_source() + (PLUGIN_ROOT / "bin" / "blisolver-mcp").read_text(
        encoding="utf-8"
    )
    read = set(re.findall(ENV_PATTERN, source))
    template = (PLUGIN_ROOT / ".env.example").read_text(encoding="utf-8")
    missing = sorted(name for name in read if name not in template)
    assert not missing, (
        f".env.example omits variables the code reads: {missing}. "
        f"A setting absent from the template is one nobody knows to set."
    )


def test_env_example_documents_nothing_the_code_ignores():
    source = application_source() + (PLUGIN_ROOT / "bin" / "blisolver-mcp").read_text(
        encoding="utf-8"
    )
    read = set(re.findall(ENV_PATTERN, source))
    template = (PLUGIN_ROOT / ".env.example").read_text(encoding="utf-8")
    listed = set(re.findall(rf"^#?\s*({ENV_NAMES})=", template, re.M))
    orphans = sorted(listed - read)
    assert not orphans, (
        f".env.example lists variables nothing reads: {orphans}. "
        f"Either wire them up or remove them."
    )


def test_plugin_manifest_and_package_agree_on_the_name():
    """The plugin name, the distribution name, and the console script are one identity."""
    import json

    plugin = json.loads((PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8"))
    project = manifest()["project"]
    assert plugin["name"] == project["name"]
    assert project["scripts"] == {plugin["name"]: "blisolver.cli:main"}


def test_plugin_manifest_points_at_this_repository():
    """The manifest URLs were left pointing at a separate skill-only repository, which published
    the skill without the application. A reader following them would not find this code."""
    import json

    plugin = json.loads((PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8"))
    for field in ("homepage", "repository"):
        url = plugin.get(field, "")
        assert url.rstrip("/").lower().endswith("/blisolver"), (
            f"plugin.json {field} is {url!r}, which does not point at the BliSolver repository"
        )



# --- documented setup commands must be runnable -------------------------------------------

DOCS = [
    PLUGIN_ROOT / "README.md",
    PLUGIN_ROOT / "README_zh.md",
    *(PLUGIN_ROOT / "skills").rglob("*.md"),
]


def test_no_doc_pairs_uv_venv_with_the_venvs_own_pip():
    """`uv venv` does not seed pip, so `<venv>/bin/pip` does not exist after it.

    Four documented setup commands were this exact shape, including the OCR sandbox instructions in
    both READMEs and the bootstrap command printed by the skill's own error message. All of them
    failed at the second line. `python3 -m venv` does seed pip, so that pairing is fine and this
    check has to tell the two apart rather than banning `bin/pip` outright.
    """
    offenders = []
    for path in DOCS:
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "uv venv" not in line:
                continue
            # The bad pattern is either on this line or in the couple of lines that follow it.
            window = " ".join(lines[index : index + 3])
            if re.search(r"(?<!uv )pip install", window) and "-m venv" not in window:
                if re.search(r"bin/pip|python -m pip|bin/python -m pip", window):
                    offenders.append(f"{path.relative_to(PLUGIN_ROOT)}:{index + 1}: {line.strip()}")
    assert not offenders, (
        "`uv venv` leaves no pip in the environment; use `uv pip install --python <venv>/bin/python`:\n"
        + "\n".join(offenders)
    )


def test_documented_extras_exist():
    """A doc offering `pip install .[transcribe]` sends a reader to an extra that was removed.

    Anchored on lines that actually install something: `[...]` appears all over CLI usage lines as
    a placeholder (`blisolver ingest <url> [options]`), and treating those as extras produced false
    failures on the first attempt.
    """
    declared = set(manifest()["project"].get("optional-dependencies", {}))
    offenders = []
    for path in DOCS:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "pip install" not in line:
                continue
            for match in re.finditer(r"\[([a-z][a-z,]*)\]", line):
                for extra in (e.strip() for e in match.group(1).split(",")):
                    if extra and extra not in declared:
                        offenders.append(
                            f"{path.relative_to(PLUGIN_ROOT)}:{number}: [{extra}]"
                        )
    assert not offenders, (
        f"docs reference extras that pyproject does not declare {sorted(declared)}: "
        f"{sorted(set(offenders))}"
    )


def test_readme_states_the_real_default_cookie_browser():
    """The README claimed Firefox while config.py has defaulted to chrome since 2026-07. A reader
    setting up the wrong browser gets no bilibili AI subtitle tracks and silent Whisper fallback."""
    from blisolver.config import Settings

    default = Settings().cookies_browser
    for name in ("README.md", "README_zh.md"):
        text = (PLUGIN_ROOT / name).read_text(encoding="utf-8")
        section = text[: text.find("## 📦")] if "## 📦" in text else text
        assert default.lower() in section.lower(), (
            f"{name} does not name the actual default cookie browser ({default!r})"
        )



# --- the MCP dependency must resolve to a version the code can use ------------------------


def test_mcp_server_builds_against_the_installed_mcp():
    """Whatever `mcp` resolves to, `build_server()` must actually work.

    This calls the real SDK 2.x constructor, so an upstream API break surfaces here rather than in
    a user's first session.
    """
    pytest.importorskip("mcp", reason="the mcp extra is not installed in this environment")

    from blisolver import __version__
    from blisolver.config import Settings
    from blisolver.mcp.server import build_server

    server = build_server(Settings())
    assert server.name == "blisolver"
    assert server.version == __version__


def test_mcp_dependency_selects_only_the_supported_sdk_major():
    """The server uses the stable SDK 2.x API and must not resolve an incompatible major."""
    specs = manifest()["project"]["optional-dependencies"]["mcp"]
    spec = next(s for s in specs if s.lower().startswith("mcp"))
    supported = Requirement(spec).specifier

    assert "2.0.0" in supported
    assert "1.999.999" not in supported
    assert "3.0.0" not in supported
    assert "3.0.1" not in supported
    assert "3.1.0" not in supported
    assert "4.0.0" not in supported
