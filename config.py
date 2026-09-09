"""
config.py — Central configuration loader.
All modules import from here. Never call os.getenv() directly elsewhere.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)


def _int_env(name: str, default: int) -> int:
    """Read an int from the environment, falling back on anything unparseable."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = (
    os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
)

# Your own Telegram user ID. Admin commands (/pending, /approve, /deny, /users,
# /usage) only answer this ID. Send /whoami to the bot to find it.
ADMIN_USER_ID: int = _int_env("ADMIN_USER_ID", 0)

# ── Gemini ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH: str = os.getenv("DB_PATH", "bot.db")

# ── Defaults ──────────────────────────────────────────────────────────────────
TIMEZONE_DEFAULT: str = os.getenv("TIMEZONE_DEFAULT", "Asia/Kolkata")
DEFAULT_PERSONA: str = "girlfriend"

# ── Access control ────────────────────────────────────────────────────────────
# The first N people to message the bot are approved automatically. After that
# newcomers land in a pending queue and you approve them with /approve <id>.
AUTO_APPROVE_LIMIT: int = _int_env("AUTO_APPROVE_LIMIT", 50)

# ── Free-tier guards ──────────────────────────────────────────────────────────
# These exist so the bot stops itself before Google does. Every user message
# costs 2 Gemini calls (one reply, one analysis), so the global ceiling is the
# figure that actually protects the free tier -- headcount does not.
#
# Set GLOBAL_DAILY_API_LIMIT below whatever your key's real daily quota is.
# Check yours at https://aistudio.google.com -> rate limits, then leave margin.
USER_DAILY_MESSAGE_LIMIT: int = _int_env("USER_DAILY_MESSAGE_LIMIT", 30)
GLOBAL_DAILY_API_LIMIT: int = _int_env("GLOBAL_DAILY_API_LIMIT", 1000)

# ── Retention ─────────────────────────────────────────────────────────────────
# Only the last 20 messages are ever sent to the model, so older rows serve no
# purpose beyond history you can read. Pruned nightly. 0 disables pruning.
MESSAGE_RETENTION_PER_USER: int = _int_env("MESSAGE_RETENTION_PER_USER", 400)

# ── Runtime guards ────────────────────────────────────────────────────────────
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN is missing. "
        "Add it to your .env file:\n  TELEGRAM_BOT_TOKEN=<your token>"
    )

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is missing. "
        "Get a free key at https://aistudio.google.com and add it to .env:\n"
        "  GEMINI_API_KEY=<your key>"
    )
