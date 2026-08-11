# Resume Project Brief

## 项目名称

PuzzleOps Agent：出海拼图内容运营多模态 Agent Harness

## 简历推荐写法

面向日本/法国拼图内容运营场景，设计并实现多模态 Agent Harness，覆盖 Qwen VLM 图片解析、价值观/审核 RAG、四层 Memory、Qwen3-VL-Embedding 图像相似检索、HITL 人工反馈、FastAPI 服务化和飞书提需落地。构建 45 条真实拼图小样本评测集，输出 RAG、视觉相似、价值观判断和部署安全报告，并以 610 passed 自动化测试保障版本迭代。

## 可量化成果

- 45 条真实拼图小样本：日本 25 条，法国 20 条。
- 三段式描述合规率：100%。
- 飞书字段完整率：100%。
- 工具调用成功率：100%。
- RAG hard-negative：Hit@5 100%，MRR@5 97%，NDCG@5 98%，Precision@5 20%。
- 图像相似人工 gold：Hit@5 0.6667，MRR 0.2778。
- 最新全量回归：610 passed。

## 技术关键词

- 多模态 Agent Harness
- Qwen VLM
- RAG：chunk、父子文档、BM25、向量检索、rerank、citation
- Memory：感知记忆、短期记忆、长期记忆、结构化事实
- Milvus / Zilliz
- Qwen3-VL-Embedding
- FastAPI
- HITL
- 飞书多维表格同步
- pytest 自动化评测

## 面试讲法

这个项目不是训练一个模型，而是把真实内容运营流程工程化。Agent 会看图、召回国家价值观和审核规则、参考历史样本和图像相似依据，生成提需和价值观判断；运营可以人工修正，修正结果进入 Memory 和 Harness，下一轮继续评测改进。

## 不要写

- 不要写“大规模线上稳定预测”。
- 不要写“完全自动替代运营判断”。
- 不要写“价值观预测准确率已稳定达到生产级”。
- 不要把 45 条真实拼图小样本包装成大规模线上数据集。

## 当前不足

- 真实样本量仍小。
- RAG Precision@5 偏低，citation 仍需继续治理。
- 图像相似检索受历史图片规模影响明显。
- FastAPI 第一版没有独立登录页。
- 飞书写入 API 暂缓开放，仍走页面人工确认。

## 推荐回答

如果面试官问“这个项目最有价值的地方是什么”，回答：

> 最大价值不是某个 prompt，而是构建了一个可评测的业务 Agent Harness。它把多模态理解、RAG 依据、Memory、工具调用、HITL 和飞书落地串成闭环，并用真实小样本评测暴露 RAG citation、历史依据和指标标定问题，为后续 prompt 优化或 post-training 提供依据。
