from puzzle_ops.renderer import AppState
from puzzle_ops.server import APP, handle_action, redirect_location, update_state_from_query


def test_invalid_country_query_does_not_corrupt_state():
    state = AppState(country="日本")

    update_state_from_query(state, {"country": ["æ\x97¥æ\x9c¬"], "view": ["dashboard"]})

    assert state.country == "日本"
    assert state.view == "dashboard"


def test_redirect_location_percent_encodes_chinese_state():
    state = AppState(country="日本", view="regular")

    location = redirect_location(state)

    assert location == "/?country=%E6%97%A5%E6%9C%AC&view=regular"


def test_add_regular_action_uses_submitted_context_and_adds_need_row():
    APP.state = AppState(country="日本", view="regular", category="人物", tag="常规_日本_传统浴袍美女0604")

    handle_action(
        "/add_regular",
        {
            "country": ["日本"],
            "category": ["人物"],
            "tag": ["常规_日本_传统浴袍美女0604"],
            "image_index": ["0"],
        },
    )

    assert APP.state.view == "regular"
    assert len(APP.state.need_rows) == 1
    assert APP.state.need_rows[0].operation_tag == "常规_日本_传统浴袍美女0604"


def test_generate_descriptions_action_updates_existing_need_rows():
    APP.state = AppState(country="日本", view="regular", category="人物", tag="常规_日本_传统浴袍美女0604")
    APP.state.need_rows.append(APP.agent.add_regular_demand("日本", "人物", "常规_日本_传统浴袍美女0604", 0))

    handle_action("/generate_descriptions", {})

    assert "主体：" in APP.state.need_rows[0].subject_description


def test_save_needs_can_edit_operation_tag():
    APP.state = AppState(country="日本", view="regular", category="人物", tag="常规_日本_传统浴袍美女0604")
    APP.state.need_rows = [APP.agent.add_regular_demand("日本", "人物", "常规_日本_传统浴袍美女0604", 0)]

    handle_action(
        "/save_needs",
        {
            "country": ["日本"],
            "operation_tag_0": ["常规_日本_猫咪鲤鱼0605"],
            "priority_0": ["P1"],
            "count_0": ["7"],
            "method_0": ["限素材网"],
            "delivery_date_0": [""],
            "remark_0": [""],
        },
    )

    assert APP.state.need_rows[0].operation_tag == "常规_日本_猫咪鲤鱼0605"


def test_sync_needs_to_feishu_clears_rows_and_sets_success_message():
    APP.state = AppState(country="日本", view="regular", category="人物", tag="常规_日本_传统浴袍美女0604")
    APP.state.need_rows = [
        APP.agent.add_regular_demand("日本", "人物", "常规_日本_传统浴袍美女0604", 0),
        APP.agent.add_regular_demand("日本", "人物", "常规_日本_传统浴袍美女0604", 1),
    ]

    handle_action("/sync_needs_feishu", {"country": ["日本"], "view": ["regular"]})

    assert APP.state.need_rows == []
    assert APP.state.sync_message == "同步成功，当前已完成提需2条"


def test_apply_value_master_action_updates_trial_row():
    APP.state = AppState(country="法国", view="trial", category="花卉", trial_mode="parse")
    APP.state.trial_row = APP.agent.create_trial_demand("法国", "花卉", "parse")

    handle_action("/apply_value_master", {"country": ["法国"], "category": ["花卉"], "trial_mode": ["parse"]})

    assert "法国市场" in APP.state.trial_row.value_match


def test_simulate_trial_upload_action_updates_trial_row():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")

    handle_action(
        "/simulate_trial_upload",
        {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]},
    )

    assert APP.state.view == "trial"
    assert "已生成2张相似参考图" in APP.state.trial_row.remark


def test_save_trial_can_edit_operation_tag():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")
    APP.state.trial_row = APP.agent.create_trial_demand("日本", "人物", "parse")

    handle_action(
        "/save_trial",
        {
            "country": ["日本"],
            "view": ["trial"],
            "category": ["人物"],
            "trial_mode": ["parse"],
            "operation_tag": ["试新_日本_猫咪鲤鱼0605"],
            "priority": ["P0"],
            "count": ["3"],
            "method": ["先照片后AI"],
            "delivery_date": [""],
            "remark": ["人工确认无风险"],
        },
    )

    assert APP.state.trial_row.operation_tag == "试新_日本_猫咪鲤鱼0605"
    assert APP.state.trial_row.priority == "P0"


def test_save_analysis_persists_editable_rows_summary_and_todo():
    APP.state = AppState(country="日本", view="analysis")

    handle_action(
        "/save_analysis",
        {
            "country": ["日本"],
            "view": ["analysis"],
            "analysis_remark_0": ["人工改：重点位置继续保留"],
            "cycle_summary": ["人工周期分析"],
            "next_todo": ["人工todo"],
        },
    )

    assert APP.state.analysis_edits["remarks"][0] == "人工改：重点位置继续保留"
    assert APP.state.analysis_edits["cycle_summary"] == "人工周期分析"
    assert APP.state.analysis_edits["next_todo"] == "人工todo"


def test_approve_value_candidate_action_writes_hitl_memory():
    APP.state = AppState(country="日本", view="runtime")
    candidate = APP.agent.value_rule_candidates("日本")[0]

    handle_action(
        "/approve_value_candidate",
        {
            "country": ["日本"],
            "view": ["runtime"],
            "candidate_id": [candidate.candidate_id],
            "human_note": ["运营确认加入固定价值观"],
        },
    )

    assert APP.state.view == "runtime"
    assert any("运营确认加入固定价值观" in memory["content"] for memory in APP.agent.hitl_memories("日本"))


def test_replace_schedule_action_records_slot_replacement():
    APP.state = AppState(country="日本", view="schedule", schedule_day="周一")
    original = APP.agent.schedule("日本", "周一")[0]

    handle_action("/replace_schedule", {"slot_index": ["0"], "image_name": [original.image_name]})

    assert 0 in APP.state.schedule_replacements
    assert APP.state.schedule_replacements[0].image_name != original.image_name
