"""
validate.py — Offline sanity check.

Verifies imports, DB init/migration, scheduler startup, and the reminder
lead-time planner. Makes no network calls, so it's safe to run without
burning Gemini quota.

    python validate.py
"""
import asyncio
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")
IST = ZoneInfo("Asia/Kolkata")

errors: list[str] = []


def check(label: str, fn):
    try:
        result = fn()
        print(f"[OK] {label}" + (f": {result}" if result else ""))
    except Exception as exc:
        errors.append(f"{label}: {exc}")
        print(f"[FAIL] {label}: {exc}")


# ── Config & database ─────────────────────────────────────────────────────────

def _config():
    from config import TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, DB_PATH
    return f"token={TELEGRAM_BOT_TOKEN[:8]}..., gemini={GEMINI_API_KEY[:8]}..., db={DB_PATH}"


def _db():
    from memory.models import init_db, get_conn
    init_db()
    conn = get_conn()
    tables = [
        t["name"]
        for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not t["name"].startswith("sqlite_")
    ]
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(reminders)")}
    required = {"group_id", "event_at", "lead_label", "kind", "job_id"}
    missing = required - cols
    if missing:
        raise AssertionError(f"reminders table is missing columns: {missing}")
    return f"tables={sorted(tables)}"


check("config", _config)
check("database + migration", _db)


# ── Module imports ────────────────────────────────────────────────────────────

check("agent.client",   lambda: __import__("agent.client", fromlist=["generate"]) and "")
check("agent.llm",      lambda: __import__("agent.llm", fromlist=["call_llm"]) and "")
check("agent.extractor",lambda: __import__("agent.extractor", fromlist=["analyze_message"]) and "")
check("agent.persona",  lambda: __import__("agent.persona", fromlist=["PERSONAS"]) and "")
check("memory.context", lambda: __import__("memory.context", fromlist=["get_context"]) and "")
check("memory.user_profile",
      lambda: __import__("memory.user_profile", fromlist=["clear_user_facts"]) and "")
check("scheduler.reminder_store",
      lambda: __import__("scheduler.reminder_store", fromlist=["save_reminder_group"]) and "")
check("scheduler.jobs", lambda: __import__("scheduler.jobs", fromlist=["restore_pending"]) and "")
check("bot.handlers",   lambda: __import__("bot.handlers", fromlist=["handle_message"]) and "")
check("bot.commands",   lambda: __import__("bot.commands", fromlist=["timezone_command"]) and "")


# ── Reminder planning logic ───────────────────────────────────────────────────

def _planner():
    from scheduler.jobs import plan_pings

    now = datetime(2026, 9, 9, 10, 0, tzinfo=IST).astimezone(UTC)

    # Tomorrow 11:00 IST meeting -> night before, an hour before, just before.
    meeting = datetime(2026, 9, 10, 11, 0, tzinfo=IST).astimezone(UTC)
    pings = plan_pings(meeting, "event", False, IST, now=now)
    labels = [label for _, label in pings]
    assert labels == ["tonight", "in an hour", ""], f"unexpected labels: {labels}"

    night, hour_before, final = [p[0].astimezone(IST) for p in pings]
    assert (night.hour, night.day) == (21, 9), f"night-before wrong: {night}"
    assert (hour_before.hour, hour_before.minute) == (10, 0), f"hour-before wrong: {hour_before}"
    assert (final.hour, final.minute) == (10, 50), f"final nudge wrong: {final}"

    # An all-day task tomorrow gets a heads-up plus the 9am slot.
    errand = datetime(2026, 9, 10, 9, 0, tzinfo=IST).astimezone(UTC)
    all_day = plan_pings(errand, "task", True, IST, now=now)
    assert [l for _, l in all_day] == ["tonight", ""], f"all-day wrong: {all_day}"

    # Something later today gets no pointless night-before nudge.
    soon = datetime(2026, 9, 9, 18, 0, tzinfo=IST).astimezone(UTC)
    today = plan_pings(soon, "event", False, IST, now=now)
    assert "tonight" not in [l for _, l in today], f"same-day got a night nudge: {today}"

    # Anything already past is never scheduled backwards.
    imminent = plan_pings(now + timedelta(minutes=1), "task", False, IST, now=now)
    assert all(when > now for when, _ in imminent), "planner produced a past time"

    return f"{len(pings)} nudges for a next-day meeting"


check("reminder lead-time planner", _planner)


# ── Scheduler startup ─────────────────────────────────────────────────────────

def _scheduler():
    from scheduler.jobs import init_scheduler

    async def run():
        sched = init_scheduler()
        state = sched.running
        sched.shutdown(wait=False)
        return f"running={state}"

    return asyncio.run(run())


check("scheduler startup", _scheduler)


# ── Result ────────────────────────────────────────────────────────────────────

print()
print("=" * 52)
if errors:
    print(f"FAILED — {len(errors)} error(s):")
    for err in errors:
        print(f"  - {err}")
    sys.exit(1)

print("ALL CHECKS PASSED")
print()
print("  Run the bot with:  venv\\Scripts\\python main.py")
print("=" * 52)
