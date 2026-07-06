import json
from zipfile import ZipFile

from puzzle_ops.rag import (
    BGERerankProvider,
    DashScopeEmbeddingProvider,
    DashScopeRerankProvider,
    FeedbackAwareRerankProvider,
    FileDocumentLoaderAdapter,
    HybridRagRetriever,
    LocalEmbeddingProvider,
    LocalRerankProvider,
    MilvusVectorStore,
    MilvusVectorStoreRetriever,
    RagChunkingConfig,
    RagDocument,
    RagIndexArtifacts,
    RagProviderConfig,
    QdrantVectorStore,
    QdrantVectorStoreRetriever,
    RagRetrievalCase,
    RagRuntimeStats,
    RagVectorStoreConfig,
    RetrievalCaseLoaderAdapter,
    StaticDocumentLoaderAdapter,
    build_rag_prompt,
    build_processed_documents_from_raw,
    export_offline_rag_index,
    export_rag_acceptance_report,
    chunk_document,
    evaluate_retrieval_report,
    evaluate_retrieval_hit_rate,
    load_rag_documents_jsonl,
    load_retrieval_cases_jsonl,
    prepare_qdrant_points,
    providers_from_config,
    rewrite_rag_query,
)


def test_chunk_document_keeps_parent_child_and_semantic_overlap():
    document = RagDocument(
        document_id="JP_VALUE_001",
        country="日本",
        source_type="value_rule",
        title="文化真实性",
        text="优先日本本土元素，避免中日韩文化混淆。寿司、抹茶、温泉街等元素需要语境真实。主体需要清晰，适合拼图识别。",
        metadata={"source": "static_value_rules"},
    )

    chunks = chunk_document(document, max_chars=34, overlap_sentences=1)

    assert len(chunks) >= 2
    assert all(chunk.parent_id == "JP_VALUE_001" for chunk in chunks)
    assert chunks[0].chunk_id == "JP_VALUE_001#chunk-1"
    assert "避免中日韩文化混淆" in chunks[0].text
    assert "避免中日韩文化混淆" in chunks[1].text


def test_static_document_loader_marks_loader_boundary_for_offline_indexing():
    document = RagDocument("JP_VALUE_001", "日本", "value_rule", "文化真实性", "寿司属于日本本土饮食文化。", {})

    loaded = StaticDocumentLoaderAdapter((document,)).load()

    assert loaded == (document,)


def test_file_document_loader_reads_versioned_jsonl_documents(tmp_path):
    source = tmp_path / "knowledge" / "processed" / "value_audit_documents.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "document_id": "JP_KB_SUSHI",
                        "country": "日本",
                        "source_type": "value_rule",
                        "title": "日本饮食文化",
                        "text": "寿司、抹茶、和果子属于日本本土饮食文化。",
                        "metadata": {"knowledge_version": "2026-07-01", "source_file": "japan_values.md"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "document_id": "GLOBAL_AUDIT_IP",
                        "country": "GLOBAL",
                        "source_type": "audit_policy",
                        "title": "版权与文字风险",
                        "text": "避免文字水印、商标、热门IP角色和知名工作室点名风格。",
                    },
                    ensure_ascii=False,
                ),
            )
        ),
        encoding="utf-8",
    )

    documents = FileDocumentLoaderAdapter((source,)).load()

    assert documents == load_rag_documents_jsonl(source)
    assert documents[0].document_id == "JP_KB_SUSHI"
    assert documents[0].metadata["knowledge_version"] == "2026-07-01"
    assert documents[1].metadata["source_file"] == str(source)


def test_retrieval_case_loader_reads_jsonl_business_cases(tmp_path):
    source = tmp_path / "knowledge" / "eval" / "value_audit_cases.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "query": "日本寿司图是否符合本土饮食价值观",
                        "country": "日本",
                        "expected_parent_id": "JP_KB_SUSHI",
                        "tags": ["value", "japan"],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "query": "法国薰衣草风车石屋是否符合生活艺术",
                        "country": "法国",
                        "expected_parent_id": "FR_KB_LAVENDER",
                    },
                    ensure_ascii=False,
                ),
            )
        ),
        encoding="utf-8",
    )

    cases = RetrievalCaseLoaderAdapter(source).load()

    assert cases == load_retrieval_cases_jsonl(source)
    assert cases[0].query.startswith("日本寿司")
    assert cases[0].expected_parent_id == "JP_KB_SUSHI"


def test_build_processed_documents_from_raw_markdown_sections(tmp_path):
    raw_dir = tmp_path / "knowledge" / "raw"
    processed_path = tmp_path / "knowledge" / "processed" / "value_audit_documents.jsonl"
    raw_dir.mkdir(parents=True)
    (raw_dir / "japan_values.md").write_text(
        """---
country: 日本
source_type: value_rule
knowledge_version: unit-test
---
# 日本价值观

## 本土饮食文化 {#JP_KB_SUSHI_FOOD}
寿司、抹茶、和果子属于日本本土饮食文化。

## 治愈旅行
温泉街、浴衣、灯笼和樱花强调治愈旅行与季节感。
""",
        encoding="utf-8",
    )
    (raw_dir / "audit.md").write_text(
        """---
country: GLOBAL
source_type: audit_policy
knowledge_version: unit-test
---
# 审核规则

## 版权与文字风险
避免文字水印、商标、热门IP角色。
""",
        encoding="utf-8",
    )

    documents = build_processed_documents_from_raw(raw_dir, processed_path)

    assert processed_path.exists()
    assert [document.document_id for document in documents] == [
        "JP_KB_SUSHI_FOOD",
        "RAW_JAPAN_VALUES_治愈旅行",
        "RAW_AUDIT_版权与文字风险",
    ]
    assert documents[0].country == "日本"
    assert documents[0].source_type == "value_rule"
    assert documents[0].metadata["source_file"].endswith("japan_values.md")
    loaded = load_rag_documents_jsonl(processed_path)
    assert loaded == documents


def test_build_processed_documents_from_raw_docx_paragraphs(tmp_path):
    raw_dir = tmp_path / "knowledge" / "raw"
    processed_path = tmp_path / "knowledge" / "processed" / "value_audit_documents.jsonl"
    raw_dir.mkdir(parents=True)
    docx = raw_dir / "audit_rules.docx"
    _write_minimal_docx(
        docx,
        (
            "country: GLOBAL",
            "source_type: audit_policy",
            "knowledge_version: unit-test",
            "# 审核规则",
            "## 版权风险 {#GLOBAL_KB_AUDIT_IP_TEXT}",
            "避免文字水印、商标和热门IP角色。",
            "## AI质量风险 {#GLOBAL_KB_AUDIT_QUALITY}",
            "检查畸形肢体、文字乱码和透视错误。",
        ),
    )

    documents = build_processed_documents_from_raw(raw_dir, processed_path)

    assert [document.document_id for document in documents] == [
        "GLOBAL_KB_AUDIT_IP_TEXT",
        "GLOBAL_KB_AUDIT_QUALITY",
    ]
    assert documents[0].country == "GLOBAL"
    assert documents[0].source_type == "audit_policy"
    assert documents[0].metadata["source_file"].endswith("audit_rules.docx")


def _write_minimal_docx(path, paragraphs):
    body = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>"
        for paragraph in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)


def test_token_chunk_document_uses_sentence_boundaries_and_overlap_metadata():
    document = RagDocument(
        document_id="JP_LONG_VALUE",
        country="日本",
        source_type="value_rule",
        title="日本价值观长文",
        text=(
            "寿司和抹茶属于日本本土饮食文化，需要保持生活化语境。"
            "温泉街和浴衣人物强调治愈感、季节感和旅行动机。"
            "画面应避免中日韩文化混淆、文字水印、热门IP角色和知名工作室风格。"
            "拼图提需还需要主体清晰、色彩层次明确、构图环境适合反复观察。"
        ),
        metadata={},
    )

    chunks = chunk_document(
        document,
        max_chars=None,
        chunking=RagChunkingConfig(chunk_size_tokens=36, chunk_overlap_tokens=10),
    )

    assert len(chunks) >= 2
    assert all(chunk.parent_id == "JP_LONG_VALUE" for chunk in chunks)
    assert chunks[0].metadata["splitter"] == "sentence_token"
    assert chunks[0].metadata["chunk_size_tokens"] == 36
    assert chunks[1].metadata["chunk_overlap_tokens"] == 10
    assert "温泉街" in chunks[0].text
    assert "温泉街" in chunks[1].text


def test_hybrid_retriever_uses_bm25_vector_and_rerank_for_value_and_audit_hits():
    documents = (
        RagDocument("JP_VALUE_001", "日本", "value_rule", "文化真实性", "寿司、抹茶、温泉街属于日本本土元素，适合做饮食文化和治愈场景。", {}),
        RagDocument("FR_VALUE_001", "法国", "value_rule", "生活艺术", "薰衣草、法式窗台和石屋花园代表法国生活艺术。", {}),
        RagDocument("AUDIT_001", "GLOBAL", "audit_policy", "版权与文字风险", "避免商标、文字水印、热门 IP 角色和知名动画工作室风格。", {}),
    )
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document, max_chars=80))
    retriever = HybridRagRetriever(chunks)

    hits = retriever.search("日本寿司图片是否符合价值观，同时检查 IP 和文字水印风险", country="日本", top_k=3)

    assert hits[0].chunk.country == "日本"
    assert hits[0].rerank_score >= hits[-1].rerank_score
    assert any(hit.chunk.source_type == "value_rule" for hit in hits)
    assert any(hit.chunk.source_type == "audit_policy" for hit in hits)
    assert all(hit.bm25_score > 0 or hit.vector_score > 0 for hit in hits)


def test_build_rag_prompt_keeps_citations_for_grounded_llm_answer():
    document = RagDocument("JP_VALUE_001", "日本", "value_rule", "文化真实性", "寿司属于日本本土饮食文化，需要语境真实。", {})
    hits = HybridRagRetriever(tuple(chunk_document(document))).search("寿司是否符合日本价值观", country="日本")

    prompt = build_rag_prompt("寿司是否符合日本价值观", hits)

    assert "[JP_VALUE_001#chunk-1]" in prompt.context
    assert "引用依据" in prompt.prompt
    assert "只基于引用依据回答" in prompt.prompt
    assert "不知道" in prompt.prompt
    assert prompt.citations == ("JP_VALUE_001#chunk-1",)


def test_rewrite_rag_query_adds_domain_terms_without_losing_user_query():
    rewritten = rewrite_rag_query("这张寿司图适合日本吗", country="日本")

    assert rewritten.startswith("这张寿司图适合日本吗")
    assert "价值观" in rewritten
    assert "审核" in rewritten
    assert "文化混淆" in rewritten


def test_rag_provider_config_reports_local_fallback_without_external_keys(monkeypatch):
    monkeypatch.delenv("RAG_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("RAG_RERANK_PROVIDER", raising=False)

    config = RagProviderConfig.from_env(load_env=False)

    assert config.embedding_provider == "local"
    assert config.rerank_provider == "local"
    assert config.configured is False
    assert "本地" in config.status_text


def test_dashscope_config_defaults_to_real_embedding_and_rerank_models(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test")
    monkeypatch.setenv("RAG_ENABLE_REMOTE_CALLS", "true")
    monkeypatch.delenv("RAG_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("RAG_RERANK_MODEL", raising=False)

    config = RagProviderConfig.from_env(load_env=False)

    assert config.embedding_model == "text-embedding-v4"
    assert config.rerank_model == "qwen3-rerank"
    assert config.remote_calls_enabled is True


def test_dashscope_config_reuses_qwen_api_key_for_qwen3_embedding(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "bge")
    monkeypatch.setenv("QWEN_API_KEY", "qwen-test")
    monkeypatch.setenv("RAG_API_KEY", "")
    monkeypatch.setenv("BGE_RERANK_ENDPOINT", "http://127.0.0.1:9997/v1/rerank")
    monkeypatch.setenv("RAG_ENABLE_REMOTE_CALLS", "true")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("RAG_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("RAG_RERANK_MODEL", raising=False)

    config = RagProviderConfig.from_env(load_env=False)

    assert config.api_key == "qwen-test"
    assert config.embedding_model == "text-embedding-v4"
    assert config.rerank_model == "BAAI/bge-reranker-v2-m3"
    assert config.remote_calls_enabled is True
    assert "Qwen3-Embedding" in config.status_text


def test_qdrant_vector_store_config_reports_ready_endpoint(monkeypatch):
    monkeypatch.setenv("RAG_VECTOR_STORE_PROVIDER", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:6333")
    monkeypatch.setenv("QDRANT_COLLECTION", "puzzle_ops_rag")

    config = RagVectorStoreConfig.from_env(load_env=False)

    assert config.provider == "qdrant"
    assert config.collection == "puzzle_ops_rag"
    assert config.configured is True
    assert config.ready is True
    assert "Qdrant" in config.status_text


def test_milvus_vector_store_config_reports_ready_uri(monkeypatch):
    monkeypatch.setenv("RAG_VECTOR_STORE_PROVIDER", "milvus")
    monkeypatch.setenv("MILVUS_URI", "http://127.0.0.1:19530")
    monkeypatch.setenv("MILVUS_COLLECTION", "puzzle_ops_rag")
    monkeypatch.setenv("MILVUS_TOKEN", "milvus-token")

    config = RagVectorStoreConfig.from_env(load_env=False)

    assert config.provider == "milvus"
    assert config.endpoint == "http://127.0.0.1:19530"
    assert config.collection == "puzzle_ops_rag"
    assert config.api_key == "milvus-token"
    assert config.configured is True
    assert config.ready is True
    assert "Milvus" in config.status_text


def test_rag_provider_config_rejects_qwen3_vl_for_embedding_and_rerank(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "qwen3-vl-flash")
    monkeypatch.setenv("RAG_RERANK_MODEL", "qwen3-vl-flash")
    monkeypatch.setenv("QWEN_API_KEY", "qwen-test")
    monkeypatch.setenv("RAG_ENABLE_REMOTE_CALLS", "true")

    config = RagProviderConfig.from_env(load_env=False)

    assert config.configured is True
    assert config.remote_ready is False
    assert config.remote_calls_enabled is False
    assert "qwen3-vl" in config.status_text
    assert "视觉理解模型" in config.status_text


def test_bge_rerank_provider_requires_endpoint_for_remote_calls(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "bge")
    monkeypatch.setenv("RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test")
    monkeypatch.setenv("RAG_ENABLE_REMOTE_CALLS", "true")
    monkeypatch.delenv("BGE_RERANK_ENDPOINT", raising=False)
    monkeypatch.delenv("RAG_RERANK_ENDPOINT", raising=False)

    config = RagProviderConfig.from_env(load_env=False)

    assert config.rerank_provider == "bge"
    assert config.remote_ready is False
    assert config.remote_calls_enabled is False
    assert "BGE_RERANK_ENDPOINT" in config.status_text


def test_hybrid_retriever_accepts_pluggable_embedding_and_rerank_providers():
    class KeywordEmbeddingProvider(LocalEmbeddingProvider):
        provider_name = "fake-embedding"

        def similarity(self, query: str, text: str) -> float:
            return 0.99 if "温泉街" in query and "温泉街" in text else 0.01

    class KeywordRerankProvider(LocalRerankProvider):
        provider_name = "fake-reranker"

        def rerank(self, query: str, country: str, chunk, bm25_score: float, vector_score: float) -> float:
            return 9.9 if "温泉街" in chunk.text else 0.1

    documents = (
        RagDocument("JP_VALUE_001", "日本", "value_rule", "文化真实性", "温泉街和浴衣人物适合日本治愈旅行场景。", {}),
        RagDocument("JP_VALUE_002", "日本", "value_rule", "主体清晰", "寿司需要主体清晰，餐盘层次明确。", {}),
    )
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document, max_chars=80))
    retriever = HybridRagRetriever(chunks, embedding_provider=KeywordEmbeddingProvider(), rerank_provider=KeywordRerankProvider())

    hits = retriever.search("日本温泉街提需是否符合价值观", country="日本", top_k=2)

    assert hits[0].chunk.chunk_id == "JP_VALUE_001#chunk-1"
    assert hits[0].vector_score == 0.99
    assert hits[0].rerank_score == 9.9
    assert "fake-embedding" in hits[0].reason
    assert "fake-reranker" in hits[0].reason


def test_hybrid_retriever_merges_bm25_and_vector_candidate_pools_before_rerank():
    class VectorOnlyProvider(LocalEmbeddingProvider):
        provider_name = "vector-only"

        def similarities(self, query: str, texts: tuple[str, ...]) -> tuple[float, ...]:
            return tuple(0.99 if "生活艺术" in text else 0.0 for text in texts)

    documents = (
        RagDocument("JP_KEYWORD", "日本", "value_rule", "饮食文化", "寿司属于日本本土饮食文化。", {}),
        RagDocument("JP_VECTOR", "日本", "value_rule", "生活艺术", "海边野餐强调生活艺术与松弛感。", {}),
        RagDocument("JP_OTHER", "日本", "value_rule", "无关", "夜景城市霓虹适合另一类拼图。", {}),
    )
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document, max_chars=80))
    retriever = HybridRagRetriever(chunks, embedding_provider=VectorOnlyProvider())

    hits = retriever.search("日本寿司是否符合价值观", country="日本", top_k=2, bm25_top_k=1, vector_top_k=1)

    assert {hit.chunk.parent_id for hit in hits} == {"JP_KEYWORD", "JP_VECTOR"}


def test_rag_retrieval_hit_at_five_can_validate_business_gold_cases():
    documents = (
        RagDocument("JP_SUSHI", "日本", "value_rule", "日本饮食", "寿司、抹茶、和果子属于日本本土饮食文化。", {}),
        RagDocument("JP_ONSEN", "日本", "value_rule", "日本治愈", "温泉街、浴衣、旅馆灯笼适合日本治愈旅行场景。", {}),
        RagDocument("JP_AUDIT", "GLOBAL", "audit_policy", "审核风险", "避免文字水印、热门IP角色、商标和中日韩文化混淆。", {}),
        RagDocument("FR_LAVENDER", "法国", "value_rule", "法国自然", "薰衣草、石屋、风车体现法国乡村生活艺术。", {}),
        RagDocument("FR_PICNIC", "法国", "value_rule", "法国生活艺术", "海滩野餐、面包、奶酪和玻璃杯体现法国生活艺术。", {}),
    )
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document, max_chars=80))
    retriever = HybridRagRetriever(chunks)
    cases = (
        RagRetrievalCase("日本寿司图是否符合本土饮食价值观", "日本", "JP_SUSHI"),
        RagRetrievalCase("温泉街浴衣人物适合日本治愈旅行吗", "日本", "JP_ONSEN"),
        RagRetrievalCase("试新图有文字水印和IP角色风险吗", "日本", "JP_AUDIT"),
        RagRetrievalCase("薰衣草风车石屋适合法国吗", "法国", "FR_LAVENDER"),
        RagRetrievalCase("法国海滩野餐面包奶酪是否符合生活艺术", "法国", "FR_PICNIC"),
    )

    result = evaluate_retrieval_hit_rate(retriever, cases, k=5)

    assert result["hit@5"] >= 0.8
    assert result["hits"] == 5
    assert result["total"] == 5


def test_export_offline_rag_index_writes_manifest_documents_and_chunks_jsonl(tmp_path):
    documents = (
        RagDocument(
            "JP_VALUE_001",
            "日本",
            "value_rule",
            "饮食文化",
            "寿司、抹茶、和果子属于日本本土饮食文化。需要保持生活化餐桌语境。",
            {"source_file": "japan_values.md", "version": "2026-07-01"},
        ),
        RagDocument(
            "AUDIT_001",
            "GLOBAL",
            "audit_policy",
            "版权风险",
            "避免文字水印、商标、热门IP角色和知名工作室点名风格。",
            {"source_file": "audit.md", "version": "2026-07-01"},
        ),
    )

    artifacts = export_offline_rag_index(
        documents,
        tmp_path,
        country="日本",
        chunking=RagChunkingConfig(chunk_size_tokens=20, chunk_overlap_tokens=6),
        vector_store=RagVectorStoreConfig(provider="qdrant", endpoint="http://127.0.0.1:6333", collection="puzzle_ops_rag", configured=True, ready=True),
    )

    assert isinstance(artifacts, RagIndexArtifacts)
    assert artifacts.manifest_path.exists()
    assert artifacts.documents_path.exists()
    assert artifacts.chunks_path.exists()
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    assert manifest["country"] == "日本"
    assert manifest["document_count"] == 2
    assert manifest["chunk_count"] >= 2
    assert manifest["chunking"]["chunk_size_tokens"] == 20
    assert manifest["vector_store"]["provider"] == "qdrant"
    assert manifest["parent_child"]["JP_VALUE_001"]
    chunk_line = json.loads(artifacts.chunks_path.read_text(encoding="utf-8").splitlines()[0])
    assert {"chunk_id", "parent_id", "text", "metadata"} <= set(chunk_line)


def test_hybrid_retriever_search_trace_exposes_multiroute_candidates_and_final_hits():
    documents = (
        RagDocument("JP_SUSHI", "日本", "value_rule", "日本饮食", "寿司、抹茶、和果子属于日本本土饮食文化。", {}),
        RagDocument("JP_AUDIT", "GLOBAL", "audit_policy", "审核风险", "避免文字水印、热门IP角色、商标和中日韩文化混淆。", {}),
        RagDocument("FR_PICNIC", "法国", "value_rule", "法国生活艺术", "海滩野餐、面包和奶酪体现法国生活艺术。", {}),
    )
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document, max_chars=80))
    retriever = HybridRagRetriever(chunks)

    trace = retriever.search_with_trace(
        rewrite_rag_query("日本寿司图是否符合价值观并检查水印风险", country="日本"),
        country="日本",
        top_k=2,
        bm25_top_k=2,
        vector_top_k=2,
    )

    assert trace.query
    assert trace.country == "日本"
    assert trace.eligible_chunk_count == 2
    assert trace.bm25_top_k == 2
    assert trace.vector_top_k == 2
    assert trace.merged_candidate_count >= 2
    assert trace.rerank_provider == "local-rule-rerank"
    assert len(trace.final_hits) == 2
    assert trace.final_hits[0].rerank_score >= trace.final_hits[-1].rerank_score
    assert trace.as_dict()["final_hits"][0]["chunk_id"]


def test_evaluate_retrieval_report_includes_hit_mrr_and_threshold_status():
    documents = (
        RagDocument("JP_SUSHI", "日本", "value_rule", "日本饮食", "寿司、抹茶、和果子属于日本本土饮食文化。", {}),
        RagDocument("FR_LAVENDER", "法国", "value_rule", "法国自然", "薰衣草、石屋、风车体现法国乡村生活艺术。", {}),
    )
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document, max_chars=80))
    retriever = HybridRagRetriever(chunks)
    cases = (
        RagRetrievalCase("日本寿司图是否符合本土饮食价值观", "日本", "JP_SUSHI"),
        RagRetrievalCase("薰衣草风车石屋适合法国吗", "法国", "FR_LAVENDER"),
    )

    report = evaluate_retrieval_report(
        retriever,
        cases,
        k=5,
        threshold=0.8,
        dataset_name="value_audit_smoke",
        knowledge_version="rag-v1",
    )

    assert report["dataset_name"] == "value_audit_smoke"
    assert report["knowledge_version"] == "rag-v1"
    assert report["hit@5"] == 1.0
    assert report["mrr@5"] == 1.0
    assert report["passed_threshold"] is True
    assert report["cases"][0]["rank"] == 1


def test_evaluate_retrieval_report_diagnoses_failed_business_sample_routes():
    documents = (
        RagDocument("FR_BREAD", "法国", "value_rule", "法国饮食", "法棍和奶酪体现法国生活艺术。", {}),
        RagDocument("FR_LAVENDER", "法国", "value_rule", "法国自然", "薰衣草田和风车体现法国乡村价值观。", {}),
    )
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document, max_chars=80))
    retriever = HybridRagRetriever(chunks)

    report = evaluate_retrieval_report(
        retriever,
        (RagRetrievalCase("法国海边野餐生活艺术", "法国", "FR_PICNIC"),),
        k=1,
        threshold=0.8,
        dataset_name="真实 human_gold 业务样本 RAG gate",
    )

    case = report["cases"][0]
    assert case["hit"] is False
    assert case["diagnosis"] in {"knowledge_missing_or_query_mismatch", "candidate_recall_missing", "rerank_filtered_expected"}
    assert case["suggested_action"]
    assert "expected parent 未进入 top1" in case["failure_reason"]


def test_export_rag_acceptance_report_writes_hit_at_five_models_routes_and_traces(tmp_path):
    documents = (
        RagDocument("JP_SUSHI", "日本", "value_rule", "日本饮食", "寿司、抹茶、和果子属于日本本土饮食文化。", {}),
        RagDocument("JP_AUDIT", "GLOBAL", "audit_policy", "审核风险", "避免文字水印、热门IP角色、商标和中日韩文化混淆。", {}),
        RagDocument("FR_LAVENDER", "法国", "value_rule", "法国自然", "薰衣草、石屋、风车体现法国乡村生活艺术。", {}),
    )
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document, max_chars=80))
    retriever = HybridRagRetriever(chunks)
    cases = (
        RagRetrievalCase("日本寿司图是否符合本土饮食价值观", "日本", "JP_SUSHI"),
        RagRetrievalCase("文字水印和IP角色风险怎么审核", "日本", "JP_AUDIT"),
        RagRetrievalCase("法国薰衣草风车是否符合价值观", "法国", "FR_LAVENDER"),
    )
    provider_config = RagProviderConfig(
        embedding_provider="dashscope",
        embedding_model="text-embedding-v4",
        rerank_provider="bge",
        rerank_model="BAAI/bge-reranker-v2-m3",
        configured=True,
        remote_ready=True,
        remote_calls_enabled=True,
    )
    vector_store = RagVectorStoreConfig(
        provider="qdrant",
        endpoint="http://127.0.0.1:6333",
        collection="puzzle_ops_rag",
        configured=True,
        ready=True,
    )

    report = export_rag_acceptance_report(
        retriever,
        cases,
        tmp_path / "rag_acceptance.json",
        k=5,
        threshold=0.8,
        dataset_name="jp_fr_value_audit_gold",
        knowledge_version="rag-v0.4.8",
        provider_config=provider_config,
        vector_store=vector_store,
    )

    saved = json.loads((tmp_path / "rag_acceptance.json").read_text(encoding="utf-8"))
    assert report["hit@5"] >= 0.8
    assert saved["passed_threshold"] is True
    assert saved["embedding"]["model"] == "text-embedding-v4"
    assert saved["embedding"]["model_family"] == "Qwen3-Embedding"
    assert saved["rerank"]["provider"] == "bge"
    assert saved["vector_store"]["provider"] == "qdrant"
    assert saved["retrieval_routes"]["bm25"] is True
    assert saved["retrieval_routes"]["vector"] is True
    assert saved["retrieval_routes"]["rerank"] is True
    assert saved["trace_samples"][0]["final_hits"]


def test_export_rag_acceptance_report_records_observed_runtime_routes_and_stats(tmp_path):
    class QueryVectorEmbedding(LocalEmbeddingProvider):
        provider_name = "dashscope:text-embedding-v4"

        def __init__(self):
            self.stats = RagRuntimeStats()

        def similarities(self, query: str, texts: tuple[str, ...]) -> tuple[float, ...]:
            self.stats.embedding_remote_calls += 1
            return tuple(0.7 if "寿司" in text else 0.1 for text in texts)

        def query_vector(self, query: str) -> tuple[float, ...]:
            self.stats.embedding_remote_calls += 1
            return (1.0, 0.0)

    class FakeQdrantStore:
        provider_name = "qdrant"

        def search(self, query_vector, *, country, top_k):
            return {"JP_SUSHI#chunk-1": 0.99}

    def fake_rerank_transport(query, documents, api_key, endpoint, model):
        return {"results": [{"index": index, "relevance_score": 0.95 - index * 0.01} for index, _ in enumerate(documents)]}

    documents = (
        RagDocument("JP_SUSHI", "日本", "value_rule", "日本饮食", "寿司、抹茶、和果子属于日本本土饮食文化。", {}),
        RagDocument("JP_AUDIT", "GLOBAL", "audit_policy", "审核风险", "避免文字水印、热门IP角色、商标和中日韩文化混淆。", {}),
    )
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document, max_chars=80))
    embedding = QueryVectorEmbedding()
    rerank = BGERerankProvider(
        api_key="",
        model="BAAI/bge-reranker-v2-m3",
        endpoint="http://127.0.0.1:9997/v1/rerank",
        transport=fake_rerank_transport,
        stats=RagRuntimeStats(),
    )
    retriever = HybridRagRetriever(
        chunks,
        embedding_provider=embedding,
        rerank_provider=rerank,
        vector_store_retriever=QdrantVectorStoreRetriever(FakeQdrantStore()),
    )

    saved = export_rag_acceptance_report(
        retriever,
        (RagRetrievalCase("日本寿司价值观", "日本", "JP_SUSHI"),),
        tmp_path / "rag_acceptance_observed.json",
        provider_config=RagProviderConfig(
            embedding_provider="dashscope",
            embedding_model="text-embedding-v4",
            rerank_provider="bge",
            rerank_model="BAAI/bge-reranker-v2-m3",
            configured=True,
            remote_ready=True,
            remote_calls_enabled=True,
        ),
        vector_store=RagVectorStoreConfig(provider="qdrant", endpoint="http://127.0.0.1:6333", collection="puzzle_ops_rag", configured=True, ready=True),
    )

    assert saved["observed_retrieval"]["embedding_provider"] == "dashscope:text-embedding-v4"
    assert saved["observed_retrieval"]["vector_store_provider"] == "qdrant"
    assert saved["observed_retrieval"]["rerank_provider"] == "bge:BAAI/bge-reranker-v2-m3"
    assert saved["observed_retrieval"]["qdrant_vector_hits"] is True
    assert saved["runtime_stats"]["embedding_remote_calls"] >= 1
    assert saved["runtime_stats"]["rerank_remote_calls"] >= 1
    evidence = saved["live_model_evidence"]
    assert evidence["overall"]["verified"] is True
    assert evidence["overall"]["status"] == "verified"
    assert evidence["embedding"]["provider"] == "dashscope"
    assert evidence["embedding"]["model_family"] == "Qwen3-Embedding"
    assert evidence["embedding"]["verified_remote_call"] is True
    assert evidence["embedding"]["fallback_free"] is True
    assert evidence["rerank"]["provider_family"] == "BGE-Reranker-v2"
    assert evidence["rerank"]["verified_remote_call"] is True
    assert evidence["rerank"]["fallback_free"] is True


def test_prepare_qdrant_points_keeps_vector_text_and_parent_payload():
    chunk = chunk_document(
        RagDocument("JP_SUSHI", "日本", "value_rule", "日本饮食", "寿司属于日本本土饮食文化。", {"source": "rules"})
    )[0]

    points = prepare_qdrant_points((chunk,), {chunk.chunk_id: (0.1, 0.2, 0.3)})

    assert len(points) == 1
    assert points[0].id
    assert points[0].vector == (0.1, 0.2, 0.3)
    assert points[0].payload["chunk_id"] == chunk.chunk_id
    assert points[0].payload["parent_id"] == "JP_SUSHI"
    assert points[0].payload["text"] == "寿司属于日本本土饮食文化。"


def test_qdrant_vector_store_upserts_points_with_payload():
    calls = []

    def fake_transport(endpoint, payload, api_key):
        calls.append((endpoint, payload, api_key))
        return {"status": "ok"}

    chunk = chunk_document(RagDocument("JP_SUSHI", "日本", "value_rule", "日本饮食", "寿司属于日本本土饮食文化。", {}))[0]
    points = prepare_qdrant_points((chunk,), {chunk.chunk_id: (0.1, 0.2)})
    store = QdrantVectorStore(
        RagVectorStoreConfig(provider="qdrant", endpoint="http://127.0.0.1:6333", collection="puzzle_ops_rag", api_key="qdrant-key", configured=True, ready=True),
        transport=fake_transport,
    )

    response = store.upsert(points)

    assert response == {"status": "ok"}
    assert calls[0][0] == "http://127.0.0.1:6333/collections/puzzle_ops_rag/points?wait=true"
    assert calls[0][1]["points"][0]["vector"] == [0.1, 0.2]
    assert calls[0][1]["points"][0]["payload"]["text"] == "寿司属于日本本土饮食文化。"
    assert calls[0][2] == "qdrant-key"


def test_qdrant_vector_store_search_returns_chunk_scores_with_country_filter():
    calls = []

    def fake_transport(endpoint, payload, api_key):
        calls.append((endpoint, payload, api_key))
        return {
            "result": [
                {"score": 0.91, "payload": {"chunk_id": "JP_SUSHI#chunk-1"}},
                {"score": 0.72, "payload": {"chunk_id": "GLOBAL_AUDIT#chunk-1"}},
            ]
        }

    store = QdrantVectorStore(
        RagVectorStoreConfig(provider="qdrant", endpoint="http://127.0.0.1:6333", collection="puzzle_ops_rag", api_key="qdrant-key", configured=True, ready=True),
        transport=fake_transport,
    )

    scores = store.search((0.1, 0.2), country="日本", top_k=2)

    assert scores == {"JP_SUSHI#chunk-1": 0.91, "GLOBAL_AUDIT#chunk-1": 0.72}
    assert calls[0][0] == "http://127.0.0.1:6333/collections/puzzle_ops_rag/points/search"
    assert calls[0][1]["vector"] == [0.1, 0.2]
    assert calls[0][1]["limit"] == 2
    assert calls[0][1]["filter"]["should"][0]["key"] == "country"
    assert calls[0][2] == "qdrant-key"


def test_qdrant_vector_store_ensures_missing_collection_with_vector_size():
    calls = []

    def fake_management(method, endpoint, payload, api_key):
        calls.append((method, endpoint, payload, api_key))
        if method == "GET":
            return {"result": None}
        return {"status": "ok", "result": True}

    store = QdrantVectorStore(
        RagVectorStoreConfig(provider="qdrant", endpoint="http://127.0.0.1:6333", collection="puzzle_ops_rag", api_key="qdrant-key", configured=True, ready=True),
        management_transport=fake_management,
    )

    status = store.ensure_collection(vector_size=3)

    assert status["status"] == "created"
    assert calls[0] == ("GET", "http://127.0.0.1:6333/collections/puzzle_ops_rag", None, "qdrant-key")
    assert calls[1][0] == "PUT"
    assert calls[1][2]["vectors"]["size"] == 3
    assert calls[1][2]["vectors"]["distance"] == "Cosine"


def test_qdrant_vector_store_rejects_collection_vector_size_mismatch():
    def fake_management(method, endpoint, payload, api_key):
        return {"result": {"config": {"params": {"vectors": {"size": 4, "distance": "Cosine"}}}}}

    store = QdrantVectorStore(
        RagVectorStoreConfig(provider="qdrant", endpoint="http://127.0.0.1:6333", collection="puzzle_ops_rag", configured=True, ready=True),
        management_transport=fake_management,
    )

    try:
        store.ensure_collection(vector_size=3)
    except ValueError as exc:
        assert "维度不匹配" in str(exc)
    else:
        raise AssertionError("expected vector size mismatch")


def test_qdrant_vector_store_smoke_diagnostic_writes_searches_and_deletes_temp_point():
    calls = []

    def fake_transport(endpoint, payload, api_key):
        calls.append(("POST", endpoint, payload, api_key))
        if endpoint.endswith("/points/search"):
            return {"result": [{"score": 1.0, "payload": {"chunk_id": "SMOKE#chunk-1"}}]}
        return {"status": "ok"}

    def fake_management(method, endpoint, payload, api_key):
        calls.append((method, endpoint, payload, api_key))
        return {"status": "ok"}

    store = QdrantVectorStore(
        RagVectorStoreConfig(provider="qdrant", endpoint="http://127.0.0.1:6333", collection="puzzle_ops_rag", api_key="qdrant-key", configured=True, ready=True),
        transport=fake_transport,
        management_transport=fake_management,
    )

    result = store.smoke_diagnostic(vector_size=3, country="日本")

    assert result["status"] == "passed"
    assert result["search_hit"] is True
    assert result["cleanup_status"] == "deleted"
    assert calls[0][1] == "http://127.0.0.1:6333/collections/puzzle_ops_rag/points?wait=true"
    assert calls[1][1] == "http://127.0.0.1:6333/collections/puzzle_ops_rag/points/search"
    assert calls[2][0] == "POST"
    assert calls[2][1] == "http://127.0.0.1:6333/collections/puzzle_ops_rag/points/delete?wait=true"
    assert calls[2][2]["points"] == [result["point_id"]]


def test_qdrant_vector_store_restore_points_upserts_stored_point_records():
    calls = []

    def fake_transport(endpoint, payload, api_key):
        calls.append((endpoint, payload, api_key))
        return {"status": "ok"}

    store = QdrantVectorStore(
        RagVectorStoreConfig(provider="qdrant", endpoint="http://127.0.0.1:6333", collection="puzzle_ops_rag", api_key="qdrant-key", configured=True, ready=True),
        transport=fake_transport,
    )

    result = store.restore_points(
        ("p1",),
        point_records=(
            {"id": "p1", "vector": [0.1, 0.2], "payload": {"chunk_id": "c1", "country": "日本"}},
        ),
    )

    assert result["status"] == "restored"
    assert result["restored_points"] == 1
    assert calls[0][0] == "http://127.0.0.1:6333/collections/puzzle_ops_rag/points?wait=true"
    assert calls[0][1]["points"][0]["id"] == "p1"
    assert calls[0][1]["points"][0]["vector"] == [0.1, 0.2]
    assert calls[0][1]["points"][0]["payload"]["chunk_id"] == "c1"


def test_milvus_vector_store_healthcheck_describes_collection():
    calls = []

    def fake_transport(method, endpoint, payload, api_key):
        calls.append((method, endpoint, payload, api_key))
        return {"code": 0, "data": {"collectionName": "puzzle_ops_rag", "schema": {"fields": [{"name": "vector", "params": {"dim": 3}}]}}}

    store = MilvusVectorStore(
        RagVectorStoreConfig(provider="milvus", endpoint="http://127.0.0.1:19530", collection="puzzle_ops_rag", api_key="milvus-token", configured=True, ready=True),
        transport=fake_transport,
    )

    status = store.healthcheck()

    assert status["provider"] == "milvus"
    assert status["ready"] is True
    assert status["exists"] is True
    assert status["collection"] == "puzzle_ops_rag"
    assert status["vector_size"] == 3
    assert calls[0] == ("POST", "http://127.0.0.1:19530/v2/vectordb/collections/describe", {"collectionName": "puzzle_ops_rag"}, "milvus-token")


def test_milvus_vector_store_upserts_entities_with_metadata_payload():
    calls = []

    def fake_transport(method, endpoint, payload, api_key):
        calls.append((method, endpoint, payload, api_key))
        return {"code": 0, "data": {"insertCount": 1}}

    chunk = chunk_document(RagDocument("JP_SUSHI", "日本", "value_rule", "日本饮食", "寿司属于日本本土饮食文化。", {}))[0]
    points = prepare_qdrant_points((chunk,), {chunk.chunk_id: (0.1, 0.2)})
    store = MilvusVectorStore(
        RagVectorStoreConfig(provider="milvus", endpoint="http://127.0.0.1:19530", collection="puzzle_ops_rag", api_key="milvus-token", configured=True, ready=True),
        transport=fake_transport,
    )

    response = store.upsert(points)

    assert response["status"] == "ok"
    assert response["insert_count"] == 1
    assert calls[0][0] == "POST"
    assert calls[0][1] == "http://127.0.0.1:19530/v2/vectordb/entities/insert"
    assert calls[0][2]["collectionName"] == "puzzle_ops_rag"
    assert calls[0][2]["data"][0]["id"] == points[0].id
    assert calls[0][2]["data"][0]["vector"] == [0.1, 0.2]
    assert calls[0][2]["data"][0]["chunk_id"] == chunk.chunk_id
    assert calls[0][2]["data"][0]["text"] == "寿司属于日本本土饮食文化。"
    assert calls[0][3] == "milvus-token"


def test_milvus_vector_store_search_returns_chunk_scores_with_country_filter():
    calls = []

    def fake_transport(method, endpoint, payload, api_key):
        calls.append((method, endpoint, payload, api_key))
        return {
            "code": 0,
            "data": [
                [
                    {"distance": 0.93, "entity": {"chunk_id": "JP_SUSHI#chunk-1"}},
                    {"score": 0.81, "entity": {"chunk_id": "GLOBAL_AUDIT#chunk-1"}},
                ]
            ],
        }

    store = MilvusVectorStore(
        RagVectorStoreConfig(provider="milvus", endpoint="http://127.0.0.1:19530", collection="puzzle_ops_rag", api_key="milvus-token", configured=True, ready=True),
        transport=fake_transport,
    )

    scores = store.search((0.1, 0.2), country="日本", top_k=2)

    assert scores == {"JP_SUSHI#chunk-1": 0.93, "GLOBAL_AUDIT#chunk-1": 0.81}
    assert calls[0][0] == "POST"
    assert calls[0][1] == "http://127.0.0.1:19530/v2/vectordb/entities/search"
    assert calls[0][2]["collectionName"] == "puzzle_ops_rag"
    assert calls[0][2]["data"] == [[0.1, 0.2]]
    assert calls[0][2]["limit"] == 2
    assert calls[0][2]["filter"] == 'country in ["日本", "GLOBAL"]'
    assert "chunk_id" in calls[0][2]["outputFields"]
    assert calls[0][3] == "milvus-token"


def test_hybrid_retriever_can_use_milvus_vector_scores_before_rerank():
    class QueryVectorEmbedding(LocalEmbeddingProvider):
        provider_name = "query-vector"

        def query_vector(self, query: str) -> tuple[float, ...]:
            return (0.1, 0.2)

        def similarities(self, query: str, texts: tuple[str, ...]) -> tuple[float, ...]:
            return tuple(0.0 for _ in texts)

    class FakeMilvusStore:
        provider_name = "milvus"

        def search(self, query_vector, *, country: str, top_k: int):
            assert query_vector == (0.1, 0.2)
            assert country == "日本"
            assert top_k == 1
            return {"JP_VECTOR#chunk-1": 0.97}

    documents = (
        RagDocument("JP_KEYWORD", "日本", "value_rule", "饮食文化", "寿司属于日本本土饮食文化。", {}),
        RagDocument("JP_VECTOR", "日本", "value_rule", "旅行场景", "温泉街浴衣灯笼适合治愈旅行。", {}),
    )
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document, max_chars=80))
    retriever = HybridRagRetriever(
        chunks,
        embedding_provider=QueryVectorEmbedding(),
        vector_store_retriever=MilvusVectorStoreRetriever(FakeMilvusStore()),
    )

    trace = retriever.search_with_trace("日本寿司价值观", country="日本", top_k=2, bm25_top_k=1, vector_top_k=1)

    assert "JP_VECTOR#chunk-1" in trace.vector_candidates
    assert trace.as_dict()["vector_store_provider"] == "milvus"
    assert any(hit.chunk.parent_id == "JP_VECTOR" and hit.vector_score == 0.97 for hit in trace.final_hits)


def test_hybrid_retriever_can_use_qdrant_vector_scores_before_rerank():
    class QueryVectorEmbedding(LocalEmbeddingProvider):
        provider_name = "query-vector"

        def query_vector(self, query: str) -> tuple[float, ...]:
            return (0.1, 0.2)

        def similarities(self, query: str, texts: tuple[str, ...]) -> tuple[float, ...]:
            return tuple(0.0 for _ in texts)

    class FakeStore:
        def search(self, query_vector, *, country: str, top_k: int):
            assert query_vector == (0.1, 0.2)
            assert country == "日本"
            assert top_k == 1
            return {"JP_VECTOR#chunk-1": 0.99}

    documents = (
        RagDocument("JP_KEYWORD", "日本", "value_rule", "饮食文化", "寿司属于日本本土饮食文化。", {}),
        RagDocument("JP_VECTOR", "日本", "value_rule", "旅行场景", "温泉街浴衣灯笼适合治愈旅行。", {}),
    )
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document, max_chars=80))
    retriever = HybridRagRetriever(
        chunks,
        embedding_provider=QueryVectorEmbedding(),
        vector_store_retriever=QdrantVectorStoreRetriever(FakeStore()),
    )

    trace = retriever.search_with_trace("日本寿司价值观", country="日本", top_k=2, bm25_top_k=1, vector_top_k=1)

    assert "JP_VECTOR#chunk-1" in trace.vector_candidates
    assert trace.as_dict()["vector_store_provider"] == "qdrant"
    assert any(hit.chunk.parent_id == "JP_VECTOR" and hit.vector_score == 0.99 for hit in trace.final_hits)


def test_feedback_aware_rerank_provider_promotes_useful_chunks():
    class FlatRerankProvider(LocalRerankProvider):
        provider_name = "flat-rerank"

        def rerank(self, query: str, country: str, chunk, bm25_score: float, vector_score: float) -> float:
            return 1.0

    documents = (
        RagDocument("JP_VALUE_001", "日本", "value_rule", "饮食文化", "寿司属于日本本土饮食文化。", {}),
        RagDocument("JP_VALUE_002", "日本", "value_rule", "季节感", "樱花、红叶和夏祭属于日本季节价值观。", {}),
    )
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document, max_chars=80))
    provider = FeedbackAwareRerankProvider(FlatRerankProvider(), {"JP_VALUE_002#chunk-1": 3})
    retriever = HybridRagRetriever(chunks, rerank_provider=provider)

    hits = retriever.search("日本价值观", country="日本", top_k=2)

    assert hits[0].chunk.chunk_id == "JP_VALUE_002#chunk-1"
    assert hits[0].rerank_score > hits[1].rerank_score
    assert "feedback" in hits[0].reason


def test_dashscope_embedding_provider_uses_transport_and_cosine_similarity():
    calls = []

    def fake_transport(texts, api_key, endpoint, model):
        calls.append((texts, api_key, endpoint, model))
        vectors = {
            "寿司价值观": [1.0, 0.0, 0.0],
            "寿司属于日本饮食文化": [0.8, 0.2, 0.0],
        }
        return {"data": [{"embedding": vectors[text]} for text in texts]}

    provider = DashScopeEmbeddingProvider(
        api_key="dashscope-test",
        model="text-embedding-v3",
        endpoint="https://dashscope.test/embeddings",
        transport=fake_transport,
    )

    score = provider.similarity("寿司价值观", "寿司属于日本饮食文化")

    assert score > 0.9
    assert calls[0][1] == "dashscope-test"
    assert calls[0][3] == "text-embedding-v3"
    assert provider.provider_name == "dashscope:text-embedding-v3"


def test_hybrid_retriever_batches_dashscope_embeddings_in_one_request():
    calls = []

    def fake_transport(texts, api_key, endpoint, model):
        calls.append(tuple(texts))
        vectors = {
            "日本寿司": [1.0, 0.0],
            "寿司属于日本饮食文化 饮食文化": [0.9, 0.1],
            "日式料理保持生活语境 文化真实性": [0.8, 0.2],
        }
        return {"data": [{"embedding": vectors[text]} for text in texts]}

    documents = (
        RagDocument("JP_VALUE_001", "日本", "value_rule", "饮食文化", "寿司属于日本饮食文化", {}),
        RagDocument("JP_VALUE_002", "日本", "value_rule", "文化真实性", "日式料理保持生活语境", {}),
    )
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document))
    stats = RagRuntimeStats()
    provider = DashScopeEmbeddingProvider(
        api_key="dashscope-test",
        model="text-embedding-v3",
        endpoint="https://dashscope.test/embeddings",
        transport=fake_transport,
        stats=stats,
    )

    hits = HybridRagRetriever(chunks, embedding_provider=provider).search("日本寿司", country="日本", top_k=2)

    assert len(calls) == 1
    assert len(calls[0]) == 3
    assert len(hits) == 2
    assert stats.embedding_remote_calls == 1


def test_dashscope_embedding_provider_splits_large_batches_by_configured_limit():
    calls = []

    def fake_transport(texts, api_key, endpoint, model):
        calls.append(tuple(texts))
        return {"data": [{"embedding": [1.0, float(index)]} for index, _ in enumerate(texts)]}

    provider = DashScopeEmbeddingProvider(
        api_key="dashscope-test",
        model="text-embedding-v3",
        endpoint="https://dashscope.test/embeddings",
        transport=fake_transport,
        stats=RagRuntimeStats(),
        batch_size=10,
    )

    scores = provider.similarities("query", tuple(f"document-{index}" for index in range(11)))

    assert len(scores) == 11
    assert [len(batch) for batch in calls] == [10, 2]
    assert provider.stats.embedding_remote_calls == 2


def test_dashscope_embedding_provider_uses_persistent_cache_before_remote_call():
    calls = []
    cache = {"寿司价值观": (1.0, 0.0, 0.0), "寿司属于日本饮食文化": (0.8, 0.2, 0.0)}
    stats = RagRuntimeStats()

    def fake_transport(texts, api_key, endpoint, model):
        calls.append(texts)
        return {"data": [{"embedding": [0.0, 0.0, 1.0]} for _ in texts]}

    provider = DashScopeEmbeddingProvider(
        api_key="dashscope-test",
        model="text-embedding-v3",
        endpoint="https://dashscope.test/embeddings",
        transport=fake_transport,
        cache_get=lambda provider_name, model, text: cache.get(text),
        cache_set=lambda provider_name, model, text, vector: cache.__setitem__(text, vector),
        stats=stats,
    )

    score = provider.similarity("寿司价值观", "寿司属于日本饮食文化")

    assert score > 0.9
    assert calls == []
    assert stats.embedding_cache_hits == 2
    assert stats.embedding_remote_calls == 0


def test_dashscope_rerank_provider_uses_transport_score():
    calls = []

    def fake_transport(query, documents, api_key, endpoint, model):
        calls.append((query, documents, api_key, endpoint, model))
        return {"results": [{"index": 0, "relevance_score": 0.87}]}

    chunk = chunk_document(RagDocument("JP_VALUE_001", "日本", "value_rule", "文化真实性", "寿司属于日本本土饮食文化。", {}))[0]
    provider = DashScopeRerankProvider(
        api_key="dashscope-test",
        model="gte-rerank-v2",
        endpoint="https://dashscope.test/rerank",
        transport=fake_transport,
        stats=RagRuntimeStats(),
    )

    score = provider.rerank("寿司是否符合日本价值观", "日本", chunk, bm25_score=0.2, vector_score=0.3)

    assert score == 0.87
    assert calls[0][0] == "寿司是否符合日本价值观"
    assert calls[0][1] == ["文化真实性：寿司属于日本本土饮食文化。"]
    assert provider.provider_name == "dashscope:gte-rerank-v2"


def test_bge_rerank_provider_uses_open_rerank_transport_score():
    calls = []

    def fake_transport(query, documents, api_key, endpoint, model):
        calls.append((query, documents, api_key, endpoint, model))
        return {"results": [{"index": 0, "relevance_score": 0.93}]}

    chunk = chunk_document(RagDocument("JP_VALUE_001", "日本", "value_rule", "文化真实性", "寿司属于日本本土饮食文化。", {}))[0]
    provider = BGERerankProvider(
        api_key="",
        model="BAAI/bge-reranker-v2-m3",
        endpoint="http://127.0.0.1:9997/v1/rerank",
        transport=fake_transport,
        stats=RagRuntimeStats(),
    )

    score = provider.rerank("寿司是否符合日本价值观", "日本", chunk, bm25_score=0.2, vector_score=0.3)

    assert score == 0.93
    assert calls[0][0] == "寿司是否符合日本价值观"
    assert calls[0][1] == ["文化真实性：寿司属于日本本土饮食文化。"]
    assert provider.provider_name == "bge:BAAI/bge-reranker-v2-m3"


def test_bge_rerank_provider_healthcheck_records_probe_score():
    calls = []

    def fake_transport(query, documents, api_key, endpoint, model):
        calls.append((query, documents, api_key, endpoint, model))
        return {"results": [{"index": 0, "relevance_score": 0.88}]}

    provider = BGERerankProvider(
        api_key="bge-key",
        model="BAAI/bge-reranker-v2-m3",
        endpoint="http://127.0.0.1:9997/v1/rerank",
        transport=fake_transport,
        stats=RagRuntimeStats(),
    )

    status = provider.healthcheck()

    assert status["provider"] == "bge"
    assert status["configured"] is True
    assert status["ready"] is True
    assert status["model"] == "BAAI/bge-reranker-v2-m3"
    assert status["probe_score"] == 0.88
    assert provider.stats.rerank_remote_calls == 1
    assert calls[0][0] == "寿司是否符合日本价值观"


def test_hybrid_retriever_batches_dashscope_rerank_in_one_request():
    calls = []

    def fake_transport(query, documents, api_key, endpoint, model):
        calls.append((query, documents))
        return {
            "results": [
                {"index": index, "relevance_score": 0.9 - index * 0.1}
                for index, _ in enumerate(documents)
            ]
        }

    documents = (
        RagDocument("JP_VALUE_001", "日本", "value_rule", "饮食文化", "寿司属于日本本土饮食文化。", {}),
        RagDocument("JP_VALUE_002", "日本", "value_rule", "文化真实性", "日式料理应保持真实生活语境。", {}),
    )
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document))
    provider = DashScopeRerankProvider(
        api_key="dashscope-test",
        model="gte-rerank-v2",
        endpoint="https://dashscope.test/rerank",
        transport=fake_transport,
        stats=RagRuntimeStats(),
    )

    hits = HybridRagRetriever(chunks, rerank_provider=provider).search("日本寿司饮食文化", country="日本", top_k=2)

    assert len(calls) == 1
    assert len(calls[0][1]) == 2
    assert len(hits) == 2
    assert provider.stats.rerank_remote_calls == 1


def test_dashscope_batch_rerank_failure_falls_back_without_single_remote_retries():
    calls = []

    def broken_transport(query, documents, api_key, endpoint, model):
        calls.append(tuple(documents))
        raise RuntimeError("timeout")

    chunks = tuple(
        chunk_document(document)[0]
        for document in (
            RagDocument("JP_VALUE_001", "日本", "value_rule", "饮食文化", "寿司属于日本本土饮食文化。", {}),
            RagDocument("JP_VALUE_002", "日本", "value_rule", "真实性", "料理需要真实生活语境。", {}),
        )
    )
    stats = RagRuntimeStats()
    provider = DashScopeRerankProvider(
        api_key="dashscope-test",
        model="gte-rerank-v2",
        endpoint="https://dashscope.test/rerank",
        transport=broken_transport,
        stats=stats,
    )

    scores = provider.rerank_many("日本寿司", "日本", tuple((chunk, 0.2, 0.3) for chunk in chunks))

    assert len(scores) == 2
    assert len(calls) == 1
    assert stats.rerank_remote_calls == 1
    assert stats.rerank_fallbacks == 2


def test_rag_runtime_stats_tracks_remote_and_fallback_paths():
    stats = RagRuntimeStats()

    def broken_embedding_transport(texts, api_key, endpoint, model):
        raise RuntimeError("timeout")

    def broken_rerank_transport(query, documents, api_key, endpoint, model):
        raise RuntimeError("timeout")

    chunk = chunk_document(RagDocument("JP_VALUE_001", "日本", "value_rule", "文化真实性", "寿司属于日本本土饮食文化。", {}))[0]
    embedding = DashScopeEmbeddingProvider(
        api_key="dashscope-test",
        model="text-embedding-v3",
        endpoint="https://dashscope.test/embeddings",
        transport=broken_embedding_transport,
        stats=stats,
    )
    rerank = DashScopeRerankProvider(
        api_key="dashscope-test",
        model="gte-rerank-v2",
        endpoint="https://dashscope.test/rerank",
        transport=broken_rerank_transport,
        stats=stats,
    )

    assert embedding.similarity("寿司", "寿司属于日本饮食文化") > 0
    assert rerank.rerank("寿司", "日本", chunk, 0.2, 0.3) > 0
    assert stats.embedding_remote_calls == 1
    assert stats.embedding_fallbacks == 1
    assert stats.rerank_remote_calls == 1
    assert stats.rerank_fallbacks == 1


def test_providers_from_config_uses_dashscope_when_api_key_present(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "text-embedding-v3")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_RERANK_MODEL", "gte-rerank-v2")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test")
    monkeypatch.setenv("RAG_ENABLE_REMOTE_CALLS", "true")

    config = RagProviderConfig.from_env(load_env=False)
    embedding, rerank = providers_from_config(config)

    assert config.remote_ready is True
    assert config.remote_calls_enabled is True
    assert isinstance(embedding, DashScopeEmbeddingProvider)
    assert isinstance(rerank, DashScopeRerankProvider)


def test_providers_from_config_keeps_local_fallback_until_remote_calls_enabled(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test")
    monkeypatch.delenv("RAG_ENABLE_REMOTE_CALLS", raising=False)

    config = RagProviderConfig.from_env(load_env=False)
    embedding, rerank = providers_from_config(config)

    assert config.remote_ready is True
    assert config.remote_calls_enabled is False
    assert isinstance(embedding, LocalEmbeddingProvider)
    assert isinstance(rerank, LocalRerankProvider)
