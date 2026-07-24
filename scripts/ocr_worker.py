"""Hard-subtitle OCR worker — runs in an ISOLATED venv (``.ocr-venv``) separate from harvest's
main Python so the OCR runtime (rapidocr-onnxruntime / onnxruntime / opencv) never pollutes
harvest's dependency graph. The harvest side never imports OCR libs; it shells out to this script
over a line-JSON stdin/stdout protocol (same isolation model as ``transcribe.py``→``whisper-cli``).

Protocol:
  * Request  : ONE line of JSON on stdin, e.g.
      {"mode": "detect"|"ocr", "video": "<path>", "fps": 3.0, "band_top": 0.78,
       "band_bottom": 0.96, "detect_interval": 30.0, "min_conf": 0.5, "dim": "bottom"}
  * Response : ONE line of JSON on stdout:
      detect -> {"ok": true, "has_hardsubs": bool, "confidence": float,
                 "sampled": N, "positive": M, "reason": "..."}
      ocr    -> {"ok": true, "segments": [{"start","end","text","confidence"}, ...],
                 "frames": N, "sampled_fps": f}
      error  -> {"ok": false, "error": "..."}
  * Diagnostics go to stderr (never stdout) so the harvest shim can parse stdout as JSON.

Engine choice: ``rapidocr_onnxruntime`` reuses PaddleOCR's det+rec models exported to ONNX, so
the detection/recognition quality is PaddleOCR's, but the runtime is plain onnxruntime (cross-
platform, works on Apple Silicon) — no paddlepaddle native build required.
"""
from __future__ import annotations

import difflib
import json
import re
import sys

# stdlib-only until we lazily import cv2/rapidocr below; the shim launches us in the OCR venv.


def _stderr(*a):
    print(*a, file=sys.stderr, flush=True)


def _load_video(path: str):
    import cv2  # lazy: only the OCR venv has it
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"opencv could not open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    nframes = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    w = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0
    h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0
    return cap, {"fps": fps, "nframes": nframes, "w": int(w), "h": int(h)}


def _band_rect(frame_h: int, frame_w: int, band_top: float, band_bottom: float) -> tuple[int, int, int, int]:
    y0 = int(frame_h * band_top)
    y1 = int(frame_h * band_bottom)
    return 0, y0, frame_w, y1 - y0


def _normalize(text: str) -> str:
    """Collapse whitespace and strip common subtitle punctuation for the dedup/similarity surface.
    Preserves CJK characters; only removes characters that vary trivially between cues of the same
    content (spaces, full-width spaces, trailing punctuation)."""
    text = re.sub(r"[\s\u3000]+", "", text)
    text = text.strip("，。！？、!?.,;:;：""''\"'…—-()（）")
    return text


def _similarity(a: str, b: str) -> float:
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _run_ocr_once(ocr, frame):
    """Run RapidOCR on a frame band. Returns list of (text, conf) for detected boxes (rec)."""
    res, _elapse = ocr(frame)
    if not res:
        return []
    # RapidOCR entry: [box, text, conf]
    return [(r[1], float(r[2])) for r in res if r[1]]


# --- detect mode: cheap persistence probe --------------------------------------------

def detect(req: dict) -> dict:
    import cv2  # lazy: only the OCR venv has it
    cap, meta = _load_video(req["video"])
    band_top = float(req.get("band_top", 0.78))
    band_bottom = float(req.get("band_bottom", 0.96))
    interval = float(req.get("detect_interval", 30.0))
    min_conf = float(req.get("min_conf", 0.5))
    fps = meta["fps"] or 30.0
    dur = meta["nframes"] / fps if meta["nframes"] else 0.0
    if dur <= 0:
        return {"ok": False, "error": "could not determine video duration"}

    n_samples = max(3, int(dur // interval))
    step = dur / n_samples
    _stderr(f"[detect] dur={dur:.1f}s samples={n_samples} band=h[{band_top},{band_bottom}]")

    from rapidocr_onnxruntime import RapidOCR  # lazy: OCR venv only
    ocr = RapidOCR()
    sampled = 0
    positive = 0
    conf_sum = 0.0
    for i in range(n_samples):
        t = step * (i + 0.5)
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        x, y, w, bh = _band_rect(meta["h"], meta["w"], band_top, band_bottom)
        band = frame[y:y + bh, x:x + w]
        hits = _run_ocr_once(ocr, band)
        good = [c for (_, c) in hits if c >= min_conf]
        sampled += 1
        if good:
            positive += 1
            conf_sum += max(good)
        _stderr(f"[detect] t={t:.0f}s boxes={len(hits)} good={len(good)} top_conf={max(good) if good else 0:.2f}")
    cap.release()

    density = positive / sampled if sampled else 0.0
    avg_conf = (conf_sum / positive) if positive else 0.0
    # Heuristic: burned-in subtitles persist across the video's spoken content. A bottom band
    # that carries confident text on >40% of sampled frames, with mean conf >=0.6, is a hard-sub
    # track. (Slide text would be sparse and scattered; hard-subs are dense + bottom-anchored.)
    has = sampled >= 3 and density >= 0.40 and avg_conf >= 0.60
    confidence = round(density * (avg_conf if positive else 0.0), 3)
    reason = (
        f"density={density:.2f} avg_conf={avg_conf:.2f} positive={positive}/{sampled}"
    )
    return {
        "ok": True, "has_hardsubs": has, "confidence": confidence,
        "sampled": sampled, "positive": positive, "reason": reason,
    }


# --- ocr mode: dense-sample + temporal dedup ------------------------------------------

def _band_gray(band):
    import cv2  # lazy
    return cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)


def _band_diff(gray_a, gray_b) -> float:
    """Cheap mean-abs-diff normalized to [0,255] between two band grays; raises on shape mismatch.
    Used to gate OCR: when the band hasn't changed materially, the burned-in subtitle is the same
    cue, so skip the expensive det+rec and reuse the last frame's text."""
    import cv2  # lazy
    diff = cv2.absdiff(gray_a, gray_b)
    return float(diff.mean())


def ocr_mode(req: dict) -> dict:
    cap, meta = _load_video(req["video"])
    band_top = float(req.get("band_top", 0.78))
    band_bottom = float(req.get("band_bottom", 0.96))
    fps_target = float(req.get("fps", 3.0))
    min_conf = float(req.get("min_conf", 0.5))
    sim_threshold = float(req.get("sim_threshold", 0.80))
    min_dur = float(req.get("min_dur", 0.3))
    diff_threshold = float(req.get("diff_threshold", 4.0))  # mean-abs-gray diff that flags a change
    fps = meta["fps"] or 30.0
    dur = meta["nframes"] / fps if meta["nframes"] else 0.0
    if dur <= 0:
        return {"ok": False, "error": "could not determine video duration"}

    # Frame hop: how many decoded frames to skip between samples at the target fps. We decode
    # SEQUENTIALLY (cap.read in order) rather than seeking — mp4 seeking by timestamp re-decodes
    # from the last keyframe each call, so per-frame CAP_PROP_POS_MSEC seeks are catastrophically
    # slow (a 4-min video's 3-fps OCR run took ~50 min under seeks; sequential decode is seconds).
    hop = max(1, int(round(fps / max(1.0, min(fps_target, fps)))))
    step_s = hop / fps
    x, y, w, bh = _band_rect(meta["h"], meta["w"], band_top, band_bottom)
    _stderr(
        f"[ocr] dur={dur:.1f}s hop={hop} frames step={step_s:.3f}s band=h[{band_top},{band_bottom}] "
        f"diff_gate={diff_threshold}"
    )

    from rapidocr_onnxruntime import RapidOCR  # lazy: OCR venv only
    ocr = RapidOCR()

    samples = []  # (ts_sec, best_text, best_conf)
    last_gray = None
    last_text_conf = None  # reuse text when band unchanged (same subtitle holds)
    fi = 0
    ocr_calls = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if fi % hop == 0:
            band = frame[y:y + bh, x:x + w]
            gray = _band_gray(band)
            ts = fi / fps
            changed = last_gray is None or _band_diff(last_gray, gray) >= diff_threshold
            if changed:
                hits = _run_ocr_once(ocr, band)
                ocr_calls += 1
                good = [(t, c) for (t, c) in hits if c >= min_conf]
                if good:
                    text, conf = max(good, key=lambda tc: len(tc[0]))
                    last_text_conf = (text, conf)
                else:
                    last_text_conf = (None, 0.0)
                last_gray = gray
            # unchanged band: reuse last_text_conf (the held subtitle cue)
            if last_text_conf is None:
                samples.append((ts, None, 0.0))
            else:
                samples.append((ts, last_text_conf[0], last_text_conf[1]))
        fi += 1
    cap.release()
    _stderr(f"[ocr] decoded_frames={fi} sampled={len(samples)} ocr_calls={ocr_calls}")

    segments = _dedup(samples, step_s, sim_threshold, min_dur)
    _stderr(f"[ocr] segments={len(segments)}")
    return {
        "ok": True,
        "segments": [{"start": s[0], "end": s[1], "text": s[2], "confidence": s[3]}
                     for s in segments],
        "frames": len(samples),
        "sampled_fps": round(1.0 / step_s, 3),
        "ocr_calls": ocr_calls,
    }


def _dedup(samples, step_s, sim_threshold, min_dur):
    """Group consecutive sampled frames with near-identical OCR text into one cue.

    A burned-in subtitle typically holds for a few seconds; consecutive samples sharing high text
    similarity are the same cue. A cue's start is its first sample's ts; its end is the next cue's
    start (or last sample + step). The representative text is the highest-confidence frame's. Cues
    shorter than `min_dur` or entirely noise (no text) are dropped.
    """
    groups = []  # list of (ts_list, text_list, conf_list)
    cur = None
    for ts, text, conf in samples:
        if text is None:
            if cur is not None:
                groups.append(cur)
                cur = None
            continue
        if cur is None:
            cur = ([ts], [text], [conf])
            continue
        sim = _similarity(cur[1][-1], text)
        if sim >= sim_threshold:
            cur[0].append(ts)
            cur[1].append(text)
            cur[2].append(conf)
        else:
            groups.append(cur)
            cur = ([ts], [text], [conf])
    if cur is not None:
        groups.append(cur)

    out = []
    # end = next group's start (so adjacent cues don't overlap). We need look-ahead across groups.
    next_start = None
    for i in range(len(groups) - 1, -1, -1):
        ts_list, text_list, conf_list = groups[i]
        start = ts_list[0]
        end = (next_start if next_start is not None else ts_list[-1] + step_s)
        next_start = start
        dur = end - start
        if dur < min_dur:
            continue
        # representative = highest-confidence frame
        best_i = max(range(len(conf_list)), key=lambda k: conf_list[k])
        best_conf = conf_list[best_i]
        best_text = text_list[best_i].strip()
        if not best_text:
            continue
        out.append((round(start, 3), round(end, 3), best_text, round(best_conf, 3)))
    out.reverse()  # we built it tail-first; restore chronological order
    return out


# --- dispatch ------------------------------------------------------------------------

def main() -> int:
    raw = sys.stdin.readline()
    if not raw:
        _stderr("no stdin request")
        print(json.dumps({"ok": False, "error": "empty stdin"}))
        return 1
    try:
        req = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"bad json: {exc}"}))
        return 1
    mode = req.get("mode")
    try:
        if mode == "detect":
            resp = detect(req)
        elif mode == "ocr":
            resp = ocr_mode(req)
        else:
            resp = {"ok": False, "error": f"unknown mode: {mode!r}"}
    except Exception as exc:  # propagate to harvest as a structured error, never a traceback
        _stderr(f"[worker] uncaught: {exc!r}")
        resp = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(resp, ensure_ascii=False))
    return 0 if resp.get("ok", False) else 1


if __name__ == "__main__":
    sys.exit(main())