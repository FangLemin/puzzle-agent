from __future__ import annotations

from collections import Counter

from puzzle_ops.models import HistoricalRecord, ImageFeature, ImageProfile, ValueRuleCandidate


class ImageFeatureExtractor:
    def extract(self, record: HistoricalRecord) -> ImageFeature:
        subjects = _subjects(record)
        risk_tags = _risk_tags(record)
        style = "AI插画" if record.source.upper() == "AI" else "素材网写实图"
        colors = _color_palette(record)
        composition = "主体居中，适合拼图识别；需保留前中后景层次"
        caption = (
            f"{record.country} {record.js_category} {record.operation_tag}，主体为{record.subject_tag}，"
            f"来源{record.source}，历史等级{record.grade}，多维度{record.dimension_grade}。"
        )
        return ImageFeature(
            image_id=record.image_id,
            main_subject=record.subject_tag,
            secondary_subjects=subjects,
            color_palette=colors,
            composition=composition,
            style=style,
            culture_elements=_culture_elements(record),
            festival_elements=_festival_elements(record),
            ai_artifacts=tuple(tag for tag in risk_tags if "AI" in tag),
            risk_tags=risk_tags,
            caption=caption,
            feature_confidence=0.82 if record.local_image_path else 0.72,
        )


class SimilarImageRetriever:
    def __init__(self, records: tuple[HistoricalRecord, ...], extractor: ImageFeatureExtractor):
        self.records = records
        self.extractor = extractor

    def profile_for(self, record: HistoricalRecord, top_k: int = 2) -> ImageProfile:
        feature = self.extractor.extract(record)
        good = self._similar(record, {"S", "A"}, top_k)
        bad = self._similar(record, {"C", "D"}, top_k)
        return ImageProfile(
            asset=record,
            feature=feature,
            historical_metrics={
                "grade": record.grade,
                "open_rate": record.open_rate,
                "completion_rate": record.completion_rate,
                "avg_finish_time": record.avg_finish_time,
                "position": record.position,
            },
            similar_good_cases=good,
            similar_bad_cases=bad,
            matched_value_rules=tuple(_positive_signals(record)),
            matched_audit_rules=feature.risk_tags,
        )

    def _similar(self, target: HistoricalRecord, grades: set[str], top_k: int) -> tuple[HistoricalRecord, ...]:
        candidates = [record for record in self.records if record.grade in grades and record.image_id != target.image_id]
        if target.grade in grades:
            candidates.append(target)
        ranked = sorted(candidates, key=lambda record: self._score(target, record), reverse=True)
        return tuple(ranked[:top_k])

    def _score(self, left: HistoricalRecord, right: HistoricalRecord) -> int:
        score = 0
        score += 4 if left.js_category == right.js_category else 0
        score += 3 if left.subject_tag == right.subject_tag else 0
        score += 2 if left.source == right.source else 0
        score += len(set(_tokens(left.operation_tag)) & set(_tokens(right.operation_tag)))
        return score


class ValueInsightMiner:
    def __init__(self, extractor: ImageFeatureExtractor):
        self.extractor = extractor

    def mine(self, records: tuple[HistoricalRecord, ...], country: str) -> tuple[ValueRuleCandidate, ...]:
        good = [record for record in records if record.country == country and record.grade in {"S", "A"}]
        bad = [record for record in records if record.country == country and record.grade in {"C", "D"}]
        if not good or not bad:
            return ()
        good_subjects = Counter(record.subject_tag for record in good)
        good_categories = Counter(record.js_category for record in good)
        top_subject = good_subjects.most_common(1)[0][0]
        top_category = good_categories.most_common(1)[0][0]
        ai_good = sum(1 for record in good if record.source.upper() == "AI")
        rule_text = f"{country}市场可优先关注{top_subject}/{top_category}类高表现元素，并结合SA图的清晰主体与文化语境做提需。"
        if ai_good:
            rule_text += " AI图需同步检查低质感与版权风格风险。"
        return (
            ValueRuleCandidate(
                candidate_id=f"{country}-candidate-{top_category}-{top_subject}",
                country=country,
                rule_text=rule_text,
                confidence=round(len(good) / max(len(records), 1), 2),
                support_count=len(good),
                counterexample_count=len(bad),
                evidence_image_ids=tuple(record.image_id for record in good[:3]),
                status="pending_review",
                agent_reason=f"近周期SA样本{len(good)}条，CD反例{len(bad)}条；{top_subject}/{top_category}在好图组更突出。",
            ),
        )


def _subjects(record: HistoricalRecord) -> tuple[str, ...]:
    text = record.operation_tag + record.subject_tag
    subjects = []
    for token in ("鲤鱼", "猫", "寿司", "抹茶", "景观", "天桥立", "怀旧"):
        if token in text and token != record.subject_tag:
            subjects.append(token)
    return tuple(subjects)


def _risk_tags(record: HistoricalRecord) -> tuple[str, ...]:
    text = record.operation_tag + record.remark
    risks = []
    if any(word in text for word in ("宫崎骏", "动漫", "IP", "名画", "商标")):
        risks.append("版权/IP风险")
    if record.source.upper() == "AI" and record.grade in {"C", "D"}:
        risks.append("AI低质感风险")
    return tuple(risks)


def _color_palette(record: HistoricalRecord) -> tuple[str, ...]:
    if "猫咪" in record.operation_tag:
        return ("暖米白", "湖蓝", "浅粉")
    if "寿司" in record.operation_tag or "抹茶" in record.operation_tag:
        return ("米白", "绿色", "暖黄")
    return ("自然色", "低饱和", "暖光")


def _culture_elements(record: HistoricalRecord) -> tuple[str, ...]:
    elements = []
    if "日本" == record.country:
        elements.append("日本市场语境")
    if any(word in record.operation_tag for word in ("天桥立", "抹茶", "寿司", "猫咪鲤鱼")):
        elements.append("本土文化元素")
    return tuple(elements)


def _festival_elements(record: HistoricalRecord) -> tuple[str, ...]:
    return tuple(word for word in ("樱花", "黄金周", "铃兰", "薰衣草") if word in record.operation_tag)


def _positive_signals(record: HistoricalRecord) -> tuple[str, ...]:
    signals = []
    if record.grade in {"S", "A"}:
        signals.append("历史SA好图")
    if record.dimension_grade.count("高") >= 2:
        signals.append("多维度高表现")
    if record.position in {1, 3, 5, 8, 10}:
        signals.append("重点位置有参考价值")
    return tuple(signals)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(part for part in text.replace("_", " ").split() if part)
