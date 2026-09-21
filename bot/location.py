"""
bot/location.py — The "📍 Share my location" keyboard.

Kept separate from the command handlers so bot/handlers.py can offer the
button (when the weather tool needs a location) without an import cycle.
"""
from telegram import KeyboardButton, ReplyKeyboardMarkup

SHARE_LOCATION_TEXT = "📍 Share my location"


def location_keyboard() -> ReplyKeyboardMarkup:
    """
    One-tap location sharing. Telegram asks the user for permission first;
    the bot never sees a location unless they tap and agree. Desktop clients
    can't share location, which is why /location <city> exists too.
    """
    return ReplyKeyboardMarkup(
        [[KeyboardButton(SHARE_LOCATION_TEXT, request_location=True)]],
        one_time_keyboard=True,
        resize_keyboard=True,
        input_field_placeholder="Or type /location <your city>",
    )
