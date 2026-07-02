from __future__ import annotations

from puzzle_ops.cms import MockCMSClient
from puzzle_ops.runtime import ToolRegistry


class MCPToolAdapter:
    """MCP-like local adapter: protocol-shaped tools, local implementations."""

    def __init__(self):
        self.registry = ToolRegistry()
        self._tools: dict[str, str] = {}

    def register_cms(self, cms: MockCMSClient) -> None:
        self.registry.register("cms.query_inventory", cms.query_inventory)
        self.registry.register("cms.search_assets", cms.search_assets)
        self.registry.register("cms.low_stock_tags", cms.low_stock_tags)
        self._tools.update(
            {
                "cms.query_inventory": "查询 CMS 全局未分发素材库中某个运营 tag 的库存。",
                "cms.search_assets": "按国家/JS分类检索 CMS Mock 素材。",
                "cms.low_stock_tags": "返回库存低于阈值的运营 tag。",
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
