"""Where deals live. The service only sees the `Store` interface.

Two rules every implementation keeps:
- Optimistic locking: `save` fails with `Conflict` if the deal changed since it was loaded, so two
  workers can't both act on the same deal.
- One transfer, one deal: `claim_transfer` records which deal a transfer was credited to, globally,
  so the same on-chain transfer can never be counted twice (by two deals, or after a restart).
"""

from __future__ import annotations

from typing import Protocol

from holdfast.deal import TERMINAL, Deal


class Conflict(RuntimeError):
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
    """Phase 2. The plan, so the in-memory store above can be swapped out without touching the service:

    - `deals`: one document per deal (`Deal.model_dump(mode="json")`), `_id` = deal id, unique index on
      `number`, index on `state`, and on `buyer_id` and `seller_id`. `save` is
      `replace_one({"_id": id, "version": v}, doc_with_version_v_plus_1)`; zero matches raises `Conflict`.
    - `transfers`: `{_id: "<tx hash>:<log index>", deal_id}`; `claim_transfer` inserts and treats a
      duplicate-key error as "check who owns it". The unique `_id` is the double-credit guard.
    - `counters`: `{_id: "deal_number", n}` bumped with `find_one_and_update($inc)`.

    PyMongo (not Motor): Motor is deprecated in favor of PyMongo's own async API, and PyMongo serves
    both the sync worker and an async web API from one dependency.
    """

    def __init__(self, uri: str, database: str) -> None:
        raise NotImplementedError("MongoStore arrives in phase 2; use MemoryStore for now")
