"""集成测试：验证两种数据库初始化路径产生等价的 Schema。

路径 A: DatabaseMigrator.init_db() — 使用 metadata.create_all() 创建全新数据库
路径 B: alembic command.upgrade(cfg, "head") — 通过 Alembic 迁移脚本逐步升级

两种路径最终应产生完全相同的表结构和列定义。
"""

import asyncio
import uuid

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import create_engine, inspect as sa_inspect

from data.persistence.db_migrator import DatabaseMigrator
from data.persistence.db_url_override import override_db_url
from tests._helpers import build_db_urls, create_test_engine, get_pg_connection_params, make_alembic_cfg

pytestmark = pytest.mark.integration


def _reflect_schema(sync_db_url: str) -> dict[str, dict[str, str]]:
    """反射数据库 Schema，返回 {表名: {列名: 类型字符串}} 的嵌套字典。"""
    engine = create_engine(sync_db_url)
    try:
        inspector = sa_inspect(engine)
        schema: dict[str, dict[str, str]] = {}
        for table_name in sorted(inspector.get_table_names()):
            # alembic_version 是版本控制表，不参与业务 Schema 比较
            if table_name == "alembic_version":
                continue
            columns: dict[str, str] = {}
            for col in inspector.get_columns(table_name):
                columns[col["name"]] = str(col["type"])
            schema[table_name] = columns
        return schema
    finally:
        engine.dispose()


@pytest.fixture
def pg_params():
    """提供 PostgreSQL 连接参数。"""
    return get_pg_connection_params()


class TestSchemaPathEquivalence:
    """验证 init_db (metadata.create_all) 与 Alembic upgrade 产生等价 Schema。"""

    @pytest_asyncio.fixture
    async def db_via_init_db(self, pg_params):
        """通过 DatabaseMigrator.init_db() 初始化的隔离数据库 (路径 A)。"""
        params = pg_params
        db_name = f"schema_initdb_{uuid.uuid4().hex[:8]}"

        conn = await asyncpg.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database="postgres",
        )
        await conn.execute(f'CREATE DATABASE "{db_name}"')
        await conn.close()

        sync_db_url, async_db_url = build_db_urls(params, db_name)

        engine = create_test_engine(async_db_url)

        # 路径 A: 使用 init_db (内部走 metadata.create_all)
        with override_db_url(sync_db_url):
            await DatabaseMigrator.init_db(engine, auto_migrate=True)

        yield sync_db_url

        await engine.dispose()
        conn = await asyncpg.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database="postgres",
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        await conn.close()

    @pytest_asyncio.fixture
    async def db_via_alembic(self, pg_params):
        """通过 Alembic upgrade head 初始化的隔离数据库 (路径 B)。"""
        params = pg_params
        db_name = f"schema_alembic_{uuid.uuid4().hex[:8]}"

        conn = await asyncpg.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database="postgres",
        )
        await conn.execute(f'CREATE DATABASE "{db_name}"')
        await conn.close()

        sync_db_url, _ = build_db_urls(params, db_name)

        # 路径 B: 直接使用 Alembic upgrade
        with override_db_url(sync_db_url):
            cfg = make_alembic_cfg(sync_db_url)
            await asyncio.to_thread(command.upgrade, cfg, "head")

        yield sync_db_url

        conn = await asyncpg.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database="postgres",
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        await conn.close()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_schemas_are_identical(self, db_via_init_db, db_via_alembic):
        """两种初始化路径应产生完全相同的表结构和列定义。"""
        schema_a = await asyncio.to_thread(_reflect_schema, db_via_init_db)
        schema_b = await asyncio.to_thread(_reflect_schema, db_via_alembic)

        # 比较表集合
        tables_a = set(schema_a.keys())
        tables_b = set(schema_b.keys())
        assert tables_a == tables_b, (
            f"表集合不一致:\n仅路径A有: {tables_a - tables_b}\n仅路径B有: {tables_b - tables_a}"
        )

        # 逐表比较列定义
        for table_name in sorted(tables_a):
            cols_a = schema_a[table_name]
            cols_b = schema_b[table_name]

            # 比较列名集合
            col_names_a = set(cols_a.keys())
            col_names_b = set(cols_b.keys())
            assert col_names_a == col_names_b, (
                f"表 {table_name} 列名不一致:\n仅路径A有: {col_names_a - col_names_b}"
                f"\n仅路径B有: {col_names_b - col_names_a}"
            )

            # 比较每列的类型
            for col_name in sorted(col_names_a):
                type_a = cols_a[col_name]
                type_b = cols_b[col_name]
                assert type_a == type_b, f"表 {table_name} 列 {col_name} 类型不一致:\n路径A: {type_a}\n路径B: {type_b}"
