from pathlib import Path

from puzzle_ops.audit import AuditPolicyRetriever, AuditRuleEngine
from puzzle_ops.excel_importer import import_history_workbook
from puzzle_ops.multimodal import ImageFeatureExtractor, SimilarImageRetriever, ValueInsightMiner


FIXTURE = Path("/Users/fanglemin/Desktop/日本数据示例.xlsx")
AUDIT_DOC = Path("/Users/fanglemin/Desktop/拼图审核手册.docx")


def records(tmp_path):
    return import_history_workbook(FIXTURE, "日本", tmp_path / "images")


def test_image_feature_extractor_builds_structured_feature_from_real_row(tmp_path):
    feature = ImageFeatureExtractor().extract(records(tmp_path)[0])

    assert feature.image_id == "550e8400-e29b-41d4-a716-446655440000"
    assert feature.main_subject == "猫"
    assert "鲤鱼" in feature.secondary_subjects
    assert "animal" in feature.caption
    assert feature.feature_confidence >= 0.7
    assert feature.risk_tags == ()


def test_similar_retriever_returns_good_and_bad_evidence(tmp_path):
    history = records(tmp_path)
    retriever = SimilarImageRetriever(history, ImageFeatureExtractor())

    profile = retriever.profile_for(history[0])

    assert profile.asset.image_id == history[0].image_id
    assert profile.similar_good_cases
    assert profile.similar_bad_cases
    assert all(case.grade in {"S", "A"} for case in profile.similar_good_cases)
    assert all(case.grade in {"C", "D"} for case in profile.similar_bad_cases)


def test_value_insight_miner_creates_pending_candidate_with_evidence(tmp_path):
    candidate = ValueInsightMiner(ImageFeatureExtractor()).mine(records(tmp_path), "日本")[0]

    assert candidate.country == "日本"
    assert candidate.status == "pending_review"
    assert "SA" in candidate.agent_reason
    assert candidate.support_count >= 1
    assert candidate.counterexample_count >= 1
    assert candidate.evidence_image_ids
    assert "猫" in candidate.rule_text or "AI" in candidate.rule_text


def test_audit_policy_retriever_loads_docx_and_recalls_matching_rules():
    retriever = AuditPolicyRetriever.from_docx(AUDIT_DOC)

    hits = retriever.search("宫崎骏 动漫 角色")

    assert hits
    assert any("宫崎骏" in hit.text or "动漫" in hit.text for hit in hits)


def test_audit_rule_engine_combines_redline_rule_and_policy_evidence():
    retriever = AuditPolicyRetriever.from_docx(AUDIT_DOC)
    engine = AuditRuleEngine(retriever)

    result = engine.review_text("宫崎骏风浴袍少女，红色连衣裙黑发女孩")

    assert result.risk_level == "高"
    assert "知名动漫" in result.reason or "宫崎骏" in result.reason
    assert result.evidence
    assert "建议" in result.suggestion
