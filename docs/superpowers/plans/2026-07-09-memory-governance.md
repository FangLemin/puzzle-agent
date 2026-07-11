# Memory Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add conflict governance, memory trust weighting for RAG, and provenance tracing for PuzzleOps layered memory.

**Architecture:** Keep persistence unchanged in SQLite and implement governance as agent-level interpretation over existing layered memory payloads. Add metadata-based memory RAG weighting so local and remote vector stores remain compatible. Render the new signals in the existing Python server-side UI.

**Tech Stack:** Python 3.12, dataclasses, SQLite, pytest, Python standard library HTTP renderer.

---

### Task 1: Memory Conflict Detection

**Files:**
- Modify: `tests/test_storage_runtime.py`
- Modify: `puzzle_ops/agents.py`

- [ ] **Step 1: Write the failing test**

Add tests that create same-subject memories with opposing stances and assert `PuzzleOpsAgent.memory_conflicts()` returns a conflict containing both ids.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_storage_runtime.py::test_agent_detects_conflicting_value_memories_for_same_subject -q`

Expected: FAIL because `memory_conflicts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Add memory stance classification helpers and `PuzzleOpsAgent.memory_conflicts(country)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_storage_runtime.py::test_agent_detects_conflicting_value_memories_for_same_subject -q`

Expected: PASS.

### Task 2: Memory RAG Trust Weighting

**Files:**
- Modify: `tests/test_rag.py`
- Modify: `puzzle_ops/agents.py`
- Modify: `puzzle_ops/rag.py`

- [ ] **Step 1: Write the failing test**

Add tests proving memory documents expose `memory_weight` metadata and that `HybridRagRetriever` ranks a human-verified fact above a weaker perception memory when both match the query.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_rag.py::test_hybrid_retriever_prefers_trusted_memory_weight -q`

Expected: FAIL because rerank does not apply memory weights.

- [ ] **Step 3: Write minimal implementation**

Set memory trust metadata in `_layered_memory_rag_documents()` and multiply rerank scores by metadata `memory_weight` inside `HybridRagRetriever.search_with_trace()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_rag.py::test_hybrid_retriever_prefers_trusted_memory_weight -q`

Expected: PASS.

### Task 3: Memory Provenance API

**Files:**
- Modify: `tests/test_storage_runtime.py`
- Modify: `puzzle_ops/agents.py`

- [ ] **Step 1: Write the failing test**

Add a test that records a perception memory, promotes it to facts, records a value correction and RAG feedback, then asserts `memory_provenance(country, memory_id)` returns source/current/descendant/related steps.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_storage_runtime.py::test_memory_provenance_links_promotion_correction_and_rag_feedback -q`

Expected: FAIL because `memory_provenance` does not exist.

- [ ] **Step 3: Write minimal implementation**

Add `PuzzleOpsAgent.memory_provenance(country, memory_id)` and small payload matching helpers.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_storage_runtime.py::test_memory_provenance_links_promotion_correction_and_rag_feedback -q`

Expected: PASS.

### Task 4: Renderer Integration

**Files:**
- Modify: `tests/test_renderer.py`
- Modify: `puzzle_ops/renderer.py`

- [ ] **Step 1: Write the failing test**

Add a renderer test asserting the multimodal foundation page includes Memory Conflict and Memory Provenance sections.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_renderer.py::test_multimodal_page_shows_memory_governance_sections -q`

Expected: FAIL because the sections are not rendered.

- [ ] **Step 3: Write minimal implementation**

Render conflict rows, provenance rows for the top matching memory, and conflict badges in Memory Debug.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_renderer.py::test_multimodal_page_shows_memory_governance_sections -q`

Expected: PASS.

### Task 5: Full Verification

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update docs**

Document conflict governance, RAG trust weighting, and provenance view.

- [ ] **Step 2: Run targeted tests**

Run: `PYTHONPATH=. pytest tests/test_storage_runtime.py tests/test_rag.py tests/test_renderer.py -q`

Expected: PASS.

- [ ] **Step 3: Run full tests**

Run: `PYTHONPATH=. pytest tests -q`

Expected: PASS or report any pre-existing environment-only skips/failures.

