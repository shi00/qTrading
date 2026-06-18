import pytest


@pytest.fixture(autouse=True)
def _reset_all_singletons():
    """Reset all registered singletons before and after each unit test.

    Uses singleton_registry.reset_all_singletons() to ensure clean state.
    Complements reset_config_cache and reset_loop_local_cache in the
    root conftest.py (which handle non-singleton state).
    """
    from utils.singleton_registry import reset_all_singletons

    reset_all_singletons()
    yield
    reset_all_singletons()
