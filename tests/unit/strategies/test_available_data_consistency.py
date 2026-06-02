import re
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest
from core.i18n import I18n
from services.ai_service import AIService, AVAILABLE_DATA_LABELS, build_available_data_block
from strategies.ai_mixin import AIStrategyMixin, PreFetchedContext
from strategies.strategy_prompts import STRATEGY_PROMPTS


class TestInvariant1NoStaticEnumeration:
    def test_prompts_have_runtime_data_boundary(self):
        for key, prompt in STRATEGY_PROMPTS.items():
            assert "【数据边界】" in prompt or "available_data" in prompt, (
                f"{key} 缺少运行时清单引用指令（【数据边界】或 available_data）"
            )

    def test_prompts_no_forbidden_headers(self):
        forbidden = ["【可用数据】", "【你将收到的分析材料】", "你将收到以下数据", "你会收到以下"]
        for key, prompt in STRATEGY_PROMPTS.items():
            for header in forbidden:
                assert header not in prompt, f"{key} 仍含静态数据枚举表头「{header}」"

    def test_prompts_no_numbered_enumeration_before_boundary(self):
        pattern = re.compile(r"[\d]+[\.、)]\s*(?!\s*若)(.{2,20})(?:数据|指标|信息|材料)")
        for key, prompt in STRATEGY_PROMPTS.items():
            if "【数据边界】" in prompt:
                pre_boundary = prompt.split("【数据边界】")[0]
                assert not pattern.search(pre_boundary), f"{key} 在【数据边界】之前仍含编号式数据枚举"


class TestInvariant2LabelsSubsetOfRegistry:
    def test_all_registered_keys_have_i18n(self):
        for label_key in AVAILABLE_DATA_LABELS:
            translated = I18n.get(label_key)
            assert translated and translated != label_key, f"Label key '{label_key}' has no i18n translation"

    def test_builder_labels_subset_of_registry(self):
        builder_labels = {
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
            "ai_label_top_holder",
            "ai_label_holder_count",
            "ai_label_main_flow",
            "ai_label_top_list",
            "ai_label_northbound",
        }
        assert builder_labels.issubset(AVAILABLE_DATA_LABELS), (
            f"Builder labels not in registry: {builder_labels - AVAILABLE_DATA_LABELS}"
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
            assert I18n.get(text_key) in result, (
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
        assert result == I18n.get("ai_financial_insufficient")
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
        assert result == I18n.get("ai_financial_insufficient")
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
        assert result == I18n.get("ai_financial_fetch_failed")
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
            pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20231231"], "div_proc": ["实施"]})
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
            assert I18n.get(text_key) in result, (
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

        labels: list[str] = []
        result = await mixin._build_auxiliary_data_text(
            "000001.SZ",
            mixin.cache,
            labels_out=labels,
        )
        assert result == I18n.get("ai_no_auxiliary_data")
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
        assert result == I18n.get("ai_no_auxiliary_data")
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
            {"ts_code": "000001.SZ", "pe_ttm": 10, "pb": 1, "roe": 5, "total_mv": 1e6, "name": "测试"},
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
        assert "ai_label_macro" in financial_labels

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
            {"ts_code": "000001.SZ", "pe_ttm": 10, "pb": 1, "roe": 5, "total_mv": 1e6, "name": "测试"},
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
        assert "ai_label_macro" not in financial_labels

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
            pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20231231"], "div_proc": ["实施"]})
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
            {"ts_code": "000001.SZ", "pe_ttm": 10, "pb": 1, "roe": 5, "total_mv": 1e6, "name": "测试"},
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
        assert "ai_label_macro" in financial_labels


class TestInvariant6AnalyzeStockLabelAssembly:
    @pytest.mark.asyncio
    async def test_always_present_labels(self):
        with (
            patch("services.ai_service.build_available_data_block") as mock_build,
            patch.object(AIService, "_chat_completion_with_failover", new_callable=AsyncMock) as mock_llm,
            patch.object(AIService, "is_cloud_available", return_value=True),
        ):
            mock_build.return_value = "<available_data>test</available_data>"
            mock_llm.return_value = {"recommendation": "neutral", "score": 50, "reasoning": "ok"}

            service = AIService.__new__(AIService)
            service._initialized = True

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ", "name": "测试"},
                tech_info={"macd_signal": "bullish"},
                news_list=[],
            )

            labels = mock_build.call_args.args[0]
            assert "ai_label_quote_snapshot" in labels
            assert "ai_label_tech" in labels

    @pytest.mark.asyncio
    async def test_global_context_conditional(self):
        with (
            patch("services.ai_service.build_available_data_block") as mock_build,
            patch.object(AIService, "_chat_completion_with_failover", new_callable=AsyncMock) as mock_llm,
            patch.object(AIService, "is_cloud_available", return_value=True),
        ):
            mock_build.return_value = "<available_data>test</available_data>"
            mock_llm.return_value = {"recommendation": "neutral", "score": 50, "reasoning": "ok"}

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
            mock_llm.return_value = {"recommendation": "neutral", "score": 50, "reasoning": "ok"}

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
            mock_llm.return_value = {"recommendation": "neutral", "score": 50, "reasoning": "ok"}

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

    @pytest.mark.asyncio
    async def test_capital_labels_conditional(self):
        with (
            patch("services.ai_service.build_available_data_block") as mock_build,
            patch.object(AIService, "_chat_completion_with_failover", new_callable=AsyncMock) as mock_llm,
            patch.object(AIService, "is_cloud_available", return_value=True),
        ):
            mock_build.return_value = "<available_data>test</available_data>"
            mock_llm.return_value = {"recommendation": "neutral", "score": 50, "reasoning": "ok"}

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
            mock_llm.return_value = {"recommendation": "neutral", "score": 50, "reasoning": "ok"}

            service = AIService.__new__(AIService)
            service._initialized = True

            await service.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                history_text="近5日收盘价: 10,11,12,13,14",
                history_context="历史复盘: ...",
                strategy_context="RSI=25 超跌",
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
            )
            labels_none = mock_build.call_args.args[0]
            assert "ai_label_kline" not in labels_none
            assert "ai_label_learning" not in labels_none
            assert "ai_label_strategy_ctx" not in labels_none


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
