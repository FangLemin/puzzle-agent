from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.eval_suite import AgentEvalSuite
from puzzle_ops.trulens_eval import TruLensRAGEvaluator


def test_trulens_rag_evaluator_scores_rag_triad():
    evaluator = TruLensRAGEvaluator()

    result = evaluator.evaluate(
        query="宫崎骏 动漫角色 风险",
        contexts=("动漫角色直接使用属于红线，宫崎骏同款画风叠加角色特征属于高危。",),
        answer="该素材疑似动漫/IP风险，建议改成通用日式手绘风。",
    )

    assert result["context_relevance"] > 0
    assert result["groundedness"] > 0
    assert result["answer_relevance"] > 0
    assert result["overall_score"] > 0


def test_agent_eval_dashboard_includes_trulens_rag_metrics():
    dashboard = PuzzleOpsAgent().eval_dashboard("日本")

    assert "TruLens Context Relevance" in dashboard
    assert "TruLens Groundedness" in dashboard
    assert "TruLens Answer Relevance" in dashboard


def test_agent_eval_suite_returns_cases_thresholds_and_fail_reasons():
    report = AgentEvalSuite(PuzzleOpsAgent()).run("日本")

    assert report.dataset_name == "PuzzleOps Agent Eval Set"
    assert report.cases
    assert report.metric_results
    assert all(metric.threshold > 0 for metric in report.metric_results)
    assert all(metric.status in {"PASS", "FAIL"} for metric in report.metric_results)
    assert any(case.expected_tools for case in report.cases)
    assert any("依据" in case.judge_reason for case in report.cases)
