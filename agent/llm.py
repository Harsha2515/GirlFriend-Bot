"""
agent/llm.py — The conversational reply path.

Converts our internal message format to Gemini's and delegates the actual
call (with retries and model fallback) to agent/client.py.
"""
import logging

from google.genai import types as genai_types

from agent.client import CHAT_MODELS, generate

logger = logging.getLogger(__name__)

MAX_TOKENS  = 1024   # max output tokens per reply
TEMPERATURE = 0.85   # slightly creative, stays coherent


def _format_history(history: list[dict]) -> list[genai_types.Content]:
    """
    Convert internal format -> Gemini Content format.
      Internal: [{role: 'user'|'assistant', content: '...'}]
      Gemini:   [Content(role='user'|'model', parts=[Part(text='...')])]
    """
    return [
        genai_types.Content(
            role="model" if msg["role"] == "assistant" else "user",
            parts=[genai_types.Part(text=msg["content"])],
        )
        for msg in history
    ]


async def call_llm(
    system_prompt: str,
    history: list[dict],
    user_message: str,
) -> str:
    """
    Call Gemini with a system prompt, conversation history, and the latest
    user message. Returns the assistant reply as a string.
    """
    config = genai_types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
    )

    contents = _format_history(history) + [
        genai_types.Content(role="user", parts=[genai_types.Part(text=user_message)])
    ]

    return await generate(contents, config, CHAT_MODELS)
