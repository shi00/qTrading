"""Phase 2A.1 Task 2A.1.13：SystemViewModel 全链路测试。

测试覆盖：
- on_tier_changed 完整链路（set_tier → reload_rate_limiters → clear_capability_cache → probe → _emit_probe_result）
- _emit_probe_result 三态分类（completed / tier_too_high / all_failed）
- subscribe + state.probe_result 通知（L771 合规：probe 结果直接放入 state，无 dual-track）
- probe_in_progress state transitions（on_tier_changed / run_probe 执行期间 True→False）
- 无订阅者时 logger.warning 不抛异常（M-5：自动 probe 在 TierApiPanel 未挂载时不静默丢失）
"""

import logging
from collections.abc import Mapping
from unittest.mock import AsyncMock, MagicMock

import pytest

from data.external.tushare_client import TushareClient
from ui.viewmodels.system_viewmodel import ProbeResultRow, SystemViewModel

pytestmark = pytest.mark.unit


# --- Helpers ---


def _count_transitions(snapshots, field_getter, initial) -> int:
    """Count state transitions in snapshots for a given field.

    A transition occurs when consecutive snapshots (including the initial state)
    have different values for the field returned by field_getter(snapshots[i]).
    The `initial` argument represents the state value before any snapshots.
    """
    transitions = 0
    prev = initial
    for s in snapshots:
        value = field_getter(s)
        if value != prev:
            transitions += 1
            prev = value
    return transitions


def _patch_full_chain(monkeypatch, *, probe_results: Mapping[str, bool | None], set_tier_success: bool = True):
    """统一 patch ConfigHandler / TushareClient / ThreadPoolManager。

    使 ThreadPoolManager.run_async 直接调用同步函数（避免线程池依赖），
    TushareClient().probe_api_capabilities 返回 probe_results。
    """
    # spec=TushareClient 强制接口契约（避免 MagicMock 接受任意属性访问导致拼写错误漏检）
    mock_client = MagicMock(spec=TushareClient)
    mock_client.probe_api_capabilities = AsyncMock(return_value=probe_results)
    mock_client.get_capability_cache = MagicMock(return_value=probe_results)
    mock_client.clear_capability_cache = MagicMock()
    mock_client.reload_rate_limiters = MagicMock()
    mock_client.mark_api_available = MagicMock()
    mock_client.mark_api_unavailable = MagicMock()

    async def _mock_run_async(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tp = MagicMock()
    mock_tp.return_value.run_async = _mock_run_async

    mock_ch = MagicMock()
    mock_ch.get_tushare_point_tier.return_value = "points_5000"
    mock_ch.set_tushare_point_tier.return_value = set_tier_success

    monkeypatch.setattr("ui.viewmodels.system_viewmodel.ConfigHandler", mock_ch)
    monkeypatch.setattr("data.external.tushare_client.TushareClient", lambda: mock_client)
    monkeypatch.setattr("utils.thread_pool.ThreadPoolManager", mock_tp)
    return mock_client, mock_ch


def _subscribe(vm: SystemViewModel) -> list:
    """Subscribe to vm state changes and return the snapshots list."""
    snapshots: list = []
    vm.subscribe(lambda s: snapshots.append(s))
    return snapshots


@pytest.mark.asyncio
async def test_on_tier_changed_full_chain(monkeypatch):
    """on_tier_changed 完整链路：set_tier → reload_rate_limiters → clear_capability_cache → probe → _emit_probe_result。

    probe 返回 3 个 True + 1 个 False（非全失败、非 >50% False），应分类为 completed。
    state.probe_result 应被设置为 ProbeResultRow(type="completed")（L771 合规，无 dual-track）。
    """
    probe_results = {
        "daily": True,
        "fina_indicator": True,
        "moneyflow": True,
        "cyq_perf": False,  # 1/4 = 25% False，不触发 tier_too_high
    }
    mock_client, mock_ch = _patch_full_chain(monkeypatch, probe_results=probe_results)

    vm = SystemViewModel()
    snapshots = _subscribe(vm)

    result = await vm.on_tier_changed("points_5000")

    # 链路 1：set_tushare_point_tier 被调用
    mock_ch.set_tushare_point_tier.assert_called_once_with("points_5000")
    # 链路 2：reload_rate_limiters 被调用
    mock_client.reload_rate_limiters.assert_called_once()
    # 链路 3：clear_capability_cache 被调用
    mock_client.clear_capability_cache.assert_called_once()
    # 链路 4：probe_api_capabilities 被调用
    mock_client.probe_api_capabilities.assert_called_once()
    # 链路 5：state.probe_result 被设置（L771 合规，无 dual-track version）
    assert any(s.probe_result is not None for s in snapshots)
    assert vm.state.probe_result is not None
    assert vm.state.probe_result.type == "completed"
    assert vm.state.probe_result.tier == "points_5000"
    assert vm.state.probe_result.available == 3
    assert vm.state.probe_result.unavailable == 1
    assert vm.state.probe_result.unknown == 0
    # 返回值仍为 dict（供测试断言）
    assert result["type"] == "completed"
    assert result["tier"] == "points_5000"
    assert result["available"] == 3
    assert result["unavailable"] == 1
    assert result["unknown"] == 0


@pytest.mark.asyncio
async def test_on_tier_changed_too_high_detection(monkeypatch):
    """_emit_probe_result：unavailable/total > 0.5 时分类为 tier_too_high。"""
    probe_results = {
        "daily": True,
        "fina_indicator": False,
        "moneyflow": False,
        "cyq_perf": False,  # 3/4 = 75% False，触发 tier_too_high
    }
    _patch_full_chain(monkeypatch, probe_results=probe_results)

    vm = SystemViewModel()
    snapshots = _subscribe(vm)

    result = await vm.on_tier_changed("points_15000")

    assert result["type"] == "tier_too_high"
    assert result["tier"] == "points_15000"
    assert result["false_count"] == 3
    assert result["total"] == 4
    assert any(s.probe_result is not None for s in snapshots)
    assert vm.state.probe_result is not None  # L771 合规: probe_result 直接放入 state
    assert vm.state.probe_result.type == "tier_too_high"


@pytest.mark.asyncio
async def test_on_tier_changed_all_failed_detection(monkeypatch):
    """_emit_probe_result：全部 False 时分类为 all_failed。"""
    probe_results = {
        "daily": False,
        "fina_indicator": False,
        "moneyflow": False,
        "cyq_perf": False,  # 4/4 = 100% False，触发 all_failed
    }
    _patch_full_chain(monkeypatch, probe_results=probe_results)

    vm = SystemViewModel()
    snapshots = _subscribe(vm)

    result = await vm.on_tier_changed("points_15000")

    assert result["type"] == "all_failed"
    assert result["tier"] == "points_15000"
    assert any(s.probe_result is not None for s in snapshots)
    assert vm.state.probe_result is not None  # L771 合规: probe_result 直接放入 state
    assert vm.state.probe_result.type == "all_failed"


@pytest.mark.asyncio
async def test_viewmodel_no_subscribers_logs_warning(monkeypatch, caplog):
    """无订阅者时 logger.warning 不抛异常（M-5：自动 probe 在 TierApiPanel 未挂载时不静默丢失）。

    L771 合规: probe 结果直接放入 state.probe_result, 无 dual-track _last_probe_result.
    """
    probe_results = {"daily": True, "fina_indicator": True}
    _patch_full_chain(monkeypatch, probe_results=probe_results)

    vm = SystemViewModel()
    # 不 subscribe（无订阅者）

    with caplog.at_level(logging.WARNING, logger="ui.viewmodels.system_viewmodel"):
        result = await vm.on_tier_changed("points_5000")

    # 应记录 warning（M-5：无订阅者时提示 TierApiPanel.did_mount 主动拉取）
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("no subscribers" in msg for msg in warning_messages)
    # 返回值仍为 completed（不应因无订阅者而失败）
    assert result["type"] == "completed"
    assert result["available"] == 2
    # state.probe_result 仍存储（L771 合规: 直接放入 state, 供 View 挂载后读取）
    assert vm.state.probe_result is not None
    assert vm.state.probe_result.type == "completed"
    assert vm.state.probe_result.available == 2


# ---------------------------------------------------------------------------
# 补充覆盖：get_capability_cache / set_tier 异常路径 / set_tier 返回 False /
# probe 返回空时快照恢复 / run_probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_capability_cache_returns_client_cache(monkeypatch):
    """get_capability_cache 返回 TushareClient().get_capability_cache() 的结果。"""
    cache = {"daily": True, "fina_indicator": False, "moneyflow": None}
    mock_client = MagicMock(spec=TushareClient)
    mock_client.get_capability_cache = MagicMock(return_value=cache)
    monkeypatch.setattr("data.external.tushare_client.TushareClient", lambda: mock_client)

    vm = SystemViewModel()
    result = vm.get_capability_cache()

    assert result is cache
    mock_client.get_capability_cache.assert_called_once()


@pytest.mark.asyncio
async def test_on_tier_changed_set_tier_exception_notifies_state(monkeypatch):
    """set_tushare_point_tier 抛异常：通过 state 通知 type=set_tier_failed，返回 dict。"""
    mock_client = MagicMock(spec=TushareClient)
    mock_client.probe_api_capabilities = AsyncMock(return_value={"daily": True})
    mock_client.get_capability_cache = MagicMock(return_value={"daily": True})
    mock_client.clear_capability_cache = MagicMock()
    mock_client.reload_rate_limiters = MagicMock()

    async def _mock_run_async(task_type, func, *args, **kwargs):
        # set_tushare_point_tier 抛异常 → run_async 传播
        return func(*args, **kwargs)

    mock_tp = MagicMock()
    mock_tp.return_value.run_async = _mock_run_async

    mock_ch = MagicMock()
    mock_ch.get_tushare_point_tier.return_value = "points_5000"
    mock_ch.set_tushare_point_tier.side_effect = OSError("config file read-only")

    monkeypatch.setattr("ui.viewmodels.system_viewmodel.ConfigHandler", mock_ch)
    monkeypatch.setattr("data.external.tushare_client.TushareClient", lambda: mock_client)
    monkeypatch.setattr("utils.thread_pool.ThreadPoolManager", mock_tp)

    vm = SystemViewModel()
    snapshots = _subscribe(vm)

    result = await vm.on_tier_changed("points_15000")

    assert result["type"] == "set_tier_failed"
    assert result["tier"] == "points_5000"  # 回滚到旧档位
    assert "config file read-only" in result["error"]
    assert any(s.probe_result is not None for s in snapshots)
    assert vm.state.probe_result is not None  # L771 合规: probe_result 直接放入 state
    assert vm.state.probe_result.type == "set_tier_failed"
    assert "config file read-only" in vm.state.probe_result.error
    # 链路应在此中断，未调用 reload/clear/probe
    mock_client.reload_rate_limiters.assert_not_called()
    mock_client.clear_capability_cache.assert_not_called()


@pytest.mark.asyncio
async def test_on_tier_changed_set_tier_returns_false_notifies_state(monkeypatch):
    """set_tushare_point_tier 返回 False（档位无效）：通知 set_tier_failed，链路中断。"""
    mock_client = MagicMock(spec=TushareClient)
    mock_client.probe_api_capabilities = AsyncMock(return_value={"daily": True})
    mock_client.get_capability_cache = MagicMock(return_value={"daily": True})
    mock_client.clear_capability_cache = MagicMock()
    mock_client.reload_rate_limiters = MagicMock()

    async def _mock_run_async(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tp = MagicMock()
    mock_tp.return_value.run_async = _mock_run_async

    mock_ch = MagicMock()
    mock_ch.get_tushare_point_tier.return_value = "points_5000"
    mock_ch.set_tushare_point_tier.return_value = False  # 档位无效

    monkeypatch.setattr("ui.viewmodels.system_viewmodel.ConfigHandler", mock_ch)
    monkeypatch.setattr("data.external.tushare_client.TushareClient", lambda: mock_client)
    monkeypatch.setattr("utils.thread_pool.ThreadPoolManager", mock_tp)

    vm = SystemViewModel()
    snapshots = _subscribe(vm)

    result = await vm.on_tier_changed("invalid_tier")

    assert result["type"] == "set_tier_failed"
    assert result["tier"] == "points_5000"
    assert any(s.probe_result is not None for s in snapshots)
    assert vm.state.probe_result is not None  # L771 合规: probe_result 直接放入 state
    assert vm.state.probe_result.type == "set_tier_failed"
    # 链路中断
    mock_client.reload_rate_limiters.assert_not_called()
    mock_client.probe_api_capabilities.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_tier_changed_probe_returns_empty_restores_snapshot(monkeypatch, caplog):
    """probe 返回空 dict 时，从 clear 前的快照恢复 capability cache（v1.9.0 M-1）。"""
    snapshot = {"daily": True, "fina_indicator": False, "moneyflow": None}
    mock_client = MagicMock(spec=TushareClient)
    mock_client.probe_api_capabilities = AsyncMock(return_value={})  # 空 dict
    # get_capability_cache 被调用 2 次：snapshot 前快照 + 恢复后读取
    mock_client.get_capability_cache = MagicMock(side_effect=[snapshot, snapshot])
    mock_client.clear_capability_cache = MagicMock()
    mock_client.reload_rate_limiters = MagicMock()
    mock_client.mark_api_available = MagicMock()
    mock_client.mark_api_unavailable = MagicMock()

    async def _mock_run_async(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tp = MagicMock()
    mock_tp.return_value.run_async = _mock_run_async

    mock_ch = MagicMock()
    mock_ch.get_tushare_point_tier.return_value = "points_5000"
    mock_ch.set_tushare_point_tier.return_value = True

    monkeypatch.setattr("ui.viewmodels.system_viewmodel.ConfigHandler", mock_ch)
    monkeypatch.setattr("data.external.tushare_client.TushareClient", lambda: mock_client)
    monkeypatch.setattr("utils.thread_pool.ThreadPoolManager", mock_tp)

    vm = SystemViewModel()
    snapshots = _subscribe(vm)

    with caplog.at_level(logging.INFO, logger="ui.viewmodels.system_viewmodel"):
        result = await vm.on_tier_changed("points_5000")

    # 快照中 True → mark_api_available，False → mark_api_unavailable，None → 跳过
    mock_client.mark_api_available.assert_called_once_with("daily")
    mock_client.mark_api_unavailable.assert_called_once_with("fina_indicator")
    # 恢复日志
    restore_logs = [r for r in caplog.records if "restoring pre-clear cache snapshot" in r.message]
    assert len(restore_logs) == 1
    # 最终 results 来自恢复后的 get_capability_cache
    assert result["type"] == "completed"
    assert any(s.probe_result is not None for s in snapshots)


@pytest.mark.asyncio
async def test_run_probe_emits_result_with_current_tier(monkeypatch):
    """run_probe：执行 probe 并通过 _emit_probe_result 分类，使用当前档位。"""
    probe_results = {"daily": True, "fina_indicator": False}
    mock_client = MagicMock(spec=TushareClient)
    mock_client.probe_api_capabilities = AsyncMock(return_value=probe_results)

    mock_ch = MagicMock()
    mock_ch.get_tushare_point_tier.return_value = "points_5000"

    monkeypatch.setattr("data.external.tushare_client.TushareClient", lambda: mock_client)
    monkeypatch.setattr("ui.viewmodels.system_viewmodel.ConfigHandler", mock_ch)

    vm = SystemViewModel()
    snapshots = _subscribe(vm)

    result = await vm.run_probe()

    mock_client.probe_api_capabilities.assert_awaited_once()
    assert result["type"] == "completed"
    assert result["tier"] == "points_5000"
    assert result["available"] == 1
    assert result["unavailable"] == 1
    assert any(s.probe_result is not None for s in snapshots)
    assert vm.state.probe_result is not None  # L771 合规: probe_result 直接放入 state
    assert vm.state.probe_result.type == "completed"
    assert vm.state.probe_result.tier == "points_5000"
    assert vm.state.probe_result.available == 1
    assert vm.state.probe_result.unavailable == 1


@pytest.mark.asyncio
async def test_emit_probe_result_unknown_count_in_completed_payload(monkeypatch):
    """_emit_probe_result：completed payload 包含 unknown_count（None 值计数，M-7）。"""
    probe_results = {"daily": True, "fina_indicator": None, "moneyflow": None}
    _patch_full_chain(monkeypatch, probe_results=probe_results)

    vm = SystemViewModel()
    snapshots = _subscribe(vm)

    result = await vm.on_tier_changed("points_5000")

    assert result["type"] == "completed"
    assert result["available"] == 1
    assert result["unavailable"] == 0
    assert result["unknown"] == 2  # 2 个 None
    assert any(s.probe_result is not None for s in snapshots)


# ---------------------------------------------------------------------------
# Phase 2 新增：probe_in_progress state transition 覆盖
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_tier_changed_probe_in_progress_transitions(monkeypatch):
    """on_tier_changed 执行期间 probe_in_progress: False → True → False（含异常路径重置）。"""
    probe_results = {"daily": True}
    _patch_full_chain(monkeypatch, probe_results=probe_results)

    vm = SystemViewModel()
    snapshots = _subscribe(vm)

    await vm.on_tier_changed("points_5000")

    # 正常路径：probe_in_progress 至少一次 True transition + 一次 False 回落
    transitions = _count_transitions(snapshots, lambda s: s.probe_in_progress, initial=False)
    assert transitions >= 2  # False→True, True→False
    assert snapshots[-1].probe_in_progress is False


@pytest.mark.asyncio
async def test_on_tier_changed_probe_in_progress_reset_on_exception(monkeypatch):
    """set_tier 抛异常时 probe_in_progress 在 finally 中重置为 False。"""
    mock_client = MagicMock(spec=TushareClient)
    mock_client.probe_api_capabilities = AsyncMock(return_value={"daily": True})
    mock_client.get_capability_cache = MagicMock(return_value={"daily": True})
    mock_client.clear_capability_cache = MagicMock()
    mock_client.reload_rate_limiters = MagicMock()

    async def _mock_run_async(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tp = MagicMock()
    mock_tp.return_value.run_async = _mock_run_async

    mock_ch = MagicMock()
    mock_ch.get_tushare_point_tier.return_value = "points_5000"
    mock_ch.set_tushare_point_tier.side_effect = OSError("read-only")

    monkeypatch.setattr("ui.viewmodels.system_viewmodel.ConfigHandler", mock_ch)
    monkeypatch.setattr("data.external.tushare_client.TushareClient", lambda: mock_client)
    monkeypatch.setattr("utils.thread_pool.ThreadPoolManager", mock_tp)

    vm = SystemViewModel()
    snapshots = _subscribe(vm)

    await vm.on_tier_changed("points_15000")

    # 异常路径：finally 仍重置 probe_in_progress
    assert snapshots[-1].probe_in_progress is False
    transitions = _count_transitions(snapshots, lambda s: s.probe_in_progress, initial=False)
    assert transitions >= 2  # False→True, True→False


@pytest.mark.asyncio
async def test_run_probe_probe_in_progress_transitions(monkeypatch):
    """run_probe 执行期间 probe_in_progress: False → True → False。"""
    probe_results = {"daily": True}
    mock_client = MagicMock(spec=TushareClient)
    mock_client.probe_api_capabilities = AsyncMock(return_value=probe_results)

    mock_ch = MagicMock()
    mock_ch.get_tushare_point_tier.return_value = "points_5000"

    monkeypatch.setattr("data.external.tushare_client.TushareClient", lambda: mock_client)
    monkeypatch.setattr("ui.viewmodels.system_viewmodel.ConfigHandler", mock_ch)

    vm = SystemViewModel()
    snapshots = _subscribe(vm)

    await vm.run_probe()

    transitions = _count_transitions(snapshots, lambda s: s.probe_in_progress, initial=False)
    assert transitions >= 2
    assert snapshots[-1].probe_in_progress is False


# ---------------------------------------------------------------------------
# Phase 2 新增：subscribe / dispose 契约
# ---------------------------------------------------------------------------


def test_subscribe_returns_unsubscribe_and_removes_callback():
    """subscribe 返回的回调调用后，订阅者被移除，后续 _notify 不再触发。"""
    vm = SystemViewModel()
    received: list = []
    unsub = vm.subscribe(lambda s: received.append(s))

    vm._set_state(probe_in_progress=True)
    assert len(received) == 1

    unsub()
    vm._set_state(probe_in_progress=False)
    assert len(received) == 1  # unsubscribe 后不再接收


def test_dispose_clears_state_and_subscribers():
    """dispose 清空 _subscribers / _state（L771 合规：无 _last_probe_result 内部字段）。"""
    vm = SystemViewModel()
    received: list = []
    vm.subscribe(lambda s: received.append(s))
    vm._set_state(probe_in_progress=True, probe_result=ProbeResultRow(type="completed"))

    vm.dispose()

    assert vm.state.probe_in_progress is False
    assert vm.state.probe_result is None  # L771 合规: state.probe_result 重置为 None
    # dispose 后 _notify 无订阅者，received 不再增长
    vm._set_state(probe_in_progress=True)
    assert len(received) == 1  # dispose 前的 1 次


def test_unsubscribe_idempotent_when_callback_already_removed():
    """_unsubscribe 在 callback 已不在订阅列表时为 noop（防御性兜底，避免 ValueError）。

    覆盖 subscribe._unsubscribe 内 `if callback in self._subscribers` 为 False 的分支：
    重复调用 unsubscribe 或 dispose 后调用 unsubscribe 不应抛异常。
    """
    vm = SystemViewModel()
    received: list = []
    unsub = vm.subscribe(lambda s: received.append(s))

    unsub()  # 首次：callback 在列表中，正常移除
    # 第二次：callback 已不在列表，应 noop 不抛 ValueError
    unsub()

    vm._set_state(probe_in_progress=True)
    assert len(received) == 0  # 无订阅者，不接收任何通知
