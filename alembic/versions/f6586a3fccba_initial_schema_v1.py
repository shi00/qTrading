"""initial_schema_v1

Revision ID: f6586a3fccba
Revises:
Create Date: 2026-03-18 13:19:14.104707

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6586a3fccba"
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


def _index_exists(table_name: str, index_name: str) -> bool:
    """Check if an index exists on a table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    schema = _target_schema()
    indexes = [idx["name"] for idx in inspector.get_indexes(table_name, schema=schema)]
    return index_name in indexes


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    schema = _target_schema()
    if table_name not in inspector.get_table_names(schema=schema):
        return False
    cols = [c["name"] for c in inspector.get_columns(table_name, schema=schema)]
    return column_name in cols


NEW_SH_COLUMNS: dict[str, sa.Column] = {
    "t1_price": sa.Column("t1_price", sa.Float(), nullable=True),
    "t1_pct": sa.Column("t1_pct", sa.Float(), nullable=True),
    "t5_price": sa.Column("t5_price", sa.Float(), nullable=True),
    "t5_pct": sa.Column("t5_pct", sa.Float(), nullable=True),
    "index_pct": sa.Column("index_pct", sa.Float(), nullable=True),
    "alpha": sa.Column("alpha", sa.Float(), nullable=True),
    "ai_score": sa.Column("ai_score", sa.Integer(), nullable=True),
    "ai_reason": sa.Column("ai_reason", sa.String(), nullable=True),
    "prediction_result": sa.Column("prediction_result", sa.String(), nullable=True),
    "review_status": sa.Column("review_status", sa.String(), nullable=True, server_default="PENDING"),
}


def _get_params_snapshot_col() -> sa.Column:
    return sa.Column("params_snapshot", _json_type(), nullable=True)


LEGACY_QFQ_COLS = ("qfq_open", "qfq_high", "qfq_low", "qfq_close")


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
        if not _index_exists(table_name, index_name):
            op.create_index(index_name, table_name, [column])


def _create_all_tables_fresh() -> None:
    """Create all tables for a brand-new database (original DDL)."""
    op.create_table(
        "block_trade",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("vol", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("buyer", sa.String(), nullable=False),
        sa.Column("seller", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", "buyer", "seller", name=op.f("pk_block_trade")),
    )
    op.create_table(
        "daily_indicators",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("pe", sa.Float(), nullable=True),
        sa.Column("pe_ttm", sa.Float(), nullable=True),
        sa.Column("pb", sa.Float(), nullable=True),
        sa.Column("ps", sa.Float(), nullable=True),
        sa.Column("ps_ttm", sa.Float(), nullable=True),
        sa.Column("dv_ratio", sa.Float(), nullable=True),
        sa.Column("dv_ttm", sa.Float(), nullable=True),
        sa.Column("total_mv", sa.Float(), nullable=True),
        sa.Column("circ_mv", sa.Float(), nullable=True),
        sa.Column("total_share", sa.Float(), nullable=True),
        sa.Column("float_share", sa.Float(), nullable=True),
        sa.Column("free_share", sa.Float(), nullable=True),
        sa.Column("turnover_rate", sa.Float(), nullable=True),
        sa.Column("turnover_rate_f", sa.Float(), nullable=True),
        sa.Column("volume_ratio", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_daily_indicators")),
    )
    op.create_index(
        "ix_daily_indicators_date_code",
        "daily_indicators",
        ["trade_date", "ts_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_daily_indicators_trade_date"),
        "daily_indicators",
        ["trade_date"],
        unique=False,
    )
    op.create_table(
        "daily_quotes",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("pre_close", sa.Float(), nullable=True),
        sa.Column("change", sa.Float(), nullable=True),
        sa.Column("pct_chg", sa.Float(), nullable=True),
        sa.Column("vol", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("adj_factor", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_daily_quotes")),
    )
    op.create_index(
        "ix_daily_quotes_date_code",
        "daily_quotes",
        ["trade_date", "ts_code"],
        unique=False,
    )
    op.create_index(op.f("ix_daily_quotes_trade_date"), "daily_quotes", ["trade_date"], unique=False)
    op.create_table(
        "dividend",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=False),
        sa.Column("div_proc", sa.String(), nullable=True),
        sa.Column("stk_div", sa.Float(), nullable=True),
        sa.Column("stk_bo_rate", sa.Float(), nullable=True),
        sa.Column("stk_co_rate", sa.Float(), nullable=True),
        sa.Column("cash_div", sa.Float(), nullable=True),
        sa.Column("cash_div_tax", sa.Float(), nullable=True),
        sa.Column("record_date", sa.Date(), nullable=True),
        sa.Column("ex_date", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", "ann_date", name=op.f("pk_dividend")),
    )
    op.create_index(op.f("ix_dividend_ann_date"), "dividend", ["ann_date"], unique=False)
    op.create_table(
        "fina_audit",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("audit_result", sa.String(), nullable=True),
        sa.Column("audit_sign", sa.String(), nullable=True),
        sa.Column("audit_fees", sa.Float(), nullable=True),
        sa.Column("audit_agency", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", name=op.f("pk_fina_audit")),
    )
    op.create_table(
        "fina_forecast",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=False),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("p_change_min", sa.Float(), nullable=True),
        sa.Column("p_change_max", sa.Float(), nullable=True),
        sa.Column("net_profit_min", sa.Float(), nullable=True),
        sa.Column("net_profit_max", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", "ann_date", name=op.f("pk_fina_forecast")),
    )
    op.create_index(op.f("ix_fina_forecast_ann_date"), "fina_forecast", ["ann_date"], unique=False)
    op.create_table(
        "fina_mainbz",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("bz_item", sa.String(), nullable=False),
        sa.Column("bz_sales", sa.Float(), nullable=True),
        sa.Column("bz_profit", sa.Float(), nullable=True),
        sa.Column("bz_cost", sa.Float(), nullable=True),
        sa.Column("curr_type", sa.String(), nullable=True),
        sa.Column("update_flag", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", "bz_item", name=op.f("pk_fina_mainbz")),
    )
    op.create_index(op.f("ix_fina_mainbz_end_date"), "fina_mainbz", ["end_date"], unique=False)
    op.create_table(
        "financial_reports",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("report_type", sa.String(), nullable=True),
        sa.Column("total_revenue", sa.Float(), nullable=True),
        sa.Column("revenue", sa.Float(), nullable=True),
        sa.Column("n_income", sa.Float(), nullable=True),
        sa.Column("n_income_attr_p", sa.Float(), nullable=True),
        sa.Column("total_assets", sa.Float(), nullable=True),
        sa.Column("total_liab", sa.Float(), nullable=True),
        sa.Column("total_hldr_eqy_exc_min_int", sa.Float(), nullable=True),
        sa.Column("roe", sa.Float(), nullable=True),
        sa.Column("roe_dt", sa.Float(), nullable=True),
        sa.Column("grossprofit_margin", sa.Float(), nullable=True),
        sa.Column("netprofit_margin", sa.Float(), nullable=True),
        sa.Column("debt_to_assets", sa.Float(), nullable=True),
        sa.Column("or_yoy", sa.Float(), nullable=True),
        sa.Column("netprofit_yoy", sa.Float(), nullable=True),
        sa.Column("goodwill", sa.Float(), nullable=True),
        sa.Column("audit_result", sa.String(), nullable=True),
        sa.Column("n_cashflow_act", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", name=op.f("pk_financial_reports")),
    )
    op.create_index(
        op.f("ix_financial_reports_end_date"),
        "financial_reports",
        ["end_date"],
        unique=False,
    )
    op.create_index(
        "ix_financial_reports_ts_code_ann_date",
        "financial_reports",
        ["ts_code", "ann_date"],
        unique=False,
    )
    op.create_index(
        "ix_financial_reports_ann_date",
        "financial_reports",
        ["ann_date"],
        unique=False,
    )
    op.create_table(
        "index_daily",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("open", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("pre_close", sa.Float(), nullable=True),
        sa.Column("change", sa.Float(), nullable=True),
        sa.Column("pct_chg", sa.Float(), nullable=True),
        sa.Column("vol", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_index_daily")),
    )
    op.create_index(op.f("ix_index_daily_trade_date"), "index_daily", ["trade_date"], unique=False)
    op.create_table(
        "index_dailybasic",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("total_mv", sa.Float(), nullable=True),
        sa.Column("float_mv", sa.Float(), nullable=True),
        sa.Column("total_share", sa.Float(), nullable=True),
        sa.Column("float_share", sa.Float(), nullable=True),
        sa.Column("free_share", sa.Float(), nullable=True),
        sa.Column("turnover_rate", sa.Float(), nullable=True),
        sa.Column("turnover_rate_f", sa.Float(), nullable=True),
        sa.Column("pe", sa.Float(), nullable=True),
        sa.Column("pe_ttm", sa.Float(), nullable=True),
        sa.Column("pb", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_index_dailybasic")),
    )
    op.create_table(
        "index_weight",
        sa.Column("index_code", sa.String(), nullable=False),
        sa.Column("con_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("index_code", "con_code", "trade_date", name=op.f("pk_index_weight")),
    )
    op.create_index(op.f("ix_index_weight_trade_date"), "index_weight", ["trade_date"], unique=False)
    op.create_table(
        "limit_list",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("pct_chg", sa.Float(), nullable=True),
        sa.Column("amp", sa.Float(), nullable=True),
        sa.Column("fc_ratio", sa.Float(), nullable=True),
        sa.Column("fl_ratio", sa.Float(), nullable=True),
        sa.Column("fd_amount", sa.Float(), nullable=True),
        sa.Column("first_time", sa.String(), nullable=True),
        sa.Column("last_time", sa.String(), nullable=True),
        sa.Column("open_times", sa.Integer(), nullable=True),
        sa.Column("strth", sa.Float(), nullable=True),
        sa.Column("limit", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name=op.f("pk_limit_list")),
    )
    op.create_index(op.f("ix_limit_list_ts_code"), "limit_list", ["ts_code"], unique=False)
    op.create_table(
        "macro_economy",
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("m2", sa.Float(), nullable=True),
        sa.Column("m2_yoy", sa.Float(), nullable=True),
        sa.Column("m1", sa.Float(), nullable=True),
        sa.Column("m1_yoy", sa.Float(), nullable=True),
        sa.Column("m0", sa.Float(), nullable=True),
        sa.Column("m0_yoy", sa.Float(), nullable=True),
        sa.Column("cpi", sa.Float(), nullable=True),
        sa.Column("ppi", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("period", name=op.f("pk_macro_economy")),
    )
    op.create_table(
        "margin_daily",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("rzye", sa.Float(), nullable=True),
        sa.Column("rqye", sa.Float(), nullable=True),
        sa.Column("rzmre", sa.Float(), nullable=True),
        sa.Column("rqyl", sa.Float(), nullable=True),
        sa.Column("rzrqye", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_margin_daily")),
    )
    op.create_index(op.f("ix_margin_daily_trade_date"), "margin_daily", ["trade_date"], unique=False)
    op.create_table(
        "market_news",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("content", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("tags", sa.String(), nullable=True),
        sa.Column("publish_time", sa.DateTime(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_news")),
        sa.UniqueConstraint("content_hash", "publish_time", name="uq_market_news_hash_pub"),
    )
    op.create_index(op.f("ix_market_news_publish_time"), "market_news", ["publish_time"], unique=False)
    op.create_index(op.f("ix_market_news_source"), "market_news", ["source"], unique=False)
    op.create_table(
        "moneyflow_daily",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("buy_sm_vol", sa.BigInteger(), nullable=True),
        sa.Column("buy_sm_amount", sa.Float(), nullable=True),
        sa.Column("sell_sm_vol", sa.BigInteger(), nullable=True),
        sa.Column("sell_sm_amount", sa.Float(), nullable=True),
        sa.Column("buy_md_vol", sa.BigInteger(), nullable=True),
        sa.Column("buy_md_amount", sa.Float(), nullable=True),
        sa.Column("sell_md_vol", sa.BigInteger(), nullable=True),
        sa.Column("sell_md_amount", sa.Float(), nullable=True),
        sa.Column("buy_lg_vol", sa.BigInteger(), nullable=True),
        sa.Column("buy_lg_amount", sa.Float(), nullable=True),
        sa.Column("sell_lg_vol", sa.BigInteger(), nullable=True),
        sa.Column("sell_lg_amount", sa.Float(), nullable=True),
        sa.Column("buy_elg_vol", sa.BigInteger(), nullable=True),
        sa.Column("buy_elg_amount", sa.Float(), nullable=True),
        sa.Column("sell_elg_vol", sa.BigInteger(), nullable=True),
        sa.Column("sell_elg_amount", sa.Float(), nullable=True),
        sa.Column("net_mf_vol", sa.BigInteger(), nullable=True),
        sa.Column("net_mf_amount", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_moneyflow_daily")),
    )
    op.create_index(
        "ix_moneyflow_daily_date_code",
        "moneyflow_daily",
        ["trade_date", "ts_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_moneyflow_daily_trade_date"),
        "moneyflow_daily",
        ["trade_date"],
        unique=False,
    )
    op.create_table(
        "moneyflow_hsgt",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ggt_ss", sa.Float(), nullable=True),
        sa.Column("ggt_sz", sa.Float(), nullable=True),
        sa.Column("hgt", sa.Float(), nullable=True),
        sa.Column("sgt", sa.Float(), nullable=True),
        sa.Column("north_money", sa.Float(), nullable=True),
        sa.Column("south_money", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("trade_date", name=op.f("pk_moneyflow_hsgt")),
    )
    op.create_table(
        "northbound_holding",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("vol", sa.BigInteger(), nullable=True),
        sa.Column("ratio", sa.Float(), nullable=True),
        sa.Column("exchange", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_northbound_holding")),
    )
    op.create_index(
        op.f("ix_northbound_holding_trade_date"),
        "northbound_holding",
        ["trade_date"],
        unique=False,
    )
    op.create_table(
        "pledge_stat",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("pledge_count", sa.Integer(), nullable=True),
        sa.Column("unrest_pledge", sa.Float(), nullable=True),
        sa.Column("rest_pledge", sa.Float(), nullable=True),
        sa.Column("total_share", sa.Float(), nullable=True),
        sa.Column("pledge_ratio", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", name=op.f("pk_pledge_stat")),
    )
    op.create_table(
        "repurchase",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("proc", sa.String(), nullable=True),
        sa.Column("exp_date", sa.Date(), nullable=True),
        sa.Column("vol", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("high_limit", sa.Float(), nullable=True),
        sa.Column("low_limit", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "ann_date", name=op.f("pk_repurchase")),
    )
    op.create_index(op.f("ix_repurchase_ann_date"), "repurchase", ["ann_date"], unique=False)
    op.create_table(
        "screening_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("strategy_name", sa.String(), nullable=False),
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("pct_chg", sa.Float(), nullable=True),
        sa.Column("industry", sa.String(), nullable=True),
        sa.Column("vol", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("turnover_rate", sa.Float(), nullable=True),
        sa.Column("pe_ttm", sa.Float(), nullable=True),
        sa.Column("pb", sa.Float(), nullable=True),
        sa.Column("ps_ttm", sa.Float(), nullable=True),
        sa.Column("dv_ttm", sa.Float(), nullable=True),
        sa.Column("total_mv", sa.Float(), nullable=True),
        sa.Column("circ_mv", sa.Float(), nullable=True),
        sa.Column("roe", sa.Float(), nullable=True),
        sa.Column("grossprofit_margin", sa.Float(), nullable=True),
        sa.Column("debt_to_assets", sa.Float(), nullable=True),
        sa.Column("or_yoy", sa.Float(), nullable=True),
        sa.Column("netprofit_yoy", sa.Float(), nullable=True),
        sa.Column("t1_price", sa.Float(), nullable=True),
        sa.Column("t1_pct", sa.Float(), nullable=True),
        sa.Column("t5_price", sa.Float(), nullable=True),
        sa.Column("t5_pct", sa.Float(), nullable=True),
        sa.Column("index_pct", sa.Float(), nullable=True),
        sa.Column("alpha", sa.Float(), nullable=True),
        sa.Column("ai_score", sa.Integer(), nullable=True),
        sa.Column("ai_reason", sa.String(), nullable=True),
        sa.Column("prediction_result", sa.String(), nullable=True),
        sa.Column("review_status", sa.String(), nullable=True, server_default="PENDING"),
        sa.Column("params_snapshot", _json_type(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_screening_history")),
        sa.UniqueConstraint(
            "run_id",
            "ts_code",
            name="uq_screening_history_run_code",
        ),
    )
    op.create_index(
        "idx_sh_date_strategy",
        "screening_history",
        ["trade_date", "strategy_name"],
        unique=False,
    )
    op.create_index(
        "idx_sh_date_code",
        "screening_history",
        ["trade_date", "ts_code"],
        unique=False,
    )
    op.create_index(
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
        "review_status IN ('PENDING', 'T1_DONE') OR review_status IS NULL",
    )
    if _is_postgresql():
        op.execute("CREATE INDEX IF NOT EXISTS idx_sh_params_gin ON screening_history USING gin (params_snapshot)")
    op.create_table(
        "screening_thinking",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("history_id", sa.Integer(), nullable=False),
        sa.Column("thinking", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_st_history_id", "screening_thinking", ["history_id"])
    op.create_table(
        "shibor_daily",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("on", sa.Float(), nullable=True),
        sa.Column("1w", sa.Float(), nullable=True),
        sa.Column("2w", sa.Float(), nullable=True),
        sa.Column("1m", sa.Float(), nullable=True),
        sa.Column("3m", sa.Float(), nullable=True),
        sa.Column("6m", sa.Float(), nullable=True),
        sa.Column("9m", sa.Float(), nullable=True),
        sa.Column("1y", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("date", name=op.f("pk_shibor_daily")),
    )
    op.create_table(
        "stk_holdernumber",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("holder_num", sa.Integer(), nullable=True),
        sa.Column("holder_num_change", sa.Float(), nullable=True),
        sa.Column("holder_num_ratio", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", name=op.f("pk_stk_holdernumber")),
    )
    op.create_index(
        op.f("ix_stk_holdernumber_end_date"),
        "stk_holdernumber",
        ["end_date"],
        unique=False,
    )
    op.create_table(
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
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", name=op.f("pk_stock_basic")),
    )
    op.create_index(op.f("ix_stock_basic_list_date"), "stock_basic", ["list_date"], unique=False)
    op.create_index("idx_stock_basic_delist_date", "stock_basic", ["delist_date"], unique=False)
    op.create_index("idx_stock_basic_dates", "stock_basic", ["list_date", "delist_date"], unique=False)
    op.create_index("idx_stock_basic_status", "stock_basic", ["list_status", "list_date"], unique=False)
    op.create_table(
        "stock_concepts",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("concept_name", sa.String(), nullable=True),
        sa.Column("concept_id", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "concept_id", name=op.f("pk_stock_concepts")),
    )
    op.create_index(op.f("ix_stock_concepts_ts_code"), "stock_concepts", ["ts_code"], unique=False)
    op.create_table(
        "stock_sync_status",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("step4_completed_at", sa.DateTime(), nullable=True),
        sa.Column("sync_version", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", name=op.f("pk_stock_sync_status")),
    )
    op.create_table(
        "suspend_d",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("suspend_timing", sa.String(), nullable=True),
        sa.Column("suspend_type", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", name=op.f("pk_suspend_d")),
    )
    op.create_index(op.f("ix_suspend_d_trade_date"), "suspend_d", ["trade_date"], unique=False)
    op.create_table(
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
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("table_name", name=op.f("pk_sync_status")),
    )
    op.create_table(
        "task_history",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("task_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("progress", sa.Float(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("result", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_history")),
    )
    op.create_index(op.f("ix_task_history_created_at"), "task_history", ["created_at"], unique=False)
    op.create_table(
        "top10_holders",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("holder_name", sa.String(), nullable=False),
        sa.Column("hold_amount", sa.Float(), nullable=True),
        sa.Column("hold_ratio", sa.Float(), nullable=True),
        sa.Column("hold_float_ratio", sa.Float(), nullable=True),
        sa.Column("hold_change", sa.Float(), nullable=True),
        sa.Column("holder_type", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", "end_date", "holder_name", name=op.f("pk_top10_holders")),
    )
    op.create_index(
        op.f("ix_top10_holders_holder_name"),
        "top10_holders",
        ["holder_name"],
        unique=False,
    )
    op.create_table(
        "top_list",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("pct_change", sa.Float(), nullable=True),
        sa.Column("turnover_rate", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("l_sell", sa.Float(), nullable=True),
        sa.Column("l_buy", sa.Float(), nullable=True),
        sa.Column("l_amount", sa.Float(), nullable=True),
        sa.Column("net_amount", sa.Float(), nullable=True),
        sa.Column("net_rate", sa.Float(), nullable=True),
        sa.Column("amount_rate", sa.Float(), nullable=True),
        sa.Column("float_values", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("trade_date", "ts_code", name=op.f("pk_top_list")),
    )
    op.create_index(op.f("ix_top_list_ts_code"), "top_list", ["ts_code"], unique=False)
    op.create_table(
        "trade_cal",
        sa.Column("cal_date", sa.Date(), nullable=False),
        sa.Column("exchange", sa.String(), nullable=True),
        sa.Column("is_open", sa.Integer(), nullable=True),
        sa.Column("pretrade_date", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("cal_date", name=op.f("pk_trade_cal")),
    )
    # ### end Alembic commands ###


def _create_table_screening_history() -> None:
    """Create screening_history and screening_thinking tables (for legacy DBs missing them)."""
    op.create_table(
        "screening_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("strategy_name", sa.String(), nullable=False),
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("pct_chg", sa.Float(), nullable=True),
        sa.Column("industry", sa.String(), nullable=True),
        sa.Column("vol", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("turnover_rate", sa.Float(), nullable=True),
        sa.Column("pe_ttm", sa.Float(), nullable=True),
        sa.Column("pb", sa.Float(), nullable=True),
        sa.Column("ps_ttm", sa.Float(), nullable=True),
        sa.Column("dv_ttm", sa.Float(), nullable=True),
        sa.Column("total_mv", sa.Float(), nullable=True),
        sa.Column("circ_mv", sa.Float(), nullable=True),
        sa.Column("roe", sa.Float(), nullable=True),
        sa.Column("grossprofit_margin", sa.Float(), nullable=True),
        sa.Column("debt_to_assets", sa.Float(), nullable=True),
        sa.Column("or_yoy", sa.Float(), nullable=True),
        sa.Column("netprofit_yoy", sa.Float(), nullable=True),
        sa.Column("t1_price", sa.Float(), nullable=True),
        sa.Column("t1_pct", sa.Float(), nullable=True),
        sa.Column("t5_price", sa.Float(), nullable=True),
        sa.Column("t5_pct", sa.Float(), nullable=True),
        sa.Column("index_pct", sa.Float(), nullable=True),
        sa.Column("alpha", sa.Float(), nullable=True),
        sa.Column("ai_score", sa.Integer(), nullable=True),
        sa.Column("ai_reason", sa.String(), nullable=True),
        sa.Column("prediction_result", sa.String(), nullable=True),
        sa.Column("review_status", sa.String(), nullable=True, server_default="PENDING"),
        sa.Column("params_snapshot", _json_type(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_screening_history")),
        sa.UniqueConstraint(
            "run_id",
            "ts_code",
            name="uq_screening_history_run_code",
        ),
    )
    op.create_index("idx_sh_date_strategy", "screening_history", ["trade_date", "strategy_name"], unique=False)
    op.create_index("idx_sh_date_code", "screening_history", ["trade_date", "ts_code"], unique=False)
    op.create_index("idx_sh_run_id", "screening_history", ["run_id"], unique=False)
    _create_partial_index(
        "screening_history", "idx_sh_prediction_result", "prediction_result", "prediction_result IS NOT NULL"
    )
    _create_partial_index(
        "screening_history",
        "idx_sh_pending",
        "review_status",
        "review_status IN ('PENDING', 'T1_DONE') OR review_status IS NULL",
    )
    if _is_postgresql():
        op.execute("CREATE INDEX IF NOT EXISTS idx_sh_params_gin ON screening_history USING gin (params_snapshot)")
    _create_table_screening_thinking()


def _create_table_screening_thinking() -> None:
    """Create screening_thinking table."""
    if not _table_exists("screening_thinking"):
        op.create_table(
            "screening_thinking",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("history_id", sa.Integer(), nullable=False),
            sa.Column("thinking", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("idx_st_history_id", "screening_thinking", ["history_id"])


_ALL_EXPECTED_TABLES = [
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
    """Idempotent upgrade: works for both fresh DBs and pre-fix legacy DBs."""
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not _table_exists("daily_quotes"):
        _create_all_tables_fresh()
        return

    existing_tables = set(insp.get_table_names(schema=_target_schema()))
    missing_tables = [t for t in _ALL_EXPECTED_TABLES if t not in existing_tables]
    if missing_tables:
        import logging as _logging

        _logging.getLogger("alembic.runtime.migration").warning(
            "Legacy DB has daily_quotes but missing tables: %s. "
            "Consider running 'alembic stamp head' on a fully-initialized DB, "
            "or create a fresh DB to avoid schema drift.",
            missing_tables,
        )

    existing_dq = {c["name"] for c in insp.get_columns("daily_quotes")}
    for legacy in LEGACY_QFQ_COLS:
        if legacy in existing_dq:
            op.drop_column("daily_quotes", legacy)

    if _table_exists("screening_history"):
        existing_sh = {c["name"] for c in insp.get_columns("screening_history")}
        for col_name, sa_col in NEW_SH_COLUMNS.items():
            if col_name not in existing_sh:
                op.add_column("screening_history", sa_col)
        if "params_snapshot" not in existing_sh:
            op.add_column("screening_history", _get_params_snapshot_col())
        if not _index_exists("screening_history", "idx_sh_pending"):
            _create_partial_index(
                "screening_history",
                "idx_sh_pending",
                "review_status",
                "review_status IN ('PENDING', 'T1_DONE') OR review_status IS NULL",
            )
        if not _index_exists("screening_history", "idx_sh_prediction_result"):
            _create_partial_index(
                "screening_history", "idx_sh_prediction_result", "prediction_result", "prediction_result IS NOT NULL"
            )
        if _is_postgresql() and not _index_exists("screening_history", "idx_sh_params_gin"):
            op.execute("CREATE INDEX IF NOT EXISTS idx_sh_params_gin ON screening_history USING gin (params_snapshot)")
    else:
        _create_table_screening_history()

    _create_table_screening_thinking()


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("trade_cal")
    op.drop_index(op.f("ix_top_list_ts_code"), table_name="top_list")
    op.drop_table("top_list")
    op.drop_index(op.f("ix_top10_holders_holder_name"), table_name="top10_holders")
    op.drop_table("top10_holders")
    op.drop_index(op.f("ix_task_history_created_at"), table_name="task_history")
    op.drop_table("task_history")
    op.drop_table("sync_status")
    op.drop_index(op.f("ix_suspend_d_trade_date"), table_name="suspend_d")
    op.drop_table("suspend_d")
    op.drop_table("stock_sync_status")
    op.drop_index(op.f("ix_stock_concepts_ts_code"), table_name="stock_concepts")
    op.drop_table("stock_concepts")
    op.drop_index("idx_stock_basic_status", table_name="stock_basic")
    op.drop_index("idx_stock_basic_dates", table_name="stock_basic")
    op.drop_index("idx_stock_basic_delist_date", table_name="stock_basic")
    op.drop_index(op.f("ix_stock_basic_list_date"), table_name="stock_basic")
    op.drop_table("stock_basic")
    op.drop_index(op.f("ix_stk_holdernumber_end_date"), table_name="stk_holdernumber")
    op.drop_table("stk_holdernumber")
    op.drop_table("shibor_daily")
    op.drop_index("idx_sh_run_id", table_name="screening_history")
    op.drop_index("idx_sh_date_code", table_name="screening_history")
    op.drop_index("idx_sh_date_strategy", table_name="screening_history")
    op.drop_table("screening_history")
    op.drop_index("idx_st_history_id", table_name="screening_thinking")
    op.drop_table("screening_thinking")
    op.drop_index(op.f("ix_repurchase_ann_date"), table_name="repurchase")
    op.drop_table("repurchase")
    op.drop_table("pledge_stat")
    op.drop_index(op.f("ix_northbound_holding_trade_date"), table_name="northbound_holding")
    op.drop_table("northbound_holding")
    op.drop_table("moneyflow_hsgt")
    op.drop_index(op.f("ix_moneyflow_daily_trade_date"), table_name="moneyflow_daily")
    op.drop_index("ix_moneyflow_daily_date_code", table_name="moneyflow_daily")
    op.drop_table("moneyflow_daily")
    op.drop_table("market_news")
    op.drop_index(op.f("ix_margin_daily_trade_date"), table_name="margin_daily")
    op.drop_table("margin_daily")
    op.drop_table("macro_economy")
    op.drop_index(op.f("ix_limit_list_ts_code"), table_name="limit_list")
    op.drop_table("limit_list")
    op.drop_index(op.f("ix_index_weight_trade_date"), table_name="index_weight")
    op.drop_table("index_weight")
    op.drop_table("index_dailybasic")
    op.drop_index(op.f("ix_index_daily_trade_date"), table_name="index_daily")
    op.drop_table("index_daily")
    op.drop_index("ix_financial_reports_ann_date", table_name="financial_reports")
    op.drop_index("ix_financial_reports_ts_code_ann_date", table_name="financial_reports")
    op.drop_index(op.f("ix_financial_reports_end_date"), table_name="financial_reports")
    op.drop_table("financial_reports")
    op.drop_index(op.f("ix_fina_mainbz_end_date"), table_name="fina_mainbz")
    op.drop_table("fina_mainbz")
    op.drop_index(op.f("ix_fina_forecast_ann_date"), table_name="fina_forecast")
    op.drop_table("fina_forecast")
    op.drop_table("fina_audit")
    op.drop_index(op.f("ix_dividend_ann_date"), table_name="dividend")
    op.drop_table("dividend")
    op.drop_index(op.f("ix_daily_quotes_trade_date"), table_name="daily_quotes")
    op.drop_index("ix_daily_quotes_date_code", table_name="daily_quotes")
    op.drop_table("daily_quotes")
    op.drop_index(op.f("ix_daily_indicators_trade_date"), table_name="daily_indicators")
    op.drop_index("ix_daily_indicators_date_code", table_name="daily_indicators")
    op.drop_table("daily_indicators")
    op.drop_table("block_trade")
    # ### end Alembic commands ###
