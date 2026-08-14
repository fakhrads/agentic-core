from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.db.base import Base
from agent.memory.ingest import ExternalFetcher, html_to_text, ingest_sources
from agent.memory.models import MSTATUS_QUARANTINE, SRC_EXTERNAL


def test_html_to_text_strips_tags_and_unescapes() -> None:
    assert html_to_text("<p>hi &amp; bye</p>") == "hi & bye"
    assert html_to_text("<div>a</div>  <div>b</div>") == "a b"


def _fetcher(handler: object) -> ExternalFetcher:
    f = ExternalFetcher()
    f._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return f


async def test_fetch_carries_no_auth_header() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, text="<p>hello</p>", headers={"content-type": "text/html"})

    f = _fetcher(handler)
    res = await f.fetch("http://x")
    assert res.ok and res.text == "hello"
    assert seen["auth"] is None  # NEVER a token
    await f.aclose()


async def test_fetch_rejects_non_text() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x00\x01", headers={"content-type": "image/png"})

    f = _fetcher(handler)
    res = await f.fetch("http://x")
    assert res.ok is False and "content-type" in res.error
    await f.aclose()


async def test_fetch_http_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    f = _fetcher(handler)
    res = await f.fetch("http://x")
    assert res.ok is False and "HTTP 500" in res.error
    await f.aclose()


class _FakeFetcher:
    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages
        self.calls: list[str] = []

    async def fetch(self, url: str):  # type: ignore[no-untyped-def]
        from agent.memory.ingest import FetchResult

        self.calls.append(url)
        if url in self._pages:
            return FetchResult(url=url, ok=True, text=self._pages[url])
        return FetchResult(url=url, ok=False, error="404")


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_ingest_stages_into_quarantine_and_dedups(session: AsyncSession) -> None:
    fetcher = _FakeFetcher({"http://a": "content A", "http://b": "content B"})
    staged = await ingest_sources(session, fetcher, ["http://a", "http://b", "http://missing"])  # type: ignore[arg-type]
    assert len(staged) == 2
    assert all(i.status == MSTATUS_QUARANTINE for i in staged)
    assert all(i.source_kind == SRC_EXTERNAL for i in staged)
    await session.commit()

    # Second run: same sources already staged → nothing new.
    again = await ingest_sources(session, fetcher, ["http://a", "http://b"])  # type: ignore[arg-type]
    assert again == []
