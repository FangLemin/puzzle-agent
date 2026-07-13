from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.skills import BusinessSkillLibrary, SkillExecutionError
from puzzle_ops.storage import PuzzleRepository


def test_business_skill_library_defines_five_contracts_with_governance_metadata():
    library = BusinessSkillLibrary.default()

    assert library.skill_ids() == (
        "weekly_review_skill",
        "regular_demand_skill",
        "trial_parse_skill",
        "value_audit_skill",
        "memory_governance_skill",
    )
    regular = library.get("regular_demand_skill")
    assert "country" in regular.input_schema["required"]
    assert "draft_rows" in regular.output_schema["properties"]
    assert regular.allowed_tools
    assert regular.guarded_tools == ("feishu.write_table",)
    assert regular.rag_task_index == "value_master"
    assert "audit_policy" in regular.rag_source_types
    assert regular.memory_write_policy["layer"] == "working"
    assert "飞书字段完整率" in regular.acceptance_metrics


def test_business_skill_input_schema_validation_rejects_missing_required_fields():
    library = BusinessSkillLibrary.default()

    errors = library.validate_input("weekly_review_skill", {"country": "日本"})

    assert "date_range_start" in "；".join(errors)
    assert "date_range_end" in "；".join(errors)


def test_business_skill_rejects_tools_outside_contract():
    library = BusinessSkillLibrary.default()

    with_tool = library.assert_tool_allowed("regular_demand_skill", "cms.query_inventory")
    assert with_tool is None

    try:
        library.assert_tool_allowed("regular_demand_skill", "memory.review_approve")
    except SkillExecutionError as exc:
        assert "不允许调用工具" in str(exc)
    else:
        raise AssertionError("unapproved tool should be rejected")


def test_agent_runs_regular_demand_skill_as_draft_and_guarded_proposal(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "skills.db"))

    result = agent.run_business_skill(
        "regular_demand_skill",
        {
            "country": "日本",
            "operation_tag": "常规_日本_传统浴袍美女0604",
            "js_category": "人物",
            "stock": 2,
            "historical_metrics": {"open_rate": 0.31, "completion_rate": 0.91},
            "delivery_constraints": "本周",
        },
        actor="jp_owner",
    )

    assert result.skill_id == "regular_demand_skill"
    assert result.country == "日本"
    assert result.human_review_required is True
    assert result.guarded_action_proposals
    assert result.draft_output["draft_rows"]
    assert result.draft_output["missing_fields"] == ()
    assert "cms.query_inventory" in result.tool_calls
    assert "feishu.write_table" not in result.tool_calls
    assert result.rag_citations
    assert result.memory_refs
    assert any(memory["memory_type"] == "regular_demand_draft" for memory in agent.repository.layered_memories("日本", layer="working"))


def test_agent_runs_value_audit_skill_with_value_and_audit_rag_sources(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "skills.db"))

    result = agent.run_business_skill(
        "value_audit_skill",
        {
            "country": "日本",
            "image_or_candidate": "寿司参考图",
            "subject": "寿司",
            "operation_tag": "试新_日本_寿司0713",
            "task_type": "value_master",
        },
        actor="jp_owner",
    )

    assert result.skill_id == "value_audit_skill"
    assert result.draft_output["sabcd_prediction"] in {"S", "A", "B", "C", "D"}
    assert "risk_points" in result.draft_output
    assert result.rag_citations
    assert result.human_review_required is True
    assert any(memory["memory_type"] == "value_audit_draft" for memory in agent.repository.layered_memories("日本", layer="working"))


def test_memory_governance_skill_suggests_without_executing_review_actions(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "skills.db"))
    memory_id = agent.record_working_memory("日本", "draft_note", {"subject": "寿司", "recommendation": "待确认"}, actor="jp_owner")

    result = agent.run_business_skill(
        "memory_governance_skill",
        {
            "country": "日本",
            "memory_ids": [memory_id],
            "conflict_group_id": "",
            "cleanup_reason": "weekly_review",
            "operator_goal": "清理低质量记忆",
        },
        actor="jp_owner",
    )
    memory = agent.repository.layered_memories("日本", include_inactive=True)[0]

    assert result.draft_output["approval_suggestions"]
    assert result.human_review_required is True
    assert result.guarded_action_proposals == ()
    assert memory["review_status"] == "draft"


def test_all_business_skills_execute_demo_cases_with_expected_rag_indexes(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "skills.db"))
    skill_inputs = {
        "weekly_review_skill": {
            "country": "日本",
            "date_range_start": "2026-06-24",
            "date_range_end": "2026-06-30",
            "history_window": "上上周三到上周二",
            "js_category": "人物",
            "operator_note": "周度复盘",
        },
        "trial_parse_skill": {
            "country": "日本",
            "reference_images": ["ref-a.png"],
            "trial_mode": "parse",
            "js_category": "人物",
            "operator_hint": "参考图解析",
        },
        "value_audit_skill": {
            "country": "日本",
            "image_or_candidate": "候选图",
            "subject": "猫咪",
            "operation_tag": "试新_日本_猫咪0713",
            "task_type": "value_master",
        },
        "memory_governance_skill": {
            "country": "日本",
            "memory_ids": [],
            "conflict_group_id": "",
            "cleanup_reason": "weekly",
            "operator_goal": "治理待审记忆",
        },
    }

    for skill in agent.business_skill_contracts():
        if skill.skill_id == "regular_demand_skill":
            continue
        result = agent.run_business_skill(skill.skill_id, skill_inputs[skill.skill_id], actor="jp_owner")
        assert result.skill_id == skill.skill_id
        assert result.country == "日本"
        assert result.human_review_required is True
        assert result.draft_output
        assert skill.rag_task_index in {"weekly_review", "value_master", "memory_governance"}


def test_business_skill_contracts_pin_rag_sources_and_memory_write_rules():
    library = BusinessSkillLibrary.default()

    assert library.get("weekly_review_skill").rag_task_index == "weekly_review"
    assert library.get("value_audit_skill").rag_task_index == "value_master"
    assert library.get("memory_governance_skill").rag_task_index == "memory_governance"
    assert library.get("trial_parse_skill").memory_write_policy["layer"] == "perception"
    assert library.get("value_audit_skill").memory_write_policy["layer"] == "working"


def test_agent_exposes_five_business_skill_acceptance_cases_for_harness(tmp_path):
    agent = PuzzleOpsAgent(repository=PuzzleRepository(tmp_path / "skills.db"))

    cases = agent.business_skill_acceptance_cases("日本")

    assert len(cases) == 5
    assert {case["skill_id"] for case in cases} == set(agent.business_skills.skill_ids())
    assert all(case["country"] == "日本" for case in cases)
    assert all(case["acceptance_metrics"] for case in cases)
    assert all(case["input_payload"] for case in cases)
