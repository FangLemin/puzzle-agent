from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from puzzle_ops.models import HistoricalRecord
from puzzle_ops.rag import RagChunk, RagDocument


class PuzzleRepository:
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
            rows = conn.execute("SELECT country, memory_type, content FROM agent_memory WHERE country = ?", (country,)).fetchall()
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
    ) -> int:
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
                    expires_at, fingerprint, human_verified, updated_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (country, memory_layer, memory_type, encoded, source_memory_id, expires_at, fingerprint, int(human_verified)),
            )
            return int(cursor.lastrowid)

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
                       source_memory_id, expires_at, fingerprint, human_verified, created_at, updated_at
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
            items.append(item)
        return tuple(items)

    def promote_layered_memory(
        self,
        memory_id: int,
        *,
        target_layer: str,
        target_type: str,
        human_note: str,
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
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE layered_memory SET status = 'promoted', updated_at = CURRENT_TIMESTAMP WHERE memory_id = ?",
                (memory_id,),
            )
        return target_id

    def retire_layered_memory(self, memory_id: int) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE layered_memory SET status = 'retired', updated_at = CURRENT_TIMESTAMP WHERE memory_id = ? AND status = 'active'",
                (memory_id,),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"没有可停用的 active memory：{memory_id}")

    def expire_layered_memories(self) -> int:
        with self._connect() as conn:
            return self._expire_layered_memories_conn(conn)

    def _expire_layered_memories_conn(self, conn: sqlite3.Connection) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cursor = conn.execute(
            """
            UPDATE layered_memory
            SET status = 'expired', updated_at = CURRENT_TIMESTAMP
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
                )
            )
        return tuple(runs)

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
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS sync_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, country TEXT NOT NULL, action TEXT NOT NULL,
                    target TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
            )
            for statement in statements:
                conn.execute(statement)
            self._ensure_column(conn, "layered_memory", "status", "TEXT NOT NULL DEFAULT 'active'")
            self._ensure_column(conn, "layered_memory", "source_memory_id", "INTEGER")
            self._ensure_column(conn, "layered_memory", "expires_at", "TEXT")
            self._ensure_column(conn, "layered_memory", "fingerprint", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "layered_memory", "human_verified", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "layered_memory", "updated_at", "TEXT")
            self._backfill_layered_memory_fingerprints(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_layered_memory_active ON layered_memory(country, memory_layer, status)")

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


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
