#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from puzzle_ops.worker import DEFAULT_QUEUE_NAME, queue_from_env  # noqa: E402


def main() -> int:
    provider = os.environ.get("PUZZLEOPS_JOB_QUEUE_PROVIDER", "local").strip().lower() or "local"
    redis_url = os.environ.get("REDIS_URL", "").strip()
    queue_name = os.environ.get("PUZZLEOPS_RQ_QUEUE", DEFAULT_QUEUE_NAME).strip() or DEFAULT_QUEUE_NAME
    queue = queue_from_env()
    payload: dict[str, object] = {
        "provider": provider,
        "queue_name": queue_name,
        "configured": bool(provider == "rq" and redis_url),
        "ready": False,
    }
    if provider != "rq":
        payload["status"] = "local_fallback"
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if queue is None:
        payload["status"] = "missing_config"
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    try:
        from redis import Redis  # type: ignore

        redis_conn = Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        redis_conn.ping()
    except Exception as exc:
        payload["status"] = "failed"
        payload["error"] = str(exc)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    payload["ready"] = True
    payload["status"] = "ok"
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
