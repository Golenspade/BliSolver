"""Per-frame captioning via the LM Studio OpenAI-compatible endpoint (SPEC §5 step 5, §7, D7).

Transport (settled by first-contact, build step 5): base64 data-URI images over the REST chat API
work; no SDK fallback needed. The model is a reasoning model, so max_tokens must be generous.

D7 — projector must be verified before captioning. A missing/unbound mmproj makes the model emit
confident, well-formed, hallucinated captions while every health check passes. The only reliable
test is a nonce the model can't produce blind: render a random code, ask it to read it back,
require an exact match. The probe is armed by a fingerprint of the loaded-model metadata so it only
re-fires after LM Studio's state changes (single-user local box; the risk event is "I touched it").
On failure: hard-stop, loud error. Never degrade to silent caption-less frames.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import secrets
from pathlib import Path

from .config import Settings
from .schema import Frame

PROMPT_VERSION = "1"
_NONCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I

CAPTION_PROMPT = (
    "You are extracting study notes from a single lecture-slide frame.\n"
    "Respond in EXACTLY this format, nothing else:\n"
    "OCR:\n<every piece of visible text, verbatim, preserving Chinese; or NONE>\n"
    "DESCRIPTION:\n<one concise paragraph describing any figures, diagrams, charts, or slide "
    "layout; or NONE>"
)


def _client(settings: Settings):
    from openai import OpenAI

    return OpenAI(base_url=settings.lmstudio_base_url, api_key=settings.lmstudio_api_key)


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _ask_image(client, model: str, png: bytes, prompt: str, max_tokens: int = 2048) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _data_uri(png)}},
            ],
        }],
        temperature=0,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


def _nonce_png(text: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (480, 200), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 96)
    except Exception:
        font = ImageFont.load_default()
    draw.text((40, 50), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _fingerprint(client) -> str:
    """Hash the loaded-model metadata. /v1/models can't distinguish mmproj-loaded from text-only,
    so this only decides WHEN to probe; the nonce is what proves the projector (D7)."""
    ids = sorted(m.id for m in client.models.list().data)
    return hashlib.sha1(json.dumps(ids).encode()).hexdigest()


def _fp_path(settings: Settings) -> Path:
    return settings.cache_dir / "vision_fingerprint.json"


def verify_projector(settings: Settings) -> None:
    client = _client(settings)
    fp = _fingerprint(client)

    fpf = _fp_path(settings)
    if fpf.exists():
        last = json.loads(fpf.read_text(encoding="utf-8")).get("passed_fingerprint")
        if last == fp:
            return  # state unchanged since the last passing probe — skip (D7 step 2)

    nonce = "".join(secrets.choice(_NONCE_ALPHABET) for _ in range(6))
    answer = _ask_image(
        client,
        settings.lmstudio_vision_model,
        _nonce_png(nonce),
        f"What {len(nonce)}-character code is shown in this image? Reply with only the code.",
        max_tokens=600,
    )
    if nonce not in answer.replace(" ", ""):
        raise RuntimeError(
            "Vision projector check FAILED (D7): the model could not read a nonce image "
            f"(expected {nonce!r}, got {answer!r}). The mmproj projector is likely not loaded/"
            f"bound to {settings.lmstudio_vision_model!r} in LM Studio. Refusing to caption — a "
            "missing projector yields confident hallucinated captions. Load the mmproj and retry."
        )

    fpf.parent.mkdir(parents=True, exist_ok=True)
    fpf.write_text(json.dumps({"passed_fingerprint": fp}), encoding="utf-8")


def _parse(text: str) -> tuple[str | None, str | None]:
    """Split the OCR:/DESCRIPTION: response into (ocr, caption). Falls back to caption-only."""
    up = text
    if "DESCRIPTION:" in up:
        ocr_part, desc_part = up.split("DESCRIPTION:", 1)
        ocr = ocr_part.split("OCR:", 1)[-1].strip()
        caption = desc_part.strip()
    else:
        ocr, caption = "", up.strip()
    ocr = None if ocr.strip().upper() in ("", "NONE") else ocr.strip()
    caption = None if caption.strip().upper() in ("", "NONE") else caption.strip()
    return ocr, caption


def caption_frames(
    frames: list[Frame], frame_paths: dict[str, Path], settings: Settings
) -> list[Frame]:
    """Caption each frame independently (SPEC §5 step 4). Call verify_projector first (D7).
    Returns new Frame objects with ocr/caption filled."""
    client = _client(settings)
    model = settings.lmstudio_vision_model
    out: list[Frame] = []
    for fr in frames:
        src = frame_paths.get(fr.path or "")
        png = Path(src).read_bytes() if src else b""
        text = _ask_image(client, model, png, CAPTION_PROMPT) if png else ""
        ocr, caption = _parse(text)
        out.append(fr.model_copy(update={"ocr": ocr, "caption": caption}))
    return out
