# FastAPI Service Spec

## 1. 为什么需要 FastAPI

当前本地页面运行在：

```text
http://127.0.0.1:5199
```

`127.0.0.1` 只代表你自己的电脑。6 人运营小组要共用时，需要一个可部署到服务器或局域网机器上的 API 服务：

- 支持 `/docs` 自动生成接口文档。
- 支持 token 鉴权，避免任何人都能同步飞书或调用付费模型。
- 支持脚本、内部工具、未来前端或飞书机器人复用同一套 Agent 能力。
- 保留当前 Python 本地页面作为单人 demo，不强行迁移前端。

v0.7.64 起，FastAPI runtime 增加 PostgreSQL 用户/token fallback、job、trace、metrics 和 provider health 骨架。health、RAG search、value analyze、harness summary、visual similarity search 继续保留。

## 2. 非目标

- 不替换现有 `puzzle_ops.server` 本地页面。
- 不把 `.env` 密钥暴露到接口返回。
- 不提供无鉴权写飞书接口。
- 不让 API 默认触发远程付费模型调用；仍由 `.env` 显式开关控制。

## 3. 启动方式

安装依赖后：

```bash
PYTHONPATH=. uvicorn puzzle_ops.api:app --host 127.0.0.1 --port 8000
```

本机访问：

```text
http://127.0.0.1:8000/docs
```

局域网 6 人测试：

```bash
PYTHONPATH=. uvicorn puzzle_ops.api:app --host 0.0.0.0 --port 8000
```

推荐使用脚本：

```bash
PUZZLEOPS_API_TOKENS='ops_jp:jp_token:operator:日本,ops_fr:fr_token:operator:法国,admin:admin_token:admin:日本|法国' ./scripts/run_api.sh
PUZZLEOPS_API_TOKEN=jp_token ./scripts/smoke_api.sh
```

服务器部署建议：

- 使用 HTTPS 反向代理。
- 只开放 API 端口给可信网络或 VPN。
- `.env` 放服务器本地，不进 GitHub。
- 生产环境使用 `PUZZLEOPS_DB_PROVIDER=postgres` 和阿里云 RDS PostgreSQL。
- 图片使用阿里云 OSS，数据库只存 asset 元数据。
- worker 使用 `./scripts/run_worker.sh` 或 Redis/RQ 进程守护。

## 4. 鉴权

所有 `/api/*` 接口都需要：

```http
Authorization: Bearer <token>
```

环境变量建议：

```bash
PUZZLEOPS_API_TOKENS=ops_jp:token1:operator:日本,ops_fr:token2:operator:法国,admin:token3:admin:日本|法国
```

生产环境推荐从 PostgreSQL `users` / `api_tokens` 读取 token hash；`PUZZLEOPS_API_TOKENS` 仅作为本地 demo fallback。

解析字段：

- `user_id`：用户标识。
- `token`：访问 token。
- `role`：`viewer`、`operator`、`admin`。
- `countries`：允许操作的国家列表。

权限建议：

| Role | 能力 |
|---|---|
| `viewer` | health、rag search、harness summary |
| `operator` | viewer + value analyze + trial draft |
| `admin` | operator + feishu sync + rebuild index + provider healthcheck |

## 5.1 GET /api/me

用途：确认当前 token 对应的用户、角色和国家权限。

权限：`viewer`。

响应：

```json
{
  "user_id": "ops_jp",
  "role": "operator",
  "countries": ["日本"]
}
```

## 5.2 管理接口

```text
GET  /api/admin/users
POST /api/admin/users
POST /api/admin/tokens
GET  /api/audit/logs
```

权限：`admin`。

说明：生产环境用这些接口把 6 人账号、角色和 token hash 落到 PostgreSQL，并记录审计日志。

## 5. 通用错误格式

```json
{
  "error": {
    "code": "unauthorized",
    "message": "missing bearer token",
    "request_id": "req_20260804_xxx"
  }
}
```

常见错误码：

- `unauthorized`
- `forbidden_country`
- `invalid_request`
- `provider_disabled`
- `provider_failed`
- `vector_store_unavailable`
- `feishu_field_not_found`
- `not_evaluable`

## 6. GET /api/health

用途：检查服务是否可用、当前版本、provider 配置和关键依赖状态。

权限：`viewer`。

响应示例：

```json
{
  "status": "ok",
  "version": "0.7.59",
  "runtime_dir": "/Users/fanglemin/Desktop/puzzle_ops_runtime_prod",
  "providers": {
    "vision_llm": {
      "provider": "qwen",
      "model": "qwen3-vl-plus",
      "configured": true,
      "remote_calls_enabled": true
    },
    "rag_embedding": {
      "provider": "dashscope",
      "model": "text-embedding-v4",
      "remote_calls_enabled": true
    },
    "rag_rerank": {
      "provider": "dashscope",
      "model": "qwen3-rerank",
      "remote_calls_enabled": true
    },
    "visual_embedding": {
      "provider": "qwen",
      "model": "qwen3-vl-embedding",
      "remote_calls_enabled": true
    },
    "milvus": {
      "configured": true,
      "search_enabled": true
    },
    "feishu": {
      "configured": true,
      "attachment_supported": true
    }
  }
}
```

注意：返回只能包含是否配置、provider、model，不返回任何 key/token。

## 7. POST /api/rag/search

用途：给运营或评测脚本检索日本/法国价值观、审核规则和 memory citation。

权限：`viewer`。

请求：

```json
{
  "country": "日本",
  "query": "日式庭院 少女 和服 樱花 治愈 风险",
  "task_type": "value_match",
  "top_k": 5,
  "use_query_rewrite": true,
  "require_citation": true
}
```

响应：

```json
{
  "country": "日本",
  "query": "日式庭院 少女 和服 樱花 治愈 风险",
  "rewritten_query": "日本市场 日式庭院 和服少女 樱花 治愈 拼图 价值观 审核风险",
  "citations": [
    {
      "citation_id": "jp_value_001#c2",
      "parent_id": "jp_value_001",
      "title": "日本市场价值观：季节感与治愈",
      "source": "value_rule",
      "score": 0.87,
      "snippet": "..."
    }
  ],
  "trace": {
    "bm25_candidates": 8,
    "vector_candidates": 8,
    "reranked": 5,
    "latency_ms": 420,
    "embedding_provider": "dashscope",
    "rerank_provider": "dashscope"
  }
}
```

## 8. POST /api/value/analyze

用途：对一张候选图做价值观大师分析。

权限：`operator`。

请求：

```json
{
  "country": "法国",
  "local_image_path": "/absolute/path/to/image.png",
  "operation_tag": "试新_法国_薰衣草风车0804",
  "subject": "薰衣草风车",
  "js_category": "风景",
  "include_visual_similarity": true,
  "include_rag": true,
  "dry_run": true
}
```

响应：

```json
{
  "analysis_id": "value_20260804_xxx",
  "country": "法国",
  "visual_parse": {
    "subject_content": "薰衣草田与乡村风车",
    "color_mood": "紫色花田、蓝天与暖阳形成明亮浪漫氛围",
    "composition_environment": "低机位横向构图，前景花田延展至中景风车和远处天空"
  },
  "rag_citations": [
    {
      "citation_id": "fr_value_003#c1",
      "title": "法国市场价值观：浪漫田园与地标感",
      "score": 0.91
    }
  ],
  "visual_similarity_evidence": {
    "status": "low_confidence",
    "message": "暂无可靠历史相似图",
    "best_score": 0.11
  },
  "prediction": {
    "grade": "A",
    "sa_probability": 0.72,
    "open_rate_band": "高",
    "completion_rate_band": "中",
    "avg_duration_band": "高"
  },
  "risks": [
    "无明显 IP 风险",
    "需避免模仿特定在世画家风格"
  ],
  "layout_suggestion": "适合 1-9 位或周末高曝光位试排，建议先观察开图率。",
  "requires_human_review": true
}
```

设计要求：

- `visual_parse` 必须只包含主体内容、色彩氛围、构图环境三部分。
- `rag_citations` 不能为空时才能声称“依据 RAG”。
- 图像相似低置信时只返回说明，不注入历史图证据。
- 模型输出必须保留 `requires_human_review=true`。

## 9. GET /api/harness/summary

用途：返回真实评测集和最近 Harness run 摘要。

权限：`viewer`。

请求：

```text
GET /api/harness/summary?country=日本
```

响应：

```json
{
  "dataset": {
    "real_samples": 45,
    "synthetic_samples": 278,
    "country_distribution": {
      "日本": 25,
      "法国": 20
    },
    "gold_label_coverage": 1.0
  },
  "latest_run": {
    "run_id": "run_20260804_xxx",
    "version": "0.7.59",
    "case_count": 45,
    "metrics": {
      "three_part_description_compliance": 1.0,
      "feishu_field_completeness": 1.0,
      "rag_hit_at_5": 1.0,
      "rag_precision_at_5": 0.2,
      "visual_similarity_hit_at_5": 0.6667
    },
    "failure_categories": {
      "rag_hard_negative": 8,
      "history_evidence_unrelated": 7,
      "metric_calibration_unstable": 6
    }
  }
}
```

## 10. POST /api/visual-similarity/search

用途：对候选图进行图搜图，返回相似历史图证据。

权限：`operator`。

请求：

```json
{
  "country": "日本",
  "local_image_path": "/absolute/path/to/candidate.png",
  "top_k": 5,
  "min_reference_score": 0.1208
}
```

响应：

```json
{
  "status": "low_confidence",
  "message": "暂无可靠历史相似图",
  "best_score": 0.108,
  "min_reference_score": 0.1208,
  "items": []
}
```

高置信时：

```json
{
  "status": "ok",
  "items": [
    {
      "sample_id": "jp_001",
      "operation_tag": "常规_日本_和服少女0623",
      "grade": "S",
      "score": 0.18,
      "local_image_path": "/absolute/path/history.png",
      "usable_as_value_evidence": true
    }
  ]
}
```

## 11. Job / Trace / Metrics

### POST /api/jobs/vlm-parse

用途：创建 Qwen-VL 图片解析任务。

权限：`operator`。

```json
{
  "country": "日本",
  "payload": {
    "asset_id": "asset_xxx"
  }
}
```

响应包含 `job_id`、`status=queued`、`payload`。

### POST /api/jobs/generate-derivatives

用途：创建好图衍生生成任务。生成图必须二次 VLM 解析和人工确认后才能进入提需表。

权限：`operator`。

### POST /api/jobs/feishu-sync

用途：创建飞书同步任务。接口强制保留 `requires_human_review=true`，不做无确认自动写入。

权限：`operator`。

### POST /api/jobs/rag-rebuild

用途：创建 RAG 重建任务。

权限：`admin`。

### GET /api/jobs/{job_id}

用途：查询任务状态。

状态枚举：

```text
queued / running / succeeded / failed / cancelled / needs_review
```

### POST /api/jobs/{job_id}/retry

用途：重试任务。

权限：`admin`。

### GET /api/traces/{trace_id}

用途：回查单次价值观分析、RAG、图搜图、飞书同步或 worker 任务链路。

返回字段包括 provider、model、input_summary、rag_citations、visual_similarity_evidence、output_summary、status、error_message、latency_ms。

### GET /api/metrics/latency

用途：返回 P50/P95/P99 和平均延迟，可按 `country`、`task_type` 过滤。

### GET /api/metrics/provider-health

用途：返回数据库、VLM、RAG、Milvus、视觉 embedding、飞书等 provider 配置状态，不返回任何密钥。

## 12. POST /api/feishu/sync/trial

用途：同步试新提需到飞书。

权限：`admin`。

当前状态：推荐通过 `/api/jobs/feishu-sync` 创建任务并保留人工确认。真实写入仍必须满足字段存在、附件 `file_token` 可用、失败不清空本地提需表。

请求：

```json
{
  "row_ids": ["trial_001", "trial_002"],
  "dry_run": false
}
```

响应：

```json
{
  "status": "ok",
  "synced_count": 2,
  "feishu_record_ids": ["recxxx", "recyyy"]
}
```

要求：

- 只写真实存在的飞书字段。
- 附件字段必须上传文件获得 `file_token` 后再写入。
- 同步失败不清空本地提需表。
- 返回飞书错误时保留 `log_id`，方便排查。

## 13. 测试计划

v0.7.60 已新增：

- `tests/test_api.py::test_health_requires_token`
- `tests/test_api.py::test_health_redacts_secrets`
- `tests/test_api.py::test_rag_search_returns_citations_with_trace`
- `tests/test_api.py::test_value_analyze_requires_country_permission`
- `tests/test_api.py::test_value_analyze_keeps_human_review_flag`
- `tests/test_api.py::test_harness_summary_separates_real_and_synthetic_samples`
- `tests/test_api.py::test_openapi_schema_exposes_core_agent_routes`

后续开放飞书写接口时再新增：

- `tests/test_api.py::test_feishu_sync_requires_admin`
- `tests/test_api.py::test_feishu_sync_does_not_clear_rows_on_failure`
- `tests/test_production_stack.py::test_api_uses_repository_tokens_and_exposes_jobs_traces_metrics`
- `tests/test_production_stack.py::test_worker_executes_known_job_and_records_trace`
- `tests/test_api.py::test_feishu_sync_uploads_attachment_file_token_before_record_write`

回归命令：

```bash
ANALYSIS_LLM_ENABLE_REMOTE_CALLS=0 \
RAG_ENABLE_REMOTE_CALLS=false \
RAG_EMBEDDING_PROVIDER=local \
RAG_RERANK_PROVIDER=local \
VISUAL_EMBEDDING_ENABLE_REMOTE_CALLS=false \
VISUAL_MILVUS_ENABLE_REMOTE_CALLS=false \
VISION_LLM_PROVIDER=qwen \
QWEN_API_KEY= \
IMAGE_GENERATION_PROVIDER=mock \
PYTHONPATH=. pytest tests -q
```
