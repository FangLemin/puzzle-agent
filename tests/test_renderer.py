from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.renderer import AppState, render_page


def test_dashboard_page_contains_country_workflow_and_holiday_ai_themes():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="dashboard"))

    assert "首页工作台" in html
    assert "🇯🇵 日本" in html
    assert "周四" in html
    assert "过图会" in html
    assert "黄金周" in html
    assert "旅游踏青" in html
    assert "家庭团聚" in html


def test_regular_page_renders_business_table_fields_and_empty_delivery_input():
    agent = PuzzleOpsAgent()
    state = AppState(country="日本", view="regular")
    state.need_rows.append(agent.add_regular_demand("日本", "人物", "常规_日本_传统浴袍美女0604", 0))

    html = render_page(agent, state)

    assert "批量提需清单" in html
    assert "需求等级" in html
    assert "交付日期" in html
    assert 'name="delivery_date_0" value=""' in html
    assert "常规_日本_传统浴袍美女0604" in html


def test_trial_page_keeps_core_fields_and_value_match_column():
    agent = PuzzleOpsAgent()
    state = AppState(country="法国", view="trial")
    state.trial_row = agent.create_trial_demand("法国", "花卉", mode="derive")

    html = render_page(agent, state)

    assert "试新提需表预览" in html
    assert "需求等级" in html
    assert "价值观匹配度" in html
    assert "自动衍生2张参考图" in html
    assert 'name="delivery_date" value=""' in html


def test_schedule_page_mentions_allowed_positions_and_renders_ten_slots():
    html = render_page(PuzzleOpsAgent(), AppState(country="法国", view="schedule", schedule_day="周六"))

    assert "周末允许 1-9、12-18 位" in html
    assert html.count("排图位") == 10
