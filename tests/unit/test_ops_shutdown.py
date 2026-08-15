"""Regression: redis-py's exceptions don't subclass the builtins of the same
name, so a naive `except (TimeoutError, ConnectionError)` in `_serve()` never
actually caught the redis.exceptions.TimeoutError that a forced Ctrl-C
cancellation produces mid-shutdown — it fell through to the "stopped with an
error" branch instead of being treated as an expected clean stop.
"""

import redis.exceptions

from agent.cli.ops import _SHUTDOWN_NOISE


def test_redis_exceptions_are_not_builtin_exceptions() -> None:
    # Documents *why* both families must be listed explicitly in _SHUTDOWN_NOISE.
    assert not issubclass(redis.exceptions.TimeoutError, TimeoutError)
    assert not issubclass(redis.exceptions.ConnectionError, ConnectionError)


def test_shutdown_noise_catches_redis_timeout_and_connection_errors() -> None:
    assert isinstance(redis.exceptions.TimeoutError("boom"), _SHUTDOWN_NOISE)
    assert isinstance(redis.exceptions.ConnectionError("boom"), _SHUTDOWN_NOISE)


def test_shutdown_noise_catches_builtin_network_errors_too() -> None:
    assert isinstance(TimeoutError("boom"), _SHUTDOWN_NOISE)
    assert isinstance(ConnectionError("boom"), _SHUTDOWN_NOISE)
    assert isinstance(OSError("boom"), _SHUTDOWN_NOISE)


def test_shutdown_noise_does_not_swallow_unrelated_errors() -> None:
    assert not isinstance(ValueError("bad config"), _SHUTDOWN_NOISE)
