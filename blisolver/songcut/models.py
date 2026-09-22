"""Validated, serializable production inputs. Credentials never belong in a manifest."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)


class ClipInput(Model):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
    source: str = Field(min_length=1)
    part: int | None = Field(default=None, ge=1)
    start: float = Field(default=0, ge=0)
    end: float | None = Field(default=None, gt=0)
    title: str = ""
    performer_note: str = ""  # caller-supplied provenance, never inferred singer identity
    language: str | None = None
    reference_lyrics: str | None = None  # local UTF-8 text, never a substitute for sung words
    reference_artist: str | None = None

    @model_validator(mode="after")
    def valid_range(self):
        if self.end is not None and self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class AudioOptions(Model):
    mode: Literal["preserve", "dynamic"] = "preserve"
    target_lufs: float = Field(default=-16, ge=-30, le=-5)
    true_peak: float = Field(default=-1.5, ge=-9, le=-0.5)
    lra: float = Field(default=11, ge=1, le=50)
    highpass: int = Field(default=80, ge=0, le=1000)
    lowpass: int = Field(default=15000, ge=2000, le=22000)
    bitrate: Literal["128k", "192k", "256k", "320k"] = "192k"


class CloudOptions(Model):
    base_url: str = "https://dashscope.aliyuncs.com"
    filetrans_model: str = "qwen-audio-3.0-asr-flash-filetrans"
    flash_model: str = "qwen-audio-3.0-asr-flash"
    omni_model: str = "qwen3.8-omni-flash"
    upload: Literal["s3", "temporary"] = "s3"
    budget_cny: float = Field(default=0, ge=0)
    # Estimates are configurable; they are not a promise about a provider's invoice.
    asr_cny_per_second: float = Field(default=0.00022, gt=0)
    omni_input_cny_per_million: float = Field(default=0.8, gt=0)
    omni_output_cny_per_million: float = Field(default=2.7, gt=0)
    max_tokens: int = Field(default=4096, ge=256, le=16384)
    poll_seconds: float = Field(default=4, ge=0, le=60)
    poll_timeout: float = Field(default=1800, gt=0)
    short_windows: bool = True
    presence: bool = True
    curate: bool = True
    review_chat: bool = True

    @model_validator(mode="after")
    def valid_endpoint(self):
        url = urlsplit(self.base_url)
        host = url.hostname or ""
        allowed = re.fullmatch(r"(?:dashscope(?:-intl|-us)?\.aliyuncs\.com|[a-zA-Z0-9-]+\."
                               r"(?:cn-beijing|ap-southeast-1|us-east-1)\.maas\.aliyuncs\.com)", host)
        if (url.scheme != "https" or url.username or url.password or url.query or url.fragment
                or url.path not in {"", "/"} or url.port not in {None, 443} or not allowed):
            raise ValueError("base_url must be an HTTPS DashScope endpoint without credentials")
        self.base_url = self.base_url.rstrip("/")
        return self


class Manifest(Model):
    clips: list[ClipInput] = Field(min_length=1)
    backend: Literal["none", "whisper", "dashscope"] = "none"
    audio: AudioOptions = Field(default_factory=AudioOptions)
    cloud: CloudOptions = Field(default_factory=CloudOptions)
    reference_audio: list[str] = Field(default_factory=list)
    reference_lookup: bool = False
    workers: int = Field(default=2, ge=1, le=7)
    cpu_workers: int = Field(default=2, ge=1, le=8)
    download_interval: float = Field(default=3, ge=0)

    @model_validator(mode="after")
    def valid_batch(self):
        if len({clip.id for clip in self.clips}) != len(self.clips):
            raise ValueError("clip IDs must be unique")
        if self.backend == "dashscope" and self.cloud.budget_cny <= 0:
            raise ValueError("dashscope requires a positive budget_cny")
        return self


class Word(Model):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(min_length=1)
    source: str

    @model_validator(mode="after")
    def valid_range(self):
        if self.end < self.start:
            raise ValueError("word ends before it starts")
        return self
