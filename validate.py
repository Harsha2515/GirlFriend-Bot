"""Validation script — checks all imports and DB init work correctly."""
import sys

errors = []

try:
    from config import TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, DB_PATH
    print(f"[OK] config: token={TELEGRAM_BOT_TOKEN[:10]}..., gemini={GEMINI_API_KEY[:10]}..., db={DB_PATH}")
except Exception as e:
    errors.append(f"[FAIL] config: {e}")

try:
    from memory.models import init_db, get_conn
    init_db()
    conn = get_conn()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print(f"[OK] SQLite DB: tables = {[t['name'] for t in tables]}")
except Exception as e:
    errors.append(f"[FAIL] memory.models: {e}")

try:
    from memory.context import save_message, get_context, clear_history, trim_to_budget
    print("[OK] memory.context")
except Exception as e:
    errors.append(f"[FAIL] memory.context: {e}")

try:
    from memory.user_profile import get_user, create_or_update_user, add_user_fact
    print("[OK] memory.user_profile")
except Exception as e:
    errors.append(f"[FAIL] memory.user_profile: {e}")

try:
    from agent.llm import call_llm
    print("[OK] agent.llm")
except Exception as e:
    errors.append(f"[FAIL] agent.llm: {e}")

try:
    from agent.persona import build_system_prompt, PERSONAS
    print(f"[OK] agent.persona: personas = {list(PERSONAS.keys())}")
except Exception as e:
    errors.append(f"[FAIL] agent.persona: {e}")

try:
    from agent.extractor import extract_reminder
    print("[OK] agent.extractor")
except Exception as e:
    errors.append(f"[FAIL] agent.extractor: {e}")

try:
    from scheduler.jobs import init_scheduler
    sched = init_scheduler()
    print(f"[OK] scheduler.jobs: running={sched.running}")
    sched.shutdown(wait=False)
except Exception as e:
    errors.append(f"[FAIL] scheduler.jobs: {e}")

try:
    from scheduler.reminder_store import save_reminder, get_pending_reminders, cancel_reminder, mark_reminder_sent
    print("[OK] scheduler.reminder_store")
except Exception as e:
    errors.append(f"[FAIL] scheduler.reminder_store: {e}")

try:
    from bot.handlers import handle_message, error_handler
    print("[OK] bot.handlers")
except Exception as e:
    errors.append(f"[FAIL] bot.handlers: {e}")

try:
    from bot.commands import start, mode, name_command, reminders, cancel, forget, help_command
    print("[OK] bot.commands")
except Exception as e:
    errors.append(f"[FAIL] bot.commands: {e}")

print()
if errors:
    print("=" * 50)
    print(f"FAILED — {len(errors)} error(s):")
    for err in errors:
        print(" ", err)
    sys.exit(1)
else:
    print("=" * 50)
    print("ALL CHECKS PASSED!")
    print()
    print("  Run the bot with:")
    print("  venv\\Scripts\\python main.py")
    print("=" * 50)
