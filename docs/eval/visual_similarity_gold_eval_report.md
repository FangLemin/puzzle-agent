# PuzzleOps Visual Similarity Human Gold Eval Report

## 结论

- 本报告是人工 gold eval，基于运营人工标注的 TopK 历史依据相关性。
- 它用于判断图像相似检索和 relevance gate 是否真的改善历史依据质量。

## 标注概览

- 行数：30
- 候选图数：6
- 相关：5
- 不相关：19
- 不确定：6

## 指标

- Hit@5：67%
- MRR@5：28%
- NDCG@5：38%
- Precision@5：25%
- Recall@5：67%
- Bad Match Rate@5：100%
- Gate Precision：0%
- Gate Recall：0%

## 未命中样例

- 法国｜常规_法国_甜品店橱窗0704：相关排名=无，Top1 判定=不相关
- 法国｜常规_法国_香水0715：相关排名=无，Top1 判定=不相关

## 限制

- `manual_relevance=1` 作为相关，`0` 作为不相关，`unsure` 不计入相关/不相关分母。
- 本报告评估 TopK 历史依据相关性，不直接评价价值观大师最终等级预测。
- 当前样本量较小，适合作为调 gate 和检索策略的第一版人工 gold 基线。
