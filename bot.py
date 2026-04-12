import json
import requests
import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from personality import get_prompt

TOKEN = os.getenv("BOT_TOKEN")
HF_API_KEY = os.getenv("HF_API_KEY")

mode = "girlfriend"

# Load memory
def load_memory():
    try:
        with open("memory.json", "r") as f:
            return json.load(f)
    except:
        return {}

def save_memory(memory):
    with open("memory.json", "w") as f:
        json.dump(memory, f)

memory = load_memory()

# HuggingFace API call
def get_ai_response(prompt):
    url = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.1"

    headers = {
        "Authorization": f"Bearer {HF_API_KEY}"
    }

    payload = {
        "inputs": prompt
    }

    response = requests.post(url, headers=headers, json=payload)

    try:
        result = response.json()
        return result[0]["generated_text"].split("Reply:")[-1].strip()
    except:
        return "Hmm… I'm here… tell me again?"

# Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hey… I'm here for you ❤️")

async def set_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global mode
    if context.args:
        mode = context.args[0]
        await update.message.reply_text(f"Mode changed to {mode}")
    else:
        await update.message.reply_text("Use /mode girlfriend | sister | caretaker")

# Message handler
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.chat_id)
    user_text = update.message.text

    memory[user_id] = memory.get(user_id, []) + [user_text]
    save_memory(memory)

    prompt = get_prompt(mode, user_text, memory[user_id][-5:])

    reply = get_ai_response(prompt)

    await asyncio.sleep(1.5)  # makes it feel human
    await update.message.reply_text(reply)

# App setup
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("mode", set_mode))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# GitHub Actions runner
async def run_bot():
    await app.initialize()
    await app.start()
    print("Bot running...")

    await asyncio.sleep(600)  # run for 10 minutes

    await app.stop()
    await app.shutdown()

if __name__ == "__main__":
    asyncio.run(run_bot())