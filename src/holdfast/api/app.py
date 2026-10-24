"""The HTTP API behind the web playground: a visitor starts a deal against the demo bot and plays it through.

Every route is a thin shell over `EscrowService`: check the rate, find the caller's deal by their token,
call one use case, and return the deal as they see it. Routes are plain `def`s, so FastAPI runs them in its
thread pool and the blocking chain calls never stall other requests.

Errors: every `HoldfastError` becomes a JSON `{"error": message}` with a status that says what went wrong
(see `STATUS`); its message was written for the person who acted. Anything else is a bug: it's logged with
its traceback and the visitor sees a generic 500.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from holdfast.api.auth import Unauthorized, bearer_token, new_token, visitor_id
from holdfast.api.limits import OpenDealLimit, RateLimiter, TooManyRequests
from holdfast.api.schemas import (
    AddressIn,
    CreatedDeal,
    DealView,
    DisputeIn,
    ErrorBody,
    Health,
    Link,
    NewDemoDeal,
    PaymentSent,
    RespondIn,
)
from holdfast.api.views import deal_view
from holdfast.chains import BadAddress, ChainError
from holdfast.deal import BadPayment, Deal, DealError, IllegalTransition, NotAllowed, Role
from holdfast.errors import HoldfastError
from holdfast.guards import CapExceeded, WrongNetwork
from holdfast.money import AmountError, parse_amount
from holdfast.service import EscrowService, NotFound
from holdfast.store import Conflict
from holdfast.worker import Worker

log = logging.getLogger(__name__)

# the first match wins, so subclasses come before the classes they extend
STATUS: list[tuple[type[HoldfastError], int]] = [
    (Unauthorized, 401),
    (NotFound, 404),
    (NotAllowed, 403),
    (IllegalTransition, 409),
    (Conflict, 409),
    (TooManyRequests, 429),
    (AmountError, 422),
    (BadPayment, 422),
    (CapExceeded, 422),
    (BadAddress, 422),
    (WrongNetwork, 503),  # the server is pointed at the wrong network: nothing the visitor can fix
    (ChainError, 502),  # the test network refused or didn't answer
    (DealError, 400),
    (HoldfastError, 400),
]


def status_for(error: HoldfastError) -> int:
    return next(code for kind, code in STATUS if isinstance(error, kind))


@dataclass(frozen=True)
class ApiState:
    """What the routes share, kept on `app.state` so the dependencies below can live at module level."""

    service: EscrowService
    limiter: RateLimiter
    open_limit: OpenDealLimit


def _state(request: Request) -> ApiState:
    return cast(ApiState, request.app.state.holdfast)


def client_ip(request: Request) -> str:
    # behind the proxy, uvicorn's proxy headers make this the visitor's address (see cli.py)
    return request.client.host if request.client else "unknown"


def rate_limited(request: Request) -> str:
    """Counts the request against the caller's rate; returns their IP."""
    ip = client_ip(request)
    _state(request).limiter.check(ip)
    return ip


def visitor(authorization: Annotated[str | None, Header()] = None) -> str:
    """The caller's user id, from their deal token."""
    return visitor_id(bearer_token(authorization))


Ip = Annotated[str, Depends(rate_limited)]
Visitor = Annotated[str, Depends(visitor)]


def install_error_handlers(app: FastAPI) -> None:
    """Every refusal as `{"error": message}` with its status; bugs as a logged, generic 500."""

    @app.exception_handler(HoldfastError)
    async def holdfast_error(_: Request, error: HoldfastError) -> JSONResponse:
        status = status_for(error)
        headers = {"Retry-After": str(error.retry_after)} if isinstance(error, TooManyRequests) else None
        if status >= 500:
            log.warning("%s: %s", type(error).__name__, error)
        return JSONResponse({"error": str(error)}, status_code=status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
        first = error.errors()[0] if error.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ())[1:]) or "request"
        return JSONResponse({"error": f"{field}: {first.get('msg', 'invalid')}"}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        log.exception("unexpected error on %s %s", request.method, request.url.path, exc_info=error)
        return JSONResponse({"error": "Something went wrong on our side"}, status_code=500)


def caller_deal(service: EscrowService, deal_id: str, user: str) -> tuple[Deal, Role]:
    """The caller's deal, or 404: a deal you're not part of looks the same as one that doesn't exist."""
    deal = service.get(deal_id)
    role = deal.role_of(user)
    if role is None:
        raise NotFound("No deal with that id")
    return deal, role


def caller_view(service: EscrowService, deal_id: str, user: str) -> DealView:
    deal, role = caller_deal(service, deal_id, user)
    return deal_view(deal, role, service.chain)


def act(service: EscrowService, deal_id: str, user: str, call: Callable[[], object]) -> DealView:
    """Check the deal is the caller's, run one use case, and return the deal as they now see it."""
    caller_deal(service, deal_id, user)
    call()
    return caller_view(service, deal_id, user)


def create_app(
    service: EscrowService,
    *,
    allowed_origins: tuple[str, ...],
    rate: RateLimiter | None = None,
    open_deals: OpenDealLimit | None = None,
    background: Worker | None = None,
) -> FastAPI:
    """The API over `service`. `background`, if given, runs in a thread for the app's lifetime (the offline
    playground's watcher; in production the watcher is its own process)."""
    open_limit = open_deals or OpenDealLimit()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        stop = threading.Event()
        thread = threading.Thread(target=background.run, args=(stop,), daemon=True) if background else None
        if thread:
            thread.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=5)

    app = FastAPI(
        title="Holdfast",
        summary="A crypto escrow engine, on test networks only.",
        version="0.2.0",
        lifespan=lifespan,
        responses={400: {"model": ErrorBody}},
    )
    app.state.holdfast = ApiState(service=service, limiter=rate or RateLimiter(), open_limit=open_limit)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    install_error_handlers(app)

    # ---- routes ----

    @app.get("/health")
    def health(_: Ip) -> Health:
        chain = service.chain
        treasury = chain.demo_address()
        return Health(
            ok=True,
            network=chain.name,
            chain_id=chain.network_id(),
            head=chain.head(),
            token=service.token.symbol,
            token_address=service.token.contract,
            treasury=Link(value=treasury, url=chain.address_url(treasury)),
        )

    @app.post("/demo/deals", status_code=201)
    def start_demo(body: NewDemoDeal, ip: Ip) -> CreatedDeal:
        """A deal against the demo bot. Returns the token every later call about it needs."""
        open_limit.check(ip, lambda deal_id: not service.get(deal_id).closed)
        units = parse_amount(body.amount, service.token)
        token = new_token()
        user = visitor_id(token)
        deal = service.start_demo(
            visitor=user, visitor_role=Role(body.role), amount=units, description=body.description
        )
        open_limit.add(ip, deal.id)
        log.info("demo deal %s started from %s as the %s", deal.id, ip, body.role)
        return CreatedDeal(token=token, deal=caller_view(service, deal.id, user))

    @app.get("/deals/{deal_id}")
    def get_deal(deal_id: str, user: Visitor, _: Ip) -> DealView:
        return caller_view(service, deal_id, user)

    @app.post("/deals/{deal_id}/pay")
    def pay(deal_id: str, user: Visitor, _: Ip) -> PaymentSent:
        """The demo pays for the visitor (they're the buyer, the demo bot sells): real test tokens, minted."""
        caller_deal(service, deal_id, user)
        tx = service.demo_pay(deal_id, user)
        return PaymentSent(tx=Link(value=tx, url=service.chain.tx_url(tx)), deal=caller_view(service, deal_id, user))

    @app.post("/deals/{deal_id}/cancel")
    def cancel(deal_id: str, user: Visitor, _: Ip) -> DealView:
        return act(service, deal_id, user, lambda: service.cancel(deal_id, user))

    @app.post("/deals/{deal_id}/deliver")
    def deliver(deal_id: str, user: Visitor, _: Ip) -> DealView:
        return act(service, deal_id, user, lambda: service.mark_delivered(deal_id, user))

    @app.post("/deals/{deal_id}/confirm")
    def confirm(deal_id: str, user: Visitor, _: Ip) -> DealView:
        return act(service, deal_id, user, lambda: service.confirm_delivery(deal_id, user))

    @app.post("/deals/{deal_id}/refund")
    def refund(deal_id: str, user: Visitor, _: Ip) -> DealView:
        return act(service, deal_id, user, lambda: service.request_refund(deal_id, user))

    @app.post("/deals/{deal_id}/dispute")
    def dispute(deal_id: str, body: DisputeIn, user: Visitor, _: Ip) -> DealView:
        return act(service, deal_id, user, lambda: service.open_dispute(deal_id, user, body.reason))

    @app.post("/deals/{deal_id}/respond")
    def respond(deal_id: str, body: RespondIn, user: Visitor, _: Ip) -> DealView:
        return act(service, deal_id, user, lambda: service.respond_dispute(deal_id, user, body.note))

    @app.post("/deals/{deal_id}/payout-address")
    def payout_address(deal_id: str, body: AddressIn, user: Visitor, _: Ip) -> DealView:
        return act(service, deal_id, user, lambda: service.set_payout_address(deal_id, user, body.address))

    @app.post("/deals/{deal_id}/refund-address")
    def refund_address(deal_id: str, body: AddressIn, user: Visitor, _: Ip) -> DealView:
        return act(service, deal_id, user, lambda: service.set_refund_address(deal_id, user, body.address))

    return app
