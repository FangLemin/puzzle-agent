from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
import json
import time
from typing import Callable
from uuid import uuid4

from puzzle_ops.models import ToolResult


@dataclass(frozen=True)
class ToolSpec:
    name: str
    display_name: str = ""
    target_system: str = ""
    side_effect: str = "read"
    approval_required: bool = False
    country_scoped: bool = False
    input_schema: dict[str, object] | None = None
    output_schema: dict[str, object] | None = None
    required_env: tuple[str, ...] = ()
    rollback_strategy: str = "manual_only"
    audit_level: str = "standard"
    allowed_skill_ids: tuple[str, ...] = ()


class ToolRegistry:
    def __init__(self, repository=None):
        self._tools: dict[str, Callable[..., object]] = {}
        self._specs: dict[str, ToolSpec] = {}
        self.repository = repository

    def register(self, name: str, func: Callable[..., object], *, spec: ToolSpec | None = None) -> None:
        self._tools[name] = func
        self._specs[name] = spec or ToolSpec(name)

    def call(self, name: str, **kwargs: object) -> ToolResult:
        started = time.perf_counter()
        invocation_id = f"tool-{uuid4().hex[:10]}"
        country = str(kwargs.get("country", ""))
        actor = str(kwargs.get("actor", ""))
        skill_id = str(kwargs.get("skill_id", ""))
        source_trace_id = str(kwargs.get("source_trace_id", ""))
        proposal_id = str(kwargs.get("approved_proposal_id") or kwargs.get("proposal_id") or "")
        spec = self._specs.get(name, ToolSpec(name))
        result: ToolResult
        if name not in self._tools:
            result = ToolResult(False, {}, f"{name} 未注册", error="TOOL_NOT_FOUND")
            self._record_invocation(invocation_id, spec, country, actor, skill_id, source_trace_id, proposal_id, kwargs, result, started)
            return result
        if spec.allowed_skill_ids and skill_id and skill_id not in spec.allowed_skill_ids:
            result = ToolResult(False, {"tool": name, "skill_id": skill_id}, f"{skill_id} 不允许调用 {name}", error="TOOL_NOT_ALLOWED")
            self._record_invocation(invocation_id, spec, country, actor, skill_id, source_trace_id, proposal_id, kwargs, result, started)
            return result
        if spec.country_scoped and not country:
            result = ToolResult(False, {"tool": name}, f"{name} 需要 country filter", error="COUNTRY_REQUIRED")
            self._record_invocation(invocation_id, spec, country, actor, skill_id, source_trace_id, proposal_id, kwargs, result, started)
            return result
        if spec.approval_required and not proposal_id:
            result = ToolResult(False, {"tool": name}, f"{name} 需要人工批准后才能执行", error="APPROVAL_REQUIRED")
            self._record_invocation(invocation_id, spec, country, actor, skill_id, source_trace_id, proposal_id, kwargs, result, started)
            return result
        try:
            kwargs.pop("approved_proposal_id", None)
            data = self._tools[name](**_call_kwargs(self._tools[name], kwargs))
            if isinstance(data, ToolResult):
                result = data
            else:
                if not isinstance(data, dict):
                    data = {"value": data}
                result = ToolResult(True, data, f"{name} 调用成功")
        except Exception as exc:
            result = ToolResult(False, {}, f"{name} 调用失败", error=str(exc))
        self._record_invocation(invocation_id, spec, country, actor, skill_id, source_trace_id, proposal_id, kwargs, result, started)
        if result.success:
            result.data.setdefault("invocation_id", invocation_id)
        return result

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs[name] for name in sorted(self._specs))

    def _record_invocation(
        self,
        invocation_id: str,
        spec: ToolSpec,
        country: str,
        actor: str,
        skill_id: str,
        source_trace_id: str,
        proposal_id: str,
        kwargs: dict[str, object],
        result: ToolResult,
        started: float,
    ) -> None:
        if self.repository is None:
            return
        recorder = getattr(self.repository, "record_tool_invocation", None)
        if recorder is None:
            return
        recorder(
            invocation_id=invocation_id,
            tool_name=spec.name,
            country=country,
            actor=actor,
            skill_id=skill_id,
            source_trace_id=source_trace_id,
            proposal_id=proposal_id,
            side_effect=spec.side_effect,
            input_hash=_payload_hash(kwargs),
            input_preview=_preview(kwargs),
            output_preview=_preview(result.data),
            success=result.success,
            error_code=result.error or "",
            error_message=result.message if not result.success else "",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    scenario: str
    required_tools: tuple[str, ...]
    instructions: str


class SkillLibrary:
    def __init__(self, skills: tuple[SkillDefinition, ...]):
        self._skills = {skill.name: skill for skill in skills}

    @classmethod
    def default(cls) -> "SkillLibrary":
        return cls(
            (
                SkillDefinition(
                    "regular_demand_skill",
                    "常规提需：基于历史爆款、低库存、国家价值观生成常规需求。",
                    ("warehouse.tag_performance", "asset.search_by_tag", "vector.search_value_master", "audit.retrieve_policy"),
                    "输入国家/分类/tag；输出格式为提需表行，备注需包含审核风险。",
                ),
                SkillDefinition(
                    "trial_demand_skill",
                    "试新提需：基于上传参考图或好图衍生图生成试新需求。",
                    ("image.extract_features", "asset.search_by_image", "asset.check_duplicate", "audit.retrieve_policy"),
                    "输入参考图；输出格式为试新提需行、主体描述、价值观匹配度。",
                ),
                SkillDefinition(
                    "value_judge_skill",
                    "价值观大师：融合图片特征、历史好坏图、审核规则和 memory。",
                    ("history.search_records", "image.extract_features", "image.retrieve_similar_good_bad", "audit.retrieve_policy"),
                    "输入 ImageProfile；输出格式包含预测等级、匹配度、证据图、风险点和修改建议。",
                ),
                SkillDefinition(
                    "value_insight_mining_skill",
                    "价值观候选挖掘：从 SA/CD 差异中生成候选价值观。",
                    ("history.search_records", "memory.write", "value_rule.create_candidate"),
                    "输入国家与周期；输出格式包含候选规则、置信度、支撑样本、反例样本。",
                ),
                SkillDefinition(
                    "analysis_skill",
                    "周三数据分析：输出指标、异常原因、todo 和人工可编辑备注。",
                    ("history.aggregate_metrics", "memory.retrieve"),
                    "输入国家与周期；输出格式包含 SA/CD/AI 趋势、明细备注和下一步建议。",
                ),
            )
        )

    def get(self, name: str) -> SkillDefinition:
        return self._skills[name]

    def all(self) -> tuple[SkillDefinition, ...]:
        return tuple(self._skills.values())


def _call_kwargs(func: Callable[..., object], kwargs: dict[str, object]) -> dict[str, object]:
    signature = inspect.signature(func)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in signature.parameters}


def _payload_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _preview(payload: dict[str, object]) -> dict[str, object]:
    preview: dict[str, object] = {}
    for key, value in payload.items():
        if key in {"rows", "items"} and isinstance(value, list):
            preview[key] = {"count": len(value), "first": value[0] if value else {}}
        elif key in {"payload", "image_bytes"}:
            preview[key] = "<omitted>"
        else:
            preview[key] = value
        if len(preview) >= 12:
            break
    return preview
