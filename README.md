# PuzzleOps Agent

![PuzzleOps Agent](docs/assets/readme/puzzleops-hero.png)

**An evidence-driven multimodal Agent Harness for overseas jigsaw puzzle content operations.**

[![Version](https://img.shields.io/badge/version-0.7.79-16866f)](VERSION)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](requirements.txt)
[![CI](https://github.com/FangLemin/puzzle-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/FangLemin/puzzle-agent/actions/workflows/ci.yml)
[![FastAPI](https://img.shields.io/badge/service-FastAPI-009688?logo=fastapi&logoColor=white)](docs/API_SPEC.md)
[![Qwen](https://img.shields.io/badge/VLM-Qwen-6F65E8)](docs/IMPLEMENTATION_NOTES.md)

PuzzleOps Agent 面向日本、法国拼图内容运营，把 **Qwen VLM 看图、价值观/审核 RAG、四层 Memory、历史图像相似检索、HITL、Agent Harness、飞书提需和 FastAPI 多人服务** 串成一条可追踪、可回放、可评测的工作流。

> 不是单次 Prompt 演示：模型输出必须带业务证据，低置信证据会被拦截，外部写入保留人工确认，失败样本进入下一轮评测。

## Demo

![PuzzleOps workflow demo](docs/assets/readme/demo-workflow.gif)

演示使用仓库内置的 synthetic demo，未包含真实业务图片、行级数据、飞书地址或密钥。

## Agent Workflow

```text
Candidate image -> Qwen VLM -> RAG + visual similarity + approved memory
                -> Value Master -> HITL review -> DemandRow / Feishu
                -> trace + Harness scores -> failure analysis -> next version
```

![Architecture overview](docs/assets/readme/architecture-overview.svg)

## Why This Project

拼图运营真正困难的部分不是“描述图片”，而是把以下判断做成稳定流程：

- 当前图像是否符合不同国家的内容价值观。
- 主体、色彩、构图、文化元素和风险是否识别正确。
- RAG 规则和历史相似图是否真的支撑结论。
- AI 建议是否可编辑、可追溯，并能安全落入真实提需工具。
- Prompt、检索、模型或标定问题能否被 Harness 分开定位。

因此项目采用 evidence-first 设计：VLM 负责感知，RAG 与历史图负责提供依据，Memory 管理经过审批的经验，HITL 控制最终业务动作，Harness 量化每个环节。

## Core Capabilities

| Capability | Implementation | Safety / evaluation boundary |
|---|---|---|
| Trial parsing | Qwen VLM 输出“主体内容 / 色彩氛围 / 构图环境” | 描述和运营 tag 可人工编辑 |
| Value Master | 图像证据 + 国家 RAG + 审核规则 + 历史依据 | citation 不足时要求人工复核 |
| RAG | semantic chunk、parent metadata、BM25 + vector、rerank、Top3 citation | hard-negative gate，不把常识伪装成规则 |
| Layered Memory | perception / working / long-term / facts | approved、active、无冲突、未过期才可进 RAG |
| Visual similarity | Qwen3-VL-Embedding provider + Milvus/Zilliz | 低置信时返回“暂无可靠历史相似图” |
| Derivative generation | 通义万相/DashScope provider abstraction | 二次 VLM 审核 + HITL 后才能进提需 |
| Agent Harness | dataset、run、case trace、score、failure、override | 真实样本与 synthetic demo 分开统计 |
| Team service | FastAPI、role/country auth、job、trace、metrics | 写操作审计；飞书同步保留人工确认 |

## Product Views

| Trial parsing | Value Master |
|---|---|
| ![Trial parsing UI](docs/assets/readme/ui-trial.png) | ![Value Master UI](docs/assets/readme/ui-value-master.png) |

| Harness dashboard | Runtime health |
|---|---|
| ![Harness UI](docs/assets/readme/ui-harness.png) | ![Runtime metrics UI](docs/assets/readme/ui-api-metrics.png) |

## Architecture

线上形态将事务状态、向量和图片对象分开管理：

- **PostgreSQL**：users、tokens、audit logs、business rows、jobs、traces、memory facts、RAG metadata。
- **Milvus/Zilliz**：RAG text vectors 与 image embedding vectors。
- **OSS / local asset storage**：上传图和生成图；数据库只存 object key、URL、hash、Feishu file token。
- **Redis/RQ**：Qwen VLM、图像生成、飞书同步、RAG rebuild、embedding 入库等慢任务。
- **FastAPI**：6 人团队 API、角色/国家权限、job、trace、metrics、provider health。
- **Local backend UI**：本地单人演示，复用相同 Agent/service 层。

## Design Decisions

| Decision | Why | Rejected / fallback path |
|---|---|---|
| Harness before post-training | 先区分视觉解析、检索、排序、Prompt 和指标标定问题，避免把数据问题误判成模型能力问题 | 直接微调会把错误 citation 与合成分布固化进模型 |
| PostgreSQL owns Memory state | Memory 有审批、冲突、过期、审计和并发写入，不只是相似度检索 | SQLite 仅保留本地 demo；Milvus 只保存可检索向量 |
| Hybrid RAG + rerank | BM25 保留审核术语，vector 覆盖语义表达差异，rerank 控制最终证据顺序 | 单一向量召回曾产生较多同国异主体 hard negatives |
| Confidence-gated image search | 小样本下 TopK 总会返回结果，但“最像”不等于“足够相关” | 低分历史图不展示、不注入 LLM，不强凑依据 |
| Async jobs for slow providers | VLM、生成、飞书附件、RAG rebuild 不应阻塞多人请求 | 本地模式可同步执行，线上使用 Redis/RQ |
| HITL before external writes | 运营描述、tag、附件和风险判断必须可审可改 | 不允许模型结果未经确认直接写入飞书 |

### RAG Pipeline

![RAG pipeline](docs/assets/readme/rag-pipeline.svg)

离线阶段将日本/法国价值观、审核规则、approved memory 和 gold samples 按语义边界切分，保存父子关系与原文，再写入向量库。在线阶段组合 BM25 与向量召回，经 rerank、相关性 gate 和 Top3 citation 拼入 Prompt；资料不足时输出“需要人工复核”。

### Layered Memory

![Layered Memory lifecycle](docs/assets/readme/memory-lifecycle.svg)

Memory 使用事务数据库作为主存，因为审批、冲突、过期、审计和命中回写需要强状态管理；只有可检索的 approved memory 才生成向量进入 Milvus。

### Harness & HITL

![Agent Harness evaluation loop](docs/assets/readme/eval-loop.svg)

每次运行保存 provider、prompt/version、tool calls、citations、latency、scores 和 failure reasons。人工修正不会静默覆盖原输出，而是作为 override/gold 进入下一轮评测。

## Evaluation

![Evaluation snapshot](docs/assets/readme/evaluation-snapshot.svg)

Public reports only expose aggregate results. The private evaluation asset contains **45 real samples: Japan 25 and France 20**; synthetic samples are used only for UI demos and boundary tests.

| Area | Metric | Result | Interpretation |
|---|---:|---:|---|
| Workflow | Three-part description compliance | 100% | 输出契约稳定 |
| Workflow | Feishu field completeness | 100% | 字段映射完整 |
| Workflow | Tool-call success | 100% | 评测运行中的工具链可执行 |
| RAG | Hit@5 / MRR@5 / NDCG@5 | 100% / 97% / 98% | expected document 通常较早出现 |
| RAG | Precision@5 / Recall@5 | 20% / 100% | 召回足够，但候选池仍有 hard negatives |
| Value | SA high-potential binary accuracy | 60% | 小样本决策辅助信号，不作为生产准确率宣称 |
| Calibration | Metric-derived grade baseline | 18% | 已拒绝将三项预测指标直接反推主等级 |
| Visual search | Gold Hit@5 / MRR | 66.67% / 27.78% | 数据稀疏，使用 confidence gate 隐藏低分证据 |

完整解释见 [Evaluation Report](docs/EVAL_REPORT.md)、[RAG Release Report](docs/eval/rag_release_report.md) 与 [Visual Similarity Confidence Policy](docs/eval/visual_similarity_confidence_policy_report.md)。

## Quick Start

### Local UI

```bash
git clone https://github.com/FangLemin/puzzle-agent.git
cd puzzle-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python -c 'from puzzle_ops.server import run; run(port=5199)'
```

打开 `http://127.0.0.1:5199/?view=dashboard`。左侧导航切换常规提需、试新提需、数据分析、价值观大师、系统治理、上线验收和同步记录。

### FastAPI

```bash
PUZZLEOPS_API_TOKENS='ops_jp:jp_token:operator:日本,ops_fr:fr_token:operator:法国,admin:admin_token:admin:日本|法国' \
PYTHONPATH=. uvicorn puzzle_ops.api:app --host 0.0.0.0 --port 8000
```

打开 `http://127.0.0.1:8000/docs`。服务器部署和 6 人权限配置见 [Deployment Guide](docs/DEPLOYMENT.md)。

## Configuration

复制示例配置，真实密钥仅写入本地或服务器环境：

```bash
cp .env.example .env
```

核心 provider 示例：

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
```

多人上线推荐：

```bash
PUZZLEOPS_DB_PROVIDER=postgres
DATABASE_URL=postgresql+psycopg://user:password@host:5432/puzzleops
ASSET_STORAGE_PROVIDER=oss
PUZZLEOPS_JOB_QUEUE_PROVIDER=rq
REDIS_URL=redis://redis-host:6379/0
```

Feishu sync additionally requires valid app credentials, table identifiers and attachment fields. Provider failure is surfaced as a failed job/trace; the project does not fabricate success.

## Validation

```bash
python scripts/release_preflight.py
```

默认回归关闭远程模型和向量库调用，避免意外成本：

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

Latest verified full regression for this release: `647 passed`.

## Repository Map

```text
puzzle_ops/agents.py        Agent orchestration and business workflows
puzzle_ops/rag.py           offline indexing, hybrid retrieval and citations
puzzle_ops/storage.py       SQLite repository and layered memory state
puzzle_ops/harness.py       datasets, runs, case traces and scoring
puzzle_ops/api.py           FastAPI auth, jobs, traces and team endpoints
puzzle_ops/renderer.py      local operations UI
alembic/                    PostgreSQL schema migrations
scripts/                    startup, smoke, evaluation and release checks
docs/eval/                  aggregate evaluation reports
docs/assets/readme/         sanitized public screenshots and diagrams
```

## Documentation

Recommended reading:

- [GitHub Showcase](docs/GITHUB_SHOWCASE.md)
- [Implementation Notes](docs/IMPLEMENTATION_NOTES.md)
- [Project Defense](docs/PROJECT_DEFENSE.md)
- [Demo Walkthrough](docs/DEMO_WALKTHROUGH.md)
- [Architecture](docs/ARCHITECTURE.md)
- [FastAPI API Spec](docs/API_SPEC.md)
- [Deployment Guide](docs/DEPLOYMENT.md)
- [Evaluation Report](docs/EVAL_REPORT.md)
- [Interview Notes](docs/INTERVIEW_NOTES.md)
- [Resume Project Brief](docs/RESUME_PROJECT_BRIEF.md)
- [Security Release Checklist](docs/SECURITY_RELEASE_CHECKLIST.md)
- [Security Policy](SECURITY.md)

Deep dives:

- [Online Acceptance v0.7.70](docs/final_acceptance/v0.7.70_online_acceptance_report.md)
- [Resume Closure v0.7.49](docs/final_acceptance/v0.7.49_resume_closure_report.md)
- [Value Master Eval](docs/eval/value_master_eval_report.md)
- [RAG Hard-negative Report](docs/eval/rag_hard_negative_report.md)
- [Visual Similarity Confidence Policy](docs/eval/visual_similarity_confidence_policy_report.md)

## Security Boundary

- `.env`、真实 API key、token、飞书 URL、RDS/OSS/Milvus 凭证不会提交。
- 公开 GIF 和截图使用隔离的 synthetic demo runtime；真实图片及行级 CSV 不进入仓库。
- GitHub 发布前运行 `scripts/release_preflight.py`，检查被跟踪的环境文件、密钥模式和绝对路径。
- API token 只存 hash；写操作记录 actor、role、country 和 audit event。
- 页面、API 和 worker 共用 provider health 与失败状态，不把 provider 异常伪装成业务成功。

## Limitations

- This is an engineering Agent Harness with small-sample validation, not a claim of large-scale production prediction accuracy.
- Real images, raw row-level business data, Feishu URLs and credentials are intentionally excluded from the public repository.
- RAG citations, value predictions and visual similarity are decision-support evidence; HITL remains required for business actions.
- FastAPI is the service layer for a small team, but production deployment still requires HTTPS, secrets governance, firewall, backups and provider monitoring.
