"""
scheduler/reminder_store.py — SQLite CRUD for reminders.

Provides:
  save_reminder        — insert a new reminder
  get_pending_reminders — list pending reminders for a user
  cancel_reminder       — mark a reminder as cancelled
  mark_reminder_sent    — mark a reminder as sent
"""
import uuid
import logging
import asyncio
from datetime import datetime, timezone
from memory.models import get_conn

logger = logging.getLogger(__name__)


# ── Save ──────────────────────────────────────────────────────────────────────

async def save_reminder(
    user_id: int,
    content: str,
    remind_at: datetime,
    job_id: str = None,
) -> str:
    """
    Insert a new reminder into the DB.

    Args:
        user_id   : Telegram user ID
        content   : What to remind
        remind_at : When to remind (datetime, UTC)
        job_id    : APScheduler job ID (optional, can be updated later)

    Returns:
        The reminder UUID (str)
    """
    reminder_id = str(uuid.uuid4())
    remind_at_str = remind_at.isoformat()

    def _save():
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO reminders (id, user_id, content, remind_at, status, job_id)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (reminder_id, user_id, content, remind_at_str, job_id),
        )
        conn.commit()
        logger.info(f"Reminder saved: {reminder_id} for user {user_id} at {remind_at_str}")

    await asyncio.get_event_loop().run_in_executor(None, _save)
    return reminder_id


# ── Read ──────────────────────────────────────────────────────────────────────

async def get_pending_reminders(user_id: int) -> list[dict]:
    """
    Return all pending (not sent/cancelled) reminders for a user,
    ordered by remind_at ascending (soonest first).
    """
    def _get():
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT id, content, remind_at, status, created_at
            FROM reminders
            WHERE user_id = ? AND status = 'pending'
            ORDER BY remind_at ASC
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    return await asyncio.get_event_loop().run_in_executor(None, _get)


# ── Cancel ────────────────────────────────────────────────────────────────────

async def cancel_reminder(user_id: int, reminder_id: str) -> bool:
    """
    Mark a reminder as cancelled.

    Returns:
        True if a pending reminder with that ID was found and cancelled,
        False if not found or already sent/cancelled.
    """
    def _cancel():
        conn = get_conn()
        cur = conn.execute(
            """
            UPDATE reminders
            SET status = 'cancelled'
            WHERE id = ? AND user_id = ? AND status = 'pending'
            """,
            (reminder_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0

    return await asyncio.get_event_loop().run_in_executor(None, _cancel)


# ── Mark sent ─────────────────────────────────────────────────────────────────

async def mark_reminder_sent(reminder_id: str) -> None:
    """Update reminder status to 'sent' after it has been delivered."""
    def _mark():
        conn = get_conn()
        conn.execute(
            "UPDATE reminders SET status = 'sent' WHERE id = ?",
            (reminder_id,),
        )
        conn.commit()

    await asyncio.get_event_loop().run_in_executor(None, _mark)
