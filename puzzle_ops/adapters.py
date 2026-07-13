from __future__ import annotations

import json
import re

from puzzle_ops.cms import MockCMSClient
from puzzle_ops.runtime import ToolRegistry, ToolSpec


class MCPToolAdapter:
    """MCP-like local adapter: protocol-shaped tools, local implementations."""

    def __init__(self):
        self.registry = ToolRegistry()
        self._tools: dict[str, str] = {}

    def register_cms(self, cms: MockCMSClient) -> None:
        self.registry.register("cms.query_inventory", cms.query_inventory, spec=ToolSpec("cms.query_inventory", side_effect="read"))
        self.registry.register("cms.search_assets", cms.search_assets, spec=ToolSpec("cms.search_assets", side_effect="read"))
        self.registry.register("cms.low_stock_tags", cms.low_stock_tags, spec=ToolSpec("cms.low_stock_tags", side_effect="read"))
        self.registry.register(
            "cms.upload_asset",
            lambda **_: {"status": "not_implemented"},
            spec=ToolSpec("cms.upload_asset", side_effect="external_write", approval_required=True),
        )
        self.registry.register(
            "feishu.write_table",
            lambda **_: {"status": "guarded_executor_required"},
            spec=ToolSpec("feishu.write_table", side_effect="external_write", approval_required=True),
        )
        self._tools.update(
            {
                "cms.query_inventory": "查询 CMS 全局未分发素材库中某个运营 tag 的库存。",
                "cms.search_assets": "按国家/JS分类检索 CMS Mock 素材。",
                "cms.low_stock_tags": "返回库存低于阈值的运营 tag。",
                "cms.upload_asset": "上传素材到 CMS，必须通过 Guarded Action 人工批准。",
                "feishu.write_table": "写入飞书提需表，必须通过 Guarded Action 人工批准。",
            }
        )

    def manifest(self) -> dict[str, object]:
        return {
            "name": "puzzle_ops_mcp_like_adapter",
            "version": "0.1",
            "tools": self._tools,
        }


class PhoenixExporter:
    def export(self, run) -> dict[str, object]:
        return {
            "project_name": "puzzle_ops_agent_harness",
            "run_id": run.run_id,
            "dataset_name": run.dataset_name,
            "rag_trace_artifacts": run.rag_trace_artifacts,
            "traces": [
                {
                    "trace_id": f"{run.run_id}:{case.sample_id}:{case.task_type}",
                    "span_name": case.task_type,
                    "input": case.input_payload,
                    "output": case.agent_output,
                    "tool_calls": case.tool_calls,
                    "scores": case.scores,
                    "failure_reasons": case.failure_reasons,
                    "rag_trace_id": _case_rag_trace(case).get("rag_trace_id", ""),
                    "rag_trace_path": _case_rag_trace(case).get("rag_trace_path", ""),
                }
                for case in run.cases
            ],
        }


class DeepEvalAdapter:
    def export(self, run) -> dict[str, object]:
        return {
            "dataset": run.dataset_name,
            "pytest_hint": "assert metric.score >= threshold for each PuzzleOps Harness case",
            "test_cases": [
                {
                    "name": f"{case.sample_id}-{case.task_type}",
                    "input": case.input_payload,
                    "actual_output": case.agent_output,
                    "expected_output": "符合 PuzzleOps gold label 与业务规则",
                    "metrics": case.scores,
                }
                for case in run.cases
            ],
        }


class PromptfooExporter:
    def export(self, run) -> dict[str, object]:
        return {
            "description": f"PuzzleOps prompt/model comparison for {run.dataset_name}",
            "providers": ["qwen-vl", "openai-compatible-vlm"],
            "prompts": ["trial_parse_prompt", "value_match_prompt", "audit_prompt"],
            "metadata": {
                "run_id": run.run_id,
                "dataset_name": run.dataset_name,
                "rag_trace_artifacts": run.rag_trace_artifacts,
            },
            "tests": [
                {
                    "vars": case.input_payload,
                    "assert": [
                        {"type": "contains", "value": "主体内容"}
                        if case.task_type == "trial_parse_eval"
                        else {"type": "javascript", "value": "output.length > 0"}
                    ],
                }
                for case in run.cases
            ],
        }

    def export_yaml(self, run) -> str:
        return to_simple_yaml(self.export(run))


class ArgillaExporter:
    def export(self, run) -> dict[str, object]:
        return {
            "workspace": "puzzle_ops_hitl",
            "dataset": run.dataset_name,
            "records": [
                {
                    "id": f"{case.sample_id}-{case.task_type}",
                    "fields": {
                        "sample_id": case.sample_id,
                        "task_type": case.task_type,
                        "agent_output": case.agent_output,
                        "failure_reasons": "；".join(case.failure_reasons),
                    },
                    "questions": ("主体是否准确", "色彩氛围是否准确", "构图环境是否准确", "风险是否漏召回"),
                }
                for case in run.cases
            ],
        }


class LabelStudioExporter(ArgillaExporter):
    def export(self, run) -> dict[str, object]:
        payload = super().export(run)
        payload["project"] = "PuzzleOps HITL Label Studio"
        return payload


def _case_rag_trace(case) -> dict[str, object]:
    evidence = getattr(case, "evidence_trace", {})
    return evidence if isinstance(evidence, dict) else {}


def to_simple_yaml(value: object, indent: int = 0) -> str:
    lines = _yaml_lines(value, indent)
    return "\n".join(lines) + "\n"


def _yaml_lines(value: object, indent: int) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            rendered_key = _yaml_key(str(key))
            if isinstance(item, (dict, list, tuple)):
                lines.append(f"{prefix}{rendered_key}:")
                lines.extend(_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}{rendered_key}: {_yaml_scalar(item)}")
        return lines
    if isinstance(value, (list, tuple)):
        lines = []
        for item in value:
            if isinstance(item, dict):
                lines.append(f"{prefix}-")
                lines.extend(_yaml_lines(item, indent + 2))
            elif isinstance(item, (list, tuple)):
                lines.append(f"{prefix}-")
                lines.extend(_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
        return lines
    return [f"{prefix}{_yaml_scalar(value)}"]


def _yaml_scalar(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_key(value: str) -> str:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", value):
        return value
    return json.dumps(value, ensure_ascii=False)
