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

# Words that say nothing about WHICH page is relevant. "Oppenheimer film" should
# match "Oppenheimer (film)" on "oppenheimer", not on "film" -- otherwise every
# film page would count as relevant to every film question.
_GENERIC = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or", "is", "are",
    "was", "who", "what", "when", "where", "why", "how", "tell", "me", "about",
    "search", "info", "information", "details", "movie", "movies", "film", "films",
    "series", "show", "song", "songs", "book", "person", "latest", "news", "list",
    "wikipedia", "soundtrack", "filmography", "discography",
}

# Words the model tacks onto queries ("Kalki 2898 AD movie info") that never
# appear on the page itself. Wikipedia search requires every word to match, so
# one of these is enough to return nothing at all. Stripped before searching.
_FILLER = {
    "info", "information", "details", "detail", "about", "tell", "me", "please",
    "link", "links", "source", "sources", "url", "urls", "website", "article",
    "articles", "review", "reviews", "facts", "fact", "summary", "overview",
    "bio", "biography", "wiki", "wikipedia", "search", "find", "look", "up",
    "some", "more", "latest", "news", "everything", "all", "know", "read",
    "and", "or",
}


def _query_variants(query: str) -> list[str]:
    """
    Queries to try, most specific first:
      'Kalki 2898 AD movie info' -> ['Kalki 2898 AD movie', 'Kalki 2898 AD']
    The first keeps words like 'movie' that help pick the right page among
    namesakes; the second is the bare subject, if the first finds nothing.
    """
    words = re.findall(r"[\w.'-]+", query)
    no_filler = [w for w in words if w.lower().strip(".") not in _FILLER]
    core = [w for w in no_filler if w.lower().strip(".") not in _GENERIC]
    variants = []
    for v in (" ".join(no_filler), " ".join(core), query.strip()):
        if v and v not in variants:
            variants.append(v)
    return variants


# How much of the best article's introduction to hand the model. The intro is
# where Wikipedia puts the key facts -- this is the "matter" of the answer.
INTRO_CHARS = 2500


def _tokens(text: str) -> set[str]:
    """Meaningful lowercase words: 'M.S. Dhoni' -> {'ms', 'dhoni'}."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower().replace(".", ""))
    return {w for w in words if w not in _GENERIC and w not in _FILLER}


def _relevance(query: str, title: str) -> float:
    """
    Share of a page title's meaningful words that also appear in the query.
    'MS Dhoni' vs 'MS Dhoni' = 1.0; vs 'M.S. Dhoni: The Untold Story' = 0.5;
    vs 'Seven (brand)' = 0.0.
    """
    q, t = _tokens(query), _tokens(title)
    if not q or not t:
        return 0.0
    return len(q & t) / len(t)


# IPA pronunciation blocks, e.g. "Dhoni ([məˈɦeːnd̪ɾə ˈsɪŋɡʱ] ; born 1981)".
# A bracketed block counts as pronunciation if it contains any character from
# the Unicode IPA / spacing-modifier / combining-mark ranges.
_IPA = re.compile(r"\[[^\]]*[ɐ-˿̀-ͯ][^\]]*\]\s*;?\s*")


def _strip_pronunciation(text: str) -> str:
    text = _IPA.sub("", text)
    text = re.sub(r"\(\s*\)", "", re.sub(r"\(\s+", "(", text))
    return re.sub(r" {2,}", " ", text)


async def _search_keyless(query: str) -> dict | None:
    """
    Wikipedia does the heavy lifting: find the page that actually matches, then
    fetch its full introduction. DuckDuckGo's instant answer is a supplement.
    Pages sharing no meaningful word with the query are dropped entirely.
    """
    candidates, ddg_answer = [], ""
    variants = _query_variants(query)
    async with http_client() as http:
        try:
            r = await http.get("https://api.duckduckgo.com/", params={
                "q": variants[0], "format": "json", "no_html": 1, "skip_disambig": 1})
            r.raise_for_status()
            j = r.json()
            ddg_answer = _clean(j.get("AbstractText") or str(j.get("Answer") or ""), 600)
        except Exception as exc:
            logger.info(f"DuckDuckGo lookup failed: {type(exc).__name__}")

        # Try the query variants in turn, stopping at the first that finds a
        # relevant page. Most queries resolve on the first attempt.
        for attempt in variants:
            try:
                r = await http.get("https://en.wikipedia.org/w/api.php", params={
                    "action": "query", "list": "search", "srsearch": attempt,
                    "format": "json", "srlimit": 6, "utf8": 1})
                r.raise_for_status()
                hits = r.json().get("query", {}).get("search", [])
            except Exception as exc:
                logger.info(f"Wikipedia search failed: {type(exc).__name__}")
                break
            for rank, hit in enumerate(hits):
                title = hit.get("title", "")
                score = _relevance(query, title)
                if score == 0.0:
                    continue          # e.g. 'Seven (brand)' for 'MS Dhoni'
                candidates.append({
                    "title": title, "score": score, "rank": rank,
                    "snippet": _clean(hit.get("snippet")),
                    "url": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
                })
            if candidates:
                break

        # Best match first; Wikipedia's own ranking breaks ties.
        candidates.sort(key=lambda c: (-c["score"], c["rank"]))

        intro = ""
        if candidates:
            try:
                r = await http.get("https://en.wikipedia.org/w/api.php", params={
                    "action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                    "redirects": 1, "titles": candidates[0]["title"], "format": "json"})
                r.raise_for_status()
                pages = r.json().get("query", {}).get("pages", {})
                intro = next(iter(pages.values()), {}).get("extract", "")
                intro = _strip_pronunciation(re.sub(r"\n{2,}", "\n", intro)).strip()[:INTRO_CHARS]
            except Exception as exc:
                logger.info(f"Wikipedia extract failed: {type(exc).__name__}")

    if not candidates and not ddg_answer:
        return None

    results = [{"title": c["title"], "snippet": c["snippet"], "url": c["url"]}
               for c in candidates[:MAX_RESULTS]]
    return {
        "provider": "keyless",
        "answer": intro or ddg_answer,
        "main_topic": candidates[0]["title"] if candidates else query,
        "results": results,
        "note": ("From Wikipedia and DuckDuckGo. Good for background facts, but may "
                 "not include very recent news or live scores -- if the question is "
                 "about something recent that isn't covered here, say so."),
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
        # Not a dead end for the user: the model is told to answer from what it
        # reliably knows, and to be upfront that the lookup came back empty.
        return ToolResult(ok=False, data={
            "error": "The search didn't find anything for this.",
            "instruction": ("Answer from your own knowledge if you reliably know the topic, "
                            "and mention briefly that you couldn't find more online. "
                            "If you don't know it, say so honestly."),
        })

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
