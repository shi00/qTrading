import pytest


pytestmark = pytest.mark.unit


def test_flet_mock_page(mock_page):
    assert mock_page is not None
    assert hasattr(mock_page, "add")
    assert hasattr(mock_page, "update")

    assert len(mock_page.controls) >= 1

    mock_page.add("test_control")
    assert "test_control" in mock_page.controls

    mock_page.clean()
    assert len(mock_page.controls) == 0
