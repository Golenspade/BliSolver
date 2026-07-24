# harvest — Domain Language

Shared vocabulary for harvest, the ingestion front-door for Atlas. This is a **glossary only** —
what each term *means* as a domain fact, not how it's implemented (mechanisms live in the code;
*what/why* lives in [SPEC.md](SPEC.md); the machine contract in [PROTOCOL.md](PROTOCOL.md)).

## Established tracks (anchors)

**Bundle**:
The self-contained per-part deliverable harvest emits (`out/<id>-p<part>/`). Everything below is
either already in the bundle or a candidate to add to it.

**Transcript**:
The original-language, timeline-aligned speech track — the authoritative content of a video.
Highest authority in the bundle.

**Danmaku track**:
A faithful, verbatim MIRROR of the scrolling audience comments (弹幕) that overlay a bilibili
video, organized into content-time windows. It is crowd expression (memes, jokes, sarcasm, often
factually wrong) and carries the **lowest authority** in the bundle. Opt-in (`--danmaku`),
bilibili-only. Every danmaku — including a **UP主 danmaku** — travels in this one scrolling stream.
_Avoid_: "comments" (that word is reserved for the reply section — see **Comment**), "subtitles".

**high_like (高赞)**:
bilibili's own platform-promoted flag on an individual danmaku (peer-elevated, shown with 👍 in
the client). The one danmaku signal that is a **platform fact of higher authority than the
surrounding mirror**, not crowd-opinion-to-be-doubted.

**Authority ranking**:
The trust order harvest records so Atlas can weigh competing signals: transcript (human-sub >
whisper > auto-sub) ≫ everything crowd-sourced. Platform facts (like `high_like`) and uploader-
authored content are carve-outs that rank above the crowd mirror.

**Frame caption**:
The visual note harvest extracts per kept frame. Has two distinct halves: the **OCR** (verbatim
on-screen text) and the **visual description** (a prose description of figures/diagrams/scene/
layout). Both are produced today by one vision-model call.
_Avoid_: "analyze" (say caption); conflating "OCR" (text only) with the whole caption.

## Uploader-initiated interactions

Umbrella (by *intent*, not by mechanism) for signals a video's **author(s)** inject into the
viewing experience. The members below are mechanically unrelated — some are ordinary danmaku,
another is not danmaku at all — and share only that a video author (not the organic crowd) authored
them, which *would* give them higher authority than organic crowd expression — but only insofar as
we can actually confirm the authorship (see each member; the crc32 author-danmaku match cannot).

**Author danmaku** (SUSPECTED):
Collective term for a danmaku *believed* to be posted by a video author rather than an organic
viewer — either a **UP主 danmaku** (primary owner) or a **Collaborator danmaku** (a 合作 staff
member). Detected mechanically off the census stream by crc32-matching the danmaku's poster hash
(`midHash`) against the video's author mids. **This is an unverified hint, not a fact:** `midHash` is
a lossy 32-bit crc32 and bilibili exposes no true-sender API, so a match can be a hash collision —
empirically confirmed (real fan danmaku carry `crc32(owner_mid)` while the UP posted nothing). So it
does NOT carry author authority; surface it as "possibly from the author," never as certainty.
Orthogonal to `high_like` (one danmaku can be both; `high_like` is the reliable one).

**UP主 danmaku** (uploader danmaku):
An **Author danmaku** *suspected* to be posted by the video's own primary uploader (`owner.mid`).
When the bilibili client itself recognizes one it marks it with a solid pink "UP主" pill — but our
crc32 match cannot reproduce that recognition reliably, so we render an unverified `UP主?` pill. It
rides the **Danmaku track** like any other danmaku. _Avoid_: "official danmaku", "pinned danmaku";
do NOT confuse with **Command danmaku**.

**Collaborator danmaku** (合作):
An **Author danmaku** *suspected* to be posted by a co-author / staff member of a collaborative (合作)
video — an account in the video's `staff` list other than the primary owner. Same unverified-hash
caveat as a **UP主 danmaku** (and note the bilibili client does not even badge staff danmaku, so
these are unverifiable by eye); distinguished only by *which* author mid it matched, rendered with a
`合作?` pill. _Avoid_: "UP主 danmaku" (that term is the primary owner specifically).

**Command danmaku** (互动弹幕 / interactive danmaku):
Structured interactive widgets the uploader places on the timeline — **not** scrolling text and
**not** part of the **Danmaku track**. A separate class with its own acquisition. Several kinds
exist (follow/charge prompts, related-video cards, …); only the two that carry a crowd signal are
captured: **Vote** and **Grade**. (Terms follow bilibili's own `#VOTE#` / `#GRADE#` command names,
to keep one vocabulary between the API and our code.)

**Vote** (投票):
A command danmaku presenting an uploader-authored question with discrete options viewers click;
each option carries a running tally, and one option may be a free-text "other". The crowd's clicks
live **only** in those tallies — they are not danmaku and never touch the **Danmaku track**. The
question is uploader content; the tallies are the crowd's structured response to it.
_Avoid_: "poll", "survey" (bilibili's term is "vote").

**Grade** (评分 / 打分):
A command danmaku inviting viewers to rate the video on a **1–5 star bar**. It has **no framing
question** and no discrete options; any uploader text on it is a decorative thank-you. Its datum is
a server-computed aggregate — a **0–10 average** (the 1–5 stars ×2) plus a rater count — so it is
NOT raw "5 spam". (The earlier "1–5 rating, noise without a framing question" framing was wrong on
both counts.) The raw clicks surface separately as **Rating danmaku**.
_Avoid_: "star grading", "review", "score" (bilibili's term is "grade").

**Rating danmaku**:
The bare digit danmaku (e.g. "5", "1") that a viewer's **Grade** click posts into the ordinary
scrolling census, clustered at the moment the star bar renders (typically the opening seconds).
They ride the **Danmaku track** like any danmaku — they are NOT **Command danmaku** — and are the
raw crowd act whose server-side summary is the **Grade** aggregate. Surfacing both double-counts the
same act.
_Avoid_: "grade danmaku" (implies it belongs to the grade widget; it rides the census).

## Comments

**Comment** (评论 / reply):
A post in the video's **reply section** (the discussion below the video), as opposed to a danmaku
overlaid on the timeline. Considered/substantive relative to danmaku, and **not timeline-aligned**
(pinned to the video as a whole, not to a second of playback).
_Avoid_: "danmaku"; "reply" as a bare synonym when precision matters.

**Comment thread**:
A root comment plus its nested replies. The **thread starter** is the root comment; the rest are
its replies.

**Top comment** (高赞评论):
A comment bilibili surfaces as highly-upvoted — the reply-section analogue of `high_like`.

## Vision

**Vision stage**:
The frame pipeline: download video → sample frames → dedup → produce a **Frame caption** per kept
frame. Today it is all-or-nothing (`--no-vision` skips the whole stage) and tuned for lecture
slides.

**OCR-only**:
A candidate reduced mode that produces only the OCR half of a **Frame caption**, dropping the
visual description. A *shape/cost* lever, not a separate capability.

**Burned-in caption** (硬字幕 / hard-sub):
Speech-subtitle text etched into the video image itself (common on bilibili across genres),
distinct from a selectable subtitle track or the **Transcript**. Because it is *pixels*, the
vision stage's OCR half reads it — making that text redundant with the transcript.
_Avoid_: "subtitle" (that implies a selectable track), "caption" (overloaded with frame caption).
