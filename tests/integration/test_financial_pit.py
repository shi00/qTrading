r"""财报 PIT（Point-in-Time）查询测试：财报更正不泄露。

DATA-05：``financial_reports`` 主键扩展为 ``(ts_code, end_date, ann_date)`` 后，
同一报告期（``end_date``）保留多个公告版本（``ann_date``）。本测试验证：

- PIT 查询（``as_of_date``）：更正公告日之前返回原始版本，更正公告日之后返回更正版本，
  确保回测不会在更正前就使用更正后的数值（前视偏差）；
- 非 as_of（最新）查询：返回最新公告版本，不因历史版本而分叉。

设计：使用测试专有 ``ts_code``（``999998.SZ``）自包含插入原始/更正两个版本，
teardown 定向清理，与 MVD（``000001.SZ``）及 DAT-06 用例（``999999.SZ``）无冲突。
参考 DAT-06 的 ``setup_ann_date_null_rows`` 造数模式（\`\`tests/integration/test_financial_dao.py\`\`）。
"""

import datetime

import pytest
import pytest_asyncio
from sqlalchemy import text

from data.persistence.daos.financial_dao import FinancialDao

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def setup_restatement_versions(function_engine):
    """为同一 ``(ts_code, end_date)`` 插入原始/更正两个公告版本，teardown 定向清理。

    业务语义（DATA-05）：某公司 2024 中报于 2024-07-01 首次披露（roe=10.0），
    2024-09-15 更正（roe=12.0）。更正前只能看到原始值。
    """
    async with function_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO financial_reports (ts_code, end_date, ann_date, report_type, roe, total_revenue) "
                "VALUES ('999998.SZ', '2024-06-30', '2024-07-01', '1', 10.0, 100000000)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO financial_reports (ts_code, end_date, ann_date, report_type, roe, total_revenue) "
                "VALUES ('999998.SZ', '2024-06-30', '2024-09-15', '1', 12.0, 120000000)"
            )
        )
    yield
    async with function_engine.begin() as conn:
        await conn.execute(text("DELETE FROM financial_reports WHERE ts_code = '999998.SZ'"))


@pytest_asyncio.fixture
async def setup_multi_period_versions(function_engine):
    """插入 2 个报告期、各 2 个公告版本，teardown 定向清理。

    用于验证 batch 查询按「不同报告期」计数：多版本不会膨胀报告期数
    （rn_period 须用 DENSE_RANK，而非 ROW_NUMBER）。
    """
    async with function_engine.begin() as conn:
        rows = [
            # 报告期 1（最新）：原始 + 更正
            ("999998.SZ", "2024-12-31", "2025-01-15", "1", 20.0),
            ("999998.SZ", "2024-12-31", "2025-02-20", "1", 22.0),
            # 报告期 2（次新）：原始 + 更正
            ("999998.SZ", "2024-09-30", "2024-10-25", "1", 15.0),
            ("999998.SZ", "2024-09-30", "2024-11-10", "1", 16.0),
        ]
        for ts_code, end_date, ann_date, report_type, roe in rows:
            await conn.execute(
                text(
                    "INSERT INTO financial_reports (ts_code, end_date, ann_date, report_type, roe) "
                    "VALUES (:ts_code, :end_date, :ann_date, :report_type, :roe)"
                ),
                {
                    "ts_code": ts_code,
                    "end_date": end_date,
                    "ann_date": ann_date,
                    "report_type": report_type,
                    "roe": roe,
                },
            )
    yield
    async with function_engine.begin() as conn:
        await conn.execute(text("DELETE FROM financial_reports WHERE ts_code = '999998.SZ'"))


class TestFinancialReportsPit:
    """DATA-05：财报多版本 PIT 查询——更正前后不泄露（前视偏差防护）。"""

    @pytest.fixture
    def financial_dao(self, function_engine):
        return FinancialDao(function_engine)

    @pytest.mark.asyncio
    async def test_single_as_of_before_restatement_returns_original(self, financial_dao, setup_restatement_versions):
        """更正前 as_of：应返回原始版本（ann_date=2024-07-01, roe=10.0）。"""
        df = await financial_dao.get_financial_reports_history(
            "999998.SZ", periods=8, as_of_date=datetime.date(2024, 8, 1)
        )
        assert len(df) == 1
        assert df["end_date"].iloc[0] == datetime.date(2024, 6, 30)
        assert df["ann_date"].iloc[0] == datetime.date(2024, 7, 1)
        assert df["roe"].iloc[0] == 10.0

    @pytest.mark.asyncio
    async def test_single_as_of_after_restatement_returns_latest(self, financial_dao, setup_restatement_versions):
        """更正后 as_of：应返回更正版本（ann_date=2024-09-15, roe=12.0）。"""
        df = await financial_dao.get_financial_reports_history(
            "999998.SZ", periods=8, as_of_date=datetime.date(2024, 12, 31)
        )
        assert len(df) == 1
        assert df["end_date"].iloc[0] == datetime.date(2024, 6, 30)
        assert df["ann_date"].iloc[0] == datetime.date(2024, 9, 15)
        assert df["roe"].iloc[0] == 12.0

    @pytest.mark.asyncio
    async def test_single_latest_returns_newest_version(self, financial_dao, setup_restatement_versions):
        """非 as_of（最新）：返回最新公告版本，不因历史版本存在而分叉。"""
        df = await financial_dao.get_financial_reports_history("999998.SZ", periods=8)
        assert len(df) == 1
        assert df["end_date"].iloc[0] == datetime.date(2024, 6, 30)
        assert df["ann_date"].iloc[0] == datetime.date(2024, 9, 15)
        assert df["roe"].iloc[0] == 12.0

    @pytest.mark.asyncio
    async def test_batch_as_of_before_restatement_returns_original(self, financial_dao, setup_restatement_versions):
        """batch 版本 as_of（更正前）：每报告期仅返回最新可见一版（无笛卡尔积）。"""
        asof = await financial_dao.get_financial_reports_history_batch(
            ["999998.SZ"], periods=8, as_of_date=datetime.date(2024, 8, 1)
        )
        assert len(asof) == 1
        assert asof["end_date"].iloc[0] == datetime.date(2024, 6, 30)
        assert asof["ann_date"].iloc[0] == datetime.date(2024, 7, 1)
        assert asof["roe"].iloc[0] == 10.0

    @pytest.mark.asyncio
    async def test_batch_latest_returns_newest_version(self, financial_dao, setup_restatement_versions):
        """batch 版本非 as_of：返回最新公告版本，每报告期一行。"""
        latest = await financial_dao.get_financial_reports_history_batch(["999998.SZ"], periods=8)
        assert len(latest) == 1
        assert latest["end_date"].iloc[0] == datetime.date(2024, 6, 30)
        assert latest["ann_date"].iloc[0] == datetime.date(2024, 9, 15)
        assert latest["roe"].iloc[0] == 12.0
