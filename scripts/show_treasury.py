"""Print the demo treasury's address and its SepoliaETH balance, so you know where to send faucet ETH.

    python scripts/show_treasury.py --env-file ~/.config/holdfast/holdfast.env

Needs HOLDFAST_RPC_URL and HOLDFAST_SEED. Prints the address and balance only, never the seed.
"""

from __future__ import annotations

import argparse
import sys

from web3 import Web3

from holdfast.chains.hd import HDWallet
from holdfast.chains.sepolia import EXPLORER
from holdfast.config import load_env_file, require_env
from holdfast.errors import HoldfastError
from holdfast.guards import require_testnet
from holdfast.money import ETH, format_amount


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--env-file", help="read settings from this KEY=VALUE file first")
    args = parser.parse_args()
    try:
        if args.env_file:
            load_env_file(args.env_file)
        w3 = Web3(Web3.HTTPProvider(require_env("HOLDFAST_RPC_URL")))
        require_testnet(w3.eth.chain_id)
        treasury = HDWallet(require_env("HOLDFAST_SEED")).treasury().address
    except HoldfastError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    balance = w3.eth.get_balance(treasury)
    print(f"treasury  {treasury}")
    print(f"balance   {format_amount(balance, ETH)} on Sepolia")
    print(f"explorer  {EXPLORER}/address/{treasury}")
    if balance == 0:
        print("\nIt's empty: paste the address into a Sepolia faucet, then run this again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
