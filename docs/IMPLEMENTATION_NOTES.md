# PuzzleOps Agent Implementation Notes

这份文档用于面试时讲清楚 PuzzleOps Agent 的 why + how：为什么要做 Agent Harness，系统如何拆解多模态理解、RAG、Memory、HITL、Eval 和上线工程。

## 1. Business Context

出海拼图内容运营的核心问题不是“生成一句图片描述”，而是判断一张图是否适合某个国家市场、是否符合本地价值观、是否存在审核风险、是否能从历史好图/坏图中找到依据，并把最终提需落到飞书表格。

我把这个问题设计成 Agent Harness，而不是单次 prompt demo：

- 单次输出必须可追踪：VLM 输入、RAG citation、历史相似图、风险判断、最终建议都要有 trace。
- 模型输出必须可修正：运营可以改主体、色彩、构图、价值观理由和风险标签。
- 评测必须可回放：固定真实小样本和 synthetic demo，按版本比较指标和失败样本。
- 工具调用必须可落地：飞书、图片附件、OSS、Milvus、PostgreSQL 和异步任务都要在业务链路里有明确边界。

## 2. Agent Workflow

核心链路：

```text
上传/选择候选图
  -> Qwen VLM 解析主体、色彩、构图、环境、风险
  -> RAG 召回国家价值观与审核规则
  -> 图像相似检索召回历史好图/坏图
  -> Value Master 生成等级、SA 概率、指标区间、风险和排图建议
  -> HITL 人工编辑与确认
  -> DemandRow / 飞书同步 / Harness trace / Memory 回写
```

这个设计借鉴了 OpenHands、LangGraph、AutoGen 这类 Agent 项目的包装方式：把 agent execution、tool calls、trace、human feedback 和 eval 放在同一条闭环里，而不是只展示一个模型回答。

## 3. VLM Visual Parsing

为什么：运营提需要求图片解析必须贴近业务表达，不能写成泛泛 caption。试新提需的标准字段固定为三段：主体内容、色彩氛围、构图环境。

怎么做：

- Provider 层支持 Qwen/OpenAI fallback，公开仓库默认不带任何真实 key。
- 试新上传 1-3 张图后，优先调用 Qwen VLM；无远程配置时降级为本地像素特征和文件名规则。
- Prompt 要求输出短主体、色彩氛围、构图环境，并避免声称识别版权/IP。
- 价值观大师复用当前图像解析，但额外要求模型引用图像证据判断国家价值观。

边界：VLM 输出不直接视为最终运营结论；主体描述、运营 tag、价值观理由都保留人工编辑入口。

## 4. RAG: Offline and Online

为什么：日本/法国价值观与审核风险规则会沉淀成文档和运营经验，不能全部写死在 prompt。RAG 的职责是让模型基于可溯源规则回答，降低幻觉。

离线阶段：

- 文档来源：国家价值观规则、审核风险规则、approved memory、真实 gold 样本摘要。
- Chunk：按语义边界切分，保留父子文档关系；较长规则拆成可检索 chunk，并保留原文 metadata。
- Embedding：DashScope text embedding provider；本地测试可切 local provider，避免费用。
- 入库：Milvus/Zilliz 存向量，SQLite/PostgreSQL 存结构化文档元数据、审批状态和 memory 事实。

在线阶段：

- Query 构造：结合国家、JS 分类、主体、色彩氛围、构图环境、风险问题生成检索 query。
- 多路召回：向量检索负责语义相似，BM25/关键词规则负责精确命中价值观和审核词。
- Rerank：Qwen rerank provider 对候选 chunk 精排；本地测试可切 local rerank。
- Prompt 拼接：将问题、当前图片证据、精排 citation 和边界说明拼给 LLM，要求“只依据提供资料回答；资料不足则说明不足”。
- Trace：记录 query、citations、缺失率、latency、provider 和模型输出。

评测：RAG release report 覆盖 MRR@5、NDCG@5、Precision@5、Recall@5、hard-negative rate 和 citation usable rate。

## 5. Layered Memory

为什么：Memory 不是向量库的同义词。运营事实需要状态、审批、冲突处理和审计，不能只塞进 Milvus。

四层设计：

- 感知记忆：从上传图片、LLM 输出、操作行为中捕获候选事实。
- 短期记忆：当前任务/session 的上下文，用于连续操作和临时决策。
- 长期记忆：经过人工确认或多次命中的稳定规则。
- 结构化事实：国家、主题、风险、价值观标签、来源、置信度、审批状态等可查询字段。

落地方式：

- SQLite 用于本地 demo；PostgreSQL 用于多人上线。
- Milvus/Zilliz 只负责向量召回，不承载审批状态。
- 只有 approved memory 才进入 RAG ready pool。
- HITL 修正会写回 facts memory，成为下一轮评测与检索依据。

## 6. Harness and HITL

为什么：多模态 agent 的问题不一定是模型能力不足，可能来自 prompt、RAG citation、历史相似图、指标标定或规则文档。Harness 用来把这些问题拆开。

Harness 覆盖：

- `trial_parse_eval`：试新图片解析是否符合三段式标准。
- `value_match_eval`：价值观判断是否引用当前图像证据和国家规则。
- `audit_eval`：是否召回版权/IP、文化混淆、AI 质感等风险。
- `grade_predict_eval`：预测等级是否贴近人工直觉。
- `feishu_sync_eval`：字段完整性和同步稳定性。
- `derive_generation_eval`：生成图是否保留可复用视觉特征并通过复审。

HITL 设计：

- 运营可以修正主体、色彩、构图、价值观标签、风险标签和人工等级。
- 修正结果进入 gold dataset 和 memory，下一轮评测可复用。
- 失败样本保留 failure category，便于区分 RAG 问题、VLM 问题、history evidence 问题和指标标定问题。

## 7. Visual Similarity Evidence

为什么：只靠 VLM 文本解析和运营 tag 容易召回不相关历史图。图像相似检索用于找到“视觉上相似的历史好图/坏图”，辅助价值观判断。

怎么做：

- Qwen3-VL-Embedding provider 生成图片 embedding。
- Milvus/Zilliz image collection 存历史图片向量。
- 搜索结果经过相似度阈值和相关性 gate。
- 低于可信阈值时显示“暂无可靠历史相似图”，避免硬展示不相关依据。
- 人工 TopK 标注用于校准 gate precision/recall。

边界：当前真实样本仍少，图像相似 evidence 只辅助判断，不直接覆盖主等级预测。

## 8. FastAPI and Production Split

为什么：本地页面适合单人演示，6 人运营团队需要 API、权限、任务队列、对象存储和可观测性。

服务层：

- `/api/health`：provider、数据库、向量库、飞书配置。
- `/api/rag/search`：返回 citation 和 trace。
- `/api/value/analyze`：候选图价值观分析。
- `/api/harness/summary`：真实评测集和 run 摘要。
- `/api/visual-similarity/search`：图搜图历史依据。
- `/api/assets/upload`：图片资产上传。
- `/api/jobs/*`：创建慢任务。
- `/api/metrics/dashboard`：P50/P95/P99、provider health、任务成功率、citation 缺失率。

上线分工：

- PostgreSQL：事务型主库、用户、权限、memory、jobs、trace、assets metadata。
- Redis/RQ：异步消费 VLM、图像生成、飞书同步、RAG rebuild、embedding 入库。
- OSS：图片对象存储；数据库只保存 URL、object key、hash、Feishu file token。
- Milvus/Zilliz：向量检索，不替代主库。
- FastAPI token：viewer/operator/admin 和国家权限。

## 9. Evaluation Results

公开仓库只展示聚合指标：

- Real gold samples：45/50。
- 三段式描述合规率：100%。
- 飞书字段完整率：100%。
- 工具调用成功率：100%。
- RAG Hit@5：100%。
- RAG MRR@5：97%。
- RAG NDCG@5：98%。
- RAG Precision@5：20%。
- Prompt benchmark 发现：VLM 看图相对可用，RAG citation、历史依据排序和指标区间仍是主要优化点。

这些指标用于证明工程闭环和小样本评测能力，不用于声称大规模线上稳定准确率。

## 10. Known Limits

- 真实样本仍偏小，价值观预测不能夸大为生产级稳定模型。
- RAG 能召回 expected 文档，但 TopK 仍可能出现同国异主体 hard-negative。
- 图像相似检索受样本规模影响，低置信时应显示无可靠历史依据。
- 真实飞书同步依赖正确字段和附件类型；失败时系统保留 payload 和原始错误。
- 公开仓库不包含真实图片、raw CSV、飞书 URL、API key 或 `.env`。

