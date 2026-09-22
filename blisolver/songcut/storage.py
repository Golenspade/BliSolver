"""Private object handoff to ASR. Signed URLs never enter public production receipts."""

from __future__ import annotations

import os
import time
from pathlib import Path

from .cloud import CloudBlocked, DashScope, trusted_url
from .state import atomic_json, identity, read_json, sha256


class AudioUploader:
    def __init__(self, client: DashScope, root: Path):
        self.client, self.root = client, root

    def __call__(self, audio: Path, model: str) -> str:
        if self.client.options.upload == "temporary":
            return self.temporary(audio, model)
        return self.s3(audio)

    def s3(self, audio: Path) -> str:
        import boto3
        from botocore.config import Config
        bucket = os.environ.get("BLISOLVER_S3_BUCKET")
        if not bucket:
            raise CloudBlocked("set BLISOLVER_S3_BUCKET or explicitly select temporary upload")
        endpoint = os.environ.get("BLISOLVER_S3_ENDPOINT")
        if endpoint and not endpoint.startswith("https://"):
            raise CloudBlocked("S3 endpoint must use HTTPS")
        client = boto3.client("s3", endpoint_url=endpoint,
                              region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
                              config=Config(signature_version="s3v4"))
        key = "blisolver-songcut/" + sha256(audio) + ".wav"
        # Idempotent object key; only generated audio is sent. The bucket remains private.
        client.upload_file(str(audio), bucket, key, ExtraArgs={"ContentType": "audio/wav"})
        url = client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key},
                                           ExpiresIn=86400)
        if not url.startswith("https://"):
            raise CloudBlocked("S3 generated a non-HTTPS URL")
        return url

    def temporary(self, audio: Path, model: str) -> str:
        key = identity({"sha": sha256(audio), "model": model,
                        "base": self.client.options.base_url})
        cache = self.root / f"upload-{key}.json"
        old = read_json(cache)
        if old and time.time() - old["time"] < 40*3600:
            return old["resource"]
        policy = self.client.request("GET", "/api/v1/uploads",
                                     params={"action": "getPolicy", "model": model})["data"]
        host = trusted_url(policy["upload_host"])
        object_key = policy["upload_dir"].rstrip("/") + "/" + key + ".wav"
        fields = {"OSSAccessKeyId": policy["oss_access_key_id"], "policy": policy["policy"],
                  "Signature": policy["signature"], "key": object_key,
                  "x-oss-object-acl": policy["x_oss_object_acl"],
                  "x-oss-forbid-overwrite": policy["x_oss_forbid_overwrite"],
                  "success_action_status": "200"}
        try:
            with audio.open("rb") as handle:
                response = self.client.transport.request("POST", host, data=fields,
                    files={"file": (audio.name, handle, "audio/wav")},
                    timeout=(15, 180), allow_redirects=False)
            if response.status_code not in {200, 201, 204}:
                raise ValueError("upload failed")
        except Exception:  # noqa: BLE001 - never expose upload policies or signed request errors
            raise CloudBlocked("temporary audio upload failed; details withheld")
        resource = "oss://" + object_key
        atomic_json(cache, {"resource": resource, "time": time.time()})
        return resource
