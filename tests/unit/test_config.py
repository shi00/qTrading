import os
import sys
from pathlib import Path
from unittest.mock import patch
import pytest


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def restore_config_after_reload():
    import config

    original_db_url = config.DB_URL
    original_db_url_sync = config.DB_URL_SYNC
    original_app_root = config.APP_ROOT
    yield
    config.DB_URL = original_db_url
    config.DB_URL_SYNC = original_db_url_sync
    config.APP_ROOT = original_app_root


class TestConfigModule:
    def test_db_url_from_environment(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@host:5432/db")
        import importlib
        import config

        importlib.reload(config)
        assert config.DB_URL == "postgresql+asyncpg://user:pass@host:5432/db"
        assert config.DB_URL_SYNC == "postgresql://user:pass@host:5432/db"

    def test_app_root_when_frozen(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", "/path/to/executable", raising=False)
        import importlib
        import config

        importlib.reload(config)
        assert os.path.dirname("/path/to/executable") == config.APP_ROOT

    def test_tiktoken_cache_dir_set_when_exists(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
        tiktoken_cache = tmp_path / "data" / "tiktoken_cache"
        tiktoken_cache.mkdir(parents=True, exist_ok=True)
        with patch("os.path.isdir", return_value=True):
            with patch("os.path.join", return_value=str(tiktoken_cache)):
                import importlib
                import config

                importlib.reload(config)
                assert os.environ.get("TIKTOKEN_CACHE_DIR") == str(tiktoken_cache)

    def test_tiktoken_cache_dir_not_set_when_not_exists(self, monkeypatch):
        monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
        with patch("os.path.isdir", return_value=False):
            import importlib
            import config

            importlib.reload(config)
            assert os.environ.get("TIKTOKEN_CACHE_DIR") is None

    def test_db_url_sync_strips_asyncpg(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@host:5432/db")
        import importlib
        import config

        importlib.reload(config)
        assert config.DB_URL_SYNC is not None
        assert "+asyncpg" not in config.DB_URL_SYNC
        assert config.DB_URL_SYNC == "postgresql://user:pass@host:5432/db"


class TestConfigDotenvImport:
    def test_dotenv_import_error_handled(self, monkeypatch):
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def mock_import(name, *args, **kwargs):
            if name == "dotenv":
                raise ImportError("No module named 'dotenv'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", mock_import)
        import importlib
        import config

        importlib.reload(config)


class TestUserDataDir:
    def test_user_data_dir_env_override(self, monkeypatch):
        import config

        monkeypatch.setenv("ASTOCK_USER_DATA_DIR", "/custom/user/data")
        assert config._user_data_dir() == "/custom/user/data"

    @pytest.mark.parametrize(
        ("platform", "env", "must_contain"),
        [
            ("win32", {"APPDATA": "C:/Users/demo/AppData/Roaming"}, "appdata"),
            ("darwin", {}, "app_support"),
            ("linux", {"XDG_CONFIG_HOME": "/x/stub"}, "xdg"),
        ],
        ids=["win32", "darwin", "else"],
    )
    def test_user_data_dir_fallback_without_platformdirs(self, monkeypatch, platform, env, must_contain):
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def mock_import(name, *args, **kwargs):
            if name == "platformdirs":
                raise ImportError("No module named platformdirs")
            return original_import(name, *args, **kwargs)

        import config

        monkeypatch.delenv("ASTOCK_USER_DATA_DIR", raising=False)
        monkeypatch.setattr("builtins.__import__", mock_import)
        monkeypatch.setattr(sys, "platform", platform)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        result = config._user_data_dir()

        assert result.endswith("AStockScreener")
        if must_contain == "appdata":
            assert Path(result).as_posix().startswith("C:/Users/demo/AppData/Roaming")
        elif must_contain == "xdg":
            assert Path(result).as_posix().startswith("/x/stub")
        else:
            assert "Application Support" in result
