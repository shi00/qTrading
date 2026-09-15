import matplotlib

matplotlib.use("Agg")  # Non-interactive backend for unit tests (production uses flet_charts backend)

import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if not any(marker.name in ("unit", "integration", "e2e") for marker in item.iter_markers()):
            item.add_marker(pytest.mark.unit)


@pytest.fixture(autouse=True)
def _reset_all_singletons():
    """Reset all registered singletons before and after each unit test.

    Uses singleton_registry.reset_all_singletons() to ensure clean state.
    Also resets ProxyManager (non-registered singleton per CLAUDE.md §4.3).
    review01-A9: bootstrap 模块级 _services_initialized flag 已移除（改为
    StartupController per-session 实例状态），不再需要此处重置。
    Complements reset_config_cache and _reset_loop_local_fallback in the
    root conftest.py (which handle non-singleton state).
    """
    from utils.loop_local import clear_all_loop_locals
    from utils.proxy_manager import ProxyManager
    from utils.singleton_registry import reset_all_singletons

    # B7: 彻底隔离 loop-local 外层键空间（_stores + _fallback_store）。
    # 根 conftest 只清 _fallback_store，此处补清 _stores，避免 get_loop_local 的
    # 外层 dict[str, WeakKeyDictionary] key 跨测试累积（key 数量 = 调用点数量）。
    clear_all_loop_locals()
    # B2+B10 修复: test_shutdown 完整清理成功路径会真实调用 mark_graceful_shutdown_completed(),
    # 置位模块级 _graceful_shutdown_completed 后无 autouse 重置会跨测试污染
    # test_task_manager 的 _atexit_cleanup 短路 (test_cancels_active_tasks 失败)。
    # 此处前后重置, 保证每个测试从"未优雅停机"基线开始。
    # 注意: 须以模块引用方式修改, 不能用 from-import 值绑定 (否则只改本模块局部变量)。
    import utils.singleton_registry as _sr

    _sr._graceful_shutdown_completed = False
    reset_all_singletons()
    ProxyManager._reset_singleton()
    yield
    clear_all_loop_locals()
    _sr._graceful_shutdown_completed = False
    reset_all_singletons()
    ProxyManager._reset_singleton()


@pytest.fixture(autouse=True)
def _isolate_egress_audit_disk(tmp_path):
    """隔离 EgressAudit 落盘路径到测试临时目录（R7 测试状态隔离）。

    SEC-03: ai_service 云端分支测试（走真实 _chat_completion cloud 分支）会触发
    EgressAudit().record() 审计点，若落盘指向真实 USER_DATA_ROOT/logs/egress_audit.jsonl
    会污染真实审计文件。EgressAudit._reset_singleton 保留 _override_path（见
    utils/egress_audit.py），此 fixture 与 reset 顺序无关，始终指向本测试独立临时目录。
    """
    from utils.egress_audit import EgressAudit

    EgressAudit._configure_for_tests(str(tmp_path / "egress_audit.jsonl"))
    yield


@pytest.fixture(autouse=True)
def _reset_toast_manager_state():
    """重置 ToastManager 模块级可变状态（R7 测试状态污染）。

    ToastManager 已按 review05-E15 去单例化：状态（_state/_next_id/_is_stopping/
    _active_tasks）全部上提为模块级，实例为无状态薄壳，不被 @register_singleton
    管理（M12-030 评估结论：非事实单例，不再注册实例单例）。因此模块级可变状态
    需在此 autouse fixture 中手动重置，与 _reset_all_singletons 等互补，避免跨
    测试残留（并发 toast / 关闭状态泄漏到后续测试）。
    """
    from ui.components.toast_manager import _reset_state_for_test

    _reset_state_for_test()
    yield
    _reset_state_for_test()


@pytest.fixture(autouse=True)
def _reset_data_explorer_shared_engine():
    """Reset DataExplorerQueryClient._shared_engine before and after each unit test.

    DataExplorerQueryClient uses a class-level shared engine (_shared_engine)
    that is NOT managed by singleton_registry. This fixture ensures clean state
    to prevent cross-test pollution (CLAUDE.md R7).
    """
    from data.persistence.data_explorer_query_client import DataExplorerQueryClient

    DataExplorerQueryClient._shared_engine = None
    DataExplorerQueryClient._closed = False
    yield
    DataExplorerQueryClient._shared_engine = None
    DataExplorerQueryClient._closed = False


@pytest.fixture(autouse=True)
def _reset_egress_status_state():
    """Reset EgressStatusState singleton before and after each unit test (R7).

    app_layout 组件经 ``ft.use_state(get_egress_status_state)`` 订阅该模块级
    Observable 单例；单元测试挂载组件后不卸载组件, 残留的 ObservableSubscription
    订阅者会随单例泄漏到后续测试, 使 ``notify_egress_count`` 在无 page 上下文时
    抛 ``RuntimeError: The context is not associated with any page``。此处重置为
    None（连同订阅者一起丢弃）, 与 _reset_toast_manager_state 等模块级状态隔离
    惯例一致。
    """
    from ui.egress_status_state import _reset_state_for_test

    _reset_state_for_test()
    yield
    _reset_state_for_test()


@pytest.fixture(autouse=True)
def _reset_embedded_db_url():
    """Reset ConfigHandler._embedded_db_url before and after each unit test.

    ConfigHandler._embedded_db_url is a module/class-level mutable override NOT
    managed by singleton_registry nor any existing reset fixture. Leaving it set
    would leak to later tests and shadow get_db_url()'s P2/P0 priority assertions
    (CLAUDE.md R7 测试状态污染).
    """
    from utils.config_handler import ConfigHandler

    ConfigHandler.clear_embedded_db_url()
    yield
    ConfigHandler.clear_embedded_db_url()


@pytest.fixture(autouse=True)
def _reset_metadata_manager_cache():
    """Reset MetaDataManager._alias_cache before and after each unit test.

    MetaDataManager uses a class-level mutable dict (_alias_cache) that is NOT
    managed by singleton_registry. This fixture ensures clean state to prevent
    cross-test pollution (CLAUDE.md R7), especially when I18n locale changes
    between tests would otherwise leave stale cached aliases.
    """
    from data.persistence.metadata_manager import MetaDataManager

    MetaDataManager.invalidate_cache()
    yield
    MetaDataManager.invalidate_cache()


@pytest.fixture(autouse=True)
def _reset_logging_state():
    """Reset logging state before and after each unit test to prevent cross-test pollution.

    Tests that call setup_logging() can leave residual state (modified named
    logger levels, non-zero logging.disable, or Logger.disabled=True) that
    causes caplog-based assertions to fail intermittently under random test
    ordering.

    Root logger level is reset to WARNING (pytest default) — catching_logs
    (configured via log_level=DEBUG in pyproject.toml) will override to DEBUG
    for tests that need it. logging.disable and named logger levels are also
    reset to prevent filter-level pollution.

    Logger.disabled (instance-level boolean) is reset because some code paths
    (including third-party libraries) may set it to True, which makes
    isEnabledFor() return False and silently drops log records — leaving
    caplog.records empty. The original _reset_logging_state only reset
    manager.disable, missing this attribute.
    """
    import logging

    root = logging.getLogger()
    saved_level = root.level
    saved_disable = root.manager.disable
    saved_named_levels: dict[str, int] = {}
    saved_named_disabled: dict[str, bool] = {}
    for name, logger in root.manager.loggerDict.items():
        if isinstance(logger, logging.Logger):
            if logger.level != logging.NOTSET:
                saved_named_levels[name] = logger.level
                logger.setLevel(logging.NOTSET)
            if logger.disabled:
                saved_named_disabled[name] = logger.disabled
                logger.disabled = False
    logging.disable(logging.NOTSET)
    root.setLevel(logging.WARNING)
    yield
    logging.disable(saved_disable)
    root.setLevel(saved_level)
    for name, level in saved_named_levels.items():
        logging.getLogger(name).setLevel(level)
    for name in saved_named_disabled:
        logging.getLogger(name).disabled = False


@pytest.fixture(autouse=True)
def _reset_i18n_state():
    """Reset I18n class-level state before and after each unit test.

    I18n._locale is a class attribute (not a singleton) that persists across
    tests. Tests that call I18n.set_locale("en_US") can pollute subsequent
    tests asserting on localized text (e.g. test_review_manager,
    test_ai_mixin hard-coded Chinese assertions), causing cross-test locale
    pollution detected by test_pollution_detection.

    This fixture provides a baseline reset for all unit tests. Module-level
    fixtures in test_i18n.py / test_ui_i18n.py etc. layer on top (executed
    after this conftest fixture) and may override _initialized to False for
    auto-init testing; that is safe because module-level fixtures run inside
    this one.

    _initialized is set to True to avoid auto-init warning log noise in
    tests that don't explicitly call I18n.initialize().

    _listeners uses save-restore pattern (NOT clear-to-None) to preserve the
    ui/i18n.py _sync_i18n_state global subscription registered at module
    load time. Clearing _listeners would break set_locale/initialize → state
    sync → MetaDataManager.invalidate_cache chain, causing test_ui_i18n /
    test_ui_i18n_observable failures. The real pollution source is _locale
    (modified by set_locale), not _listeners (just a callback list, not
    modified by set_locale). Save-restore also cleans up leaky callbacks
    subscribed during test execution.
    """
    from core.i18n import DEFAULT_LOCALE, I18n

    saved_listeners = list(I18n._listeners) if I18n._listeners else None
    I18n._locale = DEFAULT_LOCALE
    I18n._initialized = True
    I18n._missing_keys = set()
    yield
    I18n._locale = DEFAULT_LOCALE
    I18n._initialized = True
    I18n._listeners = saved_listeners
    I18n._missing_keys = set()
