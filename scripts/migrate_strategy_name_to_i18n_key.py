"""Migrate historical strategy_name values in DB to i18n keys.

Background (Phase R.3.2): screening_history.strategy_name historically stored
mixed formats — identifiers ("AI_Auto_Nightly"), zh_CN translations ("价值投资"),
and en_US translations ("Value Investing"). New code (R.3.1) writes the i18n
key directly ("strategy_value_name"). This script backfills historical rows so
all stored values become i18n keys.

Usage:
    # Preview changes (default, no DB writes)
    python scripts/migrate_strategy_name_to_i18n_key.py --dry-run

    # Execute migration
    python scripts/migrate_strategy_name_to_i18n_key.py --execute

    # Override DB URL (defaults to $DATABASE_URL)
    python scripts/migrate_strategy_name_to_i18n_key.py --execute --db-url "postgresql://user:pwd@host:5432/db"

Note: scripts/ cannot import data/cache or ui/i18n (architectural boundary).
The _STRATEGY_NAME_MAP below is a hard-coded copy synced from
ui/i18n.py:_STRATEGY_NAME_MAP. After R.3.3 lands (translate_strategy_name
simplification), ui/i18n.py will drop its original table; this copy remains
as the standalone historical migration tool.

Scope: screening_history table only. backtest_results.strategy_name is tracked
as a separate out-of-scope item (see Plans.md Phase R.3 范窗外项登记).
"""

import argparse
import asyncio
import os
import sys

import asyncpg

# Synced copy from ui/i18n.py:_STRATEGY_NAME_MAP (29 entries: 1 identifier + 13 zh + 15 en).
# Do NOT import from ui/i18n.py — scripts/ must not pull in flet runtime side effects.
_STRATEGY_NAME_MAP: dict[str, str] = {
    "AI_Auto_Nightly": "strategy_ai_nightly_name",
    "AI 自动夜间选股": "strategy_ai_nightly_name",
    "AI Auto Nightly Screening": "strategy_ai_nightly_name",
    "AI 深度精选 (Beta)": "strategy_ai_active_name",
    "AI Deep Dive (Beta)": "strategy_ai_active_name",
    "价值投资": "strategy_value_name",
    "Value Investing": "strategy_value_name",
    "高成长策略": "strategy_growth_name",
    "High Growth": "strategy_growth_name",
    "高股息策略": "strategy_dividend_name",
    "High Dividend": "strategy_dividend_name",
    "放量突破": "strategy_volume_breakout_name",
    "Volume Breakout": "strategy_volume_breakout_name",
    "北向持股": "strategy_northbound_holding_name",
    "Northbound Holdings": "strategy_northbound_holding_name",
    "北向净流入": "strategy_northbound_flow_name",
    "Northbound Net Inflow": "strategy_northbound_flow_name",
    "超跌反弹": "strategy_oversold_name",
    "Oversold Rebound": "strategy_oversold_name",
    "龙虎榜机构": "strategy_institutional_name",
    "Institutional Hits": "strategy_institutional_name",
    "筹码集中 (暂不可用)": "strategy_chips_name",
    "Chip Concentration (N/A)": "strategy_chips_name",
    "大宗交易": "strategy_block_trade_name",
    "Block Trades": "strategy_block_trade_name",
    "现金流优质": "strategy_cashflow_name",
    "Quality Cashflow": "strategy_cashflow_name",
    "大盘低估": "strategy_large_pe_name",
    "Large Cap Low PE": "strategy_large_pe_name",
}


def migrate_strategy_name(name: str | None) -> str | None:
    """Map a historical strategy_name value to its i18n key.

    Idempotent: values already starting with "strategy_" are returned as-is.
    Unknown values are returned unchanged (caller should log a warning).

    Args:
        name: Raw strategy_name from DB. None/empty passthrough.

    Returns:
        i18n key (e.g. "strategy_value_name") or original value if not mappable.
    """
    if not name:
        return name
    if name.startswith("strategy_"):
        return name
    return _STRATEGY_NAME_MAP.get(name, name)


def _normalize_dsn(db_url: str) -> str:
    """Convert SQLAlchemy-style URL to asyncpg-native DSN.

    asyncpg.connect(dsn=...) expects "postgresql://..." not "postgresql+asyncpg://...".
    """
    if db_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + db_url[len("postgresql+asyncpg://") :]
    return db_url


async def migrate(dry_run: bool, db_url: str) -> dict:
    """Run the migration against screening_history.strategy_name.

    Args:
        dry_run: True to preview without writing; False to execute UPDATEs.
        db_url: PostgreSQL connection URL (postgresql:// or postgresql+asyncpg://).

    Returns:
        Stats dict: {scanned, migrated, skipped_already_i18n_key, unknown: [{name, count}]}.
    """
    dsn = _normalize_dsn(db_url)
    stats: dict = {
        "scanned": 0,
        "migrated": 0,
        "skipped_already_i18n_key": 0,
        "unknown": [],
    }

    conn = await asyncpg.connect(dsn=dsn)
    try:
        rows = await conn.fetch("SELECT strategy_name, COUNT(*) AS cnt FROM screening_history GROUP BY strategy_name")
        stats["scanned"] = len(rows)

        for row in rows:
            old = row["strategy_name"]
            count = int(row["cnt"])

            if old is None or old == "":
                continue
            if old.startswith("strategy_"):
                stats["skipped_already_i18n_key"] += 1
                continue

            new = _STRATEGY_NAME_MAP.get(old)
            if new is None:
                stats["unknown"].append({"name": old, "count": count})
                print(f"[WARN] Unmapped strategy_name: {old!r} (count={count})")
                continue

            if dry_run:
                print(f"[DRY-RUN] Would update: {old!r} -> {new!r} (count={count})")
            else:
                # R4 SQL injection: use $1/$2 placeholders (never %s).
                await conn.execute(
                    "UPDATE screening_history SET strategy_name = $1 WHERE strategy_name = $2",
                    new,
                    old,
                )
                print(f"[EXEC] Updated: {old!r} -> {new!r} (count={count})")
            stats["migrated"] += 1
    finally:
        await conn.close()

    return stats


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Migrate screening_history.strategy_name to i18n keys.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview without writing (default, no-op flag; pass --execute to write).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Execute migration (overrides --dry-run).",
    )
    parser.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL URL (defaults to $DATABASE_URL).",
    )
    args = parser.parse_args()

    dry_run = not args.execute
    db_url = args.db_url

    if not db_url:
        print("ERROR: DATABASE_URL env var required or pass --db-url", file=sys.stderr)
        sys.exit(1)

    mode = "DRY-RUN" if dry_run else "EXECUTE"
    print(f"=== migrate_strategy_name_to_i18n_key [{mode}] ===")
    print(f"DB URL: {db_url}")
    print()

    stats = asyncio.run(migrate(dry_run=dry_run, db_url=db_url))

    print()
    print("=== Migration summary ===")
    print(f"  distinct values scanned:     {stats['scanned']}")
    print(f"  migrated (or to migrate):    {stats['migrated']}")
    print(f"  skipped (already i18n key):  {stats['skipped_already_i18n_key']}")
    print(f"  unknown (unmapped):          {len(stats['unknown'])}")
    for u in stats["unknown"]:
        print(f"    - {u['name']!r} (count={u['count']})")


if __name__ == "__main__":
    main()
