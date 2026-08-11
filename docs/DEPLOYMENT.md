# PuzzleOps Deployment Guide

日期：2026-08-11

本文件用于 v0.7.61 上线前验收：一个人继续用本地页面，6 人运营团队通过 FastAPI API 服务共用 Agent 能力。

## 1. 两种入口

单人本地后台：

```bash
cd /Users/fanglemin/Desktop/puzzle-agent-python/.worktrees/multimodal-agent-runtime
PYTHONPATH=. python -c 'from puzzle_ops.server import run; run(port=5199)'
```

打开：

```text
http://127.0.0.1:5199/?view=dashboard
```

6 人 FastAPI 服务：

```bash
cd /Users/fanglemin/Desktop/puzzle-agent-python/.worktrees/multimodal-agent-runtime
PUZZLEOPS_API_TOKENS='ops_jp:jp_token:operator:日本,ops_fr:fr_token:operator:法国,admin:admin_token:admin:日本|法国' \
./scripts/run_api.sh
```

本机打开：

```text
http://127.0.0.1:8000/docs
```

局域网打开：

```text
http://<服务器IP>:8000/docs
```

局域网启动：

```bash
PUZZLEOPS_API_HOST=0.0.0.0 \
PUZZLEOPS_API_PORT=8000 \
PUZZLEOPS_API_TOKENS='ops_jp:jp_token:operator:日本,ops_fr:fr_token:operator:法国,admin:admin_token:admin:日本|法国' \
./scripts/run_api.sh
```

## 2. 6 人 token 配置

环境变量：

```bash
PUZZLEOPS_API_TOKENS='ops_jp_1:token_jp_1:operator:日本,ops_jp_2:token_jp_2:operator:日本,ops_jp_lead:token_jp_lead:admin:日本,ops_fr_1:token_fr_1:operator:法国,ops_fr_2:token_fr_2:operator:法国,ops_admin:token_admin:admin:日本|法国'
```

格式：

```text
user_id:token:role:country|country
```

角色：

- `viewer`：可看 `/api/health`、`/api/rag/search`、`/api/harness/summary`。
- `operator`：可看 viewer 能力，并可调用 `/api/value/analyze`、`/api/visual-similarity/search`。
- `admin`：预留给索引重建和飞书写入；当前飞书写入接口暂缓开放。

国家权限：

- 日本运营 token 只配置 `日本`。
- 法国运营 token 只配置 `法国`。
- 负责人 token 可配置 `日本|法国`。

## 3. 核心 API 验收

所有 `/api/*` 请求都需要：

```http
Authorization: Bearer <token>
```

### /api/health

```bash
curl -H "Authorization: Bearer token_jp_1" \
  http://127.0.0.1:8000/api/health
```

检查：

- 返回 `status=ok`。
- 返回 `version`。
- provider 状态不泄露任何 API key。

### /api/rag/search

```bash
curl -X POST http://127.0.0.1:8000/api/rag/search \
  -H "Authorization: Bearer token_jp_1" \
  -H "Content-Type: application/json" \
  -d '{"country":"日本","query":"猫咪鲤鱼是否符合日本市场价值观","top_k":5}'
```

检查：

- 返回 `citations`。
- 返回 `trace`。
- 如果 citation 为空，需要回到 RAG 知识库和 query 检查。

### /api/value/analyze

```bash
curl -X POST http://127.0.0.1:8000/api/value/analyze \
  -H "Authorization: Bearer token_jp_1" \
  -H "Content-Type: application/json" \
  -d '{"country":"日本","subject":"猫咪鲤鱼","operation_tag":"试新_日本_猫咪鲤鱼0811","js_category":"animal"}'
```

检查：

- 返回 `visual_parse.subject_content`。
- 返回 `requires_human_review=true`。
- 日本 token 请求法国数据应返回 `forbidden_country`。

### /api/harness/summary

```bash
curl -H "Authorization: Bearer token_jp_1" \
  "http://127.0.0.1:8000/api/harness/summary?country=日本"
```

检查：

- 返回真实样本和 synthetic demo 样本统计。
- 返回最近 run 或 baseline 摘要。

### /api/visual-similarity/search

```bash
curl -X POST http://127.0.0.1:8000/api/visual-similarity/search \
  -H "Authorization: Bearer token_jp_1" \
  -H "Content-Type: application/json" \
  -d '{"country":"日本","local_image_path":"/absolute/path/to/image.png","subject":"猫咪鲤鱼","top_k":5}'
```

检查：

- 图片路径不存在时返回 `missing_image` 或低置信提示，不应崩溃。
- 低置信时显示“暂无可靠历史相似图”，不强行注入历史依据。

## 4. 上线前 checklist

- `.env` 不提交到 Git。
- `PUZZLEOPS_API_TOKENS` 使用真实随机 token，不使用文档里的示例 token。
- `PUZZLEOPS_RUNTIME_DIR` 指向稳定目录，例如 `/Users/fanglemin/Desktop/puzzle_ops_runtime_prod`。
- `PUZZLEOPS_API_HOST=0.0.0.0` 只在可信局域网或服务器防火墙已配置时使用。
- 服务器防火墙只允许 6 人办公网络访问 8000 端口。
- 对外部署必须加 HTTPS 或内网/VPN。
- 飞书写入接口暂缓开放，现阶段仍从 5199 页面人工确认同步。
- 定期备份 `PUZZLEOPS_RUNTIME_DIR`，包含 SQLite、上传图片、生成图和评测报告。

## 5. 当前边界

- FastAPI 第一版是 API 服务层，不含独立登录页。
- 6 人如果需要可视化页面，当前仍使用 5199 本地页面；API 适合脚本、内部工具、未来前端或飞书机器人调用。
- `admin` 角色已设计，但高风险写操作尚未开放。
- 真实生产部署还需要进程守护，例如 launchd、systemd、supervisor 或容器平台。
