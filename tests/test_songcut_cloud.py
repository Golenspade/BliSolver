import json
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("requests")

from blisolver.songcut.cloud import CloudBlocked, DashScope, Ledger, trusted_url
from blisolver.songcut.models import CloudOptions
from blisolver.songcut.state import atomic_json, read_json, redact


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


class Transport:
    def __init__(self, rows):
        self.rows, self.calls = list(rows), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        value = self.rows.pop(0)
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, Response) else Response(value)


def client(tmp_path, rows, budget=1, **options):
    ledger = Ledger(tmp_path / "budget.json", budget, tmp_path / "STOP")
    transport = Transport(rows)
    api = DashScope(CloudOptions(budget_cny=budget, poll_seconds=0, **options),
                   tmp_path / "calls", ledger, transport, "offline-test-token")
    return api, ledger, transport


def succeeded(duration=5):
    return {"output": {"task_status": "SUCCEEDED", "results": [{
        "transcription_url": "https://result.oss-cn-beijing.aliyuncs.com/a?Signature=private",
        "subtask_status": "SUCCEEDED"}]}, "usage": {"duration": duration}}


def test_uncertain_paid_submission_never_retries_even_after_restart(tmp_path):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"audio")
    api, ledger, transport = client(tmp_path, [TimeoutError("private request")])
    for _ in range(2):
        with pytest.raises(CloudBlocked, match="uncertain"):
            api.filetrans(path, 5, lambda *args: "oss://private", "zh")
    assert len(transport.calls) == 1
    again, _, no_requests = client(tmp_path, [])
    with pytest.raises(CloudBlocked, match="uncertain"):
        again.filetrans(path, 5, lambda *args: "oss://private", "zh")
    assert not no_requests.calls
    assert ledger.summary()["reserved_cny"] > 0


def test_pending_filetrans_resumes_get_only_and_does_not_leak_token(tmp_path):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"audio")
    api, _, transport = client(tmp_path, [{"output": {"task_id": "task-1"}},
                                        {"output": {"task_status": "RUNNING"}}], poll_timeout=.000001)
    with pytest.raises(CloudBlocked, match="pending"):
        api.filetrans(path, 5, lambda *args: "oss://private", "zh")
    (tmp_path / "STOP").touch()
    resumed, ledger, resumed_transport = client(tmp_path, [succeeded(), {"transcripts": []}])
    assert resumed.filetrans(path, 5, lambda *args: pytest.fail("must not upload"), "zh") == {"transcripts": []}
    assert all(method == "GET" for method, _, _ in resumed_transport.calls)
    assert "Authorization" not in resumed_transport.calls[-1][2]["headers"]
    assert transport.calls[0][2]["headers"]["X-DashScope-Async"] == "enable"
    assert ledger.summary()["reserved_cny"] == 0
    assert "private" not in "".join(p.read_text() for p in (tmp_path / "calls").glob("*.json"))


def test_budget_reserved_before_request_and_concurrent_limit(tmp_path):
    _, ledger, _ = client(tmp_path, [], budget=.5)
    def attempt(i):
        try:
            ledger.reserve(str(i), .2)
            return True
        except CloudBlocked:
            return False
    with ThreadPoolExecutor(7) as pool:
        assert sum(pool.map(attempt, range(20))) == 2
    assert ledger.summary()["reserved_cny"] == pytest.approx(.4)


def test_budget_stop_and_unknown_usage_block_new_paid_calls(tmp_path):
    api, ledger, transport = client(tmp_path, [{"choices": [], "usage": {}}])
    api.omni("JSON example")
    assert ledger.summary()["blocked"]
    with pytest.raises(CloudBlocked, match="billing"):
        api.omni("JSON another")
    assert len(transport.calls) == 1
    assert read_json(tmp_path / "budget.json")["calls"]


def test_stop_blocks_a_reserved_but_not_submitted_call(tmp_path):
    _, ledger, _ = client(tmp_path, [])
    ledger.reserve("x", .1)
    (tmp_path / "STOP").touch()
    with pytest.raises(CloudBlocked, match="STOP"):
        ledger.reserve("x", .1)


def test_flash_has_word_payload_and_redacted_audio_receipt(tmp_path):
    path = tmp_path / "a.wav"
    path.write_bytes(b"test audio")
    raw = {"output": {"sentence": {"words": []}}, "usage": {"duration": 1}}
    api, _, transport = client(tmp_path, [raw])
    assert api.flash(path, 1, "ja") == raw
    assert api.flash(path, 1, "ja") == raw
    assert len(transport.calls) == 1
    body = transport.calls[0][2]["json"]
    assert body["parameters"]["language_hints"] == ["ja"]
    assert body["input"]["messages"][0]["content"][0]["input_audio"]["data"].startswith("data:audio/")
    assert "dGVzdCBhdWRpbw==" not in "".join(p.read_text() for p in (tmp_path / "calls").glob("*.json"))


@pytest.mark.parametrize("url", ["http://x.aliyuncs.com/a", "https://aliyuncs.com.evil/a",
                                "https://user:password@x.aliyuncs.com/a"])
def test_untrusted_result_urls_rejected(url):
    with pytest.raises(CloudBlocked):
        trusted_url(url)


def test_redaction():
    value = redact({"authorization": "Bearer test", "file_urls": ["x"], "text": "ok",
                    "url": "https://x/?Signature=token", "error": "sk-privatekey"})
    encoded = json.dumps(value)
    assert "privatekey" not in encoded and "Signature" not in encoded
    assert value["text"] == "ok"


def test_crash_during_submission_blocks_other_clips_on_restart(tmp_path):
    ledger = Ledger(tmp_path / "budget.json", 1, tmp_path / "STOP")
    ledger.reserve("interrupted", .1)
    atomic_json(tmp_path / "cloud" / "interrupted.json", {"state": "submitting"})
    restarted = Ledger(tmp_path / "budget.json", 1, tmp_path / "STOP")
    with pytest.raises(CloudBlocked, match="billing"):
        restarted.reserve("another-clip", .1)


@pytest.mark.parametrize("url", ["https://private.oss-cn-beijing.aliyuncs.com",
                                "https://dashscope.aliyuncs.com/arbitrary-path"])
def test_manifest_cannot_route_api_credentials_to_arbitrary_object_storage(url):
    with pytest.raises(ValueError, match="DashScope endpoint"):
        CloudOptions(base_url=url)
