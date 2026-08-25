from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ASSET_NODEIDS = ROOT / "tests" / "private_asset_nodeids.txt"


def load_private_asset_nodeids() -> tuple[str, ...]:
    return tuple(
        line.strip()
        for line in PRIVATE_ASSET_NODEIDS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def build_pytest_command() -> list[str]:
    command = [sys.executable, "-m", "pytest", "tests", "-q"]
    command.extend(f"--deselect={nodeid}" for nodeid in load_private_asset_nodeids())
    return command


def main() -> int:
    return subprocess.call(build_pytest_command(), cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
