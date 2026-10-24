#!/usr/bin/env python3
"""One whole trade against the demo bot, on the in-memory chain, narrated step by step.

    python examples/demo_trade.py

Nothing touches a network: `FakeChain` plays the blockchain and blocks are mined by hand, so you can watch
a payment go from seen to confirmed. The same calls drive the real thing on Sepolia.
"""

from __future__ import annotations

from holdfast import DEMO_USER, EscrowService, MemoryStore, Role, format_amount, parse_amount, tusd
from holdfast.chains.fake import FakeChain
from holdfast.deal import Deal
from holdfast.guards import Caps

TUSD = tusd("0x" + "ab" * 20)  # any address will do on the fake chain
ALICE = "alice"


def say(step: str, deal: Deal) -> None:
    print(f"  {step:<44} {deal.state:<18} received {format_amount(deal.received, TUSD)}")


def main() -> None:
    chain = FakeChain()
    service = EscrowService(
        MemoryStore(),
        chain,
        TUSD,
        caps=Caps(per_deal=parse_amount("100", TUSD), per_day=parse_amount("1000", TUSD)),
    )
    print("Alice sells a design to the demo bot for 25 tUSD.\n")

    deal = service.start_demo(
        visitor=ALICE, visitor_role=Role.SELLER, amount=parse_amount("25", TUSD), description="A logo"
    )
    say("The demo bot accepts and pays the deposit", deal)

    service.set_payout_address(deal.id, ALICE, "0x" + "a1" * 20)
    deal = service.check_payments(deal.id)
    say("The watcher sees the payment (1 block)", deal)

    chain.mine()
    deal = service.check_payments(deal.id)
    say("One more block: confirmed, so funded", deal)

    deal = service.mark_delivered(deal.id, ALICE)
    say(f"Alice delivers; {DEMO_USER} releases", deal)

    payout = deal.amount - deal.fee
    print(f"\nAlice was paid {format_amount(payout, TUSD)} (the demo's 1% fee stays put).")
    print("The deal's log:")
    for event in deal.events:
        who = event.user or event.actor
        print(f"  {event.at:%H:%M:%S}  {who:<14} {event.action:<18} -> {event.state}")


if __name__ == "__main__":
    main()
