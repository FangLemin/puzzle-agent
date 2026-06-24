from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.trial_upload import TrialImageUploadService
from puzzle_ops.vision_llm import MissingVisionLLMConfig, OpenAIVisionLLMClient
from puzzle_ops.storage import PuzzleRepository
from puzzle_ops.audit import AuditPolicyRetriever
from puzzle_ops.trial_upload import TrialImageUploadService
from puzzle_ops.vision_llm import VisionLLMResult
from datetime import date
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
    sample = agent.harness_samples("法国")[0]
    assert sample.label_source == "human_gold"
    assert sample.label_status == "reviewed"
    assert "人工抽查通过" in sample.human_note
    facts = agent.memory_debug("法国", query="法式海滩野餐")
    assert any(row["layer"] == "facts" and "法式海滩野餐" in row["summary"] for row in facts)


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
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "text-embedding-v3")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_RERANK_MODEL", "gte-rerank-v2")

    summary = PuzzleOpsAgent().value_audit_rag_summary("日本")

    assert summary["embedding_provider"] == "dashscope"
    assert summary["embedding_model"] == "text-embedding-v3"
    assert summary["rerank_provider"] == "dashscope"
    assert summary["rerank_model"] == "gte-rerank-v2"
    assert summary["provider_configured"] is True


def test_agent_rag_summary_marks_remote_ready_only_with_api_key(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

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


def test_agent_rag_summary_exposes_citation_source_parent_and_text():
    summary = PuzzleOpsAgent().value_audit_rag_summary("日本")

    assert summary["citation_details"]
    first = summary["citation_details"][0]
    assert first["chunk_id"]
    assert first["parent_id"]
    assert first["source_type"]
    assert first["text"]


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
