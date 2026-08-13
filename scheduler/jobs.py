"""
scheduler/jobs.py — APScheduler integration for timed reminders.

The scheduler is initialised once in main.py and shared here via
the module-level _scheduler variable. Reminders are persisted in
SQLite via reminder_store so they can be listed and cancelled.
"""
import logging
import asyncio
from typing import Optional
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

logger = logging.getLogger(__name__)

# ── Global scheduler singleton ─────────────────────────────────────────────────
_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """Return the global scheduler. Must call init_scheduler() first."""
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialised. Call init_scheduler() in main.py first.")
    return _scheduler


def init_scheduler() -> AsyncIOScheduler:
    """Create and start the global AsyncIOScheduler. Call once in main.py."""
    global _scheduler
    if _scheduler is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        _scheduler = AsyncIOScheduler(timezone="UTC", event_loop=loop)
        _scheduler.start()
        logger.info("APScheduler started.")
    return _scheduler


# ── Schedule a reminder ────────────────────────────────────────────────────────

async def schedule_reminder(
    user_id: int,
    content: str,
    remind_at: datetime,
    bot: Bot,
) -> str:
    """
    Persist the reminder in SQLite and schedule an APScheduler one-shot job.

    Args:
        user_id   : Telegram user ID
        content   : What to remind
        remind_at : When to fire (datetime, timezone-aware UTC preferred)
        bot       : Telegram Bot instance

    Returns:
        APScheduler job ID (str)
    """
    from scheduler.reminder_store import save_reminder

    # Save to DB first so the user can list / cancel it
    reminder_id = await save_reminder(
        user_id=user_id,
        content=content,
        remind_at=remind_at,
    )

    job_id = f"reminder_{user_id}_{reminder_id[:8]}"

    scheduler = get_scheduler()
    scheduler.add_job(
        _fire_reminder,
        trigger="date",
        run_date=remind_at,
        args=[user_id, content, reminder_id, bot],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=300,   # fire up to 5 min late if bot was offline
    )

    logger.info(f"Reminder job '{job_id}' scheduled for user {user_id} at {remind_at}")
    return job_id


# ── Reminder fire callback ─────────────────────────────────────────────────────

async def _fire_reminder(
    user_id: int,
    content: str,
    reminder_id: str,
    bot: Bot,
) -> None:
    """Called by APScheduler at the scheduled time."""
    from scheduler.reminder_store import mark_reminder_sent

    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"⏰ *Reminder:* {content}",
            parse_mode="Markdown",
        )
        await mark_reminder_sent(reminder_id)
        logger.info(f"Reminder '{reminder_id}' sent to user {user_id}")
    except Exception as exc:
        logger.error(f"Failed to send reminder '{reminder_id}' to user {user_id}: {exc}")
