"""Agent 运行时宿主组装集成测试（设计 §10.6 / HANDOFF §8.1-§8.3）。

真实 sidecar PG 上验证：
- 全新独立逻辑库上 assemble_agent_runtime 真实构建：双迁移 → 双引擎 → DBOS（未
  launch）→ Datasource（agent_ds 已迁移）→ Client（连接可达）
- close() 后引擎释放、DBOS 全局单例清理（R7 测试隔离边界）
- 不调用 launch（启动隔离 needs-spike 验证前不得 launch，HANDOFF §8.4）

编排契约与引擎预算矩阵见 ``tests/unit/services/test_agent_runtime_host.py``。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import text

from dbos import DBOS
from services.agent_service.runtime import (
    AgentRuntimeHandle,
    AgentRuntimeSettings,
    assemble_agent_runtime,
)
from tests.integration.test_agent_runtime_migrator import _reset_runtime_schemas

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]


@pytest_asyncio.fixture(loop_scope="session")
async def runtime_db_url(real_embedded_pg) -> AsyncGenerator[str]:
    """复用迁移器测试的独立逻辑库（创建/角色），并逐用例重置两 schema。"""
    info = real_embedded_pg
    admin_url = info.url
    from tests.integration.test_agent_runtime_migrator import (
        _create_runtime_database,
        _drop_runtime_database,
        _ensure_runtime_role,
    )

    await _create_runtime_database(admin_url)
    await _ensure_runtime_role(admin_url)
    sync_url = (
        sa.make_url(admin_url)
        .set(drivername="postgresql+psycopg", database="qtrading_agent_runtime")
        .render_as_string(hide_password=False)
    )
    yield sync_url
    # 关闭顺序含 DBOS.destroy：全局单例清理不得遗留到后续用例（R7）
    DBOS.destroy(destroy_registry=True)
    await _drop_runtime_database(admin_url)


@pytest.mark.asyncio(loop_scope="session")
async def test_assemble_real_host_without_launch(runtime_db_url: str) -> None:
    """真实组装：迁移→引擎→DBOS（未 launch）→Datasource→Client 全链路可用。"""
    await _reset_runtime_schemas(runtime_db_url)

    handle = assemble_agent_runtime(
        AgentRuntimeSettings(
            system_database_url=runtime_db_url,
            allow_shared_migration_credentials=True,  # 集成环境单 superuser 实例：显式共用
            application_version="0.11.7",
            executor_id="epoch-test",
        )
    )
    try:
        assert isinstance(handle, AgentRuntimeHandle)
        assert handle.launched is False

        # runtime 引擎池预算
        pool = handle.runtime_engine.pool
        assert pool.size() == 4
        assert pool._max_overflow == 0  # noqa: SLF001  # sqlalchemy 池内部属性，仅测试读取
        assert handle.runtime_engine.dialect.name == "postgresql"

        # Datasource：agent_ds 已存在且可写读（verify_migrations 已通过）
        with handle.runtime_engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'agent_ds'")
                ).fetchall()
            }
            assert "datasource_outputs" in tables

        # Client：构造即连接校验（未 lazy），system db 可达
        assert handle.client._sys_db is not None  # noqa: SLF001  # 连接事实断言
    finally:
        handle.close()
        handle.close()  # 幂等：仅关闭一次


@pytest.mark.asyncio(loop_scope="session")
async def test_close_allows_fresh_assemble(runtime_db_url: str) -> None:
    """close 释放全局单例后可重新组装（DBOS.destroy 清理生效，R7 边界）。"""
    await _reset_runtime_schemas(runtime_db_url)

    first = assemble_agent_runtime(
        AgentRuntimeSettings(
            system_database_url=runtime_db_url,
            allow_shared_migration_credentials=True,  # 集成环境单 superuser 实例：显式共用
            application_version="0.11.7",
            executor_id="epoch-a",
        )
    )
    first.close()
    assert first.launched is False

    second = assemble_agent_runtime(
        AgentRuntimeSettings(
            system_database_url=runtime_db_url,
            allow_shared_migration_credentials=True,  # 集成环境单 superuser 实例：显式共用
            application_version="0.11.7",
            executor_id="epoch-b",
        )
    )
    assert second.executor_id == "epoch-b"
    second.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_assemble_refuses_future_datasource_version(runtime_db_url: str) -> None:
    """Datasource 版本被篡改为未来值（2）：组装在迁移后守卫处拒绝，不建宿主对象。"""
    await _reset_runtime_schemas(runtime_db_url)
    # 先正常迁移到位，再把 Datasource 版本推到未来值（守卫须精确相等）
    from data.persistence.agent_migrator import run_agent_runtime_migrations

    run_agent_runtime_migrations(runtime_db_url)
    engine = sa.create_engine(runtime_db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE agent_ds.dbos_datasource_migrations SET version = :v"),
                {"v": 2},
            )
    finally:
        engine.dispose()

    from data.persistence.agent_migrator import AgentRuntimeMigrationError

    with pytest.raises(AgentRuntimeMigrationError) as exc_info:
        assemble_agent_runtime(
            AgentRuntimeSettings(
                system_database_url=runtime_db_url,
                application_version="0.11.7",
                executor_id="epoch-test",
            )
        )
    # 强断言：拒绝消息点名冻结合同值与实际值，且不含连接 URL（R9 脱敏边界）
    message = str(exc_info.value)
    assert "1" in message
    assert "2" in message
    assert "postgresql" not in message.lower()
    DBOS.destroy(destroy_registry=True)
