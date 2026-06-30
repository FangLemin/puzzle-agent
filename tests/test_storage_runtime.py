from pathlib import Path
import sqlite3

from puzzle_ops.cache import CacheProvider, RedisCache
from puzzle_ops.excel_importer import import_history_workbook
from puzzle_ops.feishu import FeishuClientFactory, MockFeishuClient
from puzzle_ops.rag import RagDocument, chunk_document
from puzzle_ops.storage import PuzzleRepository


FIXTURE = Path("/Users/fanglemin/Desktop/数据示例.xlsx")


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
