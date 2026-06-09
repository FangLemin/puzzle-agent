from puzzle_ops.renderer import AppState
from puzzle_ops.server import APP, handle_action, redirect_location, update_state_from_query
from puzzle_ops.feishu import MockFeishuClient
from puzzle_ops.image_generation import MockImageGenerationProvider
from puzzle_ops.trial_upload import TrialImageUploadService
from puzzle_ops.vision_llm import MissingVisionLLMConfig, OpenAIVisionLLMClient
from PIL import Image
from io import BytesIO
import json
import pytest


@pytest.fixture(autouse=True)
def isolate_vision_llm_from_real_api(tmp_path):
    previous_uploads = APP.agent.trial_uploads
    APP.agent.trial_uploads = TrialImageUploadService(
        tmp_path / "isolated_uploads",
        vision_config_error=MissingVisionLLMConfig(("QWEN_API_KEY",), provider="qwen"),
    )
    yield
    APP.agent.trial_uploads = previous_uploads


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
    assert APP.state.need_rows[0].operation_tag == "常规_日本_传统浴袍美女0609"


def test_generate_descriptions_action_updates_existing_need_rows():
    APP.state = AppState(country="日本", view="regular", category="人物", tag="常规_日本_传统浴袍美女0604")
    APP.state.need_rows.append(APP.agent.add_regular_demand("日本", "人物", "常规_日本_传统浴袍美女0604", 0))

    handle_action("/generate_descriptions", {})

    assert "主体内容：" in APP.state.need_rows[0].subject_description
    assert "色彩氛围：" in APP.state.need_rows[0].subject_description
    assert "构图环境：" in APP.state.need_rows[0].subject_description


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
    APP.agent.feishu = MockFeishuClient(APP.agent._runtime_dir / "test_feishu_mock")
    APP.state.need_rows = [
        APP.agent.add_regular_demand("日本", "人物", "常规_日本_传统浴袍美女0604", 0),
        APP.agent.add_regular_demand("日本", "人物", "常规_日本_传统浴袍美女0604", 1),
    ]
    APP.agent.feishu.allow_real_sync = True

    redirect = handle_action("/sync_needs_feishu", {"country": ["日本"], "view": ["regular"]})

    assert APP.state.need_rows == []
    assert APP.state.sync_message == "同步成功，当前已完成提需2条"
    assert APP.state.sync_url == APP.agent.feishu.web_url()
    assert redirect is None
    assert any(row[2] == "提需同步" for row in APP.agent.sync_rows())


def test_sync_needs_requires_real_feishu_and_keeps_rows_without_config():
    APP.state = AppState(country="日本", view="regular", category="人物", tag="常规_日本_传统浴袍美女0604")
    APP.agent.feishu = MockFeishuClient(APP.agent._runtime_dir / "test_feishu_mock")
    APP.state.need_rows = [APP.agent.add_regular_demand("日本", "人物", "常规_日本_传统浴袍美女0604", 0)]
    APP.agent.feishu.allow_real_sync = False

    handle_action("/sync_needs_feishu", {"country": ["日本"], "view": ["regular"]})

    assert len(APP.state.need_rows) == 1
    assert "未配置真实飞书" in APP.state.sync_message
    assert any(row[4] == "失败" for row in APP.agent.sync_rows())


def test_sync_needs_rejects_empty_demand_rows_before_feishu_call():
    APP.state = AppState(country="日本", view="regular", category="人物", tag="常规_日本_传统浴袍美女0604")
    APP.agent.feishu = MockFeishuClient(APP.agent._runtime_dir / "test_feishu_mock")
    APP.agent.feishu.allow_real_sync = True
    before = len(APP.agent.sync_rows())

    redirect = handle_action("/sync_needs_feishu", {"country": ["日本"], "view": ["regular"]})

    assert redirect is None
    assert APP.state.view == "regular"
    assert APP.state.sync_message == "请先加入至少一条常规提需，再同步飞书表格。"
    assert len(APP.agent.sync_rows()) == before


def test_apply_value_master_action_updates_trial_row():
    APP.state = AppState(country="法国", view="trial", category="花卉", trial_mode="parse")
    APP.state.trial_row = APP.agent.create_trial_demand("法国", "花卉", "parse")

    handle_action("/apply_value_master", {"country": ["法国"], "category": ["花卉"], "trial_mode": ["parse"]})

    assert "需要配置真实视觉 LLM" in APP.state.trial_row.value_match


def test_apply_value_master_action_uses_real_llm_result_when_configured(tmp_path):
    previous_uploads = APP.agent.trial_uploads
    try:
        APP.agent.trial_uploads = TrialImageUploadService(
            tmp_path / "value_master",
            vision_client=OpenAIVisionLLMClient(
                api_key="sk-test",
                transport=lambda payload, api_key: {
                    "output_text": json.dumps(
                        {
                            "value_match": "LLM判断：寿司图符合日本本土饮食文化价值观，需避免品牌露出。",
                            "confidence": 0.9,
                            "evidence": ["主体内容：寿司拼盘"],
                            "risk_tags": ["品牌露出"],
                        },
                        ensure_ascii=False,
                    )
                },
            ),
        )
        APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")
        APP.state.trial_row = APP.agent.create_trial_demand("日本", "人物", "parse").edited(
            subject="寿司拼盘",
            operation_tag="试新_日本_寿司拼盘0609",
            subject_description="主体内容：寿司拼盘；色彩氛围：米白、鲑鱼橙；构图环境：日式料理店铺餐桌俯拍。",
        )

        handle_action("/apply_value_master", {"country": ["日本"], "category": ["人物"], "trial_mode": ["parse"]})

        assert "LLM判断：寿司图符合日本本土饮食文化价值观" in APP.state.trial_row.value_match
        assert "价值观LLM：真实openai" in APP.state.trial_row.value_match
    finally:
        APP.agent.trial_uploads = previous_uploads


def test_simulate_trial_upload_action_updates_trial_row():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")

    handle_action(
        "/simulate_trial_upload",
        {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]},
    )

    assert APP.state.view == "trial"
    assert "衍生方向" in APP.state.trial_row.remark
    assert "已生成2张相似参考图" not in APP.state.trial_row.remark


def test_upload_trial_images_action_updates_trial_row_and_previews():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")

    handle_action(
        "/upload_trial_images",
        {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]},
        files={
            "trial_images": [
                {"filename": "cat-koi.png", "content_type": "image/png", "content": b"fake-image"}
            ]
        },
    )

    assert "cat-koi.png" in APP.state.trial_row.image_name
    assert "本地图片解析" in APP.state.trial_row.remark
    assert len(APP.state.trial_rows) == 1
    assert APP.state.trial_rows[0].image_name == APP.state.trial_row.image_name
    assert APP.state.trial_uploads[0]["filename"] == "cat-koi.png"


def test_upload_trial_images_extracts_real_visual_features_into_demand_row():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")
    image = Image.new("RGB", (120, 60), (220, 70, 60))
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    handle_action(
        "/upload_trial_images",
        {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]},
        files={
            "trial_images": [
                {"filename": "upload.png", "content_type": "image/png", "content": buffer.getvalue()}
            ]
        },
    )

    assert "暖红" in APP.state.trial_row.subject_description
    assert "横向构图" in APP.state.trial_row.subject_description
    assert "120x60" in APP.state.trial_row.remark
    assert len(APP.state.trial_rows) == 1


def test_upload_trial_images_summarizes_multiple_visual_features():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")
    warm = Image.new("RGB", (120, 60), (220, 70, 60))
    cool = Image.new("RGB", (80, 180), (40, 170, 190))
    warm_buffer = BytesIO()
    cool_buffer = BytesIO()
    warm.save(warm_buffer, format="PNG")
    cool.save(cool_buffer, format="PNG")

    handle_action(
        "/upload_trial_images",
        {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]},
        files={
            "trial_images": [
                {"filename": "cat-warm.png", "content_type": "image/png", "content": warm_buffer.getvalue()},
                {"filename": "koi-cool.png", "content_type": "image/png", "content": cool_buffer.getvalue()},
            ]
        },
    )

    assert "已读取2张参考图" in APP.state.trial_row.remark
    assert "暖红" in APP.state.trial_row.subject_description
    assert "清透蓝" in APP.state.trial_row.subject_description or "自然绿色" in APP.state.trial_row.subject_description
    assert "横向构图" in APP.state.trial_row.subject_description
    assert "竖向构图" in APP.state.trial_row.subject_description
    assert "拼图友好度" in APP.state.trial_row.remark


def test_upload_trial_images_requires_real_vlm_config_for_semantics():
    APP.state = AppState(country="日本", view="trial", category="动物", trial_mode="parse")
    image = Image.new("RGB", (100, 100), (210, 150, 120))
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    handle_action(
        "/upload_trial_images",
        {"country": ["日本"], "view": ["trial"], "category": ["动物"], "trial_mode": ["parse"]},
        files={
            "trial_images": [
                {"filename": "shiba-sakura.png", "content_type": "image/png", "content": buffer.getvalue()}
            ]
        },
    )

    assert "主体内容：" in APP.state.trial_row.subject_description
    assert "色彩氛围：" in APP.state.trial_row.subject_description
    assert "构图环境：" in APP.state.trial_row.subject_description
    assert "语义主体" not in APP.state.trial_row.subject_description
    assert "视觉LLM：未运行" in APP.state.trial_row.remark


def test_upload_trial_images_writes_real_openai_semantics_when_configured(tmp_path):
    def fake_transport(payload, api_key):
        return {
            "output_text": json.dumps(
                {
                    "subject": "柴犬樱花",
                    "scene": "日式庭院樱花季",
                    "culture_elements": ["樱花", "柴犬"],
                    "style": "明亮治愈写实插画",
                    "risk_tags": [],
                    "prompt_keywords": ["日本", "柴犬", "樱花"],
                    "confidence": 0.91,
                    "analysis": "真实 OpenAI 视觉模型返回的结构化语义。",
                },
                ensure_ascii=False,
            )
        }

    previous_uploads = APP.agent.trial_uploads
    try:
        APP.agent.trial_uploads = TrialImageUploadService(
            tmp_path / "uploads",
            vision_client=OpenAIVisionLLMClient(api_key="sk-test", transport=fake_transport),
        )
        APP.state = AppState(country="日本", view="trial", category="动物", trial_mode="parse")
        image = Image.new("RGB", (100, 100), (210, 150, 120))
        buffer = BytesIO()
        image.save(buffer, format="PNG")

        handle_action(
            "/upload_trial_images",
            {"country": ["日本"], "view": ["trial"], "category": ["动物"], "trial_mode": ["parse"]},
            files={
                "trial_images": [
                    {"filename": "shiba-sakura.png", "content_type": "image/png", "content": buffer.getvalue()}
                ]
            },
        )

        assert APP.state.trial_row.subject == "柴犬樱花"
        assert APP.state.trial_row.operation_tag == "试新_日本_柴犬樱花0609"
        assert APP.state.trial_row.reference_image_url.startswith("/uploads/")
        assert "主体内容：柴犬樱花" in APP.state.trial_row.subject_description
        assert "色彩氛围：" in APP.state.trial_row.subject_description
        assert "构图环境：日式庭院樱花季" in APP.state.trial_row.subject_description
        assert "语义主体" not in APP.state.trial_row.subject_description
        assert "视觉LLM：真实openai" in APP.state.trial_row.remark
    finally:
        APP.agent.trial_uploads = previous_uploads


def test_trial_upload_uses_real_semantic_subject_in_operation_tag_and_feishu_payload(tmp_path):
    def fake_transport(payload, api_key):
        return {
            "output_text": json.dumps(
                {
                    "subject": "日式火车店铺少女",
                    "scene": "复古日式站台旁的街边店铺",
                    "culture_elements": ["日式火车", "街边店铺"],
                    "style": "明亮暖色写实插画",
                    "risk_tags": [],
                    "prompt_keywords": ["日本", "火车", "少女", "店铺"],
                    "confidence": 0.93,
                    "analysis": "真实视觉模型识别为日式火车店铺少女。",
                },
                ensure_ascii=False,
            )
        }

    from puzzle_ops.server import _demand_row_payload

    previous_uploads = APP.agent.trial_uploads
    try:
        APP.agent.trial_uploads = TrialImageUploadService(
            tmp_path / "uploads",
            vision_client=OpenAIVisionLLMClient(api_key="sk-test", transport=fake_transport),
        )
        APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")
        image = Image.new("RGB", (160, 100), (230, 160, 90))
        buffer = BytesIO()
        image.save(buffer, format="PNG")

        handle_action(
            "/upload_trial_images",
            {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]},
            files={
                "trial_images": [
                    {"filename": "train-shop-girl.png", "content_type": "image/png", "content": buffer.getvalue()}
                ]
            },
        )

        row = APP.state.trial_row
        payload = _demand_row_payload(row)
        assert row.subject == "日式火车店铺少女"
        assert row.operation_tag == "试新_日本_日式火车店铺少女0609"
        assert row.image_name == "train-shop-girl.png"
        assert row.reference_image_url.startswith("/uploads/")
        assert row.reference_image_path
        assert payload["_reference_image_path"] == row.reference_image_path
        assert payload["_reference_image_content_type"] == "image/png"
        assert payload["图片本身"] == [{"text": "train-shop-girl.png", "link": row.reference_image_url}]
    finally:
        APP.agent.trial_uploads = previous_uploads


def test_trial_upload_compacts_long_semantic_subject_for_operation_tag(tmp_path):
    def fake_transport(payload, api_key):
        return {
            "output_text": json.dumps(
                {
                    "subject": "游客群体含儿童与背包行人在观景步道上行走背景为传统日式多层塔楼建筑",
                    "scene": "日式多层塔楼建筑旁的观景步道",
                    "culture_elements": ["日式塔楼", "观景步道"],
                    "style": "明亮旅行纪实",
                    "risk_tags": [],
                    "prompt_keywords": ["日本", "游客", "塔楼"],
                    "confidence": 0.9,
                    "analysis": "主体较长，需要压缩运营 tag。",
                },
                ensure_ascii=False,
            )
        }

    previous_uploads = APP.agent.trial_uploads
    try:
        APP.agent.trial_uploads = TrialImageUploadService(
            tmp_path / "uploads",
            vision_client=OpenAIVisionLLMClient(api_key="sk-test", transport=fake_transport),
        )
        APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")
        image = Image.new("RGB", (160, 100), (230, 160, 90))
        buffer = BytesIO()
        image.save(buffer, format="PNG")

        handle_action(
            "/upload_trial_images",
            {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]},
            files={
                "trial_images": [
                    {"filename": "tourists-tower.png", "content_type": "image/png", "content": buffer.getvalue()}
                ]
            },
        )

        tag_subject = APP.state.trial_row.operation_tag.removeprefix("试新_日本_").removesuffix("0609")
        assert tag_subject == "游客塔楼"
        assert len(tag_subject) <= 8
    finally:
        APP.agent.trial_uploads = previous_uploads


def test_derive_upload_outputs_derivative_direction_without_claiming_real_generation():
    APP.state = AppState(country="法国", view="trial", category="花卉", trial_mode="derive")
    image = Image.new("RGB", (140, 140), (240, 210, 80))
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    handle_action(
        "/upload_trial_images",
        {"country": ["法国"], "view": ["trial"], "category": ["花卉"], "trial_mode": ["derive"]},
        files={
            "trial_images": [
                {"filename": "lavender-house.png", "content_type": "image/png", "content": buffer.getvalue()}
            ]
        },
    )

    assert "衍生方向" in APP.state.trial_row.remark
    assert "视觉LLM：未运行" in APP.state.trial_row.remark
    assert "已生成2张相似参考图" not in APP.state.trial_row.remark


def test_generate_trial_derivatives_requires_provider_without_faking_images():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")
    APP.agent.image_generator = None
    APP.state.trial_row = APP.agent.simulate_trial_upload("日本", "人物", "derive")

    handle_action("/generate_trial_derivatives", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]})

    assert "生成 provider 未配置" in APP.state.trial_row.remark
    assert APP.state.trial_rows == []


def test_generate_trial_derivatives_creates_two_audited_reference_rows(tmp_path):
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")
    APP.agent.image_generator = MockImageGenerationProvider(APP.agent._runtime_dir / "trial_uploads")
    APP.state.trial_row = APP.agent.simulate_trial_upload("日本", "人物", "derive").edited(
        reference_image_path=str(tmp_path / "good.png"),
        subject="日式塔楼游客",
        subject_description="主体内容：日式塔楼游客；色彩氛围：明亮清透；构图环境：海边步道远景。",
    )

    handle_action("/generate_trial_derivatives", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]})

    assert len(APP.state.trial_rows) == 2
    assert all("衍生参考图" in row.image_name for row in APP.state.trial_rows)
    assert all(row.reference_image_url.startswith("/uploads/") for row in APP.state.trial_rows)
    assert all("二次 VLM 解析与审核" in row.remark for row in APP.state.trial_rows)
    assert "已生成2张衍生参考图" in APP.state.trial_row.remark


def test_sync_trial_to_feishu_records_success_and_resets_trial_row():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")
    APP.agent.feishu = MockFeishuClient(APP.agent._runtime_dir / "test_feishu_mock")
    APP.agent.feishu.allow_real_sync = True
    APP.state.trial_rows = [APP.agent.simulate_trial_upload("日本", "人物", "parse")]

    redirect = handle_action("/sync_trial_feishu", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]})

    assert APP.state.view == "trial"
    assert APP.state.sync_message == "同步成功，当前已完成试新提需1条"
    assert APP.state.sync_url == APP.agent.feishu.web_url()
    assert APP.state.trial_rows == []
    assert "上传参考图" in APP.state.trial_row.image_name
    assert redirect is None
    assert any(row[2] == "提需同步" and row[4] == "成功" for row in APP.agent.sync_rows())


def test_sync_trial_rejects_empty_uploaded_rows_before_feishu_call():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")
    APP.agent.feishu = MockFeishuClient(APP.agent._runtime_dir / "test_feishu_mock")
    APP.agent.feishu.allow_real_sync = True
    before = len(APP.agent.sync_rows())

    redirect = handle_action("/sync_trial_feishu", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]})

    assert redirect is None
    assert APP.state.view == "trial"
    assert APP.state.sync_message == "请先上传解析图片或模拟上传，生成至少一条试新提需记录。"
    assert len(APP.agent.sync_rows()) == before


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


def test_save_trial_can_edit_subject_description():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")
    APP.state.trial_row = APP.agent.create_trial_demand("日本", "人物", "parse")

    handle_action(
        "/save_trial",
        {
            "country": ["日本"],
            "view": ["trial"],
            "category": ["人物"],
            "trial_mode": ["parse"],
            "operation_tag": ["试新_日本_寿司0609"],
            "priority": ["P1"],
            "count": ["3"],
            "method": ["先照片后AI"],
            "delivery_date": [""],
            "subject_description": ["主体内容：寿司；色彩氛围：暖色；构图环境：日式料理店。"],
            "remark": [""],
        },
    )

    assert APP.state.trial_row.subject_description == "主体内容：寿司；色彩氛围：暖色；构图环境：日式料理店。"


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
    assert candidate.rule_text in dict(APP.agent.value_rules("日本")).values()


def test_replace_schedule_action_records_slot_replacement():
    APP.state = AppState(country="日本", view="schedule", schedule_day="周一")
    original = APP.agent.schedule("日本", "周一")[0]

    handle_action("/replace_schedule", {"slot_index": ["0"], "image_name": [original.image_name]})

    assert 0 in APP.state.schedule_replacements
    assert APP.state.schedule_replacements[0].image_name != original.image_name
