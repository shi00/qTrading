"""Agent 运行库原生目标存储（E-06-2c / 详细设计 §10.7）。

运行库是独立逻辑库（``dbos`` / ``agent_ds``，见 ``agent_migrator``），与业务库分离；
本类必须由**运行库引擎**构造。复用业务库 ``AgentDao`` 会把 DML 打到不存在的 schema，
故不并入 ``AgentDao``，也不由 ``CacheManager``（业务库）托管。

职责边界：
- 只提供九类原生目标的逐域只读计数、主键域存在性预检、Datasource 常量表参数化删除；
- Datasource 删除与「删后九域查零」在**同一事务**内完成，非零即回滚（不留
  「已提交删除、另连接核零」的部分成功窗口）；
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

#: Datasource 域在九类计数中的匹配键（回滚后恢复行数须如实并入，R21）。
_DATASOURCE_TARGET: Final = "agent_ds.datasource_outputs"

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


class _RowsRemainingAfterDelete(Exception):
    """事务内删除后仍可见行（内部信号）：抛出即回滚删除，交调用方按原 job 重试。

    ``counts`` 为回滚后真实行数的诚实表示（Datasource 域已并入被回滚的删除行数）。
    """

    def __init__(self, counts: dict[str, int]) -> None:
        super().__init__("native rows remain after datasource delete")
        self.counts = counts


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

    def delete_datasource_rows_and_count(self, ids: list[str]) -> dict[str, int]:
        """同一事务内删除 ``agent_ds.datasource_outputs`` 并九域查零。

        删除与查零共享一个事务：任一行在提交前仍可见（删除未命中/并发写入）即
        **整体回滚**，原行保持可重试（原 job 与原 fence 不动）；绝不出现
        「先提交删除、再由另连接核零」的部分成功窗口。返回值是九域计数：
        提交时即提交后真实值，回滚时 Datasource 域已并入被回滚的删除行数（R21）。
        并发写入由准入保证（I81：同集合 writer 必须已退出）后再进入本事务。
        """

        self._check_engine("delete")
        try:
            with self._engine.begin() as conn:
                result = conn.execute(_bind_ids(CLEANUP_DATASOURCE_DELETE_SQL), {"ids": ids})
                deleted = int(result.rowcount or 0)
                counts = {
                    table: int(conn.execute(_bind_ids(CLEANUP_COUNT_SQL[table]), {"ids": ids}).scalar_one())
                    for table, _key in CLEANUP_NATIVE_TARGETS
                }
                if any(counts.values()):
                    if deleted > 0:
                        counts[_DATASOURCE_TARGET] += deleted
                    raise _RowsRemainingAfterDelete(counts)
                return counts
        except _RowsRemainingAfterDelete as exc:
            return exc.counts
