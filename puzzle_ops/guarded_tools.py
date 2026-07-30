from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from puzzle_ops.models import ToolResult


GUARD_STATUSES = {"draft", "blocked", "pending_approval", "approved", "executed", "failed", "reverted"}


@dataclass(frozen=True)
class ActionProposal:
    proposal_id: str
    country: str
    actor: str
    target_system: str
    action_type: str
    payload: dict[str, object]
    payload_preview: dict[str, object]
    source_trace_id: str
    risk_level: str
    guard_status: str
    guard_reasons: tuple[str, ...]
    rollback_strategy: str
    created_at: str
    approved_by: str = ""
    approved_at: str = ""
    executed_at: str = ""
    execution_result: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class GuardDecision:
    status: str
    reasons: tuple[str, ...]
    risk_level: str
    rollback_strategy: str
    payload_preview: dict[str, object]


class GuardedToolPolicy:
    def __init__(self, writable_countries: tuple[str, ...] = ()):
        self.writable_countries = tuple(writable_countries)

    def evaluate(self, country: str, action_type: str, payload: dict[str, object], *, actor: str = "") -> GuardDecision:
        reasons: list[str] = []
        if self.writable_countries and country not in self.writable_countries:
            reasons.append(f"当前用户对 {country} 没有写权限")
        risk_level = str(payload.get("risk_level") or "low")
        if action_type == "feishu.write_table":
            reasons.extend(_feishu_payload_reasons(country, payload))
        status = "blocked" if reasons else "pending_approval"
        return GuardDecision(
            status=status,
            reasons=tuple(reasons),
            risk_level=risk_level if risk_level in {"low", "medium", "high"} else "medium",
            rollback_strategy=_rollback_strategy(action_type, payload),
            payload_preview=_payload_preview(action_type, payload),
        )

    def approval_reasons(self, proposal: ActionProposal, *, actor: str, note: str) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.writable_countries and proposal.country not in self.writable_countries:
            reasons.append(f"当前用户对 {proposal.country} 没有写权限")
        if proposal.risk_level == "high" and not note.strip():
            reasons.append("高风险 action 必须填写人工确认备注")
        if proposal.guard_status not in {"pending_approval", "blocked", "failed"}:
            reasons.append(f"当前状态不能批准：{proposal.guard_status}")
        return tuple(reasons)

    def execution_reasons(self, proposal: ActionProposal, *, actor: str) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.writable_countries and proposal.country not in self.writable_countries:
            reasons.append(f"当前用户对 {proposal.country} 没有写权限")
        if proposal.guard_status != "approved":
            reasons.append("必须先人工批准，才能执行外部写入")
        return tuple(reasons)


class GuardedToolExecutor:
    def __init__(self, repository, *, tools: dict[str, object], policy: GuardedToolPolicy):
        self.repository = repository
        self.tools = tools
        self.policy = policy

    def propose(self, country, actor, target_system, action_type, payload, source_trace_id) -> ActionProposal:
        decision = self.policy.evaluate(str(country), str(action_type), dict(payload), actor=str(actor))
        proposal = ActionProposal(
            proposal_id=f"gap-{uuid4().hex[:10]}",
            country=str(country),
            actor=str(actor),
            target_system=str(target_system),
            action_type=str(action_type),
            payload=dict(payload),
            payload_preview=decision.payload_preview,
            source_trace_id=str(source_trace_id),
            risk_level=decision.risk_level,
            guard_status=decision.status,
            guard_reasons=decision.reasons,
            rollback_strategy=decision.rollback_strategy,
            created_at=_now(),
        )
        self.repository.save_guarded_action_proposal(proposal)
        self.repository.record_guarded_action_event(
            proposal.proposal_id,
            proposal.country,
            str(actor),
            "create",
            "",
            proposal.guard_status,
            {"guard_reasons": proposal.guard_reasons},
        )
        return proposal

    def approve(self, proposal_id, actor, note) -> ActionProposal:
        proposal = self.repository.guarded_action_proposal(str(proposal_id))
        if proposal is None:
            raise ValueError(f"guarded action proposal 不存在：{proposal_id}")
        reasons = self.policy.approval_reasons(proposal, actor=str(actor), note=str(note))
        if reasons:
            updated = replace(proposal, guard_reasons=tuple(dict.fromkeys((*proposal.guard_reasons, *reasons))))
            self.repository.save_guarded_action_proposal(updated)
            return updated
        updated = replace(
            proposal,
            guard_status="approved",
            guard_reasons=(),
            approved_by=str(actor),
            approved_at=_now(),
            execution_result={**proposal.execution_result, "approval_note": str(note)},
        )
        self.repository.save_guarded_action_proposal(updated)
        self.repository.record_guarded_action_event(updated.proposal_id, updated.country, str(actor), "approve", proposal.guard_status, "approved", {"note": str(note)})
        return updated

    def execute(self, proposal_id, actor) -> ToolResult:
        proposal = self.repository.guarded_action_proposal(str(proposal_id))
        if proposal is None:
            return ToolResult(False, {}, f"proposal 不存在：{proposal_id}", error="PROPOSAL_NOT_FOUND")
        reasons = self.policy.execution_reasons(proposal, actor=str(actor))
        if reasons:
            return ToolResult(False, {"proposal_id": proposal.proposal_id, "reasons": reasons}, "Guarded action 尚未获准执行", error="APPROVAL_REQUIRED")
        tool = self.tools.get(proposal.action_type)
        if tool is None:
            return ToolResult(False, {"proposal_id": proposal.proposal_id}, f"{proposal.action_type} 未注册", error="TOOL_NOT_FOUND")
        try:
            result = tool(approved_proposal_id=proposal.proposal_id, **proposal.payload)
        except Exception as exc:
            result = ToolResult(False, {"proposal_id": proposal.proposal_id}, f"{proposal.action_type} 执行失败", error=str(exc))
        status = "executed" if result.success else "failed"
        recorder = getattr(self.repository, "record_tool_invocation", None)
        if recorder is not None:
            recorder(
                invocation_id=f"tool-{uuid4().hex[:10]}",
                tool_name=proposal.action_type,
                country=proposal.country,
                actor=str(actor),
                skill_id=str(proposal.payload.get("skill_id", "")),
                source_trace_id=proposal.source_trace_id,
                proposal_id=proposal.proposal_id,
                side_effect="external_write",
                input_hash=_payload_hash(proposal.payload),
                input_preview=proposal.payload_preview,
                output_preview=result.data,
                success=result.success,
                error_code=result.error or "",
                error_message=result.message if not result.success else "",
                latency_ms=0,
            )
        updated = replace(
            proposal,
            guard_status=status,
            executed_at=_now(),
            execution_result={
                "success": result.success,
                "data": result.data,
                "message": result.message,
                "error": result.error or "",
            },
        )
        self.repository.save_guarded_action_proposal(updated)
        self.repository.record_guarded_action_event(updated.proposal_id, updated.country, str(actor), "execute", proposal.guard_status, status, updated.execution_result)
        return result

    def revert(self, proposal_id, actor, note) -> ToolResult:
        proposal = self.repository.guarded_action_proposal(str(proposal_id))
        if proposal is None:
            return ToolResult(False, {}, f"proposal 不存在：{proposal_id}", error="PROPOSAL_NOT_FOUND")
        data = proposal.execution_result.get("data", {})
        if isinstance(data, dict):
            path = data.get("path")
            if proposal.rollback_strategy == "delete_created_rows" and path:
                target = Path(str(path))
                if target.exists():
                    target.unlink()
        updated = replace(
            proposal,
            guard_status="reverted",
            execution_result={**proposal.execution_result, "revert_note": str(note), "reverted_by": str(actor)},
        )
        self.repository.save_guarded_action_proposal(updated)
        self.repository.record_guarded_action_event(updated.proposal_id, updated.country, str(actor), "revert", proposal.guard_status, "reverted", {"note": str(note)})
        return ToolResult(True, {"proposal_id": proposal.proposal_id, "rollback_strategy": proposal.rollback_strategy}, "Guarded action 已撤销或标记需人工处理")


def _feishu_payload_reasons(country: str, payload: dict[str, object]) -> tuple[str, ...]:
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return ("飞书 payload 不能为空",)
    required = ("提需分类", "国家", "JS分类", "运营tag", "主体内容", "张数", "需求等级", "加工方式")
    reasons: list[str] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            reasons.append(f"第 {index} 行不是有效对象")
            continue
        if str(row.get("国家", "")) != country:
            reasons.append(f"第 {index} 行国家不一致：{row.get('国家', '')}")
        missing = [field for field in required if row.get(field) in {"", None}]
        if missing:
            reasons.append(f"第 {index} 行缺少字段：{','.join(missing)}")
        if row.get("_reference_image_syncable") is False:
            reasons.append(f"第 {index} 行生成图尚未二审和运营确认")
    return tuple(reasons)


def _payload_preview(action_type: str, payload: dict[str, object]) -> dict[str, object]:
    rows = payload.get("rows")
    row_count = len(rows) if isinstance(rows, list) else 0
    first_row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    return {
        "action_type": action_type,
        "table_name": str(payload.get("table_name", "")),
        "row_count": row_count,
        "fields": tuple(first_row.keys())[:12] if isinstance(first_row, dict) else (),
        "first_operation_tag": str(first_row.get("运营tag", "")) if isinstance(first_row, dict) else "",
    }


def _rollback_strategy(action_type: str, payload: dict[str, object]) -> str:
    if action_type == "feishu.write_table" and payload.get("mode") == "mock":
        return "delete_created_rows"
    if action_type == "feishu.write_table":
        return "manual_only"
    return "manual_only"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _payload_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
