#!/usr/bin/env python3
"""
tools/chats.py — Read the stored conversations from bot.db.

Everything the bot remembers lives in one SQLite file. This is a read-only
viewer for it, so you never have to write SQL by hand.

    python tools/chats.py users                 # who has used the bot
    python tools/chats.py chat Harsha           # by name
    python tools/chats.py chat @harsha2515      # by username
    python tools/chats.py chat 1273371326       # by Telegram ID
    python tools/chats.py chat Harsha -n 100    # last 100 messages
    python tools/chats.py search "meeting"      # find messages by text
    python tools/chats.py facts Harsha          # what it remembers about them
    python tools/chats.py reminders Harsha      # their pending nudges
    python tools/chats.py stats                 # database overview
    python tools/chats.py export Harsha         # dump one chat to a .txt file

Names and usernames are matched case-insensitively, and a prefix is enough.
An ambiguous name lists the candidates instead of guessing.

Run it from the project root. It opens the database read-only, so it is safe
to use while the bot is running.
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config import DB_PATH
except Exception:
    DB_PATH = os.getenv("DB_PATH", "bot.db")


def connect() -> sqlite3.Connection:
    """Open the database read-only so a running bot is never disturbed."""
    if not os.path.exists(DB_PATH):
        sys.exit(f"No database at {DB_PATH!r}. Run the bot once first.")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def local(ts: str, tz_name: str = "") -> str:
    """Render a stored timestamp readably; pass through anything unparseable."""
    if not ts:
        return "?"
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if tz_name:
            try:
                from zoneinfo import ZoneInfo
                dt = dt.astimezone(ZoneInfo(tz_name))
            except Exception:
                pass
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts


def cmd_users(conn, args):
    rows = conn.execute(
        """
        SELECT u.user_id, u.first_name, u.username, u.status, u.active_persona,
               u.timezone, u.created_at,
               (SELECT COUNT(*) FROM messages   m WHERE m.user_id = u.user_id) AS msgs,
               (SELECT COUNT(*) FROM user_facts f WHERE f.user_id = u.user_id) AS facts,
               (SELECT MAX(created_at) FROM messages m WHERE m.user_id = u.user_id) AS last_seen
        FROM users u
        ORDER BY last_seen DESC NULLS LAST
        """
    ).fetchall()
    if not rows:
        print("No users yet.")
        return

    print(f"{'USER ID':>12}  {'NAME':<16} {'STATUS':<9} {'MSGS':>6} {'FACTS':>6}  LAST SEEN")
    print("-" * 78)
    for r in rows:
        handle = f"@{r['username']}" if r["username"] else ""
        name = (r["first_name"] or "?")[:15]
        print(
            f"{r['user_id']:>12}  {name:<16} {r['status'] or 'approved':<9} "
            f"{r['msgs']:>6} {r['facts']:>6}  {local(r['last_seen'])}  {handle}"
        )
    print(f"\n{len(rows)} user(s). See one chat with:  python tools/chats.py chat <USER ID>")


def _user_row(conn, who):
    """
    Find a user by ID, @username, or name -- so you never have to memorise a
    10-digit Telegram ID. An ambiguous name lists the candidates rather than
    guessing, since picking the wrong person here means reading the wrong
    private conversation.
    """
    who = str(who).strip()

    if who.isdigit():
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (int(who),)
        ).fetchone()
        if row:
            return row
        sys.exit(f"No user with ID {who}. Run: python tools/chats.py users")

    handle = who.lstrip("@").lower()
    matches = conn.execute(
        """
        SELECT * FROM users
        WHERE LOWER(username) = ?
           OR LOWER(first_name) = ?
           OR LOWER(first_name) LIKE ?
           OR LOWER(username)   LIKE ?
        """,
        (handle, handle, f"{handle}%", f"{handle}%"),
    ).fetchall()

    if not matches:
        sys.exit(
            f"Nobody matching {who!r}. Run: python tools/chats.py users"
        )
    if len(matches) > 1:
        print(f"{who!r} matches {len(matches)} people — be more specific:\n")
        for m in matches:
            tag = f"@{m['username']}" if m["username"] else "(no username)"
            print(f"  {m['user_id']:>12}  {m['first_name']:<16} {tag}")
        sys.exit(1)

    return matches[0]


def cmd_chat(conn, args):
    user = _user_row(conn, args.who)
    tz = user["timezone"] or ""

    rows = conn.execute(
        "SELECT role, content, persona, created_at FROM messages "
        "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user["user_id"], args.number),
    ).fetchall()
    rows = list(reversed(rows))

    if not rows:
        print(f"No messages stored for {user['first_name']} ({user['user_id']}).")
        return

    print(f"Conversation with {user['first_name']} ({user['user_id']}) — "
          f"last {len(rows)} message(s), times in {tz or 'UTC'}\n")
    for r in rows:
        who = "THEM" if r["role"] == "user" else "BOT "
        print(f"[{local(r['created_at'], tz)}] {who} | {r['content']}")


def cmd_search(conn, args):
    rows = conn.execute(
        """
        SELECT m.user_id, u.first_name, m.role, m.content, m.created_at
        FROM   messages m LEFT JOIN users u ON u.user_id = m.user_id
        WHERE  m.content LIKE ?
        ORDER  BY m.id DESC LIMIT ?
        """,
        (f"%{args.term}%", args.number),
    ).fetchall()
    if not rows:
        print(f"Nothing matching {args.term!r}.")
        return
    print(f"{len(rows)} match(es) for {args.term!r}:\n")
    for r in rows:
        who = "THEM" if r["role"] == "user" else "BOT "
        print(f"[{local(r['created_at'])}] {r['first_name'] or r['user_id']} {who} | "
              f"{r['content'][:140]}")


def cmd_facts(conn, args):
    user = _user_row(conn, args.who)
    rows = conn.execute(
        "SELECT fact, source, created_at FROM user_facts WHERE user_id = ? "
        "ORDER BY created_at",
        (user["user_id"],),
    ).fetchall()
    if not rows:
        print(f"Nothing remembered about {user['first_name']} yet.")
        return
    print(f"What the bot remembers about {user['first_name']} ({user['user_id']}):\n")
    for r in rows:
        print(f"  • {r['fact']}   [{r['source']}, {local(r['created_at'])}]")


def cmd_reminders(conn, args):
    user = _user_row(conn, args.who)
    tz = user["timezone"] or ""
    rows = conn.execute(
        """
        SELECT group_id, content, kind, event_at, status,
               COUNT(*) AS nudges, MIN(remind_at) AS next_ping
        FROM   reminders WHERE user_id = ?
        GROUP  BY group_id, status
        ORDER  BY event_at DESC LIMIT ?
        """,
        (user["user_id"], args.number),
    ).fetchall()
    if not rows:
        print(f"No reminders for {user['first_name']}.")
        return
    print(f"Reminders for {user['first_name']} ({user['user_id']}), times in {tz or 'UTC'}:\n")
    for r in rows:
        print(f"  [{r['status']:<9}] {r['content']}")
        print(f"      when: {local(r['event_at'], tz)}   nudges: {r['nudges']}   "
              f"next: {local(r['next_ping'], tz)}")


def cmd_stats(conn, args):
    def one(sql, *p):
        return conn.execute(sql, p).fetchone()[0]

    size = os.path.getsize(DB_PATH) / 1024 / 1024
    print(f"Database: {os.path.abspath(DB_PATH)}  ({size:.2f} MB)\n")
    print(f"  Users          {one('SELECT COUNT(*) FROM users')}")
    for st in ("approved", "pending", "blocked"):
        print(f"    {st:<12} {one('SELECT COUNT(*) FROM users WHERE status = ?', st)}")
    print(f"  Messages       {one('SELECT COUNT(*) FROM messages')}")
    print(f"  Facts          {one('SELECT COUNT(*) FROM user_facts')}")
    print(f"  Reminders      {one('SELECT COUNT(*) FROM reminders')}")
    for st in ("pending", "sent", "cancelled", "missed"):
        print(f"    {st:<12} {one('SELECT COUNT(*) FROM reminders WHERE status = ?', st)}")

    try:
        rows = conn.execute(
            "SELECT day, api_calls, messages FROM usage_daily "
            "WHERE user_id = 0 ORDER BY day DESC LIMIT 7"
        ).fetchall()
        if rows:
            print("\n  Recent daily usage:")
            for r in rows:
                print(f"    {r['day']}  {r['api_calls']:>5} API calls  "
                      f"{r['messages']:>4} messages")
    except sqlite3.OperationalError:
        pass   # usage_daily predates this database


def cmd_export(conn, args):
    user = _user_row(conn, args.who)
    tz = user["timezone"] or ""
    rows = conn.execute(
        "SELECT role, content, created_at FROM messages WHERE user_id = ? ORDER BY id",
        (user["user_id"],),
    ).fetchall()
    if not rows:
        sys.exit(f"No messages to export for {user['first_name']}.")

    out = args.out or f"chat-{user['user_id']}.txt"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"Conversation with {user['first_name']} ({user['user_id']})\n")
        fh.write(f"{len(rows)} messages, times in {tz or 'UTC'}\n")
        fh.write("=" * 70 + "\n\n")
        for r in rows:
            who = "THEM" if r["role"] == "user" else "BOT "
            fh.write(f"[{local(r['created_at'], tz)}] {who} | {r['content']}\n")
    print(f"Wrote {len(rows)} messages to {out}")


def main():
    ap = argparse.ArgumentParser(
        description="Read the GirlFriend Bot database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("users", help="list everyone who has used the bot")
    sub.add_parser("stats", help="database overview")

    p = sub.add_parser("chat", help="show one person's conversation")
    p.add_argument("who", help="user ID, @username, or name")
    p.add_argument("-n", "--number", type=int, default=40)

    p = sub.add_parser("search", help="find messages containing text")
    p.add_argument("term")
    p.add_argument("-n", "--number", type=int, default=30)

    p = sub.add_parser("facts", help="what the bot remembers about someone")
    p.add_argument("who", help="user ID, @username, or name")

    p = sub.add_parser("reminders", help="someone's reminders")
    p.add_argument("who", help="user ID, @username, or name")
    p.add_argument("-n", "--number", type=int, default=30)

    p = sub.add_parser("export", help="write one chat to a text file")
    p.add_argument("who", help="user ID, @username, or name")
    p.add_argument("-o", "--out")

    args = ap.parse_args()
    conn = connect()
    try:
        globals()[f"cmd_{args.cmd}"](conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
