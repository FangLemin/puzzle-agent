# PuzzleOps Agent

**Multimodal Agent Harness for overseas jigsaw puzzle content operations.**

PuzzleOps Agent 是一个面向日本/法国出海拼图内容运营的 Agent Harness 项目。它把 VLM 看图理解、国家价值观 RAG、审核规则、四层 Memory、图像相似检索、HITL 人工修正、Agent 评测回放、飞书提需落地和 FastAPI 服务化串成一条可验证的运营工作流。

> 简历口径：构建了一个面向拼图内容运营的多模态 Agent Harness，覆盖 Qwen VLM 视觉解析、价值观/审核 RAG、四层 Memory、图像相似检索、HITL 标注、真实小样本评测、FastAPI 多人服务和飞书落地闭环。

## Why

拼图内容运营不是单纯“上传图片让模型描述一下”。运营需要判断一张图在不同国家市场是否适合排图、是否符合本土价值观、是否存在版权/IP/文化混淆风险、是否能从历史好图中找到依据，并把结果同步到真实飞书提需表。

这个项目的核心目标是把这些判断拆成可追踪、可回放、可人工修正、可评测的 Agent 工作流，而不是做一个单次 prompt demo。

## What It Does

- **VLM Trial Parsing**：上传 1-3 张参考图，调用 Qwen VLM 输出“主体内容 / 色彩氛围 / 构图环境”三段式解析。
- **Value Master**：结合当前图像证据、国家价值观 RAG、审核规则、历史样本和图像相似证据，输出等级预测、风险和排图建议。
- **RAG with Citations**：覆盖日本/法国价值观、审核规则、approved memory、真实 gold 样本，支持 chunk、父子文档、多路召回、rerank 和 citation 拼接。
- **Layered Memory**：按感知记忆、短期记忆、长期记忆、结构化事实管理，支持审批、冲突、过期、RAG 准入和命中回写。
- **Visual Similarity Evidence**：接入 Qwen3-VL-Embedding provider 与 Milvus/Zilliz image collection，低置信时显示“暂无可靠历史相似图”。
- **Agent Harness**：管理真实样本、synthetic demo、case trace、failure category、HITL 修正和评测报告。
- **Derivative Generation Interface**：抽象 DashScope/通义万相图像生成 provider；生成图必须二次 VLM 解析、审核复检、人工确认后才可进提需。
- **Feishu Workflow**：支持真实飞书多维表格写入和附件上传；字段或 token 未配置时不伪造成功。
- **FastAPI Service Layer**：为 6 人运营团队预留 `/docs`、权限 token、job、trace、metrics 和 provider health API。

## Architecture

```text
Candidate Image
  -> Qwen VLM visual parsing
  -> RAG retrieval: value rules + audit rules + approved memory + gold samples
  -> visual similarity search: Qwen3-VL embedding + Milvus/Zilliz
  -> Value Master reasoning
  -> HITL edit/review
  -> DemandRow / Feishu sync / Harness trace
```

Production-oriented split:

- **PostgreSQL**：users、tokens、audit logs、assets、jobs、trace events、business rows、memory facts。
- **Milvus/Zilliz**：RAG vectors 和 image embedding vectors。
- **OSS/local asset storage**：uploaded/generated images，数据库只存 URL、object key、hash、Feishu file token。
- **Redis/RQ**：Qwen VLM、图像生成、飞书同步、RAG rebuild、embedding 入库等慢任务。
- **FastAPI**：多人 API、权限、trace、metrics、provider health。
- **Local backend UI**：单人本地演示与运营页面，所有核心业务逻辑复用同一 Agent 层。

更多实现细节见 [docs/IMPLEMENTATION_NOTES.md](docs/IMPLEMENTATION_NOTES.md)。

## Quick Start

单人本地页面：

```bash
cd <repo-root>
PYTHONPATH=. python -c 'from puzzle_ops.server import run; run(port=5199)'
```

打开：

```text
http://127.0.0.1:5199/?view=dashboard
```

左侧导航会切换所有功能页，包括常规提需、试新提需、数据分析大师、价值观大师、系统治理中心、上线验收中心、排图工作台和同步记录。`?view=trial`、`?view=value`、`?view=runtime`、`?view=eval` 都是同一个本地服务的不同页面。

FastAPI 服务：

```bash
cd <repo-root>
PYTHONPATH=. uvicorn puzzle_ops.api:app --host 127.0.0.1 --port 8000
```

打开：

```text
http://127.0.0.1:8000/docs
```

6 人局域网测试：

```bash
PUZZLEOPS_API_TOKENS='ops_jp:jp_token:operator:日本,ops_fr:fr_token:operator:法国,admin:admin_token:admin:日本|法国' \
PYTHONPATH=. uvicorn puzzle_ops.api:app --host 0.0.0.0 --port 8000
```

## Configuration

复制 `.env.example` 为 `.env`，只在本地或服务器填写真实密钥，不要提交 `.env`。

```bash
cp .env.example .env
```

核心 provider：

```bash
VISION_LLM_PROVIDER=qwen
QWEN_API_KEY=your_dashscope_key
QWEN_VISION_MODEL=qwen3-vl-plus

RAG_ENABLE_REMOTE_CALLS=true
RAG_EMBEDDING_PROVIDER=dashscope
RAG_EMBEDDING_MODEL=text-embedding-v4
RAG_RERANK_PROVIDER=dashscope
RAG_RERANK_MODEL=qwen3-rerank
RAG_VECTOR_STORE_PROVIDER=milvus
MILVUS_URI=your_zilliz_or_milvus_uri
MILVUS_TOKEN=your_token

VISUAL_EMBEDDING_ENABLE_REMOTE_CALLS=true
VISUAL_EMBEDDING_MODEL=qwen3-vl-embedding
VISUAL_MILVUS_ENABLE_REMOTE_CALLS=true
VISUAL_MILVUS_URI=your_zilliz_or_milvus_uri
VISUAL_MILVUS_TOKEN=your_token
```

多人上线建议：

```bash
PUZZLEOPS_DB_PROVIDER=postgres
DATABASE_URL=postgresql+psycopg://user:password@host:5432/puzzleops

ASSET_STORAGE_PROVIDER=oss
ALIYUN_OSS_ENDPOINT=https://oss-cn-xxx.aliyuncs.com
ALIYUN_OSS_BUCKET=puzzleops-assets

PUZZLEOPS_JOB_QUEUE_PROVIDER=rq
REDIS_URL=redis://:<password>@redis-host:6379/0
```

Feishu sync requires `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_SPREADSHEET_TOKEN` and correct Bitable attachment fields. The project keeps failed sync payloads visible instead of pretending success.

## Evaluation Snapshot

The project uses a small real evaluation set plus synthetic demo samples. Public reports expose aggregate metrics only; raw row-level CSV and real business images are private assets and are not published.

- Real gold samples：45/50，Japan 25 + France 20.
- Value Master：三段式描述合规率 100%，飞书字段完整率 100%，工具调用成功率 100%.
- RAG report：Hit@5 100%，MRR@5 97%，NDCG@5 98%，Precision@5 20%，Recall@5 100%.
- Prompt benchmark：视觉解析均分 4.00/5，RAG citation 和历史依据仍是主要优化点。
- Visual similarity：低置信 evidence 会被 gate 掉，避免强行展示不相关历史图。

See [docs/EVAL_REPORT.md](docs/EVAL_REPORT.md) and [docs/eval/rag_release_report.md](docs/eval/rag_release_report.md).

## Documentation

Start here:

- [GitHub Showcase](docs/GITHUB_SHOWCASE.md)
- [Implementation Notes](docs/IMPLEMENTATION_NOTES.md)
- [Project Defense](docs/PROJECT_DEFENSE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [FastAPI API Spec](docs/API_SPEC.md)
- [Deployment Guide](docs/DEPLOYMENT.md)
- [Evaluation Report](docs/EVAL_REPORT.md)
- [Interview Notes](docs/INTERVIEW_NOTES.md)
- [Security Release Checklist](docs/SECURITY_RELEASE_CHECKLIST.md)

Deep dive reports:

- [Online Acceptance v0.7.70](docs/final_acceptance/v0.7.70_online_acceptance_report.md)
- [Resume Closure v0.7.49](docs/final_acceptance/v0.7.49_resume_closure_report.md)
- [Value Master Eval](docs/eval/value_master_eval_report.md)
- [RAG Hard-negative Report](docs/eval/rag_hard_negative_report.md)
- [Visual Similarity Confidence Policy](docs/eval/visual_similarity_confidence_policy_report.md)

## Validation

Security preflight:

```bash
python scripts/release_preflight.py
```

Default tests disable remote model/vector calls to avoid cost:

```bash
ANALYSIS_LLM_ENABLE_REMOTE_CALLS=0 \
RAG_ENABLE_REMOTE_CALLS=false \
RAG_EMBEDDING_PROVIDER=local \
RAG_RERANK_PROVIDER=local \
VISUAL_EMBEDDING_ENABLE_REMOTE_CALLS=false \
VISUAL_MILVUS_ENABLE_REMOTE_CALLS=false \
VISION_LLM_PROVIDER=qwen \
QWEN_API_KEY= \
IMAGE_GENERATION_PROVIDER=mock \
PYTHONPATH=. pytest tests -q
```

Latest verified full regression before GitHub packaging: `635 passed`.

## Limitations

- This is an engineering Agent Harness and small-sample validation project, not a claim of large-scale stable predictive accuracy.
- Real image assets, raw row-level business CSV, Feishu URLs and API keys are intentionally excluded from the public repository.
- Value prediction, RAG citation and visual similarity are decision support signals; human review remains part of the workflow.
- FastAPI provides the service layer for a 6-person team, but production deployment still requires HTTPS, token governance, server firewall, backups and provider credentials.
