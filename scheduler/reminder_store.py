"""
scheduler/reminder_store.py — SQLite CRUD for reminders.

The reminders table is the source of truth, not APScheduler. Every pending
row is re-scheduled from here at startup, so a restart never loses a nudge.

One real-world commitment ("present at the 11am meeting") produces several
rows sharing a group_id — the night-before nudge, the hour-before nudge, and
the one at the time itself. Listing and cancelling both work on the group.
"""
import asyncio
import logging
import uuid
from datetime import datetime

from memory.models import get_conn

logger = logging.getLogger(__name__)


def _run(fn):
    """Run a blocking DB function in the default executor."""
    return asyncio.get_running_loop().run_in_executor(None, fn)


async def save_reminder_group(
    user_id: int,
    content: str,
    kind: str,
    event_at: datetime,
    pings: list[tuple[datetime, str]],
) -> tuple[str, list[dict]]:
    """
    Persist one commitment as a group of pending ping rows.

    Args:
        user_id  : Telegram user ID
        content  : What the commitment is
        kind     : 'event' or 'task'
        event_at : When the commitment actually happens (UTC)
        pings    : [(remind_at, lead_label), ...] — when to nudge

    Returns:
        (group_id, [{id, remind_at, lead_label}, ...])
    """
    group_id = str(uuid.uuid4())
    rows = [
        {
            "id":         str(uuid.uuid4()),
            "remind_at":  remind_at,
            "lead_label": label,
        }
        for remind_at, label in pings
    ]

    def _save():
        conn = get_conn()
        conn.executemany(
            """
            INSERT INTO reminders
                (id, group_id, user_id, content, kind, event_at,
                 remind_at, lead_label, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            [
                (
                    r["id"], group_id, user_id, content, kind,
                    event_at.isoformat(), r["remind_at"].isoformat(),
                    r["lead_label"],
                )
                for r in rows
            ],
        )
        conn.commit()

    await _run(_save)
    logger.info(
        f"Saved commitment '{content}' for user {user_id} "
        f"@ {event_at.isoformat()} ({len(rows)} nudges, group {group_id[:8]})"
    )
    return group_id, rows


async def set_job_id(reminder_id: str, job_id: str) -> None:
    """Record which APScheduler job owns this row, so /cancel can remove it."""
    def _set():
        conn = get_conn()
        conn.execute(
            "UPDATE reminders SET job_id = ? WHERE id = ?", (job_id, reminder_id)
        )
        conn.commit()

    await _run(_set)


async def get_reminder(reminder_id: str) -> dict | None:
    """Fetch a single reminder row, or None."""
    def _get():
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
        ).fetchone()
        return dict(row) if row else None

    return await _run(_get)


async def get_all_pending() -> list[dict]:
    """
    Every pending reminder across all users, soonest first.
    Used at startup to rebuild the scheduler from the database.
    """
    def _get():
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status = 'pending' ORDER BY remind_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    return await _run(_get)


async def get_pending_groups(user_id: int) -> list[dict]:
    """
    Pending commitments for a user, one entry per group (not per ping),
    ordered by when the commitment happens.
    """
    def _get():
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT   group_id,
                     content,
                     kind,
                     event_at,
                     COUNT(*)      AS nudges,
                     MIN(remind_at) AS next_ping
            FROM     reminders
            WHERE    user_id = ? AND status = 'pending'
            GROUP BY group_id
            ORDER BY event_at ASC
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    return await _run(_get)


async def cancel_group(user_id: int, group_id: str) -> list[str]:
    """
    Cancel every pending ping in a group.

    Returns the APScheduler job IDs that were cancelled, so the caller can
    remove the live jobs too. An empty list means nothing was cancelled.
    """
    def _cancel():
        conn = get_conn()
        job_ids = [
            r["job_id"]
            for r in conn.execute(
                "SELECT job_id FROM reminders "
                "WHERE group_id = ? AND user_id = ? AND status = 'pending'",
                (group_id, user_id),
            ).fetchall()
            if r["job_id"]
        ]
        cur = conn.execute(
            """
            UPDATE reminders SET status = 'cancelled'
            WHERE group_id = ? AND user_id = ? AND status = 'pending'
            """,
            (group_id, user_id),
        )
        conn.commit()
        return job_ids if cur.rowcount else []

    return await _run(_cancel)


async def mark_status(reminder_id: str, status: str) -> None:
    """Set a reminder row to 'sent', 'cancelled' or 'missed'."""
    def _mark():
        conn = get_conn()
        conn.execute(
            "UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id)
        )
        conn.commit()

    await _run(_mark)


async def has_similar_pending(
    user_id: int,
    content: str,
    event_at: datetime,
    window_minutes: int = 90,
) -> bool:
    """
    True if this user already has a pending commitment that looks like the
    same thing at roughly the same time.

    Mentioning a meeting three times in one conversation should not produce
    three sets of nudges. We compare on time proximity plus overlapping
    content words, which is loose enough to catch rewordings by the model.
    """
    def _check():
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT DISTINCT content, event_at
            FROM   reminders
            WHERE  user_id = ? AND status = 'pending' AND event_at IS NOT NULL
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    existing = await _run(_check)
    if not existing:
        return False

    new_words = {w for w in content.lower().split() if len(w) > 3}
    window = window_minutes * 60

    for row in existing:
        try:
            other_at = datetime.fromisoformat(row["event_at"])
        except (ValueError, TypeError):
            continue
        if other_at.tzinfo is None or event_at.tzinfo is None:
            continue
        if abs((other_at - event_at).total_seconds()) > window:
            continue

        other_words = {w for w in row["content"].lower().split() if len(w) > 3}
        if not new_words or not other_words:
            return True   # nothing to compare on, but the times match
        overlap = len(new_words & other_words) / min(len(new_words), len(other_words))
        if overlap >= 0.5:
            return True

    return False
