# PuzzleOps Visual Similarity Eval Report

## 结论

- 本报告是自动 proxy eval，用于评估图像相似检索是否改善历史依据不相关问题。
- 指标只能作为上线前快速诊断，不替代人工 TopK 标注。

## 范围

- 国家：日本、法国
- Case 数：45
- TopK：5

## 指标

- Hit@5：24%
- MRR@5：14%
- NDCG@5：10%
- Precision@5：5%
- Recall@5：13%
- Bad Match Rate@5：91%
- Gate 后 Hit@5：4%
- Gate 后 MRR@5：4%
- Gate 后 NDCG@5：2%
- Gate 后 Bad Match Rate@5：9%

## 失败样例

- 日本｜常规_日本_猫咪鲤鱼0605：retrieved=6ba7b812-9dad-11d1-80b4-00c04fd430c20,6ba7b812-9dad-11d1-80b4-00c04fd430c25,6ba7b812-9dad-11d1-80b4-00c04fd430c21,6ba7b812-9dad-11d1-80b4-00c04fd430c24,6ba7b812-9dad-11d1-80b4-00c04fd430c8；relevant=6ba7b812-9dad-11d1-80b4-00c04fd430c11,6ba7b812-9dad-11d1-80b4-00c04fd430c19,6ba7b812-9dad-11d1-80b4-00c04fd430c23
- 日本｜常规_日本_寿司0521：retrieved=6ba7b812-9dad-11d1-80b4-00c04fd430c22,6ba7b812-9dad-11d1-80b4-00c04fd430c18,6ba7b812-9dad-11d1-80b4-00c04fd430c28,6ba7b812-9dad-11d1-80b4-00c04fd430c25,6ba7b811-9dad-11d1-80b4-00c04fd430c8；relevant=6ba7b812-9dad-11d1-80b4-00c04fd430c24
- 日本｜常规_日本_抹茶0405：retrieved=6ba7b812-9dad-11d1-80b4-00c04fd430c21,6ba7b812-9dad-11d1-80b4-00c04fd430c11,550e8400-e29b-41d4-a716-446655440000,6ba7b812-9dad-11d1-80b4-00c04fd430c28,6ba7b810-9dad-11d1-80b4-00c04fd430c8；relevant=无
- 日本｜常规_日本_樱花列车0605：retrieved=6ba7b812-9dad-11d1-80b4-00c04fd430c20,6ba7b812-9dad-11d1-80b4-00c04fd430c23,6ba7b811-9dad-11d1-80b4-00c04fd430c8,6ba7b812-9dad-11d1-80b4-00c04fd430c14,6ba7b812-9dad-11d1-80b4-00c04fd430c24；relevant=f47ac10b-58cc-4372-a567-0e02b2c3d479,6ba7b812-9dad-11d1-80b4-00c04fd430c26,6ba7b812-9dad-11d1-80b4-00c04fd430c27,6ba7b812-9dad-11d1-80b4-00c04fd430c28
- 日本｜常规_日本_二次元动漫少女0607：retrieved=6ba7b812-9dad-11d1-80b4-00c04fd430c25,6ba7b812-9dad-11d1-80b4-00c04fd430c20,6ba7b812-9dad-11d1-80b4-00c04fd430c23,6ba7b812-9dad-11d1-80b4-00c04fd430c28,6ba7b812-9dad-11d1-80b4-00c04fd430c11；relevant=6ba7b812-9dad-11d1-80b4-00c04fd430c15,6ba7b812-9dad-11d1-80b4-00c04fd430c16,6ba7b812-9dad-11d1-80b4-00c04fd430c17,6ba7b812-9dad-11d1-80b4-00c04fd430c22
- 日本｜常规_日本_幼猫0608：retrieved=6ba7b812-9dad-11d1-80b4-00c04fd430c13,6ba7b812-9dad-11d1-80b4-00c04fd430c8,6ba7b812-9dad-11d1-80b4-00c04fd430c12,6ba7b812-9dad-11d1-80b4-00c04fd430c10,6ba7b812-9dad-11d1-80b4-00c04fd430c19；relevant=550e8400-e29b-41d4-a716-446655440000,6ba7b812-9dad-11d1-80b4-00c04fd430c23
- 日本｜试新_日本_门球0509：retrieved=6ba7b812-9dad-11d1-80b4-00c04fd430c19,6ba7b812-9dad-11d1-80b4-00c04fd430c26,6ba7b812-9dad-11d1-80b4-00c04fd430c24,6ba7b812-9dad-11d1-80b4-00c04fd430c18,6ba7b812-9dad-11d1-80b4-00c04fd430c11；relevant=无
- 日本｜常规_日本_二木屐0510：retrieved=6ba7b811-9dad-11d1-80b4-00c04fd430c8,6ba7b812-9dad-11d1-80b4-00c04fd430c16,6ba7b812-9dad-11d1-80b4-00c04fd430c11,6ba7b812-9dad-11d1-80b4-00c04fd430c18,f47ac10b-58cc-4372-a567-0e02b2c3d479；relevant=6ba7b812-9dad-11d1-80b4-00c04fd430c19

## 限制

- 本报告是自动 proxy eval：相关性由同国家、主体 token、JS 分类和等级桶近似判断。
- 它用于上线前快速暴露明显不相关的历史图依据，不替代人工 TopK 标注。
- 本地 fallback embedding 只能验证链路，真实效果需要 Qwen3-VL-Embedding + 人工复核。
