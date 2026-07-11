# Memory Governance Design

## Goal

Add lightweight governance to PuzzleOps layered memory so operators can see contradictory memories, RAG can prefer trusted memories, and HITL provenance can be explained from perception through correction, facts, and RAG feedback.

## Scope

This design covers three additions:

- Conflict detection for memories that discuss the same country, subject, or operation tag but disagree on value suitability or risk.
- RAG ranking weights for memory-derived knowledge, with stronger treatment for human-verified facts and long-term memory.
- A read-only memory provenance view that traces source memory, promoted descendants, human corrections, fact records, and related RAG feedback.

It does not add a full labeling platform, automatic memory deletion, multi-user permissioning, or a standard MCP server.

## Architecture

The feature stays inside the existing pure Python architecture. `PuzzleRepository` remains the persistence layer and stores layered memory exactly as it does today. `PuzzleOpsAgent` adds governance methods that interpret memory payloads, calculate conflicts, expose provenance chains, and attach retrieval metadata to memory RAG documents. `renderer.py` displays the new governance signals on the existing multimodal foundation page.

Conflict detection is intentionally heuristic and explainable. It groups active and inactive memory by normalized `country`, `subject`, and `operation_tag` when those keys exist in payload text. Within each group, it classifies memory stance as positive, negative, risk, or neutral using Chinese business keywords. A conflict exists when positive memory appears with negative or risk memory in the same group.

RAG weighting uses document metadata instead of changing the SQLite schema or vector-store schema. Memory-derived `RagDocument` metadata receives `memory_weight`, `trust_level`, `rag_ready`, `human_verified`, and `governance_status`. `HybridRagRetriever` applies this weight in rerank scoring after BM25/vector/rerank candidate merging. This keeps remote embedding and Milvus/Qdrant compatibility intact.

Provenance is computed from existing fields:

- `source_memory_id` links promoted memories back to their source.
- Payload keys such as `source_working_memory_id`, `citation_ids`, `expected_parent_id`, and `retrieved_parent_ids` connect value corrections, facts, and RAG feedback.
- Related memories are discovered by shared subject, operation tag, citation id, or source id.

## Data Flow

1. A perception or working memory is written during image parsing, value correction, RAG feedback, or generation.
2. Operators can promote a memory to facts or long-term memory, preserving `source_memory_id` and `human_verified=True`.
3. `memory_conflicts(country)` scans layered memory and produces conflict groups with stance, evidence, and involved memory ids.
4. `_layered_memory_rag_documents(country)` includes only active memories with non-empty text and attaches trust metadata.
5. `HybridRagRetriever` multiplies final rerank score by memory metadata weight for memory-derived chunks.
6. `memory_provenance(country, memory_id)` returns a compact chain of source, current memory, descendants, related corrections, related facts, and related RAG feedback.
7. The UI shows conflict badges in Memory Debug and a new provenance table.

## Error Handling

Invalid or missing memory ids raise a `ValueError` with a user-facing message. Memories without subject or operation tag remain visible but are not grouped into strict subject/tag conflicts. Neutral memories do not trigger conflicts. Expired, promoted, and retired memories are shown in provenance, but only active memories are RAG ready.

## Testing

Tests should prove:

- Opposing value memories for the same subject/tag are detected and exposed with both memory ids.
- Unrelated subjects or neutral notes do not produce false conflicts.
- Memory-derived RAG documents carry trust metadata, and retriever ranking prefers human-verified facts/long-term memory over weaker perception memory when textual relevance is otherwise similar.
- Provenance links source memory, promoted fact, human correction, and RAG feedback into one readable chain.

