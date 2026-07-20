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


def test_domain_services_invalid_attribute():
    """Test that AttributeError is raised for invalid attributes."""
    with pytest.raises(AttributeError, match="has no attribute 'InvalidAttribute'"):
        _ = domain_services.InvalidAttribute
