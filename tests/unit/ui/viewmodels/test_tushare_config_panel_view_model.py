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

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_config_handler():
    """Mock ConfigHandler 模块级 patch（VM 构造时加载配置）。"""
    with patch("ui.viewmodels.tushare_config_panel_view_model.ConfigHandler") as m:
        m.get_token.return_value = ""
        m.save_token.return_value = True
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
    async def test_verify_token_success_no_probe(self, mock_config_handler, mock_thread_pool):
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token.return_value = False  # needs_probe = False

        with (
            patch("tushare.set_token") as mock_ts_set_token,
            patch("tushare.pro_api", return_value=mock_pro),
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
        ):
            vm = _make_vm(mock_config_handler)
            vm.update_token("valid_token")
            result = await vm.verify_token()

        assert result is True
        mock_ts_set_token.assert_called_once_with("valid_token")
        mock_pro.trade_cal.assert_called_once()
        mock_config_handler.save_token.assert_called_once_with("valid_token")
        mock_client.set_token.assert_called_once_with("valid_token")
        assert vm.state.status_type == "success"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "tushare_verify_success"
        assert vm.state.is_verifying is False

    @pytest.mark.asyncio
    async def test_verify_token_success_with_on_verify_success(self, mock_config_handler, mock_thread_pool):
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token.return_value = False
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
        mock_client.set_token.return_value = True  # needs_probe = True
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
        mock_client.set_token.return_value = True
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
        assert "top_list" in vm.state.status_message.params["default"]

    @pytest.mark.asyncio
    async def test_verify_token_probe_empty_results(self, mock_config_handler, mock_thread_pool):
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token.return_value = True
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
        assert "unknown" in vm.state.status_message.params["default"].lower()

    @pytest.mark.asyncio
    async def test_verify_token_probe_exception_non_fatal(self, mock_config_handler, mock_thread_pool):
        mock_pro = _make_mock_pro_api()
        mock_client = MagicMock()
        mock_client.set_token.return_value = True
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
        assert "unknown" in vm.state.status_message.params["default"].lower()

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
        mock_client.set_token.return_value = False
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
        mock_client.set_token.return_value = False
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
