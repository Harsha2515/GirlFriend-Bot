"""
agent/tools/registry.py — The only door through which a tool ever runs.

Gemini proposes; the registry disposes. For every requested call it:

  1. rejects unknown tool names
  2. validates the arguments against the tool's schema (types, enums,
     length limits, no unexpected keys) -- the model's output is untrusted
  3. enforces the per-user daily cap (admin exempt)
  4. runs the tool with a timeout, catching every exception
  5. counts successful runs toward the cap

A tool failure never raises out of here: it becomes a ToolResult the model
can apologise about, so one broken API can't take down the conversation.
"""
import asyncio
import logging
import time

from google.genai import types as genai_types

from agent.tools.base import Tool, ToolContext, ToolResult
from memory.usage import get_tool_count, record_tool_use

logger = logging.getLogger(__name__)

_TOOLS: dict[str, Tool] = {}

# Hard ceiling on any string argument, whatever the schema says.
MAX_STRING_ARG = 300


def register(tool: Tool) -> Tool:
    if tool.name in _TOOLS:
        raise ValueError(f"tool registered twice: {tool.name}")
    _TOOLS[tool.name] = tool
    return tool


def get(name: str) -> Tool | None:
    return _TOOLS.get(name)


def names() -> list[str]:
    return sorted(_TOOLS)


# ── Gemini declarations ───────────────────────────────────────────────────────

def _to_schema(spec: dict) -> genai_types.Schema:
    kind = spec.get("type", "string").upper()
    kwargs = {"type": kind}
    if "description" in spec:
        kwargs["description"] = spec["description"]
    if "enum" in spec:
        kwargs["enum"] = list(spec["enum"])
    if kind == "OBJECT":
        kwargs["properties"] = {k: _to_schema(v) for k, v in spec.get("properties", {}).items()}
        if spec.get("required"):
            kwargs["required"] = list(spec["required"])
    return genai_types.Schema(**kwargs)


def declarations(only: list[str] | None = None) -> list[genai_types.FunctionDeclaration]:
    """Function declarations for Gemini, optionally limited to some tools."""
    return [
        genai_types.FunctionDeclaration(
            name=t.name, description=t.description, parameters=_to_schema(t.parameters)
        )
        for t in _TOOLS.values()
        if only is None or t.name in only
    ]


# ── Argument validation ───────────────────────────────────────────────────────

def validate_args(tool: Tool, args: dict | None) -> tuple[dict | None, str]:
    """
    Check model-supplied arguments against the tool's schema.
    Returns (clean_args, "") or (None, reason).
    """
    args = dict(args or {})
    props = tool.parameters.get("properties", {})

    unknown = set(args) - set(props)
    if unknown:
        return None, f"unexpected argument(s): {', '.join(sorted(unknown))}"

    for required in tool.parameters.get("required", []):
        if args.get(required) in (None, ""):
            return None, f"missing required argument: {required}"

    clean = {}
    for key, value in args.items():
        spec = props[key]
        if value is None:
            continue
        if spec.get("type", "string") == "string":
            if not isinstance(value, str):
                return None, f"{key} must be text"
            value = value.strip()
            limit = min(spec.get("maxLength", MAX_STRING_ARG), MAX_STRING_ARG)
            if len(value) > limit:
                return None, f"{key} is too long (max {limit} characters)"
            if "enum" in spec and value not in spec["enum"]:
                return None, f"{key} must be one of: {', '.join(spec['enum'])}"
        clean[key] = value
    return clean, ""


# ── Execution ─────────────────────────────────────────────────────────────────

async def execute(name: str, args: dict | None, ctx: ToolContext) -> ToolResult:
    """Validate, rate-limit and run one tool call. Never raises."""
    tool = _TOOLS.get(name)
    if tool is None:
        logger.warning(f"Model requested unknown tool '{name}' (user {ctx.user_id})")
        return ToolResult.error(f"There is no tool called {name}.")

    clean, problem = validate_args(tool, args)
    if clean is None:
        logger.info(f"Rejected {name} call for user {ctx.user_id}: {problem}")
        return ToolResult.error(f"Invalid request: {problem}")

    limit = tool.daily_limit()
    if limit > 0 and not ctx.is_admin:
        if await get_tool_count(ctx.user_id, name) >= limit:
            return ToolResult(
                ok=False,
                data={"error": f"daily limit reached ({limit} per day for this feature)",
                      "limit_reached": True},
            )

    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(tool.handler(ctx, clean), timeout=tool.timeout)
    except asyncio.TimeoutError:
        logger.warning(f"Tool {name} timed out for user {ctx.user_id}")
        return ToolResult.error("That took too long. Try again in a moment.")
    except Exception as exc:
        # Log the type only -- messages from HTTP libraries can echo URLs or
        # query text, and tool arguments may be personal.
        logger.error(f"Tool {name} failed for user {ctx.user_id}: {type(exc).__name__}")
        return ToolResult.error("That didn't work right now. Try again later.")

    elapsed = (time.perf_counter() - started) * 1000
    logger.info(
        f"tool={name} user={ctx.user_id} ok={result.ok} "
        f"latency_ms={elapsed:.0f} gemini_calls={result.gemini_calls}"
    )
    if result.ok:
        await record_tool_use(ctx.user_id, name)
    return result
