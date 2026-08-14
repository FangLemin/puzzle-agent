# PuzzleOps Evaluation Report

日期：2026-08-04

本报告用于把分散在 `docs/eval/` 和 `docs/final_acceptance/` 的评测结果收口成简历和面试可用的证据口径。

上线工程化收口请看：`docs/final_acceptance/v0.7.70_online_acceptance_report.md`。该报告覆盖 PostgreSQL/Alembic、OSS、Redis/RQ、FastAPI、`/api/metrics/dashboard`、smoke 命令、安全边界和上线限制。

## 1. 真实评测集

来源：`docs/eval/gold_dataset_summary.md`。原始行级 CSV 含真实业务字段和本机图片路径，作为私有评测资产保留，不随公开仓库发布。

- 真实样本：45 条。
- 国家分布：日本 25 条，法国 20 条。
- 等级覆盖：S/A/B/C/D。
- gold label 覆盖：主体、色彩氛围、构图环境、价值观标签、风险标签、真实等级、开图率、完成率、平均完成时长。

面试口径：

> 我没有把合成数据当作效果证明，而是单独整理真实小样本评测集，用来评估价值观大师、RAG citation、历史依据和图像相似检索。

## 2. 价值观大师评测

来源：`docs/eval/value_master_eval_report.md`。

- 真实样本数：45/50。
- 三段式描述合规率：100%。
- 飞书字段完整率：100%。
- 工具调用成功率：100%。
- SA 二分类准确率：60%。
- 指标反推等级基线准确率：18%。

解释：

- 工程链路已经可跑通：字段、提需、同步、trace 和评测都有自动化覆盖。
- 指标反推等级效果差，因此当前项目没有把三项指标作为主等级预测口径。
- 价值观预测仍需要更多真实样本和 prompt/RAG/history evidence 迭代。

## 3. RAG 检索评测

来源：`docs/eval/rag_hard_negative_report.md`。

- Hit@5：100%。
- MRR@5：97%。
- NDCG@5：98%。
- Precision@5：20%。
- Recall@5：100%。
- Hard-negative TopK 率：22%。

解释：

- 系统能召回 expected 文档，说明规则入库、chunk、召回链路是可用的。
- Precision@5 偏低，说明 TopK 中存在同国异主体 hard-negative。
- 当前已做 citation 强相关过滤和 Top3 截断，但不应声称 RAG 依据完全稳定。

## 4. Prompt Benchmark

来源：`docs/eval/value_master_prompt_benchmark_v2_report.md`。

- 人工评分样本数：35。
- 视觉解析均分：4.00/5。
- RAG citation 有用性均分：1.60/5。
- 历史依据合理性均分：1.90/5。
- 预测等级可信度均分：1.90/5。
- 指标区间可信度均分：2.00/5。

解释：

- Qwen VLM 看图解析相对可用。
- 主要问题在 RAG citation 质量、历史依据排序和指标标定。
- 项目价值是用 Harness 把这些问题拆出来，而不是直接宣称模型表现稳定。

## 5. 图像相似检索评测

来源：

- `docs/eval/visual_embedding_smoke_report.md`
- `docs/eval/visual_similarity_eval_report.md`
- `docs/eval/visual_similarity_gold_eval_report.md`
- `docs/eval/visual_similarity_threshold_calibration_report.md`
- `docs/eval/visual_similarity_confidence_policy_report.md`

关键结果：

- Qwen visual embedding smoke：4 张图调用成功，embedding 维度 2560。
- 人工 TopK 标注：30 条。
- Gold Hit@5：0.6667。
- Gold MRR：0.2778。
- Gold NDCG：0.3843。
- Gold Precision：0.2472。
- Bad Match 率：1.0。

阈值校准结论：

- 当前不适合上线硬阈值。
- 相关样本最高分低于部分不相关样本最高分，score 与人工相关性不单调。
- 因样本少，低分 TopK 不应强行当作历史依据展示。

v0.7.58 产品策略：

- 当通过 gate 的历史图最高相似分低于校准提示线时，页面显示“暂无可靠历史相似图”。
- 低置信历史图不注入价值观大师 LLM。
- 相似图证据只做辅助，不改变主等级预测。

## 6. 自动化回归

v0.7.58 全量回归：

```text
596 passed
```

默认回归命令关闭远程模型和向量库调用：

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

## 7. 简历可写点

可以写：

- 设计并实现面向出海拼图运营的多模态 Agent Harness。
- 接入 Qwen VLM 完成图片主体、色彩氛围、构图环境解析。
- 构建价值观/审核 RAG，支持 chunk、父子文档、多路召回、rerank、citation 溯源。
- 设计四层 Memory 治理：感知、短期、长期、结构化事实，并支持审批、冲突、过期和 RAG 准入。
- 接入 Qwen3-VL-Embedding + Milvus/Zilliz 做历史图像相似检索，并用人工标注评估低置信策略。
- 建立 45 条真实拼图小样本评测集和 Harness 报告，暴露 RAG、历史依据、指标标定问题。
- 飞书同步真实落地，支持字段写入和图片附件上传。

不要写：

- 不要写“大规模线上稳定预测”。
- 不要写“价值观预测准确率很高”。
- 不要写“图像相似检索已达到生产级效果”。

更稳妥的表达：

> 项目已完成从多模态理解到飞书落地的 Agent 工程闭环，并通过真实小样本 Harness 量化暴露 RAG citation、历史依据排序和指标标定问题，为后续 prompt/post-training 提供数据依据。
