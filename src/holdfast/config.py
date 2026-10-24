"""Settings from the environment (see .env.example). Secrets are read here and nowhere else.

HOLDFAST_RPC_URL              a Sepolia RPC endpoint (Alchemy, Infura, or a public one)
HOLDFAST_SEED                 the demo's BIP-39 seed phrase: made fresh for this, never used for real money
HOLDFAST_TUSD_ADDRESS         the deployed tUSD contract on Sepolia
HOLDFAST_CONFIRMATIONS        confirmations before a payment counts (default 2)
HOLDFAST_PAYMENT_MINUTES      how long a buyer has to pay (default 30)
HOLDFAST_DISPUTE_MINUTES      how long a dispute waits before the timer settles it (default 10)
HOLDFAST_PER_DEAL_CAP         most a deal can be, in whole tUSD (default 100)
HOLDFAST_PER_DAY_CAP          most sent in a day, in whole tUSD (default 2000)
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field

from web3 import Web3

from holdfast.chains.hd import HDWallet
from holdfast.chains.sepolia import SepoliaChain
from holdfast.errors import HoldfastError
from holdfast.guards import Caps
from holdfast.money import Token, tusd
from holdfast.service import EscrowService, Policy
from holdfast.store import MemoryStore, Store


class ConfigError(HoldfastError):
    """A setting is missing or isn't usable."""


def _need(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Set {name} (see .env.example)")
    return value


@dataclass(frozen=True)
class Settings:
    rpc_url: str
    tusd_address: str
    seed: str = field(repr=False)  # never printed
    confirmations: int = 2
    payment_minutes: int = 30
    dispute_minutes: int = 10
    per_deal_cap: int = 100
    per_day_cap: int = 2000

    @classmethod
    def from_env(cls) -> Settings:
        def num(name: str, default: int) -> int:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return default
            if not raw.isdigit() or int(raw) <= 0:
                raise ConfigError(f"{name} must be a whole number above zero, not {raw!r}")
            return int(raw)

        return cls(
            rpc_url=_need("HOLDFAST_RPC_URL"),
            tusd_address=_need("HOLDFAST_TUSD_ADDRESS"),
            seed=_need("HOLDFAST_SEED"),
            confirmations=num("HOLDFAST_CONFIRMATIONS", 2),
            payment_minutes=num("HOLDFAST_PAYMENT_MINUTES", 30),
            dispute_minutes=num("HOLDFAST_DISPUTE_MINUTES", 10),
            per_deal_cap=num("HOLDFAST_PER_DEAL_CAP", 100),
            per_day_cap=num("HOLDFAST_PER_DAY_CAP", 2000),
        )

    @property
    def token(self) -> Token:
        return tusd(self.tusd_address)

    def policy(self) -> Policy:
        return Policy(
            required_confirmations=self.confirmations,
            payment_window=dt.timedelta(minutes=self.payment_minutes),
            dispute_window=dt.timedelta(minutes=self.dispute_minutes),
        )

    def caps(self) -> Caps:
        unit = self.token.unit()
        return Caps(per_deal=self.per_deal_cap * unit, per_day=self.per_day_cap * unit)


def build_service(settings: Settings, store: Store | None = None) -> EscrowService:
    """The service on Sepolia. Refuses to start if the RPC isn't Sepolia."""
    chain = SepoliaChain(Web3(Web3.HTTPProvider(settings.rpc_url)), HDWallet(settings.seed), settings.tusd_address)
    return EscrowService(store or MemoryStore(), chain, settings.token, caps=settings.caps(), policy=settings.policy())
