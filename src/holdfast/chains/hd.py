"""Every key Holdfast uses comes from one seed phrase, held in an environment variable.

    m/44'/60'/0'/0/{n}   deal n's deposit address (BIP-44, Ethereum, address index = deal number)
    m/44'/60'/1'/0/0     the demo treasury: pays gas, mints tUSD, plays the demo bot

Nothing is written to disk, and a fresh seed made only for the demo never holds real money.
"""

from __future__ import annotations

from eth_account import Account
from eth_account.signers.local import LocalAccount

from holdfast.errors import HoldfastError

Account.enable_unaudited_hdwallet_features()

DEPOSIT_PATH = "m/44'/60'/0'/0/{index}"
TREASURY_PATH = "m/44'/60'/1'/0/0"


class SeedError(HoldfastError, ValueError):
    """The seed phrase is missing words or isn't valid BIP-39."""


class HDWallet:
    """The demo's keys, derived on demand from one seed phrase and cached in memory."""

    def __init__(self, seed_phrase: str) -> None:
        words = seed_phrase.split()
        if len(words) not in (12, 15, 18, 21, 24):
            raise SeedError("The seed must be a 12 to 24 word BIP-39 phrase")
        try:
            Account.from_mnemonic(seed_phrase, account_path=TREASURY_PATH)
        except Exception as e:  # a bad checksum or unknown word
            raise SeedError("The seed phrase isn't valid BIP-39") from e
        self._seed = seed_phrase
        self._cache: dict[str, LocalAccount] = {}

    def _account(self, path: str) -> LocalAccount:
        if path not in self._cache:
            self._cache[path] = Account.from_mnemonic(self._seed, account_path=path)
        return self._cache[path]

    def deposit(self, index: int) -> LocalAccount:
        if index < 0:
            raise ValueError("Deal numbers start at 0")
        return self._account(DEPOSIT_PATH.format(index=index))

    def treasury(self) -> LocalAccount:
        return self._account(TREASURY_PATH)

    def __repr__(self) -> str:  # never print the seed
        return "HDWallet(<seed hidden>)"
