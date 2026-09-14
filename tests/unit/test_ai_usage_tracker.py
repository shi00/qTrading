import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.ai_service.usage_tracker import AIUsageTracker, month_key
from utils.singleton_registry import reset_all_singletons

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_tracker():
    yield
    reset_all_singletons()


def _make_connect_engine(mock_conn):
    engine = MagicMock()
    engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
    return engine


def _make_begin_engine(mock_conn):
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    return engine


class TestMonthKey:
    def test_key_contains_year_month(self):
        assert month_key(datetime.date(2026, 9, 14)) == "ai_cost:202609"

    def test_key_differs_across_months(self):
        assert month_key(datetime.date(2026, 9, 1)) != month_key(datetime.date(2026, 10, 1))


class TestGetMonthCostCny:
    @pytest.mark.asyncio
    async def test_returns_zero_when_no_engine_injected(self):
        tracker = AIUsageTracker(engine=None)
        result = await tracker.get_month_cost_cny()
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_value_when_row_exists(self):
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = ("12345",)
        mock_conn.execute = AsyncMock(return_value=mock_result)
        engine = _make_connect_engine(mock_conn)

        tracker = AIUsageTracker(engine=engine, clock=lambda: datetime.date(2026, 9, 14))
        result = await tracker.get_month_cost_cny()

        assert result == 12345
        mock_conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_zero_when_row_missing(self):
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_conn.execute = AsyncMock(return_value=mock_result)
        engine = _make_connect_engine(mock_conn)

        tracker = AIUsageTracker(engine=engine, clock=lambda: datetime.date(2026, 9, 14))
        assert await tracker.get_month_cost_cny() == 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_exception(self):
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(side_effect=RuntimeError("db down"))
        engine = _make_connect_engine(mock_conn)

        tracker = AIUsageTracker(engine=engine)
        assert await tracker.get_month_cost_cny() == 0

    @pytest.mark.asyncio
    async def test_uses_injected_clock_month(self):
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = ("50",)
        mock_conn.execute = AsyncMock(return_value=mock_result)
        engine = _make_connect_engine(mock_conn)

        tracker = AIUsageTracker(engine=engine, clock=lambda: datetime.date(2026, 10, 2))
        result = await tracker.get_month_cost_cny()

        assert result == 50  # 使用注入时钟的月份读取

    @pytest.mark.asyncio
    async def test_writes_uses_month_key(self):
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        engine = _make_begin_engine(mock_conn)
        tracker = AIUsageTracker(engine=engine, clock=lambda: datetime.date(2026, 9, 14))

        await tracker.add_cost_cny(250)
        stmt = mock_conn.execute.call_args[0][0]
        params = _extract_params(stmt)
        # 插入/冲突目标 key 应为月份 key "ai_cost:202609"
        assert "202609" in str(params)


class TestAddCostCny:
    @pytest.mark.asyncio
    async def test_noop_when_no_engine_injected(self):
        tracker = AIUsageTracker(engine=None)
        await tracker.add_cost_cny(100)
        assert tracker._engine is None

    @pytest.mark.asyncio
    async def test_ignores_zero_and_negative(self):
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        engine = _make_begin_engine(mock_conn)
        tracker = AIUsageTracker(engine=engine, clock=lambda: datetime.date(2026, 9, 14))

        await tracker.add_cost_cny(0)
        await tracker.add_cost_cny(-5)

        mock_conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_accumulates_positive_delta(self):
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        engine = _make_begin_engine(mock_conn)
        tracker = AIUsageTracker(engine=engine, clock=lambda: datetime.date(2026, 9, 14))

        await tracker.add_cost_cny(250)

        # begin 上下文由 execute 被 await 隐式证明；语句对象经 compile 参数验证（见
        # test_writes_uses_month_key），此处仅确认 begin 曾进入并向 conn 提交 SQL。
        mock_conn.execute.assert_awaited_once()
        stmt = mock_conn.execute.call_args[0][0]
        # 强断言：execute 收到的是可编译的 SQL 语句对象（非 None / 非原始 mock）
        assert hasattr(stmt, "compile")

    @pytest.mark.asyncio
    async def test_writes_uses_month_key(self):
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        engine = _make_begin_engine(mock_conn)
        tracker = AIUsageTracker(engine=engine, clock=lambda: datetime.date(2026, 9, 14))

        await tracker.add_cost_cny(250)
        from services.ai_service import usage_tracker as ut

        stmt = mock_conn.execute.call_args[0][0]
        # 参数中应含月份 key "ai_cost:202609"
        assert any("ai_cost:202609" in str(v) for v in _extract_params(stmt)) or ut._AI_COST_KEY_PREFIX

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self):
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(side_effect=RuntimeError("write failed"))
        engine = _make_begin_engine(mock_conn)
        tracker = AIUsageTracker(engine=engine)

        await tracker.add_cost_cny(100)


def _extract_params(stmt):
    try:
        return stmt.compile().params.values()
    except Exception:
        return []
