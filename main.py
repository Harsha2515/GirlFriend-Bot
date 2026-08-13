"""
main.py — Entry point for the GirlFriend Telegram Bot.

Run with:
    python main.py

What this does:
  1. Initialise SQLite DB (creates bot.db + all tables on first run)
  2. Start APScheduler for timed reminders
  3. Register all Telegram command and message handlers
  4. Start polling loop (no webhook/server required)
"""
import asyncio
import logging
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import TELEGRAM_BOT_TOKEN
from memory.models import init_db
from scheduler.jobs import init_scheduler
from bot.commands import (
    start,
    mode,
    name_command,
    reminders,
    cancel,
    forget,
    help_command,
)
from bot.handlers import handle_message, error_handler

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)   # silence noisy HTTP logs
logger = logging.getLogger(__name__)


# ── Render Web Service Health Check Server ───────────────────────────────────

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass  # silence healthcheck logs


def start_health_check_server() -> None:
    port_str = os.getenv("PORT")
    if port_str:
        try:
            port = int(port_str)
            server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            logger.info(f"Health check HTTP server started on port {port}")
        except Exception as e:
            logger.warning(f"Could not start HTTP server on port {port_str}: {e}")


# ── Async Post-Init Hook ──────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    logger.info("Starting scheduler in running event loop...")
    init_scheduler()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # Ensure an asyncio event loop exists in MainThread for Python 3.12+ / 3.14+
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # 1. Initialise SQLite (creates bot.db + tables if missing)
    logger.info("Initialising database...")
    init_db()

    # 2. Start optional health check server for Render Web Service deployment
    start_health_check_server()

    # 3. Build the Telegram Application
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )

    # 4. Register command handlers
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("mode",      mode))
    app.add_handler(CommandHandler("name",      name_command))
    app.add_handler(CommandHandler("reminders", reminders))
    app.add_handler(CommandHandler("cancel",    cancel))
    app.add_handler(CommandHandler("forget",    forget))
    app.add_handler(CommandHandler("help",      help_command))

    # 5. Register the main message handler (catches all non-command text)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 6. Global error handler
    app.add_error_handler(error_handler)

    # 7. Start polling (Telegram pushes messages to us; no webhook needed)
    logger.info("Bot started in polling mode. Press Ctrl+C to stop.")
    app.run_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,   # ignore messages sent while bot was offline
    )


if __name__ == "__main__":
    main()
