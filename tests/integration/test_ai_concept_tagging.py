"""集成测试：AI 概念打标端到端流程（错题本 + 策略 + DAO）。

覆盖：
1. 错题本 DAO 端到端：upsert → get_for_retry → clear
2. AIConceptTagSyncStrategy 端到端：LLM 失败入队 → 下次 run 优先重试 → 成功后清除
3. EngineDisposedError 在策略外层传播（R5 集成验证）
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import delete

from data.persistence.models import AIConceptFailure, StockConcepts
from data.persistence.daos.stock_dao import StockDao
from data.sync.base import SyncContext, SyncStatus
from data.sync.concept_sync import AIConceptTagSyncStrategy

pytestmark = pytest.mark.integration


@pytest.fixture
def stock_dao(function_engine):
    """Direct StockDao bound to CacheManager engine (function loop, 与 mvd_data 同 loop)."""
    return StockDao(function_engine)


@pytest_asyncio.fixture
async def clean_ai_concept_tables(test_engine):
    """Pre/post-cleanup for ai_concept_failures + stock_concepts (AI_LLM_ only)."""

    async def _cleanup():
        async with test_engine.begin() as conn:
            await conn.execute(delete(AIConceptFailure))
            await conn.execute(
                StockConcepts.__table__.delete().where(StockConcepts.__table__.c.concept_id.like("AI_LLM_%"))
            )

    await _cleanup()
    yield
    await _cleanup()


def _make_ai_service_mock(available=True, response=None, side_effect=None):
    svc = MagicMock()
    svc.is_cloud_available = MagicMock(return_value=available)
    default_content = '{"concepts": ["锂电池", "新能源车"]}'
    if side_effect is not None:
        svc.chat_with_web_search = AsyncMock(side_effect=side_effect)
    else:
        svc.chat_with_web_search = AsyncMock(
            return_value=response or {"content": default_content},
        )
    return svc


def _make_ctx(stock_dao, ai_service=None, cancel_event=None):
    ctx = MagicMock(spec=SyncContext)
    ctx.cache = MagicMock()
    ctx.cache.stock_dao = stock_dao
    ctx.api = MagicMock()
    ctx.ai_service = ai_service
    ctx.cancel_event = cancel_event
    ctx.processor = None
    # 策略-L1: 显式设置 search_engine 默认值，避免 MagicMock 自动生成非字符串对象
    ctx.config.get_ai_concept_search_engine = MagicMock(return_value="search_std")
    return ctx


class TestAIConceptFailureDAOE2E:
    """错题本 DAO 端到端：DB 真实读写"""

    pytestmark = pytest.mark.usefixtures("clean_ai_concept_tables", "mvd_data")

    @pytest.mark.asyncio
    async def test_upsert_then_get_then_clear(self, stock_dao):
        """完整错题本生命周期：upsert 失败 → 查询可重试 → 清除

        本用例验证 DAO 生命周期，不验证冷却语义（后者由 test_cooldown_prevents_early_retry
        覆盖）。传 cooldown_seconds=0 模拟立即可重试场景。
        """
        # 1. upsert 失败（cooldown=0 立即可重试）
        n = await stock_dao.upsert_ai_concept_failure(
            "000001.SZ",
            "平安银行",
            "LLM timeout",
            cooldown_seconds=0,
        )
        assert n == 1
        # 2. 查询可重试列表
        pending = await stock_dao.get_ai_concept_failures_for_retry(batch_size=10)
        assert pending == [("000001.SZ", "平安银行")]
        # 3. 清除
        deleted = await stock_dao.clear_ai_concept_failure("000001.SZ")
        assert deleted == 1
        # 4. 清除后查询为空
        pending_after = await stock_dao.get_ai_concept_failures_for_retry(batch_size=10)
        assert pending_after == []

    @pytest.mark.asyncio
    async def test_upsert_increments_retry_count(self, stock_dao):
        """多次 upsert 同一股票应累加 retry_count

        传 cooldown_seconds=0 模拟立即可重试，专注于 retry_count 累加语义验证。
        """
        await stock_dao.upsert_ai_concept_failure("000001.SZ", "平安银行", "err1", cooldown_seconds=0)
        await stock_dao.upsert_ai_concept_failure("000001.SZ", "平安银行", "err2", cooldown_seconds=0)
        await stock_dao.upsert_ai_concept_failure("000001.SZ", "平安银行", "err3", cooldown_seconds=0)

        # retry_count=3 已达 max_retry=3 阈值（retry_count < 3 不成立），不应再被拉取
        pending = await stock_dao.get_ai_concept_failures_for_retry(batch_size=10)
        assert pending == [], "retry_count=3 不应再被拉取（max_retry=3）"

        # 但 max_retry=10 时应能拉取
        pending_extended = await stock_dao.get_ai_concept_failures_for_retry(batch_size=10, max_retry=10)
        assert pending_extended == [("000001.SZ", "平安银行")]

    @pytest.mark.asyncio
    async def test_cooldown_prevents_early_retry(self, stock_dao):
        """cooldown 期内不应被拉取（next_retry_at > now）"""
        # cooldown=3600s，1 小时后才可重试
        await stock_dao.upsert_ai_concept_failure("000001.SZ", "平安银行", "err", cooldown_seconds=3600)
        pending = await stock_dao.get_ai_concept_failures_for_retry(batch_size=10)
        assert pending == [], "cooldown 期内不应被拉取"

    @pytest.mark.asyncio
    async def test_count_failures(self, stock_dao):
        await stock_dao.upsert_ai_concept_failure("000001.SZ", "n1", "e1")
        await stock_dao.upsert_ai_concept_failure("600000.SH", "n2", "e2")
        cnt = await stock_dao.count_ai_concept_failures()
        assert cnt == 2


class TestAIConceptTagStrategyE2E:
    """AIConceptTagSyncStrategy 端到端：错题本 + LLM + DB"""

    pytestmark = pytest.mark.usefixtures("clean_ai_concept_tables", "mvd_data")

    @pytest.mark.asyncio
    async def test_llm_failure_persists_to_retry_queue(self, stock_dao):
        """LLM 失败 → 写入错题本

        生产代码策略调用 upsert_ai_concept_failure 时不传 cooldown，默认 24h 冷却
        （防止短时间内反复失败）。因此本用例不通过 get_for_retry 验证（那是冷却
        过期后的语义），而用 count_ai_concept_failures 验证记录已持久化。
        """
        ai_service = _make_ai_service_mock(side_effect=RuntimeError("LLM service unavailable"))
        ctx = _make_ctx(stock_dao, ai_service=ai_service)
        strategy = AIConceptTagSyncStrategy(ctx)

        result = await strategy.run(batch_size=10)
        assert result.status == SyncStatus.PARTIAL.value
        assert len(result.errors) > 0

        # 验证错题本中存在记录（24h 冷却期内 get_for_retry 返回空是正确行为）
        cnt = await stock_dao.count_ai_concept_failures()
        assert cnt >= 1, "LLM 失败应写入错题本"

    @pytest.mark.asyncio
    async def test_retry_succeeds_and_clears_failure(self, stock_dao):
        """第二次 run（LLM 恢复）应优先从错题本拉取并成功清除

        传 cooldown_seconds=0 模拟冷却已过期，策略才能从错题本拉取到记录，
        LLM 成功后调用 clear_ai_concept_failure 真正清除记录。否则默认 24h
        冷却会让 get_for_retry 返回空，clear 不会被调用，测试语义错误。
        """
        # 1. 先注入错题本记录（cooldown=0 立即可重试）
        await stock_dao.upsert_ai_concept_failure(
            "000001.SZ",
            "平安银行",
            "prev err",
            cooldown_seconds=0,
        )

        # 2. LLM 恢复正常
        ai_service = _make_ai_service_mock()
        ctx = _make_ctx(stock_dao, ai_service=ai_service)
        strategy = AIConceptTagSyncStrategy(ctx)

        result = await strategy.run(batch_size=10)
        assert result.status == SyncStatus.SUCCESS.value

        # 3. 错题本记录应被清除（策略从错题本拉取 → LLM 成功 → clear 调用）
        pending = await stock_dao.get_ai_concept_failures_for_retry(batch_size=10)
        assert pending == [], f"成功打标后错题本应清除，剩余: {pending}"

        # 4. AI 概念应已写入 stock_concepts
        concepts = await stock_dao.get_concepts_by_prefix("AI_LLM_", ts_codes=["000001.SZ"])
        assert len(concepts) > 0

        # 5. 策略-M1: 集成层验证 search_engine 透传到 AIService
        assert ai_service.chat_with_web_search.call_args.kwargs["search_engine"] == "search_std"

    @pytest.mark.asyncio
    async def test_cancel_event_aborts_within_2s(self, stock_dao):
        """P0-2 集成验证：cancel_event 在 LLM 调用中触发，应在 2 秒内响应"""
        cancel_event = asyncio.Event()

        # 模拟 10 秒阻塞的 LLM 调用
        async def _slow_llm(*args, **kwargs):
            await asyncio.sleep(10)
            return {"content": "{}"}

        ai_service = _make_ai_service_mock(side_effect=_slow_llm)
        ctx = _make_ctx(stock_dao, ai_service=ai_service, cancel_event=cancel_event)
        strategy = AIConceptTagSyncStrategy(ctx)

        # 后台 0.5s 后设置 cancel_event
        async def _set_cancel():
            await asyncio.sleep(0.5)
            cancel_event.set()

        asyncio.create_task(_set_cancel())

        start = asyncio.get_running_loop().time()
        with pytest.raises(asyncio.CancelledError):
            await strategy.run(batch_size=10)
        elapsed = asyncio.get_running_loop().time() - start
        # 应在 ~2s 内响应（_AI_TAG_CANCEL_POLL_INTERVAL），不是 10s
        assert elapsed < 3.0, f"取消响应时间 {elapsed}s 超过 3 秒阈值"


class TestEngineDisposedE2E:
    """EngineDisposedError 端到端传播（R5）"""

    @pytest.mark.asyncio
    async def test_engine_disposed_propagates_from_strategy(self, test_engine):
        """策略外层 except EngineDisposedError 必须 raise 到调用方"""
        from data.persistence.daos.base_dao import EngineDisposedError

        # 构造一个 disposed engine 的 StockDao
        stock_dao = StockDao(test_engine)
        # 直接替换 _read_db 抛 EngineDisposedError（模拟引擎已释放）
        stock_dao.get_ai_concept_failures_for_retry = AsyncMock(side_effect=EngineDisposedError())
        stock_dao.get_stocks_without_ai_concepts = AsyncMock(return_value=[])

        ai_service = _make_ai_service_mock()
        ctx = _make_ctx(stock_dao, ai_service=ai_service)
        strategy = AIConceptTagSyncStrategy(ctx)

        with pytest.raises(EngineDisposedError):
            await strategy.run(batch_size=10)
