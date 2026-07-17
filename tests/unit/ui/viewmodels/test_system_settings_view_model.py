"""SystemSettingsViewModel 单元测试 (Task 5.2 TDD Red).

测试 VM state/commands, 不依赖 Flet 渲染。
覆盖：
- frozen state 不可变 (SystemSettingsState)
- 保存成功/失败/取消/重复提交 (save_language/save_theme/save_log_level/
  save_concurrency/save_db_pool/save_thread_pool/save_no_proxy)
- 构造注入 ConfigHandler/ThreadPoolManager
- 同步阻塞操作走 ThreadPoolManager (R16)
- R2 CancelledError 显式 raise
- subscribe / _notify / dispose
"""

import asyncio
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ui.viewmodels.system_settings_view_model import (
    SystemSettingsState,
    SystemSettingsViewModel,
)
from utils.thread_pool import TaskType

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_config_handler():
    """Mock ConfigHandler 模块级 patch (VM 构造时加载配置)。"""
    with patch("ui.viewmodels.system_settings_view_model.ConfigHandler") as m:
        m.get_locale.return_value = "zh_CN"
        m.get_theme_name.return_value = "dark"
        m.get_sync_max_concurrent_heavy.return_value = 4
        m.get_log_level.return_value = "INFO"
        m.get_db_connection_pool_size.return_value = 5
        m.get_db_max_overflow.return_value = 10
        m.get_db_pool_timeout.return_value = 30
        m.get_max_io_workers.return_value = 8
        m.get_max_cpu_workers.return_value = 4
        m.get_no_proxy_domains.return_value = []
        m.set_locale.return_value = True
        m.set_theme_name.return_value = True
        m.set_log_level.return_value = True
        m.set_sync_max_concurrent_heavy.return_value = True
        m.set_db_connection_pool_size.return_value = True
        m.set_db_max_overflow.return_value = True
        m.set_db_pool_timeout.return_value = True
        m.set_max_io_workers.return_value = True
        m.set_max_cpu_workers.return_value = True
        m.set_no_proxy_domains.return_value = True
        yield m


@pytest.fixture
def mock_thread_pool():
    """Mock ThreadPoolManager.run_async 为同步 passthrough。"""

    async def _passthrough(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_passthrough)
    mock_tpm.reload_config = MagicMock()
    mock_tpm.submit = MagicMock()
    with patch(
        "ui.viewmodels.system_settings_view_model.ThreadPoolManager",
        return_value=mock_tpm,
    ) as p:
        yield mock_tpm, p


def _make_vm(mock_config_handler) -> SystemSettingsViewModel:
    return SystemSettingsViewModel()


# --- State immutability ---


class TestStateImmutability:
    def test_state_is_frozen(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        with pytest.raises(FrozenInstanceError):
            vm.state.language_value = "en_US"  # type: ignore[misc]

    def test_state_default_values(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        assert vm.state.language_value == "zh_CN"
        assert vm.state.theme_value == "dark"
        assert vm.state.concurrency_value == "4"
        assert vm.state.log_level_value == "INFO"
        assert vm.state.pool_size_value == "5"
        assert vm.state.db_overflow_value == "10"
        assert vm.state.db_timeout_value == "30"
        assert vm.state.io_workers_value == "8"
        assert vm.state.cpu_workers_value == "4"
        assert vm.state.no_proxy_value == ""
        assert vm.state.is_saving is False


# --- Subscribe / notify ---


class TestSubscribeNotify:
    def test_subscribe_receives_state_changes(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[SystemSettingsState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.set_language_value("en_US")
        assert len(received) == 1
        assert received[0].language_value == "en_US"

    def test_unsubscribe_stops_receiving(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[SystemSettingsState] = []
        unsub = vm.subscribe(lambda s: received.append(s))
        unsub()
        vm.set_language_value("en_US")
        assert len(received) == 0

    def test_dispose_clears_subscribers(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[SystemSettingsState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.dispose()
        vm.set_language_value("en_US")
        assert len(received) == 0


# --- save_language ---


class TestSaveLanguage:
    @pytest.mark.asyncio
    async def test_save_language_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_language("en_US")
        assert result is True
        mock_config_handler.set_locale.assert_called_once_with("en_US")

    @pytest.mark.asyncio
    async def test_save_language_failure_returns_false(self, mock_config_handler, mock_thread_pool):
        mock_config_handler.set_locale.return_value = False
        vm = _make_vm(mock_config_handler)
        result = await vm.save_language("en_US")
        assert result is False

    @pytest.mark.asyncio
    async def test_save_language_io_exception_returns_false(self, mock_config_handler, mock_thread_pool):
        mock_thread_pool[0].run_async = AsyncMock(side_effect=RuntimeError("disk full"))
        vm = _make_vm(mock_config_handler)
        result = await vm.save_language("en_US")
        assert result is False

    @pytest.mark.asyncio
    async def test_save_language_cancelled_error_propagates(self, mock_config_handler, mock_thread_pool):
        mock_thread_pool[0].run_async = AsyncMock(side_effect=asyncio.CancelledError())
        vm = _make_vm(mock_config_handler)
        with pytest.raises(asyncio.CancelledError):
            await vm.save_language("en_US")

    @pytest.mark.asyncio
    async def test_save_language_duplicate_submit_returns_false(self, mock_config_handler, mock_thread_pool):
        # 模拟第一次未完成时第二次发起
        call_count = 0

        async def _slow_run_async(task_type, func, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 第一次：阻塞中, 让重复提交检测命中
                # 通过手动置 is_saving=True 来模拟并发场景（实际场景下锁会阻止真正并发）
                vm._state = SystemSettingsState(
                    language_value=vm.state.language_value,
                    theme_value=vm.state.theme_value,
                    concurrency_value=vm.state.concurrency_value,
                    log_level_value=vm.state.log_level_value,
                    pool_size_value=vm.state.pool_size_value,
                    db_overflow_value=vm.state.db_overflow_value,
                    db_timeout_value=vm.state.db_timeout_value,
                    io_workers_value=vm.state.io_workers_value,
                    cpu_workers_value=vm.state.cpu_workers_value,
                    no_proxy_value=vm.state.no_proxy_value,
                    is_saving=True,
                )
                return func(*args, **kwargs)
            return func(*args, **kwargs)

        mock_thread_pool[0].run_async = AsyncMock(side_effect=_slow_run_async)
        vm = _make_vm(mock_config_handler)
        # 模拟 in-flight 状态
        from dataclasses import replace

        vm._state = replace(vm._state, is_saving=True)
        result = await vm.save_language("en_US")
        assert result is False


# --- save_theme ---


class TestSaveTheme:
    @pytest.mark.asyncio
    async def test_save_theme_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_theme("light")
        assert result is True
        mock_config_handler.set_theme_name.assert_called_once_with("light")

    @pytest.mark.asyncio
    async def test_save_theme_failure_returns_false(self, mock_config_handler, mock_thread_pool):
        mock_config_handler.set_theme_name.return_value = False
        vm = _make_vm(mock_config_handler)
        result = await vm.save_theme("light")
        assert result is False


# --- save_log_level ---


class TestSaveLogLevel:
    @pytest.mark.asyncio
    async def test_save_log_level_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_log_level("DEBUG")
        assert result is True
        mock_config_handler.set_log_level.assert_called_once_with("DEBUG")


# --- save_concurrency ---


class TestSaveConcurrency:
    @pytest.mark.asyncio
    async def test_save_concurrency_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_concurrency("8")
        assert result is True
        mock_config_handler.set_sync_max_concurrent_heavy.assert_called_once_with(8)

    @pytest.mark.asyncio
    async def test_save_concurrency_invalid_range_returns_false(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        # 0 < 1 超出 1-32 范围
        result = await vm.save_concurrency("0")
        assert result is False
        mock_config_handler.set_sync_max_concurrent_heavy.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_concurrency_invalid_format_returns_false(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_concurrency("abc")
        assert result is False
        mock_config_handler.set_sync_max_concurrent_heavy.assert_not_called()


# --- save_db_pool ---


class TestSaveDbPool:
    @pytest.mark.asyncio
    async def test_save_db_pool_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_db_pool("10", "20", "60")
        assert result is True
        mock_config_handler.set_db_connection_pool_size.assert_called_once_with(10)
        mock_config_handler.set_db_max_overflow.assert_called_once_with(20)
        mock_config_handler.set_db_pool_timeout.assert_called_once_with(60)

    @pytest.mark.asyncio
    async def test_save_db_pool_invalid_pool_size_returns_false(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        # 100 > 50 超出范围
        result = await vm.save_db_pool("100", "20", "60")
        assert result is False
        mock_config_handler.set_db_connection_pool_size.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_db_pool_invalid_format_returns_false(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_db_pool("abc", "20", "60")
        assert result is False


# --- save_thread_pool ---


class TestSaveThreadPool:
    @pytest.mark.asyncio
    async def test_save_thread_pool_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_thread_pool("16", "8")
        assert result is True
        mock_config_handler.set_max_io_workers.assert_called_once_with(16)
        mock_config_handler.set_max_cpu_workers.assert_called_once_with(8)
        mock_thread_pool[0].reload_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_thread_pool_empty_returns_false(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_thread_pool("", "8")
        assert result is False
        mock_config_handler.set_max_io_workers.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_thread_pool_invalid_range_returns_false(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        # io=2 < 4
        result = await vm.save_thread_pool("2", "8")
        assert result is False
        mock_config_handler.set_max_io_workers.assert_not_called()


# --- save_no_proxy ---


class TestSaveNoProxy:
    @pytest.mark.asyncio
    async def test_save_no_proxy_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_no_proxy("a.com,b.com")
        assert result is True
        mock_config_handler.set_no_proxy_domains.assert_called_once_with(["a.com", "b.com"])

    @pytest.mark.asyncio
    async def test_save_no_proxy_empty_string_clears(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_no_proxy("")
        assert result is True
        mock_config_handler.set_no_proxy_domains.assert_called_once_with([])

    @pytest.mark.asyncio
    async def test_save_no_proxy_with_whitespace_trims(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_no_proxy("  a.com  ,  b.com  ")
        assert result is True
        mock_config_handler.set_no_proxy_domains.assert_called_once_with(["a.com", "b.com"])


# --- 构造注入 ---


class TestConstructorInjection:
    def test_constructor_accepts_custom_config_handler(self):
        custom_ch = MagicMock()
        custom_ch.get_locale.return_value = "ja_JP"
        custom_ch.get_theme_name.return_value = "navy"
        custom_ch.get_sync_max_concurrent_heavy.return_value = 2
        custom_ch.get_log_level.return_value = "WARNING"
        custom_ch.get_db_connection_pool_size.return_value = 7
        custom_ch.get_db_max_overflow.return_value = 3
        custom_ch.get_db_pool_timeout.return_value = 45
        custom_ch.get_max_io_workers.return_value = 12
        custom_ch.get_max_cpu_workers.return_value = 6
        custom_ch.get_no_proxy_domains.return_value = ["x.com"]
        with (
            patch("ui.viewmodels.system_settings_view_model.ConfigHandler", custom_ch),
            patch("ui.viewmodels.system_settings_view_model.ThreadPoolManager"),
        ):
            vm = SystemSettingsViewModel()
            assert vm.state.language_value == "ja_JP"
            assert vm.state.theme_value == "navy"
            assert vm.state.concurrency_value == "2"
            assert vm.state.log_level_value == "WARNING"
            assert vm.state.pool_size_value == "7"
            assert vm.state.db_overflow_value == "3"
            assert vm.state.db_timeout_value == "45"
            assert vm.state.io_workers_value == "12"
            assert vm.state.cpu_workers_value == "6"
            assert vm.state.no_proxy_value == "x.com"


# --- ThreadPoolManager 调用契约 (R16) ---


class TestThreadPoolOffloadContract:
    @pytest.mark.asyncio
    async def test_save_language_uses_thread_pool(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        await vm.save_language("en_US")
        mock_thread_pool[0].run_async.assert_called_once()
        # 第一个参数应为 TaskType.IO
        args, _ = mock_thread_pool[0].run_async.call_args
        assert args[0] is TaskType.IO

    @pytest.mark.asyncio
    async def test_save_thread_pool_calls_reload_config(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        await vm.save_thread_pool("16", "8")
        # reload_config 应被调用 (ThreadPoolManager.reload_config)
        mock_thread_pool[0].reload_config.assert_called_once()
