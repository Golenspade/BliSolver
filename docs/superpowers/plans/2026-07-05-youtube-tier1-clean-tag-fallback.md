# Plan: YouTube Tier-1 human-sub clean-tag fallback

## Problem

`YouTubeProvider.fetch_subtitle` Tier 1 matches human captions by **exact key** only
(`subtitles[target]`, `target = --lang | info["language"]`). `info["language"]` is best-effort and
often a fuller/different BCP-47 tag than the caption key (`en-US` vs `en`, `de` vs `de-DE`), and yt-dlp
appends per-track disambiguation suffixes when a language has multiple tracks. So an available
human caption under a variant key is silently skipped → the run degrades to auto-sub or Whisper, and in
the worst case a real `human-sub` is shadowed by a lower-authority `auto-sub`.

The auto path already tolerates this via a base-subtag fallback — but that is safe **only** because
`-orig` marks original audio. Human `subtitles` has **no `-orig` marker** and also carries
**hash-suffixed community-translation** tracks (`en-US-njLgzgtehjs`), so a blanket base-subtag fallback
would reuse a translation as the original — the exact failure SPEC §6 forbids.

## Design (decided)

Narrow, **BCP-47-clean** fallback. After an exact-key miss, reuse a same-base-language human key **only
if it is a clean BCP-47 tag**: primary language subtag followed solely by valid script (4-alpha) and/or
region (2-alpha / 3-digit) subtags. This admits `de-DE`, `zh-Hant`, `es-419`; it structurally rejects
`en-US-njLgzgtehjs` and `en-eEY6OEpapPo` (trailing non-region/script segment). No `-orig` needed.

- Match stays **within the base language subtag** (never crosses languages).
- Human variant **wins** over a same-base auto-sub (honors `human-sub > auto-sub`).
- Rank candidates: prefer key starting with the full target, then shortest.
- `language` on the outcome = the matched key (its true tag); `source_reason` distinguishes
  `exact-key match` vs `base-subtag match`.

## Tasks (each TDD: failing test first)

### Task 1 — `_is_clean_bcp47` + `pick_human_key` helpers (`harvest/providers/youtube_autosub.py`)

Add two pure helpers next to `pick_auto_key`/`_lang_base` (reuse `_lang_base`).

- `_is_clean_bcp47(tag)`: primary subtag alphabetic; every remaining subtag is a valid script
  (`len==4 and isalpha`) or region (`len==2 and isalpha`, or `len==3 and isdigit`); else False.
- `pick_human_key(subtitles, target)`: return `target` if present; else the best same-base
  (`_lang_base(k) == _lang_base(target)`), `_is_clean_bcp47(k)`, `k != target` key, ranked
  `(0 if k.startswith(target) else 1, len(k))`; else None.

Tests (`tests/test_youtube_autosub.py`): exact wins; `de`↔`de-DE`; `en-US`→`en`; `zh-Hant`→`zh-Hans`;
`es-419` accepted; `en-US-njLgzgtehjs` rejected; `en-eEY6OEpapPo` rejected; no same-base key → None;
ranking prefers fuller-then-shorter; `_is_clean_bcp47` unit cases.

### Task 2 — Wire Tier 1 to `pick_human_key` (`harvest/providers/youtube.py`)

Replace the exact-key block with `key = pick_human_key(subtitles, target)`; on hit, fetch the `vtt`
(or first) track, `parse_vtt`, return `human-sub` with `language=key` and a reason reflecting
exact vs base-subtag. Update the class/module docstring away from "exact key". Auto path untouched.

Tests (`tests/test_providers_youtube.py`): regional human key reused as `human-sub` (not shadowed by a
same-base auto track); suffixed community-translation key NOT reused (→ auto/whisper); existing
exact-key + auto-tier tests still green.

## Out of scope

Auto-path logic, authority order, structural net, `--force-whisper`, CLI — all unchanged. Additive;
`transcript.source` gains no new value.

## Verify

Full offline suite green; new tests cover both accept (`de-DE`) and reject (`en-US-njLgzgtehjs`) paths.
