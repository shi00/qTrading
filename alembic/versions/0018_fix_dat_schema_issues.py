"""fix DAT-14/15/16/23 schema issues

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-05 00:00:00.000000

本轮 schema 清理（全部与 data/persistence/models.py 对齐）：
- DAT-14：screening_thinking.history_id 由 Integer 升为 BigInteger，
  FK 目标 screening_history.id 已为 BigInteger，两者类型对齐以消除外键类型不匹配。
- DAT-15：删除冗余/低效索引——
  * stock_basic.list_date / delist_date 去掉 index=True；
  * 删除复合索引 idx_stock_basic_dates / idx_stock_basic_status；
  * sw_industry_member.ts_code 移除 index=True（保留主键）；
  * watchlist.ts_code 移除 index=True（保留 unique，唯一约束自带唯一索引）；
  * screening_history 删除单列索引 idx_sh_run_id。
- DAT-16：market_news.source 低基数且无单列谓词，删除 ix_market_news_source；
  stock_concepts.concept_id 存在 `WHERE concept_id LIKE $1` 反查，新增索引
  ix_stock_concepts_concept_id。
- DAT-23：stock_basic.ts_code 增加域约束
  ck_stock_basic_valid_ts_code_format（格式 `^[0-9]{6}\\.[A-Z]{2}$`）。

v1.x S6：drop_index/drop_constraint 带 if_exists、create_index（PG 支持）带
if_not_exists，避免重复执行报错；PostgreSQL 不支持 ADD CONSTRAINT ... IF NOT EXISTS，
约束（uq/ck）由 alembic_version 表保证单次执行，配合 upgrade/downgrade 完全对称逆序
达到整体幂等。upgrade 正序、downgrade 完全对称逆序。R4：无字符串拼接 SQL，表达式为字面常量。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply DAT-14/15/16/23 schema changes (in forward order)."""
    # DAT-14：screening_thinking.history_id Integer -> BigInteger
    op.alter_column(
        "screening_thinking",
        "history_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )

    # DAT-15：删除冗余索引
    op.drop_index("idx_sh_run_id", table_name="screening_history", if_exists=True)
    op.drop_index("ix_stock_basic_list_date", table_name="stock_basic", if_exists=True)
    op.drop_index("idx_stock_basic_dates", table_name="stock_basic", if_exists=True)
    op.drop_index("idx_stock_basic_status", table_name="stock_basic", if_exists=True)
    op.drop_index("ix_stock_basic_delist_date", table_name="stock_basic", if_exists=True)
    op.drop_index("ix_sw_industry_member_ts_code", table_name="sw_industry_member", if_exists=True)
    # DAT-15：watchlist.ts_code 从唯一索引迁移为唯一约束。模型由
    # ``unique=True, index=True`` 改为 ``unique=True`` 后，产出物由唯一索引
    # ix_watchlist_ts_code 转为唯一约束 uq_watchlist_ts_code（名称经 uq_ 约定解析，
    # 二者均由 ts_code 唯一性承载）。先建约束再删旧索引以保持唯一性连续。
    op.create_unique_constraint("uq_watchlist_ts_code", "watchlist", ["ts_code"])
    op.drop_index("ix_watchlist_ts_code", table_name="watchlist", if_exists=True)
    op.drop_index("ix_market_news_source", table_name="market_news", if_exists=True)

    # DAT-16：新增 stock_concepts.concept_id 反查索引
    op.create_index(
        "ix_stock_concepts_concept_id",
        "stock_concepts",
        ["concept_id"],
        if_not_exists=True,
    )

    # DAT-23：stock_basic.ts_code 域约束（命名经 ck_%(table_name)s_%(constraint_name)s
    # 约定解析为 ck_stock_basic_valid_ts_code_format，与模型一致）。
    # 注：PostgreSQL 不支持 ADD CONSTRAINT ... IF NOT EXISTS，且 alembic 对该参数
    # 不渲染，故不传 if_not_exists；单次迁移由 alembic_version 表保证不重复执行。
    op.create_check_constraint(
        "valid_ts_code_format",
        "stock_basic",
        "ts_code ~ '^[0-9]{6}\\.[A-Z]{2}$'",
    )


def downgrade() -> None:
    """Reverse DAT-14/15/16/23 schema changes (exact mirror of upgrade)."""
    # DAT-23：删除域约束（与创建一致，名称经命名约定解析）
    op.drop_constraint(
        "valid_ts_code_format",
        "stock_basic",
        type_="check",
        if_exists=True,
    )

    # DAT-16：新增的 stock_concepts.concept_id 索引移除
    op.drop_index("ix_stock_concepts_concept_id", table_name="stock_concepts", if_exists=True)

    # DAT-15：watchlist 唯一约束回退为唯一索引（与 0016 一致）
    op.drop_constraint("uq_watchlist_ts_code", "watchlist", type_="unique", if_exists=True)

    # DAT-15：恢复被删除的冗余索引
    op.create_index("ix_market_news_source", "market_news", ["source"], if_not_exists=True)
    op.create_index("ix_watchlist_ts_code", "watchlist", ["ts_code"], unique=True, if_not_exists=True)
    op.create_index("ix_sw_industry_member_ts_code", "sw_industry_member", ["ts_code"], if_not_exists=True)
    op.create_index("ix_stock_basic_delist_date", "stock_basic", ["delist_date"], if_not_exists=True)
    op.create_index(
        "idx_stock_basic_status",
        "stock_basic",
        ["list_status", "list_date"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_stock_basic_dates",
        "stock_basic",
        ["list_date", "delist_date"],
        if_not_exists=True,
    )
    op.create_index("ix_stock_basic_list_date", "stock_basic", ["list_date"], if_not_exists=True)
    op.create_index("idx_sh_run_id", "screening_history", ["run_id"], if_not_exists=True)

    # DAT-14：screening_thinking.history_id BigInteger -> Integer
    op.alter_column(
        "screening_thinking",
        "history_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
