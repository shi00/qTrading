"""QuoteDao 失败身份透传测试（D12-001）。

回测路径要求「DB 失败」与「合法稀疏空日」可区分：DAO 默认仍吞错返回空表（兼容既有
调用方），但回测 provider 显式传 ``suppress_errors=False`` 时必须把该开关透传给
``_read_db``，使错误得以传播而不是折叠成空 DataFrame。
"""

# pyright: reportArgumentType=false
# 本文件以 MagicMock 替身构造 DAO，pyright 无法验证替身类型兼容性。

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.quote_dao import QuoteDao

pytestmark = pytest.mark.unit


@pytest.fixture
def dao() -> QuoteDao:
    dao = QuoteDao(MagicMock(spec=AsyncEngine))
    dao._read_db = AsyncMock(return_value=pd.DataFrame())
    return dao


class TestSuppressErrorsPropagation:
    @pytest.mark.asyncio
    async def test_range_queries_pass_suppress_errors_false(self, dao: QuoteDao) -> None:
        await dao.get_top_list_range("20240101", "20240131", suppress_errors=False)
        await dao.get_block_trade_range("20240101", "20240131", suppress_errors=False)
        await dao.get_moneyflow_range("20240101", "20240131", suppress_errors=False)
        await dao.get_northbound_range("20240101", "20240131", suppress_errors=False)

        assert dao._read_db.await_count == 4
        for call in dao._read_db.await_args_list:
            assert call.kwargs["suppress_errors"] is False

    @pytest.mark.asyncio
    async def test_daily_queries_pass_suppress_errors_false(self, dao: QuoteDao) -> None:
        await dao.get_top_list(trade_date="20240102", suppress_errors=False)
        await dao.get_block_trade(trade_date="20240102", suppress_errors=False)
        await dao.get_moneyflow(trade_date="20240102", suppress_errors=False)
        await dao.get_northbound(trade_date="20240102", suppress_errors=False)

        assert dao._read_db.await_count == 4
        for call in dao._read_db.await_args_list:
            assert call.kwargs["suppress_errors"] is False

    @pytest.mark.asyncio
    async def test_default_keeps_swallowing_errors(self, dao: QuoteDao) -> None:
        """既有调用方默认行为不变：仍按 suppress_errors=True 吞错返回空表。"""
        await dao.get_top_list_range("20240101", "20240131")

        assert dao._read_db.await_args.kwargs["suppress_errors"] is True
