"""rename limit_list.limit to limit_type (R17: limit 是 SQL 保留字)

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-28 00:00:00.000000

R17 红线要求：禁止使用 SQL 保留字作为列名。``limit`` 是 PostgreSQL/SQL 标准
保留字（LIMIT 子句关键字），作为列名存在语法冲突风险。

本迁移将 ``limit_list.limit`` 列重命名为 ``limit_list.limit_type``，同步
ORM 定义（``models.py`` 中 ``Column(String, name="limit_type")``）与裸 SQL
查询（``quote_dao.py`` 中 ``SELECT limit_type ...``）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in the given table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    columns = inspector.get_columns(table_name)
    return any(col["name"] == column_name for col in columns)


def upgrade() -> None:
    """Rename limit_list.limit → limit_list.limit_type."""
    if not _column_exists("limit_list", "limit"):
        return
    op.alter_column("limit_list", "limit", new_column_name="limit_type")


def downgrade() -> None:
    """Rename limit_list.limit_type → limit_list.limit."""
    if not _column_exists("limit_list", "limit_type"):
        return
    op.alter_column("limit_list", "limit_type", new_column_name="limit")
