from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_mainnet", ROOT / "scripts" / "check_mainnet.py")
assert spec
assert spec.loader
check = importlib.util.module_from_spec(spec)
sys.modules["check_mainnet"] = check
spec.loader.exec_module(check)


def test_the_repo_is_clean() -> None:
    assert check.problems(ROOT) == []


def test_finds_a_mainnet_host(tmp_path: Path) -> None:
    (tmp_path / "config.py").write_text('RPC = "https://' + "main" + 'net.infura.io/v3/x"\n')
    assert len(check.problems(tmp_path)) == 1


def test_finds_chain_id_one(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("chain_id = " + "1\n")
    (tmp_path / "b.json").write_text('{"chainId": ' + '"0x1"}\n')
    (tmp_path / "c.py").write_text("chain_id = 11155111\n")
    assert sorted(p.split(":")[0] for p in check.problems(tmp_path)) == ["a.py"]


def test_skips_the_virtualenv(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "x.py").write_text("chain_id = " + "1\n")
    assert check.problems(tmp_path) == []
