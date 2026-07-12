from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
import re
import ssl
from pathlib import Path
from typing import Callable
from urllib import request
import uuid
from zipfile import ZipFile
from xml.etree import ElementTree as ET


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
class RagGeneratedAnswer:
    answer: str
    status: str
    provider: str
    model: str
    citations: tuple[str, ...]
    prompt: str
    raw_text: str = ""
    error: str = ""


@dataclass(frozen=True)
class RagRetrievalCase:
    query: str
    country: str
    expected_parent_id: str
    relevant_parent_ids: tuple[str, ...] = ()


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
    vector_store_provider: str
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
            "vector_store_provider": self.vector_store_provider,
            "rerank_provider": self.rerank_provider,
            "hybrid_mode": "bm25+dense+rerank",
            "retrieval_routes": {
                "bm25": True,
                "dense_vector": True,
                "exact_match": True,
                "rerank": True,
                "remote_vector_store": self.vector_store_provider != "local",
            },
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


def build_processed_documents_from_raw(raw_dir: Path | str, output_path: Path | str) -> tuple[RagDocument, ...]:
    raw_root = Path(raw_dir)
    documents: list[RagDocument] = []
    if raw_root.exists():
        paths = tuple(child for child in raw_root.rglob("*") if child.is_file() and child.suffix.lower() in _RAW_EXTENSIONS)
        for path in sorted(paths, key=_raw_file_sort_key):
            documents.extend(_raw_text_file_to_documents(path))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output, (_document_to_dict(document) for document in documents))
    return tuple(documents)


def _raw_file_sort_key(path: Path) -> tuple[int, str]:
    text = _raw_file_text(path)
    metadata, _ = _parse_front_matter(text)
    source_type = str(metadata.get("source_type", ""))
    country = str(metadata.get("country", ""))
    priority = 1 if source_type == "audit_policy" or country == "GLOBAL" else 0
    return (priority, path.name)


def _raw_text_file_to_documents(path: Path) -> tuple[RagDocument, ...]:
    raw_text = _raw_file_text(path)
    metadata, body = _parse_front_matter(raw_text)
    country = str(metadata.get("country", "GLOBAL")).strip() or "GLOBAL"
    source_type = str(metadata.get("source_type", "value_rule")).strip() or "value_rule"
    knowledge_version = str(metadata.get("knowledge_version", "")).strip()
    sections = _markdown_sections(body)
    documents: list[RagDocument] = []
    stem = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", path.stem).strip("_").upper() or "RAW"
    for index, (raw_title, text) in enumerate(sections, 1):
        title, explicit_id = _section_title_and_explicit_id(raw_title)
        safe_title = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", title).strip("_") or f"SECTION_{index}"
        doc_metadata: dict[str, object] = {"source_file": str(path), "raw_section_index": index}
        if knowledge_version:
            doc_metadata["knowledge_version"] = knowledge_version
        for key, value in metadata.items():
            if key not in {"country", "source_type", "knowledge_version"}:
                doc_metadata[key] = value
        documents.append(
            RagDocument(
                document_id=explicit_id or f"RAW_{stem}_{safe_title}",
                country=country,
                source_type=source_type,
                title=title,
                text=text,
                metadata=doc_metadata,
            )
        )
    return tuple(documents)


_RAW_EXTENSIONS = {".md", ".markdown", ".txt", ".docx"}


def _raw_file_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return "\n".join(_docx_paragraphs(path))
    return path.read_text(encoding="utf-8")


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


def _section_title_and_explicit_id(title: str) -> tuple[str, str]:
    match = re.search(r"\s*\{#([A-Za-z0-9_\-]+)\}\s*$", title)
    if not match:
        return title.strip(), ""
    cleaned = title[: match.start()].strip()
    return cleaned, match.group(1)


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return _parse_loose_metadata_header(text)
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, flags=re.DOTALL)
    if not match:
        return {}, text
    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata, match.group(2)


def _parse_loose_metadata_header(text: str) -> tuple[dict[str, str], str]:
    metadata: dict[str, str] = {}
    body_start = 0
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if ":" not in stripped or stripped.startswith("#"):
            body_start = index
            break
        key, value = stripped.split(":", 1)
        normalized_key = key.strip()
        if normalized_key not in {"country", "source_type", "knowledge_version"}:
            body_start = index
            break
        metadata[normalized_key] = value.strip().strip('"').strip("'")
    else:
        body_start = len(lines)
    if not metadata:
        return {}, text
    return metadata, "\n".join(lines[body_start:])


def _markdown_sections(text: str) -> tuple[tuple[str, str], ...]:
    current_title = ""
    current_lines: list[str] = []
    sections: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_title and "".join(current_lines).strip():
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = stripped.lstrip("#").strip()
            current_lines = []
        elif stripped.startswith("# ") and not current_title:
            current_title = stripped.lstrip("#").strip()
        else:
            current_lines.append(line)
    if current_title and "\n".join(current_lines).strip():
        sections.append((current_title, "\n".join(current_lines).strip()))
    if sections:
        return tuple(sections)
    cleaned = text.strip()
    return (("Raw Document", cleaned),) if cleaned else ()


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
        if provider == "milvus":
            milvus_endpoint = os.getenv("MILVUS_URI", os.getenv("RAG_MILVUS_URI", "")).strip().rstrip("/")
            milvus_collection = os.getenv("MILVUS_COLLECTION", os.getenv("RAG_MILVUS_COLLECTION", "puzzle_ops_rag")).strip() or "puzzle_ops_rag"
            milvus_token = os.getenv("MILVUS_TOKEN", os.getenv("RAG_MILVUS_TOKEN", "")).strip()
            ready = bool(milvus_endpoint and milvus_collection)
            status = (
                f"Milvus ready：{milvus_endpoint} / {milvus_collection}"
                if ready
                else "Milvus 已声明但缺少 MILVUS_URI 或 MILVUS_COLLECTION"
            )
            return cls(provider, milvus_endpoint, milvus_collection, milvus_token, True, ready, status)
        return cls()


class QdrantVectorStore:
    def __init__(
        self,
        config: RagVectorStoreConfig,
        transport: Callable[[str, dict[str, object], str], dict[str, object]] | None = None,
        management_transport: Callable[[str, str, dict[str, object] | None, str], dict[str, object]] | None = None,
    ):
        self.config = config
        self.transport = transport or _post_json
        self.management_transport = management_transport or _qdrant_json_request

    def healthcheck(self) -> dict[str, object]:
        if self.config.provider != "qdrant" or not self.config.ready:
            return {"provider": self.config.provider, "configured": self.config.configured, "ready": False, "exists": False}
        endpoint = f"{self.config.endpoint}/collections/{self.config.collection}"
        try:
            response = self.management_transport("GET", endpoint, None, self.config.api_key)
        except Exception as exc:
            return {"provider": "qdrant", "configured": True, "ready": False, "exists": False, "error": str(exc)}
        vector_size = _qdrant_vector_size(response)
        return {
            "provider": "qdrant",
            "configured": True,
            "ready": True,
            "exists": bool(response.get("result")),
            "collection": self.config.collection,
            "vector_size": vector_size,
        }

    def ensure_collection(self, vector_size: int, *, distance: str = "Cosine") -> dict[str, object]:
        if self.config.provider != "qdrant" or not self.config.ready:
            raise RuntimeError("Qdrant vector store 未就绪")
        if vector_size <= 0:
            raise ValueError("Qdrant collection 向量维度必须大于 0")
        endpoint = f"{self.config.endpoint}/collections/{self.config.collection}"
        response = self.management_transport("GET", endpoint, None, self.config.api_key)
        existing_size = _qdrant_vector_size(response)
        if existing_size is not None:
            if existing_size != vector_size:
                raise ValueError(f"Qdrant collection 向量维度不匹配：existing={existing_size}，new={vector_size}")
            return {"status": "exists", "collection": self.config.collection, "vector_size": existing_size}
        payload = {"vectors": {"size": int(vector_size), "distance": distance}}
        self.management_transport("PUT", endpoint, payload, self.config.api_key)
        return {"status": "created", "collection": self.config.collection, "vector_size": vector_size}

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

    def search(self, query_vector: tuple[float, ...], *, country: str, top_k: int) -> dict[str, float]:
        if self.config.provider != "qdrant" or not self.config.ready:
            raise RuntimeError("Qdrant vector store 未就绪")
        if not query_vector or top_k <= 0:
            return {}
        endpoint = f"{self.config.endpoint}/collections/{self.config.collection}/points/search"
        payload = {
            "vector": [float(value) for value in query_vector],
            "limit": int(top_k),
            "with_payload": True,
            "filter": {
                "should": [
                    {"key": "country", "match": {"value": country}},
                    {"key": "country", "match": {"value": "GLOBAL"}},
                ]
            },
        }
        response = self.transport(endpoint, payload, self.config.api_key)
        results = response.get("result", ())
        if not isinstance(results, list):
            return {}
        scores: dict[str, float] = {}
        for item in results:
            if not isinstance(item, dict):
                continue
            payload_obj = item.get("payload", {})
            if not isinstance(payload_obj, dict):
                continue
            chunk_id = str(payload_obj.get("chunk_id", "")).strip()
            if not chunk_id:
                continue
            raw_score = item.get("score", item.get("relevance_score", 0.0))
            try:
                scores[chunk_id] = float(raw_score)
            except (TypeError, ValueError):
                continue
        return scores

    def delete_points(self, point_ids: tuple[str, ...]) -> dict[str, object]:
        if self.config.provider != "qdrant" or not self.config.ready:
            raise RuntimeError("Qdrant vector store 未就绪")
        if not point_ids:
            return {"status": "skipped_empty"}
        endpoint = f"{self.config.endpoint}/collections/{self.config.collection}/points/delete?wait=true"
        return self.transport(endpoint, {"points": list(point_ids)}, self.config.api_key)

    def restore_points(
        self,
        point_ids: tuple[str, ...],
        point_records: tuple[dict[str, object], ...] = (),
    ) -> dict[str, object]:
        points = tuple(_qdrant_point_from_record(record) for record in point_records)
        if not points:
            return {
                "status": "manifest_pointer_only",
                "restored_points": 0,
                "point_ids": point_ids,
                "note": "Qdrant point-level restore requires stored vectors or a Qdrant snapshot.",
            }
        self.upsert(points)
        return {"status": "restored", "restored_points": len(points), "point_ids": tuple(point.id for point in points)}

    def smoke_diagnostic(self, *, vector_size: int, country: str = "GLOBAL") -> dict[str, object]:
        if vector_size <= 0:
            raise ValueError("Qdrant smoke diagnostic 需要有效向量维度")
        point_id = f"puzzleops-rag-smoke-{uuid.uuid4()}"
        chunk_id = "SMOKE#chunk-1"
        vector = tuple(1.0 if index == 0 else 0.0 for index in range(vector_size))
        point = QdrantPoint(
            id=point_id,
            vector=vector,
            payload={
                "chunk_id": chunk_id,
                "parent_id": "SMOKE",
                "country": country,
                "source_type": "qdrant_smoke",
                "title": "Qdrant smoke diagnostic",
                "text": "temporary diagnostic point",
                "chunk_index": 1,
                "metadata": {"temporary": True},
            },
        )
        self.upsert((point,))
        search_scores: dict[str, float] = {}
        cleanup_status = "not_started"
        try:
            search_scores = self.search(vector, country=country, top_k=1)
            search_hit = chunk_id in search_scores
            status = "passed" if search_hit else "failed_no_hit"
        finally:
            self.delete_points((point_id,))
            cleanup_status = "deleted"
        return {
            "status": status,
            "point_id": point_id,
            "chunk_id": chunk_id,
            "country": country,
            "vector_size": vector_size,
            "search_hit": search_hit,
            "search_score": search_scores.get(chunk_id, 0.0),
            "cleanup_status": cleanup_status,
        }


class QdrantVectorStoreRetriever:
    provider_name = "qdrant"

    def __init__(self, store: QdrantVectorStore):
        self.store = store

    def search(self, query_vector: tuple[float, ...], *, country: str, top_k: int) -> dict[str, float]:
        return self.store.search(query_vector, country=country, top_k=top_k)


class MilvusVectorStore:
    provider_name = "milvus"

    def __init__(
        self,
        config: RagVectorStoreConfig,
        transport: Callable[[str, str, dict[str, object] | None, str], dict[str, object]] | None = None,
    ):
        self.config = config
        self.transport = transport or _milvus_json_request

    def healthcheck(self) -> dict[str, object]:
        if self.config.provider != "milvus" or not self.config.ready:
            return {"provider": self.config.provider, "configured": self.config.configured, "ready": False, "exists": False}
        endpoint = f"{self.config.endpoint}/v2/vectordb/collections/describe"
        payload = {"collectionName": self.config.collection}
        try:
            response = self.transport("POST", endpoint, payload, self.config.api_key)
        except Exception as exc:
            return {"provider": "milvus", "configured": True, "ready": False, "exists": False, "error": str(exc)}
        vector_size = _milvus_vector_size(response)
        return {
            "provider": "milvus",
            "configured": True,
            "ready": bool(_milvus_success(response)),
            "exists": bool(response.get("data")),
            "collection": self.config.collection,
            "vector_size": vector_size,
        }

    def ensure_collection(self, vector_size: int) -> dict[str, object]:
        if self.config.provider != "milvus" or not self.config.ready:
            raise RuntimeError("Milvus vector store 未就绪")
        if vector_size <= 0:
            raise ValueError("Milvus collection 向量维度必须大于 0")
        describe_endpoint = f"{self.config.endpoint}/v2/vectordb/collections/describe"
        describe_payload = {"collectionName": self.config.collection}
        response = self.transport("POST", describe_endpoint, describe_payload, self.config.api_key)
        existing_size = _milvus_vector_size(response) if _milvus_success(response) else None
        if existing_size is not None:
            if existing_size != vector_size:
                raise ValueError(f"Milvus collection 向量维度不匹配：existing={existing_size}，new={vector_size}")
            return {"status": "exists", "collection": self.config.collection, "vector_size": existing_size}
        create_endpoint = f"{self.config.endpoint}/v2/vectordb/collections/create"
        self.transport("POST", create_endpoint, _milvus_create_collection_payload(self.config.collection, vector_size), self.config.api_key)
        load_endpoint = f"{self.config.endpoint}/v2/vectordb/collections/load"
        self.transport("POST", load_endpoint, {"collectionName": self.config.collection}, self.config.api_key)
        return {"status": "created", "collection": self.config.collection, "vector_size": vector_size, "index": "vector_index"}

    def upsert(self, points: tuple[QdrantPoint, ...]) -> dict[str, object]:
        if self.config.provider != "milvus" or not self.config.ready:
            raise RuntimeError("Milvus vector store 未就绪")
        endpoint = f"{self.config.endpoint}/v2/vectordb/entities/insert"
        payload = {
            "collectionName": self.config.collection,
            "data": [_milvus_entity_from_point(point) for point in points],
        }
        response = self.transport("POST", endpoint, payload, self.config.api_key)
        return {
            "status": "ok" if _milvus_success(response) else "failed",
            "insert_count": _milvus_insert_count(response, len(points)),
            "response": response,
        }

    def search(self, query_vector: tuple[float, ...], *, country: str, top_k: int) -> dict[str, float]:
        if self.config.provider != "milvus" or not self.config.ready:
            raise RuntimeError("Milvus vector store 未就绪")
        if not query_vector or top_k <= 0:
            return {}
        endpoint = f"{self.config.endpoint}/v2/vectordb/entities/search"
        payload = {
            "collectionName": self.config.collection,
            "data": [[float(value) for value in query_vector]],
            "limit": int(top_k),
            "filter": f'country in ["{_milvus_filter_value(country)}", "GLOBAL"]',
            "outputFields": ["chunk_id", "parent_id", "country", "source_type", "title", "text", "chunk_index", "metadata"],
        }
        response = self.transport("POST", endpoint, payload, self.config.api_key)
        return _milvus_search_scores(response)

    def delete_entities(self, point_ids: tuple[str, ...]) -> dict[str, object]:
        if self.config.provider != "milvus" or not self.config.ready:
            raise RuntimeError("Milvus vector store 未就绪")
        if not point_ids:
            return {"status": "skipped_empty", "deleted_count": 0}
        endpoint = f"{self.config.endpoint}/v2/vectordb/entities/delete"
        escaped = ", ".join(f'"{_milvus_filter_value(point_id)}"' for point_id in point_ids)
        response = self.transport(
            "POST",
            endpoint,
            {"collectionName": self.config.collection, "filter": f"id in [{escaped}]"},
            self.config.api_key,
        )
        return {
            "status": "deleted" if _milvus_success(response) else "failed",
            "deleted_count": _milvus_delete_count(response, len(point_ids)),
            "response": response,
        }

    def smoke_diagnostic(self, *, vector_size: int, country: str = "GLOBAL") -> dict[str, object]:
        if vector_size <= 0:
            raise ValueError("Milvus smoke diagnostic 需要有效向量维度")
        point_id = f"puzzleops-rag-smoke-{uuid.uuid4()}"
        chunk_id = f"{point_id}#chunk-1"
        vector = tuple(1.0 if index == 0 else 0.0 for index in range(vector_size))
        point = QdrantPoint(
            id=point_id,
            vector=vector,
            payload={
                "chunk_id": chunk_id,
                "parent_id": "SMOKE",
                "country": country,
                "source_type": "milvus_smoke",
                "title": "Milvus smoke diagnostic",
                "text": "temporary diagnostic entity",
                "chunk_index": 1,
                "metadata": {"temporary": True},
            },
        )
        self.upsert((point,))
        search_scores: dict[str, float] = {}
        search_hit = False
        cleanup_status = "not_started"
        try:
            search_scores = self.search(vector, country=country, top_k=1)
            search_hit = chunk_id in search_scores
            status = "passed" if search_hit else "failed_no_hit"
        finally:
            cleanup = self.delete_entities((point_id,))
            cleanup_status = str(cleanup.get("status", "unknown"))
        return {
            "status": status,
            "point_id": point_id,
            "chunk_id": chunk_id,
            "country": country,
            "vector_size": vector_size,
            "search_hit": search_hit,
            "search_score": search_scores.get(chunk_id, 0.0),
            "cleanup_status": cleanup_status,
        }


class MilvusVectorStoreRetriever:
    provider_name = "milvus"

    def __init__(self, store: MilvusVectorStore):
        self.store = store

    def search(self, query_vector: tuple[float, ...], *, country: str, top_k: int) -> dict[str, float]:
        return self.store.search(query_vector, country=country, top_k=top_k)


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
        api_key = _first_nonempty_env("RAG_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY")
        embedding_endpoint = os.getenv("RAG_EMBEDDING_ENDPOINT", "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings").strip()
        if rerank_provider in {"bge", "bge-reranker", "baai"}:
            rerank_endpoint = os.getenv("BGE_RERANK_ENDPOINT", os.getenv("RAG_RERANK_ENDPOINT", "")).strip()
        else:
            rerank_endpoint = os.getenv("RAG_RERANK_ENDPOINT", "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank").strip()
        configured = embedding_provider != "local" or rerank_provider != "local"
        model_errors = _rag_model_config_errors(embedding_model, rerank_model)
        embedding_ready = embedding_provider == "local" or (bool(api_key) and not model_errors)
        rerank_ready = rerank_provider == "local" or (
            bool(rerank_endpoint) if rerank_provider in {"bge", "bge-reranker", "baai"} else bool(api_key)
        )
        if model_errors:
            rerank_ready = False
        remote_ready = configured and embedding_ready and rerank_ready
        remote_calls_enabled = remote_ready and os.getenv("RAG_ENABLE_REMOTE_CALLS", "").strip().lower() in {"1", "true", "yes", "on"}
        if model_errors:
            status = (
                "外部 provider 已声明但模型用途不匹配："
                + "；".join(model_errors)
                + "；qwen3-vl 是视觉理解模型，不能作为 embedding/reranker。"
            )
        elif remote_ready:
            embedding_family = _embedding_model_family(embedding_model)
            status = (
                f"外部 provider 可调用：Embedding={embedding_provider}/{embedding_model}（{embedding_family}）；Rerank={rerank_provider}/{rerank_model}"
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

    def query_vector(self, query: str) -> tuple[float, ...]:
        return ()


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

    def query_vector(self, query: str) -> tuple[float, ...]:
        return self._embedding(query)

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

    def healthcheck(self) -> dict[str, object]:
        configured = bool(self.endpoint and self.model)
        status: dict[str, object] = {
            "provider": "dashscope",
            "configured": configured,
            "ready": False,
            "model": self.model,
            "endpoint": self.endpoint,
        }
        if not configured:
            status["error"] = "missing endpoint or model"
            return status
        try:
            self.stats.rerank_remote_calls += 1
            response = self.transport(
                "寿司是否符合日本价值观",
                ["日本饮食：寿司属于日本本土饮食文化。"],
                self.api_key,
                self.endpoint,
                self.model,
            )
            score = _extract_rerank_score(response)
        except Exception as exc:
            status["error"] = str(exc)
            return status
        status["ready"] = score is not None
        status["probe_score"] = score if score is not None else 0.0
        return status

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

    def healthcheck(self) -> dict[str, object]:
        status = super().healthcheck()
        status["provider"] = "bge"
        return status


class MissingRagAnswerGenerator:
    provider_name = "missing"
    model = ""

    def __init__(self, reason: str = "RAG 生成模型未配置"):
        self.reason = reason

    def generate(self, prompt: RagPrompt) -> RagGeneratedAnswer:
        return RagGeneratedAnswer(
            answer="",
            status="skipped",
            provider=self.provider_name,
            model=self.model,
            citations=prompt.citations,
            prompt=prompt.prompt,
            error=self.reason,
        )


class QwenRagAnswerGenerator:
    provider_name = "qwen"

    def __init__(
        self,
        api_key: str,
        model: str = "qwen3.7-plus",
        endpoint: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        transport: Callable[[dict[str, object], str, str], dict[str, object]] | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        self.transport = transport or _qwen_chat_transport

    def generate(self, prompt: RagPrompt) -> RagGeneratedAnswer:
        if not self.api_key:
            return RagGeneratedAnswer(
                answer="",
                status="skipped",
                provider=self.provider_name,
                model=self.model,
                citations=prompt.citations,
                prompt=prompt.prompt,
                error="缺少 RAG 生成模型 API Key",
            )
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是 PuzzleOps 拼图内容运营 RAG 助手，回答必须可溯源、简洁、面向运营决策。",
                },
                {
                    "role": "user",
                    "content": _rag_generation_user_prompt(prompt),
                },
            ],
            "temperature": 0.2,
        }
        try:
            response = self.transport(payload, self.api_key, self.endpoint)
            answer = _extract_chat_completion_text(response).strip()
        except Exception as exc:
            return RagGeneratedAnswer(
                answer="",
                status="failed",
                provider=self.provider_name,
                model=self.model,
                citations=prompt.citations,
                prompt=_rag_generation_user_prompt(prompt),
                error=str(exc),
            )
        return RagGeneratedAnswer(
            answer=answer,
            status="generated" if answer else "failed",
            provider=self.provider_name,
            model=self.model,
            citations=prompt.citations,
            prompt=_rag_generation_user_prompt(prompt),
            raw_text=answer,
            error="" if answer else "模型未返回文本",
        )


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
    precision_sum = 0.0
    recall_sum = 0.0
    ndcg_sum = 0.0
    case_results = []
    for case in cases:
        rewritten_query = rewrite_rag_query(case.query, country=case.country)
        trace = retriever.search_with_trace(rewritten_query, country=case.country, top_k=k)
        result_hits = trace.final_hits
        retrieved_parent_ids = tuple(hit.chunk.parent_id for hit in result_hits)
        relevant_parent_ids = _relevant_parent_ids(case)
        rank = 0
        for index, parent_id in enumerate(retrieved_parent_ids, 1):
            if parent_id in relevant_parent_ids:
                rank = index
                break
        relevant_hit_count = len({parent_id for parent_id in retrieved_parent_ids[:k] if parent_id in relevant_parent_ids})
        precision = relevant_hit_count / k if k else 0.0
        recall = relevant_hit_count / len(relevant_parent_ids) if relevant_parent_ids else 0.0
        ndcg = _ndcg_at_k(retrieved_parent_ids, relevant_parent_ids, k)
        precision_sum += precision
        recall_sum += recall
        ndcg_sum += ndcg
        if rank:
            hits += 1
            reciprocal_rank_sum += 1 / rank
        diagnosis = _diagnose_retrieval_case(case, trace, rank, k)
        case_results.append(
            {
                "query": case.query,
                "country": case.country,
                "expected_parent_id": case.expected_parent_id,
                "relevant_parent_ids": relevant_parent_ids,
                "retrieved_parent_ids": retrieved_parent_ids,
                "hit": bool(rank),
                "rank": rank,
                "relevant_hit_count": relevant_hit_count,
                f"precision@{k}": precision,
                f"recall@{k}": recall,
                f"ndcg@{k}": ndcg,
                "diagnosis": diagnosis["diagnosis"],
                "suggested_action": diagnosis["suggested_action"],
                "failure_reason": diagnosis["failure_reason"],
                "route_evidence": diagnosis["route_evidence"],
            }
        )
    hit_rate = hits / total if total else 0.0
    mrr = reciprocal_rank_sum / total if total else 0.0
    precision_at_k = precision_sum / total if total else 0.0
    recall_at_k = recall_sum / total if total else 0.0
    ndcg_at_k = ndcg_sum / total if total else 0.0
    return {
        "dataset_name": dataset_name,
        "knowledge_version": knowledge_version,
        f"hit@{k}": hit_rate,
        f"mrr@{k}": mrr,
        f"precision@{k}": precision_at_k,
        f"recall@{k}": recall_at_k,
        f"ndcg@{k}": ndcg_at_k,
        "passed_threshold": hit_rate >= threshold,
        "threshold": threshold,
        "hits": hits,
        "total": total,
        "cases": tuple(case_results),
    }


def _diagnose_retrieval_case(case: RagRetrievalCase, trace: RagRetrievalTrace, rank: int, k: int) -> dict[str, object]:
    expected = case.expected_parent_id
    bm25_parent_ids = _parent_ids_from_chunk_ids(trace.bm25_candidates)
    vector_parent_ids = _parent_ids_from_chunk_ids(trace.vector_candidates)
    exact_parent_ids = _parent_ids_from_chunk_ids(trace.exact_match_candidates)
    final_parent_ids = tuple(hit.chunk.parent_id for hit in trace.final_hits)
    route_evidence = {
        "bm25_has_expected": expected in bm25_parent_ids,
        "vector_has_expected": expected in vector_parent_ids,
        "exact_has_expected": expected in exact_parent_ids,
        "final_has_expected": expected in final_parent_ids,
        "merged_candidate_count": trace.merged_candidate_count,
        "eligible_chunk_count": trace.eligible_chunk_count,
        "bm25_candidate_count": len(trace.bm25_candidates),
        "vector_candidate_count": len(trace.vector_candidates),
        "final_parent_ids": final_parent_ids,
    }
    if rank:
        return {
            "diagnosis": "passed",
            "suggested_action": "",
            "failure_reason": "",
            "route_evidence": route_evidence,
        }
    if trace.eligible_chunk_count == 0:
        diagnosis = "country_knowledge_missing"
        action = "补充该国家的价值观/审核知识文档，并重新构建 RAG 索引。"
    elif expected not in bm25_parent_ids and expected not in vector_parent_ids and expected not in exact_parent_ids:
        diagnosis = "knowledge_missing_or_query_mismatch"
        action = "检查 expected parent 是否已入库；若未入库则补 human_gold 知识，若已入库则补同义词或调整 query rewrite。"
    elif expected not in bm25_parent_ids and expected in vector_parent_ids:
        diagnosis = "bm25_recall_missing"
        action = "补充关键词、别名和运营 tag 词表，提高 BM25 对真实业务说法的召回。"
    elif expected in bm25_parent_ids and expected not in vector_parent_ids:
        diagnosis = "vector_recall_missing"
        action = "检查 embedding 模型、向量入库和 chunk 文本语义，必要时重建 Qdrant 索引。"
    elif expected in bm25_parent_ids or expected in vector_parent_ids or expected in exact_parent_ids:
        diagnosis = "rerank_filtered_expected"
        action = "expected parent 已进入候选池但未进 top-k，优先检查 rerank 模型、top-k 和 hard negative。"
    else:
        diagnosis = "candidate_recall_missing"
        action = "扩大 BM25/vector top-k，并检查 chunk 切割是否把语义拆散。"
    return {
        "diagnosis": diagnosis,
        "suggested_action": action,
        "failure_reason": f"expected parent 未进入 top{k}：{expected}",
        "route_evidence": route_evidence,
    }


def _relevant_parent_ids(case: RagRetrievalCase) -> tuple[str, ...]:
    seen: set[str] = set()
    result = []
    for value in (*case.relevant_parent_ids, case.expected_parent_id):
        parent_id = str(value).strip()
        if parent_id and parent_id not in seen:
            result.append(parent_id)
            seen.add(parent_id)
    return tuple(result)


def _ndcg_at_k(retrieved_parent_ids: tuple[str, ...], relevant_parent_ids: tuple[str, ...], k: int) -> float:
    if k <= 0 or not relevant_parent_ids:
        return 0.0
    relevant = set(relevant_parent_ids)
    dcg = 0.0
    for rank, parent_id in enumerate(retrieved_parent_ids[:k], 1):
        if parent_id in relevant:
            dcg += 1.0 / math.log2(rank + 1)
    ideal_hits = min(len(relevant), k)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / ideal_dcg if ideal_dcg else 0.0


def _parent_ids_from_chunk_ids(chunk_ids: tuple[str, ...]) -> tuple[str, ...]:
    parent_ids: list[str] = []
    for chunk_id in chunk_ids:
        parent = str(chunk_id).split("#", 1)[0]
        if parent not in parent_ids:
            parent_ids.append(parent)
    return tuple(parent_ids)


def evaluate_rag_quality_report(
    *,
    answer: str,
    reference_answer: str = "",
    support_documents: tuple[str, ...] = (),
    required_facts: tuple[str, ...] = (),
    latency_ms: tuple[float, ...] = (),
    satisfaction_scores: tuple[int, ...] = (),
    total_queries: int = 0,
    total_seconds: float = 0.0,
    corpus_document_count: int = 0,
) -> dict[str, object]:
    answer_tokens = _tokens(answer)
    reference_tokens = _tokens(reference_answer)
    support_text = "\n".join(support_documents)
    support_tokens = _tokens(support_text)
    answer_accuracy = {
        "bleu1": _token_precision(answer_tokens, reference_tokens),
        "rouge_l": _rouge_l(answer_tokens, reference_tokens),
        "method": "token_overlap_bleu1_and_rouge_l",
    }
    trustworthiness = {
        "support_overlap": _token_recall(answer_tokens, support_tokens),
        "document_coverage": _fact_coverage(required_facts, support_text),
        "required_fact_count": len(required_facts),
        "covered_fact_count": sum(1 for fact in required_facts if fact and fact in support_text),
    }
    latency = {
        "average_ms": round(sum(latency_ms) / len(latency_ms), 4) if latency_ms else 0.0,
        "p95_ms": _percentile(latency_ms, 0.95),
        "p99_ms": _percentile(latency_ms, 0.99),
        "sample_count": len(latency_ms),
    }
    scalability = {
        "qps": round(total_queries / total_seconds, 4) if total_seconds > 0 else 0.0,
        "total_queries": total_queries,
        "total_seconds": total_seconds,
        "corpus_document_count": corpus_document_count,
    }
    user_experience = {
        "average_satisfaction": round(sum(satisfaction_scores) / len(satisfaction_scores), 4) if satisfaction_scores else 0.0,
        "satisfaction_rate": sum(1 for score in satisfaction_scores if score >= 4) / len(satisfaction_scores) if satisfaction_scores else 0.0,
        "readability_score": _readability_score(answer),
    }
    return {
        "answer_accuracy": answer_accuracy,
        "trustworthiness": trustworthiness,
        "latency": latency,
        "scalability": scalability,
        "user_experience": user_experience,
    }


def _token_precision(candidate: tuple[str, ...], reference: tuple[str, ...]) -> float:
    if not candidate or not reference:
        return 0.0
    candidate_counts = Counter(candidate)
    reference_counts = Counter(reference)
    overlap = sum(min(candidate_counts[token], reference_counts[token]) for token in candidate_counts)
    return overlap / len(candidate)


def _token_recall(candidate: tuple[str, ...], reference: tuple[str, ...]) -> float:
    if not candidate or not reference:
        return 0.0
    candidate_set = set(candidate)
    reference_set = set(reference)
    return len(candidate_set & reference_set) / len(candidate_set)


def _rouge_l(candidate: tuple[str, ...], reference: tuple[str, ...]) -> float:
    if not candidate or not reference:
        return 0.0
    previous = [0] * (len(reference) + 1)
    for token in candidate:
        current = [0]
        for index, ref_token in enumerate(reference, 1):
            if token == ref_token:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[-1]))
        previous = current
    return previous[-1] / len(reference)


def _fact_coverage(required_facts: tuple[str, ...], support_text: str) -> float:
    facts = tuple(fact for fact in required_facts if fact)
    if not facts:
        return 0.0
    return sum(1 for fact in facts if fact in support_text) / len(facts)


def _percentile(values: tuple[float, ...], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _readability_score(answer: str) -> float:
    sentences = _sentences(answer)
    if not sentences:
        return 0.0
    avg_tokens = sum(_estimated_sentence_tokens(sentence) for sentence in sentences) / len(sentences)
    return max(0.0, min(1.0, 1.0 - max(avg_tokens - 18.0, 0.0) / 40.0))


def export_rag_acceptance_report(
    retriever: "HybridRagRetriever",
    cases: tuple[RagRetrievalCase, ...],
    output_path: Path | str,
    *,
    k: int = 5,
    threshold: float = 0.8,
    dataset_name: str = "rag_retrieval_eval",
    knowledge_version: str = "",
    provider_config: RagProviderConfig | None = None,
    vector_store: RagVectorStoreConfig | None = None,
    quality_eval: dict[str, object] | None = None,
) -> dict[str, object]:
    provider_config = provider_config or RagProviderConfig.from_env()
    vector_store = vector_store or RagVectorStoreConfig.from_env()
    report = evaluate_retrieval_report(
        retriever,
        cases,
        k=k,
        threshold=threshold,
        dataset_name=dataset_name,
        knowledge_version=knowledge_version,
    )
    traces = tuple(
        retriever.search_with_trace(
            rewrite_rag_query(case.query, country=case.country),
            country=case.country,
            top_k=k,
        ).as_dict()
        for case in cases[: min(len(cases), 5)]
    )
    observed = _observed_retrieval_summary(traces)
    runtime_stats = _retriever_runtime_stats(retriever)
    enriched: dict[str, object] = {
        **report,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "embedding": {
            "provider": provider_config.embedding_provider,
            "model": provider_config.embedding_model,
            "model_family": _embedding_model_family(provider_config.embedding_model),
            "remote_calls_enabled": provider_config.remote_calls_enabled,
        },
        "rerank": {
            "provider": provider_config.rerank_provider,
            "model": provider_config.rerank_model,
            "remote_calls_enabled": provider_config.remote_calls_enabled,
        },
        "vector_store": {
            "provider": vector_store.provider,
            "endpoint": vector_store.endpoint,
            "collection": vector_store.collection,
            "ready": vector_store.ready,
        },
        "retrieval_routes": {
            "query_rewrite": True,
            "bm25": True,
            "vector": True,
            "rerank": True,
            "parent_child": True,
            "citation_grounding_prompt": True,
        },
        "observed_retrieval": observed,
        "runtime_stats": runtime_stats,
        "live_model_evidence": _live_model_evidence(provider_config, runtime_stats),
        "quality_eval": quality_eval or {},
        "trace_samples": traces,
        "industrial_gate": {
            "metric": f"hit@{k}",
            "threshold": threshold,
            "passed": report["passed_threshold"],
            "use": "低于阈值时不应声称 RAG 检索质量已达业务可用，需要补知识库、调 chunk 或调 rerank。",
        },
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
    return enriched


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


def _observed_retrieval_summary(traces: tuple[dict[str, object], ...]) -> dict[str, object]:
    first = traces[0] if traces else {}
    vector_candidate_count = sum(len(trace.get("vector_candidates", ())) for trace in traces if isinstance(trace, dict))
    bm25_candidate_count = sum(len(trace.get("bm25_candidates", ())) for trace in traces if isinstance(trace, dict))
    return {
        "embedding_provider": str(first.get("embedding_provider", "")),
        "vector_store_provider": str(first.get("vector_store_provider", "")),
        "rerank_provider": str(first.get("rerank_provider", "")),
        "bm25_candidate_count": bm25_candidate_count,
        "vector_candidate_count": vector_candidate_count,
        "qdrant_vector_hits": any(
            isinstance(trace, dict)
            and trace.get("vector_store_provider") == "qdrant"
            and bool(trace.get("vector_candidates"))
            for trace in traces
        ),
    }


def _retriever_runtime_stats(retriever: "HybridRagRetriever") -> dict[str, int]:
    total = RagRuntimeStats()
    for provider in (getattr(retriever, "embedding_provider", None), getattr(retriever, "rerank_provider", None)):
        stats = getattr(provider, "stats", None)
        if not isinstance(stats, RagRuntimeStats):
            continue
        total.embedding_cache_hits += stats.embedding_cache_hits
        total.embedding_remote_calls += stats.embedding_remote_calls
        total.embedding_fallbacks += stats.embedding_fallbacks
        total.rerank_remote_calls += stats.rerank_remote_calls
        total.rerank_fallbacks += stats.rerank_fallbacks
    return total.as_dict()


def _live_model_evidence(provider_config: RagProviderConfig, runtime_stats: dict[str, int]) -> dict[str, object]:
    embedding_calls = int(runtime_stats.get("embedding_remote_calls", 0) or 0)
    embedding_fallbacks = int(runtime_stats.get("embedding_fallbacks", 0) or 0)
    rerank_calls = int(runtime_stats.get("rerank_remote_calls", 0) or 0)
    rerank_fallbacks = int(runtime_stats.get("rerank_fallbacks", 0) or 0)
    embedding_family = _embedding_model_family(provider_config.embedding_model)
    rerank_family = _rerank_model_family(provider_config.rerank_provider, provider_config.rerank_model)
    embedding_verified = (
        provider_config.remote_calls_enabled
        and provider_config.embedding_provider != "local"
        and embedding_calls > 0
        and embedding_fallbacks == 0
    )
    rerank_verified = (
        provider_config.remote_calls_enabled
        and provider_config.rerank_provider != "local"
        and rerank_calls > 0
        and rerank_fallbacks == 0
    )
    blocking_reasons = []
    if not provider_config.remote_calls_enabled:
        blocking_reasons.append("RAG_ENABLE_REMOTE_CALLS 未开启或远程配置未就绪")
    if provider_config.embedding_provider != "local" and embedding_calls == 0:
        blocking_reasons.append("未观测到 embedding 远程调用")
    if embedding_fallbacks:
        blocking_reasons.append("embedding 出现 fallback")
    if provider_config.rerank_provider != "local" and rerank_calls == 0:
        blocking_reasons.append("未观测到 rerank 远程调用")
    if rerank_fallbacks:
        blocking_reasons.append("rerank 出现 fallback")
    verified = embedding_verified and rerank_verified
    if verified:
        status = "verified"
    elif provider_config.remote_calls_enabled and (embedding_calls or rerank_calls):
        status = "partial"
    else:
        status = "fallback"
    return {
        "overall": {
            "verified": verified,
            "status": status,
            "blocking_reasons": tuple(blocking_reasons),
        },
        "embedding": {
            "provider": provider_config.embedding_provider,
            "model": provider_config.embedding_model,
            "model_family": embedding_family,
            "remote_calls_enabled": provider_config.remote_calls_enabled,
            "observed_remote_calls": embedding_calls,
            "fallbacks": embedding_fallbacks,
            "verified_remote_call": embedding_verified,
            "fallback_free": embedding_fallbacks == 0,
        },
        "rerank": {
            "provider": provider_config.rerank_provider,
            "model": provider_config.rerank_model,
            "provider_family": rerank_family,
            "remote_calls_enabled": provider_config.remote_calls_enabled,
            "observed_remote_calls": rerank_calls,
            "fallbacks": rerank_fallbacks,
            "verified_remote_call": rerank_verified,
            "fallback_free": rerank_fallbacks == 0,
        },
    }


def _qdrant_point_from_record(record: dict[str, object]) -> QdrantPoint:
    raw_id = str(record.get("id", "")).strip()
    vector = record.get("vector", ())
    payload = record.get("payload", {})
    if not raw_id or not isinstance(vector, (list, tuple)) or not isinstance(payload, dict):
        raise ValueError("Qdrant point record 不完整")
    return QdrantPoint(
        id=raw_id,
        vector=tuple(float(value) for value in vector),
        payload=dict(payload),
    )


def _milvus_entity_from_point(point: QdrantPoint) -> dict[str, object]:
    entity: dict[str, object] = {
        "id": point.id,
        "vector": [float(value) for value in point.vector],
    }
    for key, value in point.payload.items():
        if key == "metadata" and isinstance(value, dict):
            entity[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            entity[key] = value
    return entity


def _milvus_success(response: dict[str, object]) -> bool:
    code = response.get("code", 0)
    return code in {0, "0", None} and str(response.get("status", "")).lower() not in {"failed", "error"}


def _milvus_insert_count(response: dict[str, object], fallback: int) -> int:
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("insertCount", "insert_count", "row_count", "count"):
            if data.get(key) is not None:
                try:
                    return int(data[key])
                except (TypeError, ValueError):
                    continue
    return fallback


def _milvus_delete_count(response: dict[str, object], fallback: int) -> int:
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("deleteCount", "delete_count", "row_count", "count"):
            if data.get(key) is not None:
                try:
                    return int(data[key])
                except (TypeError, ValueError):
                    continue
    return fallback


def _milvus_create_collection_payload(collection: str, vector_size: int) -> dict[str, object]:
    return {
        "collectionName": collection,
        "schema": {
            "autoID": False,
            "enableDynamicField": False,
            "fields": [
                {"fieldName": "id", "dataType": "VarChar", "isPrimary": True, "elementTypeParams": {"max_length": 128}},
                {"fieldName": "chunk_id", "dataType": "VarChar", "elementTypeParams": {"max_length": 256}},
                {"fieldName": "parent_id", "dataType": "VarChar", "elementTypeParams": {"max_length": 256}},
                {"fieldName": "country", "dataType": "VarChar", "elementTypeParams": {"max_length": 64}},
                {"fieldName": "source_type", "dataType": "VarChar", "elementTypeParams": {"max_length": 128}},
                {"fieldName": "title", "dataType": "VarChar", "elementTypeParams": {"max_length": 512}},
                {"fieldName": "text", "dataType": "VarChar", "elementTypeParams": {"max_length": 8192}},
                {"fieldName": "chunk_index", "dataType": "Int64"},
                {"fieldName": "metadata", "dataType": "VarChar", "elementTypeParams": {"max_length": 4096}},
                {"fieldName": "vector", "dataType": "FloatVector", "elementTypeParams": {"dim": int(vector_size)}},
            ],
        },
        "indexParams": [
            {
                "fieldName": "vector",
                "indexName": "vector_index",
                "metricType": "COSINE",
                "params": {"index_type": "AUTOINDEX"},
            }
        ],
    }


def _milvus_search_scores(response: dict[str, object]) -> dict[str, float]:
    data = response.get("data", ())
    if isinstance(data, dict):
        raw_results = data.get("result", data.get("results", ()))
    else:
        raw_results = data
    if not isinstance(raw_results, list):
        return {}
    rows = raw_results[0] if raw_results and isinstance(raw_results[0], list) else raw_results
    scores: dict[str, float] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        entity = item.get("entity", item)
        if not isinstance(entity, dict):
            continue
        chunk_id = str(entity.get("chunk_id", "")).strip()
        if not chunk_id:
            continue
        raw_score = item.get("score", item.get("distance", item.get("relevance_score", 0.0)))
        try:
            scores[chunk_id] = float(raw_score)
        except (TypeError, ValueError):
            continue
    return scores


def _milvus_vector_size(response: dict[str, object]) -> int | None:
    data = response.get("data")
    if not isinstance(data, dict):
        return None
    schema = data.get("schema")
    fields = schema.get("fields") if isinstance(schema, dict) else data.get("fields")
    if not isinstance(fields, list):
        return None
    for field in fields:
        if not isinstance(field, dict):
            continue
        if str(field.get("name", field.get("fieldName", ""))) != "vector":
            continue
        params = field.get("params", field.get("elementTypeParams"))
        if isinstance(params, list):
            params = {str(item.get("key", "")): item.get("value") for item in params if isinstance(item, dict)}
        if isinstance(params, dict) and params.get("dim") is not None:
            try:
                return int(params["dim"])
            except (TypeError, ValueError):
                return None
    return None


def _milvus_filter_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class HybridRagRetriever:
    def __init__(
        self,
        chunks: tuple[RagChunk, ...],
        *,
        embedding_provider: LocalEmbeddingProvider | None = None,
        rerank_provider: LocalRerankProvider | None = None,
        vector_store_retriever: QdrantVectorStoreRetriever | None = None,
    ):
        self.chunks = chunks
        self.embedding_provider = embedding_provider or LocalEmbeddingProvider()
        self.rerank_provider = rerank_provider or LocalRerankProvider()
        self.vector_store_retriever = vector_store_retriever
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
                self._vector_store_provider_name(),
                self.rerank_provider.provider_name,
                (),
            )
        allowed_sources = set(source_types or ())
        eligible_chunks = tuple(
            chunk
            for chunk in self.chunks
            if chunk.country in {country, "GLOBAL"} and (not allowed_sources or chunk.source_type in allowed_sources)
        )
        local_vector_scores = self.embedding_provider.similarities(
            query,
            tuple(chunk.text + " " + chunk.title for chunk in eligible_chunks),
        )
        remote_vector_scores = self._remote_vector_scores(query, country=country, top_k=vector_top_k)
        scored: list[tuple[RagChunk, float, float]] = []
        for chunk, local_vector in zip(eligible_chunks, local_vector_scores):
            vector = remote_vector_scores.get(chunk.chunk_id, local_vector)
            bm25 = self._bm25(query_tokens, chunk)
            scored.append((chunk, bm25, vector))
        candidates, bm25_ids, vector_ids, exact_ids = self._candidate_pool_with_routes(
            scored,
            query,
            bm25_top_k=bm25_top_k,
            vector_top_k=vector_top_k,
        )
        rerank_scores = self.rerank_provider.rerank_many(query, country, tuple(candidates))
        hits = []
        for (chunk, bm25, vector), rerank in zip(candidates, rerank_scores):
            memory_weight = _memory_weight(chunk)
            weighted_rerank = rerank * memory_weight
            hits.append(
                RagHit(
                    chunk,
                    round(bm25, 4),
                    round(vector, 4),
                    round(weighted_rerank, 4),
                    _reason(chunk, bm25, vector, self.embedding_provider.provider_name, self.rerank_provider.provider_name, memory_weight),
                )
            )
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
            vector_store_provider=self._vector_store_provider_name(),
            rerank_provider=self.rerank_provider.provider_name,
            final_hits=tuple(ranked[:top_k]),
        )

    def _remote_vector_scores(self, query: str, *, country: str, top_k: int) -> dict[str, float]:
        if self.vector_store_retriever is None:
            return {}
        try:
            query_vector = self.embedding_provider.query_vector(query)
            if not query_vector:
                return {}
            return self.vector_store_retriever.search(query_vector, country=country, top_k=top_k)
        except Exception:
            return {}

    def _vector_store_provider_name(self) -> str:
        if self.vector_store_retriever is None:
            return "local"
        return self.vector_store_retriever.provider_name

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


def _reason(chunk: RagChunk, bm25: float, vector: float, embedding_provider: str, rerank_provider: str, memory_weight: float = 1.0) -> str:
    reason = f"{chunk.source_type}命中；BM25={bm25:.2f}；Embedding={embedding_provider}:{vector:.2f}；Rerank={rerank_provider}"
    if memory_weight != 1.0:
        reason = f"{reason}；MemoryWeight={memory_weight:.2f}"
    return reason


def _memory_weight(chunk: RagChunk) -> float:
    value = chunk.metadata.get("memory_weight") if isinstance(chunk.metadata, dict) else None
    try:
        weight = float(value)
    except (TypeError, ValueError):
        return 1.0
    if weight <= 0:
        return 1.0
    return max(0.05, min(weight, 5.0))


def _embedding_model_family(model: str) -> str:
    normalized = model.lower()
    if "qwen3" in normalized or normalized == "text-embedding-v4":
        return "Qwen3-Embedding"
    if normalized.startswith("text-embedding-v"):
        return "DashScope-Embedding"
    if normalized.startswith("local"):
        return "Local"
    return "External"


def _rerank_model_family(provider: str, model: str) -> str:
    normalized_provider = provider.lower()
    normalized_model = model.lower()
    if normalized_provider in {"bge", "bge-reranker", "baai"} or "bge-reranker-v2" in normalized_model:
        return "BGE-Reranker-v2"
    if "qwen3" in normalized_model:
        return "Qwen3-Rerank"
    if "gte" in normalized_model:
        return "DashScope-Rerank"
    if normalized_provider == "local" or normalized_model.startswith("local"):
        return "Local"
    return "External"


def _rag_model_config_errors(embedding_model: str, rerank_model: str) -> tuple[str, ...]:
    errors = []
    if _looks_like_vlm_model(embedding_model):
        errors.append(f"Embedding 模型不能使用视觉理解模型 {embedding_model}")
    if _looks_like_vlm_model(rerank_model):
        errors.append(f"Rerank 模型不能使用视觉理解模型 {rerank_model}")
    return tuple(errors)


def _looks_like_vlm_model(model: str) -> bool:
    normalized = model.lower()
    return "qwen3-vl" in normalized or "qwen-vl" in normalized or normalized.endswith("-vl") or "vision" in normalized


def _first_nonempty_env(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


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


def _qwen_chat_transport(payload: dict[str, object], api_key: str, endpoint: str) -> dict[str, object]:
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
    with request.urlopen(req, timeout=20, context=_https_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _rag_generation_user_prompt(prompt: RagPrompt) -> str:
    citations = "、".join(prompt.citations) if prompt.citations else "无"
    return (
        "请只根据提供的资料回答，资料里没有依据就说不知道；不要编造来源、数据或规则。\n"
        "回答结构：结论；依据；风险；建议。\n"
        f"问题：{prompt.query}\n"
        f"引用ID：{citations}\n"
        "资料：\n"
        f"{prompt.context}\n"
    )


def _extract_chat_completion_text(response: dict[str, object]) -> str:
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content", "")
                if isinstance(content, str):
                    return content
            text = first.get("text", "")
            if isinstance(text, str):
                return text
    output = response.get("output")
    if isinstance(output, dict):
        text = output.get("text", "")
        if isinstance(text, str):
            return text
    return ""


def _qdrant_json_request(method: str, endpoint: str, payload: dict[str, object] | None, api_key: str) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["api-key"] = api_key
    req = request.Request(endpoint, data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=20, context=_https_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _milvus_json_request(method: str, endpoint: str, payload: dict[str, object] | None, api_key: str) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = request.Request(endpoint, data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=20, context=_https_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _https_context():
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _qdrant_vector_size(response: dict[str, object]) -> int | None:
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    config = result.get("config")
    if not isinstance(config, dict):
        return None
    params = config.get("params")
    if not isinstance(params, dict):
        return None
    vectors = params.get("vectors")
    if isinstance(vectors, dict):
        raw_size = vectors.get("size")
        if raw_size is not None:
            try:
                return int(raw_size)
            except (TypeError, ValueError):
                return None
        for value in vectors.values():
            if isinstance(value, dict) and value.get("size") is not None:
                try:
                    return int(value["size"])
                except (TypeError, ValueError):
                    return None
    return None


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
