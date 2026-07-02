from puzzle_ops.renderer import AppState
from puzzle_ops.server import APP, classify_generation_error, generation_error_recovery_hint, handle_action, redirect_location, update_state_from_query
from puzzle_ops.feishu import MockFeishuClient
from puzzle_ops.image_generation import DerivativeImage, ImageGenerationProvider, MockImageGenerationProvider
from puzzle_ops.trial_upload import TrialImageUploadService
from puzzle_ops.vision_llm import MissingVisionLLMConfig, OpenAIVisionLLMClient, VisionLLMResult
from PIL import Image
from datetime import date
from io import BytesIO
import json
import pytest


TODAY_SUFFIX = date.today().strftime("%m%d")


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        ("DashScope 图像生成失败：quota exceeded", "quota_exceeded"),
        ("DashScope 图像生成失败：Arrearage：Access denied, please make sure your account is in good standing", "billing_arrearage"),
        ("模型 qwen3-vl-flash 已下线，请迁移", "model_deprecated"),
        ("DashScope 图像生成超时：task_id=abc", "timeout"),
        ("HTTP 401 Unauthorized invalid api key", "auth_error"),
        ("生成 provider 未配置", "config_missing"),
        ("返回结构缺少 output.task_id", "response_schema"),
    ),
)
def test_classify_generation_error_for_common_provider_failures(message, expected):
    assert classify_generation_error(message) == expected


def test_generation_error_recovery_hint_explains_billing_arrearage():
    hint = generation_error_recovery_hint("billing_arrearage")

    assert "阿里云" in hint
    assert "欠费" in hint
    assert "资源包" in hint


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
    assert APP.state.need_rows[0].operation_tag == f"常规_日本_传统浴袍美女{TODAY_SUFFIX}"


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
        assert APP.state.trial_row.operation_tag == f"试新_日本_柴犬樱花{TODAY_SUFFIX}"
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
        assert row.operation_tag == f"试新_日本_日式火车店铺少女{TODAY_SUFFIX}"
        assert row.image_name == "train-shop-girl.png"
        assert row.reference_image_url.startswith("/uploads/")
        assert row.reference_image_path
        assert payload["_reference_image_path"] == row.reference_image_path
        assert payload["_reference_image_content_type"] == "image/png"
        assert payload["图片本身"] == [{"text": "train-shop-girl.png", "link": row.reference_image_url}]
        overview = APP.agent.memory_overview("日本")
        assert overview["感知记忆"]["count"] >= 1
        assert overview["结构化事实"]["count"] >= 1
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

        tag_subject = APP.state.trial_row.operation_tag.removeprefix("试新_日本_").removesuffix(TODAY_SUFFIX)
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
    assert all(row.reference_image_syncable is False for row in APP.state.trial_rows)
    assert all("二次 VLM 解析与审核" in row.remark for row in APP.state.trial_rows)
    assert "已生成2张衍生参考图" in APP.state.trial_row.remark
    assert APP.state.generation_event["source_operation_tag"] == APP.state.trial_row.operation_tag
    assert "mock-" in APP.state.generation_event["task_id"]
    assert ".png" in APP.state.generation_event["generated_image_paths"]
    assert APP.state.generation_event["second_review_status"] == "blocked"
    assert APP.state.generation_event["feishu_attachment_status"] == "blocked"


class PassingRealGenerationProvider(ImageGenerationProvider):
    provider_name = "cloud"

    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def healthcheck(self):
        return {
            "provider": "cloud",
            "configured": True,
            "message": "真实生成 provider 已配置",
            "model": "wanx-test",
            "base_url": "https://example.test/gen",
        }

    def generate_derivatives(self, reference_image, prompt, negative_prompt, count, seed, style_constraints):
        rows = []
        for index in range(count):
            path = self.output_dir / f"real_derivative_{index}.png"
            Image.new("RGB", (16, 16), (220, 180, 120)).save(path)
            rows.append(
                DerivativeImage(
                    image_id=f"real-{index}",
                    local_image_path=str(path),
                    provider="cloud",
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    seed=seed + index,
                    source_sample_id=str(style_constraints.get("source_sample_id", "")),
                    retained_features=("日式塔楼游客",),
                    changed_features=("季节元素",),
                    risk_notes=("生成图需二次 VLM 解析与审核",),
                    generated_at="2026-06-15T00:00:00",
                )
            )
        return tuple(rows)


class FailingGenerationProvider(ImageGenerationProvider):
    provider_name = "dashscope"

    def healthcheck(self):
        return {"provider": "dashscope", "configured": True, "message": "DashScope 图像生成 provider 已配置"}

    def generate_derivatives(self, reference_image, prompt, negative_prompt, count, seed, style_constraints):
        raise RuntimeError("DashScope 图像生成失败：quota exceeded")


class BillingArrearageGenerationProvider(ImageGenerationProvider):
    provider_name = "dashscope"

    def healthcheck(self):
        return {"provider": "dashscope", "configured": True, "ready": True, "message": "DashScope 图像生成 provider 已配置"}

    def generate_derivatives(self, reference_image, prompt, negative_prompt, count, seed, style_constraints):
        raise RuntimeError("DashScope 图像生成失败：Arrearage：Access denied, please make sure your account is in good standing")


def test_generate_trial_derivatives_failure_keeps_row_and_shows_message(tmp_path):
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")
    APP.agent.image_generator = FailingGenerationProvider()
    before = len(APP.agent.generation_events("日本"))
    APP.state.trial_row = APP.agent.simulate_trial_upload("日本", "人物", "derive").edited(
        reference_image_path=str(tmp_path / "good.png"),
        subject="日式塔楼游客",
        subject_description="主体内容：日式塔楼游客；色彩氛围：明亮清透；构图环境：海边步道远景。",
    )

    redirect = handle_action("/generate_trial_derivatives", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]})

    assert redirect is None
    assert APP.state.view == "trial"
    assert APP.state.trial_rows == []
    assert APP.state.trial_uploads == []
    assert "生成衍生参考图失败：DashScope 图像生成失败：quota exceeded" in APP.state.sync_message
    assert "错误类型=quota_exceeded" in APP.state.sync_message
    assert APP.state.generation_event["status"] == "failed"
    assert APP.state.generation_event["provider"] == "dashscope"
    assert APP.state.generation_event["error_type"] == "quota_exceeded"
    assert APP.state.generation_event["source_operation_tag"] == APP.state.trial_row.operation_tag
    assert APP.state.generation_event["generated_image_paths"] == ""
    assert APP.state.generation_event["second_review_status"] == "not_started"
    assert APP.state.generation_event["feishu_attachment_status"] == "blocked"
    assert "生成衍生参考图失败" in APP.state.trial_row.remark
    assert APP.state.trial_row.subject == "日式塔楼游客"
    events = APP.agent.generation_events("日本")
    assert len(events) == before + 1
    assert events[-1]["status"] == "failed"
    assert events[-1]["error_type"] == "quota_exceeded"
    assert events[-1]["provider"] == "dashscope"
    overview = APP.agent.memory_overview("日本")
    assert overview["短期记忆"]["count"] >= 1


def test_generate_trial_derivatives_billing_arrearage_shows_recovery_hint(tmp_path):
    APP.state = AppState(country="法国", view="trial", category="花卉", trial_mode="derive")
    APP.agent.image_generator = BillingArrearageGenerationProvider()
    APP.state.trial_row = APP.agent.simulate_trial_upload("法国", "花卉", "derive").edited(
        reference_image_path=str(tmp_path / "good.png"),
        subject="海滩野餐",
    )

    redirect = handle_action("/generate_trial_derivatives", {"country": ["法国"], "view": ["trial"], "category": ["花卉"], "trial_mode": ["derive"]})

    assert redirect is None
    assert APP.state.generation_event["error_type"] == "billing_arrearage"
    assert "阿里云" in APP.state.generation_event["recovery_hint"]
    assert "欠费" in APP.state.sync_message
    assert "资源包" in APP.state.sync_message


def test_check_generation_provider_action_reports_diagnostic_status(tmp_path):
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")
    APP.agent.image_generator = PassingRealGenerationProvider(tmp_path)

    redirect = handle_action("/check_generation_provider", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]})

    assert redirect is None
    assert APP.state.view == "trial"
    assert "生成 Provider 诊断" in APP.state.sync_message
    assert "provider=cloud" in APP.state.sync_message
    assert "configured=True" in APP.state.sync_message
    assert "wanx-test" in APP.state.sync_message
    assert "https://example.test/gen" in APP.state.sync_message


def test_generation_provider_diagnostic_includes_dashscope_readiness(tmp_path):
    class DiagnosticDashScopeProvider(ImageGenerationProvider):
        provider_name = "dashscope"

        def healthcheck(self):
            return {
                "provider": "dashscope",
                "configured": True,
                "model": "wan2.6-image",
                "api_key_source": "QWEN_API_KEY",
                "sdk_available": False,
                "base_url": "DashScope SDK ImageGeneration",
                "message": "DashScope 参考图生成 provider 已配置：wan2.6-image；SDK 未安装",
            }

    APP.state = AppState(country="法国", view="trial", category="人物", trial_mode="derive")
    APP.agent.image_generator = DiagnosticDashScopeProvider()

    redirect = handle_action("/check_generation_provider", {"country": ["法国"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]})

    assert redirect is None
    assert "api_key_source=QWEN_API_KEY" in APP.state.sync_message
    assert "sdk_available=False" in APP.state.sync_message


class FakeGeneratedImageVisionClient:
    provider = "qwen"

    def __init__(self, result: VisionLLMResult):
        self.result = result
        self.calls = []

    def config_status(self):
        return {"provider": "qwen", "mode": "real", "model": "qwen3.7-plus"}

    def analyze(self, images, country, category, local_summary):
        self.calls.append((images, country, category, local_summary))
        return self.result


def _safe_generated_result() -> VisionLLMResult:
    return VisionLLMResult(
        subject="日式塔楼游客",
        scene="海边步道与日式塔楼，游客群体在前景行走",
        culture_elements=("日式塔楼", "海边步道"),
        style="明亮清透的日式旅游插画",
        risk_tags=(),
        prompt_keywords=("日式塔楼", "游客", "海边步道"),
        confidence=0.93,
        provider="qwen",
        raw_text="主体清晰、无明显IP或商标风险。",
    )


def _risky_generated_result() -> VisionLLMResult:
    return VisionLLMResult(
        subject="红色连衣裙黑发女孩",
        scene="高度接近知名动漫电影里的街道场景",
        culture_elements=("日式街道",),
        style="知名动漫工作室风格",
        risk_tags=("版权/IP风险",),
        prompt_keywords=("动漫角色", "红色连衣裙黑发女孩"),
        confidence=0.91,
        provider="qwen",
        raw_text="画面含动漫角色化脸型，疑似知名动漫/IP混淆风险。",
    )


def test_real_generation_derivatives_require_vlm_second_review_before_sync(tmp_path):
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")
    APP.agent.image_generator = PassingRealGenerationProvider(tmp_path)
    fake_vision = FakeGeneratedImageVisionClient(_safe_generated_result())
    APP.agent.trial_uploads = TrialImageUploadService(tmp_path / "review_uploads", vision_client=fake_vision)
    APP.state.trial_row = APP.agent.simulate_trial_upload("日本", "人物", "derive").edited(
        reference_image_path=str(tmp_path / "good.png"),
        subject="日式塔楼游客",
        subject_description="主体内容：日式塔楼游客；色彩氛围：明亮清透；构图环境：海边步道远景。",
    )

    handle_action("/generate_trial_derivatives", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]})

    assert len(APP.state.trial_rows) == 2
    assert all(row.generation_review_status == "passed" for row in APP.state.trial_rows)
    assert all(row.human_approved is False for row in APP.state.trial_rows)
    assert all(row.reference_image_syncable is False for row in APP.state.trial_rows)
    assert all("二次 VLM 解析与审核通过" in row.remark for row in APP.state.trial_rows)
    assert all(row.subject_description.startswith("主体内容：日式塔楼游客；色彩氛围：明亮清透的日式旅游插画；构图环境：海边步道") for row in APP.state.trial_rows)
    assert all(row.reference_image_path.endswith(".png") for row in APP.state.trial_rows)
    assert len(fake_vision.calls) == 2
    assert APP.state.generation_event["second_review_status"] == "passed"
    assert APP.state.generation_event["feishu_attachment_status"] == "pending_human_approval"

    handle_action("/sync_trial_feishu", {"country": ["日本"], "view": ["trial"]})

    assert len(APP.state.trial_rows) == 2
    assert "运营确认" in APP.state.sync_message

    handle_action("/approve_generated_derivatives", {"country": ["日本"], "view": ["trial"]})

    assert all(row.human_approved is True for row in APP.state.trial_rows)
    assert all(row.reference_image_syncable is True for row in APP.state.trial_rows)
    assert APP.state.generation_event["feishu_attachment_status"] == "ready"
    assert "运营已确认" in APP.state.sync_message


def test_real_generation_derivatives_with_vlm_risk_stay_unsyncable(tmp_path):
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")
    APP.agent.image_generator = PassingRealGenerationProvider(tmp_path)
    APP.agent.trial_uploads = TrialImageUploadService(
        tmp_path / "risk_review_uploads",
        vision_client=FakeGeneratedImageVisionClient(_risky_generated_result()),
    )
    APP.state.trial_row = APP.agent.simulate_trial_upload("日本", "人物", "derive").edited(
        reference_image_path=str(tmp_path / "good.png"),
        subject="日式塔楼游客",
        subject_description="主体内容：日式塔楼游客；色彩氛围：明亮清透；构图环境：海边步道远景。",
    )

    handle_action("/generate_trial_derivatives", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]})

    assert len(APP.state.trial_rows) == 2
    assert all(row.reference_image_syncable is False for row in APP.state.trial_rows)
    assert all("二次 VLM 解析未通过" in row.remark for row in APP.state.trial_rows)
    assert all("版权/IP风险" in row.remark for row in APP.state.trial_rows)


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
    overview = APP.agent.memory_overview("日本")
    assert overview["长期记忆"]["count"] >= 1


def test_save_harness_override_action_writes_hitl_memory():
    APP.state = AppState(country="日本", view="eval")

    handle_action(
        "/save_harness_override",
        {
            "country": ["日本"],
            "view": ["eval"],
            "sample_id": ["real-001"],
            "task_type": ["value_match_eval"],
            "human_override": ["人工修正：寿司图应匹配本土饮食文化，不匹配动物互动。"],
        },
    )

    assert APP.state.view == "eval"
    assert any("real-001" in memory["content"] and "本土饮食文化" in memory["content"] for memory in APP.agent.hitl_memories("日本"))


def test_export_harness_overrides_action_writes_csv_and_status_message():
    APP.state = AppState(country="日本", view="eval")
    APP.agent.record_harness_override("日本", "real-001", "value_match_eval", "人工修正：寿司图应匹配本土饮食文化。")

    handle_action(
        "/export_harness_overrides",
        {
            "country": ["日本"],
            "view": ["eval"],
        },
    )

    assert APP.state.view == "eval"
    assert "已导出 Harness 人工修正" in APP.state.sync_message
    export_path = APP.agent._runtime_dir / "harness_overrides_日本.csv"
    assert export_path.exists()
    assert "real-001,value_match_eval,人工修正：寿司图应匹配本土饮食文化。,日本" in export_path.read_text(encoding="utf-8")


def test_export_harness_annotations_action_writes_label_tool_files():
    APP.state = AppState(country="日本", view="eval")
    APP.agent.record_harness_override("日本", "syn-001", "value_match_eval", "人工修正：补充日本市场价值观证据。")

    handle_action(
        "/export_harness_annotations",
        {
            "country": ["日本"],
            "view": ["eval"],
        },
    )

    assert APP.state.view == "eval"
    assert "已导出标注平台文件" in APP.state.sync_message
    export_dir = APP.agent._runtime_dir / "harness_annotation_exports"
    assert (export_dir / "argilla_harness_日本.jsonl").exists()
    assert (export_dir / "label_studio_harness_日本.json").exists()


def test_save_harness_gold_label_action_updates_dataset_and_memory(monkeypatch, tmp_path):
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
    APP.state = AppState(country="日本", view="eval")

    handle_action(
        "/save_harness_gold_label",
        {
            "country": ["日本"],
            "view": ["eval"],
            "sample_id": ["real-001"],
            "gold_grade": ["S"],
            "gold_subject": ["寿司拼盘"],
            "gold_color_mood": ["米白与鲑鱼橙，清爽明亮"],
            "gold_composition": ["日式料理桌面近景"],
            "gold_value_labels": ["本土饮食文化;治愈食物"],
            "gold_risk_labels": [""],
            "human_note": ["运营确认"],
            "position": ["7"],
            "open_rate": ["0.42"],
            "completion_rate": ["0.91"],
            "avg_finish_time": ["38"],
        },
    )

    assert APP.state.view == "eval"
    assert "Gold Label 已保存" in APP.state.sync_message
    assert "寿司拼盘" in dataset.read_text(encoding="utf-8")
    assert ",7,0.42,0.91,38," in dataset.read_text(encoding="utf-8")
    assert any("寿司拼盘" in memory["content"] for memory in APP.agent.hitl_memories("日本"))


def test_export_harness_gold_skeleton_action_creates_csv(tmp_path, monkeypatch):
    monkeypatch.delenv("PUZZLEOPS_HARNESS_DATASET", raising=False)
    APP.state = AppState(country="日本", view="eval")
    path = APP.agent._runtime_dir / "harness_gold_samples_日本.csv"
    if path.exists():
        path.unlink()

    handle_action("/export_harness_gold_skeleton", {"country": ["日本"], "view": ["eval"]})

    assert APP.state.view == "eval"
    assert "已生成 Gold Dataset 骨架" in APP.state.sync_message
    assert path.exists()
    assert "sample_id,country,local_image_path" in path.read_text(encoding="utf-8")


def test_auto_prelabeled_harness_gold_action_runs_agent(monkeypatch):
    APP.state = AppState(country="法国", view="eval")
    calls = []

    def fake_auto(country):
        calls.append(country)
        return {"updated_count": 5, "skipped_count": 0, "dataset": "/tmp/harness_gold_samples_法国.csv"}

    monkeypatch.setattr(APP.agent, "auto_prelabeled_harness_samples", fake_auto)

    handle_action("/auto_prelabeled_harness_gold", {"country": ["法国"], "view": ["eval"]})

    assert calls == ["法国"]
    assert APP.state.view == "eval"
    assert "AI 预标注完成：5 条" in APP.state.sync_message


def test_register_harness_real_samples_text_action_runs_agent(monkeypatch):
    APP.state = AppState(country="法国", view="eval")
    calls = []

    def fake_register(country, text):
        calls.append((country, text))
        return {"registered_count": 2, "dataset": "/tmp/harness_gold_samples_法国.csv"}

    monkeypatch.setattr(APP.agent, "register_harness_real_samples_from_text", fake_register)

    handle_action(
        "/register_harness_real_samples",
        {
            "country": ["法国"],
            "view": ["eval"],
            "samples_text": ["A /tmp/france-picnic.png\n/tmp/lavender.png,S"],
        },
    )

    assert calls == [("法国", "A /tmp/france-picnic.png\n/tmp/lavender.png,S")]
    assert APP.state.view == "eval"
    assert "真实样本已登记：2 条" in APP.state.sync_message


def test_register_harness_real_samples_text_action_can_auto_prelabeled(monkeypatch):
    APP.state = AppState(country="法国", view="eval")
    calls = []

    def fake_register(country, text):
        calls.append(("register", country, text))
        return {"registered_count": 1, "dataset": "/tmp/harness_gold_samples_法国.csv"}

    def fake_auto(country, max_count=None):
        calls.append(("auto", country, max_count))
        return {"updated_count": 1, "skipped_count": 0, "dataset": "/tmp/harness_gold_samples_法国.csv"}

    monkeypatch.setattr(APP.agent, "register_harness_real_samples_from_text", fake_register)
    monkeypatch.setattr(APP.agent, "auto_prelabeled_harness_samples", fake_auto)

    handle_action(
        "/register_harness_real_samples",
        {
            "country": ["法国"],
            "view": ["eval"],
            "samples_text": ["S /tmp/lavender.png"],
            "auto_prelabeled": ["1"],
        },
    )

    assert calls == [("register", "法国", "S /tmp/lavender.png"), ("auto", "法国", 5)]
    assert "AI 预标注 1 条" in APP.state.sync_message


def test_register_harness_real_samples_directory_action_runs_agent(monkeypatch):
    APP.state = AppState(country="法国", view="eval")
    calls = []

    def fake_register_directory(country, directory, grade_text, js_category="real_sample"):
        calls.append((country, directory, grade_text, js_category))
        return {"registered_count": 5, "image_count": 5, "dataset": "/tmp/harness_gold_samples_法国.csv"}

    monkeypatch.setattr(APP.agent, "register_harness_real_samples_from_directory", fake_register_directory)

    handle_action(
        "/register_harness_real_samples",
        {
            "country": ["法国"],
            "view": ["eval"],
            "image_dir": ["/Users/fanglemin/Desktop/图片"],
            "directory_grade_text": ["1A 2A 3B 4S 5C"],
            "directory_js_category": ["lifestyle"],
        },
    )

    assert calls == [("法国", "/Users/fanglemin/Desktop/图片", "1A 2A 3B 4S 5C", "lifestyle")]
    assert APP.state.view == "eval"
    assert "真实样本目录已登记：5/5 张" in APP.state.sync_message


def test_approve_harness_silver_labels_action_runs_agent(monkeypatch):
    APP.state = AppState(country="法国", view="eval")
    calls = []

    def fake_approve(country, **kwargs):
        calls.append((country, kwargs))
        return {
            "approved_count": 5,
            "skipped_count": 0,
            "fact_memory_count": 5,
            "rag_human_gold_count": 5,
            "dataset": "/tmp/harness_gold_samples_法国.csv",
        }

    monkeypatch.setattr(APP.agent, "approve_harness_silver_labels", fake_approve)

    handle_action(
        "/approve_harness_silver_labels",
        {"country": ["法国"], "view": ["eval"], "reviewer_note": ["抽查通过"]},
    )

    assert calls == [("法国", {"reviewer_note": "抽查通过"})]
    assert APP.state.view == "eval"
    assert "AI Silver 已确认晋升：5 条" in APP.state.sync_message
    assert "Facts 5 条" in APP.state.sync_message
    assert "RAG human_gold 5 条" in APP.state.sync_message


def test_approve_harness_silver_labels_action_passes_selected_sample_ids(monkeypatch):
    APP.state = AppState(country="法国", view="eval")
    calls = []

    def fake_approve(country, **kwargs):
        calls.append((country, kwargs))
        return {"approved_count": 1, "skipped_count": 0, "dataset": "/tmp/harness_gold_samples_法国.csv"}

    monkeypatch.setattr(APP.agent, "approve_harness_silver_labels", fake_approve)

    handle_action(
        "/approve_harness_silver_labels",
        {
            "country": ["法国"],
            "view": ["eval"],
            "reviewer_note": ["只确认第1条"],
            "sample_id": ["fr-real-001"],
        },
    )

    assert calls == [("法国", {"sample_ids": ("fr-real-001",), "reviewer_note": "只确认第1条"})]
    assert "AI Silver 已确认晋升：1 条" in APP.state.sync_message


def test_run_harness_action_requires_explicit_generation_opt_in(monkeypatch):
    APP.state = AppState(country="日本", view="eval")
    calls = []
    original = APP.agent.harness_run

    def fake_harness_run(country, **kwargs):
        calls.append((country, kwargs))
        return original(country, execute_models=False, execute_generation=False, save=False)

    monkeypatch.setattr(APP.agent, "harness_run", fake_harness_run)

    handle_action("/run_harness", {"country": ["日本"], "view": ["eval"], "run_real_models": ["1"]})

    assert calls[0][0] == "日本"
    assert calls[0][1]["execute_models"] is True
    assert calls[0][1]["execute_generation"] is False
    assert calls[0][1]["save"] is True
    assert "真实 VLM Harness" in APP.state.sync_message


def test_replace_schedule_action_records_slot_replacement():
    APP.state = AppState(country="日本", view="schedule", schedule_day="周一")
    original = APP.agent.schedule("日本", "周一")[0]

    handle_action("/replace_schedule", {"slot_index": ["0"], "image_name": [original.image_name]})

    assert 0 in APP.state.schedule_replacements
    assert APP.state.schedule_replacements[0].image_name != original.image_name


def test_memory_governance_actions_promote_and_retire_memory():
    APP.state = AppState(country="日本", view="runtime")
    source_id = APP.agent.record_perception_memory("日本", "route_test", {"subject": "寿司路由测试"})

    handle_action(
        "/promote_memory",
        {
            "country": ["日本"],
            "view": ["runtime"],
            "memory_id": [str(source_id)],
            "target_layer": ["facts"],
            "human_note": ["运营确认路由测试事实"],
        },
    )

    promoted = next(
        row
        for row in APP.agent.memory_debug("日本", query="寿司路由测试")
        if row["source_memory_id"] == source_id and row["status"] == "active"
    )
    assert "Memory 晋升成功" in APP.state.sync_message

    handle_action(
        "/retire_memory",
        {"country": ["日本"], "view": ["runtime"], "memory_id": [str(promoted["memory_id"])]},
    )

    retired = next(
        row
        for row in APP.agent.memory_debug("日本", query="寿司路由测试", limit=100)
        if row["memory_id"] == promoted["memory_id"]
    )
    assert retired["status"] == "retired"
    assert "不再进入 RAG" in APP.state.sync_message


def test_rebuild_rag_knowledge_action_reports_file_eval(monkeypatch, tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    raw = knowledge_dir / "raw"
    eval_dir = knowledge_dir / "eval"
    raw.mkdir(parents=True)
    eval_dir.mkdir(parents=True)
    (raw / "japan.md").write_text(
        """---
country: 日本
source_type: value_rule
knowledge_version: unit-test
---
# 日本价值观

## 寿司文化 {#JP_KB_SUSHI_FOOD}
寿司属于日本本土饮食文化。
""",
        encoding="utf-8",
    )
    (eval_dir / "value_audit_cases.jsonl").write_text(
        json.dumps(
            {
                "query": "日本寿司图是否符合本土饮食价值观",
                "country": "日本",
                "expected_parent_id": "JP_KB_SUSHI_FOOD",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_RAG_KNOWLEDGE_DIR", str(knowledge_dir))
    APP.state = AppState(country="日本", view="runtime")

    handle_action("/rebuild_rag_knowledge", {"country": ["日本"], "view": ["runtime"]})

    assert APP.state.view == "runtime"
    assert "RAG 知识库已重建" in APP.state.sync_message
    assert "hit@5=1.0" in APP.state.sync_message


def test_reindex_rag_qdrant_action_reports_upsert(monkeypatch):
    APP.state = AppState(country="日本", view="runtime")

    def fake_reindex(country):
        assert country == "日本"
        return {
            "status": "indexed",
            "upserted_points": 12,
            "chunk_count": 12,
            "vector_size": 3,
            "hit@5": 1.0,
            "mrr@5": 0.9,
            "qdrant_collection": "puzzle_ops_rag",
            "manifest_path": "/tmp/qdrant_reindex_日本.json",
        }

    monkeypatch.setattr(APP.agent, "reindex_rag_qdrant_from_raw", fake_reindex)

    handle_action("/reindex_rag_qdrant", {"country": ["日本"], "view": ["runtime"]})

    assert APP.state.view == "runtime"
    assert "Qdrant RAG 已重建入库" in APP.state.sync_message
    assert "points=12" in APP.state.sync_message
    assert "vector_size=3" in APP.state.sync_message
    assert "hit@5=1.0" in APP.state.sync_message
    assert "manifest=/tmp/qdrant_reindex_日本.json" in APP.state.sync_message


def test_qdrant_smoke_action_reports_search_and_cleanup(monkeypatch):
    APP.state = AppState(country="日本", view="runtime")

    def fake_smoke(country):
        assert country == "日本"
        return {"status": "passed", "search_hit": True, "cleanup_status": "deleted", "vector_size": 3}

    monkeypatch.setattr(APP.agent, "run_qdrant_smoke_diagnostic", fake_smoke)

    handle_action("/qdrant_smoke_diagnostic", {"country": ["日本"], "view": ["runtime"]})

    assert APP.state.view == "runtime"
    assert "Qdrant smoke 诊断完成" in APP.state.sync_message
    assert "status=passed" in APP.state.sync_message
    assert "cleanup=deleted" in APP.state.sync_message


def test_qdrant_manifest_rollback_action_sets_latest_run(monkeypatch):
    APP.state = AppState(country="日本", view="runtime")

    def fake_rollback(country, run_id):
        assert country == "日本"
        assert run_id == "target-run"
        return {
            "status": "rolled_back",
            "run_id": run_id,
            "vector_size": 5,
            "upserted_points": 9,
            "restore_status": {"status": "manifest_pointer_only", "restored_points": 0},
        }

    monkeypatch.setattr(APP.agent, "rollback_qdrant_manifest", fake_rollback)

    handle_action("/rollback_qdrant_manifest", {"country": ["日本"], "view": ["runtime"], "run_id": ["target-run"]})

    assert APP.state.view == "runtime"
    assert "Qdrant manifest 已回滚" in APP.state.sync_message
    assert "run_id=target-run" in APP.state.sync_message
    assert "points=9" in APP.state.sync_message
    assert "restore=manifest_pointer_only" in APP.state.sync_message


def test_record_rag_feedback_action_writes_working_memory():
    APP.state = AppState(country="日本", view="trial", trial_mode="parse")

    handle_action(
        "/record_rag_feedback",
        {
            "country": ["日本"],
            "view": ["trial"],
            "trial_mode": ["parse"],
            "chunk_id": ["JP_VALUE_001#chunk-1"],
            "usefulness": ["useful"],
            "note": ["这条依据能解释寿司价值观"],
        },
    )

    rows = APP.agent.memory_debug("日本", query="寿司价值观", limit=50)
    assert APP.state.view == "trial"
    assert "RAG 依据反馈已记录" in APP.state.sync_message
    assert any(
        row["memory_type"] == "rag_citation_feedback"
        and row["payload"]["chunk_id"] == "JP_VALUE_001#chunk-1"
        and row["payload"]["usefulness"] == "useful"
        for row in rows
    )
