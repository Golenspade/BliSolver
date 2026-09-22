import json

import pytest

pytest.importorskip("requests")
pytest.importorskip("boto3")

from blisolver.songcut.cloud import CloudBlocked
from blisolver.songcut.models import Word
from blisolver.songcut.references import ReferenceLookup
from blisolver.songcut.storage import AudioUploader
from tests.test_songcut_cloud import Response, client


def test_temporary_upload_cache_and_policy_credentials(tmp_path):
    policy = {"data": {"upload_host": "https://bucket.oss-cn-beijing.aliyuncs.com",
                       "upload_dir": "private", "oss_access_key_id": "private-access",
                       "policy": "private-policy", "signature": "private-signature",
                       "x_oss_object_acl": "private", "x_oss_forbid_overwrite": "true"}}
    api, _, transport = client(tmp_path, [policy, Response({}, 200)], upload="temporary")
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"sample")
    uploader = AudioUploader(api, tmp_path / "uploads")
    url = uploader(audio, "test-model")
    assert url.startswith("oss://private/")
    assert uploader(audio, "test-model") == url
    assert len(transport.calls) == 2
    assert "Authorization" not in transport.calls[1][2].get("headers", {})
    assert transport.calls[1][2]["allow_redirects"] is False
    stored = "".join(p.read_text() for p in (tmp_path / "uploads").glob("*.json"))
    assert "private-policy" not in stored and "private-signature" not in stored


def test_s3_private_upload_and_signed_url(tmp_path, monkeypatch):
    import boto3
    calls = []
    class S3:
        def upload_file(self, *args, **kwargs):
            calls.append((args, kwargs))

        def generate_presigned_url(self, operation, **kwargs):
            assert operation == "get_object"
            assert kwargs["ExpiresIn"] == 86400
            return "https://storage.example/private?signature=private"
    monkeypatch.setenv("BLISOLVER_S3_BUCKET", "private-bucket")
    monkeypatch.setenv("BLISOLVER_S3_ENDPOINT", "https://storage.example")
    monkeypatch.setattr(boto3, "client", lambda *a, **k: S3())
    api, _, _ = client(tmp_path, [])
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"sample")
    assert "signature" in AudioUploader(api, tmp_path / "uploads")(audio, "test")
    assert calls[0][1]["ExtraArgs"] == {"ContentType": "audio/wav"}
    assert calls[0][0][2].startswith("blisolver-songcut/")


def test_reference_candidate_strips_standard_timestamps_and_caches(tmp_path):
    class Transport:
        calls = 0
        def get(self, *args, **kwargs):
            self.calls += 1
            return Response([{"id": 17, "trackName": "song", "artistName": "artist",
                              "syncedLyrics": "[03:00.00]你好世界", "plainLyrics": None}])
    transport = Transport()
    lookup = ReferenceLookup(tmp_path, transport)
    words = [Word(start=1, end=3, text="你好世界", source="test")]
    one = lookup.search("song", None, words)
    assert one["selected"]["text"] == "你好世界"
    assert one["timing_imported"] is False
    assert lookup.search("song", None, words) == one
    assert transport.calls == 1


def test_reference_rate_rejection_stops_batch(tmp_path):
    class Transport:
        calls = 0
        def get(self, *args, **kwargs):
            self.calls += 1
            return Response({}, 429)
    transport = Transport()
    lookup = ReferenceLookup(tmp_path, transport)
    for title in ("one", "two"):
        with pytest.raises(RuntimeError):
            lookup.search(title, None, [])
    assert transport.calls == 1


def test_stop_during_upload_prevents_paid_submit(tmp_path):
    api, _, transport = client(tmp_path, [])
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"sample")
    def upload(*args):
        (tmp_path / "STOP").touch()
        return "oss://private"
    with pytest.raises(CloudBlocked, match="STOP"):
        api.filetrans(audio, 5, upload)
    assert not transport.calls


def test_paid_identical_call_is_serialized_across_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    raw = {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    api, _, transport = client(tmp_path, [raw])
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: api.omni("JSON same request"), range(4)))
    assert results == [raw]*4
    assert len(transport.calls) == 1
    assert "offline-test-token" not in json.dumps(results)


def test_chinese_homophones_and_japanese_reference_support():
    from blisolver.songcut.references import phones, supported_reference
    heard = [Word(start=0, end=2, text="泥好世界", source="test")]
    candidate = supported_reference("[03:00.00]你好世界", heard, "zh")
    assert candidate["text"] == "你好世界"
    assert candidate["lines"][0]["phonetic_support"] > .9
    assert phones("こんにちは", "ja") == phones("コンニチハ", "ja")


def test_historical_cost_does_not_change_on_cache_read(tmp_path):
    raw = {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    api, ledger, transport = client(tmp_path, [raw])
    api.omni("JSON request")
    before = ledger.summary()["settled_estimate_cny"]
    api.options.omni_input_cny_per_million = .0001
    api.options.omni_output_cny_per_million = .0001
    api.omni("JSON request")
    assert ledger.summary()["settled_estimate_cny"] == before
    assert len(transport.calls) == 1
