"""app/application.py run() 启动编排单元测试（review01-A7 补充）。

application.py 承载原 main.py 的启动编排逻辑，其 run() 主体此前仅由
tests/integration/test_main.py 覆盖（84%），但 coverage.json 只由 unit 阶段写入，
Windows unit job（只跑 tests/unit/）对 application.py 覆盖仅 44.8%，低于
per-file 门禁 80%。本文件在 unit 层复用 test_main.py 的 mock 策略
（_DummyPage + 全依赖 monkeypatch），覆盖 run() 主体，使 unit 阶段覆盖达标。

与 tests/integration/test_main.py 的差异：
- 本文件位于 tests/unit/，由 Windows/Linux unit job 收集（coverage.json 计入）
- 仅覆盖 run() 主流程与关键分支，不重复窗口/对话框交互细节（由 integration 承担）
"""

# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）, 动态属性访问（mock/替身）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.application as app_main
import utils.shutdown as shutdown_mod

pytestmark = pytest.mark.unit


class _DummyWindow:
    def __init__(self):
        self.min_width = 0
        self.min_height = 0
        self.width = 0
        self.height = 0
        self.maximized = False
        self.prevent_close = False
        self.icon = None
        self.on_event = None

    def center(self):
        return None

    def destroy(self):
        pass


class _DummyPage:
    def __init__(self):
        self.window = _DummyWindow()
        self.on_disconnect = None
        self.on_error = None
        self.title = ""
        self.padding = 0
        self.toast = None
        self.controls = []
        self.overlay = []
        self.current_dialog = None
        self.locale_configuration = None
        self.run_task_calls = []

    def add(self, control):
        self.controls.append(control)

    def render(self, component, /, *args, **kwargs):
        """Mock page.render (V1 声明式 API) — 仅记录调用, 不实际渲染。"""
        self.controls.append(component)

    def update(self):
        pass

    def clean(self):
        self.controls = []

    def run_task(self, coro, *args):
        self.run_task_calls.append((coro, args))


class _FakeCoordinator:
    """ShutdownCoordinator 替身：记录调用，不执行真实清理。"""

    def __init__(self, _page, **_kwargs):
        self.cleanup_done = False
        self.start_watchdog_calls = 0
        self.cancel_watchdog_calls = 0
        self.do_cleanup_calls = 0
        self.register_task_calls = []
        _FakeCoordinator.last = self

    def start_watchdog(self, _timeout=None):
        self.start_watchdog_calls += 1

    def cancel_watchdog(self):
        self.cancel_watchdog_calls += 1

    async def do_cleanup(self, **_kwargs):
        self.do_cleanup_calls += 1
        return True

    def force_exit(self, code):
        import os as _os

        _os._exit(code)

    def register_task(self, task):
        self.register_task_calls.append(task)


def _prepare_run_mocks(monkeypatch, *, embedded_db_url: str | None = None) -> None:
    """Mock run() 依赖的全部外部组件（与 integration _prepare_main 同源策略）。"""
    monkeypatch.setenv("QTRADING_DATABASE_MODE", "external")
    monkeypatch.setattr(app_main, "apply_page_theme", lambda _page: None)
    monkeypatch.setattr(app_main, "ToastManager", lambda _page: MagicMock())
    monkeypatch.setattr(app_main, "ToastManagerView", lambda: MagicMock())
    monkeypatch.setattr(app_main.ft, "WindowEventType", SimpleNamespace(CLOSE="close"))
    monkeypatch.setattr(app_main, "CacheManager", lambda: MagicMock())
    monkeypatch.setattr(app_main.ProxyManager, "apply_smart_proxy_policy", lambda: None)
    monkeypatch.setattr(app_main.ConfigHandler, "ensure_defaults", lambda: None)
    monkeypatch.setattr(app_main.ConfigHandler, "get_db_url", lambda: None)
    monkeypatch.setattr(app_main.ConfigHandler, "get_token", lambda: None)
    monkeypatch.setattr(app_main.ConfigHandler, "get_llm_config", lambda: {"api_key": None})
    monkeypatch.setattr(app_main.ConfigHandler, "is_onboarding_complete", lambda: False)
    monkeypatch.setattr(app_main.I18n, "initialize", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_main.I18n, "get", lambda key, default=None: default or key)
    monkeypatch.setattr(app_main, "build_locale_configuration", lambda _locale: object())
    monkeypatch.setattr(app_main, "setup_window_geometry", AsyncMock())
    monkeypatch.setattr(app_main, "StartupView", lambda *_args, **_kwargs: MagicMock())
    monkeypatch.setattr(
        app_main,
        "CloseConfirmDialog",
        lambda on_cancel, on_confirm: SimpleNamespace(actions=[SimpleNamespace(on_click=on_cancel)]),
    )
    monkeypatch.setattr(shutdown_mod, "ShutdownCoordinator", _FakeCoordinator)
    # embedded URL 分支：让 prepare_database_runtime 返回非 None
    if embedded_db_url is not None:
        monkeypatch.setattr(app_main, "_prepare_db_with_retry", AsyncMock(return_value=embedded_db_url))
        monkeypatch.setattr(app_main.ConfigHandler, "_db_url_override", SimpleNamespace(set=lambda v: None))
        monkeypatch.setattr(app_main.ConfigHandler, "set_embedded_db_url", lambda v: None)
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setattr(app_main, "_prepare_db_with_retry", AsyncMock(return_value=None))
    # StartupController 真实构造依赖 cache_manager + bridge + 回调，mock 掉类以隔离
    mock_controller = MagicMock()
    mock_controller.auto_probe_task = None
    mock_controller.start = AsyncMock()
    monkeypatch.setattr(app_main, "StartupController", lambda **_: mock_controller)
    monkeypatch.setattr(app_main, "_StartupBridge", lambda: MagicMock())
    # os._exit 不应被调用
    monkeypatch.setattr(
        os,
        "_exit",
        lambda _code: (_ for _ in ()).throw(AssertionError("os._exit should not be called")),
    )


@pytest.mark.asyncio
async def test_run_external_success_covers_main_flow(monkeypatch):
    """external 模式成功路径：run() 主流程全部执行（覆盖 334-545 大部分行）。"""
    _prepare_run_mocks(monkeypatch)
    page = _DummyPage()
    await app_main.run(page)

    # 配置读取与 UI 初始化
    assert page.window.prevent_close is True  # 非 web 模式阻止关闭
    assert page.window.on_event is not None
    assert page.on_error is not None
    assert page.on_disconnect is not None  # 非 E2E 应绑定 disconnect 处理
    assert callable(page.show_toast)  # type: ignore[attr-defined]  # [reason: run() 动态挂载]
    # 渲染了 RootView
    assert len(page.controls) >= 1


@pytest.mark.asyncio
async def test_run_e2e_mode_skips_disconnect(monkeypatch):
    """E2E 模式下不绑定 on_disconnect（避免多 session 共享进程被误关）。"""
    _prepare_run_mocks(monkeypatch)
    monkeypatch.setenv("E2E_TESTING", "true")
    page = _DummyPage()
    await app_main.run(page)
    assert page.on_disconnect is None


@pytest.mark.asyncio
async def test_run_web_mode_skips_window_event(monkeypatch):
    """Web 模式（FLET_FORCE_WEB_SERVER）不设置窗口关闭事件与 prevent_close。"""
    _prepare_run_mocks(monkeypatch)
    monkeypatch.setenv("FLET_FORCE_WEB_SERVER", "true")
    page = _DummyPage()
    await app_main.run(page)
    assert page.window.prevent_close is False
    assert page.window.on_event is None


@pytest.mark.asyncio
async def test_run_embedded_url_branch(monkeypatch):
    """embedded_db_url 非 None：config.DB_URL 永久设置 + ContextVar override + env pop。"""
    _prepare_run_mocks(monkeypatch, embedded_db_url="postgresql+asyncpg://u:p@localhost:5432/qt")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://stale:stale@host:5432/old")
    import config as config_mod

    page = _DummyPage()
    await app_main.run(page)
    assert config_mod.DB_URL == "postgresql+asyncpg://u:p@localhost:5432/qt"
    # stale DATABASE_URL 应被 pop（防御性冗余）
    assert "DATABASE_URL" not in os.environ


@pytest.mark.asyncio
async def test_run_auto_probe_task_registered(monkeypatch):
    """controller.auto_probe_task 非 None 且未 done 时注册到 ShutdownCoordinator。"""
    _prepare_run_mocks(monkeypatch)

    probe_task = asyncio.create_task(asyncio.sleep(10))
    mock_controller = MagicMock()
    mock_controller.auto_probe_task = probe_task
    mock_controller.start = AsyncMock()
    monkeypatch.setattr(app_main, "StartupController", lambda **_: mock_controller)

    page = _DummyPage()
    await app_main.run(page)

    coordinator = _FakeCoordinator.last
    assert coordinator is not None
    assert probe_task in coordinator.register_task_calls


@pytest.mark.asyncio
async def test_run_controller_start_receives_config(monkeypatch):
    """controller.start 收到脱敏后的配置参数（db_url/token/llm_key/onboarding）。"""
    _prepare_run_mocks(monkeypatch)

    captured: dict = {}

    def _fake_start(db_url, token, llm_api_key, onboarding_complete):
        captured["db_url"] = db_url
        captured["token"] = token
        captured["llm_api_key"] = llm_api_key
        captured["onboarding"] = onboarding_complete

    mock_controller = MagicMock()
    mock_controller.auto_probe_task = None
    mock_controller.start = AsyncMock(side_effect=_fake_start)
    monkeypatch.setattr(app_main, "StartupController", lambda **_: mock_controller)

    page = _DummyPage()
    await app_main.run(page)

    assert captured["db_url"] is None
    assert captured["token"] is None
    assert captured["llm_api_key"] is None
    assert captured["onboarding"] is False


class TestApplicationSession:
    """ApplicationSession（A8 统一回滚）测试。"""

    @pytest.mark.asyncio
    async def test_enter_returns_self(self):
        page = _DummyPage()
        session = app_main.ApplicationSession(page)
        entered = await session.__aenter__()
        assert entered is session
        assert session.cache_manager is None
        assert session.coordinator is None

    @pytest.mark.asyncio
    async def test_clean_exit_does_not_rollback(self):
        page = _DummyPage()
        session = app_main.ApplicationSession(page)
        session.cache_manager = MagicMock()
        session.coordinator = MagicMock()
        # 正常退出（无异常）：不调用回滚
        result = await session.__aexit__(None, None, None)
        assert result is False
        session.cache_manager.close.assert_not_called()
        session.coordinator.do_cleanup.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_exit_rolls_back_cache_manager_and_coordinator(self):
        page = _DummyPage()
        session = app_main.ApplicationSession(page)
        session.cache_manager = MagicMock()
        session.cache_manager.close = AsyncMock()
        session.coordinator = MagicMock()
        session.coordinator.do_cleanup = AsyncMock()

        result = await session.__aexit__(type(Exception), Exception("boom"), None)

        assert result is False  # 不吞没异常
        session.coordinator.do_cleanup.assert_awaited_once()
        session.cache_manager.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_exit_rolls_back_cache_manager_only(self):
        """coordinator 未创建时（CacheManager 已建但 coordinator 前失败）仅关引擎。"""
        page = _DummyPage()
        session = app_main.ApplicationSession(page)
        session.cache_manager = MagicMock()
        session.cache_manager.close = AsyncMock()

        result = await session.__aexit__(type(Exception), Exception("boom"), None)

        assert result is False
        session.cache_manager.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rollback_failure_does_not_mask_original_exception(self):
        """回滚自身失败只记日志，不掩盖原始异常（__aexit__ 仍返回 False 传播）。"""
        page = _DummyPage()
        session = app_main.ApplicationSession(page)
        session.cache_manager = MagicMock()
        session.cache_manager.close = AsyncMock(side_effect=RuntimeError("close failed"))
        session.coordinator = MagicMock()
        session.coordinator.do_cleanup = AsyncMock(side_effect=RuntimeError("cleanup failed"))

        result = await session.__aexit__(type(Exception), Exception("boom"), None)

        assert result is False  # 原始异常继续传播
        session.coordinator.do_cleanup.assert_awaited_once()
        session.cache_manager.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_controller_start_exception_propagates(monkeypatch):
    """controller.start 抛异常时 run() 传播异常（A8：回滚由 ApplicationSession 单测覆盖）。"""
    _prepare_run_mocks(monkeypatch)

    mock_controller = MagicMock()
    mock_controller.auto_probe_task = None
    mock_controller.start = AsyncMock(side_effect=RuntimeError("start failed"))
    monkeypatch.setattr(app_main, "StartupController", lambda **_: mock_controller)

    page = _DummyPage()
    with pytest.raises(RuntimeError, match="start failed"):
        await app_main.run(page)


@pytest.mark.asyncio
async def test_run_f3_cleanup_failure_is_caught_and_logged(monkeypatch):
    """F3（检视 06）：legacy 密钥清理异常被捕获并记录，不阻断启动流程。

    run() 中 _purge_legacy_key_if_safe() 失败（如权限/IO 异常）时，仅捕获并记
    log_classified，异常不得向上传播中断启动。
    """
    _prepare_run_mocks(monkeypatch)

    def _boom():
        raise PermissionError("cannot remove legacy key file")

    monkeypatch.setattr(app_main, "_purge_legacy_key_if_safe", _boom)

    page = _DummyPage()
    await app_main.run(page)  # 不抛异常：F3 清理失败可安全降级
    assert page.show_toast  # type: ignore[attr-defined]  # [reason: run() 动态挂载]


@pytest.mark.asyncio
async def test_run_f4_keyring_unavailable_logs_error(monkeypatch):
    """F4（检视 06）：启动期预检发现 keyring 不可用时记录 error 日志。

    is_keyring_available() 返回 False → 依据检视结论（安全降级需用户知情）以
    error 级别记录，提示凭证将降级存储并推荐使用环境变量；且不阻断启动。
    """
    _prepare_run_mocks(monkeypatch)
    monkeypatch.setattr(app_main, "_is_keyring_available", lambda: False)

    page = _DummyPage()
    with patch.object(app_main.logger, "error") as mock_error:
        await app_main.run(page)
    assert any("System keyring unavailable" in str(c[0][0]) for c in mock_error.call_args_list)


@pytest.mark.asyncio
async def test_run_f4_keyring_precheck_exception_is_caught(monkeypatch):
    """F4（检视 06）：启动期 keyring 预检自身异常被捕获降级，不阻断启动。

    预检的 try/except 防御边界：即使 is_keyring_available() 抛异常，也只记
    log_classified 而不中断启动流程（keyring 探测失败不影响应用可用性）。
    """
    _prepare_run_mocks(monkeypatch)

    def _boom():
        raise RuntimeError("keyring probe exploded")

    monkeypatch.setattr(app_main, "_is_keyring_available", _boom)

    page = _DummyPage()
    await app_main.run(page)  # 不抛异常：预检失败可安全降级
    assert page.show_toast  # type: ignore[attr-defined]  # [reason: run() 动态挂载]
