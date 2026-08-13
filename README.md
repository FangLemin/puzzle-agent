# PuzzleOps Agent

PuzzleOps Agent 是一个面向日本/法国出海拼图内容运营的 Agent Harness 项目。它不是单次 prompt demo，而是把“看图理解、价值观判断、审核风险、好图衍生、飞书提需、人工修正、评测回放”串成可验证的运营工作流。

当前版本适合作为简历第二项目展示，推荐表述为：

> 构建了一个面向拼图内容运营的多模态 Agent Harness，覆盖 Qwen 视觉解析、价值观/审核 RAG、四层 Memory、图像相似检索、HITL 标注、真实小样本评测和飞书落地闭环。

不要夸大为“大规模线上预测系统”。当前真实评测集是小样本工程验证，核心价值是把业务流程、模型调用、检索依据、人工反馈和评测报告做成闭环。

## 当前能力

- 本地运营后台：纯 Python 标准库 HTTP 服务，无 Java/Node/Vue/React 前端构建栈。
- 常规提需：按国家、JS 分类、运营 tag 和库存生成提需表，可编辑后同步飞书。
- 试新提需：上传 1-3 张参考图，调用 Qwen VLM 解析主体内容、色彩氛围、构图环境，生成三段式业务描述。
- 好图衍生：已抽象 `ImageGenerationProvider`，支持 DashScope/通义万相 provider；生成图必须二次 VLM 解析、审核复检、人工确认后才可进提需。
- 价值观大师：结合当前图片证据、国家价值观 RAG、审核规则、历史样本和图像相似证据输出等级、风险和排图建议。
- RAG：支持价值观规则、审核规则、approved memory、真实 gold 样本的 chunk 化、父子文档、多路召回、rerank、citation 拼接。
- Memory：按感知记忆、短期记忆、长期记忆、结构化事实管理，并有审批、冲突、过期、RAG 准入和命中回写。
- 图像相似检索：接入 Qwen3-VL-Embedding provider，Milvus/Zilliz image embedding collection，低置信时显示“暂无可靠历史相似图”。
- Agent Harness：真实样本、synthetic demo、case trace、failure category、HITL 修正、RAG/视觉相似/价值观评测报告。
- 飞书落地：支持真实飞书多维表格写入和附件上传；未配置字段或 token 时不伪造成功。

## 快速启动

单人本地页面：

```bash
cd /Users/fanglemin/Desktop/puzzle-agent-python/.worktrees/multimodal-agent-runtime
PYTHONPATH=. python -c 'from puzzle_ops.server import run; run(port=5199)'
```

打开：

```text
http://127.0.0.1:5199/?view=dashboard
```

左侧导航会切换所有功能页，包括常规提需、试新提需、数据分析大师、价值观大师、多模态底座、Agent 评测、排图工作台和同步记录。`?view=trial`、`?view=value`、`?view=runtime`、`?view=eval` 都是同一个本地服务的不同页面，不是不同项目。

## 推荐生产环境变量

复制 `.env.example` 为 `.env`，只在本地或服务器填写真实密钥，不要提交 `.env`：

```bash
cp .env.example .env
```

关键配置：

```bash
PUZZLEOPS_PRODUCTION_MODE=true
PUZZLEOPS_RUNTIME_DIR=/Users/fanglemin/Desktop/puzzle_ops_runtime_prod
PUZZLEOPS_WRITE_COUNTRIES=日本,法国

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

飞书需要 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_SPREADSHEET_TOKEN`、`FEISHU_SHEET_RANGE`。图片附件字段必须是飞书多维表格附件字段；如果字段不存在或类型不匹配，系统会保留提需并显示飞书原始错误。

## 6 人团队 FastAPI 服务

当前 `http://127.0.0.1:5199` 仍适合你本机单人使用。6 人小组共用时，使用 FastAPI 服务层：

- `GET /docs`：Swagger/OpenAPI 文档。
- `GET /api/health`：服务、版本、provider、向量库、飞书配置健康检查。
- `POST /api/rag/search`：价值观/审核 RAG 检索，返回 citation 和 trace。
- `POST /api/value/analyze`：候选图价值观分析。
- `GET /api/harness/summary`：真实评测集和最近 run 摘要。
- `POST /api/visual-similarity/search`：候选图图搜图历史依据。
- `GET /api/me`：查看当前 token 对应用户、角色、国家权限。
- `POST /api/jobs/*`：创建 VLM 解析、好图衍生、飞书同步、RAG 重建任务。
- `GET /api/traces/{trace_id}`：回查价值观/RAG/任务链路 trace。
- `GET /api/metrics/latency`：查看 P50/P95/P99 延迟。
- token 权限控制：运营只调用分析/提需，管理员才可同步飞书和重建索引。

启动 API：

```bash
cd /Users/fanglemin/Desktop/puzzle-agent-python/.worktrees/multimodal-agent-runtime
PYTHONPATH=. uvicorn puzzle_ops.api:app --host 127.0.0.1 --port 8000
```

本机打开：

```text
http://127.0.0.1:8000/docs
```

局域网 6 人测试时，把 host 改成 `0.0.0.0`，并确保服务器防火墙只允许可信网络访问：

```bash
PYTHONPATH=. uvicorn puzzle_ops.api:app --host 0.0.0.0 --port 8000
```

也可以使用脚本启动和验收：

```bash
PUZZLEOPS_API_TOKENS='ops_jp:jp_token:operator:日本,ops_fr:fr_token:operator:法国,admin:admin_token:admin:日本|法国' ./scripts/run_api.sh
PUZZLEOPS_API_TOKEN=jp_token ./scripts/smoke_api.sh
```

当前 FastAPI 第一版没有开放真实飞书写接口，避免多人共用时误写生产表。飞书同步仍走现有页面的人工确认链路。详细接口见 [docs/API_SPEC.md](docs/API_SPEC.md)。

### 正式上线主库与异步任务

本地 demo 默认继续使用 SQLite：

```bash
PUZZLEOPS_DB_PROVIDER=sqlite
```

6 人上线建议切到阿里云 RDS PostgreSQL：

```bash
PUZZLEOPS_DB_PROVIDER=postgres
DATABASE_URL=postgresql+psycopg://user:password@host:5432/puzzleops
```

初始化/验收 RDS：

```bash
DATABASE_URL=postgresql+psycopg://user:password@host:5432/puzzleops alembic upgrade head
DATABASE_URL=postgresql+psycopg://user:password@host:5432/puzzleops PUZZLEOPS_INIT_DB=1 python scripts/smoke_postgres.py
```

也可以不用 Alembic CLI，直接执行：

```bash
DATABASE_URL=postgresql+psycopg://user:password@host:5432/puzzleops python scripts/init_postgres_schema.py
```

图片不再依赖本机路径，建议上传到阿里云 OSS：

```bash
ASSET_STORAGE_PROVIDER=oss
ALIYUN_OSS_ENDPOINT=https://oss-cn-xxx.aliyuncs.com
ALIYUN_OSS_BUCKET=puzzleops-assets
ALIYUN_OSS_ACCESS_KEY_ID=...
ALIYUN_OSS_ACCESS_KEY_SECRET=...
ALIYUN_OSS_PUBLIC_BASE_URL=https://assets.example.com
```

慢任务由 worker 消费，API 只创建 job：

```bash
./scripts/run_worker.sh
```

生产语义：PostgreSQL 存 users、tokens、audit logs、assets、jobs、trace events 和业务主数据；Milvus/Zilliz 只存向量；OSS 存图片；Redis/RQ 用于异步队列，当前本地 worker 提供无 Redis fallback。

## 上线评测结果

正式 RAG release report 输出到：

```text
docs/eval/rag_release_report.md
docs/eval/rag_release_report.json
```

报告覆盖 MRR@5、NDCG@5、Precision@5、Recall@5、citation usable rate、hard-negative rate、日本/法国拆分和已知限制。真实样本仍偏小，报告用于上线验收和面试说明，不能声称大规模生产稳定性。

## 架构与评测文档

- [系统架构](docs/ARCHITECTURE.md)
- [FastAPI API Spec](docs/API_SPEC.md)
- [部署与 6 人验收](docs/DEPLOYMENT.md)
- [安全发布 Checklist](docs/SECURITY_RELEASE_CHECKLIST.md)
- [面试复习讲稿](docs/INTERVIEW_NOTES.md)
- [简历项目摘要](docs/RESUME_PROJECT_BRIEF.md)
- [评测报告汇总](docs/EVAL_REPORT.md)
- [最终收口报告 v0.7.49](docs/final_acceptance/v0.7.49_resume_closure_report.md)
- [价值观大师评测](docs/eval/value_master_eval_report.md)
- [RAG hard-negative 报告](docs/eval/rag_hard_negative_report.md)
- [视觉相似低置信策略](docs/eval/visual_similarity_confidence_policy_report.md)

## 核心代码

- `puzzle_ops/server.py`：Python 标准库本地 HTTP 服务。
- `puzzle_ops/renderer.py`：服务端渲染页面。
- `puzzle_ops/agents.py`：Agent 编排、价值观大师、提需、RAG、Memory、Harness 对接。
- `puzzle_ops/rag.py`：RAG 文档、chunk、embedding、rerank、Milvus/Qdrant 适配。
- `puzzle_ops/visual_similarity.py`：Qwen3-VL-Embedding、Milvus/Zilliz 图像相似检索和评测。
- `puzzle_ops/vision_llm.py`：Qwen/OpenAI 视觉 LLM provider。
- `puzzle_ops/image_generation.py`：DashScope/Mock 图像生成 provider。
- `puzzle_ops/storage.py`：SQLite 主数据和 Memory。
- `puzzle_ops/production_db.py`：PostgreSQL 主库 schema 与 repository 工厂。
- `puzzle_ops/assets.py`：本地/阿里云 OSS 图片对象存储 provider。
- `puzzle_ops/worker.py`：异步 job 执行入口，供 Redis/RQ worker 或本地 fallback 使用。
- `puzzle_ops/feishu.py`：飞书同步和附件上传。
- `puzzle_ops/harness.py`：Agent Harness 数据结构与运行器。
- `tests/`：自动化测试。

## 验证

GitHub 公开或上线前先跑安全预检：

```bash
python scripts/release_preflight.py
```

默认测试关闭远程模型和向量库调用，避免产生费用：

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

v0.7.58 全量回归结果：`596 passed`。每次版本修改都维护 `VERSION`、`CHANGELOG.md`，并保留 git commit。

## 当前边界

- 真实样本规模仍偏小，图像相似检索和价值观预测不能声称达到线上稳定准确率。
- 图像相似分数目前用于 evidence 辅助，不直接改主等级预测。
- RAG 能召回 expected 文档，但 TopK 中仍可能混入同国异主体 hard-negative。
- Memory 存 SQLite 是因为它保存结构化事实、审批状态、冲突和审计日志；Milvus/Zilliz 只负责向量检索，不适合承载事务型 memory 治理。
- FastAPI 已提供多人 API 第一版，但真实飞书写接口暂未开放；生产部署前仍需配置 HTTPS、token、服务器防火墙和运行目录备份。
