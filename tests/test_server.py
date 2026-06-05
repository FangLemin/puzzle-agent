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


def test_apply_value_master_action_updates_trial_row():
    APP.state = AppState(country="法国", view="trial", category="花卉", trial_mode="parse")
    APP.state.trial_row = APP.agent.create_trial_demand("法国", "花卉", "parse")

    handle_action("/apply_value_master", {"country": ["法国"], "category": ["花卉"], "trial_mode": ["parse"]})

    assert "法国市场" in APP.state.trial_row.value_match


def test_replace_schedule_action_records_slot_replacement():
    APP.state = AppState(country="日本", view="schedule", schedule_day="周一")
    original = APP.agent.schedule("日本", "周一")[0]

    handle_action("/replace_schedule", {"slot_index": ["0"], "image_name": [original.image_name]})

    assert 0 in APP.state.schedule_replacements
    assert APP.state.schedule_replacements[0].image_name != original.image_name
