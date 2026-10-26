"""ModelUsageViewModel 单元测试（S5 §11.5 使用记录）。

覆盖（R19）：
- load 读取请求页 + 预算摘要，字段进入不可变 state；
- dao 不可用降级（不伪装成功、不抛）；
- set_filter 重置分页、清空展开与旧结果并统一异步重载（F35）；非法 origin 显式失败；
- F35 请求代次隔离：两个 load 逆序完成保留最新请求结果；旧代次失败降级不覆盖；
  在途展开响应在筛选变化后不回填；重载在途时翻页 no-op（旧 total 路径已被统一重载取代）；
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
from typing import Any
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
    await vm.set_filter(scenario_id="stock_analysis")
    assert vm.state.page == 0
    assert vm.state.expanded_id is None
    assert vm.state.attempts == ()
    assert vm.state.filters.scenario_id == "stock_analysis"


@pytest.mark.asyncio
async def test_set_filter_unknown_origin_raises():
    vm = _vm(_dao())
    with pytest.raises(ValueError) as exc_info:
        await vm.set_filter(origin="bogus")
    assert exc_info.type is ValueError
    assert "unknown origin filter" in str(exc_info.value)


@pytest.mark.asyncio
async def test_set_filter_triggers_reload_forwarding_to_dao():
    """F35：筛选变化统一异步重载（真实事件链路不再依赖调用方手动 load）。"""
    dao = _dao()
    vm = _vm(dao)
    await vm.load()
    assert dao.read_request_page.await_count == 1

    await vm.set_filter(terminal_status="cancelled", cost_status="partial")

    assert dao.read_request_page.await_count == 2
    kwargs = dao.read_request_page.await_args.kwargs
    assert kwargs["terminal_status"] == "cancelled"
    assert kwargs["cost_status"] == "partial"
    assert vm.state.filters.terminal_status == "cancelled"
    assert vm.state.filters.cost_status == "partial"


# ---------------------------------------------------------------------------
# F35：请求代次隔离（乱序响应 / 清空旧结果 / 翻页边界）
# ---------------------------------------------------------------------------


def _install_gated_read(dao: MagicMock, gate: asyncio.Event, rows: list[dict], total: int) -> None:
    """把 ``read_request_page`` 换为受 ``gate`` 控制的读取（放行前挂起，模拟在途请求）。"""

    async def _read(**_kwargs: Any) -> tuple[list[dict], int]:
        await gate.wait()
        return (rows, total)

    dao.read_request_page = _read


@pytest.mark.asyncio
async def test_set_filter_clears_stale_requests_while_reload_in_flight():
    """F35：筛选变化立即清空旧结果（重载完成前不展示与新条件不符的陈旧数据）。"""
    dao = _dao(rows=[{"invocation_id": "inv-old"}], total=1)
    vm = _vm(dao)
    await vm.load()
    assert vm.state.requests != ()

    gate = asyncio.Event()
    _install_gated_read(dao, gate, rows=[{"invocation_id": "inv-new"}], total=2)
    reload_task = asyncio.create_task(vm.set_filter(scenario_id="stock_analysis"))
    await asyncio.sleep(0)  # 让 set_filter 同步段（清空 + 发起重载）执行到首个 await

    assert vm.state.requests == ()
    assert vm.state.total == 0
    assert vm.state.page == 0
    assert vm.state.filters.scenario_id == "stock_analysis"

    gate.set()
    await reload_task
    assert [r["invocation_id"] for r in vm.state.requests] == ["inv-new"]
    assert vm.state.total == 2


@pytest.mark.asyncio
async def test_out_of_order_load_responses_keep_latest_request_result():
    """F35：两个 load 逆序完成（旧筛选响应后到），最终 state 为最新请求结果。"""
    dao = _dao()
    vm = _vm(dao)
    await vm.load()

    old_gate = asyncio.Event()
    new_gate = asyncio.Event()
    _install_gated_read(dao, old_gate, rows=[{"invocation_id": "inv-old"}], total=1)
    old_task = asyncio.create_task(vm.set_filter(scenario_id="news_annotation"))
    await asyncio.sleep(0)  # 旧请求已发出并挂起

    _install_gated_read(dao, new_gate, rows=[{"invocation_id": "inv-new"}], total=2)
    new_task = asyncio.create_task(vm.set_filter(scenario_id="stock_analysis"))
    await asyncio.sleep(0)  # 新请求已发出并挂起

    old_gate.set()  # 逆序：先放行旧请求响应
    await old_task
    assert vm.state.requests == ()  # 旧代次响应被拒，保持清空态
    assert vm.state.filters.scenario_id == "stock_analysis"

    new_gate.set()
    await new_task
    assert [r["invocation_id"] for r in vm.state.requests] == ["inv-new"]
    assert vm.state.total == 2


@pytest.mark.asyncio
async def test_stale_load_failure_does_not_overwrite_newer_state():
    """F35：旧代次失败降级不覆盖新代次的消息与结果。"""
    dao = _dao()
    vm = _vm(dao)
    await vm.load()

    old_gate = asyncio.Event()

    async def _old_read(**_kwargs: Any) -> tuple[list[dict], int]:
        await old_gate.wait()
        raise RuntimeError("stale boom")

    dao.read_request_page = _old_read
    old_task = asyncio.create_task(vm.set_filter(scenario_id="news_annotation"))
    await asyncio.sleep(0)  # 旧请求已发出并挂起

    async def _new_read(**_kwargs: Any) -> tuple[list[dict], int]:
        return ([{"invocation_id": "inv-new"}], 2)

    dao.read_request_page = _new_read
    await vm.set_filter(scenario_id="stock_analysis")  # 新请求立即完成
    assert vm.state.message is None
    assert [r["invocation_id"] for r in vm.state.requests] == ["inv-new"]

    old_gate.set()  # 旧请求失败响应后到
    await old_task
    assert vm.state.message is None  # 旧代次失败不覆盖
    assert [r["invocation_id"] for r in vm.state.requests] == ["inv-new"]


@pytest.mark.asyncio
async def test_stale_expand_response_discarded_after_filter_change():
    """F35：筛选变化清空展开后，在途旧展开响应不得回填新条件界面。"""
    dao = _dao(attempts=[{"kind": "old-attempt"}])
    vm = _vm(dao)
    await vm.load()

    gate = asyncio.Event()

    async def _gated_attempts(**_kwargs: Any) -> list[dict]:
        await gate.wait()
        return [{"kind": "old-attempt"}]

    dao.read_request_attempts = _gated_attempts
    expand_task = asyncio.create_task(vm.expand("inv-old"))
    await asyncio.sleep(0)
    assert vm.state.expanded_id is None  # 尚未返回

    await vm.set_filter(scenario_id="stock_analysis")  # 清空展开并重载
    assert vm.state.expanded_id is None
    assert vm.state.attempts == ()

    gate.set()  # 旧展开响应后到
    await expand_task
    assert vm.state.expanded_id is None
    assert vm.state.attempts == ()


@pytest.mark.asyncio
async def test_next_page_noop_while_reload_in_flight_then_works_after():
    """F35：重载在途（total=0）时翻页 no-op；重载完成后按新 total 正常翻页。"""
    dao = _dao(rows=[{"invocation_id": "inv-old"}], total=60)
    vm = _vm(dao)
    await vm.load()
    assert vm.state.total == 60

    gate = asyncio.Event()
    _install_gated_read(dao, gate, rows=[{"invocation_id": "inv-new"}], total=60)
    reload_task = asyncio.create_task(vm.set_filter(origin="business"))
    await asyncio.sleep(0)
    assert vm.state.total == 0

    await vm.next_page()  # 在途：no-op（不再依赖旧 total 触发刷新）
    assert vm.state.page == 0

    gate.set()
    await reload_task
    assert vm.state.total == 60

    dao.read_request_page = AsyncMock(return_value=([{"invocation_id": "inv-p1"}], 60))
    await vm.next_page()
    assert vm.state.page == 1
    assert dao.read_request_page.await_args.kwargs["offset"] == PAGE_SIZE


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


@pytest.mark.asyncio
async def test_retention_exceeded_when_start_before_cutoff():
    vm = _vm(_dao(), retention_days=180)
    await vm.set_filter(start=dt.datetime(2020, 1, 1))
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
