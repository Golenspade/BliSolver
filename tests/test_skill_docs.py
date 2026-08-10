"""Documentation-versus-code agreement, enforced as tests.

This file exists because the drift it guards against was the original defect. The skill package
described a CLI surface, a set of helper scripts, an environment-variable vocabulary, and an output
path that the application had all moved past. Every individual claim looked plausible; nothing
failed until someone ran a documented command and it did not work.

The tests below extract facts from the code at run time and assert the prose agrees. None of them
restate a fact — a test that hardcodes the flag list would just be a third copy to drift.

What each test would have caught, historically:

* `test_every_ingest_flag_is_documented` — the wrapper's hand-maintained flag mirror silently
  dropped flags it had not been taught.
* `test_documented_env_vars_are_read_somewhere` — `HARVEST_PROJECT_ROOT` survived a project rename
  and `BLISOLVER_PROJECT_ROOT` outlived the code that read it.
* `test_documented_scripts_exist` — docs pointed at `doctor.py`, `probe.py`, and `ingest.py` after
  they were deleted.
* `test_no_doc_presents_the_old_output_path_as_an_instruction` — three files documented
  `out/<id>-p<part>/`, a directory that stopped existing when the delivery name gained a title.
* `test_command_examples_use_an_interpreter_that_exists` — every example invoked `python`, which is
  absent on macOS.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL = PLUGIN_ROOT / "skills" / "blisolver-video-ingestion"
SCRIPTS = SKILL / "scripts"
DOCS = sorted(SKILL.rglob("*.md"))

PUBLIC_SCRIPTS = {"blisolver_cli.py", "inspect_bundle.py", "validate_bundle.py"}

# Flags that appear in the prose but are not blisolver CLI flags. Kept explicit and small so an
# unrecognized flag is a failure rather than a silently widened net.
WRAPPER_FLAGS = {"--plugin-root", "--show-command"}          # scripts/blisolver_cli.py
SCRIPT_FLAGS = {"--pretty", "--help"}                        # inspect_bundle / argparse
FOREIGN_FLAGS = {"--max-context", "--no-context", "--list-subs"}  # whisper.cpp / yt-dlp, quoted as
#                                                                  external tool behavior


def doc_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in DOCS)


@pytest.fixture(scope="module")
def prose() -> str:
    return doc_text()


def _cli_surface() -> tuple[set[str], dict[str, set[str]]]:
    """(verbs, {verb: flags}) read from the real parser."""
    from blisolver.cli import build_parser

    parser = build_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None))
    surface = {}
    for verb, verb_parser in sub.choices.items():
        surface[verb] = {
            opt
            for action in verb_parser._actions
            for opt in action.option_strings
            if opt not in ("-h", "--help")
        }
    return set(sub.choices), surface


# --- CLI surface ---------------------------------------------------------------------------


def test_every_documented_verb_exists(prose):
    verbs, _ = _cli_surface()
    documented = set(re.findall(r"blisolver (\w+)", prose)) & {
        w for w in re.findall(r"blisolver (\w+)", prose)
    }
    unknown = {v for v in documented if v.islower()} - verbs - {"doctor", "video"}
    assert not unknown, f"docs name verbs the CLI does not have: {sorted(unknown)}"


def test_every_verb_is_documented(prose):
    verbs, _ = _cli_surface()
    missing = [v for v in verbs if f"blisolver {v}" not in prose]
    assert not missing, f"CLI verbs absent from the skill docs: {missing}"


def test_every_ingest_flag_is_documented(prose):
    """A new flag must be documented in the same change that adds it."""
    _, surface = _cli_surface()
    missing = sorted(f for f in surface["ingest"] if f"`{f}" not in prose)
    assert not missing, (
        f"ingest flags the skill never mentions: {missing}. "
        f"Add them to references/cli-contract.md and, if operationally relevant, SKILL.md."
    )


def test_no_documented_flag_is_invented(prose):
    """The mirror image: prose must not offer flags the CLI will reject."""
    _, surface = _cli_surface()
    real = set().union(*surface.values()) | WRAPPER_FLAGS | SCRIPT_FLAGS | FOREIGN_FLAGS
    mentioned = set(re.findall(r"`(--[a-z][a-z0-9-]*)", prose))
    invented = sorted(mentioned - real)
    assert not invented, f"docs offer flags that do not exist: {invented}"


def test_deprecated_flag_is_labelled_as_such(prose):
    """`--scene-threshold` is accepted and ignored. Documenting it without that fact would send a
    reader to tune a dial that does nothing."""
    _, surface = _cli_surface()
    assert "--scene-threshold" in surface["ingest"]
    window = "\n".join(l for l in prose.splitlines() if "--scene-threshold" in l)
    assert re.search(r"deprecat|ignored", window, re.I), (
        "--scene-threshold must be documented as deprecated/ignored"
    )


# --- environment vocabulary ----------------------------------------------------------------


def _code_text() -> str:
    parts = [p.read_text(encoding="utf-8") for p in (PLUGIN_ROOT / "blisolver").rglob("*.py")]
    parts.append((PLUGIN_ROOT / "bin" / "blisolver-mcp").read_text(encoding="utf-8"))
    parts.extend(p.read_text(encoding="utf-8") for p in SCRIPTS.glob("*.py"))
    parts.append((PLUGIN_ROOT / "mcp.json").read_text(encoding="utf-8"))
    return "\n".join(parts)


ENV_PATTERN = r"\b((?:BLISOLVER|LMSTUDIO|PLUGIN|FFMPEG)[A-Z_]*|SESSDATA)\b"


def test_documented_env_vars_are_read_somewhere(prose):
    """The generic guard for a renamed or retired variable.

    `HARVEST_PROJECT_ROOT` sat in the Chinese README through a whole project rename, and
    `BLISOLVER_PROJECT_ROOT` outlived the discovery code that consumed it. Both were documented
    instructions that did nothing.
    """
    documented = set(re.findall(ENV_PATTERN, prose))
    read = set(re.findall(ENV_PATTERN, _code_text()))
    orphans = sorted(documented - read)
    assert not orphans, (
        f"docs tell the reader to set variables nothing reads: {orphans}. "
        f"Either wire them up or remove them."
    )


def test_no_stale_project_prefix_survives(prose):
    """The project was renamed from Harvest. Any surviving identifier is a rename that was missed."""
    hits = sorted(set(re.findall(r"\b[Hh]arvest\w*\b|\bHARVEST_[A-Z_]+\b", prose)))
    assert not hits, f"pre-rename identifiers still in the docs: {hits}"


SECRET_NAME = re.compile(r"\b([A-Z][A-Z0-9_]*(?:SESSDATA|_KEY|_TOKEN|_SECRET|PASSWORD)|SESSDATA)\b")
# Values that are obviously not a credential: empty, a placeholder, or a variable reference.
PLACEHOLDER_VALUE = re.compile(r"^(?:$|[<\"']?(?:\.\.\.|<[^>]*>|\$\{?\w+\}?|your[-_\w]*)[\"']?$)")


def test_credentials_are_never_shown_being_passed_on_a_command_line():
    """A doc example is copied verbatim, so an example that inlines a secret teaches the leak.

    Matching on the whole line rather than on a leading `python` is deliberate: the shape a leak
    actually takes is `SESSDATA=abc123 python3 ...`, where the command is not the first token. An
    earlier version of this test anchored at the start of the line and missed exactly that.
    """
    offenders = []
    for path in DOCS:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in SECRET_NAME.finditer(line):
                name = match.group(0)
                after = line[match.end():]
                if not after.startswith("="):
                    continue  # a prose mention, not an assignment
                value = after[1:].split()[0] if after[1:].split() else ""
                if PLACEHOLDER_VALUE.match(value):
                    continue
                offenders.append(f"{path.relative_to(SKILL)}:{number}: {name}={value}")
    assert not offenders, (
        "credential assigned a literal value in the docs:\n" + "\n".join(offenders)
    )


# --- preflight -----------------------------------------------------------------------------


def test_every_doctor_check_is_documented():
    """SKILL.md tabulates the checks by stage. A new check that is not listed leaves a reader
    unable to interpret its warning."""
    from blisolver.config import Settings
    from blisolver.doctor import run

    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    missing = [c.name for c in run(Settings()).checks if c.name not in skill]
    assert not missing, f"doctor checks absent from SKILL.md: {missing}"


def test_every_doctor_stage_is_documented():
    from blisolver.config import Settings
    from blisolver.doctor import run

    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    stages = {c.stage for c in run(Settings()).checks}
    missing = [s for s in stages if f"`{s}`" not in skill]
    assert not missing, f"doctor stages absent from SKILL.md: {missing}"


# --- MCP surface ---------------------------------------------------------------------------


def _registered_tool_names() -> set[str]:
    """Tool names as the server actually registers them, not as a list someone maintained."""
    source = (PLUGIN_ROOT / "blisolver" / "mcp" / "server.py").read_text(encoding="utf-8")
    return set(re.findall(r"@s\.tool\(\)\s*\n\s*def (\w+)", source))


def test_every_mcp_tool_is_documented():
    contract = (SKILL / "references" / "mcp-contract.md").read_text(encoding="utf-8")
    tools = _registered_tool_names()
    assert tools, "failed to extract any registered tool; the extraction pattern is stale"
    missing = sorted(t for t in tools if t not in contract)
    assert not missing, f"MCP tools absent from references/mcp-contract.md: {missing}"


def test_no_documented_mcp_tool_is_invented():
    contract = (SKILL / "references" / "mcp-contract.md").read_text(encoding="utf-8")
    tools = _registered_tool_names()
    mentioned = set(re.findall(r"`(\w+)\(", contract))
    invented = sorted(mentioned - tools)
    assert not invented, f"mcp-contract.md documents tools that are not registered: {invented}"


def test_every_extract_transcript_mode_is_documented():
    from blisolver.mcp.server import _MODE_FLAGS

    contract = (SKILL / "references" / "mcp-contract.md").read_text(encoding="utf-8")
    missing = sorted(m for m in _MODE_FLAGS if f"`{m}`" not in contract)
    assert not missing, f"extract_transcript modes absent from mcp-contract.md: {missing}"


def test_documented_mode_flags_match_the_implementation():
    """The mode table lists which CLI flags each mode adds. A drifted table misleads about cost."""
    from blisolver.mcp.server import _MODE_FLAGS

    contract = (SKILL / "references" / "mcp-contract.md").read_text(encoding="utf-8")
    for mode, flags in _MODE_FLAGS.items():
        row = next((l for l in contract.splitlines() if f"`{mode}`" in l and "|" in l), None)
        assert row, f"no table row documents mode {mode!r}"
        for flag in flags:
            assert flag in row, f"mode {mode!r} adds {flag} but the row does not say so: {row}"


def test_mcp_json_server_is_documented():
    """The launcher path in mcp.json is what a client executes; docs must name the real one."""
    import json

    config = json.loads((PLUGIN_ROOT / "mcp.json").read_text(encoding="utf-8"))
    contract = (SKILL / "references" / "mcp-contract.md").read_text(encoding="utf-8")
    for name, entry in config["mcpServers"].items():
        assert name in contract, f"mcp.json server {name!r} is undocumented"
        if entry.get("type") == "stdio":
            assert entry["command"] in contract, f"launcher {entry['command']} is undocumented"


# --- output path ---------------------------------------------------------------------------


def test_documented_output_template_matches_the_writer(prose):
    """The delivery directory name is built from the sanitized title plus the identity triple.
    Docs must show that shape, because a reader who believes otherwise cannot find the bundle."""
    import inspect

    from blisolver import merge

    writer = inspect.getsource(merge.write_bundle)
    assert '[{bundle.id}-p{bundle.part}]' in writer, (
        "write_bundle no longer uses the bracketed suffix; update this test and the docs together"
    )
    assert "[<id>-p<part>]" in prose, (
        "no doc shows the real delivery directory template `<title> [<id>-p<part>]`"
    )


def test_no_doc_presents_the_old_output_path_as_an_instruction():
    """`out/<id>-p<part>/` may only appear while being disowned.

    It is worth naming as a pitfall — consumers really did derive it — but a line that presents it
    as where to look is the exact regression this suite exists to stop.
    """
    disowning = re.compile(r"broke|does not exist|not derivable|never|stopped|Do not", re.I)
    offenders = []
    for path in DOCS:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "out/<id>-p<part>" in line and not disowning.search(line):
                offenders.append(f"{path.relative_to(SKILL)}:{number}: {line.strip()}")
    assert not offenders, "old output path presented as guidance:\n" + "\n".join(offenders)


# --- scripts and references ----------------------------------------------------------------


def test_documented_scripts_exist(prose):
    referenced = set(re.findall(r"scripts/([\w.]+\.py)", prose))
    # ocr_worker.py lives in the plugin root's scripts/, not this skill's.
    referenced.discard("ocr_worker.py")
    missing = sorted(name for name in referenced if not (SCRIPTS / name).is_file())
    assert not missing, f"docs reference scripts that do not exist: {missing}"


def test_every_public_script_is_documented(prose):
    missing = sorted(s for s in PUBLIC_SCRIPTS if s not in prose)
    assert not missing, f"public scripts the docs never mention: {missing}"


def test_script_inventory_matches_the_documented_table():
    """SKILL.md carries a table of bundled scripts. It must be exhaustive."""
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    on_disk = {p.name for p in SCRIPTS.glob("*.py")}
    missing = sorted(name for name in on_disk if name not in skill)
    assert not missing, f"scripts on disk that SKILL.md does not list: {missing}"


def test_reference_links_resolve():
    missing = []
    for path in DOCS:
        for ref in re.findall(r"references/([\w.-]+\.md)", path.read_text(encoding="utf-8")):
            if not (SKILL / "references" / ref).is_file():
                missing.append(f"{path.relative_to(SKILL)} -> references/{ref}")
    assert not missing, "broken reference links:\n" + "\n".join(missing)


def test_no_reference_is_orphaned():
    """An unreferenced reference is never progressively disclosed, so it is dead weight that
    nonetheless has to be maintained."""
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    orphans = sorted(
        p.name for p in (SKILL / "references").glob("*.md") if f"references/{p.name}" not in skill
    )
    assert not orphans, f"reference files SKILL.md never points to: {orphans}"


def test_relative_links_resolve():
    for path in DOCS:
        for target in re.findall(r"\]\((?!https?:)([\w./-]+)\)", path.read_text(encoding="utf-8")):
            resolved = (path.parent / target).resolve()
            assert resolved.exists(), f"{path.relative_to(SKILL)} links to missing {target}"


# --- command examples ----------------------------------------------------------------------


def test_command_examples_use_an_interpreter_that_exists(prose):
    """macOS ships no `python`, only `python3`. Every example was written with the former and every
    one of them failed when run."""
    bare = re.findall(r"(?<![\w3])python (?=[\"'$])", prose)
    assert not bare, (
        f"{len(bare)} command example(s) invoke `python`, which does not exist on macOS; "
        f"use `python3`"
    )


def test_command_examples_go_through_the_wrapper(prose):
    """Invoking `python -m blisolver.cli` directly reintroduces the interpreter bug: the module is
    not importable under an arbitrary interpreter. Examples must route through the wrapper."""
    offenders = [
        line.strip()
        for line in prose.splitlines()
        if re.search(r"^\s*python3? -m blisolver", line)
    ]
    assert not offenders, (
        "examples bypass scripts/blisolver_cli.py:\n" + "\n".join(offenders)
    )


def test_public_scripts_are_executable_as_documented():
    """Each documented script must at least start and print usage under a plain interpreter."""
    import subprocess
    import sys

    for name in sorted(PUBLIC_SCRIPTS):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / name), "--help"],
            capture_output=True, text=True, cwd=str(PLUGIN_ROOT),
        )
        assert result.returncode == 0, f"{name} --help failed: {result.stderr}"
        assert "usage" in result.stdout.lower(), f"{name} --help printed no usage"


# --- schema and version claims -------------------------------------------------------------


def test_documented_schema_version_matches_the_code():
    """Docs must state the current version, and may mention an older one only as history.

    Legacy 1.0 bundles are a real compatibility concern worth documenting, so a blanket ban on
    older version numbers would be wrong. What must not happen is a document presenting a
    superseded version as the current contract.
    """
    from blisolver.schema import SCHEMA_VERSION

    prose = doc_text()
    assert f'"{SCHEMA_VERSION}"' in prose or f"schema {SCHEMA_VERSION}" in prose, (
        f"docs never state the current schema version {SCHEMA_VERSION}"
    )

    historical = re.compile(r"legacy|older|previous|pre-1|before|superseded|current bundles", re.I)
    offenders = []
    for path in DOCS:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for found in re.findall(r"schema[- ]?(\d+\.\d+)(?!\.\d)", line, re.I):
                if found != SCHEMA_VERSION and not historical.search(line):
                    offenders.append(f"{path.relative_to(SKILL)}:{number}: {line.strip()}")
            # A bare version next to "bundles"/"provenance" also counts as a schema claim.
            for found in re.findall(r"\b(\d+\.\d+)\b(?!\.\d)", line):
                if (
                    found != SCHEMA_VERSION
                    and re.search(r"bundle|provenance|contract", line, re.I)
                    and not historical.search(line)
                    and "1.0.0" not in line          # Agent Plugins spec version, not the schema
                ):
                    offenders.append(f"{path.relative_to(SKILL)}:{number}: {line.strip()}")
    assert not offenders, (
        f"a superseded schema version is presented as current (expected {SCHEMA_VERSION}):\n"
        + "\n".join(offenders)
    )


def test_skill_frontmatter_metadata_matches_the_code():
    """The frontmatter advertises the bundle schema; it is discovery metadata a client may index,
    so a stale value misroutes the skill rather than merely misinforming a reader."""
    import yaml

    from blisolver.schema import SCHEMA_VERSION

    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    front = yaml.safe_load(text.split("---", 2)[1])
    assert front["metadata"]["bundle-schema"] == SCHEMA_VERSION


def test_documented_transcript_sources_match_the_schema(prose):
    """Authority ranking is stated in prose; the tiers must be the ones the schema allows."""
    from typing import get_args

    from blisolver.schema import TranscriptSource

    for source in get_args(TranscriptSource):
        assert f"`{source}`" in prose, f"transcript source {source!r} is undocumented"


def test_data_directory_precedence_is_documented(prose):
    """Four sources decide where bundles land. A reader who knows only some of them cannot explain
    why output moved."""
    for token in ("BLISOLVER_DATA_DIR", "PLUGIN_DATA", "BLISOLVER_CACHE_DIR", "BLISOLVER_OUT_DIR"):
        assert token in prose, f"data-directory source {token} is undocumented"


# --- deleted surfaces stay deleted ---------------------------------------------------------


@pytest.mark.parametrize("gone", ["doctor.py", "probe.py", "ingest.py", "_common.py"])
def test_superseded_scripts_are_not_resurrected_in_prose(gone):
    """These four were removed: doctor moved into the application, two were flag mirrors replaced by
    pass-through, and one held a duplicate of the application's URL parser. Docs referenced all of
    them for a full batch after deletion."""
    assert not (SCRIPTS / gone).exists(), f"{gone} is back on disk"
    for path in DOCS:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if f"scripts/{gone}" in line:
                pytest.fail(f"{path.relative_to(SKILL)}:{number} references removed {gone}")


def test_retired_project_root_flag_is_gone(prose):
    """`--project-root` became `--plugin-root` when the repository became the plugin root."""
    assert "--project-root" not in prose
    assert "BLISOLVER_PROJECT_ROOT" not in prose


def test_docs_do_not_promise_an_unverified_install_path(prose):
    """Installation is client-defined under Agent Plugins. The previous docs shipped a specific
    third-party one-liner that nothing here verifies."""
    assert "npx skills" not in prose, (
        "the skill documents an install command this repository cannot verify; "
        "describe the portable layout and let the client document its own install"
    )
