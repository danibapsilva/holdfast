# pyright: basic
# (eth_tester ships without types; the rest of the suite is strict)
"""SepoliaChain against a real EVM running in-process (py-evm), set to Sepolia's chain id, with the
tUSD contract compiled and deployed. Nothing touches the internet."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from eth_tester import EthereumTester, PyEVMBackend
from eth_typing import ChecksumAddress, HexStr
from web3 import EthereumTesterProvider, Web3
from web3.contract.contract import ContractEvent
from web3.providers.eth_tester.defaults import API_ENDPOINTS, static_return
from web3.types import TxParams

from holdfast.chains import ChainError
from holdfast.chains.hd import HDWallet
from holdfast.chains.sepolia import SepoliaChain
from holdfast.deal import Role, State
from holdfast.guards import SEPOLIA, Caps, WrongNetwork
from holdfast.money import ETH, Token, tusd
from holdfast.service import EscrowService, utcnow
from holdfast.store import MemoryStore

ROOT = Path(__file__).resolve().parent.parent
DEV_SEED = "test test test test test test test test test test test junk"
DOLLAR = 10**6
RECIPIENT = Web3.to_checksum_address("0x" + "c3" * 20)


def _artifact() -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("compile_contract", ROOT / "scripts" / "compile_contract.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["compile_contract"] = module
    spec.loader.exec_module(module)
    return module.compile_testusd()


ARTIFACT = _artifact()


def make_w3(chain_id: int | None = SEPOLIA) -> tuple[EthereumTester, Web3]:
    backend = PyEVMBackend()
    endpoints = {name: dict(calls) for name, calls in API_ENDPOINTS.items()}
    if chain_id is not None:
        cast(Any, type(backend.chain)).chain_id = chain_id  # what the EVM (and `block.chainid`) sees
        endpoints["eth"]["chainId"] = static_return(chain_id)  # what eth_chainId answers
    tester = EthereumTester(backend)
    return tester, Web3(EthereumTesterProvider(tester, api_endpoints=cast(Any, endpoints)))


def account(w3: Web3, i: int) -> ChecksumAddress:
    """The tester's funded accounts (web3 types the list as a one-item tuple)."""
    return list(w3.eth.accounts)[i]


def send_eth(w3: Web3, to: str, value: int) -> None:
    w3.eth.send_transaction(cast(TxParams, {"from": account(w3, 0), "to": to, "value": value}))


def deploy(w3: Web3, minter: str) -> str:
    factory = w3.eth.contract(abi=ARTIFACT["abi"], bytecode=ARTIFACT["bin"])
    tx = factory.constructor(minter).transact({"from": account(w3, 0)})
    address = w3.eth.get_transaction_receipt(tx)["contractAddress"]
    assert address
    return address


class Env:
    def __init__(self) -> None:
        self.tester, self.w3 = make_w3()
        self.wallet = HDWallet(DEV_SEED)
        treasury = self.wallet.treasury().address
        send_eth(self.w3, treasury, 10 * 10**18)
        self.tusd_address = deploy(self.w3, treasury)
        self.token: Token = tusd(self.tusd_address)
        self.chain = SepoliaChain(self.w3, self.wallet, self.tusd_address)
        self.contract = self.w3.eth.contract(address=Web3.to_checksum_address(self.tusd_address), abi=ARTIFACT["abi"])

    def balance(self, address: str) -> int:
        return self.contract.functions.balanceOf(Web3.to_checksum_address(address)).call()

    def mine(self, n: int) -> None:
        self.tester.mine_blocks(n)


@pytest.fixture
def env() -> Env:
    return Env()


def test_refuses_a_network_that_isnt_sepolia() -> None:
    _, w3 = make_w3(chain_id=None)  # the tester's own chain id
    with pytest.raises(WrongNetwork):
        SepoliaChain(w3, HDWallet(DEV_SEED), None)


def test_rechecks_the_network_before_every_send(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.chain.fund_demo(env.chain.deposit_address(1), env.token, DOLLAR)
    monkeypatch.setattr(env.chain, "allowed", frozenset({1}))  # as if the RPC moved off Sepolia
    with pytest.raises(WrongNetwork):
        env.chain.send(1, RECIPIENT, env.token, DOLLAR)


def test_the_contract_cannot_be_deployed_off_testnet() -> None:
    _, w3 = make_w3(chain_id=None)
    with pytest.raises(Exception, match=r"revert|TestnetsOnly|execution"):
        deploy(w3, account(w3, 1))


def test_contract_basics(env: Env) -> None:
    c = env.contract.functions
    assert (c.name().call(), c.symbol().call(), c.decimals().call()) == ("Holdfast Test USD", "tUSD", 6)
    assert c.minter().call() == env.wallet.treasury().address


def test_finds_tusd_payments_with_confirmations(env: Env) -> None:
    deposit = env.chain.deposit_address(5)
    tx = env.chain.fund_demo(deposit, env.token, 12 * DOLLAR)
    (payment,) = env.chain.payments_to(deposit, env.token)
    assert (payment.tx_hash, payment.amount, payment.confirmations) == (tx, 12 * DOLLAR, 1)
    env.mine(2)
    assert env.chain.payments_to(deposit, env.token)[0].confirmations == 3
    assert env.chain.payments_to(env.chain.deposit_address(6), env.token) == []


def test_sends_tusd_from_a_deposit_topping_up_gas(env: Env) -> None:
    deposit = env.chain.deposit_address(3)
    env.chain.fund_demo(deposit, env.token, 20 * DOLLAR)
    assert env.w3.eth.get_balance(Web3.to_checksum_address(deposit)) == 0
    env.chain.send(3, RECIPIENT, env.token, 15 * DOLLAR)
    assert env.balance(RECIPIENT) == 15 * DOLLAR
    assert env.balance(deposit) == 5 * DOLLAR


def test_native_eth_payment_and_send(env: Env) -> None:
    deposit = env.chain.deposit_address(7)
    send_eth(env.w3, deposit, 10**17)
    (payment,) = env.chain.payments_to(deposit, ETH)
    assert payment.amount == 10**17
    env.chain.send(7, RECIPIENT, ETH, 5 * 10**16)
    assert env.w3.eth.get_balance(Web3.to_checksum_address(RECIPIENT)) == 5 * 10**16


def test_mint_is_capped(env: Env) -> None:
    with pytest.raises(ChainError):
        env.chain.fund_demo(RECIPIENT, env.token, 1_001 * DOLLAR)


def test_only_the_minter_mints(env: Env) -> None:
    with pytest.raises(Exception, match=r"revert|NotMinter|execution"):
        env.contract.functions.mint(RECIPIENT, DOLLAR).transact({"from": account(env.w3, 1)})


def test_faucet_once_a_day(env: Env) -> None:
    visitor = account(env.w3, 2)
    env.contract.functions.faucet().transact({"from": visitor})
    assert env.balance(visitor) == 100 * DOLLAR
    with pytest.raises(Exception, match=r"revert|FaucetCooldown|execution"):
        env.contract.functions.faucet().transact({"from": visitor})


def test_transfer_from_with_allowance(env: Env) -> None:
    owner, spender = account(env.w3, 3), account(env.w3, 4)
    env.contract.functions.faucet().transact({"from": owner})
    env.contract.functions.approve(spender, 30 * DOLLAR).transact({"from": owner})
    env.contract.functions.transferFrom(owner, RECIPIENT, 30 * DOLLAR).transact({"from": spender})
    assert env.balance(RECIPIENT) == 30 * DOLLAR
    with pytest.raises(Exception, match=r"revert|InsufficientAllowance|execution"):
        env.contract.functions.transferFrom(owner, RECIPIENT, 1).transact({"from": spender})


def test_rejects_a_different_token_contract(env: Env) -> None:
    with pytest.raises(ChainError, match="isn't the configured"):
        env.chain.payments_to(RECIPIENT, tusd("0x" + "de" * 20))


def test_addresses_and_links(env: Env) -> None:
    assert env.chain.normalize_address(RECIPIENT.lower()) == Web3.to_checksum_address(RECIPIENT)
    with pytest.raises(ChainError):
        env.chain.normalize_address("0x123")
    assert env.chain.tx_url("0xabc") == "https://sepolia.etherscan.io/tx/0xabc"
    assert env.chain.address_url(RECIPIENT).startswith("https://sepolia.etherscan.io/address/")
    assert env.chain.demo_address() == env.wallet.treasury().address


def test_a_whole_demo_deal_on_the_evm(env: Env) -> None:
    service = EscrowService(MemoryStore(), env.chain, env.token, caps=Caps(100 * DOLLAR, 1000 * DOLLAR), clock=utcnow)
    deal = service.start_demo(visitor="v", visitor_role=Role.BUYER, amount=25 * DOLLAR, description="Demo item")
    service.set_refund_address(deal.id, "v", RECIPIENT)
    service.demo_pay(deal.id, "v")
    env.mine(2)
    service.tick()
    assert service.get(deal.id).state == State.FUNDED
    deal = service.confirm_delivery(deal.id, "v")
    assert deal.state == State.RELEASED
    assert env.balance(env.chain.demo_address()) == 25 * DOLLAR - deal.fee
    receipt = env.w3.eth.get_transaction_receipt(HexStr(deal.settle_txs[0]))
    assert receipt["status"] == 1
    assert json.dumps(deal.model_dump(mode="json"))  # the API can send it as is


def test_log_search_goes_in_small_ranges_and_only_covers_new_blocks(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    chain = SepoliaChain(env.w3, env.wallet, env.tusd_address, log_range=2)
    deposit = chain.deposit_address(11)
    start = chain.head()
    env.mine(5)
    chain.fund_demo(deposit, env.token, 3 * DOLLAR)
    searched: list[tuple[int, int]] = []
    real = ContractEvent.__dict__["get_logs"]  # web3's own descriptor, called the way web3 calls it

    def spy(self: Any, **kwargs: Any) -> Any:
        searched.append((kwargs["from_block"], kwargs["to_block"]))
        return real.__get__(self, type(self))(**kwargs)

    monkeypatch.setattr(ContractEvent, "get_logs", spy)
    (payment,) = chain.payments_to(deposit, env.token, from_block=start)
    assert payment.amount == 3 * DOLLAR
    assert all(end - begin < 2 for begin, end in searched)  # never more than log_range blocks at once
    first = len(searched)
    env.mine(3)
    (again,) = chain.payments_to(deposit, env.token, from_block=start)
    assert again.confirmations == payment.confirmations + 3  # from the head, no new search for old blocks
    assert searched[first][0] > searched[first - 1][1]  # the second pass starts where the first stopped


def test_an_rpc_failure_reading_payments_is_a_chain_error(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(self: Any, **kwargs: Any) -> Any:
        raise ValueError("400 Bad Request")

    monkeypatch.setattr(ContractEvent, "get_logs", refused)
    with pytest.raises(ChainError, match="Couldn't read payments"):
        env.chain.payments_to(env.chain.deposit_address(12), env.token)


def test_log_range_is_at_least_one_block(env: Env) -> None:
    with pytest.raises(ValueError, match="at least one block"):
        SepoliaChain(env.w3, env.wallet, env.tusd_address, log_range=0)
