"""Vision perception for photo-driven queries (Phase 4, R3).

The vision model's job is **perception, not answers**: given the user's photo AND their
question, it reports the concrete on-screen facts relevant to that question (control
states, active selections, status readouts, error text) — verbatim where possible, and
WITHOUT explaining causes. The manuals (via RAG) explain the "why"; keeping the model in
a perception role preserves the grounding guarantee (R2).

The extracted text also serves a second, unavoidable purpose: retrieval is text-based, so
the image must be turned into text before the manual index can be searched at all.

Everything goes through the provider-agnostic `llm` client, so the underlying model can be
OpenRouter or a local server (Ollama/LM Studio/MLX) with no change here.
"""
import base64
import io
import mimetypes
from pathlib import Path

from PIL import Image, ImageOps

from app.config import settings
from app.services import llm

try:  # iPhones shoot HEIC by default; this teaches Pillow to read it.
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover - HEIC uploads simply fail without it
    pass

# Formats the inference server accepts directly. Anything else (notably HEIC) is
# transcoded to JPEG first, otherwise the request is rejected outright.
_WEB_SAFE_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}

VISION_SYSTEM_PROMPT = (
    "You are a machine-vision assistant for a lab. You are shown a photo of a machine or "
    "an on-screen interface, together with the user's question. Report ONLY the concrete, "
    "observable facts in the image that are relevant to the question: the state of buttons "
    "and controls (enabled/greyed-out/checked), which item is selected or active, status "
    "readouts and numbers, and any on-screen or error text — quote text verbatim. Do NOT "
    "explain causes, give instructions, or guess beyond what is visible. If the image does "
    "not show anything relevant to the question, say so plainly. Answer in 1-4 short "
    "sentences or a short bullet list."
)


def _data_url(image_path: str) -> str:
    """Read an image file and return a base64 data URL for the chat-completions API.

    Phone photos need three fixes before a vision model can use them:
      * HEIC (the iPhone default) is rejected by the inference server -> transcode to JPEG.
      * Portrait shots are often stored rotated with an EXIF orientation flag -> apply it,
        or the model reads the screen sideways.
      * A 12 MP photo is megabytes of base64 and slow to prefill for no accuracy gain ->
        downscale to `vision_max_image_px` on the long edge.
    """
    mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    raw = Path(image_path).read_bytes()

    try:
        with Image.open(io.BytesIO(raw)) as im:
            im = ImageOps.exif_transpose(im)  # honour the camera's rotation flag
            oversized = max(im.size) > settings.vision_max_image_px
            if mime in _WEB_SAFE_MIME and not oversized:
                pass  # already usable; send the original bytes untouched
            else:
                if oversized:
                    im.thumbnail(
                        (settings.vision_max_image_px, settings.vision_max_image_px),
                        Image.LANCZOS,
                    )
                buf = io.BytesIO()
                im.convert("RGB").save(buf, format="JPEG", quality=88)
                raw, mime = buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 - unreadable image: send as-is and let the API judge
        pass

    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def extract_observations(image_path: str, question: str) -> str:
    """Return the vision model's question-conditioned reading of the image.

    May raise httpx.HTTPError (rate limit, timeout, model unreachable) — callers handle
    it the same way as any other LLM failure.
    """
    messages = [
        {"role": "system", "content": VISION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f'The user asks: "{question}". '
                        "Report the on-screen facts relevant to this question."
                    ),
                },
                {"type": "image_url", "image_url": {"url": _data_url(image_path)}},
            ],
        },
    ]
    return llm.chat(messages, model=settings.llm_vision_model).strip()
