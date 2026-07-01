# PuzzleOps RAG Knowledge Base

This directory keeps versioned RAG inputs outside Python source code.

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
