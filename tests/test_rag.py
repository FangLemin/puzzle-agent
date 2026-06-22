from puzzle_ops.rag import (
    DashScopeEmbeddingProvider,
    DashScopeRerankProvider,
    HybridRagRetriever,
    LocalEmbeddingProvider,
    LocalRerankProvider,
    RagDocument,
    RagProviderConfig,
    RagRuntimeStats,
    build_rag_prompt,
    chunk_document,
    providers_from_config,
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
    assert prompt.citations == ("JP_VALUE_001#chunk-1",)


def test_rag_provider_config_reports_local_fallback_without_external_keys(monkeypatch):
    monkeypatch.delenv("RAG_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("RAG_RERANK_PROVIDER", raising=False)

    config = RagProviderConfig.from_env(load_env=False)

    assert config.embedding_provider == "local"
    assert config.rerank_provider == "local"
    assert config.configured is False
    assert "本地" in config.status_text


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
