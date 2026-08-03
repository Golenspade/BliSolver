from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skill" / "harvest-video-ingestion"
SCRIPTS = SKILL / "scripts"


def run_script(
    name: str,
    *args: str,
    cwd: Path = REPO,
    env: dict[str, str] | None = None,
):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        cwd=cwd,
        env=merged,
        capture_output=True,
        text=True,
    )


def write_bundle(
    root: Path,
    *,
    frame_path: str | None = "frames/000.png",
    create_frame: bool = True,
) -> Path:
    bundle_dir = root / "bundle"
    bundle_dir.mkdir(parents=True)
    if frame_path and create_frame and not frame_path.startswith("../"):
        frame = bundle_dir / frame_path
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"png-fixture")
    payload = {
        "schema_version": "1.1",
        "platform": "youtube.com",
        "id": "fixture-id",
        "part": 1,
        "url": "https://www.youtube.com/watch?v=fixture-id",
        "fetched_at": "2026-07-31T00:00:00Z",
        "transcript": {
            "source": "whisper",
            "source_reason": "fixture",
            "language": "en",
            "model": "fixture-model",
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "hello", "source": "whisper"}
            ],
        },
        "ocr": [{"start": 0.0, "end": 1.0, "text": "HELLO", "source": "ocr"}],
        "frames": [
            {
                "ts": 0.0,
                "path": frame_path,
                "phash": "00",
                "caption": "slide",
                "ocr": "HELLO",
            }
        ],
        "danmaku": {
            "fetched_total": 2,
            "windows": [
                {"start": 0, "end": 15, "total": 2, "lines": [{"text": "hi"}]}
            ],
        },
        "interactions": {
            "votes": [{"question": "Q", "options": [], "total_count": 0}],
            "grades": [{"avg_score": 8.0, "count": 2}],
        },
        "meta": {
            "cookies_used": False,
            "referer_used": False,
            "tool_version": "0.1.0",
        },
    }
    (bundle_dir / "bundle.json").write_text(json.dumps(payload), encoding="utf-8")
    (bundle_dir / "bundle.md").write_text("# fixture\n", encoding="utf-8")
    return bundle_dir


def test_manifest_lists_valid_skill_files():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "name: harvest-video-ingestion" in text
    assert "references/current-contract.md" in text
    assert "scripts/validate_bundle.py" in text
    for path in [
        "LICENSE.txt",
        "references/architecture.md",
        "references/current-contract.md",
        "references/provider-guide.md",
        "references/pipeline-stages.md",
        "references/operational-runbook.md",
        "references/domain-glossary.md",
        "references/source-map.md",
    ]:
        assert (SKILL / path).is_file(), path


def test_all_user_scripts_support_help():
    paths = [path for path in sorted(SCRIPTS.glob("*.py")) if path.name != "_common.py"]
    assert {path.name for path in paths} == {
        "doctor.py",
        "probe.py",
        "ingest.py",
        "inspect_bundle.py",
        "validate_bundle.py",
    }
    for path in paths:
        result = run_script(path.name, "--help")
        assert result.returncode == 0, (path.name, result.stderr)
        assert "usage" in result.stdout.lower()


def test_ingest_dry_run_forwards_current_flags(tmp_path):
    result = run_script(
        "ingest.py",
        "https://example.invalid/video",
        "--project-root",
        str(REPO),
        "--dry-run",
        "--part",
        "2",
        "--force-whisper",
        "--lang",
        "zh",
        "--robust",
        "--no-vision",
        "--dedup-threshold",
        "7",
        "--out",
        str(tmp_path / "out"),
        "--no-frame-images",
        "--danmaku",
        "--interactions",
        "--ocr",
        "--force-ocr",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["cwd"] == str(REPO)
    assert payload["command"][-2:] == ["--ocr", "--force-ocr"]
    assert payload["command"][0:3] == [sys.executable, "-m", "harvest.cli"]
    assert "--force-whisper" in payload["command"]
    assert "--no-frame-images" in payload["command"]


def test_probe_keeps_child_json_on_stdout(tmp_path):
    fake = tmp_path / "harvest"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "print('diagnostic', file=sys.stderr)\n"
        "print(json.dumps({'schema_version': '1.1', 'id': 'fixture'}))\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "HARVEST_PROJECT_ROOT": "",
    }
    result = run_script(
        "probe.py",
        "https://example.invalid/video",
        cwd=tmp_path,
        env=env,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"schema_version": "1.1", "id": "fixture"}
    assert "diagnostic" in result.stderr


def test_probe_rejects_json_non_object(tmp_path):
    fake = tmp_path / "harvest"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "print('[]')\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "HARVEST_PROJECT_ROOT": "",
    }
    result = run_script(
        "probe.py",
        "https://example.invalid/video",
        cwd=tmp_path,
        env=env,
    )
    assert result.returncode == 1
    assert "JSON object" in result.stderr
    assert result.stdout == ""


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
    assert payload["danmaku"] == {
        "windows": 1,
        "lines": 1,
        "fetched_total": 2,
    }
    assert payload["interactions"] == {"votes": 1, "grades": 1}


def test_validate_bundle_accepts_and_rejects_artifacts(tmp_path):
    valid = write_bundle(tmp_path / "valid")
    accepted = run_script(
        "validate_bundle.py", str(valid), "--project-root", str(REPO)
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert json.loads(accepted.stdout)["valid"] is True

    broken = write_bundle(
        tmp_path / "broken",
        frame_path="frames/missing.png",
        create_frame=False,
    )
    rejected = run_script(
        "validate_bundle.py", str(broken), "--project-root", str(REPO)
    )
    assert rejected.returncode == 1
    report = json.loads(rejected.stdout)
    assert report["valid"] is False
    assert any("missing" in error.lower() for error in report["errors"])


def test_validate_bundle_rejects_frame_path_traversal(tmp_path):
    bundle = write_bundle(tmp_path / "traversal", frame_path="../outside.png")
    result = run_script("validate_bundle.py", str(bundle), "--project-root", str(REPO))
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["valid"] is False
    assert any("outside" in error.lower() for error in report["errors"])


def test_doctor_json_never_contains_secret_values():
    result = run_script(
        "doctor.py",
        "--project-root",
        str(REPO),
        "--json",
        env={"SESSDATA": "secret-sessdata", "LMSTUDIO_API_KEY": "secret-api-key"},
    )
    assert result.returncode in (0, 1)
    assert "secret-sessdata" not in result.stdout + result.stderr
    assert "secret-api-key" not in result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert isinstance(report["checks"], list)


def test_doctor_detects_broken_checkout(tmp_path):
    root = tmp_path / "broken-checkout"
    (root / "harvest").mkdir(parents=True)
    (root / "harvest" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='broken'\n", encoding="utf-8")
    result = run_script("doctor.py", "--project-root", str(root), "--json")
    assert result.returncode == 1
    report = json.loads(result.stdout)
    harvest = next(check for check in report["checks"] if check["name"] == "harvest")
    assert harvest["status"] == "error"


def test_skill_ingest_dry_run_sanitizes_title_prefix():
    raw_input = "【标题】 https://www.bilibili.com/video/BV1x2T463E7L/?spm_id_from=333"
    result = run_script("ingest.py", raw_input, "--dry-run", "--project-root", str(REPO))
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["command"][-1] == "https://www.bilibili.com/video/BV1x2T463E7L/?spm_id_from=333"

