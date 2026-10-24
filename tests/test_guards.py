from __future__ import annotations

import datetime as dt

import pytest

from holdfast.chains.fake import FakeChain
from holdfast.guards import SEPOLIA, CapExceeded, Caps, WrongNetwork, require_testnet
from holdfast.money import ETH

ETHEREUM = 1  # the real network: must always be refused
DAY = dt.datetime(2026, 11, 1, 12, tzinfo=dt.UTC)


def test_sepolia_is_allowed() -> None:
    require_testnet(SEPOLIA)


@pytest.mark.parametrize("network", [ETHEREUM, 10, 137, 8453, 56, 0])
def test_everything_else_is_refused(network: int) -> None:
    with pytest.raises(WrongNetwork):
        require_testnet(network)


def test_a_chain_on_the_wrong_network_refuses_to_start() -> None:
    with pytest.raises(WrongNetwork):
        FakeChain(chain_id=ETHEREUM)


def test_a_chain_rechecks_before_sending() -> None:
    chain = FakeChain()
    chain.chain_id = ETHEREUM  # the RPC changed under it
    with pytest.raises(WrongNetwork):
        chain.send(1, "0x" + "11" * 20, ETH, 1)


def test_per_deal_cap() -> None:
    caps = Caps(per_deal=100, per_day=1000)
    caps.check_deal(100)
    with pytest.raises(CapExceeded):
        caps.check_deal(101)


def test_per_day_cap_resets_the_next_day() -> None:
    caps = Caps(per_deal=100, per_day=150)
    caps.check_send(100, DAY)
    caps.record_send(100, DAY)
    with pytest.raises(CapExceeded):
        caps.check_send(51, DAY)
    caps.check_send(50, DAY)
    tomorrow = DAY + dt.timedelta(days=1)
    caps.check_send(150, tomorrow)
    caps.record_send(150, tomorrow)
    with pytest.raises(CapExceeded):
        caps.check_send(1, tomorrow)
