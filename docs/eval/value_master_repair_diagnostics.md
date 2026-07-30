# PuzzleOps Value Master Repair Diagnostics

## 结论

- 当前为 shadow diagnostics，不直接改线上预测等级。
- 不直接改线上预测等级，避免再次破坏用户已认可的相对稳定版本。
- 样本数：45；人工 Benchmark：35。

## 阻塞项

- metric_baseline_grade_accuracy：failed，value=0.1778，threshold=0.55；三项指标反推等级效果弱，不能作为价值观大师主等级预测口径。
- history_evidence_fit_avg：failed，value=1.9，threshold=3.5；历史依据人工评分偏低，需要先做影子排序评测。
- rag_citation_usefulness_avg：failed，value=1.6，threshold=3.5；RAG citation 人工有用性偏低，需要 hard-negative 与 citation 过滤修复。
- grade_credibility_avg：failed，value=1.9，threshold=3.5；预测等级可信度人工评分偏低，需要 Prompt Benchmark v2，而不是直接训练。

## 安全实验

- 历史依据排序影子评测：只在报告中重排相似好坏图，不影响价值观大师线上预测缓存。 验收：history_evidence_fit_avg >= 3.5/5 后再考虑进入主链路。
- RAG citation hard-negative 修复：利用人工 not_useful citation feedback 给低质量 chunk 降权，并过滤弱引用。 验收：rag_citation_usefulness_avg >= 3.5/5，且 Recall@5 不明显下降。
- 等级预测 Prompt Benchmark v2：用固定 10-20 张候选图比较 Prompt v1/v2，不再用三项指标反推等级。 验收：grade_credibility_avg >= 3.5/5，SA 二分类不低于当前基线。

## 简历口径

- 当前可以写工程闭环、Harness、RAG/Memory/HITL，但不能写价值观预测高准确率。
- 简历指标应优先写数据集、三段式合规、飞书字段完整、工具调用稳定；价值观效果写为待优化 Benchmark。
