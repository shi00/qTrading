import pytest

from utils.config_handler import ConfigHandler


@pytest.mark.unit
class TestAIConcurrencyConfig:
    def test_analysis_concurrency_clamped_upper(self):
        ConfigHandler.set_ai_max_concurrent_analysis(999)
        assert ConfigHandler.get_ai_max_concurrent_analysis() == 10

    def test_analysis_concurrency_clamped_lower(self):
        ConfigHandler.set_ai_max_concurrent_analysis(0)
        assert ConfigHandler.get_ai_max_concurrent_analysis() == 1

    def test_analysis_concurrency_default(self):
        assert 1 <= ConfigHandler.get_ai_max_concurrent_analysis() <= 10

    def test_analysis_concurrency_set_and_get_roundtrip(self):
        """Normal value set → get roundtrip"""
        ConfigHandler.set_ai_max_concurrent_analysis(3)
        assert ConfigHandler.get_ai_max_concurrent_analysis() == 3

    def test_analysis_concurrency_negative_clamped(self):
        """Negative values are clamped to 1"""
        ConfigHandler.set_ai_max_concurrent_analysis(-5)
        assert ConfigHandler.get_ai_max_concurrent_analysis() == 1

    def test_news_concurrency_default_is_one(self):
        assert ConfigHandler.get_ai_news_max_concurrent() == 1

    def test_news_concurrency_clamped_upper(self):
        ConfigHandler.set_ai_news_max_concurrent(999)
        assert ConfigHandler.get_ai_news_max_concurrent() == 5

    def test_news_concurrency_clamped_lower(self):
        ConfigHandler.set_ai_news_max_concurrent(0)
        assert ConfigHandler.get_ai_news_max_concurrent() == 1

    def test_news_concurrency_set_and_get_roundtrip(self):
        """Normal value set → get roundtrip"""
        ConfigHandler.set_ai_news_max_concurrent(3)
        assert ConfigHandler.get_ai_news_max_concurrent() == 3

    def test_news_concurrency_negative_clamped(self):
        """Negative values are clamped to 1"""
        ConfigHandler.set_ai_news_max_concurrent(-10)
        assert ConfigHandler.get_ai_news_max_concurrent() == 1
