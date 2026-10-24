"""Where deals live. The service only sees the `Store` interface: `MemoryStore` for tests and the offline
playground, `MongoStore` for the running demo.

Two rules every implementation keeps:
- Optimistic locking: `save` fails with `Conflict` if the deal changed since it was loaded, so two
  workers can't both act on the same deal.
- One transfer, one deal: `claim_transfer` records which deal a transfer was credited to, globally,
  so the same on-chain transfer can never be counted twice (by two deals, or after a restart).
"""

from __future__ import annotations

from typing import Any, Protocol, cast

from pymongo import MongoClient, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from holdfast.deal import TERMINAL, Deal
from holdfast.errors import HoldfastError


class Conflict(HoldfastError):
    """The deal was changed by someone else since it was loaded; load it again and retry."""


class Store(Protocol):
    def next_number(self) -> int: ...

    def insert(self, deal: Deal) -> None: ...

    def get(self, deal_id: str) -> Deal | None: ...

    def save(self, deal: Deal) -> None: ...

    def open_deals(self) -> list[Deal]: ...

    def deals_for(self, user_id: str) -> list[Deal]: ...

    def claim_transfer(self, key: str, deal_id: str) -> bool:
        """True if `key` is now (or already was) credited to `deal_id`; False if another deal has it."""
        ...


class MemoryStore:
    """For tests and local demos. Hands out copies, like a database would."""

    def __init__(self) -> None:
        self._deals: dict[str, Deal] = {}
        self._transfers: dict[str, str] = {}
        self._number = 0

    def next_number(self) -> int:
        self._number += 1
        return self._number

    def insert(self, deal: Deal) -> None:
        if deal.id in self._deals:
            raise Conflict(f"Deal {deal.id} already exists")
        self._deals[deal.id] = deal.model_copy(deep=True)

    def get(self, deal_id: str) -> Deal | None:
        deal = self._deals.get(deal_id)
        return deal.model_copy(deep=True) if deal else None

    def save(self, deal: Deal) -> None:
        stored = self._deals.get(deal.id)
        if stored is None:
            raise KeyError(deal.id)
        if stored.version != deal.version:
            raise Conflict(f"Deal {deal.id} changed since it was loaded")
        deal.version += 1
        self._deals[deal.id] = deal.model_copy(deep=True)

    def open_deals(self) -> list[Deal]:
        return [d.model_copy(deep=True) for d in self._deals.values() if d.state not in TERMINAL]

    def deals_for(self, user_id: str) -> list[Deal]:
        return [d.model_copy(deep=True) for d in self._deals.values() if user_id in (d.buyer_id, d.seller_id)]

    def claim_transfer(self, key: str, deal_id: str) -> bool:
        owner = self._transfers.setdefault(key, deal_id)
        return owner == deal_id


class MongoStore:
    """Deals in MongoDB through PyMongo, keeping the same two rules as every store:

    - `deals`: one document per deal, `_id` = deal id. `save` replaces the document only where `version`
      still matches what was loaded, so a stale copy is refused (`Conflict`) instead of overwriting.
    - `transfers`: `{_id: "<tx hash>:<log index>", deal_id}`. The unique `_id` is the double-credit guard:
      a second deal claiming the same transfer hits a duplicate key and loses.
    - `counters`: `{_id: "deal_number", n}`, bumped atomically, so every deal gets its own deposit address.

    PyMongo (not Motor): Motor is deprecated in favor of PyMongo's own async API, and the sync client serves
    both the worker and the API's thread pool from one dependency.
    """

    def __init__(self, db: Database[dict[str, Any]]) -> None:
        self._deals = db["deals"]
        self._transfers = db["transfers"]
        self._counters = db["counters"]
        self.ensure_indexes()

    @classmethod
    def connect(cls, uri: str, database: str) -> MongoStore:
        """Connect and set up indexes. Fails within seconds if the cluster can't be reached."""
        client: MongoClient[dict[str, Any]] = MongoClient(uri, serverSelectionTimeoutMS=CONNECT_TIMEOUT_MS)
        client.admin.command("ping")
        return cls(client[database])

    def ensure_indexes(self) -> None:
        """Idempotent: creating an index that already exists does nothing."""
        self._deals.create_index("number", unique=True)
        self._deals.create_index("state")
        self._deals.create_index("buyer_id")
        self._deals.create_index("seller_id")

    def next_number(self) -> int:
        counter = self._counters.find_one_and_update(
            {"_id": "deal_number"}, {"$inc": {"n": 1}}, upsert=True, return_document=ReturnDocument.AFTER
        )
        if counter is None:  # upsert with AFTER always returns the document; this keeps the type honest
            raise Conflict("Couldn't number the deal; try again")
        return int(counter["n"])

    def insert(self, deal: Deal) -> None:
        try:
            self._deals.insert_one(_document(deal))
        except DuplicateKeyError as e:
            raise Conflict(f"Deal {deal.id} already exists") from e

    def get(self, deal_id: str) -> Deal | None:
        doc = self._deals.find_one({"_id": deal_id})
        return _deal(doc) if doc else None

    def save(self, deal: Deal) -> None:
        doc = _document(deal) | {"version": deal.version + 1}
        result = self._deals.replace_one({"_id": deal.id, "version": deal.version}, doc)
        if result.matched_count == 0:
            if self._deals.count_documents({"_id": deal.id}, limit=1) == 0:
                raise KeyError(deal.id)
            raise Conflict(f"Deal {deal.id} changed since it was loaded")
        deal.version += 1

    def open_deals(self) -> list[Deal]:
        return [_deal(d) for d in self._deals.find({"state": {"$nin": sorted(TERMINAL)}})]

    def deals_for(self, user_id: str) -> list[Deal]:
        return [_deal(d) for d in self._deals.find({"$or": [{"buyer_id": user_id}, {"seller_id": user_id}]})]

    def claim_transfer(self, key: str, deal_id: str) -> bool:
        try:
            self._transfers.insert_one({"_id": key, "deal_id": deal_id})
            return True
        except DuplicateKeyError:
            owner = self._transfers.find_one({"_id": key})
            return owner is not None and owner["deal_id"] == deal_id


# BSON integers are 64-bit. Amounts are in base units (wei for ETH), which can pass that, so anything
# bigger is stored as a string; pydantic reads a numeric string back into an int.
INT64_MAX = 2**63 - 1
CONNECT_TIMEOUT_MS = 10_000


def _bson_safe(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > INT64_MAX else value
    if isinstance(value, dict):
        return {k: _bson_safe(v) for k, v in cast(dict[str, object], value).items()}
    if isinstance(value, list):
        return [_bson_safe(v) for v in cast(list[object], value)]
    return value


def _document(deal: Deal) -> dict[str, Any]:
    """The deal as stored: its JSON form (one exact, round-trippable format for dates and durations)."""
    doc = cast(dict[str, Any], _bson_safe(deal.model_dump(mode="json")))
    return {"_id": deal.id, **doc}


def _deal(doc: dict[str, Any]) -> Deal:
    return Deal.model_validate({k: v for k, v in doc.items() if k != "_id"})
