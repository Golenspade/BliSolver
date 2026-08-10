# Domain glossary

## Atlas

The downstream knowledge base. Atlas reads the bundle and does the interpreting. BliSolver is an
acquisition and normalization boundary, not the semantic analyst.

## Bundle

The self-contained per-part delivery directory. Its stable identity is `{platform, id, part}`; its
*directory name* is the sanitized title plus that triple, so the name is not derivable from the
identity. Complete enough for Atlas to consume without calling provider APIs.

## `bundle.md` / `bundle.json`

`bundle.md` is the primary prose ingestion surface: provenance frontmatter, timestamped chunks of
transcript and visual notes, then optional OCR, danmaku, and interaction sections. `bundle.json` is
the precise backing record and the only complete one — Markdown may cap danmaku per window.

## Production method: `human-sub`, `auto-sub`, `whisper`

*How* the selected transcript was produced. `human-sub` is a human-authored caption; `auto-sub` is a
provider machine caption that passed the structural and quality checks; `whisper` is local
whisper.cpp transcription.

Authority is `human-sub > whisper > auto-sub`, even though acquisition may reach for `auto-sub` first
to avoid paying for Whisper. Cost order and trust order are different orderings and must not be
conflated.

Production method says nothing about language. See below.

## Language proxy

An acquisition outcome where the original-language track was rejected and a different language stands
in for it. On bilibili this happens when the Chinese ASR track comes back with redactions: the
pipeline falls through to a foreign-language ASR track.

Three fields together describe it: `transcript.language` (what was delivered, rendered in
`bundle.md` as `transcript_language`), `transcript.source_reason` (which contains `language proxy`,
names the rejected track, and names the marker that triggered rejection), and `original_language`.

`source_reason` is the reliable one. `original_language` is a platform default for bilibili rather
than a measurement, so treat a mismatch as corroboration and never treat a match as proof there was
no substitution.

A language-proxy transcript is a platform machine translation of the audio. It sits in the `auto-sub`
tier and is not the speaker's words. Compare the two language fields before quoting.

## Track key versus language code

bilibili keys ASR tracks `ai-<lang>`. The `ai-` prefix records production method, not language, so
`ai-en` is an English track produced by ASR. `subtitles.track_language` strips the prefix; the
language field carries the code, never the key.

## Soft subtitle versus hardsub

A soft subtitle is a timed text track obtained from the provider and eligible for the transcript
decision. A hardsub is text burned into pixels; `--ocr` extracts it into `Bundle.ocr` on an
independent timeline. `Frame.ocr` is sparse visual OCR of slide or UI text and is a third, different
thing.

## Danmaku

bilibili's scrolling audience comments — a faithful, lower-authority mirror with content-time windows
and verbatim representative lines. It signals audience reaction, never verified fact. `high_like` is
platform promotion. `author="owner"`/`"staff"` is a 32-bit hash match, explicitly unverified and
collision-prone.

## Interactions

bilibili command-danmaku widgets, currently Votes and Grades. A Vote question is structural uploader
framing — a question the uploader asked, not a claim the video makes. Grades are server-computed
crowd means. No LLM is involved.

## Provenance

Evidence about how a value was produced: transcript source, delivered language, model, quality gate,
per-cue source and confidence, OCR confidence, vision model, tool version. Load-bearing for
downstream authority ranking, not decorative — which is why a falsified language field is a real
defect rather than cosmetic.

## Structural validity versus linguistic quality

Structural checks ask whether a caption exists, covers the video, and contains enough speech-like
text. Linguistic quality asks whether the words are accurate and readable. YouTube's auto-caption net
is language-agnostic structural validation; bilibili's gate adds calibrated metrics that adapt to the
delivered language.

## Canonical part

The atomic cached unit `{platform, id, part, url}`. Multi-part bilibili videos are processed one part
at a time so a single failure never invalidates completed parts.

## Data directory

The writable root for caches and bundles. A conformant Agent Plugins client points it at
`PLUGIN_DATA`, which survives plugin updates; a developer checkout defaults to the repository. The
package directory itself may be replaced wholesale on update, so generated state does not belong
there.
