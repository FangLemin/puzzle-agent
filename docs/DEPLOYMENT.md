# PuzzleOps Deployment Guide

日期：2026-08-13

本文件用于 v0.7.64+ 上线前验收：一个人继续用本地页面，6 人运营团队通过 FastAPI、PostgreSQL 主库、OSS 图片存储和 worker 任务队列共用 Agent 能力。

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
PUZZLEOPS_API_HOST=0.0.0.0 \
PUZZLEOPS_API_PORT=8000 \
PUZZLEOPS_API_TOKENS='ops_jp:jp_token:operator:日本,ops_fr:fr_token:operator:法国,admin:admin_token:admin:日本|法国' \
./scripts/run_api.sh
```

浏览器打开：

```text
http://127.0.0.1:8000/docs
http://<服务器IP>:8000/docs
```

## 2. 生产资源配置

PostgreSQL 主库：

```bash
PUZZLEOPS_DB_PROVIDER=postgres
DATABASE_URL=postgresql+psycopg://puzzleops_user:password@rds-host:5432/puzzleops
```

PostgreSQL 存 users、tokens、audit logs、assets、jobs、trace events、Memory、Harness、RAG metadata 和业务提需数据。SQLite 只保留为本地 demo fallback。

初始化 RDS schema 有两种方式：

```bash
DATABASE_URL=postgresql+psycopg://puzzleops_user:password@rds-host:5432/puzzleops \
alembic upgrade head
```

或使用项目脚本直接执行同一份 schema：

```bash
DATABASE_URL=postgresql+psycopg://puzzleops_user:password@rds-host:5432/puzzleops \
python scripts/init_postgres_schema.py
```

RDS smoke：

```bash
DATABASE_URL=postgresql+psycopg://puzzleops_user:password@rds-host:5432/puzzleops \
PUZZLEOPS_INIT_DB=1 \
python scripts/smoke_postgres.py
```

输出里只会显示脱敏后的 `safe_database_url`，不会打印真实密码。

OSS 图片存储：

```bash
ASSET_STORAGE_PROVIDER=oss
ALIYUN_OSS_ENDPOINT=https://oss-cn-xxx.aliyuncs.com
ALIYUN_OSS_BUCKET=puzzleops-assets
ALIYUN_OSS_ACCESS_KEY_ID=...
ALIYUN_OSS_ACCESS_KEY_SECRET=...
ALIYUN_OSS_PUBLIC_BASE_URL=https://assets.example.com
```

数据库只保存 `object_key`、URL、hash、content type、size、飞书 `file_token`。图片二进制存 OSS，不依赖本机路径。

v0.7.66 起，新上传/生成图片会创建 asset 记录。飞书同步时：

- asset 已有 `feishu_file_token`：直接复用。
- asset 没有 token 但服务端本地文件仍存在：先上传飞书附件，再回写 token。
- 本地路径不可用但 OSS URL 存在：页面仍可预览，飞书附件需要后续 worker 从 OSS 下载后上传。

Worker：

```bash
./scripts/run_worker.sh
```

ECS 上建议用 systemd/supervisor 守护 API 和 worker 两个进程。Redis/RQ 可作为队列后端，当前 `puzzle_ops.worker.execute_job_once` 是统一执行入口，本地测试使用无 Redis fallback。

## 3. 6 人权限

本地 demo 可继续用：

```bash
PUZZLEOPS_API_TOKENS='ops_jp_1:token_jp_1:operator:日本,ops_jp_2:token_jp_2:operator:日本,ops_fr_1:token_fr_1:operator:法国,ops_fr_2:token_fr_2:operator:法国,ops_admin:token_admin:admin:日本|法国'
```

生产推荐通过 PostgreSQL 用户表和 token 表管理：

```text
POST /api/admin/users
POST /api/admin/tokens
```

角色：

- `viewer`：查看 health、RAG、Harness、trace、metrics。
- `operator`：viewer + 上传/解析/价值观分析/创建 job。
- `admin`：operator + 用户管理、RAG 重建、任务重试、飞书同步确认。

国家权限：

- 日本运营 token 只配置 `日本`。
- 法国运营 token 只配置 `法国`。
- 负责人 token 可配置 `日本|法国`。

## 4. 核心 API 验收

所有 `/api/*` 请求都需要：

```http
Authorization: Bearer <token>
```

检查当前用户：

```bash
curl -H "Authorization: Bearer token_jp_1" http://127.0.0.1:8000/api/me
```

检查服务：

```bash
curl -H "Authorization: Bearer token_jp_1" http://127.0.0.1:8000/api/health
```

RAG 检索：

```bash
curl -X POST http://127.0.0.1:8000/api/rag/search \
  -H "Authorization: Bearer token_jp_1" \
  -H "Content-Type: application/json" \
  -d '{"country":"日本","query":"猫咪鲤鱼是否符合日本市场价值观","top_k":5}'
```

价值观分析：

```bash
curl -X POST http://127.0.0.1:8000/api/value/analyze \
  -H "Authorization: Bearer token_jp_1" \
  -H "Content-Type: application/json" \
  -d '{"country":"日本","subject":"猫咪鲤鱼","operation_tag":"试新_日本_猫咪鲤鱼0813","js_category":"animal"}'
```

Harness 摘要：

```bash
curl -H "Authorization: Bearer token_jp_1" \
  "http://127.0.0.1:8000/api/harness/summary?country=日本"
```

视觉相似检索：

```bash
curl -X POST http://127.0.0.1:8000/api/visual-similarity/search \
  -H "Authorization: Bearer token_jp_1" \
  -H "Content-Type: application/json" \
  -d '{"country":"日本","local_image_path":"/absolute/path/to/image.png","subject":"猫咪鲤鱼","top_k":5}'
```

创建任务：

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/vlm-parse \
  -H "Authorization: Bearer token_jp_1" \
  -H "Content-Type: application/json" \
  -d '{"country":"日本","payload":{"asset_id":"asset_xxx"}}'
```

查询任务和 trace：

```bash
curl -H "Authorization: Bearer token_jp_1" http://127.0.0.1:8000/api/jobs/<job_id>
curl -H "Authorization: Bearer token_jp_1" http://127.0.0.1:8000/api/traces/<trace_id>
curl -H "Authorization: Bearer token_jp_1" http://127.0.0.1:8000/api/metrics/latency
```

验收脚本：

```bash
PUZZLEOPS_API_TOKEN=token_jp_1 ./scripts/smoke_api.sh
```

## 5. 上线前 Checklist

- `.env` 不提交到 Git。
- 文档示例 token 全部替换为真实随机 token。
- 生产配置 `PUZZLEOPS_DB_PROVIDER=postgres` 和 `DATABASE_URL`。
- OSS bucket、RDS 白名单、ECS 安全组只开放给可信网络。
- `PUZZLEOPS_RUNTIME_DIR` 指向稳定目录，例如 `/opt/puzzleops/runtime`。
- `PUZZLEOPS_API_HOST=0.0.0.0` 只在防火墙已配置时使用。
- 对外部署必须加 HTTPS 或内网/VPN。
- 飞书同步可以进入 job，但必须保留人工确认。
- 兼容旧验收口径：飞书写入接口暂缓开放为无确认直写，正式写入必须走 job 和人工确认。
- 定期备份 RDS、OSS bucket 和 `PUZZLEOPS_RUNTIME_DIR` 里的报告。

## 6. 当前边界

- 第一版不新建 Vue/React 管理端，保留现有页面 + FastAPI。
- Redis/RQ 推荐用于 ECS worker，当前本地 worker fallback 方便测试和演示。
- 真实样本仍偏小，评测报告用于上线验收与面试说明，不声称大规模生产稳定性。
