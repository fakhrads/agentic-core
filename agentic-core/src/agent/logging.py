"""structlog configuration.

- JSON to a daily-rotated file (machine-readable audit).
- Rich pretty output when attached to a tty (dev ergonomics).
- Every record is enriched with the ambient trace_id / episode_id.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog
from structlog.typing import EventDict, WrappedLogger

from agent.trace import get_episode_id, get_trace_id

_configured = False


def _add_trace(
    _logger: WrappedLogger, _method: str, event_dict: EventDict
) -> EventDict:
    tid = get_trace_id()
    eid = get_episode_id()
    if tid is not None:
        event_dict.setdefault("trace_id", tid)
    if eid is not None:
        event_dict.setdefault("episode_id", eid)
    return event_dict


def configure_logging(
    level: str = "INFO",
    log_dir: str | Path = "./logs",
    force_json: bool = False,
) -> None:
    """Idempotent global logging setup.

    Attaches a JSON file handler (always) plus a console renderer that is
    rich/plain on a tty and JSON otherwise.
    """
    global _configured
    if _configured:
        return

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_trace,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    is_tty = sys.stderr.isatty() and not force_json
    console_renderer: structlog.typing.Processor = (
        structlog.dev.ConsoleRenderer(colors=is_tty)
        if is_tty
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[*shared, console_renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Separate JSON file sink via stdlib handler, fed a JSON string.
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_path / "agent.jsonl", when="midnight", backupCount=14, utc=True
    )
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    file_logger = logging.getLogger("agent.file")
    file_logger.setLevel(level.upper())
    file_logger.addHandler(file_handler)
    file_logger.propagate = False

    _configured = True


def get_logger(component: str) -> structlog.stdlib.BoundLogger:
    """Return a bound logger tagged with its component name."""
    return structlog.get_logger().bind(component=component)  # type: ignore[no-any-return]


def reset_logging_for_tests() -> None:
    global _configured
    _configured = False
    structlog.reset_defaults()
