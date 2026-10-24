"""An in-memory chain for tests and local demos: blocks, balances, and transfers, nothing real.

Blocks advance only when `mine()` is called, so tests decide exactly when a payment confirms.
"""

from __future__ import annotations

import hashlib
import itertools
import re
from dataclasses import dataclass

from holdfast.chains import BadAddress, ChainError
from holdfast.deal import Payment
from holdfast.guards import SEPOLIA, require_testnet
from holdfast.money import Token

_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


@dataclass
class _Transfer:
    tx_hash: str
    log_index: int
    to: str
    token: str
    amount: int
    block: int


class FakeChain:
    name = "fake"

    def __init__(self, chain_id: int = SEPOLIA) -> None:
        self.chain_id = chain_id
        self.block = 1
        self.balances: dict[tuple[str, str], int] = {}
        self.transfers: list[_Transfer] = []
        self.fail_sends = False
        self._ids = itertools.count()
        self.check_network()

    # ---- test controls ----

    def mine(self, blocks: int = 1) -> None:
        self.block += blocks

    def pay(self, to: str, token: Token, amount: int) -> str:
        """Someone outside sends `amount` to `to` (lands in the current block)."""
        return self._move(None, to.lower(), token, amount)

    def balance(self, address: str, token: Token) -> int:
        return self.balances.get((address.lower(), token.symbol), 0)

    # ---- Chain ----

    def network_id(self) -> int:
        return self.chain_id

    def check_network(self) -> None:
        require_testnet(self.chain_id)

    def deposit_address(self, index: int) -> str:
        return "0x" + hashlib.sha256(f"deposit:{index}".encode()).hexdigest()[:40]

    def demo_address(self) -> str:
        return "0x" + hashlib.sha256(b"demo-treasury").hexdigest()[:40]

    def normalize_address(self, text: str) -> str:
        if not _ADDRESS.match(text.strip()):
            raise BadAddress(f"{text!r} isn't an address")
        return text.strip().lower()

    def head(self) -> int:
        return self.block

    def payments_to(self, address: str, token: Token, from_block: int = 0) -> list[Payment]:
        return [
            Payment(tx_hash=t.tx_hash, log_index=t.log_index, amount=t.amount, confirmations=self.block - t.block + 1)
            for t in self.transfers
            if t.to == address.lower() and t.token == token.symbol and t.block >= from_block
        ]

    def send(self, from_index: int, to: str, token: Token, amount: int) -> str:
        self.check_network()
        if self.fail_sends:
            raise ChainError("The network rejected the transaction")
        source = self.deposit_address(from_index)
        if self.balance(source, token) < amount:
            raise ChainError("Not enough at the deposit address")
        return self._move(source, self.normalize_address(to), token, amount)

    def fund_demo(self, to: str, token: Token, amount: int) -> str:
        self.check_network()
        return self._move(None, to.lower(), token, amount)

    def tx_url(self, tx_hash: str) -> str:
        return f"fake://tx/{tx_hash}"

    def address_url(self, address: str) -> str:
        return f"fake://address/{address}"

    def _move(self, source: str | None, to: str, token: Token, amount: int) -> str:
        if source is not None:
            self.balances[(source, token.symbol)] -= amount
        self.balances[(to, token.symbol)] = self.balances.get((to, token.symbol), 0) + amount
        tx_hash = "0x" + hashlib.sha256(f"tx:{next(self._ids)}".encode()).hexdigest()
        self.transfers.append(_Transfer(tx_hash, 0, to, token.symbol, amount, self.block))
        return tx_hash
