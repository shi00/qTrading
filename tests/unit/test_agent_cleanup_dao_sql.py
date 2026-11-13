"""E-06-2b 清理 DAO 静态合同测试（不连数据库；执行行为留集成分片）。

覆盖：九类目标与常量 SQL 一一对应、全部使用展开参数 ``:ids``、无 LIKE/前缀、
无 f-string 拼接；空集合/超 200 在触达引擎前拒绝。真实删除与逐域查 0 由
E-06-2c（真 sidecar PG）验证，本文件不冒充集成证据。
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy import bindparam, text

from core.agent_types import CLEANUP_MAX_BATCH_IDS, CLEANUP_NATIVE_TARGETS, AgentErrorCode, AgentServiceError
from data.persistence.daos.agent_dao import (
    AgentDao,
    _CLEANUP_COUNT_SQL,
    _CLEANUP_DELETE_SQL,
)


class TestStatementCoverage:
    def test_every_native_target_has_both_statements(self) -> None:
        tables = {table for table, _key in CLEANUP_NATIVE_TARGETS}
        assert set(_CLEANUP_DELETE_SQL) == tables
        assert set(_CLEANUP_COUNT_SQL) == tables
        assert len(tables) == 9

    @pytest.mark.parametrize("table", [table for table, _key in CLEANUP_NATIVE_TARGETS])
    def test_statements_are_id_list_parameterized(self, table: str) -> None:
        for sql in (_CLEANUP_DELETE_SQL[table], _CLEANUP_COUNT_SQL[table]):
            assert "ANY(:ids)" in sql
            assert "LIKE" not in sql.upper()
            assert "%" not in sql
            assert table.split(".")[0] + "." + table.split(".")[1] in sql

    @pytest.mark.parametrize("table", [table for table, _key in CLEANUP_NATIVE_TARGETS])
    def test_delete_uses_matching_constant_key(self, table: str) -> None:
        key = dict(CLEANUP_NATIVE_TARGETS)[table]
        assert f"{key} = ANY(:ids)" in _CLEANUP_DELETE_SQL[table]
        assert f"{key} = ANY(:ids)" in _CLEANUP_COUNT_SQL[table]

    def test_expanding_bindparam_renders_placeholder_not_inlined_values(self) -> None:
        stmt = text(_CLEANUP_DELETE_SQL["dbos.workflow_status"]).bindparams(bindparam("ids", expanding=True))
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        assert "workflow_uuid" in compiled
        assert "secret-id" not in compiled


class TestRejectedBeforeEngineAccess:
    def _dao(self) -> AgentDao:
        # 不初始化引擎：拒绝必须发生在触达引擎之前。
        return object.__new__(AgentDao)

    @pytest.mark.asyncio
    async def test_empty_ids_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as exc:
            await self._dao().delete_cleanup_native_rows([])
        assert exc.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio
    async def test_more_than_two_hundred_rejected(self) -> None:
        ids = [f"id-{index:03d}" for index in range(CLEANUP_MAX_BATCH_IDS + 1)]
        with pytest.raises(AgentServiceError) as exc:
            await self._dao().count_cleanup_native_rows(ids)
        assert exc.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio
    async def test_blank_id_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as exc:
            await self._dao().count_cleanup_native_rows(["ok", ""])
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
