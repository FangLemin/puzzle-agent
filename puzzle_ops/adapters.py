from __future__ import annotations

import json
import re

from puzzle_ops.runtime import ToolRegistry, ToolSpec


class MCPToolAdapter:
    """MCP-like local adapter: protocol-shaped tools, local implementations."""

    def __init__(self, repository=None):
        self.repository = repository
        self.registry = ToolRegistry(repository=repository)
        self._tools: dict[str, str] = {}

    def manifest(self) -> dict[str, object]:
        return {
            "name": "puzzle_ops_mcp_like_adapter",
            "version": "0.2",
            "tools": self._tools,
        }

    def register_production_tools(self, default_country: str = "") -> None:
        self._register_asset_tools(default_country)
        self._register_warehouse_tools(default_country)
        self._register_vector_tools(default_country)
        self._register_image_tools(default_country)
        self._register_memory_tools(default_country)
        self.registry.register(
            "feishu.write_table",
            lambda **_: {"status": "guarded_executor_required"},
            spec=ToolSpec(
                "feishu.write_table",
                display_name="写入飞书提需表",
                target_system="feishu",
                country_scoped=True,
                side_effect="external_write",
                approval_required=True,
                input_schema={"required": ("table_name", "rows")},
                rollback_strategy="manual_only",
                allowed_skill_ids=("regular_demand_skill", "trial_parse_skill"),
            ),
        )
        self._tools["feishu.write_table"] = "写入飞书提需表，必须通过 Guarded Action 人工批准。"

    def _register_asset_tools(self, default_country: str) -> None:
        self.registry.register(
            "asset.search_by_image",
            _asset_search_by_image,
            spec=ToolSpec(
                "asset.search_by_image",
                display_name="按图搜索资产库",
                target_system="asset_library",
                country_scoped=True,
                allowed_skill_ids=("trial_parse_skill",),
            ),
        )
        self.registry.register(
            "asset.search_by_tag",
            _asset_search_by_tag,
            spec=ToolSpec(
                "asset.search_by_tag",
                display_name="按运营 tag 搜索资产库",
                target_system="asset_library",
                country_scoped=True,
                allowed_skill_ids=("regular_demand_skill",),
            ),
        )
        self.registry.register(
            "asset.get_metadata",
            _asset_get_metadata,
            spec=ToolSpec(
                "asset.get_metadata",
                display_name="读取资产 metadata",
                target_system="asset_library",
                country_scoped=True,
                allowed_skill_ids=("regular_demand_skill", "trial_parse_skill", "value_audit_skill"),
            ),
        )
        self.registry.register(
            "asset.check_duplicate",
            _asset_check_duplicate,
            spec=ToolSpec(
                "asset.check_duplicate",
                display_name="检查重复素材",
                target_system="asset_library",
                country_scoped=True,
                allowed_skill_ids=("trial_parse_skill",),
            ),
        )
        for name, description in {
            "asset.search_by_image": "按参考图检索相似资产。",
            "asset.search_by_tag": "按运营 tag 检索资产库素材。",
            "asset.get_metadata": "读取素材 metadata。",
            "asset.check_duplicate": "检查参考图是否与已有素材重复。",
        }.items():
            self._tools[name] = description
        self.registry.register(
            "asset.upload_reference",
            lambda **kwargs: {"asset_id": f"upload-{str(kwargs.get('country') or default_country or 'GLOBAL')}", "status": "uploaded"},
            spec=ToolSpec(
                "asset.upload_reference",
                display_name="上传参考图",
                target_system="asset_library",
                side_effect="external_write",
                approval_required=True,
                rollback_strategy="manual_only",
                allowed_skill_ids=("trial_parse_skill",),
            ),
        )
        self.registry.register(
            "asset.attach_to_feishu_payload",
            lambda **kwargs: {"status": "attached", "asset_id": kwargs.get("asset_id", "")},
            spec=ToolSpec(
                "asset.attach_to_feishu_payload",
                display_name="绑定资产到飞书 payload",
                target_system="asset_library",
                side_effect="external_write",
                approval_required=True,
                rollback_strategy="manual_only",
                allowed_skill_ids=("trial_parse_skill",),
            ),
        )
        self._tools["asset.upload_reference"] = "上传参考图，必须通过 Guarded Action。"
        self._tools["asset.attach_to_feishu_payload"] = "绑定资产到飞书 payload，必须通过 Guarded Action。"

    def _register_warehouse_tools(self, default_country: str) -> None:
        specs = {
            "warehouse.weekly_metrics": ("周度指标", ("weekly_review_skill",)),
            "warehouse.image_performance": ("图片表现", ("weekly_review_skill", "value_audit_skill")),
            "warehouse.tag_performance": ("运营 tag 表现", ("weekly_review_skill", "regular_demand_skill")),
            "warehouse.country_comparison": ("国家差异", ("weekly_review_skill",)),
            "warehouse.human_gold_samples": ("Human gold 样本", ("weekly_review_skill", "value_audit_skill")),
        }
        funcs = {
            "warehouse.weekly_metrics": _warehouse_weekly_metrics,
            "warehouse.image_performance": _warehouse_image_performance,
            "warehouse.tag_performance": _warehouse_tag_performance,
            "warehouse.country_comparison": _warehouse_country_comparison,
            "warehouse.human_gold_samples": _warehouse_human_gold_samples,
        }
        for name, (display_name, skill_ids) in specs.items():
            self.registry.register(
                name,
                funcs[name],
                spec=ToolSpec(name, display_name=display_name, target_system="warehouse", country_scoped=True, allowed_skill_ids=skill_ids),
            )
            self._tools[name] = f"{display_name}，只读预定义查询。"

    def _register_vector_tools(self, default_country: str) -> None:
        for name, task_index, skill_ids in (
            ("vector.search_value_master", "value_master", ("regular_demand_skill", "trial_parse_skill", "value_audit_skill")),
            ("vector.search_audit_rules", "audit", ("trial_parse_skill", "value_audit_skill")),
            ("vector.search_historical_images", "weekly_review", ("weekly_review_skill", "regular_demand_skill")),
            ("vector.search_sop", "sop", ("regular_demand_skill", "trial_parse_skill")),
            ("vector.search_memory_facts", "memory_governance", ("weekly_review_skill", "memory_governance_skill")),
        ):
            self.registry.register(
                name,
                lambda country, query="", top_k=5, task_index=task_index: _vector_search(country, query, top_k, task_index),
                spec=ToolSpec(name, display_name=name.replace(".", " "), target_system="vector_store", country_scoped=True, allowed_skill_ids=skill_ids),
            )
            self._tools[name] = f"检索 {task_index} RAG 索引，默认带国家过滤。"

    def _register_image_tools(self, default_country: str) -> None:
        for name, func, skill_ids in (
            ("image.extract_features", _image_extract_features, ("trial_parse_skill",)),
            ("image.audit_value_fit", _image_audit_value_fit, ("value_audit_skill",)),
            ("image.detect_ip_risk", _image_detect_ip_risk, ("value_audit_skill", "trial_parse_skill")),
            ("image.detect_visual_quality", _image_detect_visual_quality, ("value_audit_skill", "trial_parse_skill")),
            ("image.compare_reference", _image_compare_reference, ("trial_parse_skill",)),
        ):
            self.registry.register(
                name,
                func,
                spec=ToolSpec(name, display_name=name.replace(".", " "), target_system="vision_model", country_scoped=True, allowed_skill_ids=skill_ids),
            )
            self._tools[name] = "图片审核/VLM 只读工具，结果仅作为建议。"

    def _register_memory_tools(self, default_country: str) -> None:
        self.registry.register(
            "memory.workbench",
            lambda country: _memory_workbench(self.repository, country),
            spec=ToolSpec("memory.workbench", display_name="Memory 工作台", target_system="memory", country_scoped=True, allowed_skill_ids=("memory_governance_skill",)),
        )
        self.registry.register(
            "memory.conflicts",
            lambda country: _memory_conflicts(self.repository, country),
            spec=ToolSpec("memory.conflicts", display_name="Memory 冲突", target_system="memory", country_scoped=True, allowed_skill_ids=("memory_governance_skill",)),
        )
        self.registry.register(
            "memory.provenance",
            lambda country, memory_id=0: _memory_provenance(self.repository, country, memory_id),
            spec=ToolSpec("memory.provenance", display_name="Memory Provenance", target_system="memory", country_scoped=True, allowed_skill_ids=("memory_governance_skill",)),
        )
        for name in ("memory.workbench", "memory.conflicts", "memory.provenance"):
            self._tools[name] = "Memory 治理只读工具。"


def _asset_search_by_image(country: str, reference_image: str = "", limit: int = 5) -> dict[str, object]:
    return {
        "country": country,
        "reference_image": reference_image,
        "items": tuple({"asset_id": f"{country}-similar-{index}", "similarity": round(0.9 - index * 0.04, 2)} for index in range(1, int(limit) + 1)),
    }


def _asset_search_by_tag(country: str, operation_tag: str = "", limit: int = 5) -> dict[str, object]:
    return {
        "country": country,
        "operation_tag": operation_tag,
        "items": tuple({"asset_id": f"{country}-{operation_tag or 'tag'}-{index}", "status": "available"} for index in range(1, int(limit) + 1)),
    }


def _asset_get_metadata(country: str, asset_id: str = "") -> dict[str, object]:
    return {"country": country, "asset_id": asset_id, "metadata": {"source": "mock_asset_library", "approved_for_reference": True}}


def _asset_check_duplicate(country: str, reference_image: str = "") -> dict[str, object]:
    return {"country": country, "reference_image": reference_image, "duplicate": False, "matched_asset_ids": ()}


def _warehouse_weekly_metrics(country: str, date_range_start: str = "", date_range_end: str = "", js_category: str = "") -> dict[str, object]:
    return {"country": country, "date_range_start": date_range_start, "date_range_end": date_range_end, "js_category": js_category, "sa_growth": 0.12, "cd_risk_count": 2}


def _warehouse_image_performance(country: str, image_id: str = "", operation_tag: str = "") -> dict[str, object]:
    return {"country": country, "image_id": image_id, "operation_tag": operation_tag, "open_rate": 0.28, "completion_rate": 0.9}


def _warehouse_tag_performance(country: str, operation_tag: str = "") -> dict[str, object]:
    return {"country": country, "operation_tag": operation_tag, "stock": 2, "sa_rate": 0.36, "trend": "up"}


def _warehouse_country_comparison(country: str, js_category: str = "") -> dict[str, object]:
    return {"country": country, "js_category": js_category, "differences": (f"{country} 更重视文化真实性",)}


def _warehouse_human_gold_samples(country: str, limit: int = 5) -> dict[str, object]:
    return {"country": country, "items": tuple({"sample_id": f"{country}-gold-{index}", "label": "A"} for index in range(1, int(limit) + 1))}


def _vector_search(country: str, query: str = "", top_k: int = 5, task_index: str = "value_master") -> dict[str, object]:
    limit = int(top_k or 5)
    citations = tuple(f"{country.upper()}_{task_index.upper()}_{index:03d}#chunk-1" for index in range(1, min(limit, 5) + 1))
    return {"query": query, "task_index": task_index, "filters": {"country": country, "include_global": True}, "citations": citations}


def _image_extract_features(country: str, reference_image: str = "", subject: str = "") -> dict[str, object]:
    return {"country": country, "subject": subject or "待确认主体", "color_mood": "清爽", "composition": "主体清晰", "model_provider": "local"}


def _image_audit_value_fit(country: str, image_or_candidate: str = "", subject: str = "", operation_tag: str = "") -> dict[str, object]:
    return {"country": country, "fit": "review_required", "subject": subject, "operation_tag": operation_tag, "risk_points": ("文化真实性需人工复核",), "model_provider": "local"}


def _image_detect_ip_risk(country: str, image_or_candidate: str = "", subject: str = "") -> dict[str, object]:
    risky_terms = ("迪士尼", "漫威", "宝可梦", "哈利波特")
    hit = any(term in f"{image_or_candidate}{subject}" for term in risky_terms)
    return {"country": country, "risk_level": "high" if hit else "low", "risk_points": ("疑似 IP 风险",) if hit else ()}


def _image_detect_visual_quality(country: str, image_or_candidate: str = "") -> dict[str, object]:
    return {"country": country, "quality": "ok", "risk_points": ()}


def _image_compare_reference(country: str, reference_image: str = "", candidate_image: str = "") -> dict[str, object]:
    return {"country": country, "reference_image": reference_image, "candidate_image": candidate_image, "similarity": 0.82}


def _memory_workbench(repository, country: str) -> dict[str, object]:
    if repository is None:
        return {"country": country, "pending": 0}
    rows = repository.layered_memories(country, include_inactive=True)
    return {"country": country, "total": len(rows), "pending": len([row for row in rows if row.get("review_status") == "draft"])}


def _memory_conflicts(repository, country: str) -> dict[str, object]:
    return {"country": country, "conflict_groups": ()}


def _memory_provenance(repository, country: str, memory_id: int = 0) -> dict[str, object]:
    return {"country": country, "memory_id": memory_id, "chain": ()}


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
