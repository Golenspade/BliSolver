"""DashScope clients with persistent paid-call receipts and a shared cost ledger.

POST is never automatically retried: a lost response can still have incurred a charge.
Read-only task polling may retry and always resumes the saved provider task ID.
"""

from __future__ import annotations

import base64
import math
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .models import CloudOptions
from .state import atomic_json, identity, read_json, redact, sha256


class CloudBlocked(RuntimeError):
    pass


def trusted_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password or
            not (host == "aliyuncs.com" or host.endswith(".aliyuncs.com"))):
        raise CloudBlocked("provider returned an untrusted resource URL")
    return url


class Ledger:
    def __init__(self, path: Path, limit: float, stop: Path):
        self.path, self.limit, self.stop = path, limit, stop
        self.lock = threading.RLock()
        self.call_locks = {}
        self.state = read_json(path, {"calls": {}, "blocked": False})
        changed = False
        for key, call in self.state["calls"].items():
            record = read_json(path.parent / "cloud" / f"{key}.json")
            if record and record.get("state") in {"submitting", "submission_uncertain"}:
                call["status"] = "billing_unknown"
                self.state["blocked"] = True
                changed = True
        if changed:
            atomic_json(path, self.state)

    def call_lock(self, key):
        with self.lock:
            return self.call_locks.setdefault(key, threading.RLock())

    def summary(self) -> dict:
        with self.lock:
            calls = self.state["calls"].values()
            return {"limit_cny": self.limit,
                    "settled_estimate_cny": sum(c.get("actual", 0) for c in calls),
                    "reserved_cny": sum(c["reserved"] for c in calls if "actual" not in c),
                    "blocked": self.state["blocked"], "billing": "configured_rate_estimates"}

    def reserve(self, key: str, amount: float):
        with self.lock:
            summary = self.summary()
            if self.stop.exists():
                raise CloudBlocked("STOP exists; no new paid requests")
            if self.state["blocked"]:
                raise CloudBlocked("ledger blocked by uncertain billing; inspect receipts")
            if key in self.state["calls"]:
                return
            total = summary["settled_estimate_cny"] + summary["reserved_cny"] + amount
            if total > self.limit + 1e-9:
                raise CloudBlocked("estimated cloud budget exhausted")
            self.state["calls"][key] = {"reserved": amount, "status": "reserved"}
            atomic_json(self.path, self.state)

    def settle(self, key: str, actual: float | None):
        with self.lock:
            call = self.state["calls"][key]
            if call["status"] == "settled":
                return  # historical estimates keep the rate used when the call was submitted
            if actual is None or not math.isfinite(actual) or actual < 0:
                call["status"] = "billing_unknown"
            else:
                call.update(actual=actual, status="settled")
            self.state["blocked"] = any(
                c["status"] == "billing_unknown" or c.get("actual", 0) > c["reserved"] + 1e-9
                for c in self.state["calls"].values())
            atomic_json(self.path, self.state)

    def uncertain(self, key: str):
        self.settle(key, None)


class DashScope:
    def __init__(self, options: CloudOptions, root: Path, ledger: Ledger,
                 transport=None, api_key: str | None = None):
        import requests
        self.options, self.root, self.ledger = options, root, ledger
        self.transport = transport or requests.Session()
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self.context = ""
        if not self.api_key:
            raise CloudBlocked("DASHSCOPE_API_KEY is required")

    def request(self, method, path, *, external=False, **kwargs):
        url = trusted_url(path) if external else self.options.base_url + path
        headers = kwargs.pop("headers", {})
        if not external:
            headers = {**headers, "Authorization": "Bearer " + self.api_key}
        retries = 3 if method == "GET" else 1
        for attempt in range(retries):
            try:
                response = self.transport.request(method, url, headers=headers,
                                                  timeout=(15, 180), allow_redirects=False,
                                                  **kwargs)
                if 200 <= response.status_code < 300:
                    return response.json()
                if method == "GET" and (response.status_code == 429 or
                                        response.status_code >= 500) and attempt+1 < retries:
                    time.sleep(.2 * (attempt+1))
                    continue
                raise CloudBlocked(f"provider HTTP {response.status_code}; details withheld")
            except CloudBlocked:
                raise
            except Exception:  # noqa: BLE001 - transport/JSON errors must hide credentials
                if method == "GET" and attempt+1 < retries:
                    time.sleep(.2 * (attempt+1))
                    continue
                raise CloudBlocked("provider request failed; response or billing may be unknown")
        raise CloudBlocked("provider request exhausted")

    def _receipt(self, key):
        return self.root / f"{key}.json"

    def _pricing(self):
        return self.options.model_dump(include={"asr_cny_per_second", "omni_input_cny_per_million",
                                                "omni_output_cny_per_million"})

    def _cost(self, raw, *, asr=False, pricing=None):
        pricing = pricing or self._pricing()
        usage = raw.get("usage") or {}
        try:
            if asr:
                duration = usage.get("duration", usage.get("seconds", usage.get("audio_duration")))
                return float(duration) * pricing["asr_cny_per_second"]
            prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
            completion = usage.get("completion_tokens", usage.get("output_tokens"))
            return (float(prompt)*pricing["omni_input_cny_per_million"] +
                    float(completion)*pricing["omni_output_cny_per_million"]) / 1e6
        except (ValueError, TypeError):
            return None

    def _paid(self, key, path, body, reserve, *, headers=None, asr=False):
        with self.ledger.call_lock(key):
            return self._paid_locked(key, path, body, reserve, headers=headers, asr=asr)

    def _paid_locked(self, key, path, body, reserve, *, headers=None, asr=False):
        dest = self._receipt(key)
        prior = read_json(dest)
        if prior:
            if prior["state"] == "complete":
                # Reconcile a crash between saving the response and settling its ledger entry.
                self.ledger.settle(key, self._cost(prior["response"], asr=asr, pricing=prior.get("pricing")))
                return prior["response"]
            self.ledger.uncertain(key)
            raise CloudBlocked("paid request incomplete; automatic resubmission prohibited")
        self.ledger.reserve(key, reserve)
        record = {"state": "submitting", "request": redact(body), "pricing": self._pricing()}
        atomic_json(dest, record)
        try:
            raw = self.request("POST", path, json=body, headers=headers or {})
        except Exception:
            atomic_json(dest, {**record, "state": "submission_uncertain"})
            self.ledger.uncertain(key)
            raise
        clean = redact(raw)
        atomic_json(dest, {**record, "state": "complete", "response": clean})
        self.ledger.settle(key, self._cost(raw, asr=asr))
        return clean

    def filetrans(self, audio: Path, duration: float, uploader, language=None) -> dict:
        params = {"channel_id": [0]}
        if language:
            params["language_hints"] = [language]
        key = identity({"kind": "filetrans", "sha": sha256(audio), "params": params,
                        "model": self.options.filetrans_model, "base": self.options.base_url})
        with self.ledger.call_lock(key):
            return self._filetrans_locked(key, params, audio, duration, uploader)

    def _filetrans_locked(self, key, params, audio, duration, uploader):
        dest = self._receipt(key)
        record = read_json(dest)
        if record and record["state"] == "complete":
            self.ledger.settle(key, self._cost(record["task_response"], asr=True, pricing=record.get("pricing")))
            return record["response"]
        if record and record["state"] in {"submitting", "submission_uncertain"}:
            self.ledger.uncertain(key)
            raise CloudBlocked("Filetrans submission uncertain; do not submit a second paid task")
        if record and record["state"] == "failed":
            raise CloudBlocked("saved Filetrans task failed; inspect its receipt")
        if not record:
            # Reserve BEFORE uploads and before paid submission. The upload is not a model call.
            self.ledger.reserve(key, math.ceil(duration)*self.options.asr_cny_per_second)
            url = uploader(audio, self.options.filetrans_model)
            # Uploads can take minutes; STOP or another call's billing failure may have arrived.
            self.ledger.reserve(key, math.ceil(duration)*self.options.asr_cny_per_second)
            body = {"model": self.options.filetrans_model, "input": {"file_urls": [url]},
                    "parameters": params}
            headers = {"X-DashScope-Async": "enable"}
            if url.startswith("oss://"):
                headers["X-DashScope-OssResourceResolve"] = "enable"
            submitting = {"state": "submitting", "request": redact(body),
                          "pricing": self._pricing(), "input_sha256": sha256(audio)}
            atomic_json(dest, submitting)
            try:
                submitted = self.request("POST", "/api/v1/services/audio/asr/transcription",
                                         json=body, headers=headers)
                task_id = submitted["output"]["task_id"]
                if not isinstance(task_id, str) or not task_id or "/" in task_id:
                    raise ValueError("invalid provider task ID")
            except Exception:  # noqa: BLE001 - any interruption here may have incurred a charge
                atomic_json(dest, {**submitting, "state": "submission_uncertain"})
                self.ledger.uncertain(key)
                raise CloudBlocked("Filetrans submission uncertain; see saved receipt")
            record = {**submitting, "state": "submitted", "task_id": task_id}
            atomic_json(dest, record)
        deadline = time.monotonic() + self.options.poll_timeout
        while True:
            raw = self.request("GET", "/api/v1/tasks/" + record["task_id"])
            output = raw.get("output", {})
            status = output.get("task_status")
            if status == "SUCCEEDED":
                self.ledger.settle(key, self._cost(raw, asr=True, pricing=record.get("pricing")))
                results = output.get("results", [])
                if len(results) != 1 or results[0].get("subtask_status", "SUCCEEDED") != "SUCCEEDED":
                    atomic_json(dest, {**record, "state": "failed", "task_response": redact(raw)})
                    raise CloudBlocked("Filetrans did not return one successful file")
                transcript = self.request("GET", results[0]["transcription_url"], external=True)
                record.update(state="complete", response=redact(transcript),
                              task_response=redact(raw))
                atomic_json(dest, record)
                return record["response"]
            if status in {"FAILED", "CANCELED", "UNKNOWN"}:
                record.update(state="failed", task_response=redact(raw))
                atomic_json(dest, record)
                self.ledger.settle(key, self._cost(raw, asr=True, pricing=record.get("pricing")))
                raise CloudBlocked("Filetrans task failed; retained task ID and diagnostics")
            if status not in {"RUNNING", "PENDING"}:
                raise CloudBlocked("unrecognized Filetrans task status; task ID retained")
            if time.monotonic() >= deadline:
                raise CloudBlocked("Filetrans still pending; rerun to poll the saved task ID")
            time.sleep(self.options.poll_seconds)

    @staticmethod
    def _audio_content(audio: Path):
        data = base64.b64encode(audio.read_bytes()).decode()
        if len(data) > 9_500_000:
            raise ValueError("inline audio exceeds request limit; use a shorter window")
        return {"type": "input_audio", "input_audio": {"data": "data:audio/wav;base64," + data}}

    def flash(self, audio: Path, duration: float, language=None):
        params = {"format": "wav", "sample_rate": "16000"}
        if language:
            params["language_hints"] = [language]
        body = {"model": self.options.flash_model, "parameters": params,
                "input": {"messages": [{"role": "user", "content": [self._audio_content(audio)]}]}}
        key = identity({"kind": "flash", "sha": sha256(audio), "params": params,
                        "model": self.options.flash_model, "base": self.options.base_url})
        return self._paid(key, "/api/v1/services/aigc/multimodal-generation/generation", body,
                          math.ceil(duration)*self.options.asr_cny_per_second,
                          headers={"X-DashScope-SSE": "disable"}, asr=True)

    def omni(self, prompt: str, *, audio: Path | None = None, duration=0):
        content = [{"type": "text", "text": prompt}]
        if audio:
            content.insert(0, self._audio_content(audio))
        body = {"model": self.options.omni_model,
                "messages": [{"role": "user", "content": content}], "stream": False,
                "max_tokens": self.options.max_tokens, "reasoning_effort": "none",
                "use_multichannel": bool(audio), "enable_search": False,
                "response_format": {"type": "json_object"}}
        key = identity({"kind": "omni", "prompt": prompt,
                        "context_audio_sha256": self.context,
                        "sha": sha256(audio) if audio else None, "max_tokens": self.options.max_tokens,
                        "model": self.options.omni_model, "base": self.options.base_url})
        estimate = ((len(prompt.encode()) + math.ceil(duration*50) + 1024) *
                    self.options.omni_input_cny_per_million + self.options.max_tokens *
                    self.options.omni_output_cny_per_million) / 1e6
        return self._paid(key, "/compatible-mode/v1/chat/completions", body, estimate)
