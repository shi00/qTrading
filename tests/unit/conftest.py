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
    Also resets ProxyManager (non-registered singleton per CLAUDE.md §4.3)
    and bootstrap._services_initialized module-level flag (R7 测试状态污染：
    Skeptic-MAJOR-2 幂等 guard 跨测试持久化会短路后续失败测试).
    Complements reset_config_cache and _reset_loop_local_fallback in the
    root conftest.py (which handle non-singleton state).
    """
    from app.bootstrap import reset_services_initialized
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
    reset_services_initialized()
    reset_all_singletons()
    ProxyManager._reset_singleton()
    yield
    clear_all_loop_locals()
    _sr._graceful_shutdown_completed = False
    reset_services_initialized()
    reset_all_singletons()
    ProxyManager._reset_singleton()


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
