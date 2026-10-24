from __future__ import annotations

import pytest

from holdfast.store import Conflict, MemoryStore, MongoStore
from tests.conftest import new_deal


def test_insert_get_save_with_versions() -> None:
    store = MemoryStore()
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


def test_get_returns_a_copy() -> None:
    store = MemoryStore()
    store.insert(new_deal())
    d = store.get("d1")
    assert d is not None
    d.description = "changed without saving"
    again = store.get("d1")
    assert again is not None
    assert again.description != "changed without saving"


def test_save_unknown_deal() -> None:
    with pytest.raises(KeyError):
        MemoryStore().save(new_deal())


def test_claim_transfer_once() -> None:
    store = MemoryStore()
    assert store.claim_transfer("0xa:0", "d1")
    assert store.claim_transfer("0xa:0", "d1")  # the same deal again is fine
    assert not store.claim_transfer("0xa:0", "d2")


def test_numbers_and_lookups() -> None:
    store = MemoryStore()
    assert [store.next_number() for _ in range(3)] == [1, 2, 3]
    store.insert(new_deal())
    assert [d.id for d in store.deals_for("alice")] == ["d1"]
    assert store.deals_for("eve") == []
    assert [d.id for d in store.open_deals()] == ["d1"]


def test_mongo_store_is_phase_two() -> None:
    with pytest.raises(NotImplementedError):
        MongoStore("mongodb://localhost", "holdfast")
