"""Agent 正文/谱系边成对约束的库级契约（R2-I32）。

真实 embedded PG 上验证两条 CHECK 由数据库强制（不依赖 DAO 自觉）：

- ``ck_agent_body_payload_owner_identity``：正文 owner 三元组必须与恰被选中的
  owner 外键列成对一致（message 版本固定 ``1``；artifact 版本按规范十进制字符串；
  asset 版本逐字相等）；
- ``ck_agent_source_link_derived_asset_pair``：派生来源 id/version 必须成对声明。

迁移 0047 以 ``NOT VALID`` 补建约束（存量行不因历史不一致而阻断升级），新写入
立即受检；读取侧另由 ``AgentDao._read_body_text`` 核 owner 归属（单元测试覆盖）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentAssetVersion, AgentBodyPayload, AgentSession, AgentSourceLink
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER = "scope-1"
_ASSET_ID = "src-1"
_ASSET_VERSION = "1"


async def _reset_schema(url: str) -> None:
    engine = create_test_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


async def _setup(url: str) -> tuple[AsyncEngine, AsyncConnection, AsyncTransaction]:
    await _reset_schema(url)
    cfg = make_alembic_cfg(url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    engine = create_test_engine(url)
    conn = await engine.connect()
    trans = await conn.begin()
    await conn.execute(insert(AgentSession).values(user_scope=_USER, session_id="s1", title="研究会话"))
    await conn.execute(
        insert(AgentAssetVersion).values(
            user_scope=_USER,
            asset_id=_ASSET_ID,
            asset_version=_ASSET_VERSION,
            asset_kind="source",
            display_name="来源",
            digest="a" * 64,
            read_state="ready",
            verification_state="program_verified",
        )
    )
    return engine, conn, trans


def _payload_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "user_scope": _USER,
        "body_ref": "body-1",
        "owner_kind": "asset",
        "owner_id": _ASSET_ID,
        "owner_version": _ASSET_VERSION,
        "asset_id": _ASSET_ID,
        "asset_version": _ASSET_VERSION,
        "encoding": "utf-8",
        "payload_bytes": b"body",
        "byte_length": 4,
        "payload_digest": "b" * 64,
    }
    values.update(overrides)
    return values


def _link_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "user_scope": _USER,
        "link_id": "link-1",
        "source_id": _ASSET_ID,
        "source_version": _ASSET_VERSION,
        "consumer_kind": "draft",
        "consumer_id": "draft-1",
        "consumer_version": "1",
        "session_id": "s1",
        "relation_kind": "origin",
    }
    values.update(overrides)
    return values


class TestBodyPayloadOwnerIdentity:
    @pytest.mark.asyncio
    async def test_consistent_owner_rows_are_accepted(self, real_embedded_pg: ConnectionInfo) -> None:
        engine, conn, trans = await _setup(real_embedded_pg.url)
        try:
            await conn.execute(insert(AgentBodyPayload).values(_payload_values()))
            result = await conn.execute(sa.select(sa.func.count()).select_from(AgentBodyPayload))
            assert result.scalar_one() == 1
        finally:
            await trans.rollback()
            await conn.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_owner_id_mismatching_fk_column_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        engine, conn, trans = await _setup(real_embedded_pg.url)
        try:
            with pytest.raises(IntegrityError, match="ck_agent_body_payload_owner_identity"):
                await conn.execute(insert(AgentBodyPayload).values(_payload_values(owner_id="src-other")))
        finally:
            await trans.rollback()
            await conn.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_owner_version_mismatching_asset_version_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        engine, conn, trans = await _setup(real_embedded_pg.url)
        try:
            with pytest.raises(IntegrityError, match="ck_agent_body_payload_owner_identity"):
                await conn.execute(insert(AgentBodyPayload).values(_payload_values(owner_version="2")))
        finally:
            await trans.rollback()
            await conn.close()
            await engine.dispose()


class TestSourceLinkDerivedAssetPair:
    @pytest.mark.asyncio
    async def test_single_sided_derived_version_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        engine, conn, trans = await _setup(real_embedded_pg.url)
        try:
            with pytest.raises(IntegrityError, match="ck_agent_source_link_derived_asset_pair"):
                await conn.execute(insert(AgentSourceLink).values(_link_values(derived_asset_version=_ASSET_VERSION)))
        finally:
            await trans.rollback()
            await conn.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_paired_derived_asset_is_accepted(self, real_embedded_pg: ConnectionInfo) -> None:
        engine, conn, trans = await _setup(real_embedded_pg.url)
        try:
            await conn.execute(
                insert(AgentSourceLink).values(
                    _link_values(
                        link_id="link-derived",
                        consumer_kind="asset",
                        consumer_id=_ASSET_ID,
                        consumer_version=_ASSET_VERSION,
                        session_id=None,
                        derived_asset_id=_ASSET_ID,
                        derived_asset_version=_ASSET_VERSION,
                    )
                )
            )
            result = await conn.execute(sa.select(sa.func.count()).select_from(AgentSourceLink))
            assert result.scalar_one() == 1
        finally:
            await trans.rollback()
            await conn.close()
            await engine.dispose()
