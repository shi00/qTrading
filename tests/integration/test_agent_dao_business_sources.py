"""E-01-3 业务来源登记集成测试（真实 embedded PG，单事务 asset + link）。

覆盖：同事务落 asset/link、幂等重放零新事实、异输入冲突零新事实、
未选来源零行、PIT 拒绝零行、available_at NULL 语义。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    BusinessSourceItem,
    BusinessSourceRegistrationRequest,
    Purpose,
    ReadState,
    Selection,
    SourceKind,
    SourceLocator,
    SourceRef,
    SourceRegistrationRequest,
    SourceVersionRef,
    VerificationState,
    sha256_hex,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentAssetVersion, AgentExecution, AgentSession, AgentSourceLink
from services.agent_service.sources import BusinessSourceService
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

USER_SCOPE = "scope-1"
SCOPE_DIGEST = sha256_hex(b"e01-integration")


async def _reset_schema(url: str) -> None:
    engine = create_test_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


async def _setup(url: str) -> AgentDao:
    await _reset_schema(url)
    cfg = make_alembic_cfg(url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    return AgentDao(create_test_engine(url))


async def _seed_execution(dao: AgentDao, *, session_id: str = "sess-1", execution_id: str = "exec-1") -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造消费者前置事实
        await conn.execute(insert(AgentSession).values(user_scope=USER_SCOPE, session_id=session_id, title="研究会话"))
        await conn.execute(
            insert(AgentExecution).values(
                user_scope=USER_SCOPE,
                execution_id=execution_id,
                session_id=session_id,
                execution_kind="root",
                source_module="ui",
                business_request_id="req-1",
                intent_digest=SCOPE_DIGEST,
                node_id="root",
                display_state="running",
                goal_coverage="unknown",
            )
        )


def _source(
    source_id: str = "src-1",
    version: str = "1",
    *,
    digest: str = SCOPE_DIGEST,
    available_at: str | None = "2025-12-31T00:00:00.000000Z",
    selection: Selection | None = None,
) -> SourceRef:
    return SourceRef(
        user_scope=USER_SCOPE,
        source_id=source_id,
        version=version,
        digest=digest,
        source_kind=SourceKind.ATTACHMENT,
        display_name=f"{source_id}.xlsx",
        object_ref="600000.SH",
        locator=SourceLocator(
            locator_kind="source_range",
            origin_ref=SourceVersionRef(
                source_kind=SourceKind.ATTACHMENT,
                source_id=source_id,
                source_version=version,
                source_digest=digest,
            ),
            selection=selection or Selection(kind="all"),
        ),
        available_at=available_at,
        period="2025",
        unit_note="金额单位：元",
        read_state=ReadState.READY,
        missing_reason=None,
        provenance_refs=(),
    )


def _item(source: SourceRef | None = None) -> BusinessSourceItem:
    return BusinessSourceItem(source=source or _source(), verification=VerificationState.UNVERIFIED)


def _dao_request(*items: BusinessSourceItem, consumer_kind: str = "execution", consumer_id: str = "exec-1"):
    return BusinessSourceRegistrationRequest(
        user_scope=USER_SCOPE,
        consumer_kind=consumer_kind,
        consumer_id=consumer_id,
        consumer_version="1",
        items=items or (_item(),),
    )


def _service_request(
    *items: BusinessSourceItem,
    purpose: Purpose = Purpose.END_OF_DAY_RESEARCH,
    cutoff: str | None = "2026-01-01T00:00:00.000000Z",
    unselected: tuple[BusinessSourceItem, ...] = (),
):
    return SourceRegistrationRequest(
        user_scope=USER_SCOPE,
        consumer_kind="execution",
        consumer_id="exec-1",
        consumer_version="1",
        purpose=purpose,
        decision_cutoff=cutoff,
        selected=items or (_item(),),
        unselected=unselected,
    )


async def _count(dao: AgentDao, model: type) -> int:
    async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试断言读取
        result = await conn.execute(sa.select(sa.func.count()).select_from(model))
    return int(result.scalar_one())


async def _source_ids(dao: AgentDao) -> list[str]:
    async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试断言读取
        result = await conn.execute(sa.select(AgentAssetVersion.asset_id).order_by(AgentAssetVersion.asset_id))
    return [row[0] for row in result]


class TestBusinessSourceRegistration:
    @pytest.mark.asyncio
    async def test_registers_asset_and_link_in_one_transaction(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        receipt = await dao.register_business_sources(_dao_request(_item(_source("s1")), _item(_source("s2"))))
        assert [ref.source_id for ref in receipt.registered] == ["s1", "s2"]
        assert receipt.replayed is False
        assert await _count(dao, AgentAssetVersion) == 2
        assert await _count(dao, AgentSourceLink) == 2
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试断言读取
            link = (
                await conn.execute(
                    sa.select(AgentSourceLink.relation_kind, AgentSourceLink.execution_id).where(
                        AgentSourceLink.source_id == "s1"
                    )
                )
            ).one()
            asset = (
                await conn.execute(
                    sa.select(
                        AgentAssetVersion.asset_kind,
                        AgentAssetVersion.read_state,
                        AgentAssetVersion.verification_state,
                        AgentAssetVersion.available_at,
                    ).where(AgentAssetVersion.asset_id == "s1")
                )
            ).one()
        assert link == ("accepted_input", "exec-1")
        assert asset[0] == "source"
        assert asset[1] == "ready"
        assert asset[2] == "unverified"
        assert asset[3] is not None

    @pytest.mark.asyncio
    async def test_replay_same_input_returns_original_with_zero_new_facts(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        request = _dao_request()
        first = await dao.register_business_sources(request)
        second = await dao.register_business_sources(request)
        assert second.replayed is True
        assert second.registered == first.registered
        assert second.link_ids == first.link_ids
        assert await _count(dao, AgentAssetVersion) == 1
        assert await _count(dao, AgentSourceLink) == 1

    @pytest.mark.asyncio
    async def test_same_source_version_different_digest_conflicts_with_zero_new_facts(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        await dao.register_business_sources(_dao_request())
        with pytest.raises(AgentServiceError) as exc:
            await dao.register_business_sources(_dao_request(_item(_source(digest=sha256_hex(b"different")))))
        assert exc.value.code is AgentErrorCode.INPUT_CONFLICT
        assert await _count(dao, AgentAssetVersion) == 1
        assert await _count(dao, AgentSourceLink) == 1

    @pytest.mark.asyncio
    async def test_unselected_sources_are_never_written(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        service = BusinessSourceService(dao)
        request = _service_request(
            _item(_source("selected-1")),
            unselected=(_item(_source("unselected-1")),),
        )
        await service.register(request)
        assert await _source_ids(dao) == ["selected-1"]
        assert await _count(dao, AgentSourceLink) == 1

    @pytest.mark.asyncio
    async def test_pit_rejection_writes_nothing(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        service = BusinessSourceService(dao)
        request = _service_request(
            _item(_source(available_at="2026-01-02T00:00:00.000000Z")),
            purpose=Purpose.HISTORICAL_DECISION,
            cutoff="2026-01-01T00:00:00.000000Z",
        )
        with pytest.raises(AgentServiceError) as exc:
            await service.register(request)
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert await _count(dao, AgentAssetVersion) == 0
        assert await _count(dao, AgentSourceLink) == 0

    @pytest.mark.asyncio
    async def test_end_of_day_research_rejects_unknown_available_at_with_zero_rows(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        with pytest.raises(AgentServiceError) as exc:
            await BusinessSourceService(dao).register(_service_request(_item(_source(available_at=None))))
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert await _count(dao, AgentAssetVersion) == 0
        assert await _count(dao, AgentSourceLink) == 0

    @pytest.mark.asyncio
    async def test_concept_purpose_keeps_null_available_at(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        await BusinessSourceService(dao).register(
            _service_request(_item(_source(available_at=None)), purpose=Purpose.CONCEPT, cutoff=None)
        )
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试断言读取
            available_at = (
                await conn.execute(
                    sa.select(AgentAssetVersion.available_at).where(AgentAssetVersion.asset_id == "src-1")
                )
            ).scalar_one()
        assert available_at is None

    @pytest.mark.asyncio
    async def test_duplicate_and_scope_mismatch_rejected_with_zero_rows(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        with pytest.raises(AgentServiceError) as exc_dup:
            await dao.register_business_sources(_dao_request(_item(), _item()))
        assert exc_dup.value.code is AgentErrorCode.INPUT_INVALID
        other_scope = replace(_source(), user_scope="other-scope")
        with pytest.raises(AgentServiceError) as exc_scope:
            await dao.register_business_sources(_dao_request(_item(other_scope)))
        assert exc_scope.value.code is AgentErrorCode.INPUT_INVALID
        assert await _count(dao, AgentAssetVersion) == 0
        assert await _count(dao, AgentSourceLink) == 0


async def _asset_body(dao: AgentDao, asset_id: str = "src-1") -> str | None:
    async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试断言读取
        result = await conn.execute(
            sa.select(AgentAssetVersion.body_text).where(AgentAssetVersion.asset_id == asset_id)
        )
    return result.scalar_one()


class TestBusinessSourceBody:
    """I75：受管正文随源版内联登记（读源可直接取到，不退化为元数据摘要）。"""

    @pytest.mark.asyncio
    async def test_body_is_registered_inline_and_replays_idempotently(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        item = BusinessSourceItem(
            source=_source(),
            verification=VerificationState.PROGRAM_VERIFIED,
            body_text="## 指标\n- 夏普比率：未提供\n",
        )
        first = await dao.register_business_sources(_dao_request(item))
        assert first.replayed is False
        assert await _asset_body(dao) == "## 指标\n- 夏普比率：未提供\n"
        second = await dao.register_business_sources(_dao_request(item))
        assert second.replayed is True
        assert await _asset_body(dao) == "## 指标\n- 夏普比率：未提供\n"

    @pytest.mark.asyncio
    async def test_first_prepared_body_is_frozen_on_replay(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        await dao.register_business_sources(
            _dao_request(BusinessSourceItem(source=_source(), verification=VerificationState.UNVERIFIED, body_text="A"))
        )
        receipt = await dao.register_business_sources(
            _dao_request(BusinessSourceItem(source=_source(), verification=VerificationState.UNVERIFIED, body_text="B"))
        )
        # 资产版本为冻结快照：既有正文不被重放改写（也不因表示差异阻断登记）。
        assert receipt.replayed is True
        assert await _asset_body(dao) == "A"

    @pytest.mark.asyncio
    async def test_existing_version_without_body_gets_backfilled(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        await dao.register_business_sources(_dao_request())
        assert await _asset_body(dao) is None
        receipt = await dao.register_business_sources(
            _dao_request(
                BusinessSourceItem(source=_source(), verification=VerificationState.UNVERIFIED, body_text="正文")
            )
        )
        # 补写正文是一次写入，不冒充「零新事实」的重放；版本身份/行数不变。
        assert receipt.replayed is False
        assert await _asset_body(dao) == "正文"
        assert await _count(dao, AgentAssetVersion) == 1
