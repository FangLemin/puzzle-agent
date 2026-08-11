# PuzzleOps Agent Interview Notes

日期：2026-08-11

这份文档用于面试前复习。目标是把项目讲清楚，而不是背流水账。

## 1. 一句话介绍

我做了一个面向日本/法国出海拼图内容运营的多模态 Agent Harness。它把 Qwen 看图解析、国家价值观 RAG、审核规则、四层 Memory、历史图像相似检索、HITL 人工修正、飞书同步和 FastAPI 服务化串成一个可评测、可回放、可落地的运营工作流。

## 2. 为什么做这个项目

业务里运营每天要判断大量拼图素材是否适合日本/法国市场。难点不是单纯写描述，而是要同时看主体、色彩、构图、国家文化价值观、审核风险、历史好坏图表现和排图策略。人工判断容易慢、不一致，也很难沉淀成可复用经验。

所以这个项目的核心目标是：把内容运营判断过程工程化，让 Agent 能提供依据，运营能人工复核，系统能记录评测结果，下一轮继续改进。

## 3. 系统架构

主链路：

```text
本地页面 / FastAPI
  -> PuzzleOpsAgent
  -> Qwen VLM 图片解析
  -> RAG 召回价值观和审核规则
  -> Memory 读取 approved 经验
  -> 图像相似检索找历史依据
  -> 输出提需/价值观判断/风险/建议
  -> HITL 人工确认
  -> 飞书同步或 Harness 评测
```

代码分层：

- `puzzle_ops/server.py`：本地 5199 页面。
- `puzzle_ops/api.py`：FastAPI 服务层。
- `puzzle_ops/agents.py`：核心业务编排。
- `puzzle_ops/rag.py`：RAG chunk、召回、rerank、Milvus 适配。
- `puzzle_ops/storage.py`：SQLite 主数据和 Memory。
- `puzzle_ops/visual_similarity.py`：Qwen3-VL-Embedding 图像相似检索。
- `puzzle_ops/harness.py`：Agent Harness 评测。
- `puzzle_ops/feishu.py`：飞书同步。

## 4. 多模态理解

试新和价值观大师都依赖 Qwen VLM 解析图片。业务标准要求输出三段：

- 主体内容
- 色彩氛围
- 构图环境

没有视觉 LLM 时，系统只做本地像素层解析，不声称识别真实主体。接入 Qwen 后，才会真正解析主体、场景、文化元素和风险。

## 5. RAG

RAG 的作用不是闲聊问答，而是给价值观大师和审核判断提供可溯源依据。

离线阶段：

- 加载日本/法国价值观、审核风险规则、approved memory、真实 gold 样本。
- 按语义边界切 chunk，目标 chunk size 约 600 token，overlap 约 100 token。
- 保存父子文档 metadata。
- SQLite 保存原文、metadata 和 BM25 字段。
- Milvus/Zilliz 保存向量，用于语义检索。

在线阶段：

- 根据国家、主体、场景、运营 tag、风险词构造 query。
- BM25 召回关键词相关 chunk。
- 向量检索召回语义相关 chunk。
- qwen3-rerank 或本地 rerank 精排。
- Top citation 拼接进 prompt。
- Prompt 要求模型只基于 citation 和当前图像证据回答，资料不足时必须提示人工复核。

当前 RAG 评测结果显示：Hit@5 和 Recall 较好，但 Precision@5 偏低，说明能召回 expected，但 TopK 里仍有同国异主体 hard-negative。

## 6. Memory

Memory 不放在 Milvus 里做主存储，因为 Memory 是业务状态，不只是向量。

四层 Memory：

- 感知记忆：VLM 观察和图片解析。
- 短期记忆：当前任务状态、生成 trace、临时上下文。
- 长期记忆：运营确认后的价值观和稳定经验。
- 结构化事实：主体、国家、运营 tag、图片路径、gold label、业务指标。

治理机制：

- `approved_for_rag`
- `review_status`
- `status`
- 过期时间
- 冲突组
- RAG 命中回写

只有 approved、active、未过期、无冲突的 Memory 才能进入 RAG。

## 7. 图像相似检索

历史依据图片不相关是价值观大师的核心问题之一，所以项目加入了 Qwen3-VL-Embedding + Milvus/Zilliz 的图搜图能力。

流程：

```text
历史真实图 -> image embedding -> Milvus image collection
候选图 -> image embedding -> topK search
topK -> 规则 gate -> confidence policy -> evidence
```

由于样本少，v0.7.58 做了低置信策略：如果相似分整体低，不强行展示 TopK，而是显示“暂无可靠历史相似图”，也不把低置信历史图注入 LLM。

## 8. Agent Harness

Harness 是这个项目简历叙事里最关键的部分。它让 Agent 输出不是一次性 demo，而是可以评测、回放和对比。

核心对象：

- `EvalSample`：真实图片、国家、gold subject、gold color、gold composition、gold grade、业务指标。
- `HarnessRun`：版本、模型、数据集、指标、失败样本。
- `HarnessCaseResult`：输入、输出、tool calls、trace、scores、failure reasons、human override。

评测维度：

- 三段式描述合规率。
- 飞书字段完整率。
- 工具调用成功率。
- RAG Hit/MRR/NDCG/Precision/Recall。
- 图像相似 Hit@5/MRR。
- 人工 Prompt Benchmark。
- 失败样本分类。

## 9. FastAPI

原来的 `127.0.0.1:5199` 只适合单人本机使用。6 人运营团队需要服务化入口，所以 v0.7.60-v0.7.61 加了 FastAPI。

核心接口：

- `/docs`
- `/api/health`
- `/api/rag/search`
- `/api/value/analyze`
- `/api/harness/summary`
- `/api/visual-similarity/search`

权限：

- Bearer token。
- `viewer/operator/admin`。
- 按国家限制，例如日本运营不能访问法国数据。

飞书写入接口没有开放到 API，因为这是高风险动作，当前仍走 5199 页面人工确认。

## 10. 飞书

飞书是工具落地层。提需表同步时会写真实字段和图片附件。关键点：

- 未配置飞书时不假装成功。
- 字段不存在时保留飞书原始错误。
- 图片附件必须先上传获得 `file_token`，再写入附件字段。
- 同步失败不清空本地提需表。

## 11. 评测结果

可讲的结果：

- 45 条真实拼图小样本，日本 25 条，法国 20 条。
- 三段式描述合规率：100%。
- 飞书字段完整率：100%。
- 工具调用成功率：100%。
- RAG hard-negative 报告：Hit@5 100%，MRR@5 97%，NDCG@5 98%，Precision@5 20%。
- 图像相似人工 gold：Hit@5 0.6667，MRR 0.2778。
- v0.7.62 全量测试：610 passed。

解释口径：

这些结果证明工程链路、评测闭环和问题诊断能力，不证明模型已经达到线上稳定预测准确率。

## 12. 不足

必须诚实说：

- 真实样本只有 45 条，不能代表大规模线上分布。
- RAG Precision@5 偏低，citation 有时仍不够准。
- 图像相似检索受样本数量影响大。
- 价值观预测不能完全替代运营判断。
- FastAPI 第一版没有独立登录页，token 来自环境变量。
- 飞书写接口暂未开放到 API。

## 13. 面试追问

### 为什么叫 Agent Harness？

因为它不只是调用一次模型，而是把输入、工具调用、RAG citation、Memory、输出、人工修正和评测指标都记录下来，支持版本对比和失败复盘。

### RAG 为什么不是只用向量检索？

业务规则里很多关键词很重要，比如国家、主体、风险、IP、宗教政治。向量检索能补语义相似，但 BM25 更适合精确词面召回，所以用了多路召回加 rerank。

### 为什么 Memory 用 SQLite，不用 Milvus？

Memory 要保存审批状态、冲突、过期、来源、审计日志和结构化字段，这是事务型数据。Milvus 适合向量召回，不适合作为 Memory 主库。

### 为什么不直接训练模型？

因为错误可能来自 prompt、RAG、历史依据、指标标定或样本不足。先用 Harness 拆问题，再判断是否需要 post-training，否则容易把工程问题误判成模型能力问题。

### FastAPI 解决了什么？

解决 6 人 6 台电脑不能共用 `127.0.0.1` 的问题。FastAPI 把 Agent 能力服务化，并提供 `/docs`、token 权限和国家隔离。

### 为什么飞书写 API 暂缓？

飞书写入是生产高风险动作。第一版 API 先开放只读和分析能力，真实写入仍保留人工确认链路，避免多人误写。

## 14. 简历一句话

面向日本/法国拼图内容运营，设计并实现多模态 Agent Harness，接入 Qwen VLM、价值观/审核 RAG、四层 Memory、Qwen3-VL-Embedding 图像相似检索、FastAPI 服务化和飞书同步；构建 45 条真实小样本评测集，沉淀 RAG/视觉相似/价值观判断评测报告，并通过 610 个自动化测试保障回归。
