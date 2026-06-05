# PuzzleOps Agent Python 版

这是一个纯 Python 实现的“出海拼图内容运营 Agent”项目。它用结构真实、数据模拟的方式复现法国/日本拼图运营流程，包含常规提需、试新提需、节日提需建议、数据分析大师、价值观大师、排图工作台、同步记录，以及 v0.3.0 新增的多模态 Agent Runtime。

## 你需要看的核心文件

- `puzzle_ops/agents.py`：核心 Agent 业务逻辑。
- `puzzle_ops/excel_importer.py`：真实风格 Excel 样表导入，支持 `DISPIMG` 图片抽取。
- `puzzle_ops/multimodal.py`：图片特征抽取、图文融合、相似好图/坏图检索、价值观候选挖掘。
- `puzzle_ops/audit.py`：审核手册规则召回与风险审核。
- `puzzle_ops/storage.py`：SQLite 主数据、memory、价值观规则存储。
- `puzzle_ops/cache.py`：Redis 优先、内存 fallback 的缓存抽象。
- `puzzle_ops/feishu.py`：真实飞书/Mock 飞书同步接口。
- `puzzle_ops/runtime.py`：Tool Registry 和 Skill Library。
- `puzzle_ops/synthetic_data.py`：每国每周 139 条的大规模模拟数据生成器。
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
- 交付日期默认空；需求等级默认 P1；加工方式、张数、交付日期、备注可以在表格里修改并保存。
- 试新提需支持“参考图解析提需”和“好图衍生提需”，价值观大师会写入价值观匹配度。
- 数据分析大师展示 SA/CD/AI 指标、5/10 分发位标红、图片来源和可编辑分析备注。
- 价值观大师按 S/A/B/C/D 按钮筛选预测图片，价值观规则库默认折叠。
- 排图工作台按周一到周日展示一天 10 张推荐排图；工作日遵守 1-9、12-15 位，周末遵守 1-9、12-18 位。
- 多模态底座：读取真实风格 Excel 样表示例，解析图片、构建 `ImageProfile`，展示相似历史好图/坏图证据和价值观候选池。
- Agent 评测：展示工具调用成功率、审核风险召回率、SABCD 预测准确率、价值观候选通过率、Agent plan/tool/observation trace。
- 审核规则：从 `拼图审核手册.docx` 中召回风险依据，结合规则引擎输出风险等级和修改建议。
- HITL Memory：运营通过价值观候选后，系统会写入固定价值观规则和长期 memory。
- 大规模模拟数据：支持每个国家每周 139 条历史回收数据，自动生成图片路径、指标、SABCD、多维度等级和 JS 分类。
- Tool/Skill Runtime：显式注册 function calling 工具，并定义常规提需、试新提需、价值观大师、价值观挖掘、数据分析等业务 Skill。

## 说明

这个版本不接入公司真实 CMS 或内部数据。飞书已预留真实客户端接口，缺少密钥时使用 Mock CSV fallback。项目中没有 Java、Node、Vue、React 或前端构建工具；页面由 Python 服务端渲染。

Excel 图片说明：真实样表中的“图片本身”字段使用 `DISPIMG` 公式，项目会解析 `xl/cellimages.xml` 并把图片抽取到本地路径；生产环境有真实 `image_url` 时，可以优先展示 URL。
