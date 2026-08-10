# Domain glossary

## Atlas

The downstream knowledge base. Atlas reads blisolver's bundle and performs interpretation. BliSolver is
an acquisition and normalization boundary, not the final semantic analyst.

## Bundle

The self-contained per-video-part delivery directory. Its stable identity is
`{platform, id, part}`. A bundle is complete enough for Atlas to consume without reaching back into
provider APIs.

## `bundle.md`

The primary prose ingestion surface. It contains provenance frontmatter and timestamped chunks of
transcript plus visual notes, followed by optional OCR, danmaku, and interaction sections.

## `bundle.json`

The precise backing record. It preserves schema fields, per-cue provenance, complete optional tracks,
phashes, stats snapshots, and machine-readable metadata. Consumers needing exhaustive danmaku read
JSON, not the capped Markdown rendering.

## `human-sub`, `auto-sub`, `whisper`

Production methods for the selected transcript. `human-sub` is an original-language human caption;
`auto-sub` is a provider machine caption accepted by structural/quality rules; `whisper` is local
whisper.cpp transcription. Authority is `human-sub > whisper > auto-sub`, even though acquisition
may choose auto-sub before paying for Whisper.

## Soft subtitle versus hardsub

A soft subtitle is a timed text track acquired from the provider and used in the transcript decision.
A hardsub is text burned into pixels. The optional OCR stage extracts hardsubs into an independent
`Bundle.ocr` timeline. `Frame.ocr` is sparse visual OCR, usually slide/UI text, and is different.

## Danmaku

Bilibili's scrolling audience comments. In blisolver it is a faithful, lower-authority mirror with
content-time windows and verbatim representative lines. It signals audience reaction, not verified
video facts.

## Interactions

Bilibili command-danmaku widgets, currently Votes and Grades. Vote questions are structural uploader
framing; option tallies and grades are crowd reception aggregates. They are separate from the
ordinary danmaku census and use no LLM.

## Provenance

Evidence about how a value was produced: transcript source, language, model, quality gate, cue source,
OCR confidence, vision model, and tool version. Provenance is load-bearing for downstream authority
ranking, not decorative metadata.

## Structural validity versus linguistic quality

Structural checks ask whether a caption is present, covers the video, and contains enough speech-like
text. Linguistic quality asks whether words are accurate/readable. YouTube's auto-caption net is
language-agnostic structural validation; bilibili's caption gate also uses calibrated CJK-oriented
quality metrics.

## Canonical part

The atomic cached unit `{platform, id, part, url}`. Multi-part bilibili videos are processed one
part at a time so one failure does not invalidate completed parts.
