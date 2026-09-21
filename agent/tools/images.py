"""
agent/tools/images.py — Find real photos, or generate new images.

find_photo      Openverse (free, no key): openly-licensed photos from Flickr,
                Wikimedia and others. Mature content is excluded at the API,
                and the creator + licence go in the caption, as the licences
                require.

generate_image  1. Gemini's image model, when the key includes it. A refusal
                   trips a cooldown so it isn't retried on every request.
                2. Pollinations.ai (free, no key), with its safety filter on.
                   Note: the prompt is sent to a third party.

Both refuse sexual content and anything involving minors, before any request
leaves the server. Found photos are sent to Telegram by URL (Telegram fetches
them, not us); generated images are held in memory only long enough to send,
and never written to disk.
"""
import logging
import re
import time
from urllib.parse import quote

from google.genai import types as genai_types

from agent.client import IMAGE_MODELS, generate_response, is_rate_limited
from agent.tools.base import (
    MAX_DOWNLOAD_BYTES, Attachment, Tool, ToolContext, ToolResult, http_client,
)
from agent.tools.registry import register
from config import IMAGE_DAILY_LIMIT, PHOTO_DAILY_LIMIT
from memory.usage import global_budget_left

logger = logging.getLogger(__name__)

OPENVERSE_URL = "https://api.openverse.org/v1/images/"
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"

GEMINI_IMAGE_COOLDOWN = 6 * 60 * 60
_gemini_image_blocked_until = 0.0

# Checked before anything is sent anywhere. Deliberately blunt: a false
# refusal costs a user one retry with different wording; a false pass on a
# companion bot costs far more.
_BLOCKED = re.compile(
    r"\b(nude|nudity|naked|nsfw|porn\w*|sex|sexual|sexy|erotic\w*|explicit|lingerie|"
    r"topless|bikini|undress\w*|fetish|hentai|onlyfans|boobs?|breasts?|genital\w*|"
    r"child|children|kid|kids|minor|minors|teen|teenage\w*|underage|loli|schoolgirl|"
    r"gore|beheading|dismember\w*)\b",
    re.IGNORECASE,
)

REFUSAL = "I can't make or share that kind of image. Try asking for something else."


def is_blocked(text: str) -> bool:
    return bool(_BLOCKED.search(text or ""))


# ── find_photo ────────────────────────────────────────────────────────────────

async def find_photo(query: str) -> dict | None:
    """Best openly-licensed photo for `query`, or None."""
    async with http_client() as http:
        r = await http.get(OPENVERSE_URL, params={
            "q": query, "page_size": 5, "mature": "false",
            "extension": "jpg,jpeg,png,webp",
        })
        r.raise_for_status()
        results = r.json().get("results") or []
    for item in results:
        if item.get("url") and not is_blocked(item.get("title", "")):
            return item
    return None


def photo_caption(item: dict) -> str:
    title = (item.get("title") or "Photo").strip()[:80]
    creator = (item.get("creator") or "unknown").strip()[:60]
    licence = f"{(item.get('license') or '').upper()} {item.get('license_version') or ''}".strip()
    return f"📷 {title}\nby {creator} · {licence} · via Openverse"


async def _find_photo(ctx: ToolContext, args: dict) -> ToolResult:
    query = args["query"]
    if is_blocked(query):
        return ToolResult.error(REFUSAL)
    item = await find_photo(query)
    if not item:
        return ToolResult.error(f"Couldn't find a photo of '{query}'.")
    return ToolResult(
        ok=True,
        data={"found": True, "title": item.get("title", "")[:80], "creator": item.get("creator")},
        attachments=[Attachment(kind="photo", caption=photo_caption(item),
                                url=item["url"], fallback_url=item.get("thumbnail"))],
    )


# ── generate_image ────────────────────────────────────────────────────────────

async def _generate_gemini(prompt: str) -> bytes | None:
    global _gemini_image_blocked_until
    if time.monotonic() < _gemini_image_blocked_until:
        return None
    budget = await global_budget_left()
    if budget is not None and budget < 1:
        return None      # keyless fallback costs no Gemini quota
    config = genai_types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"])
    try:
        response = await generate_response(prompt, config, IMAGE_MODELS)
    except Exception as exc:
        if is_rate_limited(exc):
            _gemini_image_blocked_until = time.monotonic() + GEMINI_IMAGE_COOLDOWN
            logger.info("Gemini image generation unavailable on this key; skipping it for 6h")
        else:
            logger.info(f"Gemini image generation failed: {type(exc).__name__}")
        return None
    for part in response.candidates[0].content.parts or []:
        blob = getattr(part, "inline_data", None)
        if blob and blob.data and (blob.mime_type or "").startswith("image/"):
            return blob.data
    return None


async def _generate_pollinations(prompt: str) -> bytes | None:
    url = POLLINATIONS_URL + quote(prompt, safe="")
    async with http_client() as http:
        async with http.stream("GET", url, params={
            "width": 1024, "height": 1024, "nologo": "true", "safe": "true",
        }, timeout=90) as r:
            r.raise_for_status()
            if not r.headers.get("content-type", "").startswith("image/"):
                logger.info("Pollinations returned a non-image response")
                return None
            chunks, size = [], 0
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    logger.warning("Generated image exceeded the size cap; discarded")
                    return None
                chunks.append(chunk)
    return b"".join(chunks) or None


async def generate_image(prompt: str) -> tuple[bytes | None, str, int]:
    """Returns (image_bytes, provider, gemini_calls_spent)."""
    data = await _generate_gemini(prompt)
    if data:
        return data, "gemini", 1
    data = await _generate_pollinations(prompt)
    return data, "pollinations", 0


async def _generate_image(ctx: ToolContext, args: dict) -> ToolResult:
    prompt = args["prompt"]
    if is_blocked(prompt):
        return ToolResult.error(REFUSAL)
    data, provider, calls = await generate_image(prompt)
    if not data:
        return ToolResult(ok=False, gemini_calls=calls,
                          data={"error": "The image couldn't be created right now."})
    return ToolResult(
        ok=True, gemini_calls=calls,
        data={"created": True, "description": prompt[:120]},
        attachments=[Attachment(kind="photo", caption="🎨 Made for you", data=data)],
    )


register(Tool(
    name="find_photo",
    description=(
        "Find a real photograph of something (a place, animal, object, food, landmark). "
        "Use when the user wants to SEE a real thing. Never for images of real people."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 100,
                      "description": "What to find, in a few English words."},
        },
        "required": ["query"],
    },
    handler=_find_photo,
    daily_limit=lambda: PHOTO_DAILY_LIMIT,
    timeout=30,
))

register(Tool(
    name="generate_image",
    description=(
        "Create a new illustration or artwork from a description. Use when the user asks "
        "you to draw, create, generate or imagine a picture. Never create sexual content, "
        "images of children, or images of real, identifiable people."
    ),
    parameters={
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "maxLength": 300,
                       "description": "A vivid, detailed English description of the image."},
        },
        "required": ["prompt"],
    },
    handler=_generate_image,
    daily_limit=lambda: IMAGE_DAILY_LIMIT,
    timeout=100,
))
