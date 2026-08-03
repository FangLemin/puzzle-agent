from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib import request
import base64
import hashlib
import json
import math
import mimetypes
import os


@dataclass(frozen=True)
class VisualEmbedding:
    vector: tuple[float, ...]
    provider: str
    model: str
    dimension: int
    source: str


@dataclass(frozen=True)
class VisualIndexRecord:
    image_id: str
    country: str
    grade: str
    local_image_path: str
    subject: str
    operation_tag: str
    vector: tuple[float, ...]
    provider: str
    model: str

    @classmethod
    def from_image(
        cls,
        *,
        image_id: str,
        country: str,
        grade: str,
        local_image_path: str,
        subject: str,
        operation_tag: str,
        embedding: VisualEmbedding,
    ) -> "VisualIndexRecord":
        return cls(
            image_id=image_id,
            country=country,
            grade=grade,
            local_image_path=local_image_path,
            subject=subject,
            operation_tag=operation_tag,
            vector=embedding.vector,
            provider=embedding.provider,
            model=embedding.model,
        )


class LocalVisualEmbeddingProvider:
    provider_name = "local-visual-hash"

    def __init__(self, dimension: int = 64):
        self.dimension = max(int(dimension), 4)
        self.model = "local-image-hash-v1"

    def embed_image(self, path: str, text: str = "") -> VisualEmbedding:
        image_path = Path(path).expanduser()
        image_bytes = image_path.read_bytes()
        seed = hashlib.sha256(image_bytes + text.encode("utf-8")).digest()
        values = []
        counter = 0
        while len(values) < self.dimension:
            digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            for index in range(0, len(digest), 4):
                raw = int.from_bytes(digest[index : index + 4], "big")
                values.append((raw / 0xFFFFFFFF) * 2 - 1)
                if len(values) >= self.dimension:
                    break
            counter += 1
        vector = _normalize(tuple(values))
        return VisualEmbedding(vector=vector, provider=self.provider_name, model=self.model, dimension=len(vector), source=str(image_path))


class QwenVLImageEmbeddingProvider:
    provider_name = "qwen-vl-embedding"

    def __init__(
        self,
        api_key: str,
        model: str = "qwen3-vl-embedding",
        endpoint: str = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding",
        transport=None,
    ):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        self.transport = transport or _dashscope_multimodal_embedding_transport

    @classmethod
    def from_env(cls) -> "QwenVLImageEmbeddingProvider | LocalVisualEmbeddingProvider":
        enabled = os.getenv("VISUAL_EMBEDDING_ENABLE_REMOTE_CALLS", "").strip().lower() in {"1", "true", "yes", "on"}
        api_key = os.getenv("VISUAL_EMBEDDING_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY") or ""
        if not enabled or not api_key:
            return LocalVisualEmbeddingProvider(dimension=int(os.getenv("VISUAL_EMBEDDING_LOCAL_DIMENSION", "64")))
        return cls(
            api_key=api_key,
            model=os.getenv("VISUAL_EMBEDDING_MODEL", "qwen3-vl-embedding"),
            endpoint=os.getenv(
                "VISUAL_EMBEDDING_ENDPOINT",
                "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding",
            ),
        )

    def embed_image(self, path: str, text: str = "") -> VisualEmbedding:
        image_path = Path(path).expanduser()
        contents = []
        if text:
            contents.append({"text": text})
        contents.append({"image": _image_data_uri(image_path)})
        payload = {"model": self.model, "input": {"contents": contents}}
        response = self.transport(payload, self.api_key, self.endpoint)
        vector = _extract_multimodal_embedding(response)
        return VisualEmbedding(vector=tuple(float(value) for value in vector), provider=self.provider_name, model=self.model, dimension=len(vector), source=str(image_path))


class VisualSimilarityIndex:
    def __init__(self):
        self._records: dict[str, VisualIndexRecord] = {}

    @property
    def record_count(self) -> int:
        return len(self._records)

    def upsert(self, records: tuple[VisualIndexRecord, ...]) -> dict[str, object]:
        for record in records:
            self._records[record.image_id] = record
        return {"status": "ok", "upsert_count": len(records)}

    def search(self, query: VisualEmbedding, *, country: str, top_k: int = 5) -> tuple[dict[str, object], ...]:
        hits = []
        for record in self._records.values():
            if record.country != country:
                continue
            score = _cosine(query.vector, record.vector)
            hits.append(_hit_from_record(record, score))
        hits.sort(key=lambda item: float(item["score"]), reverse=True)
        return tuple(hits[: max(int(top_k), 0)])

    def grouped_search(self, query: VisualEmbedding, *, country: str, top_k: int = 6) -> dict[str, object]:
        hits = self.search(query, country=country, top_k=top_k)
        good = tuple(hit for hit in hits if str(hit.get("grade")) in {"S", "A"})
        neutral = tuple(hit for hit in hits if str(hit.get("grade")) == "B")
        risk = tuple(hit for hit in hits if str(hit.get("grade")) in {"C", "D"})
        return {
            "status": "ok" if hits else "no_hits",
            "similar_good": good,
            "similar_neutral": neutral,
            "similar_risk": risk,
            "all_hits": hits,
            "retrieval_mode": "visual_embedding",
        }


class VisualMilvusImageStore:
    def __init__(self, endpoint: str, token: str, collection: str, transport=None):
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.collection = collection
        self.transport = transport or _milvus_json_request

    @classmethod
    def from_env(cls):
        enabled = os.getenv("VISUAL_MILVUS_ENABLE_REMOTE_CALLS", "").strip().lower() in {"1", "true", "yes", "on"}
        if not enabled:
            return None
        endpoint = os.getenv("VISUAL_MILVUS_URI") or os.getenv("MILVUS_URI") or ""
        token = os.getenv("VISUAL_MILVUS_TOKEN") or os.getenv("MILVUS_TOKEN") or ""
        collection = os.getenv("VISUAL_MILVUS_COLLECTION", "puzzleops_image_embeddings")
        if not endpoint or not token:
            return None
        return cls(endpoint=endpoint, token=token, collection=collection)

    def ensure_collection(self, vector_size: int) -> dict[str, object]:
        describe = self.transport("POST", f"{self.endpoint}/v2/vectordb/collections/describe", {"collectionName": self.collection}, self.token)
        if describe.get("data"):
            return {"status": "exists", "collection": self.collection, "vector_size": vector_size}
        payload = {
            "collectionName": self.collection,
            "dimension": int(vector_size),
            "primaryField": "id",
            "vectorField": "vector",
            "metricType": "COSINE",
        }
        self.transport("POST", f"{self.endpoint}/v2/vectordb/collections/create", payload, self.token)
        self.transport("POST", f"{self.endpoint}/v2/vectordb/collections/load", {"collectionName": self.collection}, self.token)
        return {"status": "created", "collection": self.collection, "vector_size": vector_size}

    def upsert(self, records: tuple[VisualIndexRecord, ...]) -> dict[str, object]:
        payload = {
            "collectionName": self.collection,
            "data": [
                {
                    "id": record.image_id,
                    "image_id": record.image_id,
                    "country": record.country,
                    "grade": record.grade,
                    "subject": record.subject,
                    "operation_tag": record.operation_tag,
                    "local_image_path": record.local_image_path,
                    "provider": record.provider,
                    "model": record.model,
                    "vector": [float(value) for value in record.vector],
                }
                for record in records
            ],
        }
        response = self.transport("POST", f"{self.endpoint}/v2/vectordb/entities/insert", payload, self.token)
        data = response.get("data", {}) if isinstance(response.get("data", {}), dict) else {}
        return {"status": "ok" if int(response.get("code", 0) or 0) == 0 else "failed", "insert_count": int(data.get("insertCount", len(records)) or 0), "response": response}

    def search(self, query_vector: tuple[float, ...], *, country: str, top_k: int) -> tuple[dict[str, object], ...]:
        payload = {
            "collectionName": self.collection,
            "data": [[float(value) for value in query_vector]],
            "limit": int(top_k),
            "filter": f'country in ["{country}"]',
            "outputFields": ["image_id", "country", "grade", "subject", "operation_tag", "local_image_path", "provider", "model"],
        }
        response = self.transport("POST", f"{self.endpoint}/v2/vectordb/entities/search", payload, self.token)
        rows = response.get("data", [])
        if rows and isinstance(rows[0], list):
            rows = rows[0]
        hits = []
        for row in rows if isinstance(rows, list) else []:
            entity = row.get("entity", row) if isinstance(row, dict) else {}
            if not isinstance(entity, dict):
                continue
            score = float(row.get("distance", row.get("score", 0.0)) or 0.0) if isinstance(row, dict) else 0.0
            hits.append(
                {
                    "image_id": str(entity.get("image_id", "")),
                    "country": str(entity.get("country", "")),
                    "grade": str(entity.get("grade", "")),
                    "subject": str(entity.get("subject", "")),
                    "operation_tag": str(entity.get("operation_tag", "")),
                    "local_image_path": str(entity.get("local_image_path", "")),
                    "score": round(score, 4),
                    "reason": f"图像向量相似度 {round(score, 4)}，来自 Milvus/Zilliz 图像索引。",
                }
            )
        return tuple(hits)


def _image_data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _dashscope_multimodal_embedding_transport(payload: dict[str, object], api_key: str, endpoint: str) -> dict[str, object]:
    req = request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def _milvus_json_request(method: str, endpoint: str, payload: dict[str, object] | None, token: str) -> dict[str, object]:
    req = request.Request(
        endpoint,
        data=json.dumps(payload or {}, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    with request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _extract_multimodal_embedding(response: dict[str, object]) -> tuple[float, ...]:
    output = response.get("output", {}) if isinstance(response, dict) else {}
    embeddings = output.get("embeddings", []) if isinstance(output, dict) else []
    if embeddings and isinstance(embeddings[0], dict):
        vector = embeddings[0].get("embedding", ())
        return tuple(float(value) for value in vector)
    data = response.get("data", []) if isinstance(response, dict) else []
    if data and isinstance(data[0], dict):
        vector = data[0].get("embedding", ())
        return tuple(float(value) for value in vector)
    raise RuntimeError("Qwen3-VL-Embedding 响应缺少 embedding 向量")


def _normalize(vector: tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return tuple(round(value / norm, 8) for value in vector)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    dot = sum(left[index] * right[index] for index in range(size))
    left_norm = math.sqrt(sum(left[index] * left[index] for index in range(size)))
    right_norm = math.sqrt(sum(right[index] * right[index] for index in range(size)))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return round(dot / (left_norm * right_norm), 4)


def _hit_from_record(record: VisualIndexRecord, score: float) -> dict[str, object]:
    return {
        "image_id": record.image_id,
        "country": record.country,
        "grade": record.grade,
        "subject": record.subject,
        "operation_tag": record.operation_tag,
        "local_image_path": record.local_image_path,
        "score": round(float(score), 4),
        "reason": f"图像向量相似度 {round(float(score), 4)}；主体={record.subject or '未标注'}；历史等级={record.grade}。",
    }
