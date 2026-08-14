from agent.tools.cache import ToolCache
from agent.tools.models import ToolEntry
from tests.fakes import FakeToolsClient


def _entry(name: str, status: str) -> ToolEntry:
    return ToolEntry(
        name=name,
        version=1,
        description=f"{name} tool",
        params_schema={"type": "object", "properties": {}},
        status=status,
        timeout_ms=1000,
    )


async def test_refresh_filters_disabled_and_flags_probation() -> None:
    client = FakeToolsClient(
        tools=[
            _entry("a", "active"),
            _entry("b", "probation"),
            _entry("c", "disabled"),
        ]
    )
    cache = ToolCache(client)
    await cache.refresh()

    usable = {t.name for t in cache.usable()}
    assert usable == {"a", "b"}  # disabled excluded
    assert len(cache.function_defs()) == 2
    assert cache.is_probation("b") is True
    assert cache.is_probation("a") is False
    assert cache.get("c") is not None  # still known, just not usable


async def test_run_without_redis_refreshes_once_and_returns() -> None:
    client = FakeToolsClient(tools=[_entry("a", "active")])
    cache = ToolCache(client, redis=None)
    await cache.run()
    assert cache.refreshes == 1
    assert client.list_calls == 1
