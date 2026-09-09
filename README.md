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
| `/help` | Show all commands |

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
└── scheduler/
    ├── jobs.py               # Lead-time planning, firing, restart recovery
    └── reminder_store.py     # Reminder CRUD + dedup
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
