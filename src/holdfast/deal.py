"""The deal: a state machine around one escrowed trade. Pure code, no I/O.

A buyer pays the deal's deposit address; once the payment is confirmed on-chain the deal is funded,
and the money goes to the seller when the buyer confirms delivery, or back to the buyer on a refund.
Every action names who is acting and when (`now` is passed in, never read from a clock), is checked
against the transition table below, and is written to the deal's event log.

States and how they move:

    draft --propose--> awaiting_terms_acceptance --accept--> awaiting_payment
    awaiting_payment --payment--> partially_paid | confirming | funded
    partially_paid --payment--> confirming | funded;  --deadline or cancel--> refund_pending
    confirming --confirmations--> funded
    funded --buyer confirms delivery--> release_pending --sent--> released
    funded --seller refunds--> refund_pending --sent--> refunded
    funded --either opens a dispute--> disputed --arbiter or timer--> release_pending | refund_pending
    draft | awaiting_* --cancel--> cancelled;  awaiting_* --deadline--> expired

The dispute timer (`auto_settle`): if the other side never answers, the side that opened the dispute
wins; if both sides are engaged, the buyer is refunded, because returning money to whoever paid it is
the outcome that can't strand anyone's funds.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from holdfast.errors import HoldfastError
from holdfast.money import Token


class State(StrEnum):
    DRAFT = "draft"
    AWAITING_TERMS_ACCEPTANCE = "awaiting_terms_acceptance"
    AWAITING_PAYMENT = "awaiting_payment"
    PARTIALLY_PAID = "partially_paid"
    CONFIRMING = "confirming"
    FUNDED = "funded"
    RELEASE_PENDING = "release_pending"
    RELEASED = "released"
    REFUND_PENDING = "refund_pending"
    REFUNDED = "refunded"
    DISPUTED = "disputed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL = frozenset({State.RELEASED, State.REFUNDED, State.CANCELLED, State.EXPIRED})
PAYING = frozenset({State.AWAITING_PAYMENT, State.PARTIALLY_PAID, State.CONFIRMING, State.FUNDED})


class Role(StrEnum):
    BUYER = "buyer"
    SELLER = "seller"
    SYSTEM = "system"  # the watcher and the timers
    ARBITER = "arbiter"


class Action(StrEnum):
    EDIT_TERMS = "edit_terms"
    PROPOSE = "propose"
    ACCEPT_TERMS = "accept_terms"
    CANCEL = "cancel"
    RECORD_PAYMENT = "record_payment"
    EXPIRE = "expire"
    MARK_DELIVERED = "mark_delivered"
    CONFIRM_DELIVERY = "confirm_delivery"
    REQUEST_REFUND = "request_refund"
    OPEN_DISPUTE = "open_dispute"
    RESPOND_DISPUTE = "respond_dispute"
    RESOLVE_DISPUTE = "resolve_dispute"
    AUTO_SETTLE = "auto_settle"
    SET_PAYOUT_ADDRESS = "set_payout_address"
    SET_REFUND_ADDRESS = "set_refund_address"
    BEGIN_PAYOUT = "begin_payout"
    MARK_RELEASED = "mark_released"
    MARK_REFUNDED = "mark_refunded"


_PARTIES = frozenset({Role.BUYER, Role.SELLER})
_OPEN = frozenset(set(State) - TERMINAL)

# Which states each action may happen in, and who may do it. Anything else is refused.
TRANSITIONS: dict[Action, tuple[frozenset[State], frozenset[Role]]] = {
    Action.EDIT_TERMS: (frozenset({State.DRAFT}), _PARTIES),
    Action.PROPOSE: (frozenset({State.DRAFT}), _PARTIES),
    Action.ACCEPT_TERMS: (frozenset({State.AWAITING_TERMS_ACCEPTANCE}), _PARTIES),
    Action.CANCEL: (
        frozenset({State.DRAFT, State.AWAITING_TERMS_ACCEPTANCE, State.AWAITING_PAYMENT, State.PARTIALLY_PAID}),
        _PARTIES,
    ),
    Action.RECORD_PAYMENT: (PAYING, frozenset({Role.SYSTEM})),
    Action.EXPIRE: (
        frozenset({State.AWAITING_TERMS_ACCEPTANCE, State.AWAITING_PAYMENT, State.PARTIALLY_PAID}),
        frozenset({Role.SYSTEM}),
    ),
    Action.MARK_DELIVERED: (frozenset({State.FUNDED}), frozenset({Role.SELLER})),
    Action.CONFIRM_DELIVERY: (frozenset({State.FUNDED}), frozenset({Role.BUYER})),
    Action.REQUEST_REFUND: (frozenset({State.FUNDED}), frozenset({Role.SELLER})),
    Action.OPEN_DISPUTE: (frozenset({State.FUNDED}), _PARTIES),
    Action.RESPOND_DISPUTE: (frozenset({State.DISPUTED}), _PARTIES),
    Action.RESOLVE_DISPUTE: (frozenset({State.DISPUTED}), frozenset({Role.ARBITER})),
    Action.AUTO_SETTLE: (frozenset({State.DISPUTED}), frozenset({Role.SYSTEM})),
    Action.SET_PAYOUT_ADDRESS: (_OPEN, frozenset({Role.SELLER})),
    Action.SET_REFUND_ADDRESS: (_OPEN, frozenset({Role.BUYER})),
    Action.BEGIN_PAYOUT: (frozenset({State.RELEASE_PENDING, State.REFUND_PENDING}), frozenset({Role.SYSTEM})),
    Action.MARK_RELEASED: (frozenset({State.RELEASE_PENDING}), frozenset({Role.SYSTEM})),
    Action.MARK_REFUNDED: (frozenset({State.REFUND_PENDING}), frozenset({Role.SYSTEM})),
}


class DealError(HoldfastError):
    """Something the deal can't do. The message is safe to show the person who tried."""


class IllegalTransition(DealError):
    def __init__(self, action: Action, state: State) -> None:
        super().__init__(f"Can't {action.value.replace('_', ' ')} while the deal is {state.value.replace('_', ' ')}")
        self.action, self.state = action, state


class NotAllowed(DealError):
    def __init__(self, action: Action, role: Role) -> None:
        super().__init__(f"The {role.value} can't {action.value.replace('_', ' ')}")
        self.action, self.role = action, role


class BadPayment(DealError):
    pass


class Payment(BaseModel):
    """One incoming transfer to the deal's deposit address."""

    model_config = ConfigDict(validate_assignment=True)

    tx_hash: str
    log_index: int = 0  # an ERC-20 transfer's position in its transaction; 0 for native coin
    amount: int = Field(gt=0)
    confirmations: int = Field(ge=0)

    @property
    def key(self) -> str:
        """Unique per transfer on the chain: what the double-credit guard is keyed on."""
        return f"{self.tx_hash.lower()}:{self.log_index}"


class TransferPurpose(StrEnum):
    PAYOUT = "payout"  # the amount less the fee, to the seller
    EXCESS = "excess"  # anything paid beyond the amount, back to the buyer
    REFUND = "refund"  # everything received, back to the buyer


class Transfer(BaseModel):
    """Money the deal owes someone, once it settles."""

    to: str
    amount: int
    purpose: TransferPurpose


class Event(BaseModel):
    at: dt.datetime
    actor: Role
    user: str | None
    action: Action
    state: State  # after the action
    detail: dict[str, str] = Field(default_factory=dict[str, str])


class Dispute(BaseModel):
    opened_by: Role
    reason: str
    opened_at: dt.datetime
    settle_at: dt.datetime
    responded: bool = False
    winner: Role | None = None


class Deal(BaseModel):
    """One trade. `amount` is what the buyer pays; the seller gets `amount - fee`."""

    model_config = ConfigDict(validate_assignment=True)  # a change is checked like a new deal is

    id: str
    number: int  # derives the deposit address, so every deal gets its own
    buyer_id: str
    seller_id: str
    creator: Role
    token: Token
    amount: int = Field(gt=0)
    fee: int = Field(ge=0)
    description: str
    created_at: dt.datetime

    # policy, fixed when the deal is made so the rules can't change under it
    required_confirmations: int = Field(ge=1)
    payment_window: dt.timedelta
    dispute_window: dt.timedelta

    state: State = State.DRAFT
    deadline: dt.datetime | None = None  # to accept the terms, then to pay
    deposit_address: str | None = None
    watch_from_block: int | None = Field(default=None, ge=0)  # the block its address was handed out at
    payout_address: str | None = None  # the seller's
    refund_address: str | None = None  # the buyer's
    payments: list[Payment] = Field(default_factory=list[Payment])
    late_payments: list[Payment] = Field(default_factory=list[Payment])  # after it closed: to return by hand
    delivered_at: dt.datetime | None = None
    funded_at: dt.datetime | None = None
    dispute: Dispute | None = None
    payout_started: bool = False
    settle_txs: list[str] = Field(default_factory=list[str])
    needs_review: bool = False
    version: int = 0  # bumped by the store on every save (optimistic locking)
    events: list[Event] = Field(default_factory=list[Event])

    # ---- reading it ----

    @property
    def received(self) -> int:
        """Everything seen at the deposit address, confirmed or not."""
        return sum(p.amount for p in self.payments)

    @property
    def confirmed(self) -> int:
        return sum(p.amount for p in self.payments if p.confirmations >= self.required_confirmations)

    @property
    def excess(self) -> int:
        """Paid beyond the deal amount: returned to the buyer when it settles."""
        return max(0, self.received - self.amount)

    @property
    def closed(self) -> bool:
        return self.state in TERMINAL

    def role_of(self, user: str) -> Role | None:
        if user == self.buyer_id:
            return Role.BUYER
        if user == self.seller_id:
            return Role.SELLER
        return None

    def transfers(self) -> list[Transfer] | None:
        """What settling this deal sends, or None while an address it needs is missing."""
        if self.state == State.RELEASE_PENDING:
            if not self.payout_address or (self.excess and not self.refund_address):
                return None
            out = [Transfer(to=self.payout_address, amount=self.amount - self.fee, purpose=TransferPurpose.PAYOUT)]
            if self.excess:
                out.append(Transfer(to=self.refund_address or "", amount=self.excess, purpose=TransferPurpose.EXCESS))
            return out
        if self.state == State.REFUND_PENDING:
            if not self.refund_address:
                return None
            return (
                [Transfer(to=self.refund_address, amount=self.received, purpose=TransferPurpose.REFUND)]
                if self.received
                else []
            )
        return None

    # ---- the actions ----

    def _open_dispute(self) -> Dispute:
        """The dispute a dispute action works on (the transition table already put the deal in DISPUTED)."""
        if self.dispute is None:
            raise DealError("This deal has no open dispute")
        return self.dispute

    def _check(self, action: Action, by: Role) -> None:
        states, roles = TRANSITIONS[action]
        if by not in roles:
            raise NotAllowed(action, by)
        if self.state not in states:
            raise IllegalTransition(action, self.state)

    def _log(self, action: Action, by: Role, user: str | None, now: dt.datetime, **detail: object) -> None:
        self.events.append(
            Event(
                at=now,
                actor=by,
                user=user,
                action=action,
                state=self.state,
                detail={k: str(v) for k, v in detail.items()},
            )
        )

    def edit_terms(self, *, by: Role, user: str, now: dt.datetime, amount: int, fee: int, description: str) -> None:
        self._check(Action.EDIT_TERMS, by)
        if not 0 <= fee < amount:
            raise DealError("The fee must be less than the amount")
        self.amount, self.fee, self.description = amount, fee, description
        self._log(Action.EDIT_TERMS, by, user, now, amount=amount, fee=fee)

    def propose(self, *, by: Role, user: str, now: dt.datetime) -> None:
        self._check(Action.PROPOSE, by)
        if by != self.creator:
            raise NotAllowed(Action.PROPOSE, by)
        self.state = State.AWAITING_TERMS_ACCEPTANCE
        self.deadline = now + self.payment_window
        self._log(Action.PROPOSE, by, user, now)

    def accept_terms(self, *, by: Role, user: str, now: dt.datetime, deposit_address: str) -> None:
        """The other party agrees; the deal gets its deposit address and the payment clock starts."""
        self._check(Action.ACCEPT_TERMS, by)
        if by == self.creator:
            raise DealError("The other party has to accept the terms")
        if self.deadline and now >= self.deadline:
            raise DealError("This offer has expired")
        self.state = State.AWAITING_PAYMENT
        self.deposit_address = deposit_address
        self.deadline = now + self.payment_window
        self._log(Action.ACCEPT_TERMS, by, user, now, deposit_address=deposit_address)

    def cancel(self, *, by: Role, user: str, now: dt.datetime) -> None:
        """Before money is in, the deal just ends; with a partial payment in, it's refunded."""
        self._check(Action.CANCEL, by)
        self.state = State.REFUND_PENDING if self.received else State.CANCELLED
        self._log(Action.CANCEL, by, user, now)

    def record_payment(self, payment: Payment, *, now: dt.datetime) -> bool:
        """The watcher saw a transfer to the deposit address (or more confirmations of one it saw).

        Idempotent: the same transfer again only updates its confirmations, which never go down.
        Returns whether anything changed. A payment after the deal is past taking them is kept apart
        and flagged for a person to return.
        """
        if self.state not in PAYING:
            if any(p.key == payment.key for p in self.late_payments):
                return False
            self.late_payments.append(payment)
            self.needs_review = True
            self._log(Action.RECORD_PAYMENT, Role.SYSTEM, None, now, late=payment.key, amount=payment.amount)
            return True
        known = next((p for p in self.payments if p.key == payment.key), None)
        if known:
            if known.amount != payment.amount:
                raise BadPayment(f"Transfer {payment.key} changed amount")
            if payment.confirmations <= known.confirmations:
                return False
            known.confirmations = payment.confirmations
        else:
            self.payments.append(payment)
        before = self.state
        if self.state != State.FUNDED:
            if self.received < self.amount:
                self.state = State.PARTIALLY_PAID
            elif self.confirmed >= self.amount:
                self.state = State.FUNDED
                self.funded_at = now
            else:
                self.state = State.CONFIRMING
        self._log(
            Action.RECORD_PAYMENT,
            Role.SYSTEM,
            None,
            now,
            transfer=payment.key,
            amount=payment.amount,
            confirmations=payment.confirmations,
            was=before.value,
        )
        return True

    def expire(self, *, now: dt.datetime) -> bool:
        """The deadline passed: an offer or an unpaid deal ends; a partly paid one is refunded."""
        if self.state not in TRANSITIONS[Action.EXPIRE][0] or not self.deadline or now < self.deadline:
            return False
        self.state = State.REFUND_PENDING if self.received else State.EXPIRED
        self._log(Action.EXPIRE, Role.SYSTEM, None, now)
        return True

    def mark_delivered(self, *, by: Role, user: str, now: dt.datetime) -> None:
        """The seller says they've delivered. It's a nudge to the buyer; only the buyer releases."""
        self._check(Action.MARK_DELIVERED, by)
        self.delivered_at = now
        self._log(Action.MARK_DELIVERED, by, user, now)

    def confirm_delivery(self, *, by: Role, user: str, now: dt.datetime) -> None:
        self._check(Action.CONFIRM_DELIVERY, by)
        self.state = State.RELEASE_PENDING
        self._log(Action.CONFIRM_DELIVERY, by, user, now)

    def request_refund(self, *, by: Role, user: str, now: dt.datetime) -> None:
        """The seller backs out after being paid: the buyer gets everything back."""
        self._check(Action.REQUEST_REFUND, by)
        self.state = State.REFUND_PENDING
        self._log(Action.REQUEST_REFUND, by, user, now)

    def open_dispute(self, *, by: Role, user: str, now: dt.datetime, reason: str) -> None:
        self._check(Action.OPEN_DISPUTE, by)
        if not reason.strip():
            raise DealError("Say what went wrong")
        self.state = State.DISPUTED
        self.dispute = Dispute(opened_by=by, reason=reason.strip(), opened_at=now, settle_at=now + self.dispute_window)
        self._log(Action.OPEN_DISPUTE, by, user, now, reason=reason.strip())

    def respond_dispute(self, *, by: Role, user: str, now: dt.datetime, note: str) -> None:
        """The other side answers, so the timer can't settle it by default against them."""
        self._check(Action.RESPOND_DISPUTE, by)
        dispute = self._open_dispute()
        if by == dispute.opened_by:
            raise DealError("The other side answers a dispute")
        dispute.responded = True
        self._log(Action.RESPOND_DISPUTE, by, user, now, note=note.strip())

    def resolve_dispute(self, *, by: Role, user: str, now: dt.datetime, winner: Role) -> None:
        self._check(Action.RESOLVE_DISPUTE, by)
        self._settle_dispute(winner, Action.RESOLVE_DISPUTE, by, user, now)

    def auto_settle(self, *, now: dt.datetime) -> bool:
        """The dispute timer ran out. See the module docstring for the rule."""
        if self.state != State.DISPUTED or not self.dispute or now < self.dispute.settle_at:
            return False
        winner = Role.BUYER if self.dispute.responded else self.dispute.opened_by
        self._settle_dispute(winner, Action.AUTO_SETTLE, Role.SYSTEM, None, now)
        return True

    def _settle_dispute(self, winner: Role, action: Action, by: Role, user: str | None, now: dt.datetime) -> None:
        if winner not in _PARTIES:
            raise DealError("A dispute is won by the buyer or the seller")
        self._open_dispute().winner = winner
        self.state = State.RELEASE_PENDING if winner == Role.SELLER else State.REFUND_PENDING
        self._log(action, by, user, now, winner=winner.value)

    def set_payout_address(self, *, by: Role, user: str, now: dt.datetime, address: str) -> None:
        self._check(Action.SET_PAYOUT_ADDRESS, by)
        if self.payout_started:
            raise DealError("The payout is already being sent")
        self.payout_address = address
        self._log(Action.SET_PAYOUT_ADDRESS, by, user, now, address=address)

    def set_refund_address(self, *, by: Role, user: str, now: dt.datetime, address: str) -> None:
        self._check(Action.SET_REFUND_ADDRESS, by)
        if self.payout_started:
            raise DealError("The refund is already being sent")
        self.refund_address = address
        self._log(Action.SET_REFUND_ADDRESS, by, user, now, address=address)

    def begin_payout(self, *, now: dt.datetime) -> None:
        """Written down (and saved) before any money is sent, so a crash mid-send is never retried blindly."""
        self._check(Action.BEGIN_PAYOUT, Role.SYSTEM)
        if self.payout_started:
            raise DealError("This payout was already started; it needs a person to check the chain")
        self.payout_started = True
        self._log(Action.BEGIN_PAYOUT, Role.SYSTEM, None, now)

    def mark_settled(self, txs: list[str], *, now: dt.datetime) -> None:
        """The settlement transfers are sent: released to the seller, or refunded to the buyer."""
        action = Action.MARK_RELEASED if self.state == State.RELEASE_PENDING else Action.MARK_REFUNDED
        self._check(action, Role.SYSTEM)
        if not self.payout_started:
            raise DealError("Start the payout before marking it sent")
        self.settle_txs = txs
        self.state = State.RELEASED if action == Action.MARK_RELEASED else State.REFUNDED
        self._log(action, Role.SYSTEM, None, now, txs=",".join(txs))
