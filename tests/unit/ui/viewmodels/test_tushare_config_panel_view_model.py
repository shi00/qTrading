"""TushareConfigPanelViewModel 单元测试。

测试 VM state/commands，不依赖 Flet 渲染。
VM 是独立类，消费方直接实例化以调用 commands。
"""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ui.viewmodels.tushare_config_panel_view_model import (
    TushareConfigPanelViewModel,
    TushareConfigState,
)
from utils.config.secrets import SaveOutcome

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_config_handler():
    """Mock ConfigHandler 模块级 patch（VM 构造时加载配置）。"""
    with patch("ui.viewmodels.tushare_config_panel_view_model.ConfigHandler") as m:
        m.get_token.return_value = ""
        m.save_token.return_value = SaveOutcome.SAVED
        m.get_tushare_point_tier.return_value = "points_5000"
        m.get_tushare_timeout.return_value = 30
        m.set_tushare_point_tier.return_value = True
        yield m


@pytest.fixture
def mock_thread_pool():
    """Mock ThreadPoolManager.run_async 为同步 passthrough。"""

    async def _passthrough(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_passthrough)
    with patch(
        "ui.viewmodels.tushare_config_panel_view_model.ThreadPoolManager",
        return_value=mock_tpm,
    ):
        yield mock_tpm


def _make_vm(
    mock_config_handler,
    *,
    on_verify_success: Callable[[str], None] | None = None,
    on_save: Callable[[dict], None] | None = None,
    on_change: Callable[[], None] | None = None,
    on_loading_change: Callable[[bool], None] | None = None,
    show_internal_loading: bool = True,
) -> TushareConfigPanelViewModel:
    return TushareConfigPanelViewModel(
        on_verify_success=on_verify_success,
        on_save=on_save,
        on_change=on_change,
        on_loading_change=on_loading_change,
        show_internal_loading=show_internal_loading,
    )


# --- State immutability ---


class TestStateImmutability:
    def test_state_is_frozen(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        with pytest.raises(FrozenInstanceError):
            vm.state.token = "modified"  # type: ignore[misc]

    def test_state_default_values(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        assert vm.state.token == ""
        assert vm.state.tier == "points_5000"
        assert vm.state.is_verifying is False
        assert vm.state.status_message is None
        assert vm.state.status_type == "info"


# --- Config loading ---


class TestConfigLoading:
    def test_load_config_populates_state(self, mock_config_handler):
        mock_config_handler.get_token.return_value = "my_token_123"
        mock_config_handler.get_tushare_point_tier.return_value = "points_2000"
        vm = _make_vm(mock_config_handler)
        assert vm.state.token == "my_token_123"
        assert vm.state.tier == "points_2000"

    def test_load_config_defaults_when_empty(self, mock_config_handler):
        mock_config_handler.get_token.return_value = ""
        mock_config_handler.get_tushare_point_tier.return_value = "points_5000"
        vm = _make_vm(mock_config_handler)
        assert vm.state.token == ""
        assert vm.state.tier == "points_5000"


# --- Update commands ---


class TestUpdateCommands:
    def test_update_token(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_token("new_token")
        assert vm.state.token == "new_token"

    def test_update_token_triggers_on_change(self, mock_config_handler):
        on_change = MagicMock()
        vm = _make_vm(mock_config_handler, on_change=on_change)
        vm.update_token("new_token")
        on_change.assert_called_once()

    def test_update_token_without_on_change_no_error(self, mock_config_handler):
        vm = _make_vm(mock_config_handler, on_change=None)
        vm.update_token("new_token")  # 不应抛异常


# --- get_config / set_config ---


class TestGetSetConfig:
    def test_get_current_config_returns_token(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_token("  my_token  ")
        config = vm.get_current_config()
        assert config == {"token": "my_token"}

    def test_get_current_config_empty_token(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        config = vm.get_current_config()
        assert config == {"token": ""}

    def test_set_config_updates_token(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.set_config({"token": "set_token"})
        assert vm.state.token == "set_token"

    def test_set_config_without_token_key_no_change(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.set_config({"other": "value"})
        assert vm.state.token == ""

    def test_set_config_notifies_subscribers(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[TushareConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.set_config({"token": "new"})
        assert len(received) == 1
        assert received[0].token == "new"


# --- reload_config ---


class TestReloadConfig:
    def test_reload_config_reloads_from_config_handler(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        vm.update_token("modified")
        assert vm.state.token == "modified"
        mock_config_handler.get_token.return_value = "reloaded_token"
        mock_config_handler.get_tushare_point_tier.return_value = "points_10000"
        vm.reload_config()
        assert vm.state.token == "reloaded_token"
        assert vm.state.tier == "points_10000"

    def test_reload_config_notifies_subscribers(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[TushareConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.reload_config()
        assert len(received) == 1


# --- Subscribe / notify ---


class TestSubscribeNotify:
    def test_subscribe_receives_state_changes(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[TushareConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.update_token("new_token")
        assert len(received) == 1
        assert received[0].token == "new_token"

    def test_unsubscribe_stops_receiving(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[TushareConfigState] = []
        unsub = vm.subscribe(lambda s: received.append(s))
        unsub()
        vm.update_token("new_token")
        assert len(received) == 0

    def test_multiple_subscribers_all_notified(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received_a: list[TushareConfigState] = []
        received_b: list[TushareConfigState] = []
        vm.subscribe(lambda s: received_a.append(s))
        vm.subscribe(lambda s: received_b.append(s))
        vm.update_token("new_token")
        assert len(received_a) == 1
        assert len(received_b) == 1

    def test_dispose_clears_subscribers(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[TushareConfigState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.dispose()
        vm.update_token("new_token")
        assert len(received) == 0


# --- update_tier (async) ---


class TestOpenRegistrationUrl:
    """open_registration_url: R16 webbrowser.open_new_tab 通过 ThreadPoolManager offload."""

    @pytest.mark.asyncio
    async def test_open_registration_url_offloads_to_thread_pool(self, mock_config_handler, mock_thread_pool):
        """DoD: 调 ThreadPoolManager.run_async(TaskType.IO, webbrowser.open_new_tab, url)."""
        with patch("ui.viewmodels.tushare_config_panel_view_model.webbrowser") as mock_webbrowser:
            vm = _make_vm(mock_config_handler)
            await vm.open_registration_url()

        mock_thread_pool.run_async.assert_awaited_once()
        call_args = mock_thread_pool.run_async.call_args
        assert call_args.args[0].name == "IO"  # TaskType.IO
        assert call_args.args[1] is mock_webbrowser.open_new_tab
        assert call_args.args[2] == "https://tushare.pro/register?reg=728426"
        mock_webbrowser.open_new_tab.assert_called_once_with("https://tushare.pro/register?reg=728426")


# --- update_tier (async) ---


class TestUpdateTier:
    @pytest.mark.asyncio
    async def test_update_tier_success(self, mock_config_handler, mock_thread_pool):
        mock_config_handler.get_tushare_point_tier.return_value = "points_5000"
        mock_client = MagicMock()
        mock_client.reload_rate_limiters = MagicMock(return_value=None)
        mock_client.clear_capability_cache = MagicMock()

        with patch("data.external.tushare_client.TushareClient", return_value=mock_client):
            vm = _make_vm(mock_config_handler)
            result = await vm.update_tier("points_2000")

        assert result is True
        mock_config_handler.set_tushare_point_tier.assert_called_once_with("points_2000")
        mock_client.reload_rate_limiters.assert_called_once()
        mock_client.clear_capability_cache.assert_called_once()
        assert vm.state.tier == "points_2000"
        assert vm.state.status_type == "success"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "sys_tier_saved_success"

    @pytest.mark.asyncio
    async def test_update_tier_same_value_short_circuit(self, mock_config_handler, mock_thread_pool):
        mock_config_handler.get_tushare_point_tier.return_value = "points_5000"
        vm = _make_vm(mock_config_handler)
        result = await vm.update_tier("points_5000")
        assert result is True
        mock_config_handler.set_tushare_point_tier.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_tier_save_fails_rolls_back(self, mock_config_handler, mock_thread_pool):
        mock_config_handler.get_tushare_point_tier.return_value = "points_5000"
        mock_config_handler.set_tushare_point_tier.return_value = False

        vm = _make_vm(mock_config_handler)
        result = await vm.update_tier("points_2000")

        assert result is False
        assert vm.state.tier == "points_5000"
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "sys_tier_save_failed"

    @pytest.mark.asyncio
    async def test_update_tier_exception_rolls_back(self, mock_config_handler, mock_thread_pool):
        mock_config_handler.get_tushare_point_tier.return_value = "points_5000"
        mock_config_handler.set_tushare_point_tier.side_effect = RuntimeError("DB error")

        vm = _make_vm(mock_config_handler)
        result = await vm.update_tier("points_2000")

        assert result is False
        assert vm.state.tier == "points_5000"
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "sys_tier_save_failed"


# --- verify_token (async) ---


def _make_mock_pro_api():
    """创建 mock ts.pro_api 返回值，trade_cal 为 MagicMock。"""
    mock_pro = MagicMock()
    mock_pro.trade_cal = MagicMock(return_value=MagicMock())
    return mock_pro


class TestVerifyToken:
    @pytest.mark.asyncio
    async def test_verify_token_empty_token_returns_false(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        vm.update_token("")
        result = await vm.verify_token()
        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "tushare_token_required"

    @pytest.mark.asyncio
    async def test_verify_token_reentry_guard(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler, show_internal_loading=False)
        vm.update_token("some_token")
        vm._set_state(is_verifying=True)  # type: ignore[attr-defined]
        result = await vm.verify_token()
        assert result is False
        assert vm.state.status_type == "warning"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "tushare_verifying_in_progress"

    @pytest.mark.asyncio
    async def test_verify_token_entering_verifying_shows_progress_warning(self, mock_config_handler, mock_thread_pool):
        """首次点击进入验证态即输出「验证中」warning 文本（E2E confirm 信号）。"""
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token_async = AsyncMock(return_value=False)  # needs_probe = False
        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
        ):
            vm = _make_vm(mock_config_handler)
            vm.update_token("valid_token")

            # 在 verify_token 内部置 is_verifying=True 后、网络结果返回前捕获中间状态：
            # 让 trade_cal 首次调用时暂停（passthrough 同步执行，此处改为抛出受控
            # 中断来观察中间状态）。
            original_trade_cal = mock_pro.trade_cal

            def _paused_trade_cal(*args, **kwargs):
                # 第一次调用时记录中间状态后正常返回，模拟网络结果到达
                assert vm.state.status_type == "warning"
                assert vm.state.status_message is not None
                assert vm.state.status_message.key == "tushare_verifying_in_progress"
                assert vm.state.is_verifying is True
                return original_trade_cal(*args, **kwargs)

            mock_pro.trade_cal = MagicMock(side_effect=_paused_trade_cal)
            result = await vm.verify_token()

        assert result is True
        assert vm.state.is_verifying is False

    @pytest.mark.asyncio
    async def test_verify_token_success_no_probe(self, mock_config_handler, mock_thread_pool):
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token_async = AsyncMock(return_value=False)  # needs_probe = False

        with (
            patch("tushare.set_token") as mock_ts_set_token,
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
        ):
            vm = _make_vm(mock_config_handler)
            vm.update_token("valid_token")
            result = await vm.verify_token()

        assert result is True
        # Task 2.1: verify_token 全程不得调用 tushare.set_token（避免明文落盘 ~/tk.csv）
        mock_ts_set_token.assert_not_called()
        mock_pro.trade_cal.assert_called_once()
        mock_config_handler.save_token.assert_called_once_with("valid_token")
        # 回归：verify_token 必须用异步 set_token_async（同步包装器 set_token 在工作线程
        # _loop 未捕获时会抛 RuntimeError），不得调用客户端同步 set_token。
        mock_client.set_token_async.assert_awaited_once_with("valid_token")
        mock_client.set_token.assert_not_called()
        assert vm.state.status_type == "success"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "tushare_verify_success"
        assert vm.state.is_verifying is False

    @pytest.mark.asyncio
    async def test_verify_token_never_calls_tushare_set_token(self, mock_config_handler, mock_thread_pool):
        """Task 2.1 TDD: verify_token 全程不得调用 tushare.set_token（token 明文落盘 ~/tk.csv 修复）。

        显式 token 传参（ts.pro_api(token=...)）已足够，无需写全局 SDK 状态。
        断言 mock_tushare 模块上 set_token 属性从未被读取或调用。
        """
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token_async = AsyncMock(return_value=False)

        # 用 mock 模块替换 tushare，捕获所有属性访问
        mock_tushare = MagicMock()
        mock_tushare.pro_api = MagicMock(return_value=mock_pro)
        # set_token 显式留作 MagicMock 以便断言未调用
        mock_tushare.set_token = MagicMock()

        with (
            patch.dict("sys.modules", {"tushare": mock_tushare}),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
        ):
            vm = _make_vm(mock_config_handler)
            vm.update_token("valid_token")
            result = await vm.verify_token()

        assert result is True
        mock_tushare.set_token.assert_not_called()
        # 验证仍走显式 token 传参
        # timeout_val 来自 ConfigHandler.get_tushare_timeout（fixture return_value=30），
        # 不是 mock_client.timeout（MagicMock 对象）
        mock_tushare.pro_api.assert_called_once_with(token="valid_token", timeout=30)

    @pytest.mark.asyncio
    async def test_verify_token_success_with_on_verify_success(self, mock_config_handler, mock_thread_pool):
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token_async = AsyncMock(return_value=False)
        on_verify_success = MagicMock()

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
        ):
            vm = _make_vm(mock_config_handler, on_verify_success=on_verify_success)
            vm.update_token("valid_token")
            await vm.verify_token()

        on_verify_success.assert_called_once_with("valid_token")

    @pytest.mark.asyncio
    async def test_verify_token_probe_available_apis(self, mock_config_handler, mock_thread_pool):
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token_async = AsyncMock(return_value=True)  # needs_probe = True
        mock_client.probe_api_capabilities = AsyncMock(return_value={"daily": True, "moneyflow": True})
        mock_strategy_manager = MagicMock()

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
            patch("strategies.all_strategies.StrategyManager", return_value=mock_strategy_manager),
        ):
            vm = _make_vm(mock_config_handler)
            vm.update_token("valid_token")
            result = await vm.verify_token()

        assert result is True
        mock_client.probe_api_capabilities.assert_called_once()
        mock_strategy_manager.invalidate_dependency_cache.assert_called_once()
        assert vm.state.status_type == "success"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "tushare_verify_success"

    @pytest.mark.asyncio
    async def test_verify_token_probe_unavailable_apis(self, mock_config_handler, mock_thread_pool):
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token_async = AsyncMock(return_value=True)
        mock_client.probe_api_capabilities = AsyncMock(return_value={"daily": True, "top_list": False})
        mock_strategy_manager = MagicMock()

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
            patch("strategies.all_strategies.StrategyManager", return_value=mock_strategy_manager),
        ):
            vm = _make_vm(mock_config_handler)
            vm.update_token("valid_token")
            result = await vm.verify_token()

        assert result is True
        assert vm.state.status_type == "warning"
        assert vm.state.status_message is not None
        assert "top_list" in vm.state.status_message.params["apis"]
        assert vm.state.status_message.key == "tushare_verify_restricted"

    @pytest.mark.asyncio
    async def test_verify_token_probe_empty_results(self, mock_config_handler, mock_thread_pool):
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token_async = AsyncMock(return_value=True)
        mock_client.probe_api_capabilities = AsyncMock(return_value={})
        mock_strategy_manager = MagicMock()

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
            patch("strategies.all_strategies.StrategyManager", return_value=mock_strategy_manager),
        ):
            vm = _make_vm(mock_config_handler)
            vm.update_token("valid_token")
            result = await vm.verify_token()

        assert result is True
        assert vm.state.status_type == "warning"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "tushare_verify_unknown_status"

    @pytest.mark.asyncio
    async def test_verify_token_probe_exception_non_fatal(self, mock_config_handler, mock_thread_pool):
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token_async = AsyncMock(return_value=True)
        mock_client.probe_api_capabilities = AsyncMock(side_effect=RuntimeError("probe failed"))

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
            patch("strategies.all_strategies.StrategyManager"),
        ):
            vm = _make_vm(mock_config_handler)
            vm.update_token("valid_token")
            result = await vm.verify_token()

        assert result is True
        assert vm.state.status_type == "success"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "tushare_verify_unknown_status"

    @pytest.mark.asyncio
    async def test_verify_token_api_failure_returns_false(self, mock_config_handler, mock_thread_pool):
        mock_pro = _make_mock_pro_api()
        mock_pro.trade_cal.side_effect = RuntimeError("API error")

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient"),
        ):
            vm = _make_vm(mock_config_handler)
            vm.update_token("valid_token")
            result = await vm.verify_token()

        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.is_verifying is False

    @pytest.mark.asyncio
    async def test_verify_token_triggers_on_loading_change(self, mock_config_handler, mock_thread_pool):
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token_async = AsyncMock(return_value=False)
        on_loading_change = MagicMock()

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
        ):
            vm = _make_vm(
                mock_config_handler,
                on_loading_change=on_loading_change,
                show_internal_loading=False,
            )
            vm.update_token("valid_token")
            await vm.verify_token()

        on_loading_change.assert_any_call(True)
        on_loading_change.assert_any_call(False)

    @pytest.mark.asyncio
    async def test_verify_token_triggers_on_loading_change_with_internal_loading(
        self, mock_config_handler, mock_thread_pool
    ):
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token_async = AsyncMock(return_value=False)
        on_loading_change = MagicMock()

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
        ):
            vm = _make_vm(
                mock_config_handler,
                on_loading_change=on_loading_change,
                show_internal_loading=True,
            )
            vm.update_token("valid_token")
            await vm.verify_token()

        assert vm.state.is_verifying is False
        on_loading_change.assert_any_call(True)
        on_loading_change.assert_any_call(False)

    @pytest.mark.asyncio
    async def test_verify_token_wait_for_timeout_resets_verifying_flag(self, mock_config_handler, mock_thread_pool):
        """回归：wait_for 超时（TimeoutError）后 is_verifying 必须复位为 False，避免 UI 无限转圈。"""
        mock_pro = _make_mock_pro_api()

        def _timeout_and_close(coro, timeout=None):
            """模拟 wait_for 超时：关闭内部协程再抛 TimeoutError（避免未 await 协程泄漏告警）。"""
            coro.close()
            raise TimeoutError("probe timed out")

        with (
            patch("tushare.pro_api", return_value=mock_pro),
            patch(
                "ui.viewmodels.tushare_config_panel_view_model.asyncio.wait_for",
                side_effect=_timeout_and_close,
            ),
        ):
            vm = _make_vm(mock_config_handler)
            vm.update_token("valid_token")
            result = await vm.verify_token()

        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.is_verifying is False


# --- verify_token: SaveOutcome 四态穷举 (VMCONFIG-F04) ---


class TestVerifyTokenSaveOutcome:
    """F04：仅 SaveOutcome.SAVED 进入配置激活成功链，其余三态拒绝且不激活共享 client。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("save_outcome", "expected_result", "expected_status_type", "expected_message_key"),
        [
            (SaveOutcome.SAVED, True, "success", "tushare_verify_success"),
            (SaveOutcome.FAILED, False, "error", "sys_snack_save_err"),
            (SaveOutcome.FAILED_NO_SECURE_STORE, False, "error", "secrets_save_failed_no_store"),
            (SaveOutcome.OVERRIDDEN_BY_ENV, False, "warning", "secrets_save_overridden"),
        ],
        ids=["saved", "failed", "failed-no-secure-store", "overridden-by-env"],
    )
    async def test_verify_token_exhausts_save_outcome(
        self,
        mock_config_handler,
        mock_thread_pool,
        save_outcome,
        expected_result,
        expected_status_type,
        expected_message_key,
    ):
        """四态参数化：SAVED 正常激活；FAILED/NO_STORE/ENV 均返回 False 且不触达激活链。"""
        mock_config_handler.save_token.return_value = save_outcome
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token_async = AsyncMock(return_value=False)
        on_verify_success = MagicMock()

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
        ):
            vm = _make_vm(mock_config_handler, on_verify_success=on_verify_success)
            vm.update_token("valid_token")
            result = await vm.verify_token()

        # 显式 token 探活对四态均保留（先探活、后保存）
        mock_pro.trade_cal.assert_called_once()
        assert result is expected_result
        assert vm.state.status_type == expected_status_type
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == expected_message_key
        assert vm.state.is_verifying is False

        if save_outcome is SaveOutcome.SAVED:
            # SAVED：唯一进入 set_token_async + 成功回调的路径
            mock_client.set_token_async.assert_awaited_once_with("valid_token")
            on_verify_success.assert_called_once_with("valid_token")
        else:
            # 未持久化/未生效输入：不替换共享 client、不调用成功回调、不写成功提示
            mock_client.set_token_async.assert_not_called()
            mock_client.set_token.assert_not_called()
            mock_client.probe_api_capabilities.assert_not_called()
            on_verify_success.assert_not_called()
            assert vm.state.status_message.key != "tushare_verify_success"

        if save_outcome is SaveOutcome.OVERRIDDEN_BY_ENV:
            # 覆盖提示保留，且携带用户可操作的 env var 名
            assert vm.state.status_message.params["env_var"] == "TS_TOKEN"

    @pytest.mark.asyncio
    async def test_failed_outcome_returns_false_without_success_callback(self, mock_config_handler, mock_thread_pool):
        """FAILED 专项回归：返回 False、错误提示为已有保存失败 Message、无成功 snack。"""
        mock_config_handler.save_token.return_value = SaveOutcome.FAILED
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token_async = AsyncMock(return_value=False)
        on_verify_success = MagicMock()

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
        ):
            vm = _make_vm(mock_config_handler, on_verify_success=on_verify_success)
            vm.update_token("valid_token")
            result = await vm.verify_token()

        assert result is False
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "sys_snack_save_err"
        mock_client.set_token_async.assert_not_called()
        on_verify_success.assert_not_called()


class TestVerifyTokenOnboardingIntegration:
    """F04：向导把 verify_token 的返回值缓存为 token 步已验证，失败态不得误标。"""

    @staticmethod
    def _make_onboarding_vm(monkeypatch, validate_token):
        from ui.viewmodels.onboarding_view_model import OnboardingViewModel

        monkeypatch.setattr(
            "ui.viewmodels.onboarding_view_model.ConfigHandler.get_init_history_years",
            staticmethod(lambda: 3),
        )
        monkeypatch.setattr(
            "ui.viewmodels.onboarding_view_model.ConfigHandler.get_auto_update_time",
            staticmethod(lambda: "16:30"),
        )
        monkeypatch.setattr(
            "ui.viewmodels.onboarding_view_model.ConfigHandler.is_risk_disclaimer_acknowledged",
            staticmethod(lambda: False),
        )
        onboarding_vm = OnboardingViewModel()
        onboarding_vm.bind(
            fn_validate_database=AsyncMock(return_value=True),
            fn_validate_token=validate_token,
            fn_validate_model_scenario=AsyncMock(return_value=True),
            fn_push_schedule_state=MagicMock(),
            on_complete=AsyncMock(),
        )
        onboarding_vm.current_step = 2  # token 步
        return onboarding_vm

    @pytest.mark.asyncio
    async def test_failed_outcome_does_not_mark_token_step_validated(
        self, mock_config_handler, mock_thread_pool, monkeypatch
    ):
        mock_config_handler.save_token.return_value = SaveOutcome.FAILED
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token_async = AsyncMock(return_value=False)

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
        ):
            vm = _make_vm(mock_config_handler)
            vm.update_token("valid_token")
            onboarding_vm = self._make_onboarding_vm(monkeypatch, vm.verify_token)
            result = await onboarding_vm.validate_and_persist_current_step()

        assert result is False
        assert onboarding_vm.is_step_validated("token") is False
        mock_client.set_token_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_saved_outcome_marks_token_step_validated(self, mock_config_handler, mock_thread_pool, monkeypatch):
        """对照组：SAVED 时向导正常缓存 token 步已验证，证明绑定链路有效。"""
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token_async = AsyncMock(return_value=False)

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
        ):
            vm = _make_vm(mock_config_handler)  # fixture 默认 save_token -> SAVED
            vm.update_token("valid_token")
            onboarding_vm = self._make_onboarding_vm(monkeypatch, vm.verify_token)
            result = await onboarding_vm.validate_and_persist_current_step()

        assert result is True
        assert onboarding_vm.is_step_validated("token") is True
        mock_client.set_token_async.assert_awaited_once_with("valid_token")


# --- Status helpers ---


class TestStatusHelpers:
    def test_show_success_sets_status(self, mock_config_handler):
        from ui.viewmodels import Message

        vm = _make_vm(mock_config_handler)
        vm._show_success(Message("test_key"))  # type: ignore[attr-defined]
        assert vm.state.status_type == "success"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "test_key"

    def test_show_error_sets_status(self, mock_config_handler):
        from ui.viewmodels import Message

        vm = _make_vm(mock_config_handler)
        vm._show_error(Message("test_key"))  # type: ignore[attr-defined]
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None

    def test_show_warning_sets_status(self, mock_config_handler):
        from ui.viewmodels import Message

        vm = _make_vm(mock_config_handler)
        vm._show_warning(Message("test_key"))  # type: ignore[attr-defined]
        assert vm.state.status_type == "warning"
        assert vm.state.status_message is not None

    def test_raw_message_wraps_text(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        msg = vm._raw_message("dynamic text")  # type: ignore[attr-defined]
        assert msg.key == "_raw_msg_"
        assert msg.params["default"] == "dynamic text"


# --- Loading state ---


class TestLoadingState:
    def test_set_loading_state_true_with_internal_loading(self, mock_config_handler):
        on_loading_change = MagicMock()
        vm = _make_vm(
            mock_config_handler,
            on_loading_change=on_loading_change,
            show_internal_loading=True,
        )
        vm._set_loading_state(True)  # type: ignore[attr-defined]
        on_loading_change.assert_called_once_with(True)
        assert vm.state.is_verifying is True

    def test_set_loading_state_false_with_internal_loading(self, mock_config_handler):
        on_loading_change = MagicMock()
        vm = _make_vm(
            mock_config_handler,
            on_loading_change=on_loading_change,
            show_internal_loading=True,
        )
        vm._set_state(is_verifying=True)
        vm._set_loading_state(False)  # type: ignore[attr-defined]
        on_loading_change.assert_called_once_with(False)
        assert vm.state.is_verifying is False

    def test_set_loading_state_without_internal_loading(self, mock_config_handler):
        on_loading_change = MagicMock()
        vm = _make_vm(
            mock_config_handler,
            on_loading_change=on_loading_change,
            show_internal_loading=False,
        )
        vm._set_loading_state(True)  # type: ignore[attr-defined]
        on_loading_change.assert_called_once_with(True)
        assert vm.state.is_verifying is False  # 不设置 is_verifying

    def test_set_loading_state_without_on_loading_change(self, mock_config_handler):
        vm = _make_vm(
            mock_config_handler,
            on_loading_change=None,
            show_internal_loading=True,
        )
        vm._set_loading_state(True)  # type: ignore[attr-defined]  # 不应抛异常
        assert vm.state.is_verifying is True
