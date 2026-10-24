from __future__ import annotations

import os
from pathlib import Path

import pytest

from holdfast.config import ConfigError, Settings, load_env_file
from holdfast.store import MemoryStore

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


@pytest.mark.parametrize("bad", ["ten", "-5", "0", "2.5"])
def test_bad_numbers_fail_clearly(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HOLDFAST_CONFIRMATIONS", bad)
    with pytest.raises(ConfigError, match="HOLDFAST_CONFIRMATIONS must be a whole number"):
        Settings.from_env()


def test_new_settings_and_their_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HOLDFAST_MONGO_URI", "mongodb+srv://user:hunter2@cluster.example/")
    s = Settings.from_env()
    assert s.mongo_db == "holdfast"
    assert s.api_port == 8710
    assert "https://danibapsilva.github.io" in s.allowed_origins
    assert "hunter2" not in repr(s)
    assert "sepolia.example" not in repr(s)
    monkeypatch.setenv("HOLDFAST_ALLOWED_ORIGINS", "https://a.example, https://b.example,")
    monkeypatch.setenv("HOLDFAST_MONGO_DB", "demo")
    s = Settings.from_env()
    assert s.allowed_origins == ("https://a.example", "https://b.example")
    assert s.mongo_db == "demo"


def test_store_is_memory_without_a_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("HOLDFAST_MONGO_URI", raising=False)
    assert isinstance(Settings.from_env().store(), MemoryStore)


def test_load_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HF_A", "HF_B", "HF_C", "HF_D"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HF_D", "already set")
    env = tmp_path / "holdfast.env"
    env.write_text("# a comment\n\nHF_A=plain\nexport HF_B=\"two words\"\nHF_C='single'\nHF_D=from the file\n")
    loaded = load_env_file(env)
    assert loaded == ["HF_A", "HF_B", "HF_C"]
    assert os.environ["HF_A"] == "plain"
    assert os.environ["HF_B"] == "two words"
    assert os.environ["HF_C"] == "single"
    assert os.environ["HF_D"] == "already set"  # the environment wins, as with systemd


def test_load_env_file_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="No settings file"):
        load_env_file(tmp_path / "missing.env")
    bad = tmp_path / "bad.env"
    bad.write_text("JUST A LINE\n")
    with pytest.raises(ConfigError, match="line 1: expected KEY=VALUE"):
        load_env_file(bad)
