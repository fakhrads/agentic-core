from datetime import timedelta

from agent.db.base import utcnow
from agent.memory.models import MSTATUS_ACTIVE, MemoryItem
from agent.memory.retrieval import rerank


def _item(success: int, retrieval: int) -> MemoryItem:
    return MemoryItem(
        tier="semantic", content="x", source="s", source_kind="self",
        status=MSTATUS_ACTIVE, success_count=success, retrieval_count=retrieval,
        human_reward=0.0, contradiction_count=0, last_used_at=utcnow(),
    )


def test_rerank_empty() -> None:
    assert rerank([]) == []


def test_high_fitness_can_outrank_slightly_closer() -> None:
    close_weak = _item(success=0, retrieval=0)   # distance 0.10, fitness 0
    far_strong = _item(success=10, retrieval=0)  # distance 0.20, fitness 20

    # Fitness-heavy weighting flips the order despite the distance gap.
    ranked = rerank(
        [(close_weak, 0.10), (far_strong, 0.20)],
        w_similarity=0.3,
        w_fitness=0.7,
    )
    assert ranked[0][0] is far_strong


def test_similarity_weighting_keeps_closest_first() -> None:
    a = _item(success=0, retrieval=0)
    b = _item(success=0, retrieval=0)
    ranked = rerank([(a, 0.05), (b, 0.50)], w_similarity=1.0, w_fitness=0.0)
    assert ranked[0][0] is a


def test_decayed_fitness_lowers_rank() -> None:
    fresh = _item(success=5, retrieval=0)
    stale = _item(success=5, retrieval=0)
    stale.last_used_at = utcnow() - timedelta(days=60)  # heavy decay
    ranked = rerank([(fresh, 0.2), (stale, 0.2)], w_similarity=0.0, w_fitness=1.0)
    assert ranked[0][0] is fresh
