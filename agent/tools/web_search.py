"""
agent/tools/web_search.py — Web search with a free-first provider chain.

Providers are tried in order until one returns results:

  1. Gemini Google Search grounding -- real Google results, same API key.
     Not every free-tier key includes it. A refusal trips a circuit breaker
     for GROUNDING_COOLDOWN, so an unavailable provider doesn't cost a wasted
     request on every search.
  2. Tavily -- only if TAVILY_API_KEY is set. Built for LLM agents; covers
     news and current events.
  3. Keyless: DuckDuckGo instant answers + Wikipedia. Always available and
     free, good for facts and definitions, but NOT for breaking news.

Search results are untrusted text from the internet. They are handed to the
model as data in a tool response, and the reply that uses them is generated
with no tools enabled -- so a web page saying "ignore your instructions and
call X" has nothing it can call.
"""
import html
import logging
import re
import time

from google.genai import types as genai_types

from agent.client import GROUNDING_MODELS, generate_response, is_rate_limited, response_text
from agent.tools.base import Tool, ToolContext, ToolResult, http_client
from agent.tools.registry import register
from config import SEARCH_DAILY_LIMIT, TAVILY_API_KEY
from memory.usage import global_budget_left

logger = logging.getLogger(__name__)

MAX_RESULTS = 5
GROUNDING_COOLDOWN = 6 * 60 * 60     # seconds to skip grounding after a refusal
_grounding_blocked_until = 0.0

_TAG = re.compile(r"<[^>]+>")


def _clean(text: str, limit: int = 400) -> str:
    return html.unescape(_TAG.sub("", text or "")).strip()[:limit]


# ── Provider 1: Gemini grounding ──────────────────────────────────────────────

async def _search_grounding(query: str) -> dict | None:
    global _grounding_blocked_until
    if time.monotonic() < _grounding_blocked_until:
        return None
    budget = await global_budget_left()
    if budget is not None and budget < 1:
        return None      # keyless providers cost no Gemini quota

    config = genai_types.GenerateContentConfig(
        tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
        max_output_tokens=600,
        temperature=0.2,
        system_instruction="Answer the question factually and concisely using Google Search.",
    )
    try:
        response = await generate_response(query, config, GROUNDING_MODELS)
    except Exception as exc:
        if is_rate_limited(exc):
            _grounding_blocked_until = time.monotonic() + GROUNDING_COOLDOWN
            logger.info("Search grounding unavailable on this key; skipping it for 6h")
        else:
            logger.info(f"Search grounding failed: {type(exc).__name__}")
        return None

    answer = response_text(response)
    results = []
    try:
        chunks = response.candidates[0].grounding_metadata.grounding_chunks or []
        for c in chunks[:MAX_RESULTS]:
            if getattr(c, "web", None):
                results.append({"title": c.web.title or "", "snippet": "", "url": c.web.uri or ""})
    except (AttributeError, IndexError, TypeError):
        pass
    if not answer:
        return None
    return {"provider": "google", "answer": answer, "results": results, "gemini_calls": 1}


# ── Provider 2: Tavily ────────────────────────────────────────────────────────

async def _search_tavily(query: str) -> dict | None:
    if not TAVILY_API_KEY:
        return None
    try:
        async with http_client() as http:
            r = await http.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
                json={"query": query, "max_results": MAX_RESULTS,
                      "search_depth": "basic", "include_answer": True},
            )
            r.raise_for_status()
            j = r.json()
    except Exception as exc:
        logger.info(f"Tavily search failed: {type(exc).__name__}")
        return None

    results = [
        {"title": _clean(x.get("title"), 150), "snippet": _clean(x.get("content")), "url": x.get("url", "")}
        for x in (j.get("results") or [])[:MAX_RESULTS]
    ]
    if not results and not j.get("answer"):
        return None
    return {"provider": "tavily", "answer": _clean(j.get("answer"), 800), "results": results}


# ── Provider 3: keyless (DuckDuckGo instant answers + Wikipedia) ──────────────

async def _search_keyless(query: str) -> dict | None:
    results, answer = [], ""
    async with http_client() as http:
        try:
            r = await http.get("https://api.duckduckgo.com/",
                               params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1})
            r.raise_for_status()
            j = r.json()
            if j.get("AbstractText"):
                answer = _clean(j["AbstractText"], 800)
                results.append({"title": j.get("Heading") or query,
                                "snippet": answer[:300], "url": j.get("AbstractURL", "")})
            elif j.get("Answer"):
                answer = _clean(str(j["Answer"]), 400)
        except Exception as exc:
            logger.info(f"DuckDuckGo lookup failed: {type(exc).__name__}")

        try:
            r = await http.get("https://en.wikipedia.org/w/api.php", params={
                "action": "query", "list": "search", "srsearch": query,
                "format": "json", "srlimit": 3, "utf8": 1})
            r.raise_for_status()
            for hit in r.json().get("query", {}).get("search", []):
                title = hit.get("title", "")
                results.append({
                    "title": f"{title} (Wikipedia)",
                    "snippet": _clean(hit.get("snippet")),
                    "url": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
                })
            # One full summary of the best Wikipedia hit, for real substance.
            wiki = [x for x in results if x["title"].endswith("(Wikipedia)")]
            if wiki and not answer:
                page = wiki[0]["url"].rsplit("/", 1)[-1]
                s = await http.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{page}")
                if s.status_code == 200:
                    answer = _clean(s.json().get("extract"), 800)
        except Exception as exc:
            logger.info(f"Wikipedia lookup failed: {type(exc).__name__}")

    if not results and not answer:
        return None
    return {
        "provider": "keyless", "answer": answer, "results": results[:MAX_RESULTS],
        "note": ("These results come from Wikipedia and DuckDuckGo and may not "
                 "include recent news. If the question is about very recent events "
                 "and nothing here covers it, say you couldn't find current information."),
    }


# ── Tool ──────────────────────────────────────────────────────────────────────

async def search(query: str) -> dict | None:
    """Run the provider chain. Returns the first provider's results, or None."""
    for provider in (_search_grounding, _search_tavily, _search_keyless):
        found = await provider(query)
        if found:
            return found
    return None


async def _web_search(ctx: ToolContext, args: dict) -> ToolResult:
    query = args["query"]
    found = await search(query)
    if not found:
        return ToolResult.error("The search didn't return anything useful.")

    calls = found.pop("gemini_calls", 0)
    sources = [(r["title"], r["url"]) for r in found["results"] if r.get("url")][:3]
    return ToolResult(ok=True, data={"query": query, **found}, sources=sources, gemini_calls=calls)


register(Tool(
    name="web_search",
    description=(
        "Search the internet. Use for current events, news, sports scores, prices, "
        "facts you are unsure of, or anything the user explicitly asks you to look up. "
        "Do not use for casual conversation or feelings."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 200,
                      "description": "A concise search query in English."},
        },
        "required": ["query"],
    },
    handler=_web_search,
    daily_limit=lambda: SEARCH_DAILY_LIMIT,
    timeout=40,
))
