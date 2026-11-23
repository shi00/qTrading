"""P1-04：回测执行时点「写读时区合同」（真实 embedded PG 回读）。

覆盖：
- 新记录经迁移默认值落库 ``executed_at_utc``（timestamptz），回读为带时区的绝对时刻，
  严格读为 ``ready`` 且可正常准备草稿；
- 存量记录（``executed_at_utc`` 为 NULL、``executed_at`` 为 naive 墙钟）回读时保留
  「未知」，不得按 UTC 猜测（R21/R24），并给出可用的受限准备路径。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import ReadState
from data.persistence.daos.backtest_dao import BacktestDAO
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import BacktestResultModel
from services.agent_service.business_research import backtest_preparation
from services.agent_service.business_sources import read_backtest_snapshot
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

USER_SCOPE = "local-desktop"


async def _reset_schema(url: str) -> None:
    engine = create_test_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


async def _setup(url: str) -> BacktestDAO:
    await _reset_schema(url)
    cfg = make_alembic_cfg(url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    return BacktestDAO(create_test_engine(url))


async def _insert_row(dao: BacktestDAO, *, run_id: str, **overrides) -> None:
    values = {
        "run_id": run_id,
        "strategy_name": "momentum",
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 3, 1),
    }
    values.update(overrides)
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接写前置事实
        await conn.execute(insert(BacktestResultModel).values(**values))


class TestBacktestExecutedAt:
    @pytest.mark.asyncio
    async def test_new_row_persists_verifiable_absolute_instant(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _insert_row(dao, run_id="bt-new")

        row = await dao.get_result("bt-new", suppress_errors=False)
        assert row is not None
        absolute = row["executed_at_utc"]
        assert isinstance(absolute, datetime)
        # 迁移默认值 now() 写入绝对时刻：回读必须带偏移，且与当前时刻同量级。
        assert absolute.utcoffset() is not None
        assert abs(absolute - datetime.now(UTC)) < timedelta(minutes=10)
        # 遗留 executed_at 仍为 without time zone（naive），不得作为绝对时点使用。
        assert row["executed_at"].utcoffset() is None

        read = await read_backtest_snapshot(dao, user_scope=USER_SCOPE, result_id="bt-new")
        assert read.read_state is ReadState.READY
        assert read.source is not None
        assert read.source.source.available_at is not None
        assert read.source.source.available_at.endswith("Z")

        preparation = backtest_preparation(read, result_id="bt-new")
        assert preparation.object_context["availability_state"] == "known"

    @pytest.mark.asyncio
    async def test_legacy_row_stays_unknown_and_supports_restricted_preparation(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _insert_row(
            dao,
            run_id="bt-legacy",
            executed_at=datetime(2026, 3, 2, 8, 0, 0),  # 会话时区墙钟，绝对时刻不可证
            executed_at_utc=None,
        )

        read = await read_backtest_snapshot(dao, user_scope=USER_SCOPE, result_id="bt-legacy")
        # 时区不可证：保留未知，不贴 UTC。
        assert read.read_state is ReadState.UNKNOWN
        assert read.missing_reason == "available_at_zone_unknown"
        assert read.source is not None
        assert read.source.source.available_at is None
        assert read.source.source.read_state is ReadState.UNKNOWN

        # 受限准备路径可用：草稿上下文显式标注时点未知（发送期由 PIT 准入拒绝）。
        preparation = backtest_preparation(read, result_id="bt-legacy")
        assert preparation.object_context["availability_state"] == "unknown"
        assert preparation.object_context["available_at"] is None

    @pytest.mark.asyncio
    async def test_legacy_row_with_cutoff_is_not_admitted(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _insert_row(
            dao,
            run_id="bt-legacy-cutoff",
            executed_at=datetime(2026, 3, 2, 8, 0, 0),
            executed_at_utc=None,
        )

        read = await read_backtest_snapshot(
            dao,
            user_scope=USER_SCOPE,
            result_id="bt-legacy-cutoff",
            cutoff="2026-04-01T00:00:00.000000Z",
        )
        assert read.source is None
        assert read.read_state is ReadState.UNAVAILABLE
        assert read.missing_reason == "available_at_zone_unknown"
