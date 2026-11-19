"""AgentDao 注册与守卫契约测试（任务 C-01-3，设计正本 §9.13）。

- R13：AgentDao 在 CacheManager.__init__ 显式实例化（self.agent_dao = AgentDao(self.engine)）；
- R5：引擎未初始化/已释放时入口守卫传播（未初始化 RuntimeError / disposed
  EngineDisposedError），不得返回空成功；
- §9.13 C-01 配套严格读：BacktestDao.get_result 支持 ``suppress_errors``
  透传（默认 True 保持旧调用行为不变；显式 False 时数据库失败分类传播，
  不能据 None 构造「报告不存在」）。

AgentDao 事务方法族随 C-02/C-03/C-04 实现，本文件只锁注册与守卫基线。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import AgentErrorCode, AgentServiceError, SourceKind, SourceVersionRef, json_doc
from data.cache.cache_manager import CacheManager
from data.persistence import engine_provider
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.daos.backtest_dao import BacktestDAO
from data.persistence.daos.base_dao import DatabaseQueryError, EngineDisposedError


def _failing_engine() -> MagicMock:
    """engine.connect() 即刻失败的假引擎（读路径错误分类触发 suppress 分支）。"""

    engine = MagicMock(spec=AsyncEngine)
    engine.connect = MagicMock(side_effect=RuntimeError("connection boom"))
    return engine


def _row_engine(row: tuple, columns: list[str]) -> MagicMock:
    """返回单行结果的假引擎（SQLAlchemy conn.execute 风格）。"""

    proxy = MagicMock()
    proxy.fetchall.return_value = [row]
    proxy.keys.return_value = columns
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=proxy)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock(spec=AsyncEngine)
    engine.connect = MagicMock(return_value=cm)
    return engine


class TestAgentDaoRegistration:
    """R13：AgentDao 注册面。"""

    def test_cache_manager_registers_agent_dao(self):
        # _reset_all_singletons autouse 保证全新实例（R7）
        mgr = CacheManager()
        assert isinstance(mgr.agent_dao, AgentDao)
        assert mgr.agent_dao.engine is mgr.engine

    def test_agent_dao_inherits_base_dao_guards(self):
        dao = AgentDao(None)
        assert hasattr(dao, "_check_engine")
        assert hasattr(dao, "_read_db_select")
        assert hasattr(dao, "_save_upsert")


class TestAgentDaoEngineGuard:
    """R5：入口守卫传播，不返回空成功。"""

    @pytest.mark.asyncio
    async def test_uninitialized_engine_rejected(self):
        dao = AgentDao(None)
        with pytest.raises(RuntimeError, match="Engine not initialized") as excinfo:
            await dao._read_db_select(sa.select(sa.literal(1)))
        assert "AgentDao" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_disposed_engine_rejected(self):
        engine = MagicMock(spec=AsyncEngine)
        engine_provider.set_engine(engine)
        engine_provider.mark_disposed(True)
        dao = AgentDao(engine)
        with pytest.raises(EngineDisposedError, match="Engine disposed") as excinfo:
            await dao._read_db_select(sa.select(sa.literal(1)))
        assert "AgentDao" in str(excinfo.value)


def _batch_engine(marker_row: object | None) -> tuple[MagicMock, MagicMock]:
    """engine.begin() 风格的假引擎：select 返回指定 marker 行，记录 conn 调用。"""

    proxy = MagicMock()
    proxy.first.return_value = marker_row
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=proxy)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock(spec=AsyncEngine)
    engine.begin = MagicMock(return_value=cm)
    return engine, conn


class TestAgentDaoDecisionBatchEngineGuard:
    """R5（检视 D25）：决定批次入口在引擎不可用时零 DB 访问即拒绝。"""

    @pytest.mark.asyncio
    async def test_begin_decision_batch_uninitialized_rejected(self):
        dao = AgentDao(None)
        with pytest.raises(RuntimeError, match="Engine not initialized") as excinfo:
            await dao.begin_decision_batch(user_scope="u1")
        assert "AgentDao" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_begin_decision_batch_disposed_rejected_without_db_calls(self, monkeypatch):
        engine, conn = _batch_engine(None)
        monkeypatch.setattr(engine_provider, "is_disposed", lambda *args: True)
        dao = AgentDao(engine)
        with pytest.raises(EngineDisposedError, match="Engine disposed") as excinfo:
            await dao.begin_decision_batch(user_scope="u1")
        assert "AgentDao" in str(excinfo.value)
        engine.begin.assert_not_called()
        assert conn.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_postpone_decision_batch_uninitialized_rejected(self):
        dao = AgentDao(None)
        with pytest.raises(RuntimeError, match="Engine not initialized"):
            await dao.postpone_decision_batch(user_scope="u1", through_batch_seq=0)

    @pytest.mark.asyncio
    async def test_postpone_decision_batch_disposed_rejected_without_db_calls(self, monkeypatch):
        engine, conn = _batch_engine(None)
        monkeypatch.setattr(engine_provider, "is_disposed", lambda *args: True)
        dao = AgentDao(engine)
        with pytest.raises(EngineDisposedError, match="Engine disposed"):
            await dao.postpone_decision_batch(user_scope="u1", through_batch_seq=0)
        engine.begin.assert_not_called()
        assert conn.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_begin_decision_batch_increments_batch_seq(self):
        # 正常路径不回归：无 marker 行时 insert 后 batch_seq 从 0 递增到 1。
        engine, conn = _batch_engine(None)
        dao = AgentDao(engine)
        fact = await dao.begin_decision_batch(user_scope="u1")
        assert fact.batch_seq == 1
        assert fact.postponed_through_seq is None
        assert fact.replayed is False
        assert conn.execute.await_count == 2  # select 行锁 + insert

    @pytest.mark.asyncio
    async def test_postpone_decision_batch_greatest_merge(self):
        # 正常路径不回归：暂缓水位取 GREATEST（前进则落库，乱序不回退）。
        row = SimpleNamespace(batch_seq=3, postponed_through_seq=1)
        engine, conn = _batch_engine(row)
        dao = AgentDao(engine)
        forward = await dao.postpone_decision_batch(user_scope="u1", through_batch_seq=2)
        assert forward.batch_seq == 3
        assert forward.postponed_through_seq == 2
        assert forward.replayed is False
        row.postponed_through_seq = forward.postponed_through_seq  # 模拟落库后的水位
        stale = await dao.postpone_decision_batch(user_scope="u1", through_batch_seq=1)
        assert stale.postponed_through_seq == 2
        assert stale.replayed is True


class TestBacktestDaoStrictRead:
    """§9.13 C-01 配套：get_result 严格读参数透传。"""

    @pytest.mark.asyncio
    async def test_default_suppresses_failure_returns_none(self):
        # 旧调用默认行为不变：DB 失败吞错返回 None（既有语义）。
        dao = BacktestDAO(_failing_engine())
        result = await dao.get_result("run-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_strict_read_propagates_failure(self):
        # 新 Agent 调用显式 suppress_errors=False：失败分类传播，不得伪装「不存在」。
        dao = BacktestDAO(_failing_engine())
        with pytest.raises(DatabaseQueryError, match="Database read failed") as excinfo:
            await dao.get_result("run-1", suppress_errors=False)
        assert "connection boom" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_strict_read_returns_row_on_success(self):
        engine = _row_engine(("run-1", 0.91), ["run_id", "sharpe_ratio"])
        dao = BacktestDAO(engine)
        result = await dao.get_result("run-1", suppress_errors=False)
        assert result is not None and result["run_id"] == "run-1"

    @pytest.mark.asyncio
    async def test_no_row_is_not_found_even_in_strict_mode(self):
        # 成功查询确无行才是不存在（strict 模式下空集 → None）。
        proxy = MagicMock()
        proxy.fetchall.return_value = []
        proxy.keys.return_value = ["run_id"]
        conn = MagicMock()
        conn.execute = AsyncMock(return_value=proxy)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        engine = MagicMock(spec=AsyncEngine)
        engine.connect = MagicMock(return_value=cm)
        dao = BacktestDAO(engine)
        result = await dao.get_result("missing-run", suppress_errors=False)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_dataframe_treated_as_not_found(self):
        df = pd.DataFrame()
        dao = BacktestDAO(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=df)
        result = await dao.get_result("run-1")
        assert result is None


_DIGEST = "a" * 64


def _ref_wire(source_id: str, *, version: str = "1", digest: str = _DIGEST) -> dict[str, str]:
    return {
        "source_kind": "attachment",
        "source_id": source_id,
        "source_version": version,
        "source_digest": digest,
    }


def _ref(source_id: str, *, version: str = "1", digest: str = _DIGEST) -> SourceVersionRef:
    return SourceVersionRef(
        source_kind=SourceKind.ATTACHMENT,
        source_id=source_id,
        source_version=version,
        source_digest=digest,
    )


def _asset_row(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "read_state": "ready",
        "digest": _DIGEST,
        "disabled_at": None,
        "cleaned_at": None,
        "tombstoned": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestArtifactSourceRefs:
    """I80：成果载荷逐原 source_ref 解析（缺键即空，坏形状 fail-closed）。"""

    def test_payload_without_trace_has_no_refs(self):
        assert AgentDao._artifact_source_refs(None, "op-1") == ()
        assert AgentDao._artifact_source_refs(json_doc({"evidence": ["src-1"]}), "op-1") == ()
        assert AgentDao._artifact_source_refs(json_doc({"trace": {"source_refs": []}}), "op-1") == ()

    def test_refs_are_decoded_and_deduplicated_in_order(self):
        payload = json_doc({"trace": {"source_refs": [_ref_wire("src-a"), _ref_wire("src-b"), _ref_wire("src-a")]}})
        assert AgentDao._artifact_source_refs(payload, "op-1") == (_ref("src-a"), _ref("src-b"))

    def test_unreadable_or_malformed_refs_are_operation_unknown(self):
        payloads = (
            "{not-json",
            json_doc({"trace": {"source_refs": "src-1"}}),
            json_doc({"trace": {"source_refs": [{"source_id": "src-a"}]}}),
        )
        for payload in payloads:
            with pytest.raises(AgentServiceError) as error:
                AgentDao._artifact_source_refs(payload, "op-1")
            assert error.value.code is AgentErrorCode.OPERATION_UNKNOWN


class TestDeliverySourceState:
    """I80：单项来源状态映射——缺失/未读/坏行一律 unknown，绝不上调为 available。"""

    def test_missing_row_is_unknown(self):
        assert AgentDao._delivery_source_state(None, _ref("src-a")) == "unknown"

    def test_ready_matching_digest_is_available(self):
        assert AgentDao._delivery_source_state(_asset_row(), _ref("src-a")) == "available"

    def test_disabled_wins_over_cleaned_or_tombstoned(self):
        row = _asset_row(disabled_at=object(), cleaned_at=object(), tombstoned=True)
        assert AgentDao._delivery_source_state(row, _ref("src-a")) == "disabled"
        assert AgentDao._delivery_source_state(_asset_row(read_state="disabled"), _ref("src-a")) == "disabled"

    def test_cleaned_or_tombstoned_is_unavailable(self):
        assert AgentDao._delivery_source_state(_asset_row(cleaned_at=object()), _ref("src-a")) == "unavailable"
        assert AgentDao._delivery_source_state(_asset_row(tombstoned=True), _ref("src-a")) == "unavailable"
        assert AgentDao._delivery_source_state(_asset_row(read_state="unavailable"), _ref("src-a")) == "unavailable"

    def test_unread_unknown_or_bad_read_state_is_unknown(self):
        for read_state in ("unread", "unknown", "weird"):
            assert AgentDao._delivery_source_state(_asset_row(read_state=read_state), _ref("src-a")) == "unknown"

    def test_digest_mismatch_is_unknown(self):
        assert AgentDao._delivery_source_state(_asset_row(digest="b" * 64), _ref("src-a")) == "unknown"
