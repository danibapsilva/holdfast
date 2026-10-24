"""Deploy tUSD to Sepolia, with the demo treasury as its minter. Refuses any other network.

    HOLDFAST_RPC_URL=... HOLDFAST_SEED=... python scripts/deploy_tusd.py

The treasury (m/44'/60'/1'/0/0 of HOLDFAST_SEED) deploys and pays the gas, so send it some
SepoliaETH from a faucet first. Prints the contract address for HOLDFAST_TUSD_ADDRESS.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, cast

from web3 import Web3
from web3.types import TxParams

from holdfast.chains.hd import HDWallet
from holdfast.guards import require_testnet

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    w3 = Web3(Web3.HTTPProvider(os.environ["HOLDFAST_RPC_URL"]))
    require_testnet(w3.eth.chain_id)  # never anything but Sepolia
    treasury = HDWallet(os.environ["HOLDFAST_SEED"]).treasury()
    spec = importlib.util.spec_from_file_location("compile_contract", ROOT / "scripts" / "compile_contract.py")
    assert spec
    assert spec.loader
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    artifact: dict[str, Any] = compiler.compile_testusd()
    factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bin"])
    tx = factory.constructor(treasury.address).build_transaction(
        cast(TxParams, {"from": treasury.address, "nonce": w3.eth.get_transaction_count(treasury.address)})
    )
    signed = treasury.sign_transaction(cast(dict[str, Any], tx))
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction), timeout=300)
    print(f"tUSD deployed at {receipt['contractAddress']} (minter {treasury.address})")
    return 0 if receipt["status"] == 1 else 1


if __name__ == "__main__":
    sys.exit(main())
