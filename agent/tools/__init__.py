"""
agent/tools — capabilities Gemini can request: web search, weather, photos,
image generation.

Importing this package registers every tool with the registry.
"""
from agent.tools import images, weather, web_search  # noqa: F401  (registers tools)
from agent.tools.registry import declarations, execute, get, names  # noqa: F401
