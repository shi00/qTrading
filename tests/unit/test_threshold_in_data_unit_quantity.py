"""E-01-4 统一换算入口数量分支（R20）：证据门与旧金额行为回归。"""

import pandas as pd
import pytest

from core.errors import StrategyParamError
from data.constants import (
    DAILY_QUOTES_VOL_UNIT,
    DAILY_QUOTES_VOL_UNIT_SOURCE,
    attach_column_unit_sources,
    attach_column_units,
)
from strategies.utils import threshold_in_data_unit


def _df(column: str = "vol", unit: str = "lot", evidence: dict | None = DAILY_QUOTES_VOL_UNIT_SOURCE) -> pd.DataFrame:
    frame = pd.DataFrame({column: [1.0]})
    frame = attach_column_units(frame, {column: unit})
    if evidence is not None:
        frame = attach_column_unit_sources(frame, {column: evidence})
    return frame


def _message_key(excinfo: pytest.ExceptionInfo[StrategyParamError]) -> str:
    return excinfo.value.info.message_key


class TestQuantityConversion:
    def test_declared_daily_vol_unit_is_lot(self) -> None:
        assert DAILY_QUOTES_VOL_UNIT == "lot"

    def test_lot_to_share_and_back_with_evidence(self) -> None:
        frame = _df()
        assert threshold_in_data_unit(frame, "vol", "lot", 2, "lot") == 2.0
        assert threshold_in_data_unit(frame, "vol", "lot", 200, "share") == 2.0
        assert threshold_in_data_unit(frame, "vol", "lot", 2.5, "lot") == 2.5

    def test_share_data_unit_to_lot_value(self) -> None:
        frame = _df(unit="share")
        assert threshold_in_data_unit(frame, "vol", "share", 2, "lot") == 200.0

    def test_missing_evidence_rejected(self) -> None:
        with pytest.raises(StrategyParamError) as exc:
            threshold_in_data_unit(pd.DataFrame({"vol": [1.0]}), "vol", "lot", 2, "lot")
        assert _message_key(exc) == "strategy_unit_evidence_missing"

    def test_altered_evidence_rejected(self) -> None:
        altered = {**DAILY_QUOTES_VOL_UNIT_SOURCE, "provider": "other.provider"}
        with pytest.raises(StrategyParamError) as exc:
            threshold_in_data_unit(_df(evidence=altered), "vol", "lot", 2, "lot")
        assert _message_key(exc) == "strategy_unit_evidence_missing"

    def test_evidence_must_match_column(self) -> None:
        frame = _df(column="amount", unit="lot", evidence=DAILY_QUOTES_VOL_UNIT_SOURCE)
        with pytest.raises(StrategyParamError) as exc:
            threshold_in_data_unit(frame, "amount", "lot", 2, "lot")
        assert _message_key(exc) == "strategy_unit_evidence_missing"


class TestAmountRegression:
    def test_existing_amount_conversion_unchanged(self) -> None:
        frame = attach_column_units(pd.DataFrame({"amount": [1.0]}), {"amount": "million_cny"})
        assert threshold_in_data_unit(frame, "amount", "million_cny", 50, "yi_cny") == 5000.0
        # 附件元数据优先于 declared_unit：1 万元 = 0.01 百万（保持既有优先级）。
        assert threshold_in_data_unit(frame, "amount", "yuan", 1, "wan_cny") == 0.01
        # 无 attrs 时回退 declared_unit（既有契约）。
        bare = pd.DataFrame({"amount": [1.0]})
        assert threshold_in_data_unit(bare, "amount", "million_cny", 1, "wan_cny") == 0.01

    def test_unknown_amount_unit_still_rejected_with_same_key(self) -> None:
        with pytest.raises(StrategyParamError) as exc:
            threshold_in_data_unit(pd.DataFrame({"amount": [1.0]}), "amount", "million_cny", 1, "btc")
        assert _message_key(exc) == "strategy_unit_unknown"

    def test_mixed_quantity_and_amount_units_rejected(self) -> None:
        with pytest.raises(StrategyParamError) as exc:
            threshold_in_data_unit(_df(), "vol", "lot", 1, "yi_cny")
        assert _message_key(exc) == "strategy_unit_unknown"
