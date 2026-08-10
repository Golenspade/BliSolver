# Pipeline stages

## Caching

Expensive artifacts are cached under the data directory, keyed by the `{platform, id, part}` identity
plus the parameters that change the output. Audio depends only on the video. Transcript adds the
force/robust/model/**language** parameters. Captions add the model, prompt version, and a fingerprint
of the frame set. OCR and danmaku add their own knobs.

The language belongs in the transcript key because it becomes whisper-cli's `-l` flag. It was
previously absent, so a re-run with a different `--lang` returned the earlier language's transcript
from cache with no indication the flag had been ignored. If you suspect a stale entry, check
`cli.py::_whisper` for the current key rather than assuming.

A cache hit on a pre-1.1 entry backfills `Segment.source` so provenance is present even on old data.

## Subtitle acquisition

Candidate tracks are ordered human-Chinese, Chinese ASR, then foreign-language ASR. Each candidate is
fetched and screened for redaction markers before acceptance; the first clean track wins. Rejections
travel back to the caller as `(track_key, reason)` pairs and surface in
`Transcript.source_reason` — they are not printed, because stdout is reserved for machine output.

The accepted track then faces a quality gate that adapts to source type and language, plus a duration
sanity check and, for parts after the first, a part-match assertion against part 1's text. Any failure
falls the run through to Whisper with the reason recorded.

See `references/provider-guide.md` for the full candidate list and the language-proxy consequences.

## Audio and whisper.cpp

`transcribe.py` downloads best audio through yt-dlp, converts it to 16 kHz mono WAV with ffmpeg, runs
`whisper-cli`, and parses the emitted SRT back into segments tagged `source="whisper"`.

`BLISOLVER_WHISPER_CLI` selects the binary; `BLISOLVER_WHISPER_MODEL` selects the GGML weights.
`--robust` maps to whisper.cpp's `--max-context 0`, which is how that engine spells "disable
condition-on-previous-text"; there is no `--no-context` flag. whisper.cpp reports no per-segment
confidence, so `Segment.confidence` stays null rather than being fabricated.

The binary and the weights are independent failure modes. `doctor` checks both.

## Frames and perceptual hashing

Unless `--no-vision` is given, the pipeline downloads a video-only stream, samples frames
periodically with ffmpeg, computes a perceptual hash for each, and compares against the last kept
frame. `--dedup-threshold` sets the Hamming distance. Dedup happens before captioning so the vision
cost is not paid for repeated slides.

`--no-frame-images` affects delivery only: metadata and captions stay in the bundle, PNGs are not
copied, and each `Frame.path` becomes null.

## Vision

`vision.py` calls LM Studio's OpenAI-compatible endpoint. Before captioning it fingerprints the
loaded model and, when needed, renders a nonce image and requires the model to read it back. A
missing or unbound mmproj projector produces confident nonsense, so a failed nonce check is a hard
stop rather than a silent caption omission. `--no-vision` is the explicit way to skip the stage;
bypassing the check is not an option.

## Burned-in subtitle OCR

`--ocr` detects and then densely samples burned-in subtitles. The worker runs inside `.ocr-venv`
through `scripts/ocr_worker.py`, so RapidOCR and OpenCV never enter the application environment —
the same isolation model as the whisper-cli shim. `--force-ocr` skips pre-detection for a track you
know exists but detection misses.

A missing isolate degrades to a no-op with a diagnostic instead of failing the ingest. Cues land in
`Bundle.ocr` on their own timeline with `source="ocr"`. `Frame.ocr` is sparse slide/UI text and a
different track.

## Fusion

When an OCR track exists, `fuse.py` compares it against the picked transcript and appends
cross-verification or hallucination diagnostics to the transcript's reason. It never replaces the
selected transcript; it records why a consumer should weigh an interval carefully. Where ASR
hallucinated, the corroborated OCR text is the better evidence for that window.

## Danmaku

`--danmaku` is bilibili-only and opt-in, and needs `BLISOLVER_DANMAKU_MODEL` — without it the flag is
accepted and then ignored at runtime, which `doctor` warns about.

The provider fetches the protobuf census and `danmaku.py` buckets it into fixed ~15-second
content-time windows, chosen independently of transcript and frame chunk boundaries because the
crowd's pace has nothing to do with slide cuts. Near-identical lines are clustered through a tightly
fenced LM call; promoted (`high_like`) and suspected-author lines are extracted verbatim *before*
clustering so a flood cannot absorb them.

`bundle.md` may cap ordinary lines per window with a `+N more` marker; `bundle.json` is always the
complete mirror. Danmaku is audience reception, below the transcript, never a fact source.
`author="owner"`/`"staff"` is a lossy 32-bit hash match and explicitly unverified.

## Interactions

`--interactions` is independent of `--danmaku` and uses no model. It fetches bilibili command-danmaku
and mechanically whitelists `#VOTE#` and `#GRADE#`; other widgets are discarded. Vote questions and
option labels are verbatim uploader framing with crowd tallies; grades are server-computed means on a
0–10 scale with a rater count. Both sit below transcript authority.

Note that star clicks also post literal digit danmaku, so with `--danmaku` on, the same act appears in
both tracks by design.
