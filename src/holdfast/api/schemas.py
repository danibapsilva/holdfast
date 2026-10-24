"""What goes over the wire. Amounts travel as both base units (a string, since a JavaScript number can't
hold every wei amount exactly) and display text; every address and transaction comes with its explorer link.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from holdfast.deal import Action, Role, State

AMOUNT_TEXT_LENGTH = 32
DESCRIPTION_LENGTH = 200
NOTE_LENGTH = 500
ADDRESS_LENGTH = 100


# ---- requests ----


class NewDemoDeal(BaseModel):
    """The visitor's side of the trade; the demo bot takes the other."""

    role: Literal["buyer", "seller"]
    amount: str = Field(min_length=1, max_length=AMOUNT_TEXT_LENGTH, examples=["25"])
    description: str = Field(min_length=1, max_length=DESCRIPTION_LENGTH, examples=["A logo"])


class DisputeIn(BaseModel):
    reason: str = Field(min_length=1, max_length=NOTE_LENGTH)


class RespondIn(BaseModel):
    note: str = Field(default="", max_length=NOTE_LENGTH)


class AddressIn(BaseModel):
    address: str = Field(min_length=1, max_length=ADDRESS_LENGTH)


# ---- responses ----


class Amount(BaseModel):
    units: str  # base units, as text
    text: str  # "24.75 tUSD"


class Link(BaseModel):
    value: str  # an address or a transaction hash
    url: str  # where to see it on the explorer


class PaymentView(BaseModel):
    tx: Link
    amount: Amount
    confirmations: int
    counted: bool  # has enough confirmations to count toward funding


class EventView(BaseModel):
    at: dt.datetime
    actor: Role
    action: Action
    state: State


class DisputeView(BaseModel):
    opened_by: Role
    reason: str
    settle_at: dt.datetime
    responded: bool
    winner: Role | None


class DealView(BaseModel):
    """One deal, as its party sees it, with the actions open to them right now."""

    id: str
    state: State
    you: Role
    with_demo_bot: bool
    description: str
    amount: Amount
    fee: Amount
    payout: Amount  # what the seller receives
    received: Amount
    confirmed: Amount
    required_confirmations: int
    deadline: dt.datetime | None
    deposit: Link | None
    payout_address: str | None
    refund_address: str | None
    payments: list[PaymentView]
    events: list[EventView]
    dispute: DisputeView | None
    settlements: list[Link]
    needs_review: bool
    actions: list[str]


class CreatedDeal(BaseModel):
    token: str  # keep it: every later call about this deal sends it as a Bearer token
    deal: DealView


class PaymentSent(BaseModel):
    tx: Link
    deal: DealView


class Health(BaseModel):
    ok: bool
    network: str
    chain_id: int
    head: int
    token: str
    token_address: str | None
    treasury: Link


class ErrorBody(BaseModel):
    error: str
