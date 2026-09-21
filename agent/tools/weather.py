"""
agent/tools/weather.py — Weather via Open-Meteo (free, no API key).

Location comes from, in order:
  1. a place the user names ("weather in Mumbai") -> geocoded
  2. the location they shared with the bot (saved, rounded to ~1 km)
  3. neither -> ask them to share it (needs_location)

Forecasts are cached per ~1 km cell for 15 minutes, so a burst of questions
from one city costs one upstream request.

Besides the Gemini tool, this module exposes plain helpers (geocode,
reverse_geocode, get_forecast, describe) used by /weather and the
share-location flow, which answer without spending any Gemini calls.
"""
import logging
import time

from agent.tools.base import Tool, ToolContext, ToolResult, http_client
from agent.tools.registry import register

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
REVERSE_URL = "https://api.bigdatacloud.net/data/reverse-geocode-client"

CACHE_SECONDS = 15 * 60
_cache: dict[tuple[float, float], tuple[float, dict]] = {}

# WMO weather interpretation codes, as used by Open-Meteo.
WMO = {
    0: ("clear sky", "☀️"), 1: ("mainly clear", "🌤️"), 2: ("partly cloudy", "⛅"),
    3: ("overcast", "☁️"), 45: ("fog", "🌫️"), 48: ("freezing fog", "🌫️"),
    51: ("light drizzle", "🌦️"), 53: ("drizzle", "🌦️"), 55: ("heavy drizzle", "🌧️"),
    56: ("freezing drizzle", "🌧️"), 57: ("freezing drizzle", "🌧️"),
    61: ("light rain", "🌦️"), 63: ("rain", "🌧️"), 65: ("heavy rain", "🌧️"),
    66: ("freezing rain", "🌧️"), 67: ("freezing rain", "🌧️"),
    71: ("light snow", "🌨️"), 73: ("snow", "🌨️"), 75: ("heavy snow", "❄️"), 77: ("snow grains", "🌨️"),
    80: ("light showers", "🌦️"), 81: ("showers", "🌧️"), 82: ("violent showers", "⛈️"),
    85: ("snow showers", "🌨️"), 86: ("heavy snow showers", "❄️"),
    95: ("thunderstorm", "⛈️"), 96: ("thunderstorm with hail", "⛈️"), 99: ("thunderstorm with hail", "⛈️"),
}


def condition(code) -> tuple[str, str]:
    return WMO.get(int(code) if code is not None else -1, ("unknown conditions", "🌡️"))


# ── Plain helpers ─────────────────────────────────────────────────────────────

async def geocode(place: str) -> dict | None:
    """'Mumbai' -> {latitude, longitude, name, timezone} or None if unknown."""
    async with http_client() as http:
        r = await http.get(GEOCODE_URL, params={"name": place, "count": 1, "language": "en"})
        r.raise_for_status()
        results = r.json().get("results") or []
    if not results:
        return None
    top = results[0]
    name = ", ".join(x for x in (top.get("name"), top.get("admin1"), top.get("country")) if x)
    return {
        "latitude": top["latitude"], "longitude": top["longitude"],
        "name": name, "timezone": top.get("timezone"),
    }


async def reverse_geocode(latitude: float, longitude: float) -> str:
    """Coordinates -> 'Hyderabad, Telangana, India'. Falls back to coordinates."""
    try:
        async with http_client() as http:
            r = await http.get(REVERSE_URL, params={
                "latitude": latitude, "longitude": longitude, "localityLanguage": "en"})
            r.raise_for_status()
            j = r.json()
        parts = [j.get("city") or j.get("locality"), j.get("principalSubdivision"), j.get("countryName")]
        name = ", ".join(p for p in parts if p)
        if name:
            return name
    except Exception as exc:
        logger.info(f"Reverse geocoding failed: {type(exc).__name__}")
    return f"{latitude:.2f}, {longitude:.2f}"


async def get_forecast(latitude: float, longitude: float) -> dict:
    """Current conditions + 7-day forecast for a location (cached 15 min)."""
    key = (round(latitude, 2), round(longitude, 2))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]

    async with http_client() as http:
        r = await http.get(FORECAST_URL, params={
            "latitude": key[0], "longitude": key[1],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                       "weather_code,wind_speed_10m,precipitation",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max,precipitation_sum",
            "timezone": "auto",
            "forecast_days": 7,
        })
        r.raise_for_status()
        data = r.json()

    _cache[key] = (time.monotonic(), data)
    if len(_cache) > 500:                       # bound memory on a 1 GB VM
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
    return data


def summarise(forecast: dict, place: str, when: str = "now") -> dict:
    """Reduce a raw forecast to the handful of facts Gemini needs."""
    cur, daily = forecast.get("current", {}), forecast.get("daily", {})

    def day(i: int) -> dict:
        return {
            "date": daily["time"][i],
            "condition": condition(daily["weather_code"][i])[0],
            "high_c": daily["temperature_2m_max"][i],
            "low_c": daily["temperature_2m_min"][i],
            "rain_chance_percent": daily["precipitation_probability_max"][i],
            "rain_mm": daily["precipitation_sum"][i],
        }

    out = {"place": place, "timezone": forecast.get("timezone")}
    if when in ("now", "today"):
        out["now"] = {
            "temperature_c": cur.get("temperature_2m"),
            "feels_like_c": cur.get("apparent_temperature"),
            "condition": condition(cur.get("weather_code"))[0],
            "humidity_percent": cur.get("relative_humidity_2m"),
            "wind_kmh": cur.get("wind_speed_10m"),
        }
        out["today"] = day(0)
    elif when == "tomorrow":
        out["tomorrow"] = day(1)
    else:  # week
        out["days"] = [day(i) for i in range(min(7, len(daily.get("time", []))))]
    return out


def describe(forecast: dict, place: str) -> str:
    """Plain-text weather card, for replies that don't go through Gemini."""
    cur, daily = forecast.get("current", {}), forecast.get("daily", {})
    text, icon = condition(cur.get("weather_code"))
    lines = [
        f"{icon} {place}",
        f"Now: {cur.get('temperature_2m')}°C, {text} (feels like {cur.get('apparent_temperature')}°C)",
    ]
    if daily.get("time"):
        lines.append(
            f"Today: {daily['temperature_2m_min'][0]}–{daily['temperature_2m_max'][0]}°C, "
            f"{daily['precipitation_probability_max'][0]}% chance of rain"
        )
    if len(daily.get("time", [])) > 1:
        t_text, t_icon = condition(daily["weather_code"][1])
        lines.append(
            f"Tomorrow: {t_icon} {t_text}, {daily['temperature_2m_min'][1]}–"
            f"{daily['temperature_2m_max'][1]}°C, {daily['precipitation_probability_max'][1]}% rain"
        )
    return "\n".join(lines)


# ── Gemini tool ───────────────────────────────────────────────────────────────

async def _get_weather(ctx: ToolContext, args: dict) -> ToolResult:
    when = args.get("when") or "now"
    place = args.get("place")

    if place:
        found = await geocode(place)
        if not found:
            return ToolResult.error(f"Couldn't find a place called '{place}'.")
        lat, lon, name = found["latitude"], found["longitude"], found["name"]
    elif ctx.profile.get("latitude") is not None:
        lat, lon = ctx.profile["latitude"], ctx.profile["longitude"]
        name = ctx.profile.get("location_name") or "your area"
    else:
        return ToolResult(
            ok=False, needs_location=True,
            data={"error": "The user hasn't shared their location yet."},
        )

    forecast = await get_forecast(lat, lon)
    return ToolResult(ok=True, data=summarise(forecast, name, when))


register(Tool(
    name="get_weather",
    description=(
        "Get the weather forecast. Use when the user asks about weather, rain, "
        "temperature, or whether to take an umbrella or jacket. Leave 'place' "
        "empty to use the user's own saved location."
    ),
    parameters={
        "type": "object",
        "properties": {
            "place": {"type": "string", "maxLength": 100,
                      "description": "A city or place name, only if the user named one."},
            "when": {"type": "string", "enum": ["now", "today", "tomorrow", "week"],
                     "description": "Which forecast the user wants. Default: now."},
        },
    },
    handler=_get_weather,
    timeout=20,
))
