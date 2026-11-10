"""Per-key rate limiting (design §7): a token bucket per API key, in this process.

The bucket holds `rpm` requests and refills at `rpm / 60` per second, so a key can burst up to a
minute's worth and then sustains `rpm`. A user token spends its issuing key's bucket. The limit is
the namespace's `limits.requests_per_minute`, else the server default (`STEWARD_RATE_LIMIT_PER_
MINUTE`); none means unlimited. The namespace value is re-read at most once a minute.

In-process: several API processes each allow the full rate (no shared store in v1; stated in the
design). A refused request spends nothing.
"""

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

CONFIG_TTL_S = 60.0


@dataclass
class _Bucket:
    tokens: float
    at: float


class RateLimiter:
    def __init__(
        self,
        default_rpm: int | None,
        namespace_rpm: Callable[[uuid.UUID], Awaitable[int | None]],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._default = default_rpm
        self._namespace_rpm = namespace_rpm
        self._clock = clock
        self._buckets: dict[uuid.UUID, _Bucket] = {}
        self._limits: dict[uuid.UUID, tuple[int | None, float]] = {}  # ns -> (rpm, read at)

    async def check(self, key_id: uuid.UUID, namespace_id: uuid.UUID) -> float | None:
        """Spend one request of the key's bucket: None if allowed, else seconds to wait."""
        rpm = await self._rpm(namespace_id)
        if rpm is None:
            return None
        now, rate = self._clock(), rpm / 60.0
        bucket = self._buckets.get(key_id)
        if bucket is None:
            bucket = self._buckets[key_id] = _Bucket(float(rpm), now)
        bucket.tokens = min(float(rpm), bucket.tokens + (now - bucket.at) * rate)
        bucket.at = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return None
        return (1.0 - bucket.tokens) / rate

    async def _rpm(self, namespace_id: uuid.UUID) -> int | None:
        now = self._clock()
        cached = self._limits.get(namespace_id)
        if cached is None or now - cached[1] > CONFIG_TTL_S:
            own = await self._namespace_rpm(namespace_id)
            cached = self._limits[namespace_id] = (own if own is not None else self._default, now)
        return cached[0]
