# PuzzleOps Agent Python 版

这是一个纯 Python 实现的“出海拼图内容运营 Agent”项目。它用结构真实、数据模拟的方式复现法国/日本拼图运营流程，包含常规提需、试新提需、节日提需建议、数据分析大师、价值观大师、排图工作台、同步记录，以及 v0.3.0 新增的多模态 Agent Runtime。

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

LLM 大脑说明：当前版本支持通过 `VISION_LLM_PROVIDER=qwen` 或 `VISION_LLM_PROVIDER=openai` 接入真实视觉语言模型。Qwen 默认模型为 `qwen3.7-plus`，用于试新图片的主体内容、色彩氛围、构图环境解析，以及价值观大师的图片证据判断。未配置真实 key 时，系统只保留本地像素层解析和明确的未配置提示，不会伪造语义主体识别。

好图衍生生成说明：`IMAGE_GENERATION_PROVIDER` 默认为空时，好图衍生只输出衍生方向；`mock` 只用于本地 Harness/UI 链路验证，生成的占位图不会同步为飞书附件；配置为 `cloud` 并提供 `IMAGE_GENERATION_API_KEY` 后，系统会调用云端图像生成 provider 生成参考图。生成图还必须经过真实视觉 LLM 二次解析和审核规则复检，只有通过的图片才允许同步到飞书图片附件字段；未配置 VLM、VLM 调用失败或命中风险时，页面保留记录但同步附件会被关闭。

DashScope/通义万相说明：如使用阿里云异步图像生成接口，可设置 `IMAGE_GENERATION_PROVIDER=dashscope`，并配置 `IMAGE_GENERATION_API_KEY`、`IMAGE_GENERATION_MODEL`、`IMAGE_GENERATION_BASE_URL` 和 `IMAGE_GENERATION_TASK_URL_TEMPLATE`。系统会先提交生成任务，再轮询 task 结果；任务失败、超时或额度不足时，试新页面会显示失败原因、保留原始提需可编辑状态，并且不会伪造生成图或生成待同步图片行。试新页提供“检查生成 Provider”诊断按钮，可查看 provider、configured、model 和 endpoint，便于生成前确认配置状态；最近一次生成任务会记录成功/失败、provider、model、错误类型和说明，并写入本地 memory，便于在同步记录页回放排障。生成任务 trace 会记录 task_id、来源运营 tag、生成图本地路径、二次审核状态和飞书附件同步资格，用于串起生成、审核、同步前检查链路。

Agent Harness 真实评测集说明：默认 Harness 会从历史样表和合成 demo 生成样本，适合本地演示；如果要证明真实业务效果，请按 `docs/harness_gold_samples_template.csv` 整理 30-50 条真实拼图样本，并在 `.env` 设置 `PUZZLEOPS_HARNESS_DATASET=/absolute/path/to/gold_samples.csv`。导入时会校验真实图片路径，缺图样本会被标记为导入问题而不会让评测崩溃；缺少 gold label 的指标会显示为 `not_evaluable`。

Harness HITL 说明：Agent 评测页的失败样本复盘区会展示样本缩略图、gold label、Agent 输出和失败原因，并提供人工修正入口。当前人工修正先写入本地 HITL memory，作为后续回写 gold dataset 或导出到 Label Studio/Argilla 的数据基础。

Harness 修正回流说明：Agent 评测页支持将 HITL 人工修正导出为 CSV，默认写到运行目录中的 `harness_overrides_<国家>.csv`。该 CSV 可作为人工复核后的中间层，再手动合并回 `PUZZLEOPS_HARNESS_DATASET`，避免直接覆盖真实 gold dataset。

标注平台导出说明：Agent 评测页支持导出 Argilla JSONL 和 Label Studio JSON 文件，默认写到运行目录 `harness_annotation_exports/`。导出内容包含失败 case、人工修正 case、图片路径、gold label、Agent 输出和失败原因；当前只做本地文件落地，不直接调用外部平台 API。
