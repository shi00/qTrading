"""rename SQL reserved columns (shibor_daily/app_state/sw_industry_classify)

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-13

R17: 修复 SQL 保留字列名（date/on/key/value/level）和数字开头列名（1w-1y）
- shibor_daily: date -> record_date, on -> on_rate, 1w/2w/1m/3m/6m/9m/1y -> week_1/week_2/month_1/month_3/month_6/month_9/year_1
- app_state: key -> config_key, value -> config_value
- sw_industry_classify: level -> sw_level

迁移后属性名与列名一致，不再需要 ORM ``name=`` 映射（最简方案）。
PostgreSQL 重命名列时自动保留主键约束，但索引名不随列名变化；
sw_industry_classify.level 上有 index=True，需手动重建索引以匹配 ORM 自动命名。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- shibor_daily: date -> record_date, on -> on_rate ---
    op.alter_column(
        "shibor_daily",
        "date",
        new_column_name="record_date",
        existing_type=sa.Date(),
        existing_nullable=False,
    )
    op.alter_column(
        "shibor_daily",
        "on",
        new_column_name="on_rate",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    # --- shibor_daily: 1w/2w/1m/3m/6m/9m/1y -> week_1/week_2/month_1/month_3/month_6/month_9/year_1 ---
    op.alter_column(
        "shibor_daily",
        "1w",
        new_column_name="week_1",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "shibor_daily",
        "2w",
        new_column_name="week_2",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "shibor_daily",
        "1m",
        new_column_name="month_1",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "shibor_daily",
        "3m",
        new_column_name="month_3",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "shibor_daily",
        "6m",
        new_column_name="month_6",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "shibor_daily",
        "9m",
        new_column_name="month_9",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "shibor_daily",
        "1y",
        new_column_name="year_1",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )

    # --- app_state: key -> config_key, value -> config_value ---
    op.alter_column(
        "app_state",
        "key",
        new_column_name="config_key",
        existing_type=sa.String(),
        existing_nullable=False,
    )
    op.alter_column(
        "app_state",
        "value",
        new_column_name="config_value",
        existing_type=sa.String(),
        existing_nullable=False,
    )

    # --- sw_industry_classify: level -> sw_level ---
    # R17：level 是 SQL 保留字。PostgreSQL ALTER TABLE RENAME COLUMN 不重命名索引，
    # 需手动重建 ix_sw_industry_classify_level -> ix_sw_industry_classify_sw_level 以匹配 ORM 自动命名。
    op.drop_index("ix_sw_industry_classify_level", table_name="sw_industry_classify")
    op.alter_column(
        "sw_industry_classify",
        "level",
        new_column_name="sw_level",
        existing_type=sa.String(2),
        existing_nullable=False,
    )
    op.create_index("ix_sw_industry_classify_sw_level", "sw_industry_classify", ["sw_level"])


def downgrade() -> None:
    # --- sw_industry_classify: sw_level -> level ---
    op.drop_index("ix_sw_industry_classify_sw_level", table_name="sw_industry_classify")
    op.alter_column(
        "sw_industry_classify",
        "sw_level",
        new_column_name="level",
        existing_type=sa.String(2),
        existing_nullable=False,
    )
    op.create_index("ix_sw_industry_classify_level", "sw_industry_classify", ["level"])

    # --- app_state: config_value -> value, config_key -> key ---
    op.alter_column(
        "app_state",
        "config_value",
        new_column_name="value",
        existing_type=sa.String(),
        existing_nullable=False,
    )
    op.alter_column(
        "app_state",
        "config_key",
        new_column_name="key",
        existing_type=sa.String(),
        existing_nullable=False,
    )

    # --- shibor_daily: year_1/month_9/month_6/month_3/month_1/week_2/week_1 -> 1y/9m/6m/3m/1m/2w/1w ---
    op.alter_column(
        "shibor_daily",
        "year_1",
        new_column_name="1y",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "shibor_daily",
        "month_9",
        new_column_name="9m",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "shibor_daily",
        "month_6",
        new_column_name="6m",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "shibor_daily",
        "month_3",
        new_column_name="3m",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "shibor_daily",
        "month_1",
        new_column_name="1m",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "shibor_daily",
        "week_2",
        new_column_name="2w",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "shibor_daily",
        "week_1",
        new_column_name="1w",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "shibor_daily",
        "on_rate",
        new_column_name="on",
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "shibor_daily",
        "record_date",
        new_column_name="date",
        existing_type=sa.Date(),
        existing_nullable=False,
    )
