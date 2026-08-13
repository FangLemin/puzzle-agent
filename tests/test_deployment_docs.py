from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_fastapi_run_script_documents_safe_defaults():
    script = ROOT / "scripts" / "run_api.sh"

    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "uvicorn puzzle_ops.api:app" in content
    assert "--host ${PUZZLEOPS_API_HOST:-127.0.0.1}" in content
    assert "--port ${PUZZLEOPS_API_PORT:-8000}" in content
    assert "PUZZLEOPS_API_TOKENS" in content


def test_fastapi_smoke_script_checks_docs_health_and_forbidden_country():
    script = ROOT / "scripts" / "smoke_api.sh"

    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "/openapi.json" in content
    assert "/api/health" in content
    assert "/api/value/analyze" in content
    assert "forbidden_country" in content
    assert "PUZZLEOPS_API_TOKEN" in content


def test_deployment_doc_covers_six_person_fastapi_checklist():
    doc = ROOT / "docs" / "DEPLOYMENT.md"

    assert doc.exists()
    content = doc.read_text(encoding="utf-8")
    for needle in (
        "http://127.0.0.1:8000/docs",
        "http://<服务器IP>:8000/docs",
        "/api/health",
        "/api/rag/search",
        "/api/value/analyze",
        "/api/harness/summary",
        "/api/visual-similarity/search",
        "/api/assets/upload",
        "/api/metrics/dashboard",
        "PUZZLEOPS_API_TOKENS",
        "viewer",
            "operator",
            "admin",
            "飞书写入接口暂缓开放",
            "alembic upgrade head",
            "scripts/smoke_postgres.py",
            "scripts/smoke_oss.py",
            "scripts/smoke_rq.py",
            "PUZZLEOPS_JOB_QUEUE_PROVIDER=rq",
            "REDIS_URL",
            "PUZZLEOPS_RQ_QUEUE",
            "PUZZLEOPS_INIT_DB=1",
        ):
            assert needle in content


def test_oss_smoke_script_exists_and_uses_upload_guard():
    script = ROOT / "scripts" / "smoke_oss.py"

    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "PUZZLEOPS_OSS_SMOKE_UPLOAD" in content
    assert "asset_storage_from_env" in content
    assert "ALIYUN_OSS_ACCESS_KEY_SECRET" not in content


def test_rq_smoke_script_and_worker_mode_are_documented():
    smoke = ROOT / "scripts" / "smoke_rq.py"
    worker = ROOT / "scripts" / "run_worker.sh"

    assert smoke.exists()
    assert worker.exists()
    smoke_content = smoke.read_text(encoding="utf-8")
    worker_content = worker.read_text(encoding="utf-8")
    assert "PUZZLEOPS_JOB_QUEUE_PROVIDER" in smoke_content
    assert "REDIS_URL" in smoke_content
    assert "rq worker" in worker_content
    assert "execute_job_once" in worker_content
