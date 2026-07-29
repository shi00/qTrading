"""add watchlist table (用户关注列表, FR-UX-004, Task 4.2)

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-29 00:00:00.000000

新增 ``watchlist`` 表存储用户关注的股票。用户从选股结果/详情对话框加入关注，
按 ``ts_code`` 唯一去重（upsert），``note`` 存用户备注，``added_at`` 保留首次加入日期。

R17: note / added_at / stock_name / ts_code / id 均非 SQL 保留字，无需 name= 映射。
v1.7.0 S6: 新建表使用 guard 避免 duplicate，downgrade 路径完整。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    """Check if a table exists in the bound database."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    """Create watchlist table."""
    if _table_exists("watchlist"):
        return

    op.create_table(
        "watchlist",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("stock_name", sa.String(), nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("note", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watchlist")),
    )
    # ORM 模型 ts_code 用 unique=True, index=True → 期望 unique index (ix_watchlist_ts_code)。
    # 用 unique index 替代 UniqueConstraint + 普通 index，与 ORM 对齐避免 alembic check 漂移。
    op.create_index(
        "ix_watchlist_ts_code",
        "watchlist",
        ["ts_code"],
        unique=True,
        if_not_exists=True,
    )


def downgrade() -> None:
    """Drop watchlist table."""
    op.drop_index("ix_watchlist_ts_code", table_name="watchlist", if_exists=True)
    if _table_exists("watchlist"):
        op.drop_table("watchlist")
