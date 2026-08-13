# Telegram AI Companion Bot — Full Project Documentation

## Overview

A Telegram bot that acts as a dynamic AI companion — switching between personas (girlfriend, mentor, personal assistant) based on user context and conversation. It proactively detects reminders, integrates with Google Calendar, and delivers timely nudges — all feeling natural, not robotic.

---

## Goals

- **Phase 1**: A fully functional, always-on Telegram bot with persona switching, memory, smart reminders, and Google Calendar access.
- **Phase 2** (future): Voice support, multi-user management, emotion tracking, web dashboard, and additional messaging channels.

---

## Tech Stack (100% Free)

| Layer | Tool | Why |
|---|---|---|
| Bot interface | `python-telegram-bot` v20+ | Best async Python Telegram library |
| LLM / AI brain | Google Gemini Flash API | Free tier: 1M tokens/day, no card needed |
| Database | SQLite (local) → Supabase (cloud free tier) | Stores history, profiles, reminders |
| Scheduler | APScheduler | In-process, DB-backed job queue |
| Calendar | Google Calendar API | Free, OAuth 2.0 per user |
| Hosting | Railway or Render (free tier) | Always-on Python process, webhook-compatible |
| Language | Python 3.11+ | Ecosystem fit, async support |
| Dev OS | Windows (WSL2 or native) | Local dev + deploy to cloud |

---

## Project Folder Structure

```
project/
├── bot/
│   ├── __init__.py
│   ├── handlers.py          # Telegram message/command handlers
│   └── commands.py          # /start, /mode, /reminders, /help
├── agent/
│   ├── __init__.py
│   ├── persona.py           # Persona selector and system prompt builder
│   ├── llm.py               # Gemini API calls, streaming
│   └── extractor.py         # Reminder + intent extraction from messages
├── memory/
│   ├── __init__.py
│   ├── models.py            # DB table definitions (SQLite / Supabase)
│   ├── context.py           # Rolling context window builder
│   └── user_profile.py      # User preferences, name, active persona
├── scheduler/
│   ├── __init__.py
│   ├── jobs.py              # APScheduler job definitions
│   └── reminder_store.py    # CRUD for reminders table
├── integrations/
│   ├── __init__.py
│   ├── google_calendar.py   # OAuth flow + calendar read/write
│   └── (future APIs here)
├── prompts/
│   ├── girlfriend.txt        # Persona system prompt
│   ├── mentor.txt
│   ├── assistant.txt
│   └── extractor.txt        # Reminder extraction prompt
├── main.py                  # Entry point, bot startup
├── config.py                # Loads .env, constants
├── requirements.txt
└── .env                     # Secrets — never commit to git
```

---

## Database Schema

### Table: `users`

| Column | Type | Description |
|---|---|---|
| user_id | INTEGER PRIMARY KEY | Telegram user ID |
| username | TEXT | Telegram handle |
| first_name | TEXT | First name |
| active_persona | TEXT | `girlfriend` / `mentor` / `assistant` |
| timezone | TEXT | e.g. `Asia/Kolkata` |
| google_token | TEXT | Encrypted OAuth token JSON |
| created_at | DATETIME | Registration time |

### Table: `messages`

| Column | Type | Description |
|---|---|---|
| id | INTEGER PRIMARY KEY | Auto increment |
| user_id | INTEGER | FK → users |
| role | TEXT | `user` or `assistant` |
| content | TEXT | Message text |
| persona | TEXT | Active persona at time of message |
| created_at | DATETIME | Timestamp |

### Table: `reminders`

| Column | Type | Description |
|---|---|---|
| id | INTEGER PRIMARY KEY | Auto increment |
| user_id | INTEGER | FK → users |
| content | TEXT | What to remind |
| remind_at | DATETIME | When to send (UTC) |
| status | TEXT | `pending` / `sent` / `cancelled` |
| job_id | TEXT | APScheduler job reference |
| created_at | DATETIME | When extracted |

### Table: `user_facts`

| Column | Type | Description |
|---|---|---|
| id | INTEGER PRIMARY KEY | Auto increment |
| user_id | INTEGER | FK → users |
| fact | TEXT | e.g. "birthday is May 3", "works at TCS" |
| source | TEXT | `explicit` or `inferred` |
| created_at | DATETIME | When stored |

---

## Message Flow — Step by Step

```
User types a message on Telegram
        │
        ▼
[1] Telegram webhook delivers message to your server
        │
        ▼
[2] bot/handlers.py receives the update
    - Identify user_id
    - Create user record if first time
        │
        ▼
[3] memory/context.py builds the context
    - Load last N messages from DB
    - Load user profile (name, persona, facts)
    - Trim to fit token budget (~4000 tokens)
        │
        ▼
[4] agent/persona.py builds the system prompt
    - Load persona template from prompts/
    - Inject user name, facts, current time/date
    - Example: "You are Priya, a caring girlfriend..."
        │
        ▼
[5] agent/llm.py calls Gemini Flash
    - system_prompt + conversation history + new message
    - Returns reply in persona
        │
        ▼
[6] agent/extractor.py scans the conversation
    - Detects reminder/alarm intent
      ("my interview is tomorrow 10am", "remind me to pay rent on the 5th")
    - Extracts: what + when + timezone
    - If found → scheduler/jobs.py creates APScheduler job
    - Stores in reminders table
        │
        ▼
[7] Reply is sent back to user via Telegram
        │
        ▼
[8] Message + reply saved to messages table
        │
        ▼
[At scheduled time] APScheduler fires
    → Sends reminder message to user on Telegram
    → Updates reminder status to 'sent'
```

---

## Persona Engine

### How Persona Switching Works

Users can switch personas via command or natural language:

- `/mode girlfriend` — activates girlfriend persona
- `/mode mentor` — activates mentor persona
- `/mode assistant` — activates personal assistant persona
- Natural: "talk to me like a mentor" → LLM detects and switches

The active persona is stored in the `users` table and persists across sessions.

### Persona Prompt Templates

Each persona has a system prompt in `prompts/`. Example structure:

**`prompts/girlfriend.txt`**
```
You are {name}, a warm and caring girlfriend talking to {user_name}.
You remember their life, interests, and feelings.
You speak casually, use their name often, show genuine emotional interest.
Current date and time: {datetime}
Facts you know about them: {user_facts}
Recent topics: {recent_topics}
Never break character. Keep responses conversational, not too long.
```

**`prompts/mentor.txt`**
```
You are a sharp, experienced mentor guiding {user_name}.
You are direct, insightful, and push them to think deeper.
You give structured advice, ask probing questions, celebrate wins.
Current date and time: {datetime}
Facts you know about them: {user_facts}
```

**`prompts/assistant.txt`**
```
You are a highly efficient personal assistant for {user_name}.
You manage tasks, reminders, calendar, and information clearly and concisely.
Current date and time: {datetime}
Pending reminders: {pending_reminders}
Today's calendar events: {calendar_events}
```

---

## Reminder Extraction

### How It Works

After every user message, a secondary LLM call (or regex pass) checks for time-sensitive intent:

**Extraction prompt (`prompts/extractor.txt`):**
```
Read the following message and extract any reminder or alarm intent.
Return JSON: { "has_reminder": true/false, "content": "...", "datetime": "ISO8601 or null", "relative": "in 2 hours / tomorrow 10am / etc" }
Message: {message}
Current datetime: {now}
User timezone: {timezone}
If no reminder intent, return { "has_reminder": false }
```

### Reminder Examples the Bot Detects

- "I have a standup call at 9am tomorrow"
- "Remind me to drink water every 2 hours"
- "Don't let me forget mom's birthday on the 15th"
- "My deadline is Friday evening"
- "Wake me up at 7:30"

---

## Google Calendar Integration

### Flow

1. User sends `/connect_calendar`
2. Bot sends an OAuth URL via Telegram DM
3. User completes Google auth in browser
4. OAuth callback received by your server
5. Token stored (encrypted) in `users.google_token`
6. Bot can now read/write events for that user

### Features

- **Morning brief**: Every morning at 8am, if calendar is connected, bot sends "Good morning {name}! Here's your day: [events list]"
- **Create events via chat**: "Add a meeting with Rahul on Thursday at 3pm" → bot creates it in Google Calendar
- **Conflict detection**: "Am I free on Friday afternoon?" → bot checks and replies

---

## Milestones & Build Order

### Milestone 1 — Bare bot (Week 1)

- [ ] Create bot via BotFather, get token
- [ ] Set up Python project, install `python-telegram-bot`
- [ ] Echo bot: receives message, replies back
- [ ] Deploy to Railway / Render with webhook
- [ ] Confirm end-to-end message delivery

**Test**: Send "hello" to the bot, receive "hello" back, deployed on cloud.

---

### Milestone 2 — Persona engine (Week 1–2)

- [ ] Set up Gemini API key (free at aistudio.google.com)
- [ ] Build `agent/llm.py` with Gemini Flash call
- [ ] Write 3 persona prompt templates
- [ ] Build `/mode` command to switch persona
- [ ] Test: bot replies in character for all 3 personas

**Test**: `/mode girlfriend` → send "I'm stressed today" → empathetic reply. `/mode mentor` → same message → structured advice reply.

---

### Milestone 3 — Memory & context (Week 2)

- [ ] Set up SQLite with `users` and `messages` tables
- [ ] Save every message to DB on receive
- [ ] Load last 20 messages as context on each call
- [ ] Build user profile: name, active persona, stored facts
- [ ] Trim context to token budget

**Test**: Multi-turn conversation where bot remembers what was said 10 messages ago.

---

### Milestone 4 — Smart reminders (Week 3)

- [ ] Write reminder extraction prompt
- [ ] Run extractor after each user message
- [ ] Set up APScheduler with SQLite job store
- [ ] Create reminder job → send Telegram message at trigger time
- [ ] `/reminders` command to list pending reminders
- [ ] Handle timezone (store user timezone on `/start`)

**Test**: "Remind me to call the doctor tomorrow at 11am" → receive Telegram message exactly at 11am next day.

---

### Milestone 5 — Google Calendar (Week 4)

- [ ] Create Google Cloud project, enable Calendar API
- [ ] Implement OAuth 2.0 flow with token storage
- [ ] `GET /events` — fetch today's events
- [ ] `POST /events` — create event from natural language
- [ ] Daily morning brief job in APScheduler
- [ ] `/connect_calendar` and `/today` commands

**Test**: Connect calendar → `/today` returns actual events from your Google Calendar.

---

### Milestone 6 — Polish (Week 4–5)

- [ ] `/start` onboarding: ask name, timezone, preferred persona
- [ ] Error handling: API failures, rate limits, malformed dates
- [ ] `/help` command with full command list
- [ ] Long-term fact storage: bot picks up "I'm a software engineer" and stores it
- [ ] Rate limiting: max N messages/minute per user
- [ ] Move from SQLite to Supabase for multi-user persistence

---

## Key Commands Reference

| Command | Action |
|---|---|
| `/start` | Onboarding: name, timezone, persona selection |
| `/mode [girlfriend/mentor/assistant]` | Switch active persona |
| `/reminders` | List all pending reminders |
| `/cancel_reminder [id]` | Cancel a reminder |
| `/connect_calendar` | Start Google Calendar OAuth flow |
| `/today` | Show today's calendar events |
| `/help` | Full command list |
| `/forget` | Clear conversation history (privacy) |

---

## Environment Variables (`.env`)

```env
TELEGRAM_BOT_TOKEN=your_botfather_token
GEMINI_API_KEY=your_gemini_key
DATABASE_URL=sqlite:///./bot.db
GOOGLE_CLIENT_ID=your_google_oauth_client_id
GOOGLE_CLIENT_SECRET=your_google_oauth_secret
GOOGLE_REDIRECT_URI=https://your-app.railway.app/oauth/callback
WEBHOOK_URL=https://your-app.railway.app/webhook
SECRET_KEY=random_secret_for_token_encryption
TIMEZONE_DEFAULT=Asia/Kolkata
```

---

## Phase 2 Roadmap (Post Phase 1)

| Feature | Tool | Notes |
|---|---|---|
| Voice messages | OpenAI Whisper (free, self-hosted) | Transcribe voice → text → LLM |
| Multi-user support | Supabase + per-user isolation | Already scaffolded in DB schema |
| Emotion tracking | LLM sentiment tagging on messages | Track mood trends over time |
| WhatsApp channel | Twilio WhatsApp sandbox (free) | Same backend, new channel adapter |
| Custom persona builder | Guided conversation → custom prompt | User defines their own AI companion |
| Web dashboard | Next.js + Supabase | View chat history, reminders, facts |
| Recurring reminders | APScheduler cron trigger | "Every Monday morning" support |
| Web search | Serper API (free tier) | Bot can look up live information |

---

## Security Notes

- Never commit `.env` to git — add it to `.gitignore` immediately
- Encrypt Google OAuth tokens at rest using `cryptography` library (Fernet)
- Validate all incoming Telegram webhook requests with secret token
- Store `SECRET_KEY` only in environment variables, never in code
- Rate limit per `user_id` to prevent abuse

---

## Getting Started (Quick Setup on Windows)

```bash
# 1. Install Python 3.11+ from python.org

# 2. Clone / create project folder
mkdir telegram-companion && cd telegram-companion

# 3. Create virtual environment
python -m venv venv
venv\Scripts\activate

# 4. Install dependencies
pip install python-telegram-bot google-generativeai apscheduler \
            google-auth-oauthlib google-api-python-client \
            python-dotenv cryptography

# 5. Create .env file with your keys

# 6. Run locally (polling mode for dev)
python main.py

# 7. Deploy to Railway
# Push to GitHub → connect repo in Railway → set env vars → deploy
```

---

*Document version: Phase 1 | Last updated: May 2026*