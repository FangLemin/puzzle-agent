from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from puzzle_ops.models import ToolResult


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Callable[..., object]] = {}

    def register(self, name: str, func: Callable[..., object]) -> None:
        self._tools[name] = func

    def call(self, name: str, **kwargs: object) -> ToolResult:
        if name not in self._tools:
            return ToolResult(False, {}, f"{name} 未注册", error="TOOL_NOT_FOUND")
        try:
            data = self._tools[name](**kwargs)
            if isinstance(data, ToolResult):
                return data
            if not isinstance(data, dict):
                data = {"value": data}
            return ToolResult(True, data, f"{name} 调用成功")
        except Exception as exc:
            return ToolResult(False, {}, f"{name} 调用失败", error=str(exc))


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
                    ("history.search_records", "cms.query_inventory", "image.retrieve_similar_good_bad", "audit.retrieve_policy"),
                    "输入国家/分类/tag；输出格式为提需表行，备注需包含审核风险。",
                ),
                SkillDefinition(
                    "trial_demand_skill",
                    "试新提需：基于上传参考图或好图衍生图生成试新需求。",
                    ("image.extract_features", "image.retrieve_similar_good_bad", "audit.retrieve_policy"),
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
