from pathlib import Path
import sqlite3

from puzzle_ops.cache import CacheProvider, RedisCache
from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.excel_importer import import_history_workbook
from puzzle_ops.feishu import FeishuClientFactory, MockFeishuClient
from puzzle_ops.rag import RagDocument, chunk_document
from puzzle_ops.storage import PuzzleRepository


FIXTURE = Path.home() / "Desktop" / "数据示例.xlsx"


def test_repository_persists_imported_history_records(tmp_path):
    records = import_history_workbook(FIXTURE, "日本", tmp_path / "images")
    repo = PuzzleRepository(tmp_path / "puzzle_ops.db")

    repo.save_history_records(records)
    loaded = repo.history_records(country="日本")

    assert len(loaded) == 25
    assert loaded[0].image_id == records[0].image_id
    assert loaded[0].local_image_path == records[0].local_image_path
    assert loaded[0].js_category == "animal"


def test_repository_stores_agent_memory_and_value_rules(tmp_path):
    repo = PuzzleRepository(tmp_path / "puzzle_ops.db")

    repo.add_memory("日本", "animal", "运营把猫咪鲤鱼补充了日式庭院场景")
    repo.add_value_rule("日本", "动物互动类图片需包含明确日式场景", status="approved")

    assert "日式庭院" in repo.memories("日本")[0]["content"]
    assert repo.approved_value_rules("日本")[0]["rule_text"] == "动物互动类图片需包含明确日式场景"


def test_repository_stores_layered_memory_payloads(tmp_path):
    repo = PuzzleRepository(tmp_path / "puzzle_ops.db")

    repo.add_layered_memory(
        "日本",
        "perception",
        "trial_image_parse",
        {"subject": "寿司", "color_mood": "米白与鲑鱼橙"},
    )
    repo.add_layered_memory(
        "日本",
        "facts",
        "image_semantic_fact",
        {"subject": "寿司", "value_labels": ["本土饮食文化"]},
    )

    perception = repo.layered_memories("日本", layer="perception")
    facts = repo.layered_memories("日本", layer="facts")

    assert perception[0]["memory_layer"] == "perception"
    assert perception[0]["payload"]["subject"] == "寿司"
    assert perception[0]["review_status"] == "draft"
    assert perception[0]["approved_for_rag"] is False
    assert facts[0]["payload"]["value_labels"] == ["本土饮食文化"]


def test_layered_memory_deduplicates_active_payload_and_tracks_status(tmp_path):
    repo = PuzzleRepository(tmp_path / "puzzle_ops.db")

    first_id = repo.add_layered_memory("日本", "perception", "vision_parse", {"subject": "寿司"})
    second_id = repo.add_layered_memory("日本", "perception", "vision_parse", {"subject": "寿司"})

    rows = repo.layered_memories("日本", layer="perception", include_inactive=True)
    assert first_id == second_id
    assert len(rows) == 1
    assert rows[0]["memory_id"] == first_id
    assert rows[0]["status"] == "active"
    assert rows[0]["fingerprint"]
    assert rows[0]["review_status"] == "draft"
    assert rows[0]["approved_for_rag"] is False


def test_layered_memory_review_and_audit_fields(tmp_path):
    repo = PuzzleRepository(tmp_path / "puzzle_ops.db")

    memory_id = repo.add_layered_memory(
        "日本",
        "facts",
        "image_fact",
        {"subject": "寿司"},
        created_by="jp_ops",
    )
    repo.review_layered_memory(memory_id, review_status="approved", approved_for_rag=True, actor="fr_ops")

    row = repo.layered_memories("日本", include_inactive=True)[0]
    assert row["created_by"] == "jp_ops"
    assert row["updated_by"] == "fr_ops"
    assert row["approved_by"] == "fr_ops"
    assert row["approved_at"]
    assert row["review_status"] == "approved"
    assert row["approved_for_rag"] is True

    audit = repo.memory_audit_events("日本")
    assert [event["action"] for event in audit] == ["create", "review"]
    assert audit[-1]["actor"] == "fr_ops"
    assert audit[-1]["memory_id"] == memory_id
    assert audit[-1]["new_review_status"] == "approved"
    assert audit[-1]["approved_for_rag"] is True


def test_repository_records_real_rag_hit_metrics_for_memory_chunks(tmp_path):
    repo = PuzzleRepository(tmp_path / "puzzle_ops.db")
    memory_id = repo.add_layered_memory(
        "日本",
        "facts",
        "image_fact",
        {"subject": "寿司", "rule": "寿司适合日本本土饮食文化"},
        review_status="approved",
        approved_for_rag=True,
    )

    repo.record_memory_rag_hits(
        "日本",
        ({"memory_id": memory_id, "chunk_id": "JP_FACT_001#chunk-1", "trace_id": "trace-1"},),
    )
    repo.record_memory_rag_hits(
        "日本",
        ({"memory_id": memory_id, "chunk_id": "JP_FACT_001#chunk-1", "trace_id": "trace-2"},),
    )

    row = repo.layered_memories("日本", include_inactive=True)[0]
    assert row["rag_hit_count"] == 2
    assert row["last_rag_hit_at"]
    audit = repo.memory_audit_events("日本", action="rag_hit")
    assert len(audit) == 2
    assert audit[-1]["metadata"]["chunk_id"] == "JP_FACT_001#chunk-1"


def test_layered_memory_retire_records_actor_and_blocks_rag(tmp_path):
    repo = PuzzleRepository(tmp_path / "puzzle_ops.db")
    memory_id = repo.add_layered_memory("日本", "facts", "image_fact", {"subject": "寿司"}, created_by="jp_ops")

    repo.retire_layered_memory(memory_id, actor="jp_ops")

    row = repo.layered_memories("日本", include_inactive=True)[0]
    assert row["status"] == "retired"
    assert row["review_status"] == "retired"
    assert row["approved_for_rag"] is False
    assert row["retired_by"] == "jp_ops"
    assert row["retired_at"]


def test_layered_memory_ttl_expires_and_leaves_active_rag_view(tmp_path):
    repo = PuzzleRepository(tmp_path / "puzzle_ops.db")
    memory_id = repo.add_layered_memory(
        "日本",
        "working",
        "task_state",
        {"status": "parsed"},
        ttl_seconds=-1,
    )

    assert repo.layered_memories("日本", layer="working") == ()
    archived = repo.layered_memories("日本", layer="working", include_inactive=True)
    assert archived[0]["memory_id"] == memory_id
    assert archived[0]["status"] == "expired"


def test_repository_promotes_memory_with_human_verified_provenance(tmp_path):
    repo = PuzzleRepository(tmp_path / "puzzle_ops.db")
    source_id = repo.add_layered_memory("日本", "perception", "vision_parse", {"subject": "寿司"})

    target_id = repo.promote_layered_memory(
        source_id,
        target_layer="facts",
        target_type="verified_image_fact",
        human_note="运营确认主体准确",
    )

    rows = repo.layered_memories("日本", include_inactive=True)
    source = next(row for row in rows if row["memory_id"] == source_id)
    target = next(row for row in rows if row["memory_id"] == target_id)
    assert source["status"] == "promoted"
    assert target["status"] == "active"
    assert target["source_memory_id"] == source_id
    assert target["human_verified"] is True
    assert target["payload"]["human_note"] == "运营确认主体准确"


def test_repository_migrates_legacy_layered_memory_schema_without_data_loss(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE layered_memory (
                memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
                country TEXT NOT NULL,
                memory_layer TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO layered_memory(country, memory_layer, memory_type, payload) VALUES (?, ?, ?, ?)",
            ("日本", "facts", "legacy_fact", '{"subject":"寿司"}'),
        )

    repo = PuzzleRepository(db_path)
    rows = repo.layered_memories("日本", include_inactive=True)

    assert len(rows) == 1
    assert rows[0]["payload"]["subject"] == "寿司"
    assert rows[0]["status"] == "active"
    assert rows[0]["fingerprint"]
    assert rows[0]["review_status"] == "draft"
    assert rows[0]["approved_for_rag"] is False


def test_repository_stores_parent_child_rag_index(tmp_path):
    repo = PuzzleRepository(tmp_path / "puzzle_ops.db")
    document = RagDocument(
        "JP_VALUE_001",
        "日本",
        "value_rule",
        "文化真实性",
        "寿司、抹茶、温泉街属于日本本土元素，需要避免文化混淆。",
        {"source": "static_value_rules"},
    )
    chunks = tuple(chunk_document(document, max_chars=40))

    repo.save_rag_index("日本", (document,), chunks)
    stored_documents = repo.rag_documents("日本")
    stored_chunks = repo.rag_chunks("日本")

    assert stored_documents[0]["document_id"] == "JP_VALUE_001"
    assert stored_documents[0]["metadata"]["source"] == "static_value_rules"
    assert stored_chunks[0]["parent_id"] == "JP_VALUE_001"
    assert stored_chunks[0]["chunk_id"] == "JP_VALUE_001#chunk-1"


def test_repository_persists_rag_embedding_cache(tmp_path):
    repo = PuzzleRepository(tmp_path / "puzzle_ops.db")

    repo.set_rag_embedding_cache("dashscope", "text-embedding-v3", "寿司属于日本饮食文化", (0.1, 0.2, 0.3))

    assert repo.get_rag_embedding_cache("dashscope", "text-embedding-v3", "寿司属于日本饮食文化") == (0.1, 0.2, 0.3)
    assert repo.get_rag_embedding_cache("dashscope", "text-embedding-v3", "不存在") is None


def test_agent_detects_conflicting_value_memories_for_same_subject(tmp_path):
    repo = PuzzleRepository(tmp_path / "puzzle_ops.db")
    agent = PuzzleOpsAgent(repository=repo)
    positive_id = agent.record_extracted_fact(
        "日本",
        "verified_value_match_fact",
        {
            "subject": "寿司",
            "operation_tag": "试新_日本_寿司拼盘0609",
            "human_correction": "寿司图符合日本本土饮食文化，适合继续试新。",
        },
    )
    negative_id = agent.record_working_memory(
        "日本",
        "value_match_human_correction",
        {
            "subject": "寿司",
            "operation_tag": "试新_日本_寿司拼盘0609",
            "human_correction": "寿司图不适合日本市场，餐具混乱且存在文化误用风险。",
        },
    )
    agent.record_working_memory(
        "日本",
        "neutral_note",
        {
            "subject": "樱花",
            "operation_tag": "试新_日本_樱花0609",
            "note": "运营记录：等待更多样本。",
        },
    )

    conflicts = agent.memory_conflicts("日本")

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["subject"] == "寿司"
    assert conflict["operation_tag"] == "试新_日本_寿司拼盘0609"
    assert set(conflict["memory_ids"]) == {positive_id, negative_id}
    assert conflict["stances"]["positive"] == [positive_id]
    assert conflict["stances"]["negative"] == [negative_id]


def test_memory_provenance_links_promotion_correction_and_rag_feedback(tmp_path):
    repo = PuzzleRepository(tmp_path / "puzzle_ops.db")
    agent = PuzzleOpsAgent(repository=repo)
    source_id = agent.record_perception_memory(
        "日本",
        "trial_image_parse",
        {
            "subject": "寿司",
            "operation_tag": "试新_日本_寿司拼盘0609",
            "observation": "主体为寿司拼盘，色彩为米白与鲑鱼橙。",
        },
    )
    fact_id = agent.promote_memory(source_id, target_layer="facts", human_note="运营确认主体准确")
    row = agent.create_trial_demand("日本", "人物", mode="parse").edited(
        subject="寿司",
        operation_tag="试新_日本_寿司拼盘0609",
        subject_description="主体内容：寿司拼盘；色彩氛围：米白与鲑鱼橙；构图环境：日式餐桌。",
        value_match="LLM判断：部分符合；系统RAG召回：JP_VALUE_001#chunk-1",
    )
    correction_ids = agent.record_value_match_human_correction(
        row,
        human_correction="运营确认：寿司拼盘符合日本饮食文化，但需避免文字水印风险。",
        satisfaction_score=4,
    )

    provenance = agent.memory_provenance("日本", fact_id)

    step_types = [step["step_type"] for step in provenance["steps"]]
    assert provenance["root_memory_id"] == fact_id
    assert "source" in step_types
    assert "current" in step_types
    assert "related_human_correction" in step_types
    assert "related_fact" in step_types
    assert "related_rag_feedback" in step_types
    assert source_id in [step["memory_id"] for step in provenance["steps"]]
    assert correction_ids["working_memory_id"] in [step["memory_id"] for step in provenance["steps"]]
    assert correction_ids["rag_feedback_memory_id"] in [step["memory_id"] for step in provenance["steps"]]


def test_cache_provider_uses_in_memory_fallback_when_redis_unavailable():
    cache = RedisCache.from_url("redis://127.0.0.1:1/0")

    assert isinstance(cache, CacheProvider)
    cache.set("hot_tags:日本", ["常规_日本_猫咪鲤鱼0605"])
    assert cache.get("hot_tags:日本") == ["常规_日本_猫咪鲤鱼0605"]


def test_feishu_factory_falls_back_to_mock_without_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_SPREADSHEET_TOKEN", raising=False)

    client = FeishuClientFactory.create(export_dir=tmp_path)
    result = client.write_table("提需表", [{"运营tag": "常规_日本_猫咪鲤鱼0605"}])

    assert isinstance(client, MockFeishuClient)
    assert result.success
    assert Path(result.data["path"]).exists()


def test_description_benchmark_scores_are_persisted_and_summarized(tmp_path):
    repo = PuzzleRepository(tmp_path / "puzzle.db")

    record_id = repo.add_description_benchmark_score(
        {
            "country": "日本",
            "actor": "jp_ops",
            "image_name": "猫咪鲤鱼",
            "operation_tag": "常规_日本_猫咪鲤鱼0605",
            "template_scores": {"subject_accuracy": 4, "production_actionability": 2, "conciseness": 1, "market_fit": 3, "remark_usefulness": 1},
            "prompt_scores": {"subject_accuracy": 4, "production_actionability": 3, "conciseness": 4, "market_fit": 3, "remark_usefulness": 4},
            "template_label": "需要大改",
            "prompt_label": "轻微修改",
            "template_output": "主体内容：猫咪鲤鱼；色彩氛围：浅粉；构图环境：庭院。",
            "prompt_output": "主体内容：猫咪与锦鲤池；色彩氛围：浅粉、湖蓝、明亮治愈；构图环境：日式庭院近景。",
        }
    )

    rows = repo.description_benchmark_scores("日本")
    summary = repo.description_benchmark_summary("日本")

    assert record_id > 0
    assert len(rows) == 1
    assert rows[0]["operation_tag"] == "常规_日本_猫咪鲤鱼0605"
    assert rows[0]["prompt_label"] == "轻微修改"
    assert summary["count"] == 1
    assert summary["template_average"] == 2.2
    assert summary["prompt_average"] == 3.6
    assert summary["prompt_light_or_direct_rate"] == 1.0
