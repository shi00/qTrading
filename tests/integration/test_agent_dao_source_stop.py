"""AgentDao C-04-3 源禁用/共同仲裁/消费者列举集成测试（§3.3.2/§5.4/§9.10/§9.13）。

真实 embedded PG 上的事务语义验证：

- ``stop_source_use``：source/version 行锁、disabled_revision GREATEST 落定、
  read_state='disabled'、已知派生闭包（递归 consumer_kind='asset' 链）整体标记
  disabled_inherited、已禁用重放零新事实、来源不存在 INPUT_INVALID；
- ``claim_source_versions``：稳定 ID 排序锁源 + 新鲜重读禁用状态（预检查/旧
  快照不是派发权）、绑定 revision 认领持久化到 operation.data_consent_claims
  （kind=source_revision）、同 claim_ref 重放/异输入冲突、禁用先胜
  SOURCE_DISABLED、claim 先胜保原认领（重放不被后续禁用改写）；
- ``list_source_consumers``：已知边全量返回、来源不存在 INPUT_INVALID。

依赖 ``real_embedded_pg`` fixture（临时目录隔离，符合隔离本地验证授权）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    BusinessSourceItem,
    BusinessSourceRegistrationRequest,
    ReadState,
    Selection,
    SourceClaimRequest,
    SourceKind,
    SourceLocator,
    SourceReadQuery,
    SourceRef,
    SourceStopCommand,
    SourceVersionRef,
    VerificationState,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import (
    AgentAssetVersion,
    AgentExecution,
    AgentOperation,
    AgentRound,
    AgentSession,
    AgentSourceLink,
)
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]


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


async def _seed_session(dao: AgentDao, user_scope: str, session_id: str) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造前置会话
        await conn.execute(
            insert(AgentSession).values(user_scope=user_scope, session_id=session_id, title=f"session {session_id}")
        )


async def _seed_execution(
    dao: AgentDao,
    user_scope: str,
    session_id: str,
    execution_id: str,
    kind: str,
    node_id: str,
    *,
    parent_execution_id: str | None = None,
) -> None:
    values: dict = {
        "user_scope": user_scope,
        "execution_id": execution_id,
        "session_id": session_id,
        "execution_kind": kind,
        "node_id": node_id,
        "display_state": "queued",
        "goal_coverage": "unknown",
        "parent_execution_id": parent_execution_id,
    }
    if kind in ("root", "comparison_lane"):
        values["source_module"] = "ui"
        values["business_request_id"] = f"req-{execution_id}"
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造前置执行
        await conn.execute(insert(AgentExecution).values(**values))


async def _seed_source(
    dao: AgentDao,
    user_scope: str,
    asset_id: str,
    asset_version: str = "1",
) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造受管源版本
        await conn.execute(
            insert(AgentAssetVersion).values(
                user_scope=user_scope,
                asset_id=asset_id,
                asset_version=asset_version,
                asset_kind="source",
                display_name=f"source {asset_id}@{asset_version}",
                digest="b" * 64,
                # 认领身份四值核对需要显式类别事实（与 _ref 的 user_message 一致）。
                metadata_payload={
                    "schema_version": 1,
                    "user_message_origin": {
                        "session_id": "s1",
                        "message_id": f"msg-{asset_id}",
                        "message_version": asset_version,
                    },
                },
                read_state="ready",
                verification_state="unverified",
            )
        )


async def _seed_link(
    dao: AgentDao,
    user_scope: str,
    link_id: str,
    source_id: str,
    source_version: str,
    *,
    consumer_kind: str,
    execution_id: str | None = None,
    derived_asset_id: str | None = None,
    derived_asset_version: str | None = None,
    relation_kind: str = "user_message_origin",
) -> None:
    values: dict = {
        "user_scope": user_scope,
        "link_id": link_id,
        "source_id": source_id,
        "source_version": source_version,
        "consumer_kind": consumer_kind,
        "consumer_id": execution_id if consumer_kind == "execution" else (derived_asset_id or ""),
        "consumer_version": "1",
        "relation_kind": relation_kind,
    }
    if consumer_kind == "execution":
        values["execution_id"] = execution_id
    if consumer_kind == "asset":
        values["derived_asset_id"] = derived_asset_id
        values["derived_asset_version"] = derived_asset_version
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造谱系边
        await conn.execute(insert(AgentSourceLink).values(**values))


async def _seed_operation(dao: AgentDao, user_scope: str, operation_id: str) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造前置 operation
        await conn.execute(
            insert(AgentOperation).values(
                user_scope=user_scope,
                operation_id=operation_id,
                owner_epoch=1,
                run_epoch=1,
                phase="prepared",
                effect_state="not_dispatched",
                io_state="unknown",
            )
        )


def _expect_error(excinfo: pytest.ExceptionInfo[AgentServiceError], code: AgentErrorCode) -> None:
    err = excinfo.value
    assert isinstance(err, AgentServiceError)
    assert err.code == code, f"expect {code.value}, got {err.code.value}: {err.sanitized_message}"


def _stop(user_scope: str = "u1", source_id: str = "src-1", command_id: str | None = None) -> SourceStopCommand:
    return SourceStopCommand(
        user_scope=user_scope,
        source_id=source_id,
        source_version="1",
        command_id=command_id or f"cmd-{uuid.uuid4().hex[:8]}",
    )


def _ref(source_id: str, source_version: str = "1") -> SourceVersionRef:
    return SourceVersionRef(
        source_id=source_id, source_version=source_version, source_kind=SourceKind.USER_MESSAGE, source_digest="b" * 64
    )


def _claim(
    claim_ref: str,
    sources: tuple[SourceVersionRef, ...],
    user_scope: str = "u1",
    operation_id: str = "op-1",
) -> SourceClaimRequest:
    return SourceClaimRequest(user_scope=user_scope, claim_ref=claim_ref, operation_id=operation_id, sources=sources)


async def _seed_graph(dao: AgentDao) -> None:
    """标准谱系图：src-1 ←（直接执行消费 e1）；src-1 →（asset 派生）d-1；
    d-1 ←（直接执行消费 e2）。禁用 src-1 应标记 L1/L2/L3 全部 disabled_inherited。"""

    await _seed_session(dao, "u1", "s1")
    await _seed_execution(dao, "u1", "s1", "e1", "root", "root")
    await _seed_execution(dao, "u1", "s1", "e2", "root", "root")
    await _seed_source(dao, "u1", "src-1")
    await _seed_source(dao, "u1", "d-1")
    await _seed_link(dao, "u1", "link-1", "src-1", "1", consumer_kind="execution", execution_id="e1")
    await _seed_link(
        dao,
        "u1",
        "link-2",
        "src-1",
        "1",
        consumer_kind="asset",
        derived_asset_id="d-1",
        derived_asset_version="1",
        relation_kind="derived_asset",
    )
    await _seed_link(
        dao, "u1", "link-3", "d-1", "1", consumer_kind="execution", execution_id="e2", relation_kind="derived_asset"
    )


# ------------------------------------------------------------------
# stop_source_use
# ------------------------------------------------------------------


class TestStopSourceUse:
    async def test_stop_disables_source_and_known_closure(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_graph(dao)

        receipt = await dao.stop_source_use(_stop(source_id="src-1"))
        assert receipt.source_id == "src-1"
        assert receipt.already_disabled is False
        assert receipt.disabled_revision == 2  # 禁用行 revision 推进后落定（GREATEST 旧值 0, 新值 2）
        assert receipt.disabled_at is not None

        engine = create_test_engine(real_embedded_pg.url)
        async with engine.connect() as conn:
            asset = (
                await conn.execute(
                    text(
                        "SELECT read_state, disabled_revision, revision, disabled_at FROM agent_asset_version "
                        "WHERE user_scope = 'u1' AND asset_id = 'src-1' AND asset_version = '1'"
                    )
                )
            ).first()
            assert asset is not None
            assert asset[0] == "disabled"
            assert asset[1] == 2
            assert asset[2] == 2  # 行 revision 单调 +1；disabled_revision 与落定 revision 一致
            assert asset[3] is not None
            flags = (
                await conn.execute(
                    text(
                        "SELECT link_id, disabled_inherited FROM agent_source_link WHERE user_scope = 'u1' ORDER BY link_id"
                    )
                )
            ).all()
            assert {row[0]: row[1] for row in flags} == {"link-1": True, "link-2": True, "link-3": True}
            # 派生资产本体不被禁用（阻断经闭包边 disabled_inherited 承载）
            derived = (
                await conn.execute(
                    text(
                        "SELECT disabled_revision FROM agent_asset_version WHERE user_scope = 'u1' AND asset_id = 'd-1'"
                    )
                )
            ).scalar_one()
            assert derived == 0
        await engine.dispose()

    async def test_stop_replay_returns_original_fact(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_graph(dao)
        first = await dao.stop_source_use(_stop(source_id="src-1", command_id="cmd-1"))
        replay = await dao.stop_source_use(_stop(source_id="src-1", command_id="cmd-2"))
        assert replay.already_disabled is True
        assert replay.disabled_revision == first.disabled_revision

        engine = create_test_engine(real_embedded_pg.url)
        async with engine.connect() as conn:
            revision = (
                await conn.execute(
                    text("SELECT revision FROM agent_asset_version WHERE user_scope = 'u1' AND asset_id = 'src-1'")
                )
            ).scalar_one()
            assert revision == 2  # 重放零新事实（revision 不再推进）
        await engine.dispose()

    async def test_stop_unknown_source_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.stop_source_use(_stop(source_id="missing"))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)


# ------------------------------------------------------------------
# list_source_consumers
# ------------------------------------------------------------------


class TestListSourceConsumers:
    async def test_lists_known_edges(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_graph(dao)
        page = await dao.list_source_consumers(user_scope="u1", source_id="src-1", source_version="1")
        assert page.source_id == "src-1"
        assert [c.link_id for c in page.consumers] == ["link-1", "link-2"]
        assert all(c.disabled_inherited is False for c in page.consumers)

        # 禁用后列举反映 disabled_inherited
        await dao.stop_source_use(_stop(source_id="src-1"))
        after = await dao.list_source_consumers(user_scope="u1", source_id="src-1", source_version="1")
        assert all(c.disabled_inherited is True for c in after.consumers)

    async def test_unknown_source_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.list_source_consumers(user_scope="u1", source_id="missing", source_version="1")
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)


# ------------------------------------------------------------------
# claim_source_versions（共同仲裁单胜者）
# ------------------------------------------------------------------


class TestClaimSourceVersions:
    async def test_claim_binds_revisions_and_persists(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_graph(dao)
        await _seed_operation(dao, "u1", "op-1")

        fact = await dao.claim_source_versions(_claim("claim-1", (_ref("src-1"), _ref("d-1"))))
        assert fact.replayed is False
        assert [b.source_id for b in fact.bindings] == ["d-1", "src-1"]  # 绑定按锁定序回传
        assert all(b.disabled_revision == 0 for b in fact.bindings)

        engine = create_test_engine(real_embedded_pg.url)
        async with engine.connect() as conn:
            claims = (
                await conn.execute(
                    text(
                        "SELECT data_consent_claims FROM agent_operation WHERE user_scope = 'u1' AND operation_id = 'op-1'"
                    )
                )
            ).scalar_one()
            assert len(claims) == 1
            entry = claims[0]
            assert entry["kind"] == "source_revision"
            assert entry["claim_ref"] == "claim-1"
            assert {s["source_id"] for s in entry["sources"]} == {"src-1", "d-1"}
        await engine.dispose()

    async def test_claim_replay_and_conflict(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_graph(dao)
        await _seed_operation(dao, "u1", "op-1")
        first = await dao.claim_source_versions(_claim("claim-1", (_ref("src-1"),)))

        replay = await dao.claim_source_versions(_claim("claim-1", (_ref("src-1"),)))
        assert replay.replayed is True
        assert replay.bindings == first.bindings

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_source_versions(_claim("claim-1", (_ref("d-1"),)))
        _expect_error(excinfo, AgentErrorCode.INPUT_CONFLICT)

    async def test_claim_after_disable_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_graph(dao)
        await _seed_operation(dao, "u1", "op-1")
        await dao.stop_source_use(_stop(source_id="src-1"))

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_source_versions(_claim("claim-1", (_ref("src-1"),)))
        _expect_error(excinfo, AgentErrorCode.SOURCE_DISABLED)
        assert "src-1@1" in excinfo.value.sanitized_message

    async def test_claim_derived_blocked_by_ancestor_disable(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_graph(dao)
        await _seed_operation(dao, "u1", "op-1")
        await dao.stop_source_use(_stop(source_id="src-1"))

        # 派生资产本体未禁用，但作为已知派生闭包成员被 disabled_inherited 覆盖
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_source_versions(_claim("claim-d", (_ref("d-1"),)))
        _expect_error(excinfo, AgentErrorCode.SOURCE_DISABLED)

    async def test_claim_wins_then_disable_keeps_claim(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_graph(dao)
        await _seed_operation(dao, "u1", "op-1")
        won = await dao.claim_source_versions(_claim("claim-w", (_ref("src-1"),)))
        assert won.replayed is False

        # claim 先胜：禁用只阻后续 claim，原认领重放返回原事实（在途保真）
        await dao.stop_source_use(_stop(source_id="src-1"))
        replay = await dao.claim_source_versions(_claim("claim-w", (_ref("src-1"),)))
        assert replay.replayed is True
        assert replay.bindings == won.bindings

        # 新 claim_ref 在禁用后被拒（禁用先胜零受影响新出站）
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_source_versions(_claim("claim-new", (_ref("src-1"),)))
        _expect_error(excinfo, AgentErrorCode.SOURCE_DISABLED)

    async def test_claim_unknown_operation_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_graph(dao)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_source_versions(_claim("claim-1", (_ref("src-1"),), operation_id="missing"))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

    async def test_claim_blocks_on_inbound_edge_lock(self, real_embedded_pg: ConnectionInfo) -> None:
        """检视闭环 M-1 回归锚：claim 须先取源直接入边行锁（与 stop 闭包 UPDATE
        冲突序列化）。外部连接持 link-2（src-1→d-1 派生边）行锁期间，claim(d-1)
        必须被阻塞；锁释放后才能完成。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_graph(dao)
        await _seed_operation(dao, "u1", "op-1")

        blocker = create_test_engine(real_embedded_pg.url)
        conn2 = await blocker.connect()
        try:
            await conn2.execute(text("SELECT link_id FROM agent_source_link WHERE link_id = 'link-2' FOR UPDATE"))
            task = asyncio.create_task(dao.claim_source_versions(_claim("claim-m1", (_ref("d-1"),))))
            await asyncio.sleep(1.0)
            assert not task.done()  # 入边行锁未被释放，claim 必须阻塞（冲突序列化生效）
            await conn2.rollback()  # 释放行锁
            fact = await asyncio.wait_for(task, timeout=5.0)
            assert fact.replayed is False
            assert [b.source_id for b in fact.bindings] == ["d-1"]
        finally:
            await conn2.close()
            await blocker.dispose()

    async def test_claim_unknown_source_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_graph(dao)
        await _seed_operation(dao, "u1", "op-1")
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_source_versions(_claim("claim-1", (_ref("missing"),)))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

    async def test_claim_empty_sources_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_operation(dao, "u1", "op-1")
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_source_versions(_claim("claim-1", ()))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)


# ------------------------------------------------------------------
# 检视 D11：stop 只改当前投影，冻结事实历史可读
# ------------------------------------------------------------------


def _business_ref() -> SourceRef:
    digest = "c" * 64
    return SourceRef(
        user_scope="u1",
        source_id="src-1",
        version="1",
        digest=digest,
        source_kind=SourceKind.ATTACHMENT,
        display_name="附件.pdf",
        object_ref=None,
        locator=SourceLocator(
            locator_kind="source_range",
            origin_ref=SourceVersionRef(
                source_kind=SourceKind.ATTACHMENT,
                source_id="src-1",
                source_version="1",
                source_digest=digest,
            ),
            selection=Selection(kind="all"),
        ),
        available_at="2026-11-01T00:00:00.000000Z",
        period=None,
        unit_note=None,
        read_state=ReadState.READY,
        missing_reason=None,
        provenance_refs=(),
    )


async def _seed_round(dao: AgentDao, *, execution_id: str = "e1", round_id: str = "round-1") -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造读取前置轮次
        await conn.execute(
            insert(AgentRound).values(
                user_scope="u1",
                round_id=round_id,
                execution_id=execution_id,
                round_number=1,
                workflow_id=f"wf-{round_id}",
                workflow_code_version="wcv-1",
                run_epoch=1,
                input_revision=1,
                plan_revision=1,
                goal_coverage="unknown",
                final_saved=False,
                convergence_confirmed=False,
                active_round=True,
            )
        )


class TestStopKeepsFrozenSourceFacts:
    """真实登记 → 禁用 → 读取：历史返回冻结事实，当前状态显式 stopped。"""

    async def test_read_after_stop_returns_frozen_facts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "e1", "root", "root")
        await _seed_round(dao)
        ref = _business_ref()
        await dao.register_business_sources(
            BusinessSourceRegistrationRequest(
                user_scope="u1",
                consumer_kind="execution",
                consumer_id="e1",
                consumer_version="1",
                items=(BusinessSourceItem(source=ref, verification=VerificationState.PROGRAM_VERIFIED),),
            )
        )

        receipt = await dao.stop_source_use(_stop())
        assert receipt.already_disabled is False

        read = await dao.read_source(
            SourceReadQuery(
                source_id="src-1",
                version="1",
                digest=ref.digest,
                locator=ref.locator,
                draft_revision=None,
                execution_id="e1",
                round_id="round-1",
                input_revision=1,
            ),
            user_scope="u1",
        )

        # 冻结事实（版本/摘要/类别/定位/核验）原样返回，不被 stop 改写
        assert read.source == ref
        assert read.verification is VerificationState.PROGRAM_VERIFIED
        # 当前投影显式标注停止；不是坏元数据
        assert read.read_state is ReadState.DISABLED
        assert read.available is False
        assert read.missing_reason == "source_disabled"

        # 停止后新研究仍被拒绝（既有判定不回归）
        await _seed_operation(dao, "u1", "op-1")
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_source_versions(_claim("claim-after-stop", (_ref("src-1"),)))
        _expect_error(excinfo, AgentErrorCode.SOURCE_DISABLED)
        await dao.engine.dispose()  # type: ignore[union-attr]
