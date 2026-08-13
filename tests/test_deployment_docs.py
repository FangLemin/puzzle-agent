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
        "PUZZLEOPS_API_TOKENS",
        "viewer",
            "operator",
            "admin",
            "飞书写入接口暂缓开放",
            "alembic upgrade head",
            "scripts/smoke_postgres.py",
            "PUZZLEOPS_INIT_DB=1",
        ):
            assert needle in content
