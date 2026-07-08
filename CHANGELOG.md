# 版本记录

这个文件用来记录每一版做了什么、为什么改、当前还存在哪些问题。以后每次你让我修改功能，我会先提交旧版本，再在这里追加阶段总结。

## v0.7.26 - Zilliz Cloud Milvus Verification

日期：2026-07-08

阶段目标：

- 在真实 Zilliz Cloud endpoint 上验证 v0.7.25 的 Milvus 自动建表与 Smoke 链路。
- 修复 Python 标准库 urllib 在 macOS 下访问 Zilliz HTTPS endpoint 的证书链问题。
- 适配 Zilliz/Milvus REST describe 返回结构，正确读取真实 collection vector dim。

已完成：

- RAG HTTP：
  - `_post_json(...)`、`_qdrant_json_request(...)`、`_milvus_json_request(...)` 增加 HTTPS context。
  - 优先使用 `certifi` CA bundle，避免 `CERTIFICATE_VERIFY_FAILED`。
- Milvus REST schema：
  - 自动建表 payload 改为 Milvus REST v2/Zilliz 可识别格式：
    - `fieldName`
    - `elementTypeParams`
    - top-level `indexParams`
  - `_milvus_vector_size(...)` 支持解析 Zilliz describe 返回的 `data.fields[].params[]`。
- 真实 Zilliz 验证：
  - 已将本地 `.env` 配置到用户提供的 Zilliz Cloud endpoint。
  - 真实 DashScope embedding 返回维度：1024。
  - `ensure_collection(1024)` 成功创建 `puzzle_ops_rag` collection。
  - `healthcheck()` 返回 ready/exists true，vector_size=1024。
  - `smoke_diagnostic(...)` 返回 `status=passed`、`search_hit=True`、`cleanup_status=deleted`。

验证：

- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_milvus_json_request_uses_https_context tests/test_rag.py::test_milvus_vector_store_creates_collection_schema_index_and_load_when_missing tests/test_rag.py::test_milvus_vector_store_smoke_diagnostic_writes_searches_and_deletes_temp_entity -q`：3 passed。
- 真实调用验证：
  - Zilliz Cloud healthcheck：ready=True，exists=True，vector_size=1024。
  - Zilliz Cloud smoke：status=passed，search_hit=True，cleanup=deleted。
- 回归验证：
  - `PYTHONPATH=. pytest tests -q`：405 passed。

当前限制：

- `.env` 仍不提交；Zilliz endpoint 和 token 只保存在本机。
- 真实入库/检索会产生 Zilliz 和 DashScope 侧调用，后续批量跑前应注意费用和额度。

## v0.7.25 - Milvus Auto Schema and Smoke

日期：2026-07-08

阶段目标：

- 将 Milvus 从“可配置、可入库”继续补齐到“自动建 collection/schema/index/load + 一键 smoke 诊断 + 默认在线检索配置”。
- 保留 Qdrant 历史兼容，不破坏已有 RAG 验收链路。

已完成：

- Milvus vector store：
  - 新增 `MilvusVectorStore.ensure_collection(vector_size)`。
  - collection 不存在时自动创建 schema：
    - `id`
    - `chunk_id`
    - `parent_id`
    - `country`
    - `source_type`
    - `title`
    - `text`
    - `chunk_index`
    - `metadata`
    - `vector`
  - 自动创建 `vector_index`，metric 使用 `COSINE`，index type 使用 `AUTOINDEX`。
  - 自动 load collection。
  - 如果已有 collection 的 vector dim 不匹配，会直接报错，避免把不同维度 embedding 写进同一个 collection。
- Milvus Smoke：
  - 新增 `MilvusVectorStore.smoke_diagnostic(...)`。
  - 写入临时向量实体。
  - 用同一向量搜索确认能命中。
  - 删除临时实体。
  - 返回 `status/search_hit/cleanup_status/vector_size`。
- Agent / Server / Runtime：
  - 新增 `run_milvus_smoke_diagnostic(...)`。
  - 新增 `/milvus_smoke_diagnostic` action。
  - Runtime 页面 Milvus 模式下展示真实 `Milvus Smoke` 表单，不再是 disabled 占位。
  - Milvus smoke 结果写回 `milvus_reindex_<国家>.json` manifest 和对应 run manifest。
- 配置：
  - `.env.example` 将 `RAG_VECTOR_STORE_SEARCH_ENABLED=true`、`RAG_MILVUS_SEARCH_ENABLED=true` 作为 Milvus 示例配置。
  - 当前本地 `.env` 已按用户授权配置为 Milvus + DashScope embedding + Qwen rerank，但 `.env` 仍被忽略，不提交。

验证：

- TDD RED：
  - Milvus auto schema/index/load 测试先因缺少 `ensure_collection` 失败。
  - Milvus smoke 测试先因缺少 `smoke_diagnostic` 失败。
  - Runtime Milvus Smoke 测试先因页面仍是 disabled 占位失败。
  - Server/Agent Milvus smoke 测试先因缺少 `run_milvus_smoke_diagnostic` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_milvus_vector_store_creates_collection_schema_index_and_load_when_missing tests/test_rag.py::test_milvus_vector_store_rejects_existing_vector_size_mismatch tests/test_rag.py::test_milvus_vector_store_smoke_diagnostic_writes_searches_and_deletes_temp_entity tests/test_renderer.py::test_runtime_page_uses_current_vector_store_actions_for_milvus tests/test_server.py::test_milvus_smoke_action_reports_search_and_cleanup tests/test_agents.py::test_agent_runs_milvus_smoke_diagnostic_from_latest_manifest -q`：6 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_server.py tests/test_renderer.py -q -k "milvus or qdrant or vector_store or rag"`：156 passed, 145 deselected。
- 回归验证：
  - `PYTHONPATH=. pytest tests -q`：404 passed。

当前限制：

- 本机检测到 Docker CLI 存在，但 Docker daemon 未运行：`Cannot connect to the Docker daemon`。因此本轮无法替你直接启动本地 Milvus 容器。
- 真实 Milvus 是否可用仍取决于外部服务是否启动、`MILVUS_URI` 是否正确、collection 创建接口是否和所用 Milvus/Zilliz 版本一致。
- 本版本实现的是 Milvus REST adapter 的自动建表和 smoke；没有加入 Docker Compose 编排文件。

## v0.7.24 - Final Acceptance Closure

日期：2026-07-08

阶段目标：

- 完成三步收口的第三步：不继续加功能，改为最终验收、文档一致性和交付说明。
- 把当前 Agent 架构、Memory、RAG、Harness、VLM、Milvus、飞书同步能力整理成可面试讲述的版本。

已完成：

- 新增最终验收说明：
  - `docs/final_acceptance/v0.7.24_final_acceptance.md`
  - 汇总当前架构、四层 memory、RAG 离线/在线链路、Harness、多模态生成、飞书同步。
  - 明确已完成、未完成、真实运行所需外部条件和面试叙事。
- README 口径修正：
  - Qwen 默认视觉模型统一为 `qwen3-vl-plus`。
  - RAG embedding / rerank 统一为推荐 `text-embedding-v4` + `qwen3-rerank`。
  - 移除旧 `text-embedding-v3/gte-rerank-v2` 批处理说法，避免和当前配置冲突。

验证：

- 回归验证：
  - `PYTHONPATH=. pytest tests -q`：399 passed。

当前限制：

- 本版本只做最终验收和文档收口，不新增 Milvus schema 自动创建、Milvus Smoke、外部 Phoenix/DeepEval API 集成或新的页面功能。
- `.env` 仍不提交；真实 VLM、RAG 远程调用、Milvus、飞书同步都依赖本地 `.env` 和外部服务可用性。

## v0.7.23 - Milvus Vector Store Closure and Full Retrieval Metrics

日期：2026-07-08

阶段目标：

- 完成第二步收口：把 RAG 向量库从历史 Qdrant 页面债收敛到通用 Vector Store 主链路，并让 Milvus 成为可配置、可入库、可展示、可讲清楚的工程方案。
- 将 RAG 检索评估从只看 `hit@5/mrr@5` 扩展到页面/manifest 可见的 `precision@5/recall@5/ndcg@5`。

已完成：

- Agent：
  - `rebuild_rag_knowledge_from_raw(...)` 返回 `precision@5`、`recall@5`、`ndcg@5`。
  - `reindex_rag_vector_store_from_raw(...)` 的 manifest 写入完整检索指标：
    - `hit@5`
    - `mrr@5`
    - `precision@5`
    - `recall@5`
    - `ndcg@5`
  - `_rag_knowledge_summary(...)` 读取通用 `vector_store_manifest_*` 指标，支持 Milvus/Qdrant/SQLite 统一展示。
  - 新增 `apply_approved_rag_patch_rebuild_and_reindex_vector_store(...)`，旧 `apply_approved_rag_patch_rebuild_and_reindex_qdrant(...)` 保持兼容。
- Runtime 页面：
  - RAG 操作区新增通用 `/reindex_rag_vector_store` 主按钮。
  - 页面根据当前 `RAG_VECTOR_STORE_PROVIDER` 显示：
    - `重建并入库Milvus`
    - `重建并入库Qdrant`
    - `重建并入库SQLite`
  - Milvus 模式不再把主按钮错误显示为 Qdrant。
  - “版本化知识库”卡片展示通用向量库 manifest 状态和完整检索指标。
- Server：
  - 新增 `/reindex_rag_vector_store` action。
  - 新增 `/apply_rag_patch_rebuild_and_reindex_vector_store` action。
  - 同步消息展示 provider、points、vector_size、hit/mrr/precision/recall/ndcg 和 manifest。
- 配置/文档：
  - `.env.example` 新增工业级 RAG 配置块：
    - `RAG_EMBEDDING_PROVIDER=dashscope`
    - `RAG_EMBEDDING_MODEL=text-embedding-v4`
    - `RAG_RERANK_PROVIDER=dashscope`
    - `RAG_RERANK_MODEL=qwen3-rerank`
    - `RAG_VECTOR_STORE_PROVIDER=milvus`
    - `MILVUS_URI`
    - `MILVUS_COLLECTION`
  - README 新增 Milvus RAG Provider 说明，明确在线向量检索需要显式开启 `RAG_VECTOR_STORE_SEARCH_ENABLED=true` 或 `RAG_MILVUS_SEARCH_ENABLED=true`。

验证：

- TDD RED：
  - Milvus Runtime 页面测试先因缺少 `/reindex_rag_vector_store` 和通用按钮失败。
  - 通用 vector store reindex server action 测试先因 server 不识别新路由失败。
  - Milvus reindex manifest 测试先因缺少 `precision@5/recall@5/ndcg@5` 失败。
  - 通用 patch+reindex action 测试先因缺少 `apply_approved_rag_patch_rebuild_and_reindex_vector_store(...)` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_renderer.py::test_runtime_page_uses_current_vector_store_actions_for_milvus tests/test_server.py::test_reindex_rag_vector_store_action_reports_provider_and_full_metrics tests/test_agents.py::test_agent_reindexes_raw_rag_knowledge_into_milvus -q`：3 passed。
  - `PYTHONPATH=. pytest tests/test_renderer.py::test_runtime_page_uses_current_vector_store_actions_for_milvus tests/test_server.py::test_apply_rag_patch_rebuild_and_reindex_vector_store_action_reports_provider tests/test_server.py::test_apply_rag_patch_rebuild_and_reindex_qdrant_action_reports_manifest tests/test_agents.py::test_agent_applies_rag_patch_rebuilds_and_reindexes_qdrant_with_manifest -q`：4 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_renderer.py tests/test_server.py tests/test_agents.py -q -k "rag or qdrant or milvus or vector_store"`：151 passed, 145 deselected。
- 回归验证：
  - `PYTHONPATH=. pytest tests -q`：399 passed。

当前限制：

- Milvus collection schema 自动创建仍未完成；当前假设 collection/schema 已按 Milvus REST 写入字段准备好。
- Milvus Smoke 目前在页面展示为未实现状态；Qdrant 的 smoke/rollback 仍保留为历史兼容能力。
- 本地 `.env` 不会提交；如果要真实走 Milvus，需要自行配置 `RAG_VECTOR_STORE_PROVIDER=milvus`、`MILVUS_URI`、`MILVUS_COLLECTION`，并显式开启在线检索开关。

## v0.7.22 - Value Match HITL UI and Qwen VL Default

日期：2026-07-08

阶段目标：

- 将 v0.7.21 已完成的数据层“价值观人工修正 -> Memory/RAG feedback”闭环接到试新页面，减少只停留在后端接口里的落差。
- 将图片理解默认模型从普通文本/多模态兼容口径收敛到 Qwen3-VL，避免真实图片解析继续被旧配置误导。
- 明确 RAG rerank 使用 Qwen rerank 专用模型，而不是把视觉模型当成 rerank 模型使用。

已完成：

- 试新页面：
  - 当已有价值观匹配结果时，展示“价值观人工修正”面板。
  - 支持运营填写人工修正和满意度。
  - 点击“反哺RAG/Memory”后调用 `/save_value_match_correction`。
- Server action：
  - 新增 `/save_value_match_correction`。
  - 调用 `record_value_match_human_correction(...)` 写入 working memory、facts memory 和 RAG eval feedback。
  - 页面返回清晰状态，包含本次写入的 memory id。
- 多模态配置：
  - `QwenVisionLLMClient` 默认模型改为 `qwen3-vl-plus`。
  - `VisionLLMClientFactory` 默认 `QWEN_VISION_MODEL` 改为 `qwen3-vl-plus`。
  - `.env.example` 同步为 `QWEN_VISION_MODEL=qwen3-vl-plus`。
- RAG 文档口径：
  - README 的 DashScope RAG 示例更新为 `text-embedding-v4` + `qwen3-rerank`。
  - 明确 Milvus 是后续替换方向，当前版本仍是本地 Python RAG 存储与检索闭环。

验证：

- 定向验证：
  - `PYTHONPATH=. pytest tests/test_renderer.py::test_trial_page_shows_value_match_human_correction_form tests/test_server.py::test_save_value_match_correction_action_writes_memory_and_status tests/test_server.py::test_rebuild_rag_knowledge_action_reports_file_eval tests/test_vision_llm.py::test_vision_llm_factory_creates_qwen_client_when_configured tests/test_rag.py::test_dashscope_config_defaults_to_real_embedding_and_rerank_models -q`：5 passed。
- 回归验证：
  - `PYTHONPATH=. pytest tests/test_server.py -q`：76 passed。
  - `PYTHONPATH=. pytest tests -q`：396 passed。

当前限制：

- 本地 `.env` 被 `.gitignore` 忽略，不会提交；如果 `.env` 仍写着 `QWEN_VISION_MODEL=qwen3.7-plus`，运行时会覆盖代码默认值，需要手动改成 `qwen3-vl-plus`。
- 本轮没有接入 Milvus 服务端，也没有把 RAG 存储迁移到 Milvus；这属于下一次收口。
- RAG rerank 使用 `qwen3-rerank` 专用模型；`qwen3-vl-plus` 只用于图片理解。

## v0.7.21 - Value Match HITL Feedback Memory Loop

日期：2026-07-07

阶段目标：

- 将价值观大师的人工修正沉淀到 Memory 与 RAG eval 反馈中，形成“AI 判断 - 运营修正 - 下轮评测/知识补丁”的闭环。
- 让生成式 RAG 与 VLM 判断后的人工修正不只停留在页面文本里，而是进入可检索、可复盘、可生成补丁的数据层。

已完成：

- Agent：
  - 新增 `record_value_match_human_correction(row, human_correction, satisfaction_score=None)`。
  - 写入 working memory：
    - `memory_type=value_match_human_correction`
    - 保存原 AI 价值观判断、人工修正、citation ids、满意度。
  - 写入 facts memory：
    - `memory_type=verified_value_match_fact`
    - 保存主体、三段式描述、人工修正、价值观标签、风险标签、citation ids。
  - 写入 RAG eval failure feedback：
    - `label_source=human_value_match_correction`
    - 将人工修正转成后续 RAG 评测/知识补丁候选。
  - 新增轻量抽取 helper：
    - citation id -> parent id
    - 人工修正 -> value labels
    - 人工修正 -> risk labels

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_records_value_match_human_correction_into_memory_and_rag_feedback -q`：先因缺少 `record_value_match_human_correction` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_records_value_match_human_correction_into_memory_and_rag_feedback -q`：1 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "memory or rag_eval_failure_feedback or harness_override or value_match_human_correction"`：13 passed。

当前限制：

- 当前方法已经完成数据沉淀，但页面上还没有单独的“保存价值观人工修正并反哺 RAG”按钮；后续需要接入 UI 和 server action，让运营能直接在工作台触发。

## v0.7.20 - Value Master Uses Generated RAG Evidence

日期：2026-07-07

阶段目标：

- 将 v0.7.19 的生成式 RAG 能力接入价值观大师，让价值观判断不只依赖片段召回，还能使用一段可溯源的 RAG 生成答案作为判断依据。
- 保持现有 VLM 主判断链路不被破坏，并继续保留未配置生成模型时的本地检索 fallback。

已完成：

- Agent：
  - 新增 `_rag_evidence_for_value_master(row)`，统一生成价值观大师需要的 RAG 证据。
  - `apply_value_master(row)` 优先尝试 `value_audit_rag_generated_answer(...)`。
  - 当生成式 RAG 成功时，VLM prompt 会收到：
    - `生成式RAG答案`
    - 真实 citation id
  - 当生成式 RAG 未配置或 skipped 时，仍回退到原有 RAG chunk 规则列表，不伪造生成答案。
  - 最终 `value_match` 追加 `生成式RAG依据：...` 摘要，方便运营在提需表里快速看到 AI 判断依据。
  - 摘要做了长度压缩，避免提需表字段过度拥挤。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_value_master_passes_generated_rag_answer_to_llm_prompt -q`：先因 VLM prompt 缺少 `生成式RAG答案` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_value_master_passes_generated_rag_answer_to_llm_prompt -q`：1 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "value_master"`：6 passed。

当前限制：

- 生成式 RAG 目前进入了价值观判断链路，但运营人工修改后的反馈还没有自动回写为 RAG eval case 或 memory fact；下一步应做 HITL 反馈沉淀。

## v0.7.19 - RAG Grounded Answer Generation

日期：2026-07-07

阶段目标：

- 将 RAG 在线阶段从“检索 + prompt 拼接”推进到“检索 + prompt + 生成答案 + trace 记录”。
- 为后续价值观判断、审核判断和内容发散提供可溯源的最终答案字段。

已完成：

- RAG：
  - 新增 `RagGeneratedAnswer`，统一承载 RAG 生成结果。
  - 新增 `QwenRagAnswerGenerator`，支持 DashScope/Qwen compatible chat-completions 风格调用。
  - 生成 prompt 明确要求：
    - 只根据提供资料回答。
    - 资料里没有依据就说不知道。
    - 不编造来源、数据或规则。
    - 输出围绕结论、依据、风险、建议。
  - 新增 `MissingRagAnswerGenerator`，未配置生成模型时返回 `skipped`，不伪造生成结果。

- Agent：
  - 新增 `value_audit_rag_generated_answer(...)`。
  - 生成结果写回最近 RAG trace：
    - `llm_answer`
    - `answer_source`
    - `generation_status`
    - `generation_provider`
    - `generation_model`
    - `generation_prompt`
    - `generation_citations`
    - `generation_latency_ms`
    - `generation_error`
  - 默认只有配置 `RAG_GENERATION_PROVIDER=qwen` 且 `RAG_ENABLE_REMOTE_CALLS=1` 时才会调用远程生成模型，避免无意产生费用。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_qwen_rag_answer_generator_builds_grounded_prompt_and_extracts_answer tests/test_agents.py::test_agent_generated_rag_answer_records_llm_output_in_trace -q`：先因缺少 `QwenRagAnswerGenerator` / `RagGeneratedAnswer` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_qwen_rag_answer_generator_builds_grounded_prompt_and_extracts_answer tests/test_agents.py::test_agent_generated_rag_answer_records_llm_output_in_trace -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "generated_rag_answer"`：2 passed。

当前限制：

- 当前生成链路已经可调用和可记录，但尚未默认替换价值观大师主流程；后续需要将生成式 RAG 答案接入价值观判断、审核判断和内容发散，并加入人工确认入口。

## v0.7.18 - RAG Trace Answer And Latency Fields

日期：2026-07-07

阶段目标：

- 继续补强工业级 RAG 可观测链路，让真实 `value_audit_rag_answer(...)` 调用写入可用于质量评估的 trace 字段。
- 为 v0.7.17 的 trace quality aggregation 提供真实运行数据来源，而不是只依赖手工构造的 trace。

已完成：

- Agent：
  - `value_audit_rag_answer(...)` 记录整段 RAG 检索与 prompt 构建耗时，并写入 `latency_ms`。
  - `_write_rag_trace(...)` 写入：
    - `answer`
    - `answer_source`
    - `support_documents`
    - `latency_ms`
  - `answer_source` 明确标记为 `retrieved_context`，表示当前是基于检索上下文的可评估答案，不伪装成真实 LLM 生成答案。
  - `recent_rag_traces(...)` 规范化 `support_documents` 和 `required_facts`，方便页面、报告和测试稳定读取。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_persists_value_audit_rag_trace_for_replay -q`：先因 trace 缺少 `answer` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_persists_value_audit_rag_trace_for_replay -q`：1 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "rag_trace or trace_quality_eval or rag_quality_eval_from_recent_traces"`：4 passed。

当前限制：

- 当前 RAG trace 的 `answer` 仍是检索上下文型答案；后续如果接入真正的 RAG 生成链路，需要把 LLM 最终回复、模型名、prompt 版本、人工修改结果一并写入 trace。

## v0.7.17 - RAG Trace Quality Eval Aggregation

日期：2026-07-07

阶段目标：

- 将 RAG 质量评估从“验收报告里手动传入”推进到“可以基于真实运行 trace 自动汇总”。
- 让 Ops 报告在没有 acceptance `quality_eval` 时，也能从最近 RAG trace 生成准确性、可信度、延迟、扩展性和用户体验摘要。

已完成：

- Agent：
  - 新增 `rag_trace_quality_eval_summary(country, limit=50)`。
  - 从最近 RAG trace 自动聚合：
    - `answer`
    - `reference_answer`
    - `support_documents`
    - `required_facts`
    - `latency_ms`
    - `satisfaction_score`
    - `citations`
  - 复用 `evaluate_rag_quality_report(...)` 生成统一质量指标。
  - `export_rag_ops_report(...)` 在 acceptance 缺少 `quality_eval` 时，自动 fallback 到 trace 汇总结果。
  - Ops Markdown 的 `RAG Quality Eval` 增加 `source` 和 `trace_count`，区分质量数据来源。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "rag_quality_eval_from_recent_traces or trace_quality_eval"`：先因缺少 `rag_trace_quality_eval_summary` 和 trace fallback 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "rag_quality_eval_from_recent_traces or trace_quality_eval"`：2 passed。

当前限制：

- 真实业务 trace 目前还需要更多字段沉淀，例如最终 LLM 答案、人工满意度、人工是否修改，后续才能把质量报告从“可计算”进一步升级为“可诊断、可回放、可改进”。

## v0.7.16 - RAG Ops Quality Eval Report

日期：2026-07-07

阶段目标：

- 将 v0.7.15 的 `quality_eval` 从底层 acceptance JSON 透出到 RAG Ops 报告。
- 让 RAG Ops 不只展示检索和模型调用，也展示答案准确率、可信度、延迟、扩展性和用户体验指标摘要。

已完成：

- Agent：
  - `latest_rag_acceptance_summary(country)` 透出 `quality_eval`。
  - `export_rag_ops_report(country, output_dir)` 顶层写入 `quality_eval`。
  - Markdown 报告新增 `RAG Quality Eval` 小节。
  - Markdown 展示：
    - `bleu1`
    - `rouge_l`
    - `support_overlap`
    - `document_coverage`
    - `average_ms`
    - `p95_ms`
    - `p99_ms`
    - `qps`
    - `corpus_document_count`
    - `average_satisfaction`
    - `satisfaction_rate`
    - `readability_score`

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_rag_ops_report_json_and_markdown -q`：先因 Ops 报告缺少 `quality_eval` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_rag_ops_report_json_and_markdown -q`：1 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q`：103 passed。
  - `PYTHONPATH=. pytest tests -q`：387 passed。
  - `git diff --check`：passed。

当前限制：

- `quality_eval` 目前仍需调用方提供或从 acceptance 阶段传入；后续应从真实 trace、人工反馈和回答样本自动聚合。

## v0.7.15 - RAG Quality Evaluation

日期：2026-07-07

阶段目标：

- 在检索指标之外，补齐 RAG 效果评估的第一版综合质量框架。
- 覆盖答案准确率、可信度、响应速度、可扩展性和用户体验评估。

已完成：

- RAG：
  - 新增 `evaluate_rag_quality_report(...)`。
  - 答案准确率：
    - `bleu1`：基于 token overlap 的 BLEU-1 风格精度。
    - `rouge_l`：基于最长公共子序列的 ROUGE-L 风格召回。
  - 可信度：
    - `support_overlap`：答案 token 在支持文档中的覆盖比例。
    - `document_coverage`：必需事实是否能在支持文档中找到。
  - 响应速度：
    - `average_ms`
    - `p95_ms`
    - `p99_ms`
    - `sample_count`
  - 可扩展性：
    - `qps`
    - `total_queries`
    - `total_seconds`
    - `corpus_document_count`
  - 用户体验：
    - `average_satisfaction`
    - `satisfaction_rate`
    - `readability_score`
  - `export_rag_acceptance_report(...)` 新增可选 `quality_eval` 参数，可把综合质量评估写入 acceptance JSON。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_evaluate_rag_quality_report_covers_answer_trust_latency_scalability_and_ux -q`：先因 `evaluate_rag_quality_report` 不存在失败。
  - `PYTHONPATH=. pytest tests/test_rag.py::test_export_rag_acceptance_report_can_include_quality_eval_block -q`：先因 acceptance report 不支持 `quality_eval` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_evaluate_rag_quality_report_covers_answer_trust_latency_scalability_and_ux tests/test_rag.py::test_export_rag_acceptance_report_can_include_quality_eval_block -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py -q`：54 passed。
  - `PYTHONPATH=. pytest tests -q`：387 passed。
  - `git diff --check`：passed。

当前限制：

- BLEU/ROUGE 为轻量本地实现，适合工程展示和趋势跟踪；后续可接专业评测库或 LLM-as-judge。
- 响应速度、吞吐量和满意度当前依赖调用方传入观测数据，后续可以从真实 trace 自动聚合。

## v0.7.14 - Retrieval Metrics Precision Recall NDCG

日期：2026-07-07

阶段目标：

- 按新增要求补齐 RAG 检索评估指标：MRR、NDCG@K、Precision@K、Recall@K。
- 支持一个查询对应多个相关父文档，适配多文档综合场景。

已完成：

- RAG：
  - `RagRetrievalCase` 新增 `relevant_parent_ids`，兼容原有单 `expected_parent_id`。
  - `evaluate_retrieval_report(...)` 输出：
    - `hit@K`
    - `mrr@K`
    - `precision@K`
    - `recall@K`
    - `ndcg@K`
  - case 级结果新增：
    - `relevant_parent_ids`
    - `relevant_hit_count`
    - `precision@K`
    - `recall@K`
    - `ndcg@K`
  - 新增 `_relevant_parent_ids(...)` 和 `_ndcg_at_k(...)`。
  - `export_rag_acceptance_report(...)` 复用 retrieval report，因此 acceptance JSON 自动包含新增指标。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_evaluate_retrieval_report_includes_precision_recall_and_ndcg_for_multi_relevant_docs -q`：先因 `RagRetrievalCase` 不支持 `relevant_parent_ids` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_evaluate_retrieval_report_includes_precision_recall_and_ndcg_for_multi_relevant_docs -q`：1 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py -q`：52 passed。
  - `PYTHONPATH=. pytest tests -q`：385 passed。
  - `git diff --check`：passed。

当前限制：

- 当前相关性等级先按二元相关处理；后续可扩展 graded relevance，用于更严格的 NDCG 分级相关性。
- 答案准确率、可信度、响应速度、可扩展性和用户体验评估将在后续版本继续补齐。

## v0.7.13 - Milvus Reindex and Acceptance Path

日期：2026-07-07

阶段目标：

- 把 Milvus 从在线检索 adapter 继续推进到 RAG 重建索引和 full industrial acceptance 主链路。
- 减少 Qdrant 专属方法对新 Milvus 目标的阻碍，同时保留旧接口兼容。

已完成：

- Agent：
  - 新增 `reindex_rag_vector_store_from_raw(...)`，作为通用向量库 reindex 入口。
  - 旧 `reindex_rag_qdrant_from_raw(...)` 委托通用入口，保留历史兼容。
  - 通用 reindex 结果新增 `vector_store_provider`、`vector_store_collection`、`vector_store_response`。
  - Milvus reindex manifest 写入 `indices/runs/milvus_reindex_<country>_<run_id>.json` 和 `indices/milvus_reindex_<country>.json`。
  - `_rag_knowledge_summary(...)` 新增 `vector_store_manifest_*` 通用字段，当前 provider 为 Milvus 时能展示 Milvus manifest。
  - full industrial acceptance 根据实际 store/provider 选择 `MilvusVectorStoreRetriever` 或 `QdrantVectorStoreRetriever`。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_reindexes_raw_rag_knowledge_into_milvus -q`：先因 `reindex_rag_vector_store_from_raw` 不存在失败。
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_runs_full_rag_industrial_acceptance_with_milvus -q`：先因 full acceptance 仍标记 `vector_store_provider=qdrant` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_reindexes_raw_rag_knowledge_into_milvus -q`：1 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_runs_full_rag_industrial_acceptance_with_milvus -q`：1 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q`：103 passed。
  - `PYTHONPATH=. pytest tests -q`：384 passed。
  - `git diff --check`：passed。

当前限制：

- 页面和 server action 仍有历史 `qdrant` 命名，后续应统一成 `vector_store` 或 `milvus` 文案。
- Milvus collection 建表/schema 自动创建还未做；当前假设 Milvus collection 已存在或由运维提前创建。

## v0.7.12 - Milvus Vector Store Adapter

日期：2026-07-07

阶段目标：

- 将 v0.7.11 的 Milvus 配置层推进到真实向量库 adapter。
- 让 RAG 在线检索能够在 `RAG_VECTOR_STORE_PROVIDER=milvus` 时走 Milvus vector search，而不是仍然只认 Qdrant。

已完成：

- RAG：
  - 新增 `MilvusVectorStore`。
  - 新增 `MilvusVectorStoreRetriever`。
  - Milvus healthcheck 使用 `/v2/vectordb/collections/describe` 形态，读取 collection 与 vector dim。
  - Milvus upsert 使用 `/v2/vectordb/entities/insert` 形态，把 chunk payload 展平成 entity 字段。
  - Milvus search 使用 `/v2/vectordb/entities/search` 形态，按 `country in [当前国家, GLOBAL]` 过滤，并返回 `chunk_id -> score`。
  - 新增 Milvus response 解析 helper：vector size、insert count、search score、filter value escaping。
- Agent：
  - `_rag_vector_store_search_enabled()` 支持 `RAG_MILVUS_SEARCH_ENABLED`。
  - `_rag_vector_store_retriever()` 在 provider 为 `milvus` 时返回 `MilvusVectorStoreRetriever`。
  - `value_audit_rag_summary(...)` 可以展示 Milvus provider 并让 retrieval trace 标记 `vector_store_provider=milvus`。

验证：

- TDD RED：
  - Milvus adapter 相关测试先因 `MilvusVectorStore` / `MilvusVectorStoreRetriever` 不存在失败。
  - Agent Milvus online search 测试先因 `_rag_vector_store_search_enabled()` 只认 Qdrant 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py -q`：51 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q`：101 passed。
  - `PYTHONPATH=. pytest tests -q`：382 passed。
  - `git diff --check`：passed。

当前限制：

- 旧页面和 server action 中仍有部分 `qdrant` 命名，这是历史 UI/路由命名债；核心 provider 已能识别并路由 Milvus。
- 本地测试通过 fake transport 验证 Milvus REST payload，不要求测试机启动真实 Milvus 服务。
- `qwen3-vl` 仍保留给视觉理解，不能作为 RAG embedding/reranker；RAG 模型链路仍应使用 Qwen3-Embedding / qwen3-rerank 或 BGE-Reranker-v2。

## v0.7.11 - Milvus Config and VLM Model Guard

日期：2026-07-06

阶段目标：

- 响应新增方向：RAG 向量数据库从只支持 Qdrant 配置，推进到可声明 Milvus。
- 防止把 `qwen3-vl` 这类视觉理解模型误配置成 embedding/reranker，避免 RAG 运行时出现“看似用了 Qwen，实际模型用途错误”的问题。

已完成：

- RAG：
  - `RagVectorStoreConfig.from_env(...)` 支持 `RAG_VECTOR_STORE_PROVIDER=milvus`。
  - Milvus 配置读取 `MILVUS_URI` / `RAG_MILVUS_URI`、`MILVUS_COLLECTION` / `RAG_MILVUS_COLLECTION`、`MILVUS_TOKEN` / `RAG_MILVUS_TOKEN`。
  - Milvus ready 状态和 `status_text` 会进入现有向量库配置/manifest/report 链路。
  - `RagProviderConfig.from_env(...)` 新增模型用途校验：如果 embedding 或 rerank 模型名像 `qwen3-vl` / `qwen-vl` / vision model，则 `remote_ready=False`、`remote_calls_enabled=False`。
  - 新增 `_rag_model_config_errors(...)` 和 `_looks_like_vlm_model(...)`，把“VLM 不能当 embedding/reranker”变成代码级保护。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_milvus_vector_store_config_reports_ready_uri tests/test_rag.py::test_rag_provider_config_rejects_qwen3_vl_for_embedding_and_rerank -q`：先因 Milvus 回落 sqlite、qwen3-vl 未拦截失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_milvus_vector_store_config_reports_ready_uri tests/test_rag.py::test_rag_provider_config_rejects_qwen3_vl_for_embedding_and_rerank -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py -q`：47 passed。
  - `PYTHONPATH=. pytest tests -q`：376 passed。
  - `git diff --check`：passed。

当前限制：

- 本版先完成 Milvus 配置层和报告链路，尚未接真实 Milvus 写入/查询 adapter。
- `qwen3-vl` 保留给多模态图片理解；RAG embedding/reranker 应继续使用 Qwen3-Embedding / qwen3-rerank 或 BGE-Reranker-v2 等专用模型。

## v0.7.10 - RAG Live Model Evidence

日期：2026-07-06

阶段目标：

- 把“是否真实调用 Qwen3-Embedding / BGE-Reranker-v2”从口头说明升级为可导出的验收证据。
- 在不把普通 pytest 变成付费外部调用的前提下，让真实 RAG acceptance run 和 RAG Ops report 能记录远程调用、fallback 与模型家族。

已完成：

- RAG：
  - `export_rag_acceptance_report(...)` 新增 `live_model_evidence`。
  - `live_model_evidence.embedding` 记录 provider、model、`model_family`、observed remote calls、fallbacks、是否 fallback-free。
  - `live_model_evidence.rerank` 记录 provider、model、`provider_family`、observed remote calls、fallbacks、是否 fallback-free。
  - 新增 `_rerank_model_family(...)`，将 BGE provider 或 `bge-reranker-v2` 模型归类为 `BGE-Reranker-v2`。
  - `overall.verified` 只有在远程调用已开启、embedding/rerank 都观测到 remote call 且 fallback 为 0 时才为 true。
- Agent：
  - `latest_rag_acceptance_summary(country)` 透出 `live_model_evidence`。
  - `export_rag_ops_report(country, output_dir)` 顶层导出 `live_model_evidence`。
  - Markdown 报告新增 `RAG Live Model Evidence` 小节，展示 Qwen3-Embedding 与 BGE-Reranker-v2 证据。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_export_rag_acceptance_report_records_observed_runtime_routes_and_stats -q`：先因缺少 `live_model_evidence` 失败。
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_rag_ops_report_json_and_markdown -q`：先因 Ops 报告未透出 `live_model_evidence` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_export_rag_acceptance_report_records_observed_runtime_routes_and_stats tests/test_agents.py::test_agent_exports_rag_ops_report_json_and_markdown -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py -q`：45 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q`：99 passed。
  - `PYTHONPATH=. pytest tests -q`：374 passed。
  - `git diff --check`：passed。

当前限制：

- 普通自动化测试仍使用 fake transport / mock provider 证明证据结构，不会默认真实消耗阿里云费用。
- 真正的外部调用证明需要在 `.env` 中配置 `RAG_ENABLE_REMOTE_CALLS=true`、DashScope/Qwen key、BGE rerank endpoint 后运行 live acceptance；报告会用 remote call/fallback 数据证明是否生效。

## v0.7.9 - RAG Ops Report Case Diff

日期：2026-07-05

阶段目标：

- 让导出的 RAG Ops 报告不仅展示 patch run 总体指标，还能直接说明 case 级别的修复与新增失败。
- 支撑后续工业级 RAG 验收：每次知识补丁应用后，可以在 JSON/Markdown 报告中追踪“修好了哪些 query / 又引入了哪些失败”。

已完成：

- Agent：
  - `export_rag_ops_report(country, output_dir)` 新增顶层 `patch_case_diff`。
  - `patch_case_diff` 包含 `fixed_failure_count`、`new_failure_count`、`fixed_failures`、`new_failures`。
  - Markdown 报告新增 `RAG Patch Case Diff` 小节。
  - 报告复用 v0.7.7/v0.7.8 已建立的 patch run comparison 和 manifest eval cases，不新增外部依赖。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_rag_ops_report_json_and_markdown -q`：先因缺少 `patch_case_diff` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_rag_ops_report_json_and_markdown -q`：1 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q`：99 passed。
  - `PYTHONPATH=. pytest tests -q`：374 passed。
  - `git diff --check`：passed。

当前限制：

- 报告中的 case diff 依赖 patch manifest 已记录 `rebuild.cases`；v0.7.8 之前的历史 run 仍只能显示空 diff。
- 当前只导出 case id，后续可以继续扩展为 query、gold chunk、实际召回 chunk、失败诊断和人工修正建议。

## v0.7.8 - RAG Patch Manifest Eval Cases

日期：2026-07-05

阶段目标：

- 补齐 v0.7.7 case-level diff 的数据生产链路。
- 确保每次应用 RAG 知识补丁并重建时，patch manifest 自动记录完整 eval case 明细，后续 run comparison 可以稳定判断哪些失败样本被修复、哪些失败样本新增。

已完成：

- Agent：
  - `rebuild_rag_knowledge_from_raw(country)` 返回 `failed_count` 和完整 `cases`。
  - `apply_approved_rag_patch_and_rebuild(country)` 的 manifest `rebuild` 写入 `failed_count` 和 `cases`。
  - `rollback_latest_approved_rag_patch_and_rebuild(country)` 的 manifest `rebuild_after_rollback` 同样写入 `failed_count` 和 `cases`。
  - 新 patch run 不再只记录总分，也记录 case-level replay 数据。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_applies_approved_rag_patch_and_rebuilds_processed_with_eval -q`：先因 manifest 缺少 `rebuild.cases` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_applies_approved_rag_patch_and_rebuilds_processed_with_eval -q`：1 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q`：99 passed。
  - `PYTHONPATH=. pytest tests -q`：374 passed。

当前限制：

- 旧历史 manifest 仍然没有 `rebuild.cases`，只能从 v0.7.8 之后的新 run 开始完整 case diff。
- case 明细来自当前本地 RAG eval，不代表真实线上用户反馈；后续仍需和真实 human_gold / HITL 修正闭环结合。

## v0.7.7 - RAG Patch Case Diff

日期：2026-07-05

阶段目标：

- 将 patch run 对比从总分层面推进到 case 级别。
- 让 RAG Ops 能说明“哪些失败样本被修复了、哪些失败样本是新增的”，而不是只看 `hit@5` 总分变化。

已完成：

- Agent：
  - `_rag_patch_manifest_row(...)` 从 manifest 的 `rebuild.cases` 中保留 `rebuild_cases`。
  - `_rag_patch_run_comparison(...)` 新增 case-level diff。
  - 新增 `fixed_failure_count`、`new_failure_count`、`fixed_failures`、`new_failures`。
  - 兼容老 manifest：没有 cases 时返回空 diff，不影响现有 patch runs。
- Runtime 页面：
  - `RAG Patch Compare` 卡片新增 `fixed`、`new_failures` 和前 3 个 fixed ids。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_patch_ops_summary_compares_latest_two_runs tests/test_renderer.py::test_runtime_rag_summary_shows_patch_ops_status -q`：先因缺少 case diff 字段和页面展示失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_patch_ops_summary_compares_latest_two_runs tests/test_renderer.py::test_runtime_rag_summary_shows_patch_ops_status -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py -q`：151 passed。
  - `PYTHONPATH=. pytest tests -q`：374 passed。

当前限制：

- case diff 依赖 patch manifest 中存在 `rebuild.cases`；历史旧 run 没有该字段时只能显示空 diff。
- 后续应在 patch apply 阶段完整写入 eval cases，保证所有新 run 都可做 case-level replay。

## v0.7.6 - RAG Patch Priority Impact

日期：2026-07-05

阶段目标：

- 把 P0/P1/P2 知识补丁队列和 patch run 指标对比打通。
- 让 Runtime 不只展示“有多少高优先级补丁”，还展示最近 patch run 是否带来 `hit@5/mrr@5` 改善，以及下一步建议动作。

已完成：

- Agent：
  - `rag_patch_ops_summary(country)` 新增 `priority_summary` 和 `priority_impact`。
  - `priority_impact` 汇总 pending P0/P1/P2、`hit@5_delta`、`mrr@5_delta`、effect 和 recommended_action。
  - effect 支持：
    - `improved`
    - `regressed`
    - `no_change`
    - `no_baseline`
  - recommended_action 支持继续应用高优先级补丁、回滚/复核、调整权重或继续跑实验。
- Runtime 页面：
  - `RAG Patch Compare` 卡片新增 pending_P0、effect 和 recommended_action。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_patch_ops_summary_compares_latest_two_runs tests/test_renderer.py::test_runtime_rag_summary_shows_patch_ops_status -q`：先因缺少 `priority_impact` 和页面展示失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_patch_ops_summary_compares_latest_two_runs tests/test_renderer.py::test_runtime_rag_summary_shows_patch_ops_status -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py -q`：151 passed。
  - `PYTHONPATH=. pytest tests -q`：374 passed。

当前限制：

- 当前 impact 基于最近两个 patch run 的指标变化，不是完整 A/B 实验。
- 后续可进一步引入按 case 的失败样本新增/修复对比，证明具体 P0 patch 修复了哪些真实 human_gold 样本。

## v0.7.5 - RAG Patch Priority Ops Summary

日期：2026-07-05

阶段目标：

- 将 v0.7.4 的 P0/P1/P2 知识补丁优先级从表格行级信息，上升到 Runtime 摘要和 RAG Ops 报告。
- 让运营/工程一眼看到高优先级 RAG 知识补丁积压，而不必逐行扫表。

已完成：

- Agent：
  - `rag_knowledge_patch_drafts(...)` 新增 `priority_summary`。
  - `priority_summary` 汇总 P0/P1/P2 数量、total、top_score 和 top_patch。
  - `export_rag_ops_report(...)` 的 JSON 新增 `patch_priority_summary`。
  - Markdown 报告新增 `RAG Patch Priority` 小节。
- Runtime 页面：
  - `RAG知识补丁草案` 摘要卡新增 P0/P1/P2 积压展示。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_rag_ops_report_json_and_markdown tests/test_renderer.py::test_runtime_page_shows_rag_patch_priority -q`：先因缺少 `patch_priority_summary` 和 Runtime 摘要失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_rag_ops_report_json_and_markdown tests/test_renderer.py::test_runtime_page_shows_rag_patch_priority -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py -q`：151 passed。
  - `PYTHONPATH=. pytest tests -q`：374 passed。

当前限制：

- 本版展示的是当前草案队列积压，不直接证明 patch 应用后的指标收益。
- 后续应继续把 P0 patch 应用前后的 `hit@5/mrr@5/failed_count` 变化接入实验对比。

## v0.7.4 - RAG Patch Priority

日期：2026-07-05

阶段目标：

- 把 v0.7.3 的 RAG 失败诊断进一步转化为知识补丁优先级。
- 让运营和工程不只是看到失败 case，而是知道哪些 patch 最应该优先审核和补入知识库。

已完成：

- Agent：
  - `record_rag_eval_failure_feedback(...)` 支持记录 `diagnosis`、`suggested_action`、`gold_grade`、`label_source`。
  - `rag_eval_failure_feedback_summary(...)` 透传诊断和业务标签。
  - `rag_knowledge_patch_drafts(...)` 新增 `priority_score`、`priority_band`、`priority_reason`。
  - 草案按 `priority_score` 排序，高价值真实样本优先。
  - 评分考虑：
    - `human_gold` / `ai_silver`
    - S/A/B/C/D 等级
    - 知识缺失、候选召回缺失、BM25/向量召回缺失、rerank 过滤等诊断
    - audit/global 类型 expected parent
- Runtime 页面：
  - `RAG知识补丁草案` 表格新增 `优先级` 列。
  - 展示 `P0/P1/P2`、`priority_score` 和优先级原因。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_prioritizes_rag_knowledge_patch_drafts_by_business_impact tests/test_renderer.py::test_runtime_page_shows_rag_patch_priority -q`：先因缺少诊断入参和优先级列失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_prioritizes_rag_knowledge_patch_drafts_by_business_impact tests/test_renderer.py::test_runtime_page_shows_rag_patch_priority -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py -q`：151 passed。
  - `PYTHONPATH=. pytest tests -q`：374 passed。

当前限制：

- 优先级评分是规则化权重，不是训练模型；后续可用真实修复收益反向校准权重。
- 当前优先级只影响草案排序和展示，后续还可以接入 patch run 对比，证明 P0 patch 是否实际提升 hit@5。

## v0.7.3 - RAG Failure Diagnosis

日期：2026-07-05

阶段目标：

- 让真实样本 RAG gate 失败时不只给出 `FAIL`，而是解释失败更可能发生在知识缺失、候选召回、向量召回、BM25 召回或 rerank 阶段。
- 将失败 case 变成可行动的优化线索，支撑后续补知识库、调 chunk、调 top-k、调 rerank 和 hard negative。

已完成：

- RAG 评测：
  - `evaluate_retrieval_report(...)` 改为基于 `search_with_trace(...)` 生成 case 结果。
  - 每个 case 新增 `diagnosis`、`suggested_action`、`failure_reason` 和 `route_evidence`。
  - 失败诊断覆盖：
    - `country_knowledge_missing`
    - `knowledge_missing_or_query_mismatch`
    - `bm25_recall_missing`
    - `vector_recall_missing`
    - `rerank_filtered_expected`
    - `candidate_recall_missing`
  - `route_evidence` 记录 BM25/vector/exact/final 是否包含 expected parent，以及候选池规模。
- Runtime 页面：
  - `RAG Eval Case 证据` 表格新增 `诊断` 和 `建议动作` 两列。
  - 失败 case 可以直接看到下一步应补知识、扩同义词、重建 Qdrant、调 rerank 或扩大 top-k。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_evaluate_retrieval_report_diagnoses_failed_business_sample_routes tests/test_renderer.py::test_runtime_rag_eval_case_evidence_shows_failure_diagnosis -q`：先因缺少诊断字段和页面列失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_evaluate_retrieval_report_diagnoses_failed_business_sample_routes tests/test_renderer.py::test_runtime_rag_eval_case_evidence_shows_failure_diagnosis -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_renderer.py -q`：194 passed。
  - `PYTHONPATH=. pytest tests -q`：372 passed。

当前限制：

- 本版诊断是基于 trace 的规则化解释，不是 LLM 自动根因分析。
- 诊断先解决检索层可运维问题；后续还需要把诊断结果沉淀到实验对比和知识补丁优先级中。

## v0.7.2 - Human Gold RAG Business Gate

日期：2026-07-05

阶段目标：

- 让 RAG 验收不再只看静态规则/审核案例的整体 `hit@5`，而是单独统计真实 `human_gold` 拼图样本的业务验收指标。
- 避免“静态 eval 通过”掩盖真实业务样本召回失败的问题，让工业级 RAG 包装更可信。

已完成：

- Agent：
  - 新增真实 `human_gold` 样本的 `business_sample_gate`。
  - `value_audit_rag_eval_report(country)` 同时返回整体 eval 和真实业务样本 gate。
  - `export_value_audit_rag_acceptance_report(country, output_dir)` 导出的 JSON 中写入 `business_sample_gate`。
  - gate 包含 `case_count`、`hit@5`、`mrr@5`、`threshold`、`passed_threshold`、`failed_count` 和失败 case 明细。
- Runtime 页面：
  - `真实 Eval Dataset` 卡片新增 `business cases`、`business_hit@5` 和 `business_gate`。
  - 页面能直接看出真实样本是否达到 `hit@5 >= 0.8`。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_acceptance_report_tracks_human_gold_business_sample_gate tests/test_renderer.py::test_runtime_page_shows_business_sample_rag_gate -q`：先因缺少 `business_sample_gate` 和页面字段失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_acceptance_report_tracks_human_gold_business_sample_gate tests/test_renderer.py::test_runtime_page_shows_business_sample_rag_gate -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py -q`：148 passed。
  - `PYTHONPATH=. pytest tests -q`：370 passed。

当前限制：

- `business_sample_gate` 只统计已人工确认的 `human_gold/reviewed` 样本；`ai_silver` 不会被当作最终业务验收标准。
- 当前 gate 仍是检索层指标，后续还需要把真实 VLM 解析准确率、价值观判断一致率和风险召回率并入同一份实验对比。

## v0.7.1 - RAG Ops Report Export

日期：2026-07-04

阶段目标：

- 把 Runtime 中分散的 RAG Live Model Ops、Patch Ops、Acceptance、真实样本评测集和 Qdrant 知识库状态汇总为一份可导出的 Ops 报告。
- 为“工业级 RAG”包装补一层可复盘证据：既能给机器读取 JSON，也能给业务/面试复盘直接阅读 Markdown。

已完成：

- Agent：
  - 新增 `export_rag_ops_report(country, output_dir)`。
  - 导出 `rag_ops_report_<国家>.json` 和 `rag_ops_report_<国家>.md`。
  - 报告汇总 live model 状态、embedding/rerank 远程调用次数、fallback 次数、Qdrant 命中、hit@5、mrr@5、补丁状态、真实样本集状态和 Qdrant manifest。
- Runtime 页面：
  - 在 `价值观与审核 RAG` 操作区新增 `导出RAG Ops报告`。
- Server：
  - 新增 `/export_rag_ops_report` action。
  - 点击后写入 `runtime/rag_acceptance_reports`，并在页面消息中回显 JSON/Markdown 路径。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_rag_ops_report_json_and_markdown tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_export_rag_ops_report_action_writes_json_and_markdown -q`：先因缺少 Agent 导出方法、页面按钮和 Server action 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_rag_ops_report_json_and_markdown tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_export_rag_ops_report_action_writes_json_and_markdown -q`：3 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：221 passed。
  - `PYTHONPATH=. pytest tests -q`：368 passed。

当前限制：

- 报告读取的是最近一次 RAG acceptance summary 和 patch manifest，不会主动触发真实模型调用。
- 若还没有跑过 `一键RAG全链路验收`，报告会明确显示 `mode=not_run` 或空验收状态。

## v0.7.0 - RAG Live Model Ops

日期：2026-07-04

阶段目标：

- 在 Runtime 中集中展示 Qwen embedding、Qdrant、BGE rerank 的 live/fast preflight 与真实调用统计。
- 让“真实模型调用是否发生、是否 fallback、Qdrant 是否命中”不只存在于 JSON 报告和同步消息里，而是在页面上直接可见。

已完成：

- Agent：
  - 新增 `rag_live_model_ops_summary(country)`。
  - 基于最近一次 `rag_acceptance_full_summary_<国家>.json` 汇总 live model 状态。
  - 输出 mode、status、failure_stage、embedding/qdrant/rerank ready、provider、embedding/rerank remote calls、fallbacks、qdrant_vector_hits、hit@5、mrr@5。
  - 将 `rag_live_model_ops` 合入 `value_audit_rag_summary(country)`。
- Runtime 页面：
  - 新增 `RAG Live Model Ops` 卡片。
  - 展示 embedding/qdrant/rerank ready 状态、remote/fallback 次数、qdrant_hit、hit@5 和 provider。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_live_model_ops_summary_reads_latest_acceptance tests/test_renderer.py::test_runtime_page_shows_latest_rag_preflight_summary -q`：先因缺少 Agent summary 和页面卡片失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_live_model_ops_summary_reads_latest_acceptance tests/test_renderer.py::test_runtime_page_shows_latest_rag_preflight_summary -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：219 passed。
  - `PYTHONPATH=. pytest tests -q`：366 passed。

当前限制：

- 页面展示的是最近一次 acceptance summary；真正的 live 检查仍由 `一键RAG全链路验收` 触发。
- 如果没有运行过验收，会显示 `mode=not_run` 和当前配置状态，不会主动发起外部网络调用。

## v0.6.9 - RAG Patch Run Comparison

日期：2026-07-04

阶段目标：

- 在 `RAG Patch Ops` 中增加 latest run 与上一 run 的对比摘要。
- 让运营和面试讲解可以直观看到 patch 变更对 hit@5、mrr@5 和 Qdrant points 的影响。

已完成：

- Agent：
  - `rag_patch_ops_summary(country)` 新增 `run_comparison`。
  - 以 latest manifest 作为 current run，避免依赖文件名排序判断当前版本。
  - 从 history runs 中选取第一个不同 run_id 作为 previous run。
  - 输出 current_run_id、previous_run_id、hit@5_delta、mrr@5_delta、qdrant_points_delta、status_changed。
- Runtime 页面：
  - 新增 `RAG Patch Compare` 卡片。
  - 展示 current vs previous、hit@5 Δ、mrr@5 Δ、points Δ、status_changed。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_patch_ops_summary_compares_latest_two_runs tests/test_renderer.py::test_runtime_rag_summary_shows_patch_ops_status -q`：先因缺少 run_comparison 和页面 compare 卡片失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_patch_ops_summary_compares_latest_two_runs tests/test_renderer.py::test_runtime_rag_summary_shows_patch_ops_status -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：218 passed。
  - `PYTHONPATH=. pytest tests -q`：365 passed。

当前限制：

- 当前只对比 latest 与上一条不同 run；还未支持任意两个 run 的手动选择。
- 对比指标先覆盖 hit@5、mrr@5、Qdrant points 和状态变化；更细的失败样本差异后续可接 Harness case evidence。

## v0.6.8 - RAG Patch Run Evidence Details

日期：2026-07-04

阶段目标：

- 让 `RAG Patch Runs` 不只展示状态，还能展开查看每个 run 的证据链。
- 补强 Agent Harness/RAG Ops 的可追溯性：从 run 行可以追到 patch ids、raw patch、processed 文档、patch manifest、Qdrant manifest 和 rollback 文件。

已完成：

- Agent：
  - `_rag_patch_manifest_row()` 新增 `evidence` 字段。
  - evidence 包含 patch_ids、raw_patch_path、processed_path、patch_manifest_path、qdrant_manifest_path、rollback_removed。
  - `rag_patch_ops_summary(country)` 的 latest 与 recent runs 均携带同结构 evidence。
- Runtime 页面：
  - `RAG Patch Runs` 表格新增 `证据` 列。
  - 每条 run 支持 `<details>` 展开证据明细。
  - 展示 patch_ids、raw、processed、patch_manifest、qdrant_manifest、rollback。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_patch_ops_summary_includes_recent_runs tests/test_renderer.py::test_runtime_rag_summary_shows_patch_ops_status -q`：先因缺少 evidence 字段和页面详情展示失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_patch_ops_summary_includes_recent_runs tests/test_renderer.py::test_runtime_rag_summary_shows_patch_ops_status -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：217 passed。
  - `PYTHONPATH=. pytest tests -q`：364 passed。

当前限制：

- 证据详情目前以文本形式展示路径；还未提供点击打开文件、diff 对比或下载 manifest。
- 只展示最近 8 条 run。

## v0.6.7 - RAG Patch Runs History

日期：2026-07-04

阶段目标：

- 在 `RAG Patch Ops` latest 状态卡之外，增加最近 patch run 历史复盘。
- 让运营和面试讲解可以看到最近几次 apply/rebuild/rollback/Qdrant 入库的状态变化，而不只看最新状态。

已完成：

- Agent：
  - `rag_patch_ops_summary(country)` 新增 `recent_runs`。
  - 读取 `knowledge/patch_manifests/runs/rag_patch_apply_<国家>_*.json`。
  - 每条 run 统一解析 run_id、status、patch_count、raw patch、rebuild hit@5/mrr@5、Qdrant status/points/vector_size、rollback removed path。
  - latest summary 与 recent run 共用 manifest row 解析逻辑。
- Runtime 页面：
  - 新增 `RAG Patch Runs` 表格。
  - 展示 Run、状态、Patch 数、hit@5、Qdrant、Points、Rollback。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_patch_ops_summary_includes_recent_runs tests/test_renderer.py::test_runtime_rag_summary_shows_patch_ops_status -q`：先因缺少 recent_runs 和页面表格失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_patch_ops_summary_includes_recent_runs tests/test_renderer.py::test_runtime_rag_summary_shows_patch_ops_status -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：217 passed。
  - `PYTHONPATH=. pytest tests -q`：364 passed。

当前限制：

- 本版本展示最近 8 条 patch run；还未提供页面上的 run_id 级详情展开或任意 run 对比。
- 回滚后的 run 会展示回滚后的 rebuild 指标，这是为了直接呈现当前生效状态。

## v0.6.6 - Runtime RAG Patch Ops Status

日期：2026-07-04

阶段目标：

- 把 RAG patch 的 raw、processed、eval、Qdrant 入库证据集中展示到 Runtime 页面。
- 让运营和面试讲解时可以直接看到 latest patch 的状态，而不是只依赖操作后的同步消息。

已完成：

- Agent：
  - 新增 `rag_patch_ops_summary(country)`。
  - 读取 `knowledge/patch_manifests/rag_patch_apply_<国家>.json`。
  - 汇总 status、run_id、patch_count、patch_ids、raw_patch_file、processed_path、rebuild hit@5/mrr@5、qdrant status/points/vector_size/manifest、rollback removed path。
  - 将 `rag_patch_ops` 合入 `value_audit_rag_summary(country)`。
  - 无 manifest 时返回稳定的 `status=none`。
- Runtime 页面：
  - 新增 `RAG Patch Ops` 卡片。
  - 展示 patch 数量、rebuild hit@5/mrr@5、Qdrant status、points、vector_size 和 raw patch 文件名。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_patch_ops_summary_reads_latest_patch_manifest tests/test_renderer.py::test_runtime_rag_summary_shows_patch_ops_status -q`：先因缺少 Agent summary 和页面卡片失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_patch_ops_summary_reads_latest_patch_manifest tests/test_renderer.py::test_runtime_rag_summary_shows_patch_ops_status -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：216 passed。
  - `PYTHONPATH=. pytest tests -q`：363 passed。

当前限制：

- 本版本只展示 latest patch manifest；历史 run 的完整对比还未做成页面表格。
- Qdrant 真实可用性仍以 reindex/acceptance action 的运行结果为准。

## v0.6.5 - RAG Patch Qdrant Acceptance

日期：2026-07-04

阶段目标：

- 把已审核 RAG 补丁治理从 raw/processed/eval 继续推进到向量库层。
- 让一次补丁应用可以串联完成：写入 raw、重建 processed、重建 Qdrant、并把 Qdrant 验收结果写入 patch manifest。

已完成：

- Agent：
  - 新增 `apply_approved_rag_patch_rebuild_and_reindex_qdrant(country, ...)`。
  - 支持可选注入 `embedding_provider` 和 `vector_store`，便于单测和本地诊断不依赖真实 Qdrant。
  - 内部复用 `apply_approved_rag_patch_and_rebuild()` 与 `reindex_rag_qdrant_from_raw()`。
  - patch manifest 的 status 更新为 `applied_rebuilt_qdrant_indexed`。
  - patch manifest 新增 `qdrant` 区块，记录 status、qdrant manifest path、latest manifest path、upserted_points、chunk_count、vector_count、vector_size、hit@5、mrr@5、collection。
- Runtime 页面：
  - `价值观与审核 RAG` 操作区新增 `应用补丁并入库Qdrant` 按钮。
- Server：
  - 新增 `/apply_rag_patch_rebuild_and_reindex_qdrant` action。
  - 同步消息展示 Qdrant status、points、vector_size、hit@5、patch manifest 和 qdrant manifest。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_applies_rag_patch_rebuilds_and_reindexes_qdrant_with_manifest tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_apply_rag_patch_rebuild_and_reindex_qdrant_action_reports_manifest -q`：先因缺少 Agent 方法、页面入口和 server action 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_applies_rag_patch_rebuilds_and_reindexes_qdrant_with_manifest tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_apply_rag_patch_rebuild_and_reindex_qdrant_action_reports_manifest -q`：3 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：214 passed。
  - `PYTHONPATH=. pytest tests -q`：361 passed。

当前限制：

- Runtime 的真实 Qdrant 入库仍依赖 `.env` 中 Qdrant、embedding provider 和网络/额度可用性。
- 本版本将 Qdrant 结果写回 patch manifest，但暂未把 patch rollback 与 Qdrant point restore 自动联动；如已入库后回滚 raw patch，仍需要显式执行 Qdrant 重建或后续补联动回滚。

## v0.6.4 - Rollback Latest RAG Patch

日期：2026-07-04

阶段目标：

- 给已审核 RAG 补丁的 raw 生效层增加回滚能力，避免知识库补丁只能单向写入。
- 回滚后自动 rebuild processed RAG 文档，并把回滚证据写回 patch manifest。

已完成：

- Agent：
  - 新增 `rollback_latest_approved_rag_patch_and_rebuild(country)`。
  - 读取 `knowledge/patch_manifests/rag_patch_apply_<国家>.json`。
  - 删除 latest manifest 指向的 `knowledge/raw/approved_rag_patch_<国家>_<run_id>.md`。
  - 自动执行 `rebuild_rag_knowledge_from_raw(country)`。
  - 将 latest manifest 和对应 run manifest 的 status 更新为 `rolled_back_rebuilt`。
  - manifest 新增 `rollback` 和 `rebuild_after_rollback` 区块，记录 removed_raw_patch_path、processed_path、hit@5、mrr@5、passed_threshold、eval_total。
- Runtime 页面：
  - `价值观与审核 RAG` 操作区新增 `回滚最新补丁并重建` 按钮。
- Server：
  - 新增 `/rollback_latest_rag_patch_and_rebuild` action。
  - 成功后展示 removed、processed、hit@5、mrr@5 和 manifest 路径。
  - 找不到 latest manifest 或 raw patch 时返回明确失败信息。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rolls_back_latest_approved_rag_patch_and_rebuilds tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_rollback_latest_rag_patch_and_rebuild_action_reports_eval -q`：先因缺少 rollback 方法、页面入口和 server action 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rolls_back_latest_approved_rag_patch_and_rebuilds tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_rollback_latest_rag_patch_and_rebuild_action_reports_eval -q`：3 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：212 passed。
  - `PYTHONPATH=. pytest tests -q`：359 passed。

当前限制：

- 本版本回滚的是 raw 知识库生效文件，不删除长期 memory 中的人工审核记录。
- 回滚后不会自动更新 Qdrant；如果线上向量库已经入库，需要继续执行 Qdrant 重建或未来补 Qdrant 联动回滚。

## v0.6.3 - Apply Patch and Rebuild RAG

日期：2026-07-04

阶段目标：

- 把已审核 RAG 补丁从“写入 raw”推进到“写入 raw 后自动 rebuild processed，并记录 eval 结果”。
- 让知识补丁不只停留在文件层，而是能被 RAG processed 文档和 hit@5 验收证明已经生效。

已完成：

- Agent：
  - `export_approved_rag_patch_markdown()` 导出的补丁标题新增显式 section id：`{#expected_parent_id}`。
  - 新增 `apply_approved_rag_patch_and_rebuild(country)`。
  - 执行流程为：应用已审补丁到 `knowledge/raw` → rebuild processed RAG 文档 → 跑 file/business eval → 更新 patch manifest。
  - patch manifest 的 status 会从 `applied` 更新为 `applied_rebuilt`。
  - manifest 新增 `rebuild` 区块，记录 processed_path、document_count、hit@5、mrr@5、passed_threshold、eval_total。
- Runtime 页面：
  - `价值观与审核 RAG` 操作区新增 `应用补丁并重建RAG` 按钮。
- Server：
  - 新增 `/apply_approved_rag_patch_and_rebuild` action。
  - 同步消息展示 raw、processed、hit@5、mrr@5 和 manifest 路径。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_applies_approved_rag_patch_and_rebuilds_processed_with_eval tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_apply_approved_rag_patch_and_rebuild_action_reports_eval -q`：先因缺少 apply+rebuild 方法、页面入口和 server action 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_applies_approved_rag_patch_and_rebuilds_processed_with_eval tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_apply_approved_rag_patch_and_rebuild_action_reports_eval -q`：3 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：210 passed。
  - `PYTHONPATH=. pytest tests -q`：357 passed。

当前限制：

- 本版本不会自动重建 Qdrant；向量库入库仍需要运营显式点击 `重建并入库Qdrant` 或 `一键RAG全链路验收`。
- 暂未实现 patch manifest 回滚；下一步可以基于 `patch_manifests/runs` 做 raw patch 回滚/禁用。

## v0.6.2 - Apply Approved RAG Patch to Raw

日期：2026-07-04

阶段目标：

- 把已审核 RAG Markdown 补丁从“可导出文件”推进到“可受控写入 raw 知识库”的版本治理入口。
- 为后续知识库回滚、版本对比和正式 rebuild/reindex 打基础。

已完成：

- Agent：
  - 新增 `apply_approved_rag_patch_markdown_to_raw(country)`。
  - 将已审核长期记忆导出到 `knowledge/raw/approved_rag_patch_<国家>_<run_id>.md`。
  - 新增 `knowledge/patch_manifests/runs/rag_patch_apply_<国家>_<run_id>.json`。
  - 同步写入 latest manifest：`knowledge/patch_manifests/rag_patch_apply_<国家>.json`。
  - manifest 记录 run_id、country、status、raw_patch_path、applied_patch_count、patch_ids、source_memory_ids 和 next_step。
- Runtime 页面：
  - `价值观与审核 RAG` 操作区新增 `应用已审补丁到raw` 按钮。
- Server：
  - 新增 `/apply_approved_rag_patch_markdown` action。
  - 成功后返回 raw patch 路径、manifest 路径和应用补丁数量。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_applies_approved_rag_patch_markdown_to_raw_with_manifest tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_apply_approved_rag_patch_markdown_action_writes_raw_and_manifest -q`：先因缺少 Agent apply 方法、页面入口和 server action 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_applies_approved_rag_patch_markdown_to_raw_with_manifest tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_apply_approved_rag_patch_markdown_action_writes_raw_and_manifest -q`：3 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：208 passed。
  - `PYTHONPATH=. pytest tests -q`：355 passed。

当前限制：

- 本版本只把已审补丁写入 raw 并生成 manifest；不会自动 rebuild processed 文档，也不会自动重建 Qdrant。运营仍需显式点击 `重建RAG知识库` 和 `重建并入库Qdrant`。
- 暂未提供 raw patch 的回滚动作；下一步应基于 `patch_manifests` 增加回滚到上一个 raw patch 状态或禁用指定 patch 的能力。

## v0.6.1 - Approved RAG Patch Markdown Export

日期：2026-07-04

阶段目标：

- 把已人工审核通过的 RAG 知识补丁从长期记忆导出为 Markdown patch，作为后续纳入 `knowledge/raw` 正式知识库版本治理的桥接层。
- 继续保持安全边界：导出动作只写 runtime 文件，不自动改写正式知识库，避免误把未复核内容写入生产 RAG 文档。

已完成：

- Agent：
  - 新增 `export_approved_rag_patch_markdown(country, output_path)`。
  - 仅导出 `long_term` memory 中 `memory_type=approved_rag_knowledge_patch` 且 `human_verified=True`、`status=active` 的补丁。
  - Markdown front matter 标记 `source_type: approved_rag_patch`、`review_status: approved`、`generated_from: long_term_memory`。
  - 每条补丁保留 patch_id、source_memory_id、memory_id、optimization_use、query、人工审核备注和 rule_text。
- Runtime 页面：
  - `价值观与审核 RAG` 操作区新增 `导出已审Markdown补丁` 按钮。
- Server：
  - 新增 `/export_approved_rag_patch_markdown` action。
  - 默认导出到 `runtime/approved_rag_patch_<国家>.md`。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_approved_rag_patch_memory_as_raw_markdown_patch tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_export_approved_rag_patch_markdown_action_writes_md -q`：先因缺少 Agent 导出方法、页面入口和 server action 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_approved_rag_patch_memory_as_raw_markdown_patch tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_export_approved_rag_patch_markdown_action_writes_md -q`：3 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：206 passed。
  - `PYTHONPATH=. pytest tests -q`：353 passed。

当前限制：

- 本版本只导出可审阅 Markdown patch，不自动 apply 到 `knowledge/raw`，也不自动触发 Qdrant 重建；后续还需要补正式 apply/review、manifest、回滚和知识库版本对比。

## v0.6.0 - Approved RAG Patch Memory

日期：2026-07-03

阶段目标：

- 完成 RAG 失败反馈闭环中的人工审核入库步骤：草案必须经人工审核通过后，才能进入可被 RAG 使用的长期记忆。
- 保持知识库安全边界：仍不自动改写 raw markdown，避免未审草案污染正式知识库。

已完成：

- Agent：
  - 新增 `approve_rag_knowledge_patch_draft(country, patch_id, human_note=...)`。
  - 根据 patch_id 查找草案，写入 `long_term` memory。
  - memory_type 为 `approved_rag_knowledge_patch`。
  - 写入时设置 `human_verified=True`，并保留 source_memory_id、expected_parent_id、query、rule_text、review_status。
- Runtime 页面：
  - `RAG知识补丁草案`表新增`审核通过草案`表单。
  - 人工备注会随表单一起写入长期记忆。
- Server：
  - 新增 `/approve_rag_knowledge_patch_draft` action。
  - 成功后保持 Runtime 页面，并返回新 memory_id。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_approves_rag_knowledge_patch_draft_into_long_term_memory tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_approve_rag_knowledge_patch_draft_action_writes_long_term_memory -q`：先因缺少审核方法、页面表单和 server action 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_approves_rag_knowledge_patch_draft_into_long_term_memory tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_approve_rag_knowledge_patch_draft_action_writes_long_term_memory -q`：3 passed。
  - `PYTHONPATH=. pytest tests/test_server.py::test_approve_rag_knowledge_patch_draft_action_writes_long_term_memory -q`：修正全局 APP 下 patch_id 不固定后的回归，1 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：204 passed。
  - `PYTHONPATH=. pytest tests -q`：351 passed。

当前限制：

- 本版本将审核通过的草案写入长期记忆并参与 RAG；后续如果需要正式知识库版本治理，还应增加 raw markdown patch apply/review 流程。

## v0.5.9 - RAG Knowledge Patch Drafts

日期：2026-07-03

阶段目标：

- 把 RAG 失败反馈进一步转成可人工审核的知识库补丁草案。
- 保持安全边界：草案不自动写入 raw/processed 知识库，避免未经确认的模型/检索错误污染 RAG。

已完成：

- Agent：
  - 新增 `rag_knowledge_patch_drafts(country)`。
  - 从 `rag_eval_failure_feedback` 队列生成 patch draft。
  - 每条草案包含 patch_id、source_type、expected_parent_id、source_memory_id、query、draft_text、review_status、optimization_use。
  - 新增 `export_rag_knowledge_patch_drafts(country, output_path)`，导出 JSONL。
- Runtime 页面：
  - 新增 `RAG知识补丁草案`卡片和明细表。
  - 展示草案数量、source type、expected parent、审核状态和草案内容。
  - RAG 操作区新增`导出知识补丁草案`按钮。
- Server：
  - 新增 `/export_rag_knowledge_patch_drafts` action。
  - 默认导出到 `runtime/rag_knowledge_patch_drafts_<国家>.jsonl`。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_builds_and_exports_rag_knowledge_patch_drafts tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_export_rag_knowledge_patch_drafts_action_writes_jsonl -q`：先因缺少草案方法、页面展示和 server action 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_builds_and_exports_rag_knowledge_patch_drafts tests/test_renderer.py::test_runtime_page_shows_rag_knowledge_patch_drafts tests/test_server.py::test_export_rag_knowledge_patch_drafts_action_writes_jsonl -q`：3 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：202 passed。
  - `PYTHONPATH=. pytest tests -q`：349 passed。

当前限制：

- 本版本只生成草案和导出文件；后续需要做人工审核通过后，才允许把草案转为 raw markdown 补丁或 long_term/facts memory。

## v0.5.8 - RAG Failure Feedback Export Queue

日期：2026-07-03

阶段目标：

- 把 `rag_eval_failure_feedback` 从 working memory 中的单条记录升级为可查看、可导出的优化队列。
- 让 RAG eval 失败样本能沉淀为 hard negative、知识库补充任务或 rerank 调优样本。

已完成：

- Agent：
  - 新增 `rag_eval_failure_feedback_summary(country)`。
  - 汇总 active working memory 中的 `rag_eval_failure_feedback`。
  - 新增 `export_rag_eval_failure_feedback(country, output_path)`，导出 JSONL。
  - 每条导出记录包含 query、expected parent、retrieved parents、note、memory_id、optimization_use。
- Runtime 页面：
  - 新增 `RAG失败反馈队列`卡片和明细表。
  - 展示待处理数量、query、expected parent、retrieved parents、用途和备注。
  - RAG 操作区新增`导出RAG失败反馈`按钮。
- Server：
  - 新增 `/export_rag_eval_failure_feedback` action。
  - 默认导出到 `runtime/rag_eval_failure_feedback_<国家>.jsonl`。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_summarizes_and_exports_rag_eval_failure_feedback tests/test_renderer.py::test_runtime_page_shows_rag_failure_feedback_queue tests/test_server.py::test_export_rag_eval_failure_feedback_action_writes_jsonl -q`：先因缺少 summary/export、页面队列和 server action 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_summarizes_and_exports_rag_eval_failure_feedback tests/test_renderer.py::test_runtime_page_shows_rag_failure_feedback_queue tests/test_server.py::test_export_rag_eval_failure_feedback_action_writes_jsonl -q`：3 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：199 passed。
  - `PYTHONPATH=. pytest tests -q`：346 passed。

当前限制：

- 本版本先导出 JSONL 队列；后续还需要把导出的 hard negative 自动合入评测集，或生成可审核的知识库补丁草案。

## v0.5.7 - RAG Eval Failure HITL Feedback

日期：2026-07-03

阶段目标：

- 把 RAG eval 失败 case 从“只读表格”推进到 HITL 反馈入口。
- 让运营可以把未命中 expected parent 的 case 记录为 working memory，后续用于知识库补充、hard negative 或 rerank 反馈。

已完成：

- Agent：
  - 新增 `record_rag_eval_failure_feedback()`。
  - 将失败 case 的 query、expected parent、retrieved parents、人工备注写入 `working` memory。
  - memory_type 为 `rag_eval_failure_feedback`，task_type 为 `rag_eval_case_review`。
- Runtime 页面：
  - `RAG Eval Case 证据` 表格中，失败 case 新增 `记录失败case` 表单。
  - 表单带上 query、expected parent、retrieved parents 和人工备注。
- Server：
  - 新增 `/record_rag_eval_failure_feedback` action。
  - 写入成功后保持在 Runtime 页面，并显示 memory_id。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_records_rag_eval_failure_feedback_as_working_memory tests/test_renderer.py::test_runtime_page_shows_rag_eval_case_evidence tests/test_server.py::test_record_rag_eval_failure_feedback_action_writes_working_memory -q`：先因缺少 Agent 方法、页面表单和 server action 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_records_rag_eval_failure_feedback_as_working_memory tests/test_renderer.py::test_runtime_page_shows_rag_eval_case_evidence tests/test_server.py::test_record_rag_eval_failure_feedback_action_writes_working_memory -q`：3 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：196 passed。
  - `PYTHONPATH=. pytest tests -q`：343 passed。

当前限制：

- 本版本先把失败 case 进入 working memory；后续还需要把这些反馈批量导出为 hard negative、知识库补充任务或 rerank 训练/调参样本。

## v0.5.6 - RAG Eval Case Evidence Review

日期：2026-07-03

阶段目标：

- 让 RAG `hit@5` 不再只是一个总分，而是能下钻到每条 query 的 expected citation、实际 retrieved parents、rank 和失败原因。
- 支撑后续 HITL 复盘：运营/面试官可以看到是哪条真实业务 case 没命中，而不是只看到整体通过率。

已完成：

- Agent：
  - 新增 `rag_eval_case_evidence(country)`。
  - 从 `value_audit_rag_eval_report()` 提取 case 明细。
  - 每条 case 标记 `PASS/FAIL`、expected parent、retrieved parents、rank 和失败原因。
  - 将 `rag_eval_case_evidence` 合入 Runtime RAG summary。
- Runtime 页面：
  - 新增 `RAG Eval Case 证据` 表格。
  - 展示 dataset、hit@5、failed/total、threshold。
  - 表格展示状态、Query、Expected Parent、Retrieved Parents、Rank、失败原因。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_eval_case_evidence_marks_failed_expected_citation tests/test_renderer.py::test_runtime_page_shows_rag_eval_case_evidence -q`：先因缺少 `rag_eval_case_evidence()` 和页面证据表失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_eval_case_evidence_marks_failed_expected_citation tests/test_renderer.py::test_runtime_page_shows_rag_eval_case_evidence -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_rag.py -q`：173 passed。
  - `PYTHONPATH=. pytest tests -q`：341 passed。

当前限制：

- 本版本先做 RAG eval case 的只读复盘；人工修正入口仍主要在 Harness/引用反馈链路中。
- 后续需要把失败 case 一键转成 hard negative、知识库补充任务或 rerank feedback。

## v0.5.5 - Real RAG Eval Dataset Surface

日期：2026-07-03

阶段目标：

- 让 RAG 评测从静态 smoke/file cases 继续升级为真实业务样本驱动的 Eval Dataset。
- 把真实样本数量、human_gold 覆盖、harness eval cases 和 hit@5 门槛展示到 Runtime 页面，支撑“工业级 RAG”面试叙事。

已完成：

- Agent：
  - 新增 `_rag_retrieval_cases(country)` 聚合入口。
  - 新增 `_harness_gold_rag_eval_cases(country)`，把 `human_gold + reviewed` 的真实拼图样本转成 RAG 检索评测 case。
  - `value_audit_rag_eval_report()`、`export_value_audit_rag_acceptance_report()`、`run_full_rag_industrial_acceptance()` 统一使用聚合后的 business cases。
  - 新增 `rag_eval_dataset_summary(country)`，统计真实样本、AI silver、manual grade、human_gold、file cases、harness cases、total cases、30-50 样本目标和 hit@5 阈值。
- Runtime 页面：
  - 新增 `真实 Eval Dataset` 卡片。
  - 展示 `real/human_gold/cases`，以及 `file cases/harness cases/ai_silver/manual_grade/target/hit@5 threshold/status`。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_eval_cases_include_human_gold_harness_samples tests/test_renderer.py::test_runtime_page_shows_real_rag_eval_dataset_summary -q`：先因缺少 `_rag_retrieval_cases` 和页面卡片失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_eval_cases_include_human_gold_harness_samples tests/test_renderer.py::test_runtime_page_shows_real_rag_eval_dataset_summary -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_value_audit_rag_eval_report_tracks_hit_at_five_threshold tests/test_agents.py::test_agent_loads_versioned_knowledge_documents_and_eval_cases tests/test_agents.py::test_agent_rag_eval_cases_include_human_gold_harness_samples tests/test_renderer.py::test_runtime_page_shows_real_rag_eval_dataset_summary -q`：4 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_rag.py -q`：171 passed。
  - `PYTHONPATH=. pytest tests -q`：339 passed。

当前限制：

- 只有 `human_gold + reviewed` 的真实样本会进入 RAG eval cases；`ai_silver` 仍需要人工抽查确认后才能作为强评测证据。
- 本版本先做评测资产统计和 case 接入；后续还需要导出每条真实 case 的 expected citation、失败样本和人工修正链路。

## v0.5.4 - Runtime RAG Preflight Evidence

日期：2026-07-03

阶段目标：

- 把“一键RAG全链路验收”的 preflight 结果从同步消息/JSON 报告提升到 Runtime 页面可见证据。
- 让运营和面试官能直接看到 Qwen embedding、Qdrant、BGE rerank 是否 ready，以及真实 hit@5、qdrant_hit、remote/fallback 统计。

已完成：

- Agent：
  - 新增 `latest_rag_acceptance_summary(country)`。
  - 从 `runtime/rag_acceptance_reports/rag_acceptance_full_summary_<国家>.json` 读取最近一次全链路验收结果。
  - 将 status、failure_stage、error、preflight、hit@5、mrr@5、qdrant_vector_hits、runtime_stats 合入 `value_audit_rag_summary()`。
- Runtime 页面：
  - 新增 `RAG Preflight` 卡片。
  - 未运行一键验收时显示明确空状态。
  - 已运行时展示 `mode/status/stage`、embedding/qdrant/rerank ready 状态、provider、错误信息、full hit@5、mrr@5、qdrant_hit、embedding/rerank remote/fallback。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_renderer.py::test_runtime_page_shows_latest_rag_preflight_summary -q`：先因页面缺少 `RAG Preflight` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_renderer.py::test_runtime_page_shows_latest_rag_preflight_summary -q`：1 passed。
  - `PYTHONPATH=. pytest tests/test_renderer.py::test_runtime_page_shows_rag_feedback_summary tests/test_renderer.py::test_runtime_page_shows_latest_rag_preflight_summary -q`：2 passed。

当前限制：

- 本版本只展示最近一次 summary 文件中的证据；如果从未点击“一键RAG全链路验收”，页面会显示未运行状态。
- BGE/Qdrant/Qwen 是否真实 ready 仍以 live preflight 和外部服务可用性为准。

## v0.5.3 - Fast/Live RAG Preflight Modes

日期：2026-07-03

阶段目标：

- 解决 v0.5.2 暴露出的工程问题：preflight 在真实 `.env` 下可能触发网络/API 检查，导致测试和普通调用变慢。
- 保持真实验收能力，同时让测试默认轻量、可控、快速。

已完成：

- Agent：
  - `run_full_rag_industrial_acceptance()` 新增 `preflight_mode` 参数。
  - 默认 `preflight_mode="fast"`，只读取 provider 配置，不触发 live healthcheck / embedding smoke。
  - `preflight_mode="live"` 时才调用真实 provider `healthcheck()` 或 embedding smoke。
  - `preflight.mode` 写入结果和 summary。
- Server：
  - Runtime 页面的一键验收仍显式传 `preflight_mode="live"`，保留真实检查语义。
- 测试隔离：
  - 新增测试确保默认 fast preflight 不调用 provider healthcheck。
  - server 测试断言页面动作会传 `preflight_mode="live"`。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_full_rag_acceptance_defaults_to_fast_preflight_without_live_healthchecks tests/test_agents.py::test_agent_runs_full_rag_industrial_acceptance_with_qdrant_and_bge -q`：先因缺少 `preflight_mode` 和 `preflight.mode` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_full_rag_acceptance_defaults_to_fast_preflight_without_live_healthchecks tests/test_agents.py::test_agent_runs_full_rag_industrial_acceptance_with_qdrant_and_bge tests/test_server.py::test_run_full_rag_acceptance_action_reports_reindex_and_hit_rate tests/test_server.py::test_run_full_rag_acceptance_action_reports_failure_stage -q`：4 passed。

当前限制：

- 页面一键验收仍会做 live preflight；这是符合业务语义的真实检查，但如果外部服务慢，页面动作也会相应变慢。

## v0.5.2 - RAG Preflight Checks

日期：2026-07-03

阶段目标：

- 在“一键RAG全链路验收”里加入前置检查，先确认 Qwen embedding、Qdrant、BGE rerank 是否就绪。
- 让报告和页面消息能区分“服务未就绪”和“检索质量未达标”。

已完成：

- Agent：
  - `run_full_rag_industrial_acceptance()` 新增 `preflight`。
  - `preflight.embedding` 优先调用 provider `healthcheck()`；无 healthcheck 时用 `query_vector("寿司价值观")` 做 smoke。
  - `preflight.qdrant` 调用 Qdrant store `healthcheck()`。
  - `preflight.rerank` 调用 BGE/DashScope reranker `healthcheck()`。
  - 成功和失败 summary 都会保留 `preflight`。
- Server：
  - `/run_full_rag_acceptance` 同步消息新增 preflight 摘要，如 `preflight=embedding:True,qdrant:True,rerank:True`。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_runs_full_rag_industrial_acceptance_with_qdrant_and_bge -q`：先因缺少 `preflight` 失败。
  - `PYTHONPATH=. pytest tests/test_server.py::test_run_full_rag_acceptance_action_reports_reindex_and_hit_rate -q`：先因同步消息缺少 preflight 摘要失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_server.py::test_run_full_rag_acceptance_action_reports_reindex_and_hit_rate tests/test_server.py::test_run_full_rag_acceptance_action_reports_failure_stage tests/test_agents.py::test_agent_runs_full_rag_industrial_acceptance_with_qdrant_and_bge tests/test_agents.py::test_agent_full_rag_acceptance_returns_diagnostics_when_qdrant_fails -q`：4 passed。

当前限制：

- preflight 会尽量调用真实 provider 的 healthcheck；是否产生远程调用取决于 provider 实现和 `.env` 配置。
- 后续可继续把 preflight 结果渲染成页面卡片，而不仅显示在同步消息和 JSON 报告中。

## v0.5.1 - Full RAG Failure Diagnostics

日期：2026-07-03

阶段目标：

- 让“一键RAG全链路验收”失败时不再黑盒：明确失败阶段、错误信息和组件级诊断。
- 帮运营/面试官快速判断问题是在 Qwen embedding、Qdrant、BGE rerank、还是 hit@5 阈值。

已完成：

- Agent：
  - `run_full_rag_industrial_acceptance()` 增加 `qdrant_reindex` 和 `acceptance_report` 两个阶段的异常捕获。
  - 失败时返回 `status=failed`、`failure_stage`、`error`、`diagnostics`。
  - 失败时仍写出 `rag_acceptance_full_summary_<国家>.json`，保留排查证据。
- 诊断结构：
  - `embedding`：provider、remote_calls、fallbacks。
  - `qdrant`：upserted_points、qdrant_vector_hits、错误信息。
  - `rerank`：provider、remote_calls、fallbacks。
  - `hit_rate`：hit@5、threshold。
- Server：
  - `/run_full_rag_acceptance` 同步消息新增 `stage` 和 `error`。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_full_rag_acceptance_returns_diagnostics_when_qdrant_fails tests/test_server.py::test_run_full_rag_acceptance_action_reports_failure_stage -q`：先因 Qdrant 异常直接抛出、server 不显示 stage 失败。
- 定向验证：
  - 上述 2 项测试：2 passed。
  - 成功路径回归：`PYTHONPATH=. pytest tests/test_agents.py::test_agent_runs_full_rag_industrial_acceptance_with_qdrant_and_bge tests/test_server.py::test_run_full_rag_acceptance_action_reports_reindex_and_hit_rate tests/test_renderer.py::test_runtime_page_shows_rag_feedback_summary -q`：3 passed。

当前限制：

- 本版本优先覆盖 Qdrant/reindex 阶段和报告阶段的失败诊断；后续还可以把真实 BGE healthcheck 与 Qwen embedding smoke 合入一键验收前置检查。

## v0.5.0 - Full RAG Industrial Acceptance

日期：2026-07-03

阶段目标：

- 把前几版拆开的 RAG 工程能力串成一个显式动作：重建知识库、Qwen3-Embedding 向量化、Qdrant 入库、Qdrant 检索、BM25 多路召回、BGE rerank、导出 hit@5 验收报告。
- Runtime 页面提供“一键RAG全链路验收”入口，便于运营演示和面试讲解。

已完成：

- Agent：
  - 新增 `PuzzleOpsAgent.run_full_rag_industrial_acceptance()`。
  - 流程会先调用 `reindex_rag_qdrant_from_raw()` 写入 Qdrant。
  - 再用同一套 embedding provider、rerank provider、Qdrant store 运行 `export_rag_acceptance_report()`。
  - 输出 `rag_acceptance_full_<国家>.json` 和 `rag_acceptance_full_summary_<国家>.json`。
- Server / Runtime：
  - 新增 `/run_full_rag_acceptance` 动作。
  - Runtime 的“价值观与审核 RAG”区域新增“一键RAG全链路验收”按钮。
  - 同步消息展示 points、vector_size、hit@5、mrr@5、qdrant_hit、embedding/rerank remote calls 和 report path。
- 验收证据：
  - 结果包含 reindex 状态、Qdrant 入库点数、hit@5、observed qdrant hit、runtime stats。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_runs_full_rag_industrial_acceptance_with_qdrant_and_bge -q`：先因缺少 Agent 方法失败。
  - `PYTHONPATH=. pytest tests/test_server.py::test_run_full_rag_acceptance_action_reports_reindex_and_hit_rate -q`：先因 server 路由不存在失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_renderer.py::test_runtime_page_shows_rag_feedback_summary tests/test_agents.py::test_agent_runs_full_rag_industrial_acceptance_with_qdrant_and_bge tests/test_server.py::test_run_full_rag_acceptance_action_reports_reindex_and_hit_rate -q`：3 passed。

当前限制：

- 真实全链路依赖 `.env` 中 Qwen/DashScope key、Qdrant endpoint 和 BGE rerank endpoint 均可用。
- 如果 Qdrant 或 BGE 服务不可用，报告会暴露失败或 fallback 迹象；这正是该版本希望显性化的工程风险。

## v0.4.9 - Observed RAG Runtime Evidence

日期：2026-07-03

阶段目标：

- 继续补强工业级 RAG 的可观测性：报告不能只写“配置了 Qdrant/BGE”，还要记录当次验收实际观察到的检索路线和 provider runtime stats。
- 给 BGE reranker 增加 healthcheck，便于确认 BGE endpoint、模型名和 probe score。

已完成：

- BGE reranker healthcheck：
  - `DashScopeRerankProvider.healthcheck()` 会发起一次轻量 probe，并返回 `configured`、`ready`、`model`、`endpoint`、`probe_score`。
  - `BGERerankProvider.healthcheck()` 标记 provider 为 `bge`，复用 open-rerank transport。
- RAG 验收报告增强：
  - `export_rag_acceptance_report()` 新增 `observed_retrieval`。
  - 记录当次 trace 观察到的 `embedding_provider`、`vector_store_provider`、`rerank_provider`。
  - 记录 `bm25_candidate_count`、`vector_candidate_count`、`qdrant_vector_hits`。
  - 新增 `runtime_stats`，聚合 embedding/rerank remote calls、cache hits、fallbacks。
- Agent 导出：
  - `PuzzleOpsAgent.export_value_audit_rag_acceptance_report()` 导出的报告现在包含 observed/runtime 字段。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_bge_rerank_provider_healthcheck_records_probe_score tests/test_rag.py::test_export_rag_acceptance_report_records_observed_runtime_routes_and_stats -q`：先因缺少 `healthcheck` 与 `observed_retrieval` 失败。
- 定向验证：
  - 上述 2 项测试：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_value_audit_rag_acceptance_report -q`：1 passed。

当前限制：

- BGE healthcheck 能验证 endpoint 形式和 probe 调用；真实可用性仍取决于你本机或云端是否部署了兼容 `/v1/rerank` 的服务。
- Qdrant 是否真正命中会以 `observed_retrieval.qdrant_vector_hits` 标记，避免只看配置误判为已经走向量库。

## v0.4.8 - RAG Acceptance Report

日期：2026-07-02

阶段目标：

- 把“工业级 RAG”从页面说明推进到可导出的验收证据：真实 provider 配置、Qdrant/BM25/rerank 路由、hit@5 阈值和 trace 样本写入报告。
- 让阿里/Qwen 账号配置更稳：可复用现有 `QWEN_API_KEY`，避免空 `RAG_API_KEY` 阻断真实 Qwen3-Embedding 调用。

已完成：

- Provider 配置：
  - `RagProviderConfig` 支持按 `RAG_API_KEY`、`DASHSCOPE_API_KEY`、`QWEN_API_KEY` 顺序取第一个非空 key。
  - DashScope embedding 默认模型继续使用 `text-embedding-v4`，并在状态里标记为 `Qwen3-Embedding` 家族。
  - BGE reranker 默认保持 `BAAI/bge-reranker-v2-m3`。
- RAG 验收报告：
  - 新增 `export_rag_acceptance_report()`。
  - 报告包含 `hit@5`、`mrr@5`、阈值通过状态、embedding/rerank 模型、Qdrant 配置、召回路线和 trace samples。
  - 报告明确记录 `retrieval_routes`：query rewrite、BM25、vector、rerank、parent-child、citation grounding prompt。
- Agent / Server：
  - 新增 `PuzzleOpsAgent.export_value_audit_rag_acceptance_report()`。
  - 新增 `/export_rag_acceptance_report` 动作。
  - Runtime 的“价值观与审核 RAG”区域新增“导出RAG验收报告”按钮。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_rag.py::test_dashscope_config_reuses_qwen_api_key_for_qwen3_embedding -q`：先因空 `RAG_API_KEY` 无法 fallback 到 `QWEN_API_KEY` 失败。
  - `PYTHONPATH=. pytest tests/test_rag.py::test_export_rag_acceptance_report_writes_hit_at_five_models_routes_and_traces -q`：先因缺少 `export_rag_acceptance_report` 失败。
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_value_audit_rag_acceptance_report -q`：先因 Agent 缺少导出方法失败。
  - `PYTHONPATH=. pytest tests/test_server.py::test_export_rag_acceptance_report_action_writes_report -q`：先因 server 路由不存在失败。
- 定向验证：
  - 上述 4 项测试：4 passed。
- 真实配置 smoke：
  - 读取本地 `.env` 后，RAG 配置为 `dashscope / text-embedding-v4` + `bge / BAAI/bge-reranker-v2-m3`。
  - 单次 `寿司价值观` embedding 调用成功：vector_dim=1024，remote_calls=1，fallbacks=0。

当前限制：

- 本版本生成“可审计验收报告”，不自动执行外部 `promptfoo eval`。
- 真实远程 embedding/rerank 调用仍受 `.env`、额度、网络和 BGE endpoint 可用性影响；未开启 `RAG_ENABLE_REMOTE_CALLS` 时会保持本地 fallback。

## v0.4.7 - Promptfoo YAML Export

日期：2026-07-02

阶段目标：

- 在 v0.4.5 的外部评测 JSON 导出基础上，补齐更贴近 Promptfoo CLI 使用习惯的 YAML 配置文件。
- 保持 Python 标准库实现，不引入 PyYAML 等新依赖。

已完成：

- Promptfoo YAML：
  - `PromptfooExporter.export_yaml(run)` 输出 YAML 文本。
  - 新增轻量 `to_simple_yaml()` 序列化器，支持 dict/list/tuple/str/number/bool/null。
  - YAML key 在安全情况下使用裸 key，如 `providers:`、`tests:`、`rag_trace_artifacts:`。
- Agent 导出：
  - `export_harness_external_eval_artifacts()` 新增 `promptfoo_yaml`。
  - 写出 `promptfoo_harness_<country>.yaml`。
- Server：
  - `/export_harness_external_eval` 同步消息新增 Promptfoo YAML 文件路径。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_exports_harness_external_eval_artifacts tests/test_server.py::test_export_harness_external_eval_action_writes_eval_tool_files -q`：先因缺少 `promptfoo_yaml` 与 YAML 文件失败。
- 定向验证：
  - 上述 2 项测试：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_server.py tests/test_harness.py tests/test_external_adapters.py -q`：185 passed。

当前限制：

- YAML 序列化器是面向本项目 Promptfoo config 的轻量实现，不是通用 YAML 全功能库。
- 还未直接调用 `promptfoo eval`，只生成可交给 CLI 的配置文件。

## v0.4.6 - Inline RAG Trace Replay

日期：2026-07-02

阶段目标：

- 让 RAG trace / prompt 不只作为 JSON 文件路径存在，而是在 Runtime 与 Harness Dashboard 页面内可直接展开查看。
- 提升面试演示与运营复盘效率：可在页面内看到 prompt、引用上下文、精排命中摘要。

已完成：

- Harness artifact 增强：
  - `rag_trace_artifacts` 新增 `context`、`prompt`、`retrieval_trace`。
  - `Harness RAG Artifacts` 表格新增详情折叠区。
- Runtime RAG trace 回放：
  - `最近 RAG Trace` 表格新增详情列。
  - 每条 trace 可展开 `Prompt 回放详情`。
  - 展示 `引用上下文`、完整 prompt 摘要、`检索命中详情`。
- 页面体验：
  - 新增 `.trace-replay` 样式，限制 prompt/context 的高度和宽度，避免撑爆页面。
  - 使用 `<details>` 原生折叠，默认不挤占页面空间。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_harness_run_links_rag_trace_artifacts_for_replay tests/test_renderer.py::test_runtime_page_shows_rag_feedback_summary tests/test_renderer.py::test_eval_page_shows_case_evidence_trace_and_failure_categories -q`：先因 artifact 缺少 `prompt`、页面缺少 prompt/context/final hits 详情失败。
- 定向验证：
  - 上述 3 项测试：3 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_harness.py tests/test_server.py -q`：206 passed。

当前限制：

- 页面内展示的是 trace 摘要与截断 prompt，不是完整 JSON 编辑器。
- 仍未直接调用 Phoenix/Promptfoo CLI；当前是本地导出 + 页面回放。

## v0.4.5 - External Eval Exporters for Harness

日期：2026-07-02

阶段目标：

- 把内置轻量 Harness 的 run、case、RAG trace artifacts 导出为 Phoenix / Promptfoo / DeepEval 可消费的本地文件。
- 保持“不强依赖外部服务”的双层设计：本地 Harness 稳定运行，同时预留外部 Agent Eval 工具链。

已完成：

- Adapter payload 增强：
  - `PhoenixExporter` 新增 run 级 `rag_trace_artifacts`。
  - Phoenix trace span 新增 case 级 `rag_trace_id` 与 `rag_trace_path`。
  - `PromptfooExporter` 新增 `metadata.rag_trace_artifacts`。
- Agent 导出：
  - 新增 `export_harness_external_eval_artifacts(country, output_dir)`。
  - 写出 `phoenix_harness_<country>.json`、`promptfoo_harness_<country>.json`、`deepeval_harness_<country>.json`。
- Eval 页面与 server action：
  - 新增 `/export_harness_external_eval`。
  - Harness Dashboard 新增 `导出外部评测文件` 按钮。
  - 同步消息返回 Phoenix / Promptfoo / DeepEval 文件路径。
- 稳定性修复：
  - `harness_display_run()` 会检查最新保存 run 的 sample_id 是否匹配当前数据集。
  - 切换 `PUZZLEOPS_HARNESS_DATASET` 后不会误展示旧 run 的失败样本。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_harness.py::test_harness_external_adapters_include_rag_trace_artifacts tests/test_agents.py::test_agent_exports_harness_external_eval_artifacts tests/test_server.py::test_export_harness_external_eval_action_writes_eval_tool_files tests/test_renderer.py::test_eval_page_has_harness_override_export_action -q`：先因 adapter 缺少 RAG artifacts、agent/server/页面导出入口缺失失败。
- 定向验证：
  - 上述 4 项测试：4 passed。
  - `PYTHONPATH=. pytest tests/test_renderer.py::test_eval_failure_samples_show_image_gold_label_and_hitl_form tests/test_harness.py::test_harness_external_adapters_include_rag_trace_artifacts tests/test_agents.py::test_agent_exports_harness_external_eval_artifacts tests/test_server.py::test_export_harness_external_eval_action_writes_eval_tool_files tests/test_renderer.py::test_eval_page_has_harness_override_export_action -q`：5 passed。
  - `PYTHONPATH=. pytest tests/test_harness.py tests/test_agents.py tests/test_server.py tests/test_renderer.py tests/test_external_adapters.py -q`：228 passed。

当前限制：

- 当前导出的是本地 JSON 文件，不会直接调用 Phoenix / Promptfoo / DeepEval 服务或 CLI。
- Promptfoo 使用 JSON config 形态，后续如需直接给 CLI 使用，可再增加 YAML 输出。

## v0.4.4 - Harness RAG Trace Artifacts

日期：2026-07-02

阶段目标：

- 把 v0.4.3 的本地 RAG trace 文件纳入 Harness run artifacts。
- 让一次 Harness 评测 run 可以回放它使用过的 RAG query、prompt、引用和检索 trace，强化“可评测、可回放、可复盘”的 Agent Harness 主线。

已完成：

- Harness run artifacts：
  - `HarnessRun` 新增 `rag_trace_artifacts`。
  - `AgentHarness._prepare_run_rag_evidence()` 在准备每个国家的 RAG evidence 后，记录最近一次 `rag_trace_*.json` 的 trace id、query、引用和文件路径。
  - `value_match_eval` 的 `evidence_trace` 新增 `rag_trace_id` 与 `rag_trace_path`。
- 持久化兼容：
  - `PuzzleRepository.save_harness_run()` 自动保存新增字段。
  - `PuzzleRepository.harness_runs()` 读取旧 run 时保持兼容，读取新 run 时规整 `citations` 为 tuple。
- Eval 页面：
  - `Case 证据链` 新增 `RAG Trace` 列。
  - 新增 `Harness RAG Artifacts` 表格，展示国家、trace id、query、引用和 trace JSON 文件路径。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_harness_run_links_rag_trace_artifacts_for_replay tests/test_renderer.py::test_eval_page_shows_case_evidence_trace_and_failure_categories -q`：先因 `HarnessRun` 缺少 `rag_trace_artifacts`、页面缺少 RAG Trace/Harness Artifacts 展示失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_harness_run_links_rag_trace_artifacts_for_replay tests/test_renderer.py::test_eval_page_shows_case_evidence_trace_and_failure_categories -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_harness.py -q`：142 passed。

当前限制：

- Artifact 已进入 Harness run，但页面还只是展示文件路径，没有在页面内展开完整 prompt/request/response。
- 还未导出 Phoenix/Promptfoo/DeepEval 兼容格式。

## v0.4.3 - RAG Online Trace Replay

日期：2026-07-02

阶段目标：

- 继续补齐工业级 RAG 在线阶段：每次价值观/审核 RAG 回答都能落盘为可回放 trace。
- 让 Runtime 页面不只展示“当前一次”的 trace，还能看到最近 trace 文件，便于复盘 prompt、引用和召回路径。

已完成：

- RAG trace 持久化：
  - `value_audit_rag_answer()` 每次生成答案后写入 `runtime/rag_traces/<country>/rag_trace_*.json`。
  - trace JSON 包含原始 query、改写 query、context、citations、完整 prompt、retrieval_trace、runtime_stats、embedding/rerank/vector store 配置。
  - 当审核 query 触发额外 audit policy 补召回时，持久化 trace 会同步最终命中列表。
  - 每个国家默认保留最近 30 份 trace，避免 runtime 目录无限增长。
- 可回放入口：
  - 新增 `recent_rag_traces(country)` 读取最近 trace 文件。
  - Runtime 页面新增 `最近 RAG Trace` 表格，展示 trace id、query、引用和可回放 prompt 文件路径。
- 页面可观测性：
  - `价值观与审核 RAG` 现在同时覆盖离线建库、在线召回、精排明细、最近 trace 文件。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_persists_value_audit_rag_trace_for_replay tests/test_renderer.py::test_runtime_page_shows_rag_feedback_summary -q`：先因缺少 `recent_rag_traces()` 和页面最近 trace 展示失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py::test_agent_persists_value_audit_rag_trace_for_replay tests/test_renderer.py::test_runtime_page_shows_rag_feedback_summary -q`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_rag.py -q`：158 passed。

当前限制：

- trace 已持久化为本地 JSON，但还没有提供页面内直接展开完整 prompt/response 的详情页。
- trace 目前跟 runtime 临时目录绑定；如果要作为长期实验资产，后续应导出到 `knowledge/runs` 或 Harness run artifacts。
- 还未接 Phoenix/Promptfoo exporter，只是保留了本地可回放数据形态。

## v0.4.2 - Runtime Qdrant Restore Control 与 RAG Trace

日期：2026-07-02

阶段目标：

- 把 v0.4.1 的 point record restore 从底层能力推进到页面可控能力。
- 强化工业级 RAG 在线阶段可观测性：运营和面试官能看到 BM25、向量召回、精排最终命中，而不是只看到摘要指标。

已完成：

- Runtime 真实 restore 开关：
  - `/rollback_qdrant_manifest` 表单新增 `真实恢复 Qdrant points` 确认项。
  - 默认仍只回滚 latest manifest 指针，避免误写 Qdrant。
  - 只有用户勾选确认，且 `RAG_VECTOR_STORE_PROVIDER=qdrant`、`QDRANT_URL`、`QDRANT_COLLECTION` ready 时，server 才注入真实 `QdrantVectorStore`。
  - 同步消息展示 `restore=...` 与 `restored_points=...`。
- RAG trace 展示：
  - Runtime 页面新增 `RAG 检索 Trace` 区块。
  - 展示检索参数、BM25 召回候选、向量召回候选、精确规则候选。
  - 展示精排最终命中的 chunk、父文档、来源、BM25/vector/rerank 分数和原因。
- 页面体验：
  - 新增 trace-grid 响应式样式，避免 trace 文本撑爆页面。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_server.py::test_qdrant_manifest_rollback_action_can_restore_points_when_confirmed tests/test_renderer.py::test_runtime_page_shows_rag_feedback_summary -q`：先因 server 未传 `vector_store`、页面缺少 restore 确认和 trace 明细失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_server.py::test_qdrant_manifest_rollback_action_sets_latest_run tests/test_server.py::test_qdrant_manifest_rollback_action_can_restore_points_when_confirmed tests/test_renderer.py::test_runtime_page_shows_rag_feedback_summary -q`：3 passed。
  - `PYTHONPATH=. pytest tests/test_server.py tests/test_renderer.py tests/test_agents.py tests/test_rag.py -q`：218 passed。

当前限制：

- 页面真实 restore 仍依赖 Qdrant 配置 ready；未配置时会拒绝恢复 points 并提示配置状态。
- Qdrant snapshot/alias 级别的 collection 回滚尚未实现，目前是基于 manifest 中 `point_records` 的 upsert restore。
- trace 已展示召回与精排细节，但还未把每次线上价值观判断的完整 prompt/request/response 持久化成可回放 trace 文件。

## v0.4.1 - Qdrant Point Record Snapshot Restore

日期：2026-07-02

阶段目标：

- 继续补齐 Qdrant 数据层回滚：manifest 不只记录 point ids，还记录完整 point records，使旧版本 points 可以重新 upsert。
- 让 restore 从“边界说明”推进到“有 point_records 时可真实写回 Qdrant”。

已完成：

- Reindex manifest 增强：
  - `reindex_rag_qdrant_from_raw()` 在 manifest 中新增 `point_records`。
  - 每条 point record 包含 `id`、`vector`、`payload`。
  - 保留原 `point_ids` 字段用于审计和兼容。
- Qdrant restore：
  - `QdrantVectorStore.restore_points(point_ids, point_records=...)` 支持用完整 records 调用 `upsert()`。
  - 没有 point_records 时仍返回 `manifest_pointer_only`，不伪造数据层恢复。
- Agent rollback：
  - `rollback_qdrant_manifest(..., vector_store=...)` 会从历史 manifest 读取 `point_records`。
  - 如果注入 vector store，则把 `point_ids` 与 `point_records` 一并传给 `restore_points()`。
  - 返回 `restore_status` 供页面消息展示。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "reindexes_raw_rag_knowledge_into_qdrant or rolls_back_qdrant_latest_manifest"`：先因 manifest 缺少 `point_records`、restore 未传 records 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "reindexes_raw_rag_knowledge_into_qdrant or rolls_back_qdrant_latest_manifest"`：2 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py -q -k "restore_points_upserts"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_server.py tests/test_renderer.py -q`：217 passed。
  - `PYTHONPATH=. pytest tests -q`：319 passed。

当前限制：

- Runtime 的 `/rollback_qdrant_manifest` 仍未注入真实 Qdrant store，因此页面 action 默认只切 latest manifest 指针并显示 `skipped_no_vector_store`。
- 要让页面一键真实 restore，需要让 server action 在 Qdrant 配置 ready 且用户确认时注入真实 `QdrantVectorStore`。
- 保存完整 vectors 会增加 manifest 文件体积；后续可增加压缩或只保存最近 N 个完整 snapshot。

## v0.4.0 - Qdrant Point IDs 与 Restore 边界

日期：2026-07-02

阶段目标：

- 继续补齐工业级 RAG 的数据层回滚基础：manifest 不只记录 run 状态，也记录本次写入的 Qdrant point ids。
- 让 rollback 明确区分“latest 指针回滚”和“point-level restore 是否执行”，避免运营误以为 Qdrant collection 内容已经完全回退。

已完成：

- Reindex manifest 增强：
  - `reindex_rag_qdrant_from_raw()` 在 manifest 中记录 `point_ids`。
  - `point_ids` 来自 `prepare_qdrant_points()` 生成的稳定 point id。
- Rollback restore 边界：
  - `rollback_qdrant_manifest(country, run_id, vector_store=...)` 支持注入 vector store。
  - 如果传入 vector store，会调用 `restore_points(point_ids)`。
  - 默认 `QdrantVectorStore.restore_points()` 返回 `manifest_pointer_only`，明确说明当前没有保存完整向量/snapshot，不能伪造真实 point 级恢复。
- Server 可观测：
  - `/rollback_qdrant_manifest` 消息新增 `restore=...`。
  - 页面回滚后能看到是 manifest 指针回滚，还是有外部 store 执行了 restore。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "reindexes_raw_rag_knowledge_into_qdrant or rolls_back_qdrant_latest_manifest"`：先因 manifest 缺少 `point_ids`、rollback 不接受 `vector_store` 失败。
  - `PYTHONPATH=. pytest tests/test_server.py -q -k "qdrant_manifest_rollback_action"`：先因 server 消息缺少 `restore=...` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "reindexes_raw_rag_knowledge_into_qdrant or rolls_back_qdrant_latest_manifest"`：2 passed。
  - `PYTHONPATH=. pytest tests/test_server.py -q -k "qdrant_manifest_rollback_action"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_server.py tests/test_renderer.py -q`：216 passed。
  - `PYTHONPATH=. pytest tests -q`：318 passed。

当前限制：

- 当前默认 restore 仍是边界能力：没有保存每个 point 的完整 vector/payload 快照，也没有调用 Qdrant snapshot/alias 机制恢复 collection。
- 下一步如果要做到真正数据层回滚，需要在 manifest 中保存 point records 或建立 snapshot/collection alias 流程。
- 这版刻意不伪造“真实恢复成功”，而是把 point ids 和 restore 状态清楚暴露。

## v0.3.99 - Qdrant Manifest Rollback

日期：2026-07-02

阶段目标：

- 继续补齐工业级 RAG 的可回放与回滚能力：不仅能保留历史 Qdrant reindex run，还能选择某个历史 run 重新设为 latest。
- 让 Runtime 页面提供受控回滚入口，便于演示“版本化知识库 + 入库状态回退”。

已完成：

- Agent rollback：
  - 新增 `rollback_qdrant_manifest(country, run_id)`。
  - 从 `knowledge/indices/runs/qdrant_reindex_{country}_{run_id}.json` 读取历史 manifest。
  - 校验 manifest 国家与当前国家一致。
  - 将目标历史 run 写回 `knowledge/indices/qdrant_reindex_{country}.json` latest 指针。
  - 返回 `run_id`、`vector_size`、`upserted_points`、source/latest manifest path。
- Server action：
  - 新增 `/rollback_qdrant_manifest`。
  - 成功后提示 `run_id`、`vector_size`、`points`。
  - 失败时明确返回回滚失败原因。
- Runtime 页面：
  - RAG 操作区新增 `run_id` 输入框和“回滚Qdrant Run”按钮。
  - 继续展示 latest `run_id` 与历史 `runs` 数量。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "rolls_back_qdrant_latest_manifest"`：先因缺少 `rollback_qdrant_manifest` 失败。
  - `PYTHONPATH=. pytest tests/test_server.py -q -k "qdrant_manifest_rollback_action"`：先因缺少 `/rollback_qdrant_manifest` action 失败。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "runtime_page_shows_rag_feedback_summary"`：先因页面缺少 rollback form 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "rolls_back_qdrant_latest_manifest"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_server.py -q -k "qdrant_manifest_rollback_action"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "runtime_page_shows_rag_feedback_summary"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_server.py tests/test_renderer.py -q`：216 passed。
  - `PYTHONPATH=. pytest tests -q`：318 passed。

当前限制：

- 回滚目前只切换 latest manifest 指针，不自动把 Qdrant collection 内容恢复到旧 point 集合。
- 真正的数据层回滚需要在 manifest 中保存 point ids 或 collection alias/snapshot 信息，并配套 reapply/restore 流程。
- UI 当前通过手动输入 `run_id` 回滚，后续可以把最近 runs 做成可点击表格按钮。

## v0.3.98 - Qdrant Manifest 多版本与 Latest 指针

日期：2026-07-02

阶段目标：

- 继续补齐工业级 RAG 的可回放能力：Qdrant reindex manifest 不再只覆盖最新文件，而是按 `run_id` 多版本保留。
- 保持旧页面/旧逻辑兼容：仍保留 `qdrant_reindex_{country}.json` 作为 latest 指针。

已完成：

- Reindex manifest 多版本：
  - 每次 `reindex_rag_qdrant_from_raw()` 生成独立 `run_id`。
  - 历史 manifest 写入 `knowledge/indices/runs/qdrant_reindex_{country}_{run_id}.json`。
  - latest 指针继续写入 `knowledge/indices/qdrant_reindex_{country}.json`。
  - reindex result 新增 `run_id` 与 `latest_manifest_path`。
- Smoke 结果回写历史 run：
  - `run_qdrant_smoke_diagnostic()` 读取 latest manifest 的 `run_id`。
  - smoke 结果同时写回 latest manifest 和对应历史 run manifest。
- Runtime 可观测：
  - `value_audit_rag_summary()` 新增 `qdrant_manifest_run_id`、`qdrant_manifest_history_count`、`qdrant_manifest_recent_runs`。
  - Runtime “版本化知识库”卡片展示 latest `run_id` 和 `runs` 数量。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "reindexes_raw_rag_knowledge_into_qdrant"`：先因 reindex result 缺少 `run_id` 失败。
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "qdrant_smoke_diagnostic"`：先因 smoke 未写回历史 run manifest 失败。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "runtime_page_shows_rag_feedback_summary"`：先因页面缺少 `runs=0` 展示失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "reindexes_raw_rag_knowledge_into_qdrant"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "qdrant_smoke_diagnostic"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "runtime_page_shows_rag_feedback_summary"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_server.py tests/test_renderer.py -q`：214 passed。
  - `PYTHONPATH=. pytest tests -q`：316 passed。

当前限制：

- 当前实现保留多版本 manifest，但还没有提供 UI 选择旧 run 回滚。
- latest 指针是文件覆盖写入；如果未来多人并发 reindex，需要增加文件锁或数据库事务。
- 历史 manifest 暂不自动清理，后续可增加保留策略，例如保留最近 50 次或最近 30 天。

## v0.3.97 - Qdrant Smoke Diagnostics

日期：2026-07-02

阶段目标：

- 继续补齐工业级 RAG 的线上诊断能力：不仅校验 Qdrant collection 配置，还要能证明真实写入、检索、清理链路可用。
- 让运营后台能一键跑 Qdrant smoke，不需要到终端手动排查。

已完成：

- Qdrant adapter smoke：
  - 新增 `QdrantVectorStore.delete_points()`。
  - 新增 `QdrantVectorStore.smoke_diagnostic(vector_size, country)`。
  - smoke 流程：写入临时 point -> 用同向量 search -> 校验命中临时 chunk -> 删除临时 point。
  - 返回 `status`、`search_hit`、`search_score`、`cleanup_status`、`point_id`、`vector_size`。
- Agent 诊断入口：
  - 新增 `run_qdrant_smoke_diagnostic(country)`。
  - 从最近一次 `knowledge/indices/qdrant_reindex_{country}.json` 读取 `vector_size`。
  - 如果没有可用 vector size，返回 `skipped_no_manifest_vector_size`，不猜测维度。
  - smoke 结果会回写同一个 manifest 的 `smoke_diagnostic` 字段。
- Runtime 操作入口：
  - Server 新增 `/qdrant_smoke_diagnostic` action。
  - Runtime RAG 面板新增 `Qdrant Smoke` 按钮。
  - 成功后页面提示 `status`、`search_hit`、`cleanup`、`vector_size`。
  - 版本化知识库卡片展示 `smoke` 与 `cleanup` 最新状态。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_rag.py -q -k "smoke_diagnostic"`：先因缺少 `QdrantVectorStore.smoke_diagnostic` 失败。
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "qdrant_smoke_diagnostic"`：先因缺少 `run_qdrant_smoke_diagnostic` 失败。
  - `PYTHONPATH=. pytest tests/test_server.py -q -k "qdrant_smoke_action"`：先因缺少 `/qdrant_smoke_diagnostic` action 失败。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "runtime_page_shows_rag_feedback_summary"`：先因页面缺少 `smoke=none` 与按钮失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py -q -k "smoke_diagnostic"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "qdrant_smoke_diagnostic"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_server.py -q -k "qdrant_smoke_action"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "runtime_page_shows_rag_feedback_summary"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_server.py tests/test_renderer.py -q`：214 passed。
  - `PYTHONPATH=. pytest tests -q`：316 passed。

当前限制：

- smoke diagnostic 依赖最近一次 reindex manifest 的 `vector_size`；如果未执行过 Qdrant reindex，会明确跳过。
- smoke 当前只诊断单 collection 单向量链路，不做高并发、批量写入、过滤条件组合或延迟统计。
- 临时点删除失败时会抛错给页面，后续可增加孤儿 smoke point 清理任务。

## v0.3.96 - Qdrant Collection Guard 与 Reindex Manifest

日期：2026-07-02

阶段目标：

- 继续补齐工业级 RAG 的生产化风险控制：避免 Qdrant collection 不存在、向量维度不匹配或入库后无记录可追溯。
- 让每次 Qdrant reindex 都能留下 manifest，便于回放、排障和面试展示。

已完成：

- Qdrant collection 管理：
  - `QdrantVectorStore.healthcheck()` 新增 collection 状态读取。
  - `QdrantVectorStore.ensure_collection(vector_size)` 新增 collection 创建/校验。
  - collection 不存在时自动 PUT 创建，默认 distance 为 `Cosine`。
  - collection 已存在但向量维度不一致时抛出明确错误，避免错误向量写入。
- Reindex 入库保护：
  - `reindex_rag_qdrant_from_raw()` 在 upsert 前调用 `ensure_collection(vector_size)`。
  - reindex 结果新增 `vector_size`、`collection_status`。
  - 无向量时仍返回 `skipped_no_vectors`，并写 manifest 记录失败原因。
- Reindex manifest：
  - 新增 `knowledge/indices/qdrant_reindex_{country}.json`。
  - manifest 记录国家、processed path、document/chunk/vector/upsert 点数、vector size、collection status、hit@5、mrr@5、threshold 是否通过。
  - `value_audit_rag_summary()` 会读取最近一次 Qdrant manifest。
- Runtime 可观测：
  - `/reindex_rag_qdrant` 成功消息新增 `vector_size` 和 `manifest` 路径。
  - Runtime “版本化知识库”卡片显示 `qdrant manifest`、`vector_size` 和 `points`。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_rag.py -q -k "ensures_missing_collection or rejects_collection_vector_size_mismatch"`：先因 `QdrantVectorStore.__init__()` 缺少 `management_transport` 失败。
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "reindexes_raw_rag_knowledge_into_qdrant"`：先因 reindex 结果缺少 `vector_size` 与 manifest 字段失败。
  - `PYTHONPATH=. pytest tests/test_server.py -q -k "reindex_rag_qdrant_action"`：先因 server 消息缺少 `vector_size` 与 `manifest` 失败。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "runtime_page_shows_rag_feedback_summary"`：先因 Runtime 页面缺少 `qdrant manifest=none` 展示失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py -q -k "ensures_missing_collection or rejects_collection_vector_size_mismatch"`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "reindexes_raw_rag_knowledge_into_qdrant"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_server.py -q -k "reindex_rag_qdrant_action"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "runtime_page_shows_rag_feedback_summary"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_server.py tests/test_renderer.py -q`：211 passed。
  - `PYTHONPATH=. pytest tests -q`：313 passed。

当前限制：

- 当前 collection ensure 是单向保护：能创建和校验维度，但还没有做删除过期 point、增量 diff 或别名切换。
- Qdrant healthcheck 只读取 collection 配置，不做真实 query smoke；下一步可以增加 “写入临时 point -> search -> 清理” 的可选诊断。
- Manifest 目前每个国家保留一份最新文件；后续可改成按 timestamp/run_id 多版本保留。

## v0.3.95 - RAG Qdrant Reindex 闭环

日期：2026-07-02

阶段目标：

- 继续补齐工业级 RAG：把 `raw -> processed -> chunk -> embedding -> Qdrant upsert -> hit@5 eval` 串成受控闭环。
- 让 Qdrant 不只是“可 search”，还具备从当前知识库重建并入库的操作入口。

已完成：

- Agent reindex 能力：
  - 新增 `reindex_rag_qdrant_from_raw(country)`。
  - 先复用 `rebuild_rag_knowledge_from_raw()` 从 raw 文档生成 processed JSONL。
  - 再构建当前国家的完整 RAG index，包含文件知识、内置价值观、审核规则、Memory 和 human gold 样本。
  - 对每个 chunk 生成 embedding vector，并调用 `prepare_qdrant_points()` 生成 Qdrant payload。
  - 调用 `QdrantVectorStore.upsert()` 写入 Qdrant。
  - 返回 `status`、chunk 数、vector 数、upsert 点数、collection、hit@5、mrr@5 和 provider runtime stats。
- Runtime 操作入口：
  - Server 新增 `/reindex_rag_qdrant` action。
  - Runtime “价值观与审核 RAG”面板新增“重建并入库Qdrant”按钮。
  - 成功后页面提示 `points`、`chunks`、`hit@5` 和 collection。
- 工程边界：
  - 支持测试注入 fake embedding provider / fake Qdrant store，避免单测真实打外部服务。
  - 默认真实入库仍依赖 `RAG_VECTOR_STORE_PROVIDER=qdrant`、Qdrant 配置和可生成真实向量的 embedding provider。
  - 如果 embedding provider 没有 query vector，会返回 `skipped_no_vectors`，不伪造入库成功。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "reindexes_raw_rag_knowledge_into_qdrant"`：先因缺少 `reindex_rag_qdrant_from_raw` 失败。
  - `PYTHONPATH=. pytest tests/test_server.py -q -k "reindex_rag_qdrant_action"`：先因缺少 `/reindex_rag_qdrant` action 失败。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "runtime_page_shows_rag_feedback_summary"`：先因页面缺少 Qdrant reindex 按钮失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "reindexes_raw_rag_knowledge_into_qdrant"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_server.py -q -k "reindex_rag_qdrant_action"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "runtime_page_shows_rag_feedback_summary"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_server.py tests/test_renderer.py -q`：209 passed。
  - `PYTHONPATH=. pytest tests -q`：311 passed。

当前限制：

- 本轮补齐的是受控 reindex 能力；还没有做 Qdrant collection 自动创建、向量维度校验、增量 upsert 或删除过期 point。
- 真正生产使用前，需要确认 Qdrant 服务、collection vector size 与当前 Qwen/DashScope embedding 维度一致。
- 下一步建议补 `qdrant_healthcheck + collection ensure + index manifest`，把每次 reindex 的知识版本、chunk 数、向量维度和 upsert 结果持久化。

## v0.3.94 - Qdrant 在线检索路径与 Trace 可观测

日期：2026-07-02

阶段目标：

- 继续补齐工业级 RAG 在线阶段：让 Qdrant 不只停留在 upsert adapter，而是具备可用于 hybrid recall 的在线 search 路径。
- 保持 demo 稳定：Qdrant search 必须显式开启，未启动 Qdrant 时默认继续走本地向量召回。

已完成：

- Qdrant 在线 search：
  - `QdrantVectorStore.search()` 新增 `/points/search` 调用。
  - 查询 payload 包含 query vector、top-k、payload 返回和国家过滤。
  - 国家过滤覆盖当前国家与 `GLOBAL` 审核规则，便于日本/法国价值观与全局审核规则共同召回。
- Vector store retriever 边界：
  - 新增 `QdrantVectorStoreRetriever`。
  - `HybridRagRetriever` 可注入外部 vector store retriever。
  - 有 Qdrant 分数时优先使用 Qdrant vector score；没有时自动回退本地 embedding 相似度。
- Query vector 能力：
  - `DashScopeEmbeddingProvider.query_vector()` 暴露真实 embedding query vector，供 Qdrant search 使用。
  - 本地 embedding provider 返回空向量，避免在未配置远程 embedding 时误调外部 vector store。
- Trace 可观测：
  - `RagRetrievalTrace` 新增 `vector_store_provider`。
  - Runtime 页面展示 `VectorStore search=on/off` 与本次 trace 的 `向量库=local/qdrant`。
- Agent 可控接入：
  - 新增 `RAG_QDRANT_SEARCH_ENABLED=1` 或 `RAG_VECTOR_STORE_SEARCH_ENABLED=1` 才会启用 Qdrant 在线 search。
  - summary 新增 `vector_store_search_enabled`，避免“配置了 Qdrant 但实际没走在线检索”的灰区。

验证：

- TDD RED：
  - `PYTHONPATH=. pytest tests/test_rag.py -q -k "qdrant_vector_store_search or hybrid_retriever_can_use_qdrant"`：先因缺少 `QdrantVectorStoreRetriever` 失败。
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "qdrant_online_search_path"`：先因缺少 `vector_store_search_enabled` 失败。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "runtime_page_shows_rag_feedback_summary"`：先因页面未展示 `VectorStore search=off` 失败。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py -q -k "qdrant_vector_store_search or hybrid_retriever_can_use_qdrant"`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "qdrant_online_search_path or qdrant_vector_store_config"`：2 passed。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "runtime_page_shows_rag_feedback_summary"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_renderer.py -q`：150 passed。
  - `PYTHONPATH=. pytest tests -q`：309 passed。

当前限制：

- Qdrant online search 默认仍关闭，需要显式设置 `RAG_QDRANT_SEARCH_ENABLED=1` 并确保 Qdrant collection 已写入向量。
- 本轮没有自动做 Qdrant collection 建表、schema 初始化或全量 reindex CLI；下一步需要把 raw -> processed -> embedding -> Qdrant upsert 串成受控命令。
- 若使用本地 embedding provider，Qdrant search 会因没有真实 query vector 自动跳过；真实在线向量检索需要开启 Qwen/DashScope embedding 远程调用。

## v0.3.93 - Docx Raw Ingest 与受控重建 RAG 知识库

日期：2026-07-02

阶段目标：

- 继续补齐工业级 RAG 离线阶段：让 `.docx` 原始知识文档也能进入 raw ingest。
- 增加受控重建入口，避免只能通过代码调用 raw -> processed 管线。

已完成：

- Raw ingest 增强：
  - `build_processed_documents_from_raw()` 支持 `.docx`。
  - 使用标准库读取 `word/document.xml` 段落，无新增依赖。
  - `.docx` 可用开头连续 `country/source_type/knowledge_version` 行作为 metadata。
  - `.docx` 同样支持 `## 标题 {#DOC_ID}` 稳定 gold parent id。
- Agent 受控重建：
  - 新增 `rebuild_rag_knowledge_from_raw(country)`。
  - 显式从 `knowledge/raw` 生成 `knowledge/processed/value_audit_documents.jsonl`。
  - 重建后立即跑 `value_audit_rag_eval_report()`，返回 `hit@5`、`mrr@5`、case 数和 processed 路径。
- Runtime 页面入口：
  - “价值观与审核 RAG”面板新增“重建RAG知识库”按钮。
  - Server 新增 `/rebuild_rag_knowledge` action。
  - 成功后在页面提示 document 数、`hit@5`、`mrr@5` 和 processed 文件路径。

验证：

- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_server.py tests/test_renderer.py -q -k "raw_docx or rebuilds_processed_rag or rebuild_rag_knowledge_action or runtime_page_shows_rag_feedback_summary"`：4 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_server.py tests/test_renderer.py -q`：204 passed。
  - `PYTHONPATH=. pytest tests -q`：306 passed。

当前限制：

- 受控重建会覆盖当前 processed JSONL，因此仍建议在真实业务使用前先检查 raw 文档和 eval case。
- `.docx` loader 目前读取正文段落，不解析表格、批注、图片 OCR 或复杂样式。
- Qdrant 仍未作为默认在线 search 后端。

## v0.3.92 - RAG Raw 文档离线 Ingest 与稳定 Gold ID

日期：2026-07-02

阶段目标：

- 继续补齐工业级 RAG 离线阶段：在 processed JSONL 之外，新增可人工维护的 raw Markdown 知识源。
- 让 raw 文档能稳定生成 processed documents，并保持 parent document id 与 eval gold case 对齐。

已完成：

- 新增 raw 知识源：
  - `knowledge/raw/japan_values.md`
  - `knowledge/raw/france_values.md`
  - `knowledge/raw/global_audit.md`
- 新增 raw ingest 能力：
  - `build_processed_documents_from_raw(raw_dir, output_path)`
  - 支持 Markdown/TXT 文件。
  - 支持 front matter：`country`、`source_type`、`knowledge_version`。
  - 按 `##` 语义边界拆成父文档。
  - 保留 `source_file`、`raw_section_index`、`knowledge_version` 等 metadata。
- 稳定 Gold ID：
  - Markdown 二级标题支持 `{#DOC_ID}`。
  - raw -> processed 重建后，document id 可与 `knowledge/eval/value_audit_cases.jsonl` 的 `expected_parent_id` 保持一致。
  - 临时重建验证：日本/法国 file eval 均保持 `hit@5=1.0`。
- Runtime 展示：
  - 版本化知识库卡片新增 raw 文件数量。
- 稳定性修复：
  - 试新上传解析的运营 tag 日期改为使用 Agent 业务日期，避免长测试跨午夜时从 0701 漂到 0702。

验证：

- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py -q -k "processed_documents_from_raw or engineering_pipeline_settings"`：2 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_renderer.py tests/test_server.py -q -k "processed_documents_from_raw or engineering_pipeline_settings or runtime_page_shows_rag_feedback_summary or real_openai_semantics or real_semantic_subject or compacts_long_semantic_subject"`：6 passed。
  - `PYTHONPATH=. pytest tests -q`：303 passed。
  - raw -> processed 临时重建后，日本 file eval：`hit@5=1.0`，法国 file eval：`hit@5=1.0`。

当前限制：

- raw ingest 当前覆盖 Markdown/TXT；`.docx` 原始业务文档后续还需要补专门 loader 或转换脚本。
- 当前不会自动覆盖仓库内 processed JSONL，避免误改生产知识库；后续可增加显式 CLI 命令做受控重建。
- Qdrant 仍是 adapter 边界，尚未作为默认在线 search 后端。

## v0.3.91 - 版本化 RAG 知识库与文件级检索评测

日期：2026-07-01

阶段目标：

- 继续推进工业级 RAG：把知识源从代码内置规则扩展为可版本化、可替换、可评测的文件知识库。
- 让日本/法国价值观与审核规则具备独立的 `knowledge/` 目录，便于后续接入业务背景、审核手册和 human_gold 样本。
- 让 RAG eval 从 smoke case 升级为文件级 gold case，支持后续补到 30-50 条业务 query。

已完成：

- 新增版本化知识库目录：
  - `knowledge/README.md`
  - `knowledge/processed/value_audit_documents.jsonl`
  - `knowledge/eval/value_audit_cases.jsonl`
- 新增文件加载器：
  - `FileDocumentLoaderAdapter`
  - `RetrievalCaseLoaderAdapter`
  - `load_rag_documents_jsonl()`
  - `load_retrieval_cases_jsonl()`
- Agent 接入文件知识库：
  - 默认读取仓库内 `knowledge/processed/value_audit_documents.jsonl`。
  - 支持 `PUZZLEOPS_RAG_KNOWLEDGE_DIR=/path/to/knowledge` 覆盖知识库目录。
  - 文件知识会进入 `build_value_audit_rag_index()`，与内置价值观、审核规则、Memory、human_gold 样本一起组成 RAG index。
- RAG eval 升级：
  - 默认读取 `knowledge/eval/value_audit_cases.jsonl`。
  - 有文件 eval case 时，`value_audit_rag_eval_report()` 使用 file eval；没有时回退 smoke eval。
  - 当前种子集：日本 6 条、法国 6 条。
  - 本地验证：日本 `hit@5=1.0`、法国 `hit@5=1.0`。
- Runtime 展示：
  - RAG summary 新增 `knowledge_base`。
  - Runtime 页新增版本化知识库状态，展示 document/case 数和文件名。

验证：

- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_renderer.py -q -k "file_document_loader or retrieval_case_loader or versioned_knowledge or rag_eval_report or engineering_pipeline_settings or runtime_page_shows_rag_feedback_summary"`：6 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_renderer.py -q`：144 passed。
  - `PYTHONPATH=. pytest tests -q`：302 passed。
- 手工 smoke：
  - 日本 file eval：`hit@5=1.0`，`mrr@5=0.9167`，total=6。
  - 法国 file eval：`hit@5=1.0`，`mrr@5=0.8056`，total=6。

当前限制：

- 文件 eval 目前是 12 条种子 case，仍需结合真实业务 query、人工 gold parent ids 扩展到 30-50 条。
- 当前知识库文件是 processed JSONL；后续还需要增加 raw 文档加载链路，把业务背景、审核手册、运营沉淀 Markdown/Docx 自动归一化为 processed documents。
- 在线检索默认仍用 SQLite chunk store + hybrid retriever；Qdrant 已有 upsert adapter，但还未切为默认在线 search。

## v0.3.90 - 工业级 RAG 离线/在线闭环第一版

日期：2026-07-01

阶段目标：

- 把价值观与审核 RAG 从“能召回引用”升级为更工程化的离线建库、在线检索、可追踪、可评测闭环。
- 对齐业务定位：日本/法国价值观、审核风险规则、真实 gold 样本和四层 Memory 都可以成为 RAG 知识源。
- 为团队规模适合的 Qdrant 路线补齐代码级 adapter 边界，但不强制本机必须启动 Qdrant。

已完成：

- 离线建库制品：
  - 新增 `RagIndexArtifacts`。
  - 新增 `export_offline_rag_index()`，可导出 `rag_manifest_国家.json`、`rag_documents_国家.jsonl`、`rag_chunks_国家.jsonl`。
  - manifest 记录 document/chunk 数、source 分布、chunking 参数、vector store 配置、父子 chunk 映射。
- 在线检索 trace：
  - 新增 `RagRetrievalTrace`。
  - `HybridRagRetriever.search_with_trace()` 会返回 eligible chunk 数、BM25 候选、向量候选、精确短语候选、合并候选池、最终 rerank hits。
  - Agent 的 `value_audit_rag_summary()` 已暴露 `retrieval_trace`，Runtime 页可查看候选池规模和最终引用。
- RAG 评测报告：
  - 新增 `evaluate_retrieval_report()`。
  - 支持 `hit@k`、`mrr@k`、threshold pass/fail、case 级 rank 和 retrieved parent ids。
  - Agent 新增 `value_audit_rag_eval_report()`，默认用本地检索跑 smoke eval，避免页面刷新产生远程模型费用。
  - Runtime 页新增“RAG 检索评测”卡片，展示 `hit@5`、`mrr@5`、threshold 与候选池 trace。
- Qdrant adapter 边界：
  - 新增 `QdrantPoint`、`prepare_qdrant_points()`、`QdrantVectorStore.upsert()`。
  - Qdrant payload 保留 `chunk_id`、`parent_id`、国家、source_type、标题、原文、chunk_index、metadata。
  - 继续保留 SQLite 作为本地 chunk store 与 embedding cache。
- Agent 层接口：
  - 新增 `export_value_audit_rag_artifacts(country, output_dir)`。
  - 新增 `value_audit_rag_eval_report(country)`。
  - `value_audit_rag_summary()` 新增 trace 与 eval report。

验证：

- 新增/更新测试：
  - 离线 RAG artifact 导出。
  - search trace 候选池与最终 hits。
  - hit@5/mrr@5 retrieval report。
  - Qdrant points payload 与 HTTP upsert adapter。
  - Agent 离线 artifact 导出与 RAG eval report。
  - Runtime 页展示 RAG 检索评测。
- 定向验证：
  - `PYTHONPATH=. pytest tests/test_rag.py -q`：30 passed。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "runtime_page_shows_rag_feedback_summary"`：1 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "engineering_pipeline_settings or rag_offline_artifacts or rag_eval_report"`：3 passed。
  - `PYTHONPATH=. pytest tests -q`：299 passed。

当前限制：

- Qdrant adapter 已具备 upsert 边界，但本轮没有强制启动 Qdrant 服务，也没有把所有在线检索切到 Qdrant HTTP search；默认仍以 SQLite 本地 chunk store + embedding cache 承载 demo 与测试。
- `value_audit_rag_eval_report()` 当前是 smoke eval，后续需要用你补齐的真实业务 query/gold parent ids 扩展成 30-50 条正式评测集。
- 离线知识库已能导出标准制品，但还需要把业务背景、审核手册、真实 human_gold 样本定期纳入版本化知识库目录。

## v0.3.89 - Qwen3-Embedding 真实调用验证与 BGE-Reranker-v2 Provider 边界

日期：2026-07-01

阶段目标：

- 确认当前 RAG 是否真实调用 Qwen3-Embedding，而不是停留在本地 fallback 或旧 embedding 配置。
- 为 BAAI 出品的 BGE-Reranker-v2 增加可接入的 provider 边界，避免“模型名写成 BGE，但实际仍走规则 rerank”的伪装状态。

已完成：

- 真实 Qwen3-Embedding 调用验证：
  - `.env` 已从 `RAG_EMBEDDING_MODEL=text-embedding-v3` 调整为 `RAG_EMBEDDING_MODEL=text-embedding-v4`。
  - 使用现有 DashScope/RAG key 做最小 smoke test。
  - 运行时 provider：`dashscope:text-embedding-v4`。
  - `embedding_remote_calls=1`，`embedding_fallbacks=0`，确认真实远程调用成功。
- 新增 BGE-Reranker-v2 Provider：
  - 新增 `BGERerankProvider`。
  - 支持通用 `/rerank` 风格 HTTP endpoint，请求体为 `model/query/documents`。
  - 推荐模型名：`BAAI/bge-reranker-v2-m3`。
  - 支持环境变量：
    - `RAG_RERANK_PROVIDER=bge`
    - `RAG_RERANK_MODEL=BAAI/bge-reranker-v2-m3`
    - `BGE_RERANK_ENDPOINT=http://.../v1/rerank`
    - `BGE_RERANK_API_KEY` 可选
  - 没有 `BGE_RERANK_ENDPOINT` 时，配置会明确标记 not ready，不会假装真实调用 BGE。
- 配置安全边界：
  - 当前本机未检测到可用 BGE rerank 服务或本地 FlagEmbedding 环境，所以 `.env` 暂不切换到 `RAG_RERANK_PROVIDER=bge`。
  - 继续保留当前可真实调用的 DashScope rerank，避免 RAG remote 因 BGE endpoint 缺失整体降级。

验证：

- 真实 smoke test：
  - `embedding_provider=dashscope:text-embedding-v4`
  - `remote_calls_enabled=True`
  - `embedding_remote_calls=1`
  - `embedding_fallbacks=0`
- 定向回归：
  - `PYTHONPATH=. pytest tests/test_rag.py -q -k "bge_rerank or dashscope_config_defaults or providers_from_config"`：5 passed，20 deselected。
- 全量回归：
  - `PYTHONPATH=. pytest tests -q`：292 passed。

当前限制：

- BGE-Reranker-v2 需要你后续提供一个真实可访问的 rerank endpoint，例如 Xinference、vLLM、TEI 或其他自托管服务；当前项目不会自动下载大模型并在本机启动服务。
- 如果现在强行把 `.env` 切到 `RAG_RERANK_PROVIDER=bge` 但不配置 `BGE_RERANK_ENDPOINT`，系统会判定 RAG 远程 provider not ready，不会伪造 BGE 调用。

## v0.3.88 - Qwen3 Embedding 默认配置、RAG hit@5 评测与 Qdrant 路线

日期：2026-06-30

阶段目标：

- 回应中文业务文档为主的 RAG 诉求：默认 embedding/rerank 从旧 DashScope 命名升级为 Qwen3 系列。
- 给 RAG 增加业务相关检索评测口径，支持用真实 gold case 验证 `hit@5 >= 0.8`。
- 把向量库路线从“仅 SQLite 轻量层”升级为“默认 SQLite，团队生产可配置 Qdrant”。

已完成：

- 模型默认配置调整：
  - `text-embedding-v3` 不是 OpenAI 模型名，在本项目里它属于阿里 DashScope/百炼 embedding 配置。
  - 当 `RAG_EMBEDDING_PROVIDER=dashscope` 且未显式指定模型时，默认改为 `text-embedding-v4`，用于对齐 Qwen3-Embedding 系列。
  - 当 `RAG_RERANK_PROVIDER=dashscope` 且未显式指定模型时，默认改为 `qwen3-rerank`。
  - 仍保留显式旧模型配置能力：如果 `.env` 主动写了 `RAG_EMBEDDING_MODEL` 或 `RAG_RERANK_MODEL`，系统会尊重配置。
- RAG 业务评测增强：
  - 新增 `RagRetrievalCase`。
  - 新增 `evaluate_retrieval_hit_rate(retriever, cases, k=5)`。
  - 用日本/法国价值观与审核规则构造业务 gold case，测试 `hit@5 >= 0.8`，当前测试样本达到 5/5。
- Qdrant 配置边界：
  - 新增 `RagVectorStoreConfig`。
  - 支持：
    - `RAG_VECTOR_STORE_PROVIDER=qdrant`
    - `QDRANT_URL`
    - `QDRANT_COLLECTION`
    - `QDRANT_API_KEY`
  - Agent summary 会展示 `vector_store`、collection、ready 状态和配置说明。
  - 页面“离线建库”卡片会展示当前 store 是 SQLite 还是 Qdrant。

验证：

- TDD 红灯：
  - 缺少 `RagRetrievalCase` 时导入失败。
  - 默认模型仍为 `text-embedding-v3/gte-rerank-v2` 时失败。
  - Qdrant 配置无法进入 Agent summary 时失败。
- 定向回归：
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_renderer.py tests/test_storage_runtime.py -q -k "rag or qdrant or embedding_cache"`：56 passed，87 deselected。
- 全量回归：
  - `PYTHONPATH=. pytest tests -q`：290 passed。

当前限制：

- 本版先完成 Qdrant 配置与工程边界，没有把真实 Qdrant HTTP upsert/search 全链路接入运行路径。
- 当前 hit@5 测试使用本地小型业务 gold case；下一步应接入你整理的真实 30-50 张拼图样本与人工/AI gold label。
- `text-embedding-v4/qwen3-rerank` 真实远程调用仍依赖阿里百炼账号、额度和 `RAG_ENABLE_REMOTE_CALLS`。

## v0.3.87 - RAG 离线/在线两阶段工程化包装

日期：2026-06-30

阶段目标：

- 按“离线建知识库 + 在线混合检索生成”的标准 RAG 架构重做工程表达。
- 让项目不只说“用了 RAG”，而是能讲清楚 DocumentLoader、chunk/overlap、向量/BM25 多路召回、rerank、prompt 防幻觉和可替换存储边界。

已完成：

- 离线阶段增强：
  - 新增 `StaticDocumentLoaderAdapter`，把现有日本/法国价值观、审核规则、Memory、human_gold 样本、历史数据沉淀统一包装成可替换 loader 边界。
  - 新增 `RagChunkingConfig`，默认 `sentence_token` 切分，`chunk_size_tokens=600`，`chunk_overlap_tokens=100`。
  - `chunk_document` 支持 token-aware chunk，优先按句子语义边界切分，并把 splitter/chunk/overlap 参数写入 chunk metadata。
  - 当前存储层明确定位为轻量本地向量层：SQLite 保存 chunk 原文和 metadata，`rag_embedding_cache` 保存真实 embedding provider 返回的向量缓存；后续可替换为 Chroma/Qdrant/Milvus adapter。
- 在线阶段增强：
  - 新增 `rewrite_rag_query`，在不丢失原始 query 的前提下补充“价值观、审核、文化混淆、版权/IP、文字水印、AI质量、主体/色彩/构图”等业务检索词。
  - `HybridRagRetriever.search` 从“全量混合打分”升级为显式候选池：BM25 top-k + Vector top-k + exact phrase 去重后进入 rerank。
  - Agent 默认候选池参数：BM25 top-k 30，Vector top-k 30，最终 rerank top-k 与调用方 `top_k` 对齐。
  - RAG prompt 强化防幻觉约束：只基于引用依据回答；资料没有答案必须说“不知道/需要人工复核”。
- 真实模型配置增强：
  - `RAG_EMBEDDING_PROVIDER=dashscope` 时默认 embedding 模型为 `text-embedding-v3`。
  - `RAG_RERANK_PROVIDER=dashscope` 时默认 rerank 模型为 `gte-rerank-v2`。
  - 仍保留 `RAG_ENABLE_REMOTE_CALLS` 开关；未开启时使用本地 fallback，避免测试和 demo 强依赖外部费用。
- 页面可观测增强：
  - 多模态底座/RAG 摘要新增“离线建库”和“在线检索”卡片。
  - 展示 loader、splitter、chunk token、overlap、vector store、BM25/Vector candidate top-k、rerank top-k 和 rewritten query。

验证：

- TDD 红灯：
  - 缺少 `RagChunkingConfig`/`StaticDocumentLoaderAdapter` 时 `tests/test_rag.py` 导入失败。
  - Agent RAG summary 缺少离线/在线工程参数时失败。
  - Runtime 页面缺少“离线建库/在线检索”展示时失败。
- 定向回归：
  - `PYTHONPATH=. pytest tests/test_rag.py -q`：21 passed。
  - `PYTHONPATH=. pytest tests/test_rag.py tests/test_agents.py tests/test_renderer.py -q -k "rag or runtime_page_shows_profile_candidates"`：43 passed，86 deselected。
- 全量回归：
  - `PYTHONPATH=. pytest tests -q`：287 passed。

当前限制：

- 本版没有引入 Chroma/Qdrant/Milvus 等外部向量数据库，当前采用 SQLite chunk 存储 + embedding cache 的轻量本地实现，适合 demo 和本地闭环。
- query rewrite 仍是规则增强，不额外调用 LLM 改写，避免每次检索都产生额外费用。
- 真实 embedding/rerank 只有在配置 DashScope key 且开启 `RAG_ENABLE_REMOTE_CALLS` 后才会调用。

## v0.3.86 - 好图衍生生成 Provider 接通通义万相与 ComfyUI

日期：2026-06-30

阶段目标：

- 补齐好图衍生生成的真实 Provider 路线：云端通义万相/DashScope + 本地 ComfyUI。
- 保持既有安全边界：生成图只作为参考图，必须经过二次 VLM 解析与审核，运营确认后才允许同步飞书附件。

已完成：

- 通义万相/DashScope Provider 延续并强化：
  - `IMAGE_GENERATION_PROVIDER=dashscope` 或 `wanx` 时使用 `DashScopeImageGenerationProvider`。
  - 优先读取 `IMAGE_GENERATION_API_KEY`，缺省时复用现有 `QWEN_API_KEY`。
  - 默认模型为 `wan2.6-image`，生成结果会下载到本地并保留 prompt、negative prompt、seed、来源样本等元数据。
- 新增真实 ComfyUI Provider：
  - `IMAGE_GENERATION_PROVIDER=comfyui` 时使用 `ComfyUIImageGenerationProvider`。
  - 使用 `COMFYUI_BASE_URL`，默认 `http://127.0.0.1:8188`。
  - 使用 `COMFYUI_WORKFLOW_PATH` 读取本地 workflow JSON。
  - 不要求 API key，适合本机或内网 ComfyUI。
  - 会向 workflow 注入 prompt、negative prompt、seed、reference image 和 PuzzleOps 约束信息。
  - 支持 ComfyUI `/prompt`、`/history/{prompt_id}`、`/view` 基础链路，并把生成图保存成本地文件。
- 诊断与页面增强：
  - Trial 页生成 Provider 诊断新增 `workflow_path` 和 `workflow_configured`。
  - 服务端 `/check_generation_provider` 同步显示 ComfyUI workflow 配置状态。
  - 未配置 workflow 时不会伪造生成成功，会明确提示缺少 `COMFYUI_WORKFLOW_PATH`。

验证：

- TDD 红灯：
  - 缺少 `ComfyUIImageGenerationProvider` 类时测试导入失败。
  - `IMAGE_GENERATION_PROVIDER=comfyui` 仍要求 API key 时失败。
  - Trial 页缺少 ComfyUI workflow 诊断字段时失败。
- 定向回归：
  - `PYTHONPATH=. pytest tests/test_harness.py -q -k "comfyui or dashscope_generation_provider or cloud_generation_provider or factory_selects_generation_provider"`：6 passed。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "generation_readiness or generation_provider"`：4 passed。
  - `PYTHONPATH=. pytest tests/test_server.py -q -k "generation_provider_diagnostic or check_generation_provider"`：2 passed。
  - `PYTHONPATH=. pytest tests/test_server.py -q -k "generate_trial_derivatives or approve_generated_derivatives or sync_trial"`：6 passed。
  - `PYTHONPATH=. pytest tests/test_harness.py tests/test_renderer.py -q -k "generation"`：17 passed。

当前限制：

- ComfyUI workflow 的节点结构由运营/工程配置提供，本项目只做 workflow 注入和调用适配，不内置大型模型或 LoRA。
- 通义万相与 ComfyUI 真实出图都会产生外部依赖：云端费用、额度、模型可用性，或本机 ComfyUI 服务状态。
- 生成图仍不会自动作为最终生产图；必须经过二次 VLM 审核与运营确认。

## v0.3.85 - 真实 Harness Baseline 与失败样本复盘

日期：2026-06-30

阶段目标：

- 在 v0.3.84 的 human_gold/RAG/Memory 沉淀闭环上，补齐“真实评测基线”视角。
- 让 Eval 页不只展示单次 Harness 运行指标，还能明确说明当前 run 是否可作为 human_gold baseline，以及失败样本复盘的规模和分类。

已完成：

- 新增 `harness_baseline_summary(country)`：
  - 绑定当前展示 run 或最近保存 run。
  - 统计真实样本数、human_gold 样本数、human_gold 覆盖率。
  - 统计失败 case 数、失败样本数、`not_evaluable` 数。
  - 聚合 Top 失败分类，支撑后续人工复盘优先级。
  - 输出下一步动作：
    - human_gold 不完整时，提示先完成人工抽查。
    - 有失败 case 时，提示进入失败样本人工复盘。
    - 无失败 case 时，提示可保存为当前真实 baseline。
- Eval 页新增“真实 Baseline 复盘”：
  - 展示 baseline 状态、run_id、执行模式。
  - 展示 human_gold 覆盖率、失败 case 数、失败样本数、Top 失败分类和下一步动作。
  - 与原有“失败样本复盘”“Case 证据链”“失败分类”形成完整评测闭环。

验证：

- TDD 红灯：
  - `harness_baseline_summary` 不存在时失败。
  - Eval 页缺少“真实 Baseline 复盘”时失败。
- 定向回归：
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "harness_baseline_summary_reports or harness_summary or harness_readiness"`：4 passed。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "real_baseline_summary or failure_samples or case_evidence"`：3 passed。
  - `PYTHONPATH=. pytest tests/test_harness.py -q`：21 passed。

当前限制：

- 本版本提供 baseline 摘要和失败复盘入口；真实模型质量仍取决于 Qwen-VL 配置、human_gold 覆盖率和实际运行是否选择真实 VLM。
- 下一阶段进入好图衍生生成 Provider，重点是接通通义万相/ComfyUI，并把生成结果纳入二次 VLM 审核和 Harness 评测。

## v0.3.84 - human_gold 与 RAG/Memory 沉淀证据闭环

日期：2026-06-30

阶段目标：

- 在 v0.3.83 的 `ai_silver/pending_review` 基础上，补齐运营确认后的可信标准答案沉淀闭环。
- 让“确认 AI 预标注为 human_gold”不只是改 CSV 状态，而是能明确证明已经进入 facts memory 和 RAG 可引用知识库。

已完成：

- 增强 `approve_harness_silver_labels(country)`：
  - 确认通过的 silver 样本继续晋升为 `human_gold/reviewed`。
  - 晋升后写入 `facts` 层 memory，保留人工确认 note。
  - human_gold 样本继续进入价值观/审核 RAG 文档，作为 `harness_gold_sample` 来源。
  - 返回沉淀证据统计：
    - `fact_memory_count`
    - `rag_human_gold_count`
    - `human_gold_count`
- 服务端消息增强：
  - `/approve_harness_silver_labels` 成功后，页面提示不再只显示晋升条数。
  - 同步显示 Facts 沉淀数量与 RAG human_gold 文档数量，方便运营判断闭环是否完成。

验证：

- TDD 红灯：
  - 晋升返回值缺少 `fact_memory_count / rag_human_gold_count / human_gold_count` 时失败。
  - 服务端确认消息缺少 Facts/RAG 沉淀数量时失败。
- 定向回归：
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_server.py tests/test_renderer.py -q -k "human_gold or harness_silver or harness_readiness or rag_documents_include_human_gold or rag_answer_can_cite_human_gold"`：8 passed。

当前限制：

- 本版本证明的是 `ai_silver -> human_gold -> facts memory/RAG` 的沉淀链路。
- 真实业务效果还需要下一版运行真实 Harness baseline，把 Agent 输出与 human_gold 做批量对比和失败样本复盘。

## v0.3.83 - 真实样本 VLM 预标注批处理

日期：2026-06-30

阶段目标：

- 在 v0.3.82 已接入 45 张真实业务样本的基础上，打通“真实 VLM 预标注 -> ai_silver -> 人工抽查”的第一段闭环。
- 让运营可以分批调用 Qwen-VL，为已有人工等级的真实样本补主体、色彩氛围、构图环境、价值观候选和风险候选。

已完成：

- 增强 `auto_prelabeled_harness_samples(country)`：
  - 支持默认从真实业务 Excel 样本生成 gold CSV 草稿后直接预标注。
  - 新增 `max_count`，支持一次只处理部分样本，避免误触后产生过多模型调用费用。
  - 默认跳过已是 `ai_silver/pending_review` 或 `human_gold/reviewed` 的样本，避免重复付费覆盖。
  - 返回批处理进度：总真实样本数、可预标注数、已更新数、跳过数、剩余待预标注数、待审核 silver 数、human_gold 数。
- Eval 页增强：
  - Gold Dataset 工作台新增“AI 预标注进度”。
  - 显示：待预标注、待审核 silver、human_gold。
  - “AI 自动预标注”表单新增本次最多处理张数，默认 5 张。
- 服务端 action 增强：
  - `/auto_prelabeled_harness_gold` 支持读取 `max_count`。
  - 同步消息显示剩余待预标注和待审核 silver 数量。
  - 登记真实样本后选择“立即 AI 预标注”时，默认最多处理 5 张。

验证：

- TDD 红灯：
  - 默认业务样本批量预标注不支持 `max_count` 时失败。
  - 已有 silver/human_gold 被重复调用覆盖时失败。
  - Eval 页缺少 AI 预标注进度时失败。
- 定向回归：
  - `PYTHONPATH=. pytest tests/test_agents.py -q -k "ai_prelabeled"`：3 passed。
  - `PYTHONPATH=. pytest tests/test_renderer.py -q -k "ai_prelabel or ai_silver"`：2 passed。
  - `PYTHONPATH=. pytest tests/test_server.py -q -k "auto_prelabeled or register_harness_real_samples_text_action_can_auto"`：2 passed。
  - `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py -q`：160 passed。

当前限制：

- 本版本打通的是“预标注批处理能力”，不会把 `ai_silver` 自动当作最终标准答案。
- 真实业务效果证明仍需要运营抽查后晋升 `human_gold`，再运行真实 VLM Harness baseline。
- 如果 Qwen-VL 配置缺失或余额不足，页面会提示失败，不会伪造预标注结果。

## v0.3.82 - 真实业务样本接入与业务背景规则同步

日期：2026-06-30

阶段目标：

- 接入你补充的 `数据示例.xlsx`：在原有 5 张日本样本基础上，识别新增日本 20 张、法国 20 张，形成合计 45 张真实业务样本。
- 重新阅读更新后的业务背景，把国家 OKR、JS 分类和真实样本口径同步到分析页与 Harness。

已完成：

- Excel 导入增强：
  - 支持同一工作簿内按 `国家` 过滤日本/法国样本。
  - 支持 `DISPIMG` 单元格图片抽取，真实图片会落到本地运行目录供多模态/Harness 使用。
  - 兼容业务表中的 JS 分类别名：`house -> houses`、`object -> objects`、`flower -> flowers`。
  - 新增 `drawing` JS 分类，匹配业务背景文档中的正式分类集合。
- 数据分析大师改为优先使用真实业务样本：
  - 日本 25 条、法国 20 条明细进入分析表。
  - SA/CD/AI 占比从真实等级与图片来源实时计算。
  - 日本当前样本：SA 52%，CD 20%，AI 48%。
  - 法国当前样本：SA 50%，CD 25%，AI 75%。
- 业务背景规则同步：
  - 日本 AI OKR 更新为 30%。
  - 法国 AI OKR 更新为 35%。
  - Dashboard 与数据分析页保持同一 OKR 口径。
- Harness 样本链路优化：
  - 默认 Harness 在没有 gold CSV 时会读取全部真实业务样本，而不是只截取前 8 条。
  - 手动登记真实样本时不再自动预填 Excel 默认样本，避免运营手动 gold 数据集被 45 条默认样本污染。
  - 前两层验收状态识别到当前项目已有 45 张真实拼图样本，下一步指向真实 VLM Harness 与 AI silver 人工抽查。

验证：

- `PYTHONPATH=. pytest tests/test_importer_multimodal.py -q`：13 passed。
- `PYTHONPATH=. pytest tests/test_agents.py -q`：61 passed。
- 新增/更新测试覆盖：
  - 混合国家 Excel 过滤与真实图片抽取。
  - JS 分类别名归一化。
  - 数据分析页真实样本指标计算。
  - 默认 Harness 全量读取真实业务样本。
  - 手动 gold 登记不被默认样本污染。

当前限制：

- 这 45 张样本已有真实图片、等级和业务指标，但主体/色彩/构图等 gold label 仍需要通过真实 VLM 预标注后，由人工抽查晋升为 `human_gold`。
- 当前分析页已使用真实样本计算业务占比，但“价值观是否真正符合市场”仍需要后续运行 VLM Harness 与 RAG 引用评测来证明。

## v0.3.81 - 前两层落地验收闭环

日期：2026-06-26

阶段目标：

- 将“前两层已落地”从口头说明变成系统内可验证的验收清单。
- 明确区分：
  - 前两层：闭环稳定、RAG、Memory、Harness、HITL、可溯源评估基础设施。
  - 第三层：等待你补齐 30-50 张真实拼图图片、人工等级和真实业务字段后运行真实样本基线。

已完成：

- 新增 `front_two_layers_readiness(country)`：
  - 第一层 gate：
    - 真实样本接入工作台。
    - AI silver -> human_gold 防误用。
    - Harness 运行与失败复盘。
    - 业务指标缺口提示。
  - 第二层 gate：
    - 四层 Memory 可进入 RAG。
    - RAG 多路召回与引用溯源。
    - 价值观与审核知识源齐全。
    - RAG 人工反馈可影响 rerank。
  - 每个 gate 都输出 `passed / evidence / next_action`。
- Eval 页新增“前两层落地验收”面板：
  - 展示总体状态 `front_two_layers_landed` 或 `front_two_layers_need_attention`。
  - 逐项展示第一层、第二层 gate 的状态、证据和后续动作。
  - 明确提示第三层仍等待真实业务样本输入。
- 保留 v0.3.80 的 Harness Readiness：
  - 继续用于判断真实样本是否已经可以运行真实 VLM Harness 基线。
  - AI silver 仍不能直接当作 human_gold。

验证：

- TDD 红灯：
  - `front_two_layers_readiness` 不存在时，后端验收测试失败。
  - Eval 页缺少“前两层落地验收”面板时，页面测试失败。
- 关联测试：`PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py -q -k "harness_readiness or front_two_layers or gold_dataset_workbench"`：5 passed。
- 全量回归：`PYTHONPATH=. pytest tests -q`：267 passed，用时 21.37s。
- 页面验证：
  - 临时服务 `http://127.0.0.1:5201/?view=eval` 可看到“前两层落地验收”。
  - 默认页面状态显示 `front_two_layers_landed`，第三层提示为等待真实样本输入。

当前限制：

- 本版本证明的是前两层基础设施已可自检和可验收，不声称真实业务效果已经被验证。
- 真实效果证明仍依赖第三层：你补齐 30-50 张真实拼图、人工等级、真实业务指标后，再运行真实 VLM Harness。

## v0.3.80 - Harness Readiness 与真实评测准备度

日期：2026-06-25

阶段目标：

- 按“前两层优先”的方向，把现有闭环做稳：真实样本进入后，系统要清楚告诉运营还缺什么，不能把 AI silver label 误当成可证明业务效果的 gold。
- 继续增强 RAG/Memory 可见性：human_gold 是否已经进入 facts memory 和 RAG 文档，需要在 Harness 工作台可检查。

已完成：

- 新增 `harness_readiness(country)`：
  - 统计真实样本数、完整 gold 样本数、完整业务指标样本数。
  - 统计 `ai_silver/pending_review` 待人工审核数量。
  - 统计 `human_gold/reviewed` 样本数量。
  - 统计 human_gold 是否已经沉淀为 RAG 文档和 facts memory。
  - 输出 `ready_for_real_eval` 和下一步动作建议。
- Eval 页 Gold Dataset 工作台新增 Harness Readiness 状态条：
  - 显示“尚不能证明真实业务效果”或“可作为真实评测基线”。
  - 展示 human_gold、silver待审、待AI预标注、RAG gold文档、Facts 数量。
  - 展示下一步动作：AI预标注、人工确认 silver、补 gold 字段、补业务指标、刷新 RAG/Memory。
- 保持现有保护逻辑：
  - AI 预标注仍只是 `ai_silver`。
  - 只有人工确认后才进入 `human_gold`、facts memory 和 RAG 引用文档。

验证：

- TDD 红灯：
  - `harness_readiness` 不存在时，新增 readiness 测试失败。
  - Eval 页缺少 readiness 展示时，页面测试会失败。
- 关联测试：`PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py -q -k "harness_readiness or gold_dataset_workbench"`：3 passed。
- 全量回归：`PYTHONPATH=. pytest tests -q`：265 passed，用时 22.58s。

当前限制：

- Readiness 只判断当前本地 CSV、Memory 和 RAG 文档状态，不替代真实业务指标质量判断。
- 你后续补 30-50 张真实拼图和业务字段后，才能把 Harness 指标作为面试叙事中的真实小样本证据。

## v0.3.79 - Harness 真实样本缩略图轻量化

日期：2026-06-25

阶段目标：

- 修复 Eval 页真实样本越来越多后 HTML 过大的问题。
- 避免把本机真实图片原图 base64 内嵌进 Harness Dashboard，保障 30-50 张真实样本进入后页面仍能正常打开和滚动。

已完成：

- Harness 样本缩略图从内联 `data:image/...;base64` 改为轻量 URL：
  - `/local_image?path=...`
- Server 新增 `/local_image` 本地图片读取路由：
  - 按需读取本机图片路径。
  - 按 `.png / .jpg / .jpeg / .webp` 返回对应 `Content-Type`。
- 保留常规素材卡、价值观卡的原有内联示意图，不影响既有页面展示。

验证：

- TDD 红灯：
  - 大尺寸真实样本图导致 Eval HTML 内嵌 base64 且页面超过阈值时，新增测试失败。
- 修复后关联测试：`PYTHONPATH=. pytest tests/test_renderer.py tests/test_server.py -q`：95 passed。
- 全量回归：`PYTHONPATH=. pytest tests -q`：263 passed，用时 14.09s。
- 页面验证：
  - `http://127.0.0.1:5199/?view=eval&country=法国` HTML 约 56KB。
  - 页面包含 `/local_image?path=`，不再包含真实样本 `data:image/png;base64`。
  - `/local_image` 返回 200，`Content-Type=image/png`，PNG 头正常。

当前限制：

- `/local_image` 只服务本机已有图片路径，用于本地 demo 和 Harness 工作台；部署到多人环境时需要增加访问控制或改为受控对象存储。
- 图片文件仍不进入 Git，只保存路径引用。

## v0.3.78 - Harness 真实样本目录批量登记

日期：2026-06-25

阶段目标：

- 继续推进 Agent Harness 主线，把你准备好的真实拼图图片更低成本地纳入评测数据集。
- 支持“图片已放在一个本机目录 + 人工只提供等级”的工作方式，后续再由 AI 预标注主体、色彩、构图、价值观和风险。

已完成：

- 新增 `register_harness_real_samples_from_directory()`：
  - 扫描本机目录中的 `.png / .jpg / .jpeg / .webp` 图片。
  - 支持按文件名排序后的序号等级，例如 `1A 2A 3B 4S 5C`。
  - 支持按文件名精确指定等级，例如 `截屏2026-06-23 22.18.33.png=A`。
  - 使用文件名精确映射时，只登记被映射到的图片，避免把同目录旧图误加入数据集。
  - 复用原有真实样本 CSV 写入、路径去重、`manual_grade -> needs_ai_prelabeled` 状态流转。
- Eval 页 Gold Dataset 工作台新增“按目录登记真实样本”表单：
  - 图片目录
  - 等级映射
  - JS 分类
  - 登记后立即 AI 预标注
- `/register_harness_real_samples` 路由支持目录登记和原有逐行粘贴登记两种模式。

验证：

- TDD 红灯：
  - agent 目录登记方法不存在时，目录登记测试失败。
  - server 路由未读取 `image_dir` 时，目录登记动作测试失败。
  - Eval 页面缺少目录表单时，页面测试失败。
  - 文件名精确映射模式误登记未标注旧图时，回归测试失败。
- 关联测试：`PYTHONPATH=. pytest tests/test_agents.py tests/test_server.py tests/test_renderer.py -q`：148 passed。
- 全量回归：`PYTHONPATH=. pytest tests -q`：262 passed，用时 16.09s。

当前限制：

- `1A 2A 3B 4S 5C` 这类序号等级默认按文件名排序匹配；如果图片顺序和文件名排序不一致，建议使用 `文件名=A` 的精确映射方式，未映射文件不会登记。
- 登记只保存本机图片路径和人工等级，不复制图片、不提交图片到 Git。
- AI 预标注仍会调用真实视觉 LLM，可能产生少量费用。

## v0.3.77 - 通义万相生成图下载证书修复

日期：2026-06-25

阶段目标：

- 继续推进真实好图衍生生成主线，修复 DashScope/通义万相已生成图片但本地下载失败的问题。
- 在阿里云账号充值恢复后，用真实 provider smoke test 验证链路能走到“生成并保存图片”。

已完成：

- 图像生成结果下载 `_download_image()` 接入 HTTPS SSL context。
- 生成云接口 `_cloud_transport()` 复用同一套证书上下文。
- 证书策略与现有 VLM 调用保持一致：
  - 优先使用 `certifi` 证书包。
  - `certifi` 不可用时回退到系统默认证书。
- 新增回归测试，确保 HTTPS 图片下载时不会退回裸 `urlopen`。

验证：

- TDD 红灯：`test_image_download_uses_https_certificate_context` 在旧实现下失败，原因是 `context is None`。
- 修复后关联测试：`PYTHONPATH=. pytest tests/test_harness.py::test_image_download_uses_https_certificate_context tests/test_harness.py::test_dashscope_generation_provider_uses_reference_image_and_downloads_sdk_result tests/test_harness.py::test_cloud_generation_provider_writes_returned_images_with_generation_metadata -q`：3 passed。
- 全量回归：`PYTHONPATH=. pytest tests -q`：258 passed，用时 15.26s。
- 真实通义万相 smoke test：
  - provider：`dashscope`
  - model：`wan2.6-image`
  - 参考图：`/Users/fanglemin/Desktop/图片/截屏2026-06-23 22.18.33.png`
  - 结果：生成 1 张图片并成功保存到本地临时目录，文件大小 2,811,010 bytes。

当前限制：

- 真实生成结果仍需要二次 VLM 解析、审核和人工确认后，才能进入试新提需和飞书同步。
- 生成图片保存在运行时临时目录，不作为仓库资产提交。

## v0.3.76 - Harness 生成失败外部阻塞归因

日期：2026-06-25

阶段目标：

- 继续推进 Agent Harness 主线，把图像生成失败拆成“外部前置条件阻塞”和“Agent/适配层失败”。
- 避免把账务、额度、模型下线、鉴权、配置缺失等问题错误计入 Agent 能力缺陷。

已完成：

- 新增 `EXTERNAL_GENERATION_ERROR_TYPES`：
  - `billing_arrearage`
  - `quota_exceeded`
  - `model_deprecated`
  - `auth_error`
  - `config_missing`
  - `timeout`
- Harness 指标新增：
  - `生成外部阻塞率`
  - `生成Agent失败率`
  - `生成恢复建议覆盖率`
- `record_generation_event()` 持久化 `recovery_hint`，避免生成任务回放和 Harness 指标丢失处理建议。
- Eval 页“生成失败类型分布”从两列升级为三列：
  - 错误类型
  - 次数
  - 处理建议

验证：

- 新增 TDD 覆盖：
  - 同时存在外部阻塞和响应结构失败时，Harness 能分别统计外部阻塞率与 Agent 失败率。
  - Eval 页展示 `生成外部阻塞率 / 生成Agent失败率 / 生成恢复建议覆盖率`。
  - 生成失败类型分布展示账务错误的处理建议。
- 关联测试：`PYTHONPATH=. pytest tests/test_harness.py tests/test_renderer.py tests/test_agents.py -q`：109 passed。
- 全量回归：`PYTHONPATH=. pytest tests -q`：257 passed，用时 15.02s。

当前限制：

- 外部阻塞类型由当前错误分类规则维护；如果未来接入更多图像生成平台，需要补充平台专属错误码映射。
- 阿里云账务状态仍需你在控制台处理后才能继续真实生成 smoke test。

## v0.3.75 - 生成失败处理建议沉淀

日期：2026-06-25

阶段目标：

- 继续推进“真实好图衍生生成”主线，把真实 provider 错误从“分类”升级为“可行动处理建议”。
- 让 Trial 页、同步记录和生成事件回放都能展示同一份恢复建议，方便运营和技术排障。

已完成：

- 新增 `generation_error_recovery_hint()`：
  - `billing_arrearage`：提示到阿里云控制台处理欠费、余额或资源包状态。
  - `quota_exceeded`：提示检查额度、资源包余量或频控。
  - `model_deprecated`：提示迁移 `IMAGE_GENERATION_MODEL` 并 smoke test。
  - `timeout`：提示稍后重试、保留 task_id、降低单次生成数量。
  - `auth_error`：提示检查 API key 和模型权限。
  - `config_missing`：提示补齐生成 provider、模型和 key。
  - `response_schema`：提示保留原始响应并更新解析适配。
- 生成失败时：
  - `sync_message` 追加“处理建议”。
  - `generation_event` 持久化 `recovery_hint`。
  - 生成任务回放可继续复用该字段。
- Trial 页“最近一次生成任务”新增“处理建议”字段。

验证：

- 新增 TDD 覆盖：
  - `billing_arrearage` 能返回含“阿里云 / 欠费 / 资源包”的处理建议。
  - 生成失败为账务错误时，页面消息和事件都包含处理建议。
  - Trial 页最近一次生成任务展示“处理建议”。
- 关联测试：`PYTHONPATH=. pytest tests/test_server.py tests/test_renderer.py tests/test_agents.py -q`：144 passed。
- 全量回归：`PYTHONPATH=. pytest tests -q`：257 passed，用时 16.37s。

当前限制：

- 真实通义万相生成仍被阿里云账号账务状态阻塞；需要先处理控制台账务/资源包状态。
- 本版不再次触发真实扣费调用，只增强错误可解释性和回放质量。

## v0.3.74 - 真实生成 Smoke Test 账务错误分类

日期：2026-06-25

阶段目标：

- 继续推进“真实好图衍生生成”主线，验证通义万相真实调用链路是否能触达云端。
- 把真实 smoke test 暴露出的阿里云账务阻塞沉淀为明确错误类型，方便后续排障和面试说明。

已完成：

- 执行 1 张图的真实 DashScope smoke test：
  - Provider 状态为 `provider=dashscope`、`ready=True`、`api_key_source=QWEN_API_KEY`、`sdk_available=True`。
  - 请求已触达 DashScope 云端。
  - 云端返回 `Arrearage：Access denied, please make sure your account is in good standing`。
- `classify_generation_error()` 新增 `billing_arrearage`：
  - 命中 `Arrearage`。
  - 命中 `overdue-payment`。
  - 命中 `good standing`。
  - 命中中文 `欠费 / 逾期`。
- 保留原有 `quota_exceeded` 分类，用于余额/额度不足等非欠费类场景。

验证：

- 新增 TDD 覆盖：
  - DashScope 返回 `Arrearage` 时分类为 `billing_arrearage`。
  - 既有 quota、模型下线、超时、鉴权、配置缺失、响应结构错误分类保持不变。
- 分类与生成失败测试：8 passed。
- 关联测试：`PYTHONPATH=. pytest tests/test_server.py tests/test_renderer.py tests/test_harness.py -q`：110 passed。
- 全量回归：`PYTHONPATH=. pytest tests -q`：255 passed，用时 18.27s。

当前限制：

- 当前真实生成被阿里云账号账务状态阻塞，需要你在阿里云控制台处理欠费/余额/资源包状态后，才能完成真实图片生成。
- 这不是代码、SDK 或 API key 问题；当前链路已经能触达 DashScope 并拿到云端业务错误。

## v0.3.73 - DashScope 生成响应解析兼容增强

日期：2026-06-25

阶段目标：

- 继续推进“真实好图衍生生成”主线，减少通义万相真实调用后因为响应结构差异导致的落图失败。
- 在不触发扣费调用的前提下，用本地 TDD 固化 DashScope SDK 返回结果的解析契约。

已完成：

- 新增 `_dashscope_images_from_response()`：
  - 兼容 `output.results[*].url`。
  - 兼容 `output.images[*].url/image/image_url`。
  - 保留原有 `choices[*].message.content[*].image` 解析。
  - 如果响应包含 `task_id`，生成图记录会保留 task_id，方便后续定位生成任务。
- `_dashscope_sdk_generate()` 改为复用统一解析 helper。
- 新增轻量 `_object_get()`，兼容 dict、DashScope `DictMixin` 对象和普通对象属性。

验证：

- 新增 TDD 覆盖：
  - DashScope 响应为 `output.results` URL 结构时能正确解析 2 张图。
  - 既有 DashScope Provider fake SDK 出图测试继续通过。
  - 生成失败仍返回清晰错误。
- 关联测试：`PYTHONPATH=. pytest tests/test_harness.py tests/test_server.py tests/test_renderer.py -q`：109 passed。
- 全量回归：`PYTHONPATH=. pytest tests -q`：254 passed，用时 16.13s。

当前限制：

- 本版仍未主动发起真实通义万相生成请求；它先把返回结构兼容性做稳。
- 下一步可以在页面上用一张参考图做一次真实生成 smoke test，验证账号额度、模型权限和实际图片下载链路。

## v0.3.72 - 通义万相 Provider 诊断增强

日期：2026-06-25

阶段目标：

- 继续推进“真实好图衍生生成”主线，把图像生成 Provider 从“只显示已配置”升级为“可诊断是否真的具备运行条件”。
- 针对当前 `.env` 允许复用 `QWEN_API_KEY` 调用通义万相的配置，明确展示 key 来源和 DashScope SDK 可用性。

已完成：

- `DashScopeImageGenerationProvider.healthcheck()` 新增：
  - `ready`：同时具备 API key、模型和 SDK 时才为真。
  - `api_key_source`：区分使用 `IMAGE_GENERATION_API_KEY` 还是复用 `QWEN_API_KEY`。
  - `sdk_available`：检查 `dashscope.aigc.image_generation` 是否可导入。
- `ImageGenerationProviderFactory` 保留现有兼容逻辑：
  - 优先使用 `IMAGE_GENERATION_API_KEY`。
  - 没有独立生成 key 时复用 `QWEN_API_KEY`，并把来源写入诊断。
- 试新页“生成 Provider 诊断”展示：
  - provider、configured、ready、model、endpoint。
  - DashScope 专属的 `api_key_source` 与 `sdk_available`。
- “检查生成 Provider”按钮返回的同步消息也包含 readiness 字段，方便直接定位 SDK、key 或模型配置问题。

验证：

- 新增 TDD 覆盖：
  - Factory 创建 DashScope Provider 时能报告 `api_key_source=QWEN_API_KEY`。
  - 当前 Python 环境缺少 DashScope SDK 时，Provider 健康检查不会导致服务启动失败，而是返回 `sdk_available=False`。
  - 试新页能展示 DashScope readiness。
  - Server 诊断消息包含 `api_key_source` 和 `sdk_available`。
- 定向测试：4 passed。
- 关联测试：`PYTHONPATH=. pytest tests/test_harness.py tests/test_renderer.py tests/test_server.py -q`：107 passed。
- 全量回归：`PYTHONPATH=. pytest tests -q`：253 passed，用时 16.30s。

当前限制：

- 本版只增强真实生成链路的诊断，不主动发起通义万相扣费生成调用。
- 真正生成参考图仍需要在试新 `derive` 模式下点击“生成衍生参考图”，并通过二次 VLM 审核与人工确认后才能同步飞书。

## v0.3.71 - Human Gold 样本进入 RAG

日期：2026-06-25

阶段目标：

- 把 Eval 页确认过的 `human_gold / reviewed` 真实样本直接沉淀为 RAG 文档。
- 让价值观判断、审核解释和内容发散能检索到真实业务样本，而不是只依赖静态价值观、历史样本或某次保存动作写入的 facts memory。

已完成：

- 新增 `_harness_gold_rag_documents()`：
  - 扫描当前国家 Harness Gold Dataset。
  - 只收录 `source=real`、`label_source=human_gold`、`label_status=reviewed` 的样本。
  - 不把 `ai_silver / pending_review` 当作可信事实。
- Human Gold RAG 文档包含：
  - 主体、运营 tag、JS 分类、等级。
  - 位置、开图率、完成率、平均完成时长。
  - 色彩氛围、构图环境、价值观标签、风险标签、人工备注。
- 文档 source_type 为 `harness_gold_sample`，document_id 形如 `FR_HARNESS_GOLD_fr-real-001`，chunk citation 可追溯到具体样本。
- `_rag_documents()` 自动纳入 human gold 样本文档，与四层 memory、静态价值观、历史样本和审核规则共同参与召回。

验证：

- 新增 TDD 覆盖：
  - `human_gold / reviewed` CSV 样本会生成 `harness_gold_sample` RAG 文档。
  - 文档文本包含等级、开图率、完成率、价值观标签等业务证据。
  - RAG answer 能引用 `FR_HARNESS_GOLD_fr-real-001#chunk-1`。
- `PYTHONPATH=. pytest tests -q`：250 passed，用时 15.61s。

当前限制：

- 只有人工确认后的 `human_gold` 样本进入 RAG；当前法国 5 条样本仍是 `ai_silver / pending_review`，需要你抽查确认后才会成为 RAG 可信事实。

## v0.3.70 - Gold Dataset 行内编辑业务指标

日期：2026-06-24

阶段目标：

- 补齐 v0.3.69 的行级提示闭环，让运营不只能看到缺什么，还能直接在样本行里补齐业务指标。
- 减少整理真实样本时在批量入口和表格之间来回切换。

已完成：

- Gold Dataset 工作台每行新增业务指标输入：
  - `position`
  - `open_rate`
  - `completion_rate`
  - `avg_finish_time`
- 复用原有“保存”按钮：
  - 保存 Gold Label 时同步写入业务指标。
  - 旧表单不传指标时保留原指标，不破坏既有流程。
- Server `/save_harness_gold_label` 透传业务指标字段。
- Agent `update_harness_gold_label()` 支持更新业务指标，并写入 Harness CSV。
- 行内输入采用紧凑两列布局，仍放在“标注状态”列内，不新增表格列。

验证：

- 新增 TDD 覆盖：
  - Agent 保存 Gold Label 时能同步更新业务指标。
  - Server action 能接收并保存业务指标。
  - Eval 页渲染业务指标输入框。
- `PYTHONPATH=. pytest tests -q`：248 passed，用时 15.08s。

当前限制：

- 输入值仍按文本写入 CSV，再由 Harness CSV loader 转成数字；页面暂未做前端格式校验。

## v0.3.69 - Gold Dataset 行级业务指标状态

日期：2026-06-24

阶段目标：

- 延续 v0.3.68 的业务指标覆盖率，让运营不仅能看到总览，还能在每一行样本上看到具体缺什么。
- 降低整理 30-50 张真实拼图样本时的排查成本。

已完成：

- Gold Dataset 工作台每行新增业务指标状态：
  - 指标齐全时显示“业务指标齐全”。
  - 缺失时显示“缺业务指标：position、open_rate、completion_rate、avg_finish_time”等具体字段。
- 状态放在既有“标注状态”列内，不新增表格列，避免加宽页面。
- 增加轻量 CSS 区分缺失状态和齐全状态。

验证：

- 新增 TDD 覆盖：
  - 缺业务指标的样本行会展示具体缺失字段。
  - 业务指标齐全的样本行会展示“业务指标齐全”。
  - 既有 silver label 勾选和 Gold Dataset 页面继续可用。
- `PYTHONPATH=. pytest tests -q`：247 passed，用时 14.94s。

当前限制：

- 行级状态目前只提示业务指标缺失，不提供行内编辑这些指标；补指标仍通过批量登记入口按图片路径更新。

## v0.3.68 - Harness 业务指标覆盖率

日期：2026-06-24

阶段目标：

- 让 Eval 页不只看 Gold Label 完成率，也能看到真实业务指标是否补齐。
- 帮助运营整理 30-50 张真实拼图样本时，快速发现哪些样本缺开图率、完成率、平均完成时长或分发位置。

已完成：

- `harness_gold_coverage()` 新增业务指标覆盖统计：
  - `完整业务指标样本数`
  - `业务指标完成率`
  - `缺失业务指标摘要`
- 业务指标完整性判断覆盖：
  - `position`
  - `open_rate`
  - `completion_rate`
  - `avg_finish_time`
- Eval 页 Gold Dataset 工作台新增覆盖率卡片：
  - 展示业务指标完成率。
  - 展示缺失业务指标摘要。
- 覆盖率卡片 CSS 改成自适应网格，避免新增卡片后页面拥挤。

验证：

- 新增 TDD 覆盖：
  - 两条真实样本中只有一条补齐业务指标时，业务指标完成率显示为 50%。
  - 缺失摘要能指出 `open_rate / completion_rate / avg_finish_time` 等缺失字段。
  - Eval 页展示业务指标完成率。
- `PYTHONPATH=. pytest tests -q`：246 passed，用时 14.99s。

当前限制：

- 业务指标缺失目前按 `0` 识别；如果业务上确实存在合法 0 值，需要后续改成更细的空值标记。
- 页面只展示覆盖率和缺失摘要，还没有逐行高亮缺失业务指标；后续可以在 Gold Dataset 表格中补行级状态。

## v0.3.67 - 批量真实样本支持业务指标

日期：2026-06-24

阶段目标：

- 补齐 v0.3.66 的真实样本登记短板，让批量入口不仅能录入图片和等级，也能录入位置、开图率、完成率、平均完成时长等业务指标。
- 让 30-50 张真实样本更适合作为 Harness 评测集，而不只是图片展示集。

已完成：

- 扩展 Eval 页批量登记格式：
  - 继续支持 `等级 图片绝对路径`。
  - 继续支持 `图片绝对路径,等级,分类`。
  - 新增支持 `图片绝对路径,等级,分类,位置,开图率,完成率,平均完成时长,运营tag,主体`。
- Agent 解析逻辑会把可选业务字段写入 Harness CSV：
  - `position`
  - `open_rate`
  - `completion_rate`
  - `avg_finish_time`
  - `operation_tag`
  - `subject`
- Eval 页 placeholder 与说明文案更新，明确展示业务指标格式。
- `docs/harness_gold_samples_template.csv` 增加批量粘贴格式示例。

验证：

- 新增 TDD 覆盖：
  - 批量文本登记能写入位置、开图率、完成率、平均完成时长、运营 tag 和主体。
  - 旧的图片+等级、图片+等级+分类格式继续兼容。
  - Eval 页展示业务指标提示。
- `PYTHONPATH=. pytest tests -q`：245 passed，用时 14.68s。

当前限制：

- 批量入口仍不负责导入真实分发日期、素材来源细分或历史分发渠道；这些字段如果要进入 Harness，需要下一步扩展 CSV schema 或从历史表导入。
- 文本批量登记依赖本机绝对路径，图片移动后仍会失效。

## v0.3.66 - Harness 真实样本批量登记入口

日期：2026-06-24

阶段目标：

- 把“用户提供真实图片路径 + 人工等级，Agent 登记进 Harness”的流程产品化。
- 降低后续补 30-50 张真实拼图样本的操作成本，不再依赖聊天中由 Codex 手动登记。
- 保持成本边界：登记真实样本默认不调用模型，只有运营显式勾选后才立即 AI 预标注。

已完成：

- Agent 新增 `register_harness_real_samples_from_text()`：
  - 支持从多行文本批量解析真实样本。
  - 支持格式：`A /Users/.../image.png`、`/Users/.../image.png A`、`/Users/.../image.png,S,landscape`。
  - 跳过空行和 `#` 注释行。
  - 复用 v0.3.65 的 `local_image_path` 去重逻辑。
- Server 新增 `/register_harness_real_samples`：
  - 成功后留在 Eval 页并显示登记条数和 dataset 路径。
  - 可选 `auto_prelabeled=1`，登记后立即调用真实视觉 LLM 做 silver label。
  - 预标注失败不会回滚登记结果，会把失败原因显示在页面上。
- Eval 页 Gold Dataset 工作台新增“批量登记真实样本”表单：
  - textarea 可直接粘贴多行图片路径和等级。
  - 勾选项明确标注“登记后立即 AI 预标注”。
  - 页面说明图片只保存本机路径，不提交进 Git。

验证：

- 新增 TDD 覆盖：
  - Agent 能从粘贴文本解析并登记两条真实样本。
  - Server 能处理批量登记 action。
  - Server 勾选自动预标注时会串联调用 AI 预标注。
  - Eval 页展示批量登记入口。
- `PYTHONPATH=. pytest tests -q`：244 passed，用时 22.13s。

当前限制：

- 目前批量入口接收的是本机绝对路径，不负责上传/拷贝图片文件；图片移动后路径会失效。
- 真实业务指标（开图率、完成率、位置等）仍需后续补充导入入口；本版只解决“图片 + 等级”的最小真实样本闭环。

## v0.3.65 - 真实样本接入与视觉调用证书修复

日期：2026-06-24

阶段目标：

- 接收用户提供的 5 张法国真实拼图候选图及人工等级，进入 Harness 真实样本工作流。
- 修复本机 Python 调用 Qwen/OpenAI 视觉接口时可能出现的 SSL 证书校验失败。
- 让 Harness AI silver label 的主体字段也遵守运营短名习惯，避免长句主体进入 Gold Dataset 工作台。

已完成：

- 已将 5 条法国真实样本登记到本机 Harness gold dataset：
  - `fr-real-20260623-01`：海滩野餐，A。
  - `fr-real-20260623-02`：鲜花手推车，A。
  - `fr-real-20260623-03`：宫廷礼服，B。
  - `fr-real-20260623-04`：薰衣草风车，S。
  - `fr-real-20260623-05`：蕾丝桌旗，C。
- 已调用真实 Qwen 视觉链路为这 5 条样本补充 `ai_silver / pending_review` 的主体、色彩氛围和构图环境，等待人工抽查确认。
- 真实样本登记新增图片路径去重：
  - 同一 `local_image_path` 重复登记时更新原样本，不再新增重复记录。
  - 本机 CSV 已清理重复行，避免 Harness 指标被同一张图重复计算。
- `vision_llm.py` 的 OpenAI/Qwen urllib 调用新增 HTTPS context：
  - 优先使用 `certifi` 证书包。
  - 不存在 `certifi` 时回退系统默认 SSL context。
- Harness 预标注接入主体短名规则：
  - 模型输出长句时，写入 `subject/gold_subject` 前先压缩为运营可读短名。
  - 新增法国常见短名：薰衣草风车、鲜花手推车、海滩野餐、蕾丝桌旗、宫廷礼服、古典喷泉、法式花园。

验证：

- 新增 TDD 覆盖：
  - Qwen 视觉传输会带可用 SSL context，并保持长超时配置。
  - 同一真实图片路径重复登记时不会产生重复样本。
  - Harness AI silver label 会把长视觉主体压缩为不超过 8 字的运营短名。
- 针对性测试：`PYTHONPATH=. pytest tests/test_agents.py::test_agent_ai_silver_label_compacts_long_visual_subject tests/test_agents.py::test_agent_ai_prelabeled_real_samples_as_silver_labels tests/test_vision_llm.py::test_qwen_transport_uses_configurable_long_timeout_and_ssl_context -q`：3 passed。

当前限制：

- 这 5 张真实图目前只存在本机 Harness CSV，图片仍引用用户桌面路径，没有提交进 Git。
- AI 预标注仍是 silver label；需要运营在 Eval 页抽查后确认，才能进入 `human_gold` facts memory。
- 本机 CSV 已按图片路径去重；后续新增样本会自动避免同一路径重复登记。

## v0.3.64 - RAG 人工反馈接入本地 Rerank

日期：2026-06-24

阶段目标：

- 把 v0.3.63 聚合出来的 RAG feedback `net_score` 真正接入本地 RAG 排序。
- 让运营标记“有用/无用”的依据，能影响后续相同 chunk 在本地 fallback 检索中的排序。
- 保持远程 provider 的边界清晰：未开启远程调用时使用 feedback-aware 本地 rerank；开启远程 rerank 时尊重远程结果。

已完成：

- 新增 `FeedbackAwareRerankProvider`：
  - 包装现有 rerank provider。
  - 在基础 rerank 分数上叠加 `chunk_id -> net_score` 的人工反馈 bias。
  - provider 名称带 `+feedback`，方便 trace 中识别。
- Agent RAG 检索接入 feedback：
  - `rag_feedback_scores()` 将聚合摘要转成 `chunk_id -> net_score`。
  - `value_audit_rag_answer()` 在本地/降级 RAG 路径中自动注入 feedback-aware rerank。
  - feedback 只影响已召回候选的排序，不会把完全无关 chunk 强行拉入候选集。

验证：

- 新增 TDD 覆盖：
  - feedback-aware rerank 会提升被标记有用的 chunk。
  - Agent 的 `value_audit_rag_answer()` 会使用人工 feedback 改变本地 RAG citation 顺序。
  - RAG feedback 聚合和 Runtime 展示继续可用。
- `PYTHONPATH=. pytest tests -q`：239 passed，用时 21.63s。
- 浏览器验证：日本 Runtime 页正常加载，`RAG 人工反馈` 与 Rerank 信息存在；页面 `scrollWidth == clientWidth == 1280`，无横向溢出。

当前限制：

- feedback bias 目前只接入本地 rerank/fallback 路径；远程 rerank 开启后不混入本地 bias。后续如果要做线上融合，可以在 Harness 中先比较“远程原始排序 vs feedback 融合排序”的指标。

## v0.3.63 - RAG 反馈聚合与 Runtime 摘要

日期：2026-06-24

阶段目标：

- 延续 v0.3.62 的 RAG 依据反馈能力，把分散的短期反馈聚合成可读的调优信号。
- 让 Runtime 的“价值观与审核 RAG”区域展示反馈概况，说明哪些 chunk 更常被运营认为有用或无用。
- 为后续 rerank 偏好、Harness 失败复盘和长期记忆沉淀做数据基础。

已完成：

- 新增 `rag_feedback_summary()`：
  - 统计当前国家 active 的 `rag_citation_feedback` 短期记忆。
  - 输出总反馈数、有用数、无用数。
  - 按 chunk 聚合 `useful_count / not_useful_count / net_score`。
  - 保留最近的人工备注摘要。
- `value_audit_rag_summary()` 接入 feedback summary：
  - Runtime 页读取同一份 RAG 摘要时可同时获得反馈聚合信号。
- Runtime 页面新增“RAG 人工反馈”卡片：
  - 无反馈时显示“暂无反馈”。
  - 有反馈时展示 useful / not_useful 总数与 top chunk 净分。

验证：

- 新增 TDD 覆盖：
  - 多条 RAG feedback 会按 chunk 聚合。
  - Runtime 页面能展示 RAG 人工反馈摘要。
- `PYTHONPATH=. pytest tests -q`：237 passed，用时 24.51s。
- 浏览器验证：日本 Runtime 页正常加载，`RAG 人工反馈` 卡片存在；页面 `scrollWidth == clientWidth == 1280`，无横向溢出。

当前限制：

- 聚合结果目前只展示，不直接改变 rerank 分数；下一步可以把 `net_score` 接入本地 rerank provider，作为人工反馈权重。

## v0.3.62 - RAG 依据有用性反馈进入短期记忆

日期：2026-06-24

阶段目标：

- 继续补强 RAG + HITL 闭环，让运营不只是查看 RAG 依据，还能标记依据是否有用。
- 将 RAG 依据反馈写入四层 Memory 中的短期记忆，为后续 rerank 调优、Harness 失败复盘和人工知识沉淀做准备。
- 保持反馈动作不调用模型、不产生额外费用。

已完成：

- 新增 `record_rag_citation_feedback()`：
  - 支持记录 chunk_id、usefulness、note 和 task_type。
  - feedback 类型限定为 `useful / not_useful`。
  - 写入 `working` 层，memory_type 为 `rag_citation_feedback`。
- Runtime memory debug 增强：
  - `memory_debug()` 现在返回结构化 `payload`，便于直接检查反馈内容。
- 服务端新增 `/record_rag_feedback`：
  - 从试新页提交 RAG 依据反馈。
  - 成功后留在试新页并显示反馈记录消息。
- 试新页 RAG 明细表新增反馈入口：
  - 每条依据可标记“有用”或“无用”。
  - 支持填写简短原因。

验证：

- 新增 TDD 覆盖：
  - Agent 能把 RAG 依据反馈写入短期 memory。
  - 服务端 action 能记录反馈并返回试新页。
  - 试新页 RAG 明细表显示有用/无用反馈按钮。
- `PYTHONPATH=. pytest tests -q`：235 passed，用时 21.81s。
- 浏览器验证：日本试新页正常加载，价值观大师入口存在；页面 `scrollWidth == clientWidth == 1280`，无横向溢出。

当前限制：

- 反馈目前只进入短期记忆，还没有参与 rerank 分数；后续可以把多次人工反馈聚合成长期 rerank 偏好或 Harness 指标。

## v0.3.61 - 试新页价值观 RAG 依据明细

日期：2026-06-24

阶段目标：

- 延续 v0.3.60 的 RAG 可溯源能力，把 `系统RAG召回` 从纯 ID 文案升级成运营可读的依据明细。
- 让运营在试新提需页审价值观大师结果时，直接看到 chunk 来源、父文档、标题和正文摘要。
- 保持页面渲染不触发新模型调用、不产生额外费用。

已完成：

- 新增 `value_match_rag_citation_details()`：
  - 从 `value_match` 中解析 `#chunk-` citation。
  - 从已保存的 RAG index 读取明细。
  - 返回引用ID、知识来源、父文档、标题和内容。
- 试新提需页新增“价值观 RAG 依据明细”：
  - 当价值观大师结果包含 RAG citation 时展示。
  - 多条试新行会自动去重相同 chunk。
  - 无 citation 时不展示空表，避免干扰日常提需。

验证：

- 新增 TDD 覆盖：
  - Agent 能把 value_match 中的 citation ID 解析成 RAG 明细。
  - 试新页能展示“价值观 RAG 依据明细”与 chunk 内容。
- `PYTHONPATH=. pytest tests -q`：233 passed，用时 22.12s。
- 浏览器验证：日本试新页正常加载，价值观大师入口存在；页面 `scrollWidth == clientWidth == 1280`，无横向溢出。

当前限制：

- 明细表目前是只读展示；后续可以增加“一键标记本条依据有用/无用”，把反馈写入 Memory 或 Harness 失败样本，用于 RAG rerank 调优。

## v0.3.60 - 价值观大师 RAG 召回显性溯源

日期：2026-06-24

阶段目标：

- 继续补强 RAG 主线，让提需时的价值观判断不仅依赖 LLM 结论，还能看到本次系统实际召回的知识块。
- 避免模型没有返回 `citation_ids` 时，页面只显示“未提供可溯源引用”，导致运营无法审查依据。
- 保持本轮不新增模型调用、不增加费用，只增强结果可解释性。

已完成：

- 价值观大师调用真实视觉 LLM 前仍使用现有 RAG：
  - 召回日本/法国市场价值观。
  - 召回审核规则、版权/IP、文字水印、文化混淆和 AI 质量风险。
  - 召回 Memory 中已确认的长期规则与 facts。
- 新增系统级 RAG trace：
  - 当 LLM 返回结果后，系统会把本次 RAG 实际召回的 `#chunk-` 引用 ID 追加到 `value_match`。
  - 即使模型没有主动返回 `citation_ids`，运营也能看到“系统RAG召回：...”。
  - 不伪造价值观判断，不替代 LLM 输出，只补充可审计证据。

验证：

- 新增 TDD 覆盖：
  - 当 LLM 省略 citation 时，价值观大师仍追加系统 RAG 召回 ID。
  - 保持原有 prompt 会把 RAG 引用依据传给 LLM。
  - 原有价值观大师服务端路径仍可用。
- `PYTHONPATH=. pytest tests -q`：231 passed，用时 16.38s。
- 浏览器验证：日本试新页正常加载，价值观大师入口存在；页面 `scrollWidth == clientWidth == 1280`，无横向溢出。

当前限制：

- 这版只把 chunk ID 显示在价值观匹配结果中；后续可以在试新页面增加可展开的 RAG 明细表，直接展示 chunk 标题、来源类型、父文档和正文摘要。

## v0.3.59 - AI Silver Label 逐条勾选确认

日期：2026-06-23

阶段目标：

- 修复 v0.3.58 中 `ai_silver` 只能批量确认的问题。
- 让 Harness Gold Dataset 工作台更符合 HITL 审核：运营逐条抽查、逐条勾选，再晋升为 `human_gold`。
- 保持本轮不调用模型、不产生费用，只优化人工确认链路。

已完成：

- Gold Dataset 工作台新增逐条确认复选框：
  - 仅对 `ai_silver / pending_review` 样本显示确认勾选项。
  - 确认表单继续支持填写 `reviewer_note`。
  - 未勾选的 silver label 不会被本次确认动作晋升。
- 服务端 `/approve_harness_silver_labels` 支持接收 `sample_id` 列表：
  - 勾选样本时只确认选中项。
  - 保留旧的无选择调用兼容路径，避免历史测试和已有入口断裂。
- Agent 层确认能力回归验证：
  - 只确认选中样本。
  - 未选中的 `ai_silver / pending_review` 继续等待人工审核。
  - 确认后的样本仍写入 `facts` memory，作为可信评测标准答案。

验证：

- TDD 红绿验证：
  - 服务端 action 会传递选中的 `sample_id`。
  - Eval 页面会渲染 `approve-silver-form` 和逐条 checkbox。
  - Agent 只晋升被选中的 silver 样本。
- `PYTHONPATH=. pytest tests -q`：230 passed，用时 21.28s。

当前限制：

- 这版解决“选择确认”，但还没有做逐字段差异高亮；后续可以把 AI silver 与人工修正差异显示出来，形成更强的审核证据链。

## v0.3.58 - AI Silver Label 人工确认与 Human Gold 晋升

日期：2026-06-23

阶段目标：

- 补齐 `ai_silver / pending_review` 到 `human_gold / reviewed` 的 HITL 闭环。
- 让运营抽查通过的 AI 预标注进入 facts memory，作为可信 Harness 标准答案。
- 保持确认动作不调用模型、不产生费用。

已完成：

- 新增 `approve_harness_silver_labels()`：
  - 批量确认当前国家的 `ai_silver / pending_review` 样本。
  - 只确认字段完整的真实样本：等级、主体、色彩、构图、价值观标签必须存在。
  - 确认后将 `label_source` 改为 `human_gold`，`label_status` 改为 `reviewed`。
  - 追加人工审核备注。
  - 写入 `harness_gold_label` HITL 记录，并进入 `facts` 层结构化记忆。
- 页面新增确认入口：
  - Gold Dataset 工作台新增“确认 AI 预标注为 human_gold”按钮。
  - 支持填写 `reviewer_note`，默认为“人工抽查通过”。
  - 页面继续展示 `label_source / label_status`，方便区分 silver 与 gold。
- 服务端新增 `/approve_harness_silver_labels` action：
  - 只修改 CSV 和 memory。
  - 不调用 VLM、不调用 RAG、不触发图像生成。

验证：

- 新增 TDD 覆盖：
  - `ai_silver / pending_review` 可晋升为 `human_gold / reviewed`。
  - 晋升后写入 facts memory。
  - 服务端 action 可用。
  - Eval 页面显示确认按钮和审核备注输入。
- `PYTHONPATH=. pytest tests -q`：227 passed，用时 24.54s。
- 浏览器验证：法国 Eval 页显示确认按钮、`reviewer_note` 输入框和 `/approve_harness_silver_labels` 表单；页面 `scrollWidth == clientWidth == 1280`。

当前限制：

- 这版确认是批量确认当前国家所有待审核 silver 样本；后续可加逐条 checkbox 审核与差异高亮。
- Human gold 的质量仍取决于运营是否真实抽查，系统不会替代人工责任。

## v0.3.57 - 真实样本导入与 AI Silver Label 预标注

日期：2026-06-23

阶段目标：

- 调整 Gold Dataset 工作流：人工不再需要手填主体、色彩和构图，人工只提供真实图片和真实等级。
- 让 VLM 自动补充主体内容、色彩氛围、构图环境、价值观候选和风险候选，并明确标记为 `ai_silver`。
- 解决真实 Qwen 返回字符串标签时被拆成单字的问题。

已完成：

- Harness 样本字段扩展：
  - 新增 `label_source`，区分 `manual_grade`、`ai_silver`、`human_gold`、`synthetic_demo`。
  - 新增 `label_status`，区分 `needs_ai_prelabeled`、`pending_review`、`reviewed`、`demo_only`。
  - CSV 读写兼容旧字段，旧数据缺少新列时不会崩溃。
- 新增真实样本导入能力：
  - `register_harness_real_samples()` 支持按图片路径、国家、等级导入真实样本。
  - 人工等级会写入 `gold_grade`，但主体/色彩/构图保持待 AI 预标注。
- 新增 AI silver label：
  - `auto_prelabeled_harness_samples()` 显式调用真实视觉 LLM。
  - 自动填入 `gold_subject`、`gold_color_mood`、`gold_composition`、`gold_value_labels`、`gold_risk_labels`。
  - 结果标记为 `ai_silver / pending_review`，只写入感知记忆，不写入 facts，避免把未抽查内容当人工事实。
  - 页面新增“AI 自动预标注”按钮，打开页面不会自动调用模型。
- 真实数据落地：
  - 已将用户提供的 5 张法国真实图片导入 Harness 数据集。
  - 人工等级映射：1=A、2=A、3=B、4=S、5=C。
  - 已调用真实 Qwen VLM 完成 5 条 AI silver label 预标注。
- 解析修复：
  - `QwenVisionLLMClient.analyze()` 和 `OpenAIVisionLLMClient.analyze()` 复用 `_tuple_field()`。
  - 当模型把 `risk_tags`、`culture_elements` 或 `prompt_keywords` 返回成字符串时，不再拆成单字。

验证：

- 新增 TDD 覆盖：
  - 真实样本导入只要求人工等级，不要求人工主体/色彩/构图。
  - AI 预标注会生成 `ai_silver / pending_review`。
  - 页面展示 “AI 自动预标注” 按钮与标注状态。
  - 服务端 `/auto_prelabeled_harness_gold` action 可用。
  - Qwen analyze 对字符串标签保持完整短语。
- `PYTHONPATH=. pytest tests -q`：224 passed，用时 23.16s。
- 真实 VLM smoke：5 张法国图片全部预标注成功，风险标签已恢复为完整短语。

当前限制：

- AI silver label 仍需人工抽查后才能作为 `human_gold` 用于严肃业务准确率证明。
- 当前价值观候选由 VLM 语义 + 规则映射生成，后续可接 RAG value ideation 进一步提升标签质量。
- 已导入的 5 张样本都是法国市场；日本样本仍需要按同样流程补充。

## v0.3.56 - Gold Dataset 工作台与 HITL 标准答案回流

日期：2026-06-23

阶段目标：

- 把“需要 30-50 张真实拼图 + gold label”落到可操作页面，而不是让运营手改 CSV。
- 让 Harness 的真实业务评测依赖人工标准答案，避免把历史主体字段或模型输出误当 gold label。
- 将人工确认的 gold label 同步进入事实记忆，为后续 RAG/价值观判断提供可信依据。

已完成：

- Harness Gold Dataset 工作台：
  - Agent 评测页新增 `Gold Dataset 工作台`。
  - 展示真实样本数、完整 gold 样本数、gold 完成率和缺失字段摘要。
  - 每条真实样本可直接编辑 `gold_grade`、`gold_subject`、`gold_color_mood`、`gold_composition`、`gold_value_labels`、`gold_risk_labels` 和人工备注。
  - 新增“生成 Gold 骨架CSV”按钮，可从当前真实历史图片生成待标注数据集。
- Gold label 保存闭环：
  - 新增 `update_harness_gold_label()`，保存单条样本级人工标准答案。
  - 新增 `ensure_harness_gold_dataset()`，没有 CSV 时可生成标注骨架。
  - 新增 `harness_gold_coverage()`，用于页面和后续 Harness 前置检查。
  - 保存后写入 `harness_gold_label` HITL 记录，并作为 `facts` 层结构化记忆进入 memory/RAG。
- 数据真实性修正：
  - 真实历史图片不再默认把 `subject_tag` 当成 `gold_subject`。
  - `gold_subject`、色彩、构图和价值观标签必须由人工确认后才算完整 gold label。
  - 合成 demo 仍保留为页面展示和边界测试，不用于证明业务准确率。
- 页面体验：
  - Gold 工作台使用独立局部样式，避免整页横向溢出。
  - 复用现有 Harness 页面和失败复盘区，不改变路由结构。

验证：

- 新增 TDD 覆盖：
  - 编辑 gold label 会更新 CSV，并写入 facts memory。
  - 没有 gold CSV 时可从真实历史图片生成骨架。
  - `/save_harness_gold_label` 与 `/export_harness_gold_skeleton` 路由可用。
  - Eval 页面展示 Gold Dataset 工作台与保存表单。
- `PYTHONPATH=. pytest tests -q`：219 passed，用时 24.74s。
- 浏览器验证：`http://127.0.0.1:5199/?view=eval` 出现 Gold Dataset 工作台，保存/生成骨架表单存在；页面 `scrollWidth == clientWidth == 1280`，无整页横向溢出。

当前限制：

- 这版提供标注与回流工具，但业务准确率仍需要你补齐 30-50 张真实拼图样本及人工 gold label 后才能证明。
- 当前 gold 完成率按必填字段统计，未对人工标注质量做二次审核；后续可增加抽检队列和冲突检测。
- 保存 gold label 不调用 VLM、RAG 或图像生成模型，不产生模型费用。

## v0.3.55 - Harness 显式执行、费用门禁与真实 VLM Gold 评测

日期：2026-06-22

阶段目标：

- 修复打开 Agent 评测页面可能触发远程 RAG 或批量图像生成的费用风险。
- 让 `trial_parse_eval` 和 `value_match_eval` 真正比较模型输出与人工 gold label，而不是复述样本字段后自评分。
- 保留离线 Harness 预览与本地 RAG 引用，同时把真实模型执行改为人工显式动作。

已完成：

- Harness 执行模式：
  - `offline`：页面默认模式，只运行本地规则、本地 RAG 和字段检查。
  - `real_vlm`：显式调用真实视觉 LLM，评测主体、色彩氛围、构图环境和价值观匹配。
  - `real_vlm_and_generation`：在真实 VLM 基础上额外执行付费图像生成。
  - `generation_only`：供程序化评测使用的独立生成模式。
- 费用门禁：
  - GET 渲染评测页不再保存新 run、不调用视觉 LLM、不调用远程 embedding/rerank、不调用图像生成。
  - 离线预览仍通过本地 BM25/向量 fallback 生成可溯源 RAG 引用。
  - 页面新增“运行真实 VLM Harness”按钮。
  - “包含付费生成评测”使用独立 checkbox，默认不勾选。
- 真实 VLM case：
  - 每个真实样本只解析一次并缓存语义结果，供解析、价值观和审核 case 复用。
  - `trial_parse_eval` 使用模型输出生成三段式描述，并与 `gold_subject`、`gold_color_mood`、`gold_composition` 分别评分。
  - `value_match_eval` 使用当前图像解析、RAG 规则和真实价值观 LLM 输出，对 `gold_value_labels` 评分。
  - `audit_eval` 将 VLM 风险标签与规则审核结果合并后对照 `gold_risk_labels`。
- Harness run 增加 `country` 和 `execution_mode`，旧 SQLite run JSON 可向后兼容读取。
- Dashboard 展示执行模式；优先展示最近一次已保存 run，没有历史 run 时才生成不落库的离线预览。
- 指标新增主体识别准确率、色彩氛围准确率和构图环境准确率。
- 工具调用正确率与 Step Efficiency 改为由实际执行 case 聚合，不再硬编码 100%；跳过的远程步骤不进入分母。
- Dashboard 区分 `0%` 与“未评测”，未授权生成属于正常 skipped，不再污染失败样本和失败分类。
- 复盘区先展示真实失败，再补充尚未执行模型的待评测 case，确保完整 gold 样本仍保留 HITL 入口。
- Qwen 网络超时改为 `QWEN_TIMEOUT_SECONDS` 可配置，默认 90 秒，避免价值观长提示在原 30 秒限制下误判失败。
- 兼容 Qwen 将 `visual_evidence`、`citation_ids` 或 `risk_tags` 返回为字符串的情况，不再丢失真实证据。

验证：

- TDD 覆盖真实 VLM 输出与 gold label 对照、页面只读、显式运行及生成 opt-in。
- `PYTHONPATH=. pytest tests -q`：214 passed，用时 20.56s。
- 单条真实寿司图片 smoke test：`real_vlm` 模式完成 6 个 case；主体/色彩/构图解析成功，价值观结论成功，远程 embedding 与 rerank 各 1 次且无降级，图像生成保持未授权跳过。

当前限制：

- 真实业务效果仍取决于 30-50 张人工标注真实拼图验证集；当前 5 条真实图片只能验证链路，不能证明稳定准确率。
- 勾选生成评测会按真实图片样本产生通义万相费用，正式运行前应控制数据集规模。
- Gold label 文本评分当前采用严格包含匹配，后续可增加同义词归一化或 LLM-as-Judge，但必须保留人工抽查。

## v0.3.54 - 通义万相真实好图衍生与人工放行门禁

日期：2026-06-22

阶段目标：

- 将好图衍生从“预留生成接口”升级为真实参考图生成，且不把普通文生图冒充图生图。
- 复用现有 Qwen 密钥，保持 `.env` 不入库。
- 建立“真实生成 -> VLM 二次解析 -> 风险审核 -> 运营确认 -> 飞书附件”的完整门禁。

已完成：

- 接入官方 `dashscope==1.25.23` SDK 的 `ImageGeneration`：
  - 默认模型改为 `wan2.6-image`。
  - 请求内容包含文本指令和真实参考图，本地图片由 SDK 临时上传到 DashScope OSS。
  - 支持下载 DashScope 返回的临时图片 URL 并保存为本地生成图。
  - `IMAGE_GENERATION_API_KEY` 未设置时复用现有 `QWEN_API_KEY`。
- 删除旧的 `text2image` 异步请求实现；旧实现虽然携带 `reference_image` 字段，但服务端不会把它作为参考图处理。
- 生成图治理：
  - `DemandRow` 增加 `generation_review_status` 和 `human_approved`。
  - VLM 与审核规则通过后，生成图仍保持不可同步。
  - 运营点击“确认生成图可同步”后，附件资格才变为 ready。
  - 未通过二次审核或未人工确认的生成图会在飞书同步前被拦截。
- 配置与依赖：新增 `requirements.txt`，更新 `.env.example` 与 README，不提交 `.env` 或任何密钥。
- 测试隔离：RAG 引用测试使用独立 SQLite，避免本地历史 memory 改变测试召回结果。

真实验证：

- 使用已开通的通义万相账号和现有 `QWEN_API_KEY` 做最小 smoke test。
- 官方 SDK 直连 `wan2.6-image` 成功返回真实图片 URL。
- 项目自身 DashScope provider 成功生成并下载 1 张参考图，本地文件约 2.6 MB，不是 mock 占位图。
- 生成测试仅使用 1 张输出控制费用；密钥未打印、未写入 Git。
- `PYTHONPATH=. pytest tests -q`：209 passed，用时 24.54s；测试默认禁用付费 RAG/图像生成调用。

当前限制：

- 通义万相按实际调用计费；正式批量生成前仍建议在页面确认 prompt、张数与预算。
- 生成结果需要真实视觉 LLM 二次调用，因此 VLM 不可用时不会放行附件。
- 本版验证了 provider 与门禁；真实业务图仍应由运营在试新页面上传并完成最终人工确认。

## v0.3.53 - 四层 Memory 生命周期与人工治理

日期：2026-06-22

阶段目标：

- 让四层 memory 从“只追加、只展示”升级为有生命周期、可去重、可晋升、可停用的业务记忆系统。
- 明确短期上下文与长期事实的边界，避免临时状态永久污染 RAG。
- 保留人工确认与来源链，支持 Harness/HITL 的可解释回流。

已完成：

- SQLite `layered_memory` 增加：
  - `status`：`active/promoted/expired/retired`。
  - `source_memory_id`：记录晋升来源。
  - `expires_at`：TTL 到期时间。
  - `fingerprint`：规范化 payload 的 SHA-256 去重标识。
  - `human_verified`：运营人工确认标记。
  - `updated_at`：生命周期更新时间。
- 旧库迁移：
  - 启动时通过 `PRAGMA table_info` 检查并补列。
  - 为旧 memory 回填 fingerprint 和 updated_at。
  - 不删除、不重建已有业务数据库。
- 写入与过期策略：
  - 相同国家/层/类型/payload 的 active memory 返回已有 ID，不重复插入。
  - 感知记忆默认 TTL 7 天。
  - 短期记忆默认 TTL 24 小时。
  - 长期记忆和结构化事实不自动过期。
  - 读取 active memory 前自动将到期记录标记为 `expired`。
- 人工治理：
  - 感知/短期记忆可晋升为 `facts`。
  - 短期/事实可晋升为 `long_term`。
  - 晋升目标写入 `human_verified=true` 和 `source_memory_id`，来源改为 `promoted`。
  - active memory 可人工停用为 `retired`。
  - 不同来源的人工晋升不会被普通 payload 去重误合并。
- RAG 边界：
  - 只有 active memory 进入父子知识库。
  - RAG metadata 增加 memory_id、source_memory_id 和 human_verified。
- 页面与路由：
  - Memory 概览区分 active 与归档数量。
  - Memory Debug 展示 ID、状态、来源、人工确认和 RAG Ready。
  - 新增 `/promote_memory` 与 `/retire_memory` 操作及结果提示。

当前限制：

- TTL 当前使用固定默认值，尚未提供 `.env` 或页面级策略配置。
- 去重是规范化 payload 的精确去重，不是 embedding 语义去重。
- 停用/晋升后不可在页面撤销，后续可增加恢复与版本历史。
- 真实图像生成仍等待通义万相权限和付费调用确认。

验证记录：

- 去重、TTL 过期、人工晋升来源链、停用、旧 SQLite 迁移、RAG active 过滤、页面表单和 HTTP 路由测试通过。
- `PYTHONPATH=. pytest tests/test_storage_runtime.py tests/test_agents.py tests/test_renderer.py tests/test_server.py tests/test_rag.py tests/test_harness.py -q`：144 passed。
- `PYTHONPATH=. pytest tests -q`：207 passed，用时 13.18s。
- Chrome 1440x2400 页面验证：四层 active/归档计数、Memory Debug 状态列、来源链和治理表单完整显示，无页面级横向溢出；截图 `/private/tmp/puzzleops-runtime-v0353.png`。

## v0.3.52 - RAG 批处理、引用溯源与结构化价值观答案

日期：2026-06-22

阶段目标：

- 解决真实 RAG 在页面打开时逐 chunk 请求导致的高调用量和长尾延迟。
- 让价值观判断同时展示图像证据、RAG citation、风险和人工复核事项。
- 把 citation 从一串 ID 升级为运营可检查的知识来源明细。

已完成：

- Embedding 批处理：
  - `LocalEmbeddingProvider` 新增 `similarities(...)` 批量接口。
  - `DashScopeEmbeddingProvider` 支持缓存合并与每批 10 条文本请求。
  - 查询和候选文本不再逐 chunk 单独请求 embedding。
  - 批次失败时直接本地 cosine fallback，不递归重试远程请求。
- Rerank 批处理：
  - `LocalRerankProvider` 新增 `rerank_many(...)`。
  - `DashScopeRerankProvider` 一次提交全部候选文档，并按 response index 恢复分数。
  - 批量请求失败时直接执行本地规则精排，不再退回远程单条 rerank。
- 引用溯源：
  - `value_audit_rag_summary(...)` 新增 `citation_details`。
  - 每条引用包含 `chunk_id`、`parent_id`、`source_type`、`title`、`text`。
  - 多模态底座新增“引用明细”表。
- 价值观大师结构化输出：
  - 新 prompt 要求 `conclusion`、`visual_evidence`、`citation_ids`、`risk_tags`、`manual_review`、`confidence`。
  - 页面文本固定展示“结论 / 图像证据 / RAG依据 / 风险提示 / 人工复核 / 模型记录”。
  - 兼容旧模型返回的 `value_match` 和 `evidence` 字段。

真实模型验证：

- 使用现有本地 DashScope 配置运行日本知识库检索。
- `text-embedding-v3`：8 个批次，`embedding_fallbacks=0`。
- `gte-rerank-v2`：1 个批次，`rerank_fallbacks=0`。
- 返回 5 条带父文档和来源类型的 citation。

当前限制：

- Embedding 默认批次大小为 10，目前是代码默认值，后续可加入 `.env` 调优项。
- RAG 仍在同步页面请求中执行；批处理已明显减少调用，但后续还应增加结果 TTL 缓存和异步刷新。
- Citation 能证明知识依据，但价值观最终结论仍需运营人工复核。
- 真实图像生成 Provider 尚未配置，本版不伪造衍生图。

验证记录：

- RAG 批处理、批量失败禁止逐条重试、结构化价值观答案、引用明细测试通过。
- `PYTHONPATH=. pytest tests/test_rag.py tests/test_vision_llm.py tests/test_agents.py tests/test_renderer.py tests/test_harness.py tests/test_storage_runtime.py -q`：103 passed。
- `PYTHONPATH=. pytest tests -q`：200 passed，用时 13.53s。
- 真实 DashScope 验证：embedding 8 批、rerank 1 批，两个 provider 的 fallback 均为 0。
- Chrome 1440x2400 页面验证：Memory Debug、RAG 状态卡和引用明细表均在主内容区内换行，无页面级横向溢出；截图 `/private/tmp/puzzleops-runtime-v0352.png`。

## v0.3.51 - Harness Case Trace 与 Memory Debug

日期：2026-06-22

阶段目标：

- 把 Harness 从 run 级汇总推进到 case 级证据回放。
- 让四层 memory 不只显示数量，还能检查本次判断可使用的具体内容和 RAG 来源。
- 对失败样本进行业务分类，为后续版本对比和 HITL 回流提供结构化数据。

已完成：

- `HarnessCaseResult` 新增：
  - `evidence_trace`：保存视觉输入证据、RAG citation/context 和 memory evidence。
  - `failure_categories`：保存 `missing_image`、`missing_gold`、`risk_missed`、`grade_mismatch`、`provider_not_configured`、`generation_failed`、`field_incomplete` 等业务分类。
- Harness 在每次 run 开始时按国家执行一次真实 RAG 检索：
  - 同一国家的 case 复用 run 级 RAG 引用，避免逐样本重复远程调用造成费用放大。
  - 价值观 case 不再用 gold label 拼接伪证据，而是记录真实 RAG citation 和当前视觉输入字段。
- 新增 `memory_debug(country, query, limit)`：
  - 展示感知、短期、长期、事实四层 memory。
  - 标记对应 RAG source type、RAG Ready、query 命中分和创建时间。
  - 按命中分和时间排序，便于定位 Agent 实际可用记忆。
- 页面增强：
  - Agent 评测页新增“Case 证据链”和“失败分类”。
  - 多模态底座新增“Memory Debug”明细表。
- 真实运行容错：
  - 审核手册不存在、无读取权限、DOCX 损坏或 XML 异常时，降级为空文档检索器。
  - 版权/IP/商标等内置红线规则仍正常执行，不再因外部 DOCX 不可读导致页面空响应。
- 页面布局修复：
  - 为 `main`、Grid 子项和 panel 增加 `min-width:0` 约束。
  - citation、memory 文本和表格单元格支持强制换行，避免 1440px 页面被撑出横向滚动。
- README 修正过期 RAG 描述，并补充真实 provider、case trace、Memory Debug 和远程调用成本控制说明。

当前限制：

- 当前 case citation 是同国家 run 级检索结果，不是每个 case 单独远程 rerank；这是为控制 DashScope 调用成本做的明确取舍。
- Memory Debug 目前是只读视图，尚未支持废弃、合并、晋升或人工编辑 memory。
- Harness 的 `trial_parse_eval` 仍主要评估数据集字段；下一版需要保存真实 VLM 原始输出、模型名、耗时和 token/cost。
- 真实好图衍生仍依赖 `IMAGE_GENERATION_PROVIDER` 和对应 API key；未配置时继续明确显示未配置，不伪造生成结果。

验证记录：

- 新增测试先失败后通过：case evidence trace、失败分类、Memory Debug 查询与两个页面展示，共 5 项。
- `PYTHONPATH=. pytest tests/test_harness.py tests/test_agents.py tests/test_renderer.py tests/test_storage_runtime.py tests/test_rag.py -q`：89 passed。
- 审核手册无权限降级测试与页面防横向溢出 CSS 测试通过。
- `PYTHONPATH=. pytest tests -q`：194 passed，用时 14.04s。
- 真实 HTTP 页面验证：`/?view=eval` 与 `/?view=runtime` 均返回 200。
- Chrome 1440x1000 截图验证：评测页和多模态底座无页面级横向溢出；截图分别保存于 `/private/tmp/puzzleops-eval-v0351-layout.png`、`/private/tmp/puzzleops-runtime-v0351.png`。

## v0.3.50 - 真实 RAG 模型启用与四层 Memory 入库

日期：2026-06-16

阶段目标：

- 按用户要求启用真实 RAG 模型调用，而不是只保留本地 fallback。
- 继续优化四层 memory，让感知记忆、短期记忆、长期记忆和结构化事实都能进入 RAG 检索链路。

已完成：

- `.env` 本地配置已更新为真实 RAG 调用链路：
  - `RAG_EMBEDDING_PROVIDER=dashscope`
  - `RAG_EMBEDDING_MODEL=text-embedding-v3`
  - `RAG_RERANK_PROVIDER=dashscope`
  - `RAG_RERANK_MODEL=gte-rerank-v2`
  - `RAG_ENABLE_REMOTE_CALLS=true`
  - `RAG_API_KEY` 复用本地已有 Qwen/DashScope key。
- 真实模型 smoke test 已执行：
  - embedding provider：DashScope `text-embedding-v3`
  - rerank provider：DashScope `gte-rerank-v2`
  - `embedding_remote_calls=2`
  - `rerank_remote_calls=1`
  - `embedding_fallbacks=0`
  - `rerank_fallbacks=0`
- 测试环境保护：
  - 新增 `tests/conftest.py`，pytest 默认不启用远程 RAG 调用。
  - 需要测试远程 provider 的用例仍可通过 `monkeypatch` 显式开启。
- Memory 优化：
  - `build_value_audit_rag_index(...)` 现在把四层 memory 全部转成 RAG 文档：
    - 感知记忆 -> `memory_perception`
    - 短期记忆 -> `memory_working`
    - 长期记忆 -> `approved_value_rule`
    - 结构化事实 -> `fact`
  - `memory_overview(...)` 新增 `rag_ready_count`。
  - 多模态底座“四层 Memory 概览”展示每层 `RAG Ready` 数量。
- README 补充真实 RAG 模型和四层 memory 入库说明。

当前限制：

- 真实模型调用已验证，但仍建议在业务演示时控制调用次数，避免不必要成本。
- RAG embedding 缓存已持久化，rerank 结果暂未持久化。
- 四层 memory 已进入 RAG，但还未提供单独的 memory 搜索页面。

验证记录：

- `PYTHONPATH=. pytest tests/test_agents.py::test_agent_rag_documents_include_all_four_memory_layers tests/test_renderer.py::test_multimodal_runtime_page_shows_profile_candidates_and_evidence -q`：2 passed。
- 真实模型 smoke test：DashScope `text-embedding-v3` embedding 远程调用 2 次，`gte-rerank-v2` rerank 远程调用 1 次，fallback 0 次。
- `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_rag.py tests/test_harness.py tests/test_storage_runtime.py -q`：84 passed。
- `PYTHONPATH=. pytest tests -q`：187 passed，用时 305.56s。
- `find . -maxdepth 3 -type f \\( -name 'package.json' -o -name 'vite.config.*' -o -name '*.js' -o -name '*.ts' -o -name '*.tsx' -o -name '*.jsx' -o -name '*.vue' \\) -not -path './.git/*' -print`：无输出。
- `find puzzle_ops tests -type f -not -name '*.py' -not -path '*/__pycache__/*' -print`：无输出。
- `git diff --check`：无输出。

## v0.3.49 - Harness 接入 RAG Runtime 指标

日期：2026-06-16

阶段目标：

- 将 v0.3.48 的 RAG runtime stats 从多模态底座单次展示，推进到 Harness Run 指标体系。
- 让 RAG 的缓存、远程调用和降级情况能参与版本对比，而不是只作为当前页面状态。

已完成：

- `AgentHarness.run(...)`：
  - 在生成 run 前触发一次国家级 `value_audit_rag_answer(...)`。
  - 读取 Agent 最近一次 `_last_rag_stats`。
  - 对未知测试国家跳过 RAG 构建，避免 synthetic harness case 因缺少国家配置失败。
- `AgentHarness._aggregate_metrics(...)` 新增 RAG 指标：
  - `RAG缓存命中率`
  - `RAG远程调用率`
  - `RAG降级率`
- Agent 评测页：
  - Harness 指标卡自动展示上述 RAG 指标。
  - 历史 `HarnessRun` 保存后可参与版本对比。
- 测试增强：
  - 覆盖 Harness Run 中 RAG metrics 的存在和数值边界。
  - 覆盖 Eval 页面展示 RAG 指标。
  - 修正四层 memory 测试国家名，避免与 generation trace 测试共享 `测试国` 造成本地 DB 污染。
- README 增加 Harness RAG 指标说明。

当前限制：

- 当前 RAG 指标是 run 级聚合，尚未细化到每个 case 的 RAG trace。
- 当前指标为比例型指标，尚未加入耗时、费用估算、请求 token 数。
- synthetic/未知国家样本会跳过 RAG 构建并返回 0 值指标，避免伪造国家价值观检索结果。

验证记录：

- `PYTHONPATH=. pytest tests/test_harness.py::test_harness_metrics_include_rag_runtime_stats tests/test_renderer.py::test_eval_page_shows_clear_agent_evaluation_workflow -q`：2 passed。
- `PYTHONPATH=. pytest tests/test_harness.py tests/test_renderer.py tests/test_rag.py tests/test_agents.py -q`：76 passed。
- `PYTHONPATH=. pytest tests -q`：186 passed。
- `find . -maxdepth 3 -type f \\( -name 'package.json' -o -name 'vite.config.*' -o -name '*.js' -o -name '*.ts' -o -name '*.tsx' -o -name '*.jsx' -o -name '*.vue' \\) -not -path './.git/*' -print`：无输出。
- `find puzzle_ops tests -type f -not -name '*.py' -not -path '*/__pycache__/*' -print`：无输出。
- `git diff --check`：无输出。

## v0.3.48 - RAG Embedding 缓存与调用可观测

日期：2026-06-16

阶段目标：

- 将 v0.3.47 的 DashScope RAG provider 从“可真实调用”推进到“可缓存、可观测、可诊断”。
- 降低重复 embedding 请求成本，并让页面能说明本次 RAG 是否远程调用、是否缓存命中、是否发生 fallback。

已完成：

- SQLite 新增 `rag_embedding_cache`：
  - 主键：`provider`、`model`、`text_hash`。
  - 保存原始 text 与 vector JSON。
  - 记录 `created_at` / `updated_at`。
- `PuzzleRepository` 新增：
  - `get_rag_embedding_cache(provider, model, text)`
  - `set_rag_embedding_cache(provider, model, text, vector)`
- `puzzle_ops/rag.py` 新增：
  - `RagRuntimeStats`
  - `embedding_cache_hits`
  - `embedding_remote_calls`
  - `embedding_fallbacks`
  - `rerank_remote_calls`
  - `rerank_fallbacks`
- `DashScopeEmbeddingProvider`：
  - 优先查内存 cache。
  - 再查 SQLite 持久化 cache。
  - 未命中且远程调用开启时请求 provider。
  - 成功后写回内存和 SQLite cache。
  - 异常时记录 fallback 并回退本地 token/cosine。
- `DashScopeRerankProvider`：
  - 记录远程调用次数。
  - 解析失败或请求异常时记录 fallback 并回退本地规则 rerank。
- `PuzzleOpsAgent`：
  - 每次 `value_audit_rag_answer(...)` 创建独立 `RagRuntimeStats`。
  - 将 repository cache 方法传入 provider。
  - `value_audit_rag_summary(...)` 输出 RAG runtime stats。
- 多模态底座：
  - “价值观与审核 RAG”展示 cache hit、embedding remote、embedding fallback、rerank remote、rerank fallback。
- README 增加 RAG 可观测与缓存说明。

当前限制：

- 当前只持久化 embedding vector，rerank 结果暂未缓存。
- 当前统计是单次 RAG summary 级别，尚未写入 Harness Run 历史指标。
- 当前未记录远程调用耗时和 token/费用估算，后续可继续加入成本观测。

验证记录：

- `PYTHONPATH=. pytest tests/test_storage_runtime.py::test_repository_persists_rag_embedding_cache tests/test_rag.py::test_dashscope_embedding_provider_uses_persistent_cache_before_remote_call tests/test_rag.py::test_rag_runtime_stats_tracks_remote_and_fallback_paths tests/test_agents.py::test_agent_rag_summary_includes_runtime_stats -q`：4 passed。
- `PYTHONPATH=. pytest tests/test_rag.py tests/test_storage_runtime.py tests/test_agents.py tests/test_renderer.py -q`：69 passed。
- `PYTHONPATH=. pytest tests -q`：185 passed。
- `find . -maxdepth 3 -type f \\( -name 'package.json' -o -name 'vite.config.*' -o -name '*.js' -o -name '*.ts' -o -name '*.tsx' -o -name '*.jsx' -o -name '*.vue' \\) -not -path './.git/*' -print`：无输出。
- `find puzzle_ops tests -type f -not -name '*.py' -not -path '*/__pycache__/*' -print`：无输出。
- `git diff --check`：无输出。

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
