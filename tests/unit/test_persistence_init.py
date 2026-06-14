import pytest
import sys
import unittest.mock


def test_persistence_init_getattr_database_manager():
    # Ensure it's not already imported
    if "data.persistence" in sys.modules:
        del sys.modules["data.persistence"]
    import data.persistence

    assert hasattr(data.persistence, "DatabaseManager")
    from data.persistence.database_manager import DatabaseManager

    assert data.persistence.DatabaseManager is DatabaseManager


def test_persistence_init_getattr_base():
    if "data.persistence" in sys.modules:
        del sys.modules["data.persistence"]
    import data.persistence

    assert hasattr(data.persistence, "Base")
    from data.persistence.models import Base

    assert data.persistence.Base is Base


def test_persistence_init_getattr_models():
    if "data.persistence" in sys.modules:
        del sys.modules["data.persistence"]
    import data.persistence

    assert hasattr(data.persistence, "models")
    import data.persistence.models as models_mod

    assert data.persistence.models is models_mod
    assert data.persistence.models is models_mod


def test_persistence_init_getattr_models_failure():
    if "data.persistence" in sys.modules:
        del sys.modules["data.persistence"]
    import data.persistence

    with unittest.mock.patch("importlib.import_module", side_effect=ImportError("mocked import error")):
        with pytest.raises(ImportError, match="mocked import error"):
            _ = data.persistence.models

    assert "models" not in vars(data.persistence)


def test_persistence_init_getattr_unknown():
    if "data.persistence" in sys.modules:
        del sys.modules["data.persistence"]
    import data.persistence

    with pytest.raises(AttributeError, match="module 'data.persistence' has no attribute 'UnknownAttribute'"):
        _ = data.persistence.UnknownAttribute
