"""CLI entry point (SPEC §9). Orchestrates resolve -> probe -> transcript -> frames -> merge.

Build state: spine (resolve, subtitle probe, bundle write) is live. Whisper/frames/vision call
their stage modules, which fail loud until their build step lands.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys

from .cache import fs_key, load_json, save_json
from .config import Settings
from .danmaku import represent_danmaku
from .merge import build_bundle, write_bundle
from .parts import run_parts, select_parts
from .player_api import ViewError
from .probe import probe
from .providers.base import Canonical, select_provider
from .schema import Frame, Segment, Transcript
from .transcribe import WHISPER_MODEL, download_audio, transcribe

# NOTE: `probe` here is the metadata pre-flight probe (probe.py), used by the `probe` CLI verb
# and overridable as `cli.probe` in tests. Platform-specific subtitle acquisition + the quality
# gate now live entirely inside each Provider (see harvest/providers/); this module only asks
# the URL-selected provider for a SubtitleOutcome (decide_transcript below).


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="harvest", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="run the full ingest pipeline for a video/part")
    ingest.add_argument("url")
    ingest.add_argument(
        "--part", type=int, default=None, help="1-based part index (default: from URL)"
    )
    ingest.add_argument("--all-parts", action="store_true", help="loop every part (D12)")
    ingest.add_argument(
        "--force-whisper", action="store_true", help="skip subtitle, always Whisper"
    )
    ingest.add_argument(
        "--robust", action="store_true", help="disable condition_on_previous_text"
    )
    ingest.add_argument("--no-vision", action="store_true", help="skip frame captioning")
    ingest.add_argument(
        "--dedup-threshold", type=int, default=None,
        help="phash hamming distance to collapse near-duplicate frames (default: 10)",
    )
    ingest.add_argument(
        "--scene-threshold", type=float, default=None,
        help="DEPRECATED, ignored (D13 replaced scene-cut detection); use --dedup-threshold",
    )
    ingest.add_argument("--out", default=None, help="output root (default: ./out)")
    ingest.add_argument(
        "--no-frame-images", action="store_true", help="omit PNGs from out/ (D8)"
    )
    ingest.add_argument(
        "--lang", default=None,
        help="pin transcription language (default: zh for bilibili, auto-detect for YouTube)",
    )
    ingest.add_argument(
        "--danmaku", action="store_true",
        help="opt-in: fetch + mirror the bilibili danmaku (audience comment) track (bilibili only)",
    )
    ingest.add_argument(
        "--interactions", action="store_true",
        help="opt-in: fetch bilibili command-danmaku aggregates — 投票 votes + 评分 grades "
             "(bilibili only; independent of --danmaku)",
    )
    ingest.add_argument(
        "--ocr", action="store_true",
        help="opt-in: detect + OCR burned-in (hard) subtitles into an independent `ocr` track "
             "(Phase C; runs an isolated OCR worker — requires .ocr-venv). Auto: only runs when fewer "
             "hardsubs are detected than a soft-subtitle track left unset or rejected.",
    )
    ingest.add_argument(
        "--force-ocr", action="store_true",
        help="with --ocr: skip the hard-sub pre-detection and always run dense-sample OCR "
             "(use when detection misses a hard-sub track you know is present)",
    )

    probe_cmd = sub.add_parser("probe", help="cheap pre-flight metadata probe, no media")
    probe_cmd.add_argument("url")

    sub.add_parser("mcp", help="run the MCP server over stdio (Phase E Agent interface)")

    return p.parse_args(argv)


def apply_overrides(settings: Settings, args) -> list[str]:
    """Apply CLI levers onto Settings; return human-readable warnings for the caller to print."""
    warnings: list[str] = []
    if args.out:
        from pathlib import Path

        settings.out_dir = Path(args.out)
    if args.dedup_threshold is not None:
        settings.phash_dedup_threshold = args.dedup_threshold
    if getattr(args, "scene_threshold", None) is not None:
        warnings.append(
            "--scene-threshold is deprecated and ignored (D13 replaced scene-cut detection "
            "with periodic-sample + phash dedup); use --dedup-threshold instead."
        )
    return warnings


def decide_transcript(canonical, meta, settings, args):
    # The only place a platform name remains: choosing the Whisper-fallback default language.
    # This is a default, not a control-flow branch — acquisition is fully delegated below.
    default_lang = args.lang if args.lang else (
        "zh" if canonical.platform == "bilibili.com" else None
    )
    if args.force_whisper:
        return _whisper(canonical, settings, args,
                        reason="forced via --force-whisper", lang=default_lang)

    provider = select_provider(canonical.url)
    outcome = provider.fetch_subtitle(canonical, settings, meta, pinned_lang=args.lang)
    if outcome is None or not outcome.accepted:
        reason = outcome.source_reason if outcome else "no usable subtitle"
        gate = outcome.quality_gate if outcome else None
        return _whisper(canonical, settings, args, reason=reason, gate=gate, lang=default_lang)
    return Transcript(
        source=outcome.source, source_reason=outcome.source_reason,
        language=outcome.language, quality_gate=outcome.quality_gate,
        segments=outcome.segments,
    )


def _whisper(canonical, settings, args, *, reason, gate=None, lang=None) -> Transcript:
    # D6 transcript cache: keyed by identity + the params that change the output.
    key = fs_key(
        canonical.platform, canonical.id, canonical.part,
        stage="transcript", force_whisper=args.force_whisper,
        robust=args.robust, model=WHISPER_MODEL,
    )
    cached = load_json(settings.cache_dir, "transcript", key)
    if cached is not None:
        segments = [Segment(**s) for s in cached]
        # Provenance backfill (schema 1.1): old caches lack `source`; tag whisper so fuse/downstream
        # see the provenance even on a pre-1.1 cache hit. New caches already carry it.
        for seg in segments:
            if seg.source is None:
                seg.source = "whisper"
        print(
            f"[{canonical.id} p{canonical.part}] whisper "
            f"({len(segments)} seg, cached): {reason}"
        )
    else:
        print(f"[{canonical.id} p{canonical.part}] whisper: {reason} -> downloading audio...")
        audio = download_audio(canonical, settings)
        print(f"[{canonical.id} p{canonical.part}] transcribing {audio.name} ({WHISPER_MODEL})...")
        segments = transcribe(audio, robust=args.robust, lang=lang)
        save_json(settings.cache_dir, "transcript", key, [s.model_dump() for s in segments])
    return Transcript(
        source="whisper",
        source_reason=reason,
        language=lang,
        model=WHISPER_MODEL,
        robust=args.robust,
        quality_gate=gate,
        segments=segments,
    )


def process_part(canonical: Canonical, settings: Settings, args) -> None:
    provider = select_provider(canonical.url)
    meta = provider.fetch_metadata(canonical, settings)
    transcript = decide_transcript(canonical, meta, settings, args)

    frames = []
    frame_sources = {}
    vision_model = None
    if not args.no_vision:
        from .frames import download_video, extract_frames

        print(f"[{canonical.id} p{canonical.part}] preparing video + frames...")
        video = download_video(canonical, settings)
        frames, frame_sources = extract_frames(canonical, video, settings)
        print(f"[{canonical.id} p{canonical.part}] {len(frames)} frames after dedup")
        if frames:
            frames = _caption(canonical, frames, frame_sources, settings)
            vision_model = settings.lmstudio_vision_model

    danmaku = None
    if args.danmaku:
        if not hasattr(provider, "fetch_danmaku"):
            print(f"[{canonical.id} p{canonical.part}] --danmaku ignored: "
                  f"not supported on {canonical.platform}")
        elif not settings.lmstudio_danmaku_model:
            print(f"[{canonical.id} p{canonical.part}] --danmaku ignored: "
                  f"HARVEST_DANMAKU_MODEL not set")
        else:
            fetch = provider.fetch_danmaku(canonical, settings)
            # Danmaku gets its OWN fixed window cadence, decoupled from frame/transcript chunk
            # boundaries: the crowd's pace has nothing to do with slide cuts, and aligning to
            # frames would dump a static slide's minutes of danmaku into one giant window.
            danmaku = represent_danmaku(
                canonical, fetch, settings,
                duration_s=meta.duration_s, window_s=settings.danmaku_window_s,
            )

    interactions = None
    if args.interactions:
        if not hasattr(provider, "fetch_interactions"):
            print(f"[{canonical.id} p{canonical.part}] --interactions ignored: "
                  f"not supported on {canonical.platform}")
        else:
            interactions = provider.fetch_interactions(canonical, settings)

    ocr_track = _maybe_ocr(canonical, settings, args, transcript, log=lambda m: print(m))

    # Phase D fusion: provenance tags are already on the segments (transcribe/provider outlets);
    # fuse annotates diagnostics (ASR hallucination + OCR cross-verification) onto the
    # transcript's source_reason. Runs automatically whenever an OCR track is present — the
    # diagnosis is downstream's authority signal (when ASR hallucinates, OCR is the corroborated
    # truth in the affected windows).
    if ocr_track is not None:
        from .fuse import fuse
        fusion = fuse(transcript, ocr_track)
        transcript = fusion.transcript
        if fusion.diagnostics:
            print(f"[{canonical.id} p{canonical.part}] fusion: "
                  f"{'; '.join(fusion.diagnostics)}")

    bundle = build_bundle(
        canonical, meta, transcript, frames, settings,
        vision_model=vision_model, danmaku=danmaku, interactions=interactions, ocr=ocr_track,
    )
    out = write_bundle(
        bundle, settings, frame_sources=frame_sources, frame_images=not args.no_frame_images
    )
    n = len(transcript.segments)
    print(
        f"[{canonical.id} p{canonical.part}] {transcript.source}: "
        f"{n} segments, {len(frames)} frames, "
        f"{"no OCR" if ocr_track is None else f"ocr={len(ocr_track)} cues"} -> {out}"
    )


def _maybe_ocr(canonical, settings, args, transcript, *, log) -> list | None:
    """Phase C: detect, then OCR, the burned-in subtitle track into an independent `ocr` track.

    Skips entirely when `--ocr` is off. With `--ocr`: probes hardsubs unless `--force-ocr` is set,
    and skips dense-sample OCR when no hardsubs are detected (saves minutes on soft-sub-only
    videos). Short-circuits when the OCR isolate isn't configured (no-op + one-line diagnostic), so a
    missing ``.ocr-venv`` never breaks ingest. Requires the cached video file (frames.py downloads it
    once), so we lazily fetch it here on the rare `--ocr --no-vision` path that skipped it."""
    if not getattr(args, "ocr", False):
        return None
    if not settings.ocr_worker_path or not settings.ocr_venv_python:
        log(f"[{canonical.id} p{canonical.part}] --ocr skipped: OCR isolate not configured "
            f"(.ocr-venv or scripts/ocr_worker.py missing; Phase C setup)")
        return None

    from .frames import download_video
    from .detect_hardsubs import detect_hardsubs
    from .ocr import ocr_subtitle

    log(f"[{canonical.id} p{canonical.part}] OCR: preparing video...")
    video = download_video(canonical, settings)

    if not getattr(args, "force_ocr", False):
        verdict = detect_hardsubs(canonical, video, settings)
        log(f"[{canonical.id} p{canonical.part}] OCR: hardsub detect -> "
            f"{'present' if verdict.has_hardsubs else 'absent'} "
            f"(conf={verdict.confidence}, {verdict.positive}/{verdict.sampled}; {verdict.reason})")
        if not verdict.has_hardsubs:
            return None

    log(f"[{canonical.id} p{canonical.part}] OCR: dense-sample pass (this can take minutes)...")
    segs = ocr_subtitle(canonical, video, settings)
    log(f"[{canonical.id} p{canonical.part}] OCR: {len(segs)} burned-in cues")
    return segs or None


def _caption(canonical, frames, frame_sources, settings):
    """Step 5: D7 projector probe + per-frame captioning, all-or-nothing caption cache (D10)."""
    from .vision import PROMPT_VERSION, caption_frames, verify_projector

    frameset = hashlib.sha1("".join(f.phash for f in frames).encode()).hexdigest()[:10]
    key = fs_key(
        canonical.platform, canonical.id, canonical.part,
        stage="captions", model=settings.lmstudio_vision_model,
        prompt=PROMPT_VERSION, frameset=frameset,
    )
    cached = load_json(settings.cache_dir, "captions", key)
    if cached is not None:
        print(f"[{canonical.id} p{canonical.part}] captions: cached ({len(cached)})")
        return [Frame(**f) for f in cached]

    verify_projector(settings)  # D7: hard-stop if the mmproj isn't really reading images
    print(f"[{canonical.id} p{canonical.part}] captioning {len(frames)} frames via "
          f"{settings.lmstudio_vision_model}...")
    captioned = caption_frames(frames, frame_sources, settings)
    save_json(settings.cache_dir, "captions", key, [f.model_dump() for f in captioned])
    return captioned


def _run_probe(args) -> int:
    """`probe` verb: cheap pre-flight metadata, JSON on stdout only. Diagnostics/errors go to
    stderr so a caller can safely parse stdout as JSON. `probe` takes only a url (no levers),
    so `apply_overrides` doesn't apply here."""
    settings = Settings.load()
    try:
        canonical = select_provider(args.url).resolve(args.url)
        result = probe(canonical, settings)
    except (ViewError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result.model_dump()))
    return 0


def _run_ingest(args) -> int:
    settings = Settings.load()
    for w in apply_overrides(settings, args):
        print(f"[warn] {w}")

    try:
        canonical = select_provider(args.url).resolve(args.url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Enumerate parts once (cheap, no media), then run the single-part pipeline per selected
    # part with failure isolation (D12). bilibili.tv has no player-API view endpoint yet
    # (deferred), so ingest stays bilibili.com/youtube.com-only, same as probe.
    if canonical.platform == "bilibili.tv":
        print("error: ingest is bilibili.com-only; bilibili.tv unsupported (deferred)",
              file=sys.stderr)
        return 1
    provider = select_provider(canonical.url)
    total = provider.enumerate_parts(canonical, settings)

    parts = select_parts(args, canonical, total=total)
    if len(parts) > 1:
        print(f"[{canonical.id}] {total} parts; running {len(parts)} -> {parts}")

    results = run_parts(
        canonical, parts, settings=settings, args=args, processor=process_part
    )

    failed = [r for r in results if not r.ok]
    if len(results) > 1 or failed:
        for r in results:
            status = "ok" if r.ok else f"FAILED ({r.error})"
            print(f"[{canonical.id} p{r.part}] {status}")
    return 1 if failed else 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # CJK titles on Windows consoles
    except Exception:
        pass

    args = parse_args(argv)
    if args.command == "probe":
        return _run_probe(args)
    if args.command == "mcp":
        from .mcp import main as mcp_main
        return mcp_main()
    return _run_ingest(args)


if __name__ == "__main__":
    raise SystemExit(main())
