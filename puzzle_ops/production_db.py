from __future__ import annotations

from pathlib import Path
import os

from puzzle_ops.storage import PuzzleRepository


class PostgresPuzzleRepository(PuzzleRepository):
    backend = "postgres"

    def __init__(self, database_url: str):
        self.database_url = database_url
        super().__init__(Path(":memory:"))


def create_repository_from_env(runtime_dir: Path | str) -> PuzzleRepository:
    provider = os.environ.get("PUZZLEOPS_DB_PROVIDER", "sqlite").strip().lower()
    if provider == "postgres":
        database_url = os.environ.get("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError("PUZZLEOPS_DB_PROVIDER=postgres 时必须配置 DATABASE_URL")
        return PostgresPuzzleRepository(database_url)
    return PuzzleRepository(Path(runtime_dir) / "puzzle_ops.db")


def postgres_schema_statements() -> tuple[str, ...]:
    return (
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL,
            countries JSONB NOT NULL DEFAULT '[]'::jsonb,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS api_tokens (
            token_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(user_id),
            token_hash TEXT NOT NULL UNIQUE,
            created_by TEXT NOT NULL DEFAULT '',
            expires_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            audit_id TEXT PRIMARY KEY,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            country TEXT NOT NULL DEFAULT '',
            resource_type TEXT NOT NULL DEFAULT '',
            resource_id TEXT NOT NULL DEFAULT '',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS demand_rows (
            demand_id TEXT PRIMARY KEY,
            country TEXT NOT NULL,
            need_type TEXT NOT NULL,
            operation_tag TEXT NOT NULL,
            subject TEXT NOT NULL DEFAULT '',
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trial_uploads (
            upload_id TEXT PRIMARY KEY,
            country TEXT NOT NULL,
            asset_id TEXT NOT NULL DEFAULT '',
            parse_status TEXT NOT NULL DEFAULT 'queued',
            derivative_status TEXT NOT NULL DEFAULT 'not_requested',
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS assets (
            asset_id TEXT PRIMARY KEY,
            object_key TEXT NOT NULL,
            public_url TEXT NOT NULL DEFAULT '',
            sha256 TEXT NOT NULL,
            content_type TEXT NOT NULL DEFAULT '',
            size_bytes BIGINT NOT NULL DEFAULT 0,
            source_filename TEXT NOT NULL DEFAULT '',
            feishu_file_token TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS layered_memory (
            memory_id BIGSERIAL PRIMARY KEY,
            country TEXT NOT NULL,
            memory_layer TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'active',
            source_memory_id BIGINT,
            human_verified BOOLEAN NOT NULL DEFAULT false,
            approved_for_rag BOOLEAN NOT NULL DEFAULT false,
            review_status TEXT NOT NULL DEFAULT 'draft',
            created_by TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS memory_audit_events (
            event_id BIGSERIAL PRIMARY KEY,
            country TEXT NOT NULL,
            memory_id BIGINT NOT NULL,
            action TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT '',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS rag_documents (
            document_id TEXT PRIMARY KEY,
            country TEXT NOT NULL,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL,
            text TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS rag_chunks (
            chunk_id TEXT PRIMARY KEY,
            parent_id TEXT NOT NULL,
            country TEXT NOT NULL,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL,
            text TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS rag_embedding_cache (
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            text TEXT NOT NULL,
            vector JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY(provider, model, text_hash)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS harness_runs (
            run_id TEXT PRIMARY KEY,
            version TEXT NOT NULL,
            dataset_name TEXT NOT NULL,
            model_provider TEXT NOT NULL,
            generator_provider TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS harness_case_results (
            case_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES harness_runs(run_id),
            sample_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            country TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued',
            progress INTEGER NOT NULL DEFAULT 0,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            result JSONB NOT NULL DEFAULT '{}'::jsonb,
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            raw_provider_response JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trace_events (
            trace_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL DEFAULT '',
            country TEXT NOT NULL DEFAULT '',
            task_type TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            input_summary TEXT NOT NULL DEFAULT '',
            rag_citations JSONB NOT NULL DEFAULT '[]'::jsonb,
            visual_similarity_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
            output_summary TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'succeeded',
            error_message TEXT NOT NULL DEFAULT '',
            latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    )
