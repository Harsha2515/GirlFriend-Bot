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