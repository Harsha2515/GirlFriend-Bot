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

# Conversational replies — quality matters, so full Flash first.
CHAT_MODELS = [
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-flash-lite-latest",
]

# Structured JSON extraction — Lite is plenty and costs far less quota.
ANALYZE_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
]

MAX_RETRIES = 2
RETRY_DELAY = 1     # seconds between retries on the same model


def _is_rate_limited(exc: Exception) -> bool:
    text = str(exc)
    return "RESOURCE_EXHAUSTED" in text or "429" in text


async def generate(
    contents,
    config: genai_types.GenerateContentConfig,
    models: list[str],
) -> str:
    """
    Run generate_content against each model in `models` until one succeeds.

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

                text = response.text.strip() if response and response.text else ""
                if not text:
                    raise ValueError("Empty response from Gemini")
                return text

            except Exception as exc:
                last_exception = exc
                logger.warning(
                    f"Gemini '{model_name}' attempt {attempt}/{MAX_RETRIES} failed: {exc}"
                )
                if _is_rate_limited(exc):
                    break  # this model is out of quota — try the next one now
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)

    logger.error(f"All Gemini models exhausted: {models}")
    raise last_exception or RuntimeError("LLM call failed after all retries")
