"""
bot/commands.py — All slash command handlers.

  /start     — Onboard new user, show persona picker
  /mode      — Switch persona
  /name      — Set preferred name
  /timezone  — Set your timezone (reminders depend on it)
  /reminders — List upcoming commitments
  /cancel    — Cancel a commitment by ID
  /facts     — Show what the bot remembers about you
  /forget    — Wipe conversation history
  /help      — Show all commands

Listings deliberately avoid parse_mode. Reminder content is LLM-generated and
an unbalanced '*' or '_' would make Telegram reject the entire message.
"""
import logging
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo, available_timezones

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes

from config import (
    ADMIN_USER_ID,
    AUTO_APPROVE_LIMIT,
    GLOBAL_DAILY_API_LIMIT,
    USER_DAILY_MESSAGE_LIMIT,
)
from memory.context import clear_history
from memory.usage import usage_summary
from memory.user_profile import (
    clear_user_facts,
    count_users,
    create_or_update_user,
    get_user,
    get_user_facts,
    list_users,
    register_user,
    set_status,
)
from scheduler.jobs import cancel_jobs
from scheduler.reminder_store import cancel_group, get_pending_groups

logger = logging.getLogger(__name__)

PERSONA_KEYBOARD = [["💕 Girlfriend", "🎓 Mentor", "🗂 Assistant"]]

VALID_MODES = {
    "girlfriend":    "girlfriend",
    "mentor":        "mentor",
    "assistant":     "assistant",
    "💕 girlfriend": "girlfriend",
    "🎓 mentor":     "mentor",
    "🗂 assistant":  "assistant",
}


async def _user_tz(user_id: int) -> ZoneInfo:
    profile = await get_user(user_id) or {}
    try:
        return ZoneInfo(profile.get("timezone") or "Asia/Kolkata")
    except Exception:
        return ZoneInfo("Asia/Kolkata")


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Onboard a new user: save profile and present the persona picker."""
    user = update.effective_user

    # Same gate as handle_message -- /start must not be a way around approval.
    status = await register_user(
        user_id            = user.id,
        username           = user.username or "",
        first_name         = user.first_name or "friend",
        auto_approve_limit = AUTO_APPROVE_LIMIT,
        is_admin           = user.id == ADMIN_USER_ID,
    )
    if status == "blocked":
        await update.message.reply_text("Sorry, you don't have access to this bot.")
        return
    if status == "pending":
        await update.message.reply_text(
            "Hi! 👋 This bot is currently invite-only.\n\n"
            "Your request has been sent to the admin — you'll get a message "
            "here once you're approved."
        )
        return

    reply_markup = ReplyKeyboardMarkup(
        PERSONA_KEYBOARD, one_time_keyboard=True, resize_keyboard=True
    )

    await update.message.reply_text(
        f"Hey {user.first_name}! 👋 I'm your AI companion.\n\n"
        "I can be your girlfriend, mentor, or personal assistant — pick a mode "
        "and just start chatting.\n\n"
        "Anything you mention having to do gets picked up automatically. Say "
        "\"tomorrow I have a meeting at 11\" and I'll nudge you the night "
        "before and an hour ahead.\n\n"
        "Reminders use Asia/Kolkata time by default — change it any time with "
        "/timezone.\n\n"
        "Which persona would you like to start with?",
        reply_markup=reply_markup,
    )


# ── /mode ─────────────────────────────────────────────────────────────────────

async def mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch active persona. Usage: /mode girlfriend | mentor | assistant"""
    if not context.args:
        reply_markup = ReplyKeyboardMarkup(
            PERSONA_KEYBOARD, one_time_keyboard=True, resize_keyboard=True
        )
        await update.message.reply_text(
            "Which persona do you want?", reply_markup=reply_markup
        )
        return

    persona = VALID_MODES.get(" ".join(context.args).lower().strip())
    if not persona:
        await update.message.reply_text(
            "❌ Unknown mode. Use: /mode girlfriend | mentor | assistant"
        )
        return

    await create_or_update_user(user_id=update.effective_user.id, active_persona=persona)

    intros = {
        "girlfriend": "💕 Switched to Girlfriend mode. Hey babe, what's on your mind?",
        "mentor":     "🎓 Switched to Mentor mode. Let's get to work — what are you on?",
        "assistant":  "🗂 Switched to Assistant mode. I'm ready. What do you need?",
    }
    await update.message.reply_text(
        intros[persona], reply_markup=ReplyKeyboardRemove()
    )


# ── /name ─────────────────────────────────────────────────────────────────────

async def name_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set a preferred name. Usage: /name <your name>"""
    if not context.args:
        await update.message.reply_text("Usage: /name <your name>\nExample: /name Harsha")
        return

    new_name = " ".join(context.args).strip()
    await create_or_update_user(user_id=update.effective_user.id, first_name=new_name)
    await update.message.reply_text(f"Got it! I'll call you {new_name} from now on 😊")


# ── /timezone ─────────────────────────────────────────────────────────────────

async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the timezone all reminders are interpreted in."""
    tg_id = update.effective_user.id

    if not context.args:
        current = await _user_tz(tg_id)
        now = datetime.now(current).strftime("%I:%M %p").lstrip("0")
        await update.message.reply_text(
            f"Your timezone is {current} — it's {now} for you right now.\n\n"
            "To change it: /timezone Asia/Kolkata\n"
            "(Use a TZ database name, e.g. Europe/London, America/New_York.)"
        )
        return

    candidate = context.args[0].strip()
    if candidate not in available_timezones():
        await update.message.reply_text(
            f"❌ '{candidate}' isn't a timezone I recognise.\n"
            "Use a TZ database name like Asia/Kolkata or America/New_York."
        )
        return

    await create_or_update_user(user_id=tg_id, timezone=candidate)
    now = datetime.now(ZoneInfo(candidate)).strftime("%I:%M %p").lstrip("0")
    await update.message.reply_text(
        f"✅ Timezone set to {candidate}. That makes it {now} for you.\n"
        "Reminders already scheduled keep their original times."
    )


# ── /reminders ────────────────────────────────────────────────────────────────

async def reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List upcoming commitments, one line per commitment."""
    tg_id  = update.effective_user.id
    groups = await get_pending_groups(tg_id)

    if not groups:
        await update.message.reply_text(
            "✅ Nothing on your list right now.\n\n"
            "Just mention things naturally — \"I have a dentist appointment "
            "Thursday at 4\" — and I'll pick them up."
        )
        return

    tz = await _user_tz(tg_id)
    lines = ["📋 Your upcoming commitments:", ""]

    for g in groups:
        try:
            when = datetime.fromisoformat(g["event_at"]).astimezone(tz)
            when_str = when.strftime("%a %d %b, %I:%M %p").replace(" 0", " ")
        except (ValueError, TypeError):
            when_str = "time unknown"

        icon = "📅" if g["kind"] == "event" else "✅"
        lines.append(f"{icon} [{g['group_id'][:8]}] {g['content']}")
        lines.append(f"    {when_str} · {g['nudges']} nudge(s) queued")

    lines.append("")
    lines.append("To cancel one: /cancel <id>   (the 8 characters in brackets)")
    await update.message.reply_text("\n".join(lines))


# ── /cancel ───────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel a commitment and all of its nudges. Usage: /cancel <id>"""
    tg_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "Usage: /cancel <id>\nGet IDs from /reminders"
        )
        return

    partial = context.args[0].strip()
    groups  = await get_pending_groups(tg_id)
    match   = next((g for g in groups if g["group_id"].startswith(partial)), None)

    if not match:
        await update.message.reply_text(
            f"❌ Couldn't find '{partial}'. Check /reminders for valid IDs."
        )
        return

    job_ids = await cancel_group(tg_id, match["group_id"])
    if not job_ids and match["nudges"]:
        # Rows were updated but had no live jobs recorded — still a success.
        logger.debug(f"Cancelled group {match['group_id']} with no live jobs.")

    await cancel_jobs(job_ids)
    await update.message.reply_text(f"🗑 Cancelled: {match['content']}")


# ── /facts ────────────────────────────────────────────────────────────────────

async def facts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show — or clear — the long-term facts the bot has learned."""
    tg_id = update.effective_user.id

    if context.args and context.args[0].lower() == "clear":
        count = await clear_user_facts(tg_id)
        await update.message.reply_text(f"🧹 Forgot {count} thing(s) about you.")
        return

    stored = await get_user_facts(tg_id)
    if not stored:
        await update.message.reply_text(
            "I haven't picked up anything long-term about you yet. "
            "Keep chatting — I learn as we go."
        )
        return

    lines = ["🧠 Here's what I remember about you:", ""]
    lines += [f"• {f}" for f in stored]
    lines += ["", "To wipe this: /facts clear"]
    await update.message.reply_text("\n".join(lines))


# ── /forget ───────────────────────────────────────────────────────────────────

async def forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear conversation history for this user."""
    count = await clear_history(update.effective_user.id)
    await update.message.reply_text(
        f"🧹 Cleared {count} messages. Fresh start!\n"
        "(Your saved facts and reminders are untouched — use /facts clear for those.)",
        reply_markup=ReplyKeyboardRemove(),
    )


# ── /help ─────────────────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the full command reference."""
    await update.message.reply_text(
        "🤖 AI Companion Bot — Commands\n\n"
        "/start — Onboarding & persona selection\n"
        "/mode [girlfriend|mentor|assistant] — Switch persona\n"
        "/name <your name> — Set your preferred name\n"
        "/timezone <Area/City> — Set your timezone\n"
        "/reminders — List upcoming commitments\n"
        "/cancel <id> — Cancel a commitment\n"
        "/facts — See what I remember about you (/facts clear to wipe)\n"
        "/forget — Clear conversation history\n"
        "/whoami — Show your Telegram user ID\n"
        "/help — Show this message\n\n"
        "Just chat normally. Anything you mention having to do gets picked up "
        "automatically — I'll nudge you the night before and an hour ahead."
    )


# ── Admin commands ────────────────────────────────────────────────────────────

def admin_only(func):
    """
    Restrict a command to ADMIN_USER_ID.

    Non-admins get the ordinary "unknown command" silence rather than a denial,
    so the existence of these commands isn't advertised to strangers.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not ADMIN_USER_ID or update.effective_user.id != ADMIN_USER_ID:
            logger.info(
                f"Ignoring admin command from non-admin {update.effective_user.id}"
            )
            return
        return await func(update, context)

    return wrapper


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Report the caller's Telegram ID. Needed to set ADMIN_USER_ID in .env."""
    user = update.effective_user
    is_admin = ADMIN_USER_ID and user.id == ADMIN_USER_ID
    await update.message.reply_text(
        f"Your Telegram user ID is: {user.id}\n"
        + ("\nYou are the admin ✅" if is_admin else "")
    )


def _parse_target_id(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    if not context.args:
        return None
    try:
        return int(context.args[0].strip())
    except ValueError:
        return None


async def _change_access(update, context, status: str, verb: str, note: str) -> None:
    """Shared body for /approve, /deny and /block."""
    target = _parse_target_id(context)
    if target is None:
        await update.message.reply_text(
            f"Usage: /{verb} <user-id>\nSee /pending for IDs waiting on you."
        )
        return

    if target == ADMIN_USER_ID and status != "approved":
        await update.message.reply_text("You can't remove your own access.")
        return

    if not await set_status(target, status):
        await update.message.reply_text(
            f"No user with ID {target}. They must message the bot at least once first."
        )
        return

    await update.message.reply_text(f"✅ User {target} is now '{status}'.")

    # Tell them the good news; harmless if they've blocked the bot.
    if note:
        try:
            await context.bot.send_message(chat_id=target, text=note)
        except Exception as exc:
            logger.info(f"Could not notify user {target} of access change: {exc}")


@admin_only
async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Grant a pending user access. Usage: /approve <user-id>"""
    await _change_access(
        update, context, "approved", "approve",
        "🎉 You've been approved! Send /start to pick a persona and begin.",
    )


@admin_only
async def deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a user back to pending. Usage: /deny <user-id>"""
    await _change_access(update, context, "pending", "deny", "")


@admin_only
async def block(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Block a user permanently. Usage: /block <user-id>"""
    await _change_access(update, context, "blocked", "block", "")


@admin_only
async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List users waiting for approval."""
    waiting = await list_users(status="pending")
    if not waiting:
        await update.message.reply_text("✅ Nobody is waiting for approval.")
        return

    lines = [f"⏳ {len(waiting)} waiting for approval:", ""]
    for u in waiting:
        handle = f"@{u['username']}" if u["username"] else "(no username)"
        lines.append(f"• {u['first_name']} {handle}")
        lines.append(f"    /approve {u['user_id']}    /block {u['user_id']}")
    await update.message.reply_text("\n".join(lines))


@admin_only
async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show how many users hold each status, and how close the auto-approve cap is."""
    approved = await count_users("approved")
    waiting  = await count_users("pending")
    blocked  = await count_users("blocked")

    if AUTO_APPROVE_LIMIT > 0:
        remaining = max(0, AUTO_APPROVE_LIMIT - approved)
        gate = (
            f"Auto-approve: {approved}/{AUTO_APPROVE_LIMIT} used"
            + (f", {remaining} slot(s) left" if remaining else " — FULL, new users queue")
        )
    else:
        gate = "Auto-approve: disabled (everyone queues)"

    await update.message.reply_text(
        f"👥 Users\n\n"
        f"Approved: {approved}\n"
        f"Pending:  {waiting}\n"
        f"Blocked:  {blocked}\n\n"
        f"{gate}"
    )


@admin_only
async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent daily API consumption against the free-tier budget."""
    rows = await usage_summary(days=7)
    if not rows:
        await update.message.reply_text("No usage recorded yet.")
        return

    lines = [f"📊 Daily usage (budget: {GLOBAL_DAILY_API_LIMIT} API calls/day)", ""]
    for r in rows:
        calls = r["api_calls"] or 0
        pct = f"{calls * 100 // GLOBAL_DAILY_API_LIMIT}%" if GLOBAL_DAILY_API_LIMIT else "-"
        lines.append(
            f"{r['day']}  {calls:>5} calls ({pct})  "
            f"{r['messages'] or 0} msgs  {r['active_users'] or 0} users"
        )

    lines += ["", f"Per-user cap: {USER_DAILY_MESSAGE_LIMIT} messages/day"]
    await update.message.reply_text("\n".join(lines))
