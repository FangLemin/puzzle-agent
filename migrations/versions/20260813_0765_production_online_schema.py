"""production online schema

Revision ID: 20260813_0765
Revises:
Create Date: 2026-08-13
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa  # noqa: F401

from puzzle_ops.production_db import postgres_schema_statements


revision = "20260813_0765"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in postgres_schema_statements():
        op.execute(statement)


def downgrade() -> None:
    for table in (
        "trace_events",
        "jobs",
        "harness_case_results",
        "harness_runs",
        "rag_embedding_cache",
        "rag_chunks",
        "rag_documents",
        "memory_audit_events",
        "layered_memory",
        "assets",
        "trial_uploads",
        "demand_rows",
        "audit_logs",
        "api_tokens",
        "users",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
