"""Ethereum's Sepolia test network: native SepoliaETH and the Holdfast tUSD token, through web3.py.

- The network is checked when the adapter starts and again before every send: if the RPC isn't
  Sepolia, nothing happens (`guards.require_testnet`).
- tUSD payments are found from the token's Transfer logs filtered to the deposit address (cheap).
  Native ETH payments are found by scanning recent blocks for transfers to it (slower; the demo
  defaults to tUSD for that reason).
- A deposit address that has to send tUSD needs a little ETH for gas: the treasury tops it up first.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any, cast

from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.contract import Contract
from web3.types import TxParams, TxReceipt, Wei

from holdfast.chains import ChainError
from holdfast.chains.hd import HDWallet
from holdfast.deal import Payment
from holdfast.guards import ALLOWED_CHAIN_IDS, require_testnet
from holdfast.money import Token

EXPLORER = "https://sepolia.etherscan.io"
NATIVE_GAS = 21_000
ERC20_GAS = 65_000
MINT_GAS = 90_000
GAS_HEADROOM = 1.5  # top-ups cover this many times the estimate
NATIVE_SCAN_BLOCKS = 2_000  # how far back a native ETH scan looks at most
RECEIPT_TIMEOUT = 180


def _tx(**fields: object) -> TxParams:
    """A transaction dict (web3 types every amount as Wei; ours are plain ints, already in wei)."""
    return cast(TxParams, fields)


def _abi() -> list[dict[str, Any]]:
    return json.loads(resources.files("holdfast.chains").joinpath("tusd_abi.json").read_text())


class SepoliaChain:
    """`Chain` on Sepolia: deposit addresses from the HD wallet, payments from logs, sends signed locally."""

    name = "sepolia"

    def __init__(
        self,
        w3: Web3,
        wallet: HDWallet,
        tusd_address: str | None,
        allowed: frozenset[int] = ALLOWED_CHAIN_IDS,
    ) -> None:
        self.w3 = w3
        self.wallet = wallet
        self.allowed = allowed
        self.tusd: Contract | None = (
            w3.eth.contract(address=Web3.to_checksum_address(tusd_address), abi=_abi()) if tusd_address else None
        )
        self.check_network()

    # ---- network ----

    def check_network(self) -> None:
        require_testnet(self.w3.eth.chain_id, self.allowed)

    def head(self) -> int:
        return self.w3.eth.block_number

    def normalize_address(self, text: str) -> str:
        if not Web3.is_address(text.strip()):
            raise ChainError(f"{text!r} isn't an Ethereum address")
        return Web3.to_checksum_address(text.strip())

    def deposit_address(self, index: int) -> str:
        return self.wallet.deposit(index).address

    def demo_address(self) -> str:
        return self.wallet.treasury().address

    def tx_url(self, tx_hash: str) -> str:
        return f"{EXPLORER}/tx/{tx_hash}"

    def address_url(self, address: str) -> str:
        return f"{EXPLORER}/address/{address}"

    # ---- reading payments ----

    def _token_contract(self, token: Token) -> Contract:
        if self.tusd is None or token.contract is None:
            raise ChainError("No tUSD contract is configured")
        if Web3.to_checksum_address(token.contract) != self.tusd.address:
            raise ChainError(f"{token.symbol} at {token.contract} isn't the configured tUSD contract")
        return self.tusd

    def payments_to(self, address: str, token: Token, from_block: int = 0) -> list[Payment]:
        head = self.head()
        to = Web3.to_checksum_address(address)
        if token.native:
            return self._native_payments(to, max(from_block, head - NATIVE_SCAN_BLOCKS), head)
        logs = (
            self._token_contract(token)
            .events.Transfer()
            .get_logs(argument_filters={"to": to}, from_block=from_block, to_block=head)
        )
        return [
            Payment(
                tx_hash=log["transactionHash"].to_0x_hex(),
                log_index=log["logIndex"],
                amount=log["args"]["value"],
                confirmations=head - log["blockNumber"] + 1,
            )
            for log in logs
            if log["args"]["value"] > 0
        ]

    def _native_payments(self, to: str, start: int, head: int) -> list[Payment]:
        found: list[Payment] = []
        for number in range(max(start, 0), head + 1):
            block = self.w3.eth.get_block(number, full_transactions=True)
            for tx in cast(list[Any], block.get("transactions", [])):
                incoming = tx.get("to") == to and tx["value"] > 0
                if incoming and self.w3.eth.get_transaction_receipt(tx["hash"])["status"] == 1:
                    found.append(
                        Payment(tx_hash=tx["hash"].to_0x_hex(), amount=tx["value"], confirmations=head - number + 1)
                    )
        return found

    # ---- sending ----

    def _gas_price(self) -> int:
        return self.w3.eth.gas_price

    def _sign_and_send(self, account: LocalAccount, tx: TxParams) -> str:
        self.check_network()  # every send re-checks the network
        tx["chainId"] = self.w3.eth.chain_id
        tx["nonce"] = self.w3.eth.get_transaction_count(account.address, "pending")
        if "gasPrice" not in tx:
            tx["gasPrice"] = Wei(self._gas_price())
        signed = account.sign_transaction(cast(dict[str, Any], tx))
        try:
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt: TxReceipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=RECEIPT_TIMEOUT)
        except Exception as e:  # rejected, reverted in simulation, or the RPC failed
            raise ChainError(f"The network didn't take the transaction: {str(e)[:200]}") from e
        if receipt["status"] != 1:
            raise ChainError(f"Transaction {tx_hash.to_0x_hex()} reverted")
        return tx_hash.to_0x_hex()

    def _top_up_gas(self, address: str, needed: int) -> None:
        have = self.w3.eth.get_balance(Web3.to_checksum_address(address))
        if have >= needed:
            return
        self._sign_and_send(
            self.wallet.treasury(),
            _tx(to=Web3.to_checksum_address(address), value=needed - have, gas=NATIVE_GAS),
        )

    # build_transaction gets nonce=0 only so web3 doesn't look one up: _sign_and_send sets the real one
    # (from the pending count) just before signing, after re-checking the network.

    def send(self, from_index: int, to: str, token: Token, amount: int) -> str:
        account = self.wallet.deposit(from_index)
        recipient = self.normalize_address(to)
        price = self._gas_price()
        if token.native:
            self._top_up_gas(account.address, amount + int(NATIVE_GAS * price * GAS_HEADROOM))
            return self._sign_and_send(account, _tx(to=recipient, value=amount, gas=NATIVE_GAS, gasPrice=price))
        contract = self._token_contract(token)
        self._top_up_gas(account.address, int(ERC20_GAS * price * GAS_HEADROOM))
        tx = contract.functions.transfer(recipient, amount).build_transaction(
            _tx(**{"from": account.address}, gas=ERC20_GAS, gasPrice=price, nonce=0, chainId=self.w3.eth.chain_id)
        )
        return self._sign_and_send(account, tx)

    def fund_demo(self, to: str, token: Token, amount: int) -> str:
        """The demo bot pays a deal: mints tUSD (it's the token's minter) or sends treasury ETH."""
        treasury = self.wallet.treasury()
        recipient = self.normalize_address(to)
        if token.native:
            return self._sign_and_send(treasury, _tx(to=recipient, value=amount, gas=NATIVE_GAS))
        tx = (
            self._token_contract(token)
            .functions.mint(recipient, amount)
            .build_transaction(
                _tx(
                    **{"from": treasury.address},
                    gas=MINT_GAS,
                    gasPrice=self._gas_price(),
                    nonce=0,
                    chainId=self.w3.eth.chain_id,
                )
            )
        )
        return self._sign_and_send(treasury, tx)
