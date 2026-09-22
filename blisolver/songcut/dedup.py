"""Recording matching: landmarks retrieve; dense waveform/coherence windows verify.

No singer identity inference. A common backing track alone must not establish a match.
Original recordings are never removed, including high-fit matches.
"""

from __future__ import annotations

from collections import Counter
from itertools import pairwise
from pathlib import Path

import numpy as np
from scipy import ndimage, signal

from .audio import AudioEngine
from .state import atomic_json, identity, read_json, sha256

RATE, HOP = 8000, 256
VERSION = "landmarks-dense-piecewise-2"


def fingerprint(samples):
    if len(samples) < 1024:
        return np.array([], dtype=np.uint32), np.array([], dtype=np.uint32)
    _, _, z = signal.stft(samples, fs=RATE, nperseg=1024, noverlap=1024-HOP,
                          boundary=None, padded=False)
    logmag = 20 * np.log10(np.maximum(np.abs(z), 1e-7))
    white = logmag - ndimage.gaussian_filter(logmag, sigma=(4, 12))
    peaks = (white == ndimage.maximum_filter(white, size=(9, 9))) & (white > 5)
    peaks &= logmag > np.median(logmag, axis=0, keepdims=True) + 6
    peaks[:14] = False
    peaks[461:] = False
    freq, frame = np.where(peaks)
    order = np.lexsort((-white[freq, frame], frame))
    kept, counts = [], Counter()
    for f, t in zip(freq[order], frame[order]):
        if counts[int(t)] < 2:
            kept.append((int(t), int(f)))
            counts[int(t)] += 1
    hashes, times = [], []
    for i, (t, f) in enumerate(kept):
        fan = 0
        for t2, f2 in kept[i+1:i+42]:
            delta = t2-t
            if delta < 4:
                continue
            if delta > 48:
                break
            if abs(f2-f) < 5:
                continue
            hashes.append(((f//3) << 16) | ((f2//3) << 8) | (delta//2))
            times.append(t)
            fan += 1
            if fan == 3:
                break
    # uint16 wrapped after 35 minutes in the old batch script.
    return np.asarray(hashes, np.uint32), np.asarray(times, np.uint32)


def correlation(haystack, query):
    q = query.astype(np.float64)
    q -= q.mean()
    n = len(q)
    if len(haystack) < n:
        return np.array([])
    s = haystack.astype(np.float64)
    dots = signal.correlate(s, q, mode="valid", method="fft")
    sums = np.r_[0, np.cumsum(s)]
    squares = np.r_[0, np.cumsum(s*s)]
    energy = np.maximum(squares[n:]-squares[:-n]-(sums[n:]-sums[:-n])**2/n, 1e-15)
    return dots / np.sqrt(energy * max(float(q @ q), 1e-15))


def coherence(x, y):
    _, _, xx = signal.stft(x, fs=RATE, nperseg=512, noverlap=384,
                           boundary=None, padded=False)
    _, _, yy = signal.stft(y, fs=RATE, nperseg=512, noverlap=384,
                           boundary=None, padded=False)
    px, py = np.sum(abs(xx)**2, axis=1), np.sum(abs(yy)**2, axis=1)
    freq = np.arange(len(px))*RATE/512
    eligible = ((freq >= 350) & (freq <= 3200) & (px > np.quantile(px, .2))
                & (py > np.quantile(py, .2)))
    # Narrow-band tones cannot prove recording identity.
    if np.count_nonzero(eligible & (px > px.max() * .001)) < 12:
        return 0., 0.
    values = np.clip(abs(np.sum(xx*np.conj(yy), axis=1))**2 /
                     np.maximum(px*py, 1e-20), 0, 1)[eligible]
    return float(np.median(values)), float(np.quantile(values, .2))


def compare(query, reference) -> dict:
    seconds = len(query)/RATE
    if seconds < 8 or len(reference) < 8*RATE:
        return {"decision": "not_established", "reason": "too_short", "windows": []}
    sos = signal.butter(4, [120, 1800], btype="band", fs=RATE, output="sos")
    aa, bb = signal.sosfiltfilt(sos, query), signal.sosfiltfilt(sos, reference)
    size = 6*RATE
    starts = sorted({*range(0, len(query)-size+1, 3*RATE), len(query)-size})
    rows = []
    covered = np.zeros(math_ceil(seconds*10), dtype=bool)
    for start in starts:
        curve = correlation(bb, aa[start:start+size])
        if not len(curve):
            continue
        j = int(np.argmax(np.abs(curve)))
        corr = float(abs(curve[j]))
        median, p20 = coherence(query[start:start+size], reference[j:j+size])
        good = corr >= .75 and median >= .90 and p20 >= .80
        if good:
            covered[round(start/RATE*10):round((start+size)/RATE*10)] = True
        rows.append({"start": start/RATE, "end": (start+size)/RATE,
                     "reference_start": j/RATE, "offset": (j-start)/RATE,
                     "correlation": corr, "coherence_median": median,
                     "coherence_p20": p20, "match": good})
    good_rows = [r for r in rows if r["match"]]
    monotonic = all(b["reference_start"] > a["reference_start"]
                    for a, b in pairwise(good_rows))
    fraction = len(good_rows)/max(1, len(rows))
    coverage = float(covered.mean())
    boundaries = np.flatnonzero(np.diff(np.r_[False, ~covered, False].astype(np.int8)))
    longest_gap = float(max(boundaries[1::2] - boundaries[::2], default=0))/10
    high = (fraction >= .9 and coverage >= .88 and monotonic and
            longest_gap <= max(18, seconds*.08))
    offsets = []
    for row in good_rows:
        if offsets and abs(offsets[-1]["offset"] - row["offset"]) < .01:
            offsets[-1]["end"] = row["end"]
        else:
            offsets.append({k: row[k] for k in ("start", "end", "offset")})
    return {"decision": "same_recording_high_fit" if high else (
                "needs_review" if fraction >= .4 else "not_established"),
            "coverage": coverage, "matching_window_fraction": fraction,
            "longest_unmatched_seconds": longest_gap,
            "piecewise_offsets": offsets, "windows": rows,
            "identity_proven": False, "algorithm": VERSION}


def math_ceil(value):
    return int(np.ceil(value))


class RecordingIndex:
    def __init__(self, engine: AudioEngine, cache: Path):
        self.engine, self.cache = engine, cache
        self.items = []

    def add(self, path: Path):
        digest = sha256(path)
        key = identity({"sha": digest, "version": VERSION, "ffmpeg": self.engine.version})
        cached = read_json(self.cache / f"{key}.json")
        if cached is None:
            hashes, times = fingerprint(self.engine.samples(path))
            cached = {"hashes": hashes.tolist(), "times": times.tolist()}
            atomic_json(self.cache / f"{key}.json", cached)
        self.items.append({"path": str(path.resolve()), "sha256": digest, **cached})

    def search(self, path: Path) -> dict:
        digest = sha256(path)
        for item in self.items:
            if item["sha256"] == digest:
                return {"decision": "exact_file", "reference": item["path"],
                        "reference_sha256": digest, "identity_proven": False}
        if not self.items:
            return {"decision": "not_established", "compared": 0}
        samples = self.engine.samples(path)
        hashes, _ = fingerprint(samples)
        query = set(hashes.tolist())
        ranked = sorted(self.items, key=lambda x: len(query.intersection(x["hashes"])),
                        reverse=True)[:6]
        matches = []
        for item in ranked:
            key = identity({"query": digest, "reference": item["sha256"],
                            "algorithm": VERSION, "ffmpeg": self.engine.version})
            cache_path = self.cache / f"comparison-{key}.json"
            result = read_json(cache_path)
            if result is None:
                result = compare(samples, self.engine.samples(Path(item["path"])))
                atomic_json(cache_path, result)
            if result["decision"] != "not_established":
                matches.append({**result, "reference": item["path"],
                                "reference_sha256": item["sha256"]})
        matches.sort(key=lambda x: x.get("matching_window_fraction", 0), reverse=True)
        return {"decision": matches[0]["decision"] if matches else "not_established",
                "matches": matches, "compared": len(ranked),
                "candidate_limit": 6, "exhaustive": len(self.items) <= 6}
