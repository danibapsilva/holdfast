"""What people and the watcher can do with deals. The web API and the Discord bot both call this.

Each call loads the deal, applies one state-machine action as the person's role in that deal, and
saves it (with optimistic locking). Money only moves in `settle`, which writes down that a payout has
started before sending anything, so a crash mid-send is flagged for a person instead of retried.

The demo bot (`DEMO_USER`) can be the other side of any deal: it accepts the terms at once, pays when
it's the buyer (minting test tokens), releases once the seller says they've delivered, and takes the
payout at its own address when it's the seller.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from holdfast.chains import Chain, ChainError
from holdfast.deal import PAYING, Deal, DealError, Role, State
from holdfast.guards import Caps
from holdfast.money import Token
from holdfast.store import Store

DEMO_USER = "holdfast-demo"


class NotFound(DealError):
    pass


@dataclass(frozen=True)
class Policy:
    """The rules a new deal is made with (fixed on the deal, so changing these never affects old deals)."""

    required_confirmations: int = 2
    payment_window: dt.timedelta = dt.timedelta(minutes=30)
    dispute_window: dt.timedelta = dt.timedelta(minutes=10)
    fee_bps: int = 100  # a flat, clearly fake demo fee: 1% of the amount, kept at the deposit address


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class EscrowService:
    def __init__(
        self,
        store: Store,
        chain: Chain,
        token: Token,
        *,
        caps: Caps,
        policy: Policy | None = None,
        clock: Callable[[], dt.datetime] = utcnow,
    ) -> None:
        self.store = store
        self.chain = chain
        self.token = token
        self.caps = caps
        self.policy = policy or Policy()
        self.clock = clock

    # ---- helpers ----

    def get(self, deal_id: str) -> Deal:
        deal = self.store.get(deal_id)
        if deal is None:
            raise NotFound("No deal with that id")
        return deal

    def _role(self, deal: Deal, user: str) -> Role:
        role = deal.role_of(user)
        if role is None:
            raise DealError("You're not part of this deal")
        return role

    def _is_demo(self, deal: Deal) -> bool:
        return DEMO_USER in (deal.buyer_id, deal.seller_id)

    # ---- making a deal ----

    def create_deal(
        self, *, creator: str, creator_role: Role, counterparty: str, amount: int, description: str
    ) -> Deal:
        """A deal proposed by `creator`, waiting for `counterparty` to accept the terms."""
        if creator_role not in (Role.BUYER, Role.SELLER):
            raise DealError("You're the buyer or the seller")
        if creator == counterparty:
            raise DealError("A deal needs two different people")
        if not description.strip():
            raise DealError("Say what's being traded")
        self.caps.check_deal(amount)
        now = self.clock()
        buyer, seller = (creator, counterparty) if creator_role == Role.BUYER else (counterparty, creator)
        deal = Deal(
            id=uuid.uuid4().hex[:12],
            number=self.store.next_number(),
            buyer_id=buyer,
            seller_id=seller,
            creator=creator_role,
            token=self.token,
            amount=amount,
            fee=amount * self.policy.fee_bps // 10_000,
            description=description.strip(),
            created_at=now,
            required_confirmations=self.policy.required_confirmations,
            payment_window=self.policy.payment_window,
            dispute_window=self.policy.dispute_window,
        )
        deal.propose(by=creator_role, user=creator, now=now)
        self.store.insert(deal)
        if counterparty == DEMO_USER:
            self._demo_accept(deal.id)
        return self.get(deal.id)

    def start_demo(self, *, visitor: str, visitor_role: Role, amount: int, description: str) -> Deal:
        """A deal against the demo bot, so one person can try the whole flow alone."""
        return self.create_deal(
            creator=visitor, creator_role=visitor_role, counterparty=DEMO_USER, amount=amount, description=description
        )

    def accept_terms(self, deal_id: str, user: str) -> Deal:
        deal = self.get(deal_id)
        deal.accept_terms(
            by=self._role(deal, user),
            user=user,
            now=self.clock(),
            deposit_address=self.chain.deposit_address(deal.number),
        )
        self.store.save(deal)
        return deal

    def _demo_accept(self, deal_id: str) -> None:
        deal = self.accept_terms(deal_id, DEMO_USER)
        demo = self.chain.demo_address()
        if deal.seller_id == DEMO_USER:
            self.set_payout_address(deal_id, DEMO_USER, demo)
        else:
            self.set_refund_address(deal_id, DEMO_USER, demo)
            self.chain.fund_demo(self.get(deal_id).deposit_address or "", self.token, deal.amount)

    # ---- the parties' actions ----

    def set_payout_address(self, deal_id: str, user: str, address: str) -> Deal:
        deal = self.get(deal_id)
        deal.set_payout_address(
            by=self._role(deal, user), user=user, now=self.clock(), address=self.chain.normalize_address(address)
        )
        self.store.save(deal)
        return self._settle_if_ready(deal)

    def set_refund_address(self, deal_id: str, user: str, address: str) -> Deal:
        deal = self.get(deal_id)
        deal.set_refund_address(
            by=self._role(deal, user), user=user, now=self.clock(), address=self.chain.normalize_address(address)
        )
        self.store.save(deal)
        return self._settle_if_ready(deal)

    def demo_pay(self, deal_id: str, user: str) -> str:
        """The web playground's Pay button: for a demo deal's buyer, mint the payment to the deposit address."""
        deal = self.get(deal_id)
        if not self._is_demo(deal) or self._role(deal, user) != Role.BUYER:
            raise DealError("Only the buyer in a demo deal can use the demo payment")
        if deal.state != State.AWAITING_PAYMENT or not deal.deposit_address:
            raise DealError("This deal isn't waiting for a payment")
        return self.chain.fund_demo(deal.deposit_address, self.token, deal.amount)

    def cancel(self, deal_id: str, user: str) -> Deal:
        deal = self.get(deal_id)
        deal.cancel(by=self._role(deal, user), user=user, now=self.clock())
        self.store.save(deal)
        return self._settle_if_ready(deal)

    def mark_delivered(self, deal_id: str, user: str) -> Deal:
        deal = self.get(deal_id)
        deal.mark_delivered(by=self._role(deal, user), user=user, now=self.clock())
        self.store.save(deal)
        if deal.buyer_id == DEMO_USER:  # the demo bot trusts the delivery and releases
            return self.confirm_delivery(deal_id, DEMO_USER)
        return deal

    def confirm_delivery(self, deal_id: str, user: str) -> Deal:
        deal = self.get(deal_id)
        deal.confirm_delivery(by=self._role(deal, user), user=user, now=self.clock())
        self.store.save(deal)
        return self._settle_if_ready(deal)

    def request_refund(self, deal_id: str, user: str) -> Deal:
        deal = self.get(deal_id)
        deal.request_refund(by=self._role(deal, user), user=user, now=self.clock())
        self.store.save(deal)
        return self._settle_if_ready(deal)

    def open_dispute(self, deal_id: str, user: str, reason: str) -> Deal:
        deal = self.get(deal_id)
        deal.open_dispute(by=self._role(deal, user), user=user, now=self.clock(), reason=reason)
        self.store.save(deal)
        return deal

    def respond_dispute(self, deal_id: str, user: str, note: str) -> Deal:
        deal = self.get(deal_id)
        deal.respond_dispute(by=self._role(deal, user), user=user, now=self.clock(), note=note)
        self.store.save(deal)
        return deal

    def resolve_dispute(self, deal_id: str, arbiter: str, winner: Role) -> Deal:
        deal = self.get(deal_id)
        deal.resolve_dispute(by=Role.ARBITER, user=arbiter, now=self.clock(), winner=winner)
        self.store.save(deal)
        return self._settle_if_ready(deal)

    # ---- the watcher ----

    def check_payments(self, deal_id: str) -> Deal:
        """Look on-chain for transfers to the deal's address and credit the new ones (idempotent)."""
        deal = self.get(deal_id)
        if not deal.deposit_address:
            return deal
        changed = False
        for payment in self.chain.payments_to(deal.deposit_address, deal.token):
            if not self.store.claim_transfer(payment.key, deal.id):
                deal.needs_review = True  # credited to another deal already: never count it twice
                changed = True
                continue
            changed |= deal.record_payment(payment, now=self.clock())
        if changed:
            self.store.save(deal)
        return deal

    def tick(self) -> list[str]:
        """One pass of the background worker: payments, deadlines, dispute timers, pending payouts.
        Returns the ids of deals that changed."""
        changed: list[str] = []
        for listed in self.store.open_deals():
            before = (listed.state, listed.version)
            deal = listed
            if deal.state in PAYING or deal.state == State.AWAITING_TERMS_ACCEPTANCE:
                deal = self.check_payments(deal.id)
            now = self.clock()
            if deal.expire(now=now) or deal.auto_settle(now=now):
                self.store.save(deal)
            settled = self._settle_if_ready(deal)
            if (settled.state, settled.version) != before:
                changed.append(settled.id)
        return changed

    # ---- moving the money ----

    def _settle_if_ready(self, deal: Deal) -> Deal:
        if deal.state in (State.RELEASE_PENDING, State.REFUND_PENDING) and not deal.payout_started:
            return self.settle(deal.id)
        return deal

    def settle(self, deal_id: str) -> Deal:
        """Send what a release or refund owes. Waits (does nothing) while an address is missing."""
        deal = self.get(deal_id)
        transfers = deal.transfers()
        if transfers is None or deal.payout_started:
            return deal
        now = self.clock()
        total = sum(t.amount for t in transfers)
        self.caps.check_send(total, now)
        deal.begin_payout(now=now)
        self.store.save(deal)  # written down before any money moves
        txs: list[str] = []
        try:
            for t in transfers:
                txs.append(self.chain.send(deal.number, t.to, deal.token, t.amount))
        except ChainError:
            deal.needs_review = True  # some may have gone out: a person checks the chain before retrying
            deal.settle_txs = txs
            self.store.save(deal)
            raise
        self.caps.record_send(total, now)
        deal.mark_settled(txs, now=self.clock())
        self.store.save(deal)
        return deal
