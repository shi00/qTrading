import pytest
import data.domain_services as domain_services


def test_domain_services_lazy_imports():
    """Test that all exported symbols are lazily importable."""
    assert domain_services.MarketDataService is not None
    assert domain_services.OfflineCalendar is not None
    assert domain_services.TradeCalendarService is not None
    assert domain_services.TransactionCostModel is not None
    assert domain_services.TransactionCostConfig is not None


def test_domain_services_invalid_attribute():
    """Test that AttributeError is raised for invalid attributes."""
    with pytest.raises(AttributeError, match="has no attribute 'InvalidAttribute'"):
        _ = domain_services.InvalidAttribute
