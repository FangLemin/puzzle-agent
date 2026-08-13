from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import statistics
import uuid

from puzzle_ops.models import HistoricalRecord
from puzzle_ops.rag import RagChunk, RagDocument


class PuzzleRepository:
    backend = "sqlite"

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def save_history_records(self, records: tuple[HistoricalRecord, ...]) -> None:
        if not records:
            return
        fields = tuple(asdict(records[0]).keys())
        placeholders = ", ".join("?" for _ in fields)
        columns = ", ".join(fields)
        updates = ", ".join(f"{field}=excluded.{field}" for field in fields if field != "image_id")
        sql = f"INSERT INTO historical_records ({columns}) VALUES ({placeholders}) ON CONFLICT(image_id) DO UPDATE SET {updates}"
        with self._connect() as conn:
            conn.executemany(sql, [tuple(asdict(record)[field] for field in fields) for record in records])

    def history_records(self, country: str | None = None) -> tuple[HistoricalRecord, ...]:
        where = " WHERE country = ?" if country else ""
        params = (country,) if country else ()
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM historical_records{where} ORDER BY distribution_date, position", params).fetchall()
        return tuple(HistoricalRecord(**dict(row)) for row in rows)

    def add_memory(self, country: str, memory_type: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agent_memory(country, memory_type, content) VALUES (?, ?, ?)",
                (country, memory_type, content),
            )

    def memories(self, country: str) -> tuple[dict[str, str], ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT country, memory_type, content FROM agent_memory WHERE country = ? ORDER BY memory_id DESC",
                (country,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def add_layered_memory(
        self,
        country: str,
        memory_layer: str,
        memory_type: str,
        payload: dict[str, object],
        *,
        ttl_seconds: int | None = None,
        source_memory_id: int | None = None,
        human_verified: bool = False,
        created_by: str = "",
        review_status: str = "draft",
        approved_for_rag: bool = False,
        memory_scope: str = "operational_fact",
    ) -> int:
        if review_status not in {"draft", "approved", "rejected", "conflict_locked", "retired"}:
            raise ValueError(f"未知 memory review_status：{review_status}")
        memory_scope = memory_scope if memory_scope in {"operational_fact", "personal_preference"} else "operational_fact"
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = _text_hash(encoded)
        expires_at = None
        if ttl_seconds is not None:
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
        with self._connect() as conn:
            self._expire_layered_memories_conn(conn)
            existing = conn.execute(
                """
                SELECT memory_id FROM layered_memory
                WHERE country = ? AND memory_layer = ? AND memory_type = ?
                  AND fingerprint = ? AND status = 'active'
                  AND ((? IS NULL AND source_memory_id IS NULL) OR source_memory_id = ?)
                ORDER BY memory_id DESC LIMIT 1
                """,
                (country, memory_layer, memory_type, fingerprint, source_memory_id, source_memory_id),
            ).fetchone()
            if existing:
                return int(existing["memory_id"])
            cursor = conn.execute(
                """
                INSERT INTO layered_memory(
                    country, memory_layer, memory_type, payload, status, source_memory_id,
                    expires_at, fingerprint, human_verified, created_by, updated_by,
                    approved_by, approved_at, approved_for_rag, review_status, memory_scope, updated_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? = 'approved' THEN CURRENT_TIMESTAMP ELSE NULL END, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    country,
                    memory_layer,
                    memory_type,
                    encoded,
                    source_memory_id,
                    expires_at,
                    fingerprint,
                    int(human_verified),
                    created_by,
                    created_by,
                    created_by if review_status == "approved" else "",
                    review_status,
                    int(approved_for_rag),
                    review_status,
                    memory_scope,
                ),
            )
            memory_id = int(cursor.lastrowid)
            self._record_memory_audit_conn(
                conn,
                country=country,
                memory_id=memory_id,
                action="create",
                actor=created_by,
                new_review_status=review_status,
                approved_for_rag=approved_for_rag,
                metadata={"memory_layer": memory_layer, "memory_type": memory_type},
            )
            return memory_id

    def layered_memories(
        self,
        country: str,
        layer: str | None = None,
        *,
        include_inactive: bool = False,
    ) -> tuple[dict[str, object], ...]:
        self.expire_layered_memories()
        where = "WHERE country = ?"
        params: tuple[str, ...] = (country,)
        if layer:
            where += " AND memory_layer = ?"
            params = (country, layer)
        if not include_inactive:
            where += " AND status = 'active'"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT memory_id, country, memory_layer, memory_type, payload, status,
                       source_memory_id, expires_at, fingerprint, human_verified,
                       created_by, updated_by, approved_by, approved_at, retired_by,
                       retired_at, approved_for_rag, review_status, rag_hit_count,
                       last_rag_hit_at, memory_scope, created_at, updated_at
                FROM layered_memory {where} ORDER BY memory_id
                """,
                params,
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(str(item["payload"]))
            except json.JSONDecodeError:
                item["payload"] = {}
            item["human_verified"] = bool(item.get("human_verified"))
            item["approved_for_rag"] = bool(item.get("approved_for_rag"))
            item["rag_hit_count"] = int(item.get("rag_hit_count") or 0)
            items.append(item)
        return tuple(items)

    def migrate_layered_memory_country(self, memory_id: int, *, target_country: str, actor: str = "", note: str = "") -> int:
        with self._connect() as conn:
            source = conn.execute("SELECT * FROM layered_memory WHERE memory_id = ?", (memory_id,)).fetchone()
            if source is None:
                raise ValueError(f"memory_id 不存在：{memory_id}")
            if str(source["country"]) == target_country:
                raise ValueError("目标国家不能与源国家相同")
            try:
                payload = json.loads(str(source["payload"]))
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload = dict(payload)
            payload["source_country"] = str(source["country"])
            payload["migration_note"] = note.strip()
            migrated_id = self.add_layered_memory(
                target_country,
                str(source["memory_layer"]),
                str(source["memory_type"]),
                payload,
                source_memory_id=memory_id,
                human_verified=bool(source["human_verified"]),
                created_by=actor,
                review_status="draft",
                approved_for_rag=False,
                memory_scope=str(source["memory_scope"] or "operational_fact"),
            )
        self.retire_layered_memory(memory_id, actor=actor)
        with self._connect() as conn:
            self._record_memory_audit_conn(
                conn,
                country=str(source["country"]),
                memory_id=memory_id,
                action="country_migrate_out",
                actor=actor,
                new_review_status="retired",
                metadata={"target_country": target_country, "migrated_memory_id": migrated_id, "note": note.strip()},
            )
            self._record_memory_audit_conn(
                conn,
                country=target_country,
                memory_id=migrated_id,
                action="country_migrate_in",
                actor=actor,
                new_review_status="draft",
                metadata={"source_country": str(source["country"]), "source_memory_id": memory_id, "note": note.strip()},
            )
        return migrated_id

    def promote_layered_memory(
        self,
        memory_id: int,
        *,
        target_layer: str,
        target_type: str,
        human_note: str,
        actor: str = "",
    ) -> int:
        with self._connect() as conn:
            source = conn.execute("SELECT * FROM layered_memory WHERE memory_id = ?", (memory_id,)).fetchone()
            if source is None:
                raise ValueError(f"memory_id 不存在：{memory_id}")
            if source["status"] == "promoted":
                target = conn.execute(
                    "SELECT memory_id FROM layered_memory WHERE source_memory_id = ? AND status = 'active' ORDER BY memory_id DESC LIMIT 1",
                    (memory_id,),
                ).fetchone()
                if target:
                    return int(target["memory_id"])
            if source["status"] != "active":
                raise ValueError(f"只有 active memory 可以晋升，当前状态：{source['status']}")
            payload = json.loads(str(source["payload"]))
            if human_note.strip():
                payload["human_note"] = human_note.strip()
        target_id = self.add_layered_memory(
            str(source["country"]),
            target_layer,
            target_type,
            payload,
            source_memory_id=memory_id,
            human_verified=True,
            created_by=actor,
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE layered_memory SET status = 'promoted', updated_by = ?, updated_at = CURRENT_TIMESTAMP WHERE memory_id = ?",
                (actor, memory_id),
            )
        return target_id

    def review_layered_memory(
        self,
        memory_id: int,
        *,
        review_status: str,
        approved_for_rag: bool = False,
        actor: str = "",
    ) -> None:
        if review_status not in {"draft", "approved", "rejected", "conflict_locked", "retired"}:
            raise ValueError(f"未知 memory review_status：{review_status}")
        approved_by = actor if review_status == "approved" else ""
        approved_at_sql = "CURRENT_TIMESTAMP" if review_status == "approved" else "NULL"
        with self._connect() as conn:
            before = conn.execute(
                "SELECT country, review_status, approved_for_rag FROM layered_memory WHERE memory_id = ? AND status = 'active'",
                (memory_id,),
            ).fetchone()
            cursor = conn.execute(
                f"""
                UPDATE layered_memory
                SET review_status = ?, approved_for_rag = ?, updated_by = ?,
                    approved_by = ?, approved_at = {approved_at_sql}, updated_at = CURRENT_TIMESTAMP
                WHERE memory_id = ? AND status = 'active'
                """,
                (review_status, int(approved_for_rag and review_status == "approved"), actor, approved_by, memory_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"没有可审核的 active memory：{memory_id}")
            self._record_memory_audit_conn(
                conn,
                country=str(before["country"]) if before else "",
                memory_id=memory_id,
                action="review",
                actor=actor,
                old_review_status=str(before["review_status"]) if before else "",
                new_review_status=review_status,
                approved_for_rag=approved_for_rag and review_status == "approved",
            )

    def retire_layered_memory(self, memory_id: int, *, actor: str = "") -> None:
        with self._connect() as conn:
            before = conn.execute(
                "SELECT country, review_status FROM layered_memory WHERE memory_id = ? AND status = 'active'",
                (memory_id,),
            ).fetchone()
            cursor = conn.execute(
                """
                UPDATE layered_memory
                SET status = 'retired', review_status = 'retired', approved_for_rag = 0,
                    retired_by = ?, retired_at = CURRENT_TIMESTAMP, updated_by = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE memory_id = ? AND status = 'active'
                """,
                (actor, actor, memory_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"没有可停用的 active memory：{memory_id}")
            self._record_memory_audit_conn(
                conn,
                country=str(before["country"]) if before else "",
                memory_id=memory_id,
                action="retire",
                actor=actor,
                old_review_status=str(before["review_status"]) if before else "",
                new_review_status="retired",
                approved_for_rag=False,
            )

    def record_memory_rag_hits(self, country: str, hits: tuple[dict[str, object], ...] | list[dict[str, object]]) -> None:
        if not hits:
            return
        with self._connect() as conn:
            for hit in hits:
                try:
                    memory_id = int(hit.get("memory_id", 0))  # type: ignore[union-attr]
                except (TypeError, ValueError):
                    continue
                if memory_id <= 0:
                    continue
                cursor = conn.execute(
                    """
                    UPDATE layered_memory
                    SET rag_hit_count = COALESCE(rag_hit_count, 0) + 1,
                        last_rag_hit_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE memory_id = ? AND country = ?
                    """,
                    (memory_id, country),
                )
                if cursor.rowcount:
                    self._record_memory_audit_conn(
                        conn,
                        country=country,
                        memory_id=memory_id,
                        action="rag_hit",
                        actor="system",
                        metadata={
                            "chunk_id": str(hit.get("chunk_id", "")),
                            "trace_id": str(hit.get("trace_id", "")),
                        },
                    )

    def memory_audit_events(self, country: str, *, action: str = "", limit: int = 100) -> tuple[dict[str, object], ...]:
        where = "WHERE country = ?"
        params: list[object] = [country]
        if action:
            where += " AND action = ?"
            params.append(action)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT event_id, country, memory_id, action, actor, old_review_status,
                       new_review_status, approved_for_rag, metadata, created_at
                FROM memory_audit_events {where}
                ORDER BY event_id LIMIT ?
                """,
                (*params, max(limit, 0)),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(str(item.get("metadata", "{}") or "{}"))
            except json.JSONDecodeError:
                item["metadata"] = {}
            item["approved_for_rag"] = bool(item.get("approved_for_rag"))
            items.append(item)
        return tuple(items)

    def upsert_user(
        self,
        user_id: str,
        *,
        display_name: str = "",
        role: str = "viewer",
        countries: tuple[str, ...] = ("*",),
        status: str = "active",
    ) -> dict[str, object]:
        normalized_role = role if role in {"viewer", "operator", "admin"} else "viewer"
        encoded_countries = json.dumps(tuple(countries) or ("*",), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users(user_id, display_name, role, countries, status, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name=excluded.display_name, role=excluded.role, countries=excluded.countries,
                    status=excluded.status, updated_at=CURRENT_TIMESTAMP
                """,
                (user_id, display_name, normalized_role, encoded_countries, status),
            )
        return self.user(user_id) or {}

    def user(self, user_id: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, display_name, role, countries, status, created_at, updated_at FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return _decode_user_row(row) if row else None

    def users(self) -> tuple[dict[str, object], ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id, display_name, role, countries, status, created_at, updated_at FROM users ORDER BY user_id"
            ).fetchall()
        return tuple(_decode_user_row(row) for row in rows)

    def create_api_token(self, user_id: str, token: str, *, created_by: str = "", expires_at: str = "") -> dict[str, object]:
        token_id = f"tok_{uuid.uuid4().hex[:16]}"
        token_hash = _text_hash(token)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO api_tokens(token_id, user_id, token_hash, created_by, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token_id, user_id, token_hash, created_by, expires_at),
            )
        return {"token_id": token_id, "user_id": user_id, "token_plaintext_preview": _token_preview(token)}

    def api_user_by_token(self, token: str) -> dict[str, object] | None:
        token_hash = _text_hash(token)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT u.user_id, u.display_name, u.role, u.countries, u.status
                FROM api_tokens t JOIN users u ON u.user_id = t.user_id
                WHERE t.token_hash = ? AND t.revoked_at IS NULL
                  AND (t.expires_at = '' OR t.expires_at IS NULL OR t.expires_at > CURRENT_TIMESTAMP)
                """,
                (token_hash,),
            ).fetchone()
        if not row:
            return None
        user = _decode_user_row(row)
        return user if user.get("status") == "active" else None

    def record_audit_log(
        self,
        *,
        actor: str,
        action: str,
        country: str = "",
        resource_type: str = "",
        resource_id: str = "",
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        audit_id = f"audit_{uuid.uuid4().hex[:16]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs(audit_id, actor, action, country, resource_type, resource_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (audit_id, actor, action, country, resource_type, resource_id, json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)),
            )
        return self.audit_log(audit_id) or {}

    def audit_log(self, audit_id: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM audit_logs WHERE audit_id = ?", (audit_id,)).fetchone()
        return _decode_json_columns(dict(row), ("metadata",)) if row else None

    def audit_logs(self, *, country: str = "", actor: str = "", limit: int = 100) -> tuple[dict[str, object], ...]:
        where = []
        params: list[object] = []
        if country:
            where.append("country = ?")
            params.append(country)
        if actor:
            where.append("actor = ?")
            params.append(actor)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM audit_logs {clause} ORDER BY created_at DESC, audit_id DESC LIMIT ?", (*params, max(limit, 0))).fetchall()
        return tuple(_decode_json_columns(dict(row), ("metadata",)) for row in rows)

    def create_asset(
        self,
        *,
        object_key: str,
        public_url: str,
        sha256: str,
        content_type: str,
        size_bytes: int,
        source_filename: str,
        created_by: str = "",
    ) -> dict[str, object]:
        asset_id = f"asset_{uuid.uuid4().hex[:16]}"
        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM assets WHERE sha256 = ? ORDER BY created_at DESC LIMIT 1", (sha256,)).fetchone()
            if existing:
                return dict(existing)
            conn.execute(
                """
                INSERT INTO assets(asset_id, object_key, public_url, sha256, content_type, size_bytes, source_filename, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (asset_id, object_key, public_url, sha256, content_type, size_bytes, source_filename, created_by),
            )
        return self.asset(asset_id) or {}

    def asset(self, asset_id: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
        return dict(row) if row else None

    def update_asset_feishu_token(self, asset_id: str, file_token: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE assets SET feishu_file_token = ? WHERE asset_id = ?", (file_token, asset_id))

    def create_job(self, job_type: str, *, country: str = "", actor: str = "", payload: dict[str, object] | None = None) -> dict[str, object]:
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs(job_id, job_type, country, actor, status, progress, payload, result)
                VALUES (?, ?, ?, ?, 'queued', 0, ?, '{}')
                """,
                (job_id, job_type, country, actor, json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)),
            )
        return self.job(job_id) or {}

    def update_job(
        self,
        job_id: str,
        *,
        status: str,
        result: dict[str, object] | None = None,
        progress: int | None = None,
        error_code: str = "",
        error_message: str = "",
        raw_provider_response: dict[str, object] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs SET status = ?, progress = COALESCE(?, progress), result = COALESCE(?, result),
                    error_code = ?, error_message = ?, raw_provider_response = ?, updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ?
                """,
                (
                    status,
                    progress,
                    json.dumps(result, ensure_ascii=False, sort_keys=True) if result is not None else None,
                    error_code,
                    error_message,
                    json.dumps(raw_provider_response or {}, ensure_ascii=False, sort_keys=True),
                    job_id,
                ),
            )

    def job(self, job_id: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _decode_json_columns(dict(row), ("payload", "result", "raw_provider_response")) if row else None

    def jobs(self, *, country: str = "", status: str = "", limit: int = 100) -> tuple[dict[str, object], ...]:
        where = []
        params: list[object] = []
        if country:
            where.append("country = ?")
            params.append(country)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM jobs {clause} ORDER BY created_at DESC, job_id DESC LIMIT ?", (*params, max(limit, 0))).fetchall()
        return tuple(_decode_json_columns(dict(row), ("payload", "result", "raw_provider_response")) for row in rows)

    def record_trace_event(
        self,
        *,
        trace_id: str,
        request_id: str = "",
        actor: str = "",
        country: str = "",
        task_type: str,
        provider: str = "",
        model: str = "",
        input_summary: str = "",
        rag_citations: tuple[str, ...] | list[str] = (),
        visual_similarity_evidence: dict[str, object] | None = None,
        output_summary: str = "",
        status: str = "succeeded",
        error_message: str = "",
        latency_ms: float = 0.0,
    ) -> dict[str, object]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trace_events(
                    trace_id, request_id, actor, country, task_type, provider, model,
                    input_summary, rag_citations, visual_similarity_evidence, output_summary,
                    status, error_message, latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trace_id) DO UPDATE SET
                    status=excluded.status, error_message=excluded.error_message,
                    output_summary=excluded.output_summary, latency_ms=excluded.latency_ms
                """,
                (
                    trace_id,
                    request_id,
                    actor,
                    country,
                    task_type,
                    provider,
                    model,
                    input_summary,
                    json.dumps(tuple(rag_citations), ensure_ascii=False),
                    json.dumps(visual_similarity_evidence or {}, ensure_ascii=False, sort_keys=True),
                    output_summary,
                    status,
                    error_message,
                    float(latency_ms),
                ),
            )
        return self.trace_event(trace_id) or {}

    def trace_event(self, trace_id: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM trace_events WHERE trace_id = ?", (trace_id,)).fetchone()
        return _decode_trace_row(row) if row else None

    def trace_events(self, *, country: str = "", task_type: str = "", limit: int = 100) -> tuple[dict[str, object], ...]:
        where = []
        params: list[object] = []
        if country:
            where.append("country = ?")
            params.append(country)
        if task_type:
            where.append("task_type = ?")
            params.append(task_type)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM trace_events {clause} ORDER BY created_at DESC, trace_id DESC LIMIT ?", (*params, max(limit, 0))).fetchall()
        return tuple(_decode_trace_row(row) for row in rows)

    def latency_metrics(self, *, country: str = "", task_type: str = "") -> dict[str, object]:
        traces = self.trace_events(country=country, task_type=task_type, limit=1000)
        values = sorted(float(trace.get("latency_ms") or 0) for trace in traces if float(trace.get("latency_ms") or 0) >= 0)
        return {
            "count": len(values),
            "p50_ms": _percentile(values, 0.50),
            "p95_ms": _percentile(values, 0.95),
            "p99_ms": _percentile(values, 0.99),
            "average_ms": round(statistics.mean(values), 4) if values else 0.0,
        }

    def observability_summary(self, *, country: str = "", task_type: str = "") -> dict[str, object]:
        traces = self.trace_events(country=country, task_type=task_type, limit=1000)
        jobs = self.jobs(country=country, limit=1000)
        if task_type.startswith("job."):
            job_type = task_type.split(".", 1)[1]
            jobs = tuple(job for job in jobs if str(job.get("job_type", "")) == job_type)
        status_counts: dict[str, int] = {}
        provider_counts: dict[str, int] = {}
        for trace in traces:
            status = str(trace.get("status", "") or "unknown")
            provider = str(trace.get("provider", "") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
        job_status_counts: dict[str, int] = {}
        failure_reasons: dict[str, int] = {}
        for job in jobs:
            status = str(job.get("status", "") or "unknown")
            job_status_counts[status] = job_status_counts.get(status, 0) + 1
            if status == "failed":
                reason = str(job.get("error_code", "") or job.get("error_message", "") or "unknown")
                failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
        succeeded_jobs = job_status_counts.get("succeeded", 0)
        failed_jobs = job_status_counts.get("failed", 0)
        rag_traces = tuple(trace for trace in traces if str(trace.get("task_type", "")) == "rag_search")
        missing_citations = sum(1 for trace in rag_traces if not trace.get("rag_citations"))
        return {
            "country": country or "ALL",
            "task_type": task_type or "ALL",
            "latency": self.latency_metrics(country=country, task_type=task_type),
            "traces": {
                "total": len(traces),
                "status_counts": dict(sorted(status_counts.items())),
                "provider_counts": dict(sorted(provider_counts.items())),
            },
            "jobs": {
                "total": len(jobs),
                "status_counts": dict(sorted(job_status_counts.items())),
                "success_rate": round(succeeded_jobs / (succeeded_jobs + failed_jobs), 4) if succeeded_jobs + failed_jobs else 0.0,
                "failure_reasons": dict(sorted(failure_reasons.items())),
            },
            "rag": {
                "trace_count": len(rag_traces),
                "citation_missing_rate": round(missing_citations / len(rag_traces), 4) if rag_traces else 0.0,
            },
        }

    def export_rag_release_report(self, output_dir: Path | str) -> dict[str, object]:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        traces = self.trace_events(task_type="rag_search", limit=1000)
        by_country: dict[str, list[dict[str, object]]] = {}
        for trace in traces:
            by_country.setdefault(str(trace.get("country", "") or "GLOBAL"), []).append(trace)
        usable = sum(1 for trace in traces if trace.get("rag_citations"))
        metrics = {
            "trace_count": len(traces),
            "citation_usable_rate": round(usable / len(traces), 4) if traces else 0.0,
            "mrr@5": _extract_metric_from_traces(traces, "mrr@5"),
            "ndcg@5": _extract_metric_from_traces(traces, "ndcg@5"),
            "precision@5": _extract_metric_from_traces(traces, "precision@5"),
            "recall@5": _extract_metric_from_traces(traces, "recall@5"),
            "hard_negative_rate": _extract_metric_from_traces(traces, "hard_negative_rate"),
        }
        payload = {
            "mode": "rag_release_report",
            "metrics": metrics,
            "countries": {
                country: {
                    "trace_count": len(items),
                    "citation_usable_rate": round(sum(1 for item in items if item.get("rag_citations")) / len(items), 4) if items else 0.0,
                }
                for country, items in sorted(by_country.items())
            },
            "known_limits": "真实样本规模仍偏小，报告用于上线验收与面试说明，不能声称大规模生产稳定性。",
        }
        json_path = output_path / "rag_release_report.json"
        markdown_path = output_path / "rag_release_report.md"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(_rag_release_report_markdown(payload), encoding="utf-8")
        return {"json_path": str(json_path), "markdown_path": str(markdown_path), "payload": payload}

    @staticmethod
    def _record_memory_audit_conn(
        conn: sqlite3.Connection,
        *,
        country: str,
        memory_id: int,
        action: str,
        actor: str = "",
        old_review_status: str = "",
        new_review_status: str = "",
        approved_for_rag: bool = False,
        metadata: dict[str, object] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO memory_audit_events(
                country, memory_id, action, actor, old_review_status,
                new_review_status, approved_for_rag, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                country,
                memory_id,
                action,
                actor,
                old_review_status,
                new_review_status,
                int(approved_for_rag),
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            ),
        )

    def expire_layered_memories(self) -> int:
        with self._connect() as conn:
            return self._expire_layered_memories_conn(conn)

    def _expire_layered_memories_conn(self, conn: sqlite3.Connection) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cursor = conn.execute(
            """
            UPDATE layered_memory
            SET status = 'expired', approved_for_rag = 0, updated_at = CURRENT_TIMESTAMP
            WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (now,),
        )
        return int(cursor.rowcount)

    def add_value_rule(self, country: str, rule_text: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO value_rules(country, rule_text, status) VALUES (?, ?, ?)",
                (country, rule_text, status),
            )

    def approved_value_rules(self, country: str) -> tuple[dict[str, str], ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT country, rule_text, status FROM value_rules WHERE country = ? AND status = 'approved'",
                (country,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def save_rag_index(self, country: str, documents: tuple[RagDocument, ...], chunks: tuple[RagChunk, ...]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM rag_chunks WHERE country IN (?, 'GLOBAL')", (country,))
            conn.execute("DELETE FROM rag_documents WHERE country IN (?, 'GLOBAL')", (country,))
            conn.executemany(
                """
                INSERT INTO rag_documents(document_id, country, source_type, title, text, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        document.document_id,
                        document.country,
                        document.source_type,
                        document.title,
                        document.text,
                        json.dumps(document.metadata, ensure_ascii=False),
                    )
                    for document in documents
                ],
            )
            conn.executemany(
                """
                INSERT INTO rag_chunks(chunk_id, parent_id, country, source_type, title, text, chunk_index, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk.chunk_id,
                        chunk.parent_id,
                        chunk.country,
                        chunk.source_type,
                        chunk.title,
                        chunk.text,
                        chunk.chunk_index,
                        json.dumps(chunk.metadata, ensure_ascii=False),
                    )
                    for chunk in chunks
                ],
            )

    def rag_documents(self, country: str) -> tuple[dict[str, object], ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT document_id, country, source_type, title, text, metadata FROM rag_documents WHERE country IN (?, 'GLOBAL') ORDER BY document_id",
                (country,),
            ).fetchall()
        return tuple(_decode_metadata(dict(row)) for row in rows)

    def rag_chunks(self, country: str) -> tuple[dict[str, object], ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT chunk_id, parent_id, country, source_type, title, text, chunk_index, metadata
                FROM rag_chunks
                WHERE country IN (?, 'GLOBAL')
                ORDER BY parent_id, chunk_index
                """,
                (country,),
            ).fetchall()
        return tuple(_decode_metadata(dict(row)) for row in rows)

    def get_rag_embedding_cache(self, provider: str, model: str, text: str) -> tuple[float, ...] | None:
        text_hash = _text_hash(text)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT vector FROM rag_embedding_cache
                WHERE provider = ? AND model = ? AND text_hash = ?
                """,
                (provider, model, text_hash),
            ).fetchone()
        if not row:
            return None
        try:
            return tuple(float(value) for value in json.loads(str(row["vector"])))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def set_rag_embedding_cache(self, provider: str, model: str, text: str, vector: tuple[float, ...]) -> None:
        text_hash = _text_hash(text)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO rag_embedding_cache(provider, model, text_hash, text, vector)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider, model, text_hash) DO UPDATE SET
                    text=excluded.text,
                    vector=excluded.vector,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (provider, model, text_hash, text, json.dumps(vector)),
            )

    def add_sync_event(self, country: str, action: str, target: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sync_events(country, action, target, status) VALUES (?, ?, ?, ?)",
                (country, action, target, status),
            )

    def sync_events(self) -> tuple[tuple[str, str, str, str, str], ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT created_at, country, action, target, status FROM sync_events ORDER BY event_id DESC"
            ).fetchall()
        return tuple(tuple(str(row[index]) for index in range(5)) for row in rows)

    def save_guarded_action_proposal(self, proposal) -> None:
        payload = json.dumps(proposal.payload, ensure_ascii=False, sort_keys=True)
        payload_preview = json.dumps(proposal.payload_preview, ensure_ascii=False, sort_keys=True)
        guard_reasons = json.dumps(tuple(proposal.guard_reasons), ensure_ascii=False)
        execution_result = json.dumps(proposal.execution_result, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO guarded_action_proposals(
                    proposal_id, country, actor, target_system, action_type, payload,
                    payload_preview, source_trace_id, risk_level, guard_status, guard_reasons,
                    rollback_strategy, created_at, approved_by, approved_at, executed_at, execution_result
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    country=excluded.country,
                    actor=excluded.actor,
                    target_system=excluded.target_system,
                    action_type=excluded.action_type,
                    payload=excluded.payload,
                    payload_preview=excluded.payload_preview,
                    source_trace_id=excluded.source_trace_id,
                    risk_level=excluded.risk_level,
                    guard_status=excluded.guard_status,
                    guard_reasons=excluded.guard_reasons,
                    rollback_strategy=excluded.rollback_strategy,
                    approved_by=excluded.approved_by,
                    approved_at=excluded.approved_at,
                    executed_at=excluded.executed_at,
                    execution_result=excluded.execution_result
                """,
                (
                    proposal.proposal_id,
                    proposal.country,
                    proposal.actor,
                    proposal.target_system,
                    proposal.action_type,
                    payload,
                    payload_preview,
                    proposal.source_trace_id,
                    proposal.risk_level,
                    proposal.guard_status,
                    guard_reasons,
                    proposal.rollback_strategy,
                    proposal.created_at,
                    proposal.approved_by,
                    proposal.approved_at,
                    proposal.executed_at,
                    execution_result,
                ),
            )

    def guarded_action_proposal(self, proposal_id: str):
        from puzzle_ops.guarded_tools import ActionProposal

        with self._connect() as conn:
            row = conn.execute("SELECT * FROM guarded_action_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
        if row is None:
            return None
        return _guarded_action_from_row(dict(row), ActionProposal)

    def guarded_action_proposals(self, country: str = "", *, limit: int = 50):
        from puzzle_ops.guarded_tools import ActionProposal

        where = "WHERE country = ?" if country else ""
        params: tuple[object, ...] = (country, limit) if country else (limit,)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM guarded_action_proposals {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return tuple(_guarded_action_from_row(dict(row), ActionProposal) for row in rows)

    def record_guarded_action_event(
        self,
        proposal_id: str,
        country: str,
        actor: str,
        event_type: str,
        old_status: str,
        new_status: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO guarded_action_events(proposal_id, country, actor, event_type, old_status, new_status, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (proposal_id, country, actor, event_type, old_status, new_status, json.dumps(metadata or {}, ensure_ascii=False)),
            )

    def guarded_action_events(self, proposal_id: str = "", *, country: str = "", limit: int = 100) -> tuple[dict[str, object], ...]:
        clauses = []
        params: list[object] = []
        if proposal_id:
            clauses.append("proposal_id = ?")
            params.append(proposal_id)
        if country:
            clauses.append("country = ?")
            params.append(country)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT event_id, proposal_id, country, actor, event_type, old_status, new_status, metadata, created_at
                FROM guarded_action_events {where}
                ORDER BY event_id LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(str(item["metadata"]))
            except json.JSONDecodeError:
                item["metadata"] = {}
            events.append(item)
        return tuple(events)

    def record_tool_invocation(
        self,
        *,
        invocation_id: str,
        tool_name: str,
        country: str,
        actor: str,
        skill_id: str,
        source_trace_id: str,
        proposal_id: str,
        side_effect: str,
        input_hash: str,
        input_preview: dict[str, object],
        output_preview: dict[str, object],
        success: bool,
        error_code: str,
        error_message: str,
        latency_ms: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_invocations(
                    invocation_id, tool_name, country, actor, skill_id, source_trace_id,
                    proposal_id, side_effect, input_hash, input_preview, output_preview,
                    success, error_code, error_message, latency_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invocation_id,
                    tool_name,
                    country,
                    actor,
                    skill_id,
                    source_trace_id,
                    proposal_id,
                    side_effect,
                    input_hash,
                    json.dumps(input_preview, ensure_ascii=False, sort_keys=True, default=str),
                    json.dumps(output_preview, ensure_ascii=False, sort_keys=True, default=str),
                    1 if success else 0,
                    error_code,
                    error_message,
                    latency_ms,
                ),
            )

    def tool_invocations(self, *, country: str = "", limit: int = 50) -> tuple[dict[str, object], ...]:
        where = "WHERE country = ?" if country else ""
        params: tuple[object, ...] = (country, limit) if country else (limit,)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT invocation_id, tool_name, country, actor, skill_id, source_trace_id,
                       proposal_id, side_effect, input_hash, input_preview, output_preview,
                       success, error_code, error_message, latency_ms, created_at
                FROM tool_invocations {where}
                ORDER BY created_at DESC, rowid DESC LIMIT ?
                """,
                params,
            ).fetchall()
        invocations = []
        for row in rows:
            item = dict(row)
            item["success"] = bool(item["success"])
            for field in ("input_preview", "output_preview"):
                try:
                    item[field] = json.loads(str(item[field]))
                except json.JSONDecodeError:
                    item[field] = {}
            invocations.append(item)
        return tuple(invocations)

    def save_harness_run(self, run) -> None:
        payload = json.dumps(asdict(run), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO harness_runs(run_id, version, dataset_name, model_provider, generator_provider, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    version=excluded.version,
                    dataset_name=excluded.dataset_name,
                    model_provider=excluded.model_provider,
                    generator_provider=excluded.generator_provider,
                    payload=excluded.payload,
                    created_at=excluded.created_at
                """,
                (
                    run.run_id,
                    run.version,
                    run.dataset_name,
                    run.model_provider,
                    run.generator_provider,
                    payload,
                    run.created_at,
                ),
            )

    def harness_runs(self, limit: int = 10):
        from puzzle_ops.harness import HarnessCaseResult, HarnessRun

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM harness_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        runs = []
        for row in rows:
            payload = json.loads(row["payload"])
            cases = tuple(HarnessCaseResult(**case) for case in payload["cases"])
            failures = tuple(HarnessCaseResult(**case) for case in payload["failures"])
            rag_trace_artifacts = tuple(_normalize_harness_rag_artifact(item) for item in payload.get("rag_trace_artifacts", ()))
            runs.append(
                HarnessRun(
                    run_id=payload["run_id"],
                    version=payload["version"],
                    dataset_name=payload["dataset_name"],
                    model_provider=payload["model_provider"],
                    generator_provider=payload["generator_provider"],
                    cases=cases,
                    metrics=payload["metrics"],
                    failures=failures,
                    created_at=payload["created_at"],
                    country=payload.get("country", ""),
                    execution_mode=payload.get("execution_mode", "offline"),
                    metric_evaluable_counts=payload.get("metric_evaluable_counts", {}),
                    rag_trace_artifacts=rag_trace_artifacts,
                )
            )
        return tuple(runs)

    def add_description_benchmark_score(self, payload: dict[str, object]) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO description_benchmark_scores(
                    country, actor, image_name, operation_tag, template_scores, prompt_scores,
                    template_label, prompt_label, template_output, prompt_output, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload.get("country", "")),
                    str(payload.get("actor", "")),
                    str(payload.get("image_name", "")),
                    str(payload.get("operation_tag", "")),
                    json.dumps(payload.get("template_scores", {}), ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.get("prompt_scores", {}), ensure_ascii=False, sort_keys=True),
                    str(payload.get("template_label", "")),
                    str(payload.get("prompt_label", "")),
                    str(payload.get("template_output", "")),
                    str(payload.get("prompt_output", "")),
                    json.dumps(payload.get("metadata", {}), ensure_ascii=False, sort_keys=True),
                ),
            )
            return int(cursor.lastrowid)

    def description_benchmark_scores(self, country: str, *, limit: int = 100) -> tuple[dict[str, object], ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT record_id, country, actor, image_name, operation_tag, template_scores, prompt_scores,
                       template_label, prompt_label, template_output, prompt_output, metadata, created_at
                FROM description_benchmark_scores
                WHERE country = ?
                ORDER BY record_id DESC
                LIMIT ?
                """,
                (country, limit),
            ).fetchall()
        return tuple(_decode_description_benchmark_row(row) for row in rows)

    def description_benchmark_summary(self, country: str) -> dict[str, object]:
        rows = self.description_benchmark_scores(country, limit=10_000)
        template_scores = [_score_average(row.get("template_scores", {})) for row in rows]
        prompt_scores = [_score_average(row.get("prompt_scores", {})) for row in rows]
        light_labels = {"可直接用", "轻微修改"}
        return {
            "count": len(rows),
            "template_average": round(sum(template_scores) / len(template_scores), 2) if template_scores else 0.0,
            "prompt_average": round(sum(prompt_scores) / len(prompt_scores), 2) if prompt_scores else 0.0,
            "template_light_or_direct_rate": round(
                sum(1 for row in rows if row.get("template_label") in light_labels) / len(rows), 4
            ) if rows else 0.0,
            "prompt_light_or_direct_rate": round(
                sum(1 for row in rows if row.get("prompt_label") in light_labels) / len(rows), 4
            ) if rows else 0.0,
        }

    def add_value_prediction_benchmark_score(self, payload: dict[str, object]) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO value_prediction_benchmark_scores(
                    country, actor, candidate_id, operation_tag, baseline_scores, candidate_scores,
                    baseline_label, candidate_label, baseline_output, candidate_output, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload.get("country", "")),
                    str(payload.get("actor", "")),
                    str(payload.get("candidate_id", "")),
                    str(payload.get("operation_tag", "")),
                    json.dumps(payload.get("baseline_scores", {}), ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.get("candidate_scores", {}), ensure_ascii=False, sort_keys=True),
                    str(payload.get("baseline_label", "")),
                    str(payload.get("candidate_label", "")),
                    str(payload.get("baseline_output", "")),
                    str(payload.get("candidate_output", "")),
                    json.dumps(payload.get("metadata", {}), ensure_ascii=False, sort_keys=True),
                ),
            )
            return int(cursor.lastrowid)

    def value_prediction_benchmark_scores(self, country: str, *, limit: int = 100) -> tuple[dict[str, object], ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT record_id, country, actor, candidate_id, operation_tag, baseline_scores, candidate_scores,
                       baseline_label, candidate_label, baseline_output, candidate_output, metadata, created_at
                FROM value_prediction_benchmark_scores
                WHERE country = ?
                ORDER BY record_id DESC
                LIMIT ?
                """,
                (country, limit),
            ).fetchall()
        return tuple(_decode_value_prediction_benchmark_row(row) for row in rows)

    def value_prediction_benchmark_summary(self, country: str) -> dict[str, object]:
        rows = self.value_prediction_benchmark_scores(country, limit=10_000)
        baseline_scores = [_score_average(row.get("baseline_scores", {})) for row in rows]
        candidate_scores = [_score_average(row.get("candidate_scores", {})) for row in rows]
        light_labels = {"可直接用", "轻微修改"}
        return {
            "count": len(rows),
            "baseline_average": round(sum(baseline_scores) / len(baseline_scores), 2) if baseline_scores else 0.0,
            "candidate_average": round(sum(candidate_scores) / len(candidate_scores), 2) if candidate_scores else 0.0,
            "candidate_light_or_direct_rate": round(
                sum(1 for row in rows if row.get("candidate_label") in light_labels) / len(rows), 4
            ) if rows else 0.0,
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            statements = (
                """
                CREATE TABLE IF NOT EXISTS historical_records (
                    grade TEXT NOT NULL, image_formula TEXT NOT NULL, image_id TEXT PRIMARY KEY,
                    image_url TEXT NOT NULL, local_image_path TEXT NOT NULL, thumbnail_path TEXT NOT NULL,
                    position INTEGER NOT NULL, dimension_grade TEXT NOT NULL, open_rate REAL NOT NULL,
                    completion_rate REAL NOT NULL, avg_finish_time REAL NOT NULL, operation_tag TEXT NOT NULL,
                    subject_tag TEXT NOT NULL, js_category TEXT NOT NULL, source TEXT NOT NULL, remark TEXT NOT NULL,
                    distribution_date TEXT NOT NULL, distribution_cycle TEXT NOT NULL, country TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS agent_memory (
                    memory_id INTEGER PRIMARY KEY AUTOINCREMENT, country TEXT NOT NULL,
                    memory_type TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS value_rules (
                    rule_id INTEGER PRIMARY KEY AUTOINCREMENT, country TEXT NOT NULL,
                    rule_text TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS layered_memory (
                    memory_id INTEGER PRIMARY KEY AUTOINCREMENT, country TEXT NOT NULL,
                    memory_layer TEXT NOT NULL, memory_type TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', source_memory_id INTEGER, expires_at TEXT,
                    fingerprint TEXT NOT NULL DEFAULT '', human_verified INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL DEFAULT '',
                    approved_by TEXT NOT NULL DEFAULT '', approved_at TEXT,
                    retired_by TEXT NOT NULL DEFAULT '', retired_at TEXT,
                    approved_for_rag INTEGER NOT NULL DEFAULT 0,
                    review_status TEXT NOT NULL DEFAULT 'draft',
                    memory_scope TEXT NOT NULL DEFAULT 'operational_fact',
                    rag_hit_count INTEGER NOT NULL DEFAULT 0,
                    last_rag_hit_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS memory_audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, country TEXT NOT NULL,
                    memory_id INTEGER NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '',
                    old_review_status TEXT NOT NULL DEFAULT '', new_review_status TEXT NOT NULL DEFAULT '',
                    approved_for_rag INTEGER NOT NULL DEFAULT 0, metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS sync_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, country TEXT NOT NULL, action TEXT NOT NULL,
                    target TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS guarded_action_proposals (
                    proposal_id TEXT PRIMARY KEY, country TEXT NOT NULL, actor TEXT NOT NULL,
                    target_system TEXT NOT NULL, action_type TEXT NOT NULL, payload TEXT NOT NULL,
                    payload_preview TEXT NOT NULL, source_trace_id TEXT NOT NULL, risk_level TEXT NOT NULL,
                    guard_status TEXT NOT NULL, guard_reasons TEXT NOT NULL, rollback_strategy TEXT NOT NULL,
                    created_at TEXT NOT NULL, approved_by TEXT NOT NULL DEFAULT '', approved_at TEXT,
                    executed_at TEXT, execution_result TEXT NOT NULL DEFAULT '{}'
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS guarded_action_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL,
                    country TEXT NOT NULL, actor TEXT NOT NULL, event_type TEXT NOT NULL,
                    old_status TEXT NOT NULL, new_status TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS tool_invocations (
                    invocation_id TEXT PRIMARY KEY, tool_name TEXT NOT NULL, country TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL DEFAULT '', skill_id TEXT NOT NULL DEFAULT '',
                    source_trace_id TEXT NOT NULL DEFAULT '', proposal_id TEXT NOT NULL DEFAULT '',
                    side_effect TEXT NOT NULL DEFAULT 'read', input_hash TEXT NOT NULL DEFAULT '',
                    input_preview TEXT NOT NULL DEFAULT '{}', output_preview TEXT NOT NULL DEFAULT '{}',
                    success INTEGER NOT NULL DEFAULT 0, error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '', latency_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS harness_runs (
                    run_id TEXT PRIMARY KEY, version TEXT NOT NULL, dataset_name TEXT NOT NULL,
                    model_provider TEXT NOT NULL, generator_provider TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS rag_documents (
                    document_id TEXT PRIMARY KEY, country TEXT NOT NULL, source_type TEXT NOT NULL,
                    title TEXT NOT NULL, text TEXT NOT NULL, metadata TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    chunk_id TEXT PRIMARY KEY, parent_id TEXT NOT NULL, country TEXT NOT NULL,
                    source_type TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL, metadata TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS rag_embedding_cache (
                    provider TEXT NOT NULL, model TEXT NOT NULL, text_hash TEXT NOT NULL, text TEXT NOT NULL,
                    vector TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(provider, model, text_hash)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS description_benchmark_scores (
                    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    country TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT '',
                    image_name TEXT NOT NULL DEFAULT '',
                    operation_tag TEXT NOT NULL DEFAULT '',
                    template_scores TEXT NOT NULL DEFAULT '{}',
                    prompt_scores TEXT NOT NULL DEFAULT '{}',
                    template_label TEXT NOT NULL DEFAULT '',
                    prompt_label TEXT NOT NULL DEFAULT '',
                    template_output TEXT NOT NULL DEFAULT '',
                    prompt_output TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS value_prediction_benchmark_scores (
                    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    country TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT '',
                    candidate_id TEXT NOT NULL DEFAULT '',
                    operation_tag TEXT NOT NULL DEFAULT '',
                    baseline_scores TEXT NOT NULL DEFAULT '{}',
                    candidate_scores TEXT NOT NULL DEFAULT '{}',
                    baseline_label TEXT NOT NULL DEFAULT '',
                    candidate_label TEXT NOT NULL DEFAULT '',
                    baseline_output TEXT NOT NULL DEFAULT '',
                    candidate_output TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL, countries TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS api_tokens (
                    token_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                    created_by TEXT NOT NULL DEFAULT '', expires_at TEXT NOT NULL DEFAULT '',
                    revoked_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    audit_id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
                    country TEXT NOT NULL DEFAULT '', resource_type TEXT NOT NULL DEFAULT '',
                    resource_id TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS demand_rows (
                    demand_id TEXT PRIMARY KEY, country TEXT NOT NULL, need_type TEXT NOT NULL,
                    operation_tag TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS trial_uploads (
                    upload_id TEXT PRIMARY KEY, country TEXT NOT NULL, asset_id TEXT NOT NULL DEFAULT '',
                    parse_status TEXT NOT NULL DEFAULT 'queued', derivative_status TEXT NOT NULL DEFAULT 'not_requested',
                    payload TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY, object_key TEXT NOT NULL, public_url TEXT NOT NULL DEFAULT '',
                    sha256 TEXT NOT NULL, content_type TEXT NOT NULL DEFAULT '', size_bytes INTEGER NOT NULL DEFAULT 0,
                    source_filename TEXT NOT NULL DEFAULT '', feishu_file_token TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, country TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'queued', progress INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL DEFAULT '{}', result TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
                    raw_provider_response TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS trace_events (
                    trace_id TEXT PRIMARY KEY, request_id TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL DEFAULT '',
                    country TEXT NOT NULL DEFAULT '', task_type TEXT NOT NULL, provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '', input_summary TEXT NOT NULL DEFAULT '',
                    rag_citations TEXT NOT NULL DEFAULT '[]', visual_similarity_evidence TEXT NOT NULL DEFAULT '{}',
                    output_summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'succeeded',
                    error_message TEXT NOT NULL DEFAULT '', latency_ms REAL NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
            )
            for statement in statements:
                conn.execute(statement)
            self._ensure_column(conn, "layered_memory", "status", "TEXT NOT NULL DEFAULT 'active'")
            self._ensure_column(conn, "layered_memory", "source_memory_id", "INTEGER")
            self._ensure_column(conn, "layered_memory", "expires_at", "TEXT")
            self._ensure_column(conn, "layered_memory", "fingerprint", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "layered_memory", "human_verified", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "layered_memory", "created_by", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "layered_memory", "updated_by", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "layered_memory", "approved_by", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "layered_memory", "approved_at", "TEXT")
            self._ensure_column(conn, "layered_memory", "retired_by", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "layered_memory", "retired_at", "TEXT")
            self._ensure_column(conn, "layered_memory", "approved_for_rag", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "layered_memory", "review_status", "TEXT NOT NULL DEFAULT 'draft'")
            self._ensure_column(conn, "layered_memory", "memory_scope", "TEXT NOT NULL DEFAULT 'operational_fact'")
            self._ensure_column(conn, "layered_memory", "rag_hit_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "layered_memory", "last_rag_hit_at", "TEXT")
            self._ensure_column(conn, "layered_memory", "updated_at", "TEXT")
            self._backfill_layered_memory_fingerprints(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_layered_memory_active ON layered_memory(country, memory_layer, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_audit_country_action ON memory_audit_events(country, action, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_guarded_action_country_status ON guarded_action_proposals(country, guard_status, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_guarded_action_events_proposal ON guarded_action_events(proposal_id, event_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_invocations_country_created ON tool_invocations(country, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_invocations_tool_created ON tool_invocations(tool_name, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_country_created ON audit_logs(country, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_sha256 ON assets(sha256)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_country_status ON jobs(country, status, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_events_country_task ON trace_events(country, task_type, created_at)")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _backfill_layered_memory_fingerprints(conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT memory_id, payload FROM layered_memory WHERE fingerprint = ''").fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["payload"]))
                encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            except json.JSONDecodeError:
                encoded = str(row["payload"])
            conn.execute(
                "UPDATE layered_memory SET fingerprint = ?, updated_at = COALESCE(updated_at, created_at) WHERE memory_id = ?",
                (_text_hash(encoded), int(row["memory_id"])),
            )


def _decode_metadata(item: dict[str, object]) -> dict[str, object]:
    try:
        item["metadata"] = json.loads(str(item["metadata"]))
    except json.JSONDecodeError:
        item["metadata"] = {}
    return item


def _decode_json_columns(item: dict[str, object], keys: tuple[str, ...]) -> dict[str, object]:
    for key in keys:
        try:
            value = json.loads(str(item.get(key, "{}") or "{}"))
        except json.JSONDecodeError:
            value = {} if key != "rag_citations" else []
        if key == "rag_citations":
            item[key] = tuple(str(part) for part in value) if isinstance(value, list) else ()
        else:
            item[key] = value if isinstance(value, dict) else {}
    return item


def _decode_user_row(row) -> dict[str, object]:
    item = dict(row)
    try:
        countries = json.loads(str(item.get("countries", "[]") or "[]"))
    except json.JSONDecodeError:
        countries = []
    item["countries"] = tuple(str(country) for country in countries) if isinstance(countries, list) else ("*",)
    return item


def _decode_trace_row(row) -> dict[str, object]:
    item = dict(row)
    return _decode_json_columns(item, ("rag_citations", "visual_similarity_evidence"))


def _token_preview(token: str) -> str:
    if len(token) <= 4:
        return "***"
    return f"{token[:3]}..."


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * percentile))))
    return round(values[index], 4)


def _extract_metric_from_traces(traces: tuple[dict[str, object], ...], metric: str) -> float:
    values: list[float] = []
    marker = f"{metric}="
    for trace in traces:
        text = str(trace.get("output_summary", ""))
        if marker not in text:
            continue
        tail = text.split(marker, 1)[1].split()[0].strip("；;,")
        try:
            values.append(float(tail))
        except ValueError:
            continue
    return round(sum(values) / len(values), 4) if values else 0.0


def _rag_release_report_markdown(payload: dict[str, object]) -> str:
    metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
    countries = payload.get("countries", {}) if isinstance(payload.get("countries"), dict) else {}
    lines = [
        "# PuzzleOps RAG Release Report",
        "",
        "## 核心指标",
        f"- MRR@5：{metrics.get('mrr@5', 0.0)}",
        f"- NDCG@5：{metrics.get('ndcg@5', 0.0)}",
        f"- Precision@5：{metrics.get('precision@5', 0.0)}",
        f"- Recall@5：{metrics.get('recall@5', 0.0)}",
        f"- Citation usable rate：{metrics.get('citation_usable_rate', 0.0)}",
        f"- Hard-negative rate：{metrics.get('hard_negative_rate', 0.0)}",
        "",
        "## 国家拆分",
    ]
    for country, item in countries.items():
        if isinstance(item, dict):
            lines.append(f"- {country}：trace_count={item.get('trace_count', 0)}，citation_usable_rate={item.get('citation_usable_rate', 0.0)}")
    lines.extend(["", "## 限制", str(payload.get("known_limits", ""))])
    return "\n".join(lines) + "\n"


def _guarded_action_from_row(row: dict[str, object], action_cls):
    def decode_dict(key: str) -> dict[str, object]:
        try:
            value = json.loads(str(row.get(key, "{}") or "{}"))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}

    def decode_tuple(key: str) -> tuple[str, ...]:
        try:
            value = json.loads(str(row.get(key, "[]") or "[]"))
        except json.JSONDecodeError:
            value = []
        if isinstance(value, list):
            return tuple(str(item) for item in value)
        if isinstance(value, tuple):
            return tuple(str(item) for item in value)
        return ()

    return action_cls(
        proposal_id=str(row["proposal_id"]),
        country=str(row["country"]),
        actor=str(row["actor"]),
        target_system=str(row["target_system"]),
        action_type=str(row["action_type"]),
        payload=decode_dict("payload"),
        payload_preview=decode_dict("payload_preview"),
        source_trace_id=str(row["source_trace_id"]),
        risk_level=str(row["risk_level"]),
        guard_status=str(row["guard_status"]),
        guard_reasons=decode_tuple("guard_reasons"),
        rollback_strategy=str(row["rollback_strategy"]),
        created_at=str(row["created_at"]),
        approved_by=str(row.get("approved_by") or ""),
        approved_at=str(row.get("approved_at") or ""),
        executed_at=str(row.get("executed_at") or ""),
        execution_result=decode_dict("execution_result"),
    )


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_harness_rag_artifact(item: object) -> dict[str, object]:
    if not isinstance(item, dict):
        return {}
    normalized = dict(item)
    citations = normalized.get("citations", ())
    if isinstance(citations, list):
        normalized["citations"] = tuple(str(citation) for citation in citations)
    return normalized


def _decode_description_benchmark_row(row: sqlite3.Row) -> dict[str, object]:
    item = dict(row)
    for key in ("template_scores", "prompt_scores", "metadata"):
        try:
            value = json.loads(str(item.get(key, "{}") or "{}"))
        except json.JSONDecodeError:
            value = {}
        item[key] = value if isinstance(value, dict) else {}
    score_keys = (
        "subject_accuracy",
        "production_actionability",
        "conciseness",
        "market_fit",
        "remark_usefulness",
    )
    for prefix in ("template", "prompt"):
        scores = item.get(f"{prefix}_scores", {})
        if not isinstance(scores, dict):
            continue
        for index, key in enumerate(score_keys):
            item[f"{prefix}_score_{index}"] = scores.get(key)
    return item


def _decode_value_prediction_benchmark_row(row: sqlite3.Row) -> dict[str, object]:
    item = dict(row)
    for key in ("baseline_scores", "candidate_scores", "metadata"):
        try:
            value = json.loads(str(item.get(key, "{}") or "{}"))
        except json.JSONDecodeError:
            value = {}
        item[key] = value if isinstance(value, dict) else {}
    return item


def _score_average(scores: object) -> float:
    if not isinstance(scores, dict):
        return 0.0
    values = []
    for value in scores.values():
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return sum(values) / len(values) if values else 0.0
