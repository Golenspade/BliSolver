# harvest — Design Spec

> Authoritative design for **harvest**: a multi-source video → knowledge-bundle ingestion tool.
> This is the single source of truth for *what* to build and *why*. [PROTOCOL.md](PROTOCOL.md) is
> the machine-facing contract the downstream **Atlas** project codes against; [README.md](README.md)
> is the human quickstart.
>
> harvest is the successor to `bili-tool` (bilibili-only). It keeps that tool's proven, platform-
> agnostic back-end and generalizes the acquisition front-end to multiple sources.

---

## 1. What it is

harvest is the **ingestion front-door** for the Atlas knowledge base. Given a video URL from a
supported source, it produces a timeline-aligned, self-contained **bundle**:

- an **original-language transcript** (reuse trustworthy captions, else faster-whisper), and
- **per-frame visual notes** (OCR + figure/slide captions via a local vision model).

The tool **starts** at a URL and **ends** at `out/<id>-p<part>/` (`bundle.md` + `bundle.json` +
`frames/`). It does **not** summarize or extract entities — that judgment lives downstream in Atlas.
Keep this seam clean: harvest is a deterministic batch unit; everything interpretive happens
elsewhere.

The defining superpower is **authenticated acquisition from walled/rich media sources, then do
whatever with what you pulled** — today that "whatever" is the bundle (plus two bilibili-only opt-in
tracks: the `--danmaku` crowd mirror and the `--interactions` command-danmaku aggregates); the
architecture is built so future outputs (raw media collection, thumbnails, AV remux) are additive.

## 2. Scope

**v1 targets:** single **public** videos on **bilibili.com** and **YouTube**. Content is "people
talking" (lectures, talks) — single-speaker, slide-or-talking-head.

**Deferred** (architecture accommodates, not built): playlists, live VODs, members-only/age-gated
bulk flows, `bilibili.tv`, and roadmap stages (thumbnail metadata, AV remux for collection).
Danmaku shipped as the opt-in `--danmaku` crowd-mirror track, and command danmaku (投票 votes / 评分
grades) as the opt-in `--interactions` track (both bilibili only; see §8 and PROTOCOL.md).

## 3. Stack

- **Python 3.11.** Download/metadata/subtitles via **yt-dlp** (Python API, not shelling out).
- **Transcription:** `faster-whisper` `large-v3`, CUDA (RTX 4090). CUDA-12 runtime via `nvidia-*-cu12`
  wheels registered on PATH (ctranslate2 uses plain `LoadLibrary`).
- **Frames:** `ffmpeg` (system) periodic sampling; `Pillow` + `imagehash` phash dedup.
- **Vision:** OpenAI-compatible endpoint (**LM Studio**) serving a VL model + its mmproj projector.
- **Schema:** `pydantic` v2. **Downloads:** `aria2c` preferred for throttled CDNs, native fallback.

## 4. Architecture — modular monolith

harvest is a **modular monolith**, not microservices: the pipeline shuttles large local artifacts
(video, audio, frames) and is GPU-bound on one local GPU, so data locality and a shared local cache
beat any network decomposition. Two internal seams give the extensibility of services without the
distributed-systems tax:

### 4.1 Provider seam (per-source acquisition)

A `Provider` is selected by URL and owns **only** the platform-specific acquisition. Everything
downstream consumes normalized outputs and never sees a platform.

```
Provider (protocol; one impl per source):
  matches(url) -> bool                                 # registry selects the provider
  resolve(url) -> Canonical                            # {platform, id, part, url}
  auth_opts(settings) -> dict                          # yt-dlp opts fragment: platform auth
  fetch_metadata(canonical, settings) -> SourceMetadata
  enumerate_parts(canonical, settings) -> int          # bilibili: pages; youtube: 1
  fetch_subtitle(canonical, settings, meta) -> list[Segment] | None
```

- **`SourceMetadata`** is the normalized metadata type every provider produces. It **replaces**
  the old `ViewData`-or-yt-dlp-`info` branching — `merge`/`probe` read only `SourceMetadata`, no
  platform branches. bilibili fills it from the player API; YouTube from yt-dlp's `info`.
- **Media download stays shared** (one yt-dlp downloader for audio/video). The provider contributes
  only `auth_opts()` — the *one* thing that actually varies per source. A genuinely non-yt-dlp
  source would be the trigger to push download into the provider; don't prepay for it.

### 4.2 Stage sequence (per-capability processing)

The agnostic core runs as a sequence of stages over local artifacts: **transcribe → frames →
vision → merge**. Each is independent, cacheable, and skippable (`--no-vision`). This stays the
existing implicit sequence — **not** formalized into a registry yet; that's a named-trigger later
extraction. Future capabilities (danmaku, thumbnails, remux) slot in as new stages.

### 4.3 The one external service

The **LLM backend stays external** (LM Studio over HTTP). It earned a service boundary because it's
heavy, separately managed, and potentially shared with Atlas. Nothing else does.

### 4.4 When to extract a service later (named triggers, not now)

1. A second concurrent GPU consumer → a job-queue worker service.
2. Acquisition needs a different trust/network context than processing → split the *provider* layer
   out along the seam that already exists.
3. The bundle becomes a polled API → a thin serving layer *around* the monolith.

## 5. Core flow

1. **Resolve** — the registry picks a provider by URL; `provider.resolve(url)` → `Canonical
   {platform, id, part, url}`. The single-part triple is the atomic, cached, identity-bearing unit.
2. **Metadata** — `provider.fetch_metadata` → `SourceMetadata` (title, uploader, `uploader_id`,
   duration, `published_at`, pages/parts). One cheap call; the source of truth for bundle metadata.
3. **Transcript decision** — see §6.
4. **Frames** (unless `--no-vision`): periodic sampling (`ffmpeg fps=1/interval`, default 6s) +
   phash dedup **before** captioning (captioning is the cost sink; order matters). Then caption each
   surviving frame independently with the VL model.
5. **Merge** — align transcript segments + frame notes on a shared timeline; emit `bundle.json`
   (precise backing record) and `bundle.md` (the product Atlas ingests).

**Caching** — every stage keyed by `video-identity + stage-param-hash`, never bare identity. Changing
`--force-whisper`/`--dedup-threshold`/`--robust`/`--lang` must not force a re-download or
re-transcribe of unrelated stages, but must not silently return a stale result computed under
different params either.

## 6. Transcript logic (per source)

Common invariant: produce an **original-language** transcript; **never silently align the wrong
text**; and record provenance, which is load-bearing (§8). On the *structural* axis — is this the
right track, does it cover the video, is it non-empty — bias toward Whisper when in doubt (a
truncated/wrong-language track hardens into accepted truth in Atlas — the worst failure mode). On the
*quality* axis — are the words subtly off — the sources differ: bilibili gates on calibrated CJK
quality metrics; YouTube deliberately does **not** (unreliable across ~150 languages), instead
preferring the cheaper auto-caption and leaving the quality call to the consumer's `--force-whisper`.
Provenance is what makes that trade honest: Atlas always knows which tier it got.

**bilibili** (unchanged from bili-tool):
- Prefer human sub > AI caption; original zh only. yt-dlp does **not** surface bilibili AI subs, so a
  cookie-authenticated `x/player/v2` fallback (no WBI signing) fetches them; `ai_type` 0 = original
  transcription, 1 = translation (translations ignored).
- **Quality gate** (any one metric trips → Whisper): punct density, dup ratio, non-CJK ratio, cps.
  Thresholds are calibratable config, not law.
- **#6357 two-tier assertion:** tier-1 duration sanity (last cue vs part duration, 0.70–1.10); tier-2
  (part > 1) reject if text is near-identical to part 1 (yt-dlp's #6357 signature). Fail → Whisper.

**YouTube** (yt-dlp-native — no wall, no player-API workaround). Trust order **`human-sub > auto-sub >
whisper`**, matching bilibili's *ordering*; the only asymmetry with bilibili is the gate (structural,
below), not the preference. Resolve the target language as `--lang` if pinned, else `info["language"]`
if truthy, else **unknown**, then:
  - **known `L`:** human `subtitles[L]` (exact key), else a **BCP-47-clean** region/script variant of
    `L`'s base subtag (`de`↔`de-DE`, `zh-Hant`↔`zh-Hans`; see clean-tag rule below) → reuse
    (`human-sub`). Else the auto-caption for `L`, preferring key `L-orig` then `L` → candidate
    `auto-sub`. `info["language"]` is best-effort and often a fuller BCP-47 tag (`en-US`, `zh-Hant`)
    than the bare/script-tagged caption keys, so a non-exact `L` falls back to its base language
    subtag's original-audio key (`en-US`→`en-orig`, `zh-CN`→`zh-Hans-orig`); this stays
    original-audio-safe and never reuses a machine-translated track.
  - **unknown:** if `automatic_captions` has exactly **one** `*-orig` key, that is the original-audio
    ASR → candidate `auto-sub`; otherwise (0 or >1 `-orig` keys) → **Whisper** (we won't guess).
  - a candidate `auto-sub` must clear the **structural net** (below) or it → **Whisper**.
- **Auto-captions are reused** (`info["automatic_captions"]`, provenance `auto-sub`) — a deliberate
  reversal of the earlier skip-auto rule, chosen for **cost**: Whisper `large-v3` on a ~90-min lecture
  is real GPU/time, and modern Google ASR for major languages is punctuated and readable. Auto-captions
  are authority-ranked *below* Whisper (§8) yet acquisition-*preferred* over it — the same split
  bilibili already makes for its AI caption. `--force-whisper` is the consumer's override when the
  auto-caption is judged too low.
- **Acquisition format:** fetch the track's server-side **`srt`** (already de-rolled — one clean cue
  per line) and parse with `parse_srt`; strip leading `>>` speaker markers, keep `[music]` cues. This
  sidesteps yt-dlp's rolling, `<c>`-tagged VTT/json3, which repeats every line (~2× cue inflation).
- **Clean-tag matching only — exact key, else a well-formed region/script variant.** Prefer
  `subtitles[L]`; failing that, reuse a same-base-language human key **only if it is a clean BCP-47
  tag** — primary language subtag followed solely by valid script (4-alpha, `Hant`) and/or region
  (2-alpha `US` / 3-digit `419`) subtags (`de-DE`, `zh-Hant`, `es-419`). `subtitles` also carries
  **hash-suffixed community-translation** tracks (e.g. `en-US-njLgzgtehjs` on a Spanish video) and
  yt-dlp's per-track disambiguation suffixes (`en-eEY6OEpapPo` when a language has multiple tracks);
  both end in a segment that is *not* a valid region/script subtag, fail the clean-tag test, and are
  **never** matched — so a translation is never reused as the original. Unlike the auto path, human
  `subtitles` has no `-orig` original-audio marker, so this clean-tag rail is the *only* safety net;
  that is why unrestricted primary-subtag fuzzy matching stays forbidden. (`-orig` is yt-dlp's
  original-audio marker on the *auto* side and is exactly why the auto lookup can fall back more
  freely.)
- **Structural validity net (NOT a linguistic quality gate).** Three language-agnostic checks,
  thresholds in config; any failure → Whisper: (1) **presence** — parses to more than a few cues;
  (2) **duration-coverage** — last cue within `0.70–1.10` of `duration` (truncation guard, shared with
  bilibili D4 tier-1); (3) **chars-per-second floor** — catches an "empty-but-full-duration" track
  (music/silence spanning the timeline with almost no words). These need no per-language calibration,
  so unlike the CJK `quality.py` gate they cannot misfire across languages — which is why the earlier
  "no gate on YouTube" argument (it conflated *linguistic quality* with *structural validity*) does not
  block them. Subtler failure (wrong words, bad audio) is out of the net's scope — the consumer's
  `--force-whisper` call.
- **Deliberate limitations:** no language-ID check (the `-orig`-key discipline covers the common
  mislabel case); and pinning `--lang` to a non-original language may return that language's MT
  auto-caption — a deliberate user override, not the default original-language behavior.
- **Field mapping** (from the probe): `uploader_id` ← `info["channel_id"]` (`UC…`, stable — not the
  mutable `@handle` in yt-dlp's `uploader_id`); `published_at` ← `info["timestamp"]` (unix epoch →
  UTC `…Z`), falling back to `upload_date` (`YYYYMMDD` → `T00:00:00Z`); subtitle tracks are
  `list[{ext,url,name}]` — human tracks fetch the **`vtt`** entry (`parse_vtt`); auto tracks fetch the
  **`srt`** entry (`parse_srt`), alongside the existing `parse_bcc`.

**Language:** default `language=None` (Whisper auto-detects from the opening window). Shared `--lang`
override lets the caller pin the language (from probe hints); platform-aware default is `zh` for
bilibili, `None` for YouTube. Whisper picks one language for the whole audio (no code-switching), so
`--lang` is how a caller deliberately pins the dominant language of mixed content.

## 7. Testing

Deterministic and **offline** by default: inject `opener`s returning canned JSON, use trimmed yt-dlp
`info` dicts as fixtures, never touch the network in the default suite. One opt-in `@live` smoke test
(excluded from the default run) hits a single known-stable public video to catch drift. Capture
trimmed fixtures early — they double as documentation of the provider contract surface.

**Must-verify empirical probes — RESOLVED (5-video live probe, yt-dlp 2026.06.09):**
1. Is `info["language"]` reliable? **No — treat as best-effort.** Correct when present (`en`,`ko`),
   but `None` for 2/5, including a Spanish video that *did* carry an `es` track. `language=None` does
   **not** mean "no captions." → so unknown-language no longer goes *straight* to Whisper: §6 first
   tries the sole `*-orig` auto-caption, and only then falls back.
2. Human vs auto distinct? **Yes, cleanly** — separate `subtitles` / `automatic_captions` dicts; a
   human `en` and auto `en` coexist without overlap. This clean split is what lets §6 tier them
   (`human-sub` before `auto-sub`). (Wrinkle: `subtitles` also holds hash-suffixed community
   translations → clean-tag match only, never a suffixed key; see §6.)

**Auto-caption probe — RESOLVED (yt-dlp 2026.06.09, `-QFHIoCo-Ko` + 2 controls):**
3. Format: auto tracks offer `json3/srv1-3/ttml/srt/vtt`. `vtt`/`json3` are **rolling** (each line
   re-emitted as it scrolls, `<c>` word-timing tags, ~2× cue inflation); the server-side **`srt`** is
   **de-rolled** (clean discrete cues, punctuated, only a leading `>>` speaker marker to strip). → §6
   fetches `srt`.
4. `-orig` marker: original-audio ASR sits under a single `L-orig` key (`en-orig` on the two
   English controls; **zero** `-orig` keys on a no-speech video → clean Whisper fallback). The "exactly
   one `*-orig`" rule for the unknown-language branch held across all probes.

Trimmed fixtures seed the YouTube branches in `tests/fixtures/youtube/` and must cover: `human-sub`
(human track wins even when an auto track coexists), `auto-sub` accepted (known-lang, no human track),
`auto-sub` via sole-`-orig` (unknown lang), auto rejected by the structural net → `whisper`, and
no-captions baseline → `whisper`.

## 8. Design decisions & rationale (the calls that govern the code)

- **bundle.md is the product; bundle.json is the backing record.** Atlas is an LLM reading prose, not
  an indexer reading fields. Provenance and the transcript decision live in a readable `bundle.md`
  header, because the model weighs authority the way it weighs any source's — there is no code
  ranking `transcript.source`.
- **Provenance = production method, separate from language.** `human-sub` | `auto-sub` | `whisper`;
  language is its own field. Authority order (documented for Atlas): `human-sub` > `whisper` >
  `auto-sub`.
- **bundle.md is slide-chunked, always.** Chunk = "what was on screen + the speech while it was up."
  Boundaries = deduped frame timestamps when vision is on, else a fixed wall-clock window (60s).
  Segments assigned whole by start timestamp, never split. One coarse `[mm:ss]` header per chunk.
- **Frame candidates = periodic sampling + phash dedup**, not scene-cut detection: target content is
  continuous-shot screen recordings with soft slide transitions (no hard cuts). Same goal ("one frame
  per stable slide"), more robust mechanism. Dedup compares against the last *kept* frame.
- **Cache keys = identity + stage-param-hash** (§5). The bare key silently returns a cached `auto-sub`
  transcript when you pass `--force-whisper` — a correctness bug, not just staleness.
- **Projector check = fingerprint-armed nonce-OCR probe.** A missing mmproj makes the VL model return
  confident, well-formed, hallucinated captions while every health check passes. At vision-stage start
  hash the loaded-model metadata; on change, render a PNG with a random nonce and require the model to
  read it back. Fail → hard-stop, loud error. Never degrade to silent caption-less frames.
- **Delivered bundle = self-contained `out/<id>-p<part>/`.** Frame PNGs ship for *your* QA/reprocessing
  (Atlas reads only the markdown text); `--no-frame-images` omits them (JSON keeps phash/ts/caption).
  `cache/` holds expensive intermediates (raw audio, pre-dedup frames), gitignored.
- **Auth is per-provider `auth_opts()`.** bilibili: cookies effectively required — default
  `--cookies-from-browser` (Firefox; Chromium locks the DB on Windows), `.env` SESSDATA fallback.
  YouTube: cookies **optional** — public videos need none; a configured browser profile unlocks
  age-gated/bot-checked content. No stale-cookie detector; fail loud only if the cookie *source*
  itself fails. `meta.cookies_used` means "supplied," not "honored."
- **`published_at` timezone is per-provider.** bilibili is China Standard Time (`+08:00`); YouTube is
  UTC. Centralized so each provider maps its own source timezone.
- **Single-part atomic unit; `--all-parts` is isolate-and-continue.** `{platform, id, part}` → one
  bundle dir. `--all-parts` loops the single-part pipeline; a failed part logs and continues; re-runs
  skip done parts via caching. YouTube v1 is always `part=1`; a future playlist entry maps to a part.
- **CLI verb grammar.** `harvest <verb> <url>`: `ingest` (full pipeline), `probe` (cheap metadata, no
  media). No bare-url form. Scales to future verbs (`collect`, …) and sources.
- **Danmaku acquisition = the protobuf census endpoint (`seg.so`), not WBI-signed sampling.**
  `fetched_total` is the currently-live danmaku the census returns, `≤ source_total` by nature —
  `source_total` (bilibili's `stat.danmaku`) is a cumulative lifetime count, not the live pool, so
  the gap is lifetime attrition (deleted/expired danmaku), not a sample cut short. Two per-line
  *elevated* signals ride the same census, extracted
  verbatim before clustering: `DanmakuLine.high_like` (bilibili's own 高赞 platform-promotion flag,
  reliable) and `DanmakuLine.author` (`"owner"`/`"staff"` — a *suspected* UP主 or 合作 author danmaku,
  crc32-matched off the poster hash against the view response's author mids, no extra fetch). Only
  `high_like` ranks above the crowd; `author` is an UNVERIFIED hash match (bilibili exposes no true
  sender, so it may be a collision — empirically confirmed) and is a weak hint, not authoritative.
  See PROTOCOL.md for the full contract and `bundle.md`'s single-pass chronological rendering (marked
  lines pilled `👍`/`UP主?`/`合作?` and never dropped; only the ordinary flood is capped).
- **Command danmaku (`--interactions`) = a separate acquisition, not the census.** 互动弹幕 —
  the uploader's on-screen interactive widgets — do NOT ride the `seg.so` census; they come from
  `x/v2/dm/web/view` → `DmWebViewReply.commandDms` (plain cookies, no WBI — spike-confirmed). Two
  kinds are captured, whitelisted by their `command` tag: **Vote** (`#VOTE#`, 投票 — an
  uploader-authored `question` + discrete `options`, each with a running tally) and **Grade**
  (`#GRADE#`, 评分 — a 1–5 star bar the server pre-aggregates into a **0–10 `avg_score`** + rater
  `count`; it has no framing question). Other kinds (`#ATTENTION#` follow prompts, `#LINK#` cards)
  carry no crowd signal and are dropped. Unlike `--danmaku`, this is **purely structured — no LLM**
  (decode → schema; no LM Studio dependency). Authority sits **below `transcript`**, a peer track to
  danmaku: a Vote's question is *verified* uploader framing (no `?`-caveat — it is structural widget
  data, not a crc32 guess), while tallies and grades are crowd aggregates (reception signal, never
  facts about the video). `--danmaku` and `--interactions` are independent flags; when both run, a
  Grade's raw 1–5 **Rating danmaku** (the literal `「5」` clicks) still appear in the faithful census
  mirror AND as the clean Grade aggregate here — the same act twice, by design (the mirror must not
  lie). See PROTOCOL.md for the full contract and `bundle.md`'s `## Interactions` render.

## 9. Repository layout (target)

```
harvest/
├── SPEC.md · PROTOCOL.md · README.md
├── pyproject.toml · .env.example
├── harvest/
│   ├── cli.py              # verb dispatch, per-part orchestration
│   ├── config.py           # settings + per-provider auth/secret loading
│   ├── schema.py           # pydantic Bundle/SourceMetadata/ProbeResult (the contract)
│   ├── providers/          # provider registry + one module per source
│   │   ├── base.py         #   Provider protocol, SourceMetadata, registry
│   │   ├── bilibili.py     #   resolve + player-API metadata/subtitles (was resolve/player_api)
│   │   └── youtube.py      #   resolve + yt-dlp-native metadata/subtitles
│   ├── subtitles.py        # shared yt-dlp subtitle plumbing + parsers (bcc/srt)
│   ├── quality.py          # bilibili quality gate
│   ├── transcribe.py       # faster-whisper (shared, --lang aware)
│   ├── frames.py · vision.py · merge.py · cache.py
└── cache/                  # gitignored per-stage artifacts
```

> The package rename (`bili_tool` → `harvest`), env prefix (`BILI_*`/`SESSDATA` → `HARVEST_*` +
> per-provider), and git remote are a mechanical pass folded into the refactor.

## 10. Known follow-ups (deferred, not defects)

These are economy/purity trade-offs surfaced by the YouTube whole-branch review. None affect
correctness; each is a deliberate consequence of keeping the provider seam clean (metadata does not
leak provider-internal fetch state — e.g. bilibili `ViewData` — through `SourceMetadata`).

- **Redundant per-part metadata fetch (bilibili).** `fetch_metadata` runs the view API, then
  `fetch_subtitle` re-runs `extract_info` + a second view call (more for `part > 1`). `meta` is passed
  into `fetch_subtitle` but unused. Worth a design pass on whether the seam can carry a reusable
  handle without re-leaking `ViewData`. (`providers/bilibili.py`, `cli.py` per-part path)
- **Double `extract_info` (YouTube).** `fetch_metadata` and `fetch_subtitle` each extract in
  production. Same root cause and same fix shape as the bilibili item. (`providers/youtube.py`)
- **`.tv` guard is a platform control-flow branch**, duplicated in `cli.py` (ingest) and `probe.py`
  rather than expressed through the provider registry — a seam-purity wrinkle, unreachable-path today.
- **Dead field `SubtitleResult.last_cue_end`** (pre-existing), cleanup only. (`subtitles.py`)
