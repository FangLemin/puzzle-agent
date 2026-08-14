import json
from datetime import date

from PIL import Image

from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.models import JS_CATEGORIES
from puzzle_ops.renderer import AppState, compact_trial_remark, render_page, render_rag_summary, render_undistributed_candidate_card
from puzzle_ops.trial_upload import TrialImageUploadService
from puzzle_ops.vision_llm import MissingVisionLLMConfig
from puzzle_ops.image_generation import CloudImageGenerationProvider, DashScopeImageGenerationProvider, ComfyUIImageGenerationProvider
from puzzle_ops.storage import PuzzleRepository


def agent_without_vlm(tmp_path):
    agent = PuzzleOpsAgent()
    agent.rag_vector_store_config = agent.rag_vector_store_config.__class__()
    agent.trial_uploads = TrialImageUploadService(
        tmp_path / "uploads",
        vision_config_error=MissingVisionLLMConfig(("QWEN_API_KEY",), provider="qwen"),
    )
    return agent


def test_dashboard_page_contains_country_workflow_and_holiday_ai_themes():
    html = render_page(PuzzleOpsAgent(today=date(2026, 7, 13)), AppState(country="日本", view="dashboard"))

    assert "首页工作台" in html
    assert "🇯🇵 日本" in html
    assert "🗓️ 周一" in html
    assert "name=\"workflow_0\"" in html
    assert "name=\"task_0\"" in html
    assert "查看完整节日提需建议" in html
    assert "节日提需建议：海の日" not in html
    assert "黄金周" not in html
    holiday_html = render_page(PuzzleOpsAgent(today=date(2026, 7, 13)), AppState(country="日本", view="dashboard", show_holiday=True))
    assert "节日提需建议：海の日" in holiday_html
    assert "周四" in html
    assert "过图会" in html
    assert "海边小旅行" in holiday_html
    assert "家庭出游" in holiday_html


def test_dashboard_keeps_holiday_module_visible_before_active_window():
    html = render_page(PuzzleOpsAgent(today=date(2026, 7, 23)), AppState(country="日本", view="dashboard"))

    assert "节日提需" in html
    assert "下一个节日预告" in html
    assert "山の日" in html
    assert "未进入提前 15 天提需窗口" in html
    assert "黄金周" not in html


def test_dashboard_holiday_panel_uses_real_history_and_explicit_evidence_note(monkeypatch):
    monkeypatch.setenv("HOLIDAY_LLM_ENABLE_REMOTE_CALLS", "0")
    html = render_page(PuzzleOpsAgent(today=date(2026, 7, 31)), AppState(country="日本", view="dashboard", show_holiday=True))

    assert "节日提需建议：山の日" in html
    assert "暂无该节日直接历史样本" in html
    assert "真实历史好图参考" in html
    assert "真实历史坏图避雷" in html
    assert "价值观规则依据" in html
    assert "LLM策划建议（待人工确认）" in html
    assert "山の日历史好图" not in html


def test_weekly_review_page_shows_recycle_queues_and_confirm_action():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="weekly_review"))

    assert "周三复盘工作台" in html
    assert "新增 S/A 图" in html
    assert "下降图" in html
    assert "国家差异" in html
    assert "可复用 tag" in html
    assert "应停用 tag" in html
    assert 'action="/confirm_weekly_review_needs"' in html
    assert "确认生成提需清单" in html


def test_login_page_keeps_original_agent_icon_and_shows_readonly_country():
    html = render_page(PuzzleOpsAgent(), AppState(user_id="jp_owner", country="法国", view="login"))

    assert '<div class="login-logo">🧩</div>' in html
    assert "PuzzleOps Agent" in html
    assert "选择身份与国家" in html
    assert "日本运营" in html
    assert "法国" in html
    assert "只读" in html
    assert "进入只读工作台" in html


def test_shell_sidebar_hides_internal_python_prototype_copy():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="dashboard"))

    assert "PuzzleOps Agent" in html
    assert "纯 Python 后台原型" not in html
    assert "所有页面由 Python 标准库服务端渲染" not in html
    assert "业务逻辑在" not in html


def test_production_day_one_keeps_brazil_russia_us_readonly():
    assert "进入只读工作台" in render_page(PuzzleOpsAgent(), AppState(user_id="br_ru_owner", country="巴西", view="login"))
    assert "进入只读工作台" in render_page(PuzzleOpsAgent(), AppState(user_id="br_ru_owner", country="俄罗斯", view="login"))
    assert "进入只读工作台" in render_page(PuzzleOpsAgent(), AppState(user_id="us_owner", country="美国", view="login"))


def test_runtime_overview_shows_production_launch_status(monkeypatch, tmp_path):
    monkeypatch.setenv("PUZZLEOPS_RUNTIME_DIR", str(tmp_path / "prod_runtime"))

    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="runtime"))

    assert "生产上线收口" in html
    assert "生产运行目录" in html
    assert "日本、法国" in html
    assert "巴西、俄罗斯、美国只读" in html


def test_runtime_page_shows_session_readonly_badge_for_unowned_country():
    html = render_page(PuzzleOpsAgent(), AppState(user_id="jp_owner", country="法国", view="runtime"))

    assert "当前用户：日本运营" in html
    assert "当前国家：法国" in html
    assert "模式：只读" in html
    assert "只读模式" in html


def test_regular_page_renders_business_table_fields_and_empty_delivery_input():
    agent = PuzzleOpsAgent()
    state = AppState(country="日本", view="regular", category="drawing", tag="常规_日本_传统浴袍美女0510")
    state.need_rows.append(agent.add_regular_demand("日本", "drawing", "常规_日本_传统浴袍美女0510", 0))

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
    assert "生成同步草案" not in html
    assert "一键同步到飞书表格" in html
    assert "展开 Prompt 评测" in html
    assert "生成 Prompt 对比评测" not in html
    assert "全选" in html
    assert "批量AI生成主体描述" in html
    assert 'name="selected_rows" value="0"' in html
    assert 'action="/add_regular_all"' in html
    assert 'formtarget="_blank"' not in html
    assert "价值观匹配度" not in html
    assert "常规_日本_传统浴袍美女0510" in html
    assert "stock-hot" in html
    assert "stock-low" in html
    assert 'name="country" value="日本"' in html
    assert 'name="view" value="regular"' in html
    assert 'name="tag" value="常规_日本_传统浴袍美女0510"' in html


def test_regular_page_keeps_prompt_benchmark_collapsed_by_default():
    state = AppState(country="日本", view="regular")
    state.description_benchmarks = [
        {
            "image_name": "猫咪鲤鱼",
            "operation_tag": "常规_日本_猫咪鲤鱼0605",
            "template_subject_description": "主体内容：猫咪鲤鱼；色彩氛围：浅粉；构图环境：日式庭院。",
            "prompt_subject_description": "主体内容：猫咪与锦鲤池；色彩氛围：浅粉、湖蓝、明亮治愈；构图环境：日式庭院近景，主体清晰有层次。",
            "prompt_remark": "保留猫与锦鲤互动，避免动漫IP感。",
            "prompt_status": "ok",
            "prompt_model": "qwen-plus",
            "prompt": "很长的 prompt 原文 " * 30,
        }
    ]

    html = render_page(PuzzleOpsAgent(), state)

    assert "展开 Prompt 评测" in html
    assert "主体描述 Prompt Benchmark" not in html
    assert "批量保存全部评分" not in html


def test_regular_page_shows_description_prompt_benchmark_cards_when_expanded():
    state = AppState(country="日本", view="regular", show_prompt_benchmark=True)
    state.description_benchmarks = [
        {
            "image_name": "猫咪鲤鱼",
            "operation_tag": "常规_日本_猫咪鲤鱼0605",
            "template_subject_description": "主体内容：猫咪鲤鱼；色彩氛围：浅粉；构图环境：日式庭院。",
            "prompt_subject_description": "主体内容：猫咪与锦鲤池；色彩氛围：浅粉、湖蓝、明亮治愈；构图环境：日式庭院近景，主体清晰有层次。",
            "prompt_remark": "保留猫与锦鲤互动，避免动漫IP感。",
            "prompt_status": "ok",
            "prompt_model": "qwen-plus",
            "prompt": "很长的 prompt 原文 " * 30,
        },
        {
            "image_name": "樱花列车",
            "operation_tag": "常规_日本_樱花列车0728",
            "template_subject_description": "主体内容：樱花列车；色彩氛围：暖色；构图环境：轨道。",
            "prompt_subject_description": "主体内容：日本通勤电车穿行樱花林荫道；色彩氛围：粉白暖光；构图环境：浅景深纵深。",
            "prompt_remark": "保留樱花纵深。",
            "prompt_status": "ok",
            "prompt_model": "qwen-plus",
            "prompt": "v3 prompt",
        }
    ]

    html = render_page(PuzzleOpsAgent(), state)

    assert "主体描述 Prompt Benchmark" in html
    assert "当前模板输出" in html
    assert "强 Prompt v3 输出" in html
    assert "Prompt baseline v3" in html
    assert "生成 Prompt 对比评测" in html
    assert "当前模板评分" in html
    assert "强 Prompt评分" in html
    assert "主体准确性" in html
    assert "生产可执行性" in html
    assert "市场适配度" in html
    assert 'action="/save_description_benchmark"' in html
    assert 'class="benchmark-list"' in html
    assert 'class="benchmark-output-grid"' in html
    assert 'class="benchmark-output"' in html
    assert "填写 A/B 评分" in html
    assert 'class="prompt-pre"' in html
    assert 'name="benchmark_count" value="2"' in html
    assert 'name="template_output_0"' in html
    assert 'name="prompt_output_1"' in html
    assert 'name="template_benchmark_score_0_0"' in html
    assert 'name="prompt_benchmark_score_1_4"' in html
    assert "批量保存全部评分" in html
    assert "常规_日本_猫咪鲤鱼0605" in html


def test_eval_page_shows_prompt_benchmark_history_country_and_version_comparison(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    for country, prompt_average in (("日本", 4), ("法国", 5)):
        agent.repository.add_description_benchmark_score(
            {
                "country": country,
                "actor": "tester",
                "image_name": "sample",
                "operation_tag": f"常规_{country}_sample",
                "template_scores": {
                    "subject_accuracy": 2,
                    "production_actionability": 1,
                    "conciseness": 1,
                    "market_fit": 2,
                    "remark_usefulness": 1,
                },
                "prompt_scores": {
                    "subject_accuracy": prompt_average,
                    "production_actionability": prompt_average,
                    "conciseness": prompt_average,
                    "market_fit": prompt_average,
                    "remark_usefulness": prompt_average,
                },
                "template_label": "需要大改",
                "prompt_label": "可直接用",
                "template_output": "旧模板",
                "prompt_output": "v3 输出",
                "metadata": {"prompt_version": "v3"},
            }
        )

    html = render_page(agent, AppState(country="日本", view="eval"))

    assert "<summary>Prompt Benchmark</summary>" in html
    assert "主体描述 Prompt Benchmark" in html
    assert "国家对比" in html
    assert "版本对比" in html
    assert "Prompt baseline v3" in html
    assert "日本" in html
    assert "法国" in html


def test_value_page_keeps_value_prediction_benchmark_collapsed_by_default():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="value"))

    assert "展开价值观预测评测" in html
    assert "生成价值观预测评测" not in html
    assert "价值观预测 Benchmark" not in html


def test_value_page_shows_value_prediction_benchmark_when_expanded():
    state = AppState(country="日本", view="value", show_value_benchmark=True)
    html = render_page(PuzzleOpsAgent(), state)

    assert "价值观预测评测已开启" in html
    assert "生成价值观预测评测" in html
    assert 'name="candidate_id"' in html
    assert "筛选候选ID或tag" in html
    assert "全选可见候选" in html
    assert "JP_CAND_001" in html
    assert "JP_CAND_015" in html


def test_value_candidate_card_shows_metric_level_reasoning():
    agent = PuzzleOpsAgent()
    candidate = agent.undistributed_value_candidates("法国")[0] | {
        "prediction_status": "predicted",
        "predicted_grade": "A",
        "sa_probability": 0.68,
        "open_rate_range": "11%-14%",
        "completion_rate_range": "88%-91%",
        "finish_time_range": "19-22",
        "metric_levels": {"open_rate": "高", "completion_rate": "中", "avg_finish_time": "高"},
        "evidence": "指标分档=高中高，按业务规则推导等级=A。",
        "visual_subject": "法式花园餐桌",
        "visual_scene": "庭院下午茶",
        "visual_style": "明亮写实",
        "risk_points": (),
        "rag_citations": (),
        "rag_citation_details": (),
        "similar_positive": (),
        "similar_negative": (),
    }

    html = render_undistributed_candidate_card(agent, candidate, AppState(country="法国", view="value"))

    assert "指标分档 开图=高 · 完成=中 · 时长=高" in html
    assert "按业务规则推导等级=A" in html


def test_value_candidate_card_shows_low_confidence_visual_similarity_message():
    agent = PuzzleOpsAgent()
    candidate = agent.undistributed_value_candidates("法国")[0] | {
        "prediction_status": "predicted",
        "predicted_grade": "A",
        "sa_probability": 0.68,
        "open_rate_range": "11%-14%",
        "completion_rate_range": "88%-91%",
        "finish_time_range": "19-22",
        "metric_levels": {"open_rate": "高", "completion_rate": "中", "avg_finish_time": "高"},
        "evidence": "指标分档=高中高，按业务规则推导等级=A。",
        "visual_subject": "法式花园餐桌",
        "visual_scene": "庭院下午茶",
        "visual_style": "明亮写实",
        "risk_points": (),
        "rag_citations": (),
        "rag_citation_details": (),
        "similar_positive": (),
        "similar_negative": (),
        "visual_similarity_evidence": {
            "status": "low_confidence",
            "reliability": "low_confidence",
            "message": "暂无可靠历史相似图：当前最高相似分 0.1000 低于校准提示线。",
        },
    }

    html = render_undistributed_candidate_card(agent, candidate, AppState(country="法国", view="value"))

    assert "暂无可靠历史相似图" in html
    assert "展开图像相似依据" in html


def test_eval_page_shows_value_prediction_benchmark_summary(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.repository.add_value_prediction_benchmark_score(
        {
            "country": "日本",
            "actor": "tester",
            "candidate_id": "JP_CAND_001",
            "operation_tag": "试新_日本_樱花",
            "baseline_scores": {
                "visual_accuracy": 4,
                "country_value_fit": 4,
                "history_evidence_fit": 3,
                "rag_citation_usefulness": 4,
                "risk_detection": 3,
                "grade_credibility": 4,
                "metric_range_credibility": 3,
                "actionability": 4,
            },
            "candidate_scores": {
                "visual_accuracy": 5,
                "country_value_fit": 4,
                "history_evidence_fit": 4,
                "rag_citation_usefulness": 4,
                "risk_detection": 4,
                "grade_credibility": 4,
                "metric_range_credibility": 4,
                "actionability": 5,
            },
            "baseline_label": "轻微修改",
            "candidate_label": "可直接用",
            "baseline_output": "baseline",
            "candidate_output": "candidate",
            "metadata": {"candidate_version": "value_prompt_v1"},
        }
    )

    html = render_page(agent, AppState(country="日本", view="eval"))

    assert "<summary>价值观预测 Benchmark</summary>" in html
    assert "图像主体准确性" in html
    assert "value_prompt_v1" in html
    assert "试新_日本_樱花" in html


def test_value_prediction_benchmark_uses_single_model_scoring_ui():
    state = AppState(
        country="日本",
        view="value",
        show_value_benchmark=True,
        value_prediction_benchmarks=[
            {
                "candidate_id": "JP_CAND_001",
                "operation_tag": "试新_日本_樱花",
                "baseline_output": "模型预测输出",
                "candidate_output": "模型预测输出",
            }
        ],
    )

    html = render_page(PuzzleOpsAgent(), state)

    assert "模型预测评分" in html
    assert "候选版本评分" not in html
    assert "candidate_value_score" not in html
    assert "当前线上预测" not in html
    assert "候选版本预测" not in html


def test_regular_page_uses_real_workbook_images_when_available():
    agent = PuzzleOpsAgent()

    html = render_page(agent, AppState(country="日本", view="regular"))

    assert "/local_image?" in html
    assert "未接入真实指标" not in html


def test_trial_page_keeps_value_match_for_new_images():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="trial"))

    assert "价值观匹配度" in html
    assert "价值观大师" in html


def test_regular_page_uses_real_js_categories_and_matching_operation_tag_images():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="regular", category="food", tag="常规_日本_寿司0521"))

    for category in JS_CATEGORIES:
        assert f">{category}<" in html
    assert ">人物<" not in html
    assert ">花卉<" not in html
    assert "常规_日本_寿司0521" in html
    assert "寿司" in html
    assert "10.97%" in html
    assert "90.88%" in html
    assert "18.67" in html
    assert "猫咪鲤鱼" not in html
    assert "天桥立" not in html
    assert "模拟库存" in html
    assert "历史样本" in html


def test_regular_page_prioritizes_low_inventory_historical_hits():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="regular", category="animal", tag="常规_日本_猫咪鲤鱼0605"))

    assert "爆款缺库存" in html
    assert 'class="choice active stock-hot"' in html
    assert "模拟库存 1" in html
    assert html.index("常规_日本_猫咪鲤鱼0605") < html.index("常规_日本_幼猫0608")


def test_regular_page_uses_real_workbook_metrics_instead_of_generated_placeholders():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="regular", category="drawing", tag="常规_日本_传统浴袍美女0510"))

    assert "和服浴衣传统美女" in html
    assert "22.80%" in html
    assert "90.10%" in html
    assert "23.70" in html
    assert "开图 45%" not in html
    assert "完成 80%" not in html


def test_value_master_page_shows_precomputed_undistributed_candidate_pool():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="value"))

    assert "未分发候选排图池" in html
    assert "demo 未分发候选图" not in html
    assert "自制未分发候选图" in html
    assert "导入候选图 Excel" in html
    assert "批量预测当前国家" in html
    assert "预测值" in html
    assert "预测SA概率" in html
    assert "预测开图率" in html
    assert "相似历史好图" in html
    assert "加入下周排图池" in html
    assert "标记人工看好" in html
    assert "重新预测此图" in html
    assert "放弃" not in html
    assert "上传" not in html
    assert "JP_CAND_001" in html
    assert "常规_日本_庭院0721" in html


def test_value_master_page_renders_selected_pool_and_compact_evidence(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_value_candidate_decision("日本", "JP_CAND_001", "优先排图", note="下周主推", actor="jp_owner")

    html = render_page(agent, AppState(country="日本", view="value"))

    assert "本周排图候选池" in html
    assert "JP_CAND_001" in html
    assert "已加入排图池" in html
    assert "下周主推" in html
    assert "展开视觉解析" in html
    assert "展开相似历史图" in html
    assert "展开 RAG 依据" in html
    assert 'class="candidate-evidence-summary"' in html


def test_value_master_human_like_does_not_enter_schedule_pool(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_value_candidate_decision("日本", "JP_CAND_001", "人工看好", note="可以观察", actor="jp_owner")

    html = render_page(agent, AppState(country="日本", view="value"))

    assert "本周排图候选池" in html
    assert "暂无已加入候选" in html
    assert "已加入排图池" not in html
    assert "可以观察" in html


def test_nav_removes_schedule_workspace_and_legacy_schedule_view_redirects_to_value():
    state = AppState(country="日本", view="schedule")
    html = render_page(PuzzleOpsAgent(), state)

    assert state.view == "value"
    assert "价值观大师" in html
    assert "排图工作台" not in html
    assert 'view=schedule' not in html
    assert 'action="/replace_schedule"' not in html


def test_value_master_page_filters_real_candidates_by_country():
    japan_html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="value"))
    france_html = render_page(PuzzleOpsAgent(), AppState(country="法国", view="value"))

    assert "JP_CAND_001" in japan_html
    assert "FR_CAND_001" not in japan_html
    assert "FR_CAND_001" in france_html
    assert "JP_CAND_001" not in france_html


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
    assert "模拟上传并解析" not in html
    assert "请先上传并解析 1-3 张真实历史好图" in html
    assert 'type="file"' in html
    assert 'enctype="multipart/form-data"' in html
    assert 'action="/simulate_trial_upload"' not in html
    assert 'action="/parse_trial_uploads"' not in html
    assert 'action="/generate_trial_derivatives"' not in html
    assert "上传图片" in html
    assert "生成衍生参考图" in html
    assert "生成同步草案" not in html
    assert "一键同步到飞书表格" in html
    assert "未配置 Qwen 图像生成" in html
    assert 'action="/upload_trial_images"' in html
    assert 'formaction="/sync_trial_feishu"' in html
    assert 'formtarget="_blank"' not in html
    assert "解析结果已写入下方试新提需表" in html
    assert "Qwen 视觉解析" in html
    assert "需要配置真实视觉 LLM" in html
    assert "QWEN_API_KEY" in html
    assert "Agent 解析结果" not in html
    assert 'name="delivery_date" value=""' in html
    assert 'name="view" value="trial"' in html
    assert 'name="subject_description"' in html
    assert 'class="demand-card-list trial-demand-list"' in html


def test_trial_status_remark_is_compact_but_keeps_table_remark(tmp_path):
    agent = agent_without_vlm(tmp_path)
    row = agent.create_trial_demand("日本", "flowers", mode="derive").edited(
        remark="视觉LLM：真实qwen，置信度0.91；Prompt：很长很长的生成提示词；二次 VLM 解析与审核通过。"
    )
    state = AppState(country="日本", view="trial", trial_mode="derive", trial_derive_row=row)

    html = render_page(agent, state)

    assert compact_trial_remark(row.remark) == "生成审核完成，详情见表格"
    assert "<dt>解析备注</dt><dd>生成审核完成，详情见表格</dd>" in html
    assert "很长很长的生成提示词" in html


def test_derive_page_shows_editable_prompt_controls_and_candidate_selection(tmp_path):
    agent = agent_without_vlm(tmp_path)
    row = agent.create_trial_demand("日本", "flowers", mode="derive").edited(
        reference_image_path=str(tmp_path / "reference.png"),
        subject="柴犬樱花",
        subject_description="主体内容：柴犬樱花；色彩氛围：粉色治愈；构图环境：日式庭院。",
    )
    candidate = row.edited(image_name="衍生参考图1.png", reference_image_url="/uploads/one.png")
    state = AppState(
        country="日本",
        view="trial",
        trial_mode="derive",
        trial_derive_row=row,
        trial_derivative_candidates=[candidate],
    )

    html = render_page(agent, state)

    assert "衍生 Prompt 设置" in html
    assert 'name="derivative_prompt"' in html
    assert 'name="derivative_negative_prompt"' in html
    assert "恢复推荐 prompt" in html
    assert "单张完整画面" in html
    assert "禁止四宫格" in html
    assert 'name="selected_derivative_candidates"' in html
    assert 'value="0"' in html
    assert "清空候选并重试" in html


def test_derive_page_shows_background_generation_progress(tmp_path):
    agent = agent_without_vlm(tmp_path)
    state = AppState(
        country="日本",
        view="trial",
        trial_mode="derive",
        trial_derivative_job_id="derive-job-1",
        trial_derivative_job_status="running",
        trial_derivative_job_progress=45,
        trial_derivative_job_message="正在并行生成 2 张候选图",
    )

    html = render_page(agent, state)

    assert '<meta http-equiv="refresh" content="3">' in html
    assert "衍生图生成进度" in html
    assert 'value="45" max="100"' in html
    assert "正在并行生成 2 张候选图" in html


def test_derive_prompt_is_editable_before_reference_image_is_ready(tmp_path):
    agent = agent_without_vlm(tmp_path)
    state = AppState(country="日本", view="trial", trial_mode="derive")

    html = render_page(agent, state)

    assert 'name="derivative_prompt"' in html
    assert 'name="derivative_negative_prompt"' in html
    assert "保存 Prompt" in html
    assert 'name="derivative_prompt" rows="5" disabled' not in html
    assert '<button disabled title="请先上传并解析图片">生成衍生参考图</button>' in html
    assert 'class="demand-card-grid"' in html
    assert 'class="demand-long-fields"' in html
    assert 'class="image-preview-cell"' in html
    assert 'class="operation-tag-input"' in html
    assert 'class="small-input"' in html


def test_runtime_page_shows_guarded_actions_workbench(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    proposal = agent.propose_feishu_sync(
        "日本",
        [{"提需分类": "常规", "国家": "日本", "JS分类": "人物", "运营tag": "常规_日本_猫咪0713", "主体内容": "猫咪", "张数": 7, "需求等级": "P1", "加工方式": "纯AI", "图片本身": "猫咪", "主体描述": "主体内容：猫咪；色彩氛围：清爽；构图环境：庭院。", "备注": "人工确认。"}],
        actor="jp_owner",
        source_trace_id="trace-render",
    )

    html = render_page(agent, AppState(user_id="jp_owner", country="日本", view="runtime"))

    assert "Guarded Actions" in html
    assert "待我确认" in html
    assert "确认写入飞书" in html
    assert "查看审计链路" in html
    assert "create: pending_approval" in html
    assert proposal.proposal_id in html


def test_runtime_page_shows_skill_center_contracts(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.adapter.registry.call(
        "warehouse.tag_performance",
        country="日本",
        actor="jp_owner",
        skill_id="regular_demand_skill",
        operation_tag="常规_日本_猫咪0713",
    )

    html = render_page(agent, AppState(user_id="jp_owner", country="日本", view="runtime"))

    assert "Tools Console" in html
    assert "Tool Catalog" in html
    assert "Connector Health" in html
    assert "Recent Invocations" in html
    assert "asset.search_by_tag" in html
    assert "warehouse.tag_performance" in html
    assert "vector.search_value_master" in html
    assert "cms.query_inventory" not in html
    assert "Skill Center" in html
    assert "周三复盘 Skill" in html
    assert "常规提需 Skill" in html
    assert "试新解析 Skill" in html
    assert "价值观审核 Skill" in html
    assert "Memory 治理 Skill" in html
    assert "RAG source" in html
    assert "Memory 写入" in html
    assert "Harness 验收" in html
    assert "Guarded Action" in html
    assert 'action="/run_business_skill"' in html
    assert "运行 Demo" in html


def test_runtime_guarded_actions_are_readonly_for_unowned_country(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.propose_feishu_sync(
        "法国",
        [{"提需分类": "常规", "国家": "法国", "JS分类": "花卉", "运营tag": "常规_法国_薰衣草0713", "主体内容": "薰衣草", "张数": 7, "需求等级": "P1", "加工方式": "纯AI", "图片本身": "薰衣草", "主体描述": "主体内容：薰衣草；色彩氛围：紫色；构图环境：田野。", "备注": "人工确认。"}],
        actor="fr_owner",
    )

    html = render_page(agent, AppState(user_id="jp_owner", country="法国", view="runtime"))

    assert "Guarded Actions" in html
    assert "只读国家仅展示 Guarded Action" in html
    assert "确认写入飞书" not in html


def test_trial_page_shows_value_match_rag_citation_details(tmp_path):
    agent = agent_without_vlm(tmp_path)
    agent.repository = PuzzleRepository(tmp_path / "renderer_rag_feedback.db")
    agent.build_value_audit_rag_index("日本")
    agent.record_rag_citation_feedback("日本", chunk_id="JP_VALUE_001#chunk-1", usefulness="useful", note="能解释寿司")
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
    assert "展开反馈与评分" in html
    assert 'action="/submit_rag_feedback_batch"' in html
    assert 'name="chunk_id_0" value="JP_VALUE_001#chunk-1"' in html
    assert 'name="usefulness_0" value="useful"' in html
    assert 'name="usefulness_0" value="not_useful"' in html
    assert 'action="/record_rag_feedback"' not in html
    assert "已反馈：useful=1 / not_useful=0 / net=1" in html


def test_trial_page_shows_value_match_human_correction_form(tmp_path):
    agent = agent_without_vlm(tmp_path)
    agent.build_value_audit_rag_index("日本")
    state = AppState(country="日本", view="trial", trial_mode="parse")
    state.trial_row = agent.create_trial_demand("日本", "人物", mode="parse").edited(
        subject="寿司",
        value_match="LLM判断：部分符合；系统RAG召回：JP_VALUE_001#chunk-1",
    )

    html = render_page(agent, state)

    assert "展开反馈与评分" in html
    assert 'action="/submit_rag_feedback_batch"' in html
    assert 'name="human_correction"' in html
    assert 'name="satisfaction_score"' in html
    assert "一次性提交反馈" in html


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

    assert "Qwen 图像生成" in html
    assert "Qwen 图像生成 · wanx2.1-t2i-plus" in html


def test_trial_page_shows_dashscope_generation_readiness(tmp_path):
    agent = agent_without_vlm(tmp_path)
    agent.image_generator = DashScopeImageGenerationProvider(
        tmp_path / "generated",
        api_key="shared-qwen-key",
        api_key_source="QWEN_API_KEY",
        model="wan2.6-image",
        sdk_available=False,
    )
    state = AppState(country="法国", view="trial", trial_mode="derive")

    html = render_page(agent, state)

    assert "Qwen 图像生成" in html
    assert "wan2.6-image" in html
    assert "未就绪" in html
    assert "api_key_source" not in html
    assert "sdk_available" not in html


def test_trial_page_shows_comfyui_generation_readiness(tmp_path):
    workflow = tmp_path / "workflow.json"
    workflow.write_text("{}", encoding="utf-8")
    agent = agent_without_vlm(tmp_path)
    agent.image_generator = ComfyUIImageGenerationProvider(
        tmp_path / "generated",
        base_url="http://127.0.0.1:8188",
        workflow_path=str(workflow),
        transport=lambda payload, base_url: {"images": []},
    )
    state = AppState(country="法国", view="trial", trial_mode="derive")

    html = render_page(agent, state)

    assert "ComfyUI 本地图像生成" in html
    assert "本地工作流" in html
    assert str(workflow) in html
    assert "工作流状态" in html
    assert "已配置" in html


def test_runtime_page_shows_rag_feedback_summary(tmp_path):
    agent = agent_without_vlm(tmp_path)
    agent.record_rag_citation_feedback("日本", chunk_id="JP_VALUE_001#chunk-1", usefulness="useful", note="解释寿司价值观")
    agent.record_rag_citation_feedback("日本", chunk_id="AUDIT_001#chunk-1", usefulness="not_useful", note="和本图风险无关")

    html = render_page(agent, AppState(country="日本", view="runtime"))

    assert "RAG 人工反馈" in html
    assert "JP_VALUE_001#chunk-1" in html
    assert "AUDIT_001#chunk-1" in html
    assert "useful=1" in html
    assert "not_useful=1" in html
    assert "RAG 检索评测" in html
    assert "业务对象 Chunk Eval" in html
    assert "citation_precision@5" in html
    assert "risk_miss_rate@5" in html
    assert "任务索引" in html
    assert "value_master" in html
    assert "Milvus 主检索" in html
    assert "fallback" in html
    assert "hit@5" in html
    assert "候选池" in html
    assert "VectorStore search=off" in html
    assert "向量库=local" in html
    assert "向量库 manifest=none" in html
    assert "provider=SQLite" in html
    assert "版本化知识库" in html
    assert "value_audit_cases.jsonl" in html
    assert "raw=" in html
    assert 'action="/rebuild_rag_knowledge"' in html
    assert "重建RAG知识库" in html
    assert 'action="/export_rag_acceptance_report"' in html
    assert "导出RAG验收报告" in html
    assert 'action="/run_full_rag_acceptance"' in html
    assert "一键RAG全链路验收" in html
    assert 'action="/reindex_rag_vector_store"' in html
    assert "重建并入库SQLite" in html
    assert "RAG 检索 Trace" in html
    assert "BM25 召回候选" in html
    assert "向量召回候选" in html
    assert "精排最终命中" in html
    assert "最近 RAG Trace" in html
    assert "可回放 prompt" in html
    assert "Prompt 回放详情" in html
    assert "引用上下文" in html
    assert "检索命中详情" in html


def test_multimodal_page_shows_memory_governance_sections(tmp_path):
    agent = agent_without_vlm(tmp_path)
    agent.record_extracted_fact(
        "日本",
        "verified_value_match_fact",
        {
            "subject": "寿司",
            "operation_tag": "试新_日本_寿司拼盘0609",
            "human_correction": "寿司图符合日本本土饮食文化，适合继续试新。",
        },
    )
    agent.record_working_memory(
        "日本",
        "value_match_human_correction",
        {
            "subject": "寿司",
            "operation_tag": "试新_日本_寿司拼盘0609",
            "human_correction": "寿司图不适合日本市场，存在文化误用风险。",
        },
    )

    html = render_page(agent, AppState(country="日本", view="runtime"))

    assert "Memory Conflict" in html
    assert "Memory Provenance" in html
    assert "同一主体/tag" in html
    assert "试新_日本_寿司拼盘0609" in html
    assert "冲突" in html
    assert "只基于引用依据回答" in html


def test_runtime_page_shows_latest_rag_preflight_summary(tmp_path):
    agent = agent_without_vlm(tmp_path)
    agent._runtime_dir = tmp_path / "runtime"
    report_dir = agent._runtime_dir / "rag_acceptance_reports"
    report_dir.mkdir(parents=True)
    (report_dir / "rag_acceptance_full_summary_日本.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "failure_stage": "rerank_preflight",
                "error": "connection refused",
                "preflight": {
                    "mode": "live",
                    "embedding": {
                        "ready": True,
                        "provider": "dashscope:text-embedding-v4",
                        "vector_size": 1024,
                    },
                    "qdrant": {
                        "ready": True,
                        "provider": "qdrant",
                        "collection": "puzzle_ops_rag",
                    },
                    "rerank": {
                        "ready": False,
                        "provider": "bge:BAAI/bge-reranker-v2-m3",
                        "error": "connection refused",
                    },
                },
                "report": {
                    "hit@5": 0.8,
                    "mrr@5": 0.7,
                    "observed_retrieval": {"qdrant_vector_hits": True},
                    "runtime_stats": {
                        "embedding_remote_calls": 3,
                        "embedding_fallbacks": 0,
                        "rerank_remote_calls": 1,
                        "rerank_fallbacks": 0,
                    },
                },
                "report_path": str(report_dir / "rag_acceptance_full_日本.json"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    html = render_page(agent, AppState(country="日本", view="runtime"))

    assert "RAG Preflight" in html
    assert "mode=live" in html
    assert "status=failed" in html
    assert "stage=rerank_preflight" in html
    assert "embedding ready" in html
    assert "qdrant ready" in html
    assert "rerank not ready" in html
    assert "dashscope:text-embedding-v4" in html
    assert "bge:BAAI/bge-reranker-v2-m3" in html
    assert "connection refused" in html
    assert "full hit@5=0.8" in html
    assert "qdrant_hit=True" in html
    assert "RAG Live Model Ops" in html
    assert "embedding=ready" in html
    assert "rerank=not_ready" in html
    assert "qdrant=ready" in html
    assert "remote embedding=3" in html
    assert "remote rerank=1" in html


def test_runtime_page_uses_current_vector_store_actions_for_milvus(tmp_path):
    agent = agent_without_vlm(tmp_path)
    agent.rag_vector_store_config = agent.rag_vector_store_config.__class__(
        provider="milvus",
        endpoint="http://127.0.0.1:19530",
        collection="puzzle_ops_rag",
        configured=True,
        ready=True,
        status_text="Milvus ready：http://127.0.0.1:19530 / puzzle_ops_rag",
    )

    html = render_page(agent, AppState(country="日本", view="runtime"))

    assert 'action="/reindex_rag_vector_store"' in html
    assert 'action="/apply_rag_patch_rebuild_and_reindex_vector_store"' in html
    assert "重建并入库Milvus" in html
    assert "应用补丁并入库Milvus" in html
    assert "任务索引" in html
    assert "Milvus 主检索" in html
    assert "primary" in html
    assert 'action="/milvus_smoke_diagnostic"' in html
    assert "Milvus Smoke" in html
    assert "向量库 manifest=none" in html
    assert "qdrant manifest=none" not in html
    assert "应用补丁并入库Qdrant" not in html


def test_runtime_page_shows_real_rag_eval_dataset_summary(monkeypatch, tmp_path):
    image_path = tmp_path / "france-picnic.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                f"fr-real-001,法国,{image_path},试新_法国_海滩野餐0625,海滩野餐,lifestyle,real,7,0.42,0.91,38,A,海滩野餐,暖色,海滩构图,生活艺术,,人工确认,human_gold,reviewed",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))

    html = render_page(PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db")), AppState(country="法国", view="runtime"))

    assert "真实 Eval Dataset" in html
    assert "real=1" in html
    assert "human_gold=1" in html
    assert "harness cases=1" in html
    assert "hit@5 threshold=0.8" in html
    assert "target=30-50" in html


def test_runtime_page_shows_business_sample_rag_gate(monkeypatch, tmp_path):
    image_path = tmp_path / "france-picnic.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                f"fr-real-001,法国,{image_path},试新_法国_海滩野餐0625,海滩野餐,lifestyle,real,7,0.42,0.91,38,A,海滩野餐,暖色,海滩构图,生活艺术,,人工确认,human_gold,reviewed",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))

    html = render_page(PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db")), AppState(country="法国", view="runtime"))

    assert "business_hit@5=1.0" in html
    assert "business cases=1" in html
    assert "business_gate=passed" in html


def test_runtime_page_shows_rag_eval_case_evidence(monkeypatch, tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    processed = knowledge_dir / "processed"
    eval_dir = knowledge_dir / "eval"
    processed.mkdir(parents=True)
    eval_dir.mkdir(parents=True)
    (processed / "value_audit_documents.jsonl").write_text(
        json.dumps(
            {
                "document_id": "JP_KB_SUSHI",
                "country": "日本",
                "source_type": "value_rule",
                "title": "日本饮食文化",
                "text": "寿司、抹茶、和果子属于日本本土饮食文化。",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (eval_dir / "value_audit_cases.jsonl").write_text(
        json.dumps(
            {
                "query": "日本寿司图是否符合本土饮食价值观",
                "country": "日本",
                "expected_parent_id": "JP_KB_MISSING",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_RAG_KNOWLEDGE_DIR", str(knowledge_dir))

    html = render_page(PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db")), AppState(country="日本", view="runtime"))

    assert "RAG Eval Case 证据" in html
    assert "日本寿司图是否符合本土饮食价值观" in html
    assert "JP_KB_MISSING" in html
    assert "未命中 expected_parent_id" in html
    assert "FAIL" in html
    assert 'action="/record_rag_eval_failure_feedback"' in html
    assert "记录失败case" in html


def test_runtime_rag_eval_case_evidence_shows_failure_diagnosis():
    html = render_rag_summary(
        {
            "retrieval_eval_report": {
                "dataset_name": "真实 human_gold 业务样本 RAG gate",
                "hit@5": 0.0,
                "mrr@5": 0.0,
                "threshold": 0.8,
                "passed_threshold": False,
            },
            "rag_eval_case_evidence": {
                "dataset_name": "真实 human_gold 业务样本 RAG gate",
                "hit@5": 0.0,
                "mrr@5": 0.0,
                "threshold": 0.8,
                "total": 1,
                "failed_count": 1,
                "cases": (
                    {
                        "status": "FAIL",
                        "query": "法国海边野餐生活艺术",
                        "expected_parent_id": "FR_PICNIC",
                        "retrieved_parent_ids": ("FR_BREAD",),
                        "rank": 0,
                        "failure_reason": "expected parent 未进入 top5",
                        "diagnosis": "knowledge_missing_or_query_mismatch",
                        "suggested_action": "补充 human_gold 知识文档或扩充同义词。",
                    },
                ),
            },
        },
        AppState(country="法国", view="runtime"),
    )

    assert "诊断" in html
    assert "建议动作" in html
    assert "knowledge_missing_or_query_mismatch" in html
    assert "补充 human_gold 知识文档或扩充同义词" in html


def test_runtime_page_shows_rag_failure_feedback_queue(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI",
        retrieved_parent_ids=("JP_KB_ONSEN",),
        note="补充寿司 hard negative",
    )

    html = render_page(agent, AppState(country="日本", view="runtime"))

    assert "RAG失败反馈队列" in html
    assert "待处理=1" in html
    assert "JP_KB_SUSHI" in html
    assert "hard_negative_or_knowledge_patch" in html
    assert 'action="/export_rag_eval_failure_feedback"' in html
    assert "导出RAG失败反馈" in html


def test_runtime_page_shows_rag_knowledge_patch_drafts(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.rag_vector_store_config = agent.rag_vector_store_config.__class__()
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )

    html = render_page(agent, AppState(country="日本", view="runtime"))

    assert "RAG知识补丁草案" in html
    assert "草案=1" in html
    assert "value_rule_patch" in html
    assert "needs_human_review" in html
    assert 'action="/export_rag_knowledge_patch_drafts"' in html
    assert "导出知识补丁草案" in html
    assert 'action="/export_rag_ops_report"' in html
    assert "导出RAG Ops报告" in html
    assert 'action="/approve_rag_knowledge_patch_draft"' in html
    assert "审核通过草案" in html
    assert 'action="/export_approved_rag_patch_markdown"' in html
    assert "导出已审Markdown补丁" in html
    assert 'action="/apply_approved_rag_patch_markdown"' in html
    assert "应用已审补丁到raw" in html
    assert 'action="/apply_approved_rag_patch_and_rebuild"' in html
    assert "应用补丁并重建RAG" in html
    assert 'action="/rollback_latest_rag_patch_and_rebuild"' in html
    assert "回滚最新补丁并重建" in html
    assert 'action="/apply_rag_patch_rebuild_and_reindex_vector_store"' in html
    assert "应用补丁并入库SQLite" in html


def test_runtime_page_shows_rag_quality_governance_workbench(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.rag_vector_store_config = agent.rag_vector_store_config.__class__()
    agent.record_rag_citation_feedback("日本", chunk_id="JP_VALUE_001#chunk-1", usefulness="not_useful", note="召回不相关")
    agent.record_working_memory("日本", "value_match_human_score", {"subject": "寿司", "satisfaction_score": 2}, actor="jp_ops")
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本版权/IP风险漏召回",
        expected_parent_id="JP_KB_IP_RISK",
        retrieved_parent_ids=("JP_VALUE_001",),
        note="版权/IP 风险漏召回，需要紧急补丁",
        diagnosis="knowledge_missing_or_query_mismatch",
        gold_grade="S",
        label_source="human_gold",
    )

    html = render_page(agent, AppState(country="日本", view="runtime"))

    assert "RAG质量治理工作台" in html
    assert "月度重建 + 紧急补丁" in html
    assert "本周异常巡检" in html
    assert "生成月度知识补丁草案" in html
    assert "标记紧急补丁" in html
    assert 'action="/mark_rag_feedback_monthly"' in html
    assert 'action="/mark_rag_feedback_emergency"' in html
    assert 'action="/apply_emergency_rag_patch_and_rebuild"' in html


def test_runtime_page_shows_rag_patch_priority(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_rag_eval_failure_feedback(
        "法国",
        query="法国S级海边野餐生活艺术",
        expected_parent_id="FR_KB_PICNIC",
        retrieved_parent_ids=("FR_KB_BREAD",),
        note="真实S级样本未召回",
        diagnosis="knowledge_missing_or_query_mismatch",
        gold_grade="S",
        label_source="human_gold",
    )

    html = render_page(agent, AppState(country="法国", view="runtime"))

    assert "优先级" in html
    assert "P0" in html
    assert "priority_score" in html
    assert "P0=1" in html
    assert "P1=0" in html
    assert "P2=0" in html
    assert "knowledge_missing_or_query_mismatch" in html


def test_runtime_rag_summary_shows_patch_ops_status():
    html = render_rag_summary(
        {
            "chunk_count": 3,
            "source_counts": {},
            "citations": (),
            "context": "",
            "prompt": "",
            "embedding_provider": "dashscope",
            "embedding_model": "text-embedding-v4",
            "rerank_provider": "bge",
            "rerank_model": "BAAI/bge-reranker-v2-m3",
            "provider_status": "ready",
            "offline_loader": "FileDocumentLoaderAdapter",
            "splitter": "sentence_token",
            "chunk_size_tokens": 600,
            "chunk_overlap_tokens": 100,
            "vector_store": "qdrant",
            "vector_store_collection": "puzzle_ops_rag",
            "vector_store_search_enabled": True,
            "retrieval_eval_report": {"hit@5": 1.0, "mrr@5": 1.0, "threshold": 0.8, "passed_threshold": True},
            "retrieval_trace": {"merged_candidate_count": 0, "eligible_chunk_count": 0},
            "knowledge_base": {},
            "rag_patch_ops": {
                "status": "applied_rebuilt_qdrant_indexed",
                "patch_count": 1,
                "raw_patch_file": "approved_rag_patch_日本_run.md",
                "rebuild_hit@5": 1.0,
                "qdrant_status": "indexed",
                "qdrant_points": 9,
                "qdrant_vector_size": 3,
                "recent_runs": (
                    {
                        "run_id": "run-001",
                        "status": "applied_rebuilt_qdrant_indexed",
                        "patch_count": 1,
                        "rebuild_hit@5": 1.0,
                        "qdrant_status": "indexed",
                        "qdrant_points": 9,
                        "evidence": {
                            "raw_patch_path": "/tmp/approved.md",
                            "processed_path": "/tmp/value_audit_documents.jsonl",
                            "qdrant_manifest_path": "/tmp/qdrant.json",
                            "patch_ids": ("patch-日本-1",),
                        },
                    },
                ),
                "run_comparison": {
                    "current_run_id": "run-001",
                    "previous_run_id": "run-000",
                    "hit@5_delta": 0.5,
                    "mrr@5_delta": 0.3,
                    "qdrant_points_delta": 4,
                    "status_changed": True,
                    "fixed_failure_count": 1,
                    "new_failure_count": 1,
                    "fixed_failures": ("JP_KB_SUSHI",),
                    "new_failures": ("JP_KB_MOUNT_FUJI",),
                },
                "priority_impact": {
                    "pending_P0": 2,
                    "effect": "improved",
                    "recommended_action": "continue_apply_priority_patches",
                },
            },
        },
        AppState(country="日本", view="runtime"),
    )

    assert "RAG Patch Ops" in html
    assert "applied_rebuilt_qdrant_indexed" in html
    assert "patches=1" in html
    assert "hit@5=1.0" in html
    assert "qdrant=indexed" in html
    assert "points=9" in html
    assert "RAG Patch Runs" in html
    assert "run-001" in html
    assert "applied_rebuilt_qdrant_indexed" in html
    assert "证据" in html
    assert "patch-日本-1" in html
    assert "/tmp/qdrant.json" in html
    assert "RAG Patch Compare" in html
    assert "run-001 vs run-000" in html
    assert "hit@5 Δ=0.5" in html
    assert "points Δ=4" in html
    assert "pending_P0=2" in html
    assert "effect=improved" in html
    assert "continue_apply_priority_patches" in html
    assert "fixed=1" in html
    assert "new_failures=1" in html
    assert "JP_KB_SUSHI" in html


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
    assert "业务上线验收" in html
    assert "S/A预测准确率" in html
    assert "RAG Citation Precision" in html
    assert "飞书字段完整率" in html
    assert "Harness Readiness" in html
    assert "尚不能证明真实业务效果" in html
    assert "补齐 1 张样本的主体、色彩、构图、价值观标签" in html
    assert "gold 完成率" in html
    assert "业务指标完成率" in html
    assert "0%" in html
    assert 'action="/save_harness_gold_label"' in html
    assert 'name="gold_subject"' in html
    assert 'name="gold_color_mood"' in html
    assert 'name="gold_composition"' in html
    assert 'name="position"' in html
    assert 'name="open_rate"' in html
    assert 'name="completion_rate"' in html
    assert 'name="avg_finish_time"' in html
    assert 'action="/export_harness_gold_skeleton"' in html
    assert 'action="/register_harness_real_samples"' in html
    assert 'name="samples_text"' in html
    assert "开图率" in html
    assert "平均完成时长" in html


def test_eval_page_shows_front_two_layers_landing_audit(tmp_path):
    agent = agent_without_vlm(tmp_path)
    agent.record_perception_memory("日本", "trial_image_parse", {"subject": "寿司", "color_mood": "清爽"})
    agent.record_working_memory("日本", "trial_state", {"operation_tag": "试新_日本_寿司0626", "status": "parsed"})
    agent.record_long_term_memory("日本", "value_rule_approval", {"rule_text": "寿司提需需保留日式餐桌语境。"})
    agent.record_extracted_fact("日本", "image_semantic_fact", {"subject": "寿司", "value_labels": ["本土饮食文化"]})

    html = render_page(agent, AppState(country="日本", view="eval"))

    assert "前两层落地验收" in html
    assert "front_two_layers_landed" in html
    assert "真实样本接入工作台" in html
    assert "四层 Memory 可进入 RAG" in html
    assert "RAG 多路召回与引用溯源" in html
    assert "已接入 45 张真实拼图样本" in html


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


def test_eval_page_shows_ai_prelabel_progress_summary(monkeypatch, tmp_path):
    picnic = tmp_path / "france-picnic.png"
    lavender = tmp_path / "france-lavender.png"
    garden = tmp_path / "france-garden.png"
    picnic.write_bytes(b"fake-png")
    lavender.write_bytes(b"fake-png")
    garden.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                f"fr-real-001,法国,{picnic},试新_法国_样本一0623,待AI预标注,lifestyle,real,0,0,0,0,A,,,,,,,manual_grade,needs_ai_prelabeled",
                f"fr-real-002,法国,{lavender},试新_法国_样本二0623,薰衣草风车,landscape,real,4,0.36,0.91,42,S,薰衣草风车,紫色,普罗旺斯田野,法式乡村,,AI silver,ai_silver,pending_review",
                f"fr-real-003,法国,{garden},试新_法国_样本三0623,法式花园,travel,real,5,0.4,0.92,40,A,法式花园,暖色,庭院构图,生活艺术,,人工确认,human_gold,reviewed",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    html = render_page(agent, AppState(country="法国", view="eval"))

    assert "AI 预标注进度" in html
    assert "待预标注 1" in html
    assert "待审核 silver 1" in html
    assert "human_gold 1" in html


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


def test_eval_gold_dataset_prioritizes_batch_review_workflow(monkeypatch, tmp_path):
    image_path = tmp_path / "france-picnic.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                "fr-real-001,法国,france-picnic.png,试新_法国_待解析0623,待AI预标注,lifestyle,real,0,0,0,0,A,,,,,,,manual_grade,needs_ai_prelabeled",
                "fr-real-002,法国,france-picnic.png,试新_法国_待确认0623,法式海滩野餐,lifestyle,real,5,0.36,0.91,42,S,法式海滩野餐,暖色,海边沙滩,生活艺术,,AI silver label，待人工抽查。,ai_silver,pending_review",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    html = render_page(agent, AppState(country="法国", view="eval"))

    assert '<details class="governance-section gold-dataset-section" open><summary>Gold Dataset</summary>' in html
    assert '<details class="compact-tools"><summary>新增真实样本入口</summary>' in html
    assert 'id="prelabel-selected-form"' in html
    assert 'form="prelabel-selected-form"' in html
    assert 'type="checkbox" name="sample_id" value="fr-real-001"' in html
    assert "勾选样本 AI 预标注" in html
    assert "批量确认勾选 silver 为 human_gold" in html
    assert "全选待解析" in html
    assert "全选待确认" in html
    assert "data-select-form=\"prelabel-selected-form\"" in html
    assert "data-select-form=\"approve-silver-form\"" in html
    assert "selectGoldDatasetRows" in html
    assert "预标注进度" in html
    assert "<progress" in html


def test_eval_page_shows_qwen_prelabel_job_progress():
    state = AppState(
        country="日本",
        view="eval",
        harness_prelabel_job_id="prelabel-job-1",
        harness_prelabel_job_status="running",
        harness_prelabel_job_progress=42,
        harness_prelabel_job_message="Qwen 正在解析 2/5：常规_日本_猫咪鲤鱼0605",
    )

    html = render_page(PuzzleOpsAgent(), state)

    assert '<meta http-equiv="refresh" content="3">' in html
    assert "Qwen 预标注进度" in html
    assert "Qwen 正在解析 2/5" in html
    assert '<progress value="42" max="100">' in html


def test_eval_page_shows_human_gold_approval_job_progress():
    state = AppState(
        country="日本",
        view="eval",
        harness_approval_job_id="approve-gold-job-1",
        harness_approval_job_status="running",
        harness_approval_job_progress=55,
        harness_approval_job_message="正在确认 11/20：常规_日本_猫咪鲤鱼0605",
    )

    html = render_page(PuzzleOpsAgent(), state)

    assert '<meta http-equiv="refresh" content="3">' in html
    assert "human_gold 批量确认进度" in html
    assert "正在确认 11/20" in html
    assert '<progress value="55" max="100">' in html


def test_eval_gold_dataset_lists_all_real_samples(monkeypatch, tmp_path):
    image_path = tmp_path / "france-picnic.png"
    image_path.write_bytes(b"fake-png")
    rows = [
        "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status"
    ]
    for index in range(1, 15):
        rows.append(
            f"fr-real-{index:03d},法国,{image_path},试新_法国_样本{index:02d},待AI预标注,lifestyle,real,0,0,0,0,A,,,,,,,manual_grade,needs_ai_prelabeled"
        )
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text("\n".join(rows), encoding="utf-8")
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    html = render_page(agent, AppState(country="法国", view="eval"))

    assert "当前显示 14 / 14 条真实样本" in html
    assert "fr-real-001" in html
    assert "fr-real-014" in html


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


def test_eval_page_shows_row_level_business_metric_status(monkeypatch, tmp_path):
    missing_image = tmp_path / "france-lace.png"
    complete_image = tmp_path / "france-lavender.png"
    missing_image.write_bytes(b"fake-png")
    complete_image.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                "fr-real-001,法国,france-lace.png,试新_法国_蕾丝桌旗0623,蕾丝桌旗,still_life,real,0,0,0,0,C,蕾丝桌旗,暖色,室内桌面,生活艺术,,AI silver label，待人工抽查。,ai_silver,pending_review",
                "fr-real-002,法国,france-lavender.png,试新_法国_薰衣草风车0623,薰衣草风车,landscape,real,4,0.36,0.91,42,S,薰衣草风车,紫色,普罗旺斯田野,法式乡村,,AI silver label，待人工抽查。,ai_silver,pending_review",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    html = render_page(agent, AppState(country="法国", view="eval"))

    assert "缺业务指标" in html
    assert "position、open_rate、completion_rate、avg_finish_time" in html
    assert "业务指标齐全" in html


def test_trial_page_has_generation_provider_diagnostic_action(tmp_path):
    agent = agent_without_vlm(tmp_path)
    state = AppState(country="日本", view="trial", trial_mode="derive")

    html = render_page(agent, state)

    assert "Qwen 图像生成诊断" in html
    assert "检查 Qwen 图像生成" in html
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
        "recovery_hint": "请迁移到当前可用模型后重试。",
        "message": "模型 qwen3-vl-flash 已下线，请迁移。",
    }

    html = render_page(PuzzleOpsAgent(), state)

    assert "最近一次生成任务" in html
    assert "failed" in html
    assert "Qwen 图像生成" in html
    assert "model_deprecated" in html
    assert "处理建议" in html
    assert "请迁移到当前可用模型后重试" in html
    assert "模型 qwen3-vl-flash 已下线" in html
    assert "task-123" in html
    assert "试新_日本_寿司0615" in html
    assert "/tmp/out-1.png" in html
    assert "not_started" in html
    assert "blocked" in html


def test_sync_success_message_renders_feishu_link_without_popup_dependency():
    agent = PuzzleOpsAgent()
    state = AppState(country="日本", view="trial", sync_message="已一键同步试新提需到飞书表格：1 条。", sync_url="https://feishu.cn/base/app?table=tbl")

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


def test_sync_record_is_not_primary_navigation_or_header_action():
    agent = PuzzleOpsAgent()

    html = render_page(agent, AppState(country="日本", view="dashboard"))

    assert ">🔁 同步记录</a>" not in html
    assert "view=sync" not in html


def test_runtime_tools_actions_contains_feishu_lightweight_sync_history(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.repository.add_sync_event("日本", "提需同步", "飞书提需表", "成功")

    html = render_page(agent, AppState(country="日本", view="runtime"))

    assert "飞书同步轻量历史" in html
    assert "兼容旧版同步日志" in html
    assert "提需同步" in html
    assert "飞书提需表" in html


def test_legacy_schedule_page_redirects_to_value_master():
    state = AppState(country="法国", view="schedule", schedule_day="周六")
    html = render_page(PuzzleOpsAgent(), state)

    assert state.view == "value"
    assert "价值观大师" in html
    assert "排图工作台" not in html
    assert 'action="/replace_schedule"' not in html


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
    assert ('<img src="data:image/png;base64,' in html) or ('<img src="/local_image?' in html)


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

    assert '<span class="metric-value metric-miss">32%</span><span class="metric-sep">/</span><span class="okr-value">35%</span>' in japan
    assert "本季度累计 AI率 / OKR" in japan
    assert '<span class="metric-value metric-ok">16%</span><span class="metric-sep">/</span><span class="okr-value">30%</span>' in japan
    assert '<span class="metric-value metric-ok">14%</span><span class="metric-sep">/</span><span class="okr-value">35%</span>' in france
    assert '<span class="metric-value metric-miss">28%</span><span class="metric-sep">/</span><span class="okr-value">30%</span>' in france
    assert '<span class="okr-value">35%</span><span class="metric-alert">!</span>' not in japan


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
    }.items():
        html = render_page(agent, AppState(country="日本", view=view))
        assert f'<span class="page-icon">{icon}</span>' in html


def test_multimodal_runtime_page_shows_profile_candidates_and_evidence():
    agent = PuzzleOpsAgent()
    agent.rag_vector_store_config = agent.rag_vector_store_config.__class__()
    html = render_page(agent, AppState(country="日本", view="runtime"))

    assert "系统治理中心" in html
    assert "系统健康" in html
    assert "今日待处理" in html
    assert "最近风险" in html
    assert "<summary>Memory 治理</summary>" in html
    assert "<summary>RAG 治理</summary>" in html
    assert "<summary>Tools / Actions</summary>" in html
    assert "<summary>Skill Center</summary>" in html
    assert "<summary>Debug</summary>" in html
    assert "这个页面用来确认 Agent 的知识、RAG、工具链和审批链路是否健康。" in html
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
    assert "离线建库" in html
    assert "sentence_token" in html
    assert "在线检索" in html
    assert "SQLite 本地 chunk store + embedding cache" in html
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
            "error_type": "billing_arrearage",
            "recovery_hint": "请到阿里云控制台处理账号欠费、余额或资源包状态。",
            "message": "DashScope 图像生成失败：quota exceeded",
        },
    )

    html = render_page(agent, AppState(country="日本", view="eval"))

    assert "上线验收中心" in html
    assert "当前 Agent 是否具备上线验收条件" in html
    assert "human_gold" in html
    assert "S/A预测准确率" in html
    assert "RAG citation precision" in html
    assert "风险漏召回" in html
    assert "工具调用成功率" in html
    assert "<summary>Gold Dataset</summary>" in html
    assert "<summary>失败样本</summary>" in html
    assert "<summary>RAG 证据</summary>" in html
    assert "<summary>运行历史</summary>" in html
    assert "<summary>Debug Trace</summary>" in html
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
    assert "生成外部阻塞率" in html
    assert "生成Agent失败率" in html
    assert "生成恢复建议覆盖率" in html
    assert "二次审核通过率" in html
    assert "飞书附件Ready率" in html
    assert "生成失败类型分布" in html
    assert "billing_arrearage" in html
    assert "请到阿里云控制台处理账号欠费" in html
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


def test_eval_page_has_directory_real_sample_registration_form():
    html = render_page(PuzzleOpsAgent(), AppState(country="法国", view="eval"))

    assert 'name="image_dir"' in html
    assert 'name="directory_grade_text"' in html
    assert 'name="directory_js_category"' in html
    assert "1A 2A 3B 4S 5C" in html
    assert "按目录登记真实样本" in html


def test_harness_sample_thumb_uses_local_image_route_instead_of_inline_base64(tmp_path, monkeypatch):
    image_path = tmp_path / "large-real-sample.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 200_000)
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                f"real-001,法国,{image_path},试新_法国_真实样本0625,海滩野餐,lifestyle,real,0,0,0,0,A,海滩野餐,暖色,海滩构图,生活艺术,,AI silver,ai_silver,pending_review",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))

    html = render_page(PuzzleOpsAgent(), AppState(country="法国", view="eval"))

    assert "/local_image?path=" in html
    assert "data:image/png;base64" not in html
    assert len(html) < 120_000


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
    assert "failure-review-list" in html
    assert "failure-review-card" in html
    assert "table class=\"failure-review-table\"" not in html
    assert "real-sushi.png" in html
    assert "Gold Label" in html
    assert "gold_subject=寿司" in html
    assert "gold_color_mood=米白与鲑鱼橙" in html
    assert "Agent 输出" in html
    assert "失败原因" in html
    assert 'action="/save_harness_override"' in html
    assert "name=\"sample_id\"" in html


def test_eval_gold_dataset_explains_operator_workflow():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="eval"))

    assert "Gold Dataset 是什么" in html
    assert "上线验收标准答案集" in html
    assert "日常运营不需要每天维护" in html
    assert "你需要做什么" in html
    assert "补充真实样本" in html
    assert "确认 AI 预标注为 human_gold" in html


def test_eval_page_shows_real_baseline_summary(monkeypatch, tmp_path):
    image_path = tmp_path / "real-picnic.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                f"real-001,法国,{image_path},试新_法国_海滩野餐0625,海滩野餐,lifestyle,real,7,0.42,0.91,38,A,海滩野餐,暖色,海滩构图,生活艺术,,人工确认,human_gold,reviewed",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))

    html = render_page(PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db")), AppState(country="法国", view="eval"))

    assert "真实 Baseline 复盘" in html
    assert "human_gold 覆盖率" in html
    assert "失败 case 数" in html
    assert "Top 失败分类" in html


def test_eval_page_shows_case_evidence_trace_and_failure_categories():
    html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="eval"))

    assert "Case 证据链" in html
    assert "RAG 引用" in html
    assert "RAG Trace" in html
    assert "Harness RAG Artifacts" in html
    assert "Prompt 回放详情" in html
    assert "引用上下文" in html
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
    assert 'action="/migrate_memory_country"' in html
    assert "晋升为事实" in html
    assert "迁移国家" in html


def test_runtime_page_exposes_memory_workbench_filters_and_audit_columns():
    agent = PuzzleOpsAgent()
    agent.record_perception_memory("日本", "vision_parse", {"subject": "寿司"}, actor="jp_owner")
    html = render_page(agent, AppState(country="日本", view="runtime", memory_layer="perception", memory_subject="寿司"))

    assert "Memory 工作台筛选" in html
    assert 'name="memory_layer"' in html
    assert 'name="memory_review_status"' in html
    assert 'name="memory_created_by"' in html
    assert 'name="memory_subject"' in html
    assert "RAG命中" in html
    assert "not useful" in html


def test_runtime_page_shows_team_memory_lifecycle_summary(tmp_path):
    agent = agent_without_vlm(tmp_path)
    agent.record_personal_preference_memory("日本", "jp_owner", {"subject": "猫", "preference": "优先看猫类素材"})
    stale_id = agent.record_extracted_fact("日本", "market_fact", {"subject": "寿司", "rule": "旧规则"})
    replacement_id = agent.record_extracted_fact("日本", "market_fact", {"subject": "寿司", "rule": "新规则", "supersedes_memory_ids": [stale_id]})
    agent.review_memory(stale_id, action="approve_rag", actor="jp_owner")
    agent.review_memory(replacement_id, action="approve_rag", actor="jp_owner")

    html = render_page(agent, AppState(country="日本", view="runtime"))

    assert "团队级生命周期" in html
    assert "个人偏好不进入市场事实 RAG" in html
    assert "SQLite/Postgres + Milvus + Redis" in html


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
    assert 'action="/export_harness_external_eval"' in html
    assert "导出外部评测文件" in html


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
    value_html = render_page(PuzzleOpsAgent(), AppState(country="日本", view="value", value_grade="all"))

    assert ('<img src="data:image/png;base64,' in html) or ('<img src="/local_image?' in html)
    assert ('<img src="data:image/png;base64,' in value_html) or ('<img src="/local_image?' in value_html)
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
    assert "确认加入提需表" in html
