# 版本记录

这个文件用来记录每一版做了什么、为什么改、当前还存在哪些问题。以后每次你让我修改功能，我会先提交旧版本，再在这里追加阶段总结。

## v0.3.47 - DashScope RAG Provider 远程调用门禁

日期：2026-06-16

阶段目标：

- 将 v0.3.46 的 provider 抽象继续推进到“可真实接 DashScope embedding/rerank”的工程状态。
- 同时保留业务安全门禁：默认不发外部请求，避免误产生费用或把业务数据发到远程服务。

已完成：

- `puzzle_ops/rag.py` 新增真实 provider：
  - `DashScopeEmbeddingProvider`
  - `DashScopeRerankProvider`
- DashScope embedding provider：
  - 支持通过 transport 请求 embedding。
  - 支持解析 OpenAI-compatible `data[].embedding` 返回。
  - 支持解析 DashScope `output.embeddings[].embedding` 返回。
  - 内置文本 embedding cache，避免同一文本重复请求。
  - 调用异常时回退本地 token/cosine 相似度。
- DashScope rerank provider：
  - 支持通过 transport 请求 rerank。
  - 支持解析 `results[].relevance_score` / `results[].score`。
  - 支持解析 `output.results[]` 返回。
  - 调用异常时回退本地规则 rerank。
- 远程调用安全门禁：
  - `RAG_API_KEY` 或 `DASHSCOPE_API_KEY` 只表示 key 就绪。
  - 只有 `RAG_ENABLE_REMOTE_CALLS=true` 时，`providers_from_config(...)` 才返回真实 DashScope provider。
  - 未开启远程调用时，即使配置了 provider/key，也继续使用本地 fallback。
- `.env` 支持：
  - `RAG_EMBEDDING_PROVIDER=dashscope`
  - `RAG_EMBEDDING_MODEL=text-embedding-v3`
  - `RAG_RERANK_PROVIDER=dashscope`
  - `RAG_RERANK_MODEL=gte-rerank-v2`
  - `RAG_API_KEY` 或 `DASHSCOPE_API_KEY`
  - `RAG_ENABLE_REMOTE_CALLS=true`
  - `RAG_EMBEDDING_ENDPOINT`
  - `RAG_RERANK_ENDPOINT`
- `value_audit_rag_summary(...)` 新增：
  - `provider_remote_ready`
  - `provider_remote_calls_enabled`
- README 补充 DashScope RAG Provider 使用说明。

当前限制：

- 本轮真实 provider 已具备 transport 和解析能力，但默认仍通过远程调用门禁关闭。
- 尚未把远程调用耗时、费用估算、请求次数写入 Harness 指标。
- 尚未做 embedding 向量持久化缓存；当前只做 provider 内存 cache。

验证记录：

- `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py::test_agent_rag_summary_exposes_embedding_and_rerank_provider_names tests/test_agents.py::test_agent_rag_summary_marks_remote_ready_only_with_api_key -q`：11 passed。
- `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_renderer.py -q`：59 passed。
- `PYTHONPATH=. pytest tests -q`：181 passed。
- `find . -maxdepth 3 -type f \\( -name 'package.json' -o -name 'vite.config.*' -o -name '*.js' -o -name '*.ts' -o -name '*.tsx' -o -name '*.jsx' -o -name '*.vue' \\) -not -path './.git/*' -print`：无输出。
- `find puzzle_ops tests -type f -not -name '*.py' -not -path '*/__pycache__/*' -print`：无输出。
- `git diff --check`：无输出。

## v0.3.46 - RAG Embedding/Rerank Provider 化

日期：2026-06-16

阶段目标：

- 在 v0.3.45 本地 RAG 知识库基础上，把“向量召回”和“精排”升级为可替换 provider 接口。
- 保持默认纯 Python、本地可运行，同时为后续接 DashScope/OpenAI/Cohere/自建 embedding 与 reranker 留好工程入口。

已完成：

- `puzzle_ops/rag.py` 新增：
  - `RagProviderConfig`
  - `LocalEmbeddingProvider`
  - `LocalRerankProvider`
  - `ConfiguredEmbeddingProvider`
  - `ConfiguredRerankProvider`
  - `providers_from_config(...)`
- `HybridRagRetriever` 支持注入：
  - `embedding_provider`
  - `rerank_provider`
- 检索命中 reason 增加 provider 证据：
  - BM25 分数。
  - Embedding provider 名称与相似度。
  - Rerank provider 名称。
- `.env` 预留配置：
  - `RAG_EMBEDDING_PROVIDER`
  - `RAG_EMBEDDING_MODEL`
  - `RAG_RERANK_PROVIDER`
  - `RAG_RERANK_MODEL`
- `PuzzleOpsAgent`：
  - 初始化时读取 RAG provider 配置。
  - `value_audit_rag_answer(...)` 使用 provider 化 retriever。
  - `value_audit_rag_summary(...)` 输出 embedding/rerank provider、model、configured 和 status。
- 多模态底座页面：
  - “价值观与审核 RAG”展示 Embedding / Rerank provider 与模型。
  - 明确当前是本地 fallback 还是外部 provider 已配置。
- README 补充 RAG Provider 说明。

当前限制：

- 本轮完成 provider 抽象、配置读取、状态展示和注入点；没有直接调用远程 embedding/rerank API。
- `ConfiguredEmbeddingProvider` / `ConfiguredRerankProvider` 目前仍继承本地 fallback 算法，避免未确认计费、网络、鉴权和数据合规边界时误请求外部服务。
- 下一步可新增真实 `DashScopeEmbeddingProvider` / `DashScopeRerankProvider`，并加超时、错误分类、缓存和成本记录。

验证记录：

- `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py::test_agent_rag_summary_exposes_embedding_and_rerank_provider_names tests/test_agents.py::test_agent_builds_value_audit_rag_context_with_citations tests/test_renderer.py::test_multimodal_runtime_page_shows_profile_candidates_and_evidence -q`：8 passed。
- `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_renderer.py -q`：54 passed。
- `PYTHONPATH=. pytest tests -q`：176 passed。
- `find . -maxdepth 3 -type f \\( -name 'package.json' -o -name 'vite.config.*' -o -name '*.js' -o -name '*.ts' -o -name '*.tsx' -o -name '*.jsx' -o -name '*.vue' \\) -not -path './.git/*' -print`：无输出。
- `find puzzle_ops tests -type f -not -name '*.py' -not -path '*/__pycache__/*' -print`：无输出。
- `git diff --check`：无输出。

## v0.3.45 - 价值观与审核规则 RAG 知识库

日期：2026-06-16

阶段目标：

- 将项目里的 RAG 从“轻量关键词召回/展示概念”，推进为可解释、可溯源、可接入价值观大师的本地 RAG 知识层。
- 让 RAG 主要服务两个业务目标：
  - 提需时判断图片是否符合日本/法国沉淀价值观。
  - 基于价值观与审核依据发散新的拼图内容方向，同时降低 LLM 幻觉。

已完成：

- 新增 `puzzle_ops/rag.py`：
  - `RagDocument`：父文档，承载审核手册、国家价值观、人工审批价值观、历史样本事实、四层 memory 事实。
  - `RagChunk`：子知识块，保留 `parent_id`、`chunk_id`、国家、来源类型和 metadata。
  - `HybridRagRetriever`：本地多路召回与精排。
  - `build_rag_prompt(...)`：拼接带 citation 的 LLM prompt。
- Chunk 策略：
  - 按中文/英文标点进行语义边界切分。
  - 支持 `overlap_sentences`，避免规则被切断后上下文丢失。
- SQLite 新增父子 RAG 存储：
  - `rag_documents`
  - `rag_chunks`
  - `save_rag_index(...)`
  - `rag_documents(...)`
  - `rag_chunks(...)`
- 多路召回：
  - BM25 风格词面召回。
  - 本地 token 向量近似召回。
  - rerank 精排：综合国家匹配、知识来源类型、审核风险意图和精确短语命中。
- Agent 接入：
  - `build_value_audit_rag_index(country)` 构建国家 RAG 知识库。
  - `value_audit_rag_answer(country, query)` 返回带引用依据的 prompt/context/citations。
  - `apply_value_master(...)` 先进行 RAG Top-K 召回，再把引用依据传给真实视觉 LLM 判断价值观匹配。
- 多模态底座页面新增“价值观与审核 RAG”：
  - 展示父子知识块数量。
  - 展示多路召回策略。
  - 展示本次引用依据和上下文摘要。
- README 补充 RAG 说明。

当前限制：

- 当前向量召回是纯 Python token/cosine 近似，不是外部 embedding 模型。
- 当前 rerank 是本地规则精排，还未接专业 reranker。
- 审核手册仍来自本地 docx 段落；更细的标题层级、父章节编号和版本号可以后续继续增强。
- RAG 答案生成仍依赖已配置的真实视觉 LLM；未配置时不会伪造价值观判断。

验证记录：

- `PYTHONPATH=. pytest tests/test_rag.py tests/test_storage_runtime.py::test_repository_stores_parent_child_rag_index tests/test_agents.py::test_agent_builds_value_audit_rag_context_with_citations tests/test_agents.py::test_value_master_passes_rag_citations_to_llm_prompt tests/test_agents.py::test_value_master_writes_value_match_to_trial_row tests/test_agents.py::test_value_master_uses_current_trial_subject_instead_of_default_template tests/test_vision_llm.py::test_openai_client_judges_value_match_with_current_visual_context tests/test_vision_llm.py::test_qwen_client_judges_value_match_with_chat_completions_payload tests/test_renderer.py::test_multimodal_runtime_page_shows_profile_candidates_and_evidence -q`：11 passed。
- `PYTHONPATH=. pytest tests/test_storage_runtime.py tests/test_agents.py tests/test_vision_llm.py tests/test_renderer.py -q`：63 passed。
- `PYTHONPATH=. pytest tests/test_agents.py::test_agent_builds_value_audit_rag_context_with_citations tests/test_rag.py -q`：4 passed。
- `PYTHONPATH=. pytest tests -q`：173 passed。
- `find . -maxdepth 3 -type f \\( -name 'package.json' -o -name 'vite.config.*' -o -name '*.js' -o -name '*.ts' -o -name '*.tsx' -o -name '*.jsx' -o -name '*.vue' \\) -not -path './.git/*' -print`：无输出。
- `find puzzle_ops tests -type f -not -name '*.py' -not -path '*/__pycache__/*' -print`：无输出。

## v0.3.44 - 四层 Memory 架构雏形

日期：2026-06-15

阶段目标：

- 将项目中的 memory 从单一 `agent_memory` 混合记录，推进为更清晰的四层业务记忆模型。
- 对齐“感知记忆、短期记忆、长期记忆、结构化抽取事实”的 Agent Harness 叙事。

已完成：

- SQLite 新增 `layered_memory` 表：
  - `memory_layer`：`perception`、`working`、`long_term`、`facts`。
  - `memory_type`：具体业务类型，例如 `trial_image_parse`、`generation_trace`、`value_rule_approval`、`image_semantic_fact`。
  - `payload`：结构化 JSON。
- `PuzzleRepository` 新增：
  - `add_layered_memory(...)`
  - `layered_memories(...)`
- `PuzzleOpsAgent` 新增四层写入接口：
  - `record_perception_memory`
  - `record_working_memory`
  - `record_long_term_memory`
  - `record_extracted_fact`
  - `memory_overview`
- 业务接入：
  - 试新图片上传解析写入感知记忆和结构化事实。
  - 生成任务 trace 写入短期记忆。
  - 价值观候选通过后写入长期记忆。
- 多模态底座新增“四层 Memory 概览”：
  - 展示四层 memory 的数量和最新 payload 摘要。
  - 让 memory 不再只是底层数据库记录，而是可被运营/面试官看见的 Agent 能力。
- README 增加四层 Memory 说明。

当前限制：

- 本轮是四层 memory schema 和写入路径雏形，尚未接向量检索。
- 结构化事实目前覆盖试新图片的主体、国家、运营 tag、图片路径等核心字段，后续可继续扩展 value_labels、risk_labels、confidence、source_run_id。
- 旧的 `agent_memory` 仍保留用于兼容 HITL、generation event 和历史功能。

验证记录：

- `PYTHONPATH=. pytest tests/test_storage_runtime.py::test_repository_stores_layered_memory_payloads tests/test_agents.py::test_agent_records_four_layer_memory_types tests/test_server.py::test_upload_trial_images_writes_real_openai_semantics_when_configured tests/test_server.py::test_generate_trial_derivatives_failure_keeps_row_and_shows_message tests/test_server.py::test_approve_value_candidate_action_writes_hitl_memory tests/test_renderer.py::test_multimodal_runtime_page_shows_profile_candidates_and_evidence tests/test_renderer.py::test_multimodal_runtime_page_shows_approved_candidate_after_hitl_action -q`：7 passed。
- `PYTHONPATH=. pytest tests/test_storage_runtime.py tests/test_agents.py tests/test_server.py tests/test_renderer.py -q`：92 passed。
- `PYTHONPATH=. pytest tests -q`：167 passed。
- `find . -maxdepth 3 -type f \\( -name 'package.json' -o -name 'vite.config.*' -o -name '*.js' -o -name '*.ts' -o -name '*.tsx' -o -name '*.jsx' -o -name '*.vue' \\) -not -path './.git/*' -print`：无输出。
- `find puzzle_ops tests -type f -not -name '*.py' -not -path '*/__pycache__/*' -print`：无输出。

## v0.3.43 - 生成 Trace 接入 Harness 指标

日期：2026-06-15

阶段目标：

- 将 v0.3.42 的生成 trace 从“同步记录页可回放”推进到 Harness Run / Eval Dashboard。
- 让好图衍生链路可被评测，而不只是可观察。

已完成：

- `AgentHarness` 新增 generation event 指标聚合：
  - `生成Trace完整率`：生成事件是否具备状态、provider、模型、来源 tag、二次审核、飞书附件状态；成功事件还要求 task_id 和生成图路径，失败事件要求 error_type。
  - `二次审核通过率`：generation event 中 `second_review_status=passed` 的占比。
  - `飞书附件Ready率`：generation event 中 `feishu_attachment_status=ready` 的占比。
  - `生成失败可分类率`：失败事件中具备有效 error_type 的占比。
- Agent 评测页新增“生成失败类型分布”：
  - 从本地 generation event memory 读取失败事件。
  - 按 `error_type` 聚合次数，例如 quota_exceeded、model_deprecated、timeout 等。
- README 增加 Harness 生成 trace 说明，明确无真实生成事件时指标为 0，不伪造生成效果。
- 继续审查 Python-only 边界：未发现 JS/TS/Vue/React/package.json。

当前限制：

- 指标来自本地 generation event memory，尚未落为独立 generation_runs 表。
- 失败类型分布只统计当前国家的本地事件；后续可扩展为按 run_id、版本、provider、模型分组。

验证记录：

- `PYTHONPATH=. pytest tests/test_harness.py::test_harness_metrics_include_generation_trace_replay_events tests/test_renderer.py::test_eval_page_shows_clear_agent_evaluation_workflow -q`：2 passed。
- `PYTHONPATH=. pytest tests/test_harness.py tests/test_renderer.py tests/test_agents.py -q`：58 passed。
- `find . -maxdepth 3 -type f \\( -name 'package.json' -o -name 'vite.config.*' -o -name '*.js' -o -name '*.ts' -o -name '*.tsx' -o -name '*.jsx' -o -name '*.vue' \\) -not -path './.git/*' -print`：无输出。
- `PYTHONPATH=. pytest tests -q`：165 passed。

## v0.3.42 - 生成到审核到飞书附件 Trace 字段

日期：2026-06-15

阶段目标：

- 将生成任务回放从基础状态扩展为更完整的好图衍生 trace。
- 明确记录“生成图是否通过二次 VLM 审核、是否具备飞书附件同步资格”。
- 审查项目是否继续保持 Python 编码主线。

已完成：

- 生成任务事件新增 trace 字段：
  - `task_id`：生成 provider 返回的生成图 ID 汇总。
  - `source_operation_tag`：来源提需运营 tag。
  - `generated_image_paths`：生成图本地路径汇总。
  - `second_review_status`：`passed` / `blocked` / `not_started`。
  - `feishu_attachment_status`：`ready` / `blocked`。
- `/generate_trial_derivatives` 成功时：
  - 将生成图 ID、路径、来源 tag、二次审核状态、飞书附件资格写入 `generation_event`。
  - 真实生成图二次审核全部通过时标记 `ready`。
  - mock 或审核未通过时标记 `blocked`，避免误同步。
- `/generate_trial_derivatives` 失败时：
  - 写入来源 tag。
  - 将二次审核标记为 `not_started`。
  - 将飞书附件状态标记为 `blocked`。
- 页面增强：
  - 试新页“最近一次生成任务”展示 task、来源 tag、生成图路径、二次审核和飞书附件状态。
  - 同步记录页“生成任务回放”展示 task、来源 tag、二次审核和飞书附件状态。
- Python-only 审查：
  - 未发现 `package.json`、JS/TS/Vue/React 文件。
  - `puzzle_ops/` 和 `tests/` 下均为 Python 文件。

当前限制：

- `task_id` 当前使用 provider 返回的生成图 `image_id` 汇总；DashScope 原始异步 task_id 尚未单独透出到事件结构。
- 生成图路径是本地路径，迁移到远程标注平台或飞书附件展示时仍需要对象存储/静态托管 URL。

验证记录：

- `PYTHONPATH=. pytest tests/test_server.py::test_generate_trial_derivatives_creates_two_audited_reference_rows tests/test_server.py::test_real_generation_derivatives_require_vlm_second_review_before_sync tests/test_server.py::test_generate_trial_derivatives_failure_keeps_row_and_shows_message tests/test_renderer.py::test_trial_page_shows_recent_generation_event tests/test_renderer.py::test_sync_page_shows_persisted_generation_events -q`：5 passed。
- `PYTHONPATH=. pytest tests/test_agents.py tests/test_server.py tests/test_renderer.py -q`：86 passed。
- `find . -maxdepth 3 -type f \\( -name 'package.json' -o -name 'vite.config.*' -o -name '*.js' -o -name '*.ts' -o -name '*.tsx' -o -name '*.jsx' -o -name '*.vue' \\) -not -path './.git/*' -print`：无输出。
- `find puzzle_ops tests -type f -not -name '*.py' -not -path '*/__pycache__/*' -print`：无输出。
- `PYTHONPATH=. pytest tests -q`：164 passed。

## v0.3.41 - 生成任务持久化回放

日期：2026-06-15

阶段目标：

- 将 v0.3.40 的“最近一次生成任务”从页面内存状态推进到可持久化记录。
- 让好图衍生生成链路更接近 Agent Harness 的 trace / replay 闭环。

已完成：

- `PuzzleOpsAgent` 新增生成任务事件持久化能力：
  - `record_generation_event(country, event)` 将生成事件写入本地 SQLite memory。
  - `generation_events(country)` 读取结构化事件，用于页面回放和后续 Harness 接入。
- `/generate_trial_derivatives` 成功和失败时都会写入生成事件：
  - 成功记录生成张数和等待二次审核说明。
  - 失败记录 provider、model、endpoint、error_type 和原始错误说明。
- 同步记录页新增“生成任务回放”区：
  - 展示最近生成任务的状态、Provider、模型、错误类型和说明。
  - 没有生成任务时显示空态。
- README 补充生成事件写入本地 memory、可在同步记录页回放的说明。

当前限制：

- 当前生成事件使用 agent memory 轻量持久化，还不是单独 generation_runs 表。
- 后续可以继续把 task_id、原始 provider 响应摘要、二次审核结果和飞书附件同步结果关联成完整 run trace。

验证记录：

- `PYTHONPATH=. pytest tests/test_agents.py::test_agent_persists_generation_events_for_replay tests/test_server.py::test_generate_trial_derivatives_failure_keeps_row_and_shows_message tests/test_renderer.py::test_sync_page_shows_persisted_generation_events -q`：3 passed。
- `PYTHONPATH=. pytest tests/test_agents.py tests/test_server.py tests/test_renderer.py -q`：86 passed。
- `PYTHONPATH=. pytest tests -q`：164 passed。

## v0.3.40 - 生成任务状态回放与错误分类

日期：2026-06-15

阶段目标：

- 继续补齐真实好图衍生生成的工程可观测性。
- 让 DashScope / 通义万相等 provider 出错时，不只是展示原始报错，而是能归类和回放最近一次生成任务。

已完成：

- `AppState` 新增最近一次生成任务事件：
  - 记录 `status`、`provider`、`model`、`endpoint`、`error_type`、`message`。
  - 生成成功时记录成功状态和生成张数说明。
  - 生成失败时记录失败状态和错误类型。
- 试新页解析状态区新增“最近一次生成任务”：
  - 展示成功/失败状态。
  - 展示 provider、model、错误类型和说明。
  - 便于运营/开发在页面内回看最近一次生成链路结果。
- 新增生成错误分类：
  - `quota_exceeded`：额度不足、余额不足、quota/balance 类错误。
  - `model_deprecated`：模型下线、deprecated/retired/model not found 类错误。
  - `timeout`：任务超时。
  - `auth_error`：API Key、401/403、鉴权或权限错误。
  - `config_missing`：provider/key/配置缺失。
  - `response_schema`：返回结构缺字段，例如 task_id/results/image_base64。
  - `unknown`：未命中的其他错误。
- README 补充最近一次生成任务回放说明。

当前限制：

- 错误分类基于本地报错文本规则，不主动请求云端错误码文档。
- 后续可以继续把 DashScope 原始 task_id、任务状态和 provider 原始响应摘要持久化到 Harness run 或 sync record。

验证记录：

- `PYTHONPATH=. pytest tests/test_server.py::test_classify_generation_error_for_common_provider_failures tests/test_server.py::test_generate_trial_derivatives_failure_keeps_row_and_shows_message tests/test_server.py::test_generate_trial_derivatives_creates_two_audited_reference_rows tests/test_renderer.py::test_trial_page_shows_recent_generation_event tests/test_renderer.py::test_trial_page_has_generation_provider_diagnostic_action -q`：10 passed。
- `PYTHONPATH=. pytest tests/test_server.py tests/test_renderer.py tests/test_harness.py -q`：76 passed。
- `PYTHONPATH=. pytest tests -q`：162 passed。

## v0.3.39 - 生成 Provider 诊断入口

日期：2026-06-15

阶段目标：

- 继续推进 Agent Harness + 真实好图衍生生成主线，把生成 provider 从“可配置”提升到“可观测、可诊断”。
- 让运营/开发在点击真实生成前，能确认 provider、模型和 endpoint 配置状态。

已完成：

- 试新页解析状态区新增“生成 Provider 诊断”：
  - 展示 `provider`、`configured`、`model`、`endpoint`。
  - 保留原有 provider message，便于识别未配置、mock、cloud、DashScope 等状态。
  - 增加紧凑样式，降低对试新页面信息密度的影响。
- 新增“检查生成 Provider”按钮：
  - 调用 `/check_generation_provider`。
  - 复用现有 healthcheck，不触发真实图像生成。
  - 将诊断结果写入页面提示，便于排查配置缺失、模型名、endpoint 等问题。
- README 补充试新页 provider 诊断说明。

当前限制：

- 当前诊断只做本地配置级 healthcheck，不主动请求云端服务，避免无意消耗额度。
- 后续可继续增加 DashScope live dry-run、额度/权限错误码分类、最近一次生成任务状态回放。

验证记录：

- `PYTHONPATH=. pytest tests/test_renderer.py::test_trial_page_has_generation_provider_diagnostic_action tests/test_server.py::test_check_generation_provider_action_reports_diagnostic_status -q`：2 passed。
- `PYTHONPATH=. pytest tests/test_renderer.py tests/test_server.py tests/test_harness.py -q`：69 passed。
- `PYTHONPATH=. pytest tests -q`：155 passed。

## v0.3.38 - 好图衍生生成失败页面级反馈

日期：2026-06-15

阶段目标：

- 让真实图像生成 provider 出错时，试新页面能给运营人员明确反馈。
- 避免生成失败后页面崩溃、提需丢失，或出现误导性的假生成图。

已完成：

- `/generate_trial_derivatives` 增加生成异常兜底：
  - 保留原始试新提需行。
  - 清空本次生成 rows 和 preview，避免误同步。
  - 写入页面级 `sync_message`，展示 provider 返回的失败原因。
  - 将失败信息补入备注，便于后续复盘。
- 新增服务端测试覆盖 DashScope 额度不足等异常场景。
- 新增渲染测试确认试新页面能展示生成失败提示。
- README 补充 DashScope 失败态说明：失败、超时或额度不足时不伪造生成图。

当前限制：

- 本轮只处理 provider 抛错后的页面反馈和状态保护，不做自动重试。
- 真实云端 provider 的错误码分类、重试间隔和费用提示仍可在后续版本继续细化。

验证记录：

- `PYTHONPATH=. pytest tests/test_renderer.py::test_trial_page_shows_generation_failure_message tests/test_server.py::test_generate_trial_derivatives_failure_keeps_row_and_shows_message -q`：2 passed。
- `PYTHONPATH=. pytest tests/test_server.py tests/test_renderer.py -q`：55 passed。
- `PYTHONPATH=. pytest tests -q`：153 passed。

## v0.3.37 - DashScope 异步图像生成 Provider

日期：2026-06-15

阶段目标：

- 将好图衍生生成从通用 cloud provider 骨架推进到更贴近阿里云 DashScope / 通义万相异步任务接口的 provider。
- 为“好图衍生真的生成新参考图”补齐提交任务、轮询结果、失败提示的工程路径。

已完成：

- 新增 `DashScopeImageGenerationProvider`：
  - 支持 `IMAGE_GENERATION_PROVIDER=dashscope` 或 `wanx`。
  - 提交异步生成任务，读取 `output.task_id`。
  - 轮询 `IMAGE_GENERATION_TASK_URL_TEMPLATE`。
  - 识别 `SUCCEEDED`、`FAILED`、`CANCELED`、`UNKNOWN` 等任务状态。
  - 成功后解析 `results` 中的 `b64_json` / `image_base64` / `local_image_path` / data URL。
  - 将生成图写入本地 `dashscope_derivative_*.png`，并保留 seed、prompt、source_sample_id 和二次审核提示。
- `ImageGenerationProviderFactory` 支持 `dashscope` / `wanx` provider。
- `.env.example` 增加 `IMAGE_GENERATION_TASK_URL_TEMPLATE`。
- README 增加 DashScope/通义万相异步生成说明。

当前限制：

- 本轮仍不实际调用外网 API，只完成 provider 适配和测试替身验证。
- DashScope 不同模型/接口返回字段可能存在细微差异，真实接入后仍需用实际响应样本再校准。
- 生成图仍必须经过 v0.3.32 的二次 VLM 解析和审核门禁，不能直接同步飞书。

验证记录：

- `PYTHONPATH=. pytest tests/test_harness.py::test_dashscope_generation_provider_polls_task_and_downloads_results tests/test_harness.py::test_dashscope_generation_provider_raises_clear_error_on_failed_task -q`：2 passed。
- `PYTHONPATH=. pytest tests/test_harness.py::test_image_generation_factory_reports_unconfigured_mock_and_cloud tests/test_harness.py::test_cloud_generation_provider_writes_returned_images_with_generation_metadata tests/test_harness.py::test_dashscope_generation_provider_polls_task_and_downloads_results tests/test_harness.py::test_dashscope_generation_provider_raises_clear_error_on_failed_task -q`：4 passed。
- `PYTHONPATH=. pytest tests -q`：151 passed。

## v0.3.36 - Harness 标注平台文件导出

日期：2026-06-15

阶段目标：

- 将 Harness 失败样本和人工修正从内部页面推进到可交给外部标注平台的文件。
- 为 Label Studio / Argilla 的后续真实接入预留更清晰的数据形态。

已完成：

- 新增 `export_harness_annotation_files`：
  - 导出 Argilla JSONL：`argilla_harness_<国家>.jsonl`。
  - 导出 Label Studio JSON：`label_studio_harness_<国家>.json`。
  - 覆盖失败 case，以及已有人工修正的 case。
  - 每条记录包含 sample_id、task_type、图片路径、operation_tag、gold label、Agent 输出、失败原因和 human override。
- Agent 评测页新增“导出标注平台文件”按钮。
- 新增 `/export_harness_annotations` 路由：
  - 默认写入运行目录 `harness_annotation_exports/`。
  - 页面提示 Argilla / Label Studio 两个文件路径。
- README 增加标注平台导出说明。

当前限制：

- 本轮只做本地文件落地，不直接调用 Label Studio / Argilla API。
- 导出的 image 字段使用本地图片路径；如果迁移到远程标注平台，需要后续补静态文件托管或对象存储 URL。

验证记录：

- `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_harness_annotation_files_for_label_tools tests/test_renderer.py::test_eval_page_has_harness_override_export_action tests/test_server.py::test_export_harness_annotations_action_writes_label_tool_files -q`：3 passed。
- `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py tests/test_harness.py -q`：83 passed。
- `PYTHONPATH=. pytest tests -q`：149 passed。

## v0.3.35 - Harness HITL 修正导出回流

日期：2026-06-15

阶段目标：

- 将 v0.3.34 的 Harness 人工修正从“只能存在 memory 里”推进到可导出的回流文件。
- 为后续人工复核后更新 gold dataset、或导入 Label Studio / Argilla 标注平台提供中间数据层。

已完成：

- 新增 `export_harness_overrides`：
  - 从本地 HITL memory 中筛选 `harness_override` 记录。
  - 解析 `sample_id`、`task_type` 和人工修正内容。
  - 导出 CSV 字段：`sample_id,task_type,human_override,country`。
- Agent 评测页新增“导出人工修正CSV”按钮。
- 新增 `/export_harness_overrides` 路由：
  - 默认导出到运行目录 `harness_overrides_<国家>.csv`。
  - 导出后在页面展示导出路径提示。
- README 增加 Harness 修正回流说明，明确导出 CSV 是人工复核中间层，不直接覆盖真实 gold dataset。

当前限制：

- 当前导出的是修正建议 CSV，尚未自动 merge 回 `PUZZLEOPS_HARNESS_DATASET`。
- 后续仍需增加 Label Studio / Argilla exporter 的真实文件落地或 API 对接。

验证记录：

- `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_harness_overrides_to_csv tests/test_renderer.py::test_eval_page_has_harness_override_export_action tests/test_server.py::test_export_harness_overrides_action_writes_csv_and_status_message -q`：3 passed。
- `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：71 passed。
- `PYTHONPATH=. pytest tests -q`：147 passed。

## v0.3.34 - Harness 失败样本复盘与 HITL 修正入口

日期：2026-06-15

阶段目标：

- 将 Agent 评测页的失败样本区从“简单错误列表”升级为可运营复盘的 Harness 视图。
- 为后续 gold dataset 人工纠错、失败样本回流和 Label Studio/Argilla 类标注流预留入口。

已完成：

- Harness Dashboard 的失败样本区升级为“失败样本复盘”：
  - 展示真实样本缩略图或 fallback 视觉缩略图。
  - 展示样本文件名、sample_id 和 operation_tag。
  - 展示 gold subject、gold color mood、gold composition、价值观标签和风险标签。
  - 保留 Agent 输出和失败原因，便于对比模型结果与人工 gold label。
- 新增 HITL 修正表单：
  - 每个失败 case 可填写人工修正。
  - `/save_harness_override` 会把修正写入本地 HITL memory。
  - 当前不直接改 CSV，避免误写真实 gold dataset。
- 页面样式微调：
  - 控制失败样本缩略图和修正表单尺寸。
  - 减少长文本挤压对复盘区可读性的影响。
- README 增加 Harness HITL 说明。

当前限制：

- 人工修正暂存于本地 memory，尚未自动回写 `PUZZLEOPS_HARNESS_DATASET` CSV。
- 尚未接入 Label Studio / Argilla 外部标注平台，本轮仅完成内置轻量 HITL 入口。

验证记录：

- `PYTHONPATH=. pytest tests/test_renderer.py::test_eval_failure_samples_show_image_gold_label_and_hitl_form tests/test_server.py::test_save_harness_override_action_writes_hitl_memory -q`：2 passed。
- `PYTHONPATH=. pytest tests/test_renderer.py tests/test_server.py -q`：50 passed。
- `PYTHONPATH=. pytest tests -q`：144 passed。

## v0.3.33 - Harness 真实 Gold Dataset 导入入口

日期：2026-06-15

阶段目标：

- 补上 Agent Harness 从“默认历史/合成样本”走向“真实小样本评测集”的入口。
- 让后续 30-50 张真实拼图样本可以按 CSV 方式接入，不再只依赖程序内置 demo 数据证明效果。

已完成：

- 新增 `load_eval_samples_csv`：
  - 支持读取真实 gold dataset CSV。
  - 校验真实样本图片路径，缺失或不存在时返回导入问题，不让 Harness 崩溃。
  - 支持 `open_rate`、`completion_rate`、`avg_finish_time` 等业务指标转为评测 metrics。
  - 支持 `gold_value_labels`、`gold_risk_labels` 用 `;`、`；`、`、` 或 `|` 分隔。
  - 缺少 gold label 的样本保留进入 Harness，对应指标继续标记为 `not_evaluable`。
- Agent Harness 接入 `PUZZLEOPS_HARNESS_DATASET`：
  - 配置后优先读取真实 CSV，并按当前国家过滤样本。
  - 如果 CSV 全部无效，则回退默认样本，同时在数据集概览展示导入问题数和摘要。
  - 未配置时保持原有默认历史/合成样本行为。
- 新增 `docs/harness_gold_samples_template.csv`：
  - 给出真实样本字段模板。
  - 明确示例只用于字段格式，不代表真实业务指标。
- `.env.example` 与 README 增加 Harness gold dataset 配置说明。

当前限制：

- 本轮提供导入和校验入口，但真实 30-50 张拼图样本仍需要人工收集和标注。
- CSV 当前是轻量本地格式，后续可再扩展为 Excel/飞书导入或 Label Studio/Argilla 标注回流。

验证记录：

- `PYTHONPATH=. pytest tests/test_harness.py::test_load_eval_samples_csv_imports_real_gold_dataset_and_skips_invalid_images tests/test_harness.py::test_load_eval_samples_csv_keeps_missing_gold_as_not_evaluable -q`：2 passed。
- `PYTHONPATH=. pytest tests/test_agents.py::test_agent_harness_prefers_configured_real_gold_dataset tests/test_agents.py::test_agent_harness_summary_reports_invalid_gold_dataset_rows -q`：2 passed。
- `PYTHONPATH=. pytest tests/test_harness.py tests/test_agents.py tests/test_renderer.py -q`：46 passed。
- `PYTHONPATH=. pytest tests -q`：142 passed。

## v0.3.32 - 生成图二次 VLM 审核门禁

日期：2026-06-15

阶段目标：

- 把 v0.3.31 的“真实 provider 返回本地图即可进入同步”的工程闸门，升级为更符合业务安全要求的二次审核闭环。
- 保证好图衍生生成图只有在真实视觉 LLM 解析和审核规则复检通过后，才允许作为飞书附件同步。

已完成：

- `generate_trial_derivatives` 增加生成图二次审核：
  - mock / 未配置 provider 仍只进入待办，不会同步飞书附件。
  - 真实 provider 返回图片后，会读取本地图片像素特征。
  - 调用当前配置的视觉 LLM 重新解析生成图主体、风格、场景、文化元素和风险标签。
  - 将结构化视觉结果送入审核规则复检。
- 通过审核的生成图：
  - 写回 VLM 解析后的主体和三段式主体描述。
  - 标记 `reference_image_syncable=True`，允许飞书附件上传。
  - 备注记录 provider、seed、二次审核状态和 prompt 信息。
- 未通过审核的生成图：
  - 保留在试新表预览，方便运营查看。
  - 标记 `reference_image_syncable=False`，飞书同步时不会上传附件。
  - 备注明确失败原因，例如视觉 LLM 未配置、调用失败、版权/IP 风险或审核规则命中。
- README 更新好图衍生生成说明，明确“真实生成 provider + 真实 VLM 二次审核”两个条件都满足后才同步图片。

当前限制：

- 仍未直接部署 ComfyUI，本轮完成的是 provider 化真实生成后的审核门禁。
- 审核复检依赖当前视觉 LLM 输出的结构化 `risk_tags` 和本地审核规则；最终上线仍建议保留人工确认入口。
- 真实 30-50 张拼图 gold dataset 尚未补齐，Harness 指标仍不能替代真实业务效果验证。

验证记录：

- `PYTHONPATH=. pytest tests/test_server.py::test_real_generation_derivatives_require_vlm_second_review_before_sync tests/test_server.py::test_real_generation_derivatives_with_vlm_risk_stay_unsyncable -q`：2 passed。
- `PYTHONPATH=. pytest tests/test_server.py tests/test_harness.py tests/test_renderer.py -q`：56 passed。
- `PYTHONPATH=. pytest tests -q`：138 passed。

## v0.3.31 - 真实好图衍生生成 Provider 骨架

日期：2026-06-15

阶段目标：

- 将“好图衍生”从 mock 接口推进到可配置真实图像生成 provider 的第一版。
- 明确 derive 模式三种状态：未配置、mock、本地验证；cloud，真实生成并可进入二次审核后同步。

已完成：

- 新增 `CloudImageGenerationProvider`：
  - 支持通过云端接口返回 `b64_json` / `image_base64` / `local_image_path`。
  - 将返回图写入本地 `cloud_derivative_*.png`。
  - 记录 provider、prompt、negative prompt、seed、source_sample_id、保留特征和风险备注。
- 新增 `ImageGenerationProviderFactory`：
  - `IMAGE_GENERATION_PROVIDER` 为空时返回未配置 provider。
  - `IMAGE_GENERATION_PROVIDER=mock` 时仅用于本地 UI/Harness 验证。
  - `IMAGE_GENERATION_PROVIDER=cloud` 或 `comfyui` 时读取 `IMAGE_GENERATION_API_KEY`、`IMAGE_GENERATION_MODEL`、`IMAGE_GENERATION_BASE_URL`。
- Agent 初始化自动读取生成 provider 配置：
  - 未配置时页面显示“生成 provider 未配置”。
  - mock 生成图仍不可同步为飞书附件。
  - cloud/真实 provider 生成图存在本地文件后，会标记为“二次 VLM 解析与审核通过”，并允许进入飞书附件同步链路。
- `.env.example` 增加图像生成配置项。
- README 增加好图衍生生成说明，明确 mock 不同步图片、cloud 通过审核后才同步飞书附件。

当前限制：

- `CloudImageGenerationProvider` 是云端 image generation 的通用骨架，具体云平台返回结构仍需按实际供应商接口适配。
- 当前“二次 VLM 解析与审核通过”是 provider 返回真实本地图后的工程闸门，后续仍应接入更严格的生成图 VLM 复检和人工确认队列。
- 本轮不部署本机 ComfyUI，不做 LoRA/模型训练。

验证记录：

- `PYTHONPATH=. pytest tests/test_harness.py::test_image_generation_factory_reports_unconfigured_mock_and_cloud tests/test_harness.py::test_cloud_generation_provider_writes_returned_images_with_generation_metadata tests/test_server.py::test_real_generation_derivatives_pass_second_review_and_become_syncable tests/test_server.py::test_generate_trial_derivatives_creates_two_audited_reference_rows -q`：4 passed。
- `PYTHONPATH=. pytest tests -q`：137 passed。

## v0.3.30 - 迁移 Qwen 视觉默认模型至 qwen3.7-plus

日期：2026-06-15

阶段目标：

- 响应阿里云 2026-07-08 快照模型下线通知，提前避开 `qwen3-vl-flash` 默认模型风险。
- 保持试新图片真实 VLM 解析、价值观大师和 Harness 评测链路可继续使用。

已完成：

- Qwen 视觉默认模型从 `qwen3-vl-flash` 迁移到 `qwen3.7-plus`：
  - `QwenVisionLLMClient` 默认参数更新。
  - `VisionLLMClientFactory` 未显式配置模型时默认使用 `qwen3.7-plus`。
  - `.env.example` 更新为 `QWEN_VISION_MODEL=qwen3.7-plus`。
- README 更新当前 LLM 大脑说明：
  - 明确当前版本支持真实 Qwen/OpenAI 视觉语言模型。
  - 明确 Qwen 默认模型为 `qwen3.7-plus`。
  - 未配置 key 时仍不伪造语义识别。
- 本地 `.env` 已同步更新为 `QWEN_VISION_MODEL=qwen3.7-plus`，但 `.env` 仍被 `.gitignore` 忽略，不提交密钥。

影响范围：

- 影响真实多模态解析调用的模型名。
- 不影响飞书同步、本地像素解析、页面路由和已有历史数据。

验证记录：

- `PYTHONPATH=. pytest tests/test_vision_llm.py -q`：6 passed。
- `PYTHONPATH=. pytest tests -q`：132 passed。

## v0.3.29 - 修复占位衍生图误同步为飞书附件

日期：2026-06-14

阶段目标：

- 修复 v0.3.28 后飞书表格里部分图片显示为 `?`、无法打开的问题。
- 保留真实上传图片自动同步为飞书附件的能力。

根因：

- v0.3.28 新增了 `MockImageGenerationProvider`，用于验证好图衍生 provider 接口。
- mock provider 只生成本地占位 PNG 记录，不是真实拼图参考图。
- 但生成的衍生行仍带有 `reference_image_path`，飞书同步逻辑会把它当作真实图片附件上传到 `图片本身` 字段，导致飞书端可能显示 `?` 或不可预览。

已完成：

- `DemandRow` 增加内部标记 `reference_image_syncable`：
  - 真实上传图片默认可同步。
  - mock 衍生图明确标记为不可同步附件。
- `generate_trial_derivatives()` 生成的 mock 衍生参考图行不再被当作真实飞书附件。
- 飞书 bitable 同步前尊重 `_reference_image_syncable=False`：
  - 不调用素材上传接口。
  - 不写入 `图片本身` 附件字段。
  - 仍保留运营 tag、备注等文本字段，避免整条同步失败。
- 日期相关测试改为固定测试日期或动态后缀，避免跨天后测试误报。

当前限制：

- mock provider 仍只用于本地链路验证，不能代表真实生成图。
- 后续接入 ComfyUI / 云端图像生成 provider 后，应将真实生成图显式标记为可同步，并在二次 VLM 解析和审核通过后再写入飞书附件。

验证记录：

- `PYTHONPATH=. pytest tests/test_server.py::test_generate_trial_derivatives_creates_two_audited_reference_rows tests/test_external_adapters.py::test_real_feishu_client_does_not_upload_unsyncable_placeholder_image -q`：2 passed。
- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_uploads_local_image_before_bitable_create tests/test_server.py::test_trial_upload_uses_real_semantic_subject_in_operation_tag_and_feishu_payload -q`：2 passed。
- `PYTHONPATH=. pytest tests -q`：132 passed。

## v0.3.28 - Agent Harness 主线与好图衍生生成接口

日期：2026-06-09

阶段目标：

- 把 Agent 评测从“单条 demo 指标展示”升级为内置轻量 Agent Harness。
- 把好图衍生从“只输出衍生方向”推进到 provider 化生成接口，为后续接 ComfyUI 或云端图像生成 API 做准备。
- 保持双层设计：本地 Harness 可独立跑，Phoenix / DeepEval / Promptfoo / Argilla / Label Studio 等先做结构化导出，不强依赖外部服务。

已完成：

- 新增 `puzzle_ops/harness.py`：
  - `EvalSample` 支持真实样本与 synthetic demo 样本区分。
  - `HarnessRun` 记录版本、数据集、模型 provider、生成 provider、cases、metrics、failures。
  - `HarnessCaseResult` 记录输入、输出、工具调用、trace steps、scores、失败原因和人工覆盖入口。
  - 覆盖 6 类任务：`trial_parse_eval`、`value_match_eval`、`audit_eval`、`grade_predict_eval`、`derive_generation_eval`、`feishu_sync_eval`。
  - 缺图片路径、缺 gold label、未配置生成 provider 时明确标记 `not_evaluable` 或失败原因，不伪造效果。
- 新增 `puzzle_ops/image_generation.py`：
  - `ImageGenerationProvider` 抽象。
  - `DerivativeImage` 记录 provider、prompt、negative prompt、seed、来源样本、保留/变化特征、风险备注。
  - `MockImageGenerationProvider` 用于本地测试和页面链路验证；它只生成占位图片记录，不声明真实生成能力。
- 好图衍生提需增加生成入口：
  - derive 模式展示“生成衍生参考图”按钮。
  - 未配置 provider 时提示“生成 provider 未配置”，不会伪造参考图。
  - 配置 provider 时生成 2 条可进入试新表的参考图行，保留图片路径、预览、seed、prompt、negative prompt、二次 VLM 解析与审核提示。
- Agent 评测页升级为 Harness Dashboard：
  - 展示数据集概览、真实/合成样本数、国家/等级/来源分布。
  - 展示本次 run 的 run_id、版本、模型、生成 provider。
  - 展示任务级指标：三段式描述合规率、价值观一致率、审核风险召回率、SABCD预测准确率、工具调用正确率、Step Efficiency、生成图审核通过率、飞书同步成功率。
  - 展示失败样本、Agent 输出、失败原因、HITL 修正入口。
  - 展示当前 run 与历史 run 的版本对比。
- 本地存储新增 Harness run 持久化：
  - SQLite 新增 `harness_runs` 表。
  - 支持保存/读取历史 run，后续可用于回放和版本对比。
- 外部开源集成预留：
  - `PhoenixExporter` 导出 trace/eval payload。
  - `DeepEvalAdapter` 导出 pytest-style test cases。
  - `PromptfooExporter` 导出 prompt/model 对比配置 payload。
  - `ArgillaExporter` / `LabelStudioExporter` 导出 HITL 标注记录 payload。

当前限制：

- 还没有接真实 ComfyUI、通义万相、Qwen Image 或 Stable Diffusion API；真实好图衍生生成需要后续配置具体 provider。
- Mock provider 只用于测试和本地链路验证，不代表真实图像生成质量。
- 真实业务效果仍依赖 30-50 张人工 gold label 小样本；合成数据只能用于 demo、开发和边界测试。
- 生成图进入飞书前仍需要二次 VLM 解析、审核和人工确认，不能自动当作最终生产图。

验证记录：

- `PYTHONPATH=. pytest tests/test_harness.py -q`：5 passed。
- `PYTHONPATH=. pytest tests/test_harness.py tests/test_agent_runtime.py tests/test_renderer.py tests/test_server.py tests/test_external_adapters.py -q`：76 passed。
- `PYTHONPATH=. pytest tests -q`：131 passed。

## v0.3.27 - 修复同步成功后飞书打开 404

日期：2026-06-09

阶段目标：

- 修复点击同步成功提示里的飞书入口后，飞书显示“页面不存在”的问题。

根因：

- `FEISHU_SPREADSHEET_TOKEN` 当前是云文档节点 token。
- `web_url()` 仍用这个 token 拼 `https://feishu.cn/base/{token}?table=...`。
- 飞书网页端需要真实 bitable app token，所以打开后进入 404/页面不存在。

已完成：

- `RealFeishuClient.web_url()` 在 bitable 场景使用 canonical app token：
  - 如果已经配置或缓存 `FEISHU_BITABLE_APP_TOKEN`，直接使用。
  - 如果没有，则通过飞书接口解析真实 app token 后生成链接。
  - 同步成功后的“打开飞书表格”按钮会指向真实 base app token URL。
- 保留 `FEISHU_WEB_URL` 的最高优先级：
  - 如果你手动配置了完整飞书网页链接，仍以手动配置为准。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_bitable_web_url_uses_configured_canonical_app_token tests/test_external_adapters.py::test_real_feishu_client_bitable_web_url_resolves_canonical_app_token_when_needed -q`：2 passed。
- `PYTHONPATH=. pytest tests/test_external_adapters.py -q`：21 passed。
- `PYTHONPATH=. pytest tests -q`：124 passed。
- 真实配置下 `RealFeishuClient.web_url()` 生成 `https://feishu.cn/base/AgqW...?...`，不再使用 `CxCTw...` 云文档节点 token。

## v0.3.26 - 按真实飞书字段动态过滤同步 payload

日期：2026-06-09

阶段目标：

- 修复真实飞书表缺少 `价值观匹配度` 字段时，同步报 `FieldNameNotFound` 的问题。

根因：

- 本地 bitable 白名单允许写入 `价值观匹配度`。
- 但用户当前真实飞书提需表没有这个字段，飞书 batch_create 会直接拒绝整个请求。

已完成：

- 多维表格同步前读取真实表字段：
  - 调用 `/bitable/v1/apps/{app_token}/tables/{table_id}/fields?page_size=200`。
  - 有远端字段列表时，只写当前飞书表真实存在的字段。
  - 如果字段列表取不到或为空，才回退到本地白名单，避免弱权限场景完全不可用。
- `价值观匹配度` 变为可选同步字段：
  - 飞书表有这个字段就写。
  - 飞书表没有这个字段就自动跳过，不影响图片、运营 tag、主体描述等核心字段同步。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_omits_bitable_fields_missing_from_remote_schema -q`：1 passed。
- `PYTHONPATH=. pytest tests/test_external_adapters.py -q`：19 passed。
- `PYTHONPATH=. pytest tests -q`：122 passed。

## v0.3.25 - 修复飞书附件上传异常导致本地页面断连

日期：2026-06-09

阶段目标：

- 修复点击“同步试新到飞书”后 Safari 停在 `/sync_trial_feishu` 并提示本地服务器中断连接的问题。

根因：

- 真实飞书附件上传接口返回 `parent node not exist`。
- 当前代码没有捕获附件上传阶段的 `RuntimeError`，导致本地 HTTP 请求处理线程异常退出，浏览器只能看到“服务器意外中断了连接”。
- 当前 `.env` 里的 `FEISHU_SPREADSHEET_TOKEN` 是云文档节点 token 形态，附件上传需要真实 bitable app token 作为 `parent_node`。

已完成：

- `RealFeishuClient.write_table()` 捕获真实飞书 HTTP/素材上传异常：
  - 失败时返回 `ToolResult(success=False)`。
  - 页面会显示“同步失败：...”的具体飞书错误，不再让浏览器断连。
- 多维表格附件上传改用 canonical bitable app token：
  - 优先读取可选配置 `FEISHU_BITABLE_APP_TOKEN`。
  - 如果未配置，则通过飞书 bitable app 查询接口自动把当前 token 解析为真实 app token。
  - `upload_all` 的 `parent_node` 使用解析后的 app token。
  - 写入记录的 batch_create URL 也使用解析后的 app token。
- `.env.example` 增加 `FEISHU_BITABLE_APP_TOKEN` 可选项说明。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_upload_uses_canonical_bitable_app_token tests/test_external_adapters.py::test_real_feishu_client_returns_failure_when_bitable_attachment_upload_fails -q`：2 passed。
- `PYTHONPATH=. pytest tests/test_external_adapters.py tests/test_server.py::test_sync_trial_to_feishu_records_success_and_resets_trial_row tests/test_server.py::test_sync_needs_to_feishu_clears_rows_and_sets_success_message -q`：20 passed。
- `PYTHONPATH=. pytest tests -q`：121 passed。

## v0.3.24 - 同步确认页与单页提需卡片

日期：2026-06-09

阶段目标：

- 修复点击同步后飞书页面无法稳定打开的问题，并把常规/试新提需表改成更适合运营审核的单页编辑布局。

已完成：

- 同步成功后不再把 POST 请求直接 303 到飞书外链：
  - 服务端先回到当前 Agent 页面，展示同步成功状态。
  - 成功提示里提供“已同步，打开飞书表格”按钮，保留 `target="_blank"` 让运营主动打开飞书。
  - 即使浏览器拦截弹窗或外链跳转，页面也会明确显示同步结果和飞书入口。
- 常规提需和试新提需从超宽表格改为卡片式行编辑：
  - 图片、运营 tag、主体内容、张数、需求等级、加工方式、交付日期放在紧凑网格里。
  - 主体描述、备注、价值观匹配度放到下方宽区域，避免在窄列里夹缝审核 AI 文案。
  - 同步按钮不再依赖 `formtarget="_blank"`，减少浏览器弹窗策略影响。

验证记录：

- `PYTHONPATH=. pytest tests/test_server.py::test_sync_needs_to_feishu_clears_rows_and_sets_success_message tests/test_server.py::test_sync_trial_to_feishu_records_success_and_resets_trial_row tests/test_renderer.py::test_regular_page_renders_business_table_fields_and_empty_delivery_input tests/test_renderer.py::test_trial_page_keeps_core_fields_and_value_match_column tests/test_renderer.py::test_sync_success_message_renders_feishu_link_without_popup_dependency -q`：5 passed。
- `PYTHONPATH=. pytest tests -q`：119 passed。

## v0.3.23 - 同步跳转稳定性与提需表列宽优化

日期：2026-06-09

阶段目标：

- 修复同步成功后新页面打不开的问题，并优化试新/常规提需表列宽，避免主体描述在窄列中难以审核。

已完成：

- 同步跳转不再额外调用飞书 API：
  - `RealFeishuClient.web_url()` 对 bitable 直接返回 `https://feishu.cn/base/{app_token}?table={table_id}`。
  - 避免同步成功后为了获取 canonical app token 又发一次 GET，导致新窗口打不开或卡住。
  - 如果配置了 `FEISHU_WEB_URL` 但没有 `https://`，会自动补齐协议。
- 提需表增加固定列宽：
  - 新增 `demand-table`、`regular-demand-table`、`trial-demand-table` 和 `colgroup`。
  - 张数列压到 72px，需求等级 118px，加工方式 150px，交付日期 92px。
  - 主体描述列常规 520px，试新 620px，价值观匹配度 760px。
  - 主体描述 textarea 高度提升到 220px，便于运营审核和改写 AI 文案。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_bitable_web_url_does_not_require_extra_api_call tests/test_external_adapters.py::test_real_feishu_client_normalizes_configured_web_url tests/test_renderer.py::test_regular_page_renders_business_table_fields_and_empty_delivery_input tests/test_renderer.py::test_trial_page_keeps_core_fields_and_value_match_column -q`：4 passed。
- `PYTHONPATH=. pytest tests -q`：118 passed。
- 浏览器验证：试新表 `col-count=72px`、`col-priority=118px`、主体描述 textarea `min-height=220px`、同步按钮 `formtarget=_blank`。

## v0.3.22 - 试新上传图片自动同步为飞书附件

日期：2026-06-09

阶段目标：

- 打通“Agent 试新模块上传一次图片 -> 飞书素材上传 -> 获取 file_token -> 写入多维表格附件字段”的完整链路。

已完成：

- 试新上传解析后保留本地图片路径：
  - `DemandRow` 新增 `reference_image_path` 和 `reference_image_content_type`。
  - 上传图片保存到本地后，提需行会携带 URL、path、content-type。
- `RealFeishuClient` 新增 `upload_bitable_attachment`：
  - 调用飞书 `POST /open-apis/drive/v1/medias/upload_all`。
  - `parent_type` 根据文件类型选择 `bitable_image` 或 `bitable_file`。
  - `parent_node` 使用多维表格 app token。
  - 成功后读取 `data.file_token`。
- 真实 bitable 同步前自动上传附件：
  - 如果提需 payload 带 `_reference_image_path`，且 `图片本身` 还不是 `file_token` 附件格式，会先上传素材。
  - 上传成功后将 `图片本身` 改写为 `[{file_token: "..."}]`。
  - `_reference_image_path`、`_reference_image_content_type` 等内部字段不会写入飞书表。
- 真实飞书多维表格里 `图片本身` 可以继续保持附件字段：
  - 不需要新增 `图片链接` 字段。
  - 不需要把 `图片本身` 改成文本字段。

当前限制：

- 飞书素材上传接口限制单文件不超过 20 MB；更大的文件需要后续接分片上传。
- 应用需要具备多维表格编辑与上传图片/附件到云文档相关权限，否则飞书会返回权限错误。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_uploads_bitable_image_and_returns_file_token tests/test_external_adapters.py::test_real_feishu_client_uploads_local_image_before_bitable_create tests/test_server.py::test_trial_upload_uses_real_semantic_subject_in_operation_tag_and_feishu_payload -q`：3 passed。
- `PYTHONPATH=. pytest tests -q`：117 passed。

## v0.3.21 - 飞书字段白名单、短 tag 主体与主体描述编辑

日期：2026-06-09

阶段目标：

- 修复真实飞书表不存在 `图片链接` 字段导致同步失败、AI 生成运营 tag 主体过长、常规/试新提需表主体描述不可编辑的问题。

已完成：

- 飞书 bitable 同步增加字段白名单：
  - 只写入当前提需表已有字段：提需分类、国家、JS分类、图片本身、运营tag、主体内容、张数、需求等级、加工方式、交付日期、主体描述、备注、价值观匹配度。
  - `图片链接`、`不存在字段` 等未建字段不再写入真实多维表格，避免 `FieldNameNotFound`。
  - `图片本身` 仍只在有真实附件 `file_token` 时写入。
- 试新运营 tag 主体压缩：
  - 不再把 VLM 的完整长句直接写入 tag。
  - 长主体会抽取 8 字以内运营短主体，例如 `游客群体含儿童与背包行人在观景步道上行走背景为传统日式多层塔楼建筑` 压缩为 `游客塔楼`。
  - 保留常见业务短主体，如寿司、抹茶、传统浴袍美女、3D渲染动物拟人化等。
- 常规提需表和试新提需表的 `主体描述` 改为可编辑：
  - 页面渲染为 textarea。
  - 保存接口会保存运营人工改写后的主体描述。

当前限制：

- 如果需要把上传图片真正内嵌到飞书附件字段，仍需新增飞书文件上传流程并拿到 `file_token`。
- tag 主体压缩目前是运营短词抽取规则，后续可让 LLM 单独输出 `operation_tag_subject` 字段，并限制 8 字以内。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_omits_link_style_image_field_for_bitable_attachment tests/test_external_adapters.py::test_real_feishu_client_omits_unknown_bitable_fields_to_match_existing_schema tests/test_server.py::test_trial_upload_compacts_long_semantic_subject_for_operation_tag tests/test_server.py::test_save_trial_can_edit_subject_description tests/test_renderer.py::test_regular_page_renders_business_table_fields_and_empty_delivery_input tests/test_renderer.py::test_trial_page_keeps_core_fields_and_value_match_column -q`：6 passed。
- `PYTHONPATH=. pytest tests -q`：115 passed。

## v0.3.20 - 修复飞书多维表格图片附件字段同步失败

日期：2026-06-09

阶段目标：

- 修复真实飞书多维表格同步报错 `AttachFieldConvFail`，原因是把普通图片链接对象写入了附件字段 `图片本身`。

已完成：

- 修复 bitable 字段转换：
  - `图片本身` 只有在值为真正附件格式 `[{file_token: "..."}]` 时才写入。
  - 普通文本、普通链接对象 `[{text, link}]` 不再写入 `图片本身`，避免飞书附件字段转换失败。
  - `图片链接` 字段继续保留上传图 URL，用于同步后追溯参考图。
- 保留未来扩展空间：
  - 后续若接入飞书文件上传拿到 `file_token`，`图片本身` 附件字段会自动保留并写入。

当前限制：

- 当前还没有实现飞书附件上传，所以真实多维表格里不会把图片作为附件内嵌到 `图片本身` 字段；本版先保证同步成功，并把图片 URL 写入 `图片链接` 字段。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_omits_plain_text_attachment_fields_for_bitable tests/test_external_adapters.py::test_real_feishu_client_omits_link_style_image_field_for_bitable_attachment tests/test_external_adapters.py::test_real_feishu_client_keeps_real_attachment_file_tokens_for_bitable tests/test_server.py::test_trial_upload_uses_real_semantic_subject_in_operation_tag_and_feishu_payload -q`：4 passed。
- `PYTHONPATH=. pytest tests -q`：112 passed。

## v0.3.19 - 价值观大师接入真实 LLM 判断链路

日期：2026-06-09

阶段目标：

- 修正 v0.3.18 将价值观大师做成文本规则分支的问题，改为通过 LLM 基于当前图片解析结果和已有价值观规则做判断。

已完成：

- `OpenAIVisionLLMClient` 和 `QwenVisionLLMClient` 新增 `judge_value_match`：
  - 输入当前提需行的国家、JS分类、运营 tag、主体、主体描述、解析备注。
  - 输入当前国家已有价值观规则库。
  - Prompt 明确要求不要套默认模板，必须引用当前主体、色彩氛围、构图环境证据。
  - 输出 JSON：`value_match`、`confidence`、`evidence`、`risk_tags`。
- `apply_value_master` 改为调用真实 LLM 判断：
  - 有 Qwen/OpenAI 配置时返回真实 LLM 的价值观判断。
  - 缺少真实 LLM 配置时只提示需要配置，不再伪造“符合/不符合”结论。
- 删除 v0.3.18 的手写主体规则分支：
  - 寿司、火车少女、猫咪等不再靠 if/else 判断。
  - 价值观结论由模型读取当前图片解析结果和规则库后生成。

当前限制：

- 当前价值观大师复用视觉 LLM provider 做文本判断，仍以试新上传时保存的 VLM 解析结果作为主要视觉证据；如果要在价值观按钮点击时重新读原图做二次视觉判断，可以继续把 `reference_image_url` 解析成图片 bytes 后传入同一次多模态请求。
- 模型判断仍需运营审核，尤其是版权/IP、文化混淆和品牌露出风险。

验证记录：

- `PYTHONPATH=. pytest tests/test_vision_llm.py::test_openai_client_judges_value_match_with_current_visual_context tests/test_vision_llm.py::test_qwen_client_judges_value_match_with_chat_completions_payload tests/test_agents.py::test_value_master_writes_value_match_to_trial_row tests/test_agents.py::test_value_master_uses_current_trial_subject_instead_of_default_template tests/test_agents.py::test_value_master_requires_real_llm_instead_of_rule_fallback -q`：5 passed。
- `PYTHONPATH=. pytest tests -q`：110 passed。

## v0.3.18 - 价值观大师改为基于当前解析主体判断

日期：2026-06-09

阶段目标：

- 修复试新上传寿司图后，价值观大师仍套用日本默认“猫咪鲤鱼/动物互动”文案的问题。

已完成：

- `apply_value_master` 不再直接读取国家默认 trial 模板。
- 价值观大师会读取当前提需行的：
  - `subject`
  - `operation_tag`
  - `subject_description`
- 日本市场按主体类型生成匹配理由：
  - 寿司、抹茶、料理等走“本土饮食文化、清爽色彩、生活烟火气”。
  - 猫、犬、鲤鱼等动物主体才走“治愈、季节感、动物互动”。
  - 火车、站台、店铺、少女、人物等走“日常故事感、街景氛围、主体清晰”。
- 法国市场保留花艺/庭院/餐饮等主体分支，不再只套固定文案。

当前限制：

- 价值观大师当前仍是规则化业务判断，不调用额外 LLM；好处是稳定、便宜、可控，后续可以把真实 VLM 解析结果和 RAG 价值观规则一起交给 LLM 做更自然的解释。

验证记录：

- `PYTHONPATH=. pytest tests/test_agents.py::test_value_master_writes_value_match_to_trial_row tests/test_agents.py::test_value_master_uses_current_trial_subject_instead_of_default_template tests/test_server.py::test_apply_value_master_action_updates_trial_row -q`：3 passed。
- `PYTHONPATH=. pytest tests -q`：106 passed。

## v0.3.17 - 试新 tag、上传图片同步与图片数据可信度修复

日期：2026-06-09

阶段目标：

- 修复试新提需表里运营 tag 拥挤、tag 未跟随视觉解析主体更新、上传图片未进入提需表和飞书 payload、合成数据仍是 1x1 占位图的问题。

已完成：

- 试新上传解析后会用视觉模型返回的主体重写运营 tag：
  - 例如真实视觉模型识别为 `日式火车店铺少女` 时，tag 写为 `试新_日本_日式火车店铺少女0609`。
  - 日期使用当天日期后缀，不再保留旧的 `0604`。
- 试新提需表新增上传图贯通：
  - `DemandRow` 增加 `reference_image_url`。
  - 上传解析后保存第一张上传图片的 `/uploads/...` URL。
  - 提需表“图片本身”列优先展示真实上传图，不再只根据图片名生成示意图。
- 飞书同步 payload 增加图片信息：
  - `图片本身` 在有上传图时写为带 `text/link` 的结构化链接。
  - 额外写入 `图片链接` 字段，便于飞书表格里追溯上传参考图。
- 提需表运营 tag 输入框加宽：
  - 增加 `operation-tag-input` 样式，长 tag 不再挤成看不见字。
- 合成历史数据不再写 1x1 透明占位图：
  - `SyntheticDataGenerator` 改为生成 360x240 本地拼图风格 PNG。
  - 这仍是本地演示数据，不等同真实生产图库。

当前限制：

- 真实飞书多维表格若“图片本身”字段是附件类型，仍需要后续接飞书附件上传/file_token 才能变成真正内嵌附件；当前先同步可点击图片链接和结构化链接。
- 静态库存和合成历史数据已经不是文字卡/1x1 占位图，但仍是本地生成演示图。要完全解决“真实拼图图片数据集”，需要接真实 CMS/素材库图片 URL 或导入带真实图片的业务 Excel。
- 视觉主体以真实 VLM 返回为准，但仍建议运营审核后再同步飞书。

验证记录：

- `PYTHONPATH=. pytest tests/test_server.py::test_upload_trial_images_writes_real_openai_semantics_when_configured tests/test_server.py::test_trial_upload_uses_real_semantic_subject_in_operation_tag_and_feishu_payload tests/test_renderer.py::test_trial_page_keeps_core_fields_and_value_match_column tests/test_renderer.py::test_trial_need_table_renders_uploaded_image_url_when_available tests/test_synthetic_runtime_tools.py::test_synthetic_generator_creates_139_rows_per_country_week_with_images -q`：5 passed。
- `PYTHONPATH=. pytest tests -q`：105 passed。

## v0.3.16 - 多模态业务闭环与页面可信度修正

日期：2026-06-09

阶段目标：

- 把真实视觉模型能力接到更贴近业务的提需与展示链路，修正常规提需日期、试新解析标准、库存图片展示、数据分析明细和 Agent 测评逻辑。

已完成：

- 常规提需加入时会把运营 tag 尾部日期替换为当天日期：
  - 例如 `常规_日本_传统浴袍美女0604` 在 2026-06-09 加入提需后写为 `常规_日本_传统浴袍美女0609`。
  - 手动编辑保存仍保留运营自己输入的 tag。
- 常规提需“AI生成描述”改为业务三段式：
  - 只输出 `主体内容`、`色彩氛围`、`构图环境`。
  - 服务端启用真实视觉模型通道；缺模型或调用失败时保留本地视觉特征和人工确认提示，不伪造真实主体识别。
- 试新上传解析统一为三段式业务文案：
  - 图片主体、色彩氛围、构图环境进入 `subject_description`。
  - 视觉模型 provider、置信度、风险和未配置提示留在备注。
  - “好图衍生提需”明确只输出衍生方向，不声称生成新参考图。
- 库存、价值观、数据分析明细和多模态底座不再使用文字卡：
  - 新增本地 PNG 视觉资产层，页面以真实 `<img>` 渲染参考图和明细图片。
  - 数据分析图片明细第一列展示图片预览和图片名，便于复盘色彩、构图、来源和位置差异。
- Agent 测评页按工作流重构：
  - 拆为任务目标、输入与上下文、工具调用链路、指标与结论。
  - 保留 Eval Dataset、Case 明细、Pass/Fail、工具正确性和 TruLens 指标，展示逻辑更适合讲 Agent 工作流闭环。

当前限制：

- 库存参考图目前是本地生成的拼图风格 PNG，用于替代文字卡和支撑页面多模态展示；若要完全等同真实生产图库，还需要接入真实素材库/CMS 图片 URL。
- 常规提需的视觉模型输出仍需要人工审核，版权/IP、主体识别和文化元素不能自动放行。
- 试新衍生模式仍不会生成新图，只提供衍生方向和提需文案。

验证记录：

- `PYTHONPATH=. pytest tests -q`：103 passed。

## v0.3.15 - 强制真实视觉 LLM 配置

日期：2026-06-08

阶段目标：

- 按用户要求取消视觉语义解析的 Mock 运行路径，试新图片语义解析必须接真实视觉模型。

已完成：

- `VisionLLMClientFactory` 改为强制真实视觉 LLM：
  - 默认 provider 为 `qwen`。
  - 默认模型为 `qwen3-vl-flash`，走 Qwen Cloud OpenAI-compatible Chat Completions。
  - 缺少 `QWEN_API_KEY` 时不再回退 Mock。
  - 仍保留 OpenAI 作为可选真实 provider，但不作为默认方案。
  - 页面和上传结果会明确提示“需要配置真实视觉 LLM”，当前不会做语义解析。
- 删除视觉 LLM Mock client 的运行路径，保留本地视觉解析作为低成本像素层：
  - 本地解析仍可输出颜色、构图、明暗、质量和拼图友好度。
  - 语义主体、场景、文化元素、风格和风险必须由真实视觉模型返回。
- 试新上传链路新增真实模型单元验证：
  - 使用 fake transport 验证 Qwen Chat Completions payload、OpenAI Responses API payload 和结构化结果融合。
  - 缺真实配置时，提需字段会写入“待真实视觉 LLM 解析”，不会伪造主体。
- `.env.example` 改为真实模型配置模板：
  - `VISION_LLM_PROVIDER=qwen`
  - `QWEN_API_KEY=`
  - `QWEN_VISION_MODEL=qwen3-vl-flash`
  - `QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`

当前限制：

- 当前已通过真实 Qwen3-VL-Flash API 验证直连解析；后续可继续补更贴近业务真实图的回归样例。
- 真实模型输出仍需人工审核，版权/IP、文化元素和主体判断不能完全自动放行。

验证记录：

- `PYTHONPATH=. pytest tests/test_vision_llm.py tests/test_renderer.py tests/test_server.py -q`：40 passed。
- `PYTHONPATH=. pytest tests -q`：101 passed。
- 真实 Qwen3-VL-Flash 调用验证通过：返回 `provider=qwen`、主体、场景、文化元素、风险标签和置信度。
- 页面上传链路端到端验证通过：`/upload_trial_images` 写入 `视觉LLM：真实qwen`、语义主体、场景、文化元素、语义风险和置信度。

## v0.3.14 - 视觉 LLM 适配层与语义解析 Mock

日期：2026-06-08

阶段目标：

- 在不破坏本地 demo 和现有飞书链路的前提下，搭建真正多模态语义解析的工程入口：默认 Mock，可选接入 OpenAI 视觉 LLM。

已完成：

- 新增 `VisionLLMClient` 适配层：
  - 默认使用 `MockVisionLLMClient`，无需网络、无需密钥、无 API 成本。
  - 可通过 `VISION_LLM_PROVIDER=openai` + `OPENAI_API_KEY` 启用真实 OpenAI 视觉解析。
  - OpenAI 适配器使用 Responses API 的 `input_text + input_image(data URL)` 形态，支持 `OPENAI_VISION_MODEL` 和 `OPENAI_VISION_DETAIL` 配置。
- 试新上传链路升级为“双层解析”：
  - 本地视觉解析负责尺寸、色彩、构图、明暗、质量和拼图友好度。
  - 视觉 LLM 适配层负责主体、场景、文化元素、风格、语义风险和 prompt 关键词。
  - 默认 Mock 会明确标注“不代表真实主体识别”；真实 OpenAI 模式才会调用外部视觉模型。
- 页面增加模型状态说明：
  - 试新提需页展示“视觉 LLM 语义解析”当前模式。
  - 多模态底座展示“视觉 LLM 适配器”状态，便于面试演示工程边界。
- `.env.example` 增加可选视觉 LLM 配置，不新增必填项，不提交真实密钥。

当前限制：

- Mock 模式仍然不是图片真实语义理解，只是为了稳定演示 Agent 工程链路。
- 真实 OpenAI 模式需要用户在本地 `.env` 配置 `OPENAI_API_KEY`，且会产生网络调用和 API 成本。
- 真实模型输出仍需人工审核，版权/IP、文化混淆和主体判断不能完全自动放行。

验证记录：

- 新增视觉 LLM Mock、OpenAI payload 构造、试新语义融合、页面模型状态测试。
- `PYTHONPATH=. pytest tests/test_vision_llm.py tests/test_server.py -q`：24 passed。
- `PYTHONPATH=. pytest tests/test_renderer.py tests/test_vision_llm.py tests/test_server.py -q`：38 passed。
- `PYTHONPATH=. pytest tests -q`：99 passed。

## v0.3.13 - 多模态本地解析与分析增强

日期：2026-06-08

阶段目标：

- 在不接真实视觉 LLM 的前提下，把试新上传图片解析升级为可复用的本地多模态特征层，并接入多模态底座、价值观大师和数据分析展示。

已完成：

- 新增 `LocalImageAnalyzer` 本地视觉解析器：
  - 支持多主色/调色板摘要。
  - 识别明暗、饱和度、冷暖色倾向。
  - 判断横向/竖向/方形构图。
  - 标记过暗、过亮、低对比/纯色等本地质量风险。
  - 输出拼图友好度建议，提示主体边界、材质纹理和前中后景层次。
- 试新上传解析改为复用本地视觉解析器：
  - `parse` 模式支持多张参考图的共同视觉特征汇总。
  - `derive` 模式输出衍生方向，不再声称真实生成新图。
  - 解析结果继续写入现有试新提需表字段，不改变飞书表结构。
- 多模态底座增强：
  - 有本地历史图片时优先使用真实像素特征。
  - 无本地图片时保留运营 tag/source 规则 fallback。
  - 页面展示明暗、饱和度、冷暖、质量标签和拼图友好度。
- 多模态分析增强：
  - 数据分析大师增加视觉维度复盘。
  - 价值观候选理由增加视觉证据，用于面试展示 Agent 的图文融合归因链路。

当前限制：

- 当前仍未接入视觉 LLM，不能真正识别图片主体、IP、版权来源或复杂语义。主体仍依赖文件名、运营 tag 或默认配置；版权/IP 审核仍以文本规则和审核手册召回为主。

验证记录：

- 新增本地视觉解析、试新多图汇总、好图衍生方向、多模态特征优先级、价值观视觉证据测试。
- `PYTHONPATH=. pytest tests/test_visual_analysis.py tests/test_multimodal_core.py tests/test_server.py -q`：31 passed。
- `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py tests/test_multimodal_core.py -q`：52 passed。
- `PYTHONPATH=. pytest tests -q`：94 passed。

## v0.3.12 - 试新图片真实本地视觉解析

日期：2026-06-08

阶段目标：

- 修复试新模块“上传图片后没有真正解析图片”的核心问题。

已完成：

- 定位根因：此前试新解析只保存文件并按文件名猜主体，色彩和构图均为固定文案，不是真正视觉解析。
- 新增基于 Pillow 的本地图片解析能力：
  - 读取图片像素。
  - 提取图片尺寸。
  - 判断横向/竖向/方形构图。
  - 提取平均主色并转成业务可读色彩描述。
  - 计算整体亮度。
- 上传成功后，解析结果会写入试新提需表的主体描述和备注字段，并新增试新提需记录。

当前限制：

- 本地解析可识别主色、尺寸、明暗和构图，但不能真正识别“图里是什么主体”。主体识别仍依赖文件名/国家试新默认配置，后续需要接入视觉 LLM 或图像分类模型。

验证记录：

- 上传一张 120x60 暖红色测试图后，试新表写入“暖红”“横向构图”“120x60”。
- `PYTHONPATH=. pytest tests -q`：86 passed。

## v0.3.11 - 空提需同步拦截

日期：2026-06-08

阶段目标：

- 修复点击同步时飞书返回 `records can not be empty` 的问题。

已完成：

- 常规提需清单为空时，不再请求飞书，页面提示“请先加入至少一条常规提需，再同步飞书表格。”
- 试新提需未上传解析或模拟生成记录时，不再请求飞书，页面提示“请先上传解析图片或模拟上传，生成至少一条试新提需记录。”
- 避免空 `records` 触发飞书 `WrongRequestBody`。

验证记录：

- 新增空常规提需同步测试。
- 新增空试新提需同步测试。
- `PYTHONPATH=. pytest tests -q`：85 passed。

## v0.3.10 - 飞书网页跳转真实 app_token 修复

日期：2026-06-08

阶段目标：

- 修复同步成功后新窗口打开飞书时显示“页面不存在”的问题。

已完成：

- 定位根因：用户配置的 token 可用于开放平台 API 写入，但不是网页端最终打开 Base 页面所需的 canonical app_token。
- `RealFeishuClient.web_url()` 现在会调用飞书 Base 元数据接口解析真实 `app_token`，再生成网页跳转地址。
- 保留 `FEISHU_WEB_URL` 手动覆盖能力；如果后续飞书租户域名特殊，可以直接配置浏览器地址栏里的完整 URL。

验证记录：

- 单测覆盖：`RealFeishuClient.web_url()` 会从 metadata 解析 canonical app_token。
- 真实配置解析出的跳转地址已从原 token 切换为飞书返回的 canonical app_token。

## v0.3.9 - 同步新窗口与试新交互稳定性修复

日期：2026-06-08

阶段目标：

- 修复常规提需同步后当前运营后台被飞书页面替换的问题，并加固常规/试新表单上下文，避免旧页面状态导致试新上传或提需动作串到错误模块。

已完成：

- 常规提需和试新提需的“一键同步到飞书表格”按钮改为新窗口打开，原运营后台页面会保留。
- 常规提需表单强制携带 `view=regular`，试新相关表单强制携带 `view=trial`。
- 同步成功返回飞书地址前，服务端先把当前 view 写回对应模块，避免全局状态残留。
- 继续保留 v0.3.8 的试新上传后自动新增提需记录能力。

验证记录：

- 常规/试新同步按钮 HTML 均包含 `formtarget="_blank"`。
- 试新上传接口仍能将图片解析结果写入下方提需表记录。
- `PYTHONPATH=. pytest tests -q`：82 passed。

## v0.3.8 - 飞书外跳、试新记录与价值观入库修复

日期：2026-06-08

阶段目标：

- 修复页面实测发现的同步后页面失败/不跳飞书、试新上传未形成提需记录、审批价值观未进入价值观大师规则库、同步记录不可见的问题。

已完成：

- “一键同步到飞书表格”成功后返回飞书在线表格地址，浏览器会直接跳转到飞书。
- 试新上传解析从“单条当前行”升级为“试新提需记录列表”，上传成功后自动新增一条试新提需记录。
- 试新同步改为同步当前试新提需记录列表，成功后清空列表并保留同步事件。
- 审批通过的价值观候选会同步进入“价值观大师”的完整价值观规则库。
- 同步记录页已验证显示真实 `飞书在线表格 / 成功` 记录。

验证记录：

- 上传本地图片后，试新提需表出现 `cat-koi-v037.png` 记录和本地解析备注。
- 试新同步返回 `Location: https://feishu.cn/base/...`。
- 同步记录页出现 `提需同步 / 飞书在线表格 / 成功`。
- 审批候选价值观后，价值观大师规则库可检索到该规则。
- `PYTHONPATH=. pytest tests -q`：82 passed。

## v0.3.7 - 常规/试新真实飞书同步修复

日期：2026-06-08

阶段目标：

- 修复页面测试中发现的常规提需真实飞书同步失败、试新缺少同步按钮、上传解析结果呈现位置不符合预期的问题。

已完成：

- 多维表格写入时自动过滤普通文本形式的 `图片本身` 字段，避免附件字段 `AttachFieldConvFail`。
- 常规提需真实飞书同步已用业务 payload 验证成功。
- 试新提需表新增“一键同步到飞书表格”按钮。
- 试新上传解析结果明确写入下方试新提需表；右侧只展示解析状态，不再作为主要结果承载区。
- 服务端新增 `/sync_trial_feishu`，试新同步成功后重置试新表并写入同步记录。
- 同步记录已验证新增成功记录。

验证记录：

- 常规真实飞书同步：`code 0`，`record_count 1`。
- 试新真实飞书同步：`code 0`，`record_count 1`。
- `PYTHONPATH=. pytest tests -q`：82 passed。

## v0.3.6 - 飞书真实连接诊断与多维表格适配

日期：2026-06-08

阶段目标：

- 使用用户提供的真实飞书应用和在线表格信息进行联调，定位真实飞书写入链路的外部阻塞点。

已完成：

- 本机 `.env` 已写入真实飞书配置，并通过 `.gitignore` 保证不会提交。
- 飞书客户端已能读取真实配置并创建 Real client。
- 修复 Python `urllib` SSL 证书链问题，默认 HTTP transport 改为 `requests`。
- 识别 `FEISHU_SHEET_RANGE` 以 `tbl` 开头时自动走飞书多维表格/Base `records/batch_create` API。
- 添加飞书多维表格写入路径测试，保证 table id 不会误走 Sheets `values_append`。
- 真实请求已打到飞书开放平台，当前阻塞为应用权限缺失：需要开通 `bitable:app` 或 `base:record:create`。

当前限制：

- 飞书开放平台返回 `99991672 No permission`，应用尚未开通多维表格写入所需权限。
- 用户需要在飞书开放平台为应用开通 `bitable:app` 或 `base:record:create`，并确保目标表格授权给该应用。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py -q`：8 passed。

## v0.3.5 - 真实飞书门禁、试新图片上传与 Agent Eval 重构

日期：2026-06-05

阶段目标：

- 修复“同步成功但找不到飞书表格”的误导问题，让提需同步必须连接真实飞书；同时补齐试新本地图片上传、同步记录自动更新和更像工程项目的 Agent/RAG 评测面板。

已完成：

- 新增 `.env.example`，列出真实飞书连接所需的 App ID、App Secret、Spreadsheet Token、Sheet Range。
- `.gitignore` 忽略 `.env`，避免误提交飞书密钥。
- `RealFeishuClient` 支持在未提供 `FEISHU_ACCESS_TOKEN` 时，用 App ID/App Secret 自动获取 `tenant_access_token`。
- 提需表“一键同步到飞书表格”改为真实飞书门禁：未配置真实飞书时不会清空提需表，并显示缺失配置。
- 同步记录改为从 SQLite 动态读取，提交提需同步后自动新增成功/失败记录。
- 试新提需新增真实本地图片上传入口，支持 `multipart/form-data`，上传后保存图片并回填提需表解析结果和预览。
- 新增 `TrialImageUploadService`，把上传保存/本地解析从服务端拆出，后续接多模态 LLM 时只替换解析适配层。
- 新增 `AgentEvalSuite`，评测页展示 eval dataset、case 明细、metric 阈值、pass/fail、judge reason。
- Agent 评测新增 Context Precision、Context Recall、Tool Correctness、Plan Adherence、Step Efficiency，借鉴 AgentOps、RAGAS、DeepEval、TruLens 的分层评测思路。
- Agent 评测页的 `feishu.write_table` 仅作为 trace dry-run 展示，不会因为打开评测页而写入真实飞书。

当前限制：

- 真实飞书需要用户在本机 `.env` 填写凭证，并在飞书开放平台授予电子表格读写权限。
- 试新上传已可用，但图片理解仍是本地规则适配层，不是视觉 LLM。
- Agent Eval 是本地可解释 eval suite，尚未接真实 LLM-as-Judge provider。

验证记录：

- `PYTHONPATH=. pytest tests -q`：77 passed。

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
