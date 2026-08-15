"""Daemon assembly — the real `agent up` process.

Runs concurrently: the HTTP health/metrics server, the inbound-event consumer
(the loop), and (if configured) the Telegram poller. Handles SIGTERM/SIGINT for
graceful shutdown (spec §12): stop consuming, finish in-flight, close cleanly.
"""

from __future__ import annotations

import asyncio
import os
import signal

import redis.asyncio as redis_asyncio
import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession

from agent.autonomy.approvals import list_pending
from agent.autonomy.budget import BudgetManager
from agent.bus.events import STREAM_INBOUND, Event
from agent.bus.streams import GROUP_LOOP, ConsumerRunner, EventBus
from agent.channels.base import Channel, ChannelRegistry, InboundMessage
from agent.channels.dev import DevChannel
from agent.channels.telegram import TelegramChannel
from agent.channels.whatsapp import WhatsAppChannel
from agent.config import Settings, get_settings
from agent.db.base import dispose_engines, session_scope
from agent.db.models import SKILL_PROBATION, Skill
from agent.evolution.drift import DriftState
from agent.heartbeat import beat, mark_started
from agent.jobs.curator import sweep
from agent.jobs.night_shift import NightShift
from agent.jobs.scheduler import run_periodic
from agent.llm.base import BudgetedLLM, ChatMessage
from agent.llm.deepseek import DeepSeekProvider
from agent.llm.ollama import OllamaProvider
from agent.llm.recorder import DBCostRecorder
from agent.logging import configure_logging, get_logger
from agent.loop.context import LoopContext
from agent.loop.runner import AgentLoop
from agent.memory.episodic import recent_episodes
from agent.memory.ingest import ExternalFetcher, ingest_sources
from agent.memory.reasoning import distill_reasoning
from agent.playbook.store import PlaybookStore
from agent.skills.benchmark import evaluate_and_maybe_promote
from agent.skills.registry import list_skills
from agent.tools.cache import ToolCache
from agent.tools.client import ToolsClient
from agent.tools.forge import (
    ACTION_TOOL_REGISTER,
    FORGE_PROMPT,
    ForgeArtifact,
    ToolForge,
    parse_forge_json,
)
from agent.tools.needs import list_open_needs, mark_forged
from agent.trace import new_trace_id

log = get_logger("daemon")

_BUSY_MESSAGE = "Sistem sedang sibuk, coba lagi sebentar."


class Daemon:
    def __init__(self, settings: Settings, extra_channels: list[Channel] | None = None) -> None:
        self.settings = settings
        self.redis = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
        self.bus = EventBus(self.redis)
        self.budget = BudgetManager(
            self.redis,
            default_tokens=settings.budget_tokens,
            default_cost_usd=settings.budget_cost_usd,
            default_actions=settings.budget_actions,
        )
        self.deepseek = DeepSeekProvider(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key.get_secret_value(),
            model=settings.deepseek_model,
            timeout_s=settings.deepseek_timeout_s,
            provider_name=settings.llm_provider,
        )
        self.recorder = DBCostRecorder(settings.postgres_dsn)
        self.llm = BudgetedLLM(self.deepseek, self.budget, self.recorder)

        # Cheap local model for feasibility probes.
        self.ollama = OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_probe_model,
            timeout_s=settings.ollama_timeout_s,
            embed_model=settings.ollama_embed_model,
        )
        self.drift_state = DriftState(self.redis)
        self.night_shift = NightShift(
            settings.postgres_dsn,
            self._probe,
            ingest_hook=self._ingest_hook,
            distill_hook=self._distill_hook,
            benchmark_hook=self._benchmark_hook,
            forge_hook=self._forge_hook,
            drift_state=self.drift_state,
        )

        self.tools_client = ToolsClient(
            base_url=settings.tools_base_url,
            service_token=settings.tools_service_token.get_secret_value(),
            contract_version=settings.contract_version,
            timeout_s=settings.tools_timeout_s,
        )
        self.tool_cache = ToolCache(self.tools_client, self.redis)

        self.channels = ChannelRegistry()
        self.channels.register(DevChannel(self.bus))
        self.telegram: TelegramChannel | None = None
        token = settings.telegram_bot_token.get_secret_value()
        if token:
            self.telegram = TelegramChannel(
                token=token, allowed_chat_ids=settings.allowed_chat_ids()
            )
            self.channels.register(self.telegram)

        self.whatsapp: WhatsAppChannel | None = None
        wa_secret = settings.whatsapp_bridge_secret.get_secret_value()
        if wa_secret:
            self.whatsapp = WhatsAppChannel(
                bridge_url=settings.whatsapp_bridge_url,
                bridge_secret=wa_secret,
                allowed_numbers=settings.allowed_whatsapp_numbers(),
            )
            self.channels.register(self.whatsapp)

        for channel in extra_channels or []:
            self.channels.register(channel)

        ctx = LoopContext(
            dsn=settings.postgres_dsn,
            bus=self.bus,
            budget=self.budget,
            llm=self.llm,
            channels=self.channels,
            tools_client=self.tools_client,
            tool_cache=self.tool_cache,
            embedder=self.ollama,
        )
        self.loop = AgentLoop(ctx)
        self.consumer = ConsumerRunner(
            bus=self.bus,
            stream=STREAM_INBOUND,
            group=GROUP_LOOP,
            consumer=f"loop-{os.getpid()}",
            handler=self._on_event,
        )

    async def _on_event(self, event: Event) -> None:
        inbound = InboundMessage.from_payload(event.payload)
        trace_id = event.trace_id or new_trace_id()
        await self.loop.handle(inbound, trace_id)

    async def _curator_job(self) -> None:
        async with session_scope(self.settings.postgres_dsn) as session:
            await sweep(session)

    async def _heartbeat_job(self) -> None:
        await beat(self.redis)

    async def _probe(self, prompt: str) -> str:
        result = await self.ollama.complete(
            [ChatMessage(role="user", content=prompt)], max_tokens=256
        )
        return result.text

    async def _night_shift_job(self) -> None:
        await self.night_shift.run(dry_run=False)

    async def _distiller(self, prompt: str) -> str:
        result = await self.llm.complete(
            [ChatMessage(role="user", content=prompt)], max_tokens=256
        )
        return result.text

    async def _distill_hook(self, session: AsyncSession, dry_run: bool) -> dict[str, object]:
        episodes = await recent_episodes(session, limit=5)
        if dry_run:
            return {"would_distill": [e.trace_id for e in episodes], "count": len(episodes)}
        made: list[int] = []
        for ep in episodes:
            item = await distill_reasoning(session, ep, self._distiller, embedder=self.ollama)
            made.append(item.id)
        return {"reasoning_items": made}

    async def _skill_solver(self, skill: Skill, prompt: str) -> str:
        result = await self.llm.complete(
            [
                ChatMessage(
                    role="system",
                    content=f"Apply this skill:\n{skill.body}\n"
                    "Answer with ONLY the requested value.",
                ),
                ChatMessage(role="user", content=prompt),
            ],
            max_tokens=128,
        )
        return result.text

    async def _benchmark_hook(self, session: AsyncSession, dry_run: bool) -> dict[str, object]:
        skills = await list_skills(session, status=SKILL_PROBATION)
        if dry_run:
            return {"would_evaluate": [s.name for s in skills], "count": len(skills)}
        evaluated: list[dict[str, object]] = []
        for skill in skills:
            pr = await evaluate_and_maybe_promote(session, skill, self._skill_solver)
            evaluated.append({"skill": skill.name, "promoted": pr.promoted})
        return {"evaluated": evaluated}

    async def _ingest_hook(self, session: AsyncSession, dry_run: bool) -> dict[str, object]:
        sources = self.settings.research_source_list()
        if not sources:
            return {"skipped": "no research sources configured"}
        if dry_run:
            return {"would_fetch": sources, "count": len(sources)}
        fetcher = ExternalFetcher(timeout_s=self.settings.fetch_timeout_s)
        try:
            items = await ingest_sources(session, fetcher, sources, embedder=self.ollama)
            return {"staged": [i.id for i in items]}
        finally:
            await fetcher.aclose()

    async def _forge_generate(self, need_text: str) -> ForgeArtifact:
        result = await self.llm.complete(
            [ChatMessage(role="user", content=FORGE_PROMPT.format(need=need_text))],
            max_tokens=1500,
        )
        return parse_forge_json(result.text)

    async def _forge_hook(self, session: AsyncSession, dry_run: bool) -> dict[str, object]:
        needs = await list_open_needs(session, limit=3)
        if dry_run:
            return {"would_forge": [n.name for n in needs], "count": len(needs)}
        pending = await list_pending(session)
        pending_names = {
            (a.payload.get("submission", {}) or {}).get("name")
            for a in pending
            if a.action_kind == ACTION_TOOL_REGISTER
        }
        forge = ToolForge(self._forge_generate)
        forged: list[dict[str, object]] = []
        for need in needs:
            if need.name in pending_names:
                await mark_forged(session, need.id)
                continue
            try:
                need_text = (
                    f"{need.description}\nProposed name: {need.name}\n"
                    f"Example args: {need.args_sample}"
                )
                approval, _artifact = await forge.forge_and_request(
                    session, need=need_text, trace_id=need.requested_by_trace or "night-shift"
                )
                await mark_forged(session, need.id)
                forged.append({"need": need.name, "approval": approval.id})
            except Exception as exc:  # noqa: BLE001 — a bad generation must not abort the shift
                log.warning("forge_failed", need=need.name, error=str(exc))
        return {"forged": forged}

    async def ingest(self, inbound: InboundMessage) -> None:
        """Publish an inbound message, applying backpressure (spec §12)."""
        qlen = await self.bus.xlen(STREAM_INBOUND)
        if qlen > self.settings.max_queue:
            log.warning("backpressure_reject", qlen=qlen)
            try:
                await self.channels.get(inbound.channel).send(inbound.chat_id, _BUSY_MESSAGE)
            except Exception as exc:  # noqa: BLE001
                log.warning("backpressure_notify_failed", error=str(exc))
            return
        await self.bus.publish(
            STREAM_INBOUND,
            Event(
                type="inbound",
                trace_id=new_trace_id(),
                component="ingest",
                payload=dict(inbound.as_dict()),
            ),
        )

    async def run(self) -> None:
        configure_logging(level=self.settings.log_level, log_dir=self.settings.log_dir)
        PlaybookStore(self.settings.playbook_dir).ensure()
        await mark_started(self.redis)
        await beat(self.redis)
        log.info("daemon_starting", telegram=bool(self.telegram))

        from agent.api.health import create_app

        config = uvicorn.Config(
            create_app(daemon=self),
            host=self.settings.http_host,
            port=self.settings.http_port,
            log_level=self.settings.log_level.lower(),
        )
        server = uvicorn.Server(config)
        # We manage SIGTERM/SIGINT ourselves; stop uvicorn from grabbing them.
        # setattr (not attribute assignment) keeps mypy happy about the dynamic method.
        setattr(server, "install_signal_handlers", lambda: None)  # noqa: B010

        stop = asyncio.Event()

        def _shutdown() -> None:
            log.info("daemon_shutdown_signal")
            server.should_exit = True
            self.consumer.stop()
            self.tool_cache.stop()
            if self.telegram is not None:
                self.telegram.stop()
            stop.set()

        running_loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            running_loop.add_signal_handler(sig, _shutdown)

        tasks = [
            asyncio.create_task(server.serve(), name="health"),
            asyncio.create_task(self.consumer.run(), name="consumer"),
            asyncio.create_task(self.tool_cache.run(), name="tool_cache"),
            asyncio.create_task(
                run_periodic(
                    86_400, self._curator_job, name="curator", stop=stop
                ),
                name="curator",
            ),
            asyncio.create_task(
                run_periodic(
                    86_400, self._night_shift_job, name="night_shift", stop=stop
                ),
                name="night_shift",
            ),
            asyncio.create_task(
                run_periodic(10, self._heartbeat_job, name="heartbeat", stop=stop),
                name="heartbeat",
            ),
        ]
        if self.telegram is not None:
            tasks.append(asyncio.create_task(self.telegram.poll(self.ingest), name="telegram"))

        try:
            await asyncio.gather(*tasks)
        finally:
            # If any task raised (or we got here via cancellation), the rest
            # are typically still running — leaving them dangling means they
            # get force-cancelled by asyncio.run()'s own teardown instead, each
            # logging its own separate "exception was never retrieved"-style
            # traceback. Cancel + drain them here first so shutdown produces
            # at most one exception, not a cascade.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.aclose()

    async def aclose(self) -> None:
        log.info("daemon_closing")
        await self.deepseek.aclose()
        await self.ollama.aclose()
        await self.tools_client.aclose()
        if self.telegram is not None:
            await self.telegram.aclose()
        if self.whatsapp is not None:
            await self.whatsapp.aclose()
        await self.bus.aclose()
        await dispose_engines()


async def run_daemon() -> None:
    await Daemon(get_settings()).run()
