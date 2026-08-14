"""Ollama provider — local cheap model (probe) + embeddings.

M3 uses only `complete` (as a budget-free fallback / probe model). Embeddings
(`embed`) land in M5 when semantic memory needs them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from agent.llm.base import (
    Attempt,
    ChatMessage,
    LLMResult,
    OnAttempt,
    messages_to_payload,
)


class OllamaProvider:
    name = "ollama"

    def __init__(
        self, *, base_url: str, model: str, timeout_s: float, embed_model: str = ""
    ) -> None:
        self.model = model
        self.embed_model = embed_model or model
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed(self, text: str) -> list[float]:
        """Return an embedding vector for `text` (dimension defined by the model)."""
        resp = await self._client.post(
            "/api/embeddings", json={"model": self.embed_model, "prompt": text}
        )
        resp.raise_for_status()
        data = resp.json()
        vector = data.get("embedding", [])
        return [float(x) for x in vector]

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.7,
        tools: Sequence[dict[str, Any]] | None = None,
        on_attempt: OnAttempt | None = None,
    ) -> LLMResult:
        # M4: the local model is used tool-free (probe/fallback).
        payload = {
            "model": self.model,
            "messages": messages_to_payload(messages),
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        resp = await self._client.post("/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        text = data.get("message", {}).get("content", "")
        tok_in = int(data.get("prompt_eval_count", 0))
        tok_out = int(data.get("eval_count", 0))
        # Local model → zero cost.
        if on_attempt is not None:
            await on_attempt(
                Attempt(
                    provider=self.name,
                    model=self.model,
                    tok_in=tok_in,
                    tok_out=tok_out,
                    cost_usd=0.0,
                    ok=True,
                )
            )
        return LLMResult(
            text=text,
            provider=self.name,
            model=self.model,
            tok_in=tok_in,
            tok_out=tok_out,
            cost_usd=0.0,
        )
