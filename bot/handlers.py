"""
bot/handlers.py — Core message handler for the Telegram bot.

Flow for every incoming text message:
  1. Load or auto-create the user profile
  2. Handle persona-picker keyboard responses
  3. Build conversation context from SQLite
  4. Call Gemini for the reply AND analyse the message for commitments/facts,
     both at the same time so the analysis costs no extra latency
  5. Schedule any commitments and tell the user about them in the same reply
  6. Persist both turns and any newly learned facts
"""
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes

from agent.extractor import analyze_message
from agent.llm import call_llm
from agent.persona import build_system_prompt
from memory.context import get_context, save_message, trim_to_budget
from memory.user_profile import add_user_fact, create_or_update_user, get_user
from scheduler.jobs import schedule_commitment
from scheduler.reminder_store import has_similar_pending

logger = logging.getLogger(__name__)

# Keyboard button text -> persona slug
PERSONA_BUTTON_MAP = {
    "💕 girlfriend": "girlfriend",
    "🎓 mentor":     "mentor",
    "🗂 assistant":  "assistant",
}

PERSONA_INTROS = {
    "girlfriend": "💕 Hey babe! What's on your mind?",
    "mentor":     "🎓 Good — let's get focused. What are you working on?",
    "assistant":  "🗂 Ready. What do you need?",
}


def _resolve_tz(profile: dict) -> ZoneInfo:
    try:
        return ZoneInfo(profile.get("timezone") or "Asia/Kolkata")
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _describe_when(event_at: datetime, tz: ZoneInfo, now: datetime) -> str:
    """Render a commitment time the way a person would say it."""
    local = event_at.astimezone(tz)
    today = now.astimezone(tz).date()
    delta_days = (local.date() - today).days

    clock = local.strftime("%I:%M %p").lstrip("0")
    if delta_days == 0:
        return f"today at {clock}"
    if delta_days == 1:
        return f"tomorrow at {clock}"
    if 2 <= delta_days <= 6:
        return f"{local.strftime('%A')} at {clock}"
    return f"{local.strftime('%a %d %b')} at {clock}"


async def _handle_commitments(
    commitments: list[dict],
    user_id: int,
    profile: dict,
    bot,
) -> list[str]:
    """
    Schedule each extracted commitment, skipping ones we already track.
    Returns human-readable confirmation lines for the reply.
    """
    tz = _resolve_tz(profile)
    now = datetime.now(ZoneInfo("UTC"))
    lines = []

    for item in commitments:
        try:
            if await has_similar_pending(user_id, item["content"], item["event_at"]):
                logger.info(f"Skipping duplicate commitment: {item['content']}")
                continue

            scheduled = await schedule_commitment(
                user_id  = user_id,
                content  = item["content"],
                kind     = item["kind"],
                event_at = item["event_at"],
                all_day  = item["all_day"],
                tz       = tz,
                bot      = bot,
            )

            when = _describe_when(scheduled["event_at"], tz, now)
            extra = ""
            if any(label == "tonight" for _, label in scheduled["pings"]):
                extra = " — I'll nudge you tonight too"
            lines.append(f"📌 {scheduled['content']} — {when}{extra}")

        except Exception as exc:
            logger.warning(f"Could not schedule commitment for {user_id}: {exc}")

    return lines


async def _store_facts(user_id: int, facts: list[str]) -> None:
    """Persist newly learned long-term facts about the user."""
    for fact in facts:
        try:
            await add_user_fact(user_id, fact, source="inferred")
        except Exception as exc:
            logger.warning(f"Could not store fact for {user_id}: {exc}")


# ── Main message handler ───────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle every incoming text message that is not a command."""
    user      = update.effective_user
    tg_id     = user.id
    user_text = update.message.text.strip()

    # ── 1. Load / create user profile ────────────────────────────────────────
    profile = await get_user(tg_id)
    if not profile:
        await create_or_update_user(
            user_id    = tg_id,
            username   = user.username or "",
            first_name = user.first_name or "friend",
        )
        profile = await get_user(tg_id)

    # ── 2. Handle persona-picker keyboard replies ─────────────────────────────
    if user_text.lower() in PERSONA_BUTTON_MAP:
        persona = PERSONA_BUTTON_MAP[user_text.lower()]
        await create_or_update_user(user_id=tg_id, active_persona=persona)
        await update.message.reply_text(
            PERSONA_INTROS[persona], reply_markup=ReplyKeyboardRemove()
        )
        return

    # ── 3. Build conversation context ─────────────────────────────────────────
    active_persona = profile.get("active_persona", "girlfriend")
    history        = trim_to_budget(await get_context(tg_id), max_tokens=3000)
    system_prompt  = await build_system_prompt(profile, active_persona)

    await context.bot.send_chat_action(chat_id=tg_id, action="typing")

    # ── 4. Reply and analysis run together, so analysis is effectively free ───
    analysis_task = asyncio.create_task(
        analyze_message(user_text, profile, history)
    )

    try:
        reply = await call_llm(
            system_prompt = system_prompt,
            history       = history,
            user_message  = user_text,
        )
    except Exception as exc:
        logger.error(f"LLM call failed for user {tg_id}: {exc}")
        analysis_task.cancel()
        await update.message.reply_text(
            "Sorry, I'm having a brain moment 🥴 Try again in a sec."
        )
        return

    # Analysis must never block or break the conversation.
    try:
        analysis = await analysis_task
    except Exception as exc:
        logger.warning(f"Analysis failed for user {tg_id}: {exc}")
        analysis = {"commitments": [], "facts": []}

    # ── 5. Schedule commitments and confirm them in the same message ─────────
    confirmations = await _handle_commitments(
        analysis["commitments"], tg_id, profile, context.bot
    )

    message = reply
    if confirmations:
        message = f"{reply}\n\n" + "\n".join(confirmations)

    # No parse_mode: the reply is LLM-generated and a stray '*' or '_' would
    # make Telegram reject the whole message.
    await update.message.reply_text(message)

    # ── 6. Persist the turn and anything new we learned ──────────────────────
    await save_message(tg_id, role="user",      content=user_text, persona=active_persona)
    await save_message(tg_id, role="assistant", content=reply,     persona=active_persona)
    await _store_facts(tg_id, analysis["facts"])


# ── Global error handler ───────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log all unhandled exceptions from the Telegram dispatcher."""
    logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Something went wrong on my end. Please try again!"
            )
        except Exception:
            # The failure may well have been the send itself — don't loop.
            logger.debug("Could not deliver the error notice to the user.")
