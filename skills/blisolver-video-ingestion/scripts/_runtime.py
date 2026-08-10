"""Internal helper: locate the plugin root and the interpreter that can actually run blisolver.

Not a public entry point. `blisolver.py`, `inspect_bundle.py`, and `validate_bundle.py` import it.

Why a helper is needed at all
-----------------------------
Agent Plugins 1.0.0 §9.1 guarantees `PLUGIN_ROOT` and `PLUGIN_DATA` only for stdio MCP
subprocesses. A skill script is run by the agent, not launched by the client, so it receives
neither. What it does have is a fixed position in the package: the Agent Plugins skill discovery
rule (§7.1) puts every skill at `skills/<name>/`, so this file is always exactly three levels
below the plugin root. That is a deterministic anchor, unlike searching ancestor directories for
something that looks like a checkout.

The interpreter is the other half. §7.2.1 forbids placeholder expansion in an MCP `command`, and a
skill script has no client-provided interpreter at all, so both surfaces have to resolve it the
same way or they will disagree about whether the environment works. This module and
`bin/blisolver-mcp` implement the identical chain; `tests/test_portable_skill.py` asserts they
stay identical.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# skills/<skill-name>/scripts/_runtime.py -> plugin root
PLUGIN_ROOT = Path(__file__).resolve().parents[3]


class RuntimeError_(RuntimeError):
    """Raised when no interpreter can run blisolver. Carries an actionable message."""


@dataclass(frozen=True)
class Runtime:
    """How to invoke the blisolver CLI."""

    command: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    kind: str  # "project-venv" | "plugin-data-venv" | "explicit" | "installed"
    interpreter: str | None


def plugin_root(explicit: str | None = None) -> Path:
    """Resolve the plugin root, preferring an explicit override for out-of-tree checkouts."""
    for candidate in (
        explicit,
        os.environ.get("BLISOLVER_PLUGIN_ROOT"),
        os.environ.get("PLUGIN_ROOT"),
    ):
        if candidate:
            root = Path(candidate).expanduser().resolve()
            if (root / "plugin.json").is_file():
                return root
            raise RuntimeError_(f"no plugin.json at {root}")
    if (PLUGIN_ROOT / "plugin.json").is_file():
        return PLUGIN_ROOT
    raise RuntimeError_(
        f"this script expects to live at <plugin-root>/skills/<name>/scripts/, which puts the "
        f"plugin root at {PLUGIN_ROOT}, but no plugin.json is there. Pass --plugin-root or set "
        f"BLISOLVER_PLUGIN_ROOT."
    )


def _interpreter_candidates(root: Path) -> list[tuple[str, str]]:
    """(kind, path) pairs in resolution order. Mirrors bin/blisolver-mcp exactly."""
    candidates: list[tuple[str, str]] = []
    explicit = os.environ.get("BLISOLVER_PYTHON")
    if explicit:
        candidates.append(("explicit", explicit))
    plugin_data = os.environ.get("PLUGIN_DATA")
    if plugin_data:
        # §9.1 names virtual environments as a PLUGIN_DATA use case; present when an MCP client
        # exported it, absent for a plain agent-run script.
        candidates.append(("plugin-data-venv", str(Path(plugin_data) / "venv" / "bin" / "python")))
    candidates.append(("project-venv", str(root / ".venv" / "bin" / "python")))
    return candidates


def resolve_runtime(explicit_root: str | None = None) -> Runtime:
    """Find an interpreter that owns blisolver's dependencies, else an installed console script.

    Deliberately does NOT fall back to `sys.executable`. The interpreter running this wrapper is
    whichever `python3` the agent happened to invoke; using it is what previously turned a healthy
    checkout into `ModuleNotFoundError: No module named 'dotenv'`.
    """
    root = plugin_root(explicit_root)
    env = os.environ.copy()
    # Lets a non-editable environment still import the bundled package.
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(root), env.get("PYTHONPATH")) if p
    )

    for kind, candidate in _interpreter_candidates(root):
        if candidate and os.access(candidate, os.X_OK) and Path(candidate).is_file():
            return Runtime(
                command=(candidate, "-m", "blisolver.cli"),
                cwd=root,
                env=env,
                kind=kind,
                interpreter=candidate,
            )

    installed = shutil.which("blisolver")
    if installed:
        return Runtime(
            command=(installed,), cwd=root, env=env, kind="installed", interpreter=None
        )

    attempted = "\n".join(f"  {kind:<18} {path}" for kind, path in _interpreter_candidates(root))
    raise RuntimeError_(
        "no interpreter with blisolver's dependencies was found.\n"
        f"Tried, in order:\n{attempted}\n  installed          blisolver on PATH\n"
        f"Create the environment once, with either:\n"
        f"  uv venv {root}/.venv && uv pip install --python {root}/.venv/bin/python -e '{root}[mcp]'\n"
        f"  python3 -m venv {root}/.venv && {root}/.venv/bin/pip install -e '{root}[mcp]'\n"
        f"(`uv venv` does not install pip into the environment, so `python -m pip` will not work "
        f"there; use `uv pip` as above.)"
    )


def run_cli(
    runtime: Runtime, args: Sequence[str], *, capture_output: bool = False
) -> subprocess.CompletedProcess[str]:
    """Invoke the CLI with an argument array. No shell, so URLs are never re-parsed by one."""
    return subprocess.run(
        [*runtime.command, *args],
        cwd=str(runtime.cwd),
        env=runtime.env,
        capture_output=capture_output,
        text=True,
        check=False,
    )


def emit(payload: dict, *, pretty: bool = False) -> None:
    """One JSON object on stdout. Compact by default so a caller can parse a single line."""
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
    )


def fail(message: str, code: int = 2) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


# --- local bundle helpers (no counterpart in the application) ------------------------------


def bundle_json_path(value: str | Path) -> tuple[Path, Path]:
    """Return ``(bundle_dir, bundle.json)`` for a bundle directory or a direct JSON path."""
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        bundle_dir, json_path = candidate, candidate / "bundle.json"
    else:
        json_path, bundle_dir = candidate, candidate.parent
    if not json_path.is_file():
        raise FileNotFoundError(f"bundle.json not found: {json_path}")
    return bundle_dir.resolve(), json_path.resolve()


def safe_child_path(bundle_dir: Path, relative_path: str) -> Path | None:
    """Resolve a bundle-relative artifact; None for absolute paths or traversal escapes."""
    candidate = Path(relative_path)
    if candidate.is_absolute():
        return None
    resolved = (bundle_dir / candidate).resolve()
    try:
        resolved.relative_to(bundle_dir.resolve())
    except ValueError:
        return None
    return resolved
