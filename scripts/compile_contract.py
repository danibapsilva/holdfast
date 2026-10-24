"""Compile contracts/TestUSD.sol with a pinned solc and write its ABI into the package.

    python scripts/compile_contract.py          compile, and update src/holdfast/chains/tusd_abi.json
    python scripts/compile_contract.py --check  compile, and fail if the committed ABI is out of date (CI)

The bytecode goes to build/TestUSD.json (not committed); the deploy step reads it from there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import solcx

SOLC = "0.8.24"
ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "contracts" / "TestUSD.sol"
ABI_OUT = ROOT / "src" / "holdfast" / "chains" / "tusd_abi.json"
BUILD_OUT = ROOT / "build" / "TestUSD.json"


def compile_testusd() -> dict[str, object]:
    if SOLC not in [str(v) for v in solcx.get_installed_solc_versions()]:
        solcx.install_solc(SOLC)
    out = solcx.compile_files([str(SOURCE)], output_values=["abi", "bin"], solc_version=SOLC, optimize=True)
    return next(v for k, v in out.items() if k.endswith(":TestUSD"))


def main() -> int:
    artifact = compile_testusd()
    abi = json.dumps(artifact["abi"], indent=1) + "\n"
    if "--check" in sys.argv:
        if ABI_OUT.read_text() != abi:
            print("tusd_abi.json is out of date: run python scripts/compile_contract.py")
            return 1
        print("ABI up to date")
        return 0
    ABI_OUT.write_text(abi)
    BUILD_OUT.parent.mkdir(exist_ok=True)
    BUILD_OUT.write_text(json.dumps(artifact))
    print(f"compiled TestUSD with solc {SOLC}: {len(str(artifact['bin'])) // 2} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
