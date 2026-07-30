from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SkillExecutionError(ValueError):
    pass


@dataclass(frozen=True)
class BusinessSkillDefinition:
    skill_id: str
    display_name: str
    scenario: str
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    allowed_tools: tuple[str, ...]
    guarded_tools: tuple[str, ...]
    rag_task_index: str
    rag_source_types: tuple[str, ...]
    memory_read_layers: tuple[str, ...]
    memory_write_policy: dict[str, object]
    human_approval_policy: str
    acceptance_metrics: tuple[str, ...]


@dataclass(frozen=True)
class SkillRunResult:
    skill_id: str
    country: str
    input_payload: dict[str, object]
    draft_output: dict[str, object]
    rag_citations: tuple[str, ...] = ()
    memory_refs: tuple[str, ...] = ()
    tool_calls: tuple[str, ...] = ()
    guarded_action_proposals: tuple[str, ...] = ()
    human_review_required: bool = True
    quality_scores: dict[str, float] = field(default_factory=dict)
    failure_reasons: tuple[str, ...] = ()


class BusinessSkillLibrary:
    def __init__(self, skills: tuple[BusinessSkillDefinition, ...]):
        self._skills = {skill.skill_id: skill for skill in skills}

    @classmethod
    def default(cls) -> "BusinessSkillLibrary":
        return cls(
            (
                _weekly_review_skill(),
                _regular_demand_skill(),
                _trial_parse_skill(),
                _value_audit_skill(),
                _memory_governance_skill(),
            )
        )

    def get(self, skill_id: str) -> BusinessSkillDefinition:
        try:
            return self._skills[skill_id]
        except KeyError as exc:
            raise SkillExecutionError(f"未知业务 Skill：{skill_id}") from exc

    def all(self) -> tuple[BusinessSkillDefinition, ...]:
        return tuple(self._skills[skill_id] for skill_id in self.skill_ids())

    def skill_ids(self) -> tuple[str, ...]:
        return tuple(self._skills.keys())

    def validate_input(self, skill_id: str, payload: dict[str, object]) -> tuple[str, ...]:
        skill = self.get(skill_id)
        schema = skill.input_schema
        required = schema.get("required", ())
        errors = [f"缺少必填字段：{field}" for field in required if _missing(payload.get(str(field)))]
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for field, spec in properties.items():
                if field not in payload or _missing(payload.get(field)) or not isinstance(spec, dict):
                    continue
                expected = spec.get("type")
                if expected and not _matches_type(payload[field], str(expected)):
                    errors.append(f"字段类型不匹配：{field} 应为 {expected}")
        return tuple(errors)

    def validate_output(self, skill_id: str, payload: dict[str, object]) -> tuple[str, ...]:
        skill = self.get(skill_id)
        required = skill.output_schema.get("required", ())
        return tuple(f"缺少输出字段：{field}" for field in required if _missing(payload.get(str(field))))

    def assert_tool_allowed(self, skill_id: str, tool_name: str) -> None:
        skill = self.get(skill_id)
        if tool_name not in skill.allowed_tools and tool_name not in skill.guarded_tools:
            raise SkillExecutionError(f"{skill.display_name} 不允许调用工具：{tool_name}")


def _schema(required: tuple[str, ...], properties: dict[str, str]) -> dict[str, object]:
    return {
        "type": "object",
        "required": required,
        "properties": {key: {"type": value} for key, value in properties.items()},
    }


def _weekly_review_skill() -> BusinessSkillDefinition:
    return BusinessSkillDefinition(
        skill_id="weekly_review_skill",
        display_name="周三复盘 Skill",
        scenario="上上周三到上周二数据复盘，输出增长、风险、国家差异和提需方向。",
        input_schema=_schema(
            ("country", "date_range_start", "date_range_end", "history_window", "js_category"),
            {"country": "string", "date_range_start": "string", "date_range_end": "string", "history_window": "string", "js_category": "string", "operator_note": "string"},
        ),
        output_schema=_schema(
            ("sa_growth", "cd_risks", "country_differences", "need_directions"),
            {"sa_growth": "array", "cd_risks": "array", "country_differences": "array", "need_directions": "array", "action_proposals": "array"},
        ),
        allowed_tools=("warehouse.weekly_metrics", "warehouse.tag_performance", "vector.search_memory_facts", "history.aggregate_metrics", "rag.retrieve.weekly_review", "memory.retrieve"),
        guarded_tools=(),
        rag_task_index="weekly_review",
        rag_source_types=("sample_fact", "harness_gold_sample", "value_rule", "approved_value_rule", "approved_rag_patch", "fact"),
        memory_read_layers=("facts", "long_term"),
        memory_write_policy={"layer": "working", "memory_type": "weekly_review_insight", "approved_for_rag": False},
        human_approval_policy="生成提需方向后由运营确认；飞书写入另走 Guarded Action。",
        acceptance_metrics=("S/A召回准确率", "C/D风险漏召回率", "推荐提需采纳率", "RAG citation precision"),
    )


def _regular_demand_skill() -> BusinessSkillDefinition:
    return BusinessSkillDefinition(
        skill_id="regular_demand_skill",
        display_name="常规提需 Skill",
        scenario="基于国家、运营 tag、库存和历史表现生成飞书字段完整的常规提需草案。",
        input_schema=_schema(
            ("country", "operation_tag", "js_category", "stock", "historical_metrics"),
            {"country": "string", "operation_tag": "string", "js_category": "string", "stock": "number", "historical_metrics": "object", "delivery_constraints": "string"},
        ),
        output_schema=_schema(
            ("draft_rows", "missing_fields", "risk_notes", "value_evidence"),
            {"draft_rows": "array", "missing_fields": "array", "risk_notes": "array", "value_evidence": "array", "action_proposals": "array"},
        ),
        allowed_tools=("warehouse.tag_performance", "asset.search_by_tag", "vector.search_value_master", "history.search_records", "rag.retrieve.value_master"),
        guarded_tools=("feishu.write_table",),
        rag_task_index="value_master",
        rag_source_types=("value_rule", "approved_value_rule", "approved_rag_patch", "audit_policy", "fact", "harness_gold_sample"),
        memory_read_layers=("facts", "long_term"),
        memory_write_policy={"layer": "working", "memory_type": "regular_demand_draft", "approved_for_rag": False},
        human_approval_policy="提需草稿可编辑；写飞书必须生成 Guarded Action 并人工确认。",
        acceptance_metrics=("飞书字段完整率", "低库存爆款命中率", "工具调用成功率", "同步 proposal 审计完整率"),
    )


def _trial_parse_skill() -> BusinessSkillDefinition:
    return BusinessSkillDefinition(
        skill_id="trial_parse_skill",
        display_name="试新解析 Skill",
        scenario="解析 1 张或多张参考图，输出共同主体、色彩、构图、运营 tag 和试新提需描述。",
        input_schema=_schema(
            ("country", "reference_images", "trial_mode", "js_category"),
            {"country": "string", "reference_images": "array", "trial_mode": "string", "js_category": "string", "operator_hint": "string"},
        ),
        output_schema=_schema(
            ("common_subject", "color_mood", "composition", "operation_tag", "draft_rows"),
            {"common_subject": "string", "color_mood": "string", "composition": "string", "operation_tag": "string", "draft_rows": "array", "risk_notes": "array"},
        ),
        allowed_tools=("image.extract_features", "asset.search_by_image", "asset.check_duplicate", "vector.search_audit_rules", "rag.retrieve.value_master", "rag.retrieve.audit"),
        guarded_tools=("feishu.write_table", "asset.upload_reference", "asset.attach_to_feishu_payload"),
        rag_task_index="value_master",
        rag_source_types=("value_rule", "approved_value_rule", "approved_rag_patch", "audit_policy", "fact", "harness_gold_sample"),
        memory_read_layers=("facts", "long_term"),
        memory_write_policy={"layer": "perception", "memory_type": "trial_image_parse", "approved_for_rag": False},
        human_approval_policy="生成图、附件和飞书同步均需二审与运营确认。",
        acceptance_metrics=("主体识别准确率", "色彩/构图匹配率", "试新提需字段完整率", "生成图二审通过率"),
    )


def _value_audit_skill() -> BusinessSkillDefinition:
    return BusinessSkillDefinition(
        skill_id="value_audit_skill",
        display_name="价值观审核 Skill",
        scenario="对素材图或候选图输出 SABCD 预测、RAG citation、风险点和人工复核建议。",
        input_schema=_schema(
            ("country", "image_or_candidate", "subject", "operation_tag", "task_type"),
            {"country": "string", "image_or_candidate": "string", "subject": "string", "operation_tag": "string", "task_type": "string"},
        ),
        output_schema=_schema(
            ("sabcd_prediction", "rag_citations", "risk_points", "human_review_suggestion"),
            {"sabcd_prediction": "string", "rag_citations": "array", "risk_points": "array", "revision_suggestions": "array", "human_review_suggestion": "string"},
        ),
        allowed_tools=("image.audit_value_fit", "image.detect_ip_risk", "vector.search_value_master", "vector.search_audit_rules", "rag.retrieve.value_master", "rag.retrieve.audit", "memory.retrieve", "history.search_records", "audit.retrieve_policy"),
        guarded_tools=(),
        rag_task_index="value_master",
        rag_source_types=("value_rule", "approved_value_rule", "approved_rag_patch", "audit_policy", "fact", "harness_gold_sample"),
        memory_read_layers=("facts", "long_term"),
        memory_write_policy={"layer": "working", "memory_type": "value_audit_draft", "approved_for_rag": False},
        human_approval_policy="只生成审核建议，不直接批准素材；人工确认后才可沉淀 facts。",
        acceptance_metrics=("S/A预测准确率", "RAG citation precision", "国家文化风险漏召回率", "人工复核建议采纳率"),
    )


def _memory_governance_skill() -> BusinessSkillDefinition:
    return BusinessSkillDefinition(
        skill_id="memory_governance_skill",
        display_name="Memory 治理 Skill",
        scenario="对待审 memory、冲突组和低质量建议输出批准、驳回、合并、停用建议。",
        input_schema=_schema(
            ("country", "operator_goal"),
            {"country": "string", "memory_ids": "array", "conflict_group_id": "string", "cleanup_reason": "string", "operator_goal": "string"},
        ),
        output_schema=_schema(
            ("approval_suggestions", "reject_suggestions", "merge_suggestions", "retire_suggestions", "provenance_explanation"),
            {"approval_suggestions": "array", "reject_suggestions": "array", "merge_suggestions": "array", "retire_suggestions": "array", "provenance_explanation": "string", "rag_impact": "string"},
        ),
        allowed_tools=("memory.workbench", "memory.conflicts", "memory.provenance", "vector.search_memory_facts", "memory.rag_stats", "rag.retrieve.memory_governance"),
        guarded_tools=(),
        rag_task_index="memory_governance",
        rag_source_types=("memory_perception", "memory_working", "approved_value_rule", "fact"),
        memory_read_layers=("perception", "working", "long_term", "facts"),
        memory_write_policy={"layer": "working", "memory_type": "memory_governance_suggestion", "approved_for_rag": False},
        human_approval_policy="治理建议不直接执行；approve/reject/merge/retire 继续走页面 HITL。",
        acceptance_metrics=("冲突识别准确率", "低质量清理 precision", "治理建议采纳率", "RAG 污染拦截率"),
    )


def _missing(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _matches_type(value: object, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, (list, tuple))
    if expected == "object":
        return isinstance(value, dict)
    if expected == "number":
        return isinstance(value, (int, float))
    if expected == "boolean":
        return isinstance(value, bool)
    return True
