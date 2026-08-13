#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from puzzle_ops.production_db import database_healthcheck, initialize_database


def main() -> int:
    if not os.environ.get("DATABASE_URL", "").strip():
        print(json.dumps({"status": "missing_config", "message": "DATABASE_URL is required"}, ensure_ascii=False, indent=2))
        return 2
    if os.environ.get("PUZZLEOPS_INIT_DB", "").strip().lower() in {"1", "true", "yes"}:
        init_result = initialize_database()
    else:
        init_result = {"status": "skipped", "message": "set PUZZLEOPS_INIT_DB=1 to create missing tables"}
    health = database_healthcheck()
    print(json.dumps({"init": init_result, "health": health}, ensure_ascii=False, indent=2))
    return 0 if health.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
