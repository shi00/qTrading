"""
财务数据完整性测试

测试 Phase 0.5: DAO 分层修复
测试 Phase 1: AI Prompt 数据注入增强
- F1: n_cashflow_act 字段
- L2: 批量预取避免 N+1 查询
"""

from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest
from sqlalchemy import text

from data.persistence.daos.financial_dao import FinancialDao

pytestmark = pytest.mark.integration


class TestFinancialDaoIntegrity:
    """测试财务数据完整性检查方法（基于 MVD 真实数据）"""

    pytestmark = pytest.mark.usefixtures("mvd_data")

    @pytest.fixture
    def financial_dao(self, function_engine):
        return FinancialDao(function_engine)

    @pytest.mark.asyncio
    async def test_get_financial_reports_history(self, financial_dao):
        """Level 2: 验证多期财报查询返回正确行数和字段值"""
        df = await financial_dao.get_financial_reports_history("000001.SZ", periods=8)

        assert df is not None
        assert len(df) == 8
        assert "roe" in df.columns
        assert "n_income_attr_p" in df.columns
        assert "n_cashflow_act" in df.columns
        # Level 2: 验证第一期与最新期 ROE 值
        # 注意：DAO 使用 ORDER BY end_date DESC，显式按 end_date ASC 排序后再断言（见约束 3）
        df_sorted = df.sort_values("end_date", ascending=True).reset_index(drop=True)
        assert df_sorted["roe"].iloc[0] == 12.5  # 第一期（2024Q1），DAT-10/11 归一化后为 float64
        assert df_sorted["roe"].iloc[-1] == 16.0  # 最新期（2025Q4）

    @pytest.mark.asyncio
    async def test_get_financial_reports_history_empty(self, financial_dao):
        """
        测试不存在的股票代码
        """
        df = await financial_dao.get_financial_reports_history("999999.SZ", periods=8)

        assert df is not None
        assert df.empty

    @pytest.mark.asyncio
    async def test_get_fina_audit_batch(self, financial_dao):
        """Level 2: 验证批量审计意见查询"""
        ts_codes = ["000001.SZ", "000002.SZ", "600000.SH"]
        df = await financial_dao.get_fina_audit_batch(ts_codes)

        assert df is not None
        assert not df.empty
        assert "ts_code" in df.columns
        assert "audit_result" in df.columns
        # Level 2: 验证 000001.SZ 的审计意见（DISTINCT ON (ts_code) 仅返回 1 条）
        row = df[df["ts_code"] == "000001.SZ"]
        assert len(row) == 1
        assert row["audit_result"].iloc[0] == "标准无保留意见"

    @pytest.mark.asyncio
    async def test_get_fina_audit_batch_empty(self, financial_dao):
        """
        测试空股票列表
        """
        df = await financial_dao.get_fina_audit_batch([])

        assert df is not None
        assert df.empty

    @pytest.mark.asyncio
    async def test_get_dividend_batch(self, financial_dao):
        """Level 2: 验证批量分红记录查询"""
        ts_codes = ["000001.SZ", "000002.SZ", "600000.SH"]
        df = await financial_dao.get_dividend_batch(ts_codes)

        assert df is not None
        assert not df.empty
        assert "ts_code" in df.columns
        # Level 2: 验证 000001.SZ 有分红记录
        row = df[df["ts_code"] == "000001.SZ"]
        assert len(row) >= 1

    @pytest.mark.asyncio
    async def test_get_pledge_stat_batch(self, financial_dao):
        """Level 2: 验证批量质押比例查询"""
        ts_codes = ["000001.SZ", "000002.SZ", "600000.SH"]
        df = await financial_dao.get_pledge_stat_batch(ts_codes)

        assert df is not None
        assert not df.empty
        assert "ts_code" in df.columns
        assert "pledge_ratio" in df.columns
        # Level 2: 验证 000001.SZ 的质押比例（DISTINCT ON (ts_code) 仅返回 1 条）
        row = df[df["ts_code"] == "000001.SZ"]
        assert len(row) == 1
        assert row["pledge_ratio"].iloc[0] == 10.5

    @pytest.mark.asyncio
    async def test_get_fina_mainbz(self, financial_dao):
        """Level 2: 验证主营业务构成查询"""
        df = await financial_dao.get_fina_mainbz("000001.SZ")

        assert df is not None
        assert not df.empty
        assert "bz_item" in df.columns
        assert "bz_sales" in df.columns
        # Level 2: 验证主营业务项（MVD 仅 1 条，iloc[0] 即该条）
        assert df["bz_item"].iloc[0] == "利息收入"


class TestFinancialDaoBatchPerformance:
    """批量查询性能测试"""

    @pytest.fixture
    def financial_dao(self, test_engine):
        return FinancialDao(test_engine)

    @pytest.mark.asyncio
    async def test_batch_vs_individual_queries(self, financial_dao):
        """
        性能测试：批量查询 vs 单独查询

        批量查询应该只执行 1 次 SQL，而非 N 次
        """
        call_count = 0

        async def count_calls(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "audit_result": ["标准无保留意见"],
                    "audit_sign": ["签字会计师"],
                    "audit_fees": [1000000],
                    "audit_agency": ["审计机构"],
                }
            )

        with patch.object(financial_dao, "_read_db", new_callable=AsyncMock, side_effect=count_calls):
            ts_codes = ["000001.SZ", "000002.SZ", "600000.SH"]
            await financial_dao.get_fina_audit_batch(ts_codes)

            assert call_count == 1, f"Expected 1 DB call, got {call_count}"


class TestFinancialDaoEdgeCases:
    """边界条件测试"""

    @pytest.fixture
    def financial_dao(self, test_engine):
        return FinancialDao(test_engine)

    @pytest.mark.asyncio
    async def test_db_error_handling(self, financial_dao):
        """
        测试数据库错误处理
        """
        with patch.object(
            financial_dao,
            "_read_db",
            new_callable=AsyncMock,
            side_effect=Exception("DB Error"),
        ):
            df = await financial_dao.get_financial_reports_history("000001.SZ", periods=8)

            assert df is not None
            assert df.empty

    @pytest.mark.asyncio
    async def test_none_result_handling(self, financial_dao):
        """
        测试 None 结果处理
        """
        with patch.object(
            financial_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=None,
        ):
            df = await financial_dao.get_financial_reports_history("000001.SZ", periods=8)

            assert df is not None
            assert df.empty

    @pytest.mark.asyncio
    async def test_empty_dataframe_handling(self, financial_dao):
        """
        测试空 DataFrame 处理
        """
        with patch.object(
            financial_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=pd.DataFrame(),
        ):
            df = await financial_dao.get_financial_reports_history("000001.SZ", periods=8)

            assert df is not None
            assert df.empty


class TestCashflowField:
    """n_cashflow_act 字段测试 (F1 修复)"""

    @pytest.fixture
    def financial_dao(self, test_engine):
        return FinancialDao(test_engine)

    @pytest.mark.asyncio
    async def test_cashflow_field_in_history(self, financial_dao):
        """
        F1 测试：验证 n_cashflow_act 字段存在于查询结果中
        """
        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20231231"],
                "roe": [12.5],
                "n_income_attr_p": [50000000],
                "n_cashflow_act": [100000000],
            }
        )

        with patch.object(
            financial_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=mock_df,
        ):
            df = await financial_dao.get_financial_reports_history("000001.SZ", periods=8)

            assert "n_cashflow_act" in df.columns
            assert df["n_cashflow_act"].iloc[0] == 100000000


class TestDat06AnnDateNull:
    """DAT-06: ann_date 缺失防护在 DATA-05 后由 NOT NULL 主键在 schema 层杜绝。

    DATA-05（0022 迁移）将 ``ann_date`` 纳入主键，PG 主键列隐式 NOT NULL，
    插入 ``ann_date IS NULL`` 的行会违反约束直接失败——DAT-06 的 NULL 行告警场景
    在 schema 层不再可能构造。因此本类从"插入 NULL 行验证查询排除"改为验证：
      - schema 拒绝 NULL ann_date 行（插入抛约束异常）；
      - 干净 MVD 下 ``has_ann_date_nulls()`` 恒为 False。

    xdist_group("serial")：``has_ann_date_nulls()`` 为全表 EXISTS 扫描，保留串行
    锁定避免并行 worker 间相互污染（仓库既有模式）。
    """

    pytestmark = pytest.mark.xdist_group("serial")

    @pytest.fixture
    def financial_dao(self, function_engine):
        return FinancialDao(function_engine)

    @pytest.mark.asyncio
    async def test_insert_ann_date_null_rejected(self, financial_dao, function_engine):
        """DATA-05: 插入 ann_date NULL 的行违反 NOT NULL 主键约束，被 PostgreSQL 拒绝。

        验证 DAT-06 的 NULL 场景已由 schema 层兜底：未来任何绕过应用的裸 SQL 写入
        也会被约束拦截，PIT 双分支口径不会因 NULL 行分叉。
        """
        import asyncpg
        import sqlalchemy.exc as sa_exc

        # SQLAlchemy 引擎会把 asyncpg 驱动异常包装为 sqlalchemy.exc.IntegrityError，
        # 原始 asyncpg 异常可通过 .orig 取回——捕获包装后的异常并断言原始类型，
        # 否则异常逃逸导致测试失败（CI PR-880 已复现）。
        with pytest.raises(sa_exc.IntegrityError) as excinfo:
            async with function_engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO financial_reports (ts_code, end_date, ann_date, roe) "
                        "VALUES ('999999.SZ', '2024-12-31', NULL, 20.0)"
                    )
                )

        assert isinstance(excinfo.value.orig, asyncpg.NotNullViolationError)

    @pytest.mark.asyncio
    async def test_has_ann_date_nulls_false_on_clean_mvd(self, financial_dao):
        """DATA-05: 干净 MVD（所有 ann_date 非空）下返回 False。

        依赖隐式约定：MVD 造数（mvd_data.py MVD_FINANCIAL_REPORTS）不得含 ann_date NULL 行，
        否则本测试会假失败——新增 NULL 行造数时需同步更新本断言。
        """
        assert await financial_dao.has_ann_date_nulls() is False
