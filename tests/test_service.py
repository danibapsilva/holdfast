"""The service end to end on FakeChain: what the web playground and the Discord bot will do."""

from __future__ import annotations

import datetime as dt

import pytest

from holdfast.chains import ChainError
from holdfast.chains.fake import FakeChain
from holdfast.deal import DealError, Payment, Role, State
from holdfast.guards import CapExceeded
from holdfast.service import DEMO_USER, EscrowService, NotFound, utcnow
from holdfast.store import Conflict, MemoryStore
from tests.conftest import DOLLAR, TUSD, Clock

ALICE_WALLET = "0x" + "a1" * 20
BOB_WALLET = "0x" + "b0" * 20


def open_deal(service: EscrowService, amount: int = 50 * DOLLAR) -> str:
    deal = service.create_deal(
        creator="alice", creator_role=Role.BUYER, counterparty="bob", amount=amount, description="A skin bundle"
    )
    assert deal.state == State.AWAITING_TERMS_ACCEPTANCE
    deal = service.accept_terms(deal.id, "bob")
    assert deal.state == State.AWAITING_PAYMENT
    assert deal.deposit_address
    return deal.id


def fund(service: EscrowService, chain: FakeChain, deal_id: str, amount: int | None = None) -> None:
    deal = service.get(deal_id)
    chain.pay(deal.deposit_address or "", TUSD, amount or deal.amount)
    chain.mine(2)
    service.tick()


def test_full_trade_released_to_the_seller(service: EscrowService, chain: FakeChain) -> None:
    deal_id = open_deal(service)
    deposit = service.get(deal_id).deposit_address or ""
    assert deposit == chain.deposit_address(service.get(deal_id).number)
    chain.pay(deposit, TUSD, 50 * DOLLAR)
    assert service.tick() == [deal_id]
    assert service.get(deal_id).state == State.CONFIRMING  # 1 confirmation of 2
    chain.mine()
    service.tick()
    assert service.get(deal_id).state == State.FUNDED
    service.mark_delivered(deal_id, "bob")
    deal = service.confirm_delivery(deal_id, "alice")
    assert deal.state == State.RELEASE_PENDING  # waits for the seller's address
    deal = service.set_payout_address(deal_id, "bob", BOB_WALLET)
    assert deal.state == State.RELEASED
    assert chain.balance(BOB_WALLET, TUSD) == 50 * DOLLAR - deal.fee
    assert chain.balance(deposit, TUSD) == deal.fee  # the fake fee stays behind
    assert len(deal.settle_txs) == 1


def test_seller_refunds(service: EscrowService, chain: FakeChain) -> None:
    deal_id = open_deal(service)
    service.set_refund_address(deal_id, "alice", ALICE_WALLET)
    fund(service, chain, deal_id)
    deal = service.request_refund(deal_id, "bob")
    assert deal.state == State.REFUNDED
    assert chain.balance(ALICE_WALLET, TUSD) == 50 * DOLLAR


def test_partial_payment_expires_into_a_refund(service: EscrowService, chain: FakeChain, clock: Clock) -> None:
    deal_id = open_deal(service)
    service.set_refund_address(deal_id, "alice", ALICE_WALLET)
    fund(service, chain, deal_id, amount=20 * DOLLAR)
    assert service.get(deal_id).state == State.PARTIALLY_PAID
    clock.advance(minutes=31)
    service.tick()
    assert service.get(deal_id).state == State.REFUNDED
    assert chain.balance(ALICE_WALLET, TUSD) == 20 * DOLLAR


def test_unpaid_deal_expires(service: EscrowService, clock: Clock) -> None:
    deal_id = open_deal(service)
    clock.advance(minutes=31)
    assert service.tick() == [deal_id]
    assert service.get(deal_id).state == State.EXPIRED


def test_overpayment_excess_goes_back(service: EscrowService, chain: FakeChain) -> None:
    deal_id = open_deal(service)
    service.set_refund_address(deal_id, "alice", ALICE_WALLET)
    service.set_payout_address(deal_id, "bob", BOB_WALLET)
    fund(service, chain, deal_id, amount=55 * DOLLAR)
    deal = service.confirm_delivery(deal_id, "alice")
    assert deal.state == State.RELEASED
    assert chain.balance(ALICE_WALLET, TUSD) == 5 * DOLLAR
    assert len(deal.settle_txs) == 2


def test_dispute_auto_settles_for_the_opener(service: EscrowService, chain: FakeChain, clock: Clock) -> None:
    deal_id = open_deal(service)
    service.set_refund_address(deal_id, "alice", ALICE_WALLET)
    fund(service, chain, deal_id)
    service.open_dispute(deal_id, "alice", "It never arrived")
    clock.advance(minutes=9)
    service.tick()
    assert service.get(deal_id).state == State.DISPUTED
    clock.advance(minutes=2)
    service.tick()
    assert service.get(deal_id).state == State.REFUNDED
    assert chain.balance(ALICE_WALLET, TUSD) == 50 * DOLLAR


def test_arbiter_releases_a_dispute(service: EscrowService, chain: FakeChain) -> None:
    deal_id = open_deal(service)
    service.set_payout_address(deal_id, "bob", BOB_WALLET)
    fund(service, chain, deal_id)
    service.open_dispute(deal_id, "bob", "Buyer went quiet")
    service.respond_dispute(deal_id, "alice", "I'm here, checking")
    deal = service.resolve_dispute(deal_id, "daniel", Role.SELLER)
    assert deal.state == State.RELEASED


def test_the_same_transfer_never_counts_twice(
    service: EscrowService, chain: FakeChain, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = open_deal(service), open_deal(service)
    shared = Payment(tx_hash="0xdup", amount=50 * DOLLAR, confirmations=5)

    def same_transfer_everywhere(*_args: object, **_kwargs: object) -> list[Payment]:
        return [shared]

    monkeypatch.setattr(chain, "payments_to", same_transfer_everywhere)
    service.tick()
    assert service.get(first).state == State.FUNDED
    other = service.get(second)
    assert other.state == State.AWAITING_PAYMENT
    assert other.needs_review


def test_a_failed_send_is_flagged_not_retried(service: EscrowService, chain: FakeChain) -> None:
    deal_id = open_deal(service)
    service.set_payout_address(deal_id, "bob", BOB_WALLET)
    fund(service, chain, deal_id)
    chain.fail_sends = True
    with pytest.raises(ChainError):
        service.confirm_delivery(deal_id, "alice")
    deal = service.get(deal_id)
    assert deal.state == State.RELEASE_PENDING
    assert deal.payout_started
    assert deal.needs_review
    chain.fail_sends = False
    service.tick()  # does not send again on its own
    assert service.get(deal_id).state == State.RELEASE_PENDING
    assert chain.balance(BOB_WALLET, TUSD) == 0


def test_caps(service: EscrowService, chain: FakeChain) -> None:
    with pytest.raises(CapExceeded):
        service.create_deal(
            creator="alice", creator_role=Role.BUYER, counterparty="bob", amount=101 * DOLLAR, description="x"
        )
    a, b = open_deal(service, 100 * DOLLAR), open_deal(service, 100 * DOLLAR)
    for deal_id in (a, b):
        service.set_payout_address(deal_id, "bob", BOB_WALLET)
        fund(service, chain, deal_id)
    service.confirm_delivery(a, "alice")
    with pytest.raises(CapExceeded):
        service.confirm_delivery(b, "alice")  # the day's sending limit (150) is reached
    assert service.get(b).state == State.RELEASE_PENDING
    assert not service.get(b).payout_started


def test_who_can_do_what(service: EscrowService, chain: FakeChain) -> None:
    deal_id = open_deal(service)
    with pytest.raises(DealError, match="not part"):
        service.cancel(deal_id, "eve")
    fund(service, chain, deal_id)
    with pytest.raises(DealError):
        service.confirm_delivery(deal_id, "bob")
    with pytest.raises(NotFound):
        service.get("nope")
    with pytest.raises(ChainError):
        service.set_payout_address(deal_id, "bob", "not an address")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"creator_role": Role.ARBITER}, "buyer or the seller"),
        ({"counterparty": "alice"}, "two different"),
        ({"description": "  "}, "what's being traded"),
    ],
)
def test_create_deal_validation(service: EscrowService, kwargs: dict[str, object], message: str) -> None:
    args: dict[str, object] = {
        "creator": "alice",
        "creator_role": Role.BUYER,
        "counterparty": "bob",
        "amount": DOLLAR,
        "description": "x",
    }
    args.update(kwargs)
    with pytest.raises(DealError, match=message):
        service.create_deal(**args)  # type: ignore[arg-type]


def test_cancel_before_payment(service: EscrowService) -> None:
    deal_id = open_deal(service)
    assert service.cancel(deal_id, "bob").state == State.CANCELLED


def test_concurrent_writers_conflict(store: MemoryStore, service: EscrowService) -> None:
    deal_id = open_deal(service)
    stale = store.get(deal_id)
    assert stale is not None
    service.cancel(deal_id, "alice")
    stale.description = "edited elsewhere"
    with pytest.raises(Conflict):
        store.save(stale)


# ---- the demo bot ----


def test_demo_as_seller(service: EscrowService, chain: FakeChain) -> None:
    deal = service.start_demo(visitor="visitor-1", visitor_role=Role.BUYER, amount=10 * DOLLAR, description="Demo item")
    assert deal.state == State.AWAITING_PAYMENT  # the demo bot accepted at once
    assert deal.payout_address == chain.demo_address()
    service.set_refund_address(deal.id, "visitor-1", ALICE_WALLET)
    service.demo_pay(deal.id, "visitor-1")  # the playground's Pay button
    chain.mine(2)
    service.tick()
    assert service.get(deal.id).state == State.FUNDED
    deal = service.confirm_delivery(deal.id, "visitor-1")
    assert deal.state == State.RELEASED
    assert chain.balance(chain.demo_address(), TUSD) == 10 * DOLLAR - deal.fee


def test_demo_as_buyer_pays_and_releases(service: EscrowService, chain: FakeChain) -> None:
    deal = service.start_demo(
        visitor="visitor-2", visitor_role=Role.SELLER, amount=10 * DOLLAR, description="Demo item"
    )
    assert deal.buyer_id == DEMO_USER
    assert deal.refund_address == chain.demo_address()
    chain.mine(2)
    service.tick()  # the demo bot already paid
    assert service.get(deal.id).state == State.FUNDED
    service.set_payout_address(deal.id, "visitor-2", BOB_WALLET)
    deal = service.mark_delivered(deal.id, "visitor-2")  # the demo bot trusts it and releases
    assert deal.state == State.RELEASED
    assert chain.balance(BOB_WALLET, TUSD) == 10 * DOLLAR - deal.fee


def test_demo_pay_is_only_for_demo_buyers(service: EscrowService) -> None:
    deal_id = open_deal(service)
    with pytest.raises(DealError, match="demo deal"):
        service.demo_pay(deal_id, "alice")
    demo = service.start_demo(visitor="v", visitor_role=Role.SELLER, amount=DOLLAR, description="x")
    with pytest.raises(DealError, match="demo deal"):
        service.demo_pay(demo.id, "v")  # the visitor is the seller here


def test_demo_pay_needs_an_open_payment(service: EscrowService, chain: FakeChain) -> None:
    deal = service.start_demo(visitor="v", visitor_role=Role.BUYER, amount=DOLLAR, description="x")
    service.demo_pay(deal.id, "v")
    chain.mine(2)
    service.tick()
    with pytest.raises(DealError, match="isn't waiting"):
        service.demo_pay(deal.id, "v")


def test_default_clock_is_aware() -> None:
    assert utcnow().tzinfo is dt.UTC
