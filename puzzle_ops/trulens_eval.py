import re


class TruLensRAGEvaluator:
    """Lightweight RAG Triad evaluator compatible with local demo execution.

    The project keeps this as an adapter so a real TruLens provider can replace
    the local scorer without changing Agent evaluation contracts.
    """

    def evaluate(self, query: str, contexts: tuple[str, ...], answer: str) -> dict[str, float]:
        context_text = "\n".join(contexts)
        context_relevance = _overlap_score(query, context_text)
        groundedness = _overlap_score(answer, context_text)
        answer_relevance = _overlap_score(query, answer)
        overall = (context_relevance + groundedness + answer_relevance) / 3
        return {
            "context_relevance": round(context_relevance, 3),
            "groundedness": round(groundedness, 3),
            "answer_relevance": round(answer_relevance, 3),
            "overall_score": round(overall, 3),
        }


def _overlap_score(source: str, target: str) -> float:
    source_tokens = _tokens(source)
    target_tokens = _tokens(target)
    if not source_tokens or not target_tokens:
        return 0.0
    return len(source_tokens & target_tokens) / len(source_tokens)


def _tokens(text: str) -> set[str]:
    normalized = text.lower()
    latin_words = set(re.findall(r"[a-z0-9]+", normalized))
    cjk_chars = set(re.findall(r"[\u4e00-\u9fff]", normalized))
    return latin_words | cjk_chars
