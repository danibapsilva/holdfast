"""The deal state machine: every transition, every refusal, the timers, and the money math."""

from __future__ import annotations

import datetime as dt

import pytest

from holdfast.deal import (
    TERMINAL,
    TRANSITIONS,
    Action,
    BadPayment,
    Deal,
    DealError,
    IllegalTransition,
    NotAllowed,
    Payment,
    Role,
    State,
)
from tests.conftest import DOLLAR, START, new_deal

ADDR = "0x" + "11" * 20
PAYOUT = "0x" + "22" * 20
REFUND = "0x" + "33" * 20
MIN = dt.timedelta(minutes=1)


def pay(amount: int, confirmations: int = 2, tx: str = "0xaa", index: int = 0) -> Payment:
    return Payment(tx_hash=tx, log_index=index, amount=amount, confirmations=confirmations)


def at(state: State) -> Deal:
    """A deal walked forward to `state` by real actions."""
    d = new_deal()
    if state == State.DRAFT:
        return d
    d.propose(by=Role.BUYER, user="alice", now=START)
    if state == State.AWAITING_TERMS_ACCEPTANCE:
        return d
    d.accept_terms(by=Role.SELLER, user="bob", now=START, deposit_address=ADDR)
    if state == State.AWAITING_PAYMENT:
        return d
    if state == State.PARTIALLY_PAID:
        d.record_payment(pay(10 * DOLLAR), now=START)
        return d
    if state == State.CONFIRMING:
        d.record_payment(pay(d.amount, confirmations=1), now=START)
        return d
    d.record_payment(pay(d.amount), now=START)
    if state == State.FUNDED:
        return d
    if state == State.EXPIRED:
        raise ValueError("use expired()")
    d.payout_address, d.refund_address = PAYOUT, REFUND
    if state == State.DISPUTED:
        d.open_dispute(by=Role.BUYER, user="alice", now=START, reason="Never got it")
        return d
    if state in (State.RELEASE_PENDING, State.RELEASED):
        d.confirm_delivery(by=Role.BUYER, user="alice", now=START)
    else:
        d.request_refund(by=Role.SELLER, user="bob", now=START)
    if state in (State.RELEASE_PENDING, State.REFUND_PENDING):
        return d
    d.begin_payout(now=START)
    d.mark_settled(["0xdone"], now=START)
    return d


def expired() -> Deal:
    d = at(State.AWAITING_PAYMENT)
    assert d.expire(now=START + d.payment_window)
    return d


def cancelled() -> Deal:
    d = at(State.AWAITING_PAYMENT)
    d.cancel(by=Role.SELLER, user="bob", now=START)
    return d


def deal_in(state: State) -> Deal:
    if state == State.EXPIRED:
        return expired()
    if state == State.CANCELLED:
        return cancelled()
    return at(state)


# ---- the transition table itself ----


@pytest.mark.parametrize("state", list(State))
def test_every_state_is_reachable(state: State) -> None:
    assert deal_in(state).state == state


@pytest.mark.parametrize("action", list(Action))
@pytest.mark.parametrize("state", list(State))
@pytest.mark.parametrize("role", list(Role))
def test_check_follows_the_table(action: Action, state: State, role: Role) -> None:
    d = deal_in(state)
    states, roles = TRANSITIONS[action]
    if role not in roles:
        with pytest.raises(NotAllowed):
            d._check(action, role)
    elif state not in states:
        with pytest.raises(IllegalTransition):
            d._check(action, role)
    else:
        d._check(action, role)


@pytest.mark.parametrize("state", sorted(TERMINAL))
def test_closed_deals_take_no_party_actions(state: State) -> None:
    d = deal_in(state)
    for action, (_, roles) in TRANSITIONS.items():
        for role in roles - {Role.SYSTEM}:
            with pytest.raises(IllegalTransition):
                d._check(action, role)


def test_errors_read_like_sentences() -> None:
    d = at(State.FUNDED)
    with pytest.raises(IllegalTransition, match="Can't accept terms while the deal is funded"):
        d.accept_terms(by=Role.SELLER, user="bob", now=START, deposit_address=ADDR)
    with pytest.raises(NotAllowed, match="The seller can't confirm delivery"):
        d.confirm_delivery(by=Role.SELLER, user="bob", now=START)


# ---- making the deal ----


def test_edit_terms_in_draft() -> None:
    d = new_deal()
    d.edit_terms(by=Role.BUYER, user="alice", now=START, amount=20 * DOLLAR, fee=0, description="Changed")
    assert (d.amount, d.fee, d.description) == (20 * DOLLAR, 0, "Changed")
    with pytest.raises(DealError, match="fee"):
        d.edit_terms(by=Role.BUYER, user="alice", now=START, amount=DOLLAR, fee=DOLLAR, description="x")


def test_only_the_creator_proposes() -> None:
    with pytest.raises(NotAllowed):
        new_deal().propose(by=Role.SELLER, user="bob", now=START)


def test_propose_then_accept_sets_address_and_payment_clock() -> None:
    d = at(State.AWAITING_TERMS_ACCEPTANCE)
    assert d.deadline == START + d.payment_window
    later = START + 5 * MIN
    d.accept_terms(by=Role.SELLER, user="bob", now=later, deposit_address=ADDR)
    assert d.state == State.AWAITING_PAYMENT
    assert d.deposit_address == ADDR
    assert d.deadline == later + d.payment_window


def test_the_proposer_cannot_accept_their_own_terms() -> None:
    d = at(State.AWAITING_TERMS_ACCEPTANCE)
    with pytest.raises(DealError, match="other party"):
        d.accept_terms(by=Role.BUYER, user="alice", now=START, deposit_address=ADDR)


def test_an_offer_expires() -> None:
    d = at(State.AWAITING_TERMS_ACCEPTANCE)
    with pytest.raises(DealError, match="expired"):
        d.accept_terms(by=Role.SELLER, user="bob", now=START + d.payment_window, deposit_address=ADDR)
    assert d.expire(now=START + d.payment_window)
    assert d.state == State.EXPIRED


def test_seller_created_deal_is_accepted_by_the_buyer() -> None:
    d = new_deal(creator=Role.SELLER)
    d.propose(by=Role.SELLER, user="bob", now=START)
    d.accept_terms(by=Role.BUYER, user="alice", now=START, deposit_address=ADDR)
    assert d.state == State.AWAITING_PAYMENT


@pytest.mark.parametrize("state", [State.DRAFT, State.AWAITING_TERMS_ACCEPTANCE, State.AWAITING_PAYMENT])
def test_cancel_before_money_ends_the_deal(state: State) -> None:
    d = at(state)
    d.cancel(by=Role.BUYER, user="alice", now=START)
    assert d.state == State.CANCELLED


def test_cancel_with_a_partial_payment_refunds_it() -> None:
    d = at(State.PARTIALLY_PAID)
    d.cancel(by=Role.BUYER, user="alice", now=START)
    assert d.state == State.REFUND_PENDING
    d.refund_address = REFUND
    assert d.transfers() is not None
    assert [t.amount for t in d.transfers() or []] == [10 * DOLLAR]


def test_no_cancel_once_full_payment_is_in() -> None:
    with pytest.raises(IllegalTransition):
        at(State.CONFIRMING).cancel(by=Role.BUYER, user="alice", now=START)


# ---- payments ----


def test_partial_then_full_then_confirmed() -> None:
    d = at(State.AWAITING_PAYMENT)
    d.record_payment(pay(20 * DOLLAR, confirmations=0, tx="0x1"), now=START)
    assert d.state == State.PARTIALLY_PAID
    d.record_payment(pay(30 * DOLLAR, confirmations=0, tx="0x2"), now=START)
    assert d.state == State.CONFIRMING
    d.record_payment(pay(20 * DOLLAR, confirmations=2, tx="0x1"), now=START)
    assert d.state == State.CONFIRMING  # the second one still needs confirmations
    d.record_payment(pay(30 * DOLLAR, confirmations=2, tx="0x2"), now=START + MIN)
    assert d.state == State.FUNDED
    assert d.funded_at == START + MIN
    assert d.received == d.confirmed == d.amount


def test_record_payment_is_idempotent() -> None:
    d = at(State.AWAITING_PAYMENT)
    assert d.record_payment(pay(d.amount, confirmations=1), now=START)
    assert not d.record_payment(pay(d.amount, confirmations=1), now=START)
    assert not d.record_payment(pay(d.amount, confirmations=0), now=START)  # confirmations never go down
    assert len(d.payments) == 1
    assert d.record_payment(pay(d.amount, confirmations=5), now=START)
    assert d.payments[0].confirmations == 5


def test_a_transfer_cannot_change_amount() -> None:
    d = at(State.AWAITING_PAYMENT)
    d.record_payment(pay(d.amount, confirmations=0), now=START)
    with pytest.raises(BadPayment):
        d.record_payment(pay(d.amount + 1, confirmations=1), now=START)


def test_two_transfers_in_one_transaction_are_separate() -> None:
    d = at(State.AWAITING_PAYMENT)
    d.record_payment(pay(25 * DOLLAR, index=0), now=START)
    d.record_payment(pay(25 * DOLLAR, index=1), now=START)
    assert d.state == State.FUNDED


def test_overpayment_funds_and_returns_the_excess() -> None:
    d = at(State.AWAITING_PAYMENT)
    d.record_payment(pay(d.amount + 7 * DOLLAR), now=START)
    assert d.state == State.FUNDED
    assert d.excess == 7 * DOLLAR
    d.confirm_delivery(by=Role.BUYER, user="alice", now=START)
    d.payout_address = PAYOUT
    assert d.transfers() is None  # needs somewhere to send the excess
    d.refund_address = REFUND
    plan = d.transfers() or []
    assert [(t.to, t.amount, t.purpose) for t in plan] == [
        (PAYOUT, d.amount - d.fee, "payout"),
        (REFUND, 7 * DOLLAR, "excess"),
    ]


def test_more_money_after_funded_is_still_credited() -> None:
    d = at(State.FUNDED)
    d.record_payment(pay(3 * DOLLAR, tx="0xmore"), now=START)
    assert d.state == State.FUNDED
    assert d.excess == 3 * DOLLAR


@pytest.mark.parametrize("state", [State.DISPUTED, State.RELEASE_PENDING, State.RELEASED, State.CANCELLED])
def test_late_payments_are_kept_apart_for_a_person(state: State) -> None:
    d = deal_in(state)
    received = d.received
    assert d.record_payment(pay(DOLLAR, tx="0xlate"), now=START)
    assert not d.record_payment(pay(DOLLAR, tx="0xlate"), now=START)
    assert d.received == received
    assert len(d.late_payments) == 1
    assert d.needs_review
    assert d.state == state


# ---- deadlines ----


def test_unpaid_deal_expires_at_the_deadline_not_before() -> None:
    d = at(State.AWAITING_PAYMENT)
    deadline = d.deadline
    assert deadline is not None
    assert not d.expire(now=deadline - MIN)
    assert d.expire(now=deadline)
    assert d.state == State.EXPIRED
    assert not d.expire(now=deadline + MIN)


def test_partly_paid_deal_is_refunded_at_the_deadline() -> None:
    d = at(State.PARTIALLY_PAID)
    assert d.expire(now=START + d.payment_window)
    assert d.state == State.REFUND_PENDING


def test_a_confirming_deal_never_expires() -> None:
    d = at(State.CONFIRMING)
    assert not d.expire(now=START + 10 * d.payment_window)
    assert d.state == State.CONFIRMING


# ---- delivery, release, refund ----


def test_release_path() -> None:
    d = at(State.FUNDED)
    d.mark_delivered(by=Role.SELLER, user="bob", now=START + MIN)
    assert d.delivered_at == START + MIN
    assert d.state == State.FUNDED
    d.confirm_delivery(by=Role.BUYER, user="alice", now=START)
    assert d.state == State.RELEASE_PENDING
    assert d.transfers() is None  # no payout address yet
    d.set_payout_address(by=Role.SELLER, user="bob", now=START, address=PAYOUT)
    plan = d.transfers() or []
    assert [(t.to, t.amount) for t in plan] == [(PAYOUT, 50 * DOLLAR - DOLLAR // 2)]
    d.begin_payout(now=START)
    d.mark_settled(["0xrelease"], now=START)
    assert d.state == State.RELEASED
    assert d.settle_txs == ["0xrelease"]


def test_seller_refund_path() -> None:
    d = at(State.FUNDED)
    d.request_refund(by=Role.SELLER, user="bob", now=START)
    assert d.state == State.REFUND_PENDING
    d.set_refund_address(by=Role.BUYER, user="alice", now=START, address=REFUND)
    assert [(t.to, t.amount, t.purpose) for t in d.transfers() or []] == [(REFUND, d.amount, "refund")]
    d.begin_payout(now=START)
    d.mark_settled(["0xrefund"], now=START)
    assert d.state == State.REFUNDED


def test_refund_of_nothing_sends_nothing() -> None:
    d = at(State.AWAITING_PAYMENT)
    d.state = State.REFUND_PENDING  # e.g. reached by an arbiter after a reorg wiped the payment
    d.refund_address = REFUND
    assert d.transfers() == []


def test_payout_is_started_once() -> None:
    d = at(State.RELEASE_PENDING)
    d.begin_payout(now=START)
    with pytest.raises(DealError, match="already started"):
        d.begin_payout(now=START)
    with pytest.raises(DealError, match="already being sent"):
        d.set_payout_address(by=Role.SELLER, user="bob", now=START, address=PAYOUT)
    with pytest.raises(DealError, match="already being sent"):
        d.set_refund_address(by=Role.BUYER, user="alice", now=START, address=REFUND)


def test_cannot_mark_settled_without_starting() -> None:
    with pytest.raises(DealError, match="Start the payout"):
        at(State.RELEASE_PENDING).mark_settled(["0x"], now=START)


def test_transfers_is_none_outside_settlement() -> None:
    assert at(State.FUNDED).transfers() is None


# ---- disputes ----


def test_dispute_needs_a_reason_and_sets_the_timer() -> None:
    d = at(State.FUNDED)
    with pytest.raises(DealError, match="went wrong"):
        d.open_dispute(by=Role.BUYER, user="alice", now=START, reason="  ")
    d.open_dispute(by=Role.BUYER, user="alice", now=START, reason=" Never arrived ")
    assert d.dispute is not None
    assert d.dispute.reason == "Never arrived"
    assert d.dispute.settle_at == START + d.dispute_window


def test_only_the_other_side_responds() -> None:
    d = at(State.DISPUTED)
    with pytest.raises(DealError, match="other side"):
        d.respond_dispute(by=Role.BUYER, user="alice", now=START, note="me again")
    d.respond_dispute(by=Role.SELLER, user="bob", now=START, note="I sent it")
    assert d.dispute is not None
    assert d.dispute.responded


@pytest.mark.parametrize(
    ("opener", "responded", "winner", "result"),
    [
        (Role.BUYER, False, Role.BUYER, State.REFUND_PENDING),  # seller never answered: buyer wins
        (Role.SELLER, False, Role.SELLER, State.RELEASE_PENDING),  # buyer never answered: seller wins
        (Role.BUYER, True, Role.BUYER, State.REFUND_PENDING),  # both engaged: money goes back to the payer
        (Role.SELLER, True, Role.BUYER, State.REFUND_PENDING),
    ],
)
def test_auto_settle_rule(opener: Role, responded: bool, winner: Role, result: State) -> None:
    d = at(State.FUNDED)
    user = "alice" if opener == Role.BUYER else "bob"
    d.open_dispute(by=opener, user=user, now=START, reason="Problem")
    if responded:
        other = Role.SELLER if opener == Role.BUYER else Role.BUYER
        d.respond_dispute(by=other, user="x", now=START, note="My side")
    assert not d.auto_settle(now=START + d.dispute_window - MIN)
    assert d.auto_settle(now=START + d.dispute_window)
    assert d.dispute is not None
    assert d.dispute.winner == winner
    assert d.state == result
    assert not d.auto_settle(now=START + 2 * d.dispute_window)


@pytest.mark.parametrize(
    ("winner", "result"), [(Role.SELLER, State.RELEASE_PENDING), (Role.BUYER, State.REFUND_PENDING)]
)
def test_arbiter_resolves(winner: Role, result: State) -> None:
    d = at(State.DISPUTED)
    d.resolve_dispute(by=Role.ARBITER, user="daniel", now=START, winner=winner)
    assert d.state == result


def test_arbiter_must_pick_a_party() -> None:
    with pytest.raises(DealError, match="buyer or the seller"):
        at(State.DISPUTED).resolve_dispute(by=Role.ARBITER, user="daniel", now=START, winner=Role.SYSTEM)


# ---- the event log and roles ----


def test_every_action_is_logged_with_the_resulting_state() -> None:
    d = at(State.RELEASED)
    actions = [e.action for e in d.events]
    assert actions == [
        Action.PROPOSE,
        Action.ACCEPT_TERMS,
        Action.RECORD_PAYMENT,
        Action.CONFIRM_DELIVERY,
        Action.BEGIN_PAYOUT,
        Action.MARK_RELEASED,
    ]
    assert d.events[-1].state == State.RELEASED
    assert d.events[1].detail["deposit_address"] == ADDR


def test_role_of() -> None:
    d = new_deal()
    assert d.role_of("alice") == Role.BUYER
    assert d.role_of("bob") == Role.SELLER
    assert d.role_of("eve") is None


def test_a_deal_round_trips_through_json() -> None:
    d = at(State.DISPUTED)
    again = Deal.model_validate_json(d.model_dump_json())
    assert again == d
    assert again.token == d.token
