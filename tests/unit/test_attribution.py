"""UX-04 选股结果结构化归因 —— 单元测试 (R19 配套).

覆盖新增业务逻辑:
- ``strategies.attribution`` 数据契约: fnum 归一 / JSON 序列化往返 / 非法降级 / 运算符校验。
- ``polars_base._build_attributions``: 候选池内统一排名 (升/降序、缺值排末、total 语义) 与归因列写入。
- ``strategies.fundamental`` / ``strategies.market`` 各策略 ``build_attribution`` 条件与 rank 生成。
- ``vm _decode_cell`` 归因列 JSON 解码与安全降级。
"""

# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
# 本文件含替身类/重解析 (attribution_from_json 返回 Optional / dict 型 StrategyContext / pandas zip),
# pyright 无法验证替身与生产类型兼容性, 与 test_market_strategy.py 同约定局部禁用相关告警。

import json
from typing import cast

import pandas as pd
import pytest

from strategies.attribution import (
    ATTRIBUTION_COLUMN,
    FilterAttribution,
    FilterCondition,
    RankAttribution,
    attribution_from_json,
    attribution_to_json,
    fnum,
)
from strategies.fundamental import DividendStrategy, GrowthStrategy, ValueStrategy
from strategies.market import VolumeBreakoutStrategy
from strategies.polars_base import _build_attributions
from ui.viewmodels.pagination_sorting_mixin import _decode_cell

pytestmark = pytest.mark.unit


# ============================================================================
# strategies.attribution — 数据契约
# ============================================================================


class TestFnum:
    def test_normal_number(self):
        assert fnum(3.5) == 3.5
        assert fnum("2.0") == 2.0

    def test_nan_and_inf_normalized_to_none(self):
        assert fnum(float("nan")) is None
        assert fnum(float("inf")) is None
        assert fnum(float("-inf")) is None

    def test_none_and_invalid_to_none(self):
        assert fnum(None) is None
        assert fnum("abc") is None


class TestFilterConditionValidation:
    def test_between_requires_tuple(self):
        with pytest.raises(ValueError, match="between 运算符") as exc_info:
            FilterCondition("pe", "between", 5.0, 3.0)
        assert "二元组" in str(exc_info.value)

    def test_single_op_rejects_tuple(self):
        with pytest.raises(ValueError, match="单值") as exc_info:
            FilterCondition("pe", "gt", (1.0, 2.0), 3.0)
        assert "gt 运算符" in str(exc_info.value)

    def test_unsupported_operator(self):
        with pytest.raises(ValueError, match="unsupported operator") as exc_info:
            FilterCondition("pe", "==", 5.0, 3.0)
        assert "==" in str(exc_info.value)

    def test_valid_between_ok(self):
        c = FilterCondition("pe", "between", (1.0, 2.0), 1.5)
        assert c.threshold == (1.0, 2.0)


class TestAttributionSerialization:
    def test_to_json_none_returns_none(self):
        assert attribution_to_json(None) is None

    def test_roundtrip_json_string(self):
        attr = FilterAttribution(
            conditions=(FilterCondition("pe_ttm", "between", (5.0, 20.0), 6.5),),
            rank=RankAttribution(field="dv_ttm", value=3.5, position=1, total=10),
        )
        raw = attribution_to_json(attr)
        assert isinstance(raw, str)
        back = attribution_from_json(raw)
        assert back == attr
        assert back is not None
        assert back.rank is not None
        assert back.rank.position == 1
        assert back.rank.total == 10

    def test_from_json_accepts_dict(self):
        attr = FilterAttribution(
            conditions=(FilterCondition("pv", "gt", 0.0, 1.5),),
            rank=RankAttribution(field="pe", value=8.0, position=2, total=5),
        )
        data = json.loads(attribution_to_json(attr))
        assert attribution_from_json(data) == attr

    def test_from_json_invalid_returns_none(self):
        assert attribution_from_json(None) is None
        assert attribution_from_json("") is None
        assert attribution_from_json("not-json") is None
        assert attribution_from_json("[1,2,3]") is None  # 非 dict 顶层
        assert attribution_from_json("{}") is not None  # 空 dict → 空归因

    def test_from_json_rank_none(self):
        attr = FilterAttribution(conditions=(FilterCondition("pe", "gt", 0.0, 1.0),))
        back = cast(FilterAttribution, attribution_from_json(attribution_to_json(attr)))
        assert back.rank is None
        assert back.conditions[0].column == "pe"


# ============================================================================
# polars_base._build_attributions — 统一排名与列写入
# ============================================================================


def _make_rank_strategy(rank_field: str, ascending: bool):
    class _FakeStrategy:
        attribution_enabled = True

        def build_attribution(self, row: dict, total: int, context=None) -> FilterAttribution:
            return FilterAttribution(
                conditions=(FilterCondition("pe", "gt", 0.0, fnum(row.get("pe"))),),
                rank=RankAttribution(field=rank_field, value=fnum(row.get(rank_field)), ascending=ascending),
            )

    return _FakeStrategy()


class TestBuildAttributions:
    def _df(self, values) -> pd.DataFrame:
        codes = [f"00000{i}.SZ" for i in range(len(values))]
        return pd.DataFrame({"ts_code": codes, "pe": values, "dv": values})

    def test_descending_rank(self):
        df = self._df([3.0, 1.0, 2.0])
        out = _build_attributions(_make_rank_strategy("pe", ascending=False), df, total=3, context={})
        pos = [attribution_from_json(x).rank.position for x in out[ATTRIBUTION_COLUMN]]
        # 降序: pe=3.0 → 1, pe=2.0 → 2, pe=1.0 → 3
        assert pos == [1, 3, 2]

    def test_ascending_rank_and_none_last(self):
        df = self._df([3.0, None, 1.0])  # 第二行缺值 → 恒排末 (二次检视 a2)
        out = _build_attributions(_make_rank_strategy("pe", ascending=True), df, total=3, context={})
        ranks = [attribution_from_json(x).rank for x in out[ATTRIBUTION_COLUMN]]
        ranks_by_code = {c: r for c, r in zip(out["ts_code"], ranks, strict=True)}
        # 升序: pe=1.0(000002) → rank1, pe=3.0(000000) → rank2, None(000001) → rank3 (末位)
        assert ranks_by_code["000000.SZ"].position == 2
        assert ranks_by_code["000001.SZ"].position == 3
        assert ranks_by_code["000002.SZ"].position == 1

    def test_total_is_candidate_pool(self):
        df = self._df([5.0, 4.0, 3.0])
        out = _build_attributions(_make_rank_strategy("pe", ascending=False), df, total=100, context={})
        ranks = [attribution_from_json(x).rank for x in out[ATTRIBUTION_COLUMN]]
        assert all(r.total == 100 for r in ranks)

    def test_build_attribution_none_row_writes_none(self):
        # strategy 对某行返回 None (不生成归因), 该行列值应为空
        class _NoneStrat:
            def build_attribution(self, row, total, context):
                return None

        df = self._df([1.0, 2.0])
        out = _build_attributions(_NoneStrat(), df, total=2, context={})
        assert bool(out[ATTRIBUTION_COLUMN].isna().all())


# ============================================================================
# 各策略 build_attribution — 条件与 rank 生成 (R20: 阈值须为数据单位)
# ============================================================================


class TestStrategyBuildAttribution:
    def test_value_strategy_conditions_and_rank(self):
        s = ValueStrategy()
        row = {"pe_ttm": 8.0, "pb": 1.2, "dv_ttm": 3.5}
        attr = s.build_attribution(row, total_candidates=50, context={"params": {}})
        assert attr is not None
        assert len(attr.conditions) == 3
        assert attr.conditions[0].column == "pe_ttm"
        assert attr.conditions[0].operator == "between"
        assert attr.conditions[0].actual == 8.0
        assert attr.rank is not None
        assert attr.rank.field == "dv_ttm"
        assert attr.rank.total == 50

    def test_growth_strategy_rank_field_roe(self):
        s = GrowthStrategy()
        attr = s.build_attribution(
            {"or_yoy": 30.0, "netprofit_yoy": 40.0, "roe": 18.0}, total_candidates=10, context={"params": {}}
        )
        assert attr is not None
        assert attr.rank is not None
        assert attr.rank.field == "roe"
        assert attr.rank.value == 18.0
        assert any(c.column == "or_yoy" for c in attr.conditions)

    def test_dividend_strategy_rank_field_dv(self):
        s = DividendStrategy()
        attr = cast(FilterAttribution, s.build_attribution({"dv_ttm": 5.0}, total_candidates=7, context={"params": {}}))
        rank = cast(RankAttribution, attr.rank)
        assert rank.field == "dv_ttm"
        assert rank.value == 5.0

    def test_volume_breakout_uses_effective_thresholds(self):
        s = VolumeBreakoutStrategy()
        context = {"params": {}}
        attr_pre = s.build_attribution({"pct_chg": 8.0, "turnover_rate": 5.0}, total_candidates=20, context=context)
        assert attr_pre is not None
        cond_pct = next(c for c in attr_pre.conditions if c.column == "pct_chg")
        assert cond_pct.operator == "between"
        assert cond_pct.threshold == (2.0, 7.0)  # 默认参数

        # 自动调整后的生效阈值须被采用 (二次检视 Ma4)
        context2 = {"params": {}, "_vol_break_thresholds": (3.0, 5.0, 4.0)}
        attr = s.build_attribution({"pct_chg": 4.0, "turnover_rate": 6.0}, total_candidates=20, context=context2)
        assert attr is not None
        cond_pct2 = next(c for c in attr.conditions if c.column == "pct_chg")
        assert cond_pct2.threshold == (3.0, 5.0)


# ============================================================================
# VM _decode_cell — 归因列 JSON 解码与安全降级
# ============================================================================


class TestDecodeCell:
    def test_attribution_column_json_parsed(self):
        raw = json.dumps({"conditions": [], "rank": None})
        assert _decode_cell(ATTRIBUTION_COLUMN, raw) == {"conditions": [], "rank": None}

    def test_attribution_column_already_dict_passthrough(self):
        d = {"conditions": []}
        assert _decode_cell(ATTRIBUTION_COLUMN, d) is d

    def test_attribution_column_invalid_json_returns_none(self):
        assert _decode_cell(ATTRIBUTION_COLUMN, "not-json") is None
        assert _decode_cell(ATTRIBUTION_COLUMN, None) is None

    def test_other_columns_passthrough(self):
        assert _decode_cell("pe_ttm", 6.5) == 6.5
        assert _decode_cell("ts_code", "000001.SZ") == "000001.SZ"
