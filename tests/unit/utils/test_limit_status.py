"""utils.limit_status 正本单测（MAJOR-02：涨跌停幅度与涨跌停状态判定）。"""

import math

import pytest

from utils.limit_status import classify_limit_status, get_limit_pct

pytestmark = pytest.mark.unit


class TestGetLimitPct:
    """降级路径涨跌停幅度：板块分支优先于 ST 分支，无法判定返回 None。"""

    @pytest.mark.parametrize(
        ("ts_code", "name", "expected"),
        [
            # 创业板（300/301）：注册制 20%，ST 同为 20%（非主板 5%）
            ("300001.SZ", "ST某某", 20.0),
            ("301001.SZ", "", 20.0),
            ("300750.SZ", "*ST宁德", 20.0),
            # 科创板（688/689）：20%，ST 同为 20%
            ("688001.SH", "ST某某", 20.0),
            ("689009.SH", "", 20.0),
            # 主板：风险警示 5%，一般 10%
            ("600001.SH", "ST某某", 5.0),
            ("000001.SZ", "*ST平安", 5.0),
            ("600001.SH", "", 10.0),
            ("000001.SZ", "平安银行", 10.0),
            ("001001.SZ", "", 10.0),
            # 北交所：按 .BJ 后缀 30%，覆盖 8/4/920 开头（含 ST）
            ("920001.BJ", "", 30.0),
            ("430001.BJ", "", 30.0),
            ("830001.BJ", "ST某某", 30.0),
            # 无法判定：不猜（R21）
            ("", "", None),
            ("000001", "", None),
            ("000001.XX", "", None),
        ],
    )
    def test_limit_pct(self, ts_code, name, expected):
        assert get_limit_pct(ts_code, name) == expected

    def test_none_ts_code_returns_none(self):
        assert get_limit_pct(None) is None  # type: ignore[arg-type]  # 显式验证 None 入参（脏数据防御）


class TestClassifyLimitStatus:
    """以交易所公布价判定涨跌停，容差按「分」计价 0.005 元。"""

    def test_close_at_up_limit(self):
        assert classify_limit_status(11.0, 11.0, 9.0) == "up"

    def test_close_at_down_limit(self):
        assert classify_limit_status(9.0, 11.0, 9.0) == "down"

    def test_within_cent_tolerance_is_up(self):
        # close = up_limit - 0.004 → 分计价容差内视为涨停
        assert classify_limit_status(10.996, 11.0, 9.0) == "up"

    def test_beyond_tolerance_is_none(self):
        # close = up_limit - 0.01 明显未封板 → 不判涨停（旧 0.5 个百分点容差会误标）
        assert classify_limit_status(10.99, 11.0, 9.0) is None

    def test_gain_9_6pct_not_limit_up(self):
        # 主板涨 9.6%（close=10.96, up_limit=11.0, down_limit=9.0）不得判涨停
        assert classify_limit_status(10.96, 11.0, 9.0) is None

    @pytest.mark.parametrize(
        ("close", "up_limit", "down_limit"),
        [
            (None, 11.0, 9.0),
            (11.0, None, 9.0),
            (11.0, 11.0, None),
            (float("nan"), 11.0, 9.0),
            (11.0, float("nan"), 9.0),
            (11.0, 11.0, float("nan")),
            (math.inf, 11.0, 9.0),
        ],
    )
    def test_missing_or_invalid_returns_none(self, close, up_limit, down_limit):
        assert classify_limit_status(close, up_limit, down_limit) is None

    @pytest.mark.parametrize(
        ("close", "up_limit", "down_limit"),
        [
            (0.0, 11.0, 9.0),  # F3：停牌占位收盘价 → 不得被误判为跌停
            (-1.0, 11.0, 9.0),  # F3：异常负价
            (10.0, 0.0, 0.0),  # H1：占位涨跌停价 → 不得被误判为涨停
            (10.0, 11.0, 0.0),  # H1：跌停价占位 0
            (10.0, -11.0, 9.0),  # H1：负涨停价
        ],
    )
    def test_non_positive_prices_return_none(self, close, up_limit, down_limit):
        # F3/H1：close 与 up_limit/down_limit 任一 ≤0 均属无效价，不判定涨跌停
        assert classify_limit_status(close, up_limit, down_limit) is None
