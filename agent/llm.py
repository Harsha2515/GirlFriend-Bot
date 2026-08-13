"""
agent/llm.py — Gemini Flash LLM call using the new google-genai SDK.

Uses the updated `google.genai` package (replaces deprecated google.generativeai).
Converts our internal message format to Gemini's format, runs the blocking
SDK call in a thread pool executor so the async event loop stays unblocked.
"""
import logging
import asyncio
from google import genai
from google.genai import types as genai_types
from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

# ── Configure Gemini client once ───────────────────────────────────────────────
_client = genai.Client(api_key=GEMINI_API_KEY)

MODEL_CANDIDATES = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-3.1-flash-lite"]
MAX_RETRIES  = 2
RETRY_DELAY  = 1      # seconds between retries
MAX_TOKENS   = 1024   # max output tokens per reply
TEMPERATURE  = 0.85   # slightly creative, stays coherent


# ── History formatter ──────────────────────────────────────────────────────────

def _format_history(history: list[dict]) -> list[genai_types.Content]:
    """
    Convert internal format → Gemini Content format.
      Internal: [{role: 'user'|'assistant', content: '...'}]
      Gemini:   [Content(role='user'|'model', parts=[Part(text='...')])]
    """
    formatted = []
    for msg in history:
        role = "model" if msg["role"] == "assistant" else "user"
        formatted.append(
            genai_types.Content(
                role=role,
                parts=[genai_types.Part(text=msg["content"])]
            )
        )
    return formatted


# ── Main LLM call ──────────────────────────────────────────────────────────────

async def call_llm(
    system_prompt: str,
    history: list[dict],
    user_message: str,
) -> str:
    """
    Call Gemini Flash with a system prompt, conversation history, and the
    latest user message. Returns the assistant reply as a string.
    Automatically falls back across model candidates if rate limited.
    """
    formatted_history = _format_history(history)

    config = genai_types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
    )

    contents = formatted_history + [
        genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=user_message)]
        )
    ]

    last_exception = None

    for model_name in MODEL_CANDIDATES:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda m=model_name: _client.models.generate_content(
                        model=m,
                        contents=contents,
                        config=config,
                    ),
                )

                reply = response.text.strip() if response and response.text else ""
                if not reply:
                    raise ValueError("Empty response from Gemini")

                return reply

            except Exception as exc:
                last_exception = exc
                logger.warning(f"Gemini model '{model_name}' attempt {attempt}/{MAX_RETRIES} failed: {exc}")
                if "RESOURCE_EXHAUSTED" in str(exc) or "429" in str(exc):
                    # Rate limited on this model — break immediately to try next fallback model
                    break
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)

    logger.error("All Gemini models and retries exhausted.")
    raise last_exception or RuntimeError("LLM call failed after all retries")