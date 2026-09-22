"""Optional, cached LRCLIB reference candidates. Reference timing is never imported."""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from .lyrics import join_words
from .state import atomic_json, identity, read_json


def normalize(text):
    return re.sub(r"\W+", "", unicodedata.normalize("NFKC", text)).casefold()


def lyric_lines(text):
    rows = []
    for line in text.splitlines():
        if re.match(r"\[(?:ti|ar|al|by|offset|length|re|ve):", line, re.IGNORECASE):
            continue
        clean = re.sub(r"\[[^\]]*\]|<\d+:[^>]*>", "", line).strip()
        if clean and not re.search(r"作[词詞曲]|编曲|編曲|混音|母带|母帶|lyrics? by|composed by",
                                   clean, re.IGNORECASE):
            rows.append(clean)
    return rows


@lru_cache(maxsize=16000)
def phones(text, language):
    text = unicodedata.normalize("NFKC", text).casefold()
    if language == "zh":
        from opencc import OpenCC
        from pypinyin import lazy_pinyin
        return " ".join(lazy_pinyin(OpenCC("t2s").convert(text), errors=lambda s: list(s)))
    if language == "ja":
        from pykakasi import kakasi
        return "".join(char for item in kakasi().convert(text)
                       for char in item["hepburn"] if char.isalnum())
    return normalize(text)


def supported_reference(text, words, language=None):
    from rapidfuzz import fuzz
    observed = join_words(words)
    language = language or ("ja" if re.search(r"[\u3040-\u30ff]", observed) else
                            "zh" if re.search(r"[\u3400-\u9fff]", observed) else "en")
    heard = phones(observed, language)
    rows = []
    for line in dict.fromkeys(lyric_lines(text)):
        line_phones = phones(line, language)
        if len(normalize(line)) < 3 or not heard:
            continue
        score = fuzz.partial_ratio(line_phones, heard)/100
        if score >= .64:
            rows.append({"text": line, "phonetic_support": round(score, 4)})
    return {"text": "\n".join(row["text"] for row in rows), "lines": rows,
            "language": language,
            "score": sum(len(normalize(row["text"])) * (row["phonetic_support"]-.5) for row in rows)}


class ReferenceLookup:
    def __init__(self, root: Path, transport=None):
        import requests
        self.root = root
        self.transport = transport or requests.Session()
        self.lock = threading.Lock()
        self.last = 0.
        self.blocked = False

    def search(self, title: str, artist: str | None, words, language=None) -> dict:
        params = {"track_name": title}
        if artist:
            params["artist_name"] = artist
        dest = self.root / (identity(params) + ".json")
        with self.lock:
            rows = read_json(dest)
            if rows is None:
                if self.blocked:
                    raise RuntimeError("LRCLIB lookup stopped after an access/rate limit response")
                time.sleep(max(0, .7-(time.monotonic()-self.last)))
                response = self.transport.get("https://lrclib.net/api/search", params=params,
                    headers={"User-Agent": "BliSolver songcut/1 (local lyric candidates)"},
                    timeout=25, allow_redirects=False)
                self.last = time.monotonic()
                if response.status_code in {403, 429}:
                    self.blocked = True
                if response.status_code != 200:
                    raise RuntimeError(f"LRCLIB HTTP {response.status_code}")
                raw = response.json()
                if not isinstance(raw, list):
                    raise ValueError("invalid LRCLIB response")
                rows = [{"id": row["id"], "title": row.get("trackName"),
                         "artist": row.get("artistName"),
                         "text": "\n".join(lyric_lines(row.get("plainLyrics") or
                                                       row.get("syncedLyrics") or "")),
                         "url": f"https://lrclib.net/api/get/{row['id']}"}
                        for row in raw[:20] if type(row.get("id")) is int]
                atomic_json(dest, rows)
        recognized = normalize(join_words(words))
        candidates = [{**row, "supported": supported_reference(row["text"], words, language),
                       "text_similarity": SequenceMatcher(
                           None, normalize(row["text"]), recognized, autojunk=False).ratio()}
            for row in rows if row["text"]]
        candidates.sort(key=lambda row: row["supported"]["score"], reverse=True)
        chosen = candidates[0] if candidates and candidates[0]["supported"]["score"] > 0 else None
        return {"candidates": candidates, "selected": chosen,
                "basis": "phonetic_support_from_current_asr", "review_status": "draft",
                "timing_imported": False}
