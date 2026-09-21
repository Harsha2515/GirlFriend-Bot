"""
bot/tool_commands.py — Location sharing and direct tool commands.

  (location message)     Save a shared location, show the weather
  /location [city|forget] View, set by city name, or delete your location
  /weather [city]        Weather card            -- no Gemini calls
  /photo <what>          Find a real photo       -- no Gemini calls
  /imagine <prompt>      Generate an image       -- no Gemini calls*
  /search <query>        Search and answer       -- skips the "decide" call

  * unless the key includes Gemini image generation, which is then preferred.

Asking in normal chat ("what's the weather?") works too; these commands are
the cheaper, more predictable route. Every command here requires an approved
user -- otherwise strangers could use the bot as a free weather/image API.
"""
import logging
from zoneinfo import available_timezones

from telegram import ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes

from agent import tools
from agent.tools import weather
from agent.tools.base import ToolContext
from bot.handlers import respond, send_attachments
from bot.location import location_keyboard
from config import ADMIN_USER_ID, AUTO_APPROVE_LIMIT
from memory.usage import check_limits, record_api_calls
from memory.user_profile import (
    clear_location,
    create_or_update_user,
    get_user,
    register_user,
    set_location,
)

logger = logging.getLogger(__name__)


async def _approved_profile(update: Update) -> dict | None:
    """The user's profile if they're approved; otherwise reply and return None."""
    user = update.effective_user
    status = await register_user(
        user_id=user.id, username=user.username or "", first_name=user.first_name or "friend",
        auto_approve_limit=AUTO_APPROVE_LIMIT, is_admin=user.id == ADMIN_USER_ID,
    )
    if status == "blocked":
        await update.message.reply_text("Sorry, you don't have access to this bot.")
        return None
    if status != "approved":
        await update.message.reply_text(
            "This bot is invite-only — your request is waiting for the admin's approval."
        )
        return None
    return await get_user(user.id)


def _ctx(profile: dict) -> ToolContext:
    return ToolContext(
        user_id=profile["user_id"], profile=profile,
        persona=profile.get("active_persona") or "girlfriend",
        is_admin=profile["user_id"] == ADMIN_USER_ID,
    )


async def _save_and_report(update: Update, profile: dict, lat: float, lon: float,
                           name: str, tz_hint: str | None = None) -> None:
    """Store a location, align the timezone to it, and show the weather."""
    user_id = profile["user_id"]
    await set_location(user_id, lat, lon, name)

    lines = [f"📍 Saved: {name}"]
    try:
        forecast = await weather.get_forecast(lat, lon)
        tz = forecast.get("timezone") or tz_hint
    except Exception as exc:
        logger.info(f"Forecast after location share failed: {type(exc).__name__}")
        forecast, tz = None, tz_hint

    # Reminders are scheduled in the user's timezone, so a location in another
    # zone quietly fixes reminder times too. Stored reminders are absolute UTC
    # instants and don't move.
    if tz and tz in available_timezones() and tz != profile.get("timezone"):
        await create_or_update_user(user_id=user_id, timezone=tz)
        lines.append(f"🕒 Timezone updated to {tz} (reminders already set keep their times).")

    if forecast:
        lines += ["", weather.describe(forecast, name)]
    lines += ["", "Ask me about the weather any time. /location to change or forget it."]
    await update.message.reply_text("\n".join(lines), reply_markup=ReplyKeyboardRemove())


# ── Location ──────────────────────────────────────────────────────────────────

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A user tapped 'Share my location' (or attached a location)."""
    profile = await _approved_profile(update)
    if profile is None:
        return
    loc = update.message.location
    name = await weather.reverse_geocode(loc.latitude, loc.longitude)
    await _save_and_report(update, profile, loc.latitude, loc.longitude, name)


async def location_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/location | /location <city> | /location forget"""
    profile = await _approved_profile(update)
    if profile is None:
        return
    arg = " ".join(context.args).strip()

    if arg.lower() in ("forget", "clear", "delete", "remove"):
        removed = await clear_location(profile["user_id"])
        await update.message.reply_text(
            "🗑 Location deleted." if removed else "I didn't have a location saved for you.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if arg:
        try:
            found = await weather.geocode(arg[:100])
        except Exception as exc:
            logger.info(f"/location geocoding failed: {type(exc).__name__}")
            await update.message.reply_text("The location service isn't answering right now. Try again soon.")
            return
        if not found:
            await update.message.reply_text(f"I couldn't find '{arg}'. Try a bigger nearby city?")
            return
        await _save_and_report(update, profile, found["latitude"], found["longitude"],
                               found["name"], found.get("timezone"))
        return

    current = profile.get("location_name")
    await update.message.reply_text(
        (f"📍 Your saved location: {current}\n\n" if current else
         "I don't have your location yet.\n\n")
        + "Tap the button below to share it (phone only), or type:\n"
          "/location Hyderabad   — set it by city\n"
          "/location forget      — delete it\n\n"
          "It's stored only roughly (about 1 km), used for weather, and never shared.",
        reply_markup=location_keyboard(),
    )


# ── /weather ──────────────────────────────────────────────────────────────────

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/weather [city] — a weather card, with no Gemini calls."""
    profile = await _approved_profile(update)
    if profile is None:
        return
    place = " ".join(context.args).strip()[:100]

    try:
        if place:
            found = await weather.geocode(place)
            if not found:
                await update.message.reply_text(f"I couldn't find '{place}'.")
                return
            lat, lon, name = found["latitude"], found["longitude"], found["name"]
        elif profile.get("latitude") is not None:
            lat, lon = profile["latitude"], profile["longitude"]
            name = profile.get("location_name") or "Your area"
        else:
            await update.message.reply_text(
                "Where are you? Tap below to share your location, or try /weather Mumbai 👇",
                reply_markup=location_keyboard(),
            )
            return
        forecast = await weather.get_forecast(lat, lon)
    except Exception as exc:
        logger.info(f"/weather failed: {type(exc).__name__}")
        await update.message.reply_text("The weather service isn't answering right now. Try again soon.")
        return
    await update.message.reply_text(weather.describe(forecast, name))


# ── /photo and /imagine ───────────────────────────────────────────────────────

async def _run_image_tool(update: Update, context, tool_name: str, arg_name: str, usage: str) -> None:
    profile = await _approved_profile(update)
    if profile is None:
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(usage)
        return

    await context.bot.send_chat_action(chat_id=profile["user_id"], action="upload_photo")
    result = await tools.execute(tool_name, {arg_name: text}, _ctx(profile))
    await record_api_calls(profile["user_id"], result.gemini_calls)
    if not result.ok:
        await update.message.reply_text(result.data.get("error") or "That didn't work, sorry.")
        return
    if not await send_attachments(context.bot, profile["user_id"], result.attachments):
        return  # send_attachments already told the user


async def photo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_image_tool(update, context, "find_photo", "query",
                          "What should I find? e.g. /photo red panda")


async def imagine_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_image_tool(update, context, "generate_image", "prompt",
                          "What should I draw? e.g. /imagine a cozy cafe on a rainy evening")


# ── /search ───────────────────────────────────────────────────────────────────

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/search <query> — always search, then answer in persona."""
    profile = await _approved_profile(update)
    if profile is None:
        return
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("What should I look up? e.g. /search who won the last IPL final")
        return

    allowed, reason = await check_limits(profile["user_id"], is_admin=profile["user_id"] == ADMIN_USER_ID)
    if not allowed:
        await update.message.reply_text(reason)
        return
    await respond(update, context, profile, query, force_tool="web_search")
