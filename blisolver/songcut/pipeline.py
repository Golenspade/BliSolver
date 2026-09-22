"""Recoverable songcut orchestration, with isolated per-clip failures and honest provenance."""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path

from ..config import Settings
from ..parts import part_url
from ..providers.base import Canonical, select_provider
from ..transcribe import (
    download_audio,
    require_whisper_runtime,
    transcribe,
    validate_whisper_cli,
    whisper_model_path,
)
from . import PIPELINE_VERSION
from .audio import AudioEngine
from .cloud import DashScope, Ledger
from .lyrics import (
    PRESENCE_PROMPT,
    curate,
    lrc,
    merge_window,
    parse_words,
    presence_candidate,
    review_chat,
    short_windows,
)
from .models import ClipInput, Manifest
from .references import ReferenceLookup, supported_reference
from .state import atomic_json, file_lock, identity, read_json, redact, sha256, valid_artifacts
from .storage import AudioUploader


def preflight(manifest: Manifest, settings: Settings):
    for module in ("numpy", "scipy", "requests", "boto3", "opencc", "pypinyin", "pykakasi", "rapidfuzz"):
        if importlib.util.find_spec(module) is None:
            raise RuntimeError("install BliSolver's [songcut] extra before production")
    if manifest.backend == "whisper":
        require_whisper_runtime()
    if manifest.backend == "dashscope":
        if not os.environ.get("DASHSCOPE_API_KEY"):
            raise RuntimeError("DASHSCOPE_API_KEY is required; configure it outside the manifest")
        if manifest.cloud.upload == "s3" and not os.environ.get("BLISOLVER_S3_BUCKET"):
            raise RuntimeError("set BLISOLVER_S3_BUCKET or explicitly choose temporary upload")
    return AudioEngine(settings.ffmpeg_path)


def acquire(clip: ClipInput, settings: Settings, root: Path, engine: AudioEngine) -> dict:
    local = Path(clip.source).expanduser()
    if local.is_file():
        path = local.resolve()
        return {"path": str(path), "sha256": sha256(path), "kind": "local",
                "duration": len(engine.samples(path))/8000}
    provider = select_provider(clip.source)
    canonical = provider.resolve(clip.source)
    if clip.part:
        canonical = Canonical(canonical.platform, canonical.id, clip.part,
                              part_url(canonical.url, clip.part))
    meta = provider.fetch_metadata(canonical, settings)
    if canonical.part > meta.parts:
        raise ValueError("requested part exceeds provider metadata")
    key = identity({"canonical": asdict(canonical), "source_part_id": meta.source_part_id})
    # A CID change cannot hit the downloader's old BV/P-only cache.
    scoped = replace(settings, cache_dir=settings.cache_dir / "songcut-sources" / key)
    path = download_audio(canonical, scoped).resolve()
    seconds = len(engine.samples(path))/8000
    expected = (meta.part_durations_s[canonical.part-1]
                if len(meta.part_durations_s) >= canonical.part else None)
    if expected and abs(seconds-expected) > max(2, expected*.02):
        raise ValueError("download duration disagrees with selected source part")
    record = {"path": str(path), "sha256": sha256(path), "kind": "provider",
              "canonical": asdict(canonical), "metadata": asdict(meta), "duration": seconds}
    # Pydantic warnings inside the dataclass become ordinary JSON values.
    record["metadata"]["warnings"] = [w.model_dump() for w in meta.warnings]
    atomic_json(root / "sources" / f"{key}.json", record)
    return record


def _backend_identity(manifest: Manifest):
    config = manifest.cloud.model_dump(exclude={"budget_cny", "poll_seconds", "poll_timeout"})
    if manifest.backend == "whisper":
        config = {"model_sha256": sha256(Path(whisper_model_path())),
                  "binary_sha256": sha256(Path(validate_whisper_cli()))}
    return {"backend": manifest.backend, "config": config if manifest.backend != "none" else {}}


def run(manifest: Manifest, out: Path, settings: Settings | None = None,
        *, client_factory=DashScope, uploader_factory=AudioUploader) -> dict:
    settings = settings or Settings.load()
    out = out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    with file_lock(out / ".run.lock"):
        engine = preflight(manifest, settings)
        from .dedup import RecordingIndex
        state = out / "state"
        ledger = Ledger(state / "budget.json", manifest.cloud.budget_cny, out / "STOP")
        cpu = threading.BoundedSemaphore(manifest.cpu_workers)
        lookup = ReferenceLookup(state / "references") if manifest.reference_lookup else None
        atomic_json(out / "manifest.json", manifest.model_dump())
        index = RecordingIndex(engine, state / "fingerprints")
        for path in manifest.reference_audio:
            index.add(Path(path).expanduser().resolve())
        acquired, results = [], {}
        backend_key = _backend_identity(manifest)
        last_download = 0.
        for clip in manifest.clips:
            try:
                is_remote = not Path(clip.source).expanduser().is_file()
                if is_remote:
                    time.sleep(max(0, manifest.download_interval - (time.monotonic()-last_download)))
                    last_download = time.monotonic()
                source = acquire(clip, settings, state, engine)
                if clip.start >= source["duration"] or (clip.end is not None and
                                                       clip.end > source["duration"]+.1):
                    raise ValueError("clip range is outside source audio")
                reference = Path(clip.reference_lyrics).read_text(encoding="utf-8") if clip.reference_lyrics else ""
                cut_key = identity({"source": source["sha256"], "start": clip.start,
                                    "end": clip.end, "ffmpeg": engine.version, "version": PIPELINE_VERSION})
                cut = state / "cuts" / f"{cut_key}.wav"
                cut_receipt = read_json(cut.with_suffix(".json"))
                if not valid_artifacts(cut_receipt):
                    duration = engine.pcm(Path(source["path"]), cut, clip.start, clip.end)
                    atomic_json(cut.with_suffix(".json"), {"duration": duration,
                                "artifacts": {str(cut): sha256(cut)}})
                duration = read_json(cut.with_suffix(".json"))["duration"]
                dedup = index.search(cut)
                index.add(cut)
                key = identity({"cut": cut_key, "audio": manifest.audio.model_dump(),
                                "clip": clip.model_dump(),
                                "canonical": source.get("canonical"),
                                "source_part_id": source.get("metadata", {}).get("source_part_id"),
                                "asr": backend_key, "language": clip.language,
                                "reference": identity(reference), "title": clip.title,
                                "reference_artist": clip.reference_artist,
                                "reference_lookup": manifest.reference_lookup,
                                "dedup": identity(dedup)})
                dest = out / "clips" / clip.id / key[:20]
                dest.mkdir(parents=True, exist_ok=True)
                acquired.append((clip, source, cut, duration, dedup, reference, dest))
            except Exception as exc:  # noqa: BLE001 - one bad input must not abort siblings
                results[clip.id] = {"id": clip.id, "status": "failed", "stage": "acquire",
                                    "error": redact(f"{type(exc).__name__}: {exc}")}

        def process(item):
            clip, source, cut, duration, dedup, reference, dest = item
            try:
                old = read_json(dest / "bundle.json")
                if old and old.get("status") == "complete" and valid_artifacts(old):
                    return {"id": clip.id, "status": "complete", "reused": True,
                            "bundle_json": str(dest / "bundle.json"), "audio": old["audio"]}
                prior_lyrics = read_json(dest / "lyrics.json", {})
                if (prior_lyrics.get("reviewed") is True or
                        prior_lyrics.get("review_status") in {"reviewed", "approved"}):
                    raise ValueError("protected human-reviewed lyrics changed; keep this version and inspect it")
                print(f"[songcut] {clip.id}: audio / {manifest.backend}", file=sys.stderr)
                with cpu:
                    audio = engine.master(cut, dest, manifest.audio)
                playback = Path(audio["audio"])
                playback_sha = sha256(playback)
                issues = list(audio["issues"])
                words, presence = [], []
                candidate = curate([])
                asr_info = {"backend": manifest.backend, "input_complete": False}
                duplicate = dedup["decision"] in {"exact_file", "same_recording_high_fit"}
                if duplicate:
                    issues.append("duplicate_candidate_kept_audio_skipped_asr")
                elif manifest.backend == "whisper":
                    with cpu:
                        segments = transcribe(playback, robust=True, lang=clip.language or "auto")
                    lines = [{"start": s.start, "end": s.end, "text": s.text,
                              "kind": "uncertain"} for s in segments
                             if 0 <= s.start <= s.end <= duration+.1]
                    if len(lines) != len(segments):
                        issues.append("invalid_local_asr_intervals")
                    candidate.update(lines=lines, timing_basis="whisper_segments")
                    asr_info.update(input_complete=True, input_duration=audio["decoded_duration"],
                                    input_audio_sha256=playback_sha,
                                    model_sha256=backend_key["config"]["model_sha256"])
                elif manifest.backend == "dashscope":
                    client = client_factory(manifest.cloud, state / "cloud", ledger)
                    client.context = playback_sha
                    uploader = uploader_factory(client, state / "uploads")
                    asr_file = dest / "asr-input.wav"
                    with cpu:
                        input_duration = engine.pcm(playback, asr_file, rate=16000, channels=1, bits=16)
                    if abs(input_duration - audio["decoded_duration"]) > .1:
                        raise ValueError("ASR input does not cover the full playback file")
                    asr_info.update(input_complete=True, input_duration=input_duration,
                                    input_sha256=sha256(asr_file), input_audio_sha256=playback_sha,
                                    model=manifest.cloud.filetrans_model)
                    raw = client.filetrans(asr_file, input_duration, uploader, clip.language)
                    atomic_json(dest / "asr-raw.json", raw)
                    words, parse_issues = parse_words(raw, input_duration, "filetrans")
                    issues.extend(parse_issues)
                    atomic_json(dest / "words-primary.json", [w.model_dump() for w in words])
                    window_evidence = []
                    if manifest.cloud.short_windows:
                        for n, window in enumerate(short_windows(words, input_duration, clip.language)):
                            wav = dest / "windows" / f"asr-{n}.wav"
                            with cpu:
                                length = engine.pcm(asr_file, wav, window["start"], window["end"],
                                                    rate=16000, channels=1, bits=16)
                            alternative_raw = client.flash(wav, length, clip.language)
                            alternative, faults = parse_words(alternative_raw, length, "flash", window["start"])
                            words, conflicts = merge_window(words, alternative, window)
                            issues.extend(faults+conflicts)
                            window_evidence.append({**window, "raw": alternative_raw,
                                "words": [w.model_dump() for w in alternative], "issues": faults+conflicts})
                            atomic_json(dest / "windows.json", window_evidence)
                    if manifest.cloud.presence:
                        for n, window in enumerate(short_windows([], input_duration)):
                            wav = dest / "windows" / f"presence-{n}.wav"
                            with cpu:
                                length = engine.pcm(playback, wav, window["start"], window["end"],
                                                    rate=16000, channels=2, bits=16)
                            raw_presence = client.omni(PRESENCE_PROMPT, audio=wav, duration=length)
                            try:
                                presence.append(presence_candidate(raw_presence, window["start"], length))
                            except (ValueError, KeyError, TypeError):
                                presence.append({"window": window, "status": "invalid_response",
                                                 "needs_review": True})
                                issues.append("invalid_presence_response")
                            atomic_json(dest / "presence.json", presence)
                    if lookup and clip.title:
                        try:
                            references = lookup.search(clip.title, clip.reference_artist, words, clip.language)
                            atomic_json(dest / "references.json", references)
                            if not reference and references["selected"]:
                                reference = references["selected"]["supported"]["text"]
                        except Exception as exc:  # noqa: BLE001 - references are optional evidence
                            issues.append("reference_lookup_failed")
                            atomic_json(dest / "references.json", {"error": redact(str(exc))})
                    if reference:
                        supported = supported_reference(reference, words, clip.language)
                        atomic_json(dest / "reference-support.json", supported)
                        reference = supported["text"]
                        if not reference:
                            issues.append("reference_not_supported_by_current_asr")
                    candidate = curate(words, client if manifest.cloud.curate else None, reference)
                    if manifest.cloud.review_chat:
                        candidate = review_chat(candidate, client)
                candidate.update(audio_sha256=playback_sha, audio=str(playback),
                                 review_status="draft", reviewed=False)
                if not candidate["lines"]:
                    issues.append("no_live_lyrics")
                if not presence:
                    issues.append("presence_not_assessed")
                atomic_json(dest / "words.json", [w.model_dump() for w in words])
                atomic_json(dest / "lyrics.json", candidate)
                atomic_json(dest / "dedup.json", dedup)
                atomic_json(dest / "provenance.json", {"source": source, "clip": clip.model_dump(),
                            "cut_sha256": sha256(cut), "audio_sha256": playback_sha,
                            "asr": asr_info, "pipeline_version": PIPELINE_VERSION})
                (dest / "lyrics.lrc").write_text(lrc(candidate["lines"]), encoding="utf-8")
                (dest / "lyrics.txt").write_text("\n".join(r["text"] for r in candidate["lines"])+"\n",
                                                  encoding="utf-8")
                atomic_json(dest / "qc.json", {"audio": audio, "asr": asr_info,
                            "issues": sorted(set(issues+candidate["issues"])),
                            "presence": presence, "review_status": "draft", "reviewed": False})
                artifacts = {str(p.resolve()): sha256(p) for p in dest.rglob("*") if p.is_file()
                             and p.name not in {"bundle.json", "failure.json"}}
                receipt = {"version": PIPELINE_VERSION, "id": clip.id, "status": "complete",
                           "audio": str(playback), "audio_sha256": playback_sha,
                           "lyrics_lrc": str(dest / "lyrics.lrc"), "review_status": "draft",
                           "issues": sorted(set(issues+candidate["issues"])), "artifacts": artifacts}
                atomic_json(dest / "bundle.json", receipt)
                (dest / "failure.json").unlink(missing_ok=True)
                return {"id": clip.id, "status": "complete", "audio": str(playback),
                        "bundle_json": str(dest / "bundle.json"), "reused": False}
            except Exception as exc:  # noqa: BLE001 - persist diagnostics and retain other clips
                result = {"id": clip.id, "status": "failed", "stage": "production",
                          "artifact_dir": str(dest), "error": redact(f"{type(exc).__name__}: {exc}")}
                atomic_json(dest / "failure.json", result)
                return result

        with ThreadPoolExecutor(max_workers=manifest.workers) as pool:
            futures = {pool.submit(process, item): item[0].id for item in acquired}
            for future in as_completed(futures):
                result = future.result()
                results[result["id"]] = result
                atomic_json(out / "progress.json", list(results.values()))
        ordered = [results[clip.id] for clip in manifest.clips]
        result = {"version": PIPELINE_VERSION,
                  "status": "complete" if all(r["status"] == "complete" for r in ordered) else "partial",
                  "output": str(out), "clips": ordered, "budget": ledger.summary()}
        atomic_json(out / "result.json", result)
        return result
