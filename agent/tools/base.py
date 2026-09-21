"""
agent/tools/base.py — Shared types for the agent's tools.

A tool is a capability Gemini can ask for (web search, weather, photos, image
generation). Gemini only ever *requests* a tool by name with arguments; the
application decides whether to run it, runs it, and hands back a result.

Two rules every tool follows:

  * Identity comes from ToolContext, never from the model. The user a tool
    acts for is the Telegram user who sent the message -- a tool has no way to
    accept a user ID as an argument, so one user can never reach another's
    data through a tool call.
  * Tools only talk to fixed, known APIs. Nothing ever fetches a URL that
    came from the model or the user, so the bot can't be used to probe other
    hosts.
"""
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

# Wikimedia and others reject anonymous clients: a descriptive User-Agent
# with a contact URL is required by their API etiquette.
USER_AGENT = (
    "GirlFriendBot/1.0 (personal Telegram companion bot; "
    "https://github.com/Harsha2515/GirlFriend-Bot)"
)

HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# Largest download a tool will accept, e.g. a generated image.
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024


def http_client() -> httpx.AsyncClient:
    """A short-lived client with sane timeouts and our User-Agent."""
    return httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


@dataclass
class ToolContext:
    """Who a tool is acting for. Built by the application, never the model."""
    user_id: int
    profile: dict
    persona: str
    is_admin: bool = False


@dataclass
class Attachment:
    """Something to send to the user alongside the text reply."""
    kind: str                     # 'photo'
    caption: str = ""
    url: str | None = None        # sent by URL: Telegram fetches it, not us
    data: bytes | None = None     # or raw bytes we already hold
    fallback_url: str | None = None


@dataclass
class ToolResult:
    """
    What a tool hands back.

    `data` is what Gemini sees -- keep it small and never put anything
    sensitive in it. `attachments` go straight to Telegram. `sources` are
    appended to the reply by the application, so the model can't invent them.
    """
    ok: bool
    data: dict = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)
    sources: list[tuple[str, str]] = field(default_factory=list)   # (title, url)
    needs_location: bool = False
    gemini_calls: int = 0          # extra Gemini calls the tool itself made

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(ok=False, data={"error": message})


Handler = Callable[[ToolContext, dict], Awaitable[ToolResult]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]    # JSON-schema style: {"type": "object", ...}
    handler: Handler
    daily_limit: Callable[[], int] = lambda: 0    # per user; 0 = unlimited
    timeout: float = 30.0
