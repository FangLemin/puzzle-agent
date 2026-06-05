from puzzle_ops.agents import PuzzleOpsAgent
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
