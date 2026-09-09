"""
memory/models.py — SQLite database layer.

Zero-config local database (bot.db). All tables are created on first run and
migrated in place on upgrade. Async callers wrap these in run_in_executor so
the Telegram event loop stays unblocked.
"""
import logging
import sqlite3
import threading
from pathlib import Path

from config import DB_PATH

logger = logging.getLogger(__name__)

# One connection per thread (SQLite connections aren't safe to share).
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection, creating it if needed."""
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row                  # dict-like rows
        conn.execute("PRAGMA journal_mode=WAL")         # concurrent reads
        conn.execute("PRAGMA foreign_keys=ON")
        # Handlers run concurrently, so a writer may briefly hold the lock.
        # Wait rather than raising "database is locked".
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return _local.conn


# Current shape of the reminders table. One row per *ping*, with rows sharing
# a group_id when they are lead-up nudges for the same real-world commitment.
_REMINDERS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS reminders (
        id         TEXT    PRIMARY KEY,          -- UUID, one per ping
        group_id   TEXT    NOT NULL,             -- shared by nudges for one commitment
        user_id    INTEGER NOT NULL REFERENCES users(user_id),
        content    TEXT    NOT NULL,
        kind       TEXT    DEFAULT 'task',       -- 'event' | 'task'
        event_at   TEXT,                         -- when the thing happens (ISO-8601 UTC)
        remind_at  TEXT    NOT NULL,             -- when to ping (ISO-8601 UTC)
        lead_label TEXT    DEFAULT '',           -- 'tonight' | 'in an hour' | ''
        status     TEXT    DEFAULT 'pending'
                   CHECK(status IN ('pending','sent','cancelled','missed')),
        job_id     TEXT,
        created_at TEXT    DEFAULT (datetime('now'))
    );
"""


def _migrate_reminders(conn: sqlite3.Connection) -> None:
    """
    Upgrade a pre-grouping reminders table in place.

    The original table had one row per reminder with no lead-up nudges and no
    'missed' status. Rebuilding is the only way to widen the status CHECK
    constraint, so we copy the old rows across and drop the original.
    """
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='reminders'"
    ).fetchone()
    if not exists:
        return

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(reminders)")}
    if "group_id" in columns:
        return  # already migrated

    logger.info("Migrating reminders table to the grouped schema...")
    conn.execute("ALTER TABLE reminders RENAME TO reminders_legacy")
    conn.executescript(_REMINDERS_SCHEMA)
    conn.execute(
        """
        INSERT INTO reminders
            (id, group_id, user_id, content, kind, event_at, remind_at,
             lead_label, status, job_id, created_at)
        SELECT id, id, user_id, content, 'task', remind_at, remind_at,
               '', status, job_id, created_at
        FROM reminders_legacy
        """
    )
    conn.execute("DROP TABLE reminders_legacy")
    logger.info("Reminders table migrated.")


def _migrate_users(conn: sqlite3.Connection) -> None:
    """
    Add the access-control columns to an existing users table.

    Everyone already in the database predates the approval gate, so they are
    grandfathered in as 'approved' -- the DEFAULT on the new column does that
    for existing rows automatically.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}

    if "status" not in columns:
        logger.info("Adding access-control columns to users table...")
        # 'approved' | 'pending' | 'blocked'. No CHECK constraint: SQLite can't
        # add one via ALTER TABLE, and the values are only ever set in code.
        conn.execute(
            "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'"
        )
    if "approved_at" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN approved_at TEXT")
    if "requested_at" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN requested_at TEXT")


def init_db() -> None:
    """Create all tables if they don't exist, then apply migrations."""
    conn = get_conn()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id        INTEGER PRIMARY KEY,
            username       TEXT    DEFAULT '',
            first_name     TEXT    DEFAULT 'friend',
            active_persona TEXT    DEFAULT 'girlfriend',
            timezone       TEXT    DEFAULT 'Asia/Kolkata',
            created_at     TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(user_id),
            role       TEXT    NOT NULL CHECK(role IN ('user', 'assistant')),
            content    TEXT    NOT NULL,
            persona    TEXT    DEFAULT 'girlfriend',
            created_at TEXT    DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_messages_user_created
            ON messages(user_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS user_facts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(user_id),
            fact       TEXT    NOT NULL,
            source     TEXT    DEFAULT 'inferred' CHECK(source IN ('explicit','inferred')),
            created_at TEXT    DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_facts_user
            ON user_facts(user_id);

        -- Daily counters used to stop the bot before Gemini's free-tier
        -- quota does. user_id 0 is the reserved row holding the global
        -- total for that day.
        CREATE TABLE IF NOT EXISTS usage_daily (
            day       TEXT    NOT NULL,          -- 'YYYY-MM-DD' in UTC
            user_id   INTEGER NOT NULL,          -- 0 = whole-bot total
            messages  INTEGER NOT NULL DEFAULT 0,
            api_calls INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, user_id)
        );
        """
    )

    _migrate_users(conn)
    _migrate_reminders(conn)
    conn.executescript(_REMINDERS_SCHEMA)
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_reminders_user_status
            ON reminders(user_id, status);
        CREATE INDEX IF NOT EXISTS idx_reminders_status_time
            ON reminders(status, remind_at);
        CREATE INDEX IF NOT EXISTS idx_reminders_group
            ON reminders(group_id);
        """
    )

    conn.commit()
    logger.info(f"SQLite DB ready at: {Path(DB_PATH).resolve()}")
