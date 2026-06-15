from puzzle_ops.rag import HybridRagRetriever, RagDocument, build_rag_prompt, chunk_document


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
