# YouTube extraction hardening: JS runtime + degraded-response guard

Issue: #5. Follow-up to #3 (the `StopIteration` audio-download bug, already merged).

## Problem

yt-dlp 2026.06.09 needs a JavaScript runtime to drive YouTube's web player client. On a box
without one (only `deno` is auto-enabled by yt-dlp, and it may be absent), extraction
**intermittently** falls back to a stripped/blocked response — placeholder `title`
(e.g. `recommended`), no `duration`, no `language`, no `subtitles`. harvest then writes a
corrupt bundle and misses human subtitles, falling back to Whisper unnecessarily.

### What was verified

- With a JS runtime wired (deno 2.9.1 at `~/.deno/bin/deno.exe`), the same video returned real
  metadata: `title="Google & AWS Veteran…"`, `duration=3895`, `language="en"`,
  `subtitles={"en": …}`, 27 formats incl. bestaudio.
- A JS runtime does **not** fully solve YouTube's `nsig` challenge — yt-dlp still logs
  `n challenge solving failed` and asks for the EJS solver component
  (`--remote-components ejs:github` / `ejs:npm`). That only risks dropping *some* premium/video
  formats; **bestaudio, subtitles, and metadata all come through**, which is all harvest needs.
  harvest already sets `quiet`/`no_warnings`, so these warnings never reach users.
- ffmpeg is **not** involved: `find_ffmpeg()` locates the winget binary and harvest passes
  `ffmpeg_location`; faster-whisper decodes webm via bundled PyAV. The "ffmpeg not found" report
  came from a standalone yt-dlp call, not harvest.

## Goals

1. Auto-detect a JS runtime and hand it to yt-dlp, so extraction reliably uses the real web
   player client (prevents the degraded-response corruption at its source).
2. Fail loud when a degraded response slips through anyway, instead of silently writing a corrupt
   bundle (defense-in-depth for boxes without a runtime).

## Non-goals

- The EJS `--remote-components` GitHub/NPM solver fetch (downloads executable JS at runtime — a
  supply-chain decision deferred; not needed for bestaudio/subs/metadata).
- The secondary `url: …/watch?p=1` bundle-URL observation (verify separately under #5).

## Design

### 1. Runtime detection — `config.py`

Add `find_js_runtime() -> tuple[str, str] | None`, mirroring `find_ffmpeg()`/`find_aria2c()`.
Returns `(name, path)` or `None`. Search order, **deno preferred over node**:

1. `shutil.which("deno")`, then the deno.land scripted-installer location:
   `os.environ.get("DENO_INSTALL")`/`~/.deno` → `bin/deno.exe` (Windows) / `bin/deno`. The
   installer touches neither PATH-for-this-process nor winget, so this explicit path is the only
   way to find it on such a box.
2. `shutil.which("node")` (node is typically on PATH), then the winget package glob for other
   boxes.

deno is preferred because it is yt-dlp's default-priority runtime; node is a working fallback
(it still flips extraction to the real web client even though neither fully solves `nsig`).

`Settings` gains `js_runtime: tuple[str, str] | None = None`, populated in `Settings.load()`
alongside `ffmpeg_path`/`aria2c_path`.

### 2. Wire into yt-dlp — `subtitles.py::ydl_opts`

When `settings.js_runtime` is set, add `opts["js_runtimes"] = {name: {"path": path}}`. Applied to
every yt-dlp call (extraction, subtitle, download). Harmless for bilibili — a registered runtime
is only exercised when a challenge actually needs solving. No new function parameters; it reads
from `settings`, so all existing callers get it for free.

### 3. Fail-loud degraded guard — `providers/youtube.py::_extract_info`

`_extract_info` is the single choke point for every YouTube `extract_info` (metadata and
subtitle paths both flow through it). After the call, if the response looks degraded, raise a
`RuntimeError` instead of returning it.

- **Primary signal: `info.get("duration")` is falsy.** Every real lecture/video has a duration;
  the blocked "recommended" page had none. Chosen over brittle title-string matching.
- The error message names the likely cause (no working JS runtime / YouTube challenge/bot check)
  and the remediation (install deno; whether `settings.js_runtime` was detected), plus the
  observed title so the symptom is legible.

This guarantees harvest never writes a bundle built from a degraded response.

### 4. Tests

- `find_js_runtime`: prefers deno over node; finds deno via the `~/.deno/bin` fallback when not on
  PATH; returns `None` when neither present (monkeypatch `shutil.which` + a fake HOME/`DENO_INSTALL`).
- `ydl_opts`: includes `js_runtimes={name: {"path": path}}` when `settings.js_runtime` set; omits
  the key when `None`.
- `_extract_info`: raises `RuntimeError` on degraded info (no duration); returns healthy info
  unchanged (inject a fake `YoutubeDL`).

### 5. Docs — README

Add `deno` to dependencies with an accurate note: it provides the JS runtime yt-dlp needs to use
YouTube's real web player client (reliable metadata + subtitles). Install via
`irm https://deno.land/install.ps1 | iex` (winget also works where available). Note that the
`nsig` challenge solver (EJS) is a separate optional component harvest does not require.

## Rollout

Single PR against `master`, TDD, verified end-to-end against
`https://www.youtube.com/watch?v=F8X9_Dp3ZUk` (real title + `en` human-sub, no Whisper fallback).
