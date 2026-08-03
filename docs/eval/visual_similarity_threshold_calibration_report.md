# PuzzleOps Visual Similarity Threshold Calibration Report

## 结论

- 不建议上线硬阈值。
- 不建议上线硬阈值；当前样本显示 score 与人工相关性不单调，先作为低置信提示使用。
- 原因：相关样本最高分低于/不高于不相关样本最高分，说明当前 score 不能单独承担硬阈值判断。

## Score 分布

- 相关：count=5，min=0.0541，median=0.1208，max=0.1483，avg=0.1119
- 不相关：count=19，min=0.0727，median=0.1206，max=0.27，avg=0.1554
- 不确定：count=6，min=0.0739，median=0.0941，max=0.1352，avg=0.1009

## 候选阈值

- threshold=0.1208：precision=0.25，recall=0.6，隐藏不相关率=0.5263，误藏相关数=2
- threshold=0.1206：precision=0.2308，recall=0.6，隐藏不相关率=0.4737，误藏相关数=2
- threshold=0.1183：precision=0.2143，recall=0.6，隐藏不相关率=0.4211，误藏相关数=2
- threshold=0.0541：precision=0.2083，recall=1.0，隐藏不相关率=0.0，误藏相关数=0
- threshold=0.1074：precision=0.2，recall=0.6，隐藏不相关率=0.3684，误藏相关数=2

## 下一步

- 当前先用于页面提示“暂无可靠历史相似图”，不要直接作为价值观大师硬过滤条件。
- 增加真实样本后重新运行校准，再决定是否把阈值接入主链路。
