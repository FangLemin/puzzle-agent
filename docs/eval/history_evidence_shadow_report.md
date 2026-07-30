# PuzzleOps History Evidence Shadow Report

## 结论

- 当前是历史依据排序影子评测，不改主预测缓存，不改线上预测等级。
- 目标是先验证相似历史依据是否更相关，再决定是否进入价值观大师主链路。

## 指标

- Case 数：45
- Top1 改变率：69%
- 旧排序 Top1 主体重合率：0%
- 影子排序 Top1 主体重合率：11%

## Top 改变样例

- 550e8400-e29b-41d4-a716-446655440000：招财猫与锦鲤 -> 试新_日本_儿童节鲤鱼旗0527
- f47ac10b-58cc-4372-a567-0e02b2c3d479：游客 -> 常规_日本_金阁寺0623
- 6ba7b812-9dad-11d1-80b4-00c04fd430c8：抹茶 -> 试新_日本_门球0509
- 6ba7b812-9dad-11d1-80b4-00c04fd430c9：日本通勤电车JR -> 常规_日本_金阁寺0623
- 6ba7b812-9dad-11d1-80b4-00c04fd430c10：年轻日本女性 -> 试新_日本_儿童节鲤鱼旗0527

## 验收

- shadow_top1_subject_overlap_rate >= legacy_top1_subject_overlap_rate 且人工 history_evidence_fit_avg >= 3.5/5
- 继续停留在影子评测，不改主预测缓存。
