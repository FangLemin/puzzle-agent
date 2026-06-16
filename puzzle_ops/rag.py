from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import os
import re
from pathlib import Path


@dataclass(frozen=True)
class RagDocument:
    document_id: str
    country: str
    source_type: str
    title: str
    text: str
    metadata: dict[str, object]


@dataclass(frozen=True)
class RagChunk:
    chunk_id: str
    parent_id: str
    country: str
    source_type: str
    title: str
    text: str
    chunk_index: int
    metadata: dict[str, object]


@dataclass(frozen=True)
class RagHit:
    chunk: RagChunk
    bm25_score: float
    vector_score: float
    rerank_score: float
    reason: str


@dataclass(frozen=True)
class RagPrompt:
    query: str
    context: str
    citations: tuple[str, ...]
    prompt: str


@dataclass(frozen=True)
class RagProviderConfig:
    embedding_provider: str = "local"
    embedding_model: str = "local-token-cosine"
    rerank_provider: str = "local"
    rerank_model: str = "local-rule-rerank"
    configured: bool = False
    status_text: str = "本地 fallback：token/cosine embedding + 规则 rerank"

    @classmethod
    def from_env(cls, load_env: bool = True) -> "RagProviderConfig":
        if load_env:
            _load_env_file(Path.cwd() / ".env")
        embedding_provider = os.getenv("RAG_EMBEDDING_PROVIDER", "local").strip().lower() or "local"
        rerank_provider = os.getenv("RAG_RERANK_PROVIDER", "local").strip().lower() or "local"
        embedding_model = os.getenv("RAG_EMBEDDING_MODEL", "local-token-cosine").strip() or "local-token-cosine"
        rerank_model = os.getenv("RAG_RERANK_MODEL", "local-rule-rerank").strip() or "local-rule-rerank"
        configured = embedding_provider != "local" or rerank_provider != "local"
        status = (
            f"外部 provider 已配置：Embedding={embedding_provider}/{embedding_model}；Rerank={rerank_provider}/{rerank_model}"
            if configured
            else "本地 fallback：token/cosine embedding + 规则 rerank"
        )
        return cls(embedding_provider, embedding_model, rerank_provider, rerank_model, configured, status)


class LocalEmbeddingProvider:
    provider_name = "local-token-cosine"

    def similarity(self, query: str, text: str) -> float:
        return _cosine(_tokens(query), _tokens(text))


class LocalRerankProvider:
    provider_name = "local-rule-rerank"

    def rerank(self, query: str, country: str, chunk: RagChunk, bm25_score: float, vector_score: float) -> float:
        return _rerank_score(query, country, chunk, bm25_score, vector_score)


class ConfiguredEmbeddingProvider(LocalEmbeddingProvider):
    def __init__(self, provider_name: str, model: str):
        self.provider_name = provider_name
        self.model = model


class ConfiguredRerankProvider(LocalRerankProvider):
    def __init__(self, provider_name: str, model: str):
        self.provider_name = provider_name
        self.model = model


def providers_from_config(config: RagProviderConfig | None = None) -> tuple[LocalEmbeddingProvider, LocalRerankProvider]:
    config = config or RagProviderConfig.from_env()
    embedding = (
        LocalEmbeddingProvider()
        if config.embedding_provider == "local"
        else ConfiguredEmbeddingProvider(config.embedding_provider, config.embedding_model)
    )
    rerank = (
        LocalRerankProvider()
        if config.rerank_provider == "local"
        else ConfiguredRerankProvider(config.rerank_provider, config.rerank_model)
    )
    return embedding, rerank


def chunk_document(document: RagDocument, max_chars: int = 220, overlap_sentences: int = 1) -> tuple[RagChunk, ...]:
    sentences = _sentences(document.text)
    if not sentences:
        return ()
    chunks: list[RagChunk] = []
    current: list[str] = []
    index = 1
    for sentence in sentences:
        candidate = "".join(current + [sentence])
        if current and len(candidate) > max_chars:
            chunks.append(_make_chunk(document, current, index))
            index += 1
            current = current[-overlap_sentences:] if overlap_sentences else []
        current.append(sentence)
    if current:
        chunks.append(_make_chunk(document, current, index))
    return tuple(chunks)


def build_rag_prompt(query: str, hits: tuple[RagHit, ...]) -> RagPrompt:
    context_lines = [f"[{hit.chunk.chunk_id}] {hit.chunk.title}：{hit.chunk.text}" for hit in hits]
    context = "\n".join(context_lines)
    citations = tuple(hit.chunk.chunk_id for hit in hits)
    prompt = (
        "你是 PuzzleOps 出海拼图内容运营 Agent。\n"
        "只基于引用依据回答，禁止编造未提供的事实；如果依据不足，要明确说需要人工复核。\n"
        "请围绕当前提需判断是否符合国家价值观，并给出可发散的新拼图内容方向。\n\n"
        f"问题：{query}\n\n"
        f"引用依据：\n{context}\n\n"
        "输出格式：\n"
        "主体内容：\n"
        "色彩氛围：\n"
        "构图环境：\n"
        "价值观判断：\n"
        "发散提需建议：\n"
        "风险提醒：\n"
        "引用依据："
    )
    return RagPrompt(query=query, context=context, citations=citations, prompt=prompt)


class HybridRagRetriever:
    def __init__(
        self,
        chunks: tuple[RagChunk, ...],
        *,
        embedding_provider: LocalEmbeddingProvider | None = None,
        rerank_provider: LocalRerankProvider | None = None,
    ):
        self.chunks = chunks
        self.embedding_provider = embedding_provider or LocalEmbeddingProvider()
        self.rerank_provider = rerank_provider or LocalRerankProvider()
        self._tokenized = {chunk.chunk_id: _tokens(chunk.text + " " + chunk.title) for chunk in chunks}
        self._doc_freq = self._document_frequency()
        self._avg_len = sum(len(tokens) for tokens in self._tokenized.values()) / max(len(self._tokenized), 1)

    def search(
        self,
        query: str,
        *,
        country: str,
        top_k: int = 6,
        source_types: tuple[str, ...] | None = None,
    ) -> tuple[RagHit, ...]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return ()
        allowed_sources = set(source_types or ())
        hits = []
        for chunk in self.chunks:
            if chunk.country not in {country, "GLOBAL"}:
                continue
            if allowed_sources and chunk.source_type not in allowed_sources:
                continue
            bm25 = self._bm25(query_tokens, chunk)
            vector = self.embedding_provider.similarity(query, chunk.text + " " + chunk.title)
            rerank = self.rerank_provider.rerank(query, country, chunk, bm25, vector)
            if bm25 > 0 or vector > 0 or _has_exact_phrase(query, chunk.text):
                hits.append(RagHit(chunk, round(bm25, 4), round(vector, 4), round(rerank, 4), _reason(chunk, bm25, vector, self.embedding_provider.provider_name, self.rerank_provider.provider_name)))
        ranked = sorted(hits, key=lambda hit: hit.rerank_score, reverse=True)
        return tuple(ranked[:top_k])

    def _document_frequency(self) -> Counter[str]:
        counter: Counter[str] = Counter()
        for tokens in self._tokenized.values():
            counter.update(set(tokens))
        return counter

    def _bm25(self, query_tokens: tuple[str, ...], chunk: RagChunk) -> float:
        tokens = self._tokenized[chunk.chunk_id]
        if not tokens:
            return 0.0
        counts = Counter(tokens)
        score = 0.0
        k1 = 1.4
        b = 0.75
        doc_count = max(len(self.chunks), 1)
        doc_len = len(tokens)
        for token in query_tokens:
            freq = counts[token]
            if freq == 0:
                continue
            doc_freq = self._doc_freq[token]
            idf = math.log(1 + (doc_count - doc_freq + 0.5) / (doc_freq + 0.5))
            denom = freq + k1 * (1 - b + b * doc_len / max(self._avg_len, 1))
            score += idf * freq * (k1 + 1) / denom
        return score


def _make_chunk(document: RagDocument, sentences: list[str], index: int) -> RagChunk:
    return RagChunk(
        chunk_id=f"{document.document_id}#chunk-{index}",
        parent_id=document.document_id,
        country=document.country,
        source_type=document.source_type,
        title=document.title,
        text="".join(sentences).strip(),
        chunk_index=index,
        metadata=document.metadata,
    )


def _sentences(text: str) -> tuple[str, ...]:
    cleaned = re.sub(r"\s+", " ", text.strip())
    if not cleaned:
        return ()
    parts = re.findall(r"[^。！？；;.!?\n]+[。！？；;.!?]?", cleaned)
    return tuple(part.strip() for part in parts if part.strip())


def _tokens(text: str) -> tuple[str, ...]:
    normalized = text.lower()
    latin = re.findall(r"[a-z0-9]+", normalized)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    cjk_bigrams = [left + right for left, right in zip(cjk_chars, cjk_chars[1:])]
    return tuple(latin + cjk_chars + cjk_bigrams)


def _cosine(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if not left or not right:
        return 0.0
    left_counts = Counter(left)
    right_counts = Counter(right)
    shared = set(left_counts) & set(right_counts)
    dot = sum(left_counts[token] * right_counts[token] for token in shared)
    left_norm = math.sqrt(sum(value * value for value in left_counts.values()))
    right_norm = math.sqrt(sum(value * value for value in right_counts.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _rerank_score(query: str, country: str, chunk: RagChunk, bm25: float, vector: float) -> float:
    score = bm25 * 0.56 + vector * 0.34
    if chunk.country == country:
        score += 2.4
    if chunk.source_type == "approved_value_rule":
        score += 0.18
    elif chunk.source_type == "value_rule":
        score += 0.14
    elif chunk.source_type == "audit_policy" and any(word in query for word in ("风险", "审核", "水印", "IP", "版权", "商标")):
        score += 0.18
    elif chunk.source_type == "fact":
        score += 0.1
    if _has_exact_phrase(query, chunk.text):
        score += 0.25
    return score


def _has_exact_phrase(query: str, text: str) -> bool:
    cjk_terms = re.findall(r"[\u4e00-\u9fff]{2,}", query)
    return any(term in text for term in cjk_terms)


def _reason(chunk: RagChunk, bm25: float, vector: float, embedding_provider: str, rerank_provider: str) -> str:
    return f"{chunk.source_type}命中；BM25={bm25:.2f}；Embedding={embedding_provider}:{vector:.2f}；Rerank={rerank_provider}"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
