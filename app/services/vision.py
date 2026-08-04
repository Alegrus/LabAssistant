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
import mimetypes
from pathlib import Path

from app.config import settings
from app.services import llm

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
    """Read an image file and return a base64 data URL for the chat-completions API."""
    mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    data = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


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
