"""``read_leaf_dispatch_facts`` 成果归属投影（I43 扩展）。

- 单一 join 同时给出 user_scope / owner_epoch / run_epoch / session_id / input_revision；
- execution/round 缺失或轮不属执行 → ``INPUT_INVALID``（零写）；
- 不新增写路径、不迁移表结构。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import AgentErrorCode, AgentServiceError
from data.persistence.daos.agent_dao import AgentDao, LeafDispatchFacts

pytestmark = pytest.mark.unit


class _Conn:
    def __init__(self, row: Any) -> None:
        self.row = row

    async def execute(self, statement: Any, params: Any = None) -> Any:
        result = MagicMock()
        result.first.return_value = self.row
        return result


def _dao(row: Any) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=_Conn(row))
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.connect = MagicMock(return_value=cm)
    return AgentDao(engine)


class TestReadLeafDispatchFacts:
    async def test_projects_attribution_fields(self) -> None:
        row = SimpleNamespace(
            user_scope="user-1",
            owner_epoch=3,
            run_epoch=2,
            session_id="session-1",
            input_revision=4,
        )

        facts = await _dao(row).read_leaf_dispatch_facts("execution-1", "round-1")

        assert facts == LeafDispatchFacts(
            user_scope="user-1",
            owner_epoch=3,
            run_epoch=2,
            session_id="session-1",
            input_revision=4,
        )

    async def test_missing_round_is_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(None).read_leaf_dispatch_facts("execution-1", "round-1")

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
