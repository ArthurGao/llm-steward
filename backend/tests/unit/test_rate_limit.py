"""The per-key token bucket (design §7): pure, with an injected clock."""

import asyncio
import uuid

import pytest

from llm_steward.services.rate_limit import RateLimiter

KEY, OTHER, NS = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _limiter(clock: Clock, default: int | None = 3, overrides: dict[uuid.UUID, int] | None = None):  # type: ignore[no-untyped-def]
    calls: list[uuid.UUID] = []

    async def namespace_rpm(namespace_id: uuid.UUID) -> int | None:
        calls.append(namespace_id)
        return (overrides or {}).get(namespace_id)

    return RateLimiter(default, namespace_rpm, clock=clock), calls


def _check(limiter: RateLimiter, key: uuid.UUID = KEY, ns: uuid.UUID = NS) -> float | None:
    return asyncio.run(limiter.check(key, ns))


def test_a_full_bucket_allows_a_burst_then_says_how_long_to_wait() -> None:
    clock = Clock()
    limiter, _ = _limiter(clock)
    assert [_check(limiter) for _ in range(3)] == [None, None, None]
    assert _check(limiter) == pytest.approx(20.0)  # 3 per minute: one token every 20 s
    clock.now += 10
    assert _check(limiter) == pytest.approx(10.0)  # a refused request costs nothing
    clock.now += 10
    assert _check(limiter) is None
    assert _check(limiter) == pytest.approx(20.0)


def test_the_bucket_refills_to_its_size_and_no_further() -> None:
    clock = Clock()
    limiter, _ = _limiter(clock)
    _check(limiter)
    clock.now += 3600  # an hour idle
    assert [_check(limiter) for _ in range(4)][-1] == pytest.approx(20.0)  # still only 3


def test_each_key_has_its_own_bucket() -> None:
    clock = Clock()
    limiter, _ = _limiter(clock)
    for _ in range(3):
        _check(limiter)
    assert _check(limiter) is not None
    assert _check(limiter, OTHER) is None


def test_a_namespace_override_wins_and_is_read_once_a_minute() -> None:
    clock = Clock()
    limiter, calls = _limiter(clock, default=3, overrides={NS: 1})
    assert _check(limiter) is None
    assert _check(limiter) == pytest.approx(60.0)  # 1 per minute
    assert calls == [NS]  # cached
    clock.now += 61
    _check(limiter)
    assert calls == [NS, NS]


def test_no_limit_configured_means_unlimited() -> None:
    limiter, _ = _limiter(Clock(), default=None)
    assert all(_check(limiter) is None for _ in range(1000))
