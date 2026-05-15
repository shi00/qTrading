import asyncio
import os
import sys
import tempfile
from contextlib import contextmanager
from unittest.mock import MagicMock

try:
    from dotenv import load_dotenv

    _env_test = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.test")
    if os.path.isfile(_env_test):
        load_dotenv(_env_test, override=False)
except ImportError:
    pass

import pytest


@pytest.fixture(scope="session")
def event_loop_policy():
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@contextmanager
def reset_singleton(cls, extra_attrs=None):
    """Context manager that saves and restores a singleton class's _instance.

    Usage:
        with reset_singleton(TaskManager):
            mgr = TaskManager()
            ...

        with reset_singleton(AIService, extra_attrs=["_initialized"]):
            svc = AIService()
            ...
    """
    saved = {"_instance": cls._instance}
    cls._instance = None
    if extra_attrs:
        for attr in extra_attrs:
            saved[attr] = getattr(cls, attr, None)
            if attr == "_initialized":
                setattr(cls, attr, False)
            else:
                setattr(cls, attr, None)
    try:
        yield
    finally:
        for attr, value in saved.items():
            setattr(cls, attr, value)


@pytest.fixture
def singleton_reset():
    """Fixture that provides the reset_singleton context manager.

    Usage in tests:
        def test_something(self, singleton_reset):
            with singleton_reset(TaskManager):
                mgr = TaskManager()
    """
    return reset_singleton


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


_MOCK_KEYRING = None
_MOCK_LITELLM = None
_ORIGINAL_KEYRING = None
_ORIGINAL_LITELLM = None


def _create_mock_keyring():
    """Create a mock keyring module for CI environments."""
    _password_store = {}

    def get_password(service_name, username):
        return _password_store.get(f"{service_name}:{username}")

    def set_password(service_name, username, password):
        _password_store[f"{service_name}:{username}"] = password

    def delete_password(service_name, username):
        _password_store.pop(f"{service_name}:{username}", None)

    def clear():
        _password_store.clear()

    mock_kr = MagicMock()
    mock_kr.get_password = get_password
    mock_kr.set_password = set_password
    mock_kr.delete_password = delete_password
    mock_kr.clear = clear
    mock_kr.errors = MagicMock()
    mock_kr.errors.NoKeyringError = type("NoKeyringError", (Exception,), {})
    return mock_kr


def _create_mock_litellm():
    from unittest.mock import AsyncMock

    mock_lt = MagicMock()
    mock_lt.suppress_debug_info = True
    mock_lt.set_verbose = False
    mock_lt.drop_params = True
    mock_lt.set_timeout = 30.0
    mock_lt.max_retries = 2
    mock_lt.success_callback = []
    mock_lt.failure_callback = []
    mock_lt.modify_params = True
    mock_lt.acompletion = AsyncMock()
    mock_lt.utils = MagicMock()

    _REASONING_PATTERNS = ("deepseek-reasoner", "o3", "o4-mini")

    def _supports_reasoning(model=""):
        m = model.lower()
        return any(p in m for p in _REASONING_PATTERNS)

    mock_lt.utils.supports_reasoning = MagicMock(side_effect=_supports_reasoning)
    return mock_lt


def pytest_unconfigure(config):
    """Restore original keyring/litellm modules and clean up temp config after test session."""
    if _ORIGINAL_KEYRING is not None:
        sys.modules["keyring"] = _ORIGINAL_KEYRING
    else:
        sys.modules.pop("keyring", None)

    if _ORIGINAL_LITELLM is not None:
        sys.modules["litellm"] = _ORIGINAL_LITELLM
    else:
        sys.modules.pop("litellm", None)

    if _MOCK_KEYRING is not None and hasattr(_MOCK_KEYRING, "clear"):
        _MOCK_KEYRING.clear()

    import shutil

    if os.path.exists(_temp_config_dir):
        shutil.rmtree(_temp_config_dir, ignore_errors=True)


_temp_config_dir = tempfile.mkdtemp(prefix="astock_test_config_")
_temp_config_file = os.path.join(_temp_config_dir, "test_user_settings.json")


def _get_test_db_url():
    from tests.integration.conftest import TEST_DB_URL

    return TEST_DB_URL


def pytest_configure(config):
    global _MOCK_KEYRING, _MOCK_LITELLM, _ORIGINAL_KEYRING, _ORIGINAL_LITELLM

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    _ORIGINAL_KEYRING = sys.modules.get("keyring")
    _MOCK_KEYRING = _create_mock_keyring()
    sys.modules["keyring"] = _MOCK_KEYRING

    _ORIGINAL_LITELLM = sys.modules.get("litellm")
    _MOCK_LITELLM = _create_mock_litellm()
    sys.modules["litellm"] = _MOCK_LITELLM

    os.environ["DATABASE_URL"] = _get_test_db_url()

    import utils.config_handler

    utils.config_handler.CONFIG_FILE = _temp_config_file


@pytest.fixture(autouse=True, scope="session")
def isolate_config_file():
    """
    Ensure config isolation is active throughout the test session.
    The actual patching is done in pytest_configure for early interception.
    """
    yield _temp_config_file


@pytest.fixture(autouse=True)
def _reset_mock_keyring_store():
    """Clear mock keyring password store between tests to prevent leakage."""
    yield
    if _MOCK_KEYRING is not None and hasattr(_MOCK_KEYRING, "clear"):
        _MOCK_KEYRING.clear()
    if _MOCK_LITELLM is not None:
        _MOCK_LITELLM.success_callback.clear()
        _MOCK_LITELLM.failure_callback.clear()
