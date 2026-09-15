import pytest
import data.domain_services as domain_services

pytestmark = pytest.mark.unit


def test_domain_services_lazy_imports():
    """Test that all exported symbols are lazily importable."""
    assert domain_services.MarketDataService is not None  # noqa: weak-assertion lazy import 契约验证符号可解析
    assert domain_services.OfflineCalendar is not None  # noqa: weak-assertion lazy import 契约验证符号可解析
    assert domain_services.TradeCalendarService is not None  # noqa: weak-assertion lazy import 契约验证符号可解析
    assert domain_services.TransactionCostModel is not None  # noqa: weak-assertion lazy import 契约验证符号可解析
    assert domain_services.TransactionCostConfig is not None  # noqa: weak-assertion lazy import 契约验证符号可解析


def test_domain_services_review_stats_lazy_imports():
    """review_stats_service 家族符号经 __getattr__ 懒加载可解析 (BIZ-04 补充 归因统计)。"""
    from data.domain_services import review_stats_service as rss

    assert domain_services.MetricStat is rss.MetricStat
    assert domain_services.SampleGrade is rss.SampleGrade
    assert domain_services.StrategyStatRow is rss.StrategyStatRow
    assert domain_services.AiAttributionRow is rss.AiAttributionRow
    assert domain_services.compute_ai_attribution_stats is rss.compute_ai_attribution_stats
    assert domain_services.compute_strategy_review_stats is rss.compute_strategy_review_stats
    assert domain_services.grade_for is rss.grade_for


def test_domain_services_invalid_attribute():
    """Test that AttributeError is raised for invalid attributes."""
    with pytest.raises(AttributeError, match="has no attribute 'InvalidAttribute'"):
        _ = domain_services.InvalidAttribute
