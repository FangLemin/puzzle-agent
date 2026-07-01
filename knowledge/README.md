# PuzzleOps RAG Knowledge Base

This directory keeps versioned RAG inputs outside Python source code.

- `raw/*.md`: human-editable source notes for country values and audit policies.
- `processed/value_audit_documents.jsonl`: normalized RAG parent documents for value matching and audit rules.
- `eval/value_audit_cases.jsonl`: retrieval gold cases used to validate `hit@5` and `mrr@5`.

The app can be pointed to another knowledge directory with:

```bash
PUZZLEOPS_RAG_KNOWLEDGE_DIR=/path/to/knowledge
```

Each document row should include:

```json
{"document_id":"JP_KB_SUSHI","country":"日本","source_type":"value_rule","title":"日本饮食文化","text":"...","metadata":{"knowledge_version":"2026-07-01"}}
```

Each eval case row should include:

```json
{"query":"日本寿司图是否符合本土饮食价值观","country":"日本","expected_parent_id":"JP_KB_SUSHI"}
```

Raw Markdown files support a small front matter block:

```markdown
---
country: 日本
source_type: value_rule
knowledge_version: 2026-07-02
---
# 日本市场价值观沉淀

## 日本本土饮食文化 {#JP_KB_SUSHI_FOOD}
寿司、抹茶、和果子属于日本本土饮食文化。
```

The ingest helper splits each `##` section into one parent `RagDocument`, preserving the source file path and version in metadata. Optional `{#DOC_ID}` markers keep parent IDs stable so retrieval eval cases can point to the same gold document after raw-to-processed regeneration.
