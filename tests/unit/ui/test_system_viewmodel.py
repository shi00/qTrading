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
