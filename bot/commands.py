"""
bot/commands.py — All slash command handlers.

Commands:
  /start    — Onboard new user, show persona picker
  /mode     — Switch persona
  /name     — Set preferred name
  /reminders — List pending reminders
  /cancel   — Cancel a reminder by ID
  /forget   — Wipe conversation history
  /help     — Show all commands
"""
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes

from memory.user_profile import get_user, create_or_update_user
from memory.context import clear_history
from scheduler.reminder_store import get_pending_reminders, cancel_reminder


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Onboard a new user: save profile and present the persona picker."""
    user  = update.effective_user
    tg_id = user.id

    await create_or_update_user(
        user_id    = tg_id,
        username   = user.username or "",
        first_name = user.first_name or "friend",
    )

    keyboard     = [["💕 Girlfriend", "🎓 Mentor", "🗂 Assistant"]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, one_time_keyboard=True, resize_keyboard=True
    )

    await update.message.reply_text(
        f"Hey {user.first_name}! 👋 I'm your AI companion.\n\n"
        "I can be your *girlfriend*, *mentor*, or *personal assistant* — "
        "just pick a mode and start chatting.\n\n"
        "Which persona would you like to start with?",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


# ── /mode ─────────────────────────────────────────────────────────────────────

async def mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch active persona. Usage: /mode girlfriend | mentor | assistant"""
    tg_id = update.effective_user.id
    args  = context.args

    valid_modes = {
        "girlfriend":    "girlfriend",
        "mentor":        "mentor",
        "assistant":     "assistant",
        "💕 girlfriend": "girlfriend",
        "🎓 mentor":     "mentor",
        "🗂 assistant":  "assistant",
    }

    if not args:
        keyboard     = [["💕 Girlfriend", "🎓 Mentor", "🗂 Assistant"]]
        reply_markup = ReplyKeyboardMarkup(
            keyboard, one_time_keyboard=True, resize_keyboard=True
        )
        await update.message.reply_text(
            "Which persona do you want?",
            reply_markup=reply_markup,
        )
        return

    chosen  = " ".join(args).lower().strip()
    persona = valid_modes.get(chosen)

    if not persona:
        await update.message.reply_text(
            "❌ Unknown mode. Use: /mode girlfriend | mentor | assistant"
        )
        return

    await create_or_update_user(user_id=tg_id, active_persona=persona)

    intros = {
        "girlfriend": "💕 Switched to *Girlfriend* mode. Hey babe, what's on your mind?",
        "mentor":     "🎓 Switched to *Mentor* mode. Let's get to work — what are you working on?",
        "assistant":  "🗂 Switched to *Assistant* mode. I'm ready. What do you need done?",
    }

    await update.message.reply_text(
        intros[persona],
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )


# ── /name ─────────────────────────────────────────────────────────────────────

async def name_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set a preferred name. Usage: /name <your name>"""
    tg_id = update.effective_user.id
    args  = context.args

    if not args:
        await update.message.reply_text(
            "Usage: /name <your name>\nExample: /name Arjun"
        )
        return

    new_name = " ".join(args).strip()
    await create_or_update_user(user_id=tg_id, first_name=new_name)
    await update.message.reply_text(
        f"Got it! I'll call you *{new_name}* from now on 😊",
        parse_mode="Markdown",
    )


# ── /reminders ────────────────────────────────────────────────────────────────

async def reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all pending reminders for the user."""
    tg_id   = update.effective_user.id
    pending = await get_pending_reminders(tg_id)

    if not pending:
        await update.message.reply_text("✅ You have no pending reminders.")
        return

    lines = ["📋 *Your pending reminders:*\n"]
    for r in pending:
        short_id = r["id"][:8]
        lines.append(f"• `[{short_id}]` {r['content']}\n  ⏰ {r['remind_at']}")

    lines.append("\nTo cancel: /cancel <reminder-id>")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /cancel ───────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel a reminder by ID. Usage: /cancel <reminder-id>"""
    tg_id = update.effective_user.id
    args  = context.args

    if not args:
        await update.message.reply_text(
            "Usage: /cancel <reminder-id>\nGet IDs from /reminders"
        )
        return

    # Accept both short (8-char) and full UUID
    partial_id  = args[0].strip()
    pending     = await get_pending_reminders(tg_id)
    matched_id  = next(
        (r["id"] for r in pending if r["id"].startswith(partial_id)),
        None,
    )

    if not matched_id:
        await update.message.reply_text(
            f"❌ Couldn't find reminder `{partial_id}`. Check /reminders for valid IDs.",
            parse_mode="Markdown",
        )
        return

    success = await cancel_reminder(tg_id, matched_id)
    if success:
        await update.message.reply_text(
            f"🗑 Reminder `{partial_id}` cancelled.", parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"❌ Could not cancel `{partial_id}`. It may already be sent or cancelled.",
            parse_mode="Markdown",
        )


# ── /forget ───────────────────────────────────────────────────────────────────

async def forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear conversation history for this user."""
    tg_id = update.effective_user.id
    count = await clear_history(tg_id)
    await update.message.reply_text(
        f"🧹 Cleared {count} messages. Fresh start!",
        reply_markup=ReplyKeyboardRemove(),
    )


# ── /help ─────────────────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the full command reference."""
    text = (
        "🤖 *AI Companion Bot — Commands*\n\n"
        "/start — Onboarding & persona selection\n"
        "/mode `[girlfriend|mentor|assistant]` — Switch persona\n"
        "/name `<your name>` — Set your preferred name\n"
        "/reminders — List your pending reminders\n"
        "/cancel `<id>` — Cancel a reminder\n"
        "/forget — Clear conversation history\n"
        "/help — Show this message\n\n"
        "_Just chat normally — reminders, memory, and persona all work automatically._"
    )
    await update.message.reply_text(text, parse_mode="Markdown")