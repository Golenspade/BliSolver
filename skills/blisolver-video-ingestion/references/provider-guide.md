# Provider guide

## The seam

`blisolver.providers.base.select_provider(url)` normalizes the URL, then returns the first registered
provider that matches it. The provider owns resolve, auth, metadata, part enumeration, and subtitle
acquisition for one source. Everything downstream reads normalized `SourceMetadata` and never
branches on platform.

URL normalization lives in `blisolver/resolve.py::extract_url` and handles pasted text with a title
prefix, `<iframe>` snippets, `player.bilibili.com` embeds, `b23.tv` short links, and tracking
parameters. Callers pass raw text through; nothing needs to pre-clean a URL, and the skill wrappers
deliberately do not.

## bilibili.com

* Resolves to `platform="bilibili.com"`, a `BV…`/`av…` id, and a 1-based part.
* A logged-in browser session is normally required: bilibili exposes its AI subtitle tracks only to
  an authenticated session. `BLISOLVER_COOKIES_BROWSER` selects the browser (chrome by default),
  `BLISOLVER_COOKIES_PROFILE` a named profile, and `SESSDATA` is the headless fallback. Requests
  carry the bilibili referer where required.
* Candidate track order is **fixed** and `--lang` does not influence it. The CLI prints a note when
  `--lang` is passed for a bilibili URL so the flag is not silently swallowed.
* Accepted tracks pass a source-aware quality gate plus duration and part-match assertions.
* `--danmaku` fetches the audience census; `--interactions` separately fetches vote/grade widgets.
  Both are bilibili-only and opt-in, and `--danmaku` additionally needs a configured model.

`meta.cookies_used` records that a cookie source was *supplied*, never that the server honored it.

### The censorship fallback, and why the transcript may not be Chinese

bilibili sometimes returns its AI Chinese track with redactions substituted for words. Acquisition
walks a candidate list and takes the first track without redaction markers:

1. human Chinese — `zh-Hans`, `zh-CN`, `zh`
2. Chinese ASR — `ai-zh`
3. foreign-language ASR — `ai-en`/`en`, then `ai-ja`/`ja`, `ai-es`, `ai-ar`, `ai-pt`, `ai-ko`,
   `ai-th`, `ai-id`, `ai-vi`

The detector is textual: `**`, `XX`, and `和X`. It can fire on innocent content — `和X` is ordinary
in a maths lecture, `**` appears in Markdown-flavoured speech. A false positive is therefore
possible, and it is not silent: the rejected track and the marker that triggered it are recorded.

When the accepted track is not Chinese, three things hold and you should check all three:

* `transcript.language` is the language actually delivered, derived from the track key. The `ai-`
  prefix records how a track was produced, not what language it holds, so `ai-en` reports `en`.
* `original_language` still reports what the platform says was spoken.
* `transcript.source_reason` contains `language proxy`, names the rejected track, and names the
  marker.

Treating a language-proxy transcript as the speaker's own words is a category error: the text is a
machine translation of the audio produced by the platform, sitting in the `auto-sub` authority tier.

The quality gate adapts to the delivered language — the CJK-oriented non-Chinese-character ratio is
bypassed for a non-Chinese track, and the characters-per-second band accommodates the naturally
higher rate of English text. Without that, a correct English fallback would be rejected as garbage.

## YouTube

* Acquired through yt-dlp's native metadata and subtitle path. Always part 1 in v1.
* A JavaScript runtime matters: without deno or node, yt-dlp cannot drive YouTube's real web-player
  client and extraction intermittently degrades to a stripped response — placeholder title, no
  duration, no subtitles. `doctor` reports this under `javascript-runtime`.
* Public videos are cookie-free by default; a logged-in session breaks yt-dlp's format selection.
  Opt in with `BLISOLVER_YT_COOKIES` only for gated content.
* Language resolution uses `--lang` first, then yt-dlp's best-effort `info["language"]`, else an
  unknown-language branch. Do not infer a language when several original-audio tracks exist.
* Human captions are accepted on an exact language key or a clean BCP-47 script/region variant.
  Hash-suffixed community translations are never treated as original-language human captions.
* Auto captions follow the original-audio `*-orig` discipline and fetch server-side SRT rather than
  rolling VTT/json3. A language-agnostic structural net checks cue count, duration coverage, and a
  minimum characters-per-second floor; failing it falls back to Whisper.

`--lang` on YouTube **does** select a human caption track, and a human caption is the highest
authority tier. Requesting a language the video was not spoken in therefore stamps a translation
with top confidence. That is a deliberate caller override, distinct from the default
original-language path, and it should be a conscious choice rather than a habit.

YouTube has no danmaku or command-danmaku concept. The opt-in flags warn and leave those tracks null.

## The two axes, kept apart

* **Acquisition cost** — reuse a usable caption before paying for local ASR.
* **Authority** — downstream ranks `human-sub` above `whisper` above `auto-sub`.

These are independent. A caption chosen to save money still ranks below Whisper if it is an
auto-caption. When a structural or source guard is uncertain, choose Whisper rather than silently
accepting a wrong, truncated, or translated track, and record the decision in
`Transcript.source_reason`.

## Authentication troubleshooting

1. `python3 "$SCRIPTS/blisolver_cli.py" doctor --json` and read `provider-auth`. It reports presence,
   never a value.
2. Confirm the configured browser's profile exists and is not locked by a running browser.
3. Supply `SESSDATA` through the environment or `.env` only — never as an argument or URL.
4. For YouTube, remove optional cookies first when a public extraction unexpectedly degrades.
5. A `.tv` URL is deferred; stop rather than looking for a workaround.
6. Under MCP, remember the server inherits the client's environment. A sanitized environment means
   no bilibili session and a Whisper fallback on every bilibili video.
