"""The HTTP API over FakeChain and MemoryStore, the way the web playground calls it."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holdfast.api import create_app
from holdfast.api.app import STATUS, status_for
from holdfast.api.auth import Unauthorized, bearer_token, visitor_id
from holdfast.api.limits import OpenDealLimit, RateLimiter, TokenBucket, TooManyRequests
from holdfast.chains import ChainError
from holdfast.chains.fake import FakeChain
from holdfast.deal import State
from holdfast.errors import HoldfastError
from holdfast.guards import WrongNetwork
from holdfast.service import DEMO_USER, EscrowService
from tests.conftest import DOLLAR, TUSD

ORIGIN = "https://danibapsilva.github.io"
WALLET = "0x" + "c3" * 20


class Visitor:
    """One playground visitor: starts a deal, then acts on it with its token."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.token = ""
        self.deal_id = ""

    def start(self, role: str = "buyer", amount: str = "25", description: str = "A logo") -> dict[str, Any]:
        r = self.client.post("/demo/deals", json={"role": role, "amount": amount, "description": description})
        assert r.status_code == 201, r.text
        body = r.json()
        self.token, self.deal_id = body["token"], body["deal"]["id"]
        return body["deal"]

    def get(self) -> dict[str, Any]:
        r = self.client.get(f"/deals/{self.deal_id}", headers=self.auth())
        assert r.status_code == 200, r.text
        return r.json()

    def post(self, action: str, json: dict[str, str] | None = None) -> Any:
        return self.client.post(f"/deals/{self.deal_id}/{action}", headers=self.auth(), json=json)

    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@pytest.fixture
def client(service: EscrowService) -> Iterator[TestClient]:
    app = create_app(service, allowed_origins=(ORIGIN,), rate=RateLimiter(capacity=1000), open_deals=OpenDealLimit(3))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def visitor(client: TestClient) -> Visitor:
    return Visitor(client)


def confirm_payments(service: EscrowService, chain: FakeChain) -> None:
    chain.mine(2)
    service.tick()


# ---- a whole trade, both ways ----


def test_visitor_buys_from_the_demo_bot(visitor: Visitor, service: EscrowService, chain: FakeChain) -> None:
    deal = visitor.start(role="buyer")
    assert deal["state"] == "awaiting_payment"
    assert deal["you"] == "buyer"
    assert deal["with_demo_bot"]
    assert deal["amount"] == {"units": str(25 * DOLLAR), "text": "25 tUSD"}
    assert deal["deposit"]["url"].startswith("fake://address/")
    assert deal["actions"] == ["pay", "cancel", "refund_address"]

    paid = visitor.post("pay").json()
    assert paid["tx"]["url"].startswith("fake://tx/")
    confirm_payments(service, chain)
    deal = visitor.get()
    assert deal["state"] == "funded"
    assert deal["payments"][0]["counted"]
    assert deal["actions"] == ["confirm", "dispute", "refund_address"]

    deal = visitor.post("confirm").json()
    assert deal["state"] == "released"  # the demo bot's payout address was set when it accepted
    assert deal["settlements"][0]["url"].startswith("fake://tx/")
    assert deal["actions"] == []


def test_visitor_sells_to_the_demo_bot(visitor: Visitor, service: EscrowService, chain: FakeChain) -> None:
    deal = visitor.start(role="seller", amount="12.5")
    assert deal["you"] == "seller"
    assert "pay" not in deal["actions"]  # the demo bot pays on its own
    visitor.post("payout-address", {"address": WALLET})
    confirm_payments(service, chain)
    assert visitor.get()["state"] == "funded"
    deal = visitor.post("deliver").json()
    assert deal["state"] == "released"  # the demo bot trusts the delivery and releases
    assert chain.balance(WALLET, TUSD) == int(deal["payout"]["units"])


def test_dispute_and_answer(visitor: Visitor, service: EscrowService, chain: FakeChain) -> None:
    visitor.start(role="seller")
    confirm_payments(service, chain)
    deal = visitor.post("dispute", {"reason": "They never confirmed"}).json()
    assert deal["state"] == "disputed"
    assert deal["dispute"]["opened_by"] == "seller"
    assert "respond" not in deal["actions"]  # the other side answers
    r = visitor.post("respond", {"note": "me again"})
    assert r.status_code == 400
    assert r.json() == {"error": "The other side answers a dispute"}


def test_seller_refunds_and_buyer_cancels(
    visitor: Visitor, client: TestClient, service: EscrowService, chain: FakeChain
) -> None:
    visitor.start(role="seller")
    confirm_payments(service, chain)
    assert visitor.post("refund").json()["state"] == "refunded"  # back to the demo bot's address
    buyer = Visitor(client)
    buyer.start(role="buyer")
    assert buyer.post("cancel").json()["state"] == "cancelled"


def test_refund_address(visitor: Visitor) -> None:
    visitor.start(role="buyer")
    deal = visitor.post("refund-address", {"address": WALLET}).json()
    assert deal["refund_address"] == WALLET.lower()


# ---- who can see and do what ----


def test_a_token_only_opens_its_own_deal(client: TestClient, visitor: Visitor) -> None:
    visitor.start()
    other = Visitor(client)
    other.start()
    r = client.get(f"/deals/{visitor.deal_id}", headers=other.auth())
    assert r.status_code == 404  # someone else's deal looks like no deal at all
    assert r.json() == {"error": "No deal with that id"}
    assert client.post(f"/deals/{visitor.deal_id}/cancel", headers=other.auth()).status_code == 404


@pytest.mark.parametrize("header", [None, "Basic abc", "Bearer short", "Bearer "])
def test_missing_or_bad_tokens(client: TestClient, visitor: Visitor, header: str | None) -> None:
    visitor.start()
    headers = {"Authorization": header} if header is not None else {}
    r = client.get(f"/deals/{visitor.deal_id}", headers=headers)
    assert r.status_code == 401
    assert "Bearer" in r.json()["error"]


def test_unknown_deal(client: TestClient, visitor: Visitor) -> None:
    visitor.start()
    r = client.get("/deals/nope", headers=visitor.auth())
    assert r.status_code == 404


def test_illegal_actions_are_conflicts(visitor: Visitor) -> None:
    visitor.start(role="buyer")
    r = visitor.post("confirm")  # not paid yet
    assert r.status_code == 409
    assert r.json() == {"error": "Can't confirm delivery while the deal is awaiting payment"}


def test_a_seller_can_not_use_the_demo_payment(visitor: Visitor) -> None:
    visitor.start(role="seller")
    r = visitor.post("pay")
    assert r.status_code == 400
    assert "Only the buyer" in r.json()["error"]


def test_the_wrong_role_is_forbidden(visitor: Visitor) -> None:
    visitor.start(role="buyer")
    r = visitor.post("payout-address", {"address": WALLET})
    assert r.status_code == 403
    assert r.json() == {"error": "The buyer can't set payout address"}


# ---- input ----


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"role": "arbiter", "amount": "5", "description": "x"}, "role"),
        ({"role": "buyer", "amount": "", "description": "x"}, "amount"),
        ({"role": "buyer", "amount": "5", "description": ""}, "description"),
        ({"role": "buyer", "amount": "5", "description": "x" * 201}, "description"),
    ],
)
def test_invalid_requests(client: TestClient, body: dict[str, str], message: str) -> None:
    r = client.post("/demo/deals", json=body)
    assert r.status_code == 422
    assert r.json()["error"].startswith(message)


@pytest.mark.parametrize(("amount", "status"), [("abc", 422), ("0.0000001", 422), ("1000", 422)])
def test_amounts_are_checked(client: TestClient, amount: str, status: int) -> None:
    r = client.post("/demo/deals", json={"role": "buyer", "amount": amount, "description": "x"})
    assert r.status_code == status
    assert r.json()["error"]


def test_bad_address(visitor: Visitor) -> None:
    visitor.start(role="seller")
    r = visitor.post("payout-address", {"address": "not-an-address"})
    assert r.status_code == 422
    assert "isn't an address" in r.json()["error"]


# ---- limits ----


def test_open_deals_per_ip(client: TestClient) -> None:
    for _ in range(3):
        Visitor(client).start()
    r = client.post("/demo/deals", json={"role": "buyer", "amount": "5", "description": "x"})
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "60"
    assert "3 demo deals open" in r.json()["error"]


def test_closed_deals_stop_counting(client: TestClient) -> None:
    visitors = [Visitor(client) for _ in range(3)]
    for v in visitors:
        v.start()
    visitors[0].post("cancel")
    Visitor(client).start()  # one closed, so there's room again


def test_request_rate(service: EscrowService) -> None:
    now = [0.0]
    app = create_app(
        service, allowed_origins=(ORIGIN,), rate=RateLimiter(capacity=2, per_second=1, clock=lambda: now[0])
    )
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/health").status_code == 200
        r = c.get("/health")
        assert r.status_code == 429
        assert r.headers["Retry-After"] == "1"
        now[0] += 1.0  # a second refills one request
        assert c.get("/health").status_code == 200


def test_token_bucket_and_idle_buckets() -> None:
    bucket = TokenBucket(capacity=1, per_second=0.5)
    assert bucket.take(0.0)
    assert not bucket.take(1.0)  # half a token back
    assert bucket.take(2.0)
    now = [0.0]
    limiter = RateLimiter(capacity=1, per_second=1, clock=lambda: now[0])
    limiter.check("a")
    now[0] = 10_000.0
    limiter.check("b")  # "a" sat idle long enough to be forgotten
    assert list(limiter._buckets) == ["b"]


# ---- the rest ----


def test_health(client: TestClient, chain: FakeChain) -> None:
    body = client.get("/health").json()
    assert body["ok"]
    assert body["network"] == "fake"
    assert body["chain_id"] == 11155111
    assert body["head"] == chain.head()
    assert body["token"] == "tUSD"
    assert body["treasury"]["value"] == chain.demo_address()


def test_cors_allows_only_the_site(client: TestClient) -> None:
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}
    ok = client.options("/demo/deals", headers={"Origin": ORIGIN, **preflight})
    assert ok.headers["access-control-allow-origin"] == ORIGIN
    blocked = client.options("/demo/deals", headers={"Origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in blocked.headers


def test_network_errors_map_to_gateway_errors(visitor: Visitor, chain: FakeChain, service: EscrowService) -> None:
    visitor.start(role="seller")
    confirm_payments(service, chain)
    visitor.post("payout-address", {"address": WALLET})
    chain.fail_sends = True
    r = visitor.post("deliver")
    assert r.status_code == 502
    assert r.json() == {"error": "The network rejected the transaction"}


def test_unexpected_errors_stay_generic(service: EscrowService, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(service, allowed_origins=(ORIGIN,))

    def broken() -> int:
        raise RuntimeError("a bug with internals in its message")

    monkeypatch.setattr(service.chain, "head", broken)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/health")
    assert r.status_code == 500
    assert r.json() == {"error": "Something went wrong on our side"}


def test_status_table() -> None:
    assert status_for(WrongNetwork("x")) == 503
    assert status_for(ChainError("x")) == 502
    assert status_for(TooManyRequests("x")) == 429
    assert status_for(HoldfastError("x")) == 400
    kinds = [kind for kind, _ in STATUS]
    for i, kind in enumerate(kinds):  # no class is shadowed by a parent listed before it
        assert not any(issubclass(kind, parent) for parent in kinds[:i]), kind


def test_auth_helpers() -> None:
    assert visitor_id("t" * 43).startswith("visitor-")
    assert visitor_id("a" * 43) != visitor_id("b" * 43)
    assert bearer_token("bearer " + "x" * 43) == "x" * 43
    with pytest.raises(Unauthorized):
        bearer_token(None)


def test_demo_user_never_gets_a_token(client: TestClient) -> None:
    body = client.post("/demo/deals", json={"role": "buyer", "amount": "5", "description": "x"}).json()
    assert visitor_id(body["token"]) != DEMO_USER
    assert body["deal"]["state"] == State.AWAITING_PAYMENT
