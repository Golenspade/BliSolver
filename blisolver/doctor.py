"""Preflight diagnostics: what will actually run, and what will fail, before paying for media.

This module exists in the application, not in the skill package, on purpose. The previous
arrangement reimplemented these checks inside a copied skill wrapper, which drifted from the code
it claimed to describe: it reported `whisper-cli: ok` while the GGML model the binary needs was
absent, it never looked at the danmaku model that `--danmaku` hard-requires, and it resolved the
OCR isolate with the opposite precedence to `config.py`. Every check below reads the same
`Settings` the pipeline reads, so a check cannot disagree with the stage it gates.

Every check names the `stage` it gates, so a caller can tell "this warning blocks the OCR track"
from "this warning blocks everything". Offline by design: nothing here opens a socket, so a
diagnosis is cheap and safe to run before an expensive job. Service reachability (LM Studio and
its projector) is verified at ingest time by `vision.verify_projector`.

Secret discipline: a check MAY report that a credential is configured; it MUST NOT report its
value. `SESSDATA`, `LMSTUDIO_API_KEY`, and cookie contents are reported as presence only.
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import PROJECT_ROOT, Settings, find_aria2c, find_ffmpeg, find_js_runtime

OK = "ok"
WARN = "warn"
ERROR = "error"

# Stage labels: which part of the pipeline a check gates.
CORE = "core"              # nothing runs without this
TRANSCRIPT = "transcript"  # the Whisper fallback path
VISION = "vision"          # frames + captioning
OCR = "ocr"                # burned-in subtitle track
DANMAKU = "danmaku"        # bilibili audience track
AUTH = "auth"              # provider credentials

_MIN_PYTHON = (3, 11)


@dataclass
class Check:
    name: str
    status: str
    stage: str
    detail: str


@dataclass
class Report:
    status: str
    plugin_root: str
    interpreter: str
    checks: list[Check]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "plugin_root": self.plugin_root,
            "interpreter": self.interpreter,
            "checks": [asdict(c) for c in self.checks],
        }


def _python_check() -> Check:
    v = sys.version_info
    ok = (v.major, v.minor) >= _MIN_PYTHON
    return Check(
        "python",
        OK if ok else ERROR,
        CORE,
        f"Python {v.major}.{v.minor}.{v.micro}"
        + ("" if ok else f" (blisolver requires >={_MIN_PYTHON[0]}.{_MIN_PYTHON[1]})"),
    )


def _interpreter_check() -> Check:
    """Which interpreter is running, and does it look like the project environment?

    This is the check that would have caught the failure mode where a wrapper launched the
    pipeline with whatever `python3` happened to be on PATH: the checkout was found, the
    dependencies were not, and the error surfaced as an unrelated import failure.

    Compares `sys.prefix`, not `sys.executable`. A virtual environment's `bin/python` is a symlink
    to the base interpreter, so resolving the executable path reports the base installation and
    makes an in-venv run look like an outside one.
    """
    prefix = Path(sys.prefix).resolve()
    project_venv = (PROJECT_ROOT / ".venv").resolve()
    if prefix == project_venv:
        return Check("interpreter", OK, CORE, f"project environment ({sys.executable})")
    in_venv = sys.prefix != sys.base_prefix
    return Check(
        "interpreter",
        WARN,
        CORE,
        f"{'virtual environment' if in_venv else 'system interpreter'} at {prefix}, not "
        f"{project_venv}; importing this module already proved it has blisolver's dependencies, "
        f"so this is informational",
    )


def _plugin_manifest_check() -> Check:
    """The repository root doubles as the Agent Plugins plugin root, so the manifest is part of
    the runtime contract rather than packaging trivia."""
    manifest = PROJECT_ROOT / "plugin.json"
    if not manifest.is_file():
        return Check("plugin-manifest", WARN, CORE, f"no plugin.json at {PROJECT_ROOT}")
    import json

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return Check("plugin-manifest", ERROR, CORE, f"plugin.json is not valid JSON: {exc}")
    name = data.get("name")
    version = data.get("version")
    if not isinstance(name, str) or not name:
        return Check("plugin-manifest", ERROR, CORE, "plugin.json has no usable `name`")
    return Check("plugin-manifest", OK, CORE, f"{name}{f' {version}' if version else ''}")


def _ffmpeg_check(settings: Settings) -> Check:
    path = settings.ffmpeg_path or find_ffmpeg()
    if path:
        return Check("ffmpeg", OK, CORE, path)
    return Check(
        "ffmpeg", ERROR, CORE,
        "not found on PATH or via FFMPEG_PATH; audio downmix, frame extraction, and OCR "
        "sampling all shell out to it",
    )


def _aria2c_check(settings: Settings) -> Check:
    path = settings.aria2c_path or find_aria2c()
    if path:
        return Check("aria2c", OK, CORE, f"{path} (bilibili CDN acceleration)")
    return Check(
        "aria2c", WARN, CORE,
        "not found; bilibili media downloads fall back to yt-dlp's native downloader, which is "
        "slower on throttled CDNs but correct",
    )


def _js_runtime_check(settings: Settings) -> Check:
    runtime = settings.js_runtime or find_js_runtime()
    if runtime:
        name, path = runtime
        return Check("javascript-runtime", OK, CORE, f"{name} ({path})")
    return Check(
        "javascript-runtime", WARN, CORE,
        "neither deno nor node found; yt-dlp cannot drive YouTube's real web player client, so "
        "YouTube extraction degrades intermittently (placeholder title, no duration/subtitles). "
        "bilibili is unaffected",
    )


def _whisper_cli_check() -> Check:
    from .transcribe import ASRPreflightError, validate_whisper_cli

    try:
        resolved = validate_whisper_cli()
    except ASRPreflightError as exc:
        return Check("whisper-cli", WARN, TRANSCRIPT, str(exc))
    return Check("whisper-cli", OK, TRANSCRIPT, resolved)


def _whisper_model_check() -> Check:
    """The check the previous doctor was missing.

    `whisper-cli` being installed says nothing about the GGML weights it loads. The default model
    path lives under /tmp, so it disappears on reboot; without this check the failure surfaces
    only after the audio download has already been paid for.
    """
    from .transcribe import ASRPreflightError, validate_whisper_model

    try:
        path = validate_whisper_model()
        size_mb = path.stat().st_size / (1024 * 1024)
    except ASRPreflightError as exc:
        return Check("whisper-model", WARN, TRANSCRIPT, str(exc))
    except OSError as exc:
        return Check("whisper-model", WARN, TRANSCRIPT, f"model became unavailable: {exc}")
    return Check(
        "whisper-model", OK, TRANSCRIPT,
        f"{path} ({size_mb:.0f} MB; readable GGML header, full weights not validated)",
    )


def _vision_check(settings: Settings) -> Check:
    if settings.lmstudio_vision_model:
        return Check(
            "vision-model", OK, VISION,
            f"{settings.lmstudio_vision_model} via {settings.lmstudio_base_url} "
            f"(projector is verified at ingest time, not here)",
        )
    return Check(
        "vision-model", WARN, VISION,
        "LMSTUDIO_VISION_MODEL is not set; frame captioning cannot run, so pass --no-vision",
    )


def _danmaku_check(settings: Settings) -> Check:
    """The other check the previous doctor was missing: without this model `--danmaku` is accepted
    on the command line and then silently ignored at runtime."""
    if settings.lmstudio_danmaku_model:
        return Check("danmaku-model", OK, DANMAKU, settings.lmstudio_danmaku_model)
    return Check(
        "danmaku-model", WARN, DANMAKU,
        "BLISOLVER_DANMAKU_MODEL is not set; --danmaku is silently ignored at runtime. "
        "--interactions is independent and does not need a model",
    )


def _ocr_check(settings: Settings) -> Check:
    """Reads the resolved Settings rather than re-deriving paths.

    The previous wrapper preferred the environment override and fell back to the repository
    layout; `config._resolve_ocr_paths` does the opposite. Reading the settings object removes the
    possibility of reporting a path the pipeline will not use.
    """
    worker, python = settings.ocr_worker_path, settings.ocr_venv_python
    missing = []
    if not worker or not Path(worker).is_file():
        missing.append(f"worker script ({worker or 'unresolved'})")
    if not python or not Path(python).is_file():
        missing.append(f"isolated Python ({python or 'unresolved'})")
    if not missing:
        return Check("ocr-isolate", OK, OCR, f"{worker} via {python}")
    return Check(
        "ocr-isolate", WARN, OCR,
        "hard-subtitle OCR unavailable: " + "; ".join(missing) + ". --ocr degrades to a no-op. "
        "From the repository root, set up with: uv venv .ocr-venv && "
        "uv pip install --python .ocr-venv/bin/python rapidocr-onnxruntime opencv-python",
    )


def _auth_check(settings: Settings) -> Check:
    """Presence only. A credential's value never enters this report."""
    if settings.sessdata:
        return Check("provider-auth", OK, AUTH, "SESSDATA is configured (value not shown)")
    if settings.cookies_profile:
        return Check(
            "provider-auth", OK, AUTH,
            f"cookies-from-browser: {settings.cookies_browser}, named profile configured",
        )
    return Check(
        "provider-auth", WARN, AUTH,
        f"no SESSDATA and no named profile; relying on the default {settings.cookies_browser} "
        f"profile being logged in. bilibili AI subtitle tracks are only visible to a logged-in "
        f"session, so a cold browser degrades every bilibili video to Whisper",
    )


def _writable_check(name: str, path: Path, stage: str) -> Check:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".blisolver-write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check(name, ERROR, stage, f"{path} is not writable: {exc}")
    return Check(name, OK, stage, str(path))


def run(settings: Settings | None = None) -> Report:
    """Collect every check. Never raises for an unhealthy environment — that is the report."""
    s = settings or Settings.load()
    checks = [
        _python_check(),
        _interpreter_check(),
        _plugin_manifest_check(),
        _ffmpeg_check(s),
        _aria2c_check(s),
        _js_runtime_check(s),
        _whisper_cli_check(),
        _whisper_model_check(),
        _vision_check(s),
        _danmaku_check(s),
        _ocr_check(s),
        _auth_check(s),
        _writable_check("cache-dir", s.cache_dir, CORE),
        _writable_check("out-dir", s.out_dir, CORE),
    ]
    statuses = {c.status for c in checks}
    overall = ERROR if ERROR in statuses else WARN if WARN in statuses else OK
    return Report(
        status=overall,
        plugin_root=str(PROJECT_ROOT),
        interpreter=sys.executable,
        checks=checks,
    )


def render(report: Report) -> str:
    """Human-readable rendering, grouped so a reader sees blockers before optional stages."""
    lines = [
        f"status: {report.status}",
        f"plugin root: {report.plugin_root}",
        f"interpreter: {report.interpreter}",
        "",
    ]
    order = [CORE, AUTH, TRANSCRIPT, VISION, OCR, DANMAKU]
    for stage in order:
        staged = [c for c in report.checks if c.stage == stage]
        if not staged:
            continue
        lines.append(f"[{stage}]")
        for c in staged:
            lines.append(f"  {c.status:<5} {c.name:<20} {c.detail}")
        lines.append("")
    blockers = [c.name for c in report.checks if c.status == ERROR]
    if blockers:
        lines.append(f"blocking: {', '.join(blockers)}")
    return "\n".join(lines).rstrip() + "\n"


def main(as_json: bool = False, settings: Settings | None = None) -> int:
    """Exit code: 0 when nothing is blocking (warnings included), 1 when a core check failed.

    A warning must not fail the command — an environment with no LM Studio and no OCR isolate is a
    perfectly good caption-and-transcript environment.
    """
    import json

    report = run(settings)
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False))
    else:
        print(render(report), end="")
    return 1 if report.status == ERROR else 0
