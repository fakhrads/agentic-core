"""Shared loop context — the dependencies every episode needs.

Passed to planner/executor/runner so they stay free of global state and are
trivially testable with fakes.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.autonomy.budget import BudgetManager
from agent.bus.streams import EventBus
from agent.channels.base import ChannelRegistry
from agent.llm.base import BudgetedLLM
from agent.memory.retrieval import Embedder
from agent.tools.cache import ToolCache
from agent.tools.client import ToolsClient

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful autonomous assistant. Answer concisely and truthfully. "
    "If you are unsure, say so. When a tool would help, call it; do not invent "
    "tool outputs."
)


@dataclass(slots=True)
class LoopContext:
    dsn: str
    bus: EventBus
    budget: BudgetManager
    llm: BudgetedLLM
    channels: ChannelRegistry
    # Tools are optional — without them the loop is a plain chat responder (M3).
    tools_client: ToolsClient | None = None
    tool_cache: ToolCache | None = None
    # Embedder is optional — when set, the loop retrieves relevant memory (M10).
    embedder: Embedder | None = None
    retrieval_k: int = 3
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    max_reply_tokens: int = 512
    max_tool_iters: int = 4
    drift_paused: bool = False
