#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${PUZZLEOPS_API_BASE_URL:-http://127.0.0.1:8000}"
TOKEN="${PUZZLEOPS_API_TOKEN:-}"

if [[ -z "$TOKEN" ]]; then
  echo "PUZZLEOPS_API_TOKEN is required for smoke checks."
  exit 2
fi

python - <<'PY'
import json
import os
import urllib.error
import urllib.request

base_url = os.environ.get("PUZZLEOPS_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
token = os.environ["PUZZLEOPS_API_TOKEN"]


def request(path, *, method="GET", payload=None):
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


status, openapi = request("/openapi.json")
assert status == 200, (status, openapi)
assert "/api/health" in openapi["paths"], openapi["paths"].keys()

status, health = request("/api/health")
assert status == 200, (status, health)
assert health["status"] == "ok", health

status, forbidden = request(
    "/api/value/analyze",
    method="POST",
    payload={"country": "法国", "subject": "薰衣草风车", "operation_tag": "试新_法国_薰衣草风车0811"},
)
assert status == 403, (status, forbidden)
assert forbidden["error"]["code"] == "forbidden_country", forbidden

print("PuzzleOps API smoke passed:", base_url)
PY
