#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from puzzle_ops.assets import asset_storage_from_env  # noqa: E402
from puzzle_ops.production import resolve_runtime_dir  # noqa: E402


def main() -> int:
    provider = asset_storage_from_env(resolve_runtime_dir())
    health = provider.healthcheck()
    result: dict[str, object] = {"health": health, "upload_test": {"enabled": False}}

    if os.environ.get("PUZZLEOPS_OSS_SMOKE_UPLOAD", "").strip() == "1":
        with tempfile.NamedTemporaryFile(prefix="puzzleops_oss_smoke_", suffix=".txt", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(b"puzzleops oss smoke")
        try:
            stored = provider.upload(temp_path, content_type="text/plain", actor="smoke")
            downloaded = provider.download(stored.object_key)
            result["upload_test"] = {
                "enabled": True,
                "status": "ok" if downloaded == b"puzzleops oss smoke" else "failed",
                "object_key": stored.object_key,
                "public_url": stored.public_url,
                "sha256": stored.sha256,
                "size_bytes": stored.size_bytes,
            }
        finally:
            temp_path.unlink(missing_ok=True)

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if bool(health.get("ready")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
