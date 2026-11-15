"""Agent 运行库原生目标存储（E-06-2c / 详细设计 §10.7）。

运行库是独立逻辑库（``dbos`` / ``agent_ds``，见 ``agent_migrator``），与业务库分离；
本类必须由**运行库引擎**构造。复用业务库 ``AgentDao`` 会把 DML 打到不存在的 schema，
故不并入 ``AgentDao``，也不由 ``CacheManager``（业务库）托管。

职责边界：
- 只提供九类原生目标的逐域只读计数、主键域存在性预检、Datasource 常量表参数化删除；
- 系统表（``dbos.*``）删除**不在此直写**：按 §10.7 走 DBOS 公开 API
  ``Client.delete_workflows``（见 ``services.agent_service.cleanup_native``），
  不调用私有 purge、不用 LIKE/前缀/递归/全表删除；
- 同步引擎 API：调用方（应用服务执行器）负责经受管线程提交（R16）。
"""

from __future__ import annotations

from typing import Final

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from core.agent_types import CLEANUP_NATIVE_TARGETS
from data.persistence import engine_provider
from data.persistence.daos.base_dao import EngineDisposedError

#: 九类原生目标逐域计数（只读；键为常量匹配键，集合经展开参数绑定）。
CLEANUP_COUNT_SQL: Final[dict[str, str]] = {
    "dbos.workflow_status": "SELECT COUNT(*) FROM dbos.workflow_status WHERE workflow_uuid = ANY(:ids)",
    "dbos.workflow_input": "SELECT COUNT(*) FROM dbos.workflow_input WHERE workflow_uuid = ANY(:ids)",
    "dbos.workflow_output": "SELECT COUNT(*) FROM dbos.workflow_output WHERE workflow_uuid = ANY(:ids)",
    "dbos.operation_outputs": "SELECT COUNT(*) FROM dbos.operation_outputs WHERE workflow_uuid = ANY(:ids)",
    "dbos.workflow_events": "SELECT COUNT(*) FROM dbos.workflow_events WHERE workflow_uuid = ANY(:ids)",
    "dbos.workflow_events_history": (
        "SELECT COUNT(*) FROM dbos.workflow_events_history WHERE workflow_uuid = ANY(:ids)"
    ),
    "dbos.streams": "SELECT COUNT(*) FROM dbos.streams WHERE workflow_uuid = ANY(:ids)",
    "dbos.notifications": "SELECT COUNT(*) FROM dbos.notifications WHERE destination_uuid = ANY(:ids)",
    "agent_ds.datasource_outputs": ("SELECT COUNT(*) FROM agent_ds.datasource_outputs WHERE workflow_id = ANY(:ids)"),
}

#: Datasource 常量表参数化删除（§10.7：固定参数化 DML，不调用私有 purge）。
CLEANUP_DATASOURCE_DELETE_SQL: Final = "DELETE FROM agent_ds.datasource_outputs WHERE workflow_id = ANY(:ids)"

#: 主键域存在性预检（§10.7「查不到不是已清」）：ID 至少应出现在系统状态或 Datasource 输出之一。
CLEANUP_PRIMARY_PRESENCE_SQL: Final = """
SELECT workflow_uuid FROM dbos.workflow_status WHERE workflow_uuid = ANY(:ids)
UNION
SELECT DISTINCT workflow_id FROM agent_ds.datasource_outputs WHERE workflow_id = ANY(:ids)
"""


def _bind_ids(statement: str) -> sa.TextClause:
    """常量 SQL + 数组参数绑定（R4：SQL 为常量，集合经 ``:ids`` 参数化）。

    准确 ID 集合作为**单参数 text[]** 绑定给 ``= ANY(:ids)``：不展开占位符，
    避免单元素集合被展开成标量而无法按数组比较（psycopg/asyncpg 均按数组适配）。
    """

    return sa.text(statement).bindparams(sa.bindparam("ids", type_=sa.ARRAY(sa.Text)))


class AgentRuntimeNativeStore:
    """运行库九类原生目标的计数/预检查回/ Datasource 删除（同步引擎）。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _check_engine(self, context: str = "cleanup") -> None:
        """R5 守卫：已释放引擎上不得执行计数/预检/删除。"""

        if engine_provider.is_disposed(self._engine):
            raise EngineDisposedError(f"[{self.__class__.__name__}] Engine disposed, {context} rejected.")

    def counts(self, ids: list[str]) -> dict[str, int]:
        """逐域计数：返回九类目标各自命中准确集合的行数。"""

        self._check_engine("count")
        counts: dict[str, int] = {}
        with self._engine.connect() as conn:
            for table, _key in CLEANUP_NATIVE_TARGETS:
                counts[table] = int(conn.execute(_bind_ids(CLEANUP_COUNT_SQL[table]), {"ids": ids}).scalar_one())
        return counts

    def missing_primary_ids(self, ids: list[str]) -> tuple[str, ...]:
        """返回在两个主键域都查不到的 ID（查不到不得当作已清除）。"""

        self._check_engine("preflight")
        with self._engine.connect() as conn:
            found = {
                str(row[0]) for row in conn.execute(_bind_ids(CLEANUP_PRIMARY_PRESENCE_SQL), {"ids": ids}).fetchall()
            }
        return tuple(sorted(set(ids) - found))

    def delete_datasource_rows(self, ids: list[str]) -> int:
        """按准确集合删除 ``agent_ds.datasource_outputs``；返回删除行数。"""

        self._check_engine("delete")
        with self._engine.begin() as conn:
            result = conn.execute(_bind_ids(CLEANUP_DATASOURCE_DELETE_SQL), {"ids": ids})
            return int(result.rowcount or 0)
