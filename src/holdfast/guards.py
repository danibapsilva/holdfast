"""The walls that keep Holdfast on test networks and keep a bug from draining the demo treasury.

- Only allowlisted chain ids run. The chain adapter checks the RPC's chain id at startup and again
  before every send, so a misconfigured URL fails closed.
- Every deal is capped, and so is everything sent in one day.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

SEPOLIA = 11155111
ALLOWED_CHAIN_IDS: frozenset[int] = frozenset({SEPOLIA})


class GuardError(RuntimeError):
    """A guard refused: the wrong network, or over a cap."""


class WrongNetwork(GuardError):
    pass


class CapExceeded(GuardError):
    pass


def require_testnet(reported: int, allowed: frozenset[int] = ALLOWED_CHAIN_IDS) -> None:
    """Raise unless `reported` (what the RPC says it is) is an allowlisted test network."""
    if reported not in allowed:
        raise WrongNetwork(f"Refusing to run on chain {reported}: only {sorted(allowed)} are allowed")


@dataclass
class Caps:
    """Per-deal and per-day limits, in base units of the deal's token."""

    per_deal: int
    per_day: int
    _spent: dict[dt.date, int] = field(default_factory=dict[dt.date, int])

    def check_deal(self, amount: int) -> None:
        if amount > self.per_deal:
            raise CapExceeded(f"A demo deal can be at most {self.per_deal} base units")

    def check_send(self, amount: int, now: dt.datetime) -> None:
        """Before money leaves: would this send go over today's total?"""
        day = now.astimezone(dt.UTC).date()
        if self._spent.get(day, 0) + amount > self.per_day:
            raise CapExceeded("The demo's daily sending limit is reached; try again tomorrow")

    def record_send(self, amount: int, now: dt.datetime) -> None:
        day = now.astimezone(dt.UTC).date()
        self._spent = {day: self._spent.get(day, 0) + amount}  # only today matters
