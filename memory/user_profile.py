"""
memory/user_profile.py — User profile stored in SQLite.

Provides:
  get_user              — fetch user dict by telegram ID
  create_or_update_user — upsert user record
  add_user_fact         — store a long-term fact
  get_user_facts        — list all facts
  remove_user_fact      — delete a specific fact
"""
import logging
import asyncio
from datetime import datetime, timezone
from memory.models import get_conn

logger = logging.getLogger(__name__)


# ── Read ──────────────────────────────────────────────────────────────────────

async def get_user(user_id: int) -> dict | None:
    """Fetch user profile dict, or None if not registered yet."""
    def _get():
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return None
        profile = dict(row)
        # Attach facts list
        facts_rows = conn.execute(
            "SELECT fact FROM user_facts WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        ).fetchall()
        profile["facts"] = [r["fact"] for r in facts_rows]
        return profile

    return await asyncio.get_event_loop().run_in_executor(None, _get)


# ── Create / Update ───────────────────────────────────────────────────────────

async def create_or_update_user(
    user_id: int,
    username: str = None,
    first_name: str = None,
    active_persona: str = None,
    timezone: str = None,
    gender: str = None,
) -> None:
    """
    INSERT or UPDATE a user record.
    Only non-None fields are written on update.
    Defaults on first creation: persona=girlfriend, tz=Asia/Kolkata.
    """
    def _write():
        conn = get_conn()
        existing = conn.execute(
            "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO users (user_id, username, first_name, active_persona, timezone)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username or "",
                    first_name or "friend",
                    active_persona or "girlfriend",
                    timezone or "Asia/Kolkata",
                ),
            )
            logger.info(f"New user created: {user_id}")
        else:
            # Build a dynamic UPDATE only for fields that were passed
            fields, values = [], []
            if username       is not None: fields.append("username = ?");       values.append(username)
            if first_name     is not None: fields.append("first_name = ?");     values.append(first_name)
            if active_persona is not None: fields.append("active_persona = ?"); values.append(active_persona)
            if timezone       is not None: fields.append("timezone = ?");       values.append(timezone)
            if gender         is not None: fields.append("gender = ?");         values.append(gender)

            if fields:
                values.append(user_id)
                conn.execute(
                    f"UPDATE users SET {', '.join(fields)} WHERE user_id = ?",
                    values,
                )
                logger.debug(f"User {user_id} updated: {fields}")

        conn.commit()

    await asyncio.get_event_loop().run_in_executor(None, _write)


# ── Facts ─────────────────────────────────────────────────────────────────────

async def add_user_fact(user_id: int, fact: str, source: str = "inferred") -> None:
    """Store a long-term fact (e.g. 'works at TCS', 'birthday May 3')."""
    def _add():
        conn = get_conn()
        # Avoid duplicates
        existing = conn.execute(
            "SELECT id FROM user_facts WHERE user_id = ? AND fact = ?",
            (user_id, fact),
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO user_facts (user_id, fact, source) VALUES (?, ?, ?)",
                (user_id, fact, source),
            )
            conn.commit()
            logger.info(f"Fact added for user {user_id} [{source}]: {fact}")

    await asyncio.get_event_loop().run_in_executor(None, _add)


async def get_user_facts(user_id: int) -> list[str]:
    """Return a list of fact strings for the user."""
    def _get():
        conn = get_conn()
        rows = conn.execute(
            "SELECT fact FROM user_facts WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        ).fetchall()
        return [r["fact"] for r in rows]

    return await asyncio.get_event_loop().run_in_executor(None, _get)


async def remove_user_fact(user_id: int, fact: str) -> None:
    """Remove a specific fact from the DB."""
    def _remove():
        conn = get_conn()
        conn.execute(
            "DELETE FROM user_facts WHERE user_id = ? AND fact = ?",
            (user_id, fact),
        )
        conn.commit()

    await asyncio.get_event_loop().run_in_executor(None, _remove)

async def clear_user_facts(user_id: int) -> int:
    """Delete every stored fact for a user (/facts clear). Returns count."""
    def _clear():
        conn = get_conn()
        cur = conn.execute("DELETE FROM user_facts WHERE user_id = ?", (user_id,))
        conn.commit()
        logger.info(f"Cleared {cur.rowcount} facts for user {user_id}")
        return cur.rowcount

    return await asyncio.get_event_loop().run_in_executor(None, _clear)


# ── Access control ────────────────────────────────────────────────────────────

async def count_users(status: str = "approved") -> int:
    """How many users currently hold this status."""
    def _count():
        conn = get_conn()
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE status = ?", (status,)
        ).fetchone()[0]

    return await asyncio.get_event_loop().run_in_executor(None, _count)


async def register_user(
    user_id: int,
    username: str,
    first_name: str,
    auto_approve_limit: int,
    is_admin: bool = False,
) -> str:
    """
    Ensure a user row exists and return their status.

    The first `auto_approve_limit` people through the door are approved
    automatically; after that newcomers land in the pending queue for the
    admin to approve. The admin is always approved, so a full queue can never
    lock you out of your own bot.

    Returns 'approved', 'pending' or 'blocked'.
    """
    def _register():
        conn = get_conn()
        row = conn.execute(
            "SELECT status FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

        if row is not None:
            # Keep display details fresh, but never touch an existing status.
            conn.execute(
                "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                (username or "", first_name or "friend", user_id),
            )
            conn.commit()
            return row["status"]

        approved_count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE status = 'approved'"
        ).fetchone()[0]

        if is_admin or auto_approve_limit <= 0 or approved_count < auto_approve_limit:
            status = "approved"
        else:
            status = "pending"

        conn.execute(
            """
            INSERT INTO users
                (user_id, username, first_name, status, approved_at, requested_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                username or "",
                first_name or "friend",
                status,
                datetime.now(timezone.utc).isoformat() if status == "approved" else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        logger.info(
            f"New user {user_id} ({first_name}) registered as '{status}' "
            f"({approved_count}/{auto_approve_limit} approved before this)"
        )
        return status

    return await asyncio.get_event_loop().run_in_executor(None, _register)


async def set_status(user_id: int, status: str) -> bool:
    """
    Set a user's access status to 'approved', 'pending' or 'blocked'.
    Returns False if there is no such user.
    """
    if status not in ("approved", "pending", "blocked"):
        raise ValueError(f"invalid status: {status}")

    def _set():
        conn = get_conn()
        approved_at = (
            datetime.now(timezone.utc).isoformat() if status == "approved" else None
        )
        cur = conn.execute(
            "UPDATE users SET status = ?, approved_at = COALESCE(?, approved_at) "
            "WHERE user_id = ?",
            (status, approved_at, user_id),
        )
        conn.commit()
        return cur.rowcount > 0

    return await asyncio.get_event_loop().run_in_executor(None, _set)


async def list_users(status: str | None = None, limit: int = 100) -> list[dict]:
    """List users, optionally filtered by status, newest request first."""
    def _list():
        conn = get_conn()
        if status:
            rows = conn.execute(
                "SELECT user_id, username, first_name, status, requested_at, created_at "
                "FROM users WHERE status = ? "
                "ORDER BY COALESCE(requested_at, created_at) DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT user_id, username, first_name, status, requested_at, created_at "
                "FROM users ORDER BY COALESCE(requested_at, created_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    return await asyncio.get_event_loop().run_in_executor(None, _list)


# ── Activity tracking & inactive-user cleanup ─────────────────────────────────

async def touch_last_seen(user_id: int) -> None:
    """
    Record that this user just interacted with the bot.

    Called for every incoming update, so it is throttled to one write per hour
    per user: the cleanup works in days, and there's no reason to rewrite the
    row on every message. Unknown users are ignored -- registration creates
    them, and their created_at stands in until the first touch.
    """
    def _touch():
        conn = get_conn()
        conn.execute(
            """
            UPDATE users SET last_seen_at = datetime('now')
            WHERE user_id = ?
              AND (last_seen_at IS NULL OR last_seen_at < datetime('now', '-1 hour'))
            """,
            (user_id,),
        )
        conn.commit()

    await asyncio.get_event_loop().run_in_executor(None, _touch)


# Tables holding per-user rows, in the order they must be emptied. Children
# first: they reference users(user_id) and foreign keys are enforced, so the
# users row can only go once nothing points at it.
_USER_DATA_TABLES = ("messages", "user_facts", "reminders", "usage_daily")


async def purge_inactive_users(days: int, protected_ids: tuple[int, ...] = ()) -> list[int]:
    """
    Delete users who haven't interacted in `days` days, with all their data.

    A deleted user who comes back is indistinguishable from a brand-new one:
    they go through approval, get asked their gender again, and the bot has no
    memory of them.

    Never deleted:
      * blocked users -- deleting them would let a blocked person wait out
        the window and return unblocked
      * anyone in `protected_ids` (the admin)
      * users with a reminder still pending -- someone who set a reminder for
        three months out hasn't left; they become eligible once it's delivered

    Each user is removed in its own transaction, so a failure part-way through
    can never leave half a user behind. Returns the IDs that were deleted.
    A `days` value <= 0 disables the cleanup entirely.
    """
    if days <= 0:
        return []

    def _purge():
        conn = get_conn()
        placeholders = ",".join("?" * len(protected_ids)) or "NULL"
        candidates = [
            r["user_id"]
            for r in conn.execute(
                f"""
                SELECT u.user_id FROM users u
                WHERE COALESCE(u.last_seen_at, u.created_at) < datetime('now', ?)
                  AND u.status != 'blocked'
                  AND u.user_id NOT IN ({placeholders})
                  AND NOT EXISTS (
                      SELECT 1 FROM reminders r
                      WHERE r.user_id = u.user_id AND r.status = 'pending'
                  )
                """,
                (f"-{days} days", *protected_ids),
            )
        ]

        deleted = []
        for user_id in candidates:
            try:
                with conn:   # one transaction per user: all-or-nothing
                    for table in _USER_DATA_TABLES:
                        conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
                    conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
                deleted.append(user_id)
            except Exception as exc:
                logger.error(f"Could not purge inactive user {user_id}: {exc}")

        if deleted:
            conn.execute("VACUUM")   # actually release the freed space
            logger.info(
                f"Purged {len(deleted)} user(s) inactive for {days}+ days: {deleted}"
            )
        return deleted

    return await asyncio.get_event_loop().run_in_executor(None, _purge)
