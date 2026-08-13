# 💕 GirlFriend Bot

> A Telegram AI companion powered by **Google Gemini Flash** (free, 1M tokens/day).  
> Three personas: **Girlfriend**, **Mentor**, **Assistant** — with persistent memory, smart reminders, and natural conversation.

---

## Quick Start

### 1. Prerequisites
- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A Gemini API key from [Google AI Studio](https://aistudio.google.com) (free, no credit card)

### 2. Setup

```powershell
# Clone / open the project folder
cd "c:\Z - Work\GirlFriend-Bot"

# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure `.env`

Edit `.env` and paste your keys:

```env
TELEGRAM_BOT_TOKEN=<your BotFather token>
GEMINI_API_KEY=<your Gemini key>
DB_PATH=bot.db
TIMEZONE_DEFAULT=Asia/Kolkata
```

### 4. Run the bot

```powershell
python main.py
```

Open Telegram, find your bot, send `/start` — done! 🎉

---

## Commands

| Command | Description |
|---|---|
| `/start` | Onboarding + persona picker |
| `/mode girlfriend\|mentor\|assistant` | Switch persona |
| `/name <name>` | Set your preferred name |
| `/reminders` | List pending reminders |
| `/cancel <id>` | Cancel a reminder |
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

## Features

- **Multi-turn memory** — Remembers your last 20 messages per session
- **Long-term facts** — Picks up personal details from conversation
- **Smart reminders** — Detects "remind me to..." in natural chat and fires a Telegram message at the right time
- **Persona switching** — `/mode girlfriend` or just say "talk to me like a mentor"
- **SQLite storage** — All data in `bot.db` (auto-created, zero config)
- **Gemini Flash** — Fast, free (1M tokens/day on free tier)

---

## Project Structure

```
├── main.py              # Entry point — run this
├── config.py            # Loads .env, exports all config vars
├── requirements.txt
├── .env                 # Your secrets (never commit)
├── bot.db               # SQLite DB (auto-created)
│
├── agent/
│   ├── llm.py           # Gemini Flash API call with retry
│   ├── persona.py       # System prompt builder per persona
│   └── extractor.py     # Reminder intent extraction
│
├── bot/
│   ├── handlers.py      # Main message handler
│   └── commands.py      # All /commands
│
├── memory/
│   ├── models.py        # SQLite init + table definitions
│   ├── context.py       # Conversation history CRUD
│   └── user_profile.py  # User profile + facts CRUD
│
└── scheduler/
    ├── jobs.py          # APScheduler setup + reminder firing
    └── reminder_store.py # Reminder CRUD in SQLite
```

---

## Tech Stack

| Layer | Tool | Notes |
|---|---|---|
| Bot framework | `python-telegram-bot` v20 | Async, webhook-ready |
| LLM | Google Gemini Flash | Free 1M tokens/day |
| Database | SQLite (`bot.db`) | Zero config, local |
| Scheduler | APScheduler | In-process, async |
| Language | Python 3.11+ | |

---

## Troubleshooting

**Bot doesn't respond?**  
→ Check your `TELEGRAM_BOT_TOKEN` in `.env`

**Gemini errors?**  
→ Check your `GEMINI_API_KEY` — get a fresh one at [aistudio.google.com](https://aistudio.google.com)

**Reminders not firing?**  
→ The bot must be running when the reminder time comes. Keep `python main.py` open.

**Want to reset everything?**  
→ Delete `bot.db` and restart the bot.