import pytest
from services.ai_service import AIService


def pytest_collection_modifyitems(items):
    for item in items:
        if not any(marker.name in ("unit", "integration", "e2e") for marker in item.iter_markers()):
            item.add_marker(pytest.mark.unit)


@pytest.fixture(autouse=True)
def reset_ai_singleton():
    AIService._reset_singleton()
    yield
    AIService._reset_singleton()
