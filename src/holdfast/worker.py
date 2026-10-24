"""The watcher: calls `EscrowService.tick` on a loop, so payments are credited, deadlines expire, dispute
timers settle, and released or refunded deals are paid out, without anyone pressing a button.

A failing network (`ChainError`) backs off exponentially with jitter, so a down RPC isn't hammered and
several workers don't retry in step. A save that lost a race (`Conflict`, the API changed the deal first)
is simply tried again on the next pass. `run` stops cleanly between passes when its stop event is set
(the CLI sets it on SIGTERM and Ctrl-C).
"""

from __future__ import annotations

import logging
import random
import threading
from collections.abc import Callable

from holdfast.chains import ChainError
from holdfast.service import EscrowService
from holdfast.store import Conflict

log = logging.getLogger(__name__)

INTERVAL_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 120.0


class Worker:
    """One loop over the service. `before_tick` runs first on every pass (the offline playground mines a
    block there)."""

    def __init__(
        self,
        service: EscrowService,
        *,
        interval: float = INTERVAL_SECONDS,
        max_backoff: float = MAX_BACKOFF_SECONDS,
        jitter: Callable[[], float] = random.random,
        before_tick: Callable[[], None] | None = None,
    ) -> None:
        self.service = service
        self.interval = interval
        self.max_backoff = max_backoff
        self.jitter = jitter
        self.before_tick = before_tick
        self.failures = 0

    def run_once(self) -> float:
        """One pass. Returns how long to wait before the next one."""
        if self.before_tick:
            self.before_tick()
        try:
            changed = self.service.tick()
        except ChainError:
            self.failures += 1
            delay = self.backoff()
            log.warning("the network failed (%d in a row); trying again in %.0fs", self.failures, delay, exc_info=True)
            return delay
        except Conflict:
            log.info("a deal changed while the watcher worked on it; it's picked up on the next pass")
            return self.interval
        if self.failures:
            log.info("the network is back after %d failed passes", self.failures)
        self.failures = 0
        if changed:
            log.info("deals changed: %s", ", ".join(changed))
        return self.interval

    def backoff(self) -> float:
        """Doubles with each failure up to the cap, then lands somewhere in its upper half (the jitter)."""
        ceiling = min(self.max_backoff, self.interval * 2**self.failures)
        return ceiling * (0.5 + self.jitter() / 2)

    def run(self, stop: threading.Event) -> None:
        log.info("watcher started: a pass every %.0fs", self.interval)
        while not stop.is_set():
            stop.wait(self.run_once())
        log.info("watcher stopped")
