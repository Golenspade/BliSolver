"""Offline environment report for the portable blisolver skill."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from _common import Runtime, resolve_project_root, resolve_runtime


Check = dict[str, str]


def _check(name: str, status: str, detail: str) -> Check:
    return {"name": name, "status": status, "detail": detail}


def _command_available(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return name
    return None


def _doctor(project_root: str | None) -> dict:
    checks: list[Check] = []
    python_ok = sys.version_info >= (3, 11)
    checks.append(
        _check(
            "python",
            "ok" if python_ok else "error",
            f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            + (" (requires >=3.11)" if not python_ok else ""),
        )
    )

    root = resolve_project_root(project_root)
    runtime: Runtime | None = None
    try:
        runtime = resolve_runtime(project_root)
        if runtime.kind == "checkout":
            imported = subprocess.run(
                [sys.executable, "-c", "import blisolver.cli"],
                cwd=str(runtime.cwd),
                env=runtime.env,
                capture_output=True,
                text=True,
                check=False,
            )
            if imported.returncode != 0:
                detail = "checkout found but blisolver import failed"
                error_line = (imported.stderr or "").strip().splitlines()
                if error_line:
                    detail += f": {error_line[-1][:240]}"
                checks.append(_check("blisolver", "error", detail))
            else:
                checks.append(_check("blisolver", "ok", "checkout runtime discovered"))
        else:
            checks.append(_check("blisolver", "ok", "installed command available"))
    except RuntimeError as exc:
        checks.append(_check("blisolver", "error", str(exc)))

    ffmpeg = _command_available("ffmpeg") or (
        "configured" if os.environ.get("FFMPEG_PATH") else None
    )
    checks.append(
        _check(
            "ffmpeg",
            "ok" if ffmpeg else "warn",
            "available" if ffmpeg else "not found; required for audio/video stages",
        )
    )

    javascript = _command_available("deno", "node")
    checks.append(
        _check(
            "javascript-runtime",
            "ok" if javascript else "warn",
            f"{javascript} available" if javascript else "deno/node not found; required for YouTube",
        )
    )

    whisper = os.environ.get("BLISOLVER_WHISPER_CLI") or _command_available("whisper-cli")
    checks.append(
        _check(
            "whisper-cli",
            "ok" if whisper else "warn",
            "configured" if whisper else "not found; required when captions are unavailable",
        )
    )

    vision_model = bool(os.environ.get("LMSTUDIO_VISION_MODEL", "").strip())
    checks.append(
        _check(
            "vision-config",
            "ok" if vision_model else "warn",
            "LM Studio vision model configured"
            if vision_model
            else "LMSTUDIO_VISION_MODEL is not set; --no-vision is required",
        )
    )

    worker = os.environ.get("BLISOLVER_OCR_WORKER")
    python = os.environ.get("BLISOLVER_OCR_VENV_PYTHON")
    if root:
        worker = worker or str(root / "scripts" / "ocr_worker.py")
        python = python or str(root / ".ocr-venv" / "bin" / "python")
    ocr_ready = bool(worker and python and Path(worker).is_file() and Path(python).is_file())
    checks.append(
        _check(
            "ocr-isolate",
            "ok" if ocr_ready else "warn",
            "OCR worker and isolated Python available"
            if ocr_ready
            else "optional OCR isolate not found",
        )
    )

    if os.environ.get("SESSDATA"):
        auth_detail = "bilibili SESSDATA configured"
        auth_status = "ok"
    elif os.environ.get("BLISOLVER_COOKIES_PROFILE"):
        auth_detail = "browser cookie profile configured"
        auth_status = "ok"
    elif os.environ.get("BLISOLVER_COOKIES_BROWSER"):
        auth_detail = "browser cookie source configured"
        auth_status = "warn"
    else:
        auth_detail = "no explicit bilibili credential configured; verify default browser login"
        auth_status = "warn"
    checks.append(_check("provider-auth", auth_status, auth_detail))

    statuses = {check["status"] for check in checks}
    overall = "error" if "error" in statuses else "warn" if "warn" in statuses else "ok"
    return {
        "status": overall,
        "project_root": str(root) if root else None,
        "checks": checks,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report blisolver runtime and stage prerequisites without network calls."
    )
    parser.add_argument("--project-root", help="BliSolver checkout to use")
    parser.add_argument("--json", action="store_true", help="emit one JSON object")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _doctor(args.project_root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    else:
        print(f"status: {report['status']}")
        if report["project_root"]:
            print(f"project_root: {report['project_root']}")
        for check in report["checks"]:
            print(f"[{check['status']}] {check['name']}: {check['detail']}")
    return 1 if report["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
