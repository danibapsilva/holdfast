"""The watcher loop, one pass at a time (no sleeping)."""

from __future__ import annotations

import threading

import pytest

from holdfast.chains import ChainError
from holdfast.chains.fake import FakeChain
from holdfast.deal import Role, State
from holdfast.service import EscrowService
from holdfast.store import Conflict
from holdfast.worker import Worker
from tests.conftest import DOLLAR, TUSD


def test_a_pass_credits_and_confirms_a_payment(service: EscrowService, chain: FakeChain) -> None:
    deal = service.create_deal(
        creator="alice", creator_role=Role.BUYER, counterparty="bob", amount=10 * DOLLAR, description="Art"
    )
    deal = service.accept_terms(deal.id, "bob")
    chain.pay(deal.deposit_address or "", TUSD, 10 * DOLLAR)
    worker = Worker(service, interval=5, before_tick=chain.mine)
    assert worker.run_once() == 5
    assert service.get(deal.id).state == State.FUNDED  # mined one more block: two confirmations


def test_network_failures_back_off_and_recover(service: EscrowService, monkeypatch: pytest.MonkeyPatch) -> None:
    worker = Worker(service, interval=5, max_backoff=60, jitter=lambda: 1.0)

    def down() -> list[str]:
        raise ChainError("the RPC is down")

    monkeypatch.setattr(service, "tick", down)
    assert [worker.run_once() for _ in range(5)] == [10, 20, 40, 60, 60]  # doubles, then capped
    monkeypatch.setattr(service, "tick", list)
    assert worker.run_once() == 5
    assert worker.failures == 0


def test_jitter_keeps_the_wait_in_the_upper_half(service: EscrowService) -> None:
    worker = Worker(service, interval=5, jitter=lambda: 0.0)
    worker.failures = 2
    assert worker.backoff() == 10  # half of the 20s ceiling


def test_a_lost_race_is_retried_next_pass(service: EscrowService, monkeypatch: pytest.MonkeyPatch) -> None:
    def raced() -> list[str]:
        raise Conflict("changed")

    monkeypatch.setattr(service, "tick", raced)
    worker = Worker(service, interval=5)
    assert worker.run_once() == 5
    assert worker.failures == 0


def test_run_stops_between_passes(service: EscrowService) -> None:
    stop = threading.Event()
    passes: list[int] = []

    def count() -> None:
        passes.append(1)
        stop.set()  # asked to stop during the first pass

    Worker(service, interval=0.01, before_tick=count).run(stop)
    assert passes == [1]
