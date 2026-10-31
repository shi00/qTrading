"""M12-022：ViewModel / mixin 缓存注册单例引用（消除重复构造调用）。

债务 ``P3-M12-ViewModels-Repeated-Singleton-Calls``：``ui/viewmodels/`` 中多处经
``TushareClient()`` / ``TaskManager()`` / ``CacheManager()`` 反复进入各注册单例的
``__new__``。本文件锁定最小契约：**每实例仅构造一次，后续访问复用缓存引用**。

缓存采用惰性 ``cached_property``（而非 ``__init__`` 构造）：单例为进程级资源、
生命周期由 singleton registry 管理（VM dispose 不释放），惰性获取同时保持既有
测试的 patch 时点语义。
"""

from unittest.mock import MagicMock, patch

import pytest

from ui.viewmodels.ai_stream_mixin import AIStreamMixin
from ui.viewmodels.backtest_view_model import BacktestViewModel
from ui.viewmodels.history_mode_mixin import HistoryModeMixin
from ui.viewmodels.screener_view_model import ScreenerViewModel
from ui.viewmodels.system_viewmodel import SystemViewModel
from ui.viewmodels.tushare_config_panel_view_model import TushareConfigPanelViewModel

pytestmark = pytest.mark.unit


class _StreamHost(AIStreamMixin):
    """最小宿主实例：仅验证 mixin 的单例缓存属性，不触达业务方法。"""


class _HistoryHost(HistoryModeMixin):
    """最小宿主实例：仅验证 mixin 的单例缓存属性，不触达业务方法。"""


def test_ai_stream_mixin_caches_task_manager():
    """AIStreamMixin._task_manager：首次访问构造一次，后续复用同一引用。"""
    with patch("ui.viewmodels.ai_stream_mixin.TaskManager") as mock_tm_cls:
        host = _StreamHost()
        first = host._task_manager
        second = host._task_manager

    mock_tm_cls.assert_called_once_with()
    assert first is second
    assert first is mock_tm_cls.return_value


def test_history_mode_mixin_caches_cache_manager():
    """HistoryModeMixin._cache：首次访问构造一次，后续复用同一引用。"""
    with patch("ui.viewmodels.history_mode_mixin.CacheManager") as mock_cache_cls:
        host = _HistoryHost()
        first = host._cache
        second = host._cache

    mock_cache_cls.assert_called_once_with()
    assert first is second
    assert first is mock_cache_cls.return_value


def test_screener_vm_subscription_pair_reuses_single_task_manager():
    """ScreenerViewModel：subscribe + unsubscribe 成对调用只构造一次 TaskManager。"""
    with (
        patch("ui.viewmodels.screener_view_model.DataProcessor"),
        patch("ui.viewmodels.screener_view_model.StrategyManager"),
        patch("ui.viewmodels.screener_view_model.ReviewManager"),
        patch("ui.viewmodels.screener_view_model.TaskManager") as mock_tm_cls,
    ):
        vm = ScreenerViewModel()
        vm.subscribe_task_manager()
        vm.unsubscribe_task_manager()

    mock_tm_cls.assert_called_once_with()
    mock_tm_cls.return_value.subscribe.assert_called_once_with(vm._on_tasks_updated)
    mock_tm_cls.return_value.unsubscribe.assert_called_once_with(vm._on_tasks_updated)


def test_backtest_vm_caches_task_manager():
    """BacktestViewModel._task_manager：多次访问只构造一次，供进度/取消回调复用。"""
    with patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls:
        vm = BacktestViewModel()
        first = vm._task_manager
        second = vm._task_manager

    mock_tm_cls.assert_called_once_with()
    assert first is second
    assert first is mock_tm_cls.return_value


def test_system_vm_caches_tushare_client():
    """SystemViewModel._tushare_client：多个 pass-through 方法共享同一实例。"""
    mock_client = MagicMock()
    with patch("data.external.tushare_client.TushareClient", return_value=mock_client) as mock_cls:
        vm = SystemViewModel()
        assert vm.get_capability_cache() is mock_client.get_capability_cache.return_value
        assert vm.get_last_probe_time() is mock_client.get_last_probe_time.return_value

    mock_cls.assert_called_once_with()


def test_tushare_config_panel_vm_caches_tushare_client():
    """TushareConfigPanelViewModel._tushare_client：档位变更与验证共享同一实例。"""
    with (
        patch("ui.viewmodels.tushare_config_panel_view_model.ConfigHandler") as mock_config_handler,
        patch("data.external.tushare_client.TushareClient") as mock_cls,
    ):
        mock_config_handler.get_token.return_value = ""
        mock_config_handler.get_tushare_point_tier.return_value = "points_5000"
        vm = TushareConfigPanelViewModel()
        first = vm._tushare_client
        second = vm._tushare_client

    mock_cls.assert_called_once_with()
    assert first is second
    assert first is mock_cls.return_value
