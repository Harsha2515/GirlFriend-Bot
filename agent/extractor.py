"""
agent/extractor.py — Understands what a message implies beyond the chat reply.

One Gemini call per user message extracts BOTH:
  * time-sensitive commitments (meetings, tasks, errands, deadlines)
  * durable personal facts worth remembering long term

Doing both in a single call halves our free-tier quota usage.

Note there is deliberately no keyword pre-filter here. The old version
required words like "tomorrow" or "meeting" to be present, which silently
dropped things like "I need to submit the report by end of this week".
Instead we only skip messages that are too short to carry any commitment.
"""
import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from google.genai import types as genai_types

from agent.client import ANALYZE_MODELS, generate

logger = logging.getLogger(__name__)

# Messages this short can't contain a commitment worth an API call.
MIN_ANALYZE_LENGTH = 12

# Chatter that clears the length bar but never carries intent.
SKIP_EXACT = {
    "ok", "okay", "k", "hi", "hey", "hello", "lol", "haha", "hmm", "yeah",
    "yes", "no", "nope", "thanks", "thank you", "good night", "goodnight",
    "good morning", "love you", "miss you", "bye", "sure",
}

ANALYZE_PROMPT = """
You extract structured data from a chat message. You never write prose.

Current datetime: {now}
User timezone: {timezone}
User's name: {user_name}

Recent conversation (for context — resolve references like "that" or "it"):
{history}

Latest user message: "{message}"

--- TASK 1: COMMITMENTS ---
Find anything the user has to DO or ATTEND at a future time. Include things
stated indirectly — a commitment does not need the word "remind".

Extract these:
- "tomorrow I have a meeting at 11am and I have to present"  -> event
- "mom asked me to go to the bank, I have to go tomorrow"    -> task
- "I need to submit the report by end of this week"          -> task
- "my interview is on Friday 10am"                           -> event
- "pay rent on the 5th"                                      -> task

Do NOT extract:
- Things already finished ("I had a meeting yesterday")
- Habits or generalities ("I usually wake up at 7")
- Hypotheticals ("I might go out sometime")

Rules for timing:
- "event" = a fixed appointment the user attends at a specific time.
- "task"  = something to get done, where the time is a deadline or a rough slot.
- If only a day is given with no clock time, use 09:00 in the user's timezone
  and set "all_day": true.
- "end of the week" means Friday. "this weekend" means Saturday morning.
- Always output event_at as an ISO8601 datetime in UTC ending with Z.
- Never output a time in the past.

--- TASK 2: FACTS ---
Pull out durable personal facts worth remembering for months: their job,
studies, family and friends' names, home city, hobbies, health, preferences,
important dates, ongoing goals.

Do NOT store: passing moods, one-off plans, anything already in the recent
conversation above, or anything you would not still care about in a month.
Write each fact as a short third-person statement, e.g. "Works as a backend
developer at TCS" or "Mother's name is Lakshmi".

--- OUTPUT ---
Respond with ONLY this JSON object. No markdown, no code fences, no comments.

{{
  "commitments": [
    {{
      "content": "short imperative description, e.g. 'Present at the team meeting'",
      "event_at": "ISO8601 UTC ending in Z",
      "kind": "event" or "task",
      "all_day": true or false
    }}
  ],
  "facts": ["short third-person fact", "..."]
}}

Use empty arrays when there is nothing to extract.
""".strip()


def _resolve_tz(profile: dict) -> ZoneInfo:
    try:
        return ZoneInfo(profile.get("timezone") or "Asia/Kolkata")
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _format_history(history: list[dict], limit: int = 6) -> str:
    """Render the tail of the conversation as plain text for the prompt."""
    if not history:
        return "(no earlier messages)"
    recent = history[-limit:]
    return "\n".join(
        f"{'User' if m['role'] == 'user' else 'You'}: {m['content']}" for m in recent
    )


def _parse_json(raw: str) -> dict | None:
    """Pull the first JSON object out of a model response."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        logger.warning(f"Analyzer response had no JSON object: {raw[:200]}")
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        logger.warning(f"Analyzer returned invalid JSON: {exc}")
        return None


def _parse_commitment(item: dict, now_utc: datetime) -> dict | None:
    """Validate one raw commitment dict into our internal shape, or drop it."""
    raw_when = item.get("event_at")
    content = (item.get("content") or "").strip()
    if not raw_when or not content:
        return None

    try:
        event_at = datetime.fromisoformat(str(raw_when).replace("Z", "+00:00"))
    except ValueError:
        logger.warning(f"Analyzer gave an unparseable event_at: {raw_when!r}")
        return None

    if event_at.tzinfo is None:
        event_at = event_at.replace(tzinfo=ZoneInfo("UTC"))

    if event_at <= now_utc:
        logger.info(f"Dropping commitment in the past: {content} @ {event_at}")
        return None

    kind = item.get("kind")
    if kind not in ("event", "task"):
        kind = "task"

    return {
        "content":  content[:200],
        "event_at": event_at,
        "kind":     kind,
        "all_day":  bool(item.get("all_day")),
    }


async def analyze_message(
    message: str,
    profile: dict,
    history: list[dict] | None = None,
) -> dict:
    """
    Analyse a user message for commitments and durable facts.

    Returns {"commitments": [...], "facts": [...]}, always — on any failure
    it returns empty lists rather than raising, so chat is never blocked.
    """
    empty = {"commitments": [], "facts": []}

    stripped = message.strip()
    if len(stripped) < MIN_ANALYZE_LENGTH or stripped.lower().strip("!.? ") in SKIP_EXACT:
        return empty

    tz = _resolve_tz(profile)
    prompt = ANALYZE_PROMPT.format(
        now       = datetime.now(tz).strftime("%A, %d %B %Y %I:%M %p %Z"),
        timezone  = str(tz),
        user_name = profile.get("first_name", "the user"),
        history   = _format_history(history or []),
        message   = message,
    )

    config = genai_types.GenerateContentConfig(
        max_output_tokens=1024,
        temperature=0.1,          # deterministic JSON
        response_mime_type="application/json",
    )

    try:
        raw = await generate(prompt, config, ANALYZE_MODELS)
    except Exception as exc:
        logger.warning(f"Message analysis failed: {exc}")
        return empty

    data = _parse_json(raw)
    if not data:
        return empty

    now_utc = datetime.now(ZoneInfo("UTC"))

    commitments = []
    for item in data.get("commitments") or []:
        if isinstance(item, dict):
            parsed = _parse_commitment(item, now_utc)
            if parsed:
                commitments.append(parsed)

    facts = [
        str(f).strip()[:200]
        for f in (data.get("facts") or [])
        if isinstance(f, str) and f.strip()
    ]

    return {"commitments": commitments, "facts": facts}
