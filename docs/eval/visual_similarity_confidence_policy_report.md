# PuzzleOps Visual Similarity Confidence Policy Report

## 结论

- v0.7.58 将 v0.7.57 的校准结论接入 evidence 层。
- 当历史相似图最高分低于校准提示线时，页面显示“暂无可靠历史相似图”。
- 低置信历史图不再作为价值观大师 LLM 的具体证据。

## 策略

- 默认提示线：`0.1208`。
- 来源：v0.7.57 人工 gold 标注校准。
- 可通过 `VISUAL_SIMILARITY_MIN_REFERENCE_SCORE` 覆盖。
- 低置信时：
  - `status=low_confidence`。
  - `reliability=low_confidence`。
  - 清空 `similar_good/similar_risk`。
  - 保留 `message/best_score/min_reference_score` 给页面解释。

## 产品口径

- 当前历史库样本少，TopK 只是“相对最像”，不等于“真的像”。
- 低相似分不应该误导运营，也不应该污染 LLM 判断。
- 本版只影响证据展示和 LLM 上下文，不改变价值观大师等级预测主链路。

## 限制

- `0.1208` 不是最终线上硬阈值。
- 需要更多人工 TopK 标注后继续校准。
- 本版不做 rerank，不证明图像相似检索准确率提升。
