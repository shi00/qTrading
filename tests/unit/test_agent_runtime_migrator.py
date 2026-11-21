"""Agent 运行库迁移器单元测试：版本守卫纯函数矩阵 + 双迁移编排契约。

- 守卫矩阵（缺/旧/未来/SDK 漂移 → 拒绝）针对纯函数 ``_verify_snapshot``，无 DB 依赖。
- 编排契约（DBOS.migrate 先于 SQLAlchemyDatasource.migrate；中间失败异常传播且
  不创建引擎/不授予权限/不校验版本）通过模块级 seam 注入，无 DB 依赖。
- 真实 PG 行为（迁移产物/权限/篡改守卫）见 ``tests/integration/test_agent_runtime_migrator.py``。
"""

from __future__ import annotations

import pytest

from data.persistence import agent_migrator
from data.persistence.agent_migrator import (
    EXPECTED_DATASOURCE_MIGRATION_VERSION,
    EXPECTED_DBOS_MIGRATION_VERSION,
    AgentRuntimeMigrationError,
    AgentRuntimeMigrationSnapshot,
    _verify_snapshot,
    run_agent_runtime_migrations,
)

pytestmark = pytest.mark.unit


def _snapshot(
    dbos_recorded: int = EXPECTED_DBOS_MIGRATION_VERSION,
    dbos_latest: int = EXPECTED_DBOS_MIGRATION_VERSION,
    datasource_recorded: int = EXPECTED_DATASOURCE_MIGRATION_VERSION,
    datasource_latest: int = EXPECTED_DATASOURCE_MIGRATION_VERSION,
) -> AgentRuntimeMigrationSnapshot:
    return AgentRuntimeMigrationSnapshot(
        dbos_recorded=dbos_recorded,
        dbos_latest=dbos_latest,
        datasource_recorded=datasource_recorded,
        datasource_latest=datasource_latest,
    )


class TestVerifySnapshotGuardMatrix:
    """版本守卫精确相等矩阵（HANDOFF §8.1 第 6 条）。"""

    @pytest.mark.parametrize(
        ("field", "value", "expected_fragment"),
        [
            ("dbos_recorded", 0, "125"),  # 缺失表读作 0
            ("dbos_recorded", 124, "125"),  # 旧
            ("dbos_recorded", 126, "125"),  # 未来（DBOS 自身 verify 容忍，应用层须拒绝）
            ("dbos_latest", 124, "125"),  # SDK 漂移（旧版 SDK）
            ("dbos_latest", 126, "125"),  # SDK 漂移（升级未重新核）
            ("datasource_recorded", 0, "1"),
            ("datasource_recorded", 2, "1"),
            ("datasource_latest", 2, "1"),
        ],
    )
    def test_mismatch_refused(self, field: str, value: int, expected_fragment: str) -> None:
        snapshot = _snapshot(**{field: value})
        with pytest.raises(AgentRuntimeMigrationError) as exc_info:
            _verify_snapshot(snapshot)
        # 强断言：拒绝消息必须同时点名冻结合同值与实际值（R9 边界：不含 URL）
        message = str(exc_info.value)
        assert expected_fragment in message
        assert str(value) in message
        assert "postgresql" not in message.lower()

    def test_exact_match_accepted(self) -> None:
        _verify_snapshot(_snapshot())

    def test_error_message_names_expected_versions(self) -> None:
        """错误消息包含期望版本与实际值，不含连接 URL（R9 脱敏边界）。"""
        snapshot = _snapshot(dbos_recorded=124)
        with pytest.raises(AgentRuntimeMigrationError) as exc_info:
            _verify_snapshot(snapshot)
        message = str(exc_info.value)
        assert "125" in message
        assert "124" in message
        assert "postgresql" not in message.lower()


class TestDoubleMigrationOrchestration:
    """双迁移编排契约（seam 注入，无 DB）。"""

    def test_failure_propagates_without_engine_or_grants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """第二迁移失败：异常传播，不创建引擎、不授权限、不校验版本。"""
        calls: list[str] = []

        def _failing_double_migration(url: str) -> None:
            calls.append("double_migration")
            raise AgentRuntimeMigrationError("datasource migrate failed")

        def _forbidden(name: str):
            def _record(*args: object, **kwargs: object) -> object:
                calls.append(name)
                raise AssertionError(f"{name} 不应在迁移失败后被调用")

            return _record

        monkeypatch.setattr(agent_migrator, "_run_double_migration", _failing_double_migration)
        monkeypatch.setattr(agent_migrator.sa, "create_engine", _forbidden("create_engine"))
        monkeypatch.setattr(agent_migrator, "verify_agent_runtime_versions", _forbidden("verify"))
        monkeypatch.setattr(agent_migrator, "_apply_runtime_role_grants", _forbidden("grants"))

        with pytest.raises(AgentRuntimeMigrationError, match="datasource migrate failed"):
            run_agent_runtime_migrations("postgresql+psycopg://migrator@host/db")
        assert calls == ["double_migration"]

    def test_success_path_order_and_engine_dispose(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """成功路径：迁移 → 引擎 → 版本校验 → 权限授予，且引擎必被 dispose。"""
        calls: list[str] = []
        monkeypatch.setattr(
            agent_migrator,
            "_run_double_migration",
            lambda url: calls.append("double_migration"),
        )

        class _FakeEngine:
            def __init__(self) -> None:
                self.disposed = False

            def dispose(self) -> None:
                self.disposed = True
                calls.append("dispose")

        engine_holder: list[_FakeEngine] = []

        def _fake_create_engine(url: str) -> _FakeEngine:
            calls.append("create_engine")
            engine = _FakeEngine()
            engine_holder.append(engine)
            return engine

        def _fake_verify(engine: object) -> AgentRuntimeMigrationSnapshot:
            assert engine is engine_holder[0]
            calls.append("verify")
            return _snapshot()

        monkeypatch.setattr(agent_migrator.sa, "create_engine", _fake_create_engine)
        monkeypatch.setattr(agent_migrator, "verify_agent_runtime_versions", _fake_verify)
        monkeypatch.setattr(
            agent_migrator,
            "_apply_runtime_role_grants",
            lambda engine: calls.append("grants"),
        )

        snapshot = run_agent_runtime_migrations("postgresql+psycopg://migrator@host/db")
        assert snapshot == _snapshot()
        assert calls == [
            "double_migration",
            "create_engine",
            "verify",
            "grants",
            "dispose",
        ]
