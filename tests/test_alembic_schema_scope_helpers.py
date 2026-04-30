import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


def _load_migration_module():
    migration_path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "f6586a3fccba_initial_schema_v1.py"
    spec = importlib.util.spec_from_file_location("migration_f6586a3fccba", migration_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


mig = _load_migration_module()


class _FakeInspector:
    def __init__(self):
        self.schemas = []

    def get_table_names(self, schema=None):
        self.schemas.append(("tables", schema))
        return ["daily_quotes", "screening_history"]

    def get_indexes(self, table_name, schema=None):
        self.schemas.append(("indexes", schema, table_name))
        return [{"name": "idx_sh_pending"}]

    def get_columns(self, table_name, schema=None):
        self.schemas.append(("columns", schema, table_name))
        return [{"name": "trade_date"}, {"name": "close"}]


def test_table_index_column_helpers_use_version_table_schema(monkeypatch):
    fake_inspector = _FakeInspector()
    monkeypatch.setattr(mig.op, "get_bind", lambda: MagicMock())
    monkeypatch.setattr(mig.op, "get_context", lambda: SimpleNamespace(version_table_schema="tenant_a"))
    monkeypatch.setattr(mig.sa, "inspect", lambda _bind: fake_inspector)

    assert mig._table_exists("daily_quotes") is True
    assert mig._index_exists("screening_history", "idx_sh_pending") is True
    assert mig._column_exists("daily_quotes", "close") is True

    assert ("tables", "tenant_a") in fake_inspector.schemas
    assert ("indexes", "tenant_a", "screening_history") in fake_inspector.schemas
    assert ("columns", "tenant_a", "daily_quotes") in fake_inspector.schemas


def test_target_schema_none_when_context_unavailable(monkeypatch):
    monkeypatch.setattr(mig.op, "get_context", lambda: (_ for _ in ()).throw(RuntimeError("no ctx")))
    assert mig._target_schema() is None
