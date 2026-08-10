"""The declared minimum Python must actually be able to load the package.

`pyproject.toml` says `requires-python = ">=3.11"`, `blisolver.doctor` enforces 3.11, and the skill
documents "Python 3.11+". None of that was true: `blisolver/cli.py` nested a same-type quote inside
an f-string, which PEP 701 only permits from 3.12, so on a real 3.11 the module failed to *parse*
and the entire CLI was unimportable.

Nothing already here could catch it. The suite runs on the development interpreter — 3.14 on this
machine — where the syntax is legal. `doctor` cannot catch it either: it inspects the running
version, and on 3.11 the CLI cannot be imported far enough for doctor to run at all.

`ast.parse(..., feature_version=(3, 11))` looks like the answer and is not: it gates a limited set
of grammar decisions and accepts PEP 701 nesting, which is resolved by the tokenizer. That was
verified before writing this file — a version of it built on `feature_version` passed while the
defect was deliberately reinserted. So the check has to run a real interpreter at the floor.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("blisolver", "skills", "scripts", "tests")


def declared_floor() -> tuple[int, int]:
    """Read the floor from pyproject rather than restating it, so the two cannot disagree."""
    text = (PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'requires-python\s*=\s*"[><=~^]*\s*(\d+)\.(\d+)', text)
    assert match, "could not read requires-python from pyproject.toml"
    return int(match.group(1)), int(match.group(2))


def python_files() -> list[Path]:
    files: list[Path] = []
    for root in SOURCE_ROOTS:
        base = PLUGIN_ROOT / root
        if base.is_dir():
            files.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def floor_interpreter() -> str | None:
    """Locate an interpreter at the declared floor, without downloading one during a test run.

    Tries `uv python find`, which resolves an already-managed install, then a bare `pythonX.Y` on
    PATH. Returns None when neither exists, so the suite stays runnable on a machine that has only
    the development interpreter.
    """
    major, minor = declared_floor()
    version = f"{major}.{minor}"

    uv = shutil.which("uv")
    if uv:
        found = subprocess.run(
            [uv, "python", "find", version], capture_output=True, text=True, check=False
        )
        candidate = found.stdout.strip()
        if found.returncode == 0 and candidate and Path(candidate).is_file():
            return candidate

    return shutil.which(f"python{version}")


def test_pyproject_declares_a_floor():
    assert declared_floor() >= (3, 11)


def test_doctor_enforces_the_declared_floor():
    """doctor's minimum and pyproject's must be one number, not two that happen to agree."""
    from blisolver.doctor import _MIN_PYTHON

    assert _MIN_PYTHON == declared_floor(), (
        f"doctor requires {_MIN_PYTHON} but pyproject declares {declared_floor()}"
    )


def test_every_source_file_parses_on_the_floor_interpreter():
    """Compile every source file with a real interpreter at the declared floor.

    Grammar only. A 3.12+ standard-library call would still pass here and fail at runtime; catching
    that needs an install and a test run on the floor interpreter.
    """
    interpreter = floor_interpreter()
    if interpreter is None:
        pytest.skip(
            f"no Python {'.'.join(map(str, declared_floor()))} available; "
            f"install one (uv python install) to enforce the declared floor"
        )

    files = python_files()
    assert files, "no source files discovered"
    program = (
        "import ast, sys\n"
        "bad = []\n"
        "for path in sys.argv[1:]:\n"
        "    with open(path, encoding='utf-8') as handle:\n"
        "        source = handle.read()\n"
        "    try:\n"
        "        ast.parse(source, filename=path)\n"
        "    except SyntaxError as exc:\n"
        "        bad.append(f'{path}:{exc.lineno}: {exc.msg}')\n"
        "print('\\n'.join(bad))\n"
    )
    result = subprocess.run(
        [interpreter, "-c", program, *map(str, files)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    offenders = [line for line in result.stdout.splitlines() if line.strip()]
    assert not offenders, (
        f"source that does not parse on Python {'.'.join(map(str, declared_floor()))}, "
        f"the version pyproject declares:\n" + "\n".join(offenders)
    )


def test_the_floor_check_would_notice_newer_syntax(tmp_path):
    """Guard the guard.

    An earlier version of this test used `ast.parse(feature_version=...)` and silently passed while
    the defect was present. This feeds the floor interpreter a construct that is legal on the
    development interpreter and illegal at the floor, and requires it to object — so a check that
    has stopped checking fails here rather than reporting success.
    """
    interpreter = floor_interpreter()
    if interpreter is None:
        pytest.skip("no floor interpreter available")

    # PEP 701: reusing the outer quote inside an f-string is legal from 3.12 only.
    probe = tmp_path / "probe.py"
    probe.write_text('x = f"{"inner" if True else "other"}"\n', encoding="utf-8")
    ast.parse(probe.read_text(encoding="utf-8"))  # legal on the interpreter running the suite

    result = subprocess.run(
        [interpreter, "-c", "import ast,sys; ast.parse(open(sys.argv[1]).read())", str(probe)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0, (
        "the floor interpreter accepted 3.12-only syntax; this check is not actually checking"
    )
    assert "f-string" in result.stderr or "SyntaxError" in result.stderr
