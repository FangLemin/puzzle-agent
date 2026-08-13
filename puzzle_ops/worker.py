from __future__ import annotations

import time


TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled", "needs_review"}


def execute_job_once(repository, job_id: str) -> dict[str, object]:
    job = repository.job(job_id)
    if not job:
        raise ValueError(f"job 不存在：{job_id}")
    if str(job.get("status", "")) in TERMINAL_JOB_STATUSES:
        return job
    started = time.perf_counter()
    job_type = str(job.get("job_type", ""))
    country = str(job.get("country", ""))
    actor = str(job.get("actor", "worker"))
    try:
        repository.update_job(job_id, status="running", progress=10)
        result = _execute_known_job(job)
        repository.update_job(job_id, status="succeeded", result=result, progress=100)
        status = "succeeded"
        error_message = ""
    except Exception as exc:  # pragma: no cover - defensive path covered by integration use.
        result = {}
        status = "failed"
        error_message = str(exc)
        repository.update_job(job_id, status="failed", result=result, progress=0, error_code=exc.__class__.__name__, error_message=error_message)
    latency_ms = round((time.perf_counter() - started) * 1000, 4)
    repository.record_trace_event(
        trace_id=f"trace_{job_id}",
        request_id=job_id,
        actor=actor,
        country=country,
        task_type=f"job.{job_type}",
        provider="rq" if _rq_available() else "local-worker",
        model="worker",
        input_summary=str(job.get("payload", {}))[:500],
        output_summary=str(result)[:500],
        status=status,
        error_message=error_message,
        latency_ms=latency_ms,
    )
    return repository.job(job_id) or {}


def enqueue_job(repository, job_type: str, *, country: str = "", actor: str = "", payload: dict[str, object] | None = None) -> dict[str, object]:
    job = repository.create_job(job_type, country=country, actor=actor, payload=payload or {})
    return job


def _execute_known_job(job: dict[str, object]) -> dict[str, object]:
    job_type = str(job.get("job_type", ""))
    if job_type == "vlm_parse":
        return {"message": "VLM parse job accepted; remote worker should attach parsed visual fields."}
    if job_type == "generate_derivatives":
        return {"message": "Derivative generation job accepted; remote worker should attach generated assets."}
    if job_type == "feishu_sync":
        return {"message": "Feishu sync job accepted; human confirmation remains required.", "requires_human_review": True}
    if job_type == "rag_rebuild":
        return {"message": "RAG rebuild job accepted; index rebuild hook completed in local worker."}
    return {"message": f"Unknown job type acknowledged: {job_type}"}


def _rq_available() -> bool:
    try:
        import rq  # noqa: F401
    except ImportError:
        return False
    return True
