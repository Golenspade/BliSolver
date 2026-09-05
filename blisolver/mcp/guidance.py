"""Lazy MCP resources backed by the repository's authoritative Agent Skill files."""

from __future__ import annotations

import json
import re
from pathlib import Path
from threading import Lock

from mcp.server import MCPServer

from .. import doctor
from ..config import PROJECT_ROOT, Settings

GUIDANCE_URI = "blisolver://guidance/SKILL.md"
DOCTOR_URI = "blisolver://runtime/doctor.json"
SERVER_INSTRUCTIONS = (
    f"Before using BliSolver, read {GUIDANCE_URI} with resources/read. "
    "Load only the references needed for the current step; do not guess parameters. "
    f"Before the first media operation, read {DOCTOR_URI} for local preflight diagnostics."
)
_REFERENCE_NAME = re.compile(r"[a-z0-9][a-z0-9-]*\.md\Z")
_DOCTOR_LOCK = Lock()
_MISSING_GUIDANCE = (
    "# BliSolver guidance unavailable\n\n"
    "This installation does not contain the authoritative Agent Skill files. "
    "Use a complete BliSolver repository/plugin checkout containing "
    "skills/blisolver-video-ingestion/SKILL.md and its references, then restart the server. "
    "A Python wheel alone does not include these files. Do not guess workflow parameters.\n"
)


def _read_markdown(root: Path, relative: str) -> str:
    """Read only the registered file, checking containment again at read time."""
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError("guidance file is outside the skill directory")
    if not path.is_file():
        if relative == "SKILL.md":
            return _MISSING_GUIDANCE
        raise FileNotFoundError("guidance file is unavailable; restart from a complete checkout")
    return path.read_text(encoding="utf-8")


def _markdown_reader(root: Path, relative: str):
    def read() -> str:
        return _read_markdown(root, relative)

    return read


def register_guidance(server: MCPServer, settings: Settings) -> None:
    """Register short metadata only; file bodies are loaded on resources/read."""
    project = PROJECT_ROOT.resolve()
    root = (project / "skills" / "blisolver-video-ingestion").resolve()
    if not root.is_relative_to(project):
        # An invalid or wheel-only installation must not break existing tools.
        def unavailable() -> str:
            return _MISSING_GUIDANCE

        server.resource(
            GUIDANCE_URI, name="blisolver-skill", mime_type="text/markdown",
            description="Start here: installation diagnostics for missing skill guidance.",
        )(unavailable)
    else:
        server.resource(
            GUIDANCE_URI, name="blisolver-skill", mime_type="text/markdown",
            description="Start here: workflow and links to references to read only when needed.",
        )(_markdown_reader(root, "SKILL.md"))
        for path in sorted((root / "references").glob("*.md")):
            if (
                not _REFERENCE_NAME.fullmatch(path.name)
                or not path.is_file()
                or not path.resolve().is_relative_to(root)
            ):
                continue
            relative = f"references/{path.name}"
            server.resource(
                f"blisolver://guidance/{relative}", name=f"blisolver-{path.stem}",
                mime_type="text/markdown",
                description=f"On-demand reference: {path.stem.replace('-', ' ')}.",
            )(_markdown_reader(root, relative))

    @server.resource(
        DOCTOR_URI, name="blisolver-doctor", mime_type="application/json",
        description=(
            "Offline runtime preflight. Checks configured dependencies and credential presence; "
            "creates cache/output directories and writes/removes local permission probes."
        ),
    )
    def runtime_doctor() -> str:
        # doctor uses fixed filenames for its brief writability probes.
        with _DOCTOR_LOCK:
            return json.dumps(doctor.run(settings).to_dict(), ensure_ascii=False)
