"""Shared runtime and subprocess helpers for the portable blisolver skill."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


import re
from urllib.parse import parse_qs, urlparse

_URL_RE = re.compile(r"(https?://[^\s\"'>]+|//[^\s\"'>]+)")


def extract_url(text: str) -> str:
    """Extract and sanitize a valid video URL from raw input text."""
    if not text:
        return ""

    match = _URL_RE.search(text)
    url = match.group(1) if match else text.strip()

    if url.startswith("//"):
        url = "https:" + url

    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if "player.bilibili.com" in host:
        qs = parse_qs(parsed.query)
        bvid = qs.get("bvid", [None])[0]
        part = qs.get("p", ["1"])[0]
        if bvid:
            canon = f"https://www.bilibili.com/video/{bvid}"
            if part and part != "1":
                canon += f"?p={part}"
            return canon

    return url


@dataclass(frozen=True)
class Runtime:
    """How a wrapper should invoke blisolver."""

    command: tuple[str, ...]
    cwd: Path | None
    env: dict[str, str]
    kind: str


def resolve_project_root(explicit: str | None = None) -> Path | None:
    """Find a BliSolver checkout without assuming where the skill was copied."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    configured = os.environ.get("BLISOLVER_PROJECT_ROOT")
    if configured:
        candidates.append(Path(configured).expanduser())

    here = Path.cwd().resolve()
    candidates.extend([here, *here.parents])
    for candidate in candidates:
        candidate = candidate.resolve()
        if (
            (candidate / "blisolver" / "__init__.py").is_file()
            and (candidate / "pyproject.toml").is_file()
        ):
            return candidate
    return None


def resolve_runtime(explicit: str | None = None) -> Runtime:
    """Resolve an explicit/nearby checkout before falling back to PATH."""
    root = resolve_project_root(explicit)
    env = os.environ.copy()
    if root is not None:
        old_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(root)
            if not old_pythonpath
            else os.pathsep.join((str(root), old_pythonpath))
        )
        return Runtime(
            command=(sys.executable, "-m", "blisolver.cli"),
            cwd=root,
            env=env,
            kind="checkout",
        )

    executable = shutil.which("blisolver")
    if executable:
        return Runtime(
            command=(executable,),
            cwd=None,
            env=env,
            kind="installed",
        )
    raise RuntimeError(
        "could not find a BliSolver checkout or an installed blisolver command; "
        "use --project-root or set BLISOLVER_PROJECT_ROOT"
    )


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None,
    env: dict[str, str],
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a child with an argument list and no shell interpretation."""
    return subprocess.run(
        list(command),
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=capture_output,
        text=True,
        check=False,
    )


def bundle_json_path(value: str | Path) -> tuple[Path, Path]:
    """Return ``(bundle_dir, bundle.json)`` for a bundle directory or JSON path."""
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        bundle_dir = candidate
        json_path = candidate / "bundle.json"
    else:
        json_path = candidate
        bundle_dir = candidate.parent
    if not json_path.is_file():
        raise FileNotFoundError(f"bundle.json not found: {json_path}")
    return bundle_dir.resolve(), json_path.resolve()


def safe_child_path(bundle_dir: Path, relative_path: str) -> Path | None:
    """Resolve a bundle-relative artifact, returning None for absolute/traversal paths."""
    candidate = Path(relative_path)
    if candidate.is_absolute():
        return None
    resolved = (bundle_dir / candidate).resolve()
    try:
        resolved.relative_to(bundle_dir.resolve())
    except ValueError:
        return None
    return resolved
