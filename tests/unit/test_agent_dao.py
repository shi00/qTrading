"""AgentDao 注册与守卫契约测试（任务 C-01-3，设计正本 §9.13）。

- R13：AgentDao 在 CacheManager.__init__ 显式实例化（self.agent_dao = AgentDao(self.engine)）；
- R5：引擎未初始化/已释放时入口守卫传播（未初始化 RuntimeError / disposed
  EngineDisposedError），不得返回空成功；
- §9.13 C-01 配套严格读：BacktestDao.get_result 支持 ``suppress_errors``
  透传（默认 True 保持旧调用行为不变；显式 False 时数据库失败分类传播，
  不能据 None 构造「报告不存在」）。

AgentDao 事务方法族随 C-02/C-03/C-04 实现，本文件只锁注册与守卫基线。
"""

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

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
