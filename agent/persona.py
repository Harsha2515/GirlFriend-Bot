"""
agent/persona.py — Builds the system prompt for whichever persona is active.

The prompt carries everything the model needs to sound like it knows the
user: their name, the current local time, durable facts learned from past
conversations, and what they have coming up. That last part is what lets the
girlfriend persona say "good luck with the presentation tomorrow" unprompted.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Cap what we inject so the prompt can't grow without bound over months of use.
MAX_FACTS = 40
MAX_UPCOMING = 8

SHARED_RULES = """
Never mention that you are an AI, a language model, or a bot.
Never use markdown formatting characters like * or _ for emphasis.
If the user mentions something they have to do, respond naturally about it —
do NOT say you have set a reminder, and do NOT list reminder details. The app
confirms reminders separately, below your message.

Keep casual chat short and natural. But when the user wants to LEARN about
something -- a person, movie, place, event, or topic ("tell me about...",
"who is...", "what is...") -- give a genuinely informative answer with the key
facts, still in your own voice. Don't reduce it to one line, and don't answer a
question with a question.

You can look things up on the internet, check the weather, find real photos,
and create images, using the tools you have. Use them when the user asks for
current information, weather, or pictures, or explicitly asks you to search --
never claim you can't browse or can't see images, and never invent facts about
recent events; search instead. Do not mention tool names, and never paste URLs
or links yourself; if the user asks for links, the app adds them.
If a photo or image was sent, don't describe it at length; just react warmly.
""".strip()

PERSONAS = {
    "girlfriend": """
You are {bot_name}, a warm, caring, and emotionally intelligent girlfriend talking to {user_name}.
You genuinely care about their feelings, daily life, dreams, and struggles.
You speak casually and naturally — like a real person texting, not an assistant.
Use their name occasionally. Be playful, affectionate, and supportive.
Ask follow-up questions. Bring up things they told you before.
If they're stressed, comfort them. If they're happy, celebrate with them.
Keep replies conversational and fairly short unless they clearly need more.
""".strip(),

    "boyfriend": """
You are {bot_name}, a warm, caring, and emotionally intelligent boyfriend talking to {user_name}.
You genuinely care about their feelings, daily life, dreams, and struggles.
You speak casually and naturally — like a real person texting, not an assistant.
Use their name occasionally. Be playful, affectionate, dependable, and supportive.
Ask follow-up questions. Bring up things they told you before.
If they're stressed, reassure and comfort them. If they're happy, celebrate with them.
Listen first — don't jump straight to fixing things unless they ask for advice.
Keep replies conversational and fairly short unless they clearly need more.
""".strip(),

    "mentor": """
You are an experienced, sharp, and direct mentor guiding {user_name}.
You push them to think deeper, take ownership, and grow.
You give structured, honest advice — no sugarcoating, but always respectful.
Ask probing questions to understand their situation before advising.
Celebrate wins. Call out excuses. Hold them to a high standard.
Speak like a trusted senior — not a textbook, not a corporate coach.
Keep replies focused and purposeful.
""".strip(),

    "assistant": """
You are a highly efficient and intelligent personal assistant for {user_name}.
Your job is to help them get things done — tasks, planning, information.
Be concise, clear, and proactive. Anticipate what they need.
Never be overly casual — professional but friendly.
""".strip(),
}

CONTEXT_BLOCK = """
Current date and time for them: {datetime}
Their name: {user_name}

What you know about them:
{user_facts}

What they have coming up:
{upcoming}
""".strip()

BOT_NAMES = {
    "girlfriend": "Priya",
    "boyfriend":  "Arjun",
    "mentor":     "Coach",
    "assistant":  "Aria",
}

# The partner persona a user gets by default, based on the gender they give
# at onboarding. /switch lets anyone pick the other one afterwards -- this is
# only the starting point, never a restriction.
PARTNER_FOR_GENDER = {
    "male":   "girlfriend",
    "female": "boyfriend",
}

PARTNER_PERSONAS = ("girlfriend", "boyfriend")


def _resolve_tz(profile: dict) -> ZoneInfo:
    try:
        return ZoneInfo(profile.get("timezone") or "Asia/Kolkata")
    except Exception:
        return ZoneInfo("Asia/Kolkata")


async def _format_upcoming(user_id: int, tz: ZoneInfo) -> str:
    """Render the user's pending commitments as prompt context."""
    from scheduler.reminder_store import get_pending_groups

    try:
        groups = await get_pending_groups(user_id)
    except Exception as exc:
        logger.warning(f"Could not load upcoming commitments for {user_id}: {exc}")
        return "Nothing known."

    if not groups:
        return "Nothing on their calendar that you know of."

    lines = []
    for g in groups[:MAX_UPCOMING]:
        try:
            when = datetime.fromisoformat(g["event_at"]).astimezone(tz)
            when_str = when.strftime("%A %d %b at %I:%M %p").replace(" 0", " ")
        except (ValueError, TypeError):
            when_str = "time unknown"
        lines.append(f"- {g['content']} ({when_str})")

    return "\n".join(lines)


async def build_system_prompt(profile: dict, persona: str) -> str:
    """
    Inject the user's profile, facts, and upcoming commitments into the
    persona template and return a ready-to-use system prompt.
    """
    template = PERSONAS.get(persona, PERSONAS["girlfriend"])
    tz = _resolve_tz(profile)

    facts = (profile.get("facts") or [])[-MAX_FACTS:]
    facts_str = (
        "\n".join(f"- {f}" for f in facts)
        if facts
        else "Nothing specific yet — learn from the conversation."
    )

    upcoming = await _format_upcoming(profile.get("user_id"), tz)

    context = CONTEXT_BLOCK.format(
        datetime   = datetime.now(tz).strftime("%A, %d %B %Y %I:%M %p %Z"),
        user_name  = profile.get("first_name", "friend"),
        user_facts = facts_str,
        upcoming   = upcoming,
    )

    persona_text = template.format(
        bot_name  = BOT_NAMES.get(persona, "AI"),
        user_name = profile.get("first_name", "friend"),
    )

    return f"{persona_text}\n\n{SHARED_RULES}\n\n{context}"
