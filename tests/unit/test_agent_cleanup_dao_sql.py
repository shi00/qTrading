"""E-06-2c 清理执行静态合同与边界测试（不连数据库）。

覆盖：
- 运行库存储的九类计数 SQL 与 :data:`CLEANUP_NATIVE_TARGETS` 一一对应、全部
  ``ANY(:ids)`` 展参、无 LIKE/前缀；Datasource 删除只命中常量表；
- Datasource 删除与九域查零在同一事务内完成，非零即回滚（不留「先提交删除、
  再另连接核零」的部分成功窗口，I82）；
- 执行器顺序（公开系统删除 → Datasource DML 与同事务查零）、显式不递归子工作流、
  空集合零副作用、超 200/空 ID 在触达客户端与存储前拒绝；
- 应用域 tombstone 的入参边界（空 user_scope、非法资产键、超 200）；
- 删除尝试阶段只写 ``error_category`` 且要求 fence 已提交、未 verified（M14）。

真实九类删除/集合外不变/NOT NULL tombstone 由 ``tests/integration/test_agent_dao_cleanup.py``
（真 sidecar PG）验证，本文件不冒充集成证据。
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    CLEANUP_MAX_BATCH_IDS,
    CLEANUP_NATIVE_TARGETS,
    CLEANUP_TOMBSTONE,
    CLEANUP_TOMBSTONE_DIGEST,
    AgentErrorCode,
    AgentServiceError,
    CleanupPhase,
    sha256_hex,
)
from data.persistence import engine_provider
from data.persistence.agent_runtime_store import (
    CLEANUP_COUNT_SQL,
    CLEANUP_DATASOURCE_DELETE_SQL,
    CLEANUP_PRIMARY_PRESENCE_SQL,
    AgentRuntimeNativeStore,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.daos.base_dao import EngineDisposedError
from services.agent_service.cleanup_native import NativeCleanupExecutor, NativeVerification


class _FakeStore:
    def __init__(self, counts: dict[str, int] | None = None) -> None:
        self.events: list[str] = []
        self.delete_calls: list[list[str]] = []
        self._counts = counts or {}

    def counts(self, ids: list[str]) -> dict[str, int]:
        self.events.append("counts")
        return dict(self._counts)

    def missing_primary_ids(self, ids: list[str]) -> tuple[str, ...]:
        self.events.append("preflight")
        return ()

    def delete_datasource_rows_and_count(self, ids: list[str]) -> dict[str, int]:
        self.events.append("datasource")
        self.delete_calls.append(list(ids))
        return dict(self._counts)


class _FakeVerification:
    def __init__(self, result: NativeVerification | None = None) -> None:
        self.result = result or NativeVerification(authoritative=True)
        self.calls: list[tuple[tuple[str, ...], str]] = []

    async def verify(self, ids, *, user_scope: str) -> NativeVerification:
        self.calls.append((tuple(ids), user_scope))
        return self.result


class _FakeClient:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.calls: list[tuple[list[str], bool]] = []

    def delete_workflows(self, workflow_ids: list[str], *, delete_children: bool = False) -> None:
        self.events.append("client")
        self.calls.append((list(workflow_ids), delete_children))


async def _inline_runner(func, *args):
    return func(*args)


def _executor(
    store: _FakeStore | None = None,
    client: _FakeClient | None = None,
    verification: _FakeVerification | None = None,
) -> NativeCleanupExecutor:
    return NativeCleanupExecutor(
        store=store or _FakeStore(),
        client=client or _FakeClient(),
        verification=verification or _FakeVerification(),
        run_blocking=_inline_runner,
    )


class TestNativeStatementCoverage:
    def test_every_native_target_has_count_statement(self) -> None:
        tables = {table for table, _key in CLEANUP_NATIVE_TARGETS}
        assert set(CLEANUP_COUNT_SQL) == tables
        assert len(tables) == 9

    @pytest.mark.parametrize("table", [table for table, _key in CLEANUP_NATIVE_TARGETS])
    def test_count_statement_is_id_set_parameterized(self, table: str) -> None:
        sql = CLEANUP_COUNT_SQL[table]
        assert sql.startswith("SELECT COUNT(*)")
        assert "ANY(:ids)" in sql
        assert "LIKE" not in sql.upper()
        assert "%" not in sql

    def test_datasource_delete_targets_only_constant_table(self) -> None:
        assert CLEANUP_DATASOURCE_DELETE_SQL.startswith("DELETE FROM agent_ds.datasource_outputs")
        assert "workflow_id = ANY(:ids)" in CLEANUP_DATASOURCE_DELETE_SQL
        assert "dbos." not in CLEANUP_DATASOURCE_DELETE_SQL
        assert "LIKE" not in CLEANUP_DATASOURCE_DELETE_SQL.upper()

    def test_primary_presence_covers_status_and_datasource(self) -> None:
        assert "dbos.workflow_status" in CLEANUP_PRIMARY_PRESENCE_SQL
        assert "agent_ds.datasource_outputs" in CLEANUP_PRIMARY_PRESENCE_SQL
        assert CLEANUP_PRIMARY_PRESENCE_SQL.count("ANY(:ids)") == 2


class _DeleteResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _AtomicConn:
    """假连接：同一事务内先 DELETE 再逐域 COUNT，记录语句顺序。"""

    def __init__(self, *, deleted: int, counts: dict[str, int] | None = None) -> None:
        self.deleted = deleted
        self.overrides = counts or {}
        self.statements: list[str] = []

    def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        self.statements.append(sql)
        if sql.startswith("DELETE"):
            return _DeleteResult(self.deleted)
        table = re.search(r"FROM\s+([\w.]+)", sql)
        assert table is not None, sql
        return _ScalarResult(self.overrides.get(table.group(1), 0))


class _AtomicTransaction:
    def __init__(self, engine: _AtomicEngine) -> None:
        self._engine = engine

    def __enter__(self) -> _AtomicConn:
        return self._engine.conn

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object) -> bool:
        if exc_type is None:
            self._engine.commits += 1
        else:
            self._engine.rollbacks += 1
        return False


class _AtomicEngine:
    """假引擎：只提供 ``begin()``（无 ``connect``），证明核零不跨事务/跨连接。"""

    def __init__(self, conn: _AtomicConn) -> None:
        self.conn = conn
        self.begins = 0
        self.commits = 0
        self.rollbacks = 0

    def begin(self) -> _AtomicTransaction:
        self.begins += 1
        return _AtomicTransaction(self)


class TestDatasourceDeleteAtomicity:
    """I82：Datasource 删除与删后查零同一事务；非零即回滚，不留跨事务窗口。"""

    @staticmethod
    def _zeros() -> dict[str, int]:
        return {table: 0 for table, _key in CLEANUP_NATIVE_TARGETS}

    @staticmethod
    def _store(engine: _AtomicEngine) -> AgentRuntimeNativeStore:
        return AgentRuntimeNativeStore(engine)  # type: ignore[arg-type]  # 测试替身引擎

    def test_zero_counts_commit_in_single_transaction(self) -> None:
        conn = _AtomicConn(deleted=2)
        engine = _AtomicEngine(conn)

        counts = self._store(engine).delete_datasource_rows_and_count(["wf-1"])

        assert engine.begins == 1
        assert engine.commits == 1
        assert engine.rollbacks == 0
        assert set(counts) == {table for table, _key in CLEANUP_NATIVE_TARGETS}
        assert set(counts.values()) == {0}
        assert len(conn.statements) == 1 + len(CLEANUP_NATIVE_TARGETS)
        assert conn.statements[0].startswith("DELETE FROM agent_ds.datasource_outputs")

    def test_non_zero_rolls_back_and_reports_restored_rows(self) -> None:
        in_txn = self._zeros()
        in_txn["dbos.streams"] = 1
        engine = _AtomicEngine(_AtomicConn(deleted=2, counts=in_txn))

        counts = self._store(engine).delete_datasource_rows_and_count(["wf-1"])

        # 抛异常即回滚：删除不提交，原行保持（原 job/fence 可重试）。
        assert engine.rollbacks == 1
        assert engine.commits == 0
        assert counts["dbos.streams"] == 1
        # 回滚恢复被删行：Datasource 域如实并入 2 行，不得报 0（R21）。
        assert counts["agent_ds.datasource_outputs"] == 2

    def test_datasource_rows_surviving_delete_roll_back_and_count_sum(self) -> None:
        in_txn = self._zeros()
        in_txn["agent_ds.datasource_outputs"] = 1
        engine = _AtomicEngine(_AtomicConn(deleted=3, counts=in_txn))

        counts = self._store(engine).delete_datasource_rows_and_count(["wf-1"])

        assert engine.rollbacks == 1
        assert counts["agent_ds.datasource_outputs"] == 4

    def test_disposed_engine_rejected_before_transaction(self) -> None:
        engine = _AtomicEngine(_AtomicConn(deleted=0))
        engine_provider.set_engine(engine)
        engine_provider.mark_disposed(True)
        try:
            with pytest.raises(EngineDisposedError) as exc:
                self._store(engine).delete_datasource_rows_and_count(["wf-1"])
        finally:
            engine_provider.reset_engine_provider()

        assert "Engine disposed, delete rejected." in str(exc.value)
        assert engine.begins == 0
        assert engine.commits == 0


class TestNativeCleanupExecutor:
    @pytest.mark.asyncio
    async def test_delete_runs_public_system_delete_before_atomic_datasource_step(self) -> None:
        store = _FakeStore(counts={"dbos.workflow_status": 0})
        client = _FakeClient()
        result = await _executor(store, client).delete(["wf-2", "wf-1"])

        assert client.events + store.events == ["client", "datasource"]
        assert client.calls == [(["wf-1", "wf-2"], False)]
        assert store.delete_calls == [["wf-1", "wf-2"]]
        assert result.exact_ids == ("wf-1", "wf-2")
        assert result.verified_counts == {"dbos.workflow_status": 0}

    @pytest.mark.asyncio
    async def test_delete_never_requests_children(self) -> None:
        client = _FakeClient()
        await _executor(client=client).delete(["wf-1"])
        assert client.calls[0][1] is False

    @pytest.mark.asyncio
    async def test_empty_set_is_zero_side_effect(self) -> None:
        store = _FakeStore()
        client = _FakeClient()
        result = await _executor(store, client).delete([])

        assert client.events == []
        assert store.events == []
        assert result.exact_ids == ()
        assert len(result.verified_counts) == 9
        assert set(result.verified_counts.values()) == {0}

    @pytest.mark.asyncio
    async def test_preflight_returns_counts_and_missing(self) -> None:
        store = _FakeStore(counts={"dbos.workflow_status": 1})
        verification = _FakeVerification()
        result = await _executor(store, verification=verification).preflight(["wf-1"], user_scope="owner-1")

        assert store.events == ["counts", "preflight"]
        assert verification.calls == [(("wf-1",), "owner-1")]
        assert result.exact_ids == ("wf-1",)
        assert result.counts == {"dbos.workflow_status": 1}
        assert result.missing_ids == ()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("verification", "reason"),
        [
            (NativeVerification(authoritative=False), "verification_unavailable"),
            (NativeVerification(authoritative=True, foreign_ids=("wf-1",)), "ownership_mismatch"),
            (NativeVerification(authoritative=True, non_terminal_ids=("wf-1",)), "non_terminal_target"),
            (
                NativeVerification(authoritative=True, externally_referenced_ids=("wf-1",)),
                "external_reference_present",
            ),
            (NativeVerification(authoritative=True, writers_running_ids=("wf-1",)), "writers_still_running"),
            (NativeVerification(authoritative=True, unknown_delivery_ids=("wf-1",)), "unknown_delivery_pending"),
        ],
    )
    async def test_preflight_refuses_without_authoritative_clearance(
        self, verification: NativeVerification, reason: str
    ) -> None:
        store = _FakeStore()
        with pytest.raises(AgentServiceError) as exc:
            await _executor(store, verification=_FakeVerification(verification)).preflight(
                ["wf-1"], user_scope="owner-1"
            )
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert reason in str(exc.value)
        assert store.events == []

    @pytest.mark.asyncio
    async def test_more_than_two_hundred_rejected_before_side_effects(self) -> None:
        store = _FakeStore()
        client = _FakeClient()
        executor = _executor(store, client)
        ids = [f"wf-{index:03d}" for index in range(CLEANUP_MAX_BATCH_IDS + 1)]

        with pytest.raises(AgentServiceError) as exc:
            await executor.delete(ids)
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert client.events == []
        assert store.events == []

    @pytest.mark.asyncio
    async def test_blank_id_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as exc:
            await _executor().preflight(["wf-1", ""], user_scope="owner-1")
        assert exc.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio
    async def test_blank_user_scope_rejected_before_verification(self) -> None:
        verification = _FakeVerification()
        with pytest.raises(AgentServiceError) as exc:
            await _executor(verification=verification).preflight(["wf-1"], user_scope="")
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert verification.calls == []


class TestCleanupTombstoneContract:
    def test_tombstone_constant_has_no_original_text_and_fixed_digest(self) -> None:
        assert CLEANUP_TOMBSTONE == "[已清理]"
        assert sha256_hex(CLEANUP_TOMBSTONE.encode("utf-8")) == CLEANUP_TOMBSTONE_DIGEST
        assert len(CLEANUP_TOMBSTONE_DIGEST) == 64

    def _dao(self) -> AgentDao:
        # 未初始化引擎也可校验入参：校验发生在使用引擎之前（引擎仅需存在）。
        return AgentDao(object())

    @pytest.mark.asyncio
    async def test_blank_user_scope_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as exc:
            await self._dao().tombstone_cleanup_text("", session_ids=["s-1"])
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert "user_scope" in str(exc.value)

    @pytest.mark.asyncio
    async def test_invalid_asset_key_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as exc:
            await self._dao().tombstone_cleanup_text("u", asset_keys=[("asset-1", "")])
        assert exc.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio
    async def test_asset_key_over_batch_limit_rejected(self) -> None:
        keys = [(f"asset-{index:03d}", "1") for index in range(CLEANUP_MAX_BATCH_IDS + 1)]
        with pytest.raises(AgentServiceError) as exc:
            await self._dao().tombstone_cleanup_text("u", asset_keys=keys)
        assert exc.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio
    async def test_empty_domains_are_no_write(self) -> None:
        touched = await self._dao().tombstone_cleanup_text("u")
        assert set(touched) == {
            "agent_session",
            "agent_asset_version",
            "agent_message",
            "agent_artifact",
            "agent_round",
            "agent_operation",
            "agent_body_payload",
            "agent_execution",
            "agent_decision",
            "agent_command",
            "agent_source_link",
        }
        assert all(count == 0 for count in touched.values())


class TestCleanupPhaseWriteValidation:
    """D31：落库前严格核验，缺域/非零/bool/complete 矛盾一律 INPUT_INVALID。"""

    def _dao(self) -> AgentDao:
        # 未初始化引擎也可校验入参：校验发生在使用引擎之前。
        return AgentDao(object())

    @staticmethod
    def _zeros() -> dict[str, int]:
        return {table: 0 for table, _key in CLEANUP_NATIVE_TARGETS}

    async def _rejected(self, **kwargs: object) -> AgentServiceError:
        with pytest.raises(AgentServiceError) as exc:
            await self._dao().save_cleanup_phase("u", "job-1", **kwargs)  # type: ignore[arg-type]  # 测试受控入参
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        return exc.value

    @pytest.mark.asyncio
    async def test_verified_without_counts_rejected(self) -> None:
        await self._rejected(phase=CleanupPhase.VERIFIED, complete=True)

    @pytest.mark.asyncio
    async def test_verified_with_missing_domain_rejected(self) -> None:
        counts = self._zeros()
        del counts["agent_ds.datasource_outputs"]
        await self._rejected(phase=CleanupPhase.VERIFIED, verified_counts=counts, complete=True)

    @pytest.mark.asyncio
    async def test_verified_with_bool_count_rejected(self) -> None:
        counts = self._zeros()
        counts["dbos.streams"] = False
        await self._rejected(phase=CleanupPhase.VERIFIED, verified_counts=counts, complete=True)

    @pytest.mark.asyncio
    async def test_verified_with_non_zero_count_rejected(self) -> None:
        counts = self._zeros()
        counts["agent_ds.datasource_outputs"] = 1
        await self._rejected(phase=CleanupPhase.VERIFIED, verified_counts=counts, complete=True)

    @pytest.mark.asyncio
    async def test_verified_with_complete_false_rejected(self) -> None:
        await self._rejected(phase=CleanupPhase.VERIFIED, verified_counts=self._zeros(), complete=False)

    @pytest.mark.asyncio
    async def test_retry_pending_with_complete_true_rejected(self) -> None:
        await self._rejected(phase=CleanupPhase.RETRY_PENDING, verified_counts=self._zeros(), complete=True)

    @pytest.mark.asyncio
    async def test_retry_pending_with_bool_count_rejected(self) -> None:
        await self._rejected(
            phase=CleanupPhase.RETRY_PENDING,
            verified_counts={"dbos.workflow_status": False},
            complete=False,
        )

    @pytest.mark.asyncio
    async def test_valid_verified_reaches_engine_requirement(self) -> None:
        """校验通过后才触达引擎要求（本单测无真实引擎，证明未被入参校验拦下）。"""

        with pytest.raises(AgentServiceError) as exc:
            await self._dao().save_cleanup_phase(
                "u", "job-1", phase=CleanupPhase.VERIFIED, verified_counts=self._zeros(), complete=True
            )
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_retry_pending_non_zero_partial_counts_reaches_engine_requirement(self) -> None:
        with pytest.raises(AgentServiceError) as exc:
            await self._dao().save_cleanup_phase(
                "u",
                "job-1",
                phase=CleanupPhase.RETRY_PENDING,
                verified_counts={"dbos.workflow_status": 2},
                complete=False,
            )
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE


class _StageResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _StageConn:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _StageResult:
        self.statements.append(str(statement))
        return _StageResult(self.rowcount)


class TestCleanupAttemptStageWrite:
    """M14：删除尝试阶段只经 ``error_category`` 承载，且要求 fence 已提交、未 verified。"""

    def _dao(self, conn: _StageConn) -> AgentDao:
        engine = MagicMock(spec=AsyncEngine)
        for name in ("begin", "connect"):
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=conn)
            cm.__aexit__ = AsyncMock(return_value=False)
            setattr(engine, name, MagicMock(return_value=cm))
        return AgentDao(engine)

    @pytest.mark.asyncio
    async def test_stage_write_targets_error_category_under_fence_and_unverified(self) -> None:
        conn = _StageConn()
        await self._dao(conn).save_cleanup_attempt_stage("u", "job-1", stage="cleanup_native_delete_started")

        assert len(conn.statements) == 1
        sql = conn.statements[0]
        assert sql.startswith("UPDATE agent_cleanup_job")
        assert "error_category=" in sql
        assert "fence_committed IS true" in sql
        assert "phase !=" in sql
        # 阶段写入不得改写阶段/计数（单调与诊断事实由既有列各自承载）。
        assert "verified_counts" not in sql
        assert "phase=" not in sql

    @pytest.mark.asyncio
    async def test_stage_write_rejected_when_target_missing_or_verified(self) -> None:
        with pytest.raises(AgentServiceError) as exc:
            await self._dao(_StageConn(rowcount=0)).save_cleanup_attempt_stage(
                "u", "job-1", stage="cleanup_native_delete_started"
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert "already verified" in str(exc.value)

    @pytest.mark.asyncio
    async def test_blank_stage_rejected_before_engine_requirement(self) -> None:
        with pytest.raises(AgentServiceError) as exc:
            await AgentDao(object()).save_cleanup_attempt_stage("u", "job-1", stage="")
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert "attempt stage" in str(exc.value)

    @pytest.mark.asyncio
    async def test_valid_stage_reaches_engine_requirement(self) -> None:
        with pytest.raises(AgentServiceError) as exc:
            await AgentDao(object()).save_cleanup_attempt_stage("u", "job-1", stage="cleanup_native_delete_started")
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
