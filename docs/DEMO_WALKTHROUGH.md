# PuzzleOps Agent Demo Walkthrough

这份文档用于 GitHub 阅读和技术面试演示。目标是在 5 分钟内讲清业务问题、Agent 如何调用证据、为什么需要 Harness，以及哪些结果仍需谨慎解释。

> 安全边界：公开截图和 GIF 全部来自隔离的 synthetic demo runtime。真实业务图片、行级指标、飞书地址、API key 和本机生产运行目录不会出现在仓库中。

## 0:00-0:30 项目定位

打开 `http://127.0.0.1:5199/?view=dashboard`。

口语稿：

> PuzzleOps Agent 面向日本和法国拼图内容运营。它不是单次看图 Prompt，而是把 Qwen VLM、价值观与审核 RAG、四层 Memory、图像相似依据、HITL、Harness 评测和飞书落地串成一条可追踪工作流。

演示重点：左侧所有页面属于同一个应用；当前国家和权限贯穿后续操作。

## 0:30-1:30 试新图片解析

打开 `http://127.0.0.1:5199/?view=trial`。

![Trial parsing](assets/readme/ui-trial.png)

1. 选择“参考图解析提需”，上传 1-3 张演示图。
2. 说明 VLM 输出契约只包含主体内容、色彩氛围、构图环境。
3. 展示主体描述、运营 tag 和备注都可以由运营人工修改。
4. 说明好图衍生使用独立 generation provider；生成图需要二次 VLM 审核与人工确认。

面试追问入口：为什么不直接让 VLM 输出长 caption？因为业务字段需要短、稳定、可编辑，长描述会挤压提需表并污染 tag。

## 1:30-2:40 价值观大师与证据

打开 `http://127.0.0.1:5199/?view=value`。

![Value Master](assets/readme/ui-value-master.png)

1. 候选图先经过 Qwen VLM 解析主体、色彩、构图、文化元素和风险。
2. RAG 按国家和视觉事实召回价值观、审核规则和 approved memory。
3. 图像 embedding 检索历史好图/坏图；低于 gate 时显示“暂无可靠历史相似图”。
4. Value Master 输出等级、SA 潜力、风险和排图建议，并保留 citation 与 trace。

面试追问入口：为什么不把三项预测指标直接反推等级？真实 benchmark 中该基线只有 18%，会拖累原本相对可用的等级判断，因此它只作为辅助校准信号。

## 2:40-3:40 Harness 与 HITL

打开 `http://127.0.0.1:5199/?view=eval`。

![Harness dashboard](assets/readme/ui-harness.png)

1. 说明真实评测集和 synthetic demo 分开统计。
2. 每次 `HarnessRun` 固定版本、provider、数据集与时间；每个 case 保存输出、tool calls、citations、latency 和 failure reasons。
3. 人工可以修正主体、价值观、风险与等级，修正以 override/gold 形式保留，不静默覆盖原输出。
4. failure taxonomy 用来区分 VLM、RAG、相似图、Prompt、指标标定和 provider 故障。

面试追问入口：为什么先做 Harness 再考虑 post-training？如果不拆解失败来源，微调可能把错误检索和错误标签一起固化。

## 3:40-4:30 系统治理与多人服务

打开 `http://127.0.0.1:5199/?view=runtime`，并展示 FastAPI `http://127.0.0.1:8000/docs`。

![Runtime health](assets/readme/ui-api-metrics.png)

1. PostgreSQL 管事务状态、用户、权限、Memory、jobs 和 traces。
2. Milvus/Zilliz 管文本与图片向量，不替代主库。
3. OSS 管图片对象；数据库只存 URL、object key、hash 和飞书 file token。
4. Redis/RQ 执行 VLM、生成、飞书附件、RAG rebuild 等慢任务。
5. FastAPI 使用 viewer/operator/admin 与国家权限，写操作进入 audit log。

面试追问入口：为什么本地仍保留 SQLite？它用于无外部依赖的单人 demo 与测试；线上通过 repository/provider 配置切到 PostgreSQL。

## 4:30-5:00 指标和边界

![Evaluation snapshot](assets/readme/evaluation-snapshot.svg)

- 45 条真实样本：日本 25、法国 20。
- 三段式合规率、飞书字段完整率、工具调用成功率均为 100%。
- RAG MRR@5 为 97%，但 Precision@5 只有 20%，说明 expected 文档靠前，但候选池仍不够干净。
- SA 高潜二分类准确率 60%，不能包装成大规模生产预测准确率。
- 图像相似 Gold Hit@5 为 66.67%，低置信证据通过 gate 隐藏。

收尾口语稿：

> 我把项目价值定义为可量化地发现问题并控制业务风险，而不是声称模型已经完美。当前工程闭环、字段契约和可观测性较完整，模型与检索效果仍需要更多真实样本持续迭代。

## Synthetic Demo Recording

公开 GIF 的录制原则：

- 使用独立 `PUZZLEOPS_RUNTIME_DIR`。
- 关闭远程 VLM、embedding、rerank 和 Milvus 调用。
- 图像生成使用 mock provider，飞书使用 mock client。
- 页面只展示 synthetic demo 和聚合指标。
- 录制后执行 `python scripts/release_preflight.py` 与二进制字符串扫描。
