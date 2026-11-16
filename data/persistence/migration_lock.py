"""Cross-process mutex for Alembic schema upgrades.

``DatabaseMigrator._alembic_lock`` is a ``threading.Lock``: it only serialises
migrations started by threads of *one* process. Several processes can reach the
same database and run the same revision concurrently:

- two application instances (external PostgreSQL deployment);
- the running application plus ``python -m alembic upgrade head`` (documented
  CLI path) or ``scripts/verify_embedded_pg_connect.py``;
- E2E: the Flet app subprocess (``AUTO_MIGRATE=true``) and the pytest process
  seeding the same embedded sidecar database.

Without a shared lock both sides read the same ``alembic_version`` cursor and
replay the same revisions; the second writer then aborts on already-applied DDL
or on a duplicate ``alembic_version`` row. This module provides that shared
mutex using PostgreSQL session-level advisory locks, which are owned by the
database connection rather than by the Python process, so they also cover the
Alembic CLI path (``alembic/env.py`` acquires it before running migrations).

The lock must be acquired on the *same connection* that executes the upgrade:
session-level advisory locks are released when that connection closes. The
connection is closed by ``alembic/env.py`` after the migration, so callers do
not need to track it across processes.

Schema-safety note: PostgreSQL DDL is transactional and Alembic wraps a run in
a single transaction (``transaction_per_migration`` is left at its default
``False``), so an interrupted run rolls back instead of leaving a half-applied
schema. Migrations must therefore not switch to ``autocommit_block`` /
``CREATE INDEX CONCURRENTLY`` without revisiting this guarantee.
"""

import asyncio
import logging
import math
import os
import time
import typing

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Stable, application-specific 63-bit key ("ASTOCKMG" as ASCII). Must never be
# reused by another advisory-lock user in the same cluster.
MIGRATION_ADVISORY_LOCK_KEY: int = 0x4153544F434B4D47

# Bounded wait: migrations are exclusive but finite (whole upgrade chain on an
# empty database). Waiting forever would turn a stuck peer into a hung startup.
DEFAULT_LOCK_TIMEOUT_S: float = 600.0

LOCK_TIMEOUT_ENV_VAR = "MIGRATION_LOCK_TIMEOUT_S"

_LOCK_POLL_INTERVAL_S: float = 0.25

_ACQUIRE_SQL = text("SELECT pg_try_advisory_lock(:lock_key)")
_RELEASE_SQL = text("SELECT pg_advisory_unlock(:lock_key)")


class MigrationLockTimeoutError(RuntimeError):
    """Raised when the cross-process migration lock is not acquired in time."""

    def __init__(self, timeout_s: float):
        self.timeout_s = timeout_s
        super().__init__(
            "另一个进程正在执行数据库迁移，等待跨进程迁移锁超时"
            f"（{timeout_s:g}s）。本次启动未执行迁移，也未修改 schema。"
            f"请确认是否有其他应用实例或 alembic 命令仍在运行；"
            f"如迁移确实耗时较长，可调大环境变量 {LOCK_TIMEOUT_ENV_VAR}。"
        )


def _normalize_budget(value: float) -> float:
    # nan/inf 会让 deadline 比较永远为假（无限等待），一律回退默认值。
    if not math.isfinite(value):
        return DEFAULT_LOCK_TIMEOUT_S
    return max(value, 0.0)


def resolve_lock_timeout(timeout_s: float | None = None) -> float:
    """Resolve the lock wait budget: explicit argument > env var > default."""
    if timeout_s is not None:
        return _normalize_budget(timeout_s)
    raw = os.environ.get(LOCK_TIMEOUT_ENV_VAR, "").strip()
    if raw:
        try:
            return _normalize_budget(float(raw))
        except ValueError:
            logger.warning(
                "[MigrationLock] Invalid %s=%r, falling back to %ss.",
                LOCK_TIMEOUT_ENV_VAR,
                raw,
                DEFAULT_LOCK_TIMEOUT_S,
            )
    return DEFAULT_LOCK_TIMEOUT_S


async def acquire_migration_lock(
    connection: typing.Any,
    *,
    timeout_s: float | None = None,
    poll_interval_s: float = _LOCK_POLL_INTERVAL_S,
) -> None:
    """Acquire the cross-process migration mutex on ``connection``.

    ``pg_try_advisory_lock`` is polled instead of blocking in PostgreSQL so the
    wait stays bounded and cancellable (an interrupt propagates as
    ``CancelledError``, which closes the connection and drops any lock).

    Raises:
        MigrationLockTimeoutError: Another process held the lock for longer than
            the allowed budget. No migration was started.
    """
    budget = resolve_lock_timeout(timeout_s)
    deadline = time.monotonic() + budget
    while True:
        result = await connection.execute(_ACQUIRE_SQL, {"lock_key": MIGRATION_ADVISORY_LOCK_KEY})
        if result.scalar_one():
            logger.info("[MigrationLock] Acquired cross-process migration lock (key=%#x).", MIGRATION_ADVISORY_LOCK_KEY)
            return
        if time.monotonic() >= deadline:
            logger.error(
                "[MigrationLock] Timed out after %ss waiting for the cross-process migration lock; "
                "another process is migrating this database.",
                budget,
            )
            raise MigrationLockTimeoutError(budget)
        logger.debug(
            "[MigrationLock] Another process holds the migration lock, retrying in %ss (budget=%ss).",
            poll_interval_s,
            budget,
        )
        await asyncio.sleep(poll_interval_s)


async def release_migration_lock(connection: typing.Any) -> None:
    """Release the mutex; never mask the in-flight migration result.

    Cleanup runs in ``finally`` blocks, so a broken connection must not replace
    the original migration error. ``CancelledError`` is a ``BaseException`` and
    is intentionally not caught here (R2: cancellation must propagate).
    """
    try:
        await connection.execute(_RELEASE_SQL, {"lock_key": MIGRATION_ADVISORY_LOCK_KEY})
    except Exception as exc:  # 清理路径不得覆盖原始迁移异常
        logger.warning("[MigrationLock] Failed to release migration lock: %s", type(exc).__name__)
