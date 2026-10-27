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
- F37 导出范围：page 仅当前页、all 经 DAO 公开分页入口稳定遍历全集（offset 顺序、
  页不足提前终止、DAO 不可用 / 失败、过滤快照抗中途筛选变化、消息代次守卫）；
  导出行含 invocation_id；月汇总导出对齐查看月、摘要缺失空单元格（R21）；
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
    EXPORT_SCOPE_ALL,
    EXPORT_SCOPE_PAGE,
    ORIGIN_TEST,
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
    month_rows: list[dict] | None = None,
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
    dao.read_month_usage = AsyncMock(return_value=month_rows or [])
    return dao


def _vm(
    dao: MagicMock | None,
    *,
    releaser=None,
    retention_days: int = 180,
    reader=None,
    saver=None,
) -> ModelUsageViewModel:
    return ModelUsageViewModel(
        dao_provider=(lambda: dao) if dao is not None else (lambda: None),
        reservation_releaser=releaser,
        detail_retention_days=retention_days,
        month_provider=lambda: "202601",
        budget_limit_reader=reader,
        budget_limit_saver=saver,
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


@pytest.mark.asyncio
async def test_enter_monthly_summary_sets_mode_and_granularity():
    """F36：进入月汇总视图即真实读取查看月（当前月）汇总数据。"""
    dao = _dao()
    vm = _vm(dao)
    await vm.enter_monthly_summary()
    assert vm.state.monthly_summary_mode is True
    assert vm.state.export_granularity == "monthly_summary"
    assert vm.state.summary_month == "202601"
    assert vm.state.month_draft == "202601"
    dao.read_month_usage.assert_awaited_once()
    kwargs = dao.read_month_usage.await_args.kwargs
    assert kwargs["month"] == "202601"
    assert kwargs["aggregation_scope"] == "scenario"


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
    await vm.enter_monthly_summary()
    text = await vm.export_csv()
    assert "known_cny" in text
    assert "0.05" in text


# ---------------------------------------------------------------------------
# F36：月度预算录入 + 历史月汇总（与当前月分离）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enter_monthly_summary_fills_viewed_month_rows_and_budget():
    """进入月汇总后行与摘要均来自查看月真实读取（F36：有数据可展示）。"""
    month_rows = [
        {
            "month": "202601",
            "scenario_id": "stock_analysis",
            "trigger": "manual",
            "invocation_count": 3,
            "attempt_count": 4,
            "succeeded_count": 3,
            "cost_cny": Decimal("0.12"),
        }
    ]
    dao = _dao(month_rows=month_rows)
    vm = _vm(dao)
    await vm.enter_monthly_summary()
    assert vm.state.monthly_rows == tuple(month_rows)
    assert vm.state.summary_budget is not None
    assert vm.state.summary_budget.known_cny == Decimal("0.05")
    assert vm.state.message is None


@pytest.mark.asyncio
async def test_select_summary_month_history_not_replaced_by_current():
    """F36 核心：切换历史月后行与摘要来自该月读取，不被当前月数据替代。"""
    history_rows = [
        {
            "month": "202512",
            "scenario_id": "news_annotation",
            "trigger": "scheduled",
            "invocation_count": 9,
            "attempt_count": 11,
            "succeeded_count": 9,
            "cost_cny": Decimal("0.50"),
        }
    ]
    dao = _dao()
    history_summary = {
        "month": "202512",
        "known_cny": Decimal("0.50"),
        "effective_reserved_cny": Decimal("0"),
        "unconfirmed_reserved_cny": Decimal("0"),
    }

    # 按月份分流：当前月返回默认摘要、历史月返回历史摘要
    async def _summary_by_month(month: str) -> dict:
        if month == "202512":
            return history_summary
        return {
            "month": "202601",
            "known_cny": Decimal("0.05"),
            "effective_reserved_cny": Decimal("0.10"),
            "unconfirmed_reserved_cny": Decimal("0.02"),
        }

    dao.read_budget_summary = _summary_by_month

    async def _month_usage(*, month: str, aggregation_scope: str) -> list[dict]:
        return history_rows if month == "202512" else []

    dao.read_month_usage = _month_usage

    vm = _vm(dao)
    await vm.enter_monthly_summary()  # 当前月 202601
    assert vm.state.monthly_rows == ()
    await vm.select_summary_month("2025-12")
    assert vm.state.summary_month == "202512"
    assert vm.state.month_draft == "202512"
    assert vm.state.monthly_rows == tuple(history_rows)
    assert vm.state.summary_budget is not None
    assert vm.state.summary_budget.known_cny == Decimal("0.50")  # 历史月摘要，非当前月 0.05


@pytest.mark.asyncio
async def test_select_summary_month_invalid_keeps_view():
    """非法月份显式消息：不改视图、不查询、旧月数据保留。"""
    dao = _dao(month_rows=[{"month": "202601", "scenario_id": "stock_analysis", "cost_cny": Decimal("0.12")}])
    vm = _vm(dao)
    await vm.enter_monthly_summary()
    assert vm.state.monthly_rows != ()
    await vm.select_summary_month("bogus-13")
    assert vm.state.message is not None
    assert vm.state.message.key == "model_usage_month_invalid"
    assert vm.state.summary_month == "202601"  # 查看月不变
    assert vm.state.monthly_rows != ()  # 旧月数据不被清空
    dao.read_month_usage.assert_awaited_once()  # 未发起第二次读取


@pytest.mark.asyncio
async def test_select_summary_month_accepts_compact_and_normalized_form():
    """YYYY-MM 与 YYYYMM 两种输入均归一化为 YYYYMM。"""
    dao = _dao()
    vm = _vm(dao)
    await vm.enter_monthly_summary()
    vm.set_month_draft("202511")
    await vm.select_summary_month()
    assert vm.state.summary_month == "202511"
    vm.set_month_draft("2025-10")
    await vm.select_summary_month()
    assert vm.state.summary_month == "202510"
    assert dao.read_month_usage.await_args.kwargs["month"] == "202510"


@pytest.mark.asyncio
async def test_summary_generation_discards_stale_month_response():
    """F36 代次隔离：历史月响应先到、当前月挂起响应后释放，旧月数据不回填。"""
    dao = _dao()
    vm = _vm(dao)

    await vm.enter_monthly_summary()  # 当前月立即完成

    gate = asyncio.Event()

    async def _gated_month_usage(*, month: str, aggregation_scope: str) -> list[dict]:
        if month == "202601":
            await gate.wait()
            return [{"month": "202601", "scenario_id": "stale"}]
        return [{"month": "202512", "scenario_id": "fresh"}]

    dao.read_month_usage = _gated_month_usage

    stale_task = asyncio.create_task(vm.select_summary_month("202601"))
    await asyncio.sleep(0)  # 旧月份（当前月）读取已发出并挂起

    await vm.select_summary_month("2025-12")  # 新月份立即完成
    assert [r["scenario_id"] for r in vm.state.monthly_rows] == ["fresh"]

    gate.set()  # 旧月份响应后到
    await stale_task
    assert [r["scenario_id"] for r in vm.state.monthly_rows] == ["fresh"]  # 旧代次被拒


@pytest.mark.asyncio
async def test_exit_monthly_summary_resets_to_detail():
    """返回明细：mode 复位、导出粒度回 detail、月汇总数据清空（对称性）。"""
    dao = _dao()
    vm = _vm(dao)
    await vm.enter_monthly_summary()
    assert vm.state.monthly_summary_mode is True
    vm.exit_monthly_summary()
    assert vm.state.monthly_summary_mode is False
    assert vm.state.export_granularity == "detail"
    assert vm.state.summary_month == ""
    assert vm.state.month_draft == ""
    assert vm.state.monthly_rows == ()
    assert vm.state.summary_budget is None


def test_set_month_draft_updates_draft_only():
    vm = _vm(_dao())
    vm.set_month_draft("2025-10")
    assert vm.state.month_draft == "2025-10"
    assert vm.state.summary_month == ""  # 草稿不影响查看月


@pytest.mark.asyncio
async def test_save_monthly_budget_invalid_amount_rejected():
    """非法金额（不可解析 / 负数）不触达保存，显式消息（R21 不伪成功）。"""
    for draft in ("abc", "-5"):
        saver = AsyncMock(return_value=True)
        vm = _vm(_dao(), saver=saver)
        vm.set_budget_draft(draft)
        await vm.save_monthly_budget()
        assert vm.state.message is not None
        assert vm.state.message.key == "model_usage_budget_invalid"
        saver.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_monthly_budget_empty_clears_limit():
    """空输入=清除限额：saver 收到 None（未设语义），不以 0 伪装（R21）。"""
    saver = AsyncMock(return_value=True)
    vm = _vm(_dao(), saver=saver)
    vm.set_budget_draft("   ")
    await vm.save_monthly_budget()
    saver.assert_awaited_once_with(None)
    assert vm.state.message is not None
    assert vm.state.message.key == "model_usage_budget_saved"


@pytest.mark.asyncio
async def test_save_monthly_budget_unavailable_without_saver():
    vm = _vm(_dao())
    vm.set_budget_draft("100")
    await vm.save_monthly_budget()
    assert vm.state.message is not None
    assert vm.state.message.key == "model_usage_budget_save_unavailable"


@pytest.mark.asyncio
async def test_save_monthly_budget_not_committed_reports_retry():
    """CAS 冲突 / not_committed：如实反馈「未提交，请重试」，不自动重试。"""
    saver = AsyncMock(return_value=False)
    vm = _vm(_dao(), saver=saver)
    vm.set_budget_draft("100")
    await vm.save_monthly_budget()
    assert vm.state.message is not None
    assert vm.state.message.key == "model_usage_budget_save_failed"


@pytest.mark.asyncio
async def test_save_monthly_budget_saver_exception_reports_failure():
    saver = AsyncMock(side_effect=RuntimeError("cas boom"))
    vm = _vm(_dao(), saver=saver)
    vm.set_budget_draft("100")
    await vm.save_monthly_budget()
    assert vm.state.message is not None
    assert vm.state.message.key == "model_usage_budget_save_failed"


@pytest.mark.asyncio
async def test_save_monthly_budget_success_reloads_and_echoes_limit():
    """保存成功后 load 重读限额，输入框回显已保存值供重开核对。"""
    dao = _dao()
    saved: dict[str, Decimal | None] = {"limit": None}

    async def _reader() -> Decimal | None:
        return saved["limit"]

    async def _saver(amount: Decimal | None) -> bool:
        saved["limit"] = Decimal("100") if amount is not None else None
        return True

    vm = _vm(dao, reader=_reader, saver=_saver)
    await vm.load()
    assert vm.state.budget_draft == ""  # 未设限额 → 空草稿
    assert vm.state.monthly_limit_cny is None

    vm.set_budget_draft("100")
    await vm.save_monthly_budget()

    assert saved["limit"] == Decimal("100")
    assert vm.state.message is not None
    assert vm.state.message.key == "model_usage_budget_saved"
    assert vm.state.monthly_limit_cny == Decimal("100")
    assert vm.state.budget_draft == "100"  # 重开核对回显
    assert dao.read_request_page.await_count == 2  # 初始 load + 保存后重载


@pytest.mark.asyncio
async def test_load_reads_budget_limit_from_reader():
    reader = AsyncMock(return_value=Decimal("88.5"))
    vm = _vm(_dao(), reader=reader)
    await vm.load()
    assert vm.state.monthly_limit_cny == Decimal("88.5")
    assert vm.state.budget_draft == "88.5"
    assert vm.state.monthly_limit_unknown is False


@pytest.mark.asyncio
async def test_load_reader_failure_marks_unknown_not_unset():
    """R21：限额读取失败显式「未知」，不以 None 伪装「未设置」。"""
    reader = AsyncMock(side_effect=RuntimeError("config boom"))
    vm = _vm(_dao(), reader=reader)
    await vm.load()
    assert vm.state.monthly_limit_unknown is True
    assert vm.state.monthly_limit_cny is None
    assert vm.state.message is not None
    assert vm.state.message.key == "model_usage_limit_load_failed"


@pytest.mark.asyncio
async def test_load_cancelled_error_from_reader_propagates():
    """R2：reader 的 CancelledError 显式传播，不被 Exception 分支吞没。"""
    reader = AsyncMock(side_effect=asyncio.CancelledError())
    vm = _vm(_dao(), reader=reader)
    with pytest.raises(asyncio.CancelledError) as exc_info:
        await vm.load()
    assert exc_info.type is asyncio.CancelledError


@pytest.mark.asyncio
async def test_save_monthly_budget_cancelled_error_propagates():
    """R2：saver 的 CancelledError 显式传播，不被 Exception 分支吞没。"""
    saver = AsyncMock(side_effect=asyncio.CancelledError())
    vm = _vm(_dao(), saver=saver)
    vm.set_budget_draft("100")
    with pytest.raises(asyncio.CancelledError) as exc_info:
        await vm.save_monthly_budget()
    assert exc_info.type is asyncio.CancelledError


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


# ---------------------------------------------------------------------------
# F37：CSV 导出范围（当前页 / 全部筛选结果）+ 请求身份 + 月汇总对齐查看月
# ---------------------------------------------------------------------------


def test_set_export_scope_updates_state_and_rejects_unknown():
    vm = _vm(_dao())
    assert vm.state.export_scope == EXPORT_SCOPE_PAGE
    vm.set_export_scope(EXPORT_SCOPE_ALL)
    assert vm.state.export_scope == EXPORT_SCOPE_ALL
    with pytest.raises(ValueError) as exc_info:
        vm.set_export_scope("bogus")
    assert "unknown export scope" in str(exc_info.value)
    assert vm.state.export_scope == EXPORT_SCOPE_ALL  # 非法值不改状态


@pytest.mark.asyncio
async def test_export_csv_detail_includes_invocation_id():
    """F37 请求身份：导出行含 invocation_id 列（首列）。"""
    rows = [{"invocation_id": "inv-9", "scenario_id": "stock_analysis", "is_test": True}]
    vm = _vm(_dao(rows=rows, total=1))
    await vm.load()
    text = await vm.export_csv()
    header = text.splitlines()[0].split(",")
    assert header[0] == "invocation_id"
    assert "inv-9" in text
    assert ORIGIN_TEST in text


@pytest.mark.asyncio
async def test_export_csv_page_scope_exports_only_loaded_page_without_dao_refetch():
    """scope=page 仅导出当前已加载页（不隐式宣称全集），且不再触达 DAO。"""
    rows = [{"invocation_id": "inv-1"}, {"invocation_id": "inv-2"}]
    dao = _dao(rows=rows, total=120)
    vm = _vm(dao)
    await vm.load()
    dao.read_request_page = AsyncMock(return_value=([], 120))
    text = await vm.export_csv()
    assert "inv-1" in text and "inv-2" in text
    dao.read_request_page.assert_not_awaited()
    assert len(text.splitlines()) == 3  # 表头 + 2 行


def _paged_read(dao: MagicMock, pages: list[list[dict]], total: int, *, offsets: list[int]) -> None:
    """把 ``read_request_page`` 换为按调用顺序返回给定页的伪实现（记录 offset）。"""

    async def _read(**kwargs: Any) -> tuple[list[dict], int]:
        offsets.append(int(kwargs["offset"]))
        idx = len(offsets) - 1
        if idx >= len(pages):
            return ([], total)
        return (pages[idx], total)

    dao.read_request_page = _read


@pytest.mark.asyncio
async def test_export_csv_all_scope_paginates_full_filtered_set():
    """F37：scope=all 稳定分页遍历全集（120=50+50+20），不受当前页影响。"""
    pages = [
        [{"invocation_id": f"inv-{i:03d}"} for i in range(50)],
        [{"invocation_id": f"inv-{i:03d}"} for i in range(50, 100)],
        [{"invocation_id": f"inv-{i:03d}"} for i in range(100, 120)],
    ]
    dao = _dao()
    offsets: list[int] = []
    _paged_read(dao, pages, total=120, offsets=offsets)
    vm = _vm(dao)
    await vm.load()  # 首页正常加载（第 1 次 DAO 调用）
    await vm.next_page()  # 翻到第 2 页（第 2 次调用）
    assert vm.state.page == 1

    offsets.clear()  # 只统计导出期间的遍历
    vm.set_export_scope(EXPORT_SCOPE_ALL)
    text = await vm.export_csv()

    assert offsets == [0, PAGE_SIZE, 2 * PAGE_SIZE]  # 页序固定从 0 起，不受当前页影响
    lines = text.splitlines()
    assert len(lines) == 121  # 表头 + 120 行
    assert "inv-000" in text and "inv-119" in text


@pytest.mark.asyncio
async def test_export_csv_all_scope_stops_on_short_page_or_empty_page():
    """收缩数据 / total 与实际页不符：短页按行数前进、空页终止，不跳页不死循环。"""
    pages = [
        [{"invocation_id": f"inv-{i}"} for i in range(50)],
        [{"invocation_id": f"inv-{i}"} for i in range(50, 55)],  # 声明 total=120 但实际只有 55 行
        [],  # 若继续拉取则返回空页
    ]
    dao = _dao()
    offsets: list[int] = []
    _paged_read(dao, pages, total=120, offsets=offsets)
    vm = _vm(dao)
    vm.set_export_scope(EXPORT_SCOPE_ALL)
    text = await vm.export_csv()
    assert offsets == [0, PAGE_SIZE, PAGE_SIZE + 5]  # 空页后终止（不因 total 未满足而无限循环）
    # 第 2 页短页（5 行）后 offset 按实际行数前进到 55（非 +50 跳页），第 3 页空页终止
    assert len(offsets) == 3
    lines = text.splitlines()
    assert len(lines) == 56  # 表头 + 55 行（实际数据，非 total 宣称的 120）


@pytest.mark.asyncio
async def test_export_csv_all_scope_dao_unavailable_raises_with_message():
    """scope=all 且 DAO 不可用：显式消息 + 抛错由 View 呈现失败，不返回部分文本（R21）。"""
    vm = _vm(None)
    vm.set_export_scope(EXPORT_SCOPE_ALL)
    with pytest.raises(RuntimeError) as exc_info:
        await vm.export_csv()
    assert "dao" in str(exc_info.value).lower()
    assert vm.state.message is not None
    assert vm.state.message.key == "model_usage_dao_unavailable"


@pytest.mark.asyncio
async def test_export_csv_all_scope_dao_error_propagates():
    """DAO 读取失败沿既有降级边界向上传播（面板呈现「导出失败」），无部分文本。"""
    dao = _dao()
    dao.read_request_page = AsyncMock(side_effect=RuntimeError("db boom"))
    vm = _vm(dao)
    vm.set_export_scope(EXPORT_SCOPE_ALL)
    with pytest.raises(RuntimeError) as exc_info:
        await vm.export_csv()
    assert "db boom" in str(exc_info.value)
    assert vm.state.message is None  # 失败路径不写 export_ready（不伪装成功，R21）


@pytest.mark.asyncio
async def test_export_csv_all_scope_uses_filter_snapshot_when_filter_changes_mid_flight():
    """对抗（F37）：导出途中筛选变化，本次导出仍按发起时过滤快照完成，口径不混；
    中途重载使请求代次前移，就绪消息被守卫抑制、不覆盖新加载结果的消息。"""
    calls: list[dict] = []

    async def _read(**kwargs: Any) -> tuple[list[dict], int]:
        calls.append(dict(kwargs))
        if len(calls) == 1:
            # 导出首页读取在途期间，用户改筛选（触发 set_filter → load → 第 2 次调用）
            await vm.set_filter(scenario_id="news_annotation")
            return ([{"invocation_id": "inv-x"}], 1)
        raise RuntimeError("reload boom")  # 中途 set_filter 的 load 失败

    dao = _dao()
    dao.read_request_page = _read
    vm = _vm(dao)
    vm.set_export_scope(EXPORT_SCOPE_ALL)

    text = await vm.export_csv()

    assert calls[0]["scenario_id"] is None  # 导出用发起时快照口径
    assert calls[1]["scenario_id"] == "news_annotation"  # 中途重载是新口径
    assert "inv-x" in text  # 导出按快照口径完成
    assert vm.state.message is not None
    assert vm.state.message.key == "model_usage_load_failed"  # export_ready 未覆盖失败消息


@pytest.mark.asyncio
async def test_export_csv_monthly_summary_uses_viewed_month():
    """F36 移交（F37 落地）：月汇总导出对齐查看月——切换历史月后导出该月数据，不混当前月。"""
    history_summary = {
        "month": "202512",
        "known_cny": Decimal("0.50"),
        "effective_reserved_cny": Decimal("0"),
        "unconfirmed_reserved_cny": Decimal("0"),
    }

    async def _summary_by_month(month: str) -> dict:
        if month == "202512":
            return history_summary
        return {
            "month": "202601",
            "known_cny": Decimal("0.05"),
            "effective_reserved_cny": Decimal("0.10"),
            "unconfirmed_reserved_cny": Decimal("0.02"),
        }

    dao = _dao()
    dao.read_budget_summary = _summary_by_month
    vm = _vm(dao)
    await vm.enter_monthly_summary()  # 默认查看当前月 202601
    await vm.select_summary_month("2025-12")  # 切到历史月 202512

    text = await vm.export_csv()

    data_row = text.splitlines()[1]
    assert "202512" in data_row
    assert "0.50" in data_row
    assert "0.05" not in data_row and "202601" not in data_row  # 不混当前月


@pytest.mark.asyncio
async def test_export_csv_monthly_summary_missing_budget_writes_empty_cells():
    """R21：月汇总摘要未就绪（读取失败）→ 全空单元格，不以 0 伪装。"""
    dao = _dao()
    dao.read_budget_summary = AsyncMock(side_effect=RuntimeError("summary boom"))
    vm = _vm(dao)
    await vm.enter_monthly_summary()  # 读取失败降级，summary_budget 仍为 None
    assert vm.state.summary_budget is None

    text = await vm.export_csv()

    cells = text.splitlines()[1].split(",")
    assert cells[0] == "202601"  # 查看月仍在（不为空月伪装数据）
    assert cells[1] == "" and cells[2] == "" and cells[3] == ""
