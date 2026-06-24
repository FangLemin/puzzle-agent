from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import math
import os
import re
from pathlib import Path
from typing import Callable
from urllib import request


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


@dataclass
class RagRuntimeStats:
    embedding_cache_hits: int = 0
    embedding_remote_calls: int = 0
    embedding_fallbacks: int = 0
    rerank_remote_calls: int = 0
    rerank_fallbacks: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "embedding_cache_hits": self.embedding_cache_hits,
            "embedding_remote_calls": self.embedding_remote_calls,
            "embedding_fallbacks": self.embedding_fallbacks,
            "rerank_remote_calls": self.rerank_remote_calls,
            "rerank_fallbacks": self.rerank_fallbacks,
        }


@dataclass(frozen=True)
class RagProviderConfig:
    embedding_provider: str = "local"
    embedding_model: str = "local-token-cosine"
    rerank_provider: str = "local"
    rerank_model: str = "local-rule-rerank"
    configured: bool = False
    remote_ready: bool = False
    remote_calls_enabled: bool = False
    api_key: str = ""
    embedding_endpoint: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    rerank_endpoint: str = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    status_text: str = "本地 fallback：token/cosine embedding + 规则 rerank"

    @classmethod
    def from_env(cls, load_env: bool = True) -> "RagProviderConfig":
        if load_env:
            _load_env_file(Path.cwd() / ".env")
        embedding_provider = os.getenv("RAG_EMBEDDING_PROVIDER", "local").strip().lower() or "local"
        rerank_provider = os.getenv("RAG_RERANK_PROVIDER", "local").strip().lower() or "local"
        embedding_model = os.getenv("RAG_EMBEDDING_MODEL", "local-token-cosine").strip() or "local-token-cosine"
        rerank_model = os.getenv("RAG_RERANK_MODEL", "local-rule-rerank").strip() or "local-rule-rerank"
        api_key = os.getenv("RAG_API_KEY", os.getenv("DASHSCOPE_API_KEY", "")).strip()
        embedding_endpoint = os.getenv("RAG_EMBEDDING_ENDPOINT", "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings").strip()
        rerank_endpoint = os.getenv("RAG_RERANK_ENDPOINT", "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank").strip()
        configured = embedding_provider != "local" or rerank_provider != "local"
        remote_ready = configured and bool(api_key)
        remote_calls_enabled = remote_ready and os.getenv("RAG_ENABLE_REMOTE_CALLS", "").strip().lower() in {"1", "true", "yes", "on"}
        if remote_ready:
            status = (
                f"外部 provider 可调用：Embedding={embedding_provider}/{embedding_model}；Rerank={rerank_provider}/{rerank_model}"
                if remote_calls_enabled
                else f"外部 provider 已具备 key，但 RAG_ENABLE_REMOTE_CALLS 未开启；当前使用本地 fallback"
            )
        elif configured:
            status = (
                f"外部 provider 已声明但缺少 RAG_API_KEY 或 DASHSCOPE_API_KEY；"
                f"Embedding={embedding_provider}/{embedding_model}；Rerank={rerank_provider}/{rerank_model}；当前使用本地 fallback"
            )
        else:
            status = "本地 fallback：token/cosine embedding + 规则 rerank"
        return cls(
            embedding_provider,
            embedding_model,
            rerank_provider,
            rerank_model,
            configured,
            remote_ready,
            remote_calls_enabled,
            api_key,
            embedding_endpoint,
            rerank_endpoint,
            status,
        )


class LocalEmbeddingProvider:
    provider_name = "local-token-cosine"

    def similarity(self, query: str, text: str) -> float:
        return _cosine(_tokens(query), _tokens(text))

    def similarities(self, query: str, texts: tuple[str, ...]) -> tuple[float, ...]:
        return tuple(self.similarity(query, text) for text in texts)


class LocalRerankProvider:
    provider_name = "local-rule-rerank"

    def rerank(self, query: str, country: str, chunk: RagChunk, bm25_score: float, vector_score: float) -> float:
        return _rerank_score(query, country, chunk, bm25_score, vector_score)

    def rerank_many(
        self,
        query: str,
        country: str,
        candidates: tuple[tuple[RagChunk, float, float], ...],
    ) -> tuple[float, ...]:
        return tuple(self.rerank(query, country, chunk, bm25, vector) for chunk, bm25, vector in candidates)


class ConfiguredEmbeddingProvider(LocalEmbeddingProvider):
    def __init__(self, provider_name: str, model: str):
        self.provider_name = provider_name
        self.model = model


class ConfiguredRerankProvider(LocalRerankProvider):
    def __init__(self, provider_name: str, model: str):
        self.provider_name = provider_name
        self.model = model


class FeedbackAwareRerankProvider(LocalRerankProvider):
    def __init__(self, base_provider: LocalRerankProvider, feedback_scores: dict[str, int], weight: float = 0.35):
        self.base_provider = base_provider
        self.feedback_scores = feedback_scores
        self.weight = weight
        self.provider_name = f"{base_provider.provider_name}+feedback"

    def rerank(self, query: str, country: str, chunk: RagChunk, bm25_score: float, vector_score: float) -> float:
        base_score = self.base_provider.rerank(query, country, chunk, bm25_score, vector_score)
        return base_score + self.feedback_scores.get(chunk.chunk_id, 0) * self.weight

    def rerank_many(
        self,
        query: str,
        country: str,
        candidates: tuple[tuple[RagChunk, float, float], ...],
    ) -> tuple[float, ...]:
        base_scores = self.base_provider.rerank_many(query, country, candidates)
        return tuple(
            score + self.feedback_scores.get(chunk.chunk_id, 0) * self.weight
            for score, (chunk, _, _) in zip(base_scores, candidates)
        )


class DashScopeEmbeddingProvider(LocalEmbeddingProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        endpoint: str,
        transport: Callable[[list[str], str, str, str], dict[str, object]] | None = None,
        cache_get: Callable[[str, str, str], tuple[float, ...] | None] | None = None,
        cache_set: Callable[[str, str, str, tuple[float, ...]], None] | None = None,
        stats: RagRuntimeStats | None = None,
        batch_size: int = 10,
    ):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        self.transport = transport or _dashscope_embedding_transport
        self.provider_name = f"dashscope:{model}"
        self._cache: dict[str, tuple[float, ...]] = {}
        self.cache_get = cache_get
        self.cache_set = cache_set
        self.stats = stats or RagRuntimeStats()
        self.batch_size = max(batch_size, 1)

    def similarity(self, query: str, text: str) -> float:
        return self.similarities(query, (text,))[0]

    def similarities(self, query: str, texts: tuple[str, ...]) -> tuple[float, ...]:
        try:
            vectors = self._embeddings_batch((query, *texts))
        except Exception:
            self.stats.embedding_fallbacks += 1
            return tuple(LocalEmbeddingProvider.similarity(self, query, text) for text in texts)
        query_vector = vectors[0]
        return tuple(_vector_cosine(query_vector, text_vector) for text_vector in vectors[1:])

    def _embedding(self, text: str) -> tuple[float, ...]:
        return self._embeddings_batch((text,))[0]

    def _embeddings_batch(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        missing: list[str] = []
        for text in dict.fromkeys(texts):
            if text in self._cache:
                self.stats.embedding_cache_hits += 1
                continue
            cached = self.cache_get("dashscope", self.model, text) if self.cache_get else None
            if cached is not None:
                self.stats.embedding_cache_hits += 1
                self._cache[text] = cached
            else:
                missing.append(text)
        if missing:
            for start in range(0, len(missing), self.batch_size):
                batch = missing[start : start + self.batch_size]
                self.stats.embedding_remote_calls += 1
                response = self.transport(batch, self.api_key, self.endpoint, self.model)
                vectors = _extract_embedding_vectors(response)
                if len(vectors) != len(batch) or any(not vector for vector in vectors):
                    raise RuntimeError("embedding provider 返回向量数量不完整")
                for text, vector in zip(batch, vectors):
                    self._cache[text] = vector
                    if self.cache_set:
                        self.cache_set("dashscope", self.model, text, vector)
        return tuple(self._cache[text] for text in texts)


class DashScopeRerankProvider(LocalRerankProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        endpoint: str,
        transport: Callable[[str, list[str], str, str, str], dict[str, object]] | None = None,
        stats: RagRuntimeStats | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        self.transport = transport or _dashscope_rerank_transport
        self.provider_name = f"dashscope:{model}"
        self.stats = stats or RagRuntimeStats()

    def rerank(self, query: str, country: str, chunk: RagChunk, bm25_score: float, vector_score: float) -> float:
        document = f"{chunk.title}：{chunk.text}"
        try:
            self.stats.rerank_remote_calls += 1
            response = self.transport(query, [document], self.api_key, self.endpoint, self.model)
            score = _extract_rerank_score(response)
        except Exception:
            self.stats.rerank_fallbacks += 1
            return super().rerank(query, country, chunk, bm25_score, vector_score)
        if score is None:
            self.stats.rerank_fallbacks += 1
            return super().rerank(query, country, chunk, bm25_score, vector_score)
        return score

    def rerank_many(
        self,
        query: str,
        country: str,
        candidates: tuple[tuple[RagChunk, float, float], ...],
    ) -> tuple[float, ...]:
        if not candidates:
            return ()
        documents = [f"{chunk.title}：{chunk.text}" for chunk, _, _ in candidates]
        try:
            self.stats.rerank_remote_calls += 1
            response = self.transport(query, documents, self.api_key, self.endpoint, self.model)
            remote_scores = _extract_rerank_scores(response, len(candidates))
        except Exception:
            self.stats.rerank_fallbacks += len(candidates)
            return tuple(
                LocalRerankProvider.rerank(self, query, country, chunk, bm25, vector)
                for chunk, bm25, vector in candidates
            )
        scores = []
        for index, (chunk, bm25, vector) in enumerate(candidates):
            score = remote_scores[index]
            if score is None:
                self.stats.rerank_fallbacks += 1
                score = super().rerank(query, country, chunk, bm25, vector)
            scores.append(score)
        return tuple(scores)


def providers_from_config(
    config: RagProviderConfig | None = None,
    *,
    stats: RagRuntimeStats | None = None,
    cache_get: Callable[[str, str, str], tuple[float, ...] | None] | None = None,
    cache_set: Callable[[str, str, str, tuple[float, ...]], None] | None = None,
) -> tuple[LocalEmbeddingProvider, LocalRerankProvider]:
    config = config or RagProviderConfig.from_env()
    stats = stats or RagRuntimeStats()
    if config.remote_calls_enabled and config.embedding_provider == "dashscope":
        embedding: LocalEmbeddingProvider = DashScopeEmbeddingProvider(
            config.api_key,
            config.embedding_model,
            config.embedding_endpoint,
            cache_get=cache_get,
            cache_set=cache_set,
            stats=stats,
        )
    elif config.embedding_provider == "local" or not config.remote_calls_enabled:
        embedding = LocalEmbeddingProvider()
    else:
        embedding = ConfiguredEmbeddingProvider(config.embedding_provider, config.embedding_model)

    if config.remote_calls_enabled and config.rerank_provider == "dashscope":
        rerank: LocalRerankProvider = DashScopeRerankProvider(
            config.api_key,
            config.rerank_model,
            config.rerank_endpoint,
            stats=stats,
        )
    elif config.rerank_provider == "local" or not config.remote_calls_enabled:
        rerank = LocalRerankProvider()
    else:
        rerank = ConfiguredRerankProvider(config.rerank_provider, config.rerank_model)
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
        eligible_chunks = tuple(
            chunk
            for chunk in self.chunks
            if chunk.country in {country, "GLOBAL"} and (not allowed_sources or chunk.source_type in allowed_sources)
        )
        vector_scores = self.embedding_provider.similarities(
            query,
            tuple(chunk.text + " " + chunk.title for chunk in eligible_chunks),
        )
        candidates: list[tuple[RagChunk, float, float]] = []
        for chunk, vector in zip(eligible_chunks, vector_scores):
            bm25 = self._bm25(query_tokens, chunk)
            if bm25 > 0 or vector > 0 or _has_exact_phrase(query, chunk.text):
                candidates.append((chunk, bm25, vector))
        rerank_scores = self.rerank_provider.rerank_many(query, country, tuple(candidates))
        hits = [
            RagHit(
                chunk,
                round(bm25, 4),
                round(vector, 4),
                round(rerank, 4),
                _reason(chunk, bm25, vector, self.embedding_provider.provider_name, self.rerank_provider.provider_name),
            )
            for (chunk, bm25, vector), rerank in zip(candidates, rerank_scores)
        ]
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


def _vector_cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
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


def _dashscope_embedding_transport(texts: list[str], api_key: str, endpoint: str, model: str) -> dict[str, object]:
    payload = {"model": model, "input": texts}
    return _post_json(endpoint, payload, api_key)


def _dashscope_rerank_transport(query: str, documents: list[str], api_key: str, endpoint: str, model: str) -> dict[str, object]:
    payload = {"model": model, "input": {"query": query, "documents": documents}, "parameters": {"return_documents": False}}
    return _post_json(endpoint, payload, api_key)


def _post_json(endpoint: str, payload: dict[str, object], api_key: str) -> dict[str, object]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_embedding_vectors(response: dict[str, object]) -> tuple[tuple[float, ...], ...]:
    data = response.get("data")
    if isinstance(data, list):
        vectors = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("embedding"), list):
                vectors.append(tuple(float(value) for value in item["embedding"]))
        return tuple(vectors)
    output = response.get("output")
    if isinstance(output, dict) and isinstance(output.get("embeddings"), list):
        vectors = []
        for item in output["embeddings"]:
            if isinstance(item, dict) and isinstance(item.get("embedding"), list):
                vectors.append(tuple(float(value) for value in item["embedding"]))
        return tuple(vectors)
    return ()


def _extract_rerank_score(response: dict[str, object]) -> float | None:
    results = response.get("results")
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict):
            score = first.get("relevance_score", first.get("score"))
            if score is not None:
                return float(score)
    output = response.get("output")
    if isinstance(output, dict) and isinstance(output.get("results"), list) and output["results"]:
        first = output["results"][0]
        if isinstance(first, dict):
            score = first.get("relevance_score", first.get("score"))
            if score is not None:
                return float(score)
    return None


def _extract_rerank_scores(response: dict[str, object], count: int) -> tuple[float | None, ...]:
    scores: list[float | None] = [None] * count
    results = response.get("results")
    if not isinstance(results, list):
        output = response.get("output")
        results = output.get("results") if isinstance(output, dict) else None
    if not isinstance(results, list):
        return tuple(scores)
    for fallback_index, item in enumerate(results):
        if not isinstance(item, dict):
            continue
        index = int(item.get("index", fallback_index))
        value = item.get("relevance_score", item.get("score"))
        if 0 <= index < count and value is not None:
            scores[index] = float(value)
    return tuple(scores)
