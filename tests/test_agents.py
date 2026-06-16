from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.trial_upload import TrialImageUploadService
from puzzle_ops.vision_llm import MissingVisionLLMConfig, OpenAIVisionLLMClient
from datetime import date
import json


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
    country = "测试国"

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


def test_agent_builds_value_audit_rag_context_with_citations():
    agent = PuzzleOpsAgent()
    agent.record_long_term_memory("日本", "value_rule_approval", {"rule_text": "寿司提需需保留日式餐桌语境和清爽色彩。"})
    agent.record_extracted_fact("日本", "image_semantic_fact", {"subject": "寿司", "value_labels": ["本土饮食文化"]})

    answer = agent.value_audit_rag_answer("日本", "寿司试新图是否符合日本价值观，并检查文字水印风险")

    assert "引用依据" in answer.prompt
    assert answer.citations
    assert any("VALUE" in citation or "MEMORY" in citation for citation in answer.citations)
    assert any("AUDIT" in citation for citation in answer.citations)
    assert "寿司" in answer.context
    assert "文字水印" in answer.context or "水印" in answer.context


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
