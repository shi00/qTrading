"""Agent 运行时宿主组装（设计 §10.6 / HANDOFF §8.1-§8.3）。

职责（C-05）：在存储层迁移就绪后组装宿主对象并保证关闭顺序——
1. 双迁移（``data.persistence.agent_migrator.run_agent_runtime_migrations``：迁移+
   运行角色最小授权+版本守卫，独立迁移引擎 ≤2 连接，先于任何宿主对象；中间失败
   不 launch/不建对象/保历史与原迁移状态）；
2. ``runtime_engine``（pool 4 / overflow 0 / timeout 3 / hide_parameters，psycopg 同步）
   与 ``control_engine``（pool 2，禁额外监听）；
3. ``DBOS(config)``：注入 runtime_engine、``run_migrations=False``（launch 改为验证
   模式）、``enable_otlp=False``、``max_executor_threads=4``、``use_listen_notify=True``、
   ``sharding=False``、真实 ``application_version``、每进程唯一 ``executor_id``；
4. ``SQLAlchemyDatasource.create(engine=runtime_engine, schema="agent_ds",
   run_migrations=False)``（verify_migrations 模式）；
5. ``DBOSClient(system_database_engine=control_engine, use_listen_notify=False,
   retry_connection_errors=False)``（HANDOFF §8.3：禁监听、禁无限连接重试）。

明确不做：
- **不 launch**：默认 launch 会将所属 pending workflows 重新入队且无公开关闭开关
  （HANDOFF §8.4 needs-spike）；隔离方案真 PG/真进程实证通过后经
  ``launch_agent_runtime``（listen_queues([]) 先于 launch）启动；
- owner-loop 桥（§8.2：loop 句柄登记与跨线程提交）属 D-01，不在本片。

关闭顺序（HANDOFF §8.3 destroy 契约）：``DBOS.destroy``（工作收敛）→
``DBOSClient.destroy`` → 双引擎 dispose；幂等。

SQL 合规（R4）：本模块不执行任何 SQL（迁移/授权均在 data 层，静态 text() 常量）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from dbos import DBOS, DBOSClient, SQLAlchemyDatasource

from data.persistence.agent_migrator import (
    AgentRuntimeMigrationError,
    run_agent_runtime_migrations,
)

# 设计冻结值（HANDOFF §8.1 第 6/7 条、§8.3）：连接预算与线程/监听配置。
RUNTIME_DB_NAME = "qtrading-agent-runtime"
RUNTIME_ENGINE_POOL_SIZE = 4
CONTROL_ENGINE_POOL_SIZE = 2
DBOS_MAX_EXECUTOR_THREADS = 4
DBOS_SCHEMA = "dbos"
AGENT_DS_SCHEMA = "agent_ds"
PSYCGRP_APPLICATION_NAME = "qtrading_agent_runtime"


@dataclass(frozen=True)
class AgentRuntimeSettings:
    """宿主组装参数。

    Attributes:
        system_database_url: 同步 psycopg 运行库 URL（含密码，仅内存传递，不入日志）。
        application_version: 真实工作流代码版本（ADR-0017：pyproject version 唯一正本），
            禁止随机值规避重放检查（HANDOFF §8.3）。
        executor_id: 每进程执行 epoch（HANDOFF §8.4 第 4 条）；缺省每进程生成唯一值。
    """

    system_database_url: str
    application_version: str
    executor_id: str | None = None


@dataclass
class AgentRuntimeHandle:
    """组装产物：持有全部宿主对象引用与关闭入口（未 launch）。"""

    runtime_engine: sa.Engine
    control_engine: sa.Engine
    dbos: DBOS
    datasource: SQLAlchemyDatasource
    client: DBOSClient
    executor_id: str
    _launched: bool = False
    _closed: bool = False

    @property
    def launched(self) -> bool:
        """宿主是否已 launch（启动隔离 needs-spike 通过前经本入口恒 False）。"""
        return self._launched

    def close(self) -> None:
        """按契约顺序关闭（幂等）：DBOS.destroy → Client.destroy → 双引擎 dispose。"""
        if self._closed:
            return
        self._closed = True
        DBOS.destroy()
        self.client.destroy()
        self.runtime_engine.dispose()
        self.control_engine.dispose()


def assemble_agent_runtime(settings: AgentRuntimeSettings) -> AgentRuntimeHandle:
    """组装 Agent 运行时宿主（不 launch）。

    双迁移失败时异常原样传播，不创建任何引擎/宿主对象（§10.6：不 launch、不补投、
    保历史与原迁移状态）。

    Args:
        settings: 组装参数（URL/application_version/executor_id）。

    Returns:
        未 launch 的宿主句柄；启动须经 ``launch_agent_runtime``（隔离 spike 通过后）。

    Raises:
        AgentRuntimeMigrationError: 双迁移/授权/版本守卫失败。
    """
    run_agent_runtime_migrations(settings.system_database_url)

    executor_id = settings.executor_id or _new_executor_id()
    runtime_engine = _create_runtime_engine(settings.system_database_url)
    control_engine = _create_control_engine(settings.system_database_url)
    try:
        dbos = DBOS(
            config={
                "name": RUNTIME_DB_NAME,
                "system_database_url": settings.system_database_url,
                "system_database_engine": runtime_engine,
                "run_migrations": False,
                "enable_otlp": False,
                "max_executor_threads": DBOS_MAX_EXECUTOR_THREADS,
                "use_listen_notify": True,
                "sharding": False,
                "application_version": settings.application_version,
                "executor_id": executor_id,
            }
        )
        datasource = SQLAlchemyDatasource.create(
            database_url=settings.system_database_url,
            engine=runtime_engine,
            schema=AGENT_DS_SCHEMA,
            run_migrations=False,
        )
        client = DBOSClient(
            system_database_engine=control_engine,
            dbos_system_schema=DBOS_SCHEMA,
            use_listen_notify=False,
            retry_connection_errors=False,
        )
    except BaseException:
        _dispose_quietly(control_engine)
        _dispose_quietly(runtime_engine)
        raise
    return AgentRuntimeHandle(
        runtime_engine=runtime_engine,
        control_engine=control_engine,
        dbos=dbos,
        datasource=datasource,
        client=client,
        executor_id=executor_id,
    )


def launch_agent_runtime(handle: AgentRuntimeHandle) -> None:
    """启动宿主（HANDOFF §8.3/§8.4：listen_queues([]) 先于 launch 一次）。

    启动隔离（pending workflows 0 派发）为 needs-spike 验证门（§8.4）；本入口仅在
    隔离方案实证通过后由启动编排调用。
    """
    DBOS.listen_queues([])
    DBOS.launch()
    handle._launched = True  # 句柄内部状态


def _new_executor_id() -> str:
    """每进程唯一 executor epoch（HANDOFF §8.4 第 4 条）。"""
    return f"local-{uuid.uuid4().hex[:12]}"


def _create_runtime_engine(url: str) -> sa.Engine:
    """runtime 引擎：pool 4 / overflow 0 / timeout 3 / hide_parameters（§8.1 第 6 条）。"""
    return _create_engine(
        url,
        pool_size=RUNTIME_ENGINE_POOL_SIZE,
        max_overflow=0,
        pool_timeout=3,
        hide_parameters=True,
        connect_args={"application_name": PSYCGRP_APPLICATION_NAME},
    )


def _create_control_engine(url: str) -> sa.Engine:
    """control 引擎：pool 2 / overflow 0 / hide_parameters（§8.1 第 7 条，禁额外监听）。"""
    return _create_engine(
        url,
        pool_size=CONTROL_ENGINE_POOL_SIZE,
        max_overflow=0,
        pool_timeout=3,
        hide_parameters=True,
        connect_args={"application_name": PSYCGRP_APPLICATION_NAME},
    )


def _create_engine(url: str, **engine_kwargs: Any) -> sa.Engine:
    """引擎构建 seam（单元测试注入点）。"""
    return sa.create_engine(url, **engine_kwargs)


def _dispose_quietly(engine: sa.Engine) -> None:
    """组装半途失败时的兜底释放：dispose 异常不掩盖原始异常。"""
    try:
        engine.dispose()
    except Exception:  # 兜底清理不得掩盖组装原始异常
        pass


__all__ = [
    "AGENT_DS_SCHEMA",
    "DBOS_SCHEMA",
    "AgentRuntimeHandle",
    "AgentRuntimeMigrationError",
    "AgentRuntimeSettings",
    "assemble_agent_runtime",
    "launch_agent_runtime",
]
