from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.renderer import AppState, render_page


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
    assert "一键同步到飞书表格" in html
    assert "常规_日本_传统浴袍美女0604" in html
    assert "stock-hot" in html
    assert "stock-low" in html
    assert 'name="country" value="日本"' in html
    assert 'name="tag" value="常规_日本_传统浴袍美女0604"' in html


def test_trial_page_keeps_core_fields_and_value_match_column():
    agent = PuzzleOpsAgent()
    state = AppState(country="法国", view="trial")
    state.trial_row = agent.create_trial_demand("法国", "花卉", mode="derive")

    html = render_page(agent, state)

    assert "试新提需表预览" in html
    assert "上传参考图" in html
    assert "mock-upload-zone" in html
    assert "参考图 A" in html
    assert "需求等级" in html
    assert "价值观匹配度" in html
    assert "自动衍生2张参考图" in html
    assert "模拟上传并解析" in html
    assert 'type="file"' in html
    assert 'enctype="multipart/form-data"' in html
    assert 'action="/simulate_trial_upload"' in html
    assert 'action="/upload_trial_images"' in html
    assert 'name="delivery_date" value=""' in html
    assert 'class="image-preview-cell"' in html
    assert 'class="small-input"' in html


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


def test_analysis_delta_colors_follow_metric_direction_rules():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="analysis"))

    assert '<em class="delta delta-good">↑ 4%</em>' in html
    assert '<em class="delta delta-good">↓ 3%</em>' in html
    assert '<em class="delta delta-bad">↑ 2%</em>' in html


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


def test_multimodal_runtime_page_shows_approved_candidate_after_hitl_action():
    agent = PuzzleOpsAgent()
    candidate = agent.value_rule_candidates("日本")[0]
    agent.approve_value_candidate(candidate.candidate_id, "日本", "运营确认用于后续试新")

    html = render_page(agent, AppState(country="日本", view="runtime"))

    assert "运营确认用于后续试新" in html
    assert candidate.rule_text in html


def test_eval_page_shows_agent_trace_and_metrics():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="eval"))

    assert "Agent 评测" in html
    assert "Eval Dataset" in html
    assert "Case 明细" in html
    assert "Pass/Fail" in html
    assert "Tool Correctness" in html
    assert "Context Precision" in html
    assert "TruLens Context Relevance" in html
    assert "value_judge_skill" in html
    assert "history.search_records" in html
