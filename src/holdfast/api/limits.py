"""Keeping a public demo usable: a request rate per IP, and a few open demo deals per IP.

Both live in memory, per process: a restart forgets them, and two API processes would each keep their own.
That's the right trade for one small demo server; a bigger deployment would move them to Redis. They exist
to stop a script from draining the demo's daily cap, not to be a security boundary (the caps are).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from holdfast.errors import HoldfastError

IDLE_SECONDS = 600  # a bucket untouched this long is full again, so it's dropped


class TooManyRequests(HoldfastError):
    """Over the request rate, or holding too many open demo deals."""

    def __init__(self, message: str, retry_after: int = 1) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class TokenBucket:
    """`capacity` requests at once, refilled at `per_second`."""

    capacity: float
    per_second: float
    tokens: float = field(init=False)
    updated: float = 0.0

    def __post_init__(self) -> None:
        self.tokens = self.capacity

    def take(self, now: float) -> bool:
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.per_second)
        self.updated = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class RateLimiter:
    """One token bucket per client IP."""

    def __init__(
        self, *, capacity: float = 30, per_second: float = 1.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.capacity = capacity
        self.per_second = per_second
        self.clock = clock
        self._buckets: dict[str, TokenBucket] = {}

    def check(self, ip: str) -> None:
        now = self.clock()
        self._forget_idle(now)
        bucket = self._buckets.setdefault(ip, TokenBucket(self.capacity, self.per_second, updated=now))
        if not bucket.take(now):
            raise TooManyRequests("Too many requests; slow down for a moment", retry_after=1)

    def _forget_idle(self, now: float) -> None:
        idle = [ip for ip, b in self._buckets.items() if now - b.updated > IDLE_SECONDS]
        for ip in idle:
            del self._buckets[ip]


class OpenDealLimit:
    """At most `per_ip` demo deals still open per IP; closed ones stop counting."""

    def __init__(self, per_ip: int = 3) -> None:
        self.per_ip = per_ip
        self._deals: dict[str, list[str]] = {}

    def check(self, ip: str, is_open: Callable[[str], bool]) -> None:
        open_now = [d for d in self._deals.get(ip, []) if is_open(d)]
        self._deals[ip] = open_now
        if len(open_now) >= self.per_ip:
            raise TooManyRequests(
                f"You have {len(open_now)} demo deals open; finish or cancel one first", retry_after=60
            )

    def add(self, ip: str, deal_id: str) -> None:
        self._deals.setdefault(ip, []).append(deal_id)
