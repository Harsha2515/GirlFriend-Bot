"""
agent/client.py — One shared Gemini client for the whole app.

Both the chat path (agent/llm.py) and the analysis path (agent/extractor.py)
go through here, so we build the SDK client once and share a single retry /
model-fallback policy.

Model lists are ordered cheapest-and-fastest-first within each tier so the
free-tier quota stretches as far as possible. If a model is rate limited
(429 / RESOURCE_EXHAUSTED) we move straight to the next candidate instead of
burning the retry budget on a model that is already refusing us.
"""
import asyncio
import logging

from google import genai
from google.genai import types as genai_types

from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=GEMINI_API_KEY)

# Conversational replies. Lite first, deliberately: measured against this key
# the lite models answer in ~1s versus 20-43s for gemini-flash-latest, the
# persona quality is indistinguishable for casual chat, and the free-tier
# limits are far more generous — which is what lets several people share one
# key. Heavier models remain as fallbacks if the lite tier is exhausted.
CHAT_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-flash-latest",
]

# Structured JSON extraction — Lite is plenty and costs far less quota.
ANALYZE_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
]

# Google Search grounding and native image output. Neither is included in
# every free-tier key -- the tools that use these treat a refusal as "not
# available right now" and fall back to keyless providers.
GROUNDING_MODELS = ["gemini-flash-lite-latest"]
IMAGE_MODELS = ["gemini-2.5-flash-image"]

MAX_RETRIES = 2
RETRY_DELAY = 1     # seconds between retries on the same model


def is_rate_limited(exc: Exception) -> bool:
    text = str(exc)
    return "RESOURCE_EXHAUSTED" in text or "429" in text


def _response_has_content(response) -> bool:
    """True if the model produced text, a tool call, or an image."""
    try:
        parts = response.candidates[0].content.parts or []
    except (AttributeError, IndexError, TypeError):
        return False
    return any(
        getattr(p, "text", None) or getattr(p, "function_call", None)
        or getattr(p, "inline_data", None)
        for p in parts
    )


async def generate_response(
    contents,
    config: genai_types.GenerateContentConfig,
    models: list[str],
):
    """
    Run generate_content against each model in `models` until one succeeds,
    and return the full SDK response -- needed when the reply may be a tool
    call or an image rather than plain text.

    The SDK call is blocking, so it runs in the default executor to keep the
    Telegram event loop responsive.

    Raises the last exception if every model and retry is exhausted.
    """
    last_exception = None

    for model_name in models:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda m=model_name: _client.models.generate_content(
                        model=m,
                        contents=contents,
                        config=config,
                    ),
                )
                if not _response_has_content(response):
                    raise ValueError("Empty response from Gemini")
                return response

            except Exception as exc:
                last_exception = exc
                logger.warning(
                    f"Gemini '{model_name}' attempt {attempt}/{MAX_RETRIES} failed: {exc}"
                )
                if is_rate_limited(exc):
                    break  # this model is out of quota — try the next one now
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)

    logger.error(f"All Gemini models exhausted: {models}")
    raise last_exception or RuntimeError("LLM call failed after all retries")


def response_text(response) -> str:
    """Concatenate the text parts of a response, ignoring tool calls/images."""
    try:
        parts = response.candidates[0].content.parts or []
    except (AttributeError, IndexError, TypeError):
        return ""
    return "".join(p.text for p in parts if getattr(p, "text", None)).strip()


async def generate(
    contents,
    config: genai_types.GenerateContentConfig,
    models: list[str],
) -> str:
    """Like generate_response(), but for callers that only want text."""
    response = await generate_response(contents, config, models)
    text = response_text(response)
    if not text:
        raise ValueError("Gemini returned no text")
    return text
