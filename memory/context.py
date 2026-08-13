"""
memory/context.py — Conversation history stored in SQLite.

Provides:
  save_message   — persist a single message
  get_context    — load last N messages as [{role, content}] for the LLM
  clear_history  — wipe history (/forget command)
  estimate_tokens / trim_to_budget — token-budget helpers
"""
import logging
import asyncio
from memory.models import get_conn

logger = logging.getLogger(__name__)

# Last N messages sent to Gemini as context. 20 = 10 exchanges.
MAX_HISTORY = 20


# ── Save ──────────────────────────────────────────────────────────────────────

async def save_message(
    user_id: int,
    role: str,
    content: str,
    persona: str = "girlfriend",
) -> None:
    """Persist a single message (user or assistant) to the messages table."""
    def _save():
        conn = get_conn()
        conn.execute(
            "INSERT INTO messages (user_id, role, content, persona) VALUES (?, ?, ?, ?)",
            (user_id, role, content, persona),
        )
        conn.commit()

    await asyncio.get_event_loop().run_in_executor(None, _save)
    logger.debug(f"Saved [{role}] message for user {user_id}")


# ── Load ──────────────────────────────────────────────────────────────────────

async def get_context(user_id: int, limit: int = MAX_HISTORY) -> list[dict]:
    """
    Fetch the last `limit` messages for a user, ordered oldest → newest.
    Returns [{role, content}] list ready for agent/llm.py.
    """
    def _load():
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT role, content FROM (
                SELECT role, content, created_at
                FROM messages
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
            ) sub
            ORDER BY created_at ASC, id ASC
            """,
            (user_id, limit),
        ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    return await asyncio.get_event_loop().run_in_executor(None, _load)


# ── Clear ─────────────────────────────────────────────────────────────────────

async def clear_history(user_id: int) -> int:
    """Delete all messages for a user (/forget command). Returns count deleted."""
    def _clear():
        conn = get_conn()
        cur = conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        conn.commit()
        count = cur.rowcount
        logger.info(f"Cleared {count} messages for user {user_id}")
        return count

    return await asyncio.get_event_loop().run_in_executor(None, _clear)


# ── Token budget helpers ───────────────────────────────────────────────────────

def estimate_tokens(messages: list[dict]) -> int:
    """~4 characters per token (English). Good enough for budget checks."""
    return sum(len(m.get("content", "")) for m in messages) // 4


def trim_to_budget(messages: list[dict], max_tokens: int = 3000) -> list[dict]:
    """Drop oldest messages until the list fits within max_tokens."""
    while messages and estimate_tokens(messages) > max_tokens:
        messages = messages[1:]
    return messages