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

MODEL_NAME   = "gemini-3.5-flash"   # fast, free, supported model
MAX_RETRIES  = 3
RETRY_DELAY  = 2      # seconds between retries
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

    Args:
        system_prompt : Fully built persona system prompt
        history       : Previous messages [{role, content}]
        user_message  : The user's latest message

    Returns:
        str — model reply text
    """
    formatted_history = _format_history(history)

    config = genai_types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Build the full contents list: history + new user message
            contents = formatted_history + [
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=user_message)]
                )
            ]

            # Run the blocking SDK call off the event loop
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: _client.models.generate_content(
                    model=MODEL_NAME,
                    contents=contents,
                    config=config,
                ),
            )

            reply = response.text.strip() if response.text else ""
            if not reply:
                raise ValueError("Empty response from Gemini")

            return reply

        except Exception as exc:
            logger.warning(f"Gemini attempt {attempt}/{MAX_RETRIES} failed: {exc}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
            else:
                logger.error("All Gemini retries exhausted.")
                raise

    raise RuntimeError("LLM call failed after all retries")