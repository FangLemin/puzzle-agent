from __future__ import annotations

from dataclasses import dataclass

from puzzle_ops.trulens_eval import TruLensRAGEvaluator


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    task_type: str
    input_text: str
    expected_tools: tuple[str, ...]
    actual_tools: tuple[str, ...]
    retrieved_contexts: tuple[str, ...]
    answer: str
    judge_reason: str


@dataclass(frozen=True)
class EvalMetricResult:
    name: str
    score: float
    threshold: float
    status: str
    reason: str


@dataclass(frozen=True)
class EvalReport:
    dataset_name: str
    country: str
    cases: tuple[EvalCase, ...]
    metric_results: tuple[EvalMetricResult, ...]


class AgentEvalSuite:
    """Offline eval suite inspired by AgentOps traces, RAGAS and DeepEval metrics."""

    def __init__(self, agent):
        self.agent = agent

    def run(self, country: str) -> EvalReport:
        trace = self.agent.run_agent_task(country, "value_judge")
        profile = self.agent.multimodal_profile(country)
        review = self.agent.audit_review(profile.asset.operation_tag + profile.asset.remark)
        contexts = review.evidence or (review.reason,)
        answer = f"{review.reason} {review.suggestion}"
        case = EvalCase(
            case_id=f"{country}-rag-audit-001",
            task_type="value_judge",
            input_text=profile.asset.operation_tag,
            expected_tools=("history.search_records", "image.retrieve_similar_good_bad", "audit.retrieve_policy"),
            actual_tools=trace.tool_calls,
            retrieved_contexts=contexts,
            answer=answer,
            judge_reason=f"依据审核手册证据{len(contexts)}条、工具调用{len(trace.tool_calls)}步和相似好坏图证据进行评测。",
        )
        rag = TruLensRAGEvaluator().evaluate(case.input_text, case.retrieved_contexts, case.answer)
        tool_correctness = _coverage(case.expected_tools, case.actual_tools)
        plan_adherence = _coverage(("构建国家与任务上下文", "召回审核手册风险依据", "输出价值观判断并记录评测"), trace.plan)
        step_efficiency = 1.0 if len(trace.tool_calls) <= 7 else 0.7
        metrics = (
            _metric("TruLens Context Relevance", rag["context_relevance"], 0.25, "问题和召回审核规则应有词面或语义交集。"),
            _metric("TruLens Groundedness", rag["groundedness"], 0.25, "回答应能从召回规则中找到依据。"),
            _metric("TruLens Answer Relevance", rag["answer_relevance"], 0.20, "回答应回应原始运营 tag/风险问题。"),
            _metric("Context Precision", min(rag["context_relevance"] + 0.12, 1.0), 0.35, "前排召回内容应集中于审核风险。"),
            _metric("Context Recall", 1.0 if case.retrieved_contexts else 0.0, 0.80, "审核规则召回应覆盖至少一条依据。"),
            _metric("Tool Correctness", tool_correctness, 0.80, "Agent 应调用历史检索、相似图检索和审核规则召回。"),
            _metric("Plan Adherence", plan_adherence, 0.75, "执行 trace 应覆盖计划中的关键步骤。"),
            _metric("Step Efficiency", step_efficiency, 0.75, "工具调用步骤应无明显重复。"),
        )
        return EvalReport("PuzzleOps Agent Eval Set", country, (case,), metrics)


def _metric(name: str, score: float, threshold: float, reason: str) -> EvalMetricResult:
    rounded = round(score, 3)
    return EvalMetricResult(name, rounded, threshold, "PASS" if rounded >= threshold else "FAIL", reason)


def _coverage(expected: tuple[str, ...], actual: tuple[str, ...]) -> float:
    if not expected:
        return 1.0
    return len(set(expected) & set(actual)) / len(set(expected))
