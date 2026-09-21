"""
main.py — Entry point for the GirlFriend Telegram Bot.

Run with:
    python main.py

What this does:
  1. Initialise SQLite (creates bot.db + all tables on first run, migrates on upgrade)
  2. Start APScheduler and restore every pending reminder from the database
  3. Register all Telegram command and message handlers
  4. Start polling (no webhook or public URL required)
"""
import logging
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from bot.commands import (
    approve,
    block,
    cancel,
    deny,
    facts_command,
    forget,
    gender_command,
    help_command,
    mode,
    name_command,
    pending,
    reminders,
    start,
    switch,
    timezone_command,
    usage_command,
    users_command,
    whoami,
)
from bot.handlers import handle_message, error_handler
from bot.tool_commands import (
    handle_location,
    imagine_command,
    location_command,
    photo_command,
    search_command,
    weather_command,
)
from config import (
    ADMIN_USER_ID,
    INACTIVE_USER_DAYS,
    MESSAGE_RETENTION_PER_USER,
    TELEGRAM_BOT_TOKEN,
)
from memory.context import prune_old_messages
from memory.models import init_db
from memory.usage import prune_usage
from memory.user_profile import purge_inactive_users, touch_last_seen
from scheduler.jobs import init_scheduler, restore_pending

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)      # silence noisy HTTP logs
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# ── Health check server (for free hosting tiers that expect an open port) ─────

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass   # silence health check logs


def start_health_check_server() -> None:
    """
    Render and similar free web-service tiers kill a process that never binds
    a port. Only starts when PORT is set, so local runs stay quiet.
    """
    port_str = os.getenv("PORT")
    if not port_str:
        return
    try:
        port = int(port_str)
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        logger.info(f"Health check server listening on port {port}")
    except Exception as exc:
        logger.warning(f"Could not start health check server on {port_str}: {exc}")


# ── Startup hook ──────────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    """
    Runs once the event loop is up.

    Restoring reminders here is what makes them survive a restart: APScheduler
    keeps jobs in memory, but the reminders table is the source of truth.
    """
    scheduler = init_scheduler()
    summary = await restore_pending(application.bot)
    if summary["late"]:
        logger.info(f"{summary['late']} reminder(s) missed while offline — sending now.")

    if not ADMIN_USER_ID:
        logger.warning(
            "ADMIN_USER_ID is not set. Nobody can approve new users once the "
            "auto-approve limit fills. Send /whoami to the bot, then put your "
            "ID in .env."
        )

    # Nightly housekeeping at 03:30 UTC, just after the backup cron at 03:00
    # so the backup captures the pre-prune state.
    scheduler.add_job(
        _nightly_maintenance,
        trigger="cron",
        hour=3,
        minute=30,
        id="nightly_maintenance",
        replace_existing=True,
    )


async def _nightly_maintenance() -> None:
    """Remove inactive users, then trim message history and old usage counters."""
    # Each step is isolated so one failure doesn't skip the others.
    try:
        protected = (ADMIN_USER_ID,) if ADMIN_USER_ID else ()
        purged = await purge_inactive_users(INACTIVE_USER_DAYS, protected_ids=protected)
        logger.info(f"Nightly maintenance: {len(purged)} inactive user(s) removed.")
    except Exception as exc:
        logger.error(f"Inactive-user cleanup failed: {exc}")

    try:
        pruned = await prune_old_messages(MESSAGE_RETENTION_PER_USER)
        rows = await prune_usage(keep_days=90)
        logger.info(f"Nightly maintenance: {pruned} messages, {rows} usage rows pruned.")
    except Exception as exc:
        logger.error(f"Nightly maintenance failed: {exc}")


async def _track_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Stamp last_seen_at for whoever sent this update, before any handler runs.

    Registered in group -1 so it sees every message, command and button tap --
    not just chats that reach Gemini. It never blocks or replies, and a failure
    here must never stop the real handlers from running.
    """
    user = update.effective_user
    if user is None:
        return
    try:
        await touch_last_seen(user.id)
    except Exception as exc:
        logger.warning(f"Could not record activity for {user.id}: {exc}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("Initialising database...")
    init_db()

    start_health_check_server()

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )

    # Group -1 runs before every other handler, for every update.
    app.add_handler(TypeHandler(Update, _track_activity), group=-1)

    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("switch",    switch))
    app.add_handler(CommandHandler("gender",    gender_command))
    app.add_handler(CommandHandler("mode",      mode))
    app.add_handler(CommandHandler("name",      name_command))
    app.add_handler(CommandHandler("timezone",  timezone_command))
    app.add_handler(CommandHandler("tz",        timezone_command))
    app.add_handler(CommandHandler("reminders", reminders))
    app.add_handler(CommandHandler("cancel",    cancel))
    app.add_handler(CommandHandler("facts",     facts_command))
    app.add_handler(CommandHandler("forget",    forget))
    app.add_handler(CommandHandler("help",      help_command))
    app.add_handler(CommandHandler("whoami",    whoami))

    # Tools: weather, location, web search, photos, image generation.
    app.add_handler(CommandHandler("weather",   weather_command))
    app.add_handler(CommandHandler("location",  location_command))
    app.add_handler(CommandHandler("search",    search_command))
    app.add_handler(CommandHandler("photo",     photo_command))
    app.add_handler(CommandHandler("imagine",   imagine_command))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))

    # Admin only — silently ignored for everyone else.
    app.add_handler(CommandHandler("approve",   approve))
    app.add_handler(CommandHandler("deny",      deny))
    app.add_handler(CommandHandler("block",     block))
    app.add_handler(CommandHandler("pending",   pending))
    app.add_handler(CommandHandler("users",     users_command))
    app.add_handler(CommandHandler("usage",     usage_command))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info("Bot started in polling mode. Press Ctrl+C to stop.")
    app.run_polling(
        allowed_updates=["message"],
        # Deliver anything sent while the bot was down, rather than discarding
        # it — on a free tier that sleeps, dropping messages loses real ones.
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
