# PuzzleOps Agent GitHub Showcase

这份页面用于让面试官、HR 或第一次打开 GitHub 的读者快速理解项目亮点。

## One-liner

PuzzleOps Agent is a multimodal Agent Harness for overseas jigsaw puzzle content operations, combining VLM image understanding, value/audit RAG, layered memory, HITL feedback, evaluation replay, visual similarity evidence, FastAPI service APIs and Feishu workflow.

## What Problem It Solves

拼图运营需要判断候选图片是否适合日本/法国市场，并把判断落到提需表。传统做法依赖人工经验，难以复盘；普通 VLM caption 又无法回答“为什么适合这个市场、有哪些风险、历史依据是什么、是否能同步到业务表”。

PuzzleOps Agent 把这个流程拆成可追踪的 Agent 工作流：

```text
看图 -> 查规则 -> 找历史依据 -> 判断价值观 -> 人工修正 -> 评测回放 -> 飞书落地
```

## Highlights

| Area | What is implemented |
|---|---|
| Multimodal | Qwen VLM parses subject, color mood and composition from uploaded images. |
| RAG | Value rules, audit rules, approved memory and gold samples are retrieved with citation. |
| Memory | Perceptual, short-term, long-term and structured factual memory with HITL approval. |
| Harness | Real/synthetic samples, case traces, failure categories and evaluation reports. |
| HITL | Operators can edit AI outputs and promote verified facts into memory/gold labels. |
| Visual Similarity | Qwen3-VL image embedding + Milvus/Zilliz for historical good/bad image evidence. |
| Workflow | Demand rows can be edited and synced to Feishu Bitable with attachment handling. |
| Service Layer | FastAPI APIs, token roles, jobs, traces, metrics and provider health for team usage. |

## Demo Entry

Local operator UI:

```bash
PYTHONPATH=. python -c 'from puzzle_ops.server import run; run(port=5199)'
```

Open:

```text
http://127.0.0.1:5199/?view=dashboard
```

FastAPI service:

```bash
PYTHONPATH=. uvicorn puzzle_ops.api:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/docs
```

## Architecture Snapshot

```text
Local UI / FastAPI
  -> PuzzleOpsAgent
      -> Qwen VLM Provider
      -> RAG Retriever + Reranker
      -> Visual Similarity Search
      -> Layered Memory
      -> Harness Runner
      -> Feishu Client
  -> PostgreSQL / SQLite
  -> Milvus or Zilliz
  -> OSS or local asset storage
  -> Redis/RQ worker
```

## Evaluation Snapshot

| Metric | Current public result |
|---|---:|
| Real gold samples | 45/50 |
| Trial three-part description compliance | 100% |
| Feishu field completeness | 100% |
| Tool-call success | 100% |
| RAG Hit@5 | 100% |
| RAG MRR@5 | 97% |
| RAG NDCG@5 | 98% |
| RAG Precision@5 | 20% |

Interpretation:

- The engineering workflow is testable and traceable.
- VLM parsing is usable for small-sample business review.
- RAG citation and historical evidence still need more real samples and iteration.
- The project is presented as an Agent Harness and evaluation system, not as a fully stable prediction model.

## Resume Bullets

- Built a multimodal Agent Harness for overseas jigsaw content operations, connecting Qwen VLM image parsing, value/audit RAG, layered memory, HITL feedback and Feishu demand workflow.
- Designed a business-oriented RAG pipeline with chunked value/audit knowledge, multi-route retrieval, rerank, citation grounding and retrieval evaluation metrics including MRR@5, NDCG@5, Precision@5 and Recall@5.
- Implemented a small-sample evaluation loop with real gold labels, case traces, failure categories, human overrides and versioned reports to separate VLM, RAG, history-evidence and calibration errors.
- Added visual similarity evidence using Qwen3-VL image embeddings and Milvus/Zilliz, with confidence gating to avoid showing irrelevant historical images.
- Refactored the local agent into a deployable service architecture with FastAPI, token roles, PostgreSQL, OSS asset storage, Redis/RQ jobs, trace events and provider health metrics.

## Public Release Boundaries

- No real `.env`, API key, Feishu URL, raw row-level CSV or real business image is published.
- Reports expose aggregate metrics and known limitations only.
- The project does not claim large-scale production prediction accuracy.
- Human review remains part of the workflow for value judgment, risk review and Feishu sync.

