"""
bot/handlers.py — Core message handler for the Telegram bot.

Flow for every incoming text message:
  1. Load or auto-create user profile
  2. Handle persona-picker keyboard responses
  3. Build conversation context from SQLite
  4. Call Gemini with persona system prompt
  5. Reply to user + persist both messages
  6. Run reminder extractor (non-blocking background step)
"""
import logging
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes

from memory.user_profile import get_user, create_or_update_user
from memory.context import get_context, save_message, trim_to_budget
from agent.persona import build_system_prompt
from agent.llm import call_llm
from agent.extractor import extract_reminder
from scheduler.jobs import schedule_reminder

logger = logging.getLogger(__name__)

# Keyboard button text → persona slug
PERSONA_BUTTON_MAP = {
    "💕 girlfriend": "girlfriend",
    "🎓 mentor":     "mentor",
    "🗂 assistant":  "assistant",
}


# ── Main message handler ───────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle every incoming text message that is not a command."""
    user     = update.effective_user
    tg_id    = user.id
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
    normalized = user_text.lower()
    if normalized in PERSONA_BUTTON_MAP:
        persona = PERSONA_BUTTON_MAP[normalized]
        await create_or_update_user(user_id=tg_id, active_persona=persona)

        intros = {
            "girlfriend": "💕 Hey babe! What's on your mind?",
            "mentor":     "🎓 Good — let's get focused. What are you working on?",
            "assistant":  "🗂 Ready. What do you need?",
        }
        await update.message.reply_text(
            intros[persona],
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # ── 3. Build conversation context ─────────────────────────────────────────
    active_persona = profile.get("active_persona", "girlfriend")
    history        = await get_context(tg_id)
    history        = trim_to_budget(history, max_tokens=3000)
    system_prompt  = await build_system_prompt(profile, active_persona)

    # ── 4. Show typing indicator, then call LLM ───────────────────────────────
    await context.bot.send_chat_action(chat_id=tg_id, action="typing")

    try:
        reply = await call_llm(
            system_prompt=system_prompt,
            history=history,
            user_message=user_text,
        )
    except Exception as exc:
        logger.error(f"LLM call failed for user {tg_id}: {exc}")
        await update.message.reply_text(
            "Sorry, I'm having a brain moment 🥴 Try again in a sec."
        )
        return

    # ── 5. Send reply ─────────────────────────────────────────────────────────
    await update.message.reply_text(reply)

    # ── 6. Persist both turns to SQLite ──────────────────────────────────────
    await save_message(tg_id, role="user",      content=user_text, persona=active_persona)
    await save_message(tg_id, role="assistant", content=reply,     persona=active_persona)

    # ── 7. Reminder extraction (non-blocking; failure is safe to ignore) ──────
    try:
        reminder = await extract_reminder(user_text, profile)
        if reminder:
            job_id = await schedule_reminder(
                user_id   = tg_id,
                content   = reminder["content"],
                remind_at = reminder["remind_at"],
                bot       = context.bot,
            )
            logger.info(
                f"Reminder scheduled for user {tg_id}: "
                f"'{reminder['content']}' @ {reminder['remind_at']} (job: {job_id})"
            )
    except Exception as exc:
        logger.warning(f"Reminder extraction/scheduling failed for user {tg_id}: {exc}")


# ── Global error handler ───────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log all unhandled exceptions from the Telegram dispatcher."""
    logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)

    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "Something went wrong on my end. Please try again!"
        )