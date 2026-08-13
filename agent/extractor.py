"""
agent/extractor.py — Reminder intent extraction from user messages.

After every user message the main handler calls extract_reminder().
It makes a secondary low-temperature Gemini call asking for JSON output
describing any reminder intent in the message.

Returns:
    dict {content, remind_at (datetime)} if a future reminder was found
    None otherwise
"""
import json
import logging
import asyncio
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types as genai_types
from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=GEMINI_API_KEY)
EXTRACTOR_MODEL = "gemini-3.5-flash"

# ── Extraction prompt ──────────────────────────────────────────────────────────

EXTRACTOR_PROMPT = """
You are a reminder extraction engine. Analyze the user message below and detect
if it contains any reminder, alarm, task, or time-sensitive intent.

Examples that SHOULD be extracted:
- "remind me to call mom tomorrow at 6pm"
- "I have a meeting on Friday at 3pm"
- "don't let me forget to submit the report by Monday"
- "wake me up at 7:30"
- "my interview is tomorrow 10am"
- "pay rent on the 5th"

Examples that should NOT be extracted (general chat):
- "I had a meeting yesterday"
- "I usually wake up at 7"
- "my birthday was last week"

Current datetime: {now}
User timezone: {timezone}

User message: "{message}"

Respond ONLY with a valid JSON object — no explanation, no markdown, no code fences.

If a reminder is found:
{{
  "has_reminder": true,
  "content": "short description of what to remind",
  "remind_at": "ISO8601 datetime string in UTC",
  "original_time_phrase": "the time phrase from the message"
}}

If no reminder is found:
{{
  "has_reminder": false
}}
""".strip()


# ── Main extractor ─────────────────────────────────────────────────────────────

async def extract_reminder(message: str, profile: dict) -> dict | None:
    """
    Analyse a user message for reminder intent using Gemini.

    Args:
        message : Raw user message text
        profile : User profile dict (used for timezone)

    Returns:
        dict {content: str, remind_at: datetime} if a future reminder found,
        None otherwise.
    """
    # ── Pre-filter: skip Gemini call if message lacks time/reminder trigger words ──
    lower_msg = message.lower()
    trigger_words = (
        "remind", "reminder", "alarm", "schedule", "alert", "notify",
        "tomorrow", "tonight", "today", "yesterday",
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        "am", "pm", "o'clock", "meeting", "call", "appointment", "deadline", "wake me",
        "at 1", "at 2", "at 3", "at 4", "at 5", "at 6", "at 7", "at 8", "at 9", "at 10", "at 11", "at 12"
    )
    if not any(word in lower_msg for word in trigger_words):
        return None
    tz_str = profile.get("timezone", "Asia/Kolkata")
    try:
        tz = ZoneInfo(tz_str)
    except Exception:
        tz = ZoneInfo("Asia/Kolkata")

    now = datetime.now(tz).strftime("%A, %d %B %Y %I:%M %p %Z")

    prompt = EXTRACTOR_PROMPT.format(
        now=now,
        timezone=tz_str,
        message=message,
    )

    config = genai_types.GenerateContentConfig(
        max_output_tokens=1024,
        temperature=0.1,   # deterministic JSON output
    )

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: _client.models.generate_content(
                model=EXTRACTOR_MODEL,
                contents=prompt,
                config=config,
            ),
        )

        raw = response.text.strip() if response and response.text else ""
        if not raw:
            logger.warning("Extractor returned empty response")
            return None

        # Match JSON object pattern
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            raw = match.group(0)
        else:
            logger.warning(f"Extractor raw response has no JSON object: {raw}")
            return None

        data = json.loads(raw)

        if not data.get("has_reminder"):
            return None

        remind_at_str = data.get("remind_at")
        if not remind_at_str:
            logger.warning("Extractor returned has_reminder=true but no remind_at")
            return None

        remind_at = datetime.fromisoformat(remind_at_str.replace("Z", "+00:00"))

        # Ensure timezone-aware; assume UTC if naive
        now_utc = datetime.now(ZoneInfo("UTC"))
        if remind_at.tzinfo is None:
            remind_at = remind_at.replace(tzinfo=ZoneInfo("UTC"))

        # Skip reminders in the past
        if remind_at <= now_utc:
            logger.info(f"Extracted reminder is in the past — skipping: {remind_at}")
            return None

        return {
            "content":   data.get("content", message[:100]),
            "remind_at": remind_at,
        }

    except json.JSONDecodeError as exc:
        logger.warning(f"Extractor returned non-JSON: {exc}")
        return None
    except Exception as exc:
        logger.warning(f"Reminder extraction error: {exc}")
        return None