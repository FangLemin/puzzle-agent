import json
from pathlib import Path

from fastapi.testclient import TestClient

from puzzle_ops.api import create_app
from puzzle_ops.assets import LocalAssetStorageProvider, StoredAsset
from puzzle_ops.feishu import RealFeishuClient
from puzzle_ops.models import DemandRow
from puzzle_ops.production_db import create_repository_from_env, database_healthcheck, initialize_database, postgres_schema_statements
from puzzle_ops.server import _demand_row_payload
from puzzle_ops.storage import PuzzleRepository
from puzzle_ops.trial_upload import TrialImageUploadService
from puzzle_ops.worker import execute_job_once, enqueue_job


class ProductionFakeAgent:
    def __init__(self, repository):
        self.repository = repository
        self.asset_storage = LocalAssetStorageProvider(repository.db_path.parent / "api_assets", public_base_url="http://assets.local")
        self.rag_provider_config = None
        self.rag_vector_store_config = None
        self.visual_embedding_provider = None
        self.feishu = None

    def value_audit_rag_answer(self, country, query, top_k=5, task_index="value_master"):
        self.repository.record_trace_event(
            trace_id="trace-rag-1",
            request_id="req-rag-1",
            actor="ops_jp",
            country=country,
            task_type="rag_search",
            provider="local",
            model="local",
            input_summary=query,
            rag_citations=("jp_value#chunk-1",),
            output_summary="日本价值观依据",
            status="succeeded",
            latency_ms=12.5,
        )

        class Prompt:
            citations = ("jp_value#chunk-1",)
            context = "日本价值观：季节感、治愈、主体清晰。"
            prompt = "只根据资料回答。"

        self._last_rag_trace = {"final_hits": [{"chunk_id": "jp_value#chunk-1"}]}
        self._last_rag_rewritten_query = query
        return Prompt()

    def rag_citation_details(self, country, citations):
        return ({"chunk_id": "jp_value#chunk-1", "parent_id": "jp_value", "title": "日本价值观", "source_type": "value_rule", "text": "季节感、治愈。"},)

    def provider_health_summary(self):
        return {"qwen_vl": "configured"}


def headers(token="jp-token"):
    return {"Authorization": f"Bearer {token}"}


def test_create_repository_from_env_keeps_sqlite_default_and_exposes_postgres_config(monkeypatch, tmp_path):
    monkeypatch.delenv("PUZZLEOPS_DB_PROVIDER", raising=False)
    repo = create_repository_from_env(tmp_path / "runtime")
    assert isinstance(repo, PuzzleRepository)
    assert repo.db_path == tmp_path / "runtime" / "puzzle_ops.db"

    monkeypatch.setenv("PUZZLEOPS_DB_PROVIDER", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@db.example:5432/puzzleops")
    repo = create_repository_from_env(tmp_path / "runtime")
    assert repo.backend == "postgres"
    assert repo.database_url.startswith("postgresql+psycopg://")


def test_postgres_schema_contains_release_tables():
    schema = "\n".join(postgres_schema_statements())
    for table in (
        "users",
        "api_tokens",
        "audit_logs",
        "demand_rows",
        "trial_uploads",
        "assets",
        "jobs",
        "trace_events",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema


def test_alembic_scaffold_and_migration_reference_release_schema():
    root = Path(__file__).resolve().parents[1]
    assert (root / "alembic.ini").exists()
    assert (root / "migrations" / "env.py").exists()
    migration = root / "migrations" / "versions" / "20260813_0765_production_online_schema.py"
    assert migration.exists()
    content = migration.read_text(encoding="utf-8")
    assert "revision = \"20260813_0765\"" in content
    assert "postgres_schema_statements" in content
    assert "trace_events" in content


def test_initialize_database_executes_schema_against_sqlite_compat_url(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'compat.db'}"

    result = initialize_database(db_url)

    assert result["status"] == "ok"
    assert result["table_count"] >= 8
    health = database_healthcheck(db_url)
    assert health["status"] == "ok"
    assert health["safe_database_url"].startswith("sqlite:///")


def test_repository_persists_users_tokens_audit_jobs_assets_and_traces(tmp_path):
    repo = PuzzleRepository(tmp_path / "prod.db")
    repo.upsert_user("ops_jp", display_name="日本运营", role="operator", countries=("日本",), status="active")
    token = repo.create_api_token("ops_jp", "jp-token", created_by="admin")

    user = repo.api_user_by_token("jp-token")
    assert user["user_id"] == "ops_jp"
    assert user["role"] == "operator"
    assert user["countries"] == ("日本",)
    assert token["token_plaintext_preview"] == "jp-..."

    repo.record_audit_log(actor="ops_jp", action="trial.upload", country="日本", resource_type="trial_upload", resource_id="upload-1")
    assert repo.audit_logs(country="日本")[0]["action"] == "trial.upload"

    asset = repo.create_asset(
        object_key="uploads/2026/08/test.png",
        public_url="https://oss.example/test.png",
        sha256="abc123",
        content_type="image/png",
        size_bytes=10,
        source_filename="test.png",
        created_by="ops_jp",
    )
    repo.update_asset_feishu_token(asset["asset_id"], "file-token-1")
    assert repo.asset(asset["asset_id"])["feishu_file_token"] == "file-token-1"

    job = repo.create_job("vlm_parse", country="日本", actor="ops_jp", payload={"asset_id": asset["asset_id"]})
    repo.update_job(job["job_id"], status="succeeded", result={"subject": "寿司"}, progress=100)
    assert repo.job(job["job_id"])["result"]["subject"] == "寿司"

    repo.record_trace_event(
        trace_id="trace-1",
        request_id="req-1",
        actor="ops_jp",
        country="日本",
        task_type="value_analyze",
        provider="qwen",
        model="qwen3-vl-plus",
        input_summary="寿司图",
        rag_citations=("jp_food#c1",),
        visual_similarity_evidence={"status": "low_confidence"},
        output_summary="主体内容：寿司",
        status="succeeded",
        latency_ms=123.4,
    )
    assert repo.trace_event("trace-1")["rag_citations"] == ("jp_food#c1",)
    assert repo.latency_metrics()["p95_ms"] == 123.4


def test_local_asset_storage_uploads_and_deduplicates_by_hash(tmp_path):
    source = tmp_path / "sushi.png"
    source.write_bytes(b"fake-image")
    provider = LocalAssetStorageProvider(tmp_path / "objects", public_base_url="http://assets.local")

    first = provider.upload(source, "image/png", actor="ops_jp")
    second = provider.upload(source, "image/png", actor="ops_jp")

    assert isinstance(first, StoredAsset)
    assert first.sha256 == second.sha256
    assert first.object_key == second.object_key
    assert first.public_url.startswith("http://assets.local/")
    assert provider.download(first.object_key) == b"fake-image"


def test_trial_upload_can_create_asset_and_payload_carries_asset_id(tmp_path):
    repo = PuzzleRepository(tmp_path / "asset_trial.db")
    storage = LocalAssetStorageProvider(tmp_path / "objects", public_base_url="http://assets.local")
    service = TrialImageUploadService(tmp_path / "uploads", asset_storage=storage, repository=repo)
    row = DemandRow(
        need_type="试新",
        country="日本",
        js_category="food",
        image_name="",
        operation_tag="试新_日本_寿司0813",
        subject="寿司",
        count=1,
        priority="P1",
        method="先照片后AI",
        delivery_date="",
        subject_description="",
        remark="",
    )

    parsed, saved = service.parse(row, [{"filename": "sushi.png", "content_type": "image/png", "content": b"fake-image"}], "parse", run_vision=False)
    payload = _demand_row_payload(parsed)

    assert saved[0]["asset_id"]
    assert parsed.reference_image_asset_id == saved[0]["asset_id"]
    assert parsed.reference_image_url.startswith("http://assets.local/")
    assert payload["_reference_asset_id"] == parsed.reference_image_asset_id
    assert repo.asset(parsed.reference_image_asset_id)["public_url"] == parsed.reference_image_url


def test_feishu_reuses_existing_asset_file_token_without_upload(tmp_path):
    repo = PuzzleRepository(tmp_path / "feishu_asset.db")
    asset = repo.create_asset(
        object_key="assets/a/sushi.png",
        public_url="https://oss.example/sushi.png",
        sha256="sha-sushi",
        content_type="image/png",
        size_bytes=10,
        source_filename="sushi.png",
        created_by="ops_jp",
    )
    repo.update_asset_feishu_token(asset["asset_id"], "existing-token")
    uploads = []
    client = RealFeishuClient(
        "app",
        "secret",
        "spreadsheet",
        "tblTrial",
        "tenant",
        transport=lambda method, url, headers, body: {"code": 0, "data": {"items": [{"field_name": "图片本身"}, {"field_name": "运营tag"}], "records": [{"record_id": "rec1"}]}},
        media_transport=lambda *args: uploads.append(args) or {"code": 0, "data": {"file_token": "new-token"}},
        bitable_app_token="appToken",
        repository=repo,
    )
    row = {"运营tag": "试新_日本_寿司0813", "图片本身": [{"text": "sushi.png"}], "_reference_asset_id": asset["asset_id"]}

    result = client.write_table("提需表", [row])

    assert result.success is True
    assert uploads == []
    assert result.data["response"]["code"] == 0


def test_feishu_uploads_local_file_and_persists_asset_file_token(tmp_path):
    image = tmp_path / "sushi.png"
    image.write_bytes(b"fake-image")
    repo = PuzzleRepository(tmp_path / "feishu_upload.db")
    asset = repo.create_asset(
        object_key="assets/a/sushi.png",
        public_url="https://oss.example/sushi.png",
        sha256="sha-sushi",
        content_type="image/png",
        size_bytes=image.stat().st_size,
        source_filename="sushi.png",
        created_by="ops_jp",
    )
    client = RealFeishuClient(
        "app",
        "secret",
        "spreadsheet",
        "tblTrial",
        "tenant",
        transport=lambda method, url, headers, body: {"code": 0, "data": {"items": [{"field_name": "图片本身"}, {"field_name": "运营tag"}], "records": [{"record_id": "rec1"}]}},
        media_transport=lambda *args: {"code": 0, "data": {"file_token": "uploaded-token"}},
        bitable_app_token="appToken",
        repository=repo,
    )
    row = {
        "运营tag": "试新_日本_寿司0813",
        "图片本身": [{"text": "sushi.png"}],
        "_reference_asset_id": asset["asset_id"],
        "_reference_image_path": str(image),
        "_reference_image_content_type": "image/png",
    }

    result = client.write_table("提需表", [row])

    assert result.success is True
    assert repo.asset(asset["asset_id"])["feishu_file_token"] == "uploaded-token"


def test_api_uses_repository_tokens_and_exposes_jobs_traces_metrics(monkeypatch, tmp_path):
    monkeypatch.delenv("PUZZLEOPS_API_TOKENS", raising=False)
    repo = PuzzleRepository(tmp_path / "api.db")
    repo.upsert_user("ops_jp", display_name="日本运营", role="operator", countries=("日本",), status="active")
    repo.create_api_token("ops_jp", "jp-token", created_by="admin")
    agent = ProductionFakeAgent(repo)
    client = TestClient(create_app(agent=agent))

    me = client.get("/api/me", headers=headers())
    assert me.status_code == 200
    assert me.json()["user_id"] == "ops_jp"

    job = client.post("/api/jobs/vlm-parse", headers=headers(), json={"country": "日本", "payload": {"asset_id": "asset-1"}})
    assert job.status_code == 200
    job_id = job.json()["job_id"]
    assert job.json()["queue_provider"] == "local"
    assert job.json()["enqueue_status"] == "local_fallback"
    assert client.get(f"/api/jobs/{job_id}", headers=headers()).json()["status"] == "queued"

    rag = client.post("/api/rag/search", headers=headers(), json={"country": "日本", "query": "寿司是否符合日本价值观"})
    assert rag.status_code == 200
    trace = client.get("/api/traces/trace-rag-1", headers=headers())
    assert trace.status_code == 200
    assert trace.json()["task_type"] == "rag_search"
    metrics = client.get("/api/metrics/latency", headers=headers())
    assert metrics.status_code == 200
    assert metrics.json()["p95_ms"] == 12.5


def test_api_metrics_dashboard_combines_provider_health_jobs_and_traces(monkeypatch, tmp_path):
    monkeypatch.delenv("PUZZLEOPS_API_TOKENS", raising=False)
    repo = PuzzleRepository(tmp_path / "metrics_dashboard.db")
    repo.upsert_user("ops_jp", display_name="日本运营", role="operator", countries=("日本",), status="active")
    repo.create_api_token("ops_jp", "jp-token", created_by="admin")
    job = repo.create_job("feishu_sync", country="日本", actor="ops_jp", payload={})
    repo.update_job(job["job_id"], status="failed", error_code="AttachFieldConvFail", error_message="bad attachment")
    repo.record_trace_event(
        trace_id="trace-no-citation",
        request_id="req",
        actor="ops_jp",
        country="日本",
        task_type="rag_search",
        provider="dashscope",
        model="qwen3-rerank",
        rag_citations=(),
        status="failed",
        latency_ms=240,
    )
    client = TestClient(create_app(agent=ProductionFakeAgent(repo)))

    response = client.get("/api/metrics/dashboard?country=日本", headers=headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["country"] == "日本"
    assert payload["providers"]["agent"]["qwen_vl"] == "configured"
    assert payload["jobs"]["failure_reasons"]["AttachFieldConvFail"] == 1
    assert payload["rag"]["citation_missing_rate"] == 1.0
    assert payload["latency"]["p95_ms"] == 240.0


def test_api_asset_upload_creates_asset_and_get_returns_metadata(monkeypatch, tmp_path):
    monkeypatch.delenv("PUZZLEOPS_API_TOKENS", raising=False)
    repo = PuzzleRepository(tmp_path / "asset_api.db")
    repo.upsert_user("ops_jp", display_name="日本运营", role="operator", countries=("日本",), status="active")
    repo.create_api_token("ops_jp", "jp-token", created_by="admin")
    client = TestClient(create_app(agent=ProductionFakeAgent(repo)))

    response = client.post(
        "/api/assets/upload",
        headers=headers(),
        data={"country": "日本"},
        files={"file": ("sushi.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["asset_id"].startswith("asset_")
    assert payload["public_url"].startswith("http://assets.local/")
    assert payload["sha256"]
    saved = repo.asset(payload["asset_id"])
    assert saved["source_filename"] == "sushi.png"
    assert repo.audit_logs(country="日本")[0]["action"] == "asset.upload"

    fetched = client.get(f"/api/assets/{payload['asset_id']}", headers=headers())
    assert fetched.status_code == 200
    assert fetched.json()["asset_id"] == payload["asset_id"]


def test_api_asset_upload_requires_operator_role(monkeypatch, tmp_path):
    monkeypatch.delenv("PUZZLEOPS_API_TOKENS", raising=False)
    repo = PuzzleRepository(tmp_path / "asset_api_forbidden.db")
    repo.upsert_user("viewer_jp", display_name="只读", role="viewer", countries=("日本",), status="active")
    repo.create_api_token("viewer_jp", "viewer-token", created_by="admin")
    client = TestClient(create_app(agent=ProductionFakeAgent(repo)))

    response = client.post(
        "/api/assets/upload",
        headers=headers("viewer-token"),
        data={"country": "日本"},
        files={"file": ("sushi.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 403


def test_rag_release_report_exports_markdown_and_json(tmp_path):
    repo = PuzzleRepository(tmp_path / "report.db")
    repo.record_trace_event(
        trace_id="trace-rag-ok",
        request_id="req",
        actor="system",
        country="日本",
        task_type="rag_search",
        provider="local",
        model="local",
        input_summary="猫咪鲤鱼",
        rag_citations=("jp_cat#c1",),
        output_summary="hit@5=1 mrr@5=1 ndcg@5=1 precision@5=0.2 recall@5=1",
        status="succeeded",
        latency_ms=10,
    )

    report = repo.export_rag_release_report(tmp_path / "rag_release")

    assert Path(report["json_path"]).exists()
    assert Path(report["markdown_path"]).exists()
    payload = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
    assert payload["metrics"]["citation_usable_rate"] == 1.0
    markdown = Path(report["markdown_path"]).read_text(encoding="utf-8")
    assert "MRR@5" in markdown
    assert "日本" in markdown


def test_worker_executes_known_job_and_records_trace(tmp_path):
    repo = PuzzleRepository(tmp_path / "worker.db")
    job = repo.create_job("rag_rebuild", country="日本", actor="admin", payload={"reason": "release_check"})

    result = execute_job_once(repo, job["job_id"])

    assert result["status"] == "succeeded"
    saved = repo.job(job["job_id"])
    assert saved["status"] == "succeeded"
    assert saved["progress"] == 100
    traces = repo.trace_events(country="日本", task_type="job.rag_rebuild")
    assert traces[0]["status"] == "succeeded"


def test_enqueue_job_uses_local_database_fallback_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("PUZZLEOPS_JOB_QUEUE_PROVIDER", raising=False)
    repo = PuzzleRepository(tmp_path / "queue_local.db")

    job = enqueue_job(repo, "vlm_parse", country="日本", actor="ops_jp", payload={"asset_id": "asset-1"})

    assert job["status"] == "queued"
    assert job["queue_provider"] == "local"
    assert job["enqueue_status"] == "local_fallback"
    assert repo.job(job["job_id"])["status"] == "queued"


def test_enqueue_job_dispatches_same_job_id_to_injected_queue(tmp_path):
    class FakeQueue:
        provider = "rq"

        def __init__(self):
            self.dispatched = []

        def enqueue(self, job_id):
            self.dispatched.append(job_id)
            return {"provider": self.provider, "enqueue_status": "enqueued", "rq_job_id": f"rq-{job_id}"}

    repo = PuzzleRepository(tmp_path / "queue_rq.db")
    queue = FakeQueue()

    job = enqueue_job(repo, "rag_rebuild", country="日本", actor="admin", payload={}, queue=queue)

    assert queue.dispatched == [job["job_id"]]
    assert job["queue_provider"] == "rq"
    assert job["enqueue_status"] == "enqueued"
    assert job["rq_job_id"] == f"rq-{job['job_id']}"


def test_repository_observability_summary_counts_latency_failures_and_rag_citations(tmp_path):
    repo = PuzzleRepository(tmp_path / "observability.db")
    first = repo.create_job("vlm_parse", country="日本", actor="ops_jp", payload={})
    repo.update_job(first["job_id"], status="succeeded", progress=100, result={"ok": True})
    second = repo.create_job("feishu_sync", country="日本", actor="ops_jp", payload={})
    repo.update_job(second["job_id"], status="failed", progress=0, error_code="FeishuFieldError", error_message="field missing")
    third = repo.create_job("rag_rebuild", country="法国", actor="ops_fr", payload={})
    repo.update_job(third["job_id"], status="queued", progress=0)
    repo.record_trace_event(
        trace_id="trace-rag-ok",
        request_id="req-1",
        actor="ops_jp",
        country="日本",
        task_type="rag_search",
        provider="dashscope",
        model="qwen3-rerank",
        rag_citations=("jp_value#c1",),
        status="succeeded",
        latency_ms=100,
    )
    repo.record_trace_event(
        trace_id="trace-rag-missing",
        request_id="req-2",
        actor="ops_jp",
        country="日本",
        task_type="rag_search",
        provider="dashscope",
        model="qwen3-rerank",
        rag_citations=(),
        status="failed",
        error_message="no citation",
        latency_ms=300,
    )

    summary = repo.observability_summary(country="日本")

    assert summary["jobs"]["total"] == 2
    assert summary["jobs"]["success_rate"] == 0.5
    assert summary["jobs"]["failure_reasons"]["FeishuFieldError"] == 1
    assert summary["traces"]["total"] == 2
    assert summary["traces"]["status_counts"]["failed"] == 1
    assert summary["latency"]["p50_ms"] == 100.0
    assert summary["latency"]["p95_ms"] == 300.0
    assert summary["rag"]["citation_missing_rate"] == 0.5
