import ast
import inspect
import re
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest
from core.i18n import I18n
from services.ai_service import (
    AIService,
    AVAILABLE_DATA_LABELS,
    build_available_data_block,
)
from strategies.ai_mixin import AIStrategyMixin, PreFetchedContext
from strategies.strategy_prompts import STRATEGY_PROMPTS, FORBIDDEN_STATIC_HEADERS

pytestmark = pytest.mark.unit

# v5→修订点5：命名常量，用于辅助扫描编号式/项目符号式数据枚举
ENUMERATION_PATTERN = re.compile(
    r"(?:[\d]+[\.、)]\s*(?!\s*若)(.{2,20})(?:数据|指标|信息|材料))"
    r"|(?:[-•]\s+(.{2,20})(?:数据|指标|信息|材料))"
)


class TestInvariant1NoStaticEnumeration:
    def test_prompts_have_runtime_data_boundary(self):
        for key, prompt in STRATEGY_PROMPTS.items():
            assert "【数据边界】" in prompt or "available_data" in prompt, (
                f"{key} 缺少运行时清单引用指令（【数据边界】或 available_data）"
            )

    def test_prompts_no_forbidden_headers(self):
        for key, prompt in STRATEGY_PROMPTS.items():
            for header in FORBIDDEN_STATIC_HEADERS:
                assert header not in prompt, f"{key} 仍含静态数据枚举表头「{header}」"

    def test_prompts_no_numbered_enumeration_before_boundary(self):
        for key, prompt in STRATEGY_PROMPTS.items():
            if "【数据边界】" in prompt:
                pre_boundary = prompt.split("【数据边界】")[0]
                assert not ENUMERATION_PATTERN.search(pre_boundary), f"{key} 在【数据边界】之前仍含编号式数据枚举"


class TestInvariant2LabelsSubsetOfRegistry:
    def test_all_registered_keys_have_i18n(self):
        for label_key in AVAILABLE_DATA_LABELS:
            translated = I18n.get(label_key)
            assert translated and translated != label_key, f"Label key '{label_key}' has no i18n translation"

    def test_builder_labels_subset_of_registry(self):
        # 所有运行时注册的标签 key（builder 子项 + 块级标签）
        builder_labels = {
            # _build_financials_text 子项
            "ai_label_valuation",
            # _build_multi_period_financials 子项
            "ai_label_roe_trend",
            "ai_label_gross_margin_trend",
            "ai_label_revenue_growth_trend",
            "ai_label_profit_growth_trend",
            "ai_label_cf_profit_ratio",
            "ai_label_goodwill_ratio",
            "ai_label_monetary_capital",
            "ai_label_accounts_receiv",
            # _build_auxiliary_data_text 子项
            "ai_label_audit",
            "ai_label_main_business",
            "ai_label_dividend",
            "ai_label_pledge",
            "ai_label_pledge_detail",
            "ai_label_top_holder",
            "ai_label_holder_count",
            "ai_label_forecast",
            # Phase 3D：限售解禁（share_float API，points_5000）
            "ai_label_share_float",
            # Phase 3E：股东增减持（stk_holdertrade API，points_2000）
            "ai_label_holder_trade",
            # _build_capital_flow_text 子项
            "ai_label_main_flow",
            "ai_label_top_list",
            "ai_label_northbound",
            # Phase 3C：龙虎榜机构席位（top_inst API，points_2000）
            "ai_label_top_inst",
            # _build_history_text 子项
            "ai_label_kline",
            # _mixin_analyze_single 块级标签（Phase 2A.1 §4.1：ai_label_macro 拆分为 shibor + macro_full）
            "ai_label_shibor",
            "ai_label_macro_full",
        }
        assert builder_labels.issubset(AVAILABLE_DATA_LABELS), (
            f"Builder labels not in registry: {builder_labels - AVAILABLE_DATA_LABELS}"
        )

    def test_ast_scan_labels_out_appends_covered(self):
        """辅助扫描层（v5→修订点5）：通过 AST 自动扫描 ai_mixin.py 中所有
        labels_out.append("ai_label_xxx") 调用，确保硬编码的 builder_labels
        集合覆盖了源码中的所有标签注册点。若未来新增 labels_out.append
        但忘记更新 builder_labels 和 AVAILABLE_DATA_LABELS，此测试会失败。"""
        source = inspect.getsource(AIStrategyMixin)
        tree = ast.parse(source)

        # 收集所有 labels_out.append("ai_label_xxx") 中的字符串字面量
        ast_labels: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # 匹配 xxx.append(...) 且 xxx 是 labels_out 或 *_labels_out
            if not (isinstance(func, ast.Attribute) and func.attr == "append"):
                continue
            if isinstance(func.value, ast.Name) and "labels_out" in func.value.id:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        ast_labels.add(arg.value)

        # AST 扫描出的标签必须全部在 AVAILABLE_DATA_LABELS 中
        missing = ast_labels - AVAILABLE_DATA_LABELS
        assert not missing, (
            f"AST 扫描到 ai_mixin.py 中 labels_out.append 注册了以下标签，"
            f"但未在 AVAILABLE_DATA_LABELS 中声明: {missing}"
        )

        # AST 扫描出的标签也必须全部在 builder_labels 硬编码集合中
        # （反向验证：若 builder_labels 漏了某个 AST 发现的标签，说明测试覆盖不足）
        builder_labels = {
            "ai_label_valuation",
            "ai_label_roe_trend",
            "ai_label_gross_margin_trend",
            "ai_label_revenue_growth_trend",
            "ai_label_profit_growth_trend",
            "ai_label_cf_profit_ratio",
            "ai_label_goodwill_ratio",
            "ai_label_monetary_capital",
            "ai_label_accounts_receiv",
            "ai_label_audit",
            "ai_label_main_business",
            "ai_label_dividend",
            "ai_label_pledge",
            "ai_label_pledge_detail",
            "ai_label_top_holder",
            "ai_label_holder_count",
            "ai_label_forecast",
            # Phase 3D：限售解禁（share_float API，points_5000）
            "ai_label_share_float",
            # Phase 3E：股东增减持（stk_holdertrade API，points_2000）
            "ai_label_holder_trade",
            "ai_label_main_flow",
            "ai_label_top_list",
            "ai_label_northbound",
            # Phase 3C：龙虎榜机构席位（top_inst API，points_2000）
            "ai_label_top_inst",
            "ai_label_kline",
            "ai_label_shibor",
            "ai_label_macro_full",
        }
        uncovered = ast_labels - builder_labels
        assert not uncovered, (
            f"AST 扫描到 ai_mixin.py 中 labels_out.append 注册了以下标签，"
            f"但未在 test_builder_labels_subset_of_registry 的 builder_labels 中覆盖: {uncovered}"
        )


class TestInvariant3BuilderLabelEquivalence:
    @pytest.mark.asyncio
    async def test_multi_period_financials_labels_match_text(self):
        mixin = AIStrategyMixin()
        mixin.cache = type("FakeCache", (), {})()
        mixin.cache.get_financial_reports_history = _async_return(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"] * 2,
                    "roe": [15.0, 14.0],
                    "grossprofit_margin": [30.0, 28.0],
                    "or_yoy": [10.0, 8.0],
                    "netprofit_yoy": [12.0, 9.0],
                    "n_cashflow_act": [5e8, 4e8],
                    "n_income_attr_p": [3e8, 2.5e8],
                    "total_assets": [1e10, 9e9],
                    "goodwill": [5e8, 4e8],
                    "money_cap": [2e9, 1.8e9],
                    "accounts_receiv": [1e9, 9e8],
                }
            )
        )

        labels: list[str] = []
        result = await mixin._build_multi_period_financials(
            "000001.SZ",
            mixin.cache,
            labels_out=labels,
        )

        expected_labels = {
            "ai_label_roe_trend",
            "ai_label_gross_margin_trend",
            "ai_label_revenue_growth_trend",
            "ai_label_profit_growth_trend",
            "ai_label_cf_profit_ratio",
            "ai_label_goodwill_ratio",
            "ai_label_monetary_capital",
            "ai_label_accounts_receiv",
        }
        assert set(labels) == expected_labels, (
            f"Expected {expected_labels}, got {set(labels)}. "
            f"Missing: {expected_labels - set(labels)}, Extra: {set(labels) - expected_labels}"
        )
        for label_key in labels:
            assert label_key in AVAILABLE_DATA_LABELS, (
                f"Label '{label_key}' registered by builder but not in AVAILABLE_DATA_LABELS"
            )
            assert I18n.get(label_key), f"Label '{label_key}' has no i18n translation"

        label_to_text_key = {
            "ai_label_roe_trend": "ai_roe_trend",
            "ai_label_gross_margin_trend": "ai_gross_margin_trend",
            "ai_label_revenue_growth_trend": "ai_revenue_growth_trend",
            "ai_label_profit_growth_trend": "ai_profit_growth_trend",
            "ai_label_cf_profit_ratio": "ai_cf_profit_ratio",
            "ai_label_goodwill_ratio": "ai_goodwill_ratio",
            "ai_label_monetary_capital": "ai_monetary_capital",
            "ai_label_accounts_receiv": "ai_accounts_receiv",
        }
        for label_key in labels:
            text_key = label_to_text_key[label_key]
            assert I18n.get(text_key) in result[0], (
                f"Label '{label_key}' registered but corresponding text key '{text_key}' not in result"
            )

    @pytest.mark.asyncio
    async def test_multi_period_financials_empty_df_no_labels(self):
        mixin = AIStrategyMixin()
        mixin.cache = type("FakeCache", (), {})()
        mixin.cache.get_financial_reports_history = _async_return(pd.DataFrame())

        labels: list[str] = []
        result = await mixin._build_multi_period_financials(
            "000001.SZ",
            mixin.cache,
            labels_out=labels,
        )
        assert result == ("", False)
        assert labels == []

    @pytest.mark.asyncio
    async def test_multi_period_financials_none_df_no_labels(self):
        mixin = AIStrategyMixin()
        mixin.cache = type("FakeCache", (), {})()
        mixin.cache.get_financial_reports_history = _async_return(None)

        labels: list[str] = []
        result = await mixin._build_multi_period_financials(
            "000001.SZ",
            mixin.cache,
            labels_out=labels,
        )
        assert result == ("", False)
        assert labels == []

    @pytest.mark.asyncio
    async def test_multi_period_financials_exception_clears_labels(self):
        mixin = AIStrategyMixin()
        mixin.cache = type("FakeCache", (), {})()
        mixin.cache.get_financial_reports_history = _async_raise(RuntimeError("DB error"))

        labels: list[str] = []
        result = await mixin._build_multi_period_financials(
            "000001.SZ",
            mixin.cache,
            labels_out=labels,
        )
        assert result == ("", False)
        assert labels == []

    @pytest.mark.asyncio
    async def test_multi_period_financials_all_na_no_labels(self):
        """全 NaN DataFrame：dropna() 清空所有值 → 返回哨兵，不注册标签。"""
        mixin = AIStrategyMixin()
        mixin.cache = type("FakeCache", (), {})()
        mixin.cache.get_financial_reports_history = _async_return(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ"],
                    "roe": [float("nan"), float("nan")],
                    "grossprofit_margin": [float("nan"), float("nan")],
                    "or_yoy": [float("nan"), float("nan")],
                    "netprofit_yoy": [float("nan"), float("nan")],
                    "n_cashflow_act": [float("nan"), float("nan")],
                    "n_income_attr_p": [float("nan"), float("nan")],
                    "total_assets": [float("nan"), float("nan")],
                    "goodwill": [float("nan"), float("nan")],
                    "money_cap": [float("nan"), float("nan")],
                    "accounts_receiv": [float("nan"), float("nan")],
                }
            )
        )

        labels: list[str] = []
        result = await mixin._build_multi_period_financials(
            "000001.SZ",
            mixin.cache,
            labels_out=labels,
        )
        assert result == ("", False)
        assert labels == []

    @pytest.mark.asyncio
    async def test_auxiliary_data_labels_match_text(self):
        mixin = AIStrategyMixin()
        mixin.cache = type("FakeCache", (), {})()
        mixin.cache.get_fina_audit = _async_return(
            pd.DataFrame({"ts_code": ["000001.SZ"], "audit_result": ["标准无保留"]})
        )
        mixin.cache.get_fina_mainbz = _async_return(
            pd.DataFrame({"ts_code": ["000001.SZ"], "bz_item": ["白酒"], "bz_sales": [1e9]})
        )
        mixin.cache.get_dividend = _async_return(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20231231"],
                    "div_proc": ["实施"],
                }
            )
        )
        mixin.cache.get_pledge_stat = _async_return(pd.DataFrame({"ts_code": ["000001.SZ"], "pledge_ratio": [15.0]}))
        mixin.cache.get_top10_holders = _async_return(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20231231"],
                    "holder_name": ["某公司"],
                    "hold_ratio": [30.0],
                }
            )
        )
        mixin.cache.get_stk_holdernumber = _async_return(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "ann_date": ["20240101"],
                    "holder_num": [100000],
                    "holder_num_ratio": [-3.5],
                }
            )
        )
        mixin.cache.get_fina_forecast = _async_return(pd.DataFrame())
        mixin.cache.get_pledge_detail = _async_return(pd.DataFrame())
        mixin.cache.get_share_float_upcoming = _async_return(pd.DataFrame())
        mixin.cache.get_stk_holdertrade = _async_return(pd.DataFrame())

        labels: list[str] = []
        result = await mixin._build_auxiliary_data_text(
            "000001.SZ",
            mixin.cache,
            labels_out=labels,
        )

        expected_labels = {
            "ai_label_audit",
            "ai_label_main_business",
            "ai_label_dividend",
            "ai_label_pledge",
            "ai_label_top_holder",
            "ai_label_holder_count",
        }
        assert set(labels) == expected_labels, (
            f"Expected {expected_labels}, got {set(labels)}. "
            f"Missing: {expected_labels - set(labels)}, Extra: {set(labels) - expected_labels}"
        )
        for label_key in labels:
            assert label_key in AVAILABLE_DATA_LABELS
            assert I18n.get(label_key)

        label_to_text_key = {
            "ai_label_audit": "ai_audit_opinion",
            "ai_label_main_business": "ai_main_business",
            "ai_label_dividend": "ai_recent_dividend",
            "ai_label_pledge": "ai_pledge_ratio",
            "ai_label_top_holder": "ai_top_holder",
            "ai_label_holder_count": "ai_holder_count",
        }
        for label_key in labels:
            text_key = label_to_text_key[label_key]
            assert I18n.get(text_key) in result[0], (
                f"Label '{label_key}' registered but corresponding text key '{text_key}' not in result"
            )

    @pytest.mark.asyncio
    async def test_auxiliary_data_no_data_no_labels(self):
        mixin = AIStrategyMixin()
        mixin.cache = type("FakeCache", (), {})()
        mixin.cache.get_fina_audit = _async_return(pd.DataFrame())
        mixin.cache.get_fina_mainbz = _async_return(pd.DataFrame())
        mixin.cache.get_dividend = _async_return(pd.DataFrame())
        mixin.cache.get_pledge_stat = _async_return(pd.DataFrame())
        mixin.cache.get_top10_holders = _async_return(pd.DataFrame())
        mixin.cache.get_stk_holdernumber = _async_return(pd.DataFrame())
        mixin.cache.get_fina_forecast = _async_return(pd.DataFrame())
        mixin.cache.get_pledge_detail = _async_return(pd.DataFrame())
        mixin.cache.get_share_float_upcoming = _async_return(pd.DataFrame())
        mixin.cache.get_stk_holdertrade = _async_return(pd.DataFrame())

        labels: list[str] = []
        result = await mixin._build_auxiliary_data_text(
            "000001.SZ",
            mixin.cache,
            labels_out=labels,
        )
        assert result == ("", False)
        assert labels == []

    @pytest.mark.asyncio
    async def test_auxiliary_data_exception_clears_labels(self):
        mixin = AIStrategyMixin()
        mixin.cache = type("FakeCache", (), {})()
        mixin.cache.get_fina_audit = _async_raise(RuntimeError("DB error"))

        labels: list[str] = []
        result = await mixin._build_auxiliary_data_text(
            "000001.SZ",
            mixin.cache,
            labels_out=labels,
        )
        assert result == ("", False)
        assert labels == []

    @pytest.mark.asyncio
    async def test_auxiliary_data_all_na_no_labels(self):
        """全 NaN 辅助数据：pledge_ratio=None → 不注册标签，返回哨兵。"""
        mixin = AIStrategyMixin()
        mixin.cache = type("FakeCache", (), {})()
        mixin.cache.get_fina_audit = _async_return(pd.DataFrame({"ts_code": ["000001.SZ"], "audit_result": [None]}))
        mixin.cache.get_fina_mainbz = _async_return(
            pd.DataFrame({"ts_code": ["000001.SZ"], "bz_item": [None], "bz_sales": [0]})
        )
        mixin.cache.get_dividend = _async_return(
            pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": [None], "div_proc": [None]})
        )
        mixin.cache.get_pledge_stat = _async_return(pd.DataFrame({"ts_code": ["000001.SZ"], "pledge_ratio": [None]}))
        mixin.cache.get_top10_holders = _async_return(
            pd.DataFrame({"ts_code": ["000001.SZ"], "holder_name": [None], "hold_ratio": [None]})
        )
        mixin.cache.get_stk_holdernumber = _async_return(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "holder_num": [None],
                    "holder_num_ratio": [None],
                }
            )
        )
        mixin.cache.get_fina_forecast = _async_return(pd.DataFrame())
        mixin.cache.get_pledge_detail = _async_return(pd.DataFrame())
        mixin.cache.get_share_float_upcoming = _async_return(pd.DataFrame())
        mixin.cache.get_stk_holdertrade = _async_return(pd.DataFrame())

        labels: list[str] = []
        result = await mixin._build_auxiliary_data_text(
            "000001.SZ",
            mixin.cache,
            labels_out=labels,
        )
        assert result == ("", False)
        assert labels == []

    def test_capital_flow_has_data_registers_labels(self):
        mf_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "buy_lg_amount": [1000],
                "sell_lg_amount": [500],
                "buy_elg_amount": [200],
                "sell_elg_amount": [100],
                "net_mf_amount": [600],
            }
        )
        tl_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "reason": ["涨停"],
                "net_amount": [5000],
            }
        )
        nb_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "vol": [100000],
                "ratio": [2.5],
            }
        )

        labels: list[str] = []
        AIStrategyMixin._build_capital_flow_text(
            "000001.SZ",
            {"moneyflow_df": mf_df, "top_list_df": tl_df, "northbound_df": nb_df},
            labels_out=labels,
        )
        assert "ai_label_main_flow" in labels
        assert "ai_label_top_list" in labels
        assert "ai_label_northbound" in labels
        for label_key in labels:
            assert label_key in AVAILABLE_DATA_LABELS
            assert I18n.get(label_key)

    def test_capital_flow_all_na_no_labels(self):
        labels: list[str] = []
        result = AIStrategyMixin._build_capital_flow_text(
            "000001.SZ",
            {},
            labels_out=labels,
        )
        assert I18n.get("ai_stock_mf_na") in result
        assert labels == []

    def test_capital_flow_no_record_no_labels(self):
        mf_df = pd.DataFrame(
            {
                "ts_code": ["999999.SZ"],
                "buy_lg_amount": [0],
                "sell_lg_amount": [0],
                "buy_elg_amount": [0],
                "sell_elg_amount": [0],
                "net_mf_amount": [0],
            }
        )
        labels: list[str] = []
        result = AIStrategyMixin._build_capital_flow_text(
            "000001.SZ",
            {"moneyflow_df": mf_df},
            labels_out=labels,
        )
        assert I18n.get("ai_stock_mf_no_record") in result
        assert labels == []

    def test_capital_flow_exception_clears_labels(self):
        """异常路径必须清空 labels_out 并返回 I18n 哨兵。"""
        labels: list[str] = ["ai_label_main_flow"]  # 模拟部分注册后被异常清空
        with patch("strategies.ai_mixin.safe_float", side_effect=RuntimeError("unexpected")):
            result = AIStrategyMixin._build_capital_flow_text(
                "000001.SZ",
                {"moneyflow_df": pd.DataFrame({"ts_code": ["000001.SZ"], "buy_lg_amount": [100]})},
                labels_out=labels,
            )
        assert result == I18n.get("ai_capital_flow_fetch_failed")
        assert labels == []

    def test_history_text_normal_registers_kline_label(self):
        """正常数据产出时注册 ai_label_kline。"""
        labels: list[str] = []
        result = AIStrategyMixin._build_history_text(
            _make_minimal_history_df(),
            ts_code="000001.SZ",
            stock_name="测试",
            labels_out=labels,
        )
        assert "ai_label_kline" in labels
        assert result  # 非空

    def test_history_text_insufficient_no_label(self):
        """数据不足哨兵路径不注册标签，返回 ai_history_insufficient 哨兵。"""
        short_df = pd.DataFrame(
            {
                "trade_date": ["20240101", "20240102", "20240103"],
                "close": [10.0, 10.5, 11.0],
                "high": [10.5, 11.0, 11.5],
                "low": [9.5, 10.0, 10.5],
                "open": [10.0, 10.5, 11.0],
                "vol": [1e6, 1e6, 1e6],
                "amount": [1e7, 1e7, 1e7],
            }
        )
        labels: list[str] = []
        result = AIStrategyMixin._build_history_text(
            short_df,
            ts_code="000001.SZ",
            labels_out=labels,
        )
        assert result == I18n.get("ai_history_insufficient")
        assert labels == []

    def test_history_text_empty_df_no_label(self):
        """空 DataFrame 不注册标签，返回 ai_history_insufficient 哨兵。"""
        labels: list[str] = []
        result = AIStrategyMixin._build_history_text(
            pd.DataFrame(),
            ts_code="000001.SZ",
            labels_out=labels,
        )
        assert result == I18n.get("ai_history_insufficient")
        assert labels == []

    def test_history_text_none_df_no_label(self):
        """None 不注册标签，返回 ai_history_insufficient 哨兵。"""
        labels: list[str] = []
        result = AIStrategyMixin._build_history_text(
            None,
            ts_code="000001.SZ",
            labels_out=labels,
        )
        assert result == I18n.get("ai_history_insufficient")
        assert labels == []

    def test_history_text_exception_clears_labels(self):
        """异常路径清空 labels_out。"""
        labels: list[str] = ["ai_label_kline"]  # 模拟部分注册后被异常清空
        with patch(
            "strategies.ai_mixin.TechnicalAnalysis._get_qfq_df",
            side_effect=RuntimeError("QFQ error"),
        ):
            result = AIStrategyMixin._build_history_text(
                _make_minimal_history_df(),
                ts_code="000001.SZ",
                labels_out=labels,
            )
        assert result == I18n.get("ai_history_extract_error")
        assert labels == []

    def test_history_text_all_na_no_label(self):
        """全 NaN 列的 DataFrame：close 全 NaN → 返回 ai_history_insufficient 哨兵，不注册标签。"""

        dates = pd.date_range("2024-01-01", periods=35, freq="B")
        na_df = pd.DataFrame(
            {
                "trade_date": dates.strftime("%Y%m%d"),
                "close": [float("nan")] * 35,
                "high": [float("nan")] * 35,
                "low": [float("nan")] * 35,
                "open": [float("nan")] * 35,
                "vol": [float("nan")] * 35,
                "amount": [float("nan")] * 35,
            }
        )
        labels: list[str] = []
        result = AIStrategyMixin._build_history_text(
            na_df,
            ts_code="000001.SZ",
            labels_out=labels,
        )
        assert result == I18n.get("ai_history_insufficient")
        assert "ai_label_kline" not in labels

    @pytest.mark.asyncio
    async def test_macro_context_exception_returns_empty(self):
        """_build_macro_context 异常路径：catch 后返回空串，不触发 ai_label_shibor / ai_label_macro_full 注册。"""
        mixin = AIStrategyMixin()
        mixin.cache = type("FakeCache", (), {})()
        mixin.cache.get_macro_economy = _async_raise(RuntimeError("DB error"))
        mixin.cache.get_shibor_latest = _async_raise(RuntimeError("DB error"))

        result = await mixin._build_macro_context(mixin.cache)
        assert result == "", f"异常路径应返回空串，实际返回: {result!r}"


class TestInvariant4PureFunctionRendering:
    def test_empty_labels_returns_empty(self):
        assert build_available_data_block([]) == ""

    def test_single_label_rendered(self):
        result = build_available_data_block(["ai_label_roe_trend"])
        assert "<available_data>" in result
        assert "</available_data>" in result
        assert I18n.get("ai_label_roe_trend") in result

    def test_multiple_labels_each_rendered(self):
        keys = ["ai_label_roe_trend", "ai_label_valuation", "ai_label_northbound"]
        result = build_available_data_block(keys)
        for key in keys:
            assert I18n.get(key) in result, f"Label '{key}' not found in rendered block"

    def test_header_present(self):
        result = build_available_data_block(["ai_label_tech"])
        assert I18n.get("ai_available_data_header") in result

    def test_xml_structure_precise(self):
        """验证 <available_data> 的精确 XML 结构：标签包裹、条目前缀 `- `。"""
        keys = ["ai_label_tech", "ai_label_valuation"]
        result = build_available_data_block(keys)
        lines = result.split("\n")
        assert lines[0] == "<available_data>", f"首行应为 <available_data>，实际为: {lines[0]}"
        assert lines[-1] == "</available_data>", f"末行应为 </available_data>，实际为: {lines[-1]}"
        # 第二行是 header
        assert lines[1] == I18n.get("ai_available_data_header")
        # 后续行每条标签以 "- " 开头
        for key in keys:
            expected_line = f"- {I18n.get(key)}"
            assert expected_line in lines, f"标签 '{key}' 的行格式不正确，期望 '{expected_line}'"


class TestInvariant5MixinAnalyzeSingleLabelAssembly:
    @pytest.mark.asyncio
    async def test_multi_period_sentinel_excludes_labels(self):
        mixin = AIStrategyMixin()
        mixin.cache = _make_fake_cache()
        mixin.cache.get_financial_reports_history = _async_return(pd.DataFrame())

        prefetched = PreFetchedContext(
            capital={},
            auxiliary_data={},
            macro_context="CPI: 2.1%",
            concepts_map={"000001.SZ": ["白酒"]},
        )
        dp = type("FakeDP", (), {"cache": mixin.cache})()

        mock_client = AsyncMock()
        mock_client.analyze_stock = AsyncMock(return_value={"recommendation": "neutral", "score": 50})

        await mixin._mixin_analyze_single(
            {
                "ts_code": "000001.SZ",
                "pe_ttm": 10,
                "pb": 1,
                "roe": 5,
                "total_mv": 1e6,
                "name": "测试",
            },
            dp,
            mock_client,
            prefetched,
            history_df=_make_minimal_history_df(),
            news=[],
        )
        call_kwargs = mock_client.analyze_stock.call_args.kwargs
        financial_labels = call_kwargs.get("financial_labels", [])
        assert "ai_label_valuation" in financial_labels
        assert "ai_label_roe_trend" not in financial_labels
        # Phase 2A.1 §4.1：ai_label_macro 拆分为 shibor + macro_full
        assert "ai_label_shibor" in financial_labels
        assert "ai_label_macro_full" in financial_labels

    @pytest.mark.asyncio
    async def test_auxiliary_sentinel_excludes_labels(self):
        mixin = AIStrategyMixin()
        mixin.cache = _make_fake_cache()
        mixin.cache.get_financial_reports_history = _async_return(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "roe": [15.0],
                    "grossprofit_margin": [30.0],
                    "or_yoy": [10.0],
                    "netprofit_yoy": [12.0],
                    "n_cashflow_act": [5e8],
                    "n_income_attr_p": [3e8],
                    "total_assets": [1e10],
                    "goodwill": [5e8],
                    "money_cap": [2e9],
                    "accounts_receiv": [1e9],
                }
            )
        )

        prefetched = PreFetchedContext(
            capital={},
            auxiliary_data={},
            macro_context="",
            concepts_map={"000001.SZ": ["白酒"]},
        )
        dp = type("FakeDP", (), {"cache": mixin.cache})()

        mock_client = AsyncMock()
        mock_client.analyze_stock = AsyncMock(return_value={"recommendation": "neutral", "score": 50})

        await mixin._mixin_analyze_single(
            {
                "ts_code": "000001.SZ",
                "pe_ttm": 10,
                "pb": 1,
                "roe": 5,
                "total_mv": 1e6,
                "name": "测试",
            },
            dp,
            mock_client,
            prefetched,
            history_df=_make_minimal_history_df(),
            news=[],
        )
        call_kwargs = mock_client.analyze_stock.call_args.kwargs
        financial_labels = call_kwargs.get("financial_labels", [])
        assert "ai_label_valuation" in financial_labels
        assert "ai_label_roe_trend" in financial_labels
        assert "ai_label_audit" not in financial_labels
        # Phase 2A.1 §4.1：ai_label_macro 拆分为 shibor + macro_full
        assert "ai_label_shibor" not in financial_labels
        assert "ai_label_macro_full" not in financial_labels

    @pytest.mark.asyncio
    async def test_all_valid_includes_all_labels(self):
        mixin = AIStrategyMixin()
        mixin.cache = _make_fake_cache()
        mixin.cache.get_financial_reports_history = _async_return(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "roe": [15.0],
                    "grossprofit_margin": [30.0],
                    "or_yoy": [10.0],
                    "netprofit_yoy": [12.0],
                    "n_cashflow_act": [5e8],
                    "n_income_attr_p": [3e8],
                    "total_assets": [1e10],
                    "goodwill": [5e8],
                    "money_cap": [2e9],
                    "accounts_receiv": [1e9],
                }
            )
        )
        mixin.cache.get_fina_audit = _async_return(
            pd.DataFrame({"ts_code": ["000001.SZ"], "audit_result": ["标准无保留"]})
        )
        mixin.cache.get_fina_mainbz = _async_return(
            pd.DataFrame({"ts_code": ["000001.SZ"], "bz_item": ["白酒"], "bz_sales": [1e9]})
        )
        mixin.cache.get_dividend = _async_return(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20231231"],
                    "div_proc": ["实施"],
                }
            )
        )
        mixin.cache.get_pledge_stat = _async_return(pd.DataFrame({"ts_code": ["000001.SZ"], "pledge_ratio": [15.0]}))
        mixin.cache.get_top10_holders = _async_return(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20231231"],
                    "holder_name": ["某公司"],
                    "hold_ratio": [30.0],
                }
            )
        )
        mixin.cache.get_stk_holdernumber = _async_return(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "ann_date": ["20240101"],
                    "holder_num": [100000],
                    "holder_num_ratio": [-3.5],
                }
            )
        )
        mixin.cache.get_fina_forecast = _async_return(pd.DataFrame())
        mixin.cache.get_pledge_detail = _async_return(pd.DataFrame())
        mixin.cache.get_share_float_upcoming = _async_return(pd.DataFrame())
        mixin.cache.get_stk_holdertrade = _async_return(pd.DataFrame())

        prefetched = PreFetchedContext(
            capital={},
            auxiliary_data={},
            macro_context="CPI: 2.1%",
            concepts_map={"000001.SZ": ["白酒"]},
        )
        dp = type("FakeDP", (), {"cache": mixin.cache})()

        mock_client = AsyncMock()
        mock_client.analyze_stock = AsyncMock(return_value={"recommendation": "neutral", "score": 50})

        await mixin._mixin_analyze_single(
            {
                "ts_code": "000001.SZ",
                "pe_ttm": 10,
                "pb": 1,
                "roe": 5,
                "total_mv": 1e6,
                "name": "测试",
            },
            dp,
            mock_client,
            prefetched,
            history_df=_make_minimal_history_df(),
            news=[],
        )
        call_kwargs = mock_client.analyze_stock.call_args.kwargs
        financial_labels = call_kwargs.get("financial_labels", [])
        assert "ai_label_valuation" in financial_labels
        assert "ai_label_roe_trend" in financial_labels
        assert "ai_label_audit" in financial_labels
        # Phase 2A.1 §4.1：ai_label_macro 拆分为 shibor + macro_full
        assert "ai_label_shibor" in financial_labels
        assert "ai_label_macro_full" in financial_labels

    @pytest.mark.asyncio
    async def test_capital_flow_sentinel_excludes_labels(self):
        """capital_flow 为空（全 NA）时，capital_labels 应为空。"""
        mixin = AIStrategyMixin()
        mixin.cache = _make_fake_cache()
        mixin.cache.get_financial_reports_history = _async_return(pd.DataFrame())

        prefetched = PreFetchedContext(
            capital={},  # 空 → _build_capital_flow_text 返回 NA 文本
            auxiliary_data={},
            macro_context="",
            concepts_map={"000001.SZ": ["白酒"]},
        )
        dp = type("FakeDP", (), {"cache": mixin.cache})()

        mock_client = AsyncMock()
        mock_client.analyze_stock = AsyncMock(return_value={"recommendation": "neutral", "score": 50})

        await mixin._mixin_analyze_single(
            {
                "ts_code": "000001.SZ",
                "pe_ttm": 10,
                "pb": 1,
                "roe": 5,
                "total_mv": 1e6,
                "name": "测试",
            },
            dp,
            mock_client,
            prefetched,
            history_df=_make_minimal_history_df(),
            news=[],
        )
        call_kwargs = mock_client.analyze_stock.call_args.kwargs
        capital_labels = call_kwargs.get("capital_labels", [])
        assert capital_labels == [], f"全 NA 资金面不应注册标签，实际: {capital_labels}"

    @pytest.mark.asyncio
    async def test_history_sentinel_excludes_labels(self):
        """history 数据不足时，history_labels 应为空。"""
        mixin = AIStrategyMixin()
        mixin.cache = _make_fake_cache()
        mixin.cache.get_financial_reports_history = _async_return(pd.DataFrame())

        prefetched = PreFetchedContext(
            capital={},
            auxiliary_data={},
            macro_context="",
            concepts_map={"000001.SZ": ["白酒"]},
        )
        dp = type("FakeDP", (), {"cache": mixin.cache})()

        mock_client = AsyncMock()
        mock_client.analyze_stock = AsyncMock(return_value={"recommendation": "neutral", "score": 50})

        # 传入只有3行的短 DataFrame → 数据不足哨兵
        short_df = pd.DataFrame(
            {
                "trade_date": ["20240101", "20240102", "20240103"],
                "close": [10.0, 10.5, 11.0],
                "high": [10.5, 11.0, 11.5],
                "low": [9.5, 10.0, 10.5],
                "open": [10.0, 10.5, 11.0],
                "vol": [1e6, 1e6, 1e6],
                "amount": [1e7, 1e7, 1e7],
            }
        )
        await mixin._mixin_analyze_single(
            {
                "ts_code": "000001.SZ",
                "pe_ttm": 10,
                "pb": 1,
                "roe": 5,
                "total_mv": 1e6,
                "name": "测试",
            },
            dp,
            mock_client,
            prefetched,
            history_df=short_df,
            news=[],
        )
        call_kwargs = mock_client.analyze_stock.call_args.kwargs
        history_labels = call_kwargs.get("history_labels", [])
        assert "ai_label_kline" not in history_labels

    @pytest.mark.asyncio
    async def test_macro_empty_excludes_label(self):
        """macro_context 为空时，ai_label_shibor / ai_label_macro_full 不应注册。"""
        mixin = AIStrategyMixin()
        mixin.cache = _make_fake_cache()
        mixin.cache.get_financial_reports_history = _async_return(pd.DataFrame())

        prefetched = PreFetchedContext(
            capital={},
            auxiliary_data={},
            macro_context="",  # 空
            concepts_map={"000001.SZ": ["白酒"]},
        )
        dp = type("FakeDP", (), {"cache": mixin.cache})()

        mock_client = AsyncMock()
        mock_client.analyze_stock = AsyncMock(return_value={"recommendation": "neutral", "score": 50})

        await mixin._mixin_analyze_single(
            {
                "ts_code": "000001.SZ",
                "pe_ttm": 10,
                "pb": 1,
                "roe": 5,
                "total_mv": 1e6,
                "name": "测试",
            },
            dp,
            mock_client,
            prefetched,
            history_df=_make_minimal_history_df(),
            news=[],
        )
        call_kwargs = mock_client.analyze_stock.call_args.kwargs
        financial_labels = call_kwargs.get("financial_labels", [])
        # Phase 2A.1 §4.1：ai_label_macro 拆分为 shibor + macro_full
        assert "ai_label_shibor" not in financial_labels
        assert "ai_label_macro_full" not in financial_labels


class TestInvariant6AnalyzeStockLabelAssembly:
    @pytest.mark.asyncio
    async def test_base_labels_require_valid_content(self):
        with (
            patch("services.ai_service.build_available_data_block") as mock_build,
            patch.object(AIService, "_chat_completion_with_failover", new_callable=AsyncMock) as mock_llm,
            patch.object(AIService, "is_cloud_available", return_value=True),
        ):
            mock_build.return_value = "<available_data>test</available_data>"
            mock_llm.return_value = {
                "recommendation": "neutral",
                "score": 50,
                "reasoning": "ok",
            }

            service = AIService.__new__(AIService)
            service._initialized = True

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ", "name": "测试"},
                tech_info={"macd_signal": "bullish"},
                news_list=[],
            )
            labels_with_data = mock_build.call_args.args[0]
            assert "ai_label_quote_snapshot" in labels_with_data
            assert "ai_label_tech" in labels_with_data

            await service.analyze_stock(
                stock_info={},
                tech_info={},
                news_list=[],
            )
            labels_without_data = mock_build.call_args.args[0]
            assert "ai_label_quote_snapshot" not in labels_without_data
            assert "ai_label_tech" not in labels_without_data

    @pytest.mark.asyncio
    async def test_global_context_conditional(self):
        with (
            patch("services.ai_service.build_available_data_block") as mock_build,
            patch.object(AIService, "_chat_completion_with_failover", new_callable=AsyncMock) as mock_llm,
            patch.object(AIService, "is_cloud_available", return_value=True),
        ):
            mock_build.return_value = "<available_data>test</available_data>"
            mock_llm.return_value = {
                "recommendation": "neutral",
                "score": 50,
                "reasoning": "ok",
            }

            service = AIService.__new__(AIService)
            service._initialized = True

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                global_context="大盘上涨",
                include_global_context=True,
            )
            labels_with = mock_build.call_args.args[0]
            assert "ai_label_global" in labels_with

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                global_context="",
                include_global_context=True,
            )
            labels_without = mock_build.call_args.args[0]
            assert "ai_label_global" not in labels_without

    @pytest.mark.asyncio
    async def test_news_conditional(self):
        with (
            patch("services.ai_service.build_available_data_block") as mock_build,
            patch.object(AIService, "_chat_completion_with_failover", new_callable=AsyncMock) as mock_llm,
            patch.object(AIService, "is_cloud_available", return_value=True),
        ):
            mock_build.return_value = "<available_data>test</available_data>"
            mock_llm.return_value = {
                "recommendation": "neutral",
                "score": 50,
                "reasoning": "ok",
            }

            service = AIService.__new__(AIService)
            service._initialized = True

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[{"title": "利好", "source": "test", "publish_time": "2024-01-01"}],
            )
            labels_with = mock_build.call_args.args[0]
            assert "ai_label_news" in labels_with

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
            )
            labels_without = mock_build.call_args.args[0]
            assert "ai_label_news" not in labels_without

    @pytest.mark.asyncio
    async def test_financial_labels_conditional(self):
        with (
            patch("services.ai_service.build_available_data_block") as mock_build,
            patch.object(AIService, "_chat_completion_with_failover", new_callable=AsyncMock) as mock_llm,
            patch.object(AIService, "is_cloud_available", return_value=True),
        ):
            mock_build.return_value = "<available_data>test</available_data>"
            mock_llm.return_value = {
                "recommendation": "neutral",
                "score": 50,
                "reasoning": "ok",
            }

            service = AIService.__new__(AIService)
            service._initialized = True

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                financials_text="PE: 10\nPB: 1",
                financial_labels=["ai_label_valuation", "ai_label_roe_trend"],
            )
            labels_with = mock_build.call_args.args[0]
            assert "ai_label_valuation" in labels_with
            assert "ai_label_roe_trend" in labels_with

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                financials_text="(Data not available yet, assume neutral)",
                financial_labels=["ai_label_valuation"],
            )
            labels_without = mock_build.call_args.args[0]
            assert "ai_label_valuation" not in labels_without

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                financials_text=I18n.get("ai_financial_insufficient"),
                financial_labels=["ai_label_valuation"],
            )
            labels_sentinel = mock_build.call_args.args[0]
            assert "ai_label_valuation" not in labels_sentinel

    @pytest.mark.asyncio
    async def test_financial_sentinel_not_injected_into_prompt(self):
        with (
            patch.object(AIService, "_chat_completion_with_failover", new_callable=AsyncMock) as mock_llm,
            patch.object(AIService, "is_cloud_available", return_value=True),
        ):
            mock_llm.return_value = {
                "recommendation": "neutral",
                "score": 50,
                "reasoning": "ok",
            }

            service = AIService.__new__(AIService)
            service._initialized = True

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                financials_text=I18n.get("ai_financial_insufficient"),
                financial_labels=["ai_label_valuation"],
            )
            messages = mock_llm.call_args.args[0]
            user_content = next(m["content"] for m in messages if m["role"] == "user")
            assert "<financials>" not in user_content
            assert I18n.get("ai_label_valuation") not in user_content

            mock_llm.reset_mock()
            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                financials_text=I18n.get("ai_financial_fetch_failed"),
                financial_labels=["ai_label_valuation"],
            )
            messages2 = mock_llm.call_args.args[0]
            user_content2 = next(m["content"] for m in messages2 if m["role"] == "user")
            assert "<financials>" not in user_content2
            assert I18n.get("ai_label_valuation") not in user_content2

    @pytest.mark.asyncio
    async def test_capital_labels_conditional(self):
        with (
            patch("services.ai_service.build_available_data_block") as mock_build,
            patch.object(AIService, "_chat_completion_with_failover", new_callable=AsyncMock) as mock_llm,
            patch.object(AIService, "is_cloud_available", return_value=True),
        ):
            mock_build.return_value = "<available_data>test</available_data>"
            mock_llm.return_value = {
                "recommendation": "neutral",
                "score": 50,
                "reasoning": "ok",
            }

            service = AIService.__new__(AIService)
            service._initialized = True

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                capital_flow_text="主力净流入: 1000万",
                capital_labels=["ai_label_main_flow"],
            )
            labels_with = mock_build.call_args.args[0]
            assert "ai_label_main_flow" in labels_with

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                capital_flow_text="(Data not available yet, assume neutral)",
                capital_labels=["ai_label_main_flow"],
            )
            labels_without = mock_build.call_args.args[0]
            assert "ai_label_main_flow" not in labels_without

    @pytest.mark.asyncio
    async def test_kline_learning_strategy_ctx_conditional(self):
        with (
            patch("services.ai_service.build_available_data_block") as mock_build,
            patch.object(AIService, "_chat_completion_with_failover", new_callable=AsyncMock) as mock_llm,
            patch.object(AIService, "is_cloud_available", return_value=True),
        ):
            mock_build.return_value = "<available_data>test</available_data>"
            mock_llm.return_value = {
                "recommendation": "neutral",
                "score": 50,
                "reasoning": "ok",
            }

            service = AIService.__new__(AIService)
            service._initialized = True

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                history_text="近5日收盘价: 10,11,12,13,14",
                history_context="历史复盘: ...",
                strategy_context="RSI=25 超跌",
                history_labels=["ai_label_kline"],
            )
            labels_all = mock_build.call_args.args[0]
            assert "ai_label_kline" in labels_all
            assert "ai_label_learning" in labels_all
            assert "ai_label_strategy_ctx" in labels_all

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                history_text="",
                history_context="",
                strategy_context="",
                history_labels=[],
            )
            labels_none = mock_build.call_args.args[0]
            assert "ai_label_kline" not in labels_none
            assert "ai_label_learning" not in labels_none
            assert "ai_label_strategy_ctx" not in labels_none

    @pytest.mark.asyncio
    async def test_history_labels_from_builder_propagated(self):
        """history_labels 由 _build_history_text 产出，哨兵时不传播。"""
        with (
            patch("services.ai_service.build_available_data_block") as mock_build,
            patch.object(AIService, "_chat_completion_with_failover", new_callable=AsyncMock) as mock_llm,
            patch.object(AIService, "is_cloud_available", return_value=True),
        ):
            mock_build.return_value = "<available_data>test</available_data>"
            mock_llm.return_value = {
                "recommendation": "neutral",
                "score": 50,
                "reasoning": "ok",
            }

            service = AIService.__new__(AIService)
            service._initialized = True

            # 哨兵 history_text（数据不足）但 history_labels 为空 → 不注册 kline
            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                history_text=I18n.get("ai_history_insufficient"),
                history_labels=[],
            )
            labels_sentinel = mock_build.call_args.args[0]
            assert "ai_label_kline" not in labels_sentinel

    @pytest.mark.asyncio
    async def test_history_sentinel_not_injected_into_prompt(self):
        """哨兵 history_text 不应注入 <recent_price_action> 块。"""
        with (
            patch.object(AIService, "_chat_completion_with_failover", new_callable=AsyncMock) as mock_llm,
            patch.object(AIService, "is_cloud_available", return_value=True),
        ):
            mock_llm.return_value = {
                "recommendation": "neutral",
                "score": 50,
                "reasoning": "ok",
            }

            service = AIService.__new__(AIService)
            service._initialized = True

            # 哨兵1: 数据不足
            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                history_text=I18n.get("ai_history_insufficient"),
                history_labels=[],
            )
            messages = mock_llm.call_args.args[0]
            # messages = [system, system, user]
            user_content = next(m["content"] for m in messages if m["role"] == "user")
            assert "<recent_price_action>" not in user_content, "哨兵 history_text 不应注入 <recent_price_action> 块"

            # 哨兵2: 提取异常
            mock_llm.reset_mock()
            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                history_text=I18n.get("ai_history_extract_error"),
                history_labels=[],
            )
            messages2 = mock_llm.call_args.args[0]
            user_content2 = next(m["content"] for m in messages2 if m["role"] == "user")
            assert "<recent_price_action>" not in user_content2, (
                "异常哨兵 history_text 不应注入 <recent_price_action> 块"
            )

    @pytest.mark.asyncio
    async def test_history_real_data_injected_into_prompt(self):
        """正常 history_text 应注入 <recent_price_action> 块。"""
        with (
            patch.object(AIService, "_chat_completion_with_failover", new_callable=AsyncMock) as mock_llm,
            patch.object(AIService, "is_cloud_available", return_value=True),
        ):
            mock_llm.return_value = {
                "recommendation": "neutral",
                "score": 50,
                "reasoning": "ok",
            }

            service = AIService.__new__(AIService)
            service._initialized = True

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                history_text="近5日收盘价: 10,11,12,13,14",
                history_labels=["ai_label_kline"],
            )
            messages = mock_llm.call_args.args[0]
            user_content = next(m["content"] for m in messages if m["role"] == "user")
            assert "<recent_price_action>" in user_content, "正常 history_text 应注入 <recent_price_action> 块"


class TestInvariant7PledgeBoundary:
    @pytest.mark.asyncio
    async def test_pledge_zero_no_label(self):
        mixin = AIStrategyMixin()
        mixin.cache = type("FakeCache", (), {})()
        mixin.cache.get_fina_audit = _async_return(pd.DataFrame())
        mixin.cache.get_fina_mainbz = _async_return(pd.DataFrame())
        mixin.cache.get_dividend = _async_return(pd.DataFrame())
        mixin.cache.get_pledge_stat = _async_return(pd.DataFrame({"ts_code": ["000001.SZ"], "pledge_ratio": [0.0]}))
        mixin.cache.get_top10_holders = _async_return(pd.DataFrame())
        mixin.cache.get_stk_holdernumber = _async_return(pd.DataFrame())
        mixin.cache.get_fina_forecast = _async_return(pd.DataFrame())
        mixin.cache.get_pledge_detail = _async_return(pd.DataFrame())
        mixin.cache.get_share_float_upcoming = _async_return(pd.DataFrame())
        mixin.cache.get_stk_holdertrade = _async_return(pd.DataFrame())

        labels: list[str] = []
        result = await mixin._build_auxiliary_data_text(
            "000001.SZ",
            mixin.cache,
            labels_out=labels,
        )
        assert "ai_label_pledge" not in labels
        assert I18n.get("ai_pledge_ratio") not in result

    @pytest.mark.asyncio
    async def test_pledge_none_no_label(self):
        mixin = AIStrategyMixin()
        mixin.cache = type("FakeCache", (), {})()
        mixin.cache.get_fina_audit = _async_return(pd.DataFrame())
        mixin.cache.get_fina_mainbz = _async_return(pd.DataFrame())
        mixin.cache.get_dividend = _async_return(pd.DataFrame())
        mixin.cache.get_pledge_stat = _async_return(pd.DataFrame({"ts_code": ["000001.SZ"], "pledge_ratio": [None]}))
        mixin.cache.get_top10_holders = _async_return(pd.DataFrame())
        mixin.cache.get_stk_holdernumber = _async_return(pd.DataFrame())
        mixin.cache.get_fina_forecast = _async_return(pd.DataFrame())
        mixin.cache.get_pledge_detail = _async_return(pd.DataFrame())
        mixin.cache.get_share_float_upcoming = _async_return(pd.DataFrame())
        mixin.cache.get_stk_holdertrade = _async_return(pd.DataFrame())

        labels: list[str] = []
        result = await mixin._build_auxiliary_data_text(
            "000001.SZ",
            mixin.cache,
            labels_out=labels,
        )
        assert "ai_label_pledge" not in labels
        assert I18n.get("ai_pledge_ratio") not in result


def _async_return(value):
    from unittest.mock import AsyncMock

    return AsyncMock(return_value=value)


def _async_raise(exc):
    from unittest.mock import AsyncMock

    return AsyncMock(side_effect=exc)


def _make_fake_cache():
    cache = type("FakeCache", (), {})()
    cache.get_fina_audit = _async_return(pd.DataFrame())
    cache.get_fina_mainbz = _async_return(pd.DataFrame())
    cache.get_dividend = _async_return(pd.DataFrame())
    cache.get_pledge_stat = _async_return(pd.DataFrame())
    cache.get_top10_holders = _async_return(pd.DataFrame())
    cache.get_stk_holdernumber = _async_return(pd.DataFrame())
    cache.get_fina_forecast = _async_return(pd.DataFrame())
    cache.get_pledge_detail = _async_return(pd.DataFrame())
    cache.get_share_float_upcoming = _async_return(pd.DataFrame())
    cache.get_stk_holdertrade = _async_return(pd.DataFrame())
    cache.get_concepts = _async_return({})
    return cache


def _make_minimal_history_df():
    import numpy as np

    dates = pd.date_range("2024-01-01", periods=35, freq="B")
    return pd.DataFrame(
        {
            "trade_date": dates.strftime("%Y%m%d"),
            "close": np.linspace(10, 15, 35),
            "high": np.linspace(10.5, 15.5, 35),
            "low": np.linspace(9.5, 14.5, 35),
            "open": np.linspace(10, 15, 35),
            "vol": np.ones(35) * 1e6,
            "amount": np.ones(35) * 1e7,
        }
    )
