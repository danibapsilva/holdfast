from __future__ import annotations

import datetime as dt

import pytest

from holdfast.chains.fake import FakeChain
from holdfast.deal import Deal, Role
from holdfast.guards import Caps
from holdfast.money import Token, tusd
from holdfast.service import EscrowService, Policy
from holdfast.store import MemoryStore

START = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)
TUSD: Token = tusd("0x" + "ab" * 20)
DOLLAR = 10**6


class Clock:
    """A clock tests move by hand."""

    def __init__(self, now: dt.datetime = START) -> None:
        self.now = now

    def __call__(self) -> dt.datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += dt.timedelta(**delta)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def chain() -> FakeChain:
    return FakeChain()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def caps() -> Caps:
    return Caps(per_deal=100 * DOLLAR, per_day=150 * DOLLAR)


@pytest.fixture
def service(store: MemoryStore, chain: FakeChain, caps: Caps, clock: Clock) -> EscrowService:
    return EscrowService(store, chain, TUSD, caps=caps, policy=Policy(), clock=clock)


def new_deal(
    *,
    amount: int = 50 * DOLLAR,
    fee: int = DOLLAR // 2,
    creator: Role = Role.BUYER,
    confirmations: int = 2,
) -> Deal:
    return Deal(
        id="d1",
        number=1,
        buyer_id="alice",
        seller_id="bob",
        creator=creator,
        token=TUSD,
        amount=amount,
        fee=fee,
        description="A Brawl Stars account",
        created_at=START,
        required_confirmations=confirmations,
        payment_window=dt.timedelta(minutes=30),
        dispute_window=dt.timedelta(minutes=10),
    )
