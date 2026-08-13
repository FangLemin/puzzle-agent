#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="${PYTHONPATH:-.}"

python - <<'PY'
import os
import time
from puzzle_ops.production import resolve_runtime_dir
from puzzle_ops.production_db import create_repository_from_env
from puzzle_ops.worker import execute_job_once

poll_seconds = float(os.environ.get("PUZZLEOPS_WORKER_POLL_SECONDS", "2"))
runtime_dir = resolve_runtime_dir()
repo = create_repository_from_env(runtime_dir)
print(f"PuzzleOps worker started: db={getattr(repo, 'backend', 'sqlite')} runtime={runtime_dir}")

while True:
    jobs = repo.jobs(status="queued", limit=1)
    if not jobs:
        time.sleep(poll_seconds)
        continue
    job_id = str(jobs[0]["job_id"])
    result = execute_job_once(repo, job_id)
    print(f"job {job_id} -> {result.get('status')}")
PY
