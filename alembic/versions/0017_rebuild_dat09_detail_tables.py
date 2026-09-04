"""rebuild DAT-09 detail tables with complete primary keys

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-04 00:00:00.000000

DAT-09：四张明细表原主键维度不足，UPSERT 时静默丢行：
- top_list: PK (trade_date, ts_code) → (trade_date, ts_code, reason)，
  容纳同一股票同日多条上榜原因。
- top_inst: 列重写为官方契约（doc_id=107），PK (ts_code, trade_date, exalter, side)，
  容纳逐席位（exalter × side）明细。
- pledge_detail: 列重写为官方契约（doc_id=111），PK (ts_code, ann_date, holder_name,
  start_date, pledge_amount) 容纳同日多笔质押（含金额维度）。
- share_float: PK (ts_code, float_date) → (ts_code, float_date, holder_name)，
  容纳同日多股东解禁。

旧数据主键列存在 NULL（列名与契约不匹配时 API 返回缺失），无法增量修复，
故 drop+create 重建（业务上由同步逻辑重新拉取；pledge_detail 同步入口为
DAT-09 新增的 HolderSyncStrategy._sync_pledge_detail）。

v1.x S6：drop+create 属数据迁移，downgrade 路径完整恢复旧 schema。
R17：所有列名均非 SQL 保留字，无需 name= 映射。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop and recreate the four DAT-09 detail tables with complete PKs."""
    # 1. Drop legacy tables (data is corrupted: NULL in PK columns).
    op.drop_index("ix_pledge_detail_end_date", table_name="pledge_detail")
    op.drop_table("pledge_detail")
    op.drop_index("ix_share_float_float_date", table_name="share_float")
    op.drop_index("ix_share_float_ann_date", table_name="share_float")
    op.drop_table("share_float")
    op.drop_index("ix_top_inst_trade_date", table_name="top_inst")
    op.drop_table("top_inst")
    op.drop_index("ix_top_list_ts_code", table_name="top_list")
    op.drop_table("top_list")

    # 2. Recreate with corrected schema (match data/persistence/models.py).
    op.create_table(
        "top_list",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("name", sa.String()),
        sa.Column("close", sa.Numeric(12, 4)),
        sa.Column("pct_change", sa.Numeric(8, 4)),
        sa.Column("turnover_rate", sa.Numeric(12, 4)),
        sa.Column("amount", sa.Numeric(20, 4)),
        sa.Column("l_sell", sa.Numeric(20, 4)),
        sa.Column("l_buy", sa.Numeric(20, 4)),
        sa.Column("l_amount", sa.Numeric(20, 4)),
        sa.Column("net_amount", sa.Numeric(20, 4)),
        sa.Column("net_rate", sa.Numeric(12, 4)),
        sa.Column("amount_rate", sa.Numeric(12, 4)),
        sa.Column("float_values", sa.Numeric(20, 4)),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", "reason", name=op.f("pk_top_list")),
    )
    op.create_index("ix_top_list_ts_code", "top_list", ["ts_code"])

    op.create_table(
        "top_inst",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("exalter", sa.String(), nullable=False),
        sa.Column("side", sa.String(2), nullable=False),
        sa.Column("buy", sa.Numeric(20, 4)),
        sa.Column("buy_rate", sa.Numeric(12, 4)),
        sa.Column("sell", sa.Numeric(20, 4)),
        sa.Column("sell_rate", sa.Numeric(12, 4)),
        sa.Column("net_buy", sa.Numeric(20, 4)),
        sa.Column("reason", sa.String()),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", "exalter", "side", name=op.f("pk_top_inst")),
    )
    op.create_index("ix_top_inst_trade_date", "top_inst", ["trade_date"])

    op.create_table(
        "pledge_detail",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=False),
        sa.Column("holder_name", sa.String(100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("pledge_amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("end_date", sa.Date()),
        sa.Column("is_release", sa.String(2)),
        sa.Column("release_date", sa.Date()),
        sa.Column("pledgor", sa.String(100)),
        sa.Column("holding_amount", sa.Numeric(20, 4)),
        sa.Column("pledged_amount", sa.Numeric(20, 4)),
        sa.Column("p_total_ratio", sa.Numeric(12, 4)),
        sa.Column("h_total_ratio", sa.Numeric(12, 4)),
        sa.Column("is_buyback", sa.String(2)),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint(
            "ts_code",
            "ann_date",
            "holder_name",
            "start_date",
            "pledge_amount",
            name=op.f("pk_pledge_detail"),
        ),
    )
    op.create_index("ix_pledge_detail_ann_date", "pledge_detail", ["ann_date"])
    op.create_index("ix_pledge_detail_start_date", "pledge_detail", ["start_date"])

    op.create_table(
        "share_float",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("ann_date", sa.Date()),
        sa.Column("float_date", sa.Date(), nullable=False),
        sa.Column("float_share", sa.Numeric(20, 4)),
        sa.Column("float_ratio", sa.Numeric(8, 4)),
        sa.Column("holder_name", sa.String(100), nullable=False),
        sa.Column("share_type", sa.String(50)),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ts_code", "float_date", "holder_name", name=op.f("pk_share_float")),
    )
    op.create_index("ix_share_float_ann_date", "share_float", ["ann_date"])
    op.create_index("ix_share_float_float_date", "share_float", ["float_date"])


def downgrade() -> None:
    """Restore the legacy (broken) schemas so downgrade base → upgrade head is reversible."""
    # Drop recreated tables.
    op.drop_index("ix_pledge_detail_start_date", table_name="pledge_detail")
    op.drop_index("ix_pledge_detail_ann_date", table_name="pledge_detail")
    op.drop_table("pledge_detail")
    op.drop_index("ix_share_float_float_date", table_name="share_float")
    op.drop_index("ix_share_float_ann_date", table_name="share_float")
    op.drop_table("share_float")
    op.drop_index("ix_top_inst_trade_date", table_name="top_inst")
    op.drop_table("top_inst")
    op.drop_index("ix_top_list_ts_code", table_name="top_list")
    op.drop_table("top_list")

    # Restore legacy schemas (match pre-0017 definitions).
    op.create_table(
        "top_list",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("name", sa.String()),
        sa.Column("close", sa.Numeric(12, 4)),
        sa.Column("pct_change", sa.Numeric(8, 4)),
        sa.Column("turnover_rate", sa.Numeric(12, 4)),
        sa.Column("amount", sa.Numeric(20, 4)),
        sa.Column("l_sell", sa.Numeric(20, 4)),
        sa.Column("l_buy", sa.Numeric(20, 4)),
        sa.Column("l_amount", sa.Numeric(20, 4)),
        sa.Column("net_amount", sa.Numeric(20, 4)),
        sa.Column("net_rate", sa.Numeric(12, 4)),
        sa.Column("amount_rate", sa.Numeric(12, 4)),
        sa.Column("float_values", sa.Numeric(20, 4)),
        sa.Column("reason", sa.String()),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name=op.f("pk_top_list")),
    )
    op.create_index("ix_top_list_ts_code", "top_list", ["ts_code"])

    op.create_table(
        "top_inst",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String()),
        sa.Column("close", sa.Numeric(12, 4)),
        sa.Column("pct_change", sa.Numeric(8, 4)),
        sa.Column("amount", sa.Numeric(20, 4)),
        sa.Column("net_amount", sa.Numeric(20, 4)),
        sa.Column("buy_amount", sa.Numeric(20, 4)),
        sa.Column("buy_value", sa.Numeric(20, 4)),
        sa.Column("sell_amount", sa.Numeric(20, 4)),
        sa.Column("sell_value", sa.Numeric(20, 4)),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_top_inst")),
    )
    op.create_index("ix_top_inst_trade_date", "top_inst", ["trade_date"])

    op.create_table(
        "pledge_detail",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("pledge_amount", sa.Numeric(20, 4)),
        sa.Column("unlimited_pledge_amount", sa.Numeric(20, 4)),
        sa.Column("limited_pledge_amount", sa.Numeric(20, 4)),
        sa.Column("total_pledge_amount", sa.Numeric(20, 4)),
        sa.Column("pledge_ratio", sa.Numeric(12, 4)),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ts_code", "end_date", name=op.f("pk_pledge_detail")),
    )
    op.create_index("ix_pledge_detail_end_date", "pledge_detail", ["end_date"])

    op.create_table(
        "share_float",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("ann_date", sa.Date()),
        sa.Column("float_date", sa.Date(), nullable=False),
        sa.Column("float_share", sa.Numeric(20, 4)),
        sa.Column("float_ratio", sa.Numeric(8, 4)),
        sa.Column("holder_name", sa.String(100)),
        sa.Column("share_type", sa.String(50)),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ts_code", "float_date", name=op.f("pk_share_float")),
    )
    op.create_index("ix_share_float_ann_date", "share_float", ["ann_date"])
    op.create_index("ix_share_float_float_date", "share_float", ["float_date"])
