"""Every deliberate error is a HoldfastError, so the API and the bot can show its message safely."""

from __future__ import annotations

import pytest

from holdfast.chains import ChainError
from holdfast.chains.hd import SeedError
from holdfast.config import ConfigError
from holdfast.deal import BadPayment, DealError
from holdfast.errors import HoldfastError
from holdfast.guards import CapExceeded, WrongNetwork
from holdfast.money import AmountError
from holdfast.service import NotFound
from holdfast.store import Conflict


@pytest.mark.parametrize(
    "error",
    [
        ChainError,
        SeedError,
        ConfigError,
        BadPayment,
        DealError,
        CapExceeded,
        WrongNetwork,
        AmountError,
        NotFound,
        Conflict,
    ],
)
def test_family(error: type[Exception]) -> None:
    assert issubclass(error, HoldfastError)
