# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import datetime

import pytest
from unittest.mock import MagicMock, AsyncMock
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.base_dao import EngineDisposedError
from data.persistence.daos.macro_dao import MacroDao

pytestmark = pytest.mark.unit


class TestMacroDaoSaveMacroEconomy:
    @pytest.mark.asyncio
    async def test_none(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_macro_economy(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_macro_economy(pd.DataFrame())
        assert result == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=5)
        result = await dao.save_macro_economy(pd.DataFrame({"period": ["202406"]}))
        assert result == 5


class TestMacroDaoSaveShiborDaily:
    @pytest.mark.asyncio
    async def test_none(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_shibor_daily(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_shibor_daily(pd.DataFrame())
        assert result == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=3)
        result = await dao.save_shibor_daily(pd.DataFrame({"record_date": ["20240615"]}))
        assert result == 3


class TestMacroDaoSaveLprDaily:
    """S15 fix: save_lpr_daily 独立 upsert LPR 数据到 shibor_daily 表。"""

    @pytest.mark.asyncio
    async def test_none(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_lpr_daily(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_lpr_daily(pd.DataFrame())
        assert result == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=2)
        result = await dao.save_lpr_daily(
            pd.DataFrame(
                {
                    "record_date": ["20240615"],
                    "lpr_1y": [3.45],
                    "lpr_5y": [3.95],
                }
            )
        )
        assert result == 2
        # 验证仅传入 record_date/lpr_1y/lpr_5y 三列（不触碰其他 shibor 列）
        call_args = dao._save_upsert.call_args
        assert call_args.args[1] == "shibor_daily"
        columns = call_args.args[2]
        assert set(columns) == {"record_date", "lpr_1y", "lpr_5y"}


class TestMacroDaoGetMacroLatestDate:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_date": ["202406"]}))
        result = await dao.get_macro_latest_date()
        assert result == "202406"

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_date": [None]}))
        result = await dao.get_macro_latest_date()
        assert result is None


class TestMacroDaoGetShiborLatestDate:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_date": ["20240615"]}))
        result = await dao.get_shibor_latest_date()
        assert result == "20240615"

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_date": [None]}))
        result = await dao.get_shibor_latest_date()
        assert result is None


class TestMacroDaoGetShiborLatest:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"record_date": ["20240615"], "on_rate": [1.5]}))
        result = await dao.get_shibor_latest()
        assert not result.empty

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=None)
        result = await dao.get_shibor_latest()
        assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    async def test_error(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(side_effect=Exception("DB Error"))
        result = await dao.get_shibor_latest()
        assert isinstance(result, pd.DataFrame)


class TestMacroDaoGetMacroEconomyLatest:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"period": ["202406"], "m2": [100]}))
        result = await dao.get_macro_economy_latest()
        assert not result.empty

    @pytest.mark.asyncio
    async def test_error(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(side_effect=Exception("DB Error"))
        result = await dao.get_macro_economy_latest()
        assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    async def test_with_as_of_date(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"period": ["202312"], "m2": [90]}))
        result = await dao.get_macro_economy_latest(as_of_date="2024-01-01")
        assert not result.empty
        dao._read_db_select.assert_called_once()
        stmt = dao._read_db_select.call_args[0][0]
        sql_str = str(stmt)
        assert "publish_date <=" in sql_str
        # DAT-06: publish_date 与 ann_date 同构的可空 PIT 键，必须显式 IS NOT NULL
        assert "publish_date IS NOT NULL" in sql_str

    @pytest.mark.asyncio
    async def test_without_as_of_date(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"period": ["202406"], "m2": [100]}))
        result = await dao.get_macro_economy_latest(as_of_date=None)
        assert not result.empty
        stmt = dao._read_db_select.call_args[0][0]
        sql_str = str(stmt)
        assert "publish_date <=" not in sql_str
        # DAT-06: 非 as-of 分支同样必须显式 IS NOT NULL，消除两分支口径差
        assert "publish_date IS NOT NULL" in sql_str


class TestMacroDaoGetShiborLatestWithAsOfDate:
    @pytest.mark.asyncio
    async def test_with_as_of_date(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"record_date": ["20240101"], "on_rate": [1.5]}))
        result = await dao.get_shibor_latest(as_of_date="2024-01-15")
        assert not result.empty
        dao._read_db_select.assert_called_once()
        stmt = dao._read_db_select.call_args[0][0]
        sql_str = str(stmt)
        assert "record_date <=" in sql_str

    @pytest.mark.asyncio
    async def test_without_as_of_date(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"record_date": ["20240615"], "on_rate": [1.5]}))
        result = await dao.get_shibor_latest(as_of_date=None)
        assert not result.empty


class TestMacroDaoAsOfDateNormalization:
    """DAT-26 一致性：as_of_date 8 位 YYYYMMDD 串必须在 DAO 边界归一化为 date 对象。

    回归背景（review08-M08-F1）：get_shibor_latest / get_macro_economy_latest 此前
    未做 _to_db_date 归一化，SA asyncpg dialect 对 Date 列 bind_processor 为空透传，
    asyncpg date codec 仅接受 date 对象——上游 ai_mixin 经
    _normalize_trade_date_for_cache 传入 8 位串时参数编码失败，异常被 except
    Exception 降级为空 DataFrame，宏观/Shibor AI 上下文静默失效。
    """

    @pytest.mark.asyncio
    async def test_get_shibor_latest_normalizes_yyyymmdd_string(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"record_date": ["20240101"], "on_rate": [1.5]}))
        result = await dao.get_shibor_latest(as_of_date="20240115")
        assert not result.empty
        stmt = dao._read_db_select.call_args[0][0]
        # limit 的行数在默认方言 compile 下也是绑定参数（int），故只提取 date 形参断言
        date_values = [v for v in stmt.compile().params.values() if isinstance(v, datetime.date)]
        assert date_values == [datetime.date(2024, 1, 15)]

    @pytest.mark.asyncio
    async def test_get_shibor_latest_keeps_none_unbound(self):
        """as_of_date=None 不产生日期绑定参数（不限制时间窗）。"""
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"record_date": ["20240101"], "on_rate": [1.5]}))
        await dao.get_shibor_latest(as_of_date=None)
        stmt = dao._read_db_select.call_args[0][0]
        assert "record_date <=" not in str(stmt)
        assert not [v for v in stmt.compile().params.values() if isinstance(v, datetime.date)]

    @pytest.mark.asyncio
    async def test_get_macro_economy_latest_normalizes_yyyymmdd_string(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"period": ["202312"], "m2": [90]}))
        result = await dao.get_macro_economy_latest(as_of_date="20240115")
        assert not result.empty
        stmt = dao._read_db_select.call_args[0][0]
        sql_str = str(stmt)
        assert "publish_date <=" in sql_str
        assert "publish_date IS NOT NULL" in sql_str
        date_values = [v for v in stmt.compile().params.values() if isinstance(v, datetime.date)]
        assert date_values == [datetime.date(2024, 1, 15)]

    @pytest.mark.asyncio
    async def test_get_macro_economy_latest_accepts_date_object(self):
        """date 对象输入行为不变（归一化幂等）。"""
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"period": ["202312"], "m2": [90]}))
        await dao.get_macro_economy_latest(as_of_date=datetime.date(2024, 1, 15))
        stmt = dao._read_db_select.call_args[0][0]
        date_values = [v for v in stmt.compile().params.values() if isinstance(v, datetime.date)]
        assert date_values == [datetime.date(2024, 1, 15)]


class TestMacroDaoErrorPropagation:
    """R2/R5：get_shibor_latest / get_macro_economy_latest 的取消与引擎停机信号必须传播。"""

    @pytest.mark.asyncio
    async def test_get_shibor_latest_propagates_cancelled_error(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        cancel_err = asyncio.CancelledError()
        dao._read_db_select = AsyncMock(side_effect=cancel_err)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await dao.get_shibor_latest()
        assert exc_info.value is cancel_err  # R2: 取消信号必须原样传播同一实例，不可吞没或包装

    @pytest.mark.asyncio
    async def test_get_shibor_latest_propagates_engine_disposed(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError, match="disposed"):
            await dao.get_shibor_latest()

    @pytest.mark.asyncio
    async def test_get_macro_economy_latest_propagates_cancelled_error(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        cancel_err = asyncio.CancelledError()
        dao._read_db_select = AsyncMock(side_effect=cancel_err)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await dao.get_macro_economy_latest()
        assert exc_info.value is cancel_err  # R2: 取消信号必须原样传播同一实例，不可吞没或包装

    @pytest.mark.asyncio
    async def test_get_macro_economy_latest_propagates_engine_disposed(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError, match="disposed"):
            await dao.get_macro_economy_latest()
