# Pipeline stages

## Cache identity

Expensive artifacts are cached under the project cache directory using source identity plus stage
parameters. Audio depends on the video identity; transcript, captions, OCR, and danmaku add their
model/prompt/knob fingerprints. A changed `--force-whisper`, language, robust mode, frame dedup
threshold, OCR knob, or model must not return a stale incompatible result. Before relying on a
language switch, inspect the current cache key in `blisolver/cli.py::_whisper`; the current
implementation includes force/robust/model parameters but has historically not included every
caller option. Clear or isolate the relevant cache when in doubt rather than assuming the design
invariant is fully enforced.

## Audio and whisper.cpp

`blisolver/transcribe.py` downloads best audio through yt-dlp, converts it to 16 kHz mono WAV with
ffmpeg, invokes `whisper-cli`, and parses the generated SRT. `BLISOLVER_WHISPER_CLI` can select the
binary and `BLISOLVER_WHISPER_MODEL` selects the GGML model path. `--robust` maps to whisper.cpp's
`--no-context`. The resulting cues are tagged `source="whisper"`.

Do not rely on the older faster-whisper/CUDA optional-dependency description as the current backend;
inspect `blisolver/transcribe.py` when troubleshooting the actual machine.

## Frames and phash

Unless `--no-vision` is supplied, blisolver downloads a video-only stream, samples frames periodically
with ffmpeg, computes perceptual hashes, and compares each candidate with the last kept frame. The
phash threshold is controlled by `--dedup-threshold`. Deduplication happens before captioning to
avoid paying the vision cost for repeated slides.

`--no-frame-images` affects delivery only: frame metadata and generated notes remain in JSON/Markdown,
but PNGs are not copied into the bundle and frame paths become null.

## Vision projector check

`blisolver/vision.py` calls the LM Studio OpenAI-compatible endpoint. Before captioning, it fingerprints
loaded model metadata and, when needed, renders a random nonce image and requires the model to read it
back. A missing/unbound mmproj projector can produce plausible hallucinations, so a failed nonce
check is a hard stop rather than a silent caption omission. `--no-vision` is the explicit way to skip
this stage.

## Hard-subtitle OCR

`--ocr` enables detection and dense sampling of burned-in subtitles. The OCR worker runs in an
isolated `.ocr-venv` through `scripts/ocr_worker.py`; RapidOCR/OpenCV do not enter blisolver's main
Python environment. `--force-ocr` skips the hardsub pre-detection when detection is known to miss a
track. A missing isolate degrades to a clear no-op rather than poisoning the main dependency graph.

OCR cues are stored in `Bundle.ocr` on their own timeline with `source="ocr"`. Sparse `Frame.ocr`
text is visual slide/UI OCR and is not the same track.

## Fusion

When an OCR track exists, `blisolver/fuse.py` compares it with the picked transcript and adds
cross-verification or hallucination diagnostics to the transcript reason. Fusion does not replace
the selected transcript field; it records why a consumer should weigh an affected interval carefully.

## Danmaku

`--danmaku` is a bilibili-only opt-in. The provider fetches the protobuf census, then
`blisolver/danmaku.py` uses fixed approximately 15-second content-time windows. Ordinary duplicate
text may be clustered through a tightly fenced LM call; elevated `high_like` and suspected author
lines are extracted before clustering and remain verbatim. The Markdown view can cap ordinary lines,
but `bundle.json` is the complete mirror.

Danmaku is audience/reception context below the transcript, not a fact source. `high_like` is a
platform-promotion signal. `author="owner"` or `"staff"` is explicitly unverified because it is a
lossy poster-hash match.

## Interactions

`--interactions` is independent of `--danmaku`. It fetches bilibili command-danmaku and mechanically
whitelists `#VOTE#` and `#GRADE#`. Vote questions/options are structural uploader framing plus crowd
tallies; grades are server-provided 0–10 averages and counts. No LLM is used. Other command widgets
are discarded. The track remains below transcript authority.

## MCP

`blisolver mcp` exposes the verified operations to an Agent over stdio. It starts asynchronous ingest
jobs and reads completed bundles for transcript, unified timeline, or visual context. It does not
change provider or stage semantics and should inherit the same environment/auth rules as the CLI.
