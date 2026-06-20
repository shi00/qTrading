"""initial_schema

Revision ID: 0001
Revises:
Create Date: 2026-06-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _target_schema() -> str | None:
    """Resolve migration target schema (if configured)."""
    try:
        ctx = op.get_context()
    except Exception:
        return None
    return getattr(ctx, "version_table_schema", None)


def _table_exists(table_name: str) -> bool:
    """Check if a table exists in the database."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    schema = _target_schema()
    return table_name in inspector.get_table_names(schema=schema)


def _is_postgresql() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def _json_type():
    if _is_postgresql():
        return sa.dialects.postgresql.JSONB()
    return sa.JSON()


def _create_partial_index(table_name: str, index_name: str, column: str, where_clause: str) -> None:
    if _is_postgresql():
        op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({column}) WHERE {where_clause}")
    else:
        op.create_index(index_name, table_name, [column], if_not_exists=True)


def _create_table_if_not_exists(table_name: str, *args, **kwargs) -> None:
    """Create a table only if it doesn't already exist.

    Ensures idempotent migrations - safe to re-run on databases
    that may already have some tables from partial migrations.

    Uses a two-layer defense: first checks via ``_table_exists``, then
    catches ``ProgrammingError`` / ``OperationalError`` in case the
    inspector check fails in async contexts (same root cause as the
    ``_index_exists`` bug — ``sa.inspect(conn)`` may not reliably
    enumerate existing objects under asyncpg).
    """
    if _table_exists(table_name):
        return
    try:
        op.create_table(table_name, *args, **kwargs)
    except sa.exc.ProgrammingError:
        # Table already exists — safe to ignore for idempotent migrations.
        pass


def _create_index_if_not_exists(
    index_name: str,
    table_name: str,
    columns: list,
    **kwargs,
) -> None:
    """Create an index only if it doesn't already exist.

    Uses Alembic's native ``if_not_exists`` parameter so the database
    handles idempotency (``CREATE INDEX IF NOT EXISTS``).  This is more
    reliable than the previous ``_index_exists`` inspection which could
    fail inside async Alembic contexts where ``sa.inspect(conn)`` does
    not correctly enumerate existing indexes.
    """
    op.create_index(index_name, table_name, columns, if_not_exists=True, **kwargs)


def _drop_table_if_exists(table_name: str) -> None:
    """Drop a table only if it exists."""
    if _table_exists(table_name):
        op.drop_table(table_name)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    """Drop an index only if it exists."""
    op.drop_index(index_name, table_name=table_name, if_exists=True)


def _create_all_tables() -> None:
    """Create all tables for a brand-new database."""
    _create_table_if_not_exists(
        "block_trade",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("price", sa.Numeric(12, 4), nullable=True),
        sa.Column("vol", sa.BigInteger(), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("buyer", sa.String(), nullable=False),
        sa.Column("seller", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", "buyer", "seller", name=op.f("pk_block_trade")),
    )
    _create_table_if_not_exists(
        "daily_indicators",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("pe", sa.Numeric(12, 4), nullable=True),
        sa.Column("pe_ttm", sa.Numeric(12, 4), nullable=True),
        sa.Column("pb", sa.Numeric(12, 4), nullable=True),
        sa.Column("ps", sa.Numeric(12, 4), nullable=True),
        sa.Column("ps_ttm", sa.Numeric(12, 4), nullable=True),
        sa.Column("dv_ratio", sa.Numeric(12, 4), nullable=True),
        sa.Column("dv_ttm", sa.Numeric(12, 4), nullable=True),
        sa.Column("total_mv", sa.Numeric(20, 4), nullable=True),
        sa.Column("circ_mv", sa.Numeric(20, 4), nullable=True),
        sa.Column("total_share", sa.BigInteger(), nullable=True),
        sa.Column("float_share", sa.BigInteger(), nullable=True),
        sa.Column("free_share", sa.BigInteger(), nullable=True),
        sa.Column("turnover_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("turnover_rate_f", sa.Numeric(12, 4), nullable=True),
        sa.Column("volume_ratio", sa.Numeric(12, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_daily_indicators")),
    )
    _create_index_if_not_exists(
        "ix_daily_indicators_date_code",
        "daily_indicators",
        ["trade_date", "ts_code"],
        unique=False,
    )
    _create_table_if_not_exists(
        "daily_quotes",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(12, 4), nullable=True),
        sa.Column("high", sa.Numeric(12, 4), nullable=True),
        sa.Column("low", sa.Numeric(12, 4), nullable=True),
        sa.Column("close", sa.Numeric(12, 4), nullable=True),
        sa.Column("pre_close", sa.Numeric(12, 4), nullable=True),
        sa.Column("change", sa.Numeric(12, 4), nullable=True),
        sa.Column("pct_chg", sa.Numeric(8, 4), nullable=True),
        sa.Column("vol", sa.BigInteger(), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("adj_factor", sa.Numeric(20, 12), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_daily_quotes")),
    )
    _create_index_if_not_exists(
        "ix_daily_quotes_date_code",
        "daily_quotes",
        ["trade_date", "ts_code"],
        unique=False,
    )
    _create_table_if_not_exists(
        "dividend",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=False),
        sa.Column("div_proc", sa.String(), nullable=True),
        sa.Column("stk_div", sa.Numeric(12, 4), nullable=True),
        sa.Column("stk_bo_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("stk_co_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("cash_div", sa.Numeric(12, 4), nullable=True),
        sa.Column("cash_div_tax", sa.Numeric(12, 4), nullable=True),
        sa.Column("record_date", sa.Date(), nullable=True),
        sa.Column("ex_date", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", "ann_date", name=op.f("pk_dividend")),
    )
    _create_index_if_not_exists(op.f("ix_dividend_ann_date"), "dividend", ["ann_date"], unique=False)
    _create_table_if_not_exists(
        "fina_audit",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("audit_result", sa.String(), nullable=True),
        sa.Column("audit_sign", sa.String(), nullable=True),
        sa.Column("audit_fees", sa.Numeric(20, 4), nullable=True),
        sa.Column("audit_agency", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", name=op.f("pk_fina_audit")),
    )
    _create_table_if_not_exists(
        "fina_forecast",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=False),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("p_change_min", sa.Numeric(12, 4), nullable=True),
        sa.Column("p_change_max", sa.Numeric(12, 4), nullable=True),
        sa.Column("net_profit_min", sa.Numeric(20, 4), nullable=True),
        sa.Column("net_profit_max", sa.Numeric(20, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", "ann_date", name=op.f("pk_fina_forecast")),
    )
    _create_index_if_not_exists(op.f("ix_fina_forecast_ann_date"), "fina_forecast", ["ann_date"], unique=False)
    _create_table_if_not_exists(
        "fina_mainbz",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("bz_item", sa.String(), nullable=False),
        sa.Column("bz_sales", sa.Numeric(20, 4), nullable=True),
        sa.Column("bz_profit", sa.Numeric(20, 4), nullable=True),
        sa.Column("bz_cost", sa.Numeric(20, 4), nullable=True),
        sa.Column("curr_type", sa.String(), nullable=True),
        sa.Column("update_flag", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", "bz_item", name=op.f("pk_fina_mainbz")),
    )
    _create_index_if_not_exists(op.f("ix_fina_mainbz_end_date"), "fina_mainbz", ["end_date"], unique=False)
    _create_table_if_not_exists(
        "financial_reports",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("report_type", sa.String(), nullable=True),
        sa.Column("total_revenue", sa.Numeric(20, 4), nullable=True),
        sa.Column("revenue", sa.Numeric(20, 4), nullable=True),
        sa.Column("n_income", sa.Numeric(20, 4), nullable=True),
        sa.Column("n_income_attr_p", sa.Numeric(20, 4), nullable=True),
        sa.Column("total_assets", sa.Numeric(20, 4), nullable=True),
        sa.Column("total_liab", sa.Numeric(20, 4), nullable=True),
        sa.Column("total_hldr_eqy_exc_min_int", sa.Numeric(20, 4), nullable=True),
        sa.Column("roe", sa.Numeric(12, 4), nullable=True),
        sa.Column("roe_dt", sa.Numeric(12, 4), nullable=True),
        sa.Column("grossprofit_margin", sa.Numeric(12, 4), nullable=True),
        sa.Column("netprofit_margin", sa.Numeric(12, 4), nullable=True),
        sa.Column("debt_to_assets", sa.Numeric(12, 4), nullable=True),
        sa.Column("or_yoy", sa.Numeric(12, 4), nullable=True),
        sa.Column("netprofit_yoy", sa.Numeric(12, 4), nullable=True),
        sa.Column("goodwill", sa.Numeric(20, 4), nullable=True),
        sa.Column("audit_result", sa.String(), nullable=True),
        sa.Column("n_cashflow_act", sa.Numeric(20, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", name=op.f("pk_financial_reports")),
    )
    _create_index_if_not_exists(
        op.f("ix_financial_reports_end_date"),
        "financial_reports",
        ["end_date"],
        unique=False,
    )
    _create_index_if_not_exists(
        "ix_financial_reports_ts_code_ann_date",
        "financial_reports",
        ["ts_code", "ann_date"],
        unique=False,
    )
    _create_index_if_not_exists(
        "ix_financial_reports_ann_date",
        "financial_reports",
        ["ann_date"],
        unique=False,
    )
    _create_table_if_not_exists(
        "index_daily",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("close", sa.Numeric(12, 4), nullable=True),
        sa.Column("open", sa.Numeric(12, 4), nullable=True),
        sa.Column("high", sa.Numeric(12, 4), nullable=True),
        sa.Column("low", sa.Numeric(12, 4), nullable=True),
        sa.Column("pre_close", sa.Numeric(12, 4), nullable=True),
        sa.Column("change", sa.Numeric(12, 4), nullable=True),
        sa.Column("pct_chg", sa.Numeric(8, 4), nullable=True),
        sa.Column("vol", sa.BigInteger(), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_index_daily")),
    )
    _create_index_if_not_exists("idx_index_daily_date_code", "index_daily", ["trade_date", "ts_code"])
    _create_table_if_not_exists(
        "index_dailybasic",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("total_mv", sa.Numeric(20, 4), nullable=True),
        sa.Column("float_mv", sa.Numeric(20, 4), nullable=True),
        sa.Column("total_share", sa.BigInteger(), nullable=True),
        sa.Column("float_share", sa.BigInteger(), nullable=True),
        sa.Column("free_share", sa.BigInteger(), nullable=True),
        sa.Column("turnover_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("turnover_rate_f", sa.Numeric(12, 4), nullable=True),
        sa.Column("pe", sa.Numeric(12, 4), nullable=True),
        sa.Column("pe_ttm", sa.Numeric(12, 4), nullable=True),
        sa.Column("pb", sa.Numeric(12, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_index_dailybasic")),
    )
    _create_table_if_not_exists(
        "index_weight",
        sa.Column("index_code", sa.String(), nullable=False),
        sa.Column("con_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("weight", sa.Numeric(12, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("index_code", "con_code", "trade_date", name=op.f("pk_index_weight")),
    )
    _create_index_if_not_exists(op.f("ix_index_weight_trade_date"), "index_weight", ["trade_date"], unique=False)
    _create_table_if_not_exists(
        "limit_list",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("close", sa.Numeric(12, 4), nullable=True),
        sa.Column("pct_chg", sa.Numeric(8, 4), nullable=True),
        sa.Column("amp", sa.Numeric(12, 4), nullable=True),
        sa.Column("fc_ratio", sa.Numeric(12, 4), nullable=True),
        sa.Column("fl_ratio", sa.Numeric(12, 4), nullable=True),
        sa.Column("fd_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("first_time", sa.String(), nullable=True),
        sa.Column("last_time", sa.String(), nullable=True),
        sa.Column("open_times", sa.Integer(), nullable=True),
        sa.Column("strth", sa.Numeric(12, 4), nullable=True),
        sa.Column("limit", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name=op.f("pk_limit_list")),
    )
    _create_index_if_not_exists(op.f("ix_limit_list_ts_code"), "limit_list", ["ts_code"], unique=False)
    _create_table_if_not_exists(
        "macro_economy",
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("m2", sa.Numeric(20, 4), nullable=True),
        sa.Column("m2_yoy", sa.Numeric(12, 4), nullable=True),
        sa.Column("m1", sa.Numeric(20, 4), nullable=True),
        sa.Column("m1_yoy", sa.Numeric(12, 4), nullable=True),
        sa.Column("m0", sa.Numeric(20, 4), nullable=True),
        sa.Column("m0_yoy", sa.Numeric(12, 4), nullable=True),
        sa.Column("cpi", sa.Numeric(12, 4), nullable=True),
        sa.Column("ppi", sa.Numeric(12, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("period", name=op.f("pk_macro_economy")),
    )
    _create_table_if_not_exists(
        "margin_daily",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("rzye", sa.Numeric(20, 4), nullable=True),
        sa.Column("rqye", sa.Numeric(20, 4), nullable=True),
        sa.Column("rzmre", sa.Numeric(20, 4), nullable=True),
        sa.Column("rqyl", sa.Numeric(20, 4), nullable=True),
        sa.Column("rzrqye", sa.Numeric(20, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_margin_daily")),
    )
    _create_index_if_not_exists(op.f("ix_margin_daily_trade_date"), "margin_daily", ["trade_date"], unique=False)
    _create_table_if_not_exists(
        "market_news",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("content", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("tags", sa.String(), nullable=True),
        sa.Column("publish_time", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_news")),
        sa.UniqueConstraint("content_hash", "publish_time", name="uq_market_news_hash_time"),
    )
    _create_index_if_not_exists(op.f("ix_market_news_source"), "market_news", ["source"], unique=False)
    _create_index_if_not_exists("idx_market_news_pub_source", "market_news", ["publish_time", "source"], unique=False)
    _create_table_if_not_exists(
        "moneyflow_daily",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("buy_sm_vol", sa.BigInteger(), nullable=True),
        sa.Column("buy_sm_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("sell_sm_vol", sa.BigInteger(), nullable=True),
        sa.Column("sell_sm_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("buy_md_vol", sa.BigInteger(), nullable=True),
        sa.Column("buy_md_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("sell_md_vol", sa.BigInteger(), nullable=True),
        sa.Column("sell_md_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("buy_lg_vol", sa.BigInteger(), nullable=True),
        sa.Column("buy_lg_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("sell_lg_vol", sa.BigInteger(), nullable=True),
        sa.Column("sell_lg_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("buy_elg_vol", sa.BigInteger(), nullable=True),
        sa.Column("buy_elg_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("sell_elg_vol", sa.BigInteger(), nullable=True),
        sa.Column("sell_elg_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("net_mf_vol", sa.BigInteger(), nullable=True),
        sa.Column("net_mf_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_moneyflow_daily")),
    )
    _create_index_if_not_exists(
        "ix_moneyflow_daily_date_code",
        "moneyflow_daily",
        ["trade_date", "ts_code"],
        unique=False,
    )
    _create_table_if_not_exists(
        "moneyflow_hsgt",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ggt_ss", sa.Numeric(20, 4), nullable=True),
        sa.Column("ggt_sz", sa.Numeric(20, 4), nullable=True),
        sa.Column("hgt", sa.Numeric(20, 4), nullable=True),
        sa.Column("sgt", sa.Numeric(20, 4), nullable=True),
        sa.Column("north_money", sa.Numeric(20, 4), nullable=True),
        sa.Column("south_money", sa.Numeric(20, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("trade_date", name=op.f("pk_moneyflow_hsgt")),
    )
    _create_table_if_not_exists(
        "northbound_holding",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("vol", sa.BigInteger(), nullable=True),
        sa.Column("ratio", sa.Numeric(12, 4), nullable=True),
        sa.Column("exchange", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_northbound_holding")),
    )
    _create_index_if_not_exists(
        op.f("ix_northbound_holding_trade_date"),
        "northbound_holding",
        ["trade_date"],
        unique=False,
    )
    _create_table_if_not_exists(
        "pledge_stat",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("pledge_count", sa.Integer(), nullable=True),
        sa.Column("unrest_pledge", sa.Numeric(20, 4), nullable=True),
        sa.Column("rest_pledge", sa.Numeric(20, 4), nullable=True),
        sa.Column("total_share", sa.Numeric(20, 4), nullable=True),
        sa.Column("pledge_ratio", sa.Numeric(12, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", name=op.f("pk_pledge_stat")),
    )
    _create_table_if_not_exists(
        "repurchase",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("proc", sa.String(), nullable=True),
        sa.Column("exp_date", sa.Date(), nullable=True),
        sa.Column("vol", sa.BigInteger(), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("high_limit", sa.Numeric(12, 4), nullable=True),
        sa.Column("low_limit", sa.Numeric(12, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "ann_date", name=op.f("pk_repurchase")),
    )
    _create_index_if_not_exists(op.f("ix_repurchase_ann_date"), "repurchase", ["ann_date"], unique=False)
    _create_table_if_not_exists(
        "screening_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("strategy_name", sa.String(), nullable=False),
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("close", sa.Numeric(12, 4), nullable=True),
        sa.Column("pct_chg", sa.Numeric(8, 4), nullable=True),
        sa.Column("industry", sa.String(), nullable=True),
        sa.Column("vol", sa.BigInteger(), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("turnover_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("pe_ttm", sa.Numeric(12, 4), nullable=True),
        sa.Column("pb", sa.Numeric(12, 4), nullable=True),
        sa.Column("ps_ttm", sa.Numeric(12, 4), nullable=True),
        sa.Column("dv_ttm", sa.Numeric(12, 4), nullable=True),
        sa.Column("total_mv", sa.Numeric(20, 4), nullable=True),
        sa.Column("circ_mv", sa.Numeric(20, 4), nullable=True),
        sa.Column("roe", sa.Numeric(12, 4), nullable=True),
        sa.Column("grossprofit_margin", sa.Numeric(12, 4), nullable=True),
        sa.Column("debt_to_assets", sa.Numeric(12, 4), nullable=True),
        sa.Column("or_yoy", sa.Numeric(12, 4), nullable=True),
        sa.Column("netprofit_yoy", sa.Numeric(12, 4), nullable=True),
        sa.Column("t1_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("t1_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("t5_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("t5_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("index_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("alpha", sa.Numeric(12, 4), nullable=True),
        sa.Column("ai_score", sa.Numeric(12, 4), nullable=True),
        sa.Column("ai_reason", sa.String(), nullable=True),
        sa.Column("prediction_result", sa.String(), nullable=True),
        sa.Column("review_status", sa.String(), nullable=True, server_default="PENDING"),
        sa.Column("params_snapshot", _json_type(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_screening_history")),
        sa.UniqueConstraint(
            "run_id",
            "ts_code",
            name="uq_screening_history_run_code",
        ),
    )
    _create_index_if_not_exists(
        "idx_sh_date_strategy",
        "screening_history",
        ["trade_date", "strategy_name"],
        unique=False,
    )
    _create_index_if_not_exists(
        "idx_sh_date_code",
        "screening_history",
        ["trade_date", "ts_code"],
        unique=False,
    )
    _create_index_if_not_exists(
        "idx_sh_run_id",
        "screening_history",
        ["run_id"],
        unique=False,
    )

    _create_partial_index(
        "screening_history", "idx_sh_prediction_result", "prediction_result", "prediction_result IS NOT NULL"
    )
    _create_partial_index(
        "screening_history",
        "idx_sh_pending",
        "review_status",
        "review_status IN ('PENDING', 'T1_DONE') OR (review_status IS NULL)",
    )
    if _is_postgresql():
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_sh_params_gin ON screening_history USING gin (params_snapshot jsonb_path_ops)"
        )
    _create_table_if_not_exists(
        "screening_thinking",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("history_id", sa.Integer(), nullable=False),
        sa.Column("thinking", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("history_id", name=op.f("uq_screening_thinking_history_id")),
        sa.ForeignKeyConstraint(
            ["history_id"],
            ["screening_history.id"],
            ondelete="CASCADE",
            name=op.f("fk_screening_thinking_history_id_screening_history"),
        ),
    )
    _create_table_if_not_exists(
        "shibor_daily",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("on", sa.Numeric(12, 4), nullable=True),
        sa.Column("1w", sa.Numeric(12, 4), nullable=True),
        sa.Column("2w", sa.Numeric(12, 4), nullable=True),
        sa.Column("1m", sa.Numeric(12, 4), nullable=True),
        sa.Column("3m", sa.Numeric(12, 4), nullable=True),
        sa.Column("6m", sa.Numeric(12, 4), nullable=True),
        sa.Column("9m", sa.Numeric(12, 4), nullable=True),
        sa.Column("1y", sa.Numeric(12, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("date", name=op.f("pk_shibor_daily")),
    )
    _create_table_if_not_exists(
        "stk_holdernumber",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("holder_num", sa.BigInteger(), nullable=True),
        sa.Column("holder_num_change", sa.BigInteger(), nullable=True),
        sa.Column("holder_num_ratio", sa.Numeric(12, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", name=op.f("pk_stk_holdernumber")),
    )
    _create_index_if_not_exists(
        op.f("ix_stk_holdernumber_end_date"),
        "stk_holdernumber",
        ["end_date"],
        unique=False,
    )
    _create_table_if_not_exists(
        "stock_basic",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("area", sa.String(), nullable=True),
        sa.Column("industry", sa.String(), nullable=True),
        sa.Column("market", sa.String(), nullable=True),
        sa.Column("list_date", sa.Date(), nullable=True),
        sa.Column("list_status", sa.String(), nullable=True),
        sa.Column("delist_date", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", name=op.f("pk_stock_basic")),
    )
    _create_index_if_not_exists(op.f("ix_stock_basic_list_date"), "stock_basic", ["list_date"], unique=False)
    _create_index_if_not_exists(op.f("ix_stock_basic_delist_date"), "stock_basic", ["delist_date"], unique=False)
    _create_index_if_not_exists("idx_stock_basic_dates", "stock_basic", ["list_date", "delist_date"], unique=False)
    _create_index_if_not_exists("idx_stock_basic_status", "stock_basic", ["list_status", "list_date"], unique=False)
    _create_table_if_not_exists(
        "stock_concepts",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("concept_name", sa.String(), nullable=True),
        sa.Column("concept_id", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "concept_id", name=op.f("pk_stock_concepts")),
    )

    _create_table_if_not_exists(
        "stock_sync_status",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("step4_completed_at", sa.DateTime(), nullable=True),
        sa.Column("sync_version", sa.Integer(), server_default="1", nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", name=op.f("pk_stock_sync_status")),
    )
    _create_table_if_not_exists(
        "suspend_d",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("suspend_timing", sa.String(), nullable=True),
        sa.Column("suspend_type", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_suspend_d")),
    )
    _create_index_if_not_exists(op.f("ix_suspend_d_trade_date"), "suspend_d", ["trade_date"], unique=False)
    _create_table_if_not_exists(
        "sync_status",
        sa.Column("table_name", sa.String(), nullable=False),
        sa.Column("last_sync_date", sa.Date(), nullable=True),
        sa.Column("last_data_date", sa.Date(), nullable=True),
        sa.Column("record_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("last_result_status", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("table_name", name=op.f("pk_sync_status")),
    )
    _create_table_if_not_exists(
        "task_history",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("task_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("progress", sa.Numeric(5, 2), server_default="0", nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("result", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_history")),
    )
    _create_index_if_not_exists(op.f("ix_task_history_created_at"), "task_history", ["created_at"], unique=False)
    _create_index_if_not_exists(
        "idx_task_history_status_created", "task_history", ["status", "created_at"], unique=False
    )
    _create_index_if_not_exists("idx_task_history_completed", "task_history", ["completed_at"], unique=False)
    _create_table_if_not_exists(
        "top10_holders",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("holder_name", sa.String(), nullable=False),
        sa.Column("hold_amount", sa.BigInteger(), nullable=True),
        sa.Column("hold_ratio", sa.Numeric(12, 4), nullable=True),
        sa.Column("hold_float_ratio", sa.Numeric(12, 4), nullable=True),
        sa.Column("hold_change", sa.BigInteger(), nullable=True),
        sa.Column("holder_type", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", "holder_name", name=op.f("pk_top10_holders")),
    )
    _create_index_if_not_exists(
        op.f("ix_top10_holders_holder_name"),
        "top10_holders",
        ["holder_name"],
        unique=False,
    )
    _create_table_if_not_exists(
        "top_list",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("close", sa.Numeric(12, 4), nullable=True),
        sa.Column("pct_change", sa.Numeric(8, 4), nullable=True),
        sa.Column("turnover_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("l_sell", sa.Numeric(20, 4), nullable=True),
        sa.Column("l_buy", sa.Numeric(20, 4), nullable=True),
        sa.Column("l_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("net_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("net_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("amount_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("float_values", sa.Numeric(20, 4), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name=op.f("pk_top_list")),
    )
    _create_index_if_not_exists(op.f("ix_top_list_ts_code"), "top_list", ["ts_code"], unique=False)
    _create_table_if_not_exists(
        "trade_cal",
        sa.Column("cal_date", sa.Date(), nullable=False),
        sa.Column("exchange", sa.String(), nullable=True),
        sa.Column("is_open", sa.Integer(), nullable=True),
        sa.Column("pretrade_date", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("cal_date", name=op.f("pk_trade_cal")),
    )

    _create_table_if_not_exists(
        "app_state",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_state")),
    )

    _create_table_if_not_exists(
        "backtest_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(16), nullable=False),
        sa.Column("strategy_name", sa.String(), nullable=False),
        sa.Column("params_snapshot", _json_type(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("initial_capital", sa.Numeric(20, 4), nullable=True),
        sa.Column("total_return", sa.Numeric(12, 6), nullable=True),
        sa.Column("annualized_return", sa.Numeric(12, 6), nullable=True),
        sa.Column("sharpe_ratio", sa.Numeric(12, 6), nullable=True),
        sa.Column("max_drawdown", sa.Numeric(12, 6), nullable=True),
        sa.Column("calmar_ratio", sa.Numeric(12, 6), nullable=True),
        sa.Column("ic_mean", sa.Numeric(12, 6), nullable=True),
        sa.Column("ic_ir", sa.Numeric(12, 6), nullable=True),
        sa.Column("win_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("profit_factor", sa.Numeric(12, 6), nullable=True),
        sa.Column("total_trades", sa.Integer(), nullable=True),
        sa.Column("volatility", sa.Numeric(12, 6), nullable=True),
        sa.Column("information_ratio", sa.Numeric(12, 6), nullable=True),
        sa.Column("tracking_error", sa.Numeric(12, 6), nullable=True),
        sa.Column("nav_curve_json", _json_type(), nullable=True),
        sa.Column("trades_json", _json_type(), nullable=True),
        sa.Column("period_stats_json", _json_type(), nullable=True),
        sa.Column("execution_price", sa.String(20), nullable=True),
        sa.Column("allow_limit_up_buy", sa.Boolean(), nullable=True),
        sa.Column("allow_limit_down_sell", sa.Boolean(), nullable=True),
        sa.Column("slippage_model", sa.String(20), nullable=True),
        sa.Column("app_version", sa.String(32), nullable=True),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backtest_results")),
        sa.UniqueConstraint("run_id", name=op.f("uq_backtest_results_run_id")),
    )
    _create_index_if_not_exists(
        op.f("ix_backtest_results_strategy"),
        "backtest_results",
        ["strategy_name"],
        unique=False,
    )
    _create_index_if_not_exists(
        op.f("ix_backtest_results_date"),
        "backtest_results",
        ["executed_at"],
        unique=False,
    )


_ALL_EXPECTED_TABLES = [
    "app_state",
    "backtest_results",
    "block_trade",
    "daily_indicators",
    "daily_quotes",
    "dividend",
    "fina_audit",
    "fina_forecast",
    "fina_mainbz",
    "financial_reports",
    "index_daily",
    "index_dailybasic",
    "index_weight",
    "limit_list",
    "macro_economy",
    "margin_daily",
    "market_news",
    "moneyflow_daily",
    "moneyflow_hsgt",
    "northbound_holding",
    "pledge_stat",
    "repurchase",
    "screening_history",
    "screening_thinking",
    "shibor_daily",
    "stk_holdernumber",
    "stock_basic",
    "stock_concepts",
    "stock_sync_status",
    "suspend_d",
    "sync_status",
    "task_history",
    "top10_holders",
    "top_list",
    "trade_cal",
]


def upgrade() -> None:
    """Create all tables for a fresh database."""
    _create_all_tables()


def downgrade() -> None:
    """Drop all tables."""
    _drop_index_if_exists(op.f("ix_backtest_results_date"), table_name="backtest_results")
    _drop_index_if_exists(op.f("ix_backtest_results_strategy"), table_name="backtest_results")
    _drop_table_if_exists("backtest_results")
    _drop_table_if_exists("app_state")
    _drop_table_if_exists("trade_cal")
    _drop_index_if_exists(op.f("ix_top_list_ts_code"), table_name="top_list")
    _drop_table_if_exists("top_list")
    _drop_index_if_exists(op.f("ix_top10_holders_holder_name"), table_name="top10_holders")
    _drop_table_if_exists("top10_holders")
    _drop_index_if_exists(op.f("ix_task_history_created_at"), table_name="task_history")
    _drop_index_if_exists("idx_task_history_completed", table_name="task_history")
    _drop_index_if_exists("idx_task_history_status_created", table_name="task_history")
    _drop_table_if_exists("task_history")
    _drop_table_if_exists("sync_status")
    _drop_index_if_exists(op.f("ix_suspend_d_trade_date"), table_name="suspend_d")
    _drop_table_if_exists("suspend_d")
    _drop_table_if_exists("stock_sync_status")

    _drop_table_if_exists("stock_concepts")
    _drop_index_if_exists("idx_stock_basic_status", table_name="stock_basic")
    _drop_index_if_exists("idx_stock_basic_dates", table_name="stock_basic")
    _drop_index_if_exists(op.f("ix_stock_basic_delist_date"), table_name="stock_basic")
    _drop_index_if_exists(op.f("ix_stock_basic_list_date"), table_name="stock_basic")
    _drop_table_if_exists("stock_basic")
    _drop_index_if_exists(op.f("ix_stk_holdernumber_end_date"), table_name="stk_holdernumber")
    _drop_table_if_exists("stk_holdernumber")
    _drop_table_if_exists("shibor_daily")
    _drop_index_if_exists("idx_sh_run_id", table_name="screening_history")
    _drop_index_if_exists("idx_sh_date_code", table_name="screening_history")
    _drop_index_if_exists("idx_sh_date_strategy", table_name="screening_history")
    _drop_table_if_exists("screening_thinking")
    _drop_table_if_exists("screening_history")
    _drop_index_if_exists(op.f("ix_repurchase_ann_date"), table_name="repurchase")
    _drop_table_if_exists("repurchase")
    _drop_table_if_exists("pledge_stat")
    _drop_index_if_exists(op.f("ix_northbound_holding_trade_date"), table_name="northbound_holding")
    _drop_table_if_exists("northbound_holding")
    _drop_table_if_exists("moneyflow_hsgt")
    _drop_index_if_exists("ix_moneyflow_daily_date_code", table_name="moneyflow_daily")
    _drop_table_if_exists("moneyflow_daily")
    _drop_table_if_exists("market_news")
    _drop_index_if_exists(op.f("ix_margin_daily_trade_date"), table_name="margin_daily")
    _drop_table_if_exists("margin_daily")
    _drop_table_if_exists("macro_economy")
    _drop_index_if_exists(op.f("ix_limit_list_ts_code"), table_name="limit_list")
    _drop_table_if_exists("limit_list")
    _drop_index_if_exists(op.f("ix_index_weight_trade_date"), table_name="index_weight")
    _drop_table_if_exists("index_weight")
    _drop_table_if_exists("index_dailybasic")
    _drop_table_if_exists("index_daily")
    _drop_index_if_exists("ix_financial_reports_ann_date", table_name="financial_reports")
    _drop_index_if_exists("ix_financial_reports_ts_code_ann_date", table_name="financial_reports")
    _drop_index_if_exists(op.f("ix_financial_reports_end_date"), table_name="financial_reports")
    _drop_table_if_exists("financial_reports")
    _drop_index_if_exists(op.f("ix_fina_mainbz_end_date"), table_name="fina_mainbz")
    _drop_table_if_exists("fina_mainbz")
    _drop_index_if_exists(op.f("ix_fina_forecast_ann_date"), table_name="fina_forecast")
    _drop_table_if_exists("fina_forecast")
    _drop_table_if_exists("fina_audit")
    _drop_index_if_exists(op.f("ix_dividend_ann_date"), table_name="dividend")
    _drop_table_if_exists("dividend")
    _drop_index_if_exists("ix_daily_quotes_date_code", table_name="daily_quotes")
    _drop_table_if_exists("daily_quotes")
    _drop_index_if_exists("ix_daily_indicators_date_code", table_name="daily_indicators")
    _drop_table_if_exists("daily_indicators")
    _drop_table_if_exists("block_trade")
