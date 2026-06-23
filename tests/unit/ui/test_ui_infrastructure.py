import pytest


pytestmark = pytest.mark.unit


def test_flet_mock_page(mock_page):
    assert mock_page is not None
    assert hasattr(mock_page, "add")
    assert hasattr(mock_page, "update")
    assert hasattr(mock_page, "client_storage")

    assert len(mock_page.controls) >= 1

    mock_page.client_storage.set("test_key", "test_value")
    assert mock_page.client_storage.get("test_key") == "test_value"

    mock_page.add("test_control")
    assert "test_control" in mock_page.controls

    mock_page.clean()
    assert len(mock_page.controls) == 0
