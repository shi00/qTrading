"""D2-5：回测基准单一正本回归测试。

BacktestConfig 缺省 benchmark_code 必须与 data.constants.DEFAULT_BENCHMARK_INDEX
保持一致，防止任何一端单独改回旧默认（000300.SH 等）造成双基准漂移。
"""

import pytest

from data.constants import DEFAULT_BENCHMARK_INDEX
from strategies.backtest.config import BacktestConfig

pytestmark = [pytest.mark.unit]


class TestBacktestBenchmarkSingleSource:
    def test_default_benchmark_matches_single_source(self):
        assert BacktestConfig.benchmark_code == DEFAULT_BENCHMARK_INDEX
        assert BacktestConfig.benchmark_code == "000985.CSI"

    def test_default_benchmark_not_stale(self):
        """确保旧硬编码默认（上证/沪深 300）未被回退引入。"""
        assert BacktestConfig.benchmark_code != "000001.SH"
        assert BacktestConfig.benchmark_code != "000300.SH"
