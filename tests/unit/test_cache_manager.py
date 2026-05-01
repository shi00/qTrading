import pytest

from data.cache.cache_manager import CacheManager


class TestCacheManagerCloseNoneEngine:
    """CacheManager.close engine 为 None 时不崩溃"""

    @pytest.mark.asyncio
    async def test_close_with_none_engine(self):
        cm = CacheManager.__new__(CacheManager)
        cm._disposed = False
        cm.engine = None
        await cm.close()
        assert cm._disposed is True


class TestSanitizeUrl:
    """CacheManager._sanitize_url 密码脱敏"""

    def test_sanitize_url_hides_password(self):
        cm = CacheManager.__new__(CacheManager)
        url = "postgresql+asyncpg://user:secret_password@localhost:5432/astock"
        sanitized = cm._sanitize_url(url)
        assert "secret_password" not in sanitized
        assert "****" in sanitized

    def test_sanitize_url_no_password(self):
        cm = CacheManager.__new__(CacheManager)
        url = "postgresql+asyncpg://localhost:5432/astock"
        sanitized = cm._sanitize_url(url)
        assert sanitized == url

    def test_sanitize_url_empty(self):
        cm = CacheManager.__new__(CacheManager)
        assert cm._sanitize_url("") == "None"
        assert cm._sanitize_url(None) == "None"
