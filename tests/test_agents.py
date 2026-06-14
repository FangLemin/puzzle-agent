from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.trial_upload import TrialImageUploadService
from puzzle_ops.vision_llm import MissingVisionLLMConfig, OpenAIVisionLLMClient
from datetime import date


def test_country_data_is_isolated_between_japan_and_france():
    agent = PuzzleOpsAgent(today=date(2026, 6, 9))

    japan = agent.dashboard("日本")
    france = agent.dashboard("法国")

    assert japan["country_label"] == "🇯🇵 日本"
    assert france["country_label"] == "🇫🇷 法国"
    assert "常规_日本_传统浴袍美女0604" in japan["tasks"][0]["body"]
    assert "常规_法国_薰衣草0604" in france["tasks"][0]["body"]


def test_regular_demand_row_has_real_business_fields_and_empty_delivery_date():
    agent = PuzzleOpsAgent(today=date(2026, 6, 9))

    row = agent.add_regular_demand("日本", "人物", "常规_日本_传统浴袍美女0604", 0)

    assert row.need_type == "常规"
    assert row.country == "日本"
    assert row.js_category == "人物"
    assert row.operation_tag == "常规_日本_传统浴袍美女0609"
    assert row.count == 7
    assert row.priority == "P1"
    assert row.delivery_date == ""
    assert row.method == "限素材网"
    assert row.remark == ""


def test_demand_editing_only_changes_requested_editable_fields():
    agent = PuzzleOpsAgent(today=date(2026, 6, 9))
    row = agent.add_regular_demand("法国", "花卉", "常规_法国_薰衣草0604", 0)

    edited = agent.edit_demand_row(
        row,
        priority="P0",
        count=9,
        method="先照片后AI",
        delivery_date="06-20",
        remark="过图会要求提前交付",
    )

    assert edited.priority == "P0"
    assert edited.count == 9
    assert edited.method == "先照片后AI"
    assert edited.delivery_date == "06-20"
    assert edited.remark == "过图会要求提前交付"
    assert edited.operation_tag == "常规_法国_薰衣草0609"


def test_trial_demand_parse_and_derive_have_matching_core_fields():
    agent = PuzzleOpsAgent()

    parse_row = agent.create_trial_demand("日本", "人物", mode="parse")
    derive_row = agent.create_trial_demand("日本", "人物", mode="derive")

    assert parse_row.need_type == "试新"
    assert derive_row.need_type == "试新"
    assert parse_row.count == 3
    assert derive_row.count == 3
    assert parse_row.priority == "P1"
    assert parse_row.delivery_date == ""
    assert derive_row.delivery_date == ""
    assert "上传参考图" in parse_row.image_name
    assert "衍生方向" in derive_row.image_name
    assert "自动衍生" not in derive_row.image_name
    assert parse_row.value_match == ""


def test_simulate_trial_upload_updates_parse_and_derive_rows():
    agent = PuzzleOpsAgent()

    parsed = agent.simulate_trial_upload("日本", "人物", "parse")
    derived = agent.simulate_trial_upload("日本", "人物", "derive")

    assert "已解析3张参考图" in parsed.remark
    assert "参考图A+参考图B+参考图C" in parsed.image_name
    assert "衍生方向" in derived.remark
    assert "已生成2张相似参考图" not in derived.remark
    assert "历史好图解析衍生方向" in derived.image_name


def test_generated_subject_description_uses_business_three_part_standard():
    agent = PuzzleOpsAgent()
    row = agent.add_regular_demand("日本", "人物", "常规_日本_传统浴袍美女0604", 0)

    described = agent.generate_subject_description(row)

    assert described.subject_description.count("主体内容：") == 1
    assert described.subject_description.count("色彩氛围：") == 1
    assert described.subject_description.count("构图环境：") == 1
    assert "主体：" not in described.subject_description
    assert "语义主体" not in described.subject_description


def test_value_master_writes_value_match_to_trial_row():
    agent = PuzzleOpsAgent()
    agent.trial_uploads = TrialImageUploadService(
        agent._runtime_dir / "value_master_fake",
        vision_client=OpenAIVisionLLMClient(
            api_key="sk-test",
            transport=lambda payload, api_key: {
                "output_text": '{"value_match":"LLM判断：符合法国市场浪漫生活艺术价值观，建议强化法式窗台。","confidence":0.9,"evidence":["法式窗台"],"risk_tags":[]}'
            },
        ),
    )
    row = agent.create_trial_demand("法国", "花卉", mode="parse")

    judged = agent.apply_value_master(row)

    assert "法国市场" in judged.value_match
    assert "法式窗台" in judged.value_match


def test_value_master_uses_current_trial_subject_instead_of_default_template():
    agent = PuzzleOpsAgent()
    agent.trial_uploads = TrialImageUploadService(
        agent._runtime_dir / "value_master_fake",
        vision_client=OpenAIVisionLLMClient(
            api_key="sk-test",
            transport=lambda payload, api_key: {
                "output_text": '{"value_match":"LLM判断：寿司拼盘符合日本本土饮食文化与清爽色彩价值观，不应套用动物互动。","confidence":0.91,"evidence":["主体内容：寿司拼盘"],"risk_tags":[]}'
            },
        ),
    )
    row = agent.create_trial_demand("日本", "人物", mode="parse").edited(
        subject="寿司拼盘",
        operation_tag="试新_日本_寿司拼盘0609",
        subject_description="主体内容：寿司拼盘；色彩氛围：米白、鲑鱼橙、海苔绿；构图环境：日式料理店铺餐桌俯拍。",
    )

    judged = agent.apply_value_master(row)

    assert "寿司拼盘" in judged.value_match
    assert "饮食文化" in judged.value_match
    assert "LLM判断" in judged.value_match


def test_value_master_requires_real_llm_instead_of_rule_fallback():
    agent = PuzzleOpsAgent()
    agent.trial_uploads = TrialImageUploadService(
        agent._runtime_dir / "value_master_missing",
        vision_config_error=MissingVisionLLMConfig(("QWEN_API_KEY",), provider="qwen"),
    )
    row = agent.create_trial_demand("日本", "人物", mode="parse").edited(subject="寿司拼盘")

    judged = agent.apply_value_master(row)

    assert "需要配置真实视觉 LLM" in judged.value_match
    assert "动物互动" not in judged.value_match


def test_holiday_recommendation_is_ai_subject_planning_not_tag_copying():
    agent = PuzzleOpsAgent()

    holiday = agent.holiday_recommendation("日本")

    assert holiday.name == "黄金周"
    assert "旅游踏青" in holiday.ai_themes
    assert "家庭团聚" in holiday.ai_themes
    assert "新干线" in holiday.elements
    assert all(not theme.startswith(("常规_", "试新_")) for theme in holiday.ai_themes)
    assert len(holiday.history_good_images) >= 3


def test_analysis_marks_positions_5_and_10_and_keeps_editable_remarks():
    agent = PuzzleOpsAgent()

    report = agent.analysis_report("日本")
    important = [row for row in report.rows if row.position in {5, 10}]

    assert report.sa_ratio.endswith("%")
    assert report.cd_ratio.endswith("%")
    assert len(important) == 2
    assert all(row.position_is_red for row in important)
    assert all(row.remark_editable for row in report.rows)


def test_value_prediction_filters_by_grade():
    agent = PuzzleOpsAgent()

    s_cards = agent.value_predictions("日本", "S")
    a_cards = agent.value_predictions("日本", "A")

    assert s_cards
    assert a_cards
    assert all(card.image.grade == "S" for card in s_cards)
    assert all(card.image.grade == "A" for card in a_cards)
    assert "常规_日本_" in s_cards[0].operation_tag or "试新_日本_" in s_cards[0].operation_tag


def test_schedule_uses_allowed_distribution_positions():
    agent = PuzzleOpsAgent()

    monday = agent.schedule("法国", "周一")
    saturday = agent.schedule("法国", "周六")

    assert len(monday) == 10
    assert len(saturday) == 10
    assert all(item.position in {1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 15} for item in monday)
    assert all(item.position in {1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 17, 18} for item in saturday)


def test_schedule_replacement_returns_different_candidate():
    agent = PuzzleOpsAgent()
    original = agent.schedule("日本", "周一")[0]

    replacement = agent.replacement_for_slot("日本", original.image_name)

    assert replacement.image_name != original.image_name
    assert replacement.operation_tag


def test_schedule_replacement_keeps_original_distribution_position():
    agent = PuzzleOpsAgent()
    replacement = agent.replacement_for_slot("日本", "温泉街传统浴袍美女")

    schedule = agent.schedule("日本", "周一", {0: replacement})

    assert schedule[0].position == 1
    assert schedule[0].image_name.startswith("未分发候补图")


def test_value_rules_are_detailed_enough_for_business_interview():
    agent = PuzzleOpsAgent()

    japan_rules = agent.value_rules("日本")

    assert len(japan_rules) >= 8
    assert any("版权" in body or "知名动画" in body for _, body in japan_rules)
    assert any("文化混淆" in body for _, body in japan_rules)
