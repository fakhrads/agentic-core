"""Provider routing by task class + remaining budget.

M3 is deliberately minimal: chat → the primary (DeepSeek) provider, probe →
the local Ollama model. Later milestones can fall back to the cheap local model
when budget runs low.
"""

from __future__ import annotations

from agent.llm.base import LLMProvider

# Task classes used across the codebase.
TASK_CHAT = "chat"
TASK_PROBE = "probe"


class Router:
    def __init__(
        self,
        *,
        chat: LLMProvider,
        probe: LLMProvider | None = None,
    ) -> None:
        self._chat = chat
        self._probe = probe or chat

    def pick(self, task_class: str) -> LLMProvider:
        if task_class == TASK_PROBE:
            return self._probe
        return self._chat
