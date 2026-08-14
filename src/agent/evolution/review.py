"""Graded human review (spec §9).

`agent review <trace> --score 1..5` stores the score, then distributes a reward
to exactly the artefacts that episode used:

    human_reward += (score - 3) * 0.5 / n_artefacts

Score 3 is neutral; 1-2 penalize, 4-5 reinforce. Dividing by the artefact count
stops a 20-memory episode from lifting everything at once. Reward feeds fitness
(spec §5), so a curator sweep then re-ranks retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.models import (
    ARTEFACT_MEMORY,
    Episode,
    EpisodeArtefact,
)
from agent.db.repo import (
    artefact_usage_counts,
    get_episode_by_trace,
    list_episode_artefacts,
)
from agent.logging import get_logger
from agent.memory.models import MemoryItem

log = get_logger("review")


class ReviewError(Exception):
    pass


async def set_review(
    session: AsyncSession, trace_id: str, *, score: int, note: str | None = None
) -> Episode | None:
    if not 1 <= score <= 5:
        raise ReviewError("score must be 1..5")
    ep = await get_episode_by_trace(session, trace_id)
    if ep is None:
        return None
    ep.human_score = score
    ep.human_note = note
    await session.flush()
    return ep


async def distribute_reward(session: AsyncSession, episode: Episode) -> float:
    """Spread (score-3)*0.5 across the episode's artefacts. Returns per-artefact delta."""
    if episode.human_score is None:
        return 0.0
    artefacts = await list_episode_artefacts(session, episode.id)
    if not artefacts:
        return 0.0
    per = (episode.human_score - 3) * 0.5 / len(artefacts)
    if per == 0.0:
        return 0.0
    for art in artefacts:
        if art.kind == ARTEFACT_MEMORY:
            item = await session.get(MemoryItem, art.ref_id)
            if item is not None:
                item.human_reward += per
        # Skills carry benchmark-based standing, not human_reward — counted in the
        # divisor but no column to credit; memory receives the signal.
    await session.flush()
    log.info("reward_distributed", episode=episode.id, per=per, n=len(artefacts))
    return per


@dataclass(slots=True)
class PendingEpisode:
    trace_id: str
    episode_id: int
    impact: int  # summed global reuse of this episode's artefacts


async def pending_reviews(
    session: AsyncSession, *, limit: int = 5
) -> list[PendingEpisode]:
    """Unreviewed episodes, ranked by how often their artefacts are reused —
    so review effort targets the most impactful episodes (spec §9)."""
    from sqlalchemy import select

    counts = await artefact_usage_counts(session)
    unreviewed = await session.scalars(
        select(Episode).where(Episode.human_score.is_(None))
    )
    scored: list[PendingEpisode] = []
    for ep in unreviewed.all():
        arts = await session.scalars(
            select(EpisodeArtefact).where(EpisodeArtefact.episode_id == ep.id)
        )
        impact = sum(counts.get((a.kind, a.ref_id), 0) for a in arts.all())
        if impact > 0:
            scored.append(
                PendingEpisode(trace_id=ep.trace_id, episode_id=ep.id, impact=impact)
            )
    scored.sort(key=lambda p: p.impact, reverse=True)
    return scored[:limit]
