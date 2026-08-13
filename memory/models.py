"""
memory/models.py — SQLite database layer.

Replaces the original Firebase/Firestore implementation with a zero-config
local SQLite database (bot.db). All tables are created automatically on first
run. Async access via run_in_executor keeps the Telegram event loop unblocked.
"""
import sqlite3
import logging
import threading
from pathlib import Path
from config import DB_PATH

logger = logging.getLogger(__name__)

# One connection per thread (SQLite isn't thread-safe by default)
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection, creating it if needed."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row   # dict-like rows
        _local.conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db() -> None:
    """
    Create all tables if they don't exist.
    Call once at startup from main.py.
    """
    conn = get_conn()
    conn.executescript("""
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

        CREATE TABLE IF NOT EXISTS reminders (
            id         TEXT    PRIMARY KEY,       -- UUID
            user_id    INTEGER NOT NULL REFERENCES users(user_id),
            content    TEXT    NOT NULL,
            remind_at  TEXT    NOT NULL,           -- ISO-8601 UTC
            status     TEXT    DEFAULT 'pending' CHECK(status IN ('pending','sent','cancelled')),
            job_id     TEXT,
            created_at TEXT    DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_reminders_user_status
            ON reminders(user_id, status);

        CREATE TABLE IF NOT EXISTS user_facts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(user_id),
            fact       TEXT    NOT NULL,
            source     TEXT    DEFAULT 'inferred' CHECK(source IN ('explicit','inferred')),
            created_at TEXT    DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_facts_user
            ON user_facts(user_id);
    """)
    conn.commit()
    logger.info(f"SQLite DB initialised at: {Path(DB_PATH).resolve()}")