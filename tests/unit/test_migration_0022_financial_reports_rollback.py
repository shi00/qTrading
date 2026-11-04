"""D04-F01：0022 回退的多公告版本 fail-closed 与显式有损路径单测。

这些用例只验证迁移脚本的预检/授权分支与 DDL 顺序，不连接真实 PostgreSQL；
真实 populated 回退场景见 tests/integration/test_data_db_migrator.py（not_run）。
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0022_add_ann_date_to_financial_reports_pk.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migration_0022", _MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mig = _load_migration_module()


def _scalar_result(value: int) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


def _rowcount_result(value: int) -> MagicMock:
    result = MagicMock()
    result.rowcount = value
    return result


def _capture_ddl(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []

    def _record(name):
        def _inner(*args, **kwargs):
            calls.append((name, args, kwargs))

        return _inner

    monkeypatch.setattr(mig.op, "drop_constraint", _record("drop_constraint"))
    monkeypatch.setattr(mig.op, "alter_column", _record("alter_column"))
    monkeypatch.setattr(mig.op, "create_primary_key", _record("create_primary_key"))
    return calls


def _patch_online(monkeypatch, bind: MagicMock, x_args: dict[str, str] | None = None) -> None:
    monkeypatch.setattr(mig.context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(mig.context, "get_x_argument", lambda as_dictionary=False: x_args or {})
    monkeypatch.setattr(mig.op, "get_bind", lambda: bind)


@pytest.mark.parametrize(
    ("x_args", "expected"),
    [
        ({}, False),
        ({"allow_lossy_rollback": "1"}, True),
        ({"allow_lossy_rollback": "0"}, False),
        ({"allow_lossy_rollback": "true"}, False),
        ({"allow_lossy_rollback": ""}, False),
        ({"other": "1"}, False),
    ],
)
def test_lossy_rollback_requires_explicit_value(monkeypatch, x_args, expected):
    """只有显式 ``allow_lossy_rollback=1`` 才视为授权。"""
    monkeypatch.setattr(mig.context, "get_x_argument", lambda as_dictionary=False: x_args)

    assert mig._lossy_rollback_authorized() is expected


def test_count_duplicate_groups_uses_grouped_precheck():
    bind = MagicMock()
    bind.execute.return_value = _scalar_result(2)

    assert mig._count_duplicate_groups(bind) == 2
    statement = bind.execute.call_args.args[0]
    assert "GROUP BY ts_code, end_date" in str(statement)
    assert "HAVING count(*) > 1" in str(statement)


def test_delete_extra_versions_returns_rowcount_and_uses_deterministic_order():
    bind = MagicMock()
    bind.execute.return_value = _rowcount_result(3)

    assert mig._delete_extra_versions(bind) == 3
    statement = str(bind.execute.call_args.args[0])
    assert "ann_date DESC, updated_at DESC NULLS LAST, created_at DESC NULLS LAST" in statement


def test_downgrade_refuses_multiple_versions_before_any_ddl(monkeypatch):
    """默认 fail-closed：预检发现重复组时，任何破坏性 DDL 都不得执行。"""
    bind = MagicMock()
    bind.execute.return_value = _scalar_result(2)
    _patch_online(monkeypatch, bind)
    calls = _capture_ddl(monkeypatch)

    with pytest.raises(RuntimeError) as exc_info:
        mig.downgrade()

    message = str(exc_info.value)
    assert "多个公告版本" in message
    assert "pg_dump" in message
    assert "allow_lossy_rollback=1" in message
    assert calls == []
    # 仅执行预检 SELECT，未执行 DELETE。
    assert bind.execute.call_count == 1


def test_downgrade_without_duplicates_restores_old_primary_key(monkeypatch):
    bind = MagicMock()
    bind.execute.return_value = _scalar_result(0)
    _patch_online(monkeypatch, bind)
    calls = _capture_ddl(monkeypatch)

    mig.downgrade()

    assert [name for name, _, _ in calls] == ["drop_constraint", "alter_column", "create_primary_key"]
    _, create_args, _ = calls[-1]
    assert create_args[0] == "pk_financial_reports"
    assert create_args[1] == "financial_reports"
    assert create_args[2] == ["ts_code", "end_date"]
    assert bind.execute.call_count == 1


def test_downgrade_lossy_path_deletes_then_restores_old_primary_key(monkeypatch):
    bind = MagicMock()
    bind.execute.side_effect = [_scalar_result(2), _rowcount_result(3)]
    _patch_online(monkeypatch, bind, {"allow_lossy_rollback": "1"})
    calls = _capture_ddl(monkeypatch)
    mock_logger = MagicMock()
    monkeypatch.setattr(mig, "logger", mock_logger)

    mig.downgrade()

    assert bind.execute.call_count == 2
    assert [name for name, _, _ in calls] == ["drop_constraint", "alter_column", "create_primary_key"]
    assert mock_logger.info.called
    assert "3" in [str(arg) for arg in mock_logger.info.call_args.args]


def test_downgrade_rejects_offline_mode(monkeypatch):
    monkeypatch.setattr(mig.context, "is_offline_mode", lambda: True)

    with pytest.raises(RuntimeError, match="离线"):
        mig.downgrade()
