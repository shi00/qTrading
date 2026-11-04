"""0024 screening_history 历史去重回归（D04-F04）。

0001 允许 ``screening_history.created_at`` 为 NULL；0024 在创建三键唯一约束前
必须清理 ``(trade_date, strategy_name, ts_code)`` 重复组。本测试在真实 embedded
PostgreSQL 上执行 ``alembic upgrade 0023 -> 0024``，真实验证迁移 SQL 而非复制其逻辑：

- 全 NULL created_at 组：保留最大 id；
- NULL 与非 NULL 混合组：NULL 视为最旧，保留最新非 NULL 行；
- 同 created_at 组：保留最大 id；
- 单行组与不同三键行：不删除；
- ``screening_thinking`` 经 ``ondelete=CASCADE`` 级联清理；
- 空表升级不报错。

依赖 ``real_embedded_pg`` fixture（真实 sidecar binary + embedded PG），缺失
sidecar binary 时由 fixture skip。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from data.persistence.embedded_postgres.protocol import ConnectionInfo
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_INSERT_HISTORY = text(
    """
    INSERT INTO screening_history (run_id, trade_date, strategy_name, ts_code, created_at)
    VALUES (:run_id, :trade_date, :strategy_name, :ts_code, :created_at)
    RETURNING id
    """
)

_INSERT_THINKING = text(
    """
    INSERT INTO screening_thinking (history_id, thinking)
    VALUES (:history_id, :thinking)
    """
)


async def _reset_schema(url: str) -> None:
    """重置 public schema，确保每个用例从干净状态开始。"""
    engine = create_test_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


async def _insert_history(
    conn: AsyncConnection,
    *,
    run_id: str,
    trade_date: str,
    strategy_name: str,
    ts_code: str,
    created_at: datetime | None,
) -> int:
    result = await conn.execute(
        _INSERT_HISTORY,
        {
            "run_id": run_id,
            "trade_date": trade_date,
            "strategy_name": strategy_name,
            "ts_code": ts_code,
            "created_at": created_at,
        },
    )
    return int(result.scalar_one())


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_0024_dedups_null_and_tied_created_at(real_embedded_pg: ConnectionInfo) -> None:
    """NULL / 混合 / 并列时间戳的重复组均被确定性清理，thinking 级联删除。"""
    info = real_embedded_pg
    await _reset_schema(info.url)
    cfg = make_alembic_cfg(info.url)
    await asyncio.to_thread(command.upgrade, cfg, "0023")

    engine = create_test_engine(info.url)
    try:
        async with engine.begin() as conn:
            ids = {
                # 全 NULL 组：三行 created_at 均为 NULL。
                "a1": await _insert_history(
                    conn,
                    run_id="run-a1",
                    trade_date="2026-01-02",
                    strategy_name="s",
                    ts_code="000001.SZ",
                    created_at=None,
                ),
                "a2": await _insert_history(
                    conn,
                    run_id="run-a2",
                    trade_date="2026-01-02",
                    strategy_name="s",
                    ts_code="000001.SZ",
                    created_at=None,
                ),
                "a3": await _insert_history(
                    conn,
                    run_id="run-a3",
                    trade_date="2026-01-02",
                    strategy_name="s",
                    ts_code="000001.SZ",
                    created_at=None,
                ),
                # NULL 与非 NULL 混合组：NULL 视为最旧，保留最新非 NULL 行。
                "b_null": await _insert_history(
                    conn,
                    run_id="run-b-null",
                    trade_date="2026-01-01",
                    strategy_name="s",
                    ts_code="000001.SZ",
                    created_at=None,
                ),
                "b_old": await _insert_history(
                    conn,
                    run_id="run-b-old",
                    trade_date="2026-01-01",
                    strategy_name="s",
                    ts_code="000001.SZ",
                    created_at=datetime(2026, 1, 1),
                ),
                "b_new": await _insert_history(
                    conn,
                    run_id="run-b-new",
                    trade_date="2026-01-01",
                    strategy_name="s",
                    ts_code="000001.SZ",
                    created_at=datetime(2026, 1, 2),
                ),
                # 同 created_at 组：按 id DESC 保留最大 id。
                "c1": await _insert_history(
                    conn,
                    run_id="run-c1",
                    trade_date="2026-01-03",
                    strategy_name="s",
                    ts_code="000001.SZ",
                    created_at=datetime(2026, 1, 3),
                ),
                "c2": await _insert_history(
                    conn,
                    run_id="run-c2",
                    trade_date="2026-01-03",
                    strategy_name="s",
                    ts_code="000001.SZ",
                    created_at=datetime(2026, 1, 3),
                ),
                # 单行组与不同三键行：不应被删除。
                "d1": await _insert_history(
                    conn,
                    run_id="run-d1",
                    trade_date="2026-01-04",
                    strategy_name="s",
                    ts_code="600000.SH",
                    created_at=None,
                ),
                "e1": await _insert_history(
                    conn,
                    run_id="run-e1",
                    trade_date="2026-01-02",
                    strategy_name="s",
                    ts_code="600000.SH",
                    created_at=None,
                ),
                "e2": await _insert_history(
                    conn,
                    run_id="run-e2",
                    trade_date="2026-01-02",
                    strategy_name="s2",
                    ts_code="000001.SZ",
                    created_at=None,
                ),
            }
            # a1 / b_null 将被删除，其 thinking 应级联清除；a3 为幸存行。
            for history_id, thinking in (
                (ids["a1"], "thinking-deleted-a1"),
                (ids["a3"], "thinking-survivor-a3"),
                (ids["b_null"], "thinking-deleted-b-null"),
            ):
                await conn.execute(_INSERT_THINKING, {"history_id": history_id, "thinking": thinking})
    finally:
        await engine.dispose()

    await asyncio.to_thread(command.upgrade, cfg, "0024")

    expected_survivors = {
        ids["a3"]: "run-a3",
        ids["b_new"]: "run-b-new",
        ids["c2"]: "run-c2",
        ids["d1"]: "run-d1",
        ids["e1"]: "run-e1",
        ids["e2"]: "run-e2",
    }

    engine = create_test_engine(info.url)
    try:
        async with engine.connect() as conn:
            history_rows = await conn.execute(text("SELECT id, run_id FROM screening_history"))
            remaining = {int(row[0]): str(row[1]) for row in history_rows}

            thinking_rows = await conn.execute(text("SELECT history_id, thinking FROM screening_thinking"))
            remaining_thinking = {int(row[0]): str(row[1]) for row in thinking_rows}

            constraint_rows = await conn.execute(
                text(
                    """
                    SELECT conname
                    FROM pg_constraint
                    WHERE conrelid = 'screening_history'::regclass
                      AND contype = 'u'
                    """
                )
            )
            unique_constraints = {str(row[0]) for row in constraint_rows}
    finally:
        await engine.dispose()

    assert remaining == expected_survivors
    assert remaining_thinking == {ids["a3"]: "thinking-survivor-a3"}
    assert "uq_screening_history_dat_strategy_code" in unique_constraints
    assert "uq_screening_history_run_code" not in unique_constraints


@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_0024_empty_table(real_embedded_pg: ConnectionInfo) -> None:
    """空表执行 0023 -> 0024 不报错，版本推进到 0024。"""
    info = real_embedded_pg
    await _reset_schema(info.url)
    cfg = make_alembic_cfg(info.url)
    await asyncio.to_thread(command.upgrade, cfg, "0023")
    await asyncio.to_thread(command.upgrade, cfg, "0024")

    engine = create_test_engine(info.url)
    try:
        async with engine.connect() as conn:
            version = await conn.execute(text("SELECT version_num FROM alembic_version"))
            count = await conn.execute(text("SELECT count(*) FROM screening_history"))
        version_num = str(version.scalar_one())
        row_count = int(count.scalar_one())
    finally:
        await engine.dispose()

    assert version_num == "0024"
    assert row_count == 0
