from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.renderer import AppState, render_page
from puzzle_ops.trial_upload import TrialImageUploadService
from puzzle_ops.vision_llm import MissingVisionLLMConfig
from puzzle_ops.image_generation import CloudImageGenerationProvider
from puzzle_ops.storage import PuzzleRepository


def agent_without_vlm(tmp_path):
    agent = PuzzleOpsAgent()
    agent.trial_uploads = TrialImageUploadService(
        tmp_path / "uploads",
        vision_config_error=MissingVisionLLMConfig(("QWEN_API_KEY",), provider="qwen"),
    )
    return agent


def test_dashboard_page_contains_country_workflow_and_holiday_ai_themes():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="dashboard"))

    assert "首页工作台" in html
    assert "🇯🇵 日本" in html
    assert "🗓️ 周一" in html
    assert "name=\"workflow_0\"" in html
    assert "name=\"task_0\"" in html
    assert "查看完整节日提需建议" in html
    assert "节日提需建议：黄金周" not in html
    holiday_html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="dashboard", show_holiday=True))
    assert "节日提需建议：黄金周" in holiday_html
    assert "周四" in html
    assert "过图会" in html
    assert "黄金周" in holiday_html
    assert "旅游踏青" in holiday_html
    assert "家庭团聚" in holiday_html


def test_regular_page_renders_business_table_fields_and_empty_delivery_input():
    agent = PuzzleOpsAgent()
    state = AppState(country="日本", view="regular")
    state.need_rows.append(agent.add_regular_demand("日本", "人物", "常规_日本_传统浴袍美女0604", 0))

    html = render_page(agent, state)

    assert "批量提需清单" in html
    assert "需求等级" in html
    assert "交付日期" in html
    assert 'name="delivery_date_0" value=""' in html
    assert 'name="operation_tag_0"' in html
    assert 'name="subject_description_0"' in html
    assert 'class="demand-card-list regular-demand-list"' in html
    assert 'class="demand-card-grid"' in html
    assert 'class="demand-long-fields"' in html
    assert "一键同步到飞书表格" in html
    assert 'formtarget="_blank"' not in html
    assert "常规_日本_传统浴袍美女0604" in html
    assert "stock-hot" in html
    assert "stock-low" in html
    assert 'name="country" value="日本"' in html
    assert 'name="view" value="regular"' in html
    assert 'name="tag" value="常规_日本_传统浴袍美女0604"' in html


def test_trial_page_keeps_core_fields_and_value_match_column(tmp_path):
    agent = agent_without_vlm(tmp_path)
    state = AppState(country="法国", view="trial")
    state.trial_row = agent.create_trial_demand("法国", "花卉", mode="derive")

    html = render_page(agent, state)

    assert "试新提需表预览" in html
    assert "上传参考图" in html
    assert "mock-upload-zone" in html
    assert "参考图 A" in html
    assert "需求等级" in html
    assert "价值观匹配度" in html
    assert "衍生方向" in html
    assert "自动衍生2张参考图" not in html
    assert "模拟上传并解析" in html
    assert 'type="file"' in html
    assert 'enctype="multipart/form-data"' in html
    assert 'action="/simulate_trial_upload"' in html
    assert 'action="/generate_trial_derivatives"' in html
    assert "生成衍生参考图" in html
    assert "生成 provider 未配置" in html
    assert 'action="/upload_trial_images"' in html
    assert 'formaction="/sync_trial_feishu"' in html
    assert 'formtarget="_blank"' not in html
    assert "解析结果已写入下方试新提需表" in html
    assert "视觉 LLM 语义解析" in html
    assert "需要配置真实视觉 LLM" in html
    assert "QWEN_API_KEY" in html
    assert "Agent 解析结果" not in html
    assert 'name="delivery_date" value=""' in html
    assert 'name="view" value="trial"' in html
    assert 'name="subject_description"' in html
    assert 'class="demand-card-list trial-demand-list"' in html
    assert 'class="demand-card-grid"' in html
    assert 'class="demand-long-fields"' in html
    assert 'class="image-preview-cell"' in html
    assert 'class="operation-tag-input"' in html
    assert 'class="small-input"' in html


def test_trial_page_shows_value_match_rag_citation_details(tmp_path):
    agent = agent_without_vlm(tmp_path)
    agent.build_value_audit_rag_index("日本")
    state = AppState(country="日本", view="trial", trial_mode="parse")
    state.trial_row = agent.create_trial_demand("日本", "人物", mode="parse").edited(
        subject="寿司",
        value_match="结论：符合日本本土饮食文化；系统RAG召回：JP_VALUE_001#chunk-1",
    )

    html = render_page(agent, state)

    assert "价值观 RAG 依据明细" in html
    assert "JP_VALUE_001#chunk-1" in html
    assert "value_rule" in html
    assert "文化真实性" in html
    assert 'action="/record_rag_feedback"' in html
    assert 'name="chunk_id" value="JP_VALUE_001#chunk-1"' in html
    assert 'name="usefulness" value="useful"' in html
    assert 'name="usefulness" value="not_useful"' in html


def test_trial_page_shows_real_generation_provider_status(tmp_path):
    agent = agent_without_vlm(tmp_path)
    agent.image_generator = CloudImageGenerationProvider(
        tmp_path / "generated",
        api_key="gen-test",
        model="wanx2.1-t2i-plus",
        base_url="https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
        transport=lambda payload, api_key, base_url: {"images": []},
    )
    state = AppState(country="日本", view="trial", trial_mode="derive")

    html = render_page(agent, state)

    assert "图像生成 Provider" in html
    assert "真实生成 provider 已配置：wanx2.1-t2i-plus" in html


def test_eval_page_shows_gold_dataset_workbench(monkeypatch, tmp_path):
    image_path = tmp_path / "real-sushi.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note",
                "real-001,日本,real-sushi.png,试新_日本_寿司0615,寿司,food,real,5,0.31,0.93,42,,,,,,,待补 gold",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    html = render_page(agent, AppState(country="日本", view="eval"))

    assert "Gold Dataset 工作台" in html
    assert "gold 完成率" in html
    assert "0%" in html
    assert 'action="/save_harness_gold_label"' in html
    assert 'name="gold_subject"' in html
    assert 'name="gold_color_mood"' in html
    assert 'name="gold_composition"' in html
    assert 'action="/export_harness_gold_skeleton"' in html


def test_eval_page_exposes_ai_silver_label_action(monkeypatch, tmp_path):
    image_path = tmp_path / "france-picnic.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                "fr-real-001,法国,france-picnic.png,试新_法国_真实样本0623,待AI预标注,lifestyle,real,0,0,0,0,A,,,,,,,manual_grade,needs_ai_prelabeled",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    html = render_page(agent, AppState(country="法国", view="eval"))

    assert 'action="/auto_prelabeled_harness_gold"' in html
    assert "AI 自动预标注" in html
    assert "manual_grade" in html
    assert "needs_ai_prelabeled" in html


def test_eval_page_exposes_silver_approval_action(monkeypatch, tmp_path):
    image_path = tmp_path / "france-picnic.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                "fr-real-001,法国,france-picnic.png,试新_法国_真实样本0623,法式海滩野餐,lifestyle,real,0,0,0,0,A,法式海滩野餐,暖色,海边沙滩,生活艺术,,AI silver label，待人工抽查。,ai_silver,pending_review",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    html = render_page(agent, AppState(country="法国", view="eval"))

    assert 'action="/approve_harness_silver_labels"' in html
    assert "确认 AI 预标注为 human_gold" in html
    assert 'name="reviewer_note"' in html
    assert "ai_silver" in html


def test_eval_page_uses_checkboxes_for_selected_silver_approval(monkeypatch, tmp_path):
    image_path = tmp_path / "france-picnic.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                "fr-real-001,法国,france-picnic.png,试新_法国_样本一0623,法式海滩野餐,lifestyle,real,0,0,0,0,A,法式海滩野餐,暖色,海边沙滩,生活艺术,,AI silver label，待人工抽查。,ai_silver,pending_review",
                "fr-real-002,法国,france-picnic.png,试新_法国_样本二0623,薰衣草风车,landscape,real,0,0,0,0,S,薰衣草风车,紫色,普罗旺斯田野,法式乡村,,AI silver label，待人工抽查。,ai_silver,pending_review",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    html = render_page(agent, AppState(country="法国", view="eval"))

    assert 'id="approve-silver-form"' in html
    assert 'form="approve-silver-form"' in html
    assert 'type="checkbox" name="sample_id" value="fr-real-001"' in html
    assert 'type="checkbox" name="sample_id" value="fr-real-002"' in html


def test_trial_page_has_generation_provider_diagnostic_action(tmp_path):
    agent = agent_without_vlm(tmp_path)
    state = AppState(country="日本", view="trial", trial_mode="derive")

    html = render_page(agent, state)

    assert "生成 Provider 诊断" in html
    assert "检查生成 Provider" in html
    assert 'action="/check_generation_provider"' in html


def test_trial_page_shows_recent_generation_event():
    state = AppState(country="日本", view="trial", trial_mode="derive")
    state.generation_event = {
        "status": "failed",
        "provider": "dashscope",
        "model": "qwen3-vl-flash",
        "task_id": "task-123",
        "source_operation_tag": "试新_日本_寿司0615",
        "generated_image_paths": "/tmp/out-1.png",
        "second_review_status": "not_started",
        "feishu_attachment_status": "blocked",
        "error_type": "model_deprecated",
        "message": "模型 qwen3-vl-flash 已下线，请迁移。",
    }

    html = render_page(PuzzleOpsAgent(), state)

    assert "最近一次生成任务" in html
    assert "failed" in html
    assert "dashscope" in html
    assert "model_deprecated" in html
    assert "模型 qwen3-vl-flash 已下线" in html
    assert "task-123" in html
    assert "试新_日本_寿司0615" in html
    assert "/tmp/out-1.png" in html
    assert "not_started" in html
    assert "blocked" in html


def test_sync_success_message_renders_feishu_link_without_popup_dependency():
    agent = PuzzleOpsAgent()
    state = AppState(country="日本", view="trial", sync_message="同步成功，当前已完成试新提需1条", sync_url="https://feishu.cn/base/app?table=tbl")

    html = render_page(agent, state)

    assert 'class="sync-success-card"' in html
    assert 'href="https://feishu.cn/base/app?table=tbl"' in html
    assert 'target="_blank"' in html
    assert "已同步，打开飞书表格" in html


def test_sync_page_shows_persisted_generation_events():
    agent = PuzzleOpsAgent()
    agent.record_generation_event(
        "日本",
        {
            "status": "failed",
            "provider": "dashscope",
            "model": "wanx-test",
            "task_id": "task-123",
            "source_operation_tag": "试新_日本_寿司0615",
            "generated_image_paths": "/tmp/out-1.png",
            "second_review_status": "not_started",
            "feishu_attachment_status": "blocked",
            "error_type": "quota_exceeded",
            "message": "DashScope 图像生成失败：quota exceeded",
        },
    )

    html = render_page(agent, AppState(country="日本", view="sync"))

    assert "生成任务回放" in html
    assert "dashscope" in html
    assert "quota_exceeded" in html
    assert "DashScope 图像生成失败" in html
    assert "task-123" in html
    assert "试新_日本_寿司0615" in html
    assert "not_started" in html


def test_schedule_page_mentions_allowed_positions_and_renders_ten_slots():
    html = render_page(PuzzleOpsAgent(), AppState(country="法国", view="schedule", schedule_day="周六"))

    assert "周末允许 1-9、12-18 位" in html
    assert html.count("排图位") == 10
    assert 'action="/replace_schedule"' in html


def test_analysis_page_places_chart_before_detail_and_summary_at_bottom():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="analysis"))

    assert "趋势对比折线图" in html
    assert "CD历史均值" in html
    assert "AI历史均值" in html
    assert "AI OKR" in html
    assert 'action="/save_analysis"' in html
    assert 'name="analysis_remark_0"' in html
    assert 'name="cycle_summary"' in html
    assert 'name="next_todo"' in html
    assert html.index("趋势对比折线图") < html.index("图片明细与 AI 分析备注")
    assert html.index("图片明细与 AI 分析备注") < html.index("周期内容分析")
    assert 'class="image-preview-cell"' in html
    assert '<img src="data:image/png;base64,' in html


def test_analysis_delta_colors_follow_metric_direction_rules():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="analysis"))

    assert '<em class="delta delta-good">↑ 4%</em>' in html
    assert '<em class="delta delta-good">↓ 3%</em>' in html
    assert '<em class="delta delta-bad">↑ 2%</em>' in html


def test_runtime_page_shows_vision_llm_adapter_status(tmp_path):
    html = render_page(agent_without_vlm(tmp_path), AppState(country="日本", view="runtime"))

    assert "视觉 LLM 适配器" in html
    assert "需要配置真实视觉 LLM" in html
    assert "QWEN_API_KEY" in html


def test_dashboard_okr_coloring_and_alert_rules():
    agent = PuzzleOpsAgent()
    japan = render_page(agent, AppState(country="日本", view="dashboard"))
    france = render_page(agent, AppState(country="法国", view="dashboard"))

    assert '<span class="metric-value metric-miss">72%</span><span class="metric-sep">/</span><span class="okr-value">75%</span>' in japan
    assert "本季度累计 AI率 / OKR" in japan
    assert '<span class="metric-value metric-bad">16%</span><span class="metric-sep">/</span><span class="okr-value">15%</span>' in japan
    assert '<span class="metric-value metric-ok">14%</span><span class="metric-sep">/</span><span class="okr-value">15%</span>' in france
    assert '<span class="metric-value metric-miss">69%</span><span class="metric-sep">/</span><span class="okr-value">73%</span>' in france
    assert '<span class="okr-value">75%</span><span class="metric-alert">!</span>' not in japan


def test_metric_gap_over_ten_points_gets_red_alert():
    from puzzle_ops.renderer import render_metric_ratio

    html = render_metric_ratio("20% / 35%", higher_is_better=True)

    assert '<span class="metric-alert">!</span>' in html


def test_ai_rate_okr_is_red_when_equal_or_above_okr():
    from puzzle_ops.renderer import render_ai_rate_ratio

    below = render_ai_rate_ratio("14% / 15%")
    equal = render_ai_rate_ratio("15% / 15%")
    above = render_ai_rate_ratio("16% / 15%")

    assert '<span class="metric-value metric-ok">14%</span>' in below
    assert '<span class="metric-value metric-bad">15%</span>' in equal
    assert '<span class="metric-value metric-bad">16%</span>' in above


def test_every_view_header_keeps_module_icon():
    agent = PuzzleOpsAgent()

    for view, icon in {
        "regular": "📦",
        "trial": "✨",
        "analysis": "📈",
        "value": "🔮",
        "runtime": "🧠",
        "eval": "🧪",
        "schedule": "🗓️",
    }.items():
        html = render_page(agent, AppState(country="日本", view=view))
        assert f'<span class="page-icon">{icon}</span>' in html


def test_multimodal_runtime_page_shows_profile_candidates_and_evidence():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="runtime"))

    assert "多模态底座" in html
    assert "相似历史好图" in html
    assert "相似历史坏图" in html
    assert "价值观候选池" in html
    assert "pending_review" in html
    assert 'action="/approve_value_candidate"' in html
    assert "已审批价值观规则" in html
    assert "HITL Memory" in html
    assert "四层 Memory 概览" in html
    assert "感知记忆" in html
    assert "短期记忆" in html
    assert "长期记忆" in html
    assert "结构化事实" in html
    assert "RAG Ready" in html
    assert "价值观与审核 RAG" in html
    assert "父子知识块" in html
    assert "多路召回" in html
    assert "引用依据" in html
    assert "Embedding" in html
    assert "Rerank" in html
    assert "cache hit" in html
    assert "embedding remote" in html
    assert "rerank fallback" in html


def test_multimodal_runtime_page_shows_approved_candidate_after_hitl_action():
    agent = PuzzleOpsAgent()
    candidate = agent.value_rule_candidates("日本")[0]
    agent.approve_value_candidate(candidate.candidate_id, "日本", "运营确认用于后续试新")
    agent.record_perception_memory("日本", "trial_image_parse", {"subject": "寿司"})
    agent.record_working_memory("日本", "generation_trace", {"status": "failed"})
    agent.record_extracted_fact("日本", "image_semantic_fact", {"subject": "寿司"})

    html = render_page(agent, AppState(country="日本", view="runtime"))

    assert "运营确认用于后续试新" in html
    assert candidate.rule_text in html


def test_eval_page_shows_clear_agent_evaluation_workflow():
    agent = PuzzleOpsAgent()
    agent.record_generation_event(
        "日本",
        {
            "status": "failed",
            "provider": "dashscope",
            "model": "wanx-test",
            "task_id": "",
            "source_operation_tag": "试新_日本_寿司0615",
            "generated_image_paths": "",
            "second_review_status": "not_started",
            "feishu_attachment_status": "blocked",
            "error_type": "quota_exceeded",
            "message": "DashScope 图像生成失败：quota exceeded",
        },
    )

    html = render_page(agent, AppState(country="日本", view="eval"))

    assert "Agent 评测" in html
    assert "Harness Dashboard" in html
    assert "数据集概览" in html
    assert "本次运行" in html
    assert "失败样本" in html
    assert "版本对比" in html
    assert "HITL 修正入口" in html
    assert "真实样本数" in html
    assert "合成样本数" in html
    assert "生成图审核通过率" in html
    assert "生成Trace完整率" in html
    assert "RAG缓存命中率" in html
    assert "RAG远程调用率" in html
    assert "RAG降级率" in html
    assert "二次审核通过率" in html
    assert "飞书附件Ready率" in html
    assert "生成失败类型分布" in html
    assert "quota_exceeded" in html
    assert "derive_generation_eval" in html
    assert "任务目标" in html
    assert "输入与上下文" in html
    assert "工具调用链路" in html
    assert "指标与结论" in html
    assert "Eval Dataset" in html
    assert "Case 明细" in html
    assert "Pass/Fail" in html
    assert "Tool Correctness" in html
    assert "Context Precision" in html
    assert "TruLens Context Relevance" in html
    assert "value_judge_skill" in html
    assert "history.search_records" in html
    assert html.index("任务目标") < html.index("输入与上下文")
    assert html.index("输入与上下文") < html.index("工具调用链路")
    assert html.index("工具调用链路") < html.index("指标与结论")


def test_eval_failure_samples_show_image_gold_label_and_hitl_form(monkeypatch, tmp_path):
    image_path = tmp_path / "real-sushi.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note",
                "real-001,日本,real-sushi.png,试新_日本_寿司0615,寿司,food,real,5,0.31,0.93,42,S,寿司,米白与鲑鱼橙,日式料理桌面近景,本土饮食文化,,真实运营样本",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))

    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="eval"))

    assert "失败样本复盘" in html
    assert "real-sushi.png" in html
    assert "Gold Label" in html
    assert "gold_subject=寿司" in html
    assert "gold_color_mood=米白与鲑鱼橙" in html
    assert "Agent 输出" in html
    assert "失败原因" in html
    assert 'action="/save_harness_override"' in html
    assert "name=\"sample_id\"" in html


def test_eval_page_shows_case_evidence_trace_and_failure_categories():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="eval"))

    assert "Case 证据链" in html
    assert "RAG 引用" in html
    assert "Memory 证据" in html
    assert "失败分类" in html


def test_runtime_page_shows_memory_debug_table():
    agent = PuzzleOpsAgent()
    agent.record_perception_memory("日本", "vision_parse", {"subject": "寿司"})
    html = render_page(agent, AppState(country="日本", view="runtime"))

    assert "Memory Debug" in html
    assert "RAG Source" in html
    assert "命中分" in html
    assert "引用明细" in html
    assert "父文档" in html
    assert "知识来源" in html
    assert "状态" in html
    assert 'action="/promote_memory"' in html
    assert 'action="/retire_memory"' in html
    assert "晋升为事实" in html


def test_page_css_prevents_grid_content_from_widening_viewport():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="eval"))

    assert "main { padding:22px; min-width:0; overflow-x:hidden; }" in html
    assert ".grid > *, .panel { min-width:0; }" in html
    assert "overflow-wrap:anywhere" in html


def test_eval_page_has_harness_override_export_action():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="eval"))

    assert 'action="/export_harness_overrides"' in html
    assert "导出人工修正CSV" in html
    assert 'action="/export_harness_annotations"' in html
    assert "导出标注平台文件" in html


def test_eval_page_is_read_only_until_explicit_harness_run(tmp_path):
    class FailIfCalledGenerator:
        provider_name = "paid-provider"

        def healthcheck(self):
            return {"configured": True, "provider": self.provider_name}

        def generate_derivatives(self, *args, **kwargs):
            raise AssertionError("rendering eval page must not call paid generation")

    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "eval-readonly.db"))
    agent.image_generator = FailIfCalledGenerator()
    before = agent.repository.harness_runs()

    html = render_page(agent, AppState(country="日本", view="eval"))

    assert agent.repository.harness_runs() == before
    assert 'action="/run_harness"' in html
    assert 'name="include_generation"' in html
    assert "默认不调用图像生成模型" in html
    assert "主体识别准确率</span><strong>未评测" in html
    assert "价值观一致率</span><strong>未评测" in html
    assert "generation_not_authorized" not in html


def test_stock_and_value_cards_render_real_image_tags_instead_of_text_cards():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="regular"))
    value_html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="value", value_grade="S"))

    assert '<img src="data:image/png;base64,' in html
    assert '<img src="data:image/png;base64,' in value_html
    assert 'class="thumb visual-thumb"' in html


def test_trial_need_table_renders_uploaded_image_url_when_available(tmp_path):
    agent = agent_without_vlm(tmp_path)
    state = AppState(country="日本", view="trial")
    state.trial_row = agent.create_trial_demand("日本", "人物", "parse").edited(
        image_name="train-shop-girl.png",
        reference_image_url="/uploads/train-shop-girl.png",
        operation_tag="试新_日本_日式火车店铺少女0609",
    )

    html = render_page(agent, state)

    assert '<img src="/uploads/train-shop-girl.png"' in html
    assert 'value="试新_日本_日式火车店铺少女0609"' in html


def test_trial_page_shows_generation_failure_message():
    state = AppState(country="日本", view="trial", trial_mode="derive")
    state.sync_message = "生成衍生参考图失败：DashScope 图像生成失败：quota exceeded"

    html = render_page(PuzzleOpsAgent(), state)

    assert "生成衍生参考图失败" in html
    assert "quota exceeded" in html


def test_trial_page_shows_human_approval_for_vlm_passed_generated_rows(tmp_path):
    agent = agent_without_vlm(tmp_path)
    row = agent.create_trial_demand("日本", "人物", mode="derive").edited(
        image_name="衍生参考图1.png",
        reference_image_path=str(tmp_path / "generated.png"),
        generation_review_status="passed",
        human_approved=False,
        reference_image_syncable=False,
    )
    state = AppState(country="日本", view="trial", trial_mode="derive", trial_row=row, trial_rows=[row])

    html = render_page(agent, state)

    assert 'action="/approve_generated_derivatives"' in html
    assert "确认生成图可同步" in html
