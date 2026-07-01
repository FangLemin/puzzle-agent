from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
import re
from pathlib import Path
from typing import Callable
from urllib import request
import uuid


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
class RagRetrievalCase:
    query: str
    country: str
    expected_parent_id: str


@dataclass(frozen=True)
class RagIndexArtifacts:
    output_dir: Path
    manifest_path: Path
    documents_path: Path
    chunks_path: Path
    manifest: dict[str, object]


@dataclass(frozen=True)
class QdrantPoint:
    id: str
    vector: tuple[float, ...]
    payload: dict[str, object]


@dataclass(frozen=True)
class RagRetrievalTrace:
    query: str
    country: str
    eligible_chunk_count: int
    bm25_top_k: int
    vector_top_k: int
    rerank_top_k: int
    bm25_candidates: tuple[str, ...]
    vector_candidates: tuple[str, ...]
    exact_match_candidates: tuple[str, ...]
    merged_candidate_count: int
    embedding_provider: str
    rerank_provider: str
    final_hits: tuple[RagHit, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "country": self.country,
            "eligible_chunk_count": self.eligible_chunk_count,
            "bm25_top_k": self.bm25_top_k,
            "vector_top_k": self.vector_top_k,
            "rerank_top_k": self.rerank_top_k,
            "bm25_candidates": self.bm25_candidates,
            "vector_candidates": self.vector_candidates,
            "exact_match_candidates": self.exact_match_candidates,
            "merged_candidate_count": self.merged_candidate_count,
            "embedding_provider": self.embedding_provider,
            "rerank_provider": self.rerank_provider,
            "final_hits": tuple(
                {
                    "chunk_id": hit.chunk.chunk_id,
                    "parent_id": hit.chunk.parent_id,
                    "country": hit.chunk.country,
                    "source_type": hit.chunk.source_type,
                    "title": hit.chunk.title,
                    "bm25_score": hit.bm25_score,
                    "vector_score": hit.vector_score,
                    "rerank_score": hit.rerank_score,
                    "reason": hit.reason,
                }
                for hit in self.final_hits
            ),
        }


@dataclass(frozen=True)
class RagChunkingConfig:
    chunk_size_tokens: int = 600
    chunk_overlap_tokens: int = 100
    splitter: str = "sentence_token"


class StaticDocumentLoaderAdapter:
    def __init__(self, documents: tuple[RagDocument, ...]):
        self.documents = documents

    def load(self) -> tuple[RagDocument, ...]:
        return self.documents


class FileDocumentLoaderAdapter:
    def __init__(self, paths: tuple[Path | str, ...]):
        self.paths = tuple(Path(path) for path in paths)

    def load(self) -> tuple[RagDocument, ...]:
        documents: list[RagDocument] = []
        for path in self.paths:
            if path.is_dir():
                for child in sorted(path.glob("*.jsonl")):
                    documents.extend(load_rag_documents_jsonl(child))
            elif path.exists():
                documents.extend(load_rag_documents_jsonl(path))
        return tuple(documents)


class RetrievalCaseLoaderAdapter:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> tuple[RagRetrievalCase, ...]:
        return load_retrieval_cases_jsonl(self.path)


def load_rag_documents_jsonl(path: Path | str) -> tuple[RagDocument, ...]:
    source = Path(path)
    documents: list[RagDocument] = []
    if not source.exists():
        return ()
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        if not isinstance(payload, dict):
            raise ValueError(f"RAG document JSONL 第 {line_number} 行不是对象：{source}")
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata = dict(metadata)
        metadata.setdefault("source_file", str(source))
        documents.append(
            RagDocument(
                document_id=str(payload["document_id"]),
                country=str(payload["country"]),
                source_type=str(payload["source_type"]),
                title=str(payload["title"]),
                text=str(payload["text"]),
                metadata=metadata,
            )
        )
    return tuple(documents)


def load_retrieval_cases_jsonl(path: Path | str) -> tuple[RagRetrievalCase, ...]:
    source = Path(path)
    cases: list[RagRetrievalCase] = []
    if not source.exists():
        return ()
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        if not isinstance(payload, dict):
            raise ValueError(f"RAG eval case JSONL 第 {line_number} 行不是对象：{source}")
        cases.append(
            RagRetrievalCase(
                query=str(payload["query"]),
                country=str(payload["country"]),
                expected_parent_id=str(payload["expected_parent_id"]),
            )
        )
    return tuple(cases)


@dataclass(frozen=True)
class RagVectorStoreConfig:
    provider: str = "sqlite"
    endpoint: str = ""
    collection: str = "puzzle_ops_rag"
    api_key: str = ""
    configured: bool = False
    ready: bool = True
    status_text: str = "SQLite 本地 chunk store + embedding cache"

    @classmethod
    def from_env(cls, load_env: bool = True) -> "RagVectorStoreConfig":
        if load_env:
            _load_env_file(Path.cwd() / ".env")
        provider = os.getenv("RAG_VECTOR_STORE_PROVIDER", "sqlite").strip().lower() or "sqlite"
        endpoint = os.getenv("QDRANT_URL", os.getenv("RAG_QDRANT_URL", "")).strip().rstrip("/")
        collection = os.getenv("QDRANT_COLLECTION", os.getenv("RAG_QDRANT_COLLECTION", "puzzle_ops_rag")).strip() or "puzzle_ops_rag"
        api_key = os.getenv("QDRANT_API_KEY", os.getenv("RAG_QDRANT_API_KEY", "")).strip()
        if provider == "qdrant":
            ready = bool(endpoint and collection)
            status = (
                f"Qdrant ready：{endpoint} / {collection}"
                if ready
                else "Qdrant 已声明但缺少 QDRANT_URL 或 QDRANT_COLLECTION"
            )
            return cls(provider, endpoint, collection, api_key, True, ready, status)
        return cls()


class QdrantVectorStore:
    def __init__(
        self,
        config: RagVectorStoreConfig,
        transport: Callable[[str, dict[str, object], str], dict[str, object]] | None = None,
    ):
        self.config = config
        self.transport = transport or _post_json

    def upsert(self, points: tuple[QdrantPoint, ...]) -> dict[str, object]:
        if self.config.provider != "qdrant" or not self.config.ready:
            raise RuntimeError("Qdrant vector store 未就绪")
        endpoint = f"{self.config.endpoint}/collections/{self.config.collection}/points?wait=true"
        payload = {
            "points": [
                {
                    "id": point.id,
                    "vector": list(point.vector),
                    "payload": point.payload,
                }
                for point in points
            ]
        }
        return self.transport(endpoint, payload, self.config.api_key)


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
        default_embedding_model = "text-embedding-v4" if embedding_provider == "dashscope" else "local-token-cosine"
        if rerank_provider == "dashscope":
            default_rerank_model = "qwen3-rerank"
        elif rerank_provider in {"bge", "bge-reranker", "baai"}:
            default_rerank_model = "BAAI/bge-reranker-v2-m3"
        else:
            default_rerank_model = "local-rule-rerank"
        embedding_model = os.getenv("RAG_EMBEDDING_MODEL", default_embedding_model).strip() or default_embedding_model
        rerank_model = os.getenv("RAG_RERANK_MODEL", default_rerank_model).strip() or default_rerank_model
        api_key = os.getenv("RAG_API_KEY", os.getenv("DASHSCOPE_API_KEY", "")).strip()
        embedding_endpoint = os.getenv("RAG_EMBEDDING_ENDPOINT", "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings").strip()
        if rerank_provider in {"bge", "bge-reranker", "baai"}:
            rerank_endpoint = os.getenv("BGE_RERANK_ENDPOINT", os.getenv("RAG_RERANK_ENDPOINT", "")).strip()
        else:
            rerank_endpoint = os.getenv("RAG_RERANK_ENDPOINT", "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank").strip()
        configured = embedding_provider != "local" or rerank_provider != "local"
        embedding_ready = embedding_provider == "local" or bool(api_key)
        rerank_ready = rerank_provider == "local" or (
            bool(rerank_endpoint) if rerank_provider in {"bge", "bge-reranker", "baai"} else bool(api_key)
        )
        remote_ready = configured and embedding_ready and rerank_ready
        remote_calls_enabled = remote_ready and os.getenv("RAG_ENABLE_REMOTE_CALLS", "").strip().lower() in {"1", "true", "yes", "on"}
        if remote_ready:
            status = (
                f"外部 provider 可调用：Embedding={embedding_provider}/{embedding_model}；Rerank={rerank_provider}/{rerank_model}"
                if remote_calls_enabled
                else f"外部 provider 已具备 key，但 RAG_ENABLE_REMOTE_CALLS 未开启；当前使用本地 fallback"
            )
        elif configured:
            missing = []
            if not embedding_ready:
                missing.append("RAG_API_KEY 或 DASHSCOPE_API_KEY")
            if not rerank_ready and rerank_provider in {"bge", "bge-reranker", "baai"}:
                missing.append("BGE_RERANK_ENDPOINT")
            elif not rerank_ready:
                missing.append("RAG_API_KEY 或 DASHSCOPE_API_KEY")
            status = (
                f"外部 provider 已声明但缺少 {', '.join(missing) or '远程配置'}；"
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


class BGERerankProvider(DashScopeRerankProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        endpoint: str,
        transport: Callable[[str, list[str], str, str, str], dict[str, object]] | None = None,
        stats: RagRuntimeStats | None = None,
    ):
        super().__init__(
            api_key=api_key,
            model=model,
            endpoint=endpoint,
            transport=transport or _open_rerank_transport,
            stats=stats,
        )
        self.provider_name = f"bge:{model}"


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
    elif config.remote_calls_enabled and config.rerank_provider in {"bge", "bge-reranker", "baai"}:
        rerank = BGERerankProvider(
            os.getenv("BGE_RERANK_API_KEY", config.api_key),
            config.rerank_model,
            config.rerank_endpoint,
            stats=stats,
        )
    elif config.rerank_provider == "local" or not config.remote_calls_enabled:
        rerank = LocalRerankProvider()
    else:
        rerank = ConfiguredRerankProvider(config.rerank_provider, config.rerank_model)
    return embedding, rerank


def chunk_document(
    document: RagDocument,
    max_chars: int | None = 220,
    overlap_sentences: int = 1,
    *,
    chunking: RagChunkingConfig | None = None,
) -> tuple[RagChunk, ...]:
    sentences = _sentences(document.text)
    if not sentences:
        return ()
    if chunking is not None:
        return _chunk_document_by_tokens(document, sentences, chunking)
    chunks: list[RagChunk] = []
    current: list[str] = []
    index = 1
    for sentence in sentences:
        candidate = "".join(current + [sentence])
        if current and max_chars is not None and len(candidate) > max_chars:
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
        "只基于引用依据回答，禁止编造未提供的事实；如果资料里没有答案，必须说“不知道/需要人工复核”。\n"
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


def rewrite_rag_query(query: str, *, country: str = "") -> str:
    base = " ".join(part for part in (query.strip(), country.strip()) if part)
    domain_terms = "价值观 审核 风险 文化混淆 版权 IP 文字水印 AI质量 主体清晰 色彩氛围 构图环境"
    return f"{base} {domain_terms}".strip()


def evaluate_retrieval_hit_rate(
    retriever: "HybridRagRetriever",
    cases: tuple[RagRetrievalCase, ...],
    *,
    k: int = 5,
) -> dict[str, object]:
    total = len(cases)
    case_results = []
    hits = 0
    for case in cases:
        result_hits = retriever.search(rewrite_rag_query(case.query, country=case.country), country=case.country, top_k=k)
        retrieved_parent_ids = tuple(hit.chunk.parent_id for hit in result_hits)
        hit = case.expected_parent_id in retrieved_parent_ids
        hits += 1 if hit else 0
        case_results.append(
            {
                "query": case.query,
                "country": case.country,
                "expected_parent_id": case.expected_parent_id,
                "retrieved_parent_ids": retrieved_parent_ids,
                "hit": hit,
            }
        )
    metric_name = f"hit@{k}"
    return {
        metric_name: hits / total if total else 0.0,
        "hits": hits,
        "total": total,
        "cases": tuple(case_results),
    }


def evaluate_retrieval_report(
    retriever: "HybridRagRetriever",
    cases: tuple[RagRetrievalCase, ...],
    *,
    k: int = 5,
    threshold: float = 0.8,
    dataset_name: str = "rag_retrieval_eval",
    knowledge_version: str = "",
) -> dict[str, object]:
    total = len(cases)
    hits = 0
    reciprocal_rank_sum = 0.0
    case_results = []
    for case in cases:
        result_hits = retriever.search(rewrite_rag_query(case.query, country=case.country), country=case.country, top_k=k)
        retrieved_parent_ids = tuple(hit.chunk.parent_id for hit in result_hits)
        rank = 0
        for index, parent_id in enumerate(retrieved_parent_ids, 1):
            if parent_id == case.expected_parent_id:
                rank = index
                break
        if rank:
            hits += 1
            reciprocal_rank_sum += 1 / rank
        case_results.append(
            {
                "query": case.query,
                "country": case.country,
                "expected_parent_id": case.expected_parent_id,
                "retrieved_parent_ids": retrieved_parent_ids,
                "hit": bool(rank),
                "rank": rank,
            }
        )
    hit_rate = hits / total if total else 0.0
    mrr = reciprocal_rank_sum / total if total else 0.0
    return {
        "dataset_name": dataset_name,
        "knowledge_version": knowledge_version,
        f"hit@{k}": hit_rate,
        f"mrr@{k}": mrr,
        "passed_threshold": hit_rate >= threshold,
        "threshold": threshold,
        "hits": hits,
        "total": total,
        "cases": tuple(case_results),
    }


def export_offline_rag_index(
    documents: tuple[RagDocument, ...],
    output_dir: Path | str,
    *,
    country: str,
    chunking: RagChunkingConfig | None = None,
    vector_store: RagVectorStoreConfig | None = None,
) -> RagIndexArtifacts:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    chunking = chunking or RagChunkingConfig()
    vector_store = vector_store or RagVectorStoreConfig()
    chunks = tuple(
        chunk
        for document in documents
        for chunk in chunk_document(document, max_chars=None, chunking=chunking)
    )
    parent_child: dict[str, list[str]] = {}
    for chunk in chunks:
        parent_child.setdefault(chunk.parent_id, []).append(chunk.chunk_id)
    documents_path = output / f"rag_documents_{country}.jsonl"
    chunks_path = output / f"rag_chunks_{country}.jsonl"
    manifest_path = output / f"rag_manifest_{country}.json"
    _write_jsonl(documents_path, (_document_to_dict(document) for document in documents))
    _write_jsonl(chunks_path, (_chunk_to_dict(chunk) for chunk in chunks))
    source_counts: dict[str, int] = {}
    for document in documents:
        source_counts[document.source_type] = source_counts.get(document.source_type, 0) + 1
    manifest: dict[str, object] = {
        "country": country,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "source_counts": source_counts,
        "chunking": {
            "splitter": chunking.splitter,
            "chunk_size_tokens": chunking.chunk_size_tokens,
            "chunk_overlap_tokens": chunking.chunk_overlap_tokens,
        },
        "vector_store": {
            "provider": vector_store.provider,
            "endpoint": vector_store.endpoint,
            "collection": vector_store.collection,
            "ready": vector_store.ready,
            "status_text": vector_store.status_text,
        },
        "parent_child": parent_child,
        "documents_path": str(documents_path),
        "chunks_path": str(chunks_path),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return RagIndexArtifacts(output, manifest_path, documents_path, chunks_path, manifest)


def prepare_qdrant_points(
    chunks: tuple[RagChunk, ...],
    vectors_by_chunk_id: dict[str, tuple[float, ...]],
) -> tuple[QdrantPoint, ...]:
    points: list[QdrantPoint] = []
    for chunk in chunks:
        vector = vectors_by_chunk_id.get(chunk.chunk_id)
        if not vector:
            continue
        points.append(
            QdrantPoint(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id)),
                vector=tuple(float(value) for value in vector),
                payload={
                    "chunk_id": chunk.chunk_id,
                    "parent_id": chunk.parent_id,
                    "country": chunk.country,
                    "source_type": chunk.source_type,
                    "title": chunk.title,
                    "text": chunk.text,
                    "chunk_index": chunk.chunk_index,
                    "metadata": dict(chunk.metadata),
                },
            )
        )
    return tuple(points)


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
        bm25_top_k: int = 30,
        vector_top_k: int = 30,
    ) -> tuple[RagHit, ...]:
        return self.search_with_trace(
            query,
            country=country,
            top_k=top_k,
            source_types=source_types,
            bm25_top_k=bm25_top_k,
            vector_top_k=vector_top_k,
        ).final_hits

    def search_with_trace(
        self,
        query: str,
        *,
        country: str,
        top_k: int = 6,
        source_types: tuple[str, ...] | None = None,
        bm25_top_k: int = 30,
        vector_top_k: int = 30,
    ) -> RagRetrievalTrace:
        query_tokens = _tokens(query)
        if not query_tokens:
            return RagRetrievalTrace(
                query,
                country,
                0,
                bm25_top_k,
                vector_top_k,
                top_k,
                (),
                (),
                (),
                0,
                self.embedding_provider.provider_name,
                self.rerank_provider.provider_name,
                (),
            )
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
        scored: list[tuple[RagChunk, float, float]] = []
        for chunk, vector in zip(eligible_chunks, vector_scores):
            bm25 = self._bm25(query_tokens, chunk)
            scored.append((chunk, bm25, vector))
        candidates, bm25_ids, vector_ids, exact_ids = self._candidate_pool_with_routes(
            scored,
            query,
            bm25_top_k=bm25_top_k,
            vector_top_k=vector_top_k,
        )
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
        return RagRetrievalTrace(
            query=query,
            country=country,
            eligible_chunk_count=len(eligible_chunks),
            bm25_top_k=bm25_top_k,
            vector_top_k=vector_top_k,
            rerank_top_k=top_k,
            bm25_candidates=tuple(bm25_ids),
            vector_candidates=tuple(vector_ids),
            exact_match_candidates=tuple(exact_ids),
            merged_candidate_count=len(candidates),
            embedding_provider=self.embedding_provider.provider_name,
            rerank_provider=self.rerank_provider.provider_name,
            final_hits=tuple(ranked[:top_k]),
        )

    def _candidate_pool(
        self,
        scored: list[tuple[RagChunk, float, float]],
        query: str,
        *,
        bm25_top_k: int,
        vector_top_k: int,
    ) -> list[tuple[RagChunk, float, float]]:
        return self._candidate_pool_with_routes(
            scored,
            query,
            bm25_top_k=bm25_top_k,
            vector_top_k=vector_top_k,
        )[0]

    def _candidate_pool_with_routes(
        self,
        scored: list[tuple[RagChunk, float, float]],
        query: str,
        *,
        bm25_top_k: int,
        vector_top_k: int,
    ) -> tuple[list[tuple[RagChunk, float, float]], list[str], list[str], list[str]]:
        candidates_by_id: dict[str, tuple[RagChunk, float, float]] = {}
        bm25_ranked = sorted((item for item in scored if item[1] > 0), key=lambda item: item[1], reverse=True)
        vector_ranked = sorted((item for item in scored if item[2] > 0), key=lambda item: item[2], reverse=True)
        exact_matches = [item for item in scored if _has_exact_phrase(query, item[0].text)]
        bm25_selected = bm25_ranked[: max(bm25_top_k, 0)]
        vector_selected = vector_ranked[: max(vector_top_k, 0)]
        for chunk, bm25, vector in (
            bm25_selected + vector_selected + exact_matches
        ):
            candidates_by_id.setdefault(chunk.chunk_id, (chunk, bm25, vector))
        return (
            list(candidates_by_id.values()),
            [chunk.chunk_id for chunk, _, _ in bm25_selected],
            [chunk.chunk_id for chunk, _, _ in vector_selected],
            [chunk.chunk_id for chunk, _, _ in exact_matches],
        )

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
        metadata=dict(document.metadata),
    )


def _document_to_dict(document: RagDocument) -> dict[str, object]:
    return {
        "document_id": document.document_id,
        "country": document.country,
        "source_type": document.source_type,
        "title": document.title,
        "text": document.text,
        "metadata": dict(document.metadata),
    }


def _chunk_to_dict(chunk: RagChunk) -> dict[str, object]:
    return {
        "chunk_id": chunk.chunk_id,
        "parent_id": chunk.parent_id,
        "country": chunk.country,
        "source_type": chunk.source_type,
        "title": chunk.title,
        "text": chunk.text,
        "chunk_index": chunk.chunk_index,
        "metadata": dict(chunk.metadata),
    }


def _write_jsonl(path: Path, rows) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _chunk_document_by_tokens(
    document: RagDocument,
    sentences: tuple[str, ...],
    chunking: RagChunkingConfig,
) -> tuple[RagChunk, ...]:
    chunks: list[RagChunk] = []
    current: list[str] = []
    index = 1
    max_tokens = max(chunking.chunk_size_tokens, 1)
    overlap_tokens = max(chunking.chunk_overlap_tokens, 0)
    for sentence in sentences:
        candidate = current + [sentence]
        if current and _estimated_chunk_tokens(candidate) > max_tokens:
            chunks.append(_make_token_chunk(document, current, index, chunking))
            index += 1
            current = _overlap_sentences(current, overlap_tokens)
        current.append(sentence)
    if current:
        chunks.append(_make_token_chunk(document, current, index, chunking))
    return tuple(chunks)


def _make_token_chunk(document: RagDocument, sentences: list[str], index: int, chunking: RagChunkingConfig) -> RagChunk:
    metadata = dict(document.metadata)
    metadata.update(
        {
            "splitter": chunking.splitter,
            "chunk_size_tokens": chunking.chunk_size_tokens,
            "chunk_overlap_tokens": chunking.chunk_overlap_tokens,
        }
    )
    return RagChunk(
        chunk_id=f"{document.document_id}#chunk-{index}",
        parent_id=document.document_id,
        country=document.country,
        source_type=document.source_type,
        title=document.title,
        text="".join(sentences).strip(),
        chunk_index=index,
        metadata=metadata,
    )


def _overlap_sentences(sentences: list[str], overlap_tokens: int) -> list[str]:
    if overlap_tokens <= 0:
        return []
    selected: list[str] = []
    total = 0
    for sentence in reversed(sentences):
        selected.insert(0, sentence)
        total += _estimated_sentence_tokens(sentence)
        if total >= overlap_tokens:
            break
    return selected


def _estimated_chunk_tokens(sentences: list[str]) -> int:
    return sum(_estimated_sentence_tokens(sentence) for sentence in sentences)


def _estimated_sentence_tokens(sentence: str) -> int:
    latin = re.findall(r"[a-z0-9]+", sentence.lower())
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", sentence)
    return len(latin) + math.ceil(len(cjk_chars) / 2)


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


def _open_rerank_transport(query: str, documents: list[str], api_key: str, endpoint: str, model: str) -> dict[str, object]:
    payload = {"model": model, "query": query, "documents": documents}
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
