# Qwen VL Visual Similarity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Qwen3-VL multimodal embedding based image similarity retrieval, Milvus/Zilliz image vector indexing, and Value Master similar good/bad image evidence without replacing the existing grade prediction chain.

**Architecture:** Keep SQLite as source of truth for history samples and memory. Add a focused visual similarity layer that creates image embeddings through DashScope Multimodal Embedding, stores/searches vectors in a dedicated Milvus collection when configured, falls back to an in-memory/local cache for tests, and injects grouped similar good/bad evidence into Value Master outputs.

**Tech Stack:** Python standard library, existing DashScope HTTP transport style, SQLite repository, existing Milvus/Zilliz config, pytest, optional pymilvus, Qwen3-VL-Embedding API.

## Global Constraints

- Do not change the existing page routes or the current grade prediction main chain.
- Do not commit `.env` or API keys.
- Remote multimodal embedding calls must be gated by an explicit env flag.
- Milvus image collection must be optional; local tests must pass without network.
- Version and CHANGELOG must advance after implementation.
- Run `PYTHONPATH=. pytest tests -q` before final handoff.

---

### Task 1: Qwen VL Multimodal Embedding Provider

**Files:**
- Create: `puzzle_ops/visual_similarity.py`
- Test: `tests/test_visual_similarity.py`

**Interfaces:**
- Produces: `QwenVLImageEmbeddingProvider.embed_image(path: str, text: str = "") -> VisualEmbedding`
- Produces: `LocalVisualEmbeddingProvider.embed_image(path: str, text: str = "") -> VisualEmbedding`
- Produces: `VisualEmbedding(vector: tuple[float, ...], provider: str, model: str, dimension: int, source: str)`

- [ ] Write tests for local deterministic embeddings and Qwen provider payload shape using a fake transport.
- [ ] Implement providers with Base64 data URI image input and optional text fusion.
- [ ] Verify remote calls stay disabled unless `VISUAL_EMBEDDING_ENABLE_REMOTE_CALLS=true`.

### Task 2: Image Similarity Index

**Files:**
- Modify: `puzzle_ops/visual_similarity.py`
- Test: `tests/test_visual_similarity.py`

**Interfaces:**
- Produces: `VisualSimilarityIndex.upsert(records: tuple[VisualIndexRecord, ...]) -> dict[str, object]`
- Produces: `VisualSimilarityIndex.search(query: VisualEmbedding, country: str, top_k: int = 5) -> tuple[VisualSimilarityHit, ...]`

- [ ] Write tests that index three historical images and return the closest same-country result.
- [ ] Implement local in-memory index for tests and Milvus-ready adapter boundary.
- [ ] Ensure hits include image_id, country, grade, subject, operation_tag, score, and reason.

### Task 3: PuzzleOpsAgent Integration

**Files:**
- Modify: `puzzle_ops/agents.py`
- Test: `tests/test_agents.py`

**Interfaces:**
- Produces: `PuzzleOpsAgent.rebuild_visual_similarity_index(country: str) -> dict[str, object]`
- Produces: `PuzzleOpsAgent.similar_visual_history_for_candidate(candidate: dict[str, object], top_k: int = 6) -> dict[str, object]`

- [ ] Write tests that historical S/A and C/D images are grouped as similar_good and similar_risk.
- [ ] Implement history record conversion and score fusion.
- [ ] Keep this as evidence only; do not change `value_grade_model_version`.

### Task 4: Value Master Evidence Injection

**Files:**
- Modify: `puzzle_ops/agents.py`
- Test: `tests/test_agents.py`

**Interfaces:**
- Updates: value candidate prediction dict contains `visual_similarity_evidence`.
- Updates: value match prompt/result can mention similar visual good/risk evidence.

- [ ] Write tests that value candidate output includes similar visual evidence and still keeps legacy grade model.
- [ ] Add concise evidence lines to prompt context.
- [ ] Add no-evidence fallback: `历史图像相似依据不足，需人工复核。`

### Task 5: Docs, Version, Verification

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `VERSION`

- [ ] Document Qwen3-VL-Embedding config and Milvus image collection purpose.
- [ ] Record v0.7.50/0.7.51/0.7.52 logical milestones.
- [ ] Run targeted tests and full pytest.
- [ ] Commit implementation.

