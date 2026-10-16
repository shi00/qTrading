"""add ai usage ledger tables (S3 统一模型调度与计量)

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-16 00:00:00.000000

新增统一模型用量账本三表（ADR-0014 §9.2/§10）：
- ``ai_invocation``：调用归属与预算预留事实源（reservations JSONB 追加唯一
  reservation_id；owner 三元组支撑跨暂停恢复）；
- ``ai_attempt``：单次尝试账目（用量五字段归一化 + 迟到差额 revision 幂等推进）；
- ``ai_usage_monthly``：月度汇总（scenario/connection 两 scope 不相加；
  last_usage_revision 为 R22 单调水位）。

三表无 FK 约束（逻辑关联），advisory 月锁在 DAO 层实现，不依赖表级锁。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0040"
down_revision: str | Sequence[str] | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ai usage ledger tables (invocation / attempt / monthly)."""
    op.create_table(
        "ai_invocation",
        sa.Column("invocation_id", sa.String(), primary_key=True),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("budget_policy_snapshot", JSONB(none_as_null=True), nullable=True),
        sa.Column("reservations", JSONB(none_as_null=True), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "candidate_decisions", JSONB(none_as_null=True), nullable=True, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("owner_session_id", sa.String(), nullable=False),
        sa.Column("owner_epoch", sa.Integer(), nullable=False),
        sa.Column("owner_lease_expires_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("test_operation_id", sa.String(), nullable=True),
        sa.Column("draft_fingerprint", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
            nullable=False,
        ),
    )
    op.create_index("idx_ai_invocation_owner_lease", "ai_invocation", ["owner_session_id", "owner_lease_expires_at"])
    op.create_index("idx_ai_invocation_scenario_created", "ai_invocation", ["scenario_id", "created_at"])

    op.create_table(
        "ai_attempt",
        sa.Column("attempt_id", sa.String(), primary_key=True),
        sa.Column("invocation_id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("candidate_index", sa.Integer(), nullable=False),
        sa.Column("retry_index", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("connection_revision", sa.Integer(), nullable=False),
        sa.Column("adapter_kind", sa.String(), nullable=False),
        sa.Column("configured_model", sa.String(), nullable=False),
        sa.Column("actual_model", sa.String(), nullable=True),
        sa.Column("security_revision", sa.Integer(), nullable=False),
        sa.Column("reservation_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("duration_ms", sa.Double(), nullable=True),
        sa.Column("input_total_tokens", sa.Integer(), nullable=True),
        sa.Column("uncached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("encoder_input_tokens", sa.Integer(), nullable=True),
        sa.Column("text_count", sa.Integer(), nullable=True),
        sa.Column("usage_status", sa.String(), nullable=False, server_default=sa.text("'unavailable'")),
        sa.Column("usage_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("accounted_usage_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("usage_fingerprint", sa.String(), nullable=True),
        sa.Column("usage_window_expires_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("usage_window_closed_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("cost_cny", sa.Numeric(24, 8), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
            nullable=False,
        ),
    )
    op.create_index("idx_ai_attempt_invocation", "ai_attempt", ["invocation_id"])
    op.create_index("idx_ai_attempt_connection_created", "ai_attempt", ["connection_id", "created_at"])
    op.create_index("idx_ai_attempt_window_expiry", "ai_attempt", ["usage_window_expires_at"])

    op.create_table(
        "ai_usage_monthly",
        sa.Column("month", sa.String(length=6), primary_key=True),
        sa.Column("aggregation_scope", sa.String(length=16), primary_key=True),
        sa.Column("grouping_key", sa.String(), primary_key=True),
        sa.Column("scenario_id", sa.String(), nullable=True),
        sa.Column("trigger", sa.String(), nullable=True),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column("connection_id", sa.String(), nullable=True),
        sa.Column("invocation_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("succeeded_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_cny", sa.Numeric(24, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("last_usage_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop the ai usage ledger tables (data loss, non-recoverable)."""
    op.drop_table("ai_usage_monthly")
    op.drop_index("idx_ai_attempt_window_expiry", table_name="ai_attempt")
    op.drop_index("idx_ai_attempt_connection_created", table_name="ai_attempt")
    op.drop_index("idx_ai_attempt_invocation", table_name="ai_attempt")
    op.drop_table("ai_attempt")
    op.drop_index("idx_ai_invocation_scenario_created", table_name="ai_invocation")
    op.drop_index("idx_ai_invocation_owner_lease", table_name="ai_invocation")
    op.drop_table("ai_invocation")
