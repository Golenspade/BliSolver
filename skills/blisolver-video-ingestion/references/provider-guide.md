# Provider guide

## Provider selection

`blisolver.providers.base.select_provider(url)` walks the registered providers and returns a provider
that matches the URL. The provider resolves the URL to a `Canonical` identity, fetches normalized
`SourceMetadata`, enumerates parts, and owns source-specific subtitle/auth behavior. Shared stages do
not consume raw platform response shapes.

## bilibili.com

- A bilibili URL resolves to `platform="bilibili.com"`, a `BV...` id, and a 1-based part.
- Authenticated browser cookies are normally required. The default browser source is configured by
  blisolver settings; `SESSDATA` is a fallback. Requests use the bilibili referer where required.
- Human/original subtitles are preferred. The player API/cookie path can expose AI captions that
  yt-dlp alone does not surface. AI captions are accepted only after the current quality and
  duration/part-match gates; rejected or unavailable captions fall back to Whisper.
- The default Whisper language is `zh` unless `--lang` overrides it.
- `--danmaku` fetches the bilibili census track and `--interactions` separately fetches structured
  command-danmaku. Both are bilibili-only and opt-in.

Do not interpret `meta.cookies_used` as proof that the server honored a cookie; it records that a
cookie source was supplied.

## YouTube

- YouTube is acquired through yt-dlp's native metadata/subtitle path and is always part 1 in v1.
- A JavaScript runtime is important for yt-dlp's real web-player client; deno is preferred and node
  is a fallback. Public videos are cookie-free by default. Browser cookies are an explicit opt-in for
  age-gated or bot-checked content.
- Language resolution uses `--lang`, then yt-dlp's best-effort `info["language"]`, otherwise an
  unknown-language branch. Do not infer a language when multiple original-audio tracks exist.
- Human captions use exact language keys or clean BCP-47 script/region variants only. Hash-suffixed
  community translations are never treated as original-language human captions.
- Auto captions use the original-audio `*-orig` discipline and fetch server-side SRT, not rolling
  VTT/json3. A language-agnostic structural net checks cue presence, duration coverage, and a
  minimum chars-per-second floor. Failure falls back to Whisper.
- `--lang` is a deliberate caller override and can request a non-original language; that is distinct
  from the default original-language safety path.

YouTube has no bilibili danmaku or command-danmaku semantics. The opt-in flags warn and leave the
corresponding track null.

## Subtitle decision

The two axes must not be conflated:

- **Acquisition cost:** reuse a usable caption before running local ASR.
- **Authority:** downstream ranks human-sub above Whisper above auto-sub.

When the structural/source guard is uncertain, choose Whisper rather than silently accepting a wrong
or truncated track. Record the decision reason in `Transcript.source_reason`.

## Authentication troubleshooting

1. Run `doctor.py --json` without printing the environment.
2. Check that the logged-in browser profile is available to the configured browser and is not locked.
3. Use `SESSDATA` only through the environment/.env mechanism; never pass it as a URL or command
   argument.
4. For YouTube, remove optional cookies first when a public extraction unexpectedly degrades; the
   current default is cookie-free.
5. If a provider returns a `.tv` URL, stop: the deferred platform has no supported probe/player path.
