"""LLM client (OpenAI-compatible chat completions).

All LLM access goes through here so the model/provider is swappable (NFR4): it targets
whatever OpenAI-compatible endpoint `settings.llm_*` resolves to — OpenRouter (hosted)
or a local server like Ollama/LM Studio/MLX. Supports plain completion and token
streaming. For multimodal turns, a message's `content` may be a list mixing
{"type": "text"} and {"type": "image_url"} parts.
"""
from collections.abc import Iterator

import httpx

from app.config import settings


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "HTTP-Referer": settings.base_url,  # OpenRouter attribution (ignored locally)
        "X-Title": "Lab Machine Assistant",
    }


def _base_payload(messages: list[dict], model: str | None, temperature: float) -> dict:
    """Common request body. Suppresses local models' hidden reasoning tokens, which
    dominate latency on grounded turns without improving answers (the source text is
    already supplied), while leaving hosted providers' defaults alone."""
    payload = {
        "model": model or settings.llm_chat_model,
        "messages": messages,
        "temperature": temperature,
    }
    if settings.llm_provider == "local" and settings.llm_reasoning_effort:
        payload["reasoning_effort"] = settings.llm_reasoning_effort
    return payload


def warm_models(models: list[str] | None = None) -> list[tuple[str, bool, str]]:
    """Preload models into the inference server's memory so the first turn is fast.

    Ollama loads a model on first use (15-20s for a 12B) and unloads it after ~5 min
    idle, so a sporadically-used lab assistant would pay that cost repeatedly. Sending
    an empty prompt loads the weights without generating, and `keep_alive` controls how
    long they stay resident.

    Ollama-specific (uses the native /api endpoint, since the OpenAI-compatible one has
    no keep_alive), so it's a no-op unless the provider is "local". Returns one
    (model, ok, detail) per model; never raises — a failed warm just means a slow first
    query, which must not stop the app from starting.
    """
    if settings.llm_provider != "local":
        return []

    base = settings.llm_base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]

    keep_alive: object = settings.llm_keep_alive
    if isinstance(keep_alive, str) and keep_alive.lstrip("-").isdigit():
        keep_alive = int(keep_alive)  # Ollama wants -1/seconds as a number, not a string

    wanted = models or [settings.llm_chat_model, settings.llm_vision_model]
    results = []
    for name in dict.fromkeys(wanted):  # de-dupe, keep order (chat may == vision)
        try:
            resp = httpx.post(
                f"{base}/api/generate",
                json={"model": name, "prompt": "", "keep_alive": keep_alive},
                timeout=300,
            )
            resp.raise_for_status()
            results.append((name, True, resp.json().get("done_reason", "")))
        except Exception as exc:  # noqa: BLE001 - warmup is best-effort
            results.append((name, False, f"{type(exc).__name__}: {str(exc)[:100]}"))
    return results


def chat(messages: list[dict], model: str | None = None, temperature: float = 0.0) -> str:
    """Return the assistant's full reply text (non-streaming)."""
    payload = _base_payload(messages, model, temperature)
    with httpx.Client(timeout=settings.llm_request_timeout) as client:
        resp = client.post(
            f"{settings.llm_base_url}/chat/completions",
            json=payload,
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def stream_chat(
    messages: list[dict], model: str | None = None, temperature: float = 0.0
) -> Iterator[str]:
    """Yield assistant text deltas as they arrive (SSE)."""
    import json

    payload = {**_base_payload(messages, model, temperature), "stream": True}
    with httpx.Client(timeout=None) as client:
        with client.stream(
            "POST",
            f"{settings.llm_base_url}/chat/completions",
            json=payload,
            headers=_headers(),
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[len("data: ") :]
                if data == "[DONE]":
                    break
                delta = json.loads(data)["choices"][0]["delta"].get("content")
                if delta:
                    yield delta
