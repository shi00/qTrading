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
    Complements reset_config_cache and _reset_loop_local_fallback in the
    root conftest.py (which handle non-singleton state).
    """
    from utils.proxy_manager import ProxyManager
    from utils.singleton_registry import reset_all_singletons

    reset_all_singletons()
    ProxyManager._reset_singleton()
    yield
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
    yield
    DataExplorerQueryClient._shared_engine = None


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
