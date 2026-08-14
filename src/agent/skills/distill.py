"""Skill distillation — turn a successful trajectory into a probation skill.

Distilled skills start in `probation` and can only reach `active` by passing an
external benchmark suite (see skills/benchmark.py). Idempotent per name.
"""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.models import EPISODE_DONE, Episode, Skill
from agent.logging import get_logger
from agent.memory.reasoning import Distiller, build_trajectory
from agent.memory.retrieval import Embedder
from agent.skills.registry import create_skill, get_skill_by_name

log = get_logger("skills.distill")

_SKILL_PROMPT = (
    "Distill a reusable, general skill from this successful trajectory. Write it "
    "as concise step-by-step instructions an agent could follow again.\n\n{trajectory}"
)


def _slugify(text: str) -> str:
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "skill")
    slug = re.sub(r"[^a-z0-9]+", "_", first.lower()).strip("_")
    return (slug or "skill")[:48]


async def distill_skill(
    session: AsyncSession,
    episode: Episode,
    distiller: Distiller,
    *,
    name: str | None = None,
    embedder: Embedder | None = None,
) -> Skill | None:
    """Only successful episodes yield skills. Returns None otherwise."""
    if episode.status != EPISODE_DONE:
        return None

    body = (await distiller(_SKILL_PROMPT.format(trajectory=build_trajectory(episode)))).strip()
    skill_name = name or f"{_slugify(body)}_{episode.id}"

    existing = await get_skill_by_name(session, skill_name)
    if existing is not None:
        return existing

    embedding: list[float] | None = None
    if embedder is not None:
        try:
            embedding = await embedder.embed(body)
        except Exception as exc:  # noqa: BLE001 — embedding is best-effort
            log.warning("skill_embed_failed", error=str(exc))

    skill = await create_skill(
        session,
        name=skill_name,
        body=body,
        created_from_trace=episode.trace_id,
        embedding=embedding,
    )
    log.info("skill_distilled", name=skill_name, trace_id=episode.trace_id)
    return skill
