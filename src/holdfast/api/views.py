"""A deal turned into what its party sees: amounts formatted, links to the explorer, and the next actions.

The actions come from the state machine's own transition table, so the API never offers a button the deal
would refuse; the few extra conditions (a dispute is answered by the other side, an address is fixed once a
payout starts) are the same ones the deal checks.
"""

from __future__ import annotations

from holdfast.api.schemas import Amount, DealView, DisputeView, EventView, Link, PaymentView
from holdfast.chains import Chain
from holdfast.deal import TRANSITIONS, Action, Deal, Role, State
from holdfast.money import Token, format_amount
from holdfast.service import DEMO_USER

# the API's name for each action a party can take
PARTY_ACTIONS: dict[str, Action] = {
    "cancel": Action.CANCEL,
    "deliver": Action.MARK_DELIVERED,
    "confirm": Action.CONFIRM_DELIVERY,
    "refund": Action.REQUEST_REFUND,
    "dispute": Action.OPEN_DISPUTE,
    "respond": Action.RESPOND_DISPUTE,
    "payout_address": Action.SET_PAYOUT_ADDRESS,
    "refund_address": Action.SET_REFUND_ADDRESS,
}


def amount(units: int, token: Token) -> Amount:
    return Amount(units=str(units), text=format_amount(units, token))


def available_actions(deal: Deal, role: Role) -> list[str]:
    """What this party can do now, by the transition table plus the deal's own extra conditions."""
    found: list[str] = []
    if role == Role.BUYER and deal.seller_id == DEMO_USER and deal.state == State.AWAITING_PAYMENT:
        found.append("pay")  # the demo bot is the seller: the playground pays for the visitor
    for name, action in PARTY_ACTIONS.items():
        states, roles = TRANSITIONS[action]
        if deal.state not in states or role not in roles:
            continue
        if action == Action.RESPOND_DISPUTE and (
            deal.dispute is None or deal.dispute.opened_by == role or deal.dispute.responded
        ):
            continue
        if action in (Action.SET_PAYOUT_ADDRESS, Action.SET_REFUND_ADDRESS) and deal.payout_started:
            continue
        if action == Action.MARK_DELIVERED and deal.delivered_at:
            continue
        found.append(name)
    return found


def deal_view(deal: Deal, role: Role, chain: Chain) -> DealView:
    token = deal.token
    dispute = deal.dispute
    return DealView(
        id=deal.id,
        state=deal.state,
        you=role,
        with_demo_bot=DEMO_USER in (deal.buyer_id, deal.seller_id),
        description=deal.description,
        amount=amount(deal.amount, token),
        fee=amount(deal.fee, token),
        payout=amount(deal.amount - deal.fee, token),
        received=amount(deal.received, token),
        confirmed=amount(deal.confirmed, token),
        required_confirmations=deal.required_confirmations,
        deadline=deal.deadline,
        deposit=Link(value=deal.deposit_address, url=chain.address_url(deal.deposit_address))
        if deal.deposit_address
        else None,
        payout_address=deal.payout_address,
        refund_address=deal.refund_address,
        payments=[
            PaymentView(
                tx=Link(value=p.tx_hash, url=chain.tx_url(p.tx_hash)),
                amount=amount(p.amount, token),
                confirmations=p.confirmations,
                counted=p.confirmations >= deal.required_confirmations,
            )
            for p in deal.payments
        ],
        events=[EventView(at=e.at, actor=e.actor, action=e.action, state=e.state) for e in deal.events],
        dispute=DisputeView(
            opened_by=dispute.opened_by,
            reason=dispute.reason,
            settle_at=dispute.settle_at,
            responded=dispute.responded,
            winner=dispute.winner,
        )
        if dispute
        else None,
        settlements=[Link(value=tx, url=chain.tx_url(tx)) for tx in deal.settle_txs],
        needs_review=deal.needs_review,
        actions=[] if deal.closed else available_actions(deal, role),
    )
