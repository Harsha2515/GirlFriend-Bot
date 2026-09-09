# 💕 GirlFriend Bot

> A Telegram AI companion powered by **Google Gemini Flash** (free tier).
> Three personas — **Girlfriend**, **Mentor**, **Assistant** — with persistent
> memory and reminders that pick themselves up out of normal conversation.

---

## What makes it different

You don't tell it to set a reminder. You just talk:

> **You:** hey, tomorrow I have a meeting at 11 AM and I have to present the Q3 numbers
>
> **Priya:** Oh that's a big one! How are you feeling about it — nervous, or ready to
> go crush it? 💕
>
> 📌 Meeting and present Q3 numbers — tomorrow at 11:00 AM — I'll nudge you tonight too

Then it actually shows up:

| When | Message |
|---|---|
| Tonight, 9:00 PM | Heads up for tomorrow: Meeting and present Q3 numbers at 11:00 AM |
| Tomorrow, 10:00 AM | In about an hour: Meeting and present Q3 numbers (11:00 AM) |
| Tomorrow, 10:50 AM | Starting soon: Meeting and present Q3 numbers at 11:00 AM |

It also quietly remembers durable facts about you ("Works as a backend developer
at TCS") and weaves them into later conversations.

---

## Quick Start

### 1. Prerequisites
- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A Gemini API key from [Google AI Studio](https://aistudio.google.com) — free, no card

### 2. Setup

```powershell
cd "c:\Z - Work\GirlFriend-Bot"
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure `.env`

```env
TELEGRAM_BOT_TOKEN=<your BotFather token>
GEMINI_API_KEY=<your Gemini key>
DB_PATH=bot.db
TIMEZONE_DEFAULT=Asia/Kolkata
```

### 4. Check and run

```powershell
python validate.py    # offline sanity check, makes no API calls
python main.py
```

Open Telegram, send `/start`, pick a persona. Done 🎉

---

## Commands

| Command | Description |
|---|---|
| `/start` | Onboarding + persona picker |
| `/mode girlfriend\|mentor\|assistant` | Switch persona |
| `/name <name>` | Set your preferred name |
| `/timezone <Area/City>` | Set your timezone (`/tz` also works) |
| `/reminders` | List upcoming commitments |
| `/cancel <id>` | Cancel a commitment and all its nudges |
| `/facts` | See what it remembers about you (`/facts clear` to wipe) |
| `/forget` | Clear conversation history |
| `/whoami` | Show your Telegram user ID |
| `/help` | Show all commands |

**Admin only** (silently ignored for everyone else):

| Command | Description |
|---|---|
| `/pending` | List people waiting for approval |
| `/approve <id>` | Grant access |
| `/deny <id>` | Send back to the pending queue |
| `/block <id>` | Block permanently |
| `/users` | Counts per status + auto-approve slots left |
| `/usage` | Daily API consumption vs the free-tier budget |

---

## Personas

| Persona | Name | Personality |
|---|---|---|
| 💕 Girlfriend | Priya | Warm, caring, emotionally intelligent |
| 🎓 Mentor | Coach | Direct, sharp, pushes you to grow |
| 🗂 Assistant | Aria | Efficient, concise, task-focused |

---

## How reminders work

**Extraction.** After every message, one Gemini call looks for commitments *and*
durable facts at the same time — one call, not two, to stretch the free tier. It
runs concurrently with the chat reply, so it costs no extra wait. There's no
keyword filter, so indirect phrasing still lands ("I need to submit the report by
end of the week").

**Lead times.** One commitment becomes up to three nudges, sharing a `group_id`:

- **Night before, 9 PM** — only when the commitment is on a later day
- **One hour before**
- **10 minutes before** for events, or **on the dot** for task deadlines

**Surviving restarts.** APScheduler keeps jobs in memory, so the `reminders`
table in SQLite is the real source of truth. On boot, `restore_pending()`
rebuilds every job from the database:

- Still in the future → re-scheduled
- Passed while the bot was down, under 2 hours ago → delivered now, marked late
- Older than that → marked `missed` rather than sent stale

This is why the bot can be restarted, redeployed, or cycled by a free host
without losing anything.

---

## Project Structure

```
├── main.py                   # Entry point — run this
├── config.py                 # Loads .env, exports config
├── validate.py               # Offline self-check (no API calls)
│
├── agent/
│   ├── client.py             # Shared Gemini client, retry + model fallback
│   ├── llm.py                # Conversational reply path
│   ├── persona.py            # System prompt builder per persona
│   └── extractor.py          # Combined commitment + fact extraction
│
├── bot/
│   ├── handlers.py           # Main message handler
│   └── commands.py           # All /commands
│
├── memory/
│   ├── models.py             # SQLite schema + migrations
│   ├── context.py            # Conversation history
│   └── user_profile.py       # Profile + long-term facts
│
├── memory/
│   └── usage.py              # Daily counters + free-tier guards
│
├── scheduler/
│   ├── jobs.py               # Lead-time planning, firing, restart recovery
│   └── reminder_store.py     # Reminder CRUD + dedup
│
├── tools/
│   └── chats.py              # Read stored conversations (read-only)
│
└── deploy/
    ├── setup.sh              # One-command VM provisioning
    ├── backup.sh             # Nightly SQLite snapshot
    └── gfbot.service         # systemd unit
```

---

## Tech Stack

| Layer | Tool | Notes |
|---|---|---|
| Bot framework | `python-telegram-bot` v21 | Async polling |
| LLM | Google Gemini Flash | Free tier |
| Database | SQLite (`bot.db`) | Zero config |
| Scheduler | APScheduler | In-process, rebuilt from SQLite on boot |
| Language | Python 3.11 | Pinned in `runtime.txt` |

---

## Running it for free, honestly

The bot needs a process that's awake when your reminder time arrives. The catch
with every free tier is what happens when it isn't.

**Oracle Cloud Always Free — recommended.** A real VM with a persistent 50 GB
disk, free indefinitely, and the code runs unchanged. Step-by-step setup is in
**[DEPLOY.md](DEPLOY.md)**, along with `deploy/setup.sh` which provisions the
whole thing (systemd service, auto-restart, nightly backups) in one command.

**Your own machine, a Raspberry Pi, or an old Android phone via Termux.** Free
and no signup. Always-on only while the device is powered.

**Render free — not recommended.** It spins down after 15 minutes idle *and*
its disk is ephemeral, so `bot.db` is wiped on every redeploy, taking your
history, facts, and pending reminders with it. Persistent disks are paid-only.
The `PORT` health check server in `main.py` exists for this tier, but the data
loss isn't something you can work around for free.

> ⚠️ GitHub Actions **cannot** host this. A cron workflow gets a fresh container
> every run with no persistent disk, and can't hold a polling loop open. The old
> `.github/workflows/actions.yml` in this repo never worked and has been removed.
>
> **PythonAnywhere** free can't reach `api.telegram.org` — it's not on the
> outbound whitelist. **Railway** is trial credit, not a free tier.

Whatever you pick, `bot.db` holds everything — keep a copy off the box.

---

## Multiple users

The bot is multi-user out of the box — every person gets their own persona,
history, facts, reminders, and timezone, all keyed by Telegram user ID. One
person can never see or cancel another's data.

**Access control.** The first `AUTO_APPROVE_LIMIT` people (default 50) are
approved automatically. After that, newcomers get an "invite-only" reply and
land in a queue; you get a Telegram notification and approve with
`/approve <id>`. You are always approved, so a full queue can't lock you out.

**Free-tier guards — this is the part that matters.** Headcount is not the
real constraint; Gemini calls per day are. Every message costs **2 API calls**
(reply + analysis), so 50 chatty users would exhaust the free quota long
before you noticed. Two ceilings prevent that:

| Setting | Default | What it does |
|---|---|---|
| `USER_DAILY_MESSAGE_LIMIT` | 30 | One person can't drain the day for everyone |
| `GLOBAL_DAILY_API_LIMIT` | 1000 | The whole bot stops before Google says 429 |

When a ceiling is reached the bot says so honestly ("I've used up my daily
quota — it resets in a few hours") instead of failing with a generic error.
The admin is exempt from the per-user cap but **not** the global one, since
that ceiling exists to protect the API key itself.

Set `GLOBAL_DAILY_API_LIMIT` below your key's real daily quota — check it at
[AI Studio](https://aistudio.google.com) — and leave margin.

> ⚠️ On the free tier, Google may use API data to improve its products. That's
> your call for your own chats; tell people before inviting them.

---

## Reading the stored chats

Everything lives in one SQLite file — `bot.db` — with four tables:

| Table | Holds |
|---|---|
| `users` | One row per person: name, persona, timezone, access status |
| `messages` | Every turn: `user_id`, `role` (user/assistant), `content`, timestamp |
| `user_facts` | Durable facts learned about each person |
| `reminders` | One row per nudge, grouped by `group_id` per commitment |

`tools/chats.py` reads it without you writing SQL. It opens the database
read-only, so it's safe to run while the bot is live:

```bash
python tools/chats.py stats               # overview: users, messages, usage
python tools/chats.py users               # everyone, with message counts
python tools/chats.py chat Priya          # one person's conversation
python tools/chats.py chat @priya_k       # by username
python tools/chats.py chat 987654321      # by Telegram ID
python tools/chats.py chat Priya -n 100   # last 100 messages
python tools/chats.py search "meeting"    # find messages by text
python tools/chats.py facts Priya         # what it remembers about them
python tools/chats.py reminders Priya
python tools/chats.py export Priya        # dump one chat to a .txt file
```

You don't need to know anyone's Telegram ID — run `users` to see everyone, or
just use their name. Names and usernames match case-insensitively and a prefix
is enough; an ambiguous name lists the candidates rather than guessing.

On the VM: `cd ~/GirlFriend-Bot && ./venv/bin/python tools/chats.py users`

Raw SQL still works if you prefer it:

```bash
sqlite3 bot.db "SELECT role, content FROM messages WHERE user_id=123 ORDER BY id DESC LIMIT 20;"
```

**Retention.** Only the last 20 messages per user are ever sent to the model,
so history never slows the bot down or costs more as it grows. Older rows are
kept purely so you can read them, and pruned nightly to
`MESSAGE_RETENTION_PER_USER` (default 400). Measured growth is ~349 bytes per
message — about 49 MB/year for 10 active users, against a 47 GB disk.

---

## Troubleshooting

**Bot doesn't respond?** → Check `TELEGRAM_BOT_TOKEN` in `.env`.

**Gemini errors?** → Check `GEMINI_API_KEY`. On a rate limit the client falls
back down its model list automatically; persistent 429s mean the daily free
quota is spent.

**Reminders at the wrong time?** → Run `/timezone` to see what it thinks your
timezone is. Reminders already scheduled keep their original times.

**Reminders not firing?** → The bot has to be running when the time comes. Check
the startup log for `Restored reminders — N upcoming`.

**Want to reset everything?** → Delete `bot.db` and restart.

---

## Not built yet

Google Calendar sync, voice messages, and recurring reminders ("every Monday")
are all still on the roadmap — see `Bot.md` for the full plan.
