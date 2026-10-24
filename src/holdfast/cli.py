"""The two programs Holdfast runs as: `holdfast-api` (the web playground's backend) and `holdfast-worker` (the
watcher). Both read settings from the environment, optionally filled from `--env-file`.

    holdfast-api --env-file ~/.config/holdfast/holdfast.env     on Sepolia, listening on 127.0.0.1:8710
    holdfast-api --fake                                          offline: an in-memory chain that mines a block
                                                                 every few seconds, for building the playground
    holdfast-worker --env-file ~/.config/holdfast/holdfast.env   the watcher, until SIGTERM or Ctrl-C

The API only listens on 127.0.0.1: a proxy in front (Tailscale Funnel on the demo server) makes it public,
and uvicorn trusts that proxy's forwarded-for header only from 127.0.0.1, so rate limits see real visitors.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections.abc import Sequence

import uvicorn

from holdfast.api import create_app
from holdfast.chains.fake import FakeChain
from holdfast.config import DEFAULT_API_PORT, DEFAULT_ORIGINS, Settings, build_service, load_env_file
from holdfast.guards import Caps
from holdfast.money import tusd
from holdfast.service import EscrowService
from holdfast.store import MemoryStore
from holdfast.worker import Worker

log = logging.getLogger("holdfast")

LOCALHOST = "127.0.0.1"
FAKE_TUSD = "0x" + "7e" * 20  # any address does on the in-memory chain
FAKE_BLOCK_SECONDS = 3.0
FAKE_ORIGINS = (*DEFAULT_ORIGINS, "http://localhost:5173", "http://127.0.0.1:3000")


def _logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _settings(env_file: str | None) -> Settings:
    if env_file:
        names = load_env_file(env_file)
        log.info("read %d settings from %s", len(names), env_file)
    return Settings.from_env()


def fake_service() -> tuple[EscrowService, FakeChain]:
    """The service on an in-memory chain and store, with the demo's default caps."""
    chain = FakeChain()
    token = tusd(FAKE_TUSD)
    unit = token.unit()
    return EscrowService(MemoryStore(), chain, token, caps=Caps(per_deal=100 * unit, per_day=2000 * unit)), chain


def api_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="holdfast-api", description="The Holdfast HTTP API.")
    parser.add_argument("--env-file", help="read settings from this KEY=VALUE file first")
    parser.add_argument("--fake", action="store_true", help="run offline on an in-memory chain and store")
    parser.add_argument("--host", default=LOCALHOST, help="address to listen on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help=f"port to listen on (default {DEFAULT_API_PORT})")
    args = parser.parse_args(argv)
    _logging()
    if args.fake:
        service, chain = fake_service()
        worker = Worker(service, interval=FAKE_BLOCK_SECONDS, before_tick=chain.mine)
        app = create_app(service, allowed_origins=FAKE_ORIGINS, background=worker)
        port = args.port or DEFAULT_API_PORT
        log.info("offline playground API: in-memory chain, a block every %.0fs", FAKE_BLOCK_SECONDS)
    else:
        settings = _settings(args.env_file)
        app = create_app(build_service(settings), allowed_origins=settings.allowed_origins)
        port = args.port or settings.api_port
    uvicorn.run(app, host=args.host, port=port, proxy_headers=True, forwarded_allow_ips=LOCALHOST)
    return 0


def worker_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="holdfast-worker", description="The Holdfast watcher.")
    parser.add_argument("--env-file", help="read settings from this KEY=VALUE file first")
    args = parser.parse_args(argv)
    _logging()
    service = build_service(_settings(args.env_file))
    stop = threading.Event()

    def on_signal(signum: int, frame: object) -> None:
        log.info("signal %d: stopping after this pass", signum)
        stop.set()

    previous = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        Worker(service).run(stop)
    finally:
        for sig, handler in previous.items():  # leave the process as it was (it matters in tests)
            signal.signal(sig, handler)
    return 0
