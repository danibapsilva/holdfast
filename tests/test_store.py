"""One contract, every store: the same tests run against MemoryStore and MongoStore (on mongomock)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import mongomock
import pytest
from pymongo.database import Database
from pymongo.errors import ServerSelectionTimeoutError

from holdfast.deal import State
from holdfast.money import ETH
from holdfast.store import INT64_MAX, Conflict, MemoryStore, MongoStore, Store
from tests.conftest import new_deal


@pytest.fixture(params=["memory", "mongo"])
def store(request: pytest.FixtureRequest) -> Iterator[Store]:
    if request.param == "memory":
        yield MemoryStore()
    else:
        client: Any = mongomock.MongoClient()
        yield MongoStore(cast(Database[dict[str, Any]], client["holdfast"]))


def test_insert_get_save_with_versions(store: Store) -> None:
    deal = new_deal()
    store.insert(deal)
    with pytest.raises(Conflict):
        store.insert(deal)
    a, b = store.get("d1"), store.get("d1")
    assert a is not None
    assert b is not None
    a.description = "first"
    store.save(a)
    assert a.version == 1
    b.description = "second"
    with pytest.raises(Conflict):
        store.save(b)  # b was loaded before a was saved
    stored = store.get("d1")
    assert stored is not None
    assert stored.description == "first"
    assert stored.version == 1


def test_get_returns_a_copy(store: Store) -> None:
    store.insert(new_deal())
    d = store.get("d1")
    assert d is not None
    d.description = "changed without saving"
    again = store.get("d1")
    assert again is not None
    assert again.description != "changed without saving"


def test_round_trip_keeps_every_field(store: Store) -> None:
    deal = new_deal()
    deal.state = State.FUNDED
    deal.deposit_address = "0x" + "d0" * 20
    store.insert(deal)
    assert store.get("d1") == deal


def test_unknown_deal(store: Store) -> None:
    assert store.get("nope") is None
    with pytest.raises(KeyError):
        store.save(new_deal())


def test_claim_transfer_once(store: Store) -> None:
    assert store.claim_transfer("0xa:0", "d1")
    assert store.claim_transfer("0xa:0", "d1")  # the same deal again is fine
    assert not store.claim_transfer("0xa:0", "d2")


def test_numbers_and_lookups(store: Store) -> None:
    assert [store.next_number() for _ in range(3)] == [1, 2, 3]
    store.insert(new_deal())
    assert [d.id for d in store.deals_for("alice")] == ["d1"]
    assert [d.id for d in store.deals_for("bob")] == ["d1"]
    assert store.deals_for("eve") == []
    assert [d.id for d in store.open_deals()] == ["d1"]


def test_closed_deals_leave_the_open_list(store: Store) -> None:
    deal = new_deal()
    deal.state = State.RELEASED
    store.insert(deal)
    assert store.open_deals() == []


def test_amounts_beyond_64_bits_round_trip(store: Store) -> None:
    """Wei amounts can pass BSON's 64-bit integers; Mongo stores those as strings and reads them back."""
    deal = new_deal(amount=INT64_MAX * 3, fee=0)
    deal.token = ETH
    store.insert(deal)
    stored = store.get("d1")
    assert stored is not None
    assert stored.amount == INT64_MAX * 3


def test_mongo_connect_refuses_an_unreachable_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("holdfast.store.CONNECT_TIMEOUT_MS", 200)
    with pytest.raises(ServerSelectionTimeoutError):
        MongoStore.connect("mongodb://127.0.0.1:1", "holdfast")
