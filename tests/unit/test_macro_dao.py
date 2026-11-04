# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import datetime

import pytest
from unittest.mock import MagicMock, AsyncMock
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.base_dao import EngineDisposedError
from data.persistence.daos.macro_dao import MacroDao, _build_macro_economy_latest_stmt
from data.persistence.models import MacroEconomy

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
        # 月度/ GDP 两分支共用同一归一化 bind 参数，SQL 两处 WHERE 复用同一日期
        assert date_values == [datetime.date(2024, 1, 15)]
        assert sql_str.count("publish_date <=") == 2

    @pytest.mark.asyncio
    async def test_get_macro_economy_latest_accepts_date_object(self):
        """date 对象输入行为不变（归一化幂等）。"""
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"period": ["202312"], "m2": [90]}))
        await dao.get_macro_economy_latest(as_of_date=datetime.date(2024, 1, 15))
        stmt = dao._read_db_select.call_args[0][0]
        date_values = [v for v in stmt.compile().params.values() if isinstance(v, datetime.date)]
        assert date_values == [datetime.date(2024, 1, 15)]
        assert str(stmt).count("publish_date <=") == 2


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


class TestMacroDaoMixedFrequencySelection:
    """D03-F01：月度行 + GDP 行混频选行必须是「各频率最新可见 1 行」。

    既有实现按 publish_date 全表倒序 limit(2)，在 GDP 之后有 ≥2 条月度发布时
    会把 GDP 行挤出窗口。此处用 SQLite 内存库执行 DAO 生成的真实 SQL，
    覆盖 PIT 可见性、混频缺失、合法零值、稳定排序与 NULL publish_date。
    """

    @staticmethod
    def _row(period, publish_date, **values):
        row = {c.name: None for c in MacroEconomy.__table__.columns}
        row.update(period=period, publish_date=publish_date)
        row.update(values)
        return row

    @staticmethod
    def _execute(rows, as_of_date=None):
        engine = sa.create_engine("sqlite://")
        MacroEconomy.__table__.create(engine)
        with engine.begin() as conn:
            conn.execute(MacroEconomy.__table__.insert(), rows)
        with engine.connect() as conn:
            return conn.execute(_build_macro_economy_latest_stmt(as_of_date)).mappings().all()

    def test_returns_latest_monthly_and_latest_visible_gdp(self):
        """验收：两条新月度 + 一条旧但可见 GDP → 返回最新月度与 GDP。"""
        rows = [
            self._row(
                datetime.date(2024, 4, 30),
                datetime.date(2024, 4, 20),
                gdp=100.0,
                gdp_yoy=5.2,
                pi_yoy=3.1,
                si_yoy=5.0,
                ti_yoy=5.8,
            ),
            self._row(datetime.date(2024, 5, 1), datetime.date(2024, 5, 16), m2_yoy=8.5, cpi=0.2, ppi=-1.5),
            self._row(datetime.date(2024, 6, 1), datetime.date(2024, 6, 16), m2_yoy=8.4, cpi=0.3, ppi=-1.4),
        ]

        result = self._execute(rows, datetime.date(2024, 6, 20))

        assert len(result) == 2
        by_period = {row["period"]: row for row in result}
        assert set(by_period) == {datetime.date(2024, 4, 30), datetime.date(2024, 6, 1)}

        gdp = by_period[datetime.date(2024, 4, 30)]
        assert float(gdp["gdp_yoy"]) == pytest.approx(5.2)
        assert float(gdp["pi_yoy"]) == pytest.approx(3.1)
        # 值不得互填：GDP 行不带月度字段
        assert gdp["m2_yoy"] is None
        assert gdp["cpi"] is None
        assert gdp["ppi"] is None

        monthly = by_period[datetime.date(2024, 6, 1)]
        assert float(monthly["m2_yoy"]) == pytest.approx(8.4)
        assert float(monthly["cpi"]) == pytest.approx(0.3)
        assert float(monthly["ppi"]) == pytest.approx(-1.4)
        assert monthly["gdp_yoy"] is None
        assert monthly["pi_yoy"] is None

    def test_as_of_before_gdp_publish_excludes_gdp(self):
        """as_of 早于 GDP 发布日 → 无 GDP（即使月度行同样不可见则整体为空）。"""
        rows = [
            self._row(datetime.date(2024, 4, 30), datetime.date(2024, 4, 20), gdp_yoy=5.2),
            self._row(datetime.date(2024, 5, 1), datetime.date(2024, 5, 16), m2_yoy=8.5),
        ]

        assert len(self._execute(rows, datetime.date(2024, 4, 19))) == 0

        # GDP 已发布但月度尚未发布：只返回 GDP 行，不得用月度值补 GDP
        result = self._execute(rows, datetime.date(2024, 5, 1))
        assert len(result) == 1
        assert result[0]["period"] == datetime.date(2024, 4, 30)
        assert float(result[0]["gdp_yoy"]) == pytest.approx(5.2)
        assert result[0]["m2_yoy"] is None

    def test_monthly_missing_returns_gdp_only(self):
        rows = [self._row(datetime.date(2024, 4, 30), datetime.date(2024, 4, 20), gdp_yoy=5.2)]
        result = self._execute(rows, datetime.date(2024, 6, 20))
        assert len(result) == 1
        assert result[0]["period"] == datetime.date(2024, 4, 30)
        assert float(result[0]["gdp_yoy"]) == pytest.approx(5.2)
        assert result[0]["m2_yoy"] is None

    def test_gdp_missing_returns_latest_monthly_only(self):
        rows = [
            self._row(datetime.date(2024, 5, 1), datetime.date(2024, 5, 16), m2_yoy=8.5),
            self._row(datetime.date(2024, 6, 1), datetime.date(2024, 6, 16), m2_yoy=8.4),
        ]
        result = self._execute(rows, datetime.date(2024, 6, 20))
        assert len(result) == 1
        assert result[0]["period"] == datetime.date(2024, 6, 1)
        assert float(result[0]["m2_yoy"]) == pytest.approx(8.4)
        assert result[0]["gdp_yoy"] is None

    def test_same_publish_date_uses_period_desc_as_tiebreaker(self):
        """同 publish_date 时两个分支均按 period 稳定降序取最新一行。"""
        rows = [
            self._row(datetime.date(2024, 5, 1), datetime.date(2024, 6, 16), m2_yoy=8.0),
            self._row(datetime.date(2024, 6, 1), datetime.date(2024, 6, 16), m2_yoy=8.4),
            self._row(datetime.date(2024, 3, 31), datetime.date(2024, 7, 20), gdp_yoy=5.0),
            self._row(datetime.date(2024, 6, 30), datetime.date(2024, 7, 20), gdp_yoy=5.3),
        ]
        result = self._execute(rows, datetime.date(2024, 7, 31))
        by_period = {row["period"]: row for row in result}
        assert set(by_period) == {datetime.date(2024, 6, 1), datetime.date(2024, 6, 30)}
        assert float(by_period[datetime.date(2024, 6, 1)]["m2_yoy"]) == pytest.approx(8.4)
        assert float(by_period[datetime.date(2024, 6, 30)]["gdp_yoy"]) == pytest.approx(5.3)

    def test_legal_zero_and_partial_monthly_columns_are_kept(self):
        """合法零值不算缺失；月度缺某列（仅 cpi）不应被整体剔除。"""
        rows = [
            self._row(datetime.date(2024, 4, 30), datetime.date(2024, 4, 20), gdp_yoy=0.0),
            self._row(datetime.date(2024, 6, 1), datetime.date(2024, 6, 16), cpi=0.2),
        ]
        result = self._execute(rows, datetime.date(2024, 6, 20))
        assert len(result) == 2
        by_period = {row["period"]: row for row in result}
        assert float(by_period[datetime.date(2024, 4, 30)]["gdp_yoy"]) == pytest.approx(0.0)
        assert float(by_period[datetime.date(2024, 6, 1)]["cpi"]) == pytest.approx(0.2)
        # 不得用 0 或他行值补缺失字段
        assert by_period[datetime.date(2024, 6, 1)]["m2_yoy"] is None
        assert by_period[datetime.date(2024, 4, 30)]["cpi"] is None

    def test_null_publish_date_rows_are_excluded(self):
        """DAT-06：publish_date 为 NULL 的行不可见（保持既有 PIT 口径）。"""
        rows = [
            self._row(datetime.date(2024, 7, 1), None, m2_yoy=9.9),
            self._row(datetime.date(2024, 6, 1), datetime.date(2024, 6, 16), m2_yoy=8.4),
        ]
        result = self._execute(rows, None)
        assert len(result) == 1
        assert result[0]["period"] == datetime.date(2024, 6, 1)
        assert float(result[0]["m2_yoy"]) == pytest.approx(8.4)
