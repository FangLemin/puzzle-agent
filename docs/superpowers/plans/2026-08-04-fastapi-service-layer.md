# FastAPI Service Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 6 人运营团队提供可部署的 PuzzleOps Agent API 服务入口。

**Architecture:** FastAPI 作为薄服务层，保留现有 `PuzzleOpsAgent` 业务编排；API 层只负责 Pydantic schema、Bearer token 鉴权、国家权限、结构化响应和错误格式。

**Tech Stack:** Python, FastAPI, Uvicorn, Pydantic, pytest, FastAPI TestClient.

## Global Constraints

- 保留现有 Python 标准库本地页面，不迁移前端。
- `.env` 不提交，API 只返回 provider 配置状态，不返回任何 key/token。
- 第一版暂缓开放真实飞书写接口，避免多人共用时误写生产表。
- 所有新增行为先写测试，再实现。

---

### Task 1: API Dependency and Tests

**Files:**
- Modify: `requirements.txt`
- Create: `tests/test_api.py`

**Interfaces:**
- Produces: `parse_api_tokens(raw: str | None) -> dict[str, ApiUser]`
- Produces: `create_app(agent: PuzzleOpsAgent | None = None) -> FastAPI`

- [x] **Step 1: Write failing tests**

Write tests for token parsing, auth failure, secret redaction, OpenAPI routes, RAG search, country permission, value analyze and harness summary.

- [x] **Step 2: Verify red**

Run:

```bash
PYTHONPATH=. pytest tests/test_api.py -q
```

Expected first failure: `ModuleNotFoundError: No module named 'fastapi'`.

- [x] **Step 3: Add dependencies**

Add:

```text
fastapi>=0.115,<1
uvicorn[standard]>=0.30,<1
```

- [x] **Step 4: Verify next red**

Run:

```bash
PYTHONPATH=. pytest tests/test_api.py -q
```

Expected second failure: `ModuleNotFoundError: No module named 'puzzle_ops.api'`.

### Task 2: FastAPI Runtime

**Files:**
- Create: `puzzle_ops/api.py`

**Interfaces:**
- Consumes: `PuzzleOpsAgent.value_audit_rag_answer`
- Consumes: `PuzzleOpsAgent.apply_value_master`
- Consumes: `PuzzleOpsAgent.harness_summary`
- Consumes: `PuzzleOpsAgent.similar_visual_history_for_candidate`
- Produces: `app = create_app()`

- [x] **Step 1: Implement app factory**

Create `create_app(agent=None)` and attach `app.state.agent`.

- [x] **Step 2: Implement auth**

Parse `PUZZLEOPS_API_TOKENS=user:token:role:country|country` and require `Authorization: Bearer <token>`.

- [x] **Step 3: Implement endpoints**

Implement:

```text
GET /api/health
POST /api/rag/search
POST /api/value/analyze
GET /api/harness/summary
POST /api/visual-similarity/search
```

- [x] **Step 4: Verify green**

Run:

```bash
PYTHONPATH=. pytest tests/test_api.py -q
```

Expected: all API tests pass.

### Task 3: Documentation and Versioning

**Files:**
- Modify: `README.md`
- Modify: `docs/API_SPEC.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `CHANGELOG.md`
- Modify: `VERSION`

- [x] **Step 1: Document startup**

Document:

```bash
PYTHONPATH=. uvicorn puzzle_ops.api:app --host 127.0.0.1 --port 8000
```

- [x] **Step 2: Document LAN usage**

Document:

```bash
PYTHONPATH=. uvicorn puzzle_ops.api:app --host 0.0.0.0 --port 8000
```

- [x] **Step 3: Update version**

Set `VERSION` to `0.7.60`.

- [x] **Step 4: Run regression and commit**

Run compile checks and full pytest, then commit.
