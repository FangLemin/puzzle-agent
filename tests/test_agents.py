from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.trial_upload import TrialImageUploadService
from puzzle_ops.vision_llm import MissingVisionLLMConfig, OpenAIVisionLLMClient
from puzzle_ops.storage import PuzzleRepository
from puzzle_ops.audit import AuditPolicyRetriever
from puzzle_ops.rag import BGERerankProvider, RagGeneratedAnswer, RagProviderConfig, RagRuntimeStats
from puzzle_ops.trial_upload import TrialImageUploadService
from puzzle_ops.vision_llm import VisionLLMResult
from datetime import date
from pathlib import Path
import json
from PIL import Image


def test_country_data_is_isolated_between_japan_and_france():
    agent = PuzzleOpsAgent(today=date(2026, 6, 9))

    japan = agent.dashboard("日本")
    france = agent.dashboard("法国")

    assert japan["country_label"] == "🇯🇵 日本"
    assert france["country_label"] == "🇫🇷 法国"
    assert "常规_日本_传统浴袍美女0604" in japan["tasks"][0]["body"]
    assert "常规_法国_薰衣草0604" in france["tasks"][0]["body"]


def test_memory_debug_exposes_layer_source_and_query_match(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_perception_memory("日本", "vision_parse", {"subject": "寿司", "color_mood": "清爽明亮"})
    agent.record_working_memory("日本", "trial_state", {"operation_tag": "试新_日本_寿司0622", "status": "parsed"})
    agent.record_long_term_memory("日本", "approved_value_rule", {"rule": "寿司符合日本本土饮食文化"})
    agent.record_extracted_fact("日本", "image_fact", {"subject": "寿司", "country": "日本"})

    rows = agent.memory_debug("日本", query="寿司")

    assert {row["layer"] for row in rows} == {"perception", "working", "long_term", "facts"}
    assert all(row["rag_source_type"] for row in rows)
    assert all(row["rag_ready"] for row in rows)
    assert rows[0]["match_score"] >= rows[-1]["match_score"]


def test_agent_promotes_memory_and_rag_uses_only_active_target(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    source_id = agent.record_perception_memory("日本", "vision_parse", {"subject": "寿司", "scene": "料理桌面"})

    target_id = agent.promote_memory(
        source_id,
        target_layer="facts",
        human_note="运营确认视觉事实",
    )
    documents = agent._layered_memory_rag_documents("日本")
    debug = agent.memory_debug("日本", query="寿司")

    assert target_id != source_id
    assert sum(1 for document in documents if "subject=寿司" in document.text) == 1
    assert {row["status"] for row in debug} == {"active", "promoted"}
    assert any(row["source_memory_id"] == source_id for row in debug if row["status"] == "active")


def test_agent_retires_memory_and_removes_it_from_rag(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    memory_id = agent.record_long_term_memory("日本", "approved_rule", {"rule": "寿司属于本土饮食文化"})

    agent.retire_memory(memory_id)

    assert all("寿司属于本土饮食文化" not in document.text for document in agent._layered_memory_rag_documents("日本"))
    row = next(item for item in agent.memory_debug("日本") if item["memory_id"] == memory_id)
    assert row["status"] == "retired"


def test_audit_review_falls_back_when_manual_is_not_readable(monkeypatch):
    def denied(cls, path):
        raise PermissionError(f"cannot read {path}")

    monkeypatch.setattr(AuditPolicyRetriever, "from_docx", classmethod(denied))

    review = PuzzleOpsAgent().audit_review("画面出现品牌 LOGO")

    assert review.risk_level == "高"
    assert "商标" in review.reason
    assert review.evidence == ()


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


def test_value_master_passes_rag_citations_to_llm_prompt():
    captured = {}

    def fake_transport(payload, api_key):
        captured["payload"] = payload
        return {
            "output_text": '{"value_match":"LLM判断：寿司符合日本本土饮食文化。","confidence":0.9,"evidence":["JP_VALUE_001#chunk-1"],"risk_tags":[]}'
        }

    agent = PuzzleOpsAgent()
    agent.trial_uploads = TrialImageUploadService(
        agent._runtime_dir / "value_master_rag",
        vision_client=OpenAIVisionLLMClient(
            api_key="sk-test",
            transport=fake_transport,
        ),
    )
    row = agent.create_trial_demand("日本", "人物", mode="parse").edited(
        subject="寿司",
        operation_tag="试新_日本_寿司0616",
        subject_description="主体内容：寿司；色彩氛围：米白与鲑鱼橙；构图环境：日式料理店铺餐桌近景。",
    )

    agent.apply_value_master(row)

    prompt = captured["payload"]["input"][0]["content"][0]["text"]
    assert "#chunk-" in prompt
    assert "寿司" in prompt
    assert "引用依据" in prompt or "JP_VALUE" in prompt


def test_value_master_passes_generated_rag_answer_to_llm_prompt():
    captured = {}

    def fake_transport(payload, api_key):
        captured["payload"] = payload
        return {
            "output_text": '{"value_match":"LLM判断：寿司图符合日本本土饮食文化，引用RAG生成答案判断。","confidence":0.92,"evidence":["寿司图"],"risk_tags":[]}'
        }

    agent = PuzzleOpsAgent()
    agent.trial_uploads = TrialImageUploadService(
        agent._runtime_dir / "value_master_generated_rag",
        vision_client=OpenAIVisionLLMClient(api_key="sk-test", transport=fake_transport),
    )

    def fake_generated_answer(country, query, top_k=6, **kwargs):
        return RagGeneratedAnswer(
            answer="结论：符合；依据：寿司属于日本本土饮食文化；风险：避免品牌露出；建议：保留日式餐桌语境。",
            status="generated",
            provider="qwen",
            model="qwen3.7-plus",
            citations=("JP_SUSHI#chunk-1",),
            prompt="generated prompt",
        )

    agent.value_audit_rag_generated_answer = fake_generated_answer
    row = agent.create_trial_demand("日本", "人物", mode="parse").edited(
        subject="寿司",
        operation_tag="试新_日本_寿司0616",
        subject_description="主体内容：寿司；色彩氛围：米白与鲑鱼橙；构图环境：日式料理店铺餐桌近景。",
    )

    judged = agent.apply_value_master(row)

    prompt = captured["payload"]["input"][0]["content"][0]["text"]
    assert "生成式RAG答案" in prompt
    assert "寿司属于日本本土饮食文化" in prompt
    assert "JP_SUSHI#chunk-1" in prompt
    assert "生成式RAG依据：" in judged.value_match
    assert "寿司属于日本本土饮食文化" in judged.value_match


def test_value_master_appends_system_rag_citations_when_llm_omits_them():
    agent = PuzzleOpsAgent()
    agent.trial_uploads = TrialImageUploadService(
        agent._runtime_dir / "value_master_rag_trace",
        vision_client=OpenAIVisionLLMClient(
            api_key="sk-test",
            transport=lambda payload, api_key: {
                "output_text": '{"value_match":"LLM判断：寿司符合日本本土饮食文化。","confidence":0.9,"evidence":["主体为寿司"],"risk_tags":[]}'
            },
        ),
    )
    row = agent.create_trial_demand("日本", "人物", mode="parse").edited(
        subject="寿司",
        operation_tag="试新_日本_寿司0616",
        subject_description="主体内容：寿司；色彩氛围：米白与鲑鱼橙；构图环境：日式料理店铺餐桌近景。",
    )

    judged = agent.apply_value_master(row)

    assert "系统RAG召回：" in judged.value_match
    assert "#chunk-" in judged.value_match
    assert "寿司" in judged.value_match


def test_agent_resolves_value_match_rag_citation_details():
    agent = PuzzleOpsAgent()
    agent.build_value_audit_rag_index("日本")
    row = agent.create_trial_demand("日本", "人物", mode="parse").edited(
        value_match="结论：符合；系统RAG召回：JP_VALUE_001#chunk-1、AUDIT_001#chunk-1"
    )

    details = agent.value_match_rag_citation_details(row)

    assert details
    assert any(item["chunk_id"] == "JP_VALUE_001#chunk-1" for item in details)
    assert any(item["source_type"] == "value_rule" for item in details)
    assert any("寿司" in item["text"] or "日本" in item["text"] for item in details)


def test_agent_records_rag_citation_feedback_as_working_memory():
    agent = PuzzleOpsAgent()

    memory_id = agent.record_rag_citation_feedback(
        "日本",
        chunk_id="JP_VALUE_001#chunk-1",
        usefulness="useful",
        note="这条依据能解释寿司价值观",
        task_type="trial_value_match",
    )

    rows = agent.memory_debug("日本", query="寿司价值观", limit=20)
    feedback = next(row for row in rows if row["memory_id"] == memory_id)
    assert feedback["layer"] == "working"
    assert feedback["memory_type"] == "rag_citation_feedback"
    assert feedback["payload"]["chunk_id"] == "JP_VALUE_001#chunk-1"
    assert feedback["payload"]["usefulness"] == "useful"
    assert feedback["payload"]["note"] == "这条依据能解释寿司价值观"


def test_agent_aggregates_rag_citation_feedback_for_rerank_tuning(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_feedback.db"))
    agent.record_rag_citation_feedback("日本", chunk_id="JP_VALUE_001#chunk-1", usefulness="useful", note="解释寿司价值观")
    agent.record_rag_citation_feedback("日本", chunk_id="JP_VALUE_001#chunk-1", usefulness="useful", note="能支撑本土饮食文化")
    agent.record_rag_citation_feedback("日本", chunk_id="AUDIT_001#chunk-1", usefulness="not_useful", note="和本图风险无关")

    summary = agent.rag_feedback_summary("日本")

    assert summary["total_feedback"] == 3
    assert summary["useful_count"] == 2
    assert summary["not_useful_count"] == 1
    assert summary["top_chunks"][0]["chunk_id"] == "JP_VALUE_001#chunk-1"
    assert summary["top_chunks"][0]["useful_count"] == 2
    assert summary["top_chunks"][0]["net_score"] == 2
    assert summary["top_chunks"][1]["chunk_id"] == "AUDIT_001#chunk-1"
    assert summary["top_chunks"][1]["net_score"] == -1


def test_agent_rag_answer_uses_feedback_bias_for_local_rerank(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_feedback_rank.db"))
    for index in range(12):
        agent.record_rag_citation_feedback(
            "日本",
            chunk_id="JP_VALUE_002#chunk-1",
            usefulness="useful",
            note=f"季节感依据有效 {index}",
        )

    answer = agent.value_audit_rag_answer("日本", "日本柔和自然主色明确价值观", top_k=2)

    assert answer.citations[0] == "JP_VALUE_002#chunk-1"


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
    assert important
    assert all(row.position_is_red for row in important)
    assert all(row.remark_editable for row in report.rows)


def test_analysis_report_uses_updated_country_okr_from_business_background():
    agent = PuzzleOpsAgent()

    japan = agent.analysis_report("日本")
    france = agent.analysis_report("法国")

    assert japan.sa_okr == "35%"
    assert japan.ai_okr == "30%"
    assert france.sa_okr == "30%"
    assert france.ai_okr == "35%"


def test_analysis_report_uses_new_real_business_workbook_metrics():
    agent = PuzzleOpsAgent()

    japan = agent.analysis_report("日本")
    france = agent.analysis_report("法国")

    assert len(japan.rows) == 25
    assert japan.sa_ratio == "52%"
    assert japan.cd_ratio == "20%"
    assert japan.ai_ratio == "48%"
    assert {"AI", "素材网"}.issubset({row.source for row in japan.rows})
    assert {"S", "A", "B", "C", "D"}.issubset({row.grade for row in japan.rows})
    assert len(france.rows) == 20
    assert france.sa_ratio == "50%"
    assert france.cd_ratio == "25%"
    assert france.ai_ratio == "75%"
    assert any(row.source == "AI" and row.grade == "D" for row in france.rows)


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


def test_agent_harness_prefers_configured_real_gold_dataset(monkeypatch, tmp_path):
    image_path = tmp_path / "real-sushi.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note",
                "real-001,日本,real-sushi.png,试新_日本_寿司0615,寿司,food,real,5,0.31,0.93,42,S,寿司,米白与鲑鱼橙,日式料理桌面近景,本土饮食文化,,真实运营样本",
                "real-002,法国,real-sushi.png,试新_法国_乡村石屋0615,乡村石屋,houses,real,4,0.25,0.88,49,A,乡村石屋,暖米白,法式村庄远景,生活艺术,,其他国家样本",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent()

    samples = agent.harness_samples("日本")
    summary = agent.harness_summary("日本")

    assert len(samples) == 1
    assert samples[0].sample_id == "real-001"
    assert samples[0].is_real
    assert summary["真实样本数"] == 1
    assert summary["合成样本数"] == 0
    assert summary["数据集来源"].endswith("gold_samples.csv")


def test_agent_uses_new_mixed_business_workbook_for_japan_and_france_history(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path

    japan = agent._history_records("日本")
    france = agent._history_records("法国")

    assert len(japan) == 25
    assert len(france) == 20
    assert {record.country for record in japan} == {"日本"}
    assert {record.country for record in france} == {"法国"}
    assert all(record.local_image_path and Path(record.local_image_path).exists() for record in (*japan, *france))
    assert "drawing" in {record.js_category for record in japan}
    assert "houses" in {record.js_category for record in france}


def test_agent_harness_uses_all_real_business_workbook_samples_when_no_gold_csv(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path

    japan_samples = agent.harness_samples("日本")
    france_samples = agent.harness_samples("法国")

    assert len(japan_samples) == 25
    assert len(france_samples) == 20
    assert all(sample.is_real for sample in (*japan_samples, *france_samples))
    assert {sample.gold_grade for sample in france_samples} >= {"S", "A", "B", "C", "D"}


def test_agent_harness_baseline_summary_reports_human_gold_failure_replay(monkeypatch, tmp_path):
    image_path = tmp_path / "france-picnic.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                f"fr-real-001,法国,{image_path},试新_法国_海滩野餐0624,海滩野餐,lifestyle,real,7,0.42,0.91,38,A,海滩野餐,暖色,海边沙滩,生活艺术,,人工确认,human_gold,reviewed",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    run = agent.harness_run("法国", save=True)
    summary = agent.harness_baseline_summary("法国")

    assert summary["baseline_status"] == "human_gold_baseline"
    assert summary["run_id"] == run.run_id
    assert summary["真实样本数"] == 1
    assert summary["human_gold 样本数"] == 1
    assert summary["human_gold 覆盖率"] == "100%"
    assert summary["失败 case 数"] == len(run.failures)
    assert summary["失败样本数"] == len({case.sample_id for case in run.failures})
    assert summary["Top 失败分类"]
    assert summary["下一步动作"] in {"可以进入失败样本人工复盘。", "无失败样本，可保存为当前真实 baseline。"}


def test_agent_harness_summary_reports_invalid_gold_dataset_rows(monkeypatch, tmp_path):
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note",
                "real-bad,日本,missing.png,试新_日本_寿司0615,寿司,food,real,5,0.31,0.93,42,S,寿司,米白与鲑鱼橙,日式料理桌面近景,本土饮食文化,,图片缺失",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent()

    samples = agent.harness_samples("日本")
    summary = agent.harness_summary("日本")

    assert samples
    assert summary["导入问题数"] == 1
    assert "real-bad" in summary["导入问题摘要"]


def test_agent_exports_harness_overrides_to_csv(tmp_path):
    agent = PuzzleOpsAgent()
    agent.record_harness_override("日本", "real-001", "value_match_eval", "人工修正：寿司图应匹配本土饮食文化。")
    agent.record_harness_override("日本", "real-002", "audit_eval", "人工修正：补充版权/IP风险。")
    export_path = tmp_path / "harness_overrides.csv"

    written = agent.export_harness_overrides("日本", export_path)

    assert written == export_path
    content = export_path.read_text(encoding="utf-8")
    assert "sample_id,task_type,human_override,country" in content
    assert "real-001,value_match_eval,人工修正：寿司图应匹配本土饮食文化。,日本" in content
    assert "real-002,audit_eval,人工修正：补充版权/IP风险。,日本" in content


def test_agent_updates_harness_gold_label_csv_and_records_fact_memory(monkeypatch, tmp_path):
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

    saved = agent.update_harness_gold_label(
        "日本",
        "real-001",
        gold_grade="S",
        gold_subject="寿司拼盘",
        gold_color_mood="米白与鲑鱼橙，清爽明亮",
        gold_composition="日式料理桌面近景",
        gold_value_labels="本土饮食文化;治愈食物",
        gold_risk_labels="",
        human_note="运营人工确认 gold label",
    )

    assert saved == dataset
    samples = agent.harness_samples("日本")
    assert samples[0].gold_subject == "寿司拼盘"
    assert samples[0].gold_value_labels == ("本土饮食文化", "治愈食物")
    coverage = agent.harness_gold_coverage("日本")
    assert coverage["真实样本数"] == 1
    assert coverage["完整 gold 样本数"] == 1
    assert coverage["gold 完成率"] == "100%"
    facts = agent.memory_debug("日本", query="寿司拼盘")
    assert any(row["layer"] == "facts" and "寿司拼盘" in row["summary"] for row in facts)


def test_agent_updates_harness_business_metrics_from_gold_form(monkeypatch, tmp_path):
    image_path = tmp_path / "france-picnic.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note",
                "fr-real-001,法国,france-picnic.png,试新_法国_海滩野餐0624,海滩野餐,lifestyle,real,0,0,0,0,A,海滩野餐,暖色,海滩场景,生活艺术,,待补指标",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    agent.update_harness_gold_label(
        "法国",
        "fr-real-001",
        gold_grade="A",
        gold_subject="海滩野餐",
        gold_color_mood="暖色",
        gold_composition="海滩场景",
        gold_value_labels="生活艺术",
        gold_risk_labels="",
        human_note="补齐业务指标",
        position="7",
        open_rate="0.42",
        completion_rate="0.91",
        avg_finish_time="38",
    )

    sample = agent.harness_samples("法国")[0]
    assert sample.position == 7
    assert sample.metrics["open_rate"] == 0.42
    assert sample.metrics["completion_rate"] == 0.91
    assert sample.metrics["avg_finish_time"] == 38


def test_agent_rag_documents_include_human_gold_harness_samples(monkeypatch, tmp_path):
    image_path = tmp_path / "france-picnic.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                "fr-real-001,法国,france-picnic.png,试新_法国_海滩野餐0624,海滩野餐,lifestyle,real,7,0.42,0.91,38,A,海滩野餐,暖色,海滩场景,生活艺术,,人工确认,human_gold,reviewed",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    documents = agent._rag_documents("法国")
    gold_docs = [document for document in documents if document.source_type == "harness_gold_sample"]

    assert gold_docs
    assert gold_docs[0].document_id == "FR_HARNESS_GOLD_fr-real-001"
    assert "主体=海滩野餐" in gold_docs[0].text
    assert "等级=A" in gold_docs[0].text
    assert "开图率=0.42" in gold_docs[0].text
    assert "完成率=0.91" in gold_docs[0].text
    assert "价值观标签=生活艺术" in gold_docs[0].text


def test_agent_rag_eval_cases_include_human_gold_harness_samples(monkeypatch, tmp_path):
    image_path = tmp_path / "france-picnic.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                "fr-real-001,法国,france-picnic.png,试新_法国_海滩野餐0624,海滩野餐,lifestyle,real,7,0.42,0.91,38,A,海滩野餐,暖色,海滩场景,生活艺术,,人工确认,human_gold,reviewed",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    cases = agent._rag_retrieval_cases("法国")

    assert any(case.expected_parent_id == "FR_HARNESS_GOLD_fr-real-001" for case in cases)
    assert any("海滩野餐" in case.query and "生活艺术" in case.query for case in cases)


def test_agent_rag_eval_case_evidence_marks_failed_expected_citation(monkeypatch, tmp_path):
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
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    evidence = agent.rag_eval_case_evidence("日本")

    assert evidence["total"] == 1
    assert evidence["failed_count"] == 1
    assert evidence["cases"][0]["status"] == "FAIL"
    assert evidence["cases"][0]["expected_parent_id"] == "JP_KB_MISSING"
    assert "未命中 expected_parent_id" in evidence["cases"][0]["failure_reason"]


def test_agent_records_rag_eval_failure_feedback_as_working_memory(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    memory_id = agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI",
        retrieved_parent_ids=("JP_KB_ONSEN", "GLOBAL_KB_AUDIT"),
        note="需要补充寿司价值观 hard negative",
    )

    rows = agent.memory_debug("日本", query="寿司 hard negative", limit=50)
    feedback = next(row for row in rows if row["memory_id"] == memory_id)
    assert feedback["layer"] == "working"
    assert feedback["memory_type"] == "rag_eval_failure_feedback"
    assert feedback["payload"]["expected_parent_id"] == "JP_KB_SUSHI"
    assert feedback["payload"]["retrieved_parent_ids"] == ["JP_KB_ONSEN", "GLOBAL_KB_AUDIT"]
    assert feedback["payload"]["note"] == "需要补充寿司价值观 hard negative"


def test_agent_records_value_match_human_correction_into_memory_and_rag_feedback(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    row = agent.create_trial_demand("日本", "人物", mode="parse").edited(
        operation_tag="试新_日本_寿司0616",
        subject="寿司",
        subject_description="主体内容：寿司；色彩氛围：米白与鲑鱼橙；构图环境：日式料理店铺餐桌近景。",
        value_match="LLM判断：部分符合；系统RAG召回：JP_SUSHI#chunk-1；生成式RAG依据：寿司属于日本本土饮食文化。",
    )

    result = agent.record_value_match_human_correction(
        row,
        human_correction="人工修正：符合日本本土饮食文化，但需规避品牌露出。",
        satisfaction_score=5,
    )

    assert result["working_memory_id"] > 0
    assert result["fact_memory_id"] > 0
    assert result["rag_feedback_memory_id"] > 0
    rows = agent.memory_debug("日本", query="品牌露出 本土饮食文化", limit=50)
    working = next(item for item in rows if item["memory_id"] == result["working_memory_id"])
    fact = next(item for item in rows if item["memory_id"] == result["fact_memory_id"])
    rag_feedback = next(item for item in rows if item["memory_id"] == result["rag_feedback_memory_id"])
    assert working["layer"] == "working"
    assert working["memory_type"] == "value_match_human_correction"
    assert working["payload"]["satisfaction_score"] == 5
    assert working["payload"]["citation_ids"] == ["JP_SUSHI#chunk-1"]
    assert fact["layer"] == "facts"
    assert fact["memory_type"] == "verified_value_match_fact"
    assert fact["payload"]["subject"] == "寿司"
    assert fact["payload"]["human_correction"] == "人工修正：符合日本本土饮食文化，但需规避品牌露出。"
    assert rag_feedback["memory_type"] == "rag_eval_failure_feedback"
    assert rag_feedback["payload"]["expected_parent_id"] == "JP_SUSHI"
    assert rag_feedback["payload"]["label_source"] == "human_value_match_correction"


def test_agent_summarizes_and_exports_rag_eval_failure_feedback(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI",
        retrieved_parent_ids=("JP_KB_ONSEN",),
        note="补充寿司 hard negative",
    )

    summary = agent.rag_eval_failure_feedback_summary("日本")
    export_path = agent.export_rag_eval_failure_feedback("日本", tmp_path / "rag_failures.jsonl")

    assert summary["pending_count"] == 1
    assert summary["items"][0]["expected_parent_id"] == "JP_KB_SUSHI"
    assert summary["items"][0]["optimization_use"] == "hard_negative_or_knowledge_patch"
    content = export_path.read_text(encoding="utf-8")
    assert '"expected_parent_id": "JP_KB_SUSHI"' in content
    assert '"optimization_use": "hard_negative_or_knowledge_patch"' in content


def test_agent_builds_and_exports_rag_knowledge_patch_drafts(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )

    drafts = agent.rag_knowledge_patch_drafts("日本")
    export_path = agent.export_rag_knowledge_patch_drafts("日本", tmp_path / "patches.jsonl")

    assert drafts["draft_count"] == 1
    assert drafts["items"][0]["expected_parent_id"] == "JP_KB_SUSHI_FOOD"
    assert drafts["items"][0]["source_type"] == "value_rule_patch"
    assert "日本寿司图是否符合本土饮食价值观" in drafts["items"][0]["draft_text"]
    assert drafts["items"][0]["review_status"] == "needs_human_review"
    content = export_path.read_text(encoding="utf-8")
    assert '"source_type": "value_rule_patch"' in content
    assert '"review_status": "needs_human_review"' in content


def test_agent_prioritizes_rag_knowledge_patch_drafts_by_business_impact(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    low_id = agent.record_rag_eval_failure_feedback(
        "法国",
        query="法国普通花园图是否符合价值观",
        expected_parent_id="FR_KB_GARDEN",
        retrieved_parent_ids=("FR_KB_LAVENDER",),
        note="普通失败",
        diagnosis="rerank_filtered_expected",
        gold_grade="C",
        label_source="ai_silver",
    )
    high_id = agent.record_rag_eval_failure_feedback(
        "法国",
        query="法国S级海边野餐生活艺术",
        expected_parent_id="FR_KB_PICNIC",
        retrieved_parent_ids=("FR_KB_BREAD",),
        note="真实S级样本未召回",
        diagnosis="knowledge_missing_or_query_mismatch",
        gold_grade="S",
        label_source="human_gold",
    )

    drafts = agent.rag_knowledge_patch_drafts("法国")

    assert drafts["items"][0]["source_memory_id"] == high_id
    assert drafts["items"][1]["source_memory_id"] == low_id
    assert drafts["items"][0]["priority_score"] > drafts["items"][1]["priority_score"]
    assert drafts["items"][0]["priority_band"] == "P0"
    assert "human_gold" in drafts["items"][0]["priority_reason"]
    assert "knowledge_missing_or_query_mismatch" in drafts["items"][0]["priority_reason"]


def test_agent_approves_rag_knowledge_patch_draft_into_long_term_memory(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )

    target_id = agent.approve_rag_knowledge_patch_draft(
        "日本",
        "patch-日本-1",
        human_note="运营确认补入日本饮食价值观",
    )

    debug = agent.memory_debug("日本", query="寿司 饮食 价值观", limit=50)
    target = next(row for row in debug if row["memory_id"] == target_id)
    assert target["layer"] == "long_term"
    assert target["memory_type"] == "approved_rag_knowledge_patch"
    assert target["human_verified"] is True
    assert "日本寿司图是否符合本土饮食价值观" in target["summary"]
    assert any(document.metadata["human_verified"] for document in agent._layered_memory_rag_documents("日本") if document.metadata["memory_id"] == target_id)


def test_agent_exports_approved_rag_patch_memory_as_raw_markdown_patch(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )
    agent.approve_rag_knowledge_patch_draft("日本", "patch-日本-1", human_note="运营确认补入日本饮食价值观")

    output_path = agent.export_approved_rag_patch_markdown("日本", tmp_path / "approved_patch.md")

    content = output_path.read_text(encoding="utf-8")
    assert "source_type: approved_rag_patch" in content
    assert "review_status: approved" in content
    assert "## RAG补丁：JP_KB_SUSHI_FOOD" in content
    assert "日本寿司图是否符合本土饮食价值观" in content
    assert "人工审核备注" in content


def test_agent_applies_approved_rag_patch_markdown_to_raw_with_manifest(monkeypatch, tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    monkeypatch.setenv("PUZZLEOPS_RAG_KNOWLEDGE_DIR", str(knowledge_dir))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )
    agent.approve_rag_knowledge_patch_draft("日本", "patch-日本-1", human_note="运营确认补入日本饮食价值观")

    result = agent.apply_approved_rag_patch_markdown_to_raw("日本")

    raw_patch_path = Path(str(result["raw_patch_path"]))
    manifest_path = Path(str(result["manifest_path"]))
    latest_manifest_path = Path(str(result["latest_manifest_path"]))
    assert raw_patch_path.exists()
    assert raw_patch_path.parent == knowledge_dir / "raw"
    assert "source_type: approved_rag_patch" in raw_patch_path.read_text(encoding="utf-8")
    assert manifest_path.exists()
    assert latest_manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "applied"
    assert manifest["country"] == "日本"
    assert manifest["applied_patch_count"] == 1
    assert manifest["raw_patch_path"] == str(raw_patch_path)
    assert manifest["patch_ids"] == ["patch-日本-1"]


def test_agent_applies_approved_rag_patch_and_rebuilds_processed_with_eval(monkeypatch, tmp_path):
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
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )
    agent.approve_rag_knowledge_patch_draft("日本", "patch-日本-1", human_note="运营确认补入日本饮食价值观")

    result = agent.apply_approved_rag_patch_and_rebuild("日本")

    processed_path = Path(str(result["processed_path"]))
    manifest_path = Path(str(result["manifest_path"]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    processed = processed_path.read_text(encoding="utf-8")
    assert result["status"] == "applied_rebuilt"
    assert result["document_count"] >= 1
    assert result["hit@5"] == 1.0
    assert '"document_id": "JP_KB_SUSHI_FOOD"' in processed
    assert manifest["status"] == "applied_rebuilt"
    assert manifest["rebuild"]["processed_path"] == str(processed_path)
    assert manifest["rebuild"]["hit@5"] == 1.0
    assert manifest["rebuild"]["cases"][0]["expected_parent_id"] == "JP_KB_SUSHI_FOOD"
    assert manifest["rebuild"]["cases"][0]["hit"] is True
    assert manifest["rebuild"]["failed_count"] == 0


def test_agent_rolls_back_latest_approved_rag_patch_and_rebuilds(monkeypatch, tmp_path):
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
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )
    agent.approve_rag_knowledge_patch_draft("日本", "patch-日本-1", human_note="运营确认补入日本饮食价值观")
    applied = agent.apply_approved_rag_patch_and_rebuild("日本")

    result = agent.rollback_latest_approved_rag_patch_and_rebuild("日本")

    raw_patch_path = Path(str(applied["raw_patch_path"]))
    processed_path = Path(str(result["processed_path"]))
    latest_manifest = json.loads(Path(str(result["latest_manifest_path"])).read_text(encoding="utf-8"))
    assert result["status"] == "rolled_back_rebuilt"
    assert result["removed_raw_patch_path"] == str(raw_patch_path)
    assert not raw_patch_path.exists()
    assert '"document_id": "JP_KB_SUSHI_FOOD"' not in processed_path.read_text(encoding="utf-8")
    assert result["hit@5"] == 0.0
    assert latest_manifest["status"] == "rolled_back_rebuilt"
    assert latest_manifest["rollback"]["removed_raw_patch_path"] == str(raw_patch_path)


def test_agent_applies_rag_patch_rebuilds_and_reindexes_qdrant_with_manifest(monkeypatch, tmp_path):
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
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )
    agent.approve_rag_knowledge_patch_draft("日本", "patch-日本-1", human_note="运营确认补入日本饮食价值观")

    class FakeEmbedding:
        provider_name = "dashscope:text-embedding-v4"

        def query_vector(self, text: str):
            assert text
            return (0.1, 0.2, 0.3)

    class FakeQdrantStore:
        def __init__(self):
            self.points = ()

        def ensure_collection(self, vector_size):
            return {"status": "created", "vector_size": vector_size, "collection": "puzzle_ops_rag"}

        def upsert(self, points):
            self.points = points
            return {"status": "ok"}

    store = FakeQdrantStore()

    result = agent.apply_approved_rag_patch_rebuild_and_reindex_qdrant(
        "日本",
        embedding_provider=FakeEmbedding(),
        vector_store=store,
    )

    manifest = json.loads(Path(str(result["manifest_path"])).read_text(encoding="utf-8"))
    assert result["status"] == "applied_rebuilt_qdrant_indexed"
    assert result["qdrant"]["status"] == "indexed"
    assert result["qdrant"]["upserted_points"] == len(store.points)
    assert result["qdrant"]["vector_size"] == 3
    assert result["qdrant"]["hit@5"] == 1.0
    assert manifest["status"] == "applied_rebuilt_qdrant_indexed"
    assert manifest["qdrant"]["manifest_path"].endswith(".json")
    assert manifest["qdrant"]["upserted_points"] == len(store.points)


def test_agent_rag_patch_ops_summary_reads_latest_patch_manifest(monkeypatch, tmp_path):
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
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )
    agent.approve_rag_knowledge_patch_draft("日本", "patch-日本-1", human_note="运营确认补入日本饮食价值观")

    class FakeEmbedding:
        provider_name = "dashscope:text-embedding-v4"

        def query_vector(self, text: str):
            return (0.1, 0.2, 0.3)

    class FakeQdrantStore:
        def __init__(self):
            self.points = ()

        def ensure_collection(self, vector_size):
            return {"status": "created", "vector_size": vector_size}

        def upsert(self, points):
            self.points = points
            return {"status": "ok"}

    store = FakeQdrantStore()
    agent.apply_approved_rag_patch_rebuild_and_reindex_qdrant("日本", embedding_provider=FakeEmbedding(), vector_store=store)

    summary = agent.rag_patch_ops_summary("日本")

    assert summary["status"] == "applied_rebuilt_qdrant_indexed"
    assert summary["patch_count"] == 1
    assert summary["raw_patch_file"].startswith("approved_rag_patch_日本_")
    assert summary["rebuild_hit@5"] == 1.0
    assert summary["qdrant_status"] == "indexed"
    assert summary["qdrant_points"] == len(store.points)
    assert summary["qdrant_vector_size"] == 3


def test_agent_rag_patch_ops_summary_includes_recent_runs(monkeypatch, tmp_path):
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
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本寿司图是否符合本土饮食价值观",
        expected_parent_id="JP_KB_SUSHI_FOOD",
        retrieved_parent_ids=("JP_KB_ONSEN_TRAVEL",),
        note="补充寿司 hard negative",
    )
    agent.approve_rag_knowledge_patch_draft("日本", "patch-日本-1", human_note="运营确认补入日本饮食价值观")
    first = agent.apply_approved_rag_patch_and_rebuild("日本")
    agent.rollback_latest_approved_rag_patch_and_rebuild("日本")

    summary = agent.rag_patch_ops_summary("日本")

    runs = summary["recent_runs"]
    assert len(runs) >= 1
    assert runs[0]["run_id"] == first["run_id"]
    assert runs[0]["status"] == "rolled_back_rebuilt"
    assert runs[0]["patch_count"] == 1
    assert runs[0]["rebuild_hit@5"] == 0.0
    assert runs[0]["rollback_removed"].endswith(".md")
    assert "raw_patch_path" in runs[0]["evidence"]
    assert "processed_path" in runs[0]["evidence"]
    assert runs[0]["evidence"]["patch_ids"] == ("patch-日本-1",)


def test_agent_rag_patch_ops_summary_compares_latest_two_runs(monkeypatch, tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    runs_dir = knowledge_dir / "patch_manifests" / "runs"
    runs_dir.mkdir(parents=True)
    monkeypatch.setenv("PUZZLEOPS_RAG_KNOWLEDGE_DIR", str(knowledge_dir))
    older = {
        "run_id": "run-old",
        "created_at": "2026-07-04",
        "country": "日本",
        "status": "applied_rebuilt",
        "raw_patch_path": "/tmp/old.md",
        "applied_patch_count": 1,
        "patch_ids": ["patch-old"],
        "rebuild": {
            "hit@5": 0.4,
            "mrr@5": 0.2,
            "processed_path": "/tmp/old.jsonl",
            "cases": [
                {"expected_parent_id": "JP_KB_SUSHI", "hit": False},
                {"expected_parent_id": "JP_KB_ONSEN", "hit": True},
            ],
        },
        "qdrant": {"status": "indexed", "upserted_points": 5, "vector_size": 3},
    }
    newer = {
        "run_id": "run-new",
        "created_at": "2026-07-04",
        "country": "日本",
        "status": "applied_rebuilt_qdrant_indexed",
        "raw_patch_path": "/tmp/new.md",
        "applied_patch_count": 2,
        "patch_ids": ["patch-new"],
        "rebuild": {
            "hit@5": 0.9,
            "mrr@5": 0.7,
            "processed_path": "/tmp/new.jsonl",
            "cases": [
                {"expected_parent_id": "JP_KB_SUSHI", "hit": True},
                {"expected_parent_id": "JP_KB_ONSEN", "hit": True},
                {"expected_parent_id": "JP_KB_MOUNT_FUJI", "hit": False},
            ],
        },
        "qdrant": {"status": "indexed", "upserted_points": 8, "vector_size": 3},
    }
    (runs_dir / "rag_patch_apply_日本_20260704-old.json").write_text(json.dumps(older, ensure_ascii=False), encoding="utf-8")
    (runs_dir / "rag_patch_apply_日本_20260704-new.json").write_text(json.dumps(newer, ensure_ascii=False), encoding="utf-8")
    (knowledge_dir / "patch_manifests" / "rag_patch_apply_日本.json").write_text(json.dumps(newer, ensure_ascii=False), encoding="utf-8")
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本S级寿司饮食文化",
        expected_parent_id="JP_KB_SUSHI",
        retrieved_parent_ids=("JP_KB_ONSEN",),
        diagnosis="knowledge_missing_or_query_mismatch",
        gold_grade="S",
        label_source="human_gold",
    )

    summary = agent.rag_patch_ops_summary("日本")
    comparison = summary["run_comparison"]
    impact = summary["priority_impact"]

    assert comparison["current_run_id"] == "run-new"
    assert comparison["previous_run_id"] == "run-old"
    assert comparison["hit@5_delta"] == 0.5
    assert comparison["mrr@5_delta"] == 0.5
    assert comparison["qdrant_points_delta"] == 3
    assert comparison["status_changed"] is True
    assert comparison["fixed_failure_count"] == 1
    assert comparison["new_failure_count"] == 1
    assert "JP_KB_SUSHI" in comparison["fixed_failures"]
    assert "JP_KB_MOUNT_FUJI" in comparison["new_failures"]
    assert impact["pending_P0"] == 1
    assert impact["effect"] == "improved"
    assert impact["recommended_action"] == "continue_apply_priority_patches"


def test_agent_rag_live_model_ops_summary_reads_latest_acceptance(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path / "runtime"
    report_dir = agent._runtime_dir / "rag_acceptance_reports"
    report_dir.mkdir(parents=True)
    (report_dir / "rag_acceptance_full_summary_日本.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "failure_stage": "rerank_preflight",
                "preflight": {
                    "mode": "live",
                    "embedding": {"ready": True, "provider": "dashscope:text-embedding-v4"},
                    "qdrant": {"ready": True, "provider": "qdrant"},
                    "rerank": {"ready": False, "provider": "bge:BAAI/bge-reranker-v2-m3"},
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
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = agent.rag_live_model_ops_summary("日本")

    assert summary["mode"] == "live"
    assert summary["embedding_ready"] is True
    assert summary["qdrant_ready"] is True
    assert summary["rerank_ready"] is False
    assert summary["embedding_remote_calls"] == 3
    assert summary["rerank_remote_calls"] == 1
    assert summary["qdrant_vector_hits"] is True


def test_agent_exports_rag_ops_report_json_and_markdown(monkeypatch, tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path / "runtime"
    knowledge_dir = tmp_path / "rag_knowledge"
    monkeypatch.setenv("PUZZLEOPS_RAG_KNOWLEDGE_DIR", str(knowledge_dir))
    manifest_dir = knowledge_dir / "patch_manifests"
    runs_dir = manifest_dir / "runs"
    runs_dir.mkdir(parents=True)
    old_manifest = {
        "run_id": "run-old",
        "status": "completed",
        "patch_count": 1,
        "rebuild": {
            "hit@5": 0.4,
            "mrr@5": 0.2,
            "qdrant": {"status": "ok", "upserted_points": 5},
            "cases": (
                {"expected_parent_id": "JP_KB_SUSHI", "hit": False},
                {"expected_parent_id": "JP_KB_ONSEN", "hit": True},
            ),
        },
    }
    new_manifest = {
        "run_id": "run-new",
        "status": "completed",
        "patch_count": 2,
        "rebuild": {
            "hit@5": 0.9,
            "mrr@5": 0.7,
            "qdrant": {"status": "ok", "upserted_points": 8},
            "cases": (
                {"expected_parent_id": "JP_KB_SUSHI", "hit": True},
                {"expected_parent_id": "JP_KB_ONSEN", "hit": True},
                {"expected_parent_id": "JP_KB_MOUNT_FUJI", "hit": False},
            ),
        },
    }
    (manifest_dir / "rag_patch_apply_日本.json").write_text(json.dumps(new_manifest, ensure_ascii=False), encoding="utf-8")
    (runs_dir / "rag_patch_apply_日本_old.json").write_text(json.dumps(old_manifest, ensure_ascii=False), encoding="utf-8")
    (runs_dir / "rag_patch_apply_日本_new.json").write_text(json.dumps(new_manifest, ensure_ascii=False), encoding="utf-8")
    report_dir = agent._runtime_dir / "rag_acceptance_reports"
    report_dir.mkdir(parents=True)
    (report_dir / "rag_acceptance_full_summary_日本.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "preflight": {
                    "mode": "live",
                    "embedding": {"ready": True, "provider": "dashscope:text-embedding-v4"},
                    "qdrant": {"ready": True, "provider": "qdrant"},
                    "rerank": {"ready": True, "provider": "bge:BAAI/bge-reranker-v2-m3"},
                },
                "report": {
                    "hit@5": 1.0,
                    "mrr@5": 0.9,
                    "observed_retrieval": {"qdrant_vector_hits": True},
                    "runtime_stats": {"embedding_remote_calls": 2, "rerank_remote_calls": 1},
                    "quality_eval": {
                        "answer_accuracy": {"bleu1": 0.82, "rouge_l": 0.76},
                        "trustworthiness": {"support_overlap": 0.88, "document_coverage": 1.0},
                        "latency": {"average_ms": 220.0, "p95_ms": 420.0, "p99_ms": 800.0},
                        "scalability": {"qps": 4.5, "corpus_document_count": 120},
                        "user_experience": {"average_satisfaction": 4.2, "satisfaction_rate": 0.8, "readability_score": 0.91},
                    },
                    "live_model_evidence": {
                        "overall": {"verified": True, "status": "verified", "blocking_reasons": []},
                        "embedding": {
                            "provider": "dashscope",
                            "model": "text-embedding-v4",
                            "model_family": "Qwen3-Embedding",
                            "observed_remote_calls": 2,
                            "fallbacks": 0,
                            "verified_remote_call": True,
                            "fallback_free": True,
                        },
                        "rerank": {
                            "provider": "bge",
                            "model": "BAAI/bge-reranker-v2-m3",
                            "provider_family": "BGE-Reranker-v2",
                            "observed_remote_calls": 1,
                            "fallbacks": 0,
                            "verified_remote_call": True,
                            "fallback_free": True,
                        },
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    agent.record_rag_eval_failure_feedback(
        "日本",
        query="日本S级寿司饮食文化",
        expected_parent_id="JP_KB_SUSHI",
        retrieved_parent_ids=("JP_KB_ONSEN",),
        note="真实高价值样本未召回",
        diagnosis="knowledge_missing_or_query_mismatch",
        gold_grade="S",
        label_source="human_gold",
    )

    result = agent.export_rag_ops_report("日本", tmp_path / "rag_ops")

    json_path = Path(str(result["json_path"]))
    markdown_path = Path(str(result["markdown_path"]))
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert payload["country"] == "日本"
    assert payload["live_model_ops"]["mode"] == "live"
    assert payload["live_model_ops"]["embedding_remote_calls"] == 2
    assert payload["patch_priority_summary"]["P0"] == 1
    assert payload["patch_priority_summary"]["top_patch"]["priority_band"] == "P0"
    assert payload["patch_case_diff"]["fixed_failure_count"] == 1
    assert payload["patch_case_diff"]["new_failure_count"] == 1
    assert "JP_KB_SUSHI" in payload["patch_case_diff"]["fixed_failures"]
    assert "JP_KB_MOUNT_FUJI" in payload["patch_case_diff"]["new_failures"]
    assert payload["live_model_evidence"]["overall"]["verified"] is True
    assert payload["live_model_evidence"]["embedding"]["model_family"] == "Qwen3-Embedding"
    assert payload["live_model_evidence"]["rerank"]["provider_family"] == "BGE-Reranker-v2"
    assert payload["quality_eval"]["answer_accuracy"]["bleu1"] == 0.82
    assert payload["quality_eval"]["trustworthiness"]["document_coverage"] == 1.0
    assert payload["quality_eval"]["latency"]["p95_ms"] == 420.0
    assert "RAG Ops Report" in markdown
    assert "RAG Live Model Ops" in markdown
    assert "RAG Live Model Evidence" in markdown
    assert "RAG Quality Eval" in markdown
    assert "bleu1=0.82" in markdown
    assert "p95_ms=420.0" in markdown
    assert "Qwen3-Embedding" in markdown
    assert "BGE-Reranker-v2" in markdown
    assert "RAG Patch Ops" in markdown
    assert "RAG Patch Priority" in markdown
    assert "RAG Patch Case Diff" in markdown
    assert "JP_KB_SUSHI" in markdown
    assert "JP_KB_MOUNT_FUJI" in markdown
    assert "hit@5" in markdown


def test_agent_summarizes_rag_quality_eval_from_recent_traces(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_trace_quality.db"))
    agent._runtime_dir = tmp_path
    trace_dir = tmp_path / "rag_traces" / "日本"
    trace_dir.mkdir(parents=True)
    first = {
        "created_at": "20260707_100000_000000",
        "country": "日本",
        "answer": "寿司属于日本本土饮食文化，适合清爽餐桌近景。",
        "reference_answer": "寿司属于日本本土饮食文化，适合清爽餐桌近景。",
        "support_documents": ("寿司属于日本本土饮食文化，适合清爽餐桌近景。",),
        "required_facts": ("日本本土饮食文化", "清爽餐桌近景"),
        "latency_ms": 100.0,
        "satisfaction_score": 5,
        "citations": ("JP_SUSHI#chunk-1",),
    }
    second = {
        "created_at": "20260707_100001_000000",
        "country": "日本",
        "answer": "温泉旅馆庭院符合治愈感和本土旅行体验。",
        "reference_answer": "温泉旅馆庭院符合治愈感和本土旅行体验。",
        "support_documents": ("温泉旅馆庭院符合治愈感和本土旅行体验。",),
        "required_facts": ("治愈感", "本土旅行体验"),
        "latency_ms": 400.0,
        "satisfaction_score": 3,
        "citations": ("JP_ONSEN#chunk-1",),
    }
    (trace_dir / "rag_trace_20260707_100000_000000_a.json").write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
    (trace_dir / "rag_trace_20260707_100001_000000_b.json").write_text(json.dumps(second, ensure_ascii=False), encoding="utf-8")

    summary = agent.rag_trace_quality_eval_summary("日本", limit=10)

    assert summary["source"] == "rag_traces"
    assert summary["trace_count"] == 2
    assert summary["answer_accuracy"]["bleu1"] == 1.0
    assert summary["trustworthiness"]["document_coverage"] == 1.0
    assert summary["latency"]["average_ms"] == 250.0
    assert summary["latency"]["p95_ms"] == 400.0
    assert summary["scalability"]["total_queries"] == 2
    assert summary["scalability"]["qps"] == 4.0
    assert summary["user_experience"]["average_satisfaction"] == 4.0
    assert summary["user_experience"]["satisfaction_rate"] == 0.5


def test_export_rag_ops_report_falls_back_to_trace_quality_eval(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_ops_trace_quality.db"))
    agent._runtime_dir = tmp_path
    trace_dir = tmp_path / "rag_traces" / "日本"
    trace_dir.mkdir(parents=True)
    trace = {
        "created_at": "20260707_100000_000000",
        "country": "日本",
        "answer": "法国薰衣草风车图符合田园自然和地域地标价值观。",
        "reference_answer": "法国薰衣草风车图符合田园自然和地域地标价值观。",
        "support_documents": ("法国薰衣草风车图符合田园自然和地域地标价值观。",),
        "required_facts": ("田园自然", "地域地标"),
        "latency_ms": 240.0,
        "satisfaction_score": 4,
        "citations": ("FR_LAVENDER#chunk-1",),
    }
    (trace_dir / "rag_trace_20260707_100000_000000_a.json").write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")

    result = agent.export_rag_ops_report("日本", tmp_path / "rag_ops")

    payload = json.loads(Path(str(result["json_path"])).read_text(encoding="utf-8"))
    markdown = Path(str(result["markdown_path"])).read_text(encoding="utf-8")
    assert payload["quality_eval"]["source"] == "rag_traces"
    assert payload["quality_eval"]["trace_count"] == 1
    assert payload["quality_eval"]["latency"]["average_ms"] == 240.0
    assert "source=rag_traces" in markdown
    assert "trace_count=1" in markdown


def test_agent_rag_answer_can_cite_human_gold_harness_sample(monkeypatch, tmp_path):
    image_path = tmp_path / "france-picnic.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                "fr-real-001,法国,france-picnic.png,试新_法国_海滩野餐0624,海滩野餐,lifestyle,real,7,0.42,0.91,38,A,海滩野餐,暖色,海滩场景,生活艺术,,人工确认,human_gold,reviewed",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))

    answer = agent.value_audit_rag_answer("法国", "海滩野餐 生活艺术 开图率 0.42", top_k=3)

    assert "FR_HARNESS_GOLD_fr-real-001#chunk-1" in answer.citations


def test_agent_creates_gold_dataset_skeleton_from_default_real_samples(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path
    image_path = tmp_path / "real-tower.png"
    image_path.write_bytes(b"fake-png")

    class Record:
        image_id = "history-001"
        country = "日本"
        local_image_path = str(image_path)
        operation_tag = "试新_日本_游客塔楼0615"
        subject_tag = "游客塔楼"
        js_category = "travel"
        position = 3
        open_rate = 0.3
        completion_rate = 0.9
        avg_finish_time = 44
        grade = "A"

    agent._history_cache["日本"] = (Record(),)

    path = agent.ensure_harness_gold_dataset("日本")

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "history-001,日本" in content
    assert "游客塔楼" in content
    coverage = agent.harness_gold_coverage("日本")
    assert coverage["真实样本数"] == 1
    assert coverage["完整 gold 样本数"] == 0
    assert "gold_subject" in coverage["缺失字段摘要"]


def test_agent_registers_real_samples_with_manual_grade_only(tmp_path):
    image_path = tmp_path / "france-picnic.png"
    Image.new("RGB", (80, 60), (220, 180, 120)).save(image_path)
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path

    dataset = agent.register_harness_real_samples(
        "法国",
        [
            {
                "sample_id": "fr-real-001",
                "local_image_path": str(image_path),
                "gold_grade": "A",
                "subject": "待AI预标注",
                "js_category": "lifestyle",
                "operation_tag": "试新_法国_真实样本0623",
            }
        ],
    )

    content = dataset.read_text(encoding="utf-8")
    assert "fr-real-001,法国" in content
    assert ",A,,," in content
    sample = agent.harness_samples("法国")[0]
    assert sample.gold_grade == "A"
    assert sample.gold_subject == ""
    assert sample.label_source == "manual_grade"
    assert sample.label_status == "needs_ai_prelabeled"


def test_agent_registers_real_samples_dedupes_by_image_path(tmp_path):
    image_path = tmp_path / "france-picnic.png"
    Image.new("RGB", (80, 60), (220, 180, 120)).save(image_path)
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path

    agent.register_harness_real_samples(
        "法国",
        [{"sample_id": "fr-real-existing", "local_image_path": str(image_path), "gold_grade": "A"}],
    )
    agent.register_harness_real_samples(
        "法国",
        [{"sample_id": "fr-real-new", "local_image_path": str(image_path), "gold_grade": "S"}],
    )

    samples = agent.harness_samples("法国")
    assert len(samples) == 1
    assert samples[0].sample_id == "fr-real-existing"
    assert samples[0].gold_grade == "S"


def test_agent_registers_real_samples_from_pasted_lines(tmp_path):
    picnic = tmp_path / "france picnic.png"
    lavender = tmp_path / "france-lavender.png"
    Image.new("RGB", (80, 60), (220, 180, 120)).save(picnic)
    Image.new("RGB", (80, 60), (120, 90, 200)).save(lavender)
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path

    result = agent.register_harness_real_samples_from_text(
        "法国",
        f"A {picnic}\n{lavender},S,landscape",
    )

    assert result["registered_count"] == 2
    assert result["dataset"].endswith("harness_gold_samples_法国.csv")
    samples = agent.harness_samples("法国")
    assert [sample.gold_grade for sample in samples] == ["A", "S"]
    assert [sample.js_category for sample in samples] == ["real_sample", "landscape"]
    assert all(sample.label_status == "needs_ai_prelabeled" for sample in samples)


def test_agent_registers_real_samples_from_text_with_business_metrics(tmp_path):
    image_path = tmp_path / "france-picnic.png"
    Image.new("RGB", (80, 60), (220, 180, 120)).save(image_path)
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path

    agent.register_harness_real_samples_from_text(
        "法国",
        f"{image_path},A,lifestyle,7,0.42,0.91,38,试新_法国_海滩野餐0624,海滩野餐",
    )

    sample = agent.harness_samples("法国")[0]
    assert sample.gold_grade == "A"
    assert sample.js_category == "lifestyle"
    assert sample.position == 7
    assert sample.metrics["open_rate"] == 0.42
    assert sample.metrics["completion_rate"] == 0.91
    assert sample.metrics["avg_finish_time"] == 38
    assert sample.operation_tag == "试新_法国_海滩野餐0624"
    assert sample.subject == "海滩野餐"


def test_agent_registers_real_samples_from_directory_with_index_grades(tmp_path):
    image_dir = tmp_path / "real_puzzles"
    image_dir.mkdir()
    for name in ("01-picnic.png", "02-garden.png", "03-lavender.jpg"):
        Image.new("RGB", (80, 60), (220, 180, 120)).save(image_dir / name)
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path

    result = agent.register_harness_real_samples_from_directory("法国", image_dir, "1A 2S 3C", js_category="lifestyle")

    samples = agent.harness_samples("法国")
    assert result["registered_count"] == 3
    assert result["image_count"] == 3
    assert [Path(sample.local_image_path).name for sample in samples] == ["01-picnic.png", "02-garden.png", "03-lavender.jpg"]
    assert [sample.gold_grade for sample in samples] == ["A", "S", "C"]
    assert all(sample.js_category == "lifestyle" for sample in samples)
    assert all(sample.label_status == "needs_ai_prelabeled" for sample in samples)


def test_agent_registers_real_samples_from_directory_with_filename_grades(tmp_path):
    image_dir = tmp_path / "real_puzzles"
    image_dir.mkdir()
    for name in ("截屏2026-06-23 22.18.33.png", "截屏2026-06-23 22.12.09.png", "未标注旧图.png"):
        Image.new("RGB", (80, 60), (220, 180, 120)).save(image_dir / name)
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path

    result = agent.register_harness_real_samples_from_directory(
        "法国",
        image_dir,
        "截屏2026-06-23 22.18.33.png=A\n截屏2026-06-23 22.12.09.png=S",
    )

    by_name = {Path(sample.local_image_path).name: sample.gold_grade for sample in agent.harness_samples("法国")}
    assert result["registered_count"] == 2
    assert result["image_count"] == 3
    assert by_name["截屏2026-06-23 22.18.33.png"] == "A"
    assert by_name["截屏2026-06-23 22.12.09.png"] == "S"
    assert "未标注旧图.png" not in by_name


def test_agent_harness_gold_coverage_reports_business_metric_coverage(tmp_path):
    picnic = tmp_path / "france-picnic.png"
    lace = tmp_path / "france-lace.png"
    Image.new("RGB", (80, 60), (220, 180, 120)).save(picnic)
    Image.new("RGB", (80, 60), (240, 230, 210)).save(lace)
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path

    agent.register_harness_real_samples_from_text(
        "法国",
        f"{picnic},A,lifestyle,7,0.42,0.91,38,试新_法国_海滩野餐0624,海滩野餐\nC {lace}",
    )

    coverage = agent.harness_gold_coverage("法国")
    assert coverage["完整业务指标样本数"] == 1
    assert coverage["业务指标完成率"] == "50%"
    assert "open_rate:1" in coverage["缺失业务指标摘要"]
    assert "completion_rate:1" in coverage["缺失业务指标摘要"]
    assert "avg_finish_time:1" in coverage["缺失业务指标摘要"]


def test_agent_harness_readiness_guides_next_steps_for_silver_and_missing_metrics(tmp_path):
    picnic = tmp_path / "france-picnic.png"
    lace = tmp_path / "france-lace.png"
    Image.new("RGB", (80, 60), (220, 180, 120)).save(picnic)
    Image.new("RGB", (80, 60), (240, 230, 210)).save(lace)
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path
    dataset = agent.register_harness_real_samples(
        "法国",
        [
            {
                "sample_id": "fr-real-001",
                "local_image_path": str(picnic),
                "gold_grade": "A",
                "js_category": "lifestyle",
                "position": 7,
                "open_rate": 0.42,
                "completion_rate": 0.91,
                "avg_finish_time": 38,
            },
            {
                "sample_id": "fr-real-002",
                "local_image_path": str(lace),
                "gold_grade": "C",
                "js_category": "object",
            },
        ],
    )
    rows = agent._read_harness_gold_rows(dataset)
    rows[0].update(
        {
            "gold_subject": "海滩野餐",
            "gold_color_mood": "暖金色夕阳",
            "gold_composition": "海边沙滩静物近景",
            "gold_value_labels": "生活艺术",
            "label_source": "ai_silver",
            "label_status": "pending_review",
        }
    )
    agent._write_harness_gold_rows(dataset, rows)

    readiness = agent.harness_readiness("法国")

    assert readiness["ready_for_real_eval"] is False
    assert readiness["真实样本数"] == 2
    assert readiness["待人工审核 silver"] == 1
    assert readiness["human_gold 样本数"] == 0
    assert readiness["RAG human_gold 文档数"] == 0
    assert readiness["Facts memory gold 数"] == 0
    assert any("抽查并确认 AI silver" in action for action in readiness["next_actions"])
    assert any("补齐业务指标" in action for action in readiness["next_actions"])


def test_agent_harness_readiness_turns_ready_after_human_gold_and_fact_rag_deposit(tmp_path):
    picnic = tmp_path / "france-picnic.png"
    Image.new("RGB", (80, 60), (220, 180, 120)).save(picnic)
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path
    agent.register_harness_real_samples(
        "法国",
        [
            {
                "sample_id": "fr-real-001",
                "local_image_path": str(picnic),
                "gold_grade": "A",
                "js_category": "lifestyle",
                "position": 7,
                "open_rate": 0.42,
                "completion_rate": 0.91,
                "avg_finish_time": 38,
            }
        ],
    )

    agent.update_harness_gold_label(
        "法国",
        "fr-real-001",
        gold_grade="A",
        gold_subject="海滩野餐",
        gold_color_mood="暖金色夕阳",
        gold_composition="海边沙滩静物近景",
        gold_value_labels="生活艺术",
        gold_risk_labels="",
        human_note="人工确认可作为 gold",
        position="7",
        open_rate="0.42",
        completion_rate="0.91",
        avg_finish_time="38",
    )

    readiness = agent.harness_readiness("法国")

    assert readiness["ready_for_real_eval"] is True
    assert readiness["human_gold 样本数"] == 1
    assert readiness["RAG human_gold 文档数"] == 1
    assert readiness["Facts memory gold 数"] == 1
    assert readiness["next_actions"] == ("可以运行真实 VLM Harness，并把结果作为小样本基线。",)


def test_agent_front_two_layers_readiness_proves_landed_infrastructure(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path
    agent.record_perception_memory("日本", "trial_image_parse", {"subject": "寿司", "color_mood": "清爽"})
    agent.record_working_memory("日本", "trial_state", {"operation_tag": "试新_日本_寿司0626", "status": "parsed"})
    agent.record_long_term_memory("日本", "value_rule_approval", {"rule_text": "寿司提需需保留日式餐桌语境。"})
    agent.record_extracted_fact("日本", "image_semantic_fact", {"subject": "寿司", "value_labels": ["本土饮食文化"]})
    agent.record_rag_citation_feedback("日本", chunk_id="JP_VALUE_001#chunk-1", usefulness="useful", note="能支撑本土饮食文化")

    readiness = agent.front_two_layers_readiness("日本")

    assert readiness["overall_status"] == "front_two_layers_landed"
    assert readiness["waiting_for_third_layer"] == "已接入 45 张真实拼图样本；下一步运行真实 VLM Harness，并抽查 AI silver 后晋升 human_gold。"
    assert all(gate["passed"] for gate in readiness["layer1_gates"])
    assert all(gate["passed"] for gate in readiness["layer2_gates"])
    assert any(gate["name"] == "AI silver -> human_gold 防误用" for gate in readiness["layer1_gates"])
    assert any(gate["name"] == "四层 Memory 可进入 RAG" for gate in readiness["layer2_gates"])
    assert any(gate["name"] == "RAG 多路召回与引用溯源" for gate in readiness["layer2_gates"])


def test_agent_ai_prelabeled_real_samples_as_silver_labels(tmp_path):
    image_path = tmp_path / "france-picnic.png"
    Image.new("RGB", (80, 60), (220, 180, 120)).save(image_path)

    class FakeVisionClient:
        provider = "qwen"

        def analyze(self, images, country, category, local_summary):
            return VisionLLMResult(
                subject="法式海滩野餐",
                scene="海边沙滩上摆放法棍、奶酪、葡萄和酒杯，远处有遮阳伞与海面",
                culture_elements=("法式餐食", "海滨度假"),
                style="暖金色夕阳，蓝白桌布与沙滩暖色形成度假氛围",
                risk_tags=("无明显版权/IP风险",),
                prompt_keywords=("法式野餐", "海边", "法棍", "奶酪"),
                confidence=0.91,
                provider="qwen",
                raw_text="fake vision result",
            )

    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path
    agent.trial_uploads = TrialImageUploadService(tmp_path / "uploads", vision_client=FakeVisionClient())
    agent.register_harness_real_samples(
        "法国",
        [{"sample_id": "fr-real-001", "local_image_path": str(image_path), "gold_grade": "A", "js_category": "lifestyle"}],
    )

    result = agent.auto_prelabeled_harness_samples("法国")

    assert result["updated_count"] == 1
    sample = agent.harness_samples("法国")[0]
    assert sample.gold_grade == "A"
    assert sample.gold_subject == "法式海滩野餐"
    assert "暖金色夕阳" in sample.gold_color_mood
    assert "海边沙滩" in sample.gold_composition
    assert sample.label_source == "ai_silver"
    assert sample.label_status == "pending_review"
    assert "生活艺术" in sample.gold_value_labels
    rows = agent.memory_debug("法国", query="法式海滩野餐")
    assert any(row["layer"] == "perception" and "法式海滩野餐" in row["summary"] for row in rows)


def test_agent_ai_prelabeled_default_business_samples_in_batches(tmp_path):
    class FakeVisionClient:
        provider = "qwen"

        def __init__(self):
            self.calls = []

        def analyze(self, images, country, category, local_summary):
            self.calls.append((country, category, images[0]["filename"]))
            return VisionLLMResult(
                subject="薰衣草风车",
                scene="普罗旺斯薰衣草田中有风车和石屋，前景花田层次清晰",
                culture_elements=("普罗旺斯", "法式乡村"),
                style="明亮紫色花田与暖色日光，法国度假氛围",
                risk_tags=(),
                prompt_keywords=("薰衣草", "风车", "石屋"),
                confidence=0.88,
                provider="qwen",
                raw_text="fake batch result",
            )

    client = FakeVisionClient()
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path
    agent.trial_uploads = TrialImageUploadService(tmp_path / "uploads", vision_client=client)

    result = agent.auto_prelabeled_harness_samples("法国", max_count=3)

    assert result["total_count"] == 20
    assert result["eligible_count"] == 20
    assert result["updated_count"] == 3
    assert result["remaining_needs_prelabeled"] == 17
    assert result["pending_review_count"] == 3
    assert len(client.calls) == 3
    samples = agent.harness_samples("法国")
    assert sum(sample.label_source == "ai_silver" for sample in samples) == 3
    assert sum(sample.label_status == "needs_ai_prelabeled" for sample in samples) == 17


def test_agent_ai_prelabeled_skips_existing_silver_and_human_gold_by_default(tmp_path):
    image_path = tmp_path / "france-picnic.png"
    second_image_path = tmp_path / "france-lavender.png"
    Image.new("RGB", (80, 60), (220, 180, 120)).save(image_path)
    Image.new("RGB", (80, 60), (120, 90, 200)).save(second_image_path)

    class FakeVisionClient:
        provider = "qwen"

        def __init__(self):
            self.calls = 0

        def analyze(self, images, country, category, local_summary):
            self.calls += 1
            return VisionLLMResult(
                subject="法式花园",
                scene="法式庭院中的花园静物",
                culture_elements=("法式花园",),
                style="明亮暖色",
                risk_tags=(),
                prompt_keywords=("花园",),
                confidence=0.9,
                provider="qwen",
                raw_text="fake result",
            )

    client = FakeVisionClient()
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path
    agent.trial_uploads = TrialImageUploadService(tmp_path / "uploads", vision_client=client)
    dataset = agent.register_harness_real_samples(
        "法国",
        [
            {"sample_id": "fr-real-001", "local_image_path": str(image_path), "gold_grade": "A", "js_category": "lifestyle"},
            {"sample_id": "fr-real-002", "local_image_path": str(second_image_path), "gold_grade": "S", "js_category": "landscape"},
        ],
    )
    rows = agent._read_harness_gold_rows(dataset)
    rows[0].update({"gold_subject": "海滩野餐", "label_source": "ai_silver", "label_status": "pending_review"})
    rows[1].update({"gold_subject": "薰衣草风车", "label_source": "human_gold", "label_status": "reviewed"})
    agent._write_harness_gold_rows(dataset, rows)

    result = agent.auto_prelabeled_harness_samples("法国")

    assert result["updated_count"] == 0
    assert result["already_labeled_count"] == 2
    assert client.calls == 0


def test_agent_ai_silver_label_compacts_long_visual_subject(tmp_path):
    image_path = tmp_path / "flower-cart.png"
    Image.new("RGB", (80, 60), (230, 200, 120)).save(image_path)

    class FakeVisionClient:
        provider = "qwen"

        def analyze(self, images, country, category, local_summary):
            return VisionLLMResult(
                subject="一位穿着浅蓝色复古连衣裙的年轻金发女性正在推着一辆装满红玫瑰粉色百合和白色雏菊的木制独轮手推车",
                scene="阳光明媚的法式花园小径，鲜花手推车位于前景",
                culture_elements=("法式花园", "田园生活"),
                style="温暖明亮的油画风格",
                risk_tags=(),
                prompt_keywords=("花园", "手推车", "鲜花"),
                confidence=0.9,
                provider="qwen",
                raw_text="fake long subject",
            )

    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path
    agent.trial_uploads = TrialImageUploadService(tmp_path / "uploads", vision_client=FakeVisionClient())
    agent.register_harness_real_samples(
        "法国",
        [{"sample_id": "fr-real-002", "local_image_path": str(image_path), "gold_grade": "A", "js_category": "character"}],
    )

    agent.auto_prelabeled_harness_samples("法国")

    sample = agent.harness_samples("法国")[0]
    assert len(sample.gold_subject) <= 8
    assert sample.gold_subject == "鲜花手推车"
    assert sample.subject == sample.gold_subject
    assert "法式花园小径" in sample.gold_composition


def test_agent_promotes_ai_silver_labels_to_human_gold_facts(tmp_path):
    image_path = tmp_path / "france-picnic.png"
    Image.new("RGB", (80, 60), (220, 180, 120)).save(image_path)
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path
    dataset = agent.register_harness_real_samples(
        "法国",
        [{"sample_id": "fr-real-001", "local_image_path": str(image_path), "gold_grade": "A", "js_category": "lifestyle"}],
    )
    rows = agent._read_harness_gold_rows(dataset)
    rows[0].update(
        {
            "gold_subject": "法式海滩野餐",
            "gold_color_mood": "暖金色夕阳，蓝白桌布与沙滩暖色形成度假氛围",
            "gold_composition": "海边沙滩上摆放法棍、奶酪、葡萄和酒杯",
            "gold_value_labels": "生活艺术;自然治愈",
            "gold_risk_labels": "",
            "human_note": "AI silver label，待人工抽查。",
            "label_source": "ai_silver",
            "label_status": "pending_review",
        }
    )
    agent._write_harness_gold_rows(dataset, rows)

    result = agent.approve_harness_silver_labels("法国", sample_ids=("fr-real-001",), reviewer_note="人工抽查通过")

    assert result["approved_count"] == 1
    assert result["fact_memory_count"] == 1
    assert result["rag_human_gold_count"] == 1
    assert result["human_gold_count"] == 1
    sample = agent.harness_samples("法国")[0]
    assert sample.label_source == "human_gold"
    assert sample.label_status == "reviewed"
    assert "人工抽查通过" in sample.human_note
    facts = agent.memory_debug("法国", query="法式海滩野餐")
    assert any(row["layer"] == "facts" and "法式海滩野餐" in row["summary"] for row in facts)
    rag_answer = agent.value_audit_rag_answer("法国", "法式海滩野餐 生活艺术", top_k=3)
    assert "FR_HARNESS_GOLD_fr-real-001#chunk-1" in rag_answer.citations


def test_agent_approves_only_selected_ai_silver_samples(tmp_path):
    image_path = tmp_path / "france-picnic.png"
    second_image_path = tmp_path / "france-lavender.png"
    Image.new("RGB", (80, 60), (220, 180, 120)).save(image_path)
    Image.new("RGB", (80, 60), (120, 90, 200)).save(second_image_path)
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "puzzle.db"))
    agent._runtime_dir = tmp_path
    dataset = agent.register_harness_real_samples(
        "法国",
        [
            {"sample_id": "fr-real-001", "local_image_path": str(image_path), "gold_grade": "A", "js_category": "lifestyle"},
            {"sample_id": "fr-real-002", "local_image_path": str(second_image_path), "gold_grade": "S", "js_category": "landscape"},
        ],
    )
    rows = agent._read_harness_gold_rows(dataset)
    for row, subject in zip(rows, ("法式海滩野餐", "薰衣草风车")):
        row.update(
            {
                "gold_subject": subject,
                "gold_color_mood": "暖色",
                "gold_composition": "清晰构图",
                "gold_value_labels": "自然治愈",
                "label_source": "ai_silver",
                "label_status": "pending_review",
            }
        )
    agent._write_harness_gold_rows(dataset, rows)

    result = agent.approve_harness_silver_labels("法国", sample_ids=("fr-real-001",), reviewer_note="只确认第1条")

    samples = {sample.sample_id: sample for sample in agent.harness_samples("法国")}
    assert result["approved_count"] == 1
    assert samples["fr-real-001"].label_source == "human_gold"
    assert samples["fr-real-001"].label_status == "reviewed"
    assert samples["fr-real-002"].label_source == "ai_silver"
    assert samples["fr-real-002"].label_status == "pending_review"


def test_agent_persists_generation_events_for_replay():
    agent = PuzzleOpsAgent()

    agent.record_generation_event(
        "日本",
        {
            "status": "failed",
            "provider": "dashscope",
            "model": "wanx-test",
            "endpoint": "https://example.test/gen",
            "task_id": "task-123",
            "source_operation_tag": "试新_日本_寿司0615",
            "generated_image_paths": "/tmp/out-1.png",
            "second_review_status": "not_started",
            "feishu_attachment_status": "blocked",
            "error_type": "quota_exceeded",
            "message": "DashScope 图像生成失败：quota exceeded",
        },
    )

    events = agent.generation_events("日本")

    assert events[-1]["status"] == "failed"
    assert events[-1]["provider"] == "dashscope"
    assert events[-1]["model"] == "wanx-test"
    assert events[-1]["endpoint"] == "https://example.test/gen"
    assert events[-1]["task_id"] == "task-123"
    assert events[-1]["source_operation_tag"] == "试新_日本_寿司0615"
    assert events[-1]["generated_image_paths"] == "/tmp/out-1.png"
    assert events[-1]["second_review_status"] == "not_started"
    assert events[-1]["feishu_attachment_status"] == "blocked"
    assert events[-1]["error_type"] == "quota_exceeded"


def test_agent_records_four_layer_memory_types():
    agent = PuzzleOpsAgent()
    country = "四层Memory测试国"

    agent.record_perception_memory(country, "trial_image_parse", {"subject": "寿司"})
    agent.record_working_memory(country, "generation_trace", {"status": "failed"})
    agent.record_long_term_memory(country, "value_rule_approval", {"rule": "寿司匹配本土饮食文化"})
    agent.record_extracted_fact(country, "image_semantic_fact", {"subject": "寿司", "risk_labels": []})

    overview = agent.memory_overview(country)

    assert overview["感知记忆"]["count"] == 1
    assert overview["短期记忆"]["count"] == 1
    assert overview["长期记忆"]["count"] == 1
    assert overview["结构化事实"]["count"] == 1
    assert overview["感知记忆"]["latest"]["payload"]["subject"] == "寿司"


def test_agent_builds_value_audit_rag_context_with_citations(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "isolated_rag.db"))
    agent.record_long_term_memory("日本", "value_rule_approval", {"rule_text": "寿司提需需保留日式餐桌语境和清爽色彩。"})
    agent.record_extracted_fact("日本", "image_semantic_fact", {"subject": "寿司", "value_labels": ["本土饮食文化"]})

    answer = agent.value_audit_rag_answer("日本", "寿司试新图是否符合日本价值观，并检查文字水印风险")

    assert "引用依据" in answer.prompt
    assert answer.citations
    assert any("VALUE" in citation or "MEMORY" in citation for citation in answer.citations)
    assert any("AUDIT" in citation for citation in answer.citations)
    assert "寿司" in answer.context
    assert "文字水印" in answer.context or "水印" in answer.context


def test_agent_rag_documents_include_all_four_memory_layers():
    agent = PuzzleOpsAgent()
    country = "日本"
    agent.record_perception_memory(country, "trial_image_parse", {"subject": "寿司", "visual": "米白与鲑鱼橙"})
    agent.record_working_memory(country, "generation_trace", {"status": "failed", "reason": "quota_exceeded"})
    agent.record_long_term_memory(country, "value_rule_approval", {"rule_text": "寿司提需需保留日式餐桌语境。"})
    agent.record_extracted_fact(country, "image_semantic_fact", {"subject": "寿司", "value_labels": ["本土饮食文化"]})

    documents = agent.build_value_audit_rag_index(country)
    source_types = {document.source_type for document in documents}
    overview = agent.memory_overview(country)

    assert "memory_perception" in source_types
    assert "memory_working" in source_types
    assert "approved_value_rule" in source_types
    assert "fact" in source_types
    assert overview["感知记忆"]["rag_ready_count"] >= 1
    assert overview["短期记忆"]["rag_ready_count"] >= 1
    assert overview["长期记忆"]["rag_ready_count"] >= 1
    assert overview["结构化事实"]["rag_ready_count"] >= 1


def test_agent_rag_summary_exposes_embedding_and_rerank_provider_names(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "")
    monkeypatch.setenv("RAG_RERANK_MODEL", "")

    summary = PuzzleOpsAgent().value_audit_rag_summary("日本")

    assert summary["embedding_provider"] == "dashscope"
    assert summary["embedding_model"] == "text-embedding-v4"
    assert summary["rerank_provider"] == "dashscope"
    assert summary["rerank_model"] == "qwen3-rerank"
    assert summary["provider_configured"] is True


def test_agent_rag_summary_marks_remote_ready_only_with_api_key(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("QWEN_API_KEY", "")

    missing_key = PuzzleOpsAgent().value_audit_rag_summary("日本")
    assert missing_key["provider_configured"] is True
    assert missing_key["provider_remote_ready"] is False

    monkeypatch.setenv("RAG_API_KEY", "dashscope-test")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test")
    ready = PuzzleOpsAgent().value_audit_rag_summary("日本")
    assert ready["provider_remote_ready"] is True


def test_agent_rag_summary_includes_runtime_stats():
    summary = PuzzleOpsAgent().value_audit_rag_summary("日本")

    assert "embedding_cache_hits" in summary
    assert "embedding_remote_calls" in summary
    assert "embedding_fallbacks" in summary
    assert "rerank_remote_calls" in summary
    assert "rerank_fallbacks" in summary


def test_agent_rag_summary_exposes_engineering_pipeline_settings():
    summary = PuzzleOpsAgent().value_audit_rag_summary("日本")

    assert summary["offline_loader"] == "StaticDocumentLoaderAdapter"
    assert summary["splitter"] == "sentence_token"
    assert summary["chunk_size_tokens"] == 600
    assert summary["chunk_overlap_tokens"] == 100
    assert summary["vector_store"] == "sqlite"
    assert "embedding cache" in str(summary["vector_store_status"])
    assert summary["bm25_top_k"] == 30
    assert summary["vector_top_k"] == 30
    assert summary["rerank_top_k"] == 5
    assert "价值观" in str(summary["rewritten_query"])
    assert summary["retrieval_trace"]["merged_candidate_count"] >= len(summary["citations"])
    assert summary["retrieval_trace"]["final_hits"]
    assert summary["retrieval_eval_report"]["hit@5"] >= 0.8
    assert summary["retrieval_eval_report"]["passed_threshold"] is True
    assert summary["knowledge_base"]["documents_path"].endswith("knowledge/processed/value_audit_documents.jsonl")
    assert summary["knowledge_base"]["eval_cases_path"].endswith("knowledge/eval/value_audit_cases.jsonl")
    assert summary["knowledge_base"]["file_document_count"] >= 1
    assert summary["knowledge_base"]["file_eval_case_count"] >= 1
    assert summary["knowledge_base"]["raw_dir"].endswith("knowledge/raw")
    assert "raw_file_count" in summary["knowledge_base"]


def test_agent_rag_summary_uses_qdrant_vector_store_config_when_declared(monkeypatch):
    monkeypatch.setenv("RAG_VECTOR_STORE_PROVIDER", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:6333")
    monkeypatch.setenv("QDRANT_COLLECTION", "puzzle_ops_rag")

    summary = PuzzleOpsAgent().value_audit_rag_summary("日本")

    assert summary["vector_store"] == "qdrant"
    assert summary["vector_store_collection"] == "puzzle_ops_rag"
    assert summary["vector_store_ready"] is True


def test_agent_rag_summary_uses_milvus_vector_store_config_when_declared(monkeypatch):
    monkeypatch.setenv("RAG_VECTOR_STORE_PROVIDER", "milvus")
    monkeypatch.setenv("MILVUS_URI", "http://127.0.0.1:19530")
    monkeypatch.setenv("MILVUS_COLLECTION", "puzzle_ops_rag")

    summary = PuzzleOpsAgent().value_audit_rag_summary("日本")

    assert summary["vector_store"] == "milvus"
    assert summary["vector_store_collection"] == "puzzle_ops_rag"
    assert summary["vector_store_ready"] is True
    assert "Milvus" in summary["vector_store_status"]


def test_agent_rag_summary_can_enable_qdrant_online_search_path(monkeypatch):
    monkeypatch.setenv("RAG_VECTOR_STORE_PROVIDER", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:6333")
    monkeypatch.setenv("QDRANT_COLLECTION", "puzzle_ops_rag")
    monkeypatch.setenv("RAG_QDRANT_SEARCH_ENABLED", "1")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "local")
    monkeypatch.setenv("RAG_ENABLE_REMOTE_CALLS", "")

    summary = PuzzleOpsAgent().value_audit_rag_summary("日本")

    assert summary["vector_store_search_enabled"] is True
    assert summary["retrieval_trace"]["vector_store_provider"] == "qdrant"


def test_agent_rag_summary_can_enable_milvus_online_search_path(monkeypatch):
    monkeypatch.setenv("RAG_VECTOR_STORE_PROVIDER", "milvus")
    monkeypatch.setenv("MILVUS_URI", "http://127.0.0.1:19530")
    monkeypatch.setenv("MILVUS_COLLECTION", "puzzle_ops_rag")
    monkeypatch.setenv("RAG_MILVUS_SEARCH_ENABLED", "1")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "local")
    monkeypatch.setenv("RAG_ENABLE_REMOTE_CALLS", "")

    summary = PuzzleOpsAgent().value_audit_rag_summary("日本")

    assert summary["vector_store_search_enabled"] is True
    assert summary["retrieval_trace"]["vector_store_provider"] == "milvus"


def test_agent_rag_summary_exposes_citation_source_parent_and_text():
    summary = PuzzleOpsAgent().value_audit_rag_summary("日本")

    assert summary["citation_details"]
    first = summary["citation_details"][0]
    assert first["chunk_id"]
    assert first["parent_id"]
    assert first["source_type"]
    assert first["text"]


def test_agent_persists_value_audit_rag_trace_for_replay(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_trace.db"))
    agent._runtime_dir = tmp_path

    answer = agent.value_audit_rag_answer("日本", "寿司是否符合日本本土饮食文化价值观", top_k=2)

    traces = agent.recent_rag_traces("日本")
    assert traces
    latest = traces[0]
    assert latest["country"] == "日本"
    assert latest["original_query"] == "寿司是否符合日本本土饮食文化价值观"
    assert latest["rewritten_query"]
    assert latest["citations"] == answer.citations
    assert latest["prompt"] == answer.prompt
    assert latest["answer"] == answer.context
    assert latest["answer_source"] == "retrieved_context"
    assert latest["support_documents"] == (answer.context,)
    assert latest["latency_ms"] >= 0
    assert latest["retrieval_trace"]["final_hits"]
    assert Path(str(latest["trace_path"])).exists()


def test_agent_generated_rag_answer_records_llm_output_in_trace(tmp_path):
    class FakeGenerator:
        provider_name = "fake-qwen"
        model = "qwen3.7-plus"

        def generate(self, prompt):
            return RagGeneratedAnswer(
                answer="寿司图符合日本本土饮食文化，引用 JP_SUSHI 依据；需要避免品牌露出。",
                status="generated",
                provider="qwen",
                model="qwen3.7-plus",
                citations=prompt.citations,
                prompt=prompt.prompt,
            )

    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_generated_trace.db"))
    agent._runtime_dir = tmp_path

    result = agent.value_audit_rag_generated_answer(
        "日本",
        "寿司图是否符合日本本土饮食价值观？",
        top_k=2,
        generator=FakeGenerator(),
    )

    traces = agent.recent_rag_traces("日本")
    latest = traces[0]
    assert result.status == "generated"
    assert "寿司图符合日本本土饮食文化" in result.answer
    assert latest["llm_answer"] == result.answer
    assert latest["answer_source"] == "llm_generated"
    assert latest["generation_status"] == "generated"
    assert latest["generation_provider"] == "qwen"
    assert latest["generation_model"] == "qwen3.7-plus"
    assert latest["generation_citations"] == result.citations
    assert latest["generation_latency_ms"] >= 0


def test_agent_generated_rag_answer_skips_when_generation_provider_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("RAG_GENERATION_PROVIDER", raising=False)
    monkeypatch.delenv("RAG_ENABLE_REMOTE_CALLS", raising=False)
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_generation_missing.db"))
    agent._runtime_dir = tmp_path

    result = agent.value_audit_rag_generated_answer("日本", "寿司图是否符合日本价值观？", top_k=2)

    latest = agent.recent_rag_traces("日本")[0]
    assert result.status == "skipped"
    assert "RAG 生成模型未配置" in result.error
    assert latest["answer_source"] == "retrieved_context"
    assert latest["generation_status"] == "skipped"
    assert latest["generation_provider"] == "missing"
    assert latest["llm_answer"] == ""


def test_harness_run_links_rag_trace_artifacts_for_replay(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "harness_rag_trace.db"))
    agent._runtime_dir = tmp_path

    run = agent.harness_run("日本", save=True)
    saved = agent.latest_harness_run("日本")

    assert run.rag_trace_artifacts
    assert saved is not None
    assert saved.rag_trace_artifacts == run.rag_trace_artifacts
    artifact = run.rag_trace_artifacts[0]
    assert artifact["country"] == "日本"
    assert artifact["trace_path"]
    assert Path(str(artifact["trace_path"])).exists()
    assert artifact["citations"]
    assert "只基于引用依据回答" in artifact["prompt"]
    assert artifact["context"]
    assert artifact["retrieval_trace"]["final_hits"]
    value_case = next(case for case in run.cases if case.task_type == "value_match_eval")
    assert value_case.evidence_trace["rag_trace_path"] == artifact["trace_path"]


def test_agent_exports_harness_external_eval_artifacts(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "external_eval.db"))
    agent._runtime_dir = tmp_path
    agent.harness_run("日本", save=True)

    paths = agent.export_harness_external_eval_artifacts("日本", tmp_path / "external_eval")

    assert paths["phoenix"].name == "phoenix_harness_日本.json"
    assert paths["promptfoo"].name == "promptfoo_harness_日本.json"
    assert paths["promptfoo_yaml"].name == "promptfoo_harness_日本.yaml"
    assert paths["deepeval"].name == "deepeval_harness_日本.json"
    phoenix = json.loads(paths["phoenix"].read_text(encoding="utf-8"))
    promptfoo = json.loads(paths["promptfoo"].read_text(encoding="utf-8"))
    assert phoenix["rag_trace_artifacts"][0]["trace_path"]
    assert promptfoo["metadata"]["rag_trace_artifacts"][0]["trace_id"]
    promptfoo_yaml = paths["promptfoo_yaml"].read_text(encoding="utf-8")
    assert "providers:" in promptfoo_yaml
    assert "tests:" in promptfoo_yaml
    assert "rag_trace_artifacts:" in promptfoo_yaml


def test_agent_exports_value_audit_rag_offline_artifacts(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_artifacts.db"))

    artifacts = agent.export_value_audit_rag_artifacts("日本", tmp_path / "rag_export")

    assert artifacts["manifest_path"].endswith("rag_manifest_日本.json")
    assert artifacts["documents_path"].endswith("rag_documents_日本.jsonl")
    assert artifacts["chunks_path"].endswith("rag_chunks_日本.jsonl")
    assert artifacts["document_count"] >= 1
    assert artifacts["chunk_count"] >= artifacts["document_count"]
    assert artifacts["vector_store"] in {"sqlite", "qdrant"}
    assert artifacts["parent_child_count"] >= 1


def test_agent_value_audit_rag_eval_report_tracks_hit_at_five_threshold(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_eval.db"))

    report = agent.value_audit_rag_eval_report("日本")

    assert report["dataset_name"] == "日本价值观审核RAG file eval"
    assert report["hit@5"] >= 0.8
    assert report["passed_threshold"] is True
    assert report["total"] >= 3
    assert report["cases"][0]["expected_parent_id"]


def test_agent_exports_value_audit_rag_acceptance_report(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_acceptance.db"))
    agent.rag_provider_config = RagProviderConfig()

    result = agent.export_value_audit_rag_acceptance_report("日本", tmp_path / "rag_acceptance")

    assert result["path"].endswith("rag_acceptance_日本.json")
    assert result["hit@5"] >= 0.8
    assert result["passed_threshold"] is True
    assert result["embedding"]["model_family"] in {"Qwen3-Embedding", "DashScope-Embedding", "Local"}
    assert result["retrieval_routes"]["bm25"] is True
    assert result["retrieval_routes"]["rerank"] is True
    assert result["observed_retrieval"]["embedding_provider"]
    assert "embedding_remote_calls" in result["runtime_stats"]


def test_agent_acceptance_report_tracks_human_gold_business_sample_gate(monkeypatch, tmp_path):
    image_path = tmp_path / "france-picnic.png"
    image_path.write_bytes(b"fake-png")
    dataset = tmp_path / "gold_samples.csv"
    dataset.write_text(
        "\n".join(
            (
                "sample_id,country,local_image_path,operation_tag,subject,js_category,source,position,open_rate,completion_rate,avg_finish_time,gold_grade,gold_subject,gold_color_mood,gold_composition,gold_value_labels,gold_risk_labels,human_note,label_source,label_status",
                f"fr-real-001,法国,{image_path},试新_法国_海滩野餐0624,海滩野餐,lifestyle,real,7,0.42,0.91,38,A,海滩野餐,暖色,海滩沙滩,生活艺术,,人工确认,human_gold,reviewed",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_HARNESS_DATASET", str(dataset))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_acceptance.db"))
    agent.rag_provider_config = RagProviderConfig()

    result = agent.export_value_audit_rag_acceptance_report("法国", tmp_path / "rag_acceptance")

    gate = result["business_sample_gate"]
    assert gate["case_count"] == 1
    assert gate["hit@5"] == 1.0
    assert gate["passed_threshold"] is True
    assert gate["threshold"] == 0.8
    assert gate["source"] == "human_gold"


def test_agent_loads_versioned_knowledge_documents_and_eval_cases(monkeypatch, tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    processed = knowledge_dir / "processed"
    eval_dir = knowledge_dir / "eval"
    processed.mkdir(parents=True)
    eval_dir.mkdir(parents=True)
    (processed / "value_audit_documents.jsonl").write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "document_id": "JP_KB_SUSHI",
                        "country": "日本",
                        "source_type": "value_rule",
                        "title": "日本饮食文化",
                        "text": "寿司、抹茶、和果子属于日本本土饮食文化。",
                        "metadata": {"knowledge_version": "unit-test"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "document_id": "GLOBAL_KB_IP",
                        "country": "GLOBAL",
                        "source_type": "audit_policy",
                        "title": "版权风险",
                        "text": "避免文字水印、商标、热门IP角色。",
                    },
                    ensure_ascii=False,
                ),
            )
        ),
        encoding="utf-8",
    )
    (eval_dir / "value_audit_cases.jsonl").write_text(
        json.dumps(
            {
                "query": "日本寿司图是否符合本土饮食价值观",
                "country": "日本",
                "expected_parent_id": "JP_KB_SUSHI",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_RAG_KNOWLEDGE_DIR", str(knowledge_dir))

    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "knowledge.db"))
    documents = agent.build_value_audit_rag_index("日本")
    report = agent.value_audit_rag_eval_report("日本")

    assert any(document.document_id == "JP_KB_SUSHI" for document in documents)
    assert report["dataset_name"] == "日本价值观审核RAG file eval"
    assert report["total"] == 1
    assert report["cases"][0]["expected_parent_id"] == "JP_KB_SUSHI"
    assert report["hit@5"] == 1.0


def test_agent_rebuilds_processed_rag_knowledge_from_raw(monkeypatch, tmp_path):
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
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rebuild.db"))

    result = agent.rebuild_rag_knowledge_from_raw("日本")

    assert result["document_count"] == 1
    assert result["processed_path"].endswith("processed/value_audit_documents.jsonl")
    assert result["hit@5"] == 1.0
    assert (knowledge_dir / "processed" / "value_audit_documents.jsonl").exists()


def test_agent_reindexes_raw_rag_knowledge_into_qdrant(monkeypatch, tmp_path):
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
寿司属于日本本土饮食文化，适合清爽餐桌近景。
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
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "qdrant_reindex.db"))

    class FakeEmbedding:
        provider_name = "dashscope:text-embedding-v4"

        def query_vector(self, text: str):
            assert text
            return (0.1, 0.2, 0.3)

    class FakeQdrantStore:
        def __init__(self):
            self.points = ()
            self.ensure_vector_size = 0

        def ensure_collection(self, vector_size):
            self.ensure_vector_size = vector_size
            return {"status": "created", "vector_size": vector_size, "collection": "puzzle_ops_rag"}

        def upsert(self, points):
            self.points = points
            return {"status": "ok", "result": {"operation_id": 42}}

    store = FakeQdrantStore()

    result = agent.reindex_rag_qdrant_from_raw("日本", embedding_provider=FakeEmbedding(), vector_store=store)

    assert result["status"] == "indexed"
    assert result["document_count"] >= 1
    assert result["chunk_count"] == len(store.points)
    assert result["vector_count"] == len(store.points)
    assert result["upserted_points"] == len(store.points)
    assert result["hit@5"] == 1.0
    assert result["vector_size"] == 3
    assert result["collection_status"]["status"] == "created"
    assert result["run_id"]
    assert result["manifest_path"].endswith(f"indices/runs/qdrant_reindex_日本_{result['run_id']}.json")
    assert result["latest_manifest_path"].endswith("indices/qdrant_reindex_日本.json")
    assert store.ensure_vector_size == 3
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["country"] == "日本"
    assert manifest["run_id"] == result["run_id"]
    assert manifest["vector_size"] == 3
    assert manifest["upserted_points"] == len(store.points)
    assert manifest["point_ids"] == [point.id for point in store.points]
    assert manifest["point_records"][0]["id"] == store.points[0].id
    assert manifest["point_records"][0]["vector"] == list(store.points[0].vector)
    assert any(record["payload"]["parent_id"] == "JP_KB_SUSHI_FOOD" for record in manifest["point_records"])
    summary = agent.value_audit_rag_summary("日本")["knowledge_base"]
    assert summary["qdrant_manifest_exists"] is True
    assert summary["qdrant_manifest_run_id"] == result["run_id"]
    assert summary["qdrant_manifest_history_count"] >= 1
    assert summary["qdrant_manifest_recent_runs"][0]["run_id"] == result["run_id"]
    assert summary["qdrant_manifest_vector_size"] == 3
    assert summary["qdrant_manifest_upserted_points"] == len(store.points)
    assert any(point.payload["parent_id"] == "JP_KB_SUSHI_FOOD" for point in store.points)


def test_agent_reindexes_raw_rag_knowledge_into_milvus(monkeypatch, tmp_path):
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
寿司属于日本本土饮食文化，适合清爽餐桌近景。
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
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "milvus_reindex.db"))
    agent.rag_vector_store_config = agent.rag_vector_store_config.__class__(
        provider="milvus",
        endpoint="http://127.0.0.1:19530",
        collection="puzzle_ops_rag",
        configured=True,
        ready=True,
    )

    class FakeEmbedding:
        provider_name = "dashscope:text-embedding-v4"

        def query_vector(self, text: str):
            assert text
            return (0.1, 0.2, 0.3)

    class FakeMilvusStore:
        provider_name = "milvus"

        def __init__(self):
            self.points = ()

        def upsert(self, points):
            self.points = points
            return {"status": "ok", "insert_count": len(points)}

    store = FakeMilvusStore()

    result = agent.reindex_rag_vector_store_from_raw("日本", embedding_provider=FakeEmbedding(), vector_store=store)

    assert result["status"] == "indexed"
    assert result["vector_store_provider"] == "milvus"
    assert result["vector_store_collection"] == "puzzle_ops_rag"
    assert result["upserted_points"] == len(store.points)
    assert result["vector_size"] == 3
    assert "precision@5" in result
    assert "recall@5" in result
    assert "ndcg@5" in result
    assert result["manifest_path"].endswith(f"indices/runs/milvus_reindex_日本_{result['run_id']}.json")
    assert result["latest_manifest_path"].endswith("indices/milvus_reindex_日本.json")
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["vector_store"]["provider"] == "milvus"
    assert manifest["vector_store"]["collection"] == "puzzle_ops_rag"
    assert "precision@5" in manifest
    assert "recall@5" in manifest
    assert "ndcg@5" in manifest
    assert any(record["payload"]["parent_id"] == "JP_KB_SUSHI_FOOD" for record in manifest["point_records"])
    summary = agent.value_audit_rag_summary("日本")["knowledge_base"]
    assert summary["vector_store_manifest_exists"] is True
    assert summary["vector_store_manifest_provider"] == "milvus"
    assert summary["vector_store_manifest_upserted_points"] == len(store.points)


def test_agent_runs_full_rag_industrial_acceptance_with_qdrant_and_bge(monkeypatch, tmp_path):
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
寿司属于日本本土饮食文化，适合清爽餐桌近景。
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
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_full_acceptance.db"))
    agent.rag_provider_config = RagProviderConfig(
        embedding_provider="dashscope",
        embedding_model="text-embedding-v4",
        rerank_provider="bge",
        rerank_model="BAAI/bge-reranker-v2-m3",
        configured=True,
        remote_ready=True,
        remote_calls_enabled=True,
    )

    class FakeQwenEmbedding:
        provider_name = "dashscope:text-embedding-v4"

        def __init__(self):
            self.stats = RagRuntimeStats()

        def query_vector(self, text: str):
            self.stats.embedding_remote_calls += 1
            return (0.1, 0.2, 0.3)

        def similarities(self, query: str, texts: tuple[str, ...]):
            self.stats.embedding_remote_calls += 1
            return tuple(0.9 if "寿司" in text else 0.1 for text in texts)

        def healthcheck(self):
            return {"provider": "dashscope", "ready": True, "model": "text-embedding-v4", "probe_vector_dim": 3}

    class FakeQdrantStore:
        provider_name = "qdrant"

        def __init__(self):
            self.points = ()

        def ensure_collection(self, vector_size):
            return {"status": "created", "vector_size": vector_size, "collection": "puzzle_ops_rag"}

        def upsert(self, points):
            self.points = points
            return {"status": "ok"}

        def search(self, query_vector, *, country, top_k):
            assert self.points
            return {str(self.points[0].payload["chunk_id"]): 0.99}

        def healthcheck(self):
            return {"provider": "qdrant", "ready": True, "exists": True, "vector_size": 3}

    def fake_rerank_transport(query, documents, api_key, endpoint, model):
        return {"results": [{"index": index, "relevance_score": 0.96 - index * 0.01} for index, _ in enumerate(documents)]}

    embedding = FakeQwenEmbedding()
    store = FakeQdrantStore()
    rerank = BGERerankProvider(
        api_key="",
        model="BAAI/bge-reranker-v2-m3",
        endpoint="http://127.0.0.1:9997/v1/rerank",
        transport=fake_rerank_transport,
        stats=RagRuntimeStats(),
    )

    result = agent.run_full_rag_industrial_acceptance(
        "日本",
        tmp_path / "rag_full_acceptance",
        embedding_provider=embedding,
        rerank_provider=rerank,
        vector_store=store,
        preflight_mode="live",
    )

    assert result["status"] == "passed"
    assert result["reindex"]["status"] == "indexed"
    assert result["report_path"].endswith("rag_acceptance_full_日本.json")
    assert result["report"]["hit@5"] == 1.0
    assert result["report"]["observed_retrieval"]["qdrant_vector_hits"] is True
    assert result["report"]["runtime_stats"]["embedding_remote_calls"] >= 1
    assert result["report"]["runtime_stats"]["rerank_remote_calls"] >= 1
    assert result["preflight"]["embedding"]["ready"] is True
    assert result["preflight"]["qdrant"]["ready"] is True
    assert result["preflight"]["rerank"]["ready"] is True
    assert Path(result["report_path"]).exists()
    summary = json.loads(Path(result["summary_path"]).read_text(encoding="utf-8"))
    assert summary["preflight"]["embedding"]["probe_vector_dim"] == 3


def test_agent_runs_full_rag_industrial_acceptance_with_milvus(monkeypatch, tmp_path):
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
寿司属于日本本土饮食文化，适合清爽餐桌近景。
""",
        encoding="utf-8",
    )
    (eval_dir / "value_audit_cases.jsonl").write_text(
        json.dumps({"query": "日本寿司图是否符合本土饮食价值观", "country": "日本", "expected_parent_id": "JP_KB_SUSHI_FOOD"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_RAG_KNOWLEDGE_DIR", str(knowledge_dir))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_full_acceptance_milvus.db"))
    agent.rag_vector_store_config = agent.rag_vector_store_config.__class__(
        provider="milvus",
        endpoint="http://127.0.0.1:19530",
        collection="puzzle_ops_rag",
        configured=True,
        ready=True,
    )
    agent.rag_provider_config = RagProviderConfig(
        embedding_provider="dashscope",
        embedding_model="text-embedding-v4",
        rerank_provider="bge",
        rerank_model="BAAI/bge-reranker-v2-m3",
        configured=True,
        remote_ready=True,
        remote_calls_enabled=True,
    )

    class FakeQwenEmbedding:
        provider_name = "dashscope:text-embedding-v4"

        def __init__(self):
            self.stats = RagRuntimeStats()

        def query_vector(self, text: str):
            self.stats.embedding_remote_calls += 1
            return (0.1, 0.2, 0.3)

        def similarities(self, query: str, texts: tuple[str, ...]):
            self.stats.embedding_remote_calls += 1
            return tuple(0.9 if "寿司" in text else 0.1 for text in texts)

        def healthcheck(self):
            return {"provider": "dashscope", "ready": True, "model": "text-embedding-v4", "probe_vector_dim": 3}

    class FakeMilvusStore:
        provider_name = "milvus"

        def __init__(self):
            self.points = ()

        def upsert(self, points):
            self.points = points
            return {"status": "ok", "insert_count": len(points)}

        def search(self, query_vector, *, country, top_k):
            assert self.points
            return {str(self.points[0].payload["chunk_id"]): 0.99}

        def healthcheck(self):
            return {"provider": "milvus", "ready": True, "exists": True, "vector_size": 3}

    def fake_rerank_transport(query, documents, api_key, endpoint, model):
        return {"results": [{"index": index, "relevance_score": 0.96 - index * 0.01} for index, _ in enumerate(documents)]}

    embedding = FakeQwenEmbedding()
    store = FakeMilvusStore()
    rerank = BGERerankProvider(
        api_key="",
        model="BAAI/bge-reranker-v2-m3",
        endpoint="http://127.0.0.1:9997/v1/rerank",
        transport=fake_rerank_transport,
        stats=RagRuntimeStats(),
    )

    result = agent.run_full_rag_industrial_acceptance(
        "日本",
        tmp_path / "rag_full_acceptance_milvus",
        embedding_provider=embedding,
        rerank_provider=rerank,
        vector_store=store,
        preflight_mode="live",
    )

    assert result["status"] == "passed"
    assert result["reindex"]["vector_store_provider"] == "milvus"
    assert result["report"]["observed_retrieval"]["vector_store_provider"] == "milvus"
    assert result["preflight"]["qdrant"]["provider"] == "milvus"


def test_agent_full_rag_acceptance_defaults_to_fast_preflight_without_live_healthchecks(monkeypatch, tmp_path):
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
寿司属于日本本土饮食文化，适合清爽餐桌近景。
""",
        encoding="utf-8",
    )
    (eval_dir / "value_audit_cases.jsonl").write_text(
        json.dumps({"query": "日本寿司图是否符合本土饮食价值观", "country": "日本", "expected_parent_id": "JP_KB_SUSHI_FOOD"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_RAG_KNOWLEDGE_DIR", str(knowledge_dir))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_fast_preflight.db"))

    class FakeEmbedding:
        provider_name = "dashscope:text-embedding-v4"

        def __init__(self):
            self.stats = RagRuntimeStats()
            self.healthcheck_calls = 0

        def query_vector(self, text: str):
            self.stats.embedding_remote_calls += 1
            return (0.1, 0.2, 0.3)

        def similarities(self, query: str, texts: tuple[str, ...]):
            return tuple(0.9 for _ in texts)

        def healthcheck(self):
            self.healthcheck_calls += 1
            raise AssertionError("default preflight must not call live embedding healthcheck")

    class FakeStore:
        provider_name = "qdrant"

        def __init__(self):
            self.points = ()
            self.healthcheck_calls = 0

        def ensure_collection(self, vector_size):
            return {"status": "created", "vector_size": vector_size, "collection": "puzzle_ops_rag"}

        def upsert(self, points):
            self.points = points
            return {"status": "ok"}

        def search(self, query_vector, *, country, top_k):
            return {str(self.points[0].payload["chunk_id"]): 0.99}

        def healthcheck(self):
            self.healthcheck_calls += 1
            raise AssertionError("default preflight must not call live qdrant healthcheck")

    embedding = FakeEmbedding()
    store = FakeStore()

    result = agent.run_full_rag_industrial_acceptance(
        "日本",
        tmp_path / "rag_fast_preflight",
        embedding_provider=embedding,
        vector_store=store,
    )

    assert result["status"] == "passed"
    assert result["preflight"]["mode"] == "fast"
    assert result["preflight"]["embedding"]["ready"] is True
    assert result["preflight"]["qdrant"]["ready"] is True
    assert embedding.healthcheck_calls == 0
    assert store.healthcheck_calls == 0


def test_agent_full_rag_acceptance_returns_diagnostics_when_qdrant_fails(monkeypatch, tmp_path):
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
寿司属于日本本土饮食文化，适合清爽餐桌近景。
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
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rag_full_acceptance_fail.db"))

    class FakeEmbedding:
        provider_name = "dashscope:text-embedding-v4"

        def __init__(self):
            self.stats = RagRuntimeStats()

        def query_vector(self, text: str):
            self.stats.embedding_remote_calls += 1
            return (0.1, 0.2, 0.3)

        def similarities(self, query: str, texts: tuple[str, ...]):
            return tuple(0.9 for _ in texts)

    class BrokenQdrantStore:
        provider_name = "qdrant"

        def ensure_collection(self, vector_size):
            raise RuntimeError("Qdrant refused connection")

    result = agent.run_full_rag_industrial_acceptance(
        "日本",
        tmp_path / "rag_full_acceptance_fail",
        embedding_provider=FakeEmbedding(),
        vector_store=BrokenQdrantStore(),
    )

    assert result["status"] == "failed"
    assert result["failure_stage"] == "qdrant_reindex"
    assert "Qdrant refused connection" in result["error"]
    assert any(item["component"] == "qdrant" for item in result["diagnostics"])
    assert Path(result["summary_path"]).exists()


def test_agent_runs_qdrant_smoke_diagnostic_from_latest_manifest(monkeypatch, tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    indices = knowledge_dir / "indices"
    runs = indices / "runs"
    runs.mkdir(parents=True)
    run_id = "20260702-test1234"
    (indices / "qdrant_reindex_日本.json").write_text(
        json.dumps({"run_id": run_id, "country": "日本", "status": "indexed", "vector_size": 3}, ensure_ascii=False),
        encoding="utf-8",
    )
    run_manifest = runs / f"qdrant_reindex_日本_{run_id}.json"
    run_manifest.write_text(
        json.dumps({"run_id": run_id, "country": "日本", "status": "indexed", "vector_size": 3}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUZZLEOPS_RAG_KNOWLEDGE_DIR", str(knowledge_dir))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "qdrant_smoke.db"))

    class FakeQdrantStore:
        def smoke_diagnostic(self, *, vector_size: int, country: str):
            assert vector_size == 3
            assert country == "日本"
            return {"status": "passed", "search_hit": True, "cleanup_status": "deleted", "vector_size": vector_size}

    result = agent.run_qdrant_smoke_diagnostic("日本", vector_store=FakeQdrantStore())

    assert result["status"] == "passed"
    assert json.loads(run_manifest.read_text(encoding="utf-8"))["smoke_diagnostic"]["status"] == "passed"
    summary = agent.value_audit_rag_summary("日本")["knowledge_base"]
    assert summary["qdrant_manifest_smoke_status"] == "passed"
    assert summary["qdrant_manifest_smoke_cleanup_status"] == "deleted"


def test_agent_rolls_back_qdrant_latest_manifest_to_history_run(monkeypatch, tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    indices = knowledge_dir / "indices"
    runs = indices / "runs"
    runs.mkdir(parents=True)
    old_run = {"run_id": "old-run", "country": "日本", "status": "indexed", "vector_size": 3, "upserted_points": 2}
    target_run = {
        "run_id": "target-run",
        "country": "日本",
        "status": "indexed",
        "vector_size": 5,
        "upserted_points": 9,
        "point_ids": ["p1", "p2"],
        "point_records": [
            {"id": "p1", "vector": [0.1, 0.2], "payload": {"chunk_id": "c1"}},
            {"id": "p2", "vector": [0.3, 0.4], "payload": {"chunk_id": "c2"}},
        ],
    }
    (indices / "qdrant_reindex_日本.json").write_text(json.dumps(old_run, ensure_ascii=False), encoding="utf-8")
    (runs / "qdrant_reindex_日本_old-run.json").write_text(json.dumps(old_run, ensure_ascii=False), encoding="utf-8")
    (runs / "qdrant_reindex_日本_target-run.json").write_text(json.dumps(target_run, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("PUZZLEOPS_RAG_KNOWLEDGE_DIR", str(knowledge_dir))
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "rollback.db"))

    class FakeQdrantStore:
        def __init__(self):
            self.restored_point_ids = ()
            self.restored_records = ()

        def restore_points(self, point_ids, point_records=()):
            self.restored_point_ids = point_ids
            self.restored_records = point_records
            return {"status": "restored", "restored_points": len(point_records)}

    store = FakeQdrantStore()

    result = agent.rollback_qdrant_manifest("日本", "target-run", vector_store=store)

    assert result["status"] == "rolled_back"
    assert result["run_id"] == "target-run"
    assert result["restore_status"]["status"] == "restored"
    assert result["restore_status"]["restored_points"] == 2
    assert store.restored_point_ids == ("p1", "p2")
    assert store.restored_records[0]["payload"]["chunk_id"] == "c1"
    latest = json.loads((indices / "qdrant_reindex_日本.json").read_text(encoding="utf-8"))
    assert latest["run_id"] == "target-run"
    summary = agent.value_audit_rag_summary("日本")["knowledge_base"]
    assert summary["qdrant_manifest_run_id"] == "target-run"
    assert summary["qdrant_manifest_vector_size"] == 5


def test_agent_exports_harness_annotation_files_for_label_tools(monkeypatch, tmp_path):
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
    agent = PuzzleOpsAgent()
    agent.record_harness_override("日本", "real-001", "value_match_eval", "人工修正：寿司图应匹配本土饮食文化。")

    paths = agent.export_harness_annotation_files("日本", tmp_path / "exports")

    assert paths["argilla"].name == "argilla_harness_日本.jsonl"
    assert paths["label_studio"].name == "label_studio_harness_日本.json"
    argilla_line = json.loads(paths["argilla"].read_text(encoding="utf-8").splitlines()[0])
    assert argilla_line["fields"]["sample_id"] == "real-001"
    assert argilla_line["fields"]["gold_subject"] == "寿司"
    assert "本土饮食文化" in argilla_line["metadata"]["human_override"]
    label_payload = json.loads(paths["label_studio"].read_text(encoding="utf-8"))
    assert label_payload[0]["data"]["sample_id"] == "real-001"
    assert label_payload[0]["data"]["image"].endswith("real-sushi.png")
    assert "Agent 输出" in label_payload[0]["data"]["agent_output_label"]
