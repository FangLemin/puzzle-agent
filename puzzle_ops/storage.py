from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path

from puzzle_ops.models import HistoricalRecord


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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_records (
                    grade TEXT NOT NULL,
                    image_formula TEXT NOT NULL,
                    image_id TEXT PRIMARY KEY,
                    image_url TEXT NOT NULL,
                    local_image_path TEXT NOT NULL,
                    thumbnail_path TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    dimension_grade TEXT NOT NULL,
                    open_rate REAL NOT NULL,
                    completion_rate REAL NOT NULL,
                    avg_finish_time REAL NOT NULL,
                    operation_tag TEXT NOT NULL,
                    subject_tag TEXT NOT NULL,
                    js_category TEXT NOT NULL,
                    source TEXT NOT NULL,
                    remark TEXT NOT NULL,
                    distribution_date TEXT NOT NULL,
                    distribution_cycle TEXT NOT NULL,
                    country TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_memory (
                    memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    country TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS value_rules (
                    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    country TEXT NOT NULL,
                    rule_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
