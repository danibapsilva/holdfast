"""Blockchains behind one interface, so the escrow logic never knows which network it's on.

`SepoliaChain` talks to Ethereum's Sepolia test network (native ETH and the tUSD token); `FakeChain`
keeps everything in memory for tests and local demos. Adding a network means writing one more class
that satisfies `Chain`.
"""

from __future__ import annotations

from typing import Protocol

from holdfast.deal import Payment
from holdfast.errors import HoldfastError
from holdfast.money import Token


class ChainError(HoldfastError):
    """The network refused or failed: a send reverted, or an RPC was down."""


class BadAddress(ChainError):
    """Text that isn't an address on this network: the caller's mistake, not the network's."""


class Chain(Protocol):
    name: str

    def network_id(self) -> int:
        """The chain id the connected network reports (what the guards check)."""
        ...

    def check_network(self) -> None:
        """Raise `WrongNetwork` unless the connected network is an allowlisted test network."""
        ...

    def deposit_address(self, index: int) -> str:
        """The address deal number `index` is paid to, derived from the one seed."""
        ...

    def normalize_address(self, text: str) -> str:
        """A checked, canonical address, or `ChainError` if `text` isn't one."""
        ...

    def payments_to(self, address: str, token: Token, from_block: int = 0) -> list[Payment]:
        """Every transfer of `token` into `address` since `from_block`, with its confirmations."""
        ...

    def send(self, from_index: int, to: str, token: Token, amount: int) -> str:
        """Send from deal `from_index`'s deposit address. Returns the transaction hash once mined."""
        ...

    def fund_demo(self, to: str, token: Token, amount: int) -> str:
        """The demo bot pays: mints test tokens or sends from the demo treasury. Testnets only."""
        ...

    def demo_address(self) -> str:
        """The demo bot's own address (where it's paid when it plays the seller)."""
        ...

    def head(self) -> int:
        """The latest block number."""
        ...

    def tx_url(self, tx_hash: str) -> str: ...

    def address_url(self, address: str) -> str: ...
