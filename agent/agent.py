"""
agent/agent.py — The reply path, with tools.

    User message
        ↓
    Gemini (call 1, tools offered) ── plain reply? ──→ done   (same cost as before)
        ↓ requests a tool
    Registry validates + runs it (for this user only)
        ↓
    Gemini (call 2, NO tools) phrases the result in persona
        ↓
    Reply text + any photos + source links

Deliberate limits:
  * One round of tools per message, at most MAX_TOOL_CALLS calls. The second
    Gemini call has no tools at all, so the model can't loop, and text
    fetched from the web can't trigger further actions.
  * Before any extra Gemini call, the global daily budget is checked.
  * Source links are appended by the application from the tool result, never
    written by the model, so they can't be hallucinated.
"""
import logging
from dataclasses import dataclass, field

from google.genai import types as genai_types

from agent import tools
from agent.client import CHAT_MODELS, generate_response, response_text
from agent.llm import MAX_TOKENS, TEMPERATURE, _format_history
from agent.tools.base import Attachment, ToolContext, ToolResult
from memory.usage import global_budget_left

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 2


@dataclass
class AgentReply:
    text: str
    attachments: list[Attachment] = field(default_factory=list)
    sources: list[tuple[str, str]] = field(default_factory=list)
    needs_location: bool = False
    gemini_calls: int = 0
    tools_used: list[str] = field(default_factory=list)


def _config(system_prompt: str, with_tools: bool, force_tool: str | None = None):
    kwargs = dict(
        system_instruction=system_prompt,
        max_output_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
    )
    if with_tools:
        kwargs["tools"] = [genai_types.Tool(function_declarations=tools.declarations())]
        # We run tools ourselves -- never let the SDK auto-execute anything.
        kwargs["automatic_function_calling"] = genai_types.AutomaticFunctionCallingConfig(disable=True)
        if force_tool:
            kwargs["tool_config"] = genai_types.ToolConfig(
                function_calling_config=genai_types.FunctionCallingConfig(
                    mode="ANY", allowed_function_names=[force_tool]))
    return genai_types.GenerateContentConfig(**kwargs)


def _function_calls(response) -> list:
    try:
        parts = response.candidates[0].content.parts or []
    except (AttributeError, IndexError, TypeError):
        return []
    return [p.function_call for p in parts if getattr(p, "function_call", None)]


def format_sources(sources: list[tuple[str, str]]) -> str:
    if not sources:
        return ""
    lines = ["", "🔗 Sources:"]
    for title, url in sources[:3]:
        lines.append(f"• {title[:70]} — {url}" if title else f"• {url}")
    return "\n".join(lines)


async def run_agent(
    system_prompt: str,
    history: list[dict],
    user_message: str,
    ctx: ToolContext,
    force_tool: str | None = None,
) -> AgentReply:
    """
    Produce the reply to one user message, using tools if the model asks.
    Raises only if Gemini itself can't be reached for the first call.
    """
    contents = _format_history(history) + [
        genai_types.Content(role="user", parts=[genai_types.Part(text=user_message)])
    ]

    first = await generate_response(contents, _config(system_prompt, True, force_tool), CHAT_MODELS)
    calls = _function_calls(first)
    reply = AgentReply(text="", gemini_calls=1)

    if not calls:
        reply.text = response_text(first)
        return reply

    # ── Run the requested tools (for this user only) ──────────────────────────
    results: list[tuple[str, ToolResult]] = []
    for call in calls[:MAX_TOOL_CALLS]:
        result = await tools.execute(call.name, dict(call.args or {}), ctx)
        results.append((call.name, result))
        reply.tools_used.append(call.name)
        reply.gemini_calls += result.gemini_calls
        reply.attachments.extend(result.attachments)
        reply.sources.extend(result.sources)
        reply.needs_location = reply.needs_location or result.needs_location

    if len(calls) > MAX_TOOL_CALLS:
        logger.info(f"Ignored {len(calls) - MAX_TOOL_CALLS} extra tool call(s) for user {ctx.user_id}")

    # ── Second call: phrase the results in persona, with no tools ─────────────
    budget = await global_budget_left()
    if budget is not None and budget < 1:
        reply.text = _fallback_text(results)
        return reply

    function_responses = [
        genai_types.Part.from_function_response(name=name, response=result.data)
        for name, result in results
    ]
    followup = contents + [
        first.candidates[0].content,     # keeps any thought signatures intact
        genai_types.Content(role="user", parts=function_responses),
    ]
    try:
        second = await generate_response(followup, _config(system_prompt, False), CHAT_MODELS)
        reply.gemini_calls += 1
        reply.text = response_text(second) or _fallback_text(results)
    except Exception as exc:
        logger.warning(f"Tool follow-up failed for user {ctx.user_id}: {type(exc).__name__}")
        reply.text = _fallback_text(results)
    return reply


def _fallback_text(results: list[tuple[str, ToolResult]]) -> str:
    """Something sensible to say if the phrasing call can't be made."""
    for _, result in results:
        if result.attachments:
            return "Here you go 💕"
        if not result.ok and result.data.get("error"):
            return result.data["error"]
    return "Here's what I found."
