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

## 6 人团队服务化路线

当前 `http://127.0.0.1:5199` 只适合你本机单人使用。6 人小组要共用，需要下一版引入 FastAPI 服务化入口：

- `GET /docs`：Swagger/OpenAPI 文档。
- `GET /api/health`：服务、版本、provider、向量库、飞书配置健康检查。
- `POST /api/rag/search`：价值观/审核 RAG 检索，返回 citation 和 trace。
- `POST /api/value/analyze`：候选图价值观分析。
- `GET /api/harness/summary`：真实评测集和最近 run 摘要。
- token 权限控制：运营只调用分析/提需，管理员才可同步飞书和重建索引。

详细设计见 [docs/API_SPEC.md](docs/API_SPEC.md)。v0.7.59 只完成接口设计和上线说明，FastAPI 运行时代码建议作为 v0.7.60 单独实现。

## 架构与评测文档

- [系统架构](docs/ARCHITECTURE.md)
- [FastAPI API Spec](docs/API_SPEC.md)
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
- `puzzle_ops/feishu.py`：飞书同步和附件上传。
- `puzzle_ops/harness.py`：Agent Harness 数据结构与运行器。
- `tests/`：自动化测试。

## 验证

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
- FastAPI 多人服务化入口尚未实现，v0.7.59 先完成 API 设计和部署口径。
