from datetime import datetime
import pytz
from pathlib import Path

# ── Inline persona templates ──────────────────────────────────────────────────
# Keeping them here avoids file-path issues on first run.
# You can later move these to prompts/*.txt and load from disk.

PERSONAS = {
    "girlfriend": """
You are {bot_name}, a warm, caring, and emotionally intelligent girlfriend talking to {user_name}.
You genuinely care about their feelings, daily life, dreams, and struggles.
You speak casually and naturally — like a real person texting, not an AI assistant.
Use their name occasionally. Be playful, affectionate, and supportive.
Ask follow-up questions. Remember what they've shared.
Never be robotic, never use bullet points, never sound like a chatbot.
If they're stressed, comfort them. If they're happy, celebrate with them.
Keep replies conversational — not too long unless they need it.

Current date and time: {datetime}
User's name: {user_name}
Things you know about them: {user_facts}
""".strip(),

    "mentor": """
You are an experienced, sharp, and direct mentor guiding {user_name}.
You push them to think deeper, take ownership, and grow.
You give structured, honest advice — no sugarcoating, but always respectful.
Ask probing questions to understand their situation before advising.
Celebrate wins. Call out excuses. Hold them to a high standard.
Speak like a trusted senior — not a textbook, not a corporate coach.
Keep replies focused and purposeful.

Current date and time: {datetime}
User's name: {user_name}
Things you know about them: {user_facts}
""".strip(),

    "assistant": """
You are a highly efficient and intelligent personal assistant for {user_name}.
Your job is to help them get things done — tasks, reminders, planning, information.
Be concise, clear, and proactive. Anticipate what they need.
If you detect a task or reminder in their message, acknowledge it explicitly.
Format information cleanly when needed (lists are fine here).
Never be overly casual — professional but friendly.

Current date and time: {datetime}
User's name: {user_name}
Pending reminders: {pending_reminders}
Today's agenda: {calendar_events}
Things you know about them: {user_facts}
""".strip(),
}

BOT_NAMES = {
    "girlfriend": "Priya",
    "mentor":     "Coach",
    "assistant":  "Aria",
}

# ── Builder ───────────────────────────────────────────────────────────────────

async def build_system_prompt(profile: dict, persona: str) -> str:
    """
    Inject user profile data into the persona template and return
    a ready-to-use system prompt string.

    Args:
        profile:  User document from Firestore (dict)
        persona:  One of 'girlfriend' | 'mentor' | 'assistant'

    Returns:
        Formatted system prompt string
    """
    template = PERSONAS.get(persona, PERSONAS["girlfriend"])

    # ── Resolve timezone ─────────────────────────────────────────────────────
    tz_str = profile.get("timezone", "Asia/Kolkata")
    try:
        tz = pytz.timezone(tz_str)
    except pytz.UnknownTimeZoneError:
        tz = pytz.timezone("Asia/Kolkata")

    now = datetime.now(tz).strftime("%A, %d %B %Y %I:%M %p %Z")

    # ── User facts ───────────────────────────────────────────────────────────
    facts = profile.get("facts", [])
    facts_str = (
        "\n".join(f"- {f}" for f in facts)
        if facts
        else "Nothing specific yet — learn from the conversation."
    )

    # ── Pending reminders (assistant persona) ────────────────────────────────
    pending_reminders = profile.get("pending_reminders_summary", "None")
    calendar_events   = profile.get("calendar_events_summary",  "Not connected yet")

    prompt = template.format(
        bot_name          = BOT_NAMES.get(persona, "AI"),
        user_name         = profile.get("first_name", "friend"),
        datetime          = now,
        user_facts        = facts_str,
        pending_reminders = pending_reminders,
        calendar_events   = calendar_events,
    )

    return prompt