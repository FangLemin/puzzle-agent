from pathlib import Path

from PIL import Image

from puzzle_ops.visual_similarity import (
    LocalVisualEmbeddingProvider,
    QwenVLImageEmbeddingProvider,
    VisualIndexRecord,
    VisualMilvusImageStore,
    VisualSimilarityIndex,
)


def test_local_visual_embedding_provider_is_deterministic_and_image_sensitive(tmp_path):
    red = tmp_path / "red.png"
    blue = tmp_path / "blue.png"
    Image.new("RGB", (32, 32), (220, 40, 40)).save(red)
    Image.new("RGB", (32, 32), (40, 80, 220)).save(blue)
    provider = LocalVisualEmbeddingProvider(dimension=8)

    first = provider.embed_image(str(red), text="寿司")
    second = provider.embed_image(str(red), text="寿司")
    other = provider.embed_image(str(blue), text="寿司")

    assert first.vector == second.vector
    assert first.vector != other.vector
    assert first.provider == "local-visual-hash"
    assert first.dimension == 8


def test_qwen_vl_image_embedding_provider_builds_multimodal_payload(tmp_path):
    image = tmp_path / "sushi.png"
    Image.new("RGB", (24, 24), (230, 190, 140)).save(image)
    captured = {}

    def fake_transport(payload, api_key, endpoint):
        captured["payload"] = payload
        captured["api_key"] = api_key
        captured["endpoint"] = endpoint
        return {"output": {"embeddings": [{"embedding": [0.1, 0.2, 0.3]}]}}

    provider = QwenVLImageEmbeddingProvider(
        api_key="dashscope-test",
        model="qwen3-vl-embedding",
        endpoint="https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding",
        transport=fake_transport,
    )

    embedding = provider.embed_image(str(image), text="主体=寿司；国家=日本")

    assert embedding.vector == (0.1, 0.2, 0.3)
    assert embedding.provider == "qwen-vl-embedding"
    assert captured["api_key"] == "dashscope-test"
    assert captured["payload"]["model"] == "qwen3-vl-embedding"
    contents = captured["payload"]["input"]["contents"]
    assert contents[0]["text"] == "主体=寿司；国家=日本"
    assert contents[1]["image"].startswith("data:image/png;base64,")


def test_visual_similarity_index_returns_same_country_nearest_good_and_risk(tmp_path):
    provider = LocalVisualEmbeddingProvider(dimension=12)
    red = tmp_path / "red.png"
    blue = tmp_path / "blue.png"
    Image.new("RGB", (32, 32), (230, 60, 40)).save(red)
    Image.new("RGB", (32, 32), (40, 70, 230)).save(blue)
    index = VisualSimilarityIndex()
    index.upsert(
        (
            VisualIndexRecord.from_image(
                image_id="jp-good",
                country="日本",
                grade="A",
                local_image_path=str(red),
                subject="寿司",
                operation_tag="常规_日本_寿司0701",
                embedding=provider.embed_image(str(red), text="寿司"),
            ),
            VisualIndexRecord.from_image(
                image_id="jp-risk",
                country="日本",
                grade="D",
                local_image_path=str(blue),
                subject="抹茶",
                operation_tag="常规_日本_抹茶0702",
                embedding=provider.embed_image(str(blue), text="抹茶"),
            ),
        )
    )

    result = index.grouped_search(provider.embed_image(str(red), text="寿司"), country="日本", top_k=5)

    assert result["similar_good"][0]["image_id"] == "jp-good"
    assert result["similar_good"][0]["grade"] == "A"
    assert result["similar_risk"][0]["image_id"] == "jp-risk"
    assert "图像向量相似" in result["similar_good"][0]["reason"]


def test_visual_milvus_image_store_uses_image_collection_payload():
    calls = []

    def fake_transport(method, endpoint, payload, api_key):
        calls.append((method, endpoint, payload, api_key))
        if endpoint.endswith("/collections/describe"):
            return {"code": 0, "data": None}
        if endpoint.endswith("/collections/create"):
            return {"code": 0, "data": {}}
        if endpoint.endswith("/collections/load"):
            return {"code": 0, "data": {}}
        if endpoint.endswith("/entities/insert"):
            return {"code": 0, "data": {"insertCount": 1}}
        if endpoint.endswith("/entities/search"):
            return {
                "code": 0,
                "data": [
                    [
                        {
                            "id": "jp-good",
                            "distance": 0.92,
                            "entity": {"image_id": "jp-good", "country": "日本", "grade": "A", "subject": "寿司", "operation_tag": "常规_日本_寿司0701"},
                        }
                    ]
                ],
            }
        return {"code": 0, "data": {}}

    store = VisualMilvusImageStore(
        endpoint="https://zilliz.example.com",
        token="token",
        collection="puzzleops_image_embeddings",
        transport=fake_transport,
    )
    ensure = store.ensure_collection(vector_size=3)
    upsert = store.upsert(
        (
            VisualIndexRecord(
                image_id="jp-good",
                country="日本",
                grade="A",
                local_image_path="/tmp/sushi.png",
                subject="寿司",
                operation_tag="常规_日本_寿司0701",
                vector=(0.1, 0.2, 0.3),
                provider="qwen-vl-embedding",
                model="qwen3-vl-embedding",
            ),
        )
    )
    hits = store.search((0.1, 0.2, 0.3), country="日本", top_k=1)

    assert ensure["status"] == "created"
    assert upsert["insert_count"] == 1
    assert hits[0]["image_id"] == "jp-good"
    assert any(call[2].get("collectionName") == "puzzleops_image_embeddings" for call in calls if isinstance(call[2], dict))

