"""Fail if anything in the repo points at a real network: a mainnet RPC host, the mainnet explorer, or
chain id 1. Run in CI on every push, so a mainnet URL can't slip in by accident.

    python scripts/check_mainnet.py [root]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Hosts are written split so this file doesn't trip its own check.
HOSTS = [
    "main" + "net.infura.io",
    "eth-main" + "net.g.alchemy.com",
    "eth-main" + "net.alchemyapi.io",
    "cloudflare-" + "eth.com",
    "eth.llama" + "rpc.com",
    "rpc.ankr.com/" + "eth",
    "ethereum." + "publicnode.com",
    "ethereum-rpc." + "publicnode.com",
    "eth." + "drpc.org",
    "rpc.flash" + "bots.net",
    "rpc.mev" + "blocker.io",
    "api.ether" + "scan.io",
    "//ether" + "scan.io",
    "//www.ether" + "scan.io",
]
CHAIN_ID_ONE = re.compile(r"chain_?id\s*[:=]\s*(?:0x0*)?1\b", re.IGNORECASE)
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "build", "__pycache__", ".pytest_cache", ".ruff_cache"}
TEXT = {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".sol", ".txt", ".cfg", ".ini", ".env", ".example", ".sh"}


def problems(root: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or SKIP_DIRS & set(path.relative_to(root).parts):
            continue
        if path.suffix not in TEXT and path.name not in {"LICENSE", "Dockerfile", ".env.example"}:
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        for n, line in enumerate(path.read_text("utf-8", errors="ignore").splitlines(), 1):
            hit = next((h for h in HOSTS if h in line.lower()), None)
            if hit or CHAIN_ID_ONE.search(line):
                found.append(f"{path.relative_to(root)}:{n}: {hit or 'chain id 1'}: {line.strip()[:120]}")
    return found


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent)
    found = problems(root)
    for line in found:
        print(line)
    print(f"mainnet check: {'FAILED' if found else 'clean'} ({len(found)} problems)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
