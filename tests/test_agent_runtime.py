from puzzle_ops.agents import PuzzleOpsAgent


def test_agent_builds_multimodal_profile_with_evidence_from_real_sample():
    agent = PuzzleOpsAgent()

    profile = agent.multimodal_profile("日本")

    assert profile.feature.main_subject == "猫"
    assert profile.similar_good_cases
    assert profile.similar_bad_cases
    assert "open_rate" in profile.historical_metrics


def test_agent_exposes_value_candidates_and_human_approval_memory():
    agent = PuzzleOpsAgent()
    candidate = agent.value_rule_candidates("日本")[0]

    approved = agent.approve_value_candidate(candidate.candidate_id, "日本", human_note="运营确认可用于动物类试新")

    assert approved.status == "approved"
    assert "运营确认" in agent.hitl_memories("日本")[0]["content"]
    assert any(candidate.rule_text == rule["rule_text"] for rule in agent.approved_value_rules("日本"))


def test_agent_runtime_trace_records_skill_tool_context_and_eval():
    agent = PuzzleOpsAgent()

    trace = agent.run_agent_task("日本", "value_judge")

    assert trace.task_type == "value_judge"
    assert trace.skill_name == "value_judge_skill"
    assert trace.tool_calls
    assert trace.observations
    assert trace.context_summary
    assert trace.eval_result["tool_call_success_rate"] == 1.0


def test_eval_dashboard_contains_competitive_agent_metrics():
    agent = PuzzleOpsAgent()

    dashboard = agent.eval_dashboard("日本")

    assert dashboard["工具调用成功率"] == "100%"
    assert dashboard["审核风险召回率"].endswith("%")
    assert dashboard["SABCD预测准确率"].endswith("%")
    assert dashboard["价值观候选通过率"].endswith("%")
