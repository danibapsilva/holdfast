from __future__ import annotations

import pytest

from holdfast.chains.hd import HDWallet, SeedError

# the well-known development phrase: public, never holds anything
DEV_SEED = "test test test test test test test test test test test junk"


def test_derivation_is_deterministic_and_distinct() -> None:
    a, b = HDWallet(DEV_SEED), HDWallet(DEV_SEED)
    assert a.deposit(1).address == b.deposit(1).address
    addresses = {a.deposit(i).address for i in range(10)}
    assert len(addresses) == 10
    assert a.treasury().address not in addresses


def test_matches_the_standard_bip44_path() -> None:
    # m/44'/60'/0'/0/1 of the development phrase (the same address Hardhat and Anvil print)
    assert HDWallet(DEV_SEED).deposit(1).address == "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


@pytest.mark.parametrize("seed", ["", "one two three", "test " * 11 + "wrong"])
def test_bad_seeds_are_refused(seed: str) -> None:
    with pytest.raises(SeedError):
        HDWallet(seed)


def test_negative_index_is_refused() -> None:
    with pytest.raises(ValueError, match="start at 0"):
        HDWallet(DEV_SEED).deposit(-1)


def test_repr_hides_the_seed() -> None:
    assert "test" not in repr(HDWallet(DEV_SEED))
