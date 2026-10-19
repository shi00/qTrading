"""ModelUsageViewModel 单元测试（S5 §11.5 使用记录）。

覆盖（R19）：
- load 读取请求页 + 预算摘要，字段进入不可变 state；
- dao 不可用降级（不伪装成功、不抛）；
- set_filter 重置分页并清空展开；非法 origin 显式失败；
- next/prev 分页（50/页，边界 no-op）；
- expand 加载有序尝试并可折叠；
- release_unconfirmed_reservation 未注入释放器时降级；注入后刷新摘要；
- enter_monthly_summary 切月汇总模式；
- export_csv detail / monthly_summary 两种粒度；
- 超出保留期标记；
- R2：CancelledError 传播。

mock ``AIUsageDao``（经 provider 注入），不触碰 engine / flet。
"""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ui.viewmodels.model_usage_view_model import (
    PAGE_SIZE,
    BudgetSummary,
    ModelUsageState,
    ModelUsageViewModel,
    UsageFilters,
    _write_csv_text,
)
from utils.thread_pool import TaskType

pytestmark = pytest.mark.unit


def _dao(
    *,
    rows: list[dict] | None = None,
    total: int = 0,
    summary: dict | None = None,
    attempts: list[dict] | None = None,
    unconfirmed: list[dict] | None = None,
) -> MagicMock:
    dao = MagicMock()
    dao.read_request_page = AsyncMock(return_value=(rows or [], total))
    dao.read_request_attempts = AsyncMock(return_value=attempts or [])
    dao.read_budget_summary = AsyncMock(
        return_value=summary
        or {
            "month": "202601",
            "known_cny": Decimal("0.05"),
            "effective_reserved_cny": Decimal("0.10"),
            "unconfirmed_reserved_cny": Decimal("0.02"),
        }
    )
    dao.read_unconfirmed_reservations = AsyncMock(return_value=unconfirmed or [])
    return dao


def _vm(
    dao: MagicMock | None,
    *,
    releaser=None,
    retention_days: int = 180,
) -> ModelUsageViewModel:
    return ModelUsageViewModel(
        dao_provider=(lambda: dao) if dao is not None else (lambda: None),
        reservation_releaser=releaser,
        detail_retention_days=retention_days,
        month_provider=lambda: "202601",
    )


@pytest.mark.asyncio
async def test_load_populates_requests_total_and_budget():
    rows = [{"invocation_id": "inv-1", "terminal_status": "succeeded", "cost_status": "known"}]
    dao = _dao(rows=rows, total=60)
    vm = _vm(dao)
    await vm.load()
    assert vm.state.requests == tuple(rows)
    assert vm.state.total == 60
    assert vm.state.page == 0
    assert vm.state.budget_summary.known_cny == Decimal("0.05")
    assert vm.state.budget_summary.effective_reserved_cny == Decimal("0.10")
    assert vm.state.budget_summary.unconfirmed_reserved_cny == Decimal("0.02")
    dao.read_request_page.assert_awaited_once()
    kwargs = dao.read_request_page.await_args.kwargs
    assert kwargs["limit"] == PAGE_SIZE and kwargs["offset"] == 0


@pytest.mark.asyncio
async def test_load_populates_unconfirmed_reservation_details():
    """未确认详情（§11.5）：reservation_id / task / amount / billing_month 进入不可变 state。"""
    unconfirmed = [
        {"reservation_id": "res-1", "task_id": "sentiment", "amount_cny": "0.02", "billing_month": "2026-10"}
    ]
    dao = _dao(unconfirmed=unconfirmed)
    vm = _vm(dao)
    await vm.load()
    assert vm.state.unconfirmed_reservations == (unconfirmed[0],)
    # 全局读取（不按当前月过滤）：跨月历史遗留在详情区可解除（§10.3）
    assert dao.read_unconfirmed_reservations.await_args.kwargs == {}


@pytest.mark.asyncio
async def test_load_dao_unavailable_degrades_without_crash():
    vm = _vm(None)
    await vm.load()
    assert vm.state.requests == ()
    assert vm.state.message is not None
    assert vm.state.message.key == "model_usage_dao_unavailable"


@pytest.mark.asyncio
async def test_load_dao_error_degrades_without_crash():
    dao = _dao()
    dao.read_request_page = AsyncMock(side_effect=RuntimeError("boom"))
    vm = _vm(dao)
    await vm.load()
    assert vm.state.requests == ()
    assert vm.state.message is not None
    assert vm.state.message.key == "model_usage_load_failed"


@pytest.mark.asyncio
async def test_set_filter_resets_page_and_clears_expansion():
    dao = _dao(total=120)
    dao.read_request_attempts = AsyncMock(return_value=[{"kind": "attempt"}])
    vm = _vm(dao)
    await vm.load()
    await vm.next_page()
    assert vm.state.page == 1
    await vm.expand("inv-1")
    assert vm.state.expanded_id == "inv-1"
    vm.set_filter(scenario_id="stock_analysis")
    assert vm.state.page == 0
    assert vm.state.expanded_id is None
    assert vm.state.attempts == ()
    assert vm.state.filters.scenario_id == "stock_analysis"


def test_set_filter_unknown_origin_raises():
    vm = _vm(_dao())
    with pytest.raises(ValueError, match="unknown origin filter"):
        vm.set_filter(origin="bogus")


@pytest.mark.asyncio
async def test_set_filter_forwards_terminal_and_cost_status_to_dao():
    dao = _dao()
    vm = _vm(dao)
    await vm.load()

    vm.set_filter(terminal_status="cancelled", cost_status="partial")

    assert vm.state.filters.terminal_status == "cancelled"
    assert vm.state.filters.cost_status == "partial"
    await vm.load()
    kwargs = dao.read_request_page.await_args.kwargs
    assert kwargs["terminal_status"] == "cancelled"
    assert kwargs["cost_status"] == "partial"


@pytest.mark.asyncio
async def test_next_page_advances_until_total_boundary():
    dao = _dao(total=60)
    vm = _vm(dao)
    await vm.load()
    await vm.next_page()
    assert vm.state.page == 1
    # 第 1 页已覆盖全部（(1+1)*50 >= 60）：不再前进
    await vm.next_page()
    assert vm.state.page == 1
    assert dao.read_request_page.await_count == 2


@pytest.mark.asyncio
async def test_prev_page_noop_at_zero():
    dao = _dao(total=60)
    vm = _vm(dao)
    await vm.load()
    await vm.prev_page()
    assert vm.state.page == 0
    assert dao.read_request_page.await_count == 1


@pytest.mark.asyncio
async def test_expand_loads_attempts_and_toggles():
    dao = _dao(attempts=[{"kind": "attempt", "status": "failed"}])
    vm = _vm(dao)
    await vm.load()
    await vm.expand("inv-1")
    assert vm.state.expanded_id == "inv-1"
    assert vm.state.attempts == ({"kind": "attempt", "status": "failed"},)
    dao.read_request_attempts.assert_awaited_once_with(invocation_id="inv-1")
    await vm.expand("inv-1")
    assert vm.state.expanded_id is None
    assert vm.state.attempts == ()


@pytest.mark.asyncio
async def test_release_unavailable_without_releaser():
    vm = _vm(_dao())
    await vm.load()
    await vm.release_unconfirmed_reservation("res-1")
    assert vm.state.message is not None
    assert vm.state.message.key == "model_usage_release_unavailable"


@pytest.mark.asyncio
async def test_release_success_refreshes_budget():
    dao = _dao(
        summary={
            "known_cny": Decimal("0.05"),
            "effective_reserved_cny": Decimal("0"),
            "unconfirmed_reserved_cny": Decimal("0"),
        }
    )
    releaser = AsyncMock(return_value=None)
    vm = _vm(dao, releaser=releaser)
    await vm.load()
    await vm.release_unconfirmed_reservation("res-1")
    releaser.assert_awaited_once_with("res-1")
    assert vm.state.budget_summary.unconfirmed_reserved_cny == Decimal("0")
    assert vm.state.message is not None
    assert vm.state.message.key == "model_usage_release_done"
    # 释放后刷新：预算摘要 + 未确认详情各再读一次（load → 共 2 次）
    assert dao.read_unconfirmed_reservations.await_count == 2


def test_enter_monthly_summary_sets_mode_and_granularity():
    vm = _vm(_dao())
    vm.enter_monthly_summary()
    assert vm.state.monthly_summary_mode is True
    assert vm.state.export_granularity == "monthly_summary"


def test_retention_exceeded_when_start_before_cutoff():
    vm = _vm(_dao(), retention_days=180)
    vm.set_filter(start=dt.datetime(2020, 1, 1))
    assert vm.state.retention_exceeded is True


@pytest.mark.asyncio
async def test_export_csv_detail_granularity():
    rows = [{"scenario_id": "stock_analysis", "terminal_status": "succeeded", "cost_status": "known"}]
    dao = _dao(rows=rows, total=1)
    vm = _vm(dao)
    await vm.load()
    text = await vm.export_csv()
    assert "scenario_id" in text
    assert "stock_analysis" in text
    assert vm.state.export_granularity == "detail"
    assert vm.state.message is not None


@pytest.mark.asyncio
async def test_export_csv_monthly_summary_granularity():
    vm = _vm(_dao())
    await vm.load()
    vm.enter_monthly_summary()
    text = await vm.export_csv()
    assert "known_cny" in text
    assert "0.05" in text


@pytest.mark.asyncio
async def test_write_csv_offloads_to_io_pool():
    """write_csv 将文本经 IO 线程池落盘（R16 不阻塞事件循环）。"""
    vm = _vm(_dao())
    with patch("ui.viewmodels.model_usage_view_model.ThreadPoolManager") as tp:
        tp.return_value.run_async = AsyncMock()
        await vm.write_csv("/tmp/out.csv", "csv-body")

    tp.return_value.run_async.assert_awaited_once_with(TaskType.IO, _write_csv_text, "/tmp/out.csv", "csv-body")


@pytest.mark.asyncio
async def test_write_csv_propagates_cancelled_error():
    """write_csv 不吞 asyncio.CancelledError（R2）。"""
    vm = _vm(_dao())
    with patch("ui.viewmodels.model_usage_view_model.ThreadPoolManager") as tp:
        tp.return_value.run_async = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await vm.write_csv("/tmp/out.csv", "csv-body")
        assert exc_info.type is asyncio.CancelledError
        tp.return_value.run_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_error_propagates_on_load():
    dao = _dao()
    dao.read_request_page = AsyncMock(side_effect=asyncio.CancelledError())
    vm = _vm(dao)
    with pytest.raises(asyncio.CancelledError) as exc_info:
        await vm.load()
    assert exc_info.type is asyncio.CancelledError
    dao.read_request_page.assert_awaited_once()


def test_state_defaults_are_frozen_and_tuple():
    state = ModelUsageState()
    assert state.requests == ()
    assert isinstance(state.filters, UsageFilters)
    assert isinstance(state.budget_summary, BudgetSummary)
    assert state.message is None
