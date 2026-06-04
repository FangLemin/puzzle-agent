# PuzzleOps Agent Python 版

这是一个纯 Python 实现的“出海拼图内容运营 Agent”项目。它用模拟数据复现法国/日本拼图运营流程，包含常规提需、试新提需、节日提需建议、数据分析大师、价值观大师、排图工作台和同步记录。

## 你需要看的核心文件

- `puzzle_ops/agents.py`：核心 Agent 业务逻辑。
- `puzzle_ops/data.py`：模拟历史数据、运营 tag、节日、价值观、分析明细。
- `puzzle_ops/models.py`：业务字段结构。
- `puzzle_ops/renderer.py`：用 Python 生成后台页面。
- `puzzle_ops/server.py`：Python 标准库本地服务。
- `tests/`：核心功能测试。

## 运行本地后台

```bash
cd /Users/fanglemin/puzzle-agent-python
PYTHONPATH=. python3 run_app.py
```

打开：

```text
http://127.0.0.1:5188
```

## 运行测试

```bash
cd /Users/fanglemin/puzzle-agent-python
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

## 说明

这个版本不接入公司真实 CMS、飞书或内部数据，全部使用结构真实、数据模拟的方式实现。项目中没有 Java、Node、Vue、React 或前端构建工具；页面由 Python 服务端渲染。
