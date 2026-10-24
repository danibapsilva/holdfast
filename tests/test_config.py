from __future__ import annotations

import pytest

from holdfast.config import ConfigError, Settings

ENV = {
    "HOLDFAST_RPC_URL": "https://sepolia.example/rpc",
    "HOLDFAST_TUSD_ADDRESS": "0x" + "ab" * 20,
    "HOLDFAST_SEED": "test test test test test test test test test test test junk",
}


def test_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HOLDFAST_PER_DEAL_CAP", "25")
    s = Settings.from_env()
    assert s.caps().per_deal == 25 * 10**6
    assert s.policy().required_confirmations == 2
    assert s.token.symbol == "tUSD"
    assert "junk" not in repr(s)


@pytest.mark.parametrize("missing", list(ENV))
def test_missing_settings_fail_clearly(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    for k, v in ENV.items():
        if k != missing:
            monkeypatch.setenv(k, v)
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(ConfigError, match=missing):
        Settings.from_env()
