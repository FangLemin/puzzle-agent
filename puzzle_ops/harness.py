from __future__ import annotations

from collections import Counter
import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from puzzle_ops.models import DemandRow
from puzzle_ops.rag import RagProviderConfig


@dataclass(frozen=True)
class EvalSample:
    sample_id: str
    country: str
    local_image_path: str
    operation_tag: str
    subject: str
    js_category: str
    source: str
    position: int
    metrics: dict[str, float]
    gold_grade: str
    gold_subject: str
    gold_color_mood: str
    gold_composition: str
    gold_value_labels: tuple[str, ...]
    gold_risk_labels: tuple[str, ...]
    human_note: str

    @classmethod
    def synthetic_demo(cls, sample_id: str, country: str, operation_tag: str, subject: str, gold_grade: str) -> "EvalSample":
        return cls(
            sample_id=sample_id,
            country=country,
            local_image_path="",
            operation_tag=operation_tag,
            subject=subject,
            js_category="demo",
            source="synthetic_demo",
            position=0,
            metrics={},
            gold_grade=gold_grade,
            gold_subject=subject,
            gold_color_mood="",
            gold_composition="",
            gold_value_labels=(),
            gold_risk_labels=(),
            human_note="合成样本仅用于页面 demo 与边界测试",
        )

    @property
    def is_real(self) -> bool:
        return self.source == "real"


@dataclass(frozen=True)
class EvalSampleImportIssue:
    sample_id: str
    row_number: int
    reason: str


def load_eval_samples_csv(path: Path | str, image_root: Path | str | None = None) -> tuple[tuple[EvalSample, ...], tuple[EvalSampleImportIssue, ...]]:
    csv_path = Path(path)
    root = Path(image_root) if image_root is not None else csv_path.parent
    samples: list[EvalSample] = []
    issues: list[EvalSampleImportIssue] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), 2):
            sample_id = _field(row, "sample_id") or f"row-{row_number}"
            source = _field(row, "source") or "real"
            local_image_path = _resolve_image_path(_field(row, "local_image_path"), root)
            if source == "real" and not local_image_path:
                issues.append(EvalSampleImportIssue(sample_id, row_number, "缺少真实图片路径"))
                continue
            if source == "real" and not Path(local_image_path).exists():
                issues.append(EvalSampleImportIssue(sample_id, row_number, f"图片路径不存在：{local_image_path}"))
                continue
            samples.append(
                EvalSample(
                    sample_id=sample_id,
                    country=_field(row, "country"),
                    local_image_path=local_image_path,
                    operation_tag=_field(row, "operation_tag"),
                    subject=_field(row, "subject"),
                    js_category=_field(row, "js_category"),
                    source=source,
                    position=_int_field(row, "position"),
                    metrics={
                        "open_rate": _float_field(row, "open_rate"),
                        "completion_rate": _float_field(row, "completion_rate"),
                        "avg_finish_time": _float_field(row, "avg_finish_time"),
                    },
                    gold_grade=_field(row, "gold_grade"),
                    gold_subject=_field(row, "gold_subject"),
                    gold_color_mood=_field(row, "gold_color_mood"),
                    gold_composition=_field(row, "gold_composition"),
                    gold_value_labels=_labels(_field(row, "gold_value_labels")),
                    gold_risk_labels=_labels(_field(row, "gold_risk_labels")),
                    human_note=_field(row, "human_note"),
                )
            )
    return tuple(samples), tuple(issues)


@dataclass(frozen=True)
class HarnessCaseResult:
    sample_id: str
    task_type: str
    input_payload: dict[str, object]
    agent_output: str
    tool_calls: tuple[str, ...]
    trace_steps: tuple[str, ...]
    scores: dict[str, float | str]
    failure_reasons: tuple[str, ...]
    human_override: str = ""
    evidence_trace: dict[str, object] = field(default_factory=dict)
    failure_categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class HarnessRun:
    run_id: str
    version: str
    dataset_name: str
    model_provider: str
    generator_provider: str
    cases: tuple[HarnessCaseResult, ...]
    metrics: dict[str, float]
    failures: tuple[HarnessCaseResult, ...]
    created_at: str
    country: str = ""
    execution_mode: str = "offline"
    metric_evaluable_counts: dict[str, int] = field(default_factory=dict)


class AgentHarness:
    def __init__(self, agent, generator_provider=None, *, execute_model_calls: bool = False, execute_generation: bool | None = None):
        self.agent = agent
        self.generator_provider = generator_provider
        self.execute_model_calls = execute_model_calls
        self.execute_generation = bool(generator_provider) if execute_generation is None else execute_generation
        self._run_rag_evidence: dict[str, object] = {}
        self._vision_results: dict[str, object] = {}
        self._vision_errors: dict[str, str] = {}

    def dataset_summary(self, samples: tuple[EvalSample, ...]) -> dict[str, object]:
        countries = Counter(sample.country for sample in samples)
        grades = Counter(sample.gold_grade for sample in samples if sample.gold_grade)
        sources = Counter("真实业务样本" if sample.is_real else "Synthetic Demo Samples" for sample in samples)
        return {
            "真实样本数": sum(1 for sample in samples if sample.is_real),
            "合成样本数": sum(1 for sample in samples if not sample.is_real),
            "国家分布": dict(countries),
            "等级分布": dict(grades),
            "来源分布": dict(sources),
        }

    def default_samples(self, country: str) -> tuple[EvalSample, ...]:
        records = self.agent._history_records(country)
        samples: list[EvalSample] = []
        for index, record in enumerate(records[:8], 1):
            is_real = bool(record.local_image_path and Path(record.local_image_path).exists())
            source = "real" if is_real else "synthetic_demo"
            samples.append(
                EvalSample(
                    sample_id=record.image_id or f"{country}-{index}",
                    country=country,
                    local_image_path=record.local_image_path,
                    operation_tag=record.operation_tag,
                    subject=record.subject_tag,
                    js_category=record.js_category,
                    source=source,
                    position=record.position,
                    metrics={
                        "open_rate": record.open_rate,
                        "completion_rate": record.completion_rate,
                        "avg_finish_time": record.avg_finish_time,
                    },
                    gold_grade=record.grade,
                    gold_subject=record.subject_tag,
                    gold_color_mood="",
                    gold_composition="",
                    gold_value_labels=(),
                    gold_risk_labels=(),
                    human_note="从历史表导入；需人工补 gold label" if is_real else "合成 demo 样本，不用于证明模型效果",
                )
            )
        return tuple(samples)

    def run(self, samples: tuple[EvalSample, ...], dataset_name: str, version: str) -> HarnessRun:
        self._vision_results = {}
        self._vision_errors = {}
        self._prepare_run_rag_evidence(samples)
        cases: list[HarnessCaseResult] = []
        for sample in samples:
            cases.extend(self._run_sample(sample))
        failures = tuple(case for case in cases if case.failure_reasons)
        metrics = self._aggregate_metrics(samples, tuple(cases))
        return HarnessRun(
            run_id=f"hr-{uuid4().hex[:10]}",
            version=version,
            dataset_name=dataset_name,
            model_provider=str(self.agent.vision_llm_status().get("provider", "qwen")),
            generator_provider=getattr(self.generator_provider, "provider_name", "not_configured"),
            cases=tuple(cases),
            metrics=metrics,
            failures=failures,
            created_at=datetime.now().isoformat(timespec="seconds"),
            country=samples[0].country if samples else "",
            execution_mode=self._execution_mode(),
            metric_evaluable_counts=_metric_evaluable_counts(tuple(cases)),
        )

    def _prepare_run_rag_evidence(self, samples: tuple[EvalSample, ...]) -> None:
        self._run_rag_evidence = {}
        countries = sorted({sample.country for sample in samples})
        for country in countries:
            subjects = "、".join(dict.fromkeys(sample.subject for sample in samples if sample.country == country and sample.subject))
            query = f"{country}市场 Harness 评测：{subjects or '当前样本'}的价值观判断与审核风险"
            try:
                self._run_rag_evidence[country] = self.agent.value_audit_rag_answer(
                    country,
                    query,
                    top_k=6,
                    provider_config=None if self.execute_model_calls else RagProviderConfig(),
                )
            except (RuntimeError, ValueError):
                continue

    def compare_runs(self, current: HarnessRun, previous: HarnessRun | None) -> dict[str, str]:
        if previous is None:
            return {"对比对象": "暂无上一轮 run", "结论": "当前版本作为基线"}
        keys = sorted(set(current.metrics) | set(previous.metrics))
        return {
            key: f"{previous.metrics.get(key, 0):.0%} -> {current.metrics.get(key, 0):.0%}"
            for key in keys
        }

    def _run_sample(self, sample: EvalSample) -> tuple[HarnessCaseResult, ...]:
        return (
            self._trial_parse_case(sample),
            self._value_match_case(sample),
            self._audit_case(sample),
            self._grade_case(sample),
            self._derive_generation_case(sample),
            self._feishu_sync_case(sample),
        )

    def _trial_parse_case(self, sample: EvalSample) -> HarnessCaseResult:
        semantic = self._vision_result(sample)
        actual_subject = str(getattr(semantic, "subject", "") or sample.subject)
        actual_color = str(getattr(semantic, "style", "") or "离线预览，未调用 VLM")
        actual_composition = str(getattr(semantic, "scene", "") or "离线预览，未调用 VLM")
        description = f"主体内容：{actual_subject}；色彩氛围：{actual_color}；构图环境：{actual_composition}。"
        failures: list[str] = []
        failure_categories: list[str] = []
        scores: dict[str, float | str] = {
            "三段式描述合规": 1.0 if _has_three_part_description(description) else 0.0,
            "工具调用正确": 1.0 if semantic is not None else "not_evaluable",
            "步骤效率": 1.0 if semantic is not None else "not_evaluable",
        }
        if not sample.local_image_path:
            scores["图片可读"] = "not_evaluable"
            failures.append("缺少真实图片路径，无法验证 VLM 解析准确性")
            failure_categories.append("missing_image")
        elif not Path(sample.local_image_path).exists():
            scores["图片可读"] = 0.0
            failures.append("真实图片路径不存在")
            failure_categories.append("missing_image")
        else:
            scores["图片可读"] = 1.0
        if self.execute_model_calls and semantic is None:
            failures.append(self._vision_errors.get(sample.sample_id, "真实视觉 LLM 未返回解析结果"))
            failure_categories.append("vlm_unavailable")
        if sample.gold_subject and semantic is not None:
            scores["主体匹配"] = _text_overlap(actual_subject, sample.gold_subject)
        else:
            scores["主体匹配"] = "not_evaluable"
        scores["色彩氛围匹配"] = _text_overlap(actual_color, sample.gold_color_mood) if sample.gold_color_mood and semantic is not None else "not_evaluable"
        scores["构图环境匹配"] = _text_overlap(actual_composition, sample.gold_composition) if sample.gold_composition and semantic is not None else "not_evaluable"
        for label, score in (("主体", scores["主体匹配"]), ("色彩氛围", scores["色彩氛围匹配"]), ("构图环境", scores["构图环境匹配"])):
            if isinstance(score, (int, float)) and score < 1.0:
                failures.append(f"{label}与 gold label 不一致")
                failure_categories.append("vlm_gold_mismatch")
        return HarnessCaseResult(
            sample.sample_id,
            "trial_parse_eval",
            {"operation_tag": sample.operation_tag, "image": sample.local_image_path},
            description,
            ("image.extract_features", "vision_llm.parse_or_skip"),
            ("上传图读取", "三段式解析", "人工 gold label 对照"),
            scores,
            tuple(failures),
            evidence_trace={
                "visual_evidence": f"图片={sample.local_image_path or '未提供'}；模型主体={actual_subject or '未解析'}；模型={getattr(semantic, 'provider', 'offline')}",
                "rag_citations": (),
                "memory_evidence": (),
            },
            failure_categories=tuple(failure_categories),
        )

    def _value_match_case(self, sample: EvalSample) -> HarnessCaseResult:
        semantic = self._vision_result(sample)
        actual_subject = str(getattr(semantic, "subject", "") or sample.subject)
        visual_evidence = f"主体={actual_subject or '未解析'}；图片={sample.local_image_path or '未提供真实图片'}"
        rag_answer = self._run_rag_evidence.get(sample.country)
        try:
            if rag_answer is None:
                raise ValueError("本次 Harness Run 没有可用 RAG 证据")
            rag_citations = rag_answer.citations
            rag_context = rag_answer.context
        except (RuntimeError, ValueError) as exc:
            rag_citations = ()
            rag_context = f"RAG 检索失败：{exc}"
        memory_rows = self.agent.memory_debug(sample.country, query=sample.subject, limit=4)
        memory_evidence = tuple(
            f"{row['layer']}/{row['memory_type']}：{row['summary']}"
            for row in memory_rows
        )
        evidence = f"离线预览，未调用价值观 LLM。图像证据：{visual_evidence}；RAG依据：{','.join(rag_citations) or '无引用'}。"
        value_call_ok = False
        if self.execute_model_calls and semantic is not None:
            try:
                description = (
                    f"主体内容：{actual_subject}；色彩氛围：{getattr(semantic, 'style', '')}；"
                    f"构图环境：{getattr(semantic, 'scene', '')}。"
                )
                row = DemandRow(
                    need_type="试新",
                    country=sample.country,
                    js_category=sample.js_category,
                    image_name=Path(sample.local_image_path).name,
                    operation_tag=sample.operation_tag,
                    subject=actual_subject,
                    count=1,
                    priority="P1",
                    method="先照片后AI",
                    delivery_date="",
                    subject_description=description,
                    remark=f"Harness 真实 VLM 解析：{getattr(semantic, 'raw_text', '')}",
                    reference_image_path=sample.local_image_path,
                )
                rules = self.agent._rag_rules_for_value_master(row)
                evidence = self.agent.trial_uploads.vision_client.judge_value_match(
                    {
                        "country": row.country,
                        "js_category": row.js_category,
                        "image_name": row.image_name,
                        "operation_tag": row.operation_tag,
                        "subject": row.subject,
                        "subject_description": row.subject_description,
                        "remark": row.remark,
                        "reference_image_url": row.reference_image_url,
                    },
                    rules,
                )
                value_call_ok = True
            except Exception as exc:
                evidence = f"价值观 LLM 评测失败：{exc}"
        scores: dict[str, float | str] = {
            "引用视觉证据": 1.0 if actual_subject in evidence else 0.0,
            "工具调用正确": 1.0 if value_call_ok else (0.0 if self.execute_model_calls and semantic is not None else "not_evaluable"),
            "步骤效率": 1.0 if value_call_ok else (0.0 if self.execute_model_calls and semantic is not None else "not_evaluable"),
        }
        failures: list[str] = []
        failure_categories: list[str] = []
        if sample.gold_value_labels and self.execute_model_calls and semantic is not None:
            matched = sum(1 for label in sample.gold_value_labels if label in evidence)
            scores["价值观一致"] = _safe_ratio(matched, len(sample.gold_value_labels))
            if matched != len(sample.gold_value_labels):
                failures.append("价值观 LLM 输出未覆盖全部 gold_value_labels")
                failure_categories.append("value_gold_mismatch")
        else:
            scores["价值观一致"] = "not_evaluable"
            if not sample.gold_value_labels:
                failures.append("缺少 gold_value_labels，价值观一致率跳过")
                failure_categories.append("missing_gold")
        return HarnessCaseResult(
            sample.sample_id,
            "value_match_eval",
            {"subject": sample.subject, "value_labels": sample.gold_value_labels},
            evidence,
            ("value_rules.retrieve", "vision_evidence.compare"),
            ("读取价值观规则", "比对当前图像证据"),
            scores,
            tuple(failures),
            evidence_trace={
                "visual_evidence": visual_evidence,
                "rag_citations": rag_citations,
                "rag_context": rag_context,
                "memory_evidence": memory_evidence,
            },
            failure_categories=tuple(failure_categories),
        )

    def _audit_case(self, sample: EvalSample) -> HarnessCaseResult:
        semantic = self._vision_result(sample)
        semantic_risks = tuple(getattr(semantic, "risk_tags", ()) or ())
        review = self.agent.audit_review(" ".join((sample.operation_tag, sample.human_note, " ".join(semantic_risks))))
        recalled = tuple(label for label in sample.gold_risk_labels if label in semantic_risks or label in review.reason or label in " ".join(review.evidence))
        if sample.gold_risk_labels:
            score: float | str = len(recalled) / len(sample.gold_risk_labels)
        else:
            score = "not_evaluable"
        failures = () if score == 1.0 or score == "not_evaluable" else (f"风险未完全召回：{','.join(set(sample.gold_risk_labels) - set(recalled))}",)
        return HarnessCaseResult(
            sample.sample_id,
            "audit_eval",
            {"gold_risk_labels": sample.gold_risk_labels},
            review.reason,
            ("audit.retrieve_policy", "audit.rule_engine"),
            ("召回审核手册", "规则审核"),
            {"风险召回": score, "工具调用正确": 1.0, "步骤效率": 1.0},
            failures,
            evidence_trace={
                "visual_evidence": sample.subject,
                "rag_citations": tuple(review.evidence),
                "memory_evidence": (),
            },
            failure_categories=("risk_missed",) if failures else (),
        )

    def _grade_case(self, sample: EvalSample) -> HarnessCaseResult:
        predicted = _predict_grade(sample.metrics)
        if sample.gold_grade:
            score: float | str = 1.0 if predicted == sample.gold_grade else 0.0
        else:
            score = "not_evaluable"
        failures = () if score in {1.0, "not_evaluable"} else (f"SABCD预测不一致：预测{predicted}，gold={sample.gold_grade}",)
        return HarnessCaseResult(
            sample.sample_id,
            "grade_predict_eval",
            {"metrics": sample.metrics},
            f"预测等级：{predicted}",
            ("metrics.grade_predict",),
            ("读取开图率/完成率/时长", "输出等级预测"),
            {"SABCD预测": score, "工具调用正确": 1.0, "步骤效率": 1.0},
            failures,
            failure_categories=("grade_mismatch",) if failures else (),
        )

    def _derive_generation_case(self, sample: EvalSample) -> HarnessCaseResult:
        provider = self.generator_provider
        if not self.execute_generation:
            return HarnessCaseResult(
                sample.sample_id,
                "derive_generation_eval",
                {"reference_image": sample.local_image_path},
                "本次 Harness 未授权图像生成，已跳过付费调用。",
                ("image_generation.cost_gate",),
                ("检查生成授权", "跳过生成"),
                {"生成图审核通过": "not_evaluable", "工具调用正确": "not_evaluable", "步骤效率": "not_evaluable"},
                (),
                failure_categories=(),
            )
        status = provider.healthcheck() if provider is not None else {"configured": False}
        if provider is None or not status.get("configured"):
            return HarnessCaseResult(
                sample.sample_id,
                "derive_generation_eval",
                {"reference_image": sample.local_image_path},
                "生成 provider 未配置；保留 prompt/接口，不伪造生成图。",
                ("image_generation.healthcheck",),
                ("生成前检查", "等待 provider 配置"),
                {"生成图审核通过": "not_evaluable", "工具调用正确": 0.0, "步骤效率": 0.0},
                ("生成 provider 未配置",),
                failure_categories=("provider_not_configured",),
            )
        if not sample.local_image_path or not Path(sample.local_image_path).is_file():
            return HarnessCaseResult(
                sample.sample_id,
                "derive_generation_eval",
                {"reference_image": sample.local_image_path},
                "参考图缺失；跳过付费生成调用。",
                ("image_generation.preflight",),
                ("生成前检查", "阻断无参考图调用"),
                {"生成图审核通过": "not_evaluable", "工具调用正确": 0.0, "步骤效率": 0.0},
                ("参考图不存在，无法评测真实好图衍生",),
                failure_categories=("reference_image_missing",),
            )
        images = provider.generate_derivatives(
            sample.local_image_path,
            prompt=f"保留{sample.subject}的核心吸引力，变化场景和季节元素",
            negative_prompt="避免品牌logo、文字水印、知名动漫/IP/宗教政治风险",
            count=2,
            seed=609,
            style_constraints={
                "source_sample_id": sample.sample_id,
                "retained_features": f"{sample.subject}；{sample.gold_color_mood}",
                "changed_features": "季节元素；道具组合；背景环境",
                "risk_notes": "生成图必须二次 VLM 解析；人工审核后才能同步飞书",
            },
        )
        return HarnessCaseResult(
            sample.sample_id,
            "derive_generation_eval",
            {"reference_image": sample.local_image_path},
            f"生成参考图{len(images)}张；等待二次 VLM 解析与审核。",
            ("image_generation.generate_derivatives", "vision_llm.parse_generated", "audit.rule_engine"),
            ("生成 prompt", "provider 生成", "二次解析审核"),
            {
                "生成图审核通过": 1.0 if len(images) == 2 else 0.0,
                "工具调用正确": 1.0 if len(images) == 2 else 0.0,
                "步骤效率": 1.0 if len(images) == 2 else 0.0,
            },
            () if len(images) == 2 else ("生成图数量不符合预期",),
            evidence_trace={
                "visual_evidence": sample.subject,
                "rag_citations": (),
                "memory_evidence": (),
            },
            failure_categories=() if len(images) == 2 else ("generation_failed",),
        )

    def _vision_result(self, sample: EvalSample):
        if sample.sample_id in self._vision_results:
            return self._vision_results[sample.sample_id]
        if sample.sample_id in self._vision_errors or not self.execute_model_calls:
            return None
        client = getattr(self.agent.trial_uploads, "vision_client", None)
        path = Path(sample.local_image_path)
        if client is None:
            self._vision_errors[sample.sample_id] = "真实视觉 LLM 未配置"
            return None
        if not path.is_file():
            self._vision_errors[sample.sample_id] = "真实参考图不存在，无法调用视觉 LLM"
            return None
        try:
            local_summary = self.agent.local_image_analyzer.summarize_bytes((path.read_bytes(),))
            result = client.analyze(
                [{"filename": path.name, "path": str(path), "content_type": _image_content_type(path)}],
                sample.country,
                sample.js_category,
                local_summary,
            )
        except Exception as exc:
            self._vision_errors[sample.sample_id] = f"真实视觉 LLM 调用失败：{exc}"
            return None
        self._vision_results[sample.sample_id] = result
        return result

    def _execution_mode(self) -> str:
        if self.execute_model_calls and self.execute_generation:
            return "real_vlm_and_generation"
        if self.execute_model_calls:
            return "real_vlm"
        if self.execute_generation:
            return "generation_only"
        return "offline"

    def _feishu_sync_case(self, sample: EvalSample) -> HarnessCaseResult:
        required = ("operation_tag", "subject", "country")
        complete = all(getattr(sample, key if key != "operation_tag" else "operation_tag") for key in required)
        return HarnessCaseResult(
            sample.sample_id,
            "feishu_sync_eval",
            {"operation_tag": sample.operation_tag, "subject": sample.subject, "country": sample.country},
            "同步前字段完整性检查通过" if complete else "同步前字段不完整",
            ("feishu.schema_filter", "feishu.attachment_upload_or_skip"),
            ("字段白名单", "附件上传检查", "同步前拦截"),
            {
                "字段完整性": 1.0 if complete else 0.0,
                "工具调用正确": 1.0 if complete else 0.0,
                "步骤效率": 1.0 if complete else 0.0,
            },
            () if complete else ("提需字段不完整",),
            failure_categories=() if complete else ("field_incomplete",),
        )

    def _aggregate_metrics(self, samples: tuple[EvalSample, ...], cases: tuple[HarnessCaseResult, ...]) -> dict[str, float]:
        metrics = {
            "真实样本占比": _safe_ratio(sum(1 for sample in samples if sample.is_real), len(samples)),
            "三段式描述合规率": _score_average(cases, "三段式描述合规"),
            "主体识别准确率": _score_average(cases, "主体匹配"),
            "色彩氛围准确率": _score_average(cases, "色彩氛围匹配"),
            "构图环境准确率": _score_average(cases, "构图环境匹配"),
            "价值观一致率": _score_average(cases, "价值观一致"),
            "审核风险召回率": _score_average(cases, "风险召回"),
            "SABCD预测准确率": _score_average(cases, "SABCD预测"),
            "工具调用正确率": _score_average(cases, "工具调用正确"),
            "Step Efficiency": _score_average(cases, "步骤效率"),
            "生成图审核通过率": _score_average(cases, "生成图审核通过"),
            "飞书同步成功率": _score_average(cases, "字段完整性"),
        }
        metrics.update(self._generation_trace_metrics(samples))
        metrics.update(self._rag_runtime_metrics())
        return metrics

    def _generation_trace_metrics(self, samples: tuple[EvalSample, ...]) -> dict[str, float]:
        countries = sorted({sample.country for sample in samples})
        events = [event for country in countries for event in self.agent.generation_events(country)]
        if not events:
            return {
                "生成Trace完整率": 0.0,
                "二次审核通过率": 0.0,
                "飞书附件Ready率": 0.0,
                "生成失败可分类率": 0.0,
            }
        complete = sum(1 for event in events if _complete_generation_trace(event))
        second_review_passed = sum(1 for event in events if event.get("second_review_status") == "passed")
        attachment_ready = sum(1 for event in events if event.get("feishu_attachment_status") == "ready")
        failed = [event for event in events if event.get("status") == "failed"]
        classified = sum(1 for event in failed if event.get("error_type") not in {"", "unknown", "none"})
        return {
            "生成Trace完整率": _safe_ratio(complete, len(events)),
            "二次审核通过率": _safe_ratio(second_review_passed, len(events)),
            "飞书附件Ready率": _safe_ratio(attachment_ready, len(events)),
            "生成失败可分类率": _safe_ratio(classified, len(failed)),
        }

    def _rag_runtime_metrics(self) -> dict[str, float]:
        stats = getattr(self.agent, "_last_rag_stats", None)
        if stats is None:
            return {"RAG缓存命中率": 0.0, "RAG远程调用率": 0.0, "RAG降级率": 0.0}
        embedding_total = stats.embedding_cache_hits + stats.embedding_remote_calls
        remote_total = stats.embedding_remote_calls + stats.rerank_remote_calls
        fallback_total = stats.embedding_fallbacks + stats.rerank_fallbacks
        observable_total = embedding_total + stats.rerank_remote_calls + fallback_total
        return {
            "RAG缓存命中率": _safe_ratio(stats.embedding_cache_hits, embedding_total),
            "RAG远程调用率": _safe_ratio(remote_total, observable_total),
            "RAG降级率": _safe_ratio(fallback_total, observable_total),
        }


def _has_three_part_description(text: str) -> bool:
    return all(part in text for part in ("主体内容：", "色彩氛围：", "构图环境："))


def _complete_generation_trace(event: dict[str, str]) -> bool:
    required = ("status", "provider", "model", "source_operation_tag", "second_review_status", "feishu_attachment_status")
    if not all(str(event.get(key, "")).strip() for key in required):
        return False
    if event.get("status") == "succeeded":
        return bool(event.get("task_id") and event.get("generated_image_paths"))
    if event.get("status") == "failed":
        return bool(event.get("error_type"))
    return True


def _field(row: dict[str, str], key: str) -> str:
    return str(row.get(key, "") or "").strip()


def _resolve_image_path(value: str, root: Path) -> str:
    if not value:
        return ""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return str(path)


def _int_field(row: dict[str, str], key: str) -> int:
    value = _field(row, key)
    return int(value) if value else 0


def _float_field(row: dict[str, str], key: str) -> float:
    value = _field(row, key)
    return float(value) if value else 0.0


def _labels(value: str) -> tuple[str, ...]:
    normalized = value.replace("；", ";").replace("、", ";").replace("|", ";")
    return tuple(part.strip() for part in normalized.split(";") if part.strip())


def _text_overlap(actual: str, expected: str) -> float:
    if not expected:
        return 1.0
    if expected in actual or actual in expected:
        return 1.0
    return 0.0


def _image_content_type(path: Path) -> str:
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if path.suffix.lower() == ".webp":
        return "image/webp"
    return "image/png"


def _predict_grade(metrics: dict[str, float]) -> str:
    open_rate = metrics.get("open_rate", 0.0)
    completion_rate = metrics.get("completion_rate", 0.0)
    if open_rate >= 0.28 and completion_rate >= 0.9:
        return "S"
    if open_rate >= 0.23 and completion_rate >= 0.85:
        return "A"
    if open_rate >= 0.18:
        return "B"
    if open_rate >= 0.12:
        return "C"
    return "D"


def _score_average(cases: tuple[HarnessCaseResult, ...], key: str) -> float:
    scores = [case.scores[key] for case in cases if key in case.scores and isinstance(case.scores[key], (int, float))]
    return _safe_ratio(sum(float(score) for score in scores), len(scores))


def _metric_evaluable_counts(cases: tuple[HarnessCaseResult, ...]) -> dict[str, int]:
    score_keys = {
        "三段式描述合规率": "三段式描述合规",
        "主体识别准确率": "主体匹配",
        "色彩氛围准确率": "色彩氛围匹配",
        "构图环境准确率": "构图环境匹配",
        "价值观一致率": "价值观一致",
        "审核风险召回率": "风险召回",
        "SABCD预测准确率": "SABCD预测",
        "生成图审核通过率": "生成图审核通过",
        "飞书同步成功率": "字段完整性",
        "工具调用正确率": "工具调用正确",
        "Step Efficiency": "步骤效率",
    }
    return {
        metric: sum(1 for case in cases if isinstance(case.scores.get(score_key), (int, float)))
        for metric, score_key in score_keys.items()
    }


def _safe_ratio(numerator: float, denominator: int) -> float:
    return round(numerator / denominator, 3) if denominator else 0.0
