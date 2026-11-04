# pyright: reportAttributeAccessIssue=false, reportArgumentType=false
# 本文件用 MagicMock 替身 AsyncEngine 并 monkey-patch DAO 读写方法，pyright 无法验证替身兼容性。

"""D06-T01：PIT 时点条件的单元层证据（查询构造 / mock DAO）。

本文件只验证「as-of 条件被正确构造并作为参数传给数据库」，不伪造数据库行级语义：

- 财报可见性（``ann_date <= as_of`` 的包含语义与参数位）在单元层只能证明条件被原样下发；
  真实反例（报告期 2025-12-31、ann_date=2026-03-31、决策日 2026-03-30 不可见）需要
  真实 PostgreSQL 执行，属**受环境阻塞的补证项**，不得以本文件断言替代。
- 退市票池同理：``stock_alive_condition`` 仅验证「as-of 早于退市日则存活、晚于则排除」的
  SQL 结构；delist_date == as_of 当日是否算最后交易日取决于供应商口径，本文件不作判定。
- 行业/指数成员生效期需要真实成员表按 in_date/out_date 逐日取数，同属阻塞项。
"""

import datetime
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.financial_dao import FinancialDao
from data.persistence.daos.stock_dao import stock_alive_condition

pytestmark = pytest.mark.unit


def _make_dao() -> FinancialDao:
    dao = FinancialDao(MagicMock(spec=AsyncEngine))
    dao._read_db = AsyncMock(return_value=pd.DataFrame())
    return dao


class TestFinancialAsOfBoundary:
    """财报 as-of 过滤：决策日必须原样下发，且与 ann_date 比较为包含（``<=``）。"""

    @pytest.mark.asyncio
    async def test_decision_day_before_announcement_passes_inclusive_as_of(self):
        """决策日 2026-03-30 作为 as_of 原样下发，SQL 用 ``ann_date <= $2``（无位移/无排除）。"""
        dao = _make_dao()
        await dao.get_financial_reports_history("000001.SZ", as_of_date="20260330")

        sql = dao._read_db.call_args[0][0]
        params = dao._read_db.call_args[0][1]
        assert "ann_date IS NOT NULL AND ann_date <= $2" in sql
        assert params == ("000001.SZ", datetime.date(2026, 3, 30), 8)

    @pytest.mark.asyncio
    async def test_batch_as_of_param_sits_after_code_placeholders(self):
        """批量模板的 as_of 参数位必须紧跟股票代码占位符（$3），防止错位绑定。"""
        dao = _make_dao()
        await dao.get_financial_reports_history_batch(["000001.SZ", "000002.SZ"], as_of_date="20260330")

        sql = dao._read_db.call_args[0][0]
        params = dao._read_db.call_args[0][1]
        assert "ann_date IS NOT NULL AND ann_date <= $3" in sql
        assert params == ["000001.SZ", "000002.SZ", datetime.date(2026, 3, 30), 8]

    @pytest.mark.asyncio
    async def test_no_as_of_keeps_ann_date_not_null_only(self):
        """``as_of=None`` 不得残留 ``ann_date <=`` 条件（非历史模式不做时点截断）。"""
        dao = _make_dao()
        await dao.get_financial_reports_history("000001.SZ", as_of_date=None)

        sql = dao._read_db.call_args[0][0]
        assert "ann_date IS NOT NULL" in sql
        assert "ann_date <=" not in sql


class TestStockAliveConditionAsOf:
    """票池存活判定：as-of 早于退市日纳入历史、晚于退市日排除（SQL 结构层）。"""

    def test_live_stock_allows_null_or_later_delist_date(self):
        sql = stock_alive_condition(alias="b.", as_of="$5")
        assert "b.list_status = 'L'" in sql
        assert "b.delist_date IS NULL" in sql
        assert "b.delist_date > $5" in sql

    def test_delisted_stock_requires_non_null_and_later_delist_date(self):
        sql = stock_alive_condition(alias="b.", as_of="$5")
        assert "b.list_status = 'D'" in sql
        assert "b.delist_date IS NOT NULL" in sql
        assert "b.delist_date > $5" in sql

    def test_as_of_placeholder_rendered_per_caller(self):
        """单日/区间调用方使用不同 as-of 占位符，条件必须各自渲染（禁止内联复制）。"""
        single = stock_alive_condition(alias="b.", as_of="$5")
        ranged = stock_alive_condition(alias="b.", as_of="cal.cal_date")
        assert "b.delist_date > $5" in single
        assert "cal.cal_date" in ranged
        assert "$5" not in ranged
