from types import SimpleNamespace

from fastapi.testclient import TestClient

from puzzle_ops.api import create_app, parse_api_tokens


class FakeRagPrompt:
    citations = ("jp_value_001#c1",)
    context = "日本市场偏好季节感与治愈氛围。"
    prompt = "只根据 citation 回答。"


class FakeAgent:
    rag_provider_config = SimpleNamespace(
        embedding_provider="dashscope",
        embedding_model="text-embedding-v4",
        rerank_provider="dashscope",
        rerank_model="qwen3-rerank",
        configured=True,
        remote_calls_enabled=True,
    )
    rag_vector_store_config = SimpleNamespace(provider="milvus", collection="puzzle_ops_rag", ready=True)
    visual_embedding_provider = SimpleNamespace(model="qwen3-vl-embedding", remote_calls_enabled=True)
    feishu = SimpleNamespace(configured=True)

    def __init__(self):
        self._last_rag_rewritten_query = "日本市场 猫咪 鲤鱼 治愈 审核风险"
        self._last_rag_trace = {"final_hits": [{"chunk_id": "jp_value_001#c1", "score": 0.91}]}

    def value_audit_rag_answer(self, country, query, top_k=5, task_index="value_master"):
        self.rag_request = {"country": country, "query": query, "top_k": top_k, "task_index": task_index}
        return FakeRagPrompt()

    def rag_citation_details(self, country, citations):
        return ({"chunk_id": "jp_value_001#c1", "title": "日本市场价值观", "source_type": "value_rule", "text": "季节感与治愈。"},)

    def apply_value_master(self, row):
        return row.edited(
            subject_description="主体内容：猫咪与锦鲤；色彩氛围：柔和暖色；构图环境：日式庭院近景。",
            value_match="符合日本市场治愈价值观；citation=jp_value_001#c1；需人工复核。",
            remark="无明显 IP 风险。",
        )

    def similar_visual_history_for_candidate(self, candidate, top_k=5):
        return {"status": "low_confidence", "message": "暂无可靠历史相似图", "best_score": 0.11, "similar_good": (), "similar_risk": ()}

    def harness_summary(self, country):
        return {"真实样本数": 25 if country == "日本" else 20, "合成样本数": 139, "gold覆盖率": 1.0}

    def harness_baseline_summary(self, country):
        return {"最近运行": "local-preview", "失败样本数": 2}


def auth_headers(token="jp-token"):
    return {"Authorization": f"Bearer {token}"}


def test_parse_api_tokens_supports_role_and_country_scope():
    users = parse_api_tokens("ops_jp:jp-token:operator:日本,admin:admin-token:admin:日本|法国")

    assert users["jp-token"].user_id == "ops_jp"
    assert users["jp-token"].role == "operator"
    assert users["jp-token"].countries == ("日本",)
    assert users["admin-token"].countries == ("日本", "法国")


def test_health_requires_bearer_token(monkeypatch):
    monkeypatch.setenv("PUZZLEOPS_API_TOKENS", "ops_jp:jp-token:operator:日本")
    client = TestClient(create_app(agent=FakeAgent()))

    response = client.get("/api/health")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_openapi_schema_exposes_core_agent_routes(monkeypatch):
    monkeypatch.setenv("PUZZLEOPS_API_TOKENS", "ops_jp:jp-token:operator:日本")
    client = TestClient(create_app(agent=FakeAgent()))

    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/health" in paths
    assert "/api/rag/search" in paths
    assert "/api/value/analyze" in paths
    assert "/api/harness/summary" in paths
    assert "/api/assets/upload" in paths
    assert "/api/metrics/dashboard" in paths


def test_health_redacts_secrets_and_reports_version(monkeypatch):
    monkeypatch.setenv("PUZZLEOPS_API_TOKENS", "ops_jp:jp-token:operator:日本")
    monkeypatch.setenv("QWEN_API_KEY", "secret-key-that-must-not-leak")
    client = TestClient(create_app(agent=FakeAgent()))

    response = client.get("/api/health", headers=auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"]
    assert "secret-key-that-must-not-leak" not in str(payload)
    assert payload["providers"]["rag_embedding"]["model"] == "text-embedding-v4"
    assert payload["providers"]["asset_storage"]["configured"] is False


def test_rag_search_returns_citations_and_trace(monkeypatch):
    monkeypatch.setenv("PUZZLEOPS_API_TOKENS", "ops_jp:jp-token:operator:日本")
    client = TestClient(create_app(agent=FakeAgent()))

    response = client.post(
        "/api/rag/search",
        headers=auth_headers(),
        json={"country": "日本", "query": "猫咪鲤鱼是否符合日本市场", "top_k": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["country"] == "日本"
    assert payload["citations"][0]["citation_id"] == "jp_value_001#c1"
    assert payload["trace"]["reranked"] == 1


def test_value_analyze_requires_country_permission(monkeypatch):
    monkeypatch.setenv("PUZZLEOPS_API_TOKENS", "ops_jp:jp-token:operator:日本")
    client = TestClient(create_app(agent=FakeAgent()))

    response = client.post(
        "/api/value/analyze",
        headers=auth_headers(),
        json={"country": "法国", "subject": "薰衣草风车", "operation_tag": "试新_法国_薰衣草风车0804"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden_country"


def test_value_analyze_keeps_human_review_flag_and_three_part_parse(monkeypatch):
    monkeypatch.setenv("PUZZLEOPS_API_TOKENS", "ops_jp:jp-token:operator:日本")
    client = TestClient(create_app(agent=FakeAgent()))

    response = client.post(
        "/api/value/analyze",
        headers=auth_headers(),
        json={"country": "日本", "subject": "猫咪鲤鱼", "operation_tag": "试新_日本_猫咪鲤鱼0804", "js_category": "animal"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["requires_human_review"] is True
    assert payload["visual_parse"]["subject_content"].startswith("猫咪")
    assert "色彩氛围" not in payload["visual_parse"]["subject_content"]


def test_harness_summary_separates_real_and_synthetic_samples(monkeypatch):
    monkeypatch.setenv("PUZZLEOPS_API_TOKENS", "ops_jp:jp-token:operator:日本")
    client = TestClient(create_app(agent=FakeAgent()))

    response = client.get("/api/harness/summary?country=日本", headers=auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["dataset"]["real_samples"] == 25
    assert payload["dataset"]["synthetic_samples"] == 139
    assert payload["latest_run"]["failure_count"] == 2
