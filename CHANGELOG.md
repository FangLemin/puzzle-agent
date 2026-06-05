# 版本记录

这个文件用来记录每一版做了什么、为什么改、当前还存在哪些问题。以后每次你让我修改功能，我会先提交旧版本，再在这里追加阶段总结。

## v0.3.4 - 提需同步、分析持久化与 RAG 评测补齐

日期：2026-06-05

阶段目标：

- 让当前页面从“能看”继续往“能测试核心业务动作”推进，重点补齐提需同步、试新模拟上传、数据分析保存、价值观候选池反馈和 RAG 评测。

已完成：

- 常规提需表的运营 tag 字段支持编辑和保存。
- 常规提需表新增“一键同步到飞书表格”按钮；同步成功后清空当前提需表，并显示“同步成功，当前已完成提需X条”。
- 试新提需新增可点击的“模拟上传并解析”流程，分别支持参考图解析和好图衍生模式，便于本地验证。
- 试新提需保存逻辑补齐运营 tag 字段，保证常规/试新核心字段一致。
- 数据分析大师新增保存入口，图片明细备注、周期内容分析、下一步 todo 可以在当前服务进程内持久化。
- 数据分析第一行补齐 CD 历史均值，以及 AI 历史均值和 AI OKR。
- 多模态底座页面新增“已审批价值观规则”和 “HITL Memory”展示，运营点击候选规则通过后可以立即看到结果。
- 新增 `TruLensRAGEvaluator` 本地适配层，把 Context Relevance、Groundedness、Answer Relevance 接入 Agent 评测页。
- README 明确说明当前版本没有接真实 LLM/视觉语言模型，不能声称模型本身具备真实多模态能力。

当前限制：

- 飞书真实同步需要配置个人飞书开放平台凭证；未配置时使用 Mock CSV fallback。
- TruLens 评测当前是本地 TruLens-style 指标适配层，不依赖真实 TruLens provider。
- 试新上传仍是模拟图片位，主要用于验证 workflow；后续可接真实图片上传和多模态 LLM。

验证记录：

- `PYTHONPATH=. pytest tests -q`：71 passed。

## v0.3.3 - 价值观候选池 HITL 审核闭环

日期：2026-06-05

阶段目标：

- 让价值观候选池不只停留在展示和程序接口，而是能在页面上由运营点击审核通过，形成可演示的 HITL 闭环。

已完成：

- 多模态底座页面的价值观候选池新增“运营审核”操作列。
- 每条候选价值观支持填写/保留人工备注，并点击“通过”。
- 服务端新增 `/approve_value_candidate` action，调用 `approve_value_candidate`。
- 审核通过后写入固定价值观规则库和 HITL memory。
- Agent 评测页的价值观候选通过率会随审批结果变化。

验证记录：

- `PYTHONPATH=. pytest tests -q`：62 passed。

## v0.3.2 - CMS/MCP-like Adapter 与真实飞书请求骨架

日期：2026-06-05

阶段目标：

- 补齐生产环境中最容易被面试追问的外部系统适配：CMS 库存、MCP-like 工具协议、飞书真实写入请求骨架。

已完成：

- 新增 `MockCMSClient`：模拟公司 CMS 全局未分发素材库，支持按运营 tag 查库存、按国家/JS分类检索素材、识别低库存 tag。
- 新增 `MCPToolAdapter`：以 MCP-like manifest 形式暴露 `cms.query_inventory`、`cms.search_assets`、`cms.low_stock_tags`。
- 增强 `RealFeishuClient`：按飞书官方电子表格追加数据接口构造 `POST /open-apis/sheets/v2/spreadsheets/:spreadsheetToken/values_append` 请求。
- 飞书客户端保留可注入 transport，测试不打真实外网；缺少 `FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_SPREADSHEET_TOKEN/FEISHU_ACCESS_TOKEN` 时自动降级 Mock CSV。
- Agent trace 接入外部工具链，展示 `cms.query_inventory` 和 `feishu.write_table`。
- Agent 评测页新增 `CMS/MCP适配状态`、`飞书同步模式`。

当前限制：

- MCP-like adapter 是本地协议化工具层，不是独立 MCP Server 进程。
- 飞书真实写入需要用户自己配置开放平台应用权限、电子表格权限和 access token。
- CMS 仍为本地 mock，不连接公司真实 CMS。

验证记录：

- `PYTHONPATH=. pytest tests -q`：61 passed。
- `http://127.0.0.1:5190/?country=日本&view=eval`：页面显示 CMS/MCP 适配状态、飞书同步模式和完整 tool calls。

## v0.3.1 - 大规模模拟数据与 Tool/Skill Runtime 补齐

日期：2026-06-05

阶段目标：

- 补齐 v0.3.0 中还停留在计划层的“大规模数据生成”和“显式 function calling / skill library”能力。

已完成：

- 新增 `SyntheticDataGenerator`：支持按国家和周数生成历史回收数据。
- 每个国家每周固定生成 139 条记录，支持日本/法国双国家数据集。
- 每条模拟数据包含 `image_id`、`image_url`、`local_image_path`、`thumbnail_path` 和本地图片占位文件。
- 模拟数据遵守固定 JS 分类枚举，并使用日本/法国阈值自动生成多维度等级与 SABCD 等级。
- 新增 `ToolRegistry`：统一注册和调用工具，返回标准 `ToolResult`。
- 新增 `SkillLibrary`：显式定义常规提需、试新提需、价值观大师、价值观候选挖掘、数据分析等业务 Skill 及其 required tools。

当前限制：

- 大规模图片目前使用本地 1px PNG 占位图，主要用于验证链路和页面字段；后续可替换为生成式 mock 图或真实图片 URL。
- Tool/Skill 已成为显式模块，但 Orchestrator 仍是轻量本地实现，尚未接真实 MCP Server。

验证记录：

- `PYTHONPATH=. pytest tests -q`：58 passed。

## v0.3.0 - 多模态 Agent Runtime 工程化升级

日期：2026-06-05

阶段目标：

- 将项目从页面原型升级为可讲工程实现的多模态内容运营 Agent 系统。
- 以真实风格 Excel 样表和审核手册为输入，补齐图片抽取、等级校验、多模态画像、价值观候选池、相似好坏图证据、HITL memory 和 Agent eval/trace。

已完成：

- 新增真实 Excel 导入器：读取 `图片等级、图片本身、图片ID、图片URL、分发位置、多维度等级、开图率、完成率、平均完成时长、运营tag、主体tag、JS分类、图片来源、备注、分发日期、分发周期`。
- 支持 WPS/Excel `DISPIMG` 单元格图片：解析 `xl/cellimages.xml`，将图片解压为本地文件，并写入 `local_image_path/thumbnail_path`。
- 固定 JS 分类枚举：`houses/home/food/flowers/pets/animal/travel/ontheway/zen/objects/patterns/handcrafted/streetview/human`。
- 新增日本/法国等级阈值与 SABCD 校验逻辑：按开图率、完成率、平均完成时长生成多维度等级和图片等级。
- 新增 SQLite 仓库：保存历史图片、HITL memory、已审批价值观规则。
- 新增 Redis 缓存抽象：Redis 不可用时自动降级到 Python 内存缓存。
- 新增飞书客户端抽象：缺少真实飞书密钥时导出 Mock CSV，后续可接真实飞书 API。
- 新增多模态底座：`ImageFeature`、`ImageProfile`、图片结构化特征、caption、历史指标融合。
- 新增相似历史好图/坏图检索：价值观判断可以展示 S/A 证据和 C/D 风险参考。
- 新增价值观候选池：从 SA/CD 历史样本中生成 `pending_review` 候选规则，运营通过后写入固定规则和 memory。
- 新增审核规则检索与规则引擎：从 `拼图审核手册.docx` 召回红线/黄线依据，给出风险等级、原因和修改建议。
- 新增 Agent trace/eval：记录 plan、skill、tool calls、observations、context、memory hits、eval metrics。
- 新增页面入口：`多模态底座 🧠` 和 `Agent 评测 🧪`。

当前限制：

- 当前多模态特征抽取为本地规则/结构化模拟，不声称接入真实视觉大模型。
- 真实飞书客户端只完成接口预留；没有密钥时使用 Mock CSV fallback。
- SQLite/Redis/飞书/MCP-like adapter 已形成工程接口，但尚未接真实公司 CMS。
- 大规模 12 周 × 139 条/国家的数据生成器尚未展开；本版优先完成真实样表导入和 Agent runtime 骨架。

验证记录：

- `PYTHONPATH=. pytest tests -q`：54 passed。

## v0.2.2 - AI率 OKR 规则修正

日期：2026-06-05

阶段目标：

- 修正首页 AI 指标口径，从“AI占比”改回“AI率”，并按业务规则显示颜色。

已完成：

- 首页文案改回 “本季度累计 AI率 / OKR”。
- AI率 OKR 数值保持黑色。
- AI率低于 OKR 时显示绿色。
- AI率等于或超过 OKR 时显示红色。
- AI率超过 OKR 且差距大于 10 个百分点时显示红色感叹号。

验证记录：

- `PYTHONPATH=. pytest tests -q`：28 passed。

## v0.2.1 - 指标颜色语义修正

日期：2026-06-05

阶段目标：

- 修正首页和数据分析大师中的指标颜色语义，让运营判断更直观。

已完成：

- 数据分析大师：SA 占比同比上升显示绿色，下降显示红色。
- 数据分析大师：CD 占比和 AI 占比同比上升显示红色，下降显示绿色。
- 首页：本季度累计 SA/AI 占比的 OKR 数值固定为黑色。
- 首页：实际占比达到/超过 OKR 时显示绿色，未达到 OKR 时显示红色。
- 首页：实际占比与 OKR 差距大于 10 个百分点时，追加红色感叹号提醒。
- 数据分析大师：将 “AI率” 文案统一为 “AI占比”。

验证记录：

- `PYTHONPATH=. pytest tests -q`：27 passed。

## v0.2.0 - 关键交互修复与 PRD 对齐

日期：2026-06-05

阶段目标：

- 修复 v0.1.0 中“按钮点击后页面打不开/功能不生效”的问题。
- 让 Python 版更接近已通过的 PRD 原型，补齐核心业务动作和必要模拟数据。

已完成：

- 首页：本周工作流增加图标，工作流内容和今日待办内容改为可编辑文本框。
- 首页：节日提需建议改为按钮展开，不再默认铺在页面下方。
- 常规提需：修复已分发图片“加入提需”后页面打不开的问题。
- 常规提需：修复“AI生成描述”功能，点击后会批量写入主体描述。
- 常规提需：低库存爆款红色置顶，低库存稳定款黄色展示，其他正常展示。
- 试新提需：修复“价值观大师”按钮，点击后会写入价值观匹配度。
- 试新提需：增加模拟上传区域、参考图 A/B/C、好图衍生说明，使两个模式更接近 PRD。
- 试新提需：提需表字段加宽，张数/需求等级/加工方式使用更容易看见的小输入控件。
- 试新提需：图片本身字段改为图片预览样式，不再只是一句文字。
- 数据分析大师：新增 Python 渲染的 SVG 折线图，并将周期内容分析/下一步 todo 移到页面底部。
- 价值观大师：补充日本/法国价值观规则，覆盖文化真实性、版权风格风险、宗教政治敏感、主体清晰度、构图可拼性、AI 质量、节日适配等。
- 排图工作台：修复“替换”按钮，点击后会替换为未分发候补图，并保留原分发位置。
- 全局：每个功能页标题处保留对应图标，例如常规提需 📦、试新提需 ✨、数据分析 📈。
- 服务端：修复 POST 后重定向中文 URL 导致 `UnicodeEncodeError` 的问题。

当前限制：

- 仍然坚持纯 Python，因此没有使用 JavaScript 实现拖拽上传、双击单元格编辑或无刷新交互。
- 上传图片区域目前是模拟区域，不读取真实图片文件。
- 首页工作流/待办可编辑但暂存在内存里，服务重启后会恢复默认模拟数据。

验证记录：

- `PYTHONPATH=. pytest tests -q`：24 passed。
- 已用真实 POST 验证：加入提需、AI生成描述、价值观大师、排图替换均可返回页面并修改状态。
- `http://127.0.0.1:5188/`：本地页面可访问。

## v0.1.0 - Python 版业务原型基线

日期：2026-06-05

阶段目标：

- 将已通过初审的 PRD 原型转成纯 Python 项目，方便在 VSCode 里阅读和修改。
- 保留真实业务结构，使用模拟数据，不接入公司内部 CMS、飞书或真实业务资产。

已完成：

- 建立纯 Python 项目结构：`puzzle_ops/models.py`、`puzzle_ops/data.py`、`puzzle_ops/agents.py`、`puzzle_ops/renderer.py`、`puzzle_ops/server.py`。
- 实现日本/法国国家隔离：首页指标、任务、节日、分类、运营 tag、历史图、分析数据均按国家区分。
- 实现常规提需流程：分类 -> 完整中文运营 tag + 库存 -> 历史已分发图 -> 批量提需表。
- 实现试新提需流程：参考图解析提需、好图衍生提需、价值观大师写入价值观匹配度。
- 实现数据分析大师：SA/CD/AI 指标、图片来源、5/10 分发位标红、AI 分析备注。
- 实现价值观大师：S/A/B/C/D 按钮筛选预测图，规则库折叠展示。
- 实现排图工作台：按周一到周日展示每日 10 张推荐排图，区分工作日/周末允许分发位置。
- 增加测试覆盖：核心 Agent、页面渲染、服务端参数防御，共 14 个测试。

当前限制：

- 为了满足“全部 Python”要求，页面采用 Python 服务端渲染，没有使用 JavaScript，所以交互不如 HTML PRD 原型丝滑。
- 目前数据为内置模拟数据，还没有接入真实图片上传、真实模型、真实 CMS 或飞书 API。
- 表格修改采用输入框/下拉框保存，不是 PRD 原型里的双击编辑形态。

验证记录：

- `PYTHONPATH=. pytest tests -q`：14 passed。
- `http://127.0.0.1:5188/`：本地页面可访问。

下一阶段建议：

- v0.2.0：优先修复你发现“无法实现/不如 PRD”的功能点。
- v0.3.0：补充简历版项目介绍、面试 Q&A、核心代码讲解文档。
- v0.4.0：如需要展示，可上传到 GitHub 私有仓库或公开仓库。
