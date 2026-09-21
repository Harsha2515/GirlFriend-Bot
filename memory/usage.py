"""
memory/usage.py — Daily usage counters and free-tier guards.

Gemini's free tier is not a trial that expires; it is a set of rate limits
that reset each day. The failure mode is therefore not "the bot dies forever"
but "the bot is broken until the quota resets" -- which looks identical to a
crash from the user's side, and is what we are trying to avoid.

So the bot counts its own consumption and refuses politely *before* Google
starts returning 429. Two ceilings:

  * per user  -- one person cannot drain the day for everybody
  * global    -- the whole bot stays under the key's real daily quota

Counters are keyed by UTC date because that is what the scheduler and the
reminders table already use. Google's own reset is on Pacific time, so a UTC
day is a slightly conservative window -- which is the safe direction to err.
"""
import asyncio
import logging
from datetime import datetime, timezone

from config import GLOBAL_DAILY_API_LIMIT, USER_DAILY_MESSAGE_LIMIT
from memory.models import get_conn

logger = logging.getLogger(__name__)

# Every user message costs two Gemini calls: the reply and the analysis.
API_CALLS_PER_MESSAGE = 2

# Reserved user_id for the whole-bot total.
GLOBAL_ROW = 0


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _run(fn):
    return asyncio.get_running_loop().run_in_executor(None, fn)


async def record_message(user_id: int, api_calls: int = API_CALLS_PER_MESSAGE) -> None:
    """Add one message (and its API calls) to today's per-user and global rows."""
    day = _today()

    def _record():
        conn = get_conn()
        conn.executemany(
            """
            INSERT INTO usage_daily (day, user_id, messages, api_calls)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(day, user_id) DO UPDATE SET
                messages  = messages  + 1,
                api_calls = api_calls + excluded.api_calls
            """,
            [(day, user_id, api_calls), (day, GLOBAL_ROW, api_calls)],
        )
        conn.commit()

    await _run(_record)


async def get_today(user_id: int) -> dict:
    """Return {'user': {...}, 'global': {...}} counts for today."""
    day = _today()

    def _get():
        conn = get_conn()
        rows = {
            r["user_id"]: dict(r)
            for r in conn.execute(
                "SELECT user_id, messages, api_calls FROM usage_daily "
                "WHERE day = ? AND user_id IN (?, ?)",
                (day, user_id, GLOBAL_ROW),
            )
        }
        blank = {"messages": 0, "api_calls": 0}
        return {
            "user":   rows.get(user_id, blank),
            "global": rows.get(GLOBAL_ROW, blank),
        }

    return await _run(_get)


async def check_limits(user_id: int, is_admin: bool = False) -> tuple[bool, str]:
    """
    Decide whether this message may be processed.

    Returns (allowed, reason). `reason` is user-facing text when blocked.

    The admin is exempt from the per-user cap but NOT from the global one --
    the global ceiling exists to protect the API key itself, and exempting
    anyone from it would defeat the purpose.
    """
    counts = await get_today(user_id)

    if GLOBAL_DAILY_API_LIMIT > 0:
        used = counts["global"]["api_calls"]
        if used + API_CALLS_PER_MESSAGE > GLOBAL_DAILY_API_LIMIT:
            logger.warning(
                f"Global daily API budget reached: {used}/{GLOBAL_DAILY_API_LIMIT}"
            )
            return False, (
                "I've used up my daily quota for everyone 💤\n\n"
                "It resets in a few hours — talk to you then!"
            )

    if not is_admin and USER_DAILY_MESSAGE_LIMIT > 0:
        used = counts["user"]["messages"]
        if used >= USER_DAILY_MESSAGE_LIMIT:
            return False, (
                f"You've hit your daily limit of {USER_DAILY_MESSAGE_LIMIT} "
                "messages 💤\n\nIt resets tomorrow — see you then!"
            )

    return True, ""


async def usage_summary(days: int = 7) -> list[dict]:
    """Recent global usage, newest day first. For the /usage admin command."""
    def _get():
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT day,
                   SUM(CASE WHEN user_id = 0 THEN api_calls ELSE 0 END) AS api_calls,
                   SUM(CASE WHEN user_id = 0 THEN messages  ELSE 0 END) AS messages,
                   COUNT(DISTINCT CASE WHEN user_id != 0 THEN user_id END) AS active_users
            FROM   usage_daily
            GROUP  BY day
            ORDER  BY day DESC
            LIMIT  ?
            """,
            (days,),
        ).fetchall()
        return [dict(r) for r in rows]

    return await _run(_get)


async def prune_usage(keep_days: int = 90) -> int:
    """Drop usage rows older than keep_days. Returns rows deleted."""
    def _prune():
        conn = get_conn()
        deleted = 0
        for table in ("usage_daily", "tool_usage_daily"):
            cur = conn.execute(
                f"DELETE FROM {table} WHERE day < date('now', ?)",
                (f"-{keep_days} days",),
            )
            deleted += cur.rowcount
        conn.commit()
        return deleted

    return await _run(_prune)


# ── Tool usage ────────────────────────────────────────────────────────────────

async def global_budget_left() -> int | None:
    """
    Gemini calls still available today across the whole bot, or None when
    there is no global cap. Tools check this before spending extra calls, so
    a tool-heavy turn can't push the key past the ceiling.
    """
    if GLOBAL_DAILY_API_LIMIT <= 0:
        return None
    counts = await get_today(GLOBAL_ROW)
    return max(0, GLOBAL_DAILY_API_LIMIT - counts["global"]["api_calls"])


async def get_tool_count(user_id: int, tool: str) -> int:
    """How many times this user has run `tool` today."""
    day = _today()

    def _get():
        row = get_conn().execute(
            "SELECT count FROM tool_usage_daily WHERE day = ? AND user_id = ? AND tool = ?",
            (day, user_id, tool),
        ).fetchone()
        return row["count"] if row else 0

    return await _run(_get)


async def record_tool_use(user_id: int, tool: str) -> None:
    """Count one successful run of `tool` against today's per-user cap."""
    day = _today()

    def _record():
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO tool_usage_daily (day, user_id, tool, count) VALUES (?, ?, ?, 1)
            ON CONFLICT(day, user_id, tool) DO UPDATE SET count = count + 1
            """,
            (day, user_id, tool),
        )
        conn.commit()

    await _run(_record)


async def record_api_calls(user_id: int, api_calls: int) -> None:
    """
    Count Gemini calls that weren't part of a chat message -- e.g. a direct
    /imagine that used Gemini's image model -- so the global budget stays true.
    """
    if api_calls <= 0:
        return
    day = _today()

    def _record():
        conn = get_conn()
        conn.executemany(
            """
            INSERT INTO usage_daily (day, user_id, messages, api_calls) VALUES (?, ?, 0, ?)
            ON CONFLICT(day, user_id) DO UPDATE SET api_calls = api_calls + excluded.api_calls
            """,
            [(day, user_id, api_calls), (day, GLOBAL_ROW, api_calls)],
        )
        conn.commit()

    await _run(_record)
