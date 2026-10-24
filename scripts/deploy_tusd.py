"""Deploy tUSD to Sepolia with the demo treasury as its minter. Refuses any other network.

    python scripts/deploy_tusd.py --env-file ~/.config/holdfast/holdfast.env

Needs HOLDFAST_RPC_URL and HOLDFAST_SEED. The treasury (m/44'/60'/1'/0/0 of the seed) deploys and pays the
gas, so fund it with SepoliaETH first (scripts/show_treasury.py shows its address and balance). Prints the
contract's address, its Etherscan link, and the line to add to the settings file.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

from web3 import Web3
from web3.types import TxParams

from holdfast.chains.hd import HDWallet
from holdfast.chains.sepolia import EXPLORER
from holdfast.config import load_env_file, require_env
from holdfast.errors import HoldfastError
from holdfast.guards import require_testnet

ROOT = Path(__file__).resolve().parent.parent
RECEIPT_TIMEOUT = 300


def compile_testusd() -> dict[str, Any]:
    """Compile with the pinned solc, by way of scripts/compile_contract.py (scripts aren't a package)."""
    spec = importlib.util.spec_from_file_location("compile_contract", ROOT / "scripts" / "compile_contract.py")
    if spec is None or spec.loader is None:
        raise HoldfastError("Can't load scripts/compile_contract.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    return cast(dict[str, Any], compiler.compile_testusd())


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--env-file", help="read settings from this KEY=VALUE file first")
    args = parser.parse_args()
    try:
        if args.env_file:
            load_env_file(args.env_file)
        w3 = Web3(Web3.HTTPProvider(require_env("HOLDFAST_RPC_URL")))
        require_testnet(w3.eth.chain_id)  # never anything but Sepolia
        treasury = HDWallet(require_env("HOLDFAST_SEED")).treasury()
    except HoldfastError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if w3.eth.get_balance(treasury.address) == 0:
        print(f"error: the treasury {treasury.address} has no SepoliaETH for gas; fund it first", file=sys.stderr)
        return 1
    artifact = compile_testusd()
    factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bin"])
    tx = factory.constructor(treasury.address).build_transaction(
        cast(TxParams, {"from": treasury.address, "nonce": w3.eth.get_transaction_count(treasury.address)})
    )
    signed = treasury.sign_transaction(cast(dict[str, Any], tx))
    print("deploying tUSD (this waits for the block)...")
    receipt = w3.eth.wait_for_transaction_receipt(
        w3.eth.send_raw_transaction(signed.raw_transaction), timeout=RECEIPT_TIMEOUT
    )
    if receipt["status"] != 1:
        print(f"error: the deployment reverted: {EXPLORER}/tx/{receipt['transactionHash'].to_0x_hex()}")
        return 1
    address = receipt["contractAddress"]
    print(f"tUSD      {address} (minter: the treasury, {treasury.address})")
    print(f"explorer  {EXPLORER}/address/{address}")
    print(f"\nAdd this line to the settings file:\nHOLDFAST_TUSD_ADDRESS={address}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
