#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -z "${PUZZLEOPS_API_TOKENS:-}" ]]; then
  echo "PUZZLEOPS_API_TOKENS is required. Example:"
  echo "PUZZLEOPS_API_TOKENS='ops_jp:jp_token:operator:日本,ops_fr:fr_token:operator:法国,admin:admin_token:admin:日本|法国'"
  exit 2
fi

export PYTHONPATH="${PYTHONPATH:-.}"

exec uvicorn puzzle_ops.api:app \
  --host ${PUZZLEOPS_API_HOST:-127.0.0.1} \
  --port ${PUZZLEOPS_API_PORT:-8000}
