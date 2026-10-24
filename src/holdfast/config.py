"""Settings from the environment (see .env.example). Secrets are read here and nowhere else.

HOLDFAST_RPC_URL              a Sepolia RPC endpoint (Alchemy, Infura, or a public one)
HOLDFAST_SEED                 the demo's BIP-39 seed phrase: made fresh for this, never used for real money
HOLDFAST_TUSD_ADDRESS         the deployed tUSD contract on Sepolia
HOLDFAST_MONGO_URI            the MongoDB connection string (a secret: it holds the password); unset means
                              deals live in memory and vanish on restart
HOLDFAST_MONGO_DB             the database name (default holdfast)
HOLDFAST_ALLOWED_ORIGINS      the sites allowed to call the API from a browser, comma separated
HOLDFAST_API_PORT             the port the API listens on, on 127.0.0.1 (default 8710)
HOLDFAST_CONFIRMATIONS        confirmations before a payment counts (default 2)
HOLDFAST_PAYMENT_MINUTES      how long a buyer has to pay (default 30)
HOLDFAST_DISPUTE_MINUTES      how long a dispute waits before the timer settles it (default 10)
HOLDFAST_PER_DEAL_CAP         most a deal can be, in whole tUSD (default 100)
HOLDFAST_PER_DAY_CAP          most sent in a day, in whole tUSD (default 2000)

Services read these from the process environment; `load_env_file` fills it from a file first, so
`holdfast-api --env-file ~/.config/holdfast/holdfast.env` works the same as systemd's EnvironmentFile.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path

from web3 import Web3

from holdfast.chains.hd import HDWallet
from holdfast.chains.sepolia import SepoliaChain
from holdfast.errors import HoldfastError
from holdfast.guards import Caps
from holdfast.money import Token, tusd
from holdfast.service import EscrowService, Policy
from holdfast.store import MemoryStore, MongoStore, Store

DEFAULT_ORIGINS = ("https://danibapsilva.github.io", "http://localhost:3000")
DEFAULT_API_PORT = 8710


class ConfigError(HoldfastError):
    """A setting is missing or isn't usable."""


def require_env(name: str) -> str:
    """A setting that has to be there, or a `ConfigError` that names it."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Set {name} (see .env.example)")
    return value


def load_env_file(path: str | Path) -> list[str]:
    """Read `KEY=VALUE` lines into the environment (a variable already set wins, as with systemd).

    Blank lines and `#` comments are skipped, and a value may be wrapped in single or double quotes.
    Returns the names it set, never the values, so it's safe to log.
    """
    file = Path(path).expanduser()
    if not file.is_file():
        raise ConfigError(f"No settings file at {file}")
    loaded: list[str] = []
    for number, raw in enumerate(file.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key.isidentifier():
            raise ConfigError(f"{file}, line {number}: expected KEY=VALUE")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _number(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    if not raw.isdigit() or int(raw) <= 0:
        raise ConfigError(f"{name} must be a whole number above zero, not {raw!r}")
    return int(raw)


@dataclass(frozen=True)
class Settings:
    rpc_url: str = field(repr=False)  # an RPC URL usually carries an API key
    tusd_address: str
    seed: str = field(repr=False)  # never printed
    mongo_uri: str = field(default="", repr=False)  # holds the database password
    mongo_db: str = "holdfast"
    allowed_origins: tuple[str, ...] = DEFAULT_ORIGINS
    api_port: int = DEFAULT_API_PORT
    confirmations: int = 2
    payment_minutes: int = 30
    dispute_minutes: int = 10
    per_deal_cap: int = 100
    per_day_cap: int = 2000

    @classmethod
    def from_env(cls) -> Settings:
        origins = os.environ.get("HOLDFAST_ALLOWED_ORIGINS", "").strip()
        return cls(
            rpc_url=require_env("HOLDFAST_RPC_URL"),
            tusd_address=require_env("HOLDFAST_TUSD_ADDRESS"),
            seed=require_env("HOLDFAST_SEED"),
            mongo_uri=os.environ.get("HOLDFAST_MONGO_URI", "").strip(),
            mongo_db=os.environ.get("HOLDFAST_MONGO_DB", "").strip() or "holdfast",
            allowed_origins=tuple(o.strip() for o in origins.split(",") if o.strip()) if origins else DEFAULT_ORIGINS,
            api_port=_number("HOLDFAST_API_PORT", DEFAULT_API_PORT),
            confirmations=_number("HOLDFAST_CONFIRMATIONS", 2),
            payment_minutes=_number("HOLDFAST_PAYMENT_MINUTES", 30),
            dispute_minutes=_number("HOLDFAST_DISPUTE_MINUTES", 10),
            per_deal_cap=_number("HOLDFAST_PER_DEAL_CAP", 100),
            per_day_cap=_number("HOLDFAST_PER_DAY_CAP", 2000),
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

    def store(self) -> Store:
        """MongoDB when a URI is set; otherwise memory (fine to try it, gone on restart)."""
        return MongoStore.connect(self.mongo_uri, self.mongo_db) if self.mongo_uri else MemoryStore()


def build_service(settings: Settings, store: Store | None = None) -> EscrowService:
    """The service on Sepolia. Refuses to start if the RPC isn't Sepolia."""
    chain = SepoliaChain(Web3(Web3.HTTPProvider(settings.rpc_url)), HDWallet(settings.seed), settings.tusd_address)
    return EscrowService(
        store or settings.store(), chain, settings.token, caps=settings.caps(), policy=settings.policy()
    )
