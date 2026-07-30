from puzzle_ops.guarded_tools import GuardedToolExecutor, GuardedToolPolicy
from puzzle_ops.models import ToolResult
from puzzle_ops.runtime import ToolRegistry, ToolSpec
from puzzle_ops.storage import PuzzleRepository


def _valid_feishu_payload(country: str = "日本") -> dict[str, object]:
    return {
        "table_name": "提需表",
        "rows": [
            {
                "提需分类": "常规",
                "国家": country,
                "JS分类": "人物",
                "图片本身": "猫咪",
                "运营tag": "常规_日本_猫咪0713",
                "主体内容": "猫咪",
                "张数": 7,
                "需求等级": "P1",
                "加工方式": "纯AI",
                "交付日期": "",
                "主体描述": "主体内容：猫咪；色彩氛围：清爽；构图环境：庭院。",
                "备注": "人工确认。",
            }
        ],
    }


def test_tool_registry_blocks_external_write_without_approved_proposal(tmp_path):
    repository = PuzzleRepository(tmp_path / "tools.db")
    registry = ToolRegistry(repository=repository)
    registry.register(
        "feishu.write_table",
        lambda table_name, rows: {"row_count": len(rows), "table_name": table_name},
        spec=ToolSpec(
            "feishu.write_table",
            display_name="写入飞书",
            target_system="feishu",
            side_effect="external_write",
            approval_required=True,
            allowed_skill_ids=("regular_demand_skill",),
        ),
    )

    blocked = registry.call(
        "feishu.write_table",
        country="日本",
        actor="jp_owner",
        skill_id="regular_demand_skill",
        table_name="提需表",
        rows=[{"国家": "日本"}],
    )
    allowed = registry.call(
        "feishu.write_table",
        country="日本",
        actor="jp_owner",
        skill_id="regular_demand_skill",
        table_name="提需表",
        rows=[{"国家": "日本"}],
        approved_proposal_id="gap-1",
    )
    invocations = repository.tool_invocations(country="日本")

    assert not blocked.success
    assert blocked.error == "APPROVAL_REQUIRED"
    assert allowed.success
    assert allowed.data["row_count"] == 1
    assert [item["tool_name"] for item in invocations] == ["feishu.write_table", "feishu.write_table"]
    assert invocations[0]["proposal_id"] == "gap-1"
    assert invocations[1]["success"] is False


def test_tool_registry_rejects_tools_outside_skill_contract_and_audits_read_calls(tmp_path):
    repository = PuzzleRepository(tmp_path / "tools.db")
    registry = ToolRegistry(repository=repository)
    registry.register(
        "warehouse.tag_performance",
        lambda country, operation_tag: {"country": country, "operation_tag": operation_tag, "sa_rate": 0.42},
        spec=ToolSpec(
            "warehouse.tag_performance",
            display_name="Tag 表现",
            target_system="warehouse",
            country_scoped=True,
            allowed_skill_ids=("regular_demand_skill",),
        ),
    )

    rejected = registry.call(
        "warehouse.tag_performance",
        country="日本",
        actor="jp_owner",
        skill_id="trial_parse_skill",
        operation_tag="常规_日本_猫咪0713",
    )
    allowed = registry.call(
        "warehouse.tag_performance",
        country="日本",
        actor="jp_owner",
        skill_id="regular_demand_skill",
        operation_tag="常规_日本_猫咪0713",
    )
    invocations = repository.tool_invocations(country="日本")

    assert rejected.error == "TOOL_NOT_ALLOWED"
    assert allowed.success
    assert invocations[0]["tool_name"] == "warehouse.tag_performance"
    assert invocations[0]["skill_id"] == "regular_demand_skill"
    assert invocations[1]["success"] is False


def test_guarded_policy_blocks_missing_feishu_fields_and_country_mismatch():
    policy = GuardedToolPolicy(writable_countries=("日本",))
    missing_payload = _valid_feishu_payload()
    missing_payload["rows"][0]["运营tag"] = ""
    mismatch_payload = _valid_feishu_payload(country="法国")

    missing = policy.evaluate("日本", "feishu.write_table", missing_payload, actor="jp_owner")
    mismatch = policy.evaluate("日本", "feishu.write_table", mismatch_payload, actor="jp_owner")

    assert missing.status == "blocked"
    assert any("运营tag" in reason for reason in missing.reasons)
    assert mismatch.status == "blocked"
    assert any("国家不一致" in reason for reason in mismatch.reasons)


def test_guarded_executor_records_proposal_approval_execution_and_revert_events(tmp_path):
    repository = PuzzleRepository(tmp_path / "guarded.db")
    calls = []

    def write_table(table_name, rows, approved_proposal_id=""):
        calls.append((table_name, rows, approved_proposal_id))
        return ToolResult(True, {"mode": "mock", "path": str(tmp_path / "feishu.csv"), "row_count": len(rows)}, "ok")

    executor = GuardedToolExecutor(
        repository,
        tools={"feishu.write_table": write_table},
        policy=GuardedToolPolicy(writable_countries=("日本",)),
    )

    proposal = executor.propose(
        "日本",
        "jp_owner",
        "feishu",
        "feishu.write_table",
        _valid_feishu_payload(),
        "trace-1",
    )
    approved = executor.approve(proposal.proposal_id, "jp_owner", "确认写入")
    result = executor.execute(proposal.proposal_id, "jp_owner")
    reverted = executor.revert(proposal.proposal_id, "jp_owner", "测试撤销")
    events = repository.guarded_action_events(proposal.proposal_id)

    assert proposal.guard_status == "pending_approval"
    assert approved.guard_status == "approved"
    assert result.success
    assert calls[0][2] == proposal.proposal_id
    assert reverted.success
    assert repository.guarded_action_proposal(proposal.proposal_id).guard_status == "reverted"
    assert [event["event_type"] for event in events] == ["create", "approve", "execute", "revert"]


def test_high_risk_proposal_requires_human_note_before_approval(tmp_path):
    repository = PuzzleRepository(tmp_path / "guarded.db")
    executor = GuardedToolExecutor(
        repository,
        tools={"feishu.write_table": lambda **kwargs: ToolResult(True, {}, "ok")},
        policy=GuardedToolPolicy(writable_countries=("日本",)),
    )
    payload = _valid_feishu_payload()
    payload["risk_level"] = "high"

    proposal = executor.propose("日本", "jp_owner", "feishu", "feishu.write_table", payload, "trace-2")
    rejected = executor.approve(proposal.proposal_id, "jp_owner", "")
    approved = executor.approve(proposal.proposal_id, "jp_owner", "高风险已复核")

    assert rejected.guard_status == "pending_approval"
    assert any("高风险" in reason for reason in rejected.guard_reasons)
    assert approved.guard_status == "approved"
