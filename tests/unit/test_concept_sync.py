"""Unit tests for concept sync strategies (AKShare + LimitList)."""

import contextlib

import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock

from data.external.akshare_concept_client import AkshareConceptClient
from data.external.tushare_client import TushareAPIPermissionError
from data.sync.base import SyncContext, SyncStatus
from data.sync.concept_sync import (
    AKShareConceptSyncStrategy,
    LimitListSyncStrategy,
    _to_ts_code,
)

pytestmark = pytest.mark.unit


# --- Helpers ---


def _make_ctx(**overrides):
    """Build a MagicMock-backed SyncContext with all dependencies wired."""
    ctx = MagicMock(spec=SyncContext)
    ctx.cache = MagicMock()
    ctx.cache.stock_dao = MagicMock()
    ctx.api = MagicMock()
    ctx.cancel_event = None
    ctx.processor = None
    # LimitListSyncStrategy 调用 overwrite_limit_concepts（事务原子性）
    ctx.cache.stock_dao.overwrite_limit_concepts = AsyncMock(return_value=0)
    # review08-D3: LimitListSyncStrategy 停写后调用 clear_all_limit_concepts（清空存量）
    ctx.cache.stock_dao.clear_all_limit_concepts = AsyncMock(return_value=0)
    # review08 D2: AKShareConceptSyncStrategy 现在预载 code→ts_code 权威映射。
    # 默认映射覆盖 _make_constituents_df 的代码，保证既有 AKShare 用例语义不变。
    ctx.cache.stock_dao.get_ts_code_map = AsyncMock(return_value={"000001": "000001.SZ", "600000": "600000.SH"})
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


def _make_concept_list_df():
    return pd.DataFrame(
        {
            "板块名称": ["锂电池", "光伏"],
            "板块代码": ["BK0123", "BK0456"],
        }
    )


def _make_constituents_df():
    return pd.DataFrame(
        {
            "代码": ["000001", "600000"],
            "名称": ["平安银行", "浦发银行"],
        }
    )


def _make_limit_list_df():
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "600000.SH"],
            "trade_date": ["20240614", "20240614"],
            "name": ["平安银行", "浦发银行"],
        }
    )


# --- _to_ts_code helper ---

# 权威映射样例（symbol -> ts_code），覆盖沪深京三交易所
_SAMPLE_CODE_MAP = {
    "000001": "000001.SZ",
    "600000": "600000.SH",
    "688001": "688001.SH",
    "300001": "300001.SZ",
    "430001": "430001.BJ",
}


class TestToTsCode:
    """_to_ts_code 查表函数测试：AKShare 6 位代码 → 权威 Tushare ts_code。

    review08 D2 后改为经 stocks 表权威映射解析，取代前缀猜测：
    - 映射命中（沪/深/京）→ 返回权威 ts_code
    - 未知代码（不在映射）或非法输入 → 返回 None（调用方跳过并记 warnings）
    """

    @pytest.mark.parametrize(
        "code,expected",
        [
            # 映射命中
            ("000001", "000001.SZ"),
            ("600000", "600000.SH"),
            ("688001", "688001.SH"),
            ("300001", "300001.SZ"),
            ("430001", "430001.BJ"),
        ],
    )
    def test_known_code_returns_authoritative_ts_code(self, code, expected):
        assert _to_ts_code(code, _SAMPLE_CODE_MAP) == expected

    def test_unknown_code_returns_none(self):
        """未知代码（不在映射中，如未来新代码段）→ None，不猜测 fallback。"""
        assert _to_ts_code("999999", _SAMPLE_CODE_MAP) is None
        assert _to_ts_code("123456", _SAMPLE_CODE_MAP) is None

    @pytest.mark.parametrize(
        "invalid_code",
        [
            "",  # 空字符串
            "12345",  # 长度不足 6
            "1234567",  # 长度超过 6
            "600abc",  # 非数字
            "abcdef",  # 全字母
        ],
    )
    def test_invalid_input_returns_none(self, invalid_code):
        assert _to_ts_code(invalid_code, _SAMPLE_CODE_MAP) is None

    def test_empty_map_returns_none(self):
        assert _to_ts_code("000001", {}) is None


# --- AKShareConceptSyncStrategy ---


class TestAKShareConceptSync:
    @pytest.mark.asyncio
    async def test_success(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=4)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        client.get_concept_constituents = AsyncMock(return_value=_make_constituents_df())

        strategy = AKShareConceptSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status == SyncStatus.SUCCESS.value
        assert result.added > 0
        assert ctx.cache.stock_dao.overwrite_em_concepts.call_count == 1
        records = ctx.cache.stock_dao.overwrite_em_concepts.call_args.args[0]
        assert len(records) == 4  # 2 板块 × 2 成分股

    @pytest.mark.asyncio
    async def test_fully_resolved_boards_marked_for_replacement(self):
        """review09-24 MAJOR-06: 成分全部解析成功的板块进入 replace_board_codes（删除-重建）"""
        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=4)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        client.get_concept_constituents = AsyncMock(return_value=_make_constituents_df())

        strategy = AKShareConceptSyncStrategy(ctx)
        await strategy.run()

        kwargs = ctx.cache.stock_dao.overwrite_em_concepts.call_args.kwargs
        assert kwargs["replace_board_codes"] == ["BK0123", "BK0456"]

    @pytest.mark.asyncio
    async def test_partially_resolved_board_not_marked_for_replacement(self):
        """review09-24 MAJOR-06: 部分代码未解析的板块不做删除（仅 upsert），避免误删有效旧成分"""
        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=2)
        df_cons = pd.DataFrame({"代码": ["000001", "999999"], "名称": ["平安银行", "未知"]})

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        client.get_concept_constituents = AsyncMock(return_value=df_cons)

        strategy = AKShareConceptSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status == SyncStatus.SUCCESS.value
        kwargs = ctx.cache.stock_dao.overwrite_em_concepts.call_args.kwargs
        assert kwargs["replace_board_codes"] == []  # 两板块均有未解析码 → 均不删除

    @pytest.mark.asyncio
    async def test_empty_constituents_board_skipped_no_record(self):
        """review09-24 MAJOR-06 / R21: 空成分响应视为不确定，不产生记录、不删除旧行"""
        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=0)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        client.get_concept_constituents = AsyncMock(return_value=pd.DataFrame())

        strategy = AKShareConceptSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status == SyncStatus.SUCCESS.value
        ctx.cache.stock_dao.overwrite_em_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_codes_unresolved_board_skipped_no_record(self):
        """review09-24 MAJOR-06 / R21: 板块全部代码未解析时不产生记录、不删除旧行"""
        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=0)
        df_cons = pd.DataFrame({"代码": ["999999"], "名称": ["未知"]})

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        client.get_concept_constituents = AsyncMock(return_value=df_cons)

        strategy = AKShareConceptSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status == SyncStatus.SUCCESS.value
        ctx.cache.stock_dao.overwrite_em_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_code_skipped_with_warning(self):
        """review08 D2: 成分股代码在 stocks 表权威映射中缺失时，跳过该记录并记 warning，
        不再用交易所前缀猜测 fallback .SZ。"""
        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=1)
        # 默认映射含 000001/600000；999999 未知
        df_cons = pd.DataFrame(
            {
                "代码": ["000001", "999999"],
                "名称": ["平安银行", "未知代码"],
            }
        )

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        client.get_concept_constituents = AsyncMock(return_value=df_cons)

        strategy = AKShareConceptSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status == SyncStatus.SUCCESS.value
        assert result.added == 1  # 仅 000001 入库
        assert result.skipped == 1
        assert any("999999" in w for w in result.warnings)
        records = ctx.cache.stock_dao.overwrite_em_concepts.call_args.args[0]
        assert all("999999.SZ" not in r["ts_code"] for r in records)  # 无 fallback 猜测

    @pytest.mark.asyncio
    async def test_cancel_returns_cancelled(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=0)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        client.get_concept_constituents = AsyncMock(return_value=_make_constituents_df())

        strategy = AKShareConceptSyncStrategy(ctx)
        strategy.cancel()
        result = await strategy.run()

        assert result.status == SyncStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_partial_when_constituents_fail(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=2)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        # First call fails, second succeeds (will be retried up to 3 times)
        call_count = 0

        async def _flaky_constituents(symbol):
            nonlocal call_count
            call_count += 1
            if symbol == "锂电池":
                raise ConnectionError("network error")
            return _make_constituents_df()

        client.get_concept_constituents = AsyncMock(side_effect=_flaky_constituents)

        strategy = AKShareConceptSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status in (SyncStatus.PARTIAL.value, SyncStatus.SUCCESS.value)
        assert len(result.errors) > 0 or result.warnings

    @pytest.mark.asyncio
    async def test_empty_concept_list(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=0)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=pd.DataFrame())
        client.get_concept_constituents = AsyncMock(return_value=_make_constituents_df())

        strategy = AKShareConceptSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status == SyncStatus.SUCCESS.value
        assert result.added == 0
        ctx.cache.stock_dao.overwrite_em_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_concept_list_fetch_exception(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=0)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(side_effect=ConnectionError("network error"))
        client.get_concept_constituents = AsyncMock(return_value=_make_constituents_df())

        strategy = AKShareConceptSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status == SyncStatus.FAILED.value
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_ts_code_map_failure_returns_failed_without_writing(self):
        """review08 D2: 权威映射预载失败（DB 故障）→ 策略 status=FAILED 且不写 concepts。

        映射预载失败必须显式失败（R21：不可降级为空映射静默空跑，否则全部成分股
        被误判为“不在 stock_basic”并误报 SUCCESS）。DatabaseQueryError 经
        _run_impl 外层 except Exception → classify_severity=operational → FAILED。
        """
        from data.persistence.daos.base_dao import DatabaseQueryError

        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=0)
        ctx.cache.stock_dao.get_ts_code_map = AsyncMock(side_effect=DatabaseQueryError("db down"))

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        client.get_concept_constituents = AsyncMock(return_value=_make_constituents_df())

        strategy = AKShareConceptSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status == SyncStatus.FAILED.value
        assert len(result.errors) > 0
        # 映射预载失败即中止，未写入任何概念
        ctx.cache.stock_dao.overwrite_em_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_after_concept_list_fetch(self):
        """覆盖 concept_sync.py:88-89：concept_list 拉取成功后、启动 constituents 并发前触发取消。

        验证：第二次 _check_cancelled 命中 → 直接返回 CANCELLED，不调用 constituents 拉取。
        """
        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=0)
        strategy = AKShareConceptSyncStrategy(ctx)

        client = AkshareConceptClient()

        async def _cancel_then_return(*args, **kwargs):
            strategy.cancel()  # 在返回 concept_list 前触发取消标志
            return _make_concept_list_df()

        client.get_concept_list = AsyncMock(side_effect=_cancel_then_return)
        client.get_concept_constituents = AsyncMock(return_value=_make_constituents_df())

        result = await strategy.run()

        assert result.status == SyncStatus.CANCELLED.value
        # 取消后不应继续拉取 constituents
        client.get_concept_constituents.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_after_gather_before_upsert(self):
        """覆盖 concept_sync.py:141-142：所有 board 并发拉取完成后、upsert 前触发取消。

        验证：第三次 _check_cancelled 命中 → 返回 CANCELLED，不调用 overwrite_em_concepts。
        """
        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=0)
        strategy = AKShareConceptSyncStrategy(ctx)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        # constituents 拉取完成后触发取消
        original = _make_constituents_df()

        async def _cancel_after_constituents(*args, **kwargs):
            strategy.cancel()
            return original

        client.get_concept_constituents = AsyncMock(side_effect=_cancel_after_constituents)

        result = await strategy.run()

        assert result.status == SyncStatus.CANCELLED.value
        ctx.cache.stock_dao.overwrite_em_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancelled_error_in_constituents_propagates(self):
        """覆盖 concept_sync.py:115-116：sync_one_board 内部 constituents 拉取抛 CancelledError 必须传播（R2）。

        验证：CancelledError 不被 except Exception 吞掉，直接 raise 到外层 except asyncio.CancelledError。
        """
        import asyncio as _asyncio

        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=0)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        client.get_concept_constituents = AsyncMock(side_effect=_asyncio.CancelledError())

        strategy = AKShareConceptSyncStrategy(ctx)
        with pytest.raises(_asyncio.CancelledError) as exc_info:
            await strategy.run()
        assert isinstance(exc_info.value, _asyncio.CancelledError)

    @pytest.mark.asyncio
    async def test_engine_disposed_in_constituents_skips_retry(self):
        """覆盖 concept_sync.py:117-118：sync_one_board 内部 constituents 拉取抛 EngineDisposedError 时，
        except EngineDisposedError 分支直接 raise（不进入 except Exception 重试逻辑），
        由 gather_return_exceptions_propagating_cancel 捕获为返回值，不传播到外层。

        验证：get_concept_constituents 每板只调用 1 次（非 3 次重试），upsert 不执行。
        """
        from data.persistence.daos.base_dao import EngineDisposedError

        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=0)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        client.get_concept_constituents = AsyncMock(side_effect=EngineDisposedError())

        strategy = AKShareConceptSyncStrategy(ctx)
        result = await strategy.run()

        # EngineDisposedError 被 gather 捕获为返回值，不传播到外层 except
        assert result.status == SyncStatus.SUCCESS.value
        # 关键验证：每板只调用 1 次（EngineDisposedError 不进入重试逻辑）
        # _make_concept_list_df() 返回 2 个板块，所以应调用 2 次（非 6 次）
        assert client.get_concept_constituents.call_count == 2
        # records 为空，不调用 upsert
        ctx.cache.stock_dao.overwrite_em_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_system_level_error_propagates(self):
        """覆盖 concept_sync.py:168-170：system 级别异常（MemoryError）必须 raise，不可降级为 FAILED。

        验证 classify_severity 返回 "system" 时，logger.critical 后 raise，不吞异常。
        """
        ctx = _make_ctx()
        ctx.cache.stock_dao.overwrite_em_concepts = AsyncMock(return_value=0)

        client = AkshareConceptClient()
        # MemoryError 是 SYSTEM_LEVEL_EXCEPTIONS，classify_severity 返回 "system"
        client.get_concept_list = AsyncMock(side_effect=MemoryError("out of memory"))
        client.get_concept_constituents = AsyncMock(return_value=_make_constituents_df())

        strategy = AKShareConceptSyncStrategy(ctx)
        with pytest.raises(MemoryError) as exc_info:
            await strategy.run()
        assert isinstance(exc_info.value, MemoryError)


# --- LimitListSyncStrategy ---


class TestLimitListSync:
    """review08-D3 停写语义：不再构造 LIMIT_ 股票名概念、不再调用 overwrite_limit_concepts。

    任何路径（fetch 成功 / 权限不足 / 空数据）统一调用 clear_all_limit_concepts
    清空存量 + warning；CancelledError / EngineDisposedError 传播（R2/R5）。
    """

    @pytest.mark.asyncio
    async def test_success_stops_and_clears(self):
        """fetch 成功但停写：不再构造 records，清空存量 + warning。"""
        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_all_limit_concepts = AsyncMock(return_value=3)
        ctx.api.get_limit_list = AsyncMock(return_value=_make_limit_list_df())

        strategy = LimitListSyncStrategy(ctx)
        result = await strategy.run(trade_date="20240614")

        assert result.status == SyncStatus.SUCCESS.value
        assert result.added == 0
        ctx.cache.stock_dao.clear_all_limit_concepts.assert_awaited_once()
        ctx.cache.stock_dao.overwrite_limit_concepts.assert_not_called()
        assert any("LIMIT_" in w or "停写" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_cancel_returns_cancelled(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_all_limit_concepts = AsyncMock(return_value=0)
        ctx.api.get_limit_list = AsyncMock(return_value=_make_limit_list_df())

        strategy = LimitListSyncStrategy(ctx)
        strategy.cancel()
        result = await strategy.run(trade_date="20240614")

        assert result.status == SyncStatus.CANCELLED.value
        ctx.cache.stock_dao.clear_all_limit_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_permission_denied_clears_and_warns(self):
        """权限不足：同样清空存量（P0 修订，避免旧污染残留）+ warning。"""
        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_all_limit_concepts = AsyncMock(return_value=2)
        ctx.api.get_limit_list = AsyncMock(
            side_effect=TushareAPIPermissionError("limit_list", "积分不足"),
        )

        strategy = LimitListSyncStrategy(ctx)
        result = await strategy.run(trade_date="20240614")

        assert result.status == SyncStatus.SUCCESS.value
        assert len(result.warnings) > 0
        ctx.cache.stock_dao.clear_all_limit_concepts.assert_awaited_once()
        ctx.cache.stock_dao.overwrite_limit_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_limit_list_clears_and_warns(self):
        """空数据：清空 + warning（旧数据不再保留）。"""
        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_all_limit_concepts = AsyncMock(return_value=0)
        ctx.api.get_limit_list = AsyncMock(return_value=pd.DataFrame())

        strategy = LimitListSyncStrategy(ctx)
        result = await strategy.run(trade_date="20240614")

        assert result.status == SyncStatus.SUCCESS.value
        assert result.added == 0
        ctx.cache.stock_dao.clear_all_limit_concepts.assert_awaited_once()
        ctx.cache.stock_dao.overwrite_limit_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_general_exception_returns_failed(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_all_limit_concepts = AsyncMock(return_value=0)
        ctx.api.get_limit_list = AsyncMock(side_effect=RuntimeError("unexpected"))

        strategy = LimitListSyncStrategy(ctx)
        result = await strategy.run(trade_date="20240614")

        assert result.status == SyncStatus.FAILED.value
        assert len(result.errors) > 0
        # review08-D3：失败文案只声明"不再写入"，不声称存量已清除（清空自身失败也会落到此分支）
        assert any("no longer written (review08-D3)" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """覆盖 concept_sync.py 外层 except asyncio.CancelledError：状态设为 CANCELLED 并 raise（R2）。"""
        import asyncio as _asyncio

        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_all_limit_concepts = AsyncMock(side_effect=_asyncio.CancelledError())
        ctx.api.get_limit_list = AsyncMock(return_value=_make_limit_list_df())

        strategy = LimitListSyncStrategy(ctx)
        with pytest.raises(_asyncio.CancelledError) as exc_info:
            await strategy.run(trade_date="20240614")
        assert isinstance(exc_info.value, _asyncio.CancelledError)

    @pytest.mark.asyncio
    async def test_system_level_error_propagates(self):
        """覆盖 concept_sync.py：system 级别异常（PermissionError）必须 raise，不可降级为 FAILED。"""
        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_all_limit_concepts = AsyncMock(return_value=0)
        # PermissionError 是 system 级别异常
        ctx.api.get_limit_list = AsyncMock(side_effect=PermissionError("denied"))

        strategy = LimitListSyncStrategy(ctx)
        with pytest.raises(PermissionError) as exc_info:
            await strategy.run(trade_date="20240614")
        assert isinstance(exc_info.value, PermissionError)

    @pytest.mark.asyncio
    async def test_cancel_before_fetch_no_clear(self):
        """fetch 之前触发取消信号时 clear_all_limit_concepts 不应被调用（取消优先）。"""
        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_all_limit_concepts = AsyncMock(return_value=0)
        ctx.api.get_limit_list = AsyncMock(return_value=_make_limit_list_df())

        strategy = LimitListSyncStrategy(ctx)
        strategy.cancel()
        result = await strategy.run(trade_date="20240614")

        assert result.status == SyncStatus.CANCELLED.value
        ctx.api.get_limit_list.assert_not_called()
        ctx.cache.stock_dao.clear_all_limit_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_success_never_writes_records(self):
        """fetch 成功但停写：clear 被调用、不写入任何 LIMIT_ 记录。"""
        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_all_limit_concepts = AsyncMock(return_value=2)
        ctx.api.get_limit_list = AsyncMock(return_value=_make_limit_list_df())

        strategy = LimitListSyncStrategy(ctx)
        result = await strategy.run(trade_date="20240614")

        assert result.status == SyncStatus.SUCCESS.value
        assert result.added == 0
        ctx.cache.stock_dao.clear_all_limit_concepts.assert_awaited_once()
        ctx.cache.stock_dao.overwrite_limit_concepts.assert_not_called()
        ctx.cache.stock_dao.upsert_limit_concepts.assert_not_called()
