from __future__ import annotations

import pytest

from holdfast.money import ETH, AmountError, format_amount, parse_amount, tusd

TUSD = tusd("0x" + "ab" * 20)


@pytest.mark.parametrize(
    ("text", "units"),
    [
        ("12.5", 12_500_000),
        ("$1,000", 1_000_000_000),
        (" 0.000001 ", 1),
        ("7", 7_000_000),
        ("7.", 7_000_000),
        (".5", 500_000),
    ],
)
def test_parse_tusd(text: str, units: int) -> None:
    assert parse_amount(text, TUSD) == units


def test_parse_eth_keeps_all_18_decimals() -> None:
    assert parse_amount("0.000000000000000001", ETH) == 1
    assert parse_amount("1.5", ETH) == 1_500_000_000_000_000_000


@pytest.mark.parametrize("text", ["", "abc", "-5", "1e3", "1.2.3", ".", "0", "0.0", "0.0000001", "NaN", "inf", "+3"])
def test_parse_rejects(text: str) -> None:
    with pytest.raises(AmountError):
        parse_amount(text, TUSD)


@pytest.mark.parametrize(
    ("units", "text"),
    [
        (12_500_000, "12.5 tUSD"),
        (1, "0.000001 tUSD"),
        (1_000_000_000, "1,000 tUSD"),
        (0, "0 tUSD"),
        (-2_500_000, "-2.5 tUSD"),
    ],
)
def test_format(units: int, text: str) -> None:
    assert format_amount(units, TUSD) == text


def test_format_without_symbol_round_trips() -> None:
    for units in (1, 999_999, 123_456_789, 10**12):
        assert parse_amount(format_amount(units, TUSD, symbol=False), TUSD) == units


def test_token_properties() -> None:
    assert ETH.native
    assert not TUSD.native
    assert TUSD.unit() == 10**6
