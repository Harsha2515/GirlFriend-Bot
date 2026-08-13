"""
config.py — Central configuration loader.
All modules import from here. Never import os.getenv() directly.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (one level up from this file if needed)
_env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = (
    os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
)

# ── Gemini ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# ── Database ──────────────────────────────────────────────────────────────────
# SQLite DB file — created automatically on first run
DB_PATH: str = os.getenv("DB_PATH", "bot.db")

# ── Defaults ──────────────────────────────────────────────────────────────────
TIMEZONE_DEFAULT: str = os.getenv("TIMEZONE_DEFAULT", "Asia/Kolkata")
DEFAULT_PERSONA: str   = "girlfriend"

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
