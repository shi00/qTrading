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

Schema / collision policy (D04-F05):
- Supported writable revisions: revision 0033 or later only — the four-key
  unique constraint ``(trade_date, strategy_name, ts_code, run_id)`` must be
  active. The current revision is read from ``alembic_version``; a missing
  table/row, an unknown revision, or any revision before 0033 makes
  ``--execute`` fail closed (non-zero exit) with a hint to run
  ``alembic upgrade head`` first. Upgrading to head is recommended because the
  support window is ">= 0033" and this script never runs Alembic itself.
- Collision precheck: every row's normalized key is computed against the target
  revision's unique key (head / revision >= 0033:
  ``(trade_date, strategy_name, ts_code, run_id)``; legacy revision 0024-0032:
  ``(trade_date, strategy_name, ts_code)``). If any group collides after
  normalization, the script reports the group and aborts without writing
  (non-zero exit), including in dry-run. It never merges the colliding research
  runs: both runs are preserved and manual resolution (keep/rename/delete after
  review) is required.
- ``--execute`` wraps all UPDATEs in a single transaction; any failure rolls the
  whole batch back (no half-applied migration). Concurrency is ultimately
  guarded by the database unique constraint plus this single transaction; the
  precheck itself is a snapshot and may not observe rows committed after it.
- Schema drift (e.g. ``alembic_version`` says 0033 but the constraint is absent)
  is not detected here; run ``alembic check`` before executing.

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
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402 - sys.path 注入后导入

from utils.sanitizers import DataSanitizer  # noqa: E402 - sys.path 注入后导入

# Revision that introduced the four-key unique constraint (0033_add_run_id_to_screening_uk).
# Only revisions at or after this one are writable; earlier revisions (0024-0032) use
# the legacy three-key unique constraint and must be upgraded first.
_MIN_SUPPORTED_REVISION = "0033"
# First revision with the legacy three-key constraint (0024_screening_history_override_pk).
# Used only to describe collisions during dry-run on an unsupported revision.
_LEGACY_UK_MIN_REVISION = "0024"

# Synced copy from ui/i18n.py:_STRATEGY_NAME_MAP (29 entries: 1 identifier + 14 zh + 14 en).
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


def revision_is_supported(
    current_revision: str | None,
    revision_parents: Mapping[str, Sequence[str] | str | None],
    min_revision: str = _MIN_SUPPORTED_REVISION,
) -> bool:
    """Return True when ``current_revision`` is ``min_revision`` or its descendant.

    Walks the Alembic revision ancestry instead of comparing revision strings
    numerically/lexicographically, so non-numeric or future revision ids fail
    closed. A revision missing from ``revision_parents`` (unknown to this checkout)
    is treated as unsupported.
    """
    if not current_revision:
        return False
    seen: set[str] = set()
    pending: list[str] = [current_revision]
    while pending:
        revision = pending.pop()
        if revision in seen:
            continue
        seen.add(revision)
        if revision == min_revision:
            return True
        parents = revision_parents.get(revision)
        if parents is None:
            continue
        if isinstance(parents, str):
            pending.append(parents)
        else:
            pending.extend(parents)
    return False


def _load_revision_parents() -> dict[str, tuple[str, ...]]:
    """Read the local Alembic chain as ``revision -> immediate parent(s)``."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    parents: dict[str, tuple[str, ...]] = {}
    for revision in script.walk_revisions():
        down_revision = revision.down_revision
        if down_revision is None:
            parents[revision.revision] = ()
        elif isinstance(down_revision, str):
            parents[revision.revision] = (down_revision,)
        else:
            parents[revision.revision] = tuple(down_revision)
    return parents


def _single_revision(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """Return the sole ``alembic_version`` value; None when missing or ambiguous."""
    if len(rows) != 1:
        return None
    value = rows[0].get("version_num")
    if not isinstance(value, str) or not value:
        return None
    return value


def normalized_collision_key(row: Mapping[str, Any], *, include_run_id: bool) -> tuple[Any, ...]:
    """Build the unique-key tuple a row would occupy after normalization.

    ``include_run_id`` selects the target constraint shape: True for revision
    0033+ (four-key UK), False for legacy 0024-0032 (three-key UK).
    """
    normalized_name = migrate_strategy_name(row.get("strategy_name"))
    if include_run_id:
        return (row.get("trade_date"), normalized_name, row.get("ts_code"), row.get("run_id"))
    return (row.get("trade_date"), normalized_name, row.get("ts_code"))


def find_collision_groups(rows: Iterable[Mapping[str, Any]], *, include_run_id: bool) -> list[dict[str, Any]]:
    """Group rows by their normalized unique key and return groups with collisions.

    NULL strategy_name rows are skipped because PostgreSQL UNIQUE constraints
    allow multiple NULLs (they are also never updated). Empty strings are real
    values and participate normally. A returned group contains two or more rows
    that would share the same unique key after normalization; the script must
    not merge them.
    """
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        name = row.get("strategy_name")
        if name is None:
            continue
        key = normalized_collision_key(row, include_run_id=include_run_id)
        normalized_name = migrate_strategy_name(name)
        grouped[key].append(
            {
                "id": row.get("id"),
                "trade_date": row.get("trade_date"),
                "ts_code": row.get("ts_code"),
                "run_id": row.get("run_id"),
                "strategy_name": name,
                "normalized_name": normalized_name,
                "will_update": normalized_name != name,
            }
        )

    conflicts = [{"key": key, "rows": members} for key, members in grouped.items() if len(members) > 1]
    conflicts.sort(key=lambda group: tuple(str(part) for part in group["key"]))
    return conflicts


def build_update_plan(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[list[tuple[str, str, int]], list[dict[str, Any]]]:
    """Return ``(updates, unknown)`` where updates are ``(old, new, row_count)``.

    Unknown values are preserved untouched and reported separately so the caller
    can warn; the script never guesses a key for them.
    """
    value_counts: Counter[str | None] = Counter(row.get("strategy_name") for row in rows)
    updates: list[tuple[str, str, int]] = []
    unknown: list[dict[str, Any]] = []
    for old in sorted(value_counts, key=lambda value: (value is not None, str(value))):
        count = value_counts[old]
        if old is None or old == "":
            continue
        if old.startswith("strategy_"):
            continue
        new = _STRATEGY_NAME_MAP.get(old)
        if new is None:
            unknown.append({"name": old, "count": count})
            continue
        updates.append((old, new, count))
    return updates, unknown


def _normalize_dsn(db_url: str) -> str:
    """Convert SQLAlchemy-style URL to asyncpg-native DSN.

    asyncpg.connect(dsn=...) expects "postgresql://..." not "postgresql+asyncpg://...".
    """
    if db_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + db_url[len("postgresql+asyncpg://") :]
    return db_url


async def _read_current_revision(conn: asyncpg.Connection) -> str | None:
    """Read the Alembic revision; missing table / missing row / multiple rows => None."""
    try:
        rows = await conn.fetch("SELECT version_num FROM alembic_version")
    except asyncpg.UndefinedTableError:
        return None
    return _single_revision([dict(row) for row in rows])


async def migrate(dry_run: bool, db_url: str) -> dict:
    """Run the migration against screening_history.strategy_name.

    Args:
        dry_run: True to preview without writing; False to execute UPDATEs.
        db_url: PostgreSQL connection URL (postgresql:// or postgresql+asyncpg://).

    Returns:
        Stats dict with revision/support, collision groups, would-update counts,
        unknown values, and whether UPDATEs were executed.
    """
    dsn = _normalize_dsn(db_url)
    revision_parents = _load_revision_parents()
    stats: dict = {
        "revision": None,
        "revision_supported": False,
        "scanned": 0,
        "migrated": 0,
        "rows_to_update": 0,
        "skipped_already_i18n_key": 0,
        "unknown": [],
        "conflicts": [],
        "conflict_key_scope": None,
        "executed": False,
    }

    conn = await asyncpg.connect(dsn=dsn)
    try:
        current_revision = await _read_current_revision(conn)
        supported = revision_is_supported(current_revision, revision_parents)
        stats["revision"] = current_revision
        stats["revision_supported"] = supported
        print(
            f"[INFO] alembic revision: {current_revision or '<missing>'} "
            f"(writable: {supported}; minimum: {_MIN_SUPPORTED_REVISION})"
        )

        rows = [
            dict(row)
            for row in await conn.fetch("SELECT id, trade_date, strategy_name, ts_code, run_id FROM screening_history")
        ]
        value_counts: Counter[str | None] = Counter(row.get("strategy_name") for row in rows)
        stats["scanned"] = len(value_counts)
        stats["skipped_already_i18n_key"] = sum(
            1 for value in value_counts if isinstance(value, str) and value.startswith("strategy_")
        )

        updates, unknown = build_update_plan(rows)
        stats["migrated"] = len(updates)
        stats["rows_to_update"] = sum(count for _, _, count in updates)
        stats["unknown"] = unknown
        for item in unknown:
            print(f"[WARN] Unmapped strategy_name: {item['name']!r} (count={item['count']})")

        if supported:
            stats["conflict_key_scope"] = "head-4key"
            include_run_id = True
        elif revision_is_supported(current_revision, revision_parents, _LEGACY_UK_MIN_REVISION):
            stats["conflict_key_scope"] = "legacy-3key"
            include_run_id = False
        else:
            print(
                "[WARN] Cannot determine target unique-key shape "
                "(revision before 0024 or unknown); collision precheck skipped."
            )
            include_run_id = False

        conflicts = (
            find_collision_groups(rows, include_run_id=include_run_id)
            if stats["conflict_key_scope"] is not None
            else []
        )
        stats["conflicts"] = conflicts

        if dry_run:
            for old, new, count in updates:
                print(f"[DRY-RUN] Would update: {old!r} -> {new!r} (count={count})")
        elif not supported:
            print(
                f"[WARN] --execute refused: revision {current_revision or '<missing>'} is missing, unknown, "
                f"or before {_MIN_SUPPORTED_REVISION}; run `alembic upgrade head` first.",
                file=sys.stderr,
            )
        elif conflicts:
            print(
                "[WARN] --execute refused: normalized unique-key conflicts detected; nothing was written.",
                file=sys.stderr,
            )
        else:
            if updates:
                # R4 SQL injection: use $1/$2 placeholders (never %s).
                async with conn.transaction():
                    for old, new, _count in updates:
                        await conn.execute(
                            "UPDATE screening_history SET strategy_name = $1 WHERE strategy_name = $2",
                            new,
                            old,
                        )
                # 仅在事务成功提交后打印，避免回滚时留下“已更新”的误导日志。
                for old, new, count in updates:
                    print(f"[EXEC] Updated: {old!r} -> {new!r} (count={count})")
            stats["executed"] = True
    finally:
        await conn.close()

    return stats


def _print_conflicts(conflicts: list[dict[str, Any]]) -> None:
    """Print each colliding normalized-key group and its rows."""
    for conflict in conflicts:
        print(f"  [CONFLICT] key={conflict['key']!r}")
        for row in conflict["rows"]:
            marker = "will-update" if row["will_update"] else "unchanged"
            print(
                f"    - id={row['id']} strategy_name={row['strategy_name']!r} -> {row['normalized_name']!r} ({marker})"
            )


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
    # R9：DSN 可能含明文密码（如 postgresql://user:pwd@host/db），打印前须经 DataSanitizer 脱敏
    print(f"DB URL: {DataSanitizer.sanitize_error(db_url)}")
    print()

    stats = asyncio.run(migrate(dry_run=dry_run, db_url=db_url))

    print()
    print("=== Migration summary ===")
    print(f"  alembic revision:            {stats['revision'] or '<missing>'}")
    print(f"  writable (>= {_MIN_SUPPORTED_REVISION}):        {stats['revision_supported']}")
    print(f"  conflict key scope:          {stats['conflict_key_scope'] or '<unknown>'}")
    print(f"  distinct values scanned:     {stats['scanned']}")
    print(f"  migrated (distinct values):  {stats['migrated']}")
    print(f"  rows to update:              {stats['rows_to_update']}")
    print(f"  skipped (already i18n key):  {stats['skipped_already_i18n_key']}")
    print(f"  unknown (unmapped):          {len(stats['unknown'])}")
    for item in stats["unknown"]:
        print(f"    - {item['name']!r} (count={item['count']})")
    print(f"  normalized-key conflicts:    {len(stats['conflicts'])}")
    _print_conflicts(stats["conflicts"])

    if not stats["revision_supported"]:
        print(
            f"[HINT] Only revisions >= {_MIN_SUPPORTED_REVISION} (four-key UK) are writable; "
            "run `alembic upgrade head` before --execute."
        )
        if not dry_run:
            sys.exit(2)
    if stats["conflicts"]:
        print(
            "ERROR: normalized unique-key collisions detected; nothing was written. "
            "The script never merges research runs — resolve the colliding rows manually.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
