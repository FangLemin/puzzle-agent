# PuzzleOps Agent Python 版

这是一个纯 Python 实现的“出海拼图内容运营 Agent”项目。它用结构真实、数据模拟的方式复现法国/日本拼图运营流程，包含常规提需、试新提需、节日提需建议、数据分析大师、价值观大师、排图工作台、同步记录，以及 v0.3.0 新增的多模态 Agent Runtime。

## 简历级项目定位

PuzzleOps Agent 当前定位为：**面向出海拼图内容运营的 Agent Harness 系统**。

它不是单次 prompt demo，而是围绕真实运营工作流构建的可回放、可评测、可人工修正的 Agent 工程闭环：

- 多模态理解：Qwen VLM 解析图片主体内容、色彩氛围、构图环境和风险。
- RAG 依据：召回日本/法国价值观、审核规则、approved memory 和真实 gold 样本。
- Memory 治理：感知记忆、短期记忆、长期记忆、结构化事实四层管理。
- 工具落地：试新/常规提需、飞书多维表格同步、附件上传、生成图二次审核。
- Agent Harness：真实样本评测、case trace、失败分类、HITL 修正和版本报告。

第四层收口材料：

- [简历级收口报告](docs/final_acceptance/v0.7.49_resume_closure_report.md)
- [架构与链路说明](docs/final_acceptance/puzzleops_architecture.md)
- [真实评测集摘要](docs/eval/gold_dataset_summary.md)
- [价值观大师评测报告](docs/eval/value_master_eval_report.md)
- [Prompt Benchmark v2 报告](docs/eval/value_master_prompt_benchmark_v2_report.md)
- [RAG hard-negative 报告](docs/eval/rag_hard_negative_report.md)

当前应诚实表述为：项目已达到可展示的 Agent 工程闭环，但价值观预测效果仍处于小样本评测和专项修复阶段，不建议夸大为“预测准确率已稳定”。

## 你需要看的核心文件

- `puzzle_ops/agents.py`：核心 Agent 业务逻辑。
- `puzzle_ops/excel_importer.py`：真实风格 Excel 样表导入，支持 `DISPIMG` 图片抽取。
- `puzzle_ops/multimodal.py`：图片特征抽取、图文融合、相似好图/坏图检索、价值观候选挖掘。
- `puzzle_ops/audit.py`：审核手册规则召回与风险审核。
- `puzzle_ops/eval_suite.py`：Agent/RAG 评测数据集、case 明细、阈值和 pass/fail 汇总。
- `puzzle_ops/trulens_eval.py`：本地 TruLens-style RAG Triad 评测适配层。
- `puzzle_ops/trial_upload.py`：试新图片上传保存和本地解析适配层。
- `puzzle_ops/storage.py`：SQLite 主数据、memory、价值观规则存储。
- `puzzle_ops/cache.py`：Redis 优先、内存 fallback 的缓存抽象。
- `puzzle_ops/feishu.py`：真实飞书/Mock 飞书同步接口。
- `puzzle_ops/runtime.py`：Tool Registry 和 Skill Library。
- `puzzle_ops/synthetic_data.py`：每国每周 139 条的大规模模拟数据生成器。
- `puzzle_ops/cms.py`：公司 CMS 全局未分发素材库的本地 mock。
- `puzzle_ops/adapters.py`：MCP-like 工具适配层。
- `puzzle_ops/data.py`：模拟历史数据、运营 tag、节日、价值观、分析明细。
- `puzzle_ops/models.py`：业务字段结构。
- `puzzle_ops/renderer.py`：用 Python 生成后台页面。
- `puzzle_ops/server.py`：Python 标准库本地服务。
- `tests/`：核心功能测试。

## 运行本地后台

```bash
cd /Users/fanglemin/Desktop/puzzle-agent-python
PYTHONPATH=. python3 run_app.py
```

打开：

```text
http://127.0.0.1:5188
```

生产收口运行建议：

```bash
cd /Users/fanglemin/Desktop/puzzle-agent-python/.worktrees/multimodal-agent-runtime
PUZZLEOPS_PRODUCTION_MODE=true \
PUZZLEOPS_RUNTIME_DIR=/Users/fanglemin/Desktop/puzzle_ops_runtime_prod \
PUZZLEOPS_WRITE_COUNTRIES=日本,法国 \
HOLIDAY_LLM_ENABLE_REMOTE_CALLS=0 \
RAG_ENABLE_REMOTE_CALLS=0 \
RAG_RERANK_PROVIDER=local \
RAG_EMBEDDING_PROVIDER=local \
PYTHONPATH=. python -c 'from puzzle_ops.server import run; run(port=5199)'
```

最新本地页面：

```text
http://127.0.0.1:5199/?view=dashboard
http://127.0.0.1:5199/?view=regular
http://127.0.0.1:5199/?view=trial
http://127.0.0.1:5199/?view=value
http://127.0.0.1:5199/?view=runtime
http://127.0.0.1:5199/?view=eval
```

## 运行测试

```bash
cd /Users/fanglemin/Desktop/puzzle-agent-python
PYTHONPATH=. pytest tests -q
```

## 已实现功能

- 首页按日本/法国隔离数据，展示季度 SA/AI 指标、本周工作流、今日待办和节日提需建议。
- 常规提需：分类 -> 完整中文运营 tag + 库存 -> 已分发图片参考 -> 批量提需表。
- 常规/试新提需表字段包含：提需分类、国家、JS分类、图片本身、运营tag、主体内容、张数、需求等级、加工方式、交付日期、主体描述、备注。
- 交付日期默认空；需求等级默认 P1；运营tag、加工方式、张数、交付日期、备注可以在表格里修改并保存。
- 常规提需表支持“一键同步到飞书表格”；同步成功后清空当前提需表，并显示本次完成提需条数。
- 试新提需支持“参考图解析提需”和“好图衍生提需”，可以上传本地图片进行解析；未接 LLM 时使用本地图片解析适配层，也保留模拟解析按钮。
- 数据分析大师展示 SA/CD/AI 指标、CD历史均值、AI历史均值/OKR、5/10 分发位标红、图片来源和可编辑分析备注。
- 周三复盘工作台基于上传/示例 Excel 回收数据，自动列出新增 S/A 图、下降图、国家差异、可复用 tag、应停用 tag，并可一键确认生成常规提需清单，继续复用飞书同步链路。
- 数据分析明细、周期内容分析、下一步 todo 均支持保存，刷新页面后保留当前服务进程内的编辑状态。
- 价值观大师按 S/A/B/C/D 按钮筛选预测图片，价值观规则库默认折叠。
- 排图工作台按周一到周日展示一天 10 张推荐排图；工作日遵守 1-9、12-15 位，周末遵守 1-9、12-18 位。
- 多模态底座：读取真实风格 Excel 样表示例，解析图片、构建 `ImageProfile`，展示相似历史好图/坏图证据和价值观候选池。
- Agent 评测：展示 Eval Dataset、case 明细、metric 阈值、pass/fail、TruLens-style RAG Triad、Context Precision/Recall、Tool Correctness、Plan Adherence、Step Efficiency，以及 Agent plan/tool/observation trace。
- Agent 评测页展示 `feishu.write_table` 时只做 dry-run trace，不会写入真实飞书；只有提需表里的“一键同步到飞书表格”会触发真实写表。
- 审核规则：从 `拼图审核手册.docx` 中召回风险依据，结合规则引擎输出风险等级和修改建议。
- HITL Memory：运营通过价值观候选后，系统会写入固定价值观规则和长期 memory，并在多模态底座页面展示已审批规则。
- 价值观候选池审核：多模态底座页面可直接点击“通过”，将候选规则加入固定价值观规则库。
- 大规模模拟数据：支持每个国家每周 139 条历史回收数据，自动生成图片路径、指标、SABCD、多维度等级和 JS 分类。
- Tool/Skill Runtime：显式注册 function calling 工具，并定义常规提需、试新提需、价值观大师、价值观挖掘、数据分析等业务 Skill。
- CMS/MCP-like Adapter：支持 `cms.query_inventory`、`cms.search_assets`、`cms.low_stock_tags`，Agent trace 会展示对应工具调用。
- 飞书同步：提需表同步要求真实飞书配置；未配置时不会清空提需表，也不会假装同步成功。配置 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_SPREADSHEET_TOKEN`、`FEISHU_SHEET_RANGE` 后会调用飞书在线表格追加写入。

## 真实飞书连接需要你准备的信息

不要把密钥发到聊天里。复制 `.env.example` 为 `.env`，在本机填写：

```bash
cp .env.example .env
```

必须提供：

- `FEISHU_APP_ID`：个人飞书开放平台自建应用的 App ID。
- `FEISHU_APP_SECRET`：自建应用的 App Secret，只放本地 `.env`，不要提交到 Git。
- `FEISHU_SPREADSHEET_TOKEN`：在线表格 URL 里的 spreadsheet token。
- `FEISHU_SHEET_RANGE`：如果是电子表格，填写入起点，例如 `Sheet1!A1`；如果是多维表格/Base，填 table id，例如 `tblxxxxxxxx`。

可选：

- `FEISHU_ACCESS_TOKEN`：如果不填，系统会用 App ID 和 App Secret 自动请求 `tenant_access_token`。

飞书侧还需要：

- 如果使用电子表格，给自建应用开通电子表格读写权限。
- 如果使用多维表格/Base，给自建应用开通 `bitable:app` 或 `base:record:create` 权限。
- 将你的在线表格授权给这个应用或确保应用所在租户有权限访问。
- 表格第一行建议预留为字段表头，因为系统会追加写入表头和数据。

## 说明

这个版本不接入公司真实 CMS 或内部数据，CMS 使用本地 mock。飞书提需同步现在要求真实在线表格配置；缺少密钥时会明确失败并保留提需表。项目中没有 Java、Node、Vue、React 或前端构建工具；页面由 Python 服务端渲染。

Excel 图片说明：真实样表中的“图片本身”字段使用 `DISPIMG` 公式，项目会解析 `xl/cellimages.xml` 并把图片抽取到本地路径；生产环境有真实 `image_url` 时，可以优先展示 URL。

LLM 大脑说明：当前版本支持通过 `VISION_LLM_PROVIDER=qwen` 或 `VISION_LLM_PROVIDER=openai` 接入真实视觉语言模型。Qwen 默认视觉模型为 `qwen3-vl-plus`，用于试新图片的主体内容、色彩氛围、构图环境解析，以及价值观大师的图片证据判断。Qwen 请求默认超时为 90 秒，可通过 `QWEN_TIMEOUT_SECONDS` 调整；未配置真实 key 时，系统只保留本地像素层解析和明确的未配置提示，不会伪造语义主体识别。

好图衍生生成说明：`IMAGE_GENERATION_PROVIDER` 默认为空时，好图衍生只输出衍生方向；`mock` 只用于本地 Harness/UI 链路验证，生成的占位图不会同步为飞书附件；配置为 `dashscope` 后，系统使用通义万相参考图生成能力生成真实参考图。生成图必须依次通过真实视觉 LLM 二次解析、审核规则复检和运营人工确认，三层均通过后才允许同步到飞书图片附件字段；未配置 VLM、VLM 调用失败、命中风险或未人工确认时，页面保留记录但阻断同步。

DashScope/通义万相说明：先运行 `python -m pip install -r requirements.txt`，再设置 `IMAGE_GENERATION_PROVIDER=dashscope` 和 `IMAGE_GENERATION_MODEL=wan2.6-image`。默认复用现有 `QWEN_API_KEY`，如需独立密钥可设置 `IMAGE_GENERATION_API_KEY` 覆盖。系统通过官方 DashScope Python SDK 把本地参考图临时上传并调用 `ImageGeneration`，不会再把参考图错误地发送到文生图接口。任务失败、额度不足或返回结构异常时，试新页面会保留原始提需并显示可分类错误，不伪造生成图。试新页提供 Provider 诊断；generation trace 记录来源运营 tag、生成图路径、二次审核和人工确认后的飞书附件资格。

图像相似检索说明：项目新增 Qwen3-VL-Embedding 视觉相似层，用来解决“历史依据图片不相关”的问题。SQLite 仍保存历史图的国家、等级、运营 tag、业务指标和 gold label；Milvus/Zilliz 的 `puzzleops_image_embeddings` collection 只保存 image embedding 和轻量 metadata，用于以图搜图。默认使用本地 deterministic visual embedding fallback，避免页面刷新或 pytest 产生费用；设置 `VISUAL_EMBEDDING_ENABLE_REMOTE_CALLS=true`、`VISUAL_EMBEDDING_MODEL=qwen3-vl-embedding` 并提供 `VISUAL_EMBEDDING_API_KEY` 或复用 `QWEN_API_KEY` 后，系统会通过 DashScope Multimodal Embedding API 为历史图和新图生成多模态向量。价值观大师会把检索到的“视觉相似历史好图/风险图”作为 evidence 传给 LLM，但不直接改 `value_grade_model_version=v0.7.39-legacy`。

图像向量库配置示例：

```bash
VISUAL_EMBEDDING_ENABLE_REMOTE_CALLS=true
VISUAL_EMBEDDING_MODEL=qwen3-vl-embedding
VISUAL_EMBEDDING_API_KEY=
VISUAL_MILVUS_ENABLE_REMOTE_CALLS=true
VISUAL_MILVUS_URI=https://your-zilliz-endpoint
VISUAL_MILVUS_COLLECTION=puzzleops_image_embeddings
VISUAL_MILVUS_TOKEN=your_zilliz_token
```

Agent Harness 真实评测集说明：默认页面只展示离线 Harness 预览，不调用远程 RAG、视觉 LLM 或图像生成，也不会因刷新页面新增历史 run。点击“运行真实 VLM Harness”后，系统才会使用真实图片解析结果对照人工 gold label；“包含付费生成评测”需要单独勾选。默认 Harness 会从历史样表和合成 demo 生成样本，适合本地演示；如果要证明真实业务效果，请按 `docs/harness_gold_samples_template.csv` 整理 30-50 条真实拼图样本，并在 `.env` 设置 `PUZZLEOPS_HARNESS_DATASET=/absolute/path/to/gold_samples.csv`。导入时会校验真实图片路径，缺图样本会被标记为导入问题而不会让评测崩溃；缺少 gold label 或未执行真实模型的指标会显示为 `not_evaluable`。

Harness 生成 trace 说明：Agent 评测页会读取本地 generation event memory，计算 `生成Trace完整率`、`二次审核通过率`、`飞书附件Ready率`、`生成失败可分类率`，并展示生成失败类型分布。该指标用于证明好图衍生链路是否可回放、可诊断、可评测；没有真实生成事件时指标为 0，不伪造生成效果。

四层 Memory 说明：项目将 memory 拆为感知记忆、短期记忆、长期记忆和结构化事实。感知记忆承接试新图片解析与 VLM/本地视觉观察；短期记忆记录当前任务链路状态，例如 generation trace；长期记忆沉淀运营人工确认的价值观和规则；结构化事实保存主体、国家、运营 tag、图片路径等可评测字段。当前四层 memory 使用 SQLite 本地表落地，不引入向量库或前端工程栈。

价值观与审核 RAG 说明：项目新增本地 RAG 知识层，把日本/法国静态价值观、运营审批价值观、四层 memory 事实、历史样本事实和审核手册规则组织成父子知识块。检索链路采用 Python 本地实现的 chunk 语义切分、父子存储、BM25 风格词面召回、向量召回、rerank 精排和带 citation 的 prompt 拼接。价值观大师会优先使用 RAG Top-K 引用依据再调用真实视觉 LLM 判断，页面会展示父子知识块数量、多路召回说明和引用依据，降低幻觉风险。默认可使用本地 fallback，本机 `.env` 可接入 DashScope 的真实 embedding 与 Qwen rerank；后续数据规模增长时计划替换为 Milvus。

RAG Provider 说明：默认不需要额外配置，系统可使用本地 `local-token-cosine` embedding fallback 和 `local-rule-rerank` 精排。配置 `RAG_EMBEDDING_PROVIDER`、`RAG_EMBEDDING_MODEL`、`RAG_RERANK_PROVIDER`、`RAG_RERANK_MODEL`、API key，并显式设置 `RAG_ENABLE_REMOTE_CALLS=true` 后会发起真实远程请求；pytest 默认关闭远程调用，避免测试误产生费用。

DashScope RAG Provider 说明：如需真实调用通义千问/DashScope embedding 与 rerank，可配置 `RAG_EMBEDDING_PROVIDER=dashscope`、`RAG_EMBEDDING_MODEL=text-embedding-v4`、`RAG_RERANK_PROVIDER=dashscope`、`RAG_RERANK_MODEL=qwen3-rerank`，并提供 `RAG_API_KEY` 或 `DASHSCOPE_API_KEY`。为了保护业务数据和成本，系统即使检测到 key，也只有在 `.env` 显式设置 `RAG_ENABLE_REMOTE_CALLS=true` 后才会真正请求远程服务；否则页面会显示 provider/key 就绪但继续使用本地 fallback。可选配置 `RAG_EMBEDDING_ENDPOINT` 与 `RAG_RERANK_ENDPOINT` 覆盖默认 endpoint。

Milvus RAG Provider 说明：如需把 RAG chunk 入库到 Milvus，可配置 `RAG_VECTOR_STORE_PROVIDER=milvus`、`MILVUS_URI`、`MILVUS_COLLECTION`、`MILVUS_TOKEN`；Runtime 页面会按当前 provider 显示“重建并入库Milvus/SQLite/Qdrant”。Milvus 入库会自动检查 collection，不存在时创建 schema、vector index 并 load collection；点击 “Milvus Smoke” 会写入临时向量、搜索命中并删除临时实体。在线检索只有在 `RAG_VECTOR_STORE_SEARCH_ENABLED=true` 或 `RAG_MILVUS_SEARCH_ENABLED=true` 时启用，避免本地演示误连外部向量库。当前已用 Zilliz Cloud endpoint 验证：1024 维 collection 自动创建成功，Smoke `search_hit=True` 且临时实体清理成功。

RAG 可观测与缓存说明：远程 embedding 会优先读取 SQLite `rag_embedding_cache`，命中后不会重复请求 provider；未命中才会在远程调用开关开启时请求 DashScope，并写回缓存。多模态底座会展示本次 RAG 的 cache hit、embedding remote、embedding fallback、rerank remote、rerank fallback 指标，便于判断是否真的发生远程调用、是否发生降级，以及后续接入成本/耗时统计。

Harness RAG 指标说明：Agent 评测页会把最近一次 RAG runtime stats 聚合为 `RAG缓存命中率`、`RAG远程调用率`、`RAG降级率`，并随 `HarnessRun` 一起保存。这样 RAG 不再只在多模态底座单次展示，而是能进入版本对比，用来观察不同版本的检索成本、远程依赖和稳定性。

真实 RAG 模型说明：推荐本地 `.env` 配置为 DashScope RAG 真实调用链路，embedding 使用 `text-embedding-v4`，rerank 使用 `qwen3-rerank`，并通过 `RAG_ENABLE_REMOTE_CALLS=true` 显式开启远程调用。自动化测试默认关闭远程调用，避免 pytest 误产生费用；真实运行页面或脚本时按 `.env` 生效。四层 memory（感知记忆、短期记忆、长期记忆、结构化事实）都会被转成 RAG 文档并参与召回，多模态底座会显示每层 `RAG Ready` 数量。

Harness Case Trace 与 Memory Debug 说明：每个 Harness case 现在保存结构化 `evidence_trace`，区分视觉输入证据、RAG citation/context 和四层 memory 证据，并用 `failure_categories` 对缺图、缺 gold、风险漏召回、等级误判、生成 provider 未配置、生成失败和字段缺失进行业务分类。为控制真实模型成本，同一 Harness run 每个国家只执行一次真实 RAG 检索，再把 run 级引用证据关联到该国 case。多模态底座新增只读 Memory Debug 表，可按当前主体查看层级、memory type、RAG source、命中分、入库状态和内容。

RAG 批处理与引用溯源说明：DashScope embedding 推荐使用 `text-embedding-v4`，rerank 推荐使用 `qwen3-rerank`；远程调用失败后直接使用本地 fallback，不伪造 provider 成功。多模态底座新增引用明细，展示 citation ID、知识来源、父文档、标题和正文。价值观大师要求 LLM 输出结论、当前图像证据、真实 RAG citation、风险提示和人工复核事项，旧版 `value_match/evidence` JSON 仍兼容。

Memory 治理说明：四层 memory 现在包含 `memory_id`、`status`、`expires_at`、`fingerprint`、`source_memory_id`、`human_verified` 和更新时间。感知记忆默认保留 7 天，短期记忆默认保留 24 小时，长期记忆与结构化事实不自动过期；相同 active payload 自动去重。运营可在 Memory Debug 中把感知/短期记忆晋升为结构化事实，把短期/事实晋升为长期记忆，或停用错误记忆。晋升目标保留来源 ID 和人工确认标记，来源改为 `promoted`；`expired/promoted/retired` 均不再进入 RAG。旧 SQLite 会自动迁移新列并保留已有数据。

Memory 冲突、溯源与生产治理说明：多模态底座新增 Memory 工作台、Memory Conflict、Memory Provenance 和可操作 Memory Debug。Memory 只有在当前国家、`review_status=approved`、`approved_for_rag=true`、`status=active`、未过期且未处于冲突组时才会进入 RAG；草稿、驳回、停用和 conflict locked 记忆不会污染 Agent 判断。运营可在工作台按 layer、审核状态、RAG 许可、冲突、创建人、主体和运营 tag 筛选，并执行批准进 RAG、批准但不进 RAG、驳回、停用、冲突保留/合并/全部停用/暂不处理。系统会记录 `created_by`、`approved_by`、`retired_by`，并用 `memory_audit_events` 保存 create/review/retire/rag_hit 操作流水。RAG 命中 layered memory 后会回写 `rag_hit_count` 和 `last_rag_hit_at`；低满意度、`not_useful`、冲突和过期项会进入清理建议。Runtime 页可生成当前国家的生产验收样例，用来验证审批、RAG 闸门、只读权限和冲突闭环。

Harness HITL 说明：Agent 评测页的失败样本复盘区会展示样本缩略图、gold label、Agent 输出和失败原因，并提供人工修正入口。当前人工修正先写入本地 HITL memory，作为后续回写 gold dataset 或导出到 Label Studio/Argilla 的数据基础。

Harness 修正回流说明：Agent 评测页支持将 HITL 人工修正导出为 CSV，默认写到运行目录中的 `harness_overrides_<国家>.csv`。该 CSV 可作为人工复核后的中间层，再手动合并回 `PUZZLEOPS_HARNESS_DATASET`，避免直接覆盖真实 gold dataset。

标注平台导出说明：Agent 评测页支持导出 Argilla JSONL 和 Label Studio JSON 文件，默认写到运行目录 `harness_annotation_exports/`。导出内容包含失败 case、人工修正 case、图片路径、gold label、Agent 输出和失败原因；当前只做本地文件落地，不直接调用外部平台 API。
