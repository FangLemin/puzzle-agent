# PuzzleOps Agent Project Defense

这份文档是 PuzzleOps Agent 的公开项目答辩稿。目标是帮助读者在较短时间内理解：项目为什么做、怎么设计、评测结果说明了什么、还有哪些边界。

## 1. 30 秒介绍

PuzzleOps Agent 是一个面向日本/法国出海拼图内容运营的多模态 Agent Harness。它把 Qwen VLM 看图解析、国家价值观与审核规则 RAG、四层 Memory、历史图像相似检索、HITL 人工修正、飞书提需落地和 FastAPI 服务层串成一条可追踪、可回放、可评测的运营工作流。

这个项目的重点不是单次生成图片描述，而是把运营判断过程工程化：每次判断都能看到图像证据、RAG citation、历史依据、模型输出、人工修正和评测结果。

## 2. 2 分钟架构介绍

业务入口有两类：

- 本地运营页面：面向单人演示和运营工作台，包含常规提需、试新提需、价值观大师、数据分析、Harness 评测、系统治理和同步记录。
- FastAPI 服务层：面向 6 人团队上线，提供 `/docs`、`/api/health`、`/api/rag/search`、`/api/value/analyze`、`/api/harness/summary`、`/api/visual-similarity/search`、`/api/jobs/*`、`/api/metrics/dashboard` 等接口。

核心链路：

```text
候选图/试新图
  -> Qwen VLM 解析主体、色彩、构图、环境和风险
  -> RAG 召回国家价值观、审核规则、approved memory、gold samples
  -> 图像相似检索召回历史好图/坏图 evidence
  -> Value Master 输出等级、风险、价值观理由和排图建议
  -> HITL 人工编辑和确认
  -> DemandRow / 飞书同步 / Harness trace / Memory 回写
```

数据与基础设施分工：

- PostgreSQL/SQLite 保存事务型数据、memory、jobs、trace、assets metadata。
- Milvus/Zilliz 保存 RAG vector 和 image embedding vector。
- OSS/local asset storage 保存上传图和生成图。
- Redis/RQ 处理 VLM、图像生成、飞书同步、RAG rebuild、embedding 入库等慢任务。

## 3. 为什么是 Agent Harness

如果只做一个 prompt，让模型判断“这张图适不适合日本市场”，很难知道错误来自哪里。错误可能来自：

- VLM 没看懂主体或场景。
- RAG 召回了不相关的价值观规则。
- 历史相似图不相关。
- 风险规则漏召回。
- 指标标定和等级预测不一致。
- 样本太少，无法支撑稳定判断。

所以项目选择 Agent Harness 主线：保存输入、工具调用、RAG citation、相似图 evidence、输出、人工修正、评测结果和失败类型。这样每轮迭代都能定位问题，而不是把所有错误都归因于模型能力。

## 4. VLM 图像理解

试新和价值观大师都需要真实图像理解。项目接入 Qwen VLM 后，要求模型输出业务标准的三段式解析：

- 主体内容
- 色彩氛围
- 构图环境

这比普通 caption 更贴近提需表。运营可以直接审阅并编辑主体描述、运营 tag 和价值观理由。

边界：

- 没有 VLM 配置时，只做本地像素特征和文件名 fallback，不声称识别真实主体。
- VLM 输出不是最终结论，仍保留人工确认。
- 不声称自动识别版权/IP，只输出风险提示和人工复核建议。

## 5. RAG 设计

RAG 的定位是给价值观大师和审核判断提供可溯源依据。

离线阶段：

- 文档来源包括日本/法国价值观规则、审核风险规则、approved memory、真实 gold 样本摘要。
- 文档按语义边界切 chunk，并保留父子文档 metadata。
- 向量进入 Milvus/Zilliz，结构化 metadata、审批状态和原文进入 SQLite/PostgreSQL。

在线阶段：

- Query 结合国家、JS 分类、主体、色彩、构图、风险词生成。
- 多路召回结合向量检索和关键词/BM25 逻辑。
- Qwen rerank 或 local rerank 对候选 chunk 精排。
- Top citation 和当前图像证据拼入 prompt，要求模型只基于资料回答，资料不足时提示人工复核。
- Trace 记录 query、citation、provider、latency 和输出摘要。

当前 RAG 评测显示 Hit@5、MRR@5、NDCG@5 较高，但 Precision@5 偏低，说明系统能召回 expected 文档，但 TopK 中仍会混入同国异主体 hard-negative。这是后续继续优化 citation quality 的重点。

## 6. Memory 设计

Memory 不直接用 Milvus 作为主存储，因为 Memory 是业务状态，不只是向量。

项目把 Memory 拆成四层：

- 感知记忆：来自图片解析、LLM 输出和运营操作的候选事实。
- 短期记忆：当前任务或 session 的临时上下文。
- 长期记忆：经过人工确认或多次验证的稳定经验。
- 结构化事实：国家、主体、风险、价值观标签、来源、置信度、审批状态等字段。

只有 approved、active、未过期、无冲突的 memory 才能进入 RAG ready pool。人工修正后的事实会进入下一轮 gold dataset 和 memory，形成运营经验沉淀。

## 7. 图像相似检索

价值观大师需要参考历史好图/坏图，但只靠文字 tag 容易召回不相关图片。因此项目加入 Qwen3-VL image embedding + Milvus/Zilliz 图搜图。

流程：

```text
历史真实图 -> image embedding -> Milvus image collection
候选图 -> image embedding -> TopK search
TopK -> confidence gate -> 可信历史 evidence
```

由于真实样本量仍小，项目加入低置信策略：如果相似分低，不强行展示历史图，而是显示“暂无可靠历史相似图”。这样能避免模型把不相关历史图片当成依据。

## 8. HITL 与飞书落地

HITL 是这个项目的必要设计，不是附加功能。

运营可以人工修正：

- 主体内容
- 色彩氛围
- 构图环境
- 风险标签
- 价值观理由
- 人工等级

修正结果会写入 gold dataset、memory 和后续评测。飞书同步也保留人工确认，避免多人团队误写生产表。

飞书侧关键处理：

- 不伪造同步成功。
- 字段不存在或类型不匹配时保留原始错误。
- 图片附件先上传获得 file token，再写入附件字段。
- 同步失败不清空本地提需表。

## 9. 评测结果与解释

公开仓库展示的是聚合指标，不发布真实图片、raw CSV 或飞书链接。

当前可说明的结果：

- 真实 gold samples：45（日本 25、法国 20）。
- 三段式描述合规率：100%。
- 飞书字段完整率：100%。
- 工具调用成功率：100%。
- RAG Hit@5：100%。
- RAG MRR@5：97%。
- RAG NDCG@5：98%。
- RAG Precision@5：20%。

这些结果说明：

- Agent 工作流、字段生成、工具调用、trace 和评测链路已经可跑通。
- RAG 能找到 expected 文档，但 citation 排序仍有 hard-negative 问题。
- VLM 解析相对可用，但价值判断仍需要人工复核。
- 真实样本规模不足以声称大规模生产稳定准确率。

## 10. 当前边界

项目公开时必须明确边界：

- 这不是大规模线上稳定预测系统，而是一个工程化 Agent Harness 和小样本评测闭环。
- 价值观大师输出是运营决策支持，不替代人工判断。
- 图像相似 evidence 受样本规模影响，低置信时不展示历史依据。
- 真实业务图片、raw CSV、飞书 URL、API key 和 `.env` 不进入公开仓库。
- FastAPI 已提供多人服务层，但生产部署仍需要 HTTPS、token 管理、防火墙、备份和 provider 凭证。

## 11. 5 分钟答辩结构

可以按下面顺序讲：

1. 业务问题：出海拼图运营要判断图片是否符合日本/法国市场价值观，并落到飞书提需。
2. 为什么不是普通 VLM caption：运营需要依据、风险、历史好坏图、人工修正和评测闭环。
3. 核心架构：VLM + RAG + Memory + visual similarity + HITL + Harness + Feishu/FastAPI。
4. RAG 与 Memory：RAG 做 citation grounding，Memory 管结构化事实和审批状态。
5. Harness：用真实小样本、case trace、failure category 和人工修正拆解错误来源。
6. 工程上线：PostgreSQL、Milvus/Zilliz、OSS、Redis/RQ、FastAPI token 和 metrics。
7. 评测与边界：链路可验证，但样本仍小，不夸大生产预测准确率。

## 12. 一句话总结

PuzzleOps Agent 的价值不是“让模型说这张图好不好”，而是把多模态内容运营判断做成一个能看依据、能人工修正、能沉淀记忆、能回放评测、能落到业务工具的 Agent Harness。

