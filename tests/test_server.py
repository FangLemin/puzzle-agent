from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.renderer import AppState, render_page
from puzzle_ops.server import APP, classify_generation_error, generation_error_recovery_hint, handle_action, redirect_location, update_state_from_query
from puzzle_ops.feishu import MockFeishuClient
from puzzle_ops.image_generation import DerivativeImage, ImageGenerationProvider, MockImageGenerationProvider
from puzzle_ops.rag import RagProviderConfig, RagVectorStoreConfig
from puzzle_ops.storage import PuzzleRepository
from puzzle_ops.trial_upload import TrialImageUploadService
from puzzle_ops.vision_llm import MissingVisionLLMConfig, OpenAIVisionLLMClient, VisionLLMResult
from PIL import Image
from datetime import date
from io import BytesIO
from pathlib import Path
import json
import pytest
import threading
import time


TODAY_SUFFIX = date.today().strftime("%m%d")


def wait_until(predicate, timeout: float = 1.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return bool(predicate())


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        ("DashScope 图像生成失败：quota exceeded", "quota_exceeded"),
        ("DashScope 图像生成失败：Arrearage：Access denied, please make sure your account is in good standing", "billing_arrearage"),
        ("模型 qwen3-vl-flash 已下线，请迁移", "model_deprecated"),
        ("DashScope 图像生成超时：task_id=abc", "timeout"),
        ("HTTPSConnectionPool(host='Qwen.aliyuncs.com', port=443): Failed to resolve 'Qwen.aliyuncs.com'", "endpoint_dns"),
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


def test_server_keeps_state_isolated_by_user_and_country():
    APP.session_states = {}

    japan = APP.state_for_params({"user_id": ["jp_owner"], "country": ["日本"]})
    france = APP.state_for_params({"user_id": ["fr_owner"], "country": ["法国"]})
    readonly_brazil = APP.state_for_params({"user_id": ["br_ru_owner"], "country": ["巴西"]})

    japan.need_rows.append(APP.agent.add_regular_demand("日本", "animal", "常规_日本_猫咪鲤鱼0605", 0))

    assert japan is APP.state_for_params({"user_id": ["jp_owner"], "country": ["日本"]})
    assert france is not japan
    assert readonly_brazil is not japan
    assert france.need_rows == []
    assert readonly_brazil.country == "巴西"


def test_redirect_location_percent_encodes_chinese_state():
    state = AppState(country="日本", view="regular")

    location = redirect_location(state)

    assert "country=%E6%97%A5%E6%9C%AC" in location
    assert "view=regular" in location
    assert "trial_mode=parse" in location
    assert "schedule_day=%E5%91%A8%E4%B8%80" in location


def test_add_regular_action_uses_submitted_context_and_adds_need_row():
    APP.state = AppState(country="日本", view="regular", category="animal", tag="常规_日本_猫咪鲤鱼0605")

    handle_action(
        "/add_regular",
        {
            "country": ["日本"],
            "category": ["animal"],
            "tag": ["常规_日本_猫咪鲤鱼0605"],
            "image_index": ["0"],
        },
    )

    assert APP.state.view == "regular"
    assert len(APP.state.need_rows) == 1
    assert APP.state.need_rows[0].operation_tag == f"常规_日本_猫咪鲤鱼{TODAY_SUFFIX}"


def test_add_regular_all_adds_all_reference_images_for_current_tag():
    APP.state = AppState(country="日本", view="regular", category="animal", tag="常规_日本_猫咪鲤鱼0605")

    handle_action(
        "/add_regular_all",
        {
            "country": ["日本"],
            "view": ["regular"],
            "category": ["animal"],
            "tag": ["常规_日本_猫咪鲤鱼0605"],
        },
    )

    assert APP.state.view == "regular"
    assert len(APP.state.need_rows) == len(APP.agent.images_for_tag("日本", "常规_日本_猫咪鲤鱼0605"))


def test_readonly_country_blocks_write_action():
    APP.state = AppState(user_id="jp_owner", country="法国", view="regular", category="objects", tag="试新_法国_赛车0526")

    handle_action(
        "/add_regular",
        {
            "user_id": ["jp_owner"],
            "country": ["法国"],
            "category": ["objects"],
            "tag": ["试新_法国_赛车0526"],
            "image_index": ["0"],
        },
    )

    assert APP.state.view == "regular"
    assert APP.state.need_rows == []
    assert "只读权限" in APP.state.sync_message


def test_create_production_backup_route_writes_backup(monkeypatch, tmp_path):
    from puzzle_ops.agents import PuzzleOpsAgent

    previous_agent = APP.agent
    try:
        monkeypatch.setenv("PUZZLEOPS_RUNTIME_DIR", str(tmp_path / "prod_runtime"))
        APP.agent = PuzzleOpsAgent()
        APP.state = AppState(country="日本", view="runtime", user_id="jp_owner")

        handle_action(
            "/create_production_backup",
            {"country": ["日本"], "view": ["runtime"], "user_id": ["jp_owner"], "backup_label": ["launch"]},
        )

        assert "生产运行数据已备份" in APP.state.sync_message
        assert (tmp_path / "prod_runtime" / "backups").exists()
    finally:
        APP.agent = previous_agent


def test_generate_descriptions_action_updates_existing_need_rows(monkeypatch):
    def transport(payload, api_key, endpoint):
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"subject_description":"主体内容：幼猫坐在日式庭院锦鲤池边；色彩氛围：柔粉、湖蓝与暖米白自然光；构图环境：近景浅景深，猫与锦鲤形成互动层次。",'
                            '"remark":"保留猫鱼互动层次。"}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setenv("QWEN_API_KEY", "qwen-key")
    APP.state = AppState(country="日本", view="regular", category="animal", tag="常规_日本_猫咪鲤鱼0605")
    APP.agent._description_prompt_transport = transport
    APP.state.need_rows.append(APP.agent.add_regular_demand("日本", "animal", "常规_日本_猫咪鲤鱼0605", 0))
    APP.state.need_rows.append(APP.agent.add_regular_demand("日本", "animal", "常规_日本_幼猫0608", 0))

    handle_action("/generate_descriptions", {"selected_rows": ["1"]})

    assert APP.state.need_rows[0].subject_description == ""
    assert APP.state.need_rows[1].subject_description.startswith("主体内容：幼猫坐在日式庭院")
    assert APP.state.need_rows[1].remark == "保留猫鱼互动层次。"
    assert APP.state.description_benchmarks == []


def test_generate_description_benchmark_action_creates_comparison_without_polluting_normal_flow():
    APP.state = AppState(country="日本", view="regular", category="animal", tag="常规_日本_猫咪鲤鱼0605")
    APP.state.need_rows.append(APP.agent.add_regular_demand("日本", "animal", "常规_日本_猫咪鲤鱼0605", 0))

    handle_action("/generate_description_benchmark", {"selected_rows": ["0"]})

    assert len(APP.state.description_benchmarks) == 1
    assert APP.state.show_prompt_benchmark is True
    assert APP.state.description_benchmarks[0]["template_subject_description"]
    assert "Prompt baseline v3" in APP.state.description_benchmarks[0]["prompt"]


def test_save_description_benchmark_persists_scores(tmp_path):
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
        APP.state = AppState(country="日本", view="regular", user_id="jp_owner")
        APP.state.description_benchmarks = [{"operation_tag": "常规_日本_猫咪鲤鱼0605"}]

        handle_action(
            "/save_description_benchmark",
            {
                "country": ["日本"],
                "view": ["regular"],
                "image_name": ["猫咪鲤鱼"],
                "operation_tag": ["常规_日本_猫咪鲤鱼0605"],
                "template_output": ["主体内容：猫咪鲤鱼；色彩氛围：浅粉；构图环境：庭院。"],
                "prompt_output": ["主体内容：猫咪与锦鲤池；色彩氛围：浅粉、湖蓝、明亮治愈；构图环境：日式庭院近景。"],
                "template_benchmark_score_0": ["4"],
                "template_benchmark_score_1": ["2"],
                "template_benchmark_score_2": ["1"],
                "template_benchmark_score_3": ["3"],
                "template_benchmark_score_4": ["1"],
                "prompt_benchmark_score_0": ["4"],
                "prompt_benchmark_score_1": ["3"],
                "prompt_benchmark_score_2": ["4"],
                "prompt_benchmark_score_3": ["3"],
                "prompt_benchmark_score_4": ["4"],
                "template_benchmark_label": ["需要大改"],
                "prompt_benchmark_label": ["轻微修改"],
            },
        )

        rows = APP.agent.repository.description_benchmark_scores("日本")
        assert len(rows) == 1
        assert rows[0]["prompt_label"] == "轻微修改"
        assert rows[0]["template_scores"]["production_actionability"] == 2
        assert rows[0]["prompt_scores"]["conciseness"] == 4
        assert rows[0]["template_score_1"] == 2
        assert rows[0]["prompt_score_2"] == 4
        assert "Prompt Benchmark 评分已保存" in APP.state.sync_message
        assert "prompt平均 3.6" in APP.state.sync_message
    finally:
        APP.agent = previous_agent


def test_save_description_benchmark_persists_batch_scores(tmp_path):
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
        APP.state = AppState(country="日本", view="regular", user_id="jp_owner")

        handle_action(
            "/save_description_benchmark",
            {
                "country": ["日本"],
                "view": ["regular"],
                "benchmark_count": ["2"],
                "image_name_0": ["樱花列车"],
                "operation_tag_0": ["常规_日本_樱花列车0728"],
                "template_output_0": ["主体内容：樱花列车；色彩氛围：暖色；构图环境：轨道。"],
                "prompt_output_0": ["主体内容：日本通勤电车穿行樱花林荫道；色彩氛围：粉白暖光；构图环境：浅景深纵深。"],
                "template_benchmark_label_0": ["需要大改"],
                "prompt_benchmark_label_0": ["可直接用"],
                "image_name_1": ["寿司"],
                "operation_tag_1": ["常规_日本_寿司0728"],
                "template_output_1": ["主体内容：寿司；色彩氛围：综合色；构图环境：桌面。"],
                "prompt_output_1": ["主体内容：寿司拼盘搭配日式餐具；色彩氛围：米白橙红；构图环境：餐桌近景俯拍。"],
                "template_benchmark_label_1": ["不可用"],
                "prompt_benchmark_label_1": ["轻微修改"],
                **{f"template_benchmark_score_0_{index}": ["1"] for index in range(5)},
                **{f"prompt_benchmark_score_0_{index}": ["5"] for index in range(5)},
                **{f"template_benchmark_score_1_{index}": ["2"] for index in range(5)},
                **{f"prompt_benchmark_score_1_{index}": ["4"] for index in range(5)},
            },
        )

        rows = APP.agent.repository.description_benchmark_scores("日本")
        assert len(rows) == 2
        assert {row["operation_tag"] for row in rows} == {"常规_日本_樱花列车0728", "常规_日本_寿司0728"}
        assert rows[0]["prompt_scores"]["subject_accuracy"] in {4, 5}
        assert "批量保存 2 条" in APP.state.sync_message
    finally:
        APP.agent = previous_agent


def test_save_value_prediction_benchmark_persists_batch_scores(tmp_path):
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
        APP.state = AppState(country="日本", view="value", user_id="jp_owner")

        handle_action(
            "/save_value_prediction_benchmark",
            {
                "country": ["日本"],
                "view": ["value"],
                "benchmark_count": ["2"],
                "candidate_id_0": ["JP_CAND_001"],
                "operation_tag_0": ["试新_日本_樱花"],
                "baseline_output_0": ["当前预测"],
                "candidate_output_0": ["候选预测"],
                "baseline_label_0": ["轻微修改"],
                "candidate_label_0": ["可直接用"],
                "candidate_id_1": ["JP_CAND_002"],
                "operation_tag_1": ["试新_日本_寿司"],
                "baseline_output_1": ["当前预测2"],
                "candidate_output_1": ["候选预测2"],
                "baseline_label_1": ["需要大改"],
                "candidate_label_1": ["轻微修改"],
                **{f"baseline_value_score_0_{index}": ["3"] for index in range(8)},
                **{f"candidate_value_score_0_{index}": ["5"] for index in range(8)},
                **{f"baseline_value_score_1_{index}": ["2"] for index in range(8)},
                **{f"candidate_value_score_1_{index}": ["4"] for index in range(8)},
            },
        )

        rows = APP.agent.repository.value_prediction_benchmark_scores("日本")
        assert len(rows) == 2
        assert rows[0]["candidate_scores"]["visual_accuracy"] in {4, 5}
        assert "价值观预测 Benchmark 评分已保存" in APP.state.sync_message
    finally:
        APP.agent = previous_agent


def test_save_value_prediction_benchmark_uses_single_model_scores(tmp_path):
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
        APP.state = AppState(country="日本", view="value", user_id="jp_owner")

        handle_action(
            "/save_value_prediction_benchmark",
            {
                "country": ["日本"],
                "view": ["value"],
                "benchmark_count": ["1"],
                "candidate_id_0": ["JP_CAND_001"],
                "operation_tag_0": ["试新_日本_樱花"],
                "baseline_output_0": ["模型预测"],
                "baseline_label_0": ["轻微修改"],
                **{f"baseline_value_score_0_{index}": ["4"] for index in range(8)},
            },
        )

        rows = APP.agent.repository.value_prediction_benchmark_scores("日本")
        assert len(rows) == 1
        assert rows[0]["baseline_scores"]["visual_accuracy"] == 4
        assert rows[0]["candidate_scores"]["visual_accuracy"] == 4
        assert rows[0]["candidate_label"] == "轻微修改"
        assert rows[0]["candidate_output"] == "模型预测"
        assert APP.agent.repository.value_prediction_benchmark_summary("日本")["candidate_average"] == 4.0
    finally:
        APP.agent = previous_agent


def test_generate_value_prediction_benchmark_uses_selected_candidates(monkeypatch):
    APP.state = AppState(country="日本", view="value", user_id="jp_owner")

    def fake_candidates(country, grade=""):
        return (
            {
                "candidate_id": "JP_CAND_001",
                "operation_tag": "试新_日本_樱花",
                "subject": "樱花",
                "visual_subject": "樱花列车",
                "visual_scene": "铁路沿线",
                "visual_style": "柔和写实",
                "predicted_grade": "S",
                "sa_probability": 0.82,
                "open_rate_range": "28%-34%",
                "completion_rate_range": "90%-95%",
                "finish_time_range": "18-24",
                "action": "优先排图",
                "risk_points": (),
                "rag_citations": ("JP_VALUE#chunk-1",),
                "similar_positive": (),
                "similar_negative": (),
                "evidence": "相似历史好图支持。",
            },
            {"candidate_id": "JP_CAND_002", "operation_tag": "试新_日本_寿司", "predicted_grade": "B", "sa_probability": 0.5},
        )

    monkeypatch.setattr(APP.agent, "undistributed_value_candidates", fake_candidates)

    handle_action("/generate_value_prediction_benchmark", {"candidate_id": ["JP_CAND_001"]})

    assert APP.state.show_value_benchmark is True
    assert len(APP.state.value_prediction_benchmarks) == 1
    assert APP.state.value_prediction_benchmarks[0]["candidate_id"] == "JP_CAND_001"
    assert "樱花列车" in APP.state.value_prediction_benchmarks[0]["baseline_output"]


def test_generate_value_prediction_benchmark_uses_readable_rag_citation_labels(monkeypatch):
    APP.state = AppState(country="法国", view="value", user_id="jp_fr_assist")

    def fake_candidates(country, grade=""):
        return (
            {
                "candidate_id": "FR_CAND_002",
                "operation_tag": "常规_法国_薰衣草田园0702",
                "visual_subject": "薰衣草田园与风车",
                "visual_scene": "法国乡村田野",
                "visual_style": "柔和写实",
                "predicted_grade": "A",
                "sa_probability": 0.74,
                "open_rate_range": "26%-32%",
                "completion_rate_range": "88%-93%",
                "finish_time_range": "24-30",
                "action": "优先排图",
                "risk_points": (),
                "rag_citations": ("FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c31#chunk-1",),
                "similar_positive": (),
                "similar_negative": (),
                "evidence": "相似历史好图支持。",
            },
        )

    def fake_details(country, citations):
        return (
            {
                "chunk_id": str(citations[0]),
                "parent_id": "FR_HARNESS_GOLD_fr-real-001",
                "source_type": "human_gold",
                "title": "常规_法国_薰衣草风车0624 · S图",
                "text": "真实历史样本：薰衣草田园风车，法国市场高开图与高完成率。",
            },
        )

    monkeypatch.setattr(APP.agent, "undistributed_value_candidates", fake_candidates)
    monkeypatch.setattr(APP.agent, "rag_citation_details", fake_details)

    handle_action("/generate_value_prediction_benchmark", {"candidate_id": ["FR_CAND_002"]})

    output = APP.state.value_prediction_benchmarks[0]["baseline_output"]
    assert "历史样本：常规_法国_薰衣草风车0624 · S图" in output
    assert "真实历史样本：薰衣草田园风车" in output


def test_generate_value_prediction_benchmark_predicts_pending_candidates_first(monkeypatch):
    APP.state = AppState(country="日本", view="value", user_id="jp_owner")
    calls = []

    def fake_candidates(country, grade=""):
        if calls:
            return (
                {
                    "candidate_id": "JP_CAND_001",
                    "operation_tag": "试新_日本_樱花",
                    "visual_subject": "预测后的樱花庭院",
                    "predicted_grade": "A",
                    "sa_probability": 0.71,
                    "open_rate_range": "24%-30%",
                    "completion_rate_range": "88%-93%",
                    "finish_time_range": "20-26",
                    "action": "优先排图",
                    "prediction_status": "predicted",
                    "evidence": "已生成真实预测。",
                },
            )
        return (
            {
                "candidate_id": "JP_CAND_001",
                "operation_tag": "试新_日本_樱花",
                "visual_subject": "",
                "predicted_grade": "待预测",
                "sa_probability": 0,
                "prediction_status": "pending",
                "evidence": "预测值尚未生成。",
            },
        )

    def fake_predict(country, candidate_id, force=False):
        calls.append((country, candidate_id, force))
        return {"status": "predicted", "candidate_id": candidate_id}

    monkeypatch.setattr(APP.agent, "undistributed_value_candidates", fake_candidates)
    monkeypatch.setattr(APP.agent, "predict_single_undistributed_value_candidate", fake_predict)

    handle_action("/generate_value_prediction_benchmark", {"candidate_id": ["JP_CAND_001"]})

    assert calls == [("日本", "JP_CAND_001", False)]
    assert "预测后的樱花庭院" in APP.state.value_prediction_benchmarks[0]["baseline_output"]


def test_generate_value_prediction_benchmark_refreshes_stale_rag_cache(monkeypatch):
    APP.state = AppState(country="日本", view="value", user_id="jp_owner")
    calls = []

    def fake_candidates(country, grade=""):
        if calls:
            return (
                {
                    "candidate_id": "JP_CAND_001",
                    "operation_tag": "试新_日本_樱花",
                    "visual_subject": "刷新后的樱花庭院",
                    "predicted_grade": "A",
                    "sa_probability": 0.71,
                    "open_rate_range": "14%-17%",
                    "completion_rate_range": "92%-95%",
                    "finish_time_range": "15.1-19.7",
                    "metric_levels": {"open_rate": "高", "completion_rate": "高", "avg_finish_time": "中"},
                    "action": "优先排图",
                    "prediction_status": "predicted",
                    "rag_filter_version": "v0.7.32",
                    "evidence": "刷新后生成强相关RAG依据。",
                },
            )
        return (
            {
                "candidate_id": "JP_CAND_001",
                "operation_tag": "试新_日本_樱花",
                "visual_subject": "旧缓存樱花庭院",
                "predicted_grade": "A",
                "sa_probability": 0.71,
                "prediction_status": "predicted",
                "evidence": "旧缓存RAG依据未通过强相关过滤，已隐藏，重新预测后会写入强相关引用。",
            },
        )

    def fake_predict(country, candidate_id, force=False):
        calls.append((country, candidate_id, force))
        return {"status": "predicted", "candidate_id": candidate_id}

    monkeypatch.setattr(APP.agent, "undistributed_value_candidates", fake_candidates)
    monkeypatch.setattr(APP.agent, "predict_single_undistributed_value_candidate", fake_predict)

    handle_action("/generate_value_prediction_benchmark", {"candidate_id": ["JP_CAND_001"]})

    assert calls == [("日本", "JP_CAND_001", True)]
    assert "刷新后的樱花庭院" in APP.state.value_prediction_benchmarks[0]["baseline_output"]
    assert "补预测状态：JP_CAND_001:predicted" in APP.state.sync_message


def test_save_needs_can_edit_operation_tag():
    APP.state = AppState(country="日本", view="regular", category="animal", tag="常规_日本_猫咪鲤鱼0605")
    APP.state.need_rows = [APP.agent.add_regular_demand("日本", "animal", "常规_日本_猫咪鲤鱼0605", 0)]

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


def test_confirm_weekly_review_needs_adds_suggested_rows():
    APP.state = AppState(country="日本", view="weekly_review", user_id="jp_owner")
    APP.state.need_rows = []

    handle_action(
        "/confirm_weekly_review_needs",
        {"country": ["日本"], "view": ["weekly_review"], "user_id": ["jp_owner"]},
    )

    assert APP.state.view == "regular"
    assert APP.state.need_rows
    assert all(row.need_type == "常规" for row in APP.state.need_rows)
    assert "周三复盘提需建议已生成" in APP.state.sync_message


def test_sync_needs_to_feishu_writes_directly_and_clears_selected_rows():
    APP.state = AppState(country="日本", view="regular", category="animal", tag="常规_日本_猫咪鲤鱼0605")
    APP.agent.feishu = MockFeishuClient(APP.agent._runtime_dir / "test_feishu_mock")
    APP.state.need_rows = [
        APP.agent.add_regular_demand("日本", "animal", "常规_日本_猫咪鲤鱼0605", 0),
        APP.agent.add_regular_demand("日本", "animal", "常规_日本_幼猫0608", 0),
    ]
    kept = APP.state.need_rows[1]
    APP.agent.feishu.allow_real_sync = True

    redirect = handle_action("/sync_needs_feishu", {"country": ["日本"], "view": ["regular"], "selected_rows": ["0"]})

    assert len(APP.state.need_rows) == 1
    assert APP.state.need_rows[0] == kept
    assert "同步成功" in APP.state.sync_message
    assert APP.state.sync_url == APP.agent.feishu.web_url()
    assert redirect is None


def test_regular_feishu_payload_has_request_date_image_attachment_and_no_value_match():
    from puzzle_ops.server import _demand_row_payload

    previous_today = APP.agent.today
    try:
        APP.agent.today = APP.agent.today.replace(year=2026, month=7, day=13)
        row = APP.agent.add_regular_demand("日本", "animal", "常规_日本_猫咪鲤鱼0605", 0)
        payload = _demand_row_payload(row)
    finally:
        APP.agent.today = previous_today

    assert payload["提需日期"] == "20260713"
    assert "价值观匹配度" not in payload
    assert payload["备注"] == ""
    assert payload["_reference_image_path"] == row.reference_image_path
    assert payload["_reference_image_content_type"] == "image/png"
    assert payload["图片本身"] == [{"text": row.image_name}]


def test_trial_feishu_payload_leaves_js_category_for_operator_selection():
    from puzzle_ops.server import _demand_row_payload

    row = APP.agent.create_trial_demand("日本", "animal", "parse")
    payload = _demand_row_payload(row)

    assert payload["JS分类"] == ""


def test_sync_needs_to_feishu_without_selection_writes_all_rows():
    APP.state = AppState(country="日本", view="regular", category="animal", tag="常规_日本_猫咪鲤鱼0605")
    APP.agent.feishu = MockFeishuClient(APP.agent._runtime_dir / "test_feishu_mock")
    APP.agent.feishu.allow_real_sync = True
    APP.state.need_rows = [
        APP.agent.add_regular_demand("日本", "animal", "常规_日本_猫咪鲤鱼0605", 0),
        APP.agent.add_regular_demand("日本", "animal", "常规_日本_幼猫0608", 0),
    ]
    redirect = handle_action("/sync_needs_feishu", {"country": ["日本"], "view": ["regular"], "user_id": ["jp_owner"]})

    assert APP.state.need_rows == []
    assert "同步成功" in APP.state.sync_message
    assert APP.state.sync_url == APP.agent.feishu.web_url()
    assert redirect is None
    assert any(row[2] == "提需同步" and row[4] == "成功" for row in APP.agent.sync_rows())


def test_readonly_country_blocks_guarded_action_approval():
    APP.state = AppState(user_id="jp_owner", country="法国", view="runtime")
    proposal = APP.agent.propose_feishu_sync(
        "法国",
        [{"提需分类": "常规", "国家": "法国", "JS分类": "花卉", "运营tag": "常规_法国_薰衣草0713", "主体内容": "薰衣草", "张数": 7, "需求等级": "P1", "加工方式": "纯AI", "图片本身": "薰衣草", "主体描述": "主体内容：薰衣草；色彩氛围：紫色；构图环境：田野。", "备注": "人工确认。"}],
        actor="fr_owner",
    )

    handle_action(
        "/approve_guarded_action",
        {
            "user_id": ["jp_owner"],
            "country": ["法国"],
            "view": ["runtime"],
            "proposal_id": [proposal.proposal_id],
            "approval_note": ["越权确认"],
            "execute_after_approval": ["1"],
        },
    )

    assert "只有只读权限" in APP.state.sync_message
    assert APP.agent.repository.guarded_action_proposal(proposal.proposal_id).guard_status == "pending_approval"


def test_run_business_skill_action_uses_demo_payload_and_creates_draft():
    APP.state = AppState(user_id="jp_owner", country="日本", view="runtime")

    handle_action(
        "/run_business_skill",
        {
            "user_id": ["jp_owner"],
            "country": ["日本"],
            "view": ["runtime"],
            "skill_id": ["regular_demand_skill"],
        },
    )

    assert "Skill 已运行" in APP.state.sync_message
    assert "regular_demand_skill" in APP.state.sync_message
    assert APP.agent.guarded_action_proposals("日本")


def test_sync_needs_requires_real_feishu_and_keeps_rows_without_config():
    APP.state = AppState(country="日本", view="regular", category="drawing", tag="常规_日本_传统浴袍美女0510")
    APP.agent.feishu = MockFeishuClient(APP.agent._runtime_dir / "test_feishu_mock")
    APP.state.need_rows = [APP.agent.add_regular_demand("日本", "drawing", "常规_日本_传统浴袍美女0510", 0)]
    APP.agent.feishu.allow_real_sync = False

    handle_action("/sync_needs_feishu", {"country": ["日本"], "view": ["regular"]})

    assert len(APP.state.need_rows) == 1
    assert "未配置真实飞书" in APP.state.sync_message


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


def test_apply_value_master_updates_active_parse_row_pool():
    APP.state = AppState(country="法国", view="trial", category="花卉", trial_mode="parse")
    row = APP.agent.create_trial_demand("法国", "花卉", "parse").edited(subject="薰衣草花园")
    APP.state.trial_parse_row = row
    APP.state.trial_parse_rows = [row]
    APP.state.trial_row = APP.agent.create_trial_demand("法国", "花卉", "parse").edited(subject="旧行")

    handle_action("/apply_value_master", {"country": ["法国"], "view": ["trial"], "category": ["花卉"], "trial_mode": ["parse"]})

    assert APP.state.trial_parse_row.value_match
    assert APP.state.trial_parse_rows[0].value_match == APP.state.trial_parse_row.value_match
    assert APP.state.trial_row.value_match == APP.state.trial_parse_row.value_match
    assert "价值观大师已完成" in APP.state.sync_message


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


def test_apply_value_master_reads_reference_image_before_rag_judgement(tmp_path):
    previous_uploads = APP.agent.trial_uploads
    calls: list[dict[str, object]] = []

    def fake_transport(payload: dict[str, object], api_key: str) -> dict[str, object]:
        calls.append(payload)
        first_content = payload["input"][0]["content"]
        has_image = any(item.get("type") == "input_image" for item in first_content)
        if has_image:
            return {
                "output_text": json.dumps(
                    {
                        "subject": "柴犬樱花",
                        "scene": "日式庭院樱花季",
                        "culture_elements": ["樱花", "柴犬"],
                        "style": "明亮治愈写实插画",
                        "risk_tags": [],
                        "prompt_keywords": ["shiba", "sakura"],
                        "confidence": 0.91,
                        "analysis": "识别为日本庭院中的柴犬与樱花。",
                    },
                    ensure_ascii=False,
                )
            }
        return {
            "output_text": json.dumps(
                {
                    "value_match": "LLM判断：柴犬樱花符合日本春季治愈向拼图价值观。",
                    "confidence": 0.88,
                    "evidence": ["主体内容：柴犬樱花"],
                    "risk_tags": [],
                },
                ensure_ascii=False,
            )
        }

    try:
        APP.agent.trial_uploads = TrialImageUploadService(
            tmp_path / "value_master_image",
            vision_client=OpenAIVisionLLMClient(api_key="sk-test", transport=fake_transport),
        )
        reference_path = tmp_path / "shiba.png"
        Image.new("RGB", (320, 240), (244, 190, 190)).save(reference_path)
        row = APP.agent.create_trial_demand("日本", "人物", "parse").edited(
            subject="旧主体",
            operation_tag="试新_日本_旧主体0717",
            subject_description="主体内容：旧主体；色彩氛围：待确认；构图环境：待确认。",
            reference_image_path=str(reference_path),
            reference_image_content_type="image/png",
        )
        APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")
        APP.state.trial_parse_row = row
        APP.state.trial_parse_rows = [row]

        handle_action("/apply_value_master", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]})

        assert len(calls) == 2
        assert APP.state.trial_parse_row.subject == "柴犬樱花"
        assert "日式庭院樱花季" in APP.state.trial_parse_row.subject_description
        assert "价值观大师视觉解析：真实openai" in APP.state.trial_parse_row.remark
        assert "LLM判断：柴犬樱花符合日本春季治愈向拼图价值观" in APP.state.trial_parse_row.value_match
        assert APP.state.trial_parse_rows[0].value_match == APP.state.trial_parse_row.value_match
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

    assert APP.state.trial_parse_uploads[0]["filename"] == "cat-koi.png"
    assert APP.state.trial_rows == []
    assert APP.state.trial_uploads[0]["filename"] == "cat-koi.png"
    assert "已上传1张参考图" in APP.state.sync_message
    assert "点击“解析图片”" in APP.state.sync_message

    handle_action("/parse_trial_uploads", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]})

    assert "cat-koi.png" in APP.state.trial_row.image_name
    assert "本地图片解析" in APP.state.trial_row.remark
    assert len(APP.state.trial_rows) == 1
    assert APP.state.trial_rows[0].image_name == APP.state.trial_row.image_name


def test_redirect_location_preserves_trial_context_after_upload():
    APP.state = AppState(
        user_id="jp_fr_assist",
        country="日本",
        view="trial",
        category="animal",
        tag="常规_日本_猫咪鲤鱼0605",
        trial_mode="derive",
    )

    location = redirect_location(APP.state)

    assert "view=trial" in location
    assert "trial_mode=derive" in location
    assert "category=animal" in location
    assert "%E7%8C%AB%E5%92%AA" in location


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
    handle_action("/parse_trial_uploads", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]})

    assert "暖红" in APP.state.trial_row.subject_description
    assert "横向构图" in APP.state.trial_row.subject_description
    assert "120x60" in APP.state.trial_row.remark
    assert len(APP.state.trial_rows) == 1


def test_upload_trial_images_keeps_local_parse_when_vision_times_out(tmp_path):
    called = threading.Event()

    class TimeoutVisionClient:
        def analyze(self, uploads, country, js_category, visual):
            called.set()
            raise TimeoutError("<urlopen error The write operation timed out>")

    previous_uploads = APP.agent.trial_uploads
    try:
        APP.agent.trial_uploads = TrialImageUploadService(tmp_path / "uploads", vision_client=TimeoutVisionClient())
        APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")
        image = Image.new("RGB", (320, 240), (220, 70, 60))
        buffer = BytesIO()
        image.save(buffer, format="PNG")

        handle_action(
            "/upload_trial_images",
            {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]},
            files={"trial_images": [{"filename": "timeout.png", "content_type": "image/png", "content": buffer.getvalue()}]},
        )
        assert "已上传1张参考图" in APP.state.sync_message
        handle_action("/parse_trial_uploads", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]})

        assert "timeout.png" in APP.state.trial_row.image_name
        assert "本地图片解析完成" in APP.state.trial_row.remark
        assert "调用失败" in APP.state.trial_row.remark
        assert "The write operation timed out" in APP.state.trial_row.remark
        assert len(APP.state.trial_parse_rows) == 1
        assert called.wait(1.0)
    finally:
        APP.agent.trial_uploads = previous_uploads


def test_parse_trial_images_waits_then_background_completes_slow_vision(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class SlowVisionClient:
        def analyze(self, uploads, country, js_category, visual):
            started.set()
            release.wait(1.0)
            return VisionLLMResult(
                subject="柴犬樱花",
                scene="日式庭院樱花季",
                culture_elements=("樱花", "柴犬"),
                style="明亮治愈写实插画",
                risk_tags=(),
                prompt_keywords=("日本", "柴犬", "樱花"),
                confidence=0.91,
                provider="qwen",
                raw_text="后台视觉解析完成。",
            )

    monkeypatch.setenv("QWEN_FOREGROUND_WAIT_PARSE_SECONDS", "0.05")
    previous_uploads = APP.agent.trial_uploads
    try:
        APP.agent.trial_uploads = TrialImageUploadService(tmp_path / "uploads", vision_client=SlowVisionClient())
        APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")
        image = Image.new("RGB", (320, 240), (220, 70, 60))
        buffer = BytesIO()
        image.save(buffer, format="PNG")

        handle_action(
            "/upload_trial_images",
            {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]},
            files={"trial_images": [{"filename": "slow.png", "content_type": "image/png", "content": buffer.getvalue()}]},
        )
        handle_action("/parse_trial_uploads", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]})

        assert started.wait(1.0)
        assert APP.state.trial_row.subject != "柴犬樱花"
        assert "后台解析中" in APP.state.trial_row.remark
        assert "后台补充" in APP.state.sync_message

        release.set()
        assert wait_until(lambda: APP.state.trial_row.subject == "柴犬樱花", timeout=2.0)
        assert "后台解析完成" in APP.state.sync_message
    finally:
        release.set()
        APP.agent.trial_uploads = previous_uploads


def test_trial_parse_and_derive_upload_pools_are_independent():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")
    image = Image.new("RGB", (320, 240), (220, 70, 60))
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    handle_action(
        "/upload_trial_images",
        {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]},
        files={"trial_images": [{"filename": "derive.png", "content_type": "image/png", "content": buffer.getvalue()}]},
    )
    assert APP.state.trial_derive_uploads[0]["filename"] == "derive.png"
    assert APP.state.trial_parse_uploads == []

    handle_action("/clear_trial_uploads", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]})

    assert APP.state.trial_parse_uploads == []
    assert APP.state.trial_derive_uploads[0]["filename"] == "derive.png"


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
    handle_action("/parse_trial_uploads", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]})

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
    handle_action("/parse_trial_uploads", {"country": ["日本"], "view": ["trial"], "category": ["动物"], "trial_mode": ["parse"]})

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
        handle_action("/parse_trial_uploads", {"country": ["日本"], "view": ["trial"], "category": ["动物"], "trial_mode": ["parse"]})
        assert wait_until(lambda: APP.state.trial_row.subject == "柴犬樱花")

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
        handle_action("/parse_trial_uploads", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]})
        assert wait_until(lambda: APP.state.trial_row.subject == "日式火车店铺少女")

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
        handle_action("/parse_trial_uploads", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]})
        assert wait_until(lambda: "游客塔楼" in APP.state.trial_row.operation_tag)

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
    handle_action("/parse_trial_uploads", {"country": ["法国"], "view": ["trial"], "category": ["花卉"], "trial_mode": ["derive"]})

    assert "衍生方向" in APP.state.trial_row.remark
    assert "视觉LLM：未运行" in APP.state.trial_row.remark
    assert "已生成2张相似参考图" not in APP.state.trial_row.remark
    assert "可继续点击“生成衍生参考图”" in APP.state.sync_message


def test_generate_trial_derivatives_requires_provider_without_faking_images():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")
    APP.agent.image_generator = None
    APP.state.trial_row = APP.agent.simulate_trial_upload("日本", "人物", "derive")

    handle_action("/generate_trial_derivatives", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]})

    assert "请先上传并解析一张真实历史好图" in APP.state.trial_row.remark
    assert APP.state.trial_rows == []


def test_generate_trial_derivatives_creates_two_audited_reference_rows(tmp_path):
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")
    APP.agent.image_generator = MockImageGenerationProvider(APP.agent._runtime_dir / "trial_uploads")
    reference_path = tmp_path / "good.png"
    reference_path.write_bytes(b"fake-reference-image")
    APP.state.trial_row = APP.agent.simulate_trial_upload("日本", "人物", "derive").edited(
        reference_image_path=str(reference_path),
        subject="日式塔楼游客",
        subject_description="主体内容：日式塔楼游客；色彩氛围：明亮清透；构图环境：海边步道远景。",
    )

    handle_action("/generate_trial_derivatives", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]})

    assert APP.state.trial_rows == []
    assert len(APP.state.trial_derivative_candidates) == 2
    assert all("衍生参考图" in row.image_name for row in APP.state.trial_derivative_candidates)
    assert all(row.reference_image_url.startswith("/uploads/") for row in APP.state.trial_derivative_candidates)
    assert all(row.reference_image_syncable is False for row in APP.state.trial_derivative_candidates)
    assert all("二次 VLM 解析与审核" in row.remark for row in APP.state.trial_derivative_candidates)
    assert all("一张独立完整图片" in row.remark for row in APP.state.trial_derivative_candidates)
    assert all("严禁四宫格" in row.remark for row in APP.state.trial_derivative_candidates)
    assert "已生成2张衍生参考图" in APP.state.trial_row.remark
    assert APP.state.generation_event["source_operation_tag"] == APP.state.trial_row.operation_tag
    assert "mock-" in APP.state.generation_event["task_id"]
    assert ".png" in APP.state.generation_event["generated_image_paths"]
    assert APP.state.generation_event["second_review_status"] == "blocked"
    assert APP.state.generation_event["feishu_attachment_status"] == "blocked"


def test_generate_trial_derivatives_uses_operator_prompt_and_negative_prompt(tmp_path):
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")
    APP.agent.image_generator = MockImageGenerationProvider(APP.agent._runtime_dir / "trial_uploads")
    reference_path = tmp_path / "good.png"
    reference_path.write_bytes(b"fake-reference-image")
    APP.state.trial_derive_row = APP.agent.simulate_trial_upload("日本", "人物", "derive").edited(
        reference_image_path=str(reference_path),
        subject="柴犬樱花",
    )

    handle_action(
        "/generate_trial_derivatives",
        {
            "country": ["日本"],
            "view": ["trial"],
            "category": ["人物"],
            "trial_mode": ["derive"],
            "derivative_prompt": ["单张柴犬樱花庭院，不要多画面"],
            "derivative_negative_prompt": ["不要四宫格，不要四季同图"],
        },
    )

    assert len(APP.state.trial_derivative_candidates) == 2
    assert APP.state.trial_derivative_prompt == "单张柴犬樱花庭院，不要多画面"
    assert APP.state.trial_derivative_negative_prompt == "不要四宫格，不要四季同图"
    assert all("Prompt：单张柴犬樱花庭院，不要多画面" in row.remark for row in APP.state.trial_derivative_candidates)
    assert all("Negative：不要四宫格，不要四季同图" in row.remark for row in APP.state.trial_derivative_candidates)


def test_approve_generated_derivatives_only_adds_selected_candidates(tmp_path):
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")
    first = APP.agent.create_trial_demand("日本", "人物", "derive").edited(image_name="衍生参考图1.png")
    second = APP.agent.create_trial_demand("日本", "人物", "derive").edited(image_name="衍生参考图2.png")
    APP.state.trial_derivative_candidates = [first, second]

    handle_action(
        "/approve_generated_derivatives",
        {"country": ["日本"], "view": ["trial"], "trial_mode": ["derive"], "selected_derivative_candidates": ["1"]},
    )

    assert len(APP.state.trial_derive_rows) == 1
    assert APP.state.trial_derive_rows[0].image_name == "衍生参考图2.png"
    assert APP.state.trial_derivative_candidates == []


def test_approve_generated_derivatives_requires_selection():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")
    first = APP.agent.create_trial_demand("日本", "人物", "derive").edited(image_name="衍生参考图1.png")
    second = APP.agent.create_trial_demand("日本", "人物", "derive").edited(image_name="衍生参考图2.png")
    APP.state.trial_derivative_candidates = [first, second]

    handle_action("/approve_generated_derivatives", {"country": ["日本"], "view": ["trial"], "trial_mode": ["derive"]})

    assert "请至少选择一张满意的衍生图" in APP.state.sync_message
    assert APP.state.trial_derive_rows == []
    assert len(APP.state.trial_derivative_candidates) == 2


def test_clear_derivative_candidates_keeps_operator_prompt():
    APP.state = AppState(
        country="日本",
        view="trial",
        category="人物",
        trial_mode="derive",
        trial_derivative_prompt="单张柴犬庭院",
        trial_derivative_negative_prompt="不要四宫格",
        trial_derivative_prompt_touched=True,
    )
    APP.state.trial_derivative_candidates = [APP.agent.create_trial_demand("日本", "人物", "derive")]
    APP.state.trial_derivative_candidate_uploads = [{"filename": "one.png"}]

    handle_action("/clear_derivative_candidates", {"country": ["日本"], "view": ["trial"], "trial_mode": ["derive"]})

    assert APP.state.trial_derivative_candidates == []
    assert APP.state.trial_derivative_candidate_uploads == []
    assert APP.state.trial_derivative_prompt == "单张柴犬庭院"
    assert APP.state.trial_derivative_negative_prompt == "不要四宫格"


def test_save_derivative_prompt_persists_before_generation():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")

    handle_action(
        "/save_derivative_prompt",
        {
            "country": ["日本"],
            "view": ["trial"],
            "trial_mode": ["derive"],
            "derivative_prompt": ["先写单张柴犬庭院"],
            "derivative_negative_prompt": ["不要拼贴"],
        },
    )

    assert APP.state.trial_derivative_prompt == "先写单张柴犬庭院"
    assert APP.state.trial_derivative_negative_prompt == "不要拼贴"
    assert APP.state.trial_derivative_prompt_touched is True
    assert "已保存衍生 prompt" in APP.state.sync_message


def test_upload_trial_images_keeps_touched_derivative_prompt_and_clears_candidates():
    APP.state = AppState(
        country="日本",
        view="trial",
        category="人物",
        trial_mode="derive",
        trial_derivative_prompt="旧prompt",
        trial_derivative_negative_prompt="旧negative",
        trial_derivative_prompt_touched=True,
    )
    APP.state.trial_derivative_candidates = [APP.agent.create_trial_demand("日本", "人物", "derive")]
    image = Image.new("RGB", (320, 240), (220, 70, 60))
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    handle_action(
        "/upload_trial_images",
        {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]},
        files={"trial_images": [{"filename": "new.png", "content_type": "image/png", "content": buffer.getvalue()}]},
    )

    assert APP.state.trial_derivative_candidates == []
    assert APP.state.trial_derivative_prompt == "旧prompt"
    assert APP.state.trial_derivative_negative_prompt == "旧negative"
    assert APP.state.trial_derivative_prompt_touched is True


def test_upload_trial_images_clears_untouched_derivative_prompt_and_candidates():
    APP.state = AppState(
        country="日本",
        view="trial",
        category="人物",
        trial_mode="derive",
        trial_derivative_prompt="旧prompt",
        trial_derivative_negative_prompt="旧negative",
        trial_derivative_prompt_touched=False,
    )
    APP.state.trial_derivative_candidates = [APP.agent.create_trial_demand("日本", "人物", "derive")]
    image = Image.new("RGB", (320, 240), (220, 70, 60))
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    handle_action(
        "/upload_trial_images",
        {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]},
        files={"trial_images": [{"filename": "new.png", "content_type": "image/png", "content": buffer.getvalue()}]},
    )

    assert APP.state.trial_derivative_candidates == []
    assert APP.state.trial_derivative_prompt == ""
    assert APP.state.trial_derivative_negative_prompt == ""
    assert APP.state.trial_derivative_prompt_touched is False


def test_generate_trial_derivatives_blocks_too_small_reference_image(tmp_path):
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")
    APP.agent.image_generator = MockImageGenerationProvider(APP.agent._runtime_dir / "trial_uploads")
    reference_path = tmp_path / "tiny.png"
    image = Image.new("RGB", (120, 90), (80, 160, 220))
    image.save(reference_path)
    APP.state.trial_row = APP.agent.simulate_trial_upload("日本", "人物", "derive").edited(reference_image_path=str(reference_path))

    handle_action("/generate_trial_derivatives", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]})

    assert APP.state.trial_rows == []
    assert "240-8000" in APP.state.trial_row.remark
    assert APP.state.generation_event == {}


def test_generate_trial_derivatives_calls_provider_twice_with_single_image_prompt(tmp_path):
    APP.state = AppState(country="日本", view="trial", category="travel", trial_mode="derive")
    provider = RecordingGenerationProvider(tmp_path)
    APP.agent.image_generator = provider
    APP.agent.trial_uploads = TrialImageUploadService(tmp_path / "review_uploads", vision_client=FakeGeneratedImageVisionClient(_safe_generated_result()))
    reference_path = tmp_path / "sakura-road.png"
    Image.new("RGB", (320, 320), (220, 180, 120)).save(reference_path)
    APP.state.trial_derive_row = APP.agent.create_trial_demand("日本", "travel", "derive").edited(
        reference_image_path=str(reference_path),
        subject="樱花大道",
        subject_description="主体内容：樱花大道；色彩氛围：粉白春日；构图环境：两侧樱花树夹道，道路纵深透视。",
    )

    handle_action("/generate_trial_derivatives", {"country": ["日本"], "view": ["trial"], "trial_mode": ["derive"]})

    assert len(provider.calls) == 2
    assert all(call["count"] == 1 for call in provider.calls)
    assert all("本次只生成一张独立完整图片" in call["prompt"] for call in provider.calls)
    assert all("衍生2张" not in call["prompt"] for call in provider.calls)
    assert provider.calls[0]["seed"] != provider.calls[1]["seed"]


def test_generate_trial_derivatives_starts_background_job_for_slow_provider(tmp_path):
    class SlowGenerationProvider(RecordingGenerationProvider):
        def generate_derivatives(self, **kwargs):
            import time

            time.sleep(0.2)
            return super().generate_derivatives(**kwargs)

    APP.state = AppState(country="日本", view="trial", category="travel", trial_mode="derive")
    APP.derivative_job_foreground_grace_seconds = 0.01
    provider = SlowGenerationProvider(tmp_path)
    APP.agent.image_generator = provider
    APP.agent.trial_uploads = TrialImageUploadService(tmp_path / "review_uploads", vision_client=FakeGeneratedImageVisionClient(_safe_generated_result()))
    reference_path = tmp_path / "sakura-road.png"
    Image.new("RGB", (320, 320), (220, 180, 120)).save(reference_path)
    APP.state.trial_derive_row = APP.agent.create_trial_demand("日本", "travel", "derive").edited(
        reference_image_path=str(reference_path),
        subject="樱花大道",
        subject_description="主体内容：樱花大道；色彩氛围：粉白春日；构图环境：两侧樱花树夹道，道路纵深透视。",
    )

    handle_action("/generate_trial_derivatives", {"country": ["日本"], "view": ["trial"], "trial_mode": ["derive"]})

    assert APP.state.trial_derivative_job_status in {"pending", "running"}
    assert APP.state.trial_derivative_job_id
    assert APP.state.trial_derivative_candidates == []
    assert "后台生成任务已启动" in APP.state.sync_message
    assert wait_until(lambda: APP.state.trial_derivative_job_status == "succeeded", timeout=2.0)
    assert len(APP.state.trial_derivative_candidates) == 2
    APP.derivative_job_foreground_grace_seconds = 0.05


def test_generated_derivative_blocks_subject_drift_from_sakura_avenue_to_house(tmp_path):
    APP.state = AppState(country="日本", view="trial", category="travel", trial_mode="derive")
    APP.agent.image_generator = PassingRealGenerationProvider(tmp_path)
    drift_result = VisionLLMResult(
        subject="树林里的日式小屋",
        scene="森林深处的小屋建筑，周围有树木但没有道路纵深",
        culture_elements=("日式小屋", "森林"),
        style="日式自然建筑插画",
        risk_tags=(),
        prompt_keywords=("小屋", "森林", "建筑"),
        confidence=0.91,
        provider="qwen",
        raw_text="主体为日式小屋，参考图道路结构不明显。",
    )
    APP.agent.trial_uploads = TrialImageUploadService(tmp_path / "drift_review_uploads", vision_client=FakeGeneratedImageVisionClient(drift_result))
    reference_path = tmp_path / "sakura-road.png"
    Image.new("RGB", (320, 320), (230, 200, 210)).save(reference_path)
    APP.state.trial_derive_row = APP.agent.create_trial_demand("日本", "travel", "derive").edited(
        reference_image_path=str(reference_path),
        subject="樱花大道",
        subject_description="主体内容：樱花大道；色彩氛围：粉白春日；构图环境：两侧樱花树夹道，道路纵深透视。",
    )

    handle_action("/generate_trial_derivatives", {"country": ["日本"], "view": ["trial"], "trial_mode": ["derive"]})

    assert len(APP.state.trial_derivative_candidates) == 2
    assert all(row.generation_review_status == "blocked" for row in APP.state.trial_derivative_candidates)
    assert all(row.reference_image_syncable is False for row in APP.state.trial_derivative_candidates)
    assert all("主体偏离参考图" in row.remark for row in APP.state.trial_derivative_candidates)


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


class RecordingGenerationProvider(PassingRealGenerationProvider):
    def __init__(self, output_dir):
        super().__init__(output_dir)
        self.calls = []

    def generate_derivatives(self, reference_image, prompt, negative_prompt, count, seed, style_constraints):
        self.calls.append(
            {
                "reference_image": reference_image,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "count": count,
                "seed": seed,
                "style_constraints": style_constraints,
            }
        )
        return super().generate_derivatives(reference_image, prompt, negative_prompt, count, seed, style_constraints)


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
    reference_path = tmp_path / "good.png"
    Image.new("RGB", (320, 320), (220, 180, 120)).save(reference_path)
    APP.state.trial_row = APP.agent.simulate_trial_upload("日本", "人物", "derive").edited(
        reference_image_path=str(reference_path),
        subject="日式塔楼游客",
        subject_description="主体内容：日式塔楼游客；色彩氛围：明亮清透；构图环境：海边步道远景。",
    )

    redirect = handle_action("/generate_trial_derivatives", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]})

    assert redirect is None
    assert APP.state.view == "trial"
    assert APP.state.trial_rows == []
    assert APP.state.trial_uploads == []
    assert "生成衍生参考图失败：Qwen 图像生成失败：quota exceeded" in APP.state.sync_message
    assert "错误类型=quota_exceeded" in APP.state.sync_message
    assert APP.state.generation_event["status"] == "failed"
    assert APP.state.generation_event["provider"] == "Qwen 图像生成"
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
    assert events[-1]["provider"] == "Qwen 图像生成"
    overview = APP.agent.memory_overview("日本")
    assert overview["短期记忆"]["count"] >= 1


def test_generate_trial_derivatives_billing_arrearage_shows_recovery_hint(tmp_path):
    APP.state = AppState(country="法国", view="trial", category="花卉", trial_mode="derive")
    APP.agent.image_generator = BillingArrearageGenerationProvider()
    reference_path = tmp_path / "good.png"
    Image.new("RGB", (320, 320), (220, 180, 120)).save(reference_path)
    APP.state.trial_row = APP.agent.simulate_trial_upload("法国", "花卉", "derive").edited(
        reference_image_path=str(reference_path),
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
    assert "Qwen 图像生成诊断" in APP.state.sync_message
    assert "provider=Qwen 图像生成" in APP.state.sync_message
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
    reference_path = tmp_path / "good.png"
    Image.new("RGB", (320, 320), (220, 180, 120)).save(reference_path)
    APP.state.trial_row = APP.agent.simulate_trial_upload("日本", "人物", "derive").edited(
        reference_image_path=str(reference_path),
        subject="日式塔楼游客",
        subject_description="主体内容：日式塔楼游客；色彩氛围：明亮清透；构图环境：海边步道远景。",
    )

    handle_action("/generate_trial_derivatives", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]})

    assert APP.state.trial_rows == []
    assert len(APP.state.trial_derivative_candidates) == 2
    assert all(row.generation_review_status == "passed" for row in APP.state.trial_derivative_candidates)
    assert all(row.human_approved is False for row in APP.state.trial_derivative_candidates)
    assert all(row.reference_image_syncable is False for row in APP.state.trial_derivative_candidates)
    assert all("二次 VLM 解析与审核通过" in row.remark for row in APP.state.trial_derivative_candidates)
    assert all(row.subject_description.startswith("主体内容：日式塔楼游客；色彩氛围：明亮清透的日式旅游插画；构图环境：海边步道") for row in APP.state.trial_derivative_candidates)
    assert all(row.reference_image_path.endswith(".png") for row in APP.state.trial_derivative_candidates)
    assert len(fake_vision.calls) == 2
    assert APP.state.generation_event["second_review_status"] == "passed"
    assert APP.state.generation_event["feishu_attachment_status"] == "pending_human_approval"

    handle_action("/sync_trial_feishu", {"country": ["日本"], "view": ["trial"]})

    assert APP.state.trial_rows == []
    assert "请先上传解析图片" in APP.state.sync_message

    handle_action(
        "/approve_generated_derivatives",
        {"country": ["日本"], "view": ["trial"], "selected_derivative_candidates": ["0", "1"]},
    )

    assert all(row.human_approved is True for row in APP.state.trial_rows)
    assert all(row.reference_image_syncable is True for row in APP.state.trial_rows)
    assert APP.state.generation_event["feishu_attachment_status"] == "ready"
    assert "加入下方试新提需表" in APP.state.sync_message


def test_real_generation_derivatives_with_vlm_risk_stay_unsyncable(tmp_path):
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="derive")
    APP.agent.image_generator = PassingRealGenerationProvider(tmp_path)
    APP.agent.trial_uploads = TrialImageUploadService(
        tmp_path / "risk_review_uploads",
        vision_client=FakeGeneratedImageVisionClient(_risky_generated_result()),
    )
    reference_path = tmp_path / "good.png"
    Image.new("RGB", (320, 320), (220, 180, 120)).save(reference_path)
    APP.state.trial_row = APP.agent.simulate_trial_upload("日本", "人物", "derive").edited(
        reference_image_path=str(reference_path),
        subject="日式塔楼游客",
        subject_description="主体内容：日式塔楼游客；色彩氛围：明亮清透；构图环境：海边步道远景。",
    )

    handle_action("/generate_trial_derivatives", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["derive"]})

    assert len(APP.state.trial_derivative_candidates) == 2
    assert all(row.reference_image_syncable is False for row in APP.state.trial_derivative_candidates)
    assert all("二次 VLM 解析未通过" in row.remark for row in APP.state.trial_derivative_candidates)
    assert all("版权/IP风险" in row.remark for row in APP.state.trial_derivative_candidates)


def test_sync_trial_to_feishu_one_click_resets_trial_row():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")
    APP.agent.feishu = MockFeishuClient(APP.agent._runtime_dir / "test_feishu_mock")
    APP.agent.feishu.allow_real_sync = True
    APP.state.trial_rows = [APP.agent.simulate_trial_upload("日本", "人物", "parse")]

    redirect = handle_action("/sync_trial_feishu", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]})

    assert APP.state.view == "trial"
    assert "已一键同步试新提需到飞书表格" in APP.state.sync_message
    assert APP.state.sync_url == APP.agent.feishu.web_url()
    assert APP.state.trial_rows == []
    assert redirect is None
    assert "上传参考图" in APP.state.trial_row.image_name
    assert any(row[2] == "提需同步" and row[4] == "成功" for row in APP.agent.sync_rows())


def test_sync_trial_rejects_empty_uploaded_rows_before_feishu_call():
    APP.state = AppState(country="日本", view="trial", category="人物", trial_mode="parse")
    APP.agent.feishu = MockFeishuClient(APP.agent._runtime_dir / "test_feishu_mock")
    APP.agent.feishu.allow_real_sync = True
    before = len(APP.agent.sync_rows())

    redirect = handle_action("/sync_trial_feishu", {"country": ["日本"], "view": ["trial"], "category": ["人物"], "trial_mode": ["parse"]})

    assert redirect is None
    assert APP.state.view == "trial"
    assert APP.state.sync_message == "请先上传解析图片，生成至少一条试新提需记录。"
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


def test_value_candidate_import_and_prediction_actions_update_runtime_state(monkeypatch, tmp_path):
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent()
        APP.agent._runtime_dir = tmp_path
        APP.state = AppState(country="日本", view="value")

        def fake_predict(country, *, limit=100):
            return {"predicted_count": 15, "candidate_count": 15, "country": country}

        monkeypatch.setattr(APP.agent, "predict_undistributed_value_candidates", fake_predict)

        handle_action("/import_value_candidates_excel", {"country": ["日本"], "view": ["value"]})
        assert APP.state.view == "value"
        assert "候选图 Excel 已导入" in APP.state.sync_message
        assert "15 条" in APP.state.sync_message

        handle_action("/predict_value_candidates", {"country": ["日本"], "view": ["value"]})
        assert "价值观大师预测完成" in APP.state.sync_message
        assert "新预测 15 条" in APP.state.sync_message
        assert "候选共 15 条" in APP.state.sync_message
    finally:
        APP.agent = previous_agent


def test_value_candidate_human_decision_action_writes_working_memory(tmp_path):
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
        APP.state = AppState(country="日本", view="value")

        handle_action(
            "/save_value_candidate_decision",
            {
                "country": ["日本"],
                "view": ["value"],
                "candidate_id": ["JP_CAND_001"],
                "decision": ["优先排图"],
                "human_note": ["人工看好樱花庭院"],
            },
        )

        rows = APP.agent.memory_debug("日本", query="JP_CAND_001 樱花庭院", limit=50)
        assert "已加入下周排图池：JP_CAND_001" in APP.state.sync_message
        assert any(row["memory_type"] == "value_candidate_human_decision" for row in rows)
    finally:
        APP.agent = previous_agent


def test_value_candidate_revision_note_keeps_value_page(tmp_path):
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
        APP.state = AppState(country="日本", view="value")

        handle_action(
            "/save_value_candidate_decision",
            {
                "country": ["日本"],
                "view": ["value"],
                "candidate_id": ["JP_CAND_002"],
                "decision": ["人工复核"],
                "decision_note": ["主体太散，需要重画"],
            },
        )

        assert APP.state.view == "value"
        assert "要求修改已保存：JP_CAND_002" in APP.state.sync_message
        html = render_page(APP.agent, APP.state)
        assert "价值观大师" in html
        assert "主体太散，需要重画" in html
    finally:
        APP.agent = previous_agent


def test_retry_single_value_candidate_prediction_action(monkeypatch, tmp_path):
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent()
        APP.agent._runtime_dir = tmp_path
        APP.state = AppState(country="日本", view="value")

        def fake_predict(country, candidate_id, *, force=False):
            return {"country": country, "candidate_id": candidate_id, "status": "predicted", "predicted_count": 1, "cached_count": 0}

        monkeypatch.setattr(APP.agent, "predict_single_undistributed_value_candidate", fake_predict)

        handle_action(
            "/predict_single_value_candidate",
            {"country": ["日本"], "view": ["value"], "candidate_id": ["JP_CAND_015"]},
        )

        assert APP.state.view == "value"
        assert "单张预测完成：JP_CAND_015" in APP.state.sync_message
    finally:
        APP.agent = previous_agent


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


def test_save_value_match_correction_action_writes_memory_and_status():
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent()
        APP.state = AppState(country="日本", view="trial", trial_mode="parse")
        APP.state.trial_row = APP.agent.create_trial_demand("日本", "人物", "parse").edited(
            subject="寿司",
            value_match="LLM判断：部分符合；系统RAG召回：JP_SUSHI#chunk-1；生成式RAG依据：寿司属于日本本土饮食文化。",
        )

        handle_action(
            "/save_value_match_correction",
            {
                "country": ["日本"],
                "view": ["trial"],
                "human_correction": ["人工修正：符合本土饮食文化，但需规避品牌露出。"],
                "satisfaction_score": ["5"],
            },
        )

        assert APP.state.view == "trial"
        assert "价值观人工修正已反哺" in APP.state.sync_message
        rows = APP.agent.memory_debug("日本", query="品牌露出 本土饮食文化", limit=50)
        assert any(row["memory_type"] == "value_match_human_correction" for row in rows)
        assert any(row["memory_type"] == "verified_value_match_fact" for row in rows)
        assert any(row["memory_type"] == "rag_eval_failure_feedback" for row in rows)
    finally:
        APP.agent = previous_agent


def test_submit_rag_feedback_batch_records_selected_feedback_score_and_optional_correction(tmp_path):
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_feedback_batch.db"))
        APP.state = AppState(country="日本", view="trial", trial_mode="parse")
        APP.state.trial_row = APP.agent.create_trial_demand("日本", "人物", "parse").edited(
            subject="寿司",
            value_match="LLM判断：部分符合；系统RAG召回：JP_SUSHI#chunk-1、AUDIT_001#chunk-1；生成式RAG依据：寿司属于日本本土饮食文化。",
        )

        handle_action(
            "/submit_rag_feedback_batch",
            {
                "country": ["日本"],
                "view": ["trial"],
                "trial_mode": ["parse"],
                "citation_count": ["2"],
                "chunk_id_0": ["JP_SUSHI#chunk-1"],
                "usefulness_0": ["useful"],
                "note_0": ["解释本土饮食文化"],
                "chunk_id_1": ["AUDIT_001#chunk-1"],
                "usefulness_1": [""],
                "note_1": [""],
                "satisfaction_score": ["4"],
                "human_correction": [""],
            },
        )

        rows = APP.agent.memory_debug("日本", query="本土饮食文化", limit=80)
        assert APP.state.view == "trial"
        assert "RAG 批量反馈已提交" in APP.state.sync_message
        assert "citation=1" in APP.state.sync_message
        assert "score=4" in APP.state.sync_message
        assert any(
            row["memory_type"] == "rag_citation_feedback"
            and row["payload"]["chunk_id"] == "JP_SUSHI#chunk-1"
            and row["payload"]["usefulness"] == "useful"
            for row in rows
        )
        assert any(row["memory_type"] == "value_match_human_score" and row["payload"]["satisfaction_score"] == 4 for row in rows)
        assert not any(row["memory_type"] == "value_match_human_correction" for row in rows)
    finally:
        APP.agent = previous_agent


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


def test_export_harness_external_eval_action_writes_eval_tool_files():
    APP.state = AppState(country="日本", view="eval")

    handle_action(
        "/export_harness_external_eval",
        {
            "country": ["日本"],
            "view": ["eval"],
        },
    )

    assert APP.state.view == "eval"
    assert "已导出外部评测文件" in APP.state.sync_message
    export_dir = APP.agent._runtime_dir / "harness_external_eval_exports"
    assert (export_dir / "phoenix_harness_日本.json").exists()
    assert (export_dir / "promptfoo_harness_日本.json").exists()
    assert (export_dir / "promptfoo_harness_日本.yaml").exists()
    assert (export_dir / "deepeval_harness_日本.json").exists()


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

    def fake_auto(country, **kwargs):
        calls.append((country, kwargs))
        return {"updated_count": 5, "skipped_count": 0, "dataset": "/tmp/harness_gold_samples_法国.csv"}

    monkeypatch.setattr(APP.agent, "auto_prelabeled_harness_samples", fake_auto)

    handle_action("/auto_prelabeled_harness_gold", {"country": ["法国"], "view": ["eval"]})

    assert calls and calls[0][0] == "法国"
    assert set(calls[0][1]) == {"progress_callback"}
    assert APP.state.view == "eval"
    assert "AI 预标注完成：5 条" in APP.state.sync_message


def test_auto_prelabeled_harness_gold_action_passes_selected_sample_ids(monkeypatch):
    APP.state = AppState(country="法国", view="eval")
    calls = []

    def fake_auto(country, **kwargs):
        calls.append((country, kwargs))
        return {
            "updated_count": 2,
            "skipped_count": 0,
            "remaining_needs_prelabeled": 3,
            "pending_review_count": 2,
            "dataset": "/tmp/harness_gold_samples_法国.csv",
        }

    monkeypatch.setattr(APP.agent, "auto_prelabeled_harness_samples", fake_auto)

    handle_action(
        "/auto_prelabeled_harness_gold",
        {
            "country": ["法国"],
            "view": ["eval"],
            "max_count": ["5"],
            "sample_id": ["fr-real-001", "fr-real-003"],
        },
    )

    assert calls and calls[0][0] == "法国"
    assert calls[0][1]["sample_ids"] == ("fr-real-001", "fr-real-003")
    assert calls[0][1]["max_count"] == 5
    assert "progress_callback" in calls[0][1]
    assert APP.state.view == "eval"
    assert "AI 预标注完成：2 条" in APP.state.sync_message


def test_auto_prelabeled_harness_gold_starts_background_progress_job(monkeypatch):
    APP.state = AppState(country="法国", view="eval")
    APP.harness_prelabel_job_foreground_grace_seconds = 0.01
    calls = []

    def fake_auto(country, **kwargs):
        import time

        calls.append((country, kwargs))
        progress = kwargs.get("progress_callback")
        if progress:
            progress(1, 2, "fr-real-001")
        time.sleep(0.05)
        if progress:
            progress(2, 2, "fr-real-002")
        return {
            "updated_count": 2,
            "skipped_count": 0,
            "remaining_needs_prelabeled": 0,
            "pending_review_count": 2,
            "dataset": "/tmp/harness_gold_samples_法国.csv",
        }

    monkeypatch.setattr(APP.agent, "auto_prelabeled_harness_samples", fake_auto)

    handle_action(
        "/auto_prelabeled_harness_gold",
        {
            "country": ["法国"],
            "view": ["eval"],
            "sample_id": ["fr-real-001", "fr-real-002"],
        },
    )

    assert APP.state.harness_prelabel_job_status in {"pending", "running"}
    assert APP.state.harness_prelabel_job_id
    assert "Qwen 预标注任务已启动" in APP.state.sync_message
    assert wait_until(lambda: APP.state.harness_prelabel_job_status == "succeeded", timeout=1.5)
    assert calls and calls[0][0] == "法国"
    assert calls[0][1]["sample_ids"] == ("fr-real-001", "fr-real-002")
    assert "progress_callback" in calls[0][1]
    assert APP.state.harness_prelabel_job_progress == 100
    assert "AI 预标注完成：2 条" in APP.state.harness_prelabel_job_message
    APP.harness_prelabel_job_foreground_grace_seconds = 0.05


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

    assert wait_until(lambda: APP.state.harness_approval_job_status == "succeeded", timeout=1.5)
    assert calls and calls[0][0] == "法国"
    assert calls[0][1]["reviewer_note"] == "抽查通过"
    assert "progress_callback" in calls[0][1]
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

    assert wait_until(lambda: APP.state.harness_approval_job_status == "succeeded", timeout=1.5)
    assert calls and calls[0][0] == "法国"
    assert calls[0][1]["sample_ids"] == ("fr-real-001",)
    assert calls[0][1]["reviewer_note"] == "只确认第1条"
    assert "progress_callback" in calls[0][1]
    assert "AI Silver 已确认晋升：1 条" in APP.state.sync_message


def test_approve_harness_silver_labels_starts_background_progress_job(monkeypatch):
    APP.state = AppState(country="法国", view="eval")
    APP.harness_approval_job_foreground_grace_seconds = 0.01
    calls = []

    def fake_approve(country, **kwargs):
        calls.append((country, kwargs))
        progress = kwargs.get("progress_callback")
        if progress:
            progress(1, 2, "fr-real-001")
        time.sleep(0.05)
        if progress:
            progress(2, 2, "fr-real-002")
        return {
            "approved_count": 2,
            "skipped_count": 0,
            "fact_memory_count": 2,
            "rag_human_gold_count": 2,
            "dataset": "/tmp/harness_gold_samples_法国.csv",
        }

    monkeypatch.setattr(APP.agent, "approve_harness_silver_labels", fake_approve)

    handle_action(
        "/approve_harness_silver_labels",
        {
            "country": ["法国"],
            "view": ["eval"],
            "reviewer_note": ["批量抽查通过"],
            "sample_id": ["fr-real-001", "fr-real-002"],
        },
    )

    assert APP.state.harness_approval_job_status in {"pending", "running"}
    assert APP.state.harness_approval_job_id
    assert "human_gold 批量确认任务已启动" in APP.state.sync_message
    assert wait_until(lambda: APP.state.harness_approval_job_status == "succeeded", timeout=1.5)
    assert calls and calls[0][0] == "法国"
    assert calls[0][1]["sample_ids"] == ("fr-real-001", "fr-real-002")
    assert calls[0][1]["reviewer_note"] == "批量抽查通过"
    assert "progress_callback" in calls[0][1]
    assert APP.state.harness_approval_job_progress == 100
    assert "AI Silver 已确认晋升：2 条" in APP.state.harness_approval_job_message
    APP.harness_approval_job_foreground_grace_seconds = 0.05


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


def test_replace_schedule_action_is_no_longer_active():
    APP.state = AppState(country="日本", view="schedule", schedule_day="周一")
    original = APP.agent.schedule("日本", "周一")[0]

    handle_action("/replace_schedule", {"slot_index": ["0"], "image_name": [original.image_name]})

    assert APP.state.view == "value"
    assert APP.state.schedule_replacements == {}


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


def test_memory_production_validation_seed_route_creates_country_samples():
    APP.state = AppState(country="日本", view="runtime", user_id="jp_owner")

    handle_action(
        "/seed_memory_validation",
        {"country": ["日本"], "view": ["runtime"], "user_id": ["jp_owner"]},
    )

    rows = APP.agent.memory_debug("日本", query="上线验收", limit=50)
    assert any(row["created_by"] == "jp_owner" for row in rows)
    assert any(row["review_status"] == "draft" for row in rows)
    assert any(row["rag_ready"] for row in rows)
    assert "Memory 生产验收样例已生成" in APP.state.sync_message


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
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent()
        APP.agent.rag_provider_config = RagProviderConfig()
        APP.agent.rag_vector_store_config = RagVectorStoreConfig()
        APP.state = AppState(country="日本", view="runtime")

        handle_action("/rebuild_rag_knowledge", {"country": ["日本"], "view": ["runtime"]})

        assert APP.state.view == "runtime"
        assert "RAG 知识库已重建" in APP.state.sync_message
        assert "hit@5=" in APP.state.sync_message
        assert "mrr@5=" in APP.state.sync_message
    finally:
        APP.agent = previous_agent


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


def test_reindex_rag_vector_store_action_reports_provider_and_full_metrics(monkeypatch):
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent()
        APP.agent.rag_vector_store_config = RagVectorStoreConfig(
            provider="milvus",
            endpoint="http://127.0.0.1:19530",
            collection="puzzle_ops_rag",
            configured=True,
            ready=True,
            status_text="Milvus ready：http://127.0.0.1:19530 / puzzle_ops_rag",
        )
        APP.state = AppState(country="日本", view="runtime")

        def fake_reindex(country):
            assert country == "日本"
            return {
                "status": "indexed",
                "vector_store_provider": "milvus",
                "vector_store_collection": "puzzle_ops_rag",
                "upserted_points": 12,
                "chunk_count": 12,
                "vector_size": 3,
                "hit@5": 1.0,
                "mrr@5": 0.9,
                "precision@5": 0.4,
                "recall@5": 1.0,
                "ndcg@5": 0.92,
                "manifest_path": "/tmp/milvus_reindex_日本.json",
            }

        monkeypatch.setattr(APP.agent, "reindex_rag_vector_store_from_raw", fake_reindex)

        handle_action("/reindex_rag_vector_store", {"country": ["日本"], "view": ["runtime"]})

        assert APP.state.view == "runtime"
        assert "Milvus RAG 已重建入库" in APP.state.sync_message
        assert "points=12" in APP.state.sync_message
        assert "precision@5=0.4" in APP.state.sync_message
        assert "recall@5=1.0" in APP.state.sync_message
        assert "ndcg@5=0.92" in APP.state.sync_message
        assert "manifest=/tmp/milvus_reindex_日本.json" in APP.state.sync_message
    finally:
        APP.agent = previous_agent


def test_export_rag_acceptance_report_action_writes_report(monkeypatch):
    APP.state = AppState(country="日本", view="runtime")
    monkeypatch.setattr(APP.agent, "rag_provider_config", RagProviderConfig())

    handle_action("/export_rag_acceptance_report", {"country": ["日本"], "view": ["runtime"]})

    assert APP.state.view == "runtime"
    assert "RAG 工业验收报告已导出" in APP.state.sync_message
    assert "hit@5=" in APP.state.sync_message
    export_path = APP.agent._runtime_dir / "rag_acceptance_reports" / "rag_acceptance_日本.json"
    assert export_path.exists()
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert payload["hit@5"] >= 0.8
    assert payload["retrieval_routes"]["bm25"] is True


def test_export_rag_ops_report_action_writes_json_and_markdown(monkeypatch):
    APP.state = AppState(country="日本", view="runtime")

    def fake_export(country, output_dir):
        assert country == "日本"
        assert str(output_dir).endswith("rag_acceptance_reports")
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "rag_ops_report_日本.json"
        markdown_path = output_dir / "rag_ops_report_日本.md"
        json_path.write_text('{"country":"日本"}', encoding="utf-8")
        markdown_path.write_text("# RAG Ops Report", encoding="utf-8")
        return {"json_path": str(json_path), "markdown_path": str(markdown_path)}

    monkeypatch.setattr(APP.agent, "export_rag_ops_report", fake_export)

    handle_action("/export_rag_ops_report", {"country": ["日本"], "view": ["runtime"]})

    assert APP.state.view == "runtime"
    assert "RAG Ops 报告已导出" in APP.state.sync_message
    assert "rag_ops_report_日本.json" in APP.state.sync_message
    assert "rag_ops_report_日本.md" in APP.state.sync_message


def test_run_full_rag_acceptance_action_reports_reindex_and_hit_rate(monkeypatch):
    APP.state = AppState(country="日本", view="runtime")

    def fake_full_acceptance(country, output_dir, preflight_mode="fast"):
        assert country == "日本"
        assert preflight_mode == "live"
        return {
            "status": "passed",
            "report_path": str(output_dir / "rag_acceptance_full_日本.json"),
            "summary_path": str(output_dir / "rag_acceptance_full_summary_日本.json"),
            "reindex": {"status": "indexed", "upserted_points": 8, "vector_size": 1024},
            "preflight": {
                "embedding": {"ready": True},
                "qdrant": {"ready": True},
                "rerank": {"ready": True},
            },
            "report": {
                "hit@5": 1.0,
                "mrr@5": 0.9,
                "passed_threshold": True,
                "observed_retrieval": {"qdrant_vector_hits": True},
                "runtime_stats": {"embedding_remote_calls": 2, "rerank_remote_calls": 1},
            },
        }

    monkeypatch.setattr(APP.agent, "run_full_rag_industrial_acceptance", fake_full_acceptance)

    handle_action("/run_full_rag_acceptance", {"country": ["日本"], "view": ["runtime"]})

    assert APP.state.view == "runtime"
    assert "RAG 工业全链路验收完成" in APP.state.sync_message
    assert "points=8" in APP.state.sync_message
    assert "hit@5=1.0" in APP.state.sync_message
    assert "qdrant_hit=True" in APP.state.sync_message
    assert "preflight=embedding:True,qdrant:True,rerank:True" in APP.state.sync_message


def test_run_full_rag_acceptance_action_reports_failure_stage(monkeypatch):
    APP.state = AppState(country="日本", view="runtime")

    def fake_full_acceptance(country, output_dir, preflight_mode="fast"):
        assert preflight_mode == "live"
        return {
            "status": "failed",
            "failure_stage": "qdrant_reindex",
            "error": "Qdrant refused connection",
            "report_path": "",
            "summary_path": str(output_dir / "rag_acceptance_full_summary_日本.json"),
            "reindex": {},
            "report": {},
            "diagnostics": [{"component": "qdrant", "status": "failed", "message": "Qdrant refused connection"}],
        }

    monkeypatch.setattr(APP.agent, "run_full_rag_industrial_acceptance", fake_full_acceptance)

    handle_action("/run_full_rag_acceptance", {"country": ["日本"], "view": ["runtime"]})

    assert APP.state.view == "runtime"
    assert "status=failed" in APP.state.sync_message
    assert "stage=qdrant_reindex" in APP.state.sync_message
    assert "Qdrant refused connection" in APP.state.sync_message


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


def test_milvus_smoke_action_reports_search_and_cleanup(monkeypatch):
    APP.state = AppState(country="日本", view="runtime")

    def fake_smoke(country):
        assert country == "日本"
        return {"status": "passed", "search_hit": True, "cleanup_status": "deleted", "vector_size": 1024}

    monkeypatch.setattr(APP.agent, "run_milvus_smoke_diagnostic", fake_smoke)

    handle_action("/milvus_smoke_diagnostic", {"country": ["日本"], "view": ["runtime"]})

    assert APP.state.view == "runtime"
    assert "Milvus smoke 诊断完成" in APP.state.sync_message
    assert "search_hit=True" in APP.state.sync_message
    assert "cleanup=deleted" in APP.state.sync_message


def test_qdrant_manifest_rollback_action_sets_latest_run(monkeypatch):
    APP.state = AppState(country="日本", view="runtime")

    def fake_rollback(country, run_id, *, vector_store=None):
        assert country == "日本"
        assert run_id == "target-run"
        assert vector_store is None
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


def test_qdrant_manifest_rollback_action_can_restore_points_when_confirmed(monkeypatch):
    APP.state = AppState(country="日本", view="runtime")
    monkeypatch.setattr(
        APP.agent,
        "rag_vector_store_config",
        RagVectorStoreConfig(
            provider="qdrant",
            endpoint="http://127.0.0.1:6333",
            collection="puzzle_ops_rag",
            configured=True,
            ready=True,
            status_text="Qdrant ready：http://127.0.0.1:6333 / puzzle_ops_rag",
        ),
    )
    captured = {}

    def fake_rollback(country, run_id, *, vector_store=None):
        captured["has_vector_store"] = vector_store is not None
        return {
            "status": "rolled_back",
            "run_id": run_id,
            "vector_size": 5,
            "upserted_points": 9,
            "restore_status": {"status": "restored", "restored_points": 9},
        }

    monkeypatch.setattr(APP.agent, "rollback_qdrant_manifest", fake_rollback)

    handle_action(
        "/rollback_qdrant_manifest",
        {
            "country": ["日本"],
            "view": ["runtime"],
            "run_id": ["target-run"],
            "restore_points": ["1"],
        },
    )

    assert captured["has_vector_store"] is True
    assert "restore=restored" in APP.state.sync_message
    assert "restored_points=9" in APP.state.sync_message


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


def test_record_rag_eval_failure_feedback_action_writes_working_memory():
    APP.state = AppState(country="日本", view="runtime")

    handle_action(
        "/record_rag_eval_failure_feedback",
        {
            "country": ["日本"],
            "view": ["runtime"],
            "query": ["日本寿司图是否符合本土饮食价值观"],
            "expected_parent_id": ["JP_KB_SUSHI"],
            "retrieved_parent_ids": ["JP_KB_ONSEN、GLOBAL_KB_AUDIT"],
            "note": ["需要补充寿司 hard negative"],
        },
    )

    rows = APP.agent.memory_debug("日本", query="hard negative 寿司", limit=50)
    assert APP.state.view == "runtime"
    assert "RAG eval 失败case已记录" in APP.state.sync_message
    assert any(
        row["memory_type"] == "rag_eval_failure_feedback"
        and row["payload"]["expected_parent_id"] == "JP_KB_SUSHI"
        and "JP_KB_ONSEN" in row["payload"]["retrieved_parent_ids"]
        for row in rows
    )


def test_export_rag_eval_failure_feedback_action_writes_jsonl():
    APP.state = AppState(country="日本", view="runtime")
    APP.agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI",
        retrieved_parent_ids=("JP_KB_ONSEN",),
        note="补充寿司 hard negative",
    )

    handle_action(
        "/export_rag_eval_failure_feedback",
        {
            "country": ["日本"],
            "view": ["runtime"],
        },
    )

    export_path = APP.agent._runtime_dir / "rag_eval_failure_feedback_日本.jsonl"
    assert APP.state.view == "runtime"
    assert "已导出 RAG 失败反馈" in APP.state.sync_message
    assert export_path.exists()
    assert "JP_KB_SUSHI" in export_path.read_text(encoding="utf-8")


def test_export_rag_knowledge_patch_drafts_action_writes_jsonl():
    APP.state = AppState(country="日本", view="runtime")
    APP.agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )

    handle_action(
        "/export_rag_knowledge_patch_drafts",
        {
            "country": ["日本"],
            "view": ["runtime"],
        },
    )

    export_path = APP.agent._runtime_dir / "rag_knowledge_patch_drafts_日本.jsonl"
    assert APP.state.view == "runtime"
    assert "已导出 RAG 知识补丁草案" in APP.state.sync_message
    assert export_path.exists()
    assert "value_rule_patch" in export_path.read_text(encoding="utf-8")


def test_approve_rag_knowledge_patch_draft_action_writes_long_term_memory():
    APP.state = AppState(country="日本", view="runtime")
    APP.agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )
    patch_id = str(APP.agent.rag_knowledge_patch_drafts("日本")["items"][0]["patch_id"])

    handle_action(
        "/approve_rag_knowledge_patch_draft",
        {
            "country": ["日本"],
            "view": ["runtime"],
            "patch_id": [patch_id],
            "human_note": ["运营确认补入日本饮食价值观"],
        },
    )

    rows = APP.agent.memory_debug("日本", query="寿司 饮食 价值观", limit=50)
    assert APP.state.view == "runtime"
    assert "RAG 知识补丁已审核通过" in APP.state.sync_message
    assert any(row["memory_type"] == "approved_rag_knowledge_patch" and row["human_verified"] for row in rows)


def test_mark_rag_feedback_monthly_and_emergency_actions_write_governance_memory(tmp_path):
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
        APP.state = AppState(country="日本", view="runtime")
        feedback_id = APP.agent.record_rag_eval_failure_feedback(
            "日本",
            query="日本版权/IP风险漏召回",
            expected_parent_id="JP_KB_IP_RISK",
            retrieved_parent_ids=("JP_VALUE_001",),
            note="版权/IP 风险漏召回",
        )

        handle_action(
            "/mark_rag_feedback_monthly",
            {"country": ["日本"], "view": ["runtime"], "memory_id": [str(feedback_id)], "review_note": ["月度处理"]},
        )
        handle_action(
            "/mark_rag_feedback_emergency",
            {"country": ["日本"], "view": ["runtime"], "memory_id": [str(feedback_id)], "review_note": ["版权风险紧急处理"]},
        )

        rows = APP.agent.memory_debug("日本", query="版权风险", limit=80)
        assert "已标记为紧急补丁" in APP.state.sync_message
        assert any(row["memory_type"] == "rag_governance_monthly_marker" for row in rows)
        assert any(row["memory_type"] == "rag_governance_emergency_marker" for row in rows)
    finally:
        APP.agent = previous_agent


def test_apply_emergency_rag_patch_and_rebuild_action_reports_smoke(monkeypatch, tmp_path):
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
        APP.state = AppState(country="日本", view="runtime")
        feedback_id = APP.agent.record_rag_eval_failure_feedback(
            "日本",
            query="日本版权/IP风险漏召回",
            expected_parent_id="JP_KB_IP_RISK",
            retrieved_parent_ids=("JP_VALUE_001",),
            note="版权/IP 风险漏召回",
        )

        def fake_apply(country, memory_id, *, actor="", note=""):
            return {"status": "emergency_applied", "country": country, "feedback_memory_id": memory_id, "hit@5": 1.0, "patch_id": "patch-日本-emergency"}

        monkeypatch.setattr(APP.agent, "apply_emergency_rag_patch_and_rebuild", fake_apply)

        handle_action(
            "/apply_emergency_rag_patch_and_rebuild",
            {"country": ["日本"], "view": ["runtime"], "memory_id": [str(feedback_id)], "review_note": ["负责人确认"]},
        )

        assert APP.state.view == "runtime"
        assert "紧急 RAG 补丁已应用" in APP.state.sync_message
        assert "hit@5=1.0" in APP.state.sync_message
    finally:
        APP.agent = previous_agent


def test_export_approved_rag_patch_markdown_action_writes_md():
    APP.state = AppState(country="日本", view="runtime")
    APP.agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )
    patch_id = str(APP.agent.rag_knowledge_patch_drafts("日本")["items"][0]["patch_id"])
    APP.agent.approve_rag_knowledge_patch_draft("日本", patch_id, human_note="运营确认补入日本饮食价值观")

    handle_action(
        "/export_approved_rag_patch_markdown",
        {
            "country": ["日本"],
            "view": ["runtime"],
        },
    )

    export_path = APP.agent._runtime_dir / "approved_rag_patch_日本.md"
    assert APP.state.view == "runtime"
    assert "已导出已审核 RAG Markdown 补丁" in APP.state.sync_message
    assert export_path.exists()
    assert "source_type: approved_rag_patch" in export_path.read_text(encoding="utf-8")


def test_apply_approved_rag_patch_markdown_action_writes_raw_and_manifest(monkeypatch, tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    monkeypatch.setenv("PUZZLEOPS_RAG_KNOWLEDGE_DIR", str(knowledge_dir))
    APP.state = AppState(country="日本", view="runtime")
    APP.agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )
    patch_id = str(APP.agent.rag_knowledge_patch_drafts("日本")["items"][0]["patch_id"])
    APP.agent.approve_rag_knowledge_patch_draft("日本", patch_id, human_note="运营确认补入日本饮食价值观")

    handle_action(
        "/apply_approved_rag_patch_markdown",
        {
            "country": ["日本"],
            "view": ["runtime"],
        },
    )

    latest_manifest_path = knowledge_dir / "patch_manifests" / "rag_patch_apply_日本.json"
    assert APP.state.view == "runtime"
    assert "已应用已审核 RAG Markdown 补丁" in APP.state.sync_message
    assert latest_manifest_path.exists()
    manifest = json.loads(latest_manifest_path.read_text(encoding="utf-8"))
    assert Path(str(manifest["raw_patch_path"])).exists()
    assert manifest["applied_patch_count"] >= 1


def test_apply_approved_rag_patch_and_rebuild_action_reports_eval(monkeypatch, tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    eval_dir = knowledge_dir / "eval"
    eval_dir.mkdir(parents=True)
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
    APP.agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )
    patch_id = str(APP.agent.rag_knowledge_patch_drafts("日本")["items"][0]["patch_id"])
    APP.agent.approve_rag_knowledge_patch_draft("日本", patch_id, human_note="运营确认补入日本饮食价值观")

    handle_action(
        "/apply_approved_rag_patch_and_rebuild",
        {
            "country": ["日本"],
            "view": ["runtime"],
        },
    )

    manifest = json.loads((knowledge_dir / "patch_manifests" / "rag_patch_apply_日本.json").read_text(encoding="utf-8"))
    assert APP.state.view == "runtime"
    assert "已应用补丁并重建 RAG" in APP.state.sync_message
    assert "hit@5=1.0" in APP.state.sync_message
    assert manifest["status"] == "applied_rebuilt"
    assert manifest["rebuild"]["hit@5"] == 1.0


def test_rollback_latest_rag_patch_and_rebuild_action_reports_eval(monkeypatch, tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    eval_dir = knowledge_dir / "eval"
    eval_dir.mkdir(parents=True)
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
    APP.agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )
    patch_id = str(APP.agent.rag_knowledge_patch_drafts("日本")["items"][0]["patch_id"])
    APP.agent.approve_rag_knowledge_patch_draft("日本", patch_id, human_note="运营确认补入日本饮食价值观")
    applied = APP.agent.apply_approved_rag_patch_and_rebuild("日本")

    handle_action(
        "/rollback_latest_rag_patch_and_rebuild",
        {
            "country": ["日本"],
            "view": ["runtime"],
        },
    )

    manifest = json.loads((knowledge_dir / "patch_manifests" / "rag_patch_apply_日本.json").read_text(encoding="utf-8"))
    assert APP.state.view == "runtime"
    assert "已回滚最新 RAG 补丁并重建" in APP.state.sync_message
    assert "hit@5=0.0" in APP.state.sync_message
    assert not Path(str(applied["raw_patch_path"])).exists()
    assert manifest["status"] == "rolled_back_rebuilt"
    assert manifest["rollback"]["removed_raw_patch_path"] == str(applied["raw_patch_path"])


def test_apply_rag_patch_rebuild_and_reindex_qdrant_action_reports_manifest(monkeypatch):
    APP.state = AppState(country="日本", view="runtime")

    def fake_apply(country):
        assert country == "日本"
        return {
            "status": "applied_rebuilt_qdrant_indexed",
            "raw_patch_path": "/tmp/approved_patch.md",
            "processed_path": "/tmp/value_audit_documents.jsonl",
            "hit@5": 1.0,
            "manifest_path": "/tmp/rag_patch_apply_日本.json",
            "qdrant": {
                "status": "indexed",
                "upserted_points": 9,
                "vector_size": 3,
                "manifest_path": "/tmp/qdrant_reindex_日本.json",
            },
        }

    monkeypatch.setattr(APP.agent, "apply_approved_rag_patch_rebuild_and_reindex_qdrant", fake_apply)

    handle_action(
        "/apply_rag_patch_rebuild_and_reindex_qdrant",
        {
            "country": ["日本"],
            "view": ["runtime"],
        },
    )

    assert APP.state.view == "runtime"
    assert "已应用补丁、重建 RAG 并入库 Qdrant" in APP.state.sync_message
    assert "points=9" in APP.state.sync_message
    assert "vector_size=3" in APP.state.sync_message
    assert "patch_manifest=/tmp/rag_patch_apply_日本.json" in APP.state.sync_message


def test_apply_rag_patch_rebuild_and_reindex_vector_store_action_reports_provider(monkeypatch):
    previous_agent = APP.agent
    try:
        APP.agent = PuzzleOpsAgent()
        APP.agent.rag_vector_store_config = RagVectorStoreConfig(
            provider="milvus",
            endpoint="http://127.0.0.1:19530",
            collection="puzzle_ops_rag",
            configured=True,
            ready=True,
        )
        APP.state = AppState(country="日本", view="runtime")

        def fake_apply(country):
            assert country == "日本"
            return {
                "manifest_path": "/tmp/rag_patch_apply_日本.json",
                "vector_store": {
                    "provider": "milvus",
                    "status": "indexed",
                    "upserted_points": 10,
                    "vector_size": 3,
                    "hit@5": 1.0,
                    "mrr@5": 0.9,
                    "manifest_path": "/tmp/milvus_reindex_日本.json",
                },
            }

        monkeypatch.setattr(APP.agent, "apply_approved_rag_patch_rebuild_and_reindex_vector_store", fake_apply)

        handle_action("/apply_rag_patch_rebuild_and_reindex_vector_store", {"country": ["日本"], "view": ["runtime"]})

        assert APP.state.view == "runtime"
        assert "已应用补丁、重建 RAG 并入库 Milvus" in APP.state.sync_message
        assert "points=10" in APP.state.sync_message
        assert "vector_store_manifest=/tmp/milvus_reindex_日本.json" in APP.state.sync_message
    finally:
        APP.agent = previous_agent
