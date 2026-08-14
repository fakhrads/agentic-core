"""trace_id / episode_id propagation via contextvars.

Invariant (spec §Prinsip 4): no operation without a trace. Every log record,
every LLM call, every tool invoke binds the ambient trace_id from here.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_episode_id: ContextVar[str | None] = ContextVar("episode_id", default=None)


def new_trace_id() -> str:
    return str(uuid.uuid4())


def get_trace_id() -> str | None:
    return _trace_id.get()


def get_episode_id() -> str | None:
    return _episode_id.get()


def set_trace_id(trace_id: str) -> None:
    _trace_id.set(trace_id)


def set_episode_id(episode_id: str) -> None:
    _episode_id.set(episode_id)


@contextmanager
def trace_context(
    trace_id: str | None = None,
    episode_id: str | None = None,
) -> Iterator[str]:
    """Bind a trace (and optionally episode) for the duration of a block.

    Restores the previous values on exit so nested episodes don't leak.
    """
    tid = trace_id or new_trace_id()
    trace_tok = _trace_id.set(tid)
    ep_tok = _episode_id.set(episode_id) if episode_id is not None else None
    try:
        yield tid
    finally:
        _trace_id.reset(trace_tok)
        if ep_tok is not None:
            _episode_id.reset(ep_tok)
