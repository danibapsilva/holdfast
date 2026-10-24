"""The two programs, started without a network or a server (uvicorn and the loop are stubbed)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from holdfast import cli
from holdfast.service import EscrowService
from holdfast.worker import Worker
from tests.test_config import ENV


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def run(app: FastAPI, **options: Any) -> None:
        seen["app"], seen["options"] = app, options

    monkeypatch.setattr(cli.uvicorn, "run", run)
    return seen


def test_fake_api_runs_offline(served: dict[str, Any]) -> None:
    assert cli.api_main(["--fake", "--port", "9999"]) == 0
    assert isinstance(served["app"], FastAPI)
    assert served["options"]["host"] == "127.0.0.1"  # never the open internet directly
    assert served["options"]["port"] == 9999
    assert served["options"]["forwarded_allow_ips"] == "127.0.0.1"


def test_fake_service_has_the_demo_caps() -> None:
    service, chain = cli.fake_service()
    assert service.chain is chain
    assert service.caps.per_deal == 100 * service.token.unit()


def env_file(tmp_path: Path) -> Path:
    path = tmp_path / "holdfast.env"
    path.write_text("".join(f"{k}={v}\n" for k, v in ENV.items()))
    return path


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("clean_env")
def test_api_reads_the_env_file(
    tmp_path: Path, served: dict[str, Any], service: EscrowService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "build_service", lambda settings: service)  # pyright: ignore[reportUnknownLambdaType]
    assert cli.api_main(["--env-file", str(env_file(tmp_path))]) == 0
    assert served["options"]["port"] == 8710


@pytest.mark.usefixtures("clean_env")
def test_worker_runs_until_stopped(tmp_path: Path, service: EscrowService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "build_service", lambda settings: service)  # pyright: ignore[reportUnknownLambdaType]
    ran: list[bool] = []

    def run(self: Worker, stop: threading.Event) -> None:
        ran.append(not stop.is_set())

    monkeypatch.setattr(Worker, "run", run)
    assert cli.worker_main(["--env-file", str(env_file(tmp_path))]) == 0
    assert ran == [True]
