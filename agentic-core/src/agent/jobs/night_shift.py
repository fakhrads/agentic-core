"""Night shift — the nightly autonomous cycle (spec §11 M8).

Order: probe → ingest → distill → benchmark → curate. `dry_run` is mandatory
support: it performs no writes (transaction rolled back) and issues no probe LLM
calls, reporting only what WOULD happen. ingest/distill/benchmark are wired as
hooks — distillation and self-benchmarks arrive in M9.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent.autonomy.goals import Prober, open_self_goals, probe_goal
from agent.db.base import get_sessionmaker
from agent.jobs.curator import sweep
from agent.logging import get_logger
from agent.memory.archive import resample_archived

if TYPE_CHECKING:
    from agent.evolution.drift import DriftState

log = get_logger("night_shift")

# A hook runs an optional pipeline stage. dry_run → no writes.
Hook = Callable[[AsyncSession, bool], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class StepReport:
    name: str
    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NightShiftReport:
    dry_run: bool
    steps: list[StepReport] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"dry_run": self.dry_run, "steps": [asdict(s) for s in self.steps]}


class NightShift:
    def __init__(
        self,
        dsn: str,
        prober: Prober,
        *,
        probe_cap: int = 10,
        resample_cap: int = 5,
        ingest_hook: Hook | None = None,
        distill_hook: Hook | None = None,
        benchmark_hook: Hook | None = None,
        forge_hook: Hook | None = None,
        drift_state: DriftState | None = None,
    ) -> None:
        self.dsn = dsn
        self.prober = prober
        self.probe_cap = probe_cap
        self.resample_cap = resample_cap
        self.ingest_hook = ingest_hook
        self.distill_hook = distill_hook
        self.benchmark_hook = benchmark_hook
        self.forge_hook = forge_hook
        self.drift_state = drift_state

    async def run(self, *, dry_run: bool) -> NightShiftReport:
        report = NightShiftReport(dry_run=dry_run)
        # In drift-pause, NOTIFY-tier steps (distill/benchmark) are held (spec §7/§10);
        # AUTO steps (probe, curate, resample) keep running.
        paused = await self.drift_state.is_paused() if self.drift_state is not None else False

        maker = get_sessionmaker(self.dsn)
        async with maker() as session:
            report.steps.append(await self._probe(session, dry_run))
            report.steps.append(await self._hook("ingest", self.ingest_hook, session, dry_run))
            report.steps.append(
                await self._notify_hook("distill", self.distill_hook, session, dry_run, paused)
            )
            report.steps.append(
                await self._notify_hook(
                    "benchmark", self.benchmark_hook, session, dry_run, paused
                )
            )
            # Forge only enqueues APPROVE requests (a human still decides), so it
            # runs even under drift-pause.
            report.steps.append(await self._hook("forge", self.forge_hook, session, dry_run))
            report.steps.append(await self._curate(session, dry_run))
            report.steps.append(await self._resample(session, dry_run))

            if dry_run:
                await session.rollback()
            else:
                await session.commit()
        log.info("night_shift_done", dry_run=dry_run, paused=paused, steps=len(report.steps))
        return report

    async def _probe(self, session: AsyncSession, dry_run: bool) -> StepReport:
        goals = await open_self_goals(session, self.probe_cap)
        if dry_run:
            return StepReport(
                "probe", True, {"would_probe": [g.id for g in goals], "count": len(goals)}
            )
        probed: list[dict[str, Any]] = []
        for goal in goals:
            await probe_goal(session, goal, self.prober)
            probed.append({"id": goal.id, "status": goal.status})
        return StepReport("probe", True, {"probed": probed})

    async def _hook(
        self, name: str, hook: Hook | None, session: AsyncSession, dry_run: bool
    ) -> StepReport:
        if hook is None:
            return StepReport(name, True, {"skipped": "not wired yet"})
        try:
            detail = await hook(session, dry_run)
            return StepReport(name, True, detail)
        except Exception as exc:  # noqa: BLE001 — one stage failing must not abort the shift
            log.error("night_shift_hook_failed", step=name, error=str(exc))
            return StepReport(name, False, {"error": str(exc)})

    async def _notify_hook(
        self, name: str, hook: Hook | None, session: AsyncSession, dry_run: bool, paused: bool
    ) -> StepReport:
        if paused:
            return StepReport(name, True, {"skipped": "drift-pause (NOTIFY held)"})
        return await self._hook(name, hook, session, dry_run)

    async def _resample(self, session: AsyncSession, dry_run: bool) -> StepReport:
        if dry_run:
            return StepReport("resample", True, {"cap": self.resample_cap, "persisted": False})
        revived = await resample_archived(session, limit=self.resample_cap)
        return StepReport(
            "resample", True, {"revived": [i.id for i in revived], "count": len(revived)}
        )

    async def _curate(self, session: AsyncSession, dry_run: bool) -> StepReport:
        rep = await sweep(session)
        return StepReport(
            "curate",
            True,
            {
                "scanned": rep.scanned,
                "retired": rep.retired,
                "archived": rep.archived,
                "fitness_updated": rep.fitness_updated,
                "persisted": not dry_run,
            },
        )
