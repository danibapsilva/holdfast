"""Money as integers.

Every amount in Holdfast is an integer count of a token's smallest unit (wei for ETH, a millionth of a
dollar for tUSD). Floats never touch money: text from a user is parsed with Decimal, checked against
the token's decimals, and turned into an int once, at the edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class AmountError(ValueError):
    """An amount that can't be used: not a number, zero or negative, or more precise than the token."""


@dataclass(frozen=True, slots=True)
class Token:
    """A token a deal can be in. `contract` is None for the chain's native coin."""

    symbol: str
    decimals: int
    contract: str | None = None

    @property
    def native(self) -> bool:
        return self.contract is None

    def unit(self) -> int:
        """Base units in one whole token (10 ** decimals)."""
        return 10**self.decimals


ETH = Token("ETH", 18)


def tusd(contract: str) -> Token:
    """Holdfast Test USD at its deployed address (6 decimals, like USDC)."""
    return Token("tUSD", 6, contract)


def parse_amount(text: str, token: Token) -> int:
    """'12.5' tUSD -> 12_500_000. Rejects junk, signs, exponents, zero, and extra precision."""
    cleaned = text.strip().replace(",", "").removeprefix("$")
    if not cleaned or any(c not in "0123456789." for c in cleaned) or cleaned.count(".") > 1:
        raise AmountError(f"{text!r} isn't an amount")
    try:
        value = Decimal(cleaned)
    except InvalidOperation as e:  # e.g. "."
        raise AmountError(f"{text!r} isn't an amount") from e
    scaled = value * token.unit()
    if scaled != scaled.to_integral_value():
        raise AmountError(f"{token.symbol} has {token.decimals} decimal places; {text!r} has more")
    units = int(scaled)
    if units <= 0:
        raise AmountError("The amount must be more than zero")
    return units


def format_amount(units: int, token: Token, *, symbol: bool = True) -> str:
    """12_500_000 tUSD -> '12.5 tUSD'. Exact: no rounding, trailing zeros trimmed."""
    sign = "-" if units < 0 else ""
    whole, frac = divmod(abs(units), token.unit())
    text = f"{sign}{whole:,}"
    if frac:
        text += "." + str(frac).rjust(token.decimals, "0").rstrip("0")
    return f"{text} {token.symbol}" if symbol else text
