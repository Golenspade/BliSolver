import json
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("requests")
pytest.importorskip("boto3")

from blisolver.config import Settings
from blisolver.songcut.cloud import DashScope
from blisolver.songcut.models import Manifest
from blisolver.songcut.pipeline import run
from blisolver.songcut.state import atomic_json, read_json, sha256


def settings(tmp_path):
    return Settings(data_dir=tmp_path, cache_dir=tmp_path / "cache", out_dir=tmp_path / "out")


def test_local_end_to_end_resume_and_parameter_change(songcut_wav, tmp_path):
    path = songcut_wav()
    original = sha256(path)
    manifest = Manifest(clips=[{"id": "first", "source": str(path), "start": 1, "end": 12}])
    out = tmp_path / "run"
    one = run(manifest, out, settings(tmp_path))
    assert one["status"] == "complete", one
    bundle = read_json(Path(one["clips"][0]["bundle_json"]))
    assert Path(bundle["audio"]).is_file()
    assert bundle["review_status"] == "draft"
    two = run(manifest, out, settings(tmp_path))
    assert two["clips"][0]["reused"]
    assert two["clips"][0]["bundle_json"] == one["clips"][0]["bundle_json"]
    manifest.clips[0].end = 11
    three = run(manifest, out, settings(tmp_path))
    assert three["clips"][0]["bundle_json"] != one["clips"][0]["bundle_json"]
    assert sha256(path) == original


def test_per_clip_failure_does_not_abort_sibling(songcut_wav, tmp_path):
    path = str(songcut_wav())
    manifest = Manifest(clips=[{"id": "bad", "source": path, "start": 300},
                               {"id": "good", "source": path}])
    result = run(manifest, tmp_path / "run", settings(tmp_path))
    assert result["status"] == "partial"
    assert [r["status"] for r in result["clips"]] == ["failed", "complete"]


class FullTransport:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        if url.endswith("/transcription"):
            raw = {"output": {"task_id": "offline-task"}}
        elif "/tasks/" in url:
            raw = {"output": {"task_status": "SUCCEEDED", "results": [{
                "transcription_url": "https://result.aliyuncs.com/transcript.json"}]},
                   "usage": {"duration": 14}}
        elif url.endswith("transcript.json"):
            raw = {"transcripts": [{"sentences": [{"words": [
                {"begin_time": 1000, "end_time": 1500, "text": "你好"},
                {"begin_time": 2000, "end_time": 2500, "text": "世界"}]}]}]}
        elif url.endswith("multimodal-generation/generation"):
            raw = {"output": {"sentence": {"words": []}}, "usage": {"duration": 1}}
        else:
            message = kwargs["json"]["messages"][0]["content"]
            if message[0]["type"] == "input_audio":
                content = {"events": [{"start_sec": 1, "end_sec": 3, "type": "singing"}],
                           "source_assessment": {"decision": "unknown", "uncertainty": "candidate"}}
            else:
                data = json.loads(message[0]["text"].rsplit("\n", 1)[-1])
                content = {"rows": [[i, i, row[2], "singing"] for i, row in enumerate(data["words"])],
                           "discarded": []}
            raw = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
                   "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
        return type("Response", (), {"status_code": 200, "json": lambda self: raw})()


def test_real_audio_full_cloud_pipeline_with_offline_transport(songcut_wav, tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "offline-token")
    transport = FullTransport()
    def factory(options, root, ledger):
        return DashScope(options, root, ledger, transport, api_key="offline-token")
    manifest = Manifest(clips=[{"id": "song", "source": str(songcut_wav()), "language": "en"}],
                        backend="dashscope", cloud={"budget_cny": 1, "upload": "temporary"})
    result = run(manifest, tmp_path / "run", settings(tmp_path), client_factory=factory,
                 uploader_factory=lambda *args: lambda *a: "oss://offline-input")
    assert result["status"] == "complete", result
    root = Path(result["clips"][0]["bundle_json"]).parent
    audio_sha = sha256(root / "audio.m4a")
    lyrics = read_json(root / "lyrics.json")
    assert lyrics["audio_sha256"] == audio_sha and lyrics["reviewed"] is False
    assert (root / "lyrics.lrc").read_text().startswith("[00:01.00]你好")
    qc = read_json(root / "qc.json")
    assert qc["asr"]["input_complete"]
    assert qc["asr"]["input_audio_sha256"] == audio_sha
    assert qc["presence"][0]["identity_proven"] is False
    paid = sum(method == "POST" for method, _ in transport.calls)
    assert paid == 4  # Filetrans, independent Flash, presence, lyric partition
    # Invalid local derived file is rebuilt, but already-paid calls are reused.
    (root / "lyrics.lrc").write_text("corrupt")
    again = run(manifest, tmp_path / "run", settings(tmp_path), client_factory=factory,
                uploader_factory=lambda *args: lambda *a: pytest.fail("must not upload again"))
    assert again["status"] == "complete", again
    assert sum(method == "POST" for method, _ in transport.calls) == paid
    assert result["budget"]["reserved_cny"] == 0


def test_duplicate_never_inherits_other_lyrics(songcut_wav, tmp_path):
    path = str(songcut_wav())
    result = run(Manifest(clips=[{"id": "a", "source": path}, {"id": "b", "source": path}]),
                 tmp_path / "run", settings(tmp_path))
    second = Path(result["clips"][1]["bundle_json"]).parent
    assert read_json(second / "dedup.json")["decision"] == "exact_file"
    assert read_json(second / "lyrics.json")["lines"] == []
    assert (second / "audio.m4a").is_file()


def test_provider_revision_gets_a_separate_download_cache(songcut_wav, tmp_path, monkeypatch):
    from blisolver.providers.base import Canonical, SourceMetadata
    from blisolver.songcut.audio import AudioEngine
    from blisolver.songcut.models import ClipInput
    from blisolver.songcut.pipeline import acquire
    path = songcut_wav()
    meta = SourceMetadata(platform="bilibili.com", id="BVexample", title="song", uploader="u",
                          uploader_id="1", description="", duration_s=14, published_at=None,
                          parts=1, part_durations_s=[14], source_part_id="cid-first")
    class Provider:
        def resolve(self, url):
            return Canonical("bilibili.com", "BVexample", 1, url)

        def fetch_metadata(self, canonical, settings):
            return meta
    caches = []
    def download(canonical, configured):
        caches.append(configured.cache_dir)
        return path
    monkeypatch.setattr("blisolver.songcut.pipeline.select_provider", lambda source: Provider())
    monkeypatch.setattr("blisolver.songcut.pipeline.download_audio", download)
    clip = ClipInput(id="song", source="https://www.bilibili.com/video/BVexample")
    one = acquire(clip, settings(tmp_path), tmp_path / "state", AudioEngine())
    meta.source_part_id = "cid-second"
    two = acquire(clip, settings(tmp_path), tmp_path / "state", AudioEngine())
    assert caches[0] != caches[1]
    assert one["metadata"]["source_part_id"] == "cid-first"
    assert two["metadata"]["source_part_id"] == "cid-second"


def test_local_asr_uses_auto_language_and_segment_draft(songcut_wav, tmp_path, monkeypatch):
    from blisolver.schema import Segment
    model = tmp_path / "offline-model.bin"
    model.write_bytes(b"synthetic model identity")
    monkeypatch.setattr("blisolver.songcut.pipeline.require_whisper_runtime", lambda: "offline")
    monkeypatch.setattr("blisolver.songcut.pipeline.whisper_model_path", lambda: str(model))
    monkeypatch.setattr("blisolver.songcut.pipeline.validate_whisper_cli", lambda: str(model))
    def transcribe(audio, **kwargs):
        assert kwargs["lang"] == "auto"
        return [Segment(start=1, end=3, text="performed phrase", source="whisper")]
    monkeypatch.setattr("blisolver.songcut.pipeline.transcribe", transcribe)
    result = run(Manifest(clips=[{"id": "song", "source": str(songcut_wav())}], backend="whisper"),
                 tmp_path / "run", settings(tmp_path))
    assert result["status"] == "complete", result
    root = Path(result["clips"][0]["bundle_json"]).parent
    assert read_json(root / "lyrics.json")["timing_basis"] == "whisper_segments"


def test_changed_human_reviewed_lyrics_are_never_overwritten(songcut_wav, tmp_path):
    manifest = Manifest(clips=[{"id": "song", "source": str(songcut_wav())}])
    out = tmp_path / "run"
    result = run(manifest, out, settings(tmp_path))
    root = Path(result["clips"][0]["bundle_json"]).parent
    lyrics = read_json(root / "lyrics.json")
    lyrics.update(reviewed=True, review_status="reviewed", human_note="retained correction")
    atomic_json(root / "lyrics.json", lyrics)
    before = sha256(root / "lyrics.json")
    rerun = run(manifest, out, settings(tmp_path))
    assert rerun["clips"][0]["status"] == "failed"
    assert "protected human-reviewed" in rerun["clips"][0]["error"]
    assert sha256(root / "lyrics.json") == before
