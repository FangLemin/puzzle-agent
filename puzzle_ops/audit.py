from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from puzzle_ops.models import AuditPolicyHit, AuditReviewResult


class AuditPolicyRetriever:
    def __init__(self, hits: tuple[AuditPolicyHit, ...]):
        self.hits = hits

    @classmethod
    def from_docx(cls, path: Path | str) -> "AuditPolicyRetriever":
        hits = []
        for index, text in enumerate(_docx_paragraphs(Path(path)), 1):
            if not text:
                continue
            risk_level = "高" if any(word in text for word in ("红线", "绝对禁止", "极高", "侵权")) else "中"
            hits.append(AuditPolicyHit(f"policy-{index}", text, risk_level))
        return cls(tuple(hits))

    def search(self, query: str, top_k: int = 5) -> tuple[AuditPolicyHit, ...]:
        terms = tuple(term for term in query.replace("，", " ").replace("、", " ").split() if term)
        ranked = sorted(self.hits, key=lambda hit: self._score(hit.text, terms), reverse=True)
        return tuple(hit for hit in ranked if self._score(hit.text, terms) > 0)[:top_k]

    def _score(self, text: str, terms: tuple[str, ...]) -> int:
        score = sum(2 for term in terms if term in text)
        if "动漫" in text and any(term in {"宫崎骏", "角色", "动漫"} for term in terms):
            score += 3
        if "宫崎骏" in text and "宫崎骏" in terms:
            score += 4
        return score


class AuditRuleEngine:
    def __init__(self, retriever: AuditPolicyRetriever):
        self.retriever = retriever

    def review_text(self, text: str) -> AuditReviewResult:
        hits = self.retriever.search(text)
        risk_level = "低"
        reason = "未命中明显红线风险。"
        suggestion = "建议保留来源记录，继续进行常规质量审核。"
        if any(word in text for word in ("宫崎骏", "龙猫", "动漫", "角色", "红色连衣裙黑发女孩")):
            risk_level = "高"
            reason = "疑似知名动漫/IP或宫崎骏相关视觉混淆风险。"
            suggestion = "建议改为通用日式温暖手绘风，删除角色化脸型、服饰和标志性场景。"
        elif any(word in text for word in ("名画", "博物馆", "商标", "LOGO")):
            risk_level = "高"
            reason = "疑似名画/博物馆图片/商标红线风险。"
            suggestion = "建议替换为原创素材或授权图库素材，并保留授权凭证。"
        evidence = tuple(hit.text for hit in hits[:3])
        return AuditReviewResult(risk_level, reason, evidence, suggestion)


def _docx_paragraphs(path: Path) -> tuple[str, ...]:
    with ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for paragraph in root.findall(".//w:p", ns):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns)).strip()
        if text:
            paragraphs.append(text)
    return tuple(paragraphs)
