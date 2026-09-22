# 制作歌切 / Songcut

`blisolver songcut` is an opt-in audio product, independent of Atlas's existing bundle schema.
It accepts observed BV/AV/URLs, local audio/video, or a JSON manifest. Use the existing runtime
wrapper from [SKILL.md](../SKILL.md). Install the `songcut` optional dependency group in that
runtime; FFmpeg is required. Local ASR additionally requires whisper.cpp and a readable GGML
model. Cloud ASR does not require local Whisper or LM Studio. Production preflight checks the
chosen dependencies before downloading media; `--plan` only validates the manifest and flags.

## Commands

With `SCRIPTS` anchored as described in the skill:

```bash
python3 "$SCRIPTS/blisolver_cli.py" songcut /path/to/audio.wav --start 12 --end 200 --out out/song --json
python3 "$SCRIPTS/blisolver_cli.py" songcut /path/to/audio.wav --backend whisper --out out/song --json
python3 "$SCRIPTS/blisolver_cli.py" songcut --manifest /path/to/batch.json --out out/batch --plan
python3 "$SCRIPTS/blisolver_cli.py" songcut --manifest /path/to/batch.json --out out/batch --json
```

Cloud commands require explicit `backend: dashscope` and a positive estimated `budget_cny`.
Read the user's existing authorization: no cloud calls are needed merely to test source changes.

| Argument | Meaning |
|---|---|
| positional `source` | Local media, observed BV/AV or supported URL; mutually exclusive with `--manifest` |
| `--manifest` | JSON batch; relative input/reference paths resolve beside the manifest |
| `--id`, `--title` | Single clip ID and song title; ID uses letters/numbers/underscore/hyphen |
| `--part` | Selected one-based source part |
| `--start`, `--end` | Source seconds, end exclusive; absent end means the remaining source |
| `--lang` | Actual audio language hint; absent means auto-detect |
| `--backend` | `none` (default), `whisper`, `dashscope` |
| `--normalization` | `preserve` (default constant gain), or explicit `dynamic` |
| `--reference-audio` | Repeat for existing recordings to compare |
| `--reference-lyrics` | Local UTF-8 reference text/LRC, for a single clip |
| `--lookup-lyrics` | Opt into LRCLIB search by supplied title, for cloud curation |
| `--budget-cny` | Positive cloud estimate limit; overrides manifest budget |
| `--upload` | `s3` (default) or explicit development-only `temporary` |
| `--workers` | Parallel clips, 1–7; downloads stay serial |
| `--out` | Stable output root; keep it for recovery; default is configured out directory + `songcut` |
| `--plan` | Validate and print normalized plan; no media probes, writes or network calls |
| `--json` | One result object on stdout, also the default; diagnostics go to stderr |

Single-clip flags cannot accompany a manifest. Batch-level flags override its corresponding
fields; CPU concurrency, detailed mastering, models and rate estimates are manifest-only.

## Batch manifest

Example structure (replace paths/ranges with the actual source; create this file beside inputs):

```json
{
  "clips": [
    {"id": "song-1", "source": "source.wav", "start": 12, "end": 200,
     "title": "Song title", "language": "zh", "reference_lyrics": "reference.txt"},
    {"id": "song-2", "source": "source.wav", "start": 215, "end": 420}
  ],
  "backend": "dashscope",
  "audio": {"mode": "preserve", "target_lufs": -16, "true_peak": -1.5,
            "lra": 11, "highpass": 80, "lowpass": 15000, "bitrate": "192k"},
  "cloud": {
    "base_url": "https://dashscope.aliyuncs.com",
    "filetrans_model": "qwen-audio-3.0-asr-flash-filetrans",
    "flash_model": "qwen-audio-3.0-asr-flash",
    "omni_model": "qwen3.8-omni-flash",
    "upload": "s3", "budget_cny": 2,
    "asr_cny_per_second": 0.00022,
    "omni_input_cny_per_million": 0.8,
    "omni_output_cny_per_million": 2.7,
    "max_tokens": 4096, "poll_seconds": 4, "poll_timeout": 1800,
    "short_windows": true, "presence": true, "curate": true, "review_chat": true
  },
  "reference_audio": ["existing.m4a"],
  "reference_lookup": false,
  "workers": 2, "cpu_workers": 2, "download_interval": 3
}
```

`reference_artist` is an optional per-clip reference-song artist. `performer_note` preserves the
caller's performer description in provenance; neither establishes singer identity.
Unknown fields, duplicate IDs, unsafe IDs, invalid times and non-finite numbers are rejected.
Provide exact song ranges for a long recording. This feature does not automatically find songs,
fetch creator feeds, identify singers, generate covers, assemble albums or publish a website.

## Cloud and reference providers

Configure `DASHSCOPE_API_KEY` in private local `.env` or the process's secret environment. It is
never a manifest, CLI or MCP argument. Set a matching region/workspace `cloud.base_url` as needed;
the default is the Beijing DashScope endpoint. Model names are configurable within the documented
Filetrans, Flash and Omni wire contracts; arbitrary model families are not interchangeable.

- **Default private storage:** set `BLISOLVER_S3_BUCKET` and, for non-AWS services,
  `BLISOLVER_S3_ENDPOINT` (HTTPS). Boto3 uses its standard AWS credential/profile chain and
  `AWS_DEFAULT_REGION` (uploader fallback: `us-east-1`). Uploads use deterministic content keys
  under `blisolver-songcut/`, with a one-day presigned GET URL. Configure the bucket's lifecycle
  for retention; the pipeline does not delete remote objects. Storage/egress fees are outside the
  model ledger. Short-lived credentials may make URLs expire sooner; allow access until ASR ends.
- **Explicit temporary mode:** `--upload temporary` uses DashScope's upload policy and `oss://`
  resource. Alibaba documents 48-hour validity and development-only use. Do not use it as durable
  production storage. A private upload receipt is reused for at most 40 hours.
- **Filetrans:** submits one complete mono 16 kHz WAV derived from the final playback file;
  saves provider task ID, polls, and downloads timestamped words without forwarding API credentials
  to the result object URL. Input duration is checked independently of the last recognized word.
- **Flash:** independent windows up to 60 seconds investigate empty results, long gaps, and
  English/Japanese tracks. Only core intervals are eligible for merging. Conflicts retain the
  full-file words and save both candidates for listening review.
- **Omni:** listens to bounded stereo windows for vocal-presence candidates, then groups/corrects
  indexed ASR words into live lyric lines. An independent semantic pass checks suspicious stream
  chat. Invalid/truncated model partitions fall back to recognized words; missing words remain
  uncertain. Discarded speech remains in `lyrics.json`; no unsupported word timestamps are made.
- **LRCLIB:** optional cached reference search. Candidates retain text similarity and per-line
  phonetic support from current ASR (simplified Chinese/pinyin, Japanese romanization, normalized
  text otherwise), not proof of the same performance. Only supported lines reach curation, also
  for local reference files; unsupported references stay unselected. Standard lyric
  timestamps are stripped, and the prompt forbids adding unsung verses/repetitions. Access/rate
  rejections stop new lookups in that batch; failed reference lookup does not destroy audio.

Default rates are configurable estimates inherited from the tested source workflow, not verified
current billing prices. Check the provider's rate for your model/region before paid use. Reservations
cover configured estimates for audio duration or bounded token output. The actual provider bill
can differ. Missing usage holds the reservation and blocks new paid calls; usage above its estimate
also blocks new calls. Do not report the ledger as an invoice or claim a strict monetary guarantee.

API references:
[Filetrans](https://help.aliyun.com/en/model-studio/fun-asr-recorded-speech-recognition-http-api),
[Flash](https://help.aliyun.com/en/model-studio/fun-asr-flash-recorded-speech-recognition-http-api),
[Omni JSON](https://help.aliyun.com/en/model-studio/qwen-structured-output),
[temporary uploads](https://help.aliyun.com/zh/model-studio/get-temporary-file-url/),
[LRCLIB](https://lrclib.net/docs),
[FFmpeg loudnorm](https://ffmpeg.org/ffmpeg-filters.html#loudnorm).

## Artifacts, quality and recovery

Read paths from the JSON result's `clips[]`, not from reconstructed IDs. Status is `complete` or
`partial` for the batch; individual clips are `complete` or `failed`. Exit code is nonzero for a
partial batch. `complete` means the requested processing finished, never publication/listening
approval. `backend: none` and duplicate matches deliberately carry empty lyrics with QC issues.

Each immutable parameter version lives under `clips/<id>/<identity>/`:

| Artifact | Meaning |
|---|---|
| `audio.m4a`, `audio.json` | AAC playback and actual mastering mode, input/output LUFS/TP/LRA |
| `lyrics.lrc`, `lyrics.txt`, `lyrics.json` | Draft live lyrics; word indices, discarded speech and audio hash |
| `words.json`, `words-primary.json`, `asr-raw.json` | Merged words, full-file words and redacted provider output when cloud ASR ran |
| `asr-input.wav`, `windows/`, `windows.json` | Exact request audio and independent short-window candidates when applicable |
| `presence.json`, `references.json` | Vocal-presence and reference candidates when applicable |
| `provenance.json` | Source SHA, normalized platform/ID/part/CID, effective range, playback SHA and ASR-input identity |
| `dedup.json`, `qc.json` | Recording evidence, unresolved issues and review status |
| `bundle.json` | Songcut receipt and SHA-256 map for derived files; not an Atlas bundle |
| `failure.json` | Failed stage record beside any completed local artifacts |

Source media remains untouched. Cutting/filtering uses 24-bit PCM; playback is 48 kHz stereo AAC.
Default constant gain respects true peak and preserves dynamics. Peak-limited tracks may remain
quieter than the LUFS target. Explicit dynamic mode uses two-pass loudnorm and verifies the actual
reported mode. Encoded audio is decoded and measured again. Peak/duration issues remain visible.
Recording matches use landmark retrieval plus dense aligned waveform/coherence checks, including
piecewise offsets for internal edits. Retrieval verifies at most six candidates and reports whether
it was exhaustive. This is recording evidence, not singer identity. High-fit matches keep their
audio/evidence and skip redundant ASR; they never copy another performance's lyrics or review.

Re-run the same manifest with the same output to resume; inspect `result.json`, `progress.json`
and per-clip `failure.json`. Validated artifact hashes permit stage reuse; source/range/audio/model/
reference changes produce new identities. Paid-call receipts are shared under `state/cloud/`, so
rebuilding a damaged LRC does not submit the same audio again. `state/budget.json` persists across
runs; increasing the budget is explicit and does not erase past spending.

Create `STOP` in the output root to block new paid calls; remove it to allow new calls again.
Known Filetrans task IDs can still be polled while stopped or after budget exhaustion. A timeout
while polling retains the ID for the next run. `submitting` / `submission_uncertain` receipts never
auto-resubmit: check the provider's task/billing records before any manual reconciliation. Do not
delete receipts or change output directories to bypass an uncertain charge. Failed terminal tasks
also stay terminal. Ordinary GET failures have bounded retries; paid POSTs have none.

All generated lyric/vocal assessments stay `draft` with `reviewed: false`. Human review in a
downstream library must bind to both final audio and lyric hashes; regeneration cannot inherit
that acceptance. If a generated lyric file has been changed and explicitly marked human-reviewed,
the same-version rebuild stops and preserves it for inspection. Album metadata, covers and
publication remain downstream.

## MCP

`make_songcut(manifest, output?)` starts a persisted worker and returns `job_id` immediately.
`get_songcut(job_id)` returns `running`, `complete`, `partial`, `failed` or `unknown`, plus the
producer's paths. Handles survive server restart. The default output is stable for the input
sources, IDs and ranges, even when model/budget settings change; pass an explicit stable `output`
to retain one budget/paid-call store across changes to the input batch.
Use only one active worker per output root; a kernel lock rejects concurrent writers.
Read this guide via `blisolver://guidance/references/songcut.md` when operating through MCP.
