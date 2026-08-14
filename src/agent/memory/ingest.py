"""External research ingest → quarantine (Prinsip 2).

Fetched content NEVER becomes long-term memory directly — it lands in
quarantine, to be distilled/verified later. The fetch client carries NO service
token (spec §12) and is size-capped. Ingest is deduplicated by source URL.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.logging import get_logger
from agent.memory.models import SRC_EXTERNAL, MemoryItem
from agent.memory.quarantine import stage_external
from agent.memory.retrieval import Embedder

log = get_logger("memory.ingest")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


@dataclass(slots=True)
class FetchResult:
    url: str
    ok: bool
    text: str = ""
    error: str = ""


class ExternalFetcher:
    """Token-free HTTP client for external content."""

    def __init__(self, *, timeout_s: float = 20.0, max_bytes: int = 200_000) -> None:
        self.max_bytes = max_bytes
        # No Authorization header — must never carry a service token.
        self._client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, url: str) -> FetchResult:
        try:
            resp = await self._client.get(url)
        except httpx.HTTPError as exc:
            return FetchResult(url=url, ok=False, error=f"{type(exc).__name__}: {exc}")
        if resp.status_code >= 400:
            return FetchResult(url=url, ok=False, error=f"HTTP {resp.status_code}")
        content_type = resp.headers.get("content-type", "")
        if "html" not in content_type and "text" not in content_type:
            return FetchResult(url=url, ok=False, error=f"unsupported content-type: {content_type}")
        raw = resp.text[: self.max_bytes]
        text = html_to_text(raw) if "html" in content_type else raw.strip()
        return FetchResult(url=url, ok=True, text=text)


async def _already_staged(session: AsyncSession, url: str) -> bool:
    existing = await session.scalar(
        select(MemoryItem.id).where(
            MemoryItem.source == url, MemoryItem.source_kind == SRC_EXTERNAL
        )
    )
    return existing is not None


async def ingest_sources(
    session: AsyncSession,
    fetcher: ExternalFetcher,
    sources: list[str],
    *,
    embedder: Embedder | None = None,
    max_chars: int = 4000,
) -> list[MemoryItem]:
    """Fetch each source and stage new content into quarantine."""
    staged: list[MemoryItem] = []
    for url in sources:
        if await _already_staged(session, url):
            continue
        result = await fetcher.fetch(url)
        if not result.ok or not result.text:
            log.warning("ingest_skip", url=url, error=result.error)
            continue
        content = result.text[:max_chars]
        embedding: list[float] | None = None
        if embedder is not None:
            try:
                embedding = await embedder.embed(content)
            except Exception as exc:  # noqa: BLE001 — embedding is best-effort
                log.warning("ingest_embed_failed", url=url, error=str(exc))
        item = await stage_external(
            session, content=content, source=url, embedding=embedding
        )
        staged.append(item)
        log.info("ingest_staged", url=url, item_id=item.id)
    return staged
