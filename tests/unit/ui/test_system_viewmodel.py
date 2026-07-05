"""Phase 2A.1 Task 2A.1.13：SystemViewModel 全链路测试。

测试覆盖：
- on_tier_changed 完整链路（set_tier → reload_rate_limiters → clear_capability_cache → probe → _emit_probe_result）
- _emit_probe_result 三态分类（completed / tier_too_high / all_failed）
- on_probe_completed 回调字段（None 时 warning 不抛异常，赋值时被调用）
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from data.external.tushare_client import TushareClient
from ui.viewmodels.system_viewmodel import SystemViewModel

pytestmark = pytest.mark.unit


def _patch_full_chain(monkeypatch, *, probe_results: dict[str, bool | None], set_tier_success: bool = True):
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


@pytest.mark.asyncio
async def test_on_tier_changed_full_chain(monkeypatch):
    """on_tier_changed 完整链路：set_tier → reload_rate_limiters → clear_capability_cache → probe → _emit_probe_result。

    probe 返回 3 个 True + 1 个 False（非全失败、非 >50% False），应分类为 completed。
    on_probe_completed 回调应被调用，返回 dict type == "completed"。
    """
    probe_results = {
        "daily": True,
        "fina_indicator": True,
        "moneyflow": True,
        "cyq_perf": False,  # 1/4 = 25% False，不触发 tier_too_high
    }
    mock_client, mock_ch = _patch_full_chain(monkeypatch, probe_results=probe_results)

    vm = SystemViewModel()
    callback = MagicMock()
    vm.on_probe_completed = callback

    result = await vm.on_tier_changed("points_5000")

    # 链路 1：set_tushare_point_tier 被调用
    mock_ch.set_tushare_point_tier.assert_called_once_with("points_5000")
    # 链路 2：reload_rate_limiters 被调用
    mock_client.reload_rate_limiters.assert_called_once()
    # 链路 3：clear_capability_cache 被调用
    mock_client.clear_capability_cache.assert_called_once()
    # 链路 4：probe_api_capabilities 被调用
    mock_client.probe_api_capabilities.assert_called_once()
    # 链路 5：on_probe_completed 回调被调用
    callback.assert_called_once()
    # 返回值分类为 completed
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
    callback = MagicMock()
    vm.on_probe_completed = callback

    result = await vm.on_tier_changed("points_15000")

    assert result["type"] == "tier_too_high"
    assert result["tier"] == "points_15000"
    assert result["false_count"] == 3
    assert result["total"] == 4
    callback.assert_called_once()


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
    callback = MagicMock()
    vm.on_probe_completed = callback

    result = await vm.on_tier_changed("points_15000")

    assert result["type"] == "all_failed"
    assert result["tier"] == "points_15000"
    callback.assert_called_once()


@pytest.mark.asyncio
async def test_viewmodel_probe_completed_callback(monkeypatch, caplog):
    """on_probe_completed 回调字段：None 时 logger.warning 不抛异常，赋值时被调用。"""
    probe_results = {"daily": True, "fina_indicator": True}
    _patch_full_chain(monkeypatch, probe_results=probe_results)

    vm = SystemViewModel()
    # 不赋值 on_probe_completed（默认 None）
    assert vm.on_probe_completed is None

    with caplog.at_level(logging.WARNING, logger="ui.viewmodels.system_viewmodel"):
        result = await vm.on_tier_changed("points_5000")

    # 应记录 warning（M-5：自动 probe 在 TierApiPanel 未挂载时静默丢失 → 改为 warning）
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("on_probe_completed is None" in msg for msg in warning_messages)
    # 返回值仍为 completed（不应因回调缺失而失败）
    assert result["type"] == "completed"
    assert result["available"] == 2


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
async def test_on_tier_changed_set_tier_exception_notifies_callback(monkeypatch):
    """set_tushare_point_tier 抛异常：通知 on_probe_completed type=set_tier_failed，返回 dict。"""
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
    callback = MagicMock()
    vm.on_probe_completed = callback

    result = await vm.on_tier_changed("points_15000")

    assert result["type"] == "set_tier_failed"
    assert result["tier"] == "points_5000"  # 回滚到旧档位
    assert "config file read-only" in result["error"]
    callback.assert_called_once()
    # 链路应在此中断，未调用 reload/clear/probe
    mock_client.reload_rate_limiters.assert_not_called()
    mock_client.clear_capability_cache.assert_not_called()


@pytest.mark.asyncio
async def test_on_tier_changed_set_tier_returns_false_notifies_callback(monkeypatch):
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
    callback = MagicMock()
    vm.on_probe_completed = callback

    result = await vm.on_tier_changed("invalid_tier")

    assert result["type"] == "set_tier_failed"
    assert result["tier"] == "points_5000"
    callback.assert_called_once()
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
    vm.on_probe_completed = MagicMock()

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
    callback = MagicMock()
    vm.on_probe_completed = callback

    result = await vm.run_probe()

    mock_client.probe_api_capabilities.assert_awaited_once()
    assert result["type"] == "completed"
    assert result["tier"] == "points_5000"
    assert result["available"] == 1
    assert result["unavailable"] == 1
    callback.assert_called_once()


@pytest.mark.asyncio
async def test_emit_probe_result_unknown_count_in_completed_payload(monkeypatch):
    """_emit_probe_result：completed payload 包含 unknown_count（None 值计数，M-7）。"""
    probe_results = {"daily": True, "fina_indicator": None, "moneyflow": None}
    _patch_full_chain(monkeypatch, probe_results=probe_results)

    vm = SystemViewModel()
    vm.on_probe_completed = MagicMock()

    result = await vm.on_tier_changed("points_5000")

    assert result["type"] == "completed"
    assert result["available"] == 1
    assert result["unavailable"] == 0
    assert result["unknown"] == 2  # 2 个 None
