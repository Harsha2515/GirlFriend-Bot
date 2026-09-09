"""
scheduler/jobs.py — Turning commitments into timed Telegram nudges.

APScheduler holds jobs in memory only. The reminders table in SQLite is the
real source of truth, and restore_pending() rebuilds every live job from it
at startup — so restarting the bot (or a free-tier host cycling the process)
never silently drops a reminder.

A single commitment becomes several nudges: the night before, an hour before,
and one at the time itself. They share a group_id so /cancel kills all of them
together.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

from scheduler.reminder_store import (
    get_all_pending,
    get_reminder,
    mark_status,
    save_reminder_group,
    set_job_id,
)

logger = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")

# A nudge whose time passed while the bot was down still gets delivered if it
# is this recent. Anything older is marked 'missed' rather than sent stale.
LATE_GRACE = timedelta(hours=2)

# APScheduler tolerance for a job that fires slightly behind schedule.
MISFIRE_GRACE = 300  # seconds

# Hour (in the user's local time) for the night-before heads-up.
NIGHT_BEFORE_HOUR = 21

_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """Return the global scheduler. init_scheduler() must have run first."""
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialised. Call init_scheduler() first.")
    return _scheduler


def init_scheduler() -> AsyncIOScheduler:
    """Create and start the global AsyncIOScheduler. Call once, from main.py."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
        _scheduler.start()
        logger.info("APScheduler started.")
    return _scheduler


# ── Planning when to nudge ────────────────────────────────────────────────────

def plan_pings(
    event_at: datetime,
    kind: str,
    all_day: bool,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> list[tuple[datetime, str]]:
    """
    Work out when to nudge for a commitment happening at `event_at` (UTC).

    Returns [(remind_at_utc, lead_label), ...] sorted soonest first, with any
    time already in the past dropped. The label is what the nudge says about
    its own timing ("tonight", "in an hour", "") — an empty label means the
    nudge lands at the commitment itself.
    """
    now = now or datetime.now(UTC)
    cutoff = now + timedelta(minutes=2)   # no point scheduling for right now
    pings: list[tuple[datetime, str]] = []

    event_local = event_at.astimezone(tz)

    # Night before — only when the commitment is on a later local day, so a
    # "this afternoon" plan doesn't get a pointless 9pm reminder.
    if event_local.date() > now.astimezone(tz).date():
        night_before_local = (event_local - timedelta(days=1)).replace(
            hour=NIGHT_BEFORE_HOUR, minute=0, second=0, microsecond=0
        )
        pings.append((night_before_local.astimezone(UTC), "tonight"))

    if all_day:
        # We only know the day, so the 9am slot is the single real nudge.
        pings.append((event_at, ""))
    else:
        pings.append((event_at - timedelta(hours=1), "in an hour"))
        # Events need warning before they start; task deadlines land on time.
        final = event_at - timedelta(minutes=10) if kind == "event" else event_at
        pings.append((final, ""))

    # Drop past/too-soon entries and collapse near-duplicates.
    seen: set[int] = set()
    result = []
    for when, label in sorted(pings, key=lambda p: p[0]):
        if when < cutoff:
            continue
        bucket = int(when.timestamp()) // 60      # at most one nudge per minute
        if bucket in seen:
            continue
        seen.add(bucket)
        result.append((when, label))

    # If everything was filtered out the commitment is imminent — nudge now.
    if not result:
        result = [(cutoff, "")]

    return result


def format_nudge(
    content: str,
    kind: str,
    lead_label: str,
    event_at: datetime,
    tz: ZoneInfo,
    late: bool = False,
) -> str:
    """Build the reminder text. Plain text only — see _fire_reminder."""
    when_local = event_at.astimezone(tz).strftime("%I:%M %p").lstrip("0")

    if late:
        return f"Sorry, this is late — {content} (was set for {when_local})"
    if lead_label == "tonight":
        return f"Heads up for tomorrow: {content} at {when_local}"
    if lead_label == "in an hour":
        return f"In about an hour: {content} ({when_local})"
    if kind == "event":
        return f"Starting soon: {content} at {when_local}"
    return f"Reminder: {content}"


# ── Scheduling ────────────────────────────────────────────────────────────────

async def schedule_commitment(
    user_id: int,
    content: str,
    kind: str,
    event_at: datetime,
    all_day: bool,
    tz: ZoneInfo,
    bot: Bot,
) -> dict:
    """
    Persist a commitment and schedule all of its nudges.

    Returns {group_id, content, event_at, pings} so the caller can confirm
    back to the user inside the chat reply.
    """
    pings = plan_pings(event_at, kind, all_day, tz)

    group_id, rows = await save_reminder_group(
        user_id  = user_id,
        content  = content,
        kind     = kind,
        event_at = event_at,
        pings    = pings,
    )

    scheduler = get_scheduler()
    for row in rows:
        job_id = f"rem_{row['id']}"
        scheduler.add_job(
            _fire_reminder,
            trigger="date",
            run_date=row["remind_at"],
            args=[row["id"], bot],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=MISFIRE_GRACE,
        )
        await set_job_id(row["id"], job_id)

    logger.info(f"Scheduled '{content}' for user {user_id} with {len(rows)} nudges.")
    return {
        "group_id": group_id,
        "content":  content,
        "event_at": event_at,
        "pings":    pings,
    }


async def cancel_jobs(job_ids: list[str]) -> None:
    """Remove live APScheduler jobs. A missing job is fine — already fired."""
    scheduler = get_scheduler()
    for job_id in job_ids:
        try:
            scheduler.remove_job(job_id)
        except Exception:
            logger.debug(f"Job '{job_id}' was not live; nothing to remove.")


# ── Firing ────────────────────────────────────────────────────────────────────

async def _fire_reminder(reminder_id: str, bot: Bot, late: bool = False) -> None:
    """
    Deliver one nudge. Called by APScheduler at the scheduled time.

    Re-reads the row first: a reminder cancelled after its job was created
    must not be delivered. Sends as plain text with no parse_mode, because
    the content comes from an LLM and a stray '*' or '_' would make Telegram
    reject the entire message.
    """
    row = await get_reminder(reminder_id)
    if not row:
        logger.warning(f"Reminder '{reminder_id}' vanished before firing.")
        return
    if row["status"] != "pending":
        logger.info(f"Reminder '{reminder_id}' is '{row['status']}' — not sending.")
        return

    tz = await _user_tz(row["user_id"])

    try:
        event_at = datetime.fromisoformat(row["event_at"] or row["remind_at"])
        if event_at.tzinfo is None:
            event_at = event_at.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        event_at = datetime.now(UTC)

    text = format_nudge(
        content    = row["content"],
        kind       = row["kind"] or "task",
        lead_label = row["lead_label"] or "",
        event_at   = event_at,
        tz         = tz,
        late       = late,
    )

    try:
        await bot.send_message(chat_id=row["user_id"], text=text)
        await mark_status(reminder_id, "sent")
        logger.info(f"Reminder '{reminder_id}' delivered to user {row['user_id']}.")
    except Exception as exc:
        logger.error(f"Failed to deliver reminder '{reminder_id}': {exc}")


async def _user_tz(user_id: int) -> ZoneInfo:
    """Look up a user's timezone, falling back to the default."""
    from memory.user_profile import get_user

    try:
        profile = await get_user(user_id) or {}
        return ZoneInfo(profile.get("timezone") or "Asia/Kolkata")
    except Exception:
        return ZoneInfo("Asia/Kolkata")


# ── Restart recovery ──────────────────────────────────────────────────────────

async def restore_pending(bot: Bot) -> dict:
    """
    Rebuild the scheduler from the database. Call once at startup.

    Future nudges are re-scheduled. Nudges whose time passed while the bot was
    down are delivered shortly after boot if still recent, or marked 'missed'
    if too stale to be useful.
    """
    scheduler = get_scheduler()
    now = datetime.now(UTC)
    rescheduled = late = missed = 0

    for row in await get_all_pending():
        try:
            remind_at = datetime.fromisoformat(row["remind_at"])
        except (ValueError, TypeError):
            logger.warning(f"Bad remind_at on reminder '{row['id']}' — marking missed.")
            await mark_status(row["id"], "missed")
            missed += 1
            continue

        if remind_at.tzinfo is None:
            remind_at = remind_at.replace(tzinfo=UTC)

        if remind_at > now:
            run_date, is_late = remind_at, False
            rescheduled += 1
        elif now - remind_at <= LATE_GRACE:
            # Missed while we were down, but still recent enough to matter.
            run_date, is_late = now + timedelta(seconds=15), True
            late += 1
        else:
            await mark_status(row["id"], "missed")
            missed += 1
            continue

        job_id = f"rem_{row['id']}"
        scheduler.add_job(
            _fire_reminder,
            trigger="date",
            run_date=run_date,
            args=[row["id"], bot, is_late],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=MISFIRE_GRACE,
        )
        await set_job_id(row["id"], job_id)

    logger.info(
        f"Restored reminders — {rescheduled} upcoming, "
        f"{late} delivering late, {missed} too stale."
    )
    return {"rescheduled": rescheduled, "late": late, "missed": missed}
