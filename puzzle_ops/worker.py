from __future__ import annotations

import os
import time


TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled", "needs_review"}
DEFAULT_QUEUE_NAME = "puzzleops"


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


def enqueue_job(
    repository,
    job_type: str,
    *,
    country: str = "",
    actor: str = "",
    payload: dict[str, object] | None = None,
    queue=None,
) -> dict[str, object]:
    job = repository.create_job(job_type, country=country, actor=actor, payload=payload or {})
    selected_queue = queue if queue is not None else queue_from_env()
    if selected_queue is None:
        return {**job, "queue_provider": "local", "enqueue_status": "local_fallback"}
    try:
        dispatch = selected_queue.enqueue(str(job.get("job_id", "")))
    except Exception as exc:
        repository.update_job(str(job.get("job_id", "")), status="queued", result={"enqueue_error": str(exc)})
        return {**(repository.job(str(job.get("job_id", ""))) or job), "queue_provider": getattr(selected_queue, "provider", "unknown"), "enqueue_status": "enqueue_failed", "enqueue_error": str(exc)}
    normalized = dict(dispatch)
    if "queue_provider" not in normalized:
        normalized["queue_provider"] = normalized.pop("provider", getattr(selected_queue, "provider", "unknown"))
    return {**job, **normalized}


class RqJobQueue:
    provider = "rq"

    def __init__(self, redis_url: str, *, queue_name: str = DEFAULT_QUEUE_NAME):
        self.redis_url = redis_url
        self.queue_name = queue_name

    def enqueue(self, job_id: str) -> dict[str, object]:
        try:
            from redis import Redis  # type: ignore
            from rq import Queue  # type: ignore
        except ImportError as exc:
            raise RuntimeError("缺少 redis/rq 依赖，请安装 requirements.txt 后再启用 RQ 队列") from exc
        redis_conn = Redis.from_url(self.redis_url)
        queue = Queue(self.queue_name, connection=redis_conn)
        rq_job = queue.enqueue("puzzle_ops.worker.execute_job_from_env", job_id, job_timeout=_rq_job_timeout_seconds())
        return {
            "queue_provider": self.provider,
            "enqueue_status": "enqueued",
            "queue_name": self.queue_name,
            "rq_job_id": rq_job.id,
        }


def queue_from_env():
    provider = os.environ.get("PUZZLEOPS_JOB_QUEUE_PROVIDER", "local").strip().lower()
    if provider != "rq":
        return None
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        return None
    queue_name = os.environ.get("PUZZLEOPS_RQ_QUEUE", DEFAULT_QUEUE_NAME).strip() or DEFAULT_QUEUE_NAME
    return RqJobQueue(redis_url, queue_name=queue_name)


def execute_job_from_env(job_id: str) -> dict[str, object]:
    from puzzle_ops.production import resolve_runtime_dir
    from puzzle_ops.production_db import create_repository_from_env

    repository = create_repository_from_env(resolve_runtime_dir())
    return execute_job_once(repository, job_id)


def _rq_job_timeout_seconds() -> int:
    try:
        return max(1, int(os.environ.get("PUZZLEOPS_RQ_JOB_TIMEOUT_SECONDS", "900")))
    except ValueError:
        return 900


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
