# Qwen visual embedding smoke

## 结论

- 状态：ok
- Provider：qwen-vl-embedding
- Model：qwen3-vl-embedding
- Embedded：4
- Dimension：2560
- Error：0

## 说明

- `status=ok` 表示当前 provider 是 Qwen visual embedding，并且样本成功生成向量。
- `status=skipped` 表示当前仍是 local fallback，本报告只验证导出链路，不代表真实 Qwen 效果。
- smoke 只验证连通性和向量维度，不替代人工 TopK 相关性评测。
