"""Sample-exact cutting, explicit mastering policy, and decoded-output QC via FFmpeg."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

from .models import AudioOptions
from .state import atomic_json, identity, read_json, sha256, valid_artifacts


class AudioEngine:
    def __init__(self, ffmpeg: str | None = None):
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg")
        if not self.ffmpeg:
            raise RuntimeError("FFmpeg is required for songcut")
        self.version = subprocess.check_output(
            [self.ffmpeg, "-version"], text=True, stderr=subprocess.DEVNULL,
        ).splitlines()[0]

    def run(self, args: list[str]) -> subprocess.CompletedProcess:
        result = subprocess.run([self.ffmpeg, "-nostdin", "-hide_banner", "-y", "-threads", "1",
                                 *args], capture_output=True, check=False)
        if result.returncode:
            # Media URLs / credentials can appear in FFmpeg stderr. Do not expose them.
            raise RuntimeError(f"FFmpeg failed (exit {result.returncode}); check local media")
        return result

    def pcm(self, source: Path, dest: Path, start=0.0, end: float | None = None,
            *, rate=48000, channels=2, filters="", bits=24) -> float:
        dest.parent.mkdir(parents=True, exist_ok=True)
        chain = f"atrim=start={start}" + (f":end={end}" if end is not None else "")
        chain += ",asetpts=PTS-STARTPTS" + ("," + filters if filters else "")
        self.run(["-v", "error", "-i", str(source), "-map", "0:a:0", "-vn", "-af", chain,
                  "-ar", str(rate), "-ac", str(channels), "-c:a", f"pcm_s{bits}le", str(dest)])
        seconds = len(self.samples(dest)) / 8000
        if seconds <= 0:
            raise ValueError("requested range contains no audio")
        if end is not None and abs(seconds - (end - start)) > .1:
            raise ValueError("requested range exceeds decoded source duration")
        return seconds

    def samples(self, source: Path, rate=8000):
        import numpy as np
        result = self.run(["-v", "error", "-i", str(source), "-vn", "-ac", "1", "-ar",
                           str(rate), "-f", "f32le", "-"])
        return np.frombuffer(result.stdout, dtype=np.float32).copy()

    def measure(self, source: Path, options: AudioOptions) -> dict:
        result = self.run(["-i", str(source), "-vn", "-af",
                           (f"loudnorm=I={options.target_lufs}:TP={options.true_peak}:"
                            f"LRA={options.lra}:print_format=json"), "-f", "null", "-"])
        matches = re.findall(r'\{\s*"input_i".*?\}', result.stderr.decode(errors="replace"), re.DOTALL)
        if not matches:
            raise RuntimeError("FFmpeg did not return loudness measurements")
        raw = json.loads(matches[-1])
        return {key: (float(value) if math.isfinite(float(value)) else None)
                if key != "normalization_type" else value for key, value in raw.items()}

    def master(self, source: Path, dest: Path, options: AudioOptions) -> dict:
        dest.mkdir(parents=True, exist_ok=True)
        key = identity({"pcm": sha256(source), "options": options.model_dump(),
                        "ffmpeg": self.version, "version": 1})
        receipt_path = dest / "audio.json"
        old = read_json(receipt_path)
        if old and old.get("key") == key and valid_artifacts(old):
            return old
        filters = []
        if options.highpass:
            filters.append(f"highpass=f={options.highpass}")
        filters.append(f"lowpass=f={options.lowpass}")
        filtered = dest / "filtered.wav"
        duration = self.pcm(source, filtered, filters=",".join(filters))
        before = self.measure(filtered, options)
        if before["input_i"] is None or before["input_tp"] is None:
            raise ValueError("audio is silent or too short for loudness measurement")
        output = dest / "audio.m4a"
        gain = min(options.target_lufs - before["input_i"],
                   options.true_peak - before["input_tp"] - .3)
        attempts = []
        for _ in range(3):
            if options.mode == "preserve":
                af = f"volume={gain:.6f}dB"
                mode = "constant_gain"
            else:
                af = (f"loudnorm=I={options.target_lufs}:TP={max(-9, options.true_peak - .3)}:"
                      f"LRA={options.lra}:measured_I={before['input_i']}:"
                      f"measured_TP={before['input_tp']}:measured_LRA={before['input_lra']}:"
                      f"measured_thresh={before['input_thresh']}:offset={before['target_offset']}:"
                      "linear=false:print_format=json")
                mode = "dynamic"
            result = self.run(["-i", str(filtered), "-vn", "-af", af, "-ar", "48000",
                               "-ac", "2", "-c:a", "aac", "-b:a", options.bitrate,
                               "-movflags", "+faststart", str(output)])
            after = self.measure(output, options)
            attempts.append({"gain_db": gain if options.mode == "preserve" else None,
                             "output_i": after["input_i"], "output_tp": after["input_tp"]})
            if after["input_tp"] is None:
                raise ValueError("encoded audio is silent")
            if after["input_tp"] <= options.true_peak + .1 or options.mode == "dynamic":
                break
            gain -= after["input_tp"] - options.true_peak + .2
        if options.mode == "dynamic":
            stats = re.findall(r'\{\s*"input_i".*?\}',
                               result.stderr.decode(errors="replace"), re.DOTALL)
            if not stats or json.loads(stats[-1])["normalization_type"] != "dynamic":
                raise RuntimeError("FFmpeg mastering mode did not match requested dynamic mode")
        decoded_duration = len(self.samples(output)) / 8000
        issues = []
        if after["input_tp"] > options.true_peak + .1:
            issues.append("encoded_true_peak_exceeds_target")
        if abs(decoded_duration - duration) > .1:
            issues.append("encoded_duration_mismatch")
        if abs(after["input_i"] - options.target_lufs) > 1:
            issues.append("loudness_below_target_peak_limited" if gain < 0 or
                          options.mode == "preserve" else "loudness_outside_target")
        receipt = {"key": key, "mode": mode, "source_sha256": sha256(source),
                   "audio": str(output.resolve()), "duration": duration,
                   "decoded_duration": decoded_duration, "input": before, "output": after,
                   "attempts": attempts, "issues": issues, "ffmpeg": self.version,
                   "artifacts": {str(output.resolve()): sha256(output)}}
        atomic_json(receipt_path, receipt)
        filtered.unlink(missing_ok=True)
        return receipt
