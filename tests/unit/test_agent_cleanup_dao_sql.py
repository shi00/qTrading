"""E-06-2c 清理执行静态合同与边界测试（不连数据库）。

覆盖：
- 运行库存储的九类计数 SQL 与 :data:`CLEANUP_NATIVE_TARGETS` 一一对应、全部
  ``ANY(:ids)`` 展参、无 LIKE/前缀；Datasource 删除只命中常量表；
- 执行器顺序（公开系统删除 → Datasource DML → 逐域查回）、显式不递归子工作流、
  空集合零副作用、超 200/空 ID 在触达客户端与存储前拒绝；
- 应用域 tombstone 的入参边界（空 user_scope、非法资产键、超 200）。

真实九类删除/集合外不变/NOT NULL tombstone 由 ``tests/integration/test_agent_dao_cleanup.py``
（真 sidecar PG）验证，本文件不冒充集成证据。
"""

from __future__ import annotations

import pytest

from core.agent_types import (
    CLEANUP_MAX_BATCH_IDS,
    CLEANUP_NATIVE_TARGETS,
    CLEANUP_TOMBSTONE,
    CLEANUP_TOMBSTONE_DIGEST,
    AgentErrorCode,
    AgentServiceError,
    sha256_hex,
)
from data.persistence.agent_runtime_store import (
    CLEANUP_COUNT_SQL,
    CLEANUP_DATASOURCE_DELETE_SQL,
    CLEANUP_PRIMARY_PRESENCE_SQL,
)
from data.persistence.daos.agent_dao import AgentDao
from services.agent_service.cleanup_native import NativeCleanupExecutor


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

    def delete_datasource_rows(self, ids: list[str]) -> int:
        self.events.append("datasource")
        self.delete_calls.append(list(ids))
        return 1


class _FakeClient:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.calls: list[tuple[list[str], bool]] = []

    def delete_workflows(self, workflow_ids: list[str], *, delete_children: bool = False) -> None:
        self.events.append("client")
        self.calls.append((list(workflow_ids), delete_children))


async def _inline_runner(func, *args):
    return func(*args)


def _executor(store: _FakeStore | None = None, client: _FakeClient | None = None) -> NativeCleanupExecutor:
    return NativeCleanupExecutor(
        store=store or _FakeStore(),
        client=client or _FakeClient(),
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


class TestNativeCleanupExecutor:
    @pytest.mark.asyncio
    async def test_delete_runs_public_system_delete_before_datasource_dml(self) -> None:
        store = _FakeStore(counts={"dbos.workflow_status": 0})
        client = _FakeClient()
        result = await _executor(store, client).delete(["wf-2", "wf-1"])

        assert client.events + store.events == ["client", "datasource", "counts"]
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
        assert result.verified_counts == {}

    @pytest.mark.asyncio
    async def test_preflight_returns_counts_and_missing(self) -> None:
        store = _FakeStore(counts={"dbos.workflow_status": 1})
        result = await _executor(store).preflight(["wf-1"])

        assert store.events == ["counts", "preflight"]
        assert result.exact_ids == ("wf-1",)
        assert result.counts == {"dbos.workflow_status": 1}
        assert result.missing_ids == ()

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
            await _executor().preflight(["wf-1", ""])
        assert exc.value.code is AgentErrorCode.INPUT_INVALID


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
        assert await self._dao().tombstone_cleanup_text("u") == {"agent_session": 0, "agent_asset_version": 0}
