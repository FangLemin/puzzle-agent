from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import time
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from puzzle_ops.agents import PuzzleOpsAgent
from puzzle_ops.models import DemandRow
from puzzle_ops.production import resolve_runtime_dir


ROLE_LEVELS = {"viewer": 1, "operator": 2, "admin": 3}


@dataclass(frozen=True)
class ApiUser:
    user_id: str
    token: str
    role: str
    countries: tuple[str, ...]

    def can_access_country(self, country: str) -> bool:
        return "*" in self.countries or country in self.countries

    def has_role(self, required_role: str) -> bool:
        return ROLE_LEVELS.get(self.role, 0) >= ROLE_LEVELS.get(required_role, 99)


class RagSearchRequest(BaseModel):
    country: str
    query: str
    task_type: str = "value_master"
    top_k: int = Field(default=5, ge=1, le=20)
    use_query_rewrite: bool = True
    require_citation: bool = True


class ValueAnalyzeRequest(BaseModel):
    country: str
    local_image_path: str = ""
    operation_tag: str = ""
    subject: str = ""
    js_category: str = ""
    include_visual_similarity: bool = True
    include_rag: bool = True
    dry_run: bool = True


class VisualSimilaritySearchRequest(BaseModel):
    country: str
    local_image_path: str
    operation_tag: str = ""
    subject: str = ""
    js_category: str = ""
    top_k: int = Field(default=5, ge=1, le=20)
    min_reference_score: float | None = None


def parse_api_tokens(raw: str | None = None) -> dict[str, ApiUser]:
    """Parse PUZZLEOPS_API_TOKENS.

    Format:
        user_id:token:role:country|country,user2:token2:role:*
    """

    value = raw if raw is not None else os.environ.get("PUZZLEOPS_API_TOKENS", "")
    users: dict[str, ApiUser] = {}
    for entry in value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":", 3)
        if len(parts) != 4:
            continue
        user_id, token, role, countries = (part.strip() for part in parts)
        if not user_id or not token:
            continue
        normalized_role = role if role in ROLE_LEVELS else "viewer"
        country_list = tuple(part.strip() for part in countries.replace("，", "|").split("|") if part.strip())
        users[token] = ApiUser(
            user_id=user_id,
            token=token,
            role=normalized_role,
            countries=country_list or ("*",),
        )
    return users


def create_app(agent: PuzzleOpsAgent | None = None) -> FastAPI:
    app = FastAPI(
        title="PuzzleOps Agent API",
        version=_read_version(),
        description="FastAPI service layer for the PuzzleOps multimodal operations Agent.",
    )
    app.state.agent = agent or PuzzleOpsAgent()

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": _error_payload(detail.get("code", "http_error"), detail.get("message", ""))})

    @app.get("/api/health")
    def health(user: ApiUser = Depends(_require_role("viewer"))):
        api_agent = _agent(app)
        return {
            "status": "ok",
            "version": _read_version(),
            "runtime_dir": str(resolve_runtime_dir()),
            "auth": {"user_id": user.user_id, "role": user.role, "countries": user.countries},
            "providers": _provider_health(api_agent),
        }

    @app.post("/api/rag/search")
    def rag_search(payload: RagSearchRequest, user: ApiUser = Depends(_require_role("viewer"))):
        _ensure_country(user, payload.country)
        started_at = time.perf_counter()
        api_agent = _agent(app)
        prompt = api_agent.value_audit_rag_answer(payload.country, payload.query, top_k=payload.top_k, task_index=payload.task_type)
        citation_details = api_agent.rag_citation_details(payload.country, getattr(prompt, "citations", ()))
        citations = tuple(_citation_payload(citation) for citation in citation_details)
        if payload.require_citation and not citations:
            raise _api_error(422, "not_evaluable", "RAG did not return usable citations; please review the knowledge base or query.")
        trace = getattr(api_agent, "_last_rag_trace", {}) if isinstance(getattr(api_agent, "_last_rag_trace", {}), dict) else {}
        final_hits = trace.get("final_hits", ()) if isinstance(trace, dict) else ()
        return {
            "country": payload.country,
            "query": payload.query,
            "rewritten_query": getattr(api_agent, "_last_rag_rewritten_query", ""),
            "citations": citations,
            "context": getattr(prompt, "context", ""),
            "prompt": getattr(prompt, "prompt", ""),
            "trace": {
                "bm25_candidates": _trace_count(trace, "bm25_hits"),
                "vector_candidates": _trace_count(trace, "vector_hits"),
                "reranked": len(final_hits) if isinstance(final_hits, (tuple, list)) else len(citations),
                "latency_ms": round((time.perf_counter() - started_at) * 1000, 4),
            },
        }

    @app.post("/api/value/analyze")
    def value_analyze(payload: ValueAnalyzeRequest, user: ApiUser = Depends(_require_role("operator"))):
        _ensure_country(user, payload.country)
        api_agent = _agent(app)
        row = DemandRow(
            need_type="试新",
            country=payload.country,
            js_category=payload.js_category or "unknown",
            image_name=Path(payload.local_image_path).name if payload.local_image_path else payload.subject or payload.operation_tag,
            operation_tag=payload.operation_tag or f"API_{payload.country}_{payload.subject}",
            subject=payload.subject,
            count=1,
            priority="P1",
            method="先照片后AI",
            delivery_date="",
            subject_description="",
            remark="",
            reference_image_path=payload.local_image_path,
        )
        analyzed = api_agent.apply_value_master(row)
        visual_similarity = (
            api_agent.similar_visual_history_for_candidate(
                {
                    "country": payload.country,
                    "local_image_path": payload.local_image_path,
                    "operation_tag": analyzed.operation_tag,
                    "subject": analyzed.subject,
                    "js_category": analyzed.js_category,
                },
                top_k=5,
            )
            if payload.include_visual_similarity and payload.local_image_path
            else {"status": "not_requested", "message": "visual similarity not requested or image path is empty"}
        )
        return {
            "analysis_id": f"value_{uuid.uuid4().hex[:12]}",
            "country": payload.country,
            "visual_parse": _three_part_description(analyzed.subject_description or analyzed.remark or analyzed.subject),
            "value_match": analyzed.value_match,
            "risks": _risk_lines(analyzed.value_match, analyzed.remark),
            "visual_similarity_evidence": _jsonable(visual_similarity),
            "requires_human_review": True,
            "dry_run": payload.dry_run,
        }

    @app.get("/api/harness/summary")
    def harness_summary(country: str, user: ApiUser = Depends(_require_role("viewer"))):
        _ensure_country(user, country)
        api_agent = _agent(app)
        summary = api_agent.harness_summary(country)
        baseline = api_agent.harness_baseline_summary(country)
        return {
            "country": country,
            "dataset": {
                "real_samples": _first_int(summary, ("真实样本数", "real_samples", "real_sample_count")),
                "synthetic_samples": _first_int(summary, ("合成样本数", "synthetic_samples", "synthetic_sample_count")),
                "gold_label_coverage": _first_float(summary, ("gold覆盖率", "gold_label_coverage", "gold_complete_rate")),
                "raw": _jsonable(summary),
            },
            "latest_run": {
                "failure_count": _first_int(baseline, ("失败样本数", "failure_sample_count", "failure_count")),
                "raw": _jsonable(baseline),
            },
        }

    @app.post("/api/visual-similarity/search")
    def visual_similarity_search(payload: VisualSimilaritySearchRequest, user: ApiUser = Depends(_require_role("operator"))):
        _ensure_country(user, payload.country)
        evidence = _agent(app).similar_visual_history_for_candidate(
            {
                "country": payload.country,
                "local_image_path": payload.local_image_path,
                "operation_tag": payload.operation_tag,
                "subject": payload.subject,
                "js_category": payload.js_category,
            },
            top_k=payload.top_k,
        )
        if payload.min_reference_score is not None:
            evidence = dict(evidence)
            evidence["requested_min_reference_score"] = payload.min_reference_score
        return _jsonable(evidence)

    return app


def _require_role(required_role: str):
    def dependency(authorization: str = Header(default="")) -> ApiUser:
        token = _bearer_token(authorization)
        if not token:
            raise _api_error(401, "unauthorized", "missing bearer token")
        users = parse_api_tokens()
        user = users.get(token)
        if user is None:
            raise _api_error(401, "unauthorized", "invalid bearer token")
        if not user.has_role(required_role):
            raise _api_error(403, "forbidden_role", f"{required_role} role required")
        return user

    return dependency


def _agent(app: FastAPI):
    return app.state.agent


def _bearer_token(authorization: str) -> str:
    prefix = "Bearer "
    return authorization[len(prefix) :].strip() if authorization.startswith(prefix) else ""


def _ensure_country(user: ApiUser, country: str) -> None:
    if not user.can_access_country(country):
        raise _api_error(403, "forbidden_country", f"{user.user_id} cannot access {country}")


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _error_payload(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message, "request_id": f"req_{uuid.uuid4().hex[:12]}"}


def _provider_health(agent) -> dict[str, object]:
    rag = getattr(agent, "rag_provider_config", None)
    vector = getattr(agent, "rag_vector_store_config", None)
    visual = getattr(agent, "visual_embedding_provider", None)
    feishu = getattr(agent, "feishu", None)
    return {
        "vision_llm": {
            "provider": os.environ.get("VISION_LLM_PROVIDER", "qwen"),
            "model": os.environ.get("QWEN_VISION_MODEL", ""),
            "configured": bool(os.environ.get("QWEN_API_KEY") or os.environ.get("OPENAI_API_KEY")),
            "remote_calls_enabled": os.environ.get("VISION_LLM_PROVIDER", "").lower() in {"qwen", "openai"},
        },
        "rag_embedding": {
            "provider": getattr(rag, "embedding_provider", os.environ.get("RAG_EMBEDDING_PROVIDER", "local")),
            "model": getattr(rag, "embedding_model", os.environ.get("RAG_EMBEDDING_MODEL", "local-token-cosine")),
            "remote_calls_enabled": bool(getattr(rag, "remote_calls_enabled", False)),
        },
        "rag_rerank": {
            "provider": getattr(rag, "rerank_provider", os.environ.get("RAG_RERANK_PROVIDER", "local")),
            "model": getattr(rag, "rerank_model", os.environ.get("RAG_RERANK_MODEL", "local-rule-rerank")),
            "remote_calls_enabled": bool(getattr(rag, "remote_calls_enabled", False)),
        },
        "milvus": {
            "provider": getattr(vector, "provider", os.environ.get("RAG_VECTOR_STORE_PROVIDER", "")),
            "collection": getattr(vector, "collection", os.environ.get("MILVUS_COLLECTION", "")),
            "configured": bool(getattr(vector, "ready", False) or os.environ.get("MILVUS_URI")),
        },
        "visual_embedding": {
            "provider": os.environ.get("VISUAL_EMBEDDING_PROVIDER", "qwen"),
            "model": getattr(visual, "model", os.environ.get("VISUAL_EMBEDDING_MODEL", "qwen3-vl-embedding")),
            "remote_calls_enabled": bool(getattr(visual, "remote_calls_enabled", False)),
        },
        "feishu": {"configured": bool(getattr(feishu, "configured", False) or os.environ.get("FEISHU_APP_ID"))},
    }


def _citation_payload(citation: dict[str, object]) -> dict[str, object]:
    return {
        "citation_id": str(citation.get("chunk_id", "")),
        "parent_id": str(citation.get("parent_id", "")),
        "title": str(citation.get("title", "")),
        "source": str(citation.get("source_type", "")),
        "snippet": str(citation.get("text", ""))[:500],
    }


def _trace_count(trace: dict[str, object], key: str) -> int:
    value = trace.get(key, ())
    return len(value) if isinstance(value, (tuple, list)) else 0


def _three_part_description(text: str) -> dict[str, str]:
    subject = _section_value(text, "主体内容") or text
    color = _section_value(text, "色彩氛围")
    composition = _section_value(text, "构图环境")
    return {
        "subject_content": subject.strip("；。 "),
        "color_mood": color.strip("；。 "),
        "composition_environment": composition.strip("；。 "),
    }


def _section_value(text: str, label: str) -> str:
    marker = f"{label}："
    if marker not in text:
        return ""
    tail = text.split(marker, 1)[1]
    for next_label in ("主体内容：", "色彩氛围：", "构图环境："):
        if next_label == marker:
            continue
        if next_label in tail:
            tail = tail.split(next_label, 1)[0]
    return tail


def _risk_lines(value_match: str, remark: str) -> tuple[str, ...]:
    text = "；".join(part for part in (value_match, remark) if part)
    risks = tuple(part.strip() for part in text.replace("。", "；").split("；") if "风险" in part or "复核" in part)
    return risks or ("需人工复核。",)


def _first_int(payload: dict[str, object], keys: tuple[str, ...]) -> int:
    for key in keys:
        try:
            return int(payload.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
    return 0


def _first_float(payload: dict[str, object], keys: tuple[str, ...]) -> float | str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            return str(value)
    return "not_evaluable"


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _read_version() -> str:
    version_path = Path(__file__).resolve().parent.parent / "VERSION"
    return version_path.read_text(encoding="utf-8").strip() if version_path.exists() else "dev"


app = create_app()
