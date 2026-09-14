"""
bot/onboarding.py — Gender question, partner assignment, and persona switching.

Shared by the message handler and the slash commands so both paths offer the
same buttons and apply the same rules:

  * A user who hasn't told us their gender is asked once, before chatting.
  * 'male' starts with the Girlfriend persona, 'female' with the Boyfriend one.
  * That is only the starting point. /switch picks either partner at any time,
    and /mode still reaches Mentor and Assistant.

Pre-existing users have gender NULL after the migration, so they are asked on
their next message. Their history, facts, and reminders are untouched.
"""
import logging

from telegram import Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from agent.persona import BOT_NAMES, PARTNER_FOR_GENDER
from memory.user_profile import create_or_update_user

logger = logging.getLogger(__name__)

# ── Gender ────────────────────────────────────────────────────────────────────

GENDER_KEYBOARD = [["👨 Male", "👩 Female"]]

# The emoji buttons are unambiguous and always count as an answer.
GENDER_BUTTONS = {
    "👨 male":   "male",
    "👩 female": "female",
}

# Plain words only count while we're actually waiting for an answer. Once
# gender is set, a message that just says "female" is ordinary conversation
# ("who's your boss?" -> "female"), not a request to change it.
GENDER_WORDS = {
    "male": "male", "m": "male", "man": "male", "boy": "male",
    "female": "female", "f": "female", "woman": "female", "girl": "female",
}

# ── Personas ──────────────────────────────────────────────────────────────────

PARTNER_KEYBOARD = [["💕 Girlfriend", "💙 Boyfriend"]]

MODE_KEYBOARD = [
    ["💕 Girlfriend", "💙 Boyfriend"],
    ["🎓 Mentor", "🗂 Assistant"],
]

PERSONA_BUTTON_MAP = {
    "💕 girlfriend": "girlfriend",
    "💙 boyfriend":  "boyfriend",
    "🎓 mentor":     "mentor",
    "🗂 assistant":  "assistant",
}

# Plain-word and button forms accepted by /mode and /switch arguments.
VALID_MODES = {
    "girlfriend": "girlfriend",
    "gf":         "girlfriend",
    "boyfriend":  "boyfriend",
    "bf":         "boyfriend",
    "mentor":     "mentor",
    "assistant":  "assistant",
    **PERSONA_BUTTON_MAP,
}

PERSONA_INTROS = {
    "girlfriend": f"💕 Hey babe! It's {BOT_NAMES['girlfriend']}. What's on your mind?",
    "boyfriend":  f"💙 Hey you! It's {BOT_NAMES['boyfriend']}. How's your day going?",
    "mentor":     "🎓 Good — let's get focused. What are you working on?",
    "assistant":  "🗂 Ready. What do you need?",
}


def keyboard(rows) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True)


def parse_gender(text: str, awaiting: bool) -> str | None:
    """
    Return 'male'/'female' if `text` is a gender answer, else None.

    Plain words are only accepted while `awaiting` is True — see GENDER_WORDS.
    """
    lowered = text.strip().lower()
    if lowered in GENDER_BUTTONS:
        return GENDER_BUTTONS[lowered]
    if awaiting:
        return GENDER_WORDS.get(lowered)
    return None


async def ask_gender(message: Message, first_name: str | None, changing: bool = False) -> None:
    """Send the gender question with Male/Female buttons."""
    if changing:
        text = "Sure! Which one describes you? 👇"
    else:
        text = (
            f"Hey {first_name or 'there'}! 👋 Before we start, one quick question "
            "so I can be the right partner for you.\n\n"
            "Are you male or female?"
        )
    await message.reply_text(text, reply_markup=keyboard(GENDER_KEYBOARD))


async def apply_gender(message: Message, user_id: int, gender: str) -> None:
    """
    Store the user's gender and start them on the matching partner persona.
    """
    persona = PARTNER_FOR_GENDER[gender]
    await create_or_update_user(user_id=user_id, gender=gender, active_persona=persona)
    logger.info(f"User {user_id} set gender={gender}, persona={persona}")

    partner = "girlfriend" if persona == "girlfriend" else "boyfriend"
    await message.reply_text(
        f"{PERSONA_INTROS[persona]}\n\n"
        f"(I'll be your {partner}. Want the other one instead? Send /switch any time.)",
        reply_markup=ReplyKeyboardRemove(),
    )


async def apply_persona(message: Message, user_id: int, persona: str) -> None:
    """Switch a user's active persona and greet them in character."""
    await create_or_update_user(user_id=user_id, active_persona=persona)
    logger.info(f"User {user_id} switched persona to {persona}")
    await message.reply_text(PERSONA_INTROS[persona], reply_markup=ReplyKeyboardRemove())
