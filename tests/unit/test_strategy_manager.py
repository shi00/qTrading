import pytest
from unittest.mock import MagicMock, patch


class TestStrategyManagerCache:
    """Tests for StrategyManager dependency cache functionality."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        from strategies.all_strategies import StrategyManager

        StrategyManager._reset_singleton()
        yield
        StrategyManager._reset_singleton()

    def test_get_all_with_dependencies_returns_cached_result(self):
        """Test that second call returns cached result without recomputation."""
        from strategies.all_strategies import StrategyManager

        with patch("strategies.all_strategies._import_all_strategies"):
            with patch("strategies.all_strategies.get_strategy_registry") as mock_registry:
                mock_strategy = MagicMock()
                mock_strategy.name_key = "test_strategy"
                mock_strategy.required_apis = ["daily"]
                mock_registry.return_value = {"test": mock_strategy}

                mgr = StrategyManager()

                with patch.object(mgr, "strategies", {"test": mock_strategy}):
                    with patch("data.external.tushare_client.TushareClient") as mock_client:
                        mock_client.return_value.is_api_available.return_value = True

                        result1 = mgr.get_all_with_dependencies()
                        result2 = mgr.get_all_with_dependencies()

                        assert result1 == result2
                        assert mock_client.call_count == 1

    def test_get_all_with_dependencies_force_refresh(self):
        """Test that force_refresh=True recomputes the cache."""
        from strategies.all_strategies import StrategyManager

        with patch("strategies.all_strategies._import_all_strategies"):
            with patch("strategies.all_strategies.get_strategy_registry") as mock_registry:
                mock_strategy = MagicMock()
                mock_strategy.name_key = "test_strategy"
                mock_strategy.required_apis = ["daily"]
                mock_registry.return_value = {"test": mock_strategy}

                mgr = StrategyManager()

                with patch.object(mgr, "strategies", {"test": mock_strategy}):
                    with patch("data.external.tushare_client.TushareClient") as mock_client:
                        mock_client.return_value.is_api_available.return_value = True

                        result1 = mgr.get_all_with_dependencies()
                        result2 = mgr.get_all_with_dependencies(force_refresh=True)

                        assert result1 == result2
                        assert mock_client.call_count == 2

    def test_get_all_with_dependencies_detects_missing_apis(self):
        """Test that missing APIs are correctly detected."""
        from strategies.all_strategies import StrategyManager

        with patch("strategies.all_strategies._import_all_strategies"):
            with patch("strategies.all_strategies.get_strategy_registry") as mock_registry:
                mock_strategy = MagicMock()
                mock_strategy.name_key = "test_strategy"
                mock_strategy.required_apis = ["daily", "moneyflow_hsgt", "hk_hold"]
                mock_registry.return_value = {"test": mock_strategy}

                mgr = StrategyManager()

                with patch.object(mgr, "strategies", {"test": mock_strategy}):
                    with patch("data.external.tushare_client.TushareClient") as mock_client:
                        client_instance = mock_client.return_value

                        def is_available(api):
                            return api == "daily"

                        client_instance.is_api_available.side_effect = is_available

                        with patch("strategies.all_strategies.I18n") as mock_i18n:
                            mock_i18n.get.return_value = "Test Strategy"

                            result = mgr.get_all_with_dependencies()

                            assert result["test"]["missing_apis"] == ["moneyflow_hsgt", "hk_hold"]

    def test_invalidate_dependency_cache(self):
        """Test that invalidate_dependency_cache clears the cache."""
        from strategies.all_strategies import StrategyManager

        with patch("strategies.all_strategies._import_all_strategies"):
            with patch("strategies.all_strategies.get_strategy_registry") as mock_registry:
                mock_strategy = MagicMock()
                mock_strategy.name_key = "test_strategy"
                mock_strategy.required_apis = ["daily"]
                mock_registry.return_value = {"test": mock_strategy}

                mgr = StrategyManager()

                with patch.object(mgr, "strategies", {"test": mock_strategy}):
                    with patch("data.external.tushare_client.TushareClient") as mock_client:
                        mock_client.return_value.is_api_available.return_value = True

                        mgr.get_all_with_dependencies()
                        mgr.invalidate_dependency_cache()

                        assert mgr._dependency_cache is None

                        mgr.get_all_with_dependencies()

                        assert mock_client.call_count == 2
