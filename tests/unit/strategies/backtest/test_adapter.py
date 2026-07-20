"""BacktestStrategyAdapter 单元测试"""
# pyright: reportArgumentType=false

from datetime import date

import pandas as pd
import polars as pl
import pytest

from strategies.backtest.adapter import BacktestStrategyAdapter
from strategies.base_strategy import BaseStrategy

pytestmark = pytest.mark.unit


class MockStrategy(BaseStrategy):
    """用于测试的 Mock 策略"""

    # 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
    # pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
    # 测试行为由测试用例本身验证。

    required_context_keys = ("screening_data",)
    required_tables = ("daily_quotes",)

    def __init__(self):
        super().__init__("mock_strategy", "Mock Strategy for Testing")

    async def filter(self, context):
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "close": [10.0, 20.0],
            }
        )


class TestBacktestStrategyAdapter:
    @pytest.fixture
    def adapter(self) -> BacktestStrategyAdapter:
        return BacktestStrategyAdapter()

    @pytest.fixture
    def mock_strategy(self) -> MockStrategy:
        return MockStrategy()

    @pytest.mark.asyncio
    async def test_adapter_checks_dependencies(
        self,
        adapter: BacktestStrategyAdapter,
        mock_strategy: MockStrategy,
    ) -> None:
        context = {
            "trade_date": date(2024, 1, 1),
            "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
        }

        result = await adapter.generate_signal(
            strategy=mock_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert isinstance(result, pl.DataFrame)

    @pytest.mark.asyncio
    async def test_adapter_handles_unready_status(
        self,
        adapter: BacktestStrategyAdapter,
        mock_strategy: MockStrategy,
    ) -> None:
        context = {
            "trade_date": date(2024, 1, 1),
        }

        result = await adapter.generate_signal(
            strategy=mock_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert isinstance(result, pl.DataFrame)
        assert result.is_empty()

    @pytest.mark.asyncio
    async def test_adapter_handles_degraded_status(
        self,
        adapter: BacktestStrategyAdapter,
        mock_strategy: MockStrategy,
    ) -> None:
        context = {
            "trade_date": date(2024, 1, 1),
            "screening_data": pd.DataFrame(),
        }

        result = await adapter.generate_signal(
            strategy=mock_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert isinstance(result, pl.DataFrame)

    @pytest.mark.asyncio
    async def test_adapter_normalizes_output_schema(
        self,
        adapter: BacktestStrategyAdapter,
        mock_strategy: MockStrategy,
    ) -> None:
        context = {
            "trade_date": date(2024, 1, 1),
            "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
        }

        result = await adapter.generate_signal(
            strategy=mock_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        expected_columns = [
            "signal_date",
            "execution_date",
            "ts_code",
            "score",
            "signal_rank",
            "target_weight",
            "reason",
        ]
        for col in expected_columns:
            assert col in result.columns

    @pytest.mark.asyncio
    async def test_adapter_handles_strategy_exception(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class FailingStrategy(BaseStrategy):
            required_context_keys = ()

            def __init__(self):
                super().__init__("failing_strategy", "Failing Strategy")

            async def filter(self, context):
                raise ValueError("Strategy failed")

        failing_strategy = FailingStrategy()
        context = {
            "trade_date": date(2024, 1, 1),
        }

        with pytest.raises(ValueError, match="Strategy failed"):
            await adapter.generate_signal(
                strategy=failing_strategy,
                context=context,
                signal_date=date(2024, 1, 1),
                execution_date=date(2024, 1, 2),
            )

    @pytest.mark.asyncio
    async def test_adapter_handles_empty_result(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class EmptyResultStrategy(BaseStrategy):
            required_context_keys = ()

            def __init__(self):
                super().__init__("empty_strategy", "Empty Result Strategy")

            async def filter(self, context):
                return pd.DataFrame()

        empty_strategy = EmptyResultStrategy()
        context = {
            "trade_date": date(2024, 1, 1),
        }

        result = await adapter.generate_signal(
            strategy=empty_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert isinstance(result, pl.DataFrame)
        assert result.is_empty()

    @pytest.mark.asyncio
    async def test_adapter_assigns_rank_by_order(
        self,
        adapter: BacktestStrategyAdapter,
        mock_strategy: MockStrategy,
    ) -> None:
        context = {
            "trade_date": date(2024, 1, 1),
            "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
        }

        result = await adapter.generate_signal(
            strategy=mock_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        if not result.is_empty() and "signal_rank" in result.columns:
            ranks = result["signal_rank"].to_list()
            assert len(set(ranks)) == len(ranks)

    @pytest.mark.asyncio
    async def test_adapter_with_polars_output(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class PolarsStrategy(BaseStrategy):
            required_context_keys = ()

            def __init__(self):
                super().__init__("polars_strategy", "Polars Strategy")

            async def filter(self, context):
                return pl.DataFrame(
                    {
                        "ts_code": ["000001.SZ", "000002.SZ"],
                        "close": [10.0, 20.0],
                    }
                )

        polars_strategy = PolarsStrategy()
        context = {"trade_date": date(2024, 1, 1)}

        result = await adapter.generate_signal(
            strategy=polars_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert isinstance(result, pl.DataFrame)
        assert len(result) == 2
        assert "ts_code" in result.columns
        assert result["ts_code"].to_list() == ["000001.SZ", "000002.SZ"]

    @pytest.mark.asyncio
    async def test_adapter_with_score_column(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class ScoreStrategy(BaseStrategy):
            required_context_keys = ()

            def __init__(self):
                super().__init__("score_strategy", "Score Strategy")

            async def filter(self, context):
                return pd.DataFrame(
                    {
                        "ts_code": ["000001.SZ", "000002.SZ"],
                        "score": [0.8, 0.6],
                    }
                )

        score_strategy = ScoreStrategy()
        context = {"trade_date": date(2024, 1, 1)}

        result = await adapter.generate_signal(
            strategy=score_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert "score" in result.columns
        scores = result["score"].to_list()
        assert scores == [0.8, 0.6]

    @pytest.mark.asyncio
    async def test_adapter_with_signal_score_column(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class SignalScoreStrategy(BaseStrategy):
            required_context_keys = ()

            def __init__(self):
                super().__init__("signal_score_strategy", "Signal Score Strategy")

            async def filter(self, context):
                return pd.DataFrame(
                    {
                        "ts_code": ["000001.SZ"],
                        "signal_score": [0.9],
                    }
                )

        signal_score_strategy = SignalScoreStrategy()
        context = {"trade_date": date(2024, 1, 1)}

        result = await adapter.generate_signal(
            strategy=signal_score_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert "score" in result.columns
        assert result["score"].to_list() == [0.9]

    @pytest.mark.asyncio
    async def test_adapter_with_reason_column(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class ReasonStrategy(BaseStrategy):
            required_context_keys = ()

            def __init__(self):
                super().__init__("reason_strategy", "Reason Strategy")

            async def filter(self, context):
                return pd.DataFrame(
                    {
                        "ts_code": ["000001.SZ"],
                        "reason": ["技术指标突破"],
                    }
                )

        reason_strategy = ReasonStrategy()
        context = {"trade_date": date(2024, 1, 1)}

        result = await adapter.generate_signal(
            strategy=reason_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert "reason" in result.columns
        assert result["reason"].to_list() == ["技术指标突破"]

    @pytest.mark.asyncio
    async def test_adapter_with_signal_reason_column(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class SignalReasonStrategy(BaseStrategy):
            required_context_keys = ()

            def __init__(self):
                super().__init__("signal_reason_strategy", "Signal Reason Strategy")

            async def filter(self, context):
                return pd.DataFrame(
                    {
                        "ts_code": ["000001.SZ"],
                        "signal_reason": ["基本面改善"],
                    }
                )

        signal_reason_strategy = SignalReasonStrategy()
        context = {"trade_date": date(2024, 1, 1)}

        result = await adapter.generate_signal(
            strategy=signal_reason_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert "reason" in result.columns
        assert result["reason"].to_list() == ["基本面改善"]

    @pytest.mark.asyncio
    async def test_adapter_with_note_column(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class NoteStrategy(BaseStrategy):
            required_context_keys = ()

            def __init__(self):
                super().__init__("note_strategy", "Note Strategy")

            async def filter(self, context):
                return pd.DataFrame(
                    {
                        "ts_code": ["000001.SZ"],
                        "note": ["备注信息"],
                    }
                )

        note_strategy = NoteStrategy()
        context = {"trade_date": date(2024, 1, 1)}

        result = await adapter.generate_signal(
            strategy=note_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert "reason" in result.columns
        assert result["reason"].to_list() == ["备注信息"]

    @pytest.mark.asyncio
    async def test_adapter_missing_ts_code_column(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class MissingTsCodeStrategy(BaseStrategy):
            required_context_keys = ()

            def __init__(self):
                super().__init__("missing_ts_code_strategy", "Missing TsCode Strategy")

            async def filter(self, context):
                return pd.DataFrame(
                    {
                        "close": [10.0],
                    }
                )

        missing_ts_code_strategy = MissingTsCodeStrategy()
        context = {"trade_date": date(2024, 1, 1)}

        result = await adapter.generate_signal(
            strategy=missing_ts_code_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert isinstance(result, pl.DataFrame)
        assert result.is_empty()

    @pytest.mark.asyncio
    async def test_adapter_missing_ts_code_polars(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class MissingTsCodePolarsStrategy(BaseStrategy):
            required_context_keys = ()

            def __init__(self):
                super().__init__("missing_ts_code_polars_strategy", "Missing TsCode Polars Strategy")

            async def filter(self, context):
                return pl.DataFrame(
                    {
                        "close": [10.0],
                    }
                )

        missing_ts_code_polars_strategy = MissingTsCodePolarsStrategy()
        context = {"trade_date": date(2024, 1, 1)}

        result = await adapter.generate_signal(
            strategy=missing_ts_code_polars_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert isinstance(result, pl.DataFrame)
        assert result.is_empty()

    @pytest.mark.asyncio
    async def test_adapter_unexpected_return_type(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class UnexpectedTypeStrategy(BaseStrategy):
            required_context_keys = ()

            def __init__(self):
                super().__init__("unexpected_type_strategy", "Unexpected Type Strategy")

            async def filter(self, context):
                return "not a dataframe"

        unexpected_type_strategy = UnexpectedTypeStrategy()
        context = {"trade_date": date(2024, 1, 1)}

        result = await adapter.generate_signal(
            strategy=unexpected_type_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert isinstance(result, pl.DataFrame)
        assert result.is_empty()

    @pytest.mark.asyncio
    async def test_adapter_none_return(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class NoneReturnStrategy(BaseStrategy):
            required_context_keys = ()

            def __init__(self):
                super().__init__("none_return_strategy", "None Return Strategy")

            async def filter(self, context):
                return None

        none_return_strategy = NoneReturnStrategy()
        context = {"trade_date": date(2024, 1, 1)}

        result = await adapter.generate_signal(
            strategy=none_return_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert isinstance(result, pl.DataFrame)
        assert result.is_empty()

    @pytest.mark.asyncio
    async def test_adapter_equal_weight_calculation(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class MultiStockStrategy(BaseStrategy):
            required_context_keys = ()

            def __init__(self):
                super().__init__("multi_stock_strategy", "Multi Stock Strategy")

            async def filter(self, context):
                return pd.DataFrame(
                    {
                        "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                    }
                )

        multi_stock_strategy = MultiStockStrategy()
        context = {"trade_date": date(2024, 1, 1)}

        result = await adapter.generate_signal(
            strategy=multi_stock_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert len(result) == 3
        weights = result["target_weight"].to_list()
        expected_weight = 1.0 / 3
        for w in weights:
            assert w == expected_weight

    @pytest.mark.asyncio
    async def test_adapter_rank_assignment(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class RankTestStrategy(BaseStrategy):
            required_context_keys = ()

            def __init__(self):
                super().__init__("rank_test_strategy", "Rank Test Strategy")

            async def filter(self, context):
                return pd.DataFrame(
                    {
                        "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                    }
                )

        rank_test_strategy = RankTestStrategy()
        context = {"trade_date": date(2024, 1, 1)}

        result = await adapter.generate_signal(
            strategy=rank_test_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        ranks = result["signal_rank"].to_list()
        assert ranks == [3, 2, 1]

    def test_normalize_signal_output_direct(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        result_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "score": [0.5],
            }
        )

        result = adapter._normalize_signal_output(
            result_df,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert isinstance(result, pl.DataFrame)
        assert len(result) == 1
        assert result["signal_date"].to_list() == [date(2024, 1, 1)]
        assert result["execution_date"].to_list() == [date(2024, 1, 2)]

    def test_normalize_signal_output_empty_pandas(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        result_df = pd.DataFrame()

        result = adapter._normalize_signal_output(
            result_df,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert isinstance(result, pl.DataFrame)
        assert result.is_empty()

    def test_normalize_signal_output_none(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        result = adapter._normalize_signal_output(
            None,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert isinstance(result, pl.DataFrame)
        assert result.is_empty()
