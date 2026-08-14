from datetime import timedelta

from agent.db.base import utcnow
from agent.memory.fitness import (
    compute_fitness,
    decay,
    raw_score,
    should_archive,
    should_retire,
)
from agent.memory.models import MSTATUS_ACTIVE, MSTATUS_RETIRED, MemoryItem


def test_raw_score_formula() -> None:
    # 2*2 + 4*0.5 + 1*3 - 1*3 = 4 + 2 + 3 - 3 = 6
    assert raw_score(success_count=2, retrieval_count=4, human_reward=1, contradiction_count=1) == 6


def test_decay_halves_every_30_days() -> None:
    now = utcnow()
    assert decay(now, now) == 1.0
    assert decay(now - timedelta(days=30), now) == 0.5
    assert decay(now - timedelta(days=60), now) == 0.25
    # Never used → no decay yet.
    assert decay(None, now) == 1.0


def test_compute_fitness_applies_decay() -> None:
    now = utcnow()
    item = MemoryItem(
        tier="semantic", content="x", source="s", source_kind="self",
        status=MSTATUS_ACTIVE, success_count=3, retrieval_count=0,
        human_reward=0.0, contradiction_count=0, last_used_at=now - timedelta(days=30),
    )
    # raw = 6, decay = 0.5 → 3.0
    assert compute_fitness(item, now) == 3.0


def test_should_retire_requires_low_fitness_and_age() -> None:
    now = utcnow()
    old_weak = MemoryItem(
        tier="semantic", content="x", source="s", source_kind="self",
        status=MSTATUS_ACTIVE, success_count=0, retrieval_count=0,
        human_reward=0.0, contradiction_count=0,
        created_at=now - timedelta(days=20), last_used_at=now - timedelta(days=20),
    )
    assert should_retire(old_weak, now) is True

    young_weak = MemoryItem(
        tier="semantic", content="x", source="s", source_kind="self",
        status=MSTATUS_ACTIVE, success_count=0, retrieval_count=0,
        human_reward=0.0, contradiction_count=0,
        created_at=now - timedelta(days=2), last_used_at=now - timedelta(days=2),
    )
    assert should_retire(young_weak, now) is False


def test_should_archive_only_retired_and_old() -> None:
    now = utcnow()
    retired_old = MemoryItem(
        tier="semantic", content="x", source="s", source_kind="self",
        status=MSTATUS_RETIRED, created_at=now - timedelta(days=200),
        last_used_at=now - timedelta(days=200),
    )
    assert should_archive(retired_old, now) is True

    active_old = MemoryItem(
        tier="semantic", content="x", source="s", source_kind="self",
        status=MSTATUS_ACTIVE, created_at=now - timedelta(days=200),
    )
    assert should_archive(active_old, now) is False
