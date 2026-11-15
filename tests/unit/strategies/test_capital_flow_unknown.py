"""D13-003：资金流缺失值必须渲染为「未知」，不得伪装成业务合法的 0（R21）。

覆盖：全缺失 / 部分缺失 / 真实 0 / 负值 / 非数值，以及注入 LLM 的上下文文本断言。
"""

from unittest.mock import patch

import pandas as pd
import pytest

from core.i18n import I18n
from strategies.ai_context import _build_capital_flow_text

pytestmark = pytest.mark.unit

TS_CODE = "000001.SZ"


def _unknown() -> str:
    # 运行时取文案，避免模块导入早于 I18n 初始化
    return I18n.get("ai_unknown")


def _mf_df(**overrides: object) -> pd.DataFrame:
    row: dict[str, object] = {
        "ts_code": TS_CODE,
        "buy_lg_amount": 1000.0,
        "sell_lg_amount": 500.0,
        "buy_elg_amount": 200.0,
        "sell_elg_amount": 100.0,
        "net_mf_amount": 600.0,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _nb_df(vol: object = 100000.0, ratio: object = 2.5) -> pd.DataFrame:
    return pd.DataFrame([{"ts_code": TS_CODE, "vol": vol, "ratio": ratio}])


def _text(prefetched: dict) -> str:
    return _build_capital_flow_text(TS_CODE, prefetched)


class TestMoneyflowMissing:
    def test_all_components_missing_render_unknown(self):
        """NaN / None / inf / 非数值：主力与全市场净流入口径全部保持未知。"""
        text = _text(
            {
                "moneyflow_df": _mf_df(
                    buy_lg_amount=float("nan"),
                    sell_lg_amount=None,
                    buy_elg_amount=float("inf"),
                    sell_elg_amount="abc",
                    net_mf_amount=float("nan"),
                )
            }
        )
        assert f"{I18n.get('ai_main_net_inflow')}: {_unknown()}" in text
        assert f"{I18n.get('ai_total_net_inflow')}: {_unknown()}" in text
        # 不得出现伪造的 0 金额
        assert f"{I18n.get('ai_main_net_inflow')}: 0" not in text
        assert f"{I18n.get('ai_total_net_inflow')}: 0" not in text

    def test_partial_missing_whole_net_unknown(self):
        """四项中任一缺失即无法还原净额：整项未知，不用 0 补齐求差。"""
        text = _text({"moneyflow_df": _mf_df(sell_elg_amount=None)})
        assert f"{I18n.get('ai_main_net_inflow')}: {_unknown()}" in text
        # 独立的 net_mf_amount 仍有效，正常渲染 600 万元
        assert f"{I18n.get('ai_total_net_inflow')}: 600.00{I18n.get('ai_unit_ten_thousand')}" in text

    def test_real_zero_preserved(self):
        """真实 0 仍渲染 0（不是未知）。"""
        text = _text(
            {
                "moneyflow_df": _mf_df(
                    buy_lg_amount=0.0,
                    sell_lg_amount=0.0,
                    buy_elg_amount=0.0,
                    sell_elg_amount=0.0,
                    net_mf_amount=0.0,
                )
            }
        )
        assert f"{I18n.get('ai_main_net_inflow')}: 0{I18n.get('ai_unit_yuan')}" in text
        assert f"{I18n.get('ai_total_net_inflow')}: 0{I18n.get('ai_unit_yuan')}" in text
        assert _unknown() not in text

    def test_negative_values_preserved(self):
        """负值原样保留符号与量级。"""
        text = _text(
            {
                "moneyflow_df": _mf_df(
                    buy_lg_amount=100.0,
                    sell_lg_amount=200.0,
                    buy_elg_amount=0.0,
                    sell_elg_amount=0.0,
                    net_mf_amount=-10000.0,
                )
            }
        )
        # 主力净额 = (100+0)-(200+0) = -100 万元
        assert f"{I18n.get('ai_main_net_inflow')}: -100.00{I18n.get('ai_unit_ten_thousand')}" in text
        # 全市场净流入 -10000 万元 = -1 亿元
        assert f"{I18n.get('ai_total_net_inflow')}: -1.00{I18n.get('ai_unit_billion')}" in text


class TestNorthboundMissing:
    def test_all_missing_render_unknown(self):
        text = _text({"northbound_df": _nb_df(vol=float("nan"), ratio=None)})
        expected = f"{I18n.get('ai_north_holding')}: {_unknown()}, {I18n.get('ai_circulating_ratio')}: {_unknown()}"
        assert expected in text
        assert f"0{I18n.get('ai_shares')}" not in text
        assert "0.00%" not in text

    def test_partial_missing_only_that_field_unknown(self):
        text = _text({"northbound_df": _nb_df(vol="abc", ratio=2.5)})
        expected = f"{I18n.get('ai_north_holding')}: {_unknown()}, {I18n.get('ai_circulating_ratio')}: 2.50%"
        assert expected in text

    def test_real_zero_preserved(self):
        text = _text({"northbound_df": _nb_df(vol=0.0, ratio=0.0)})
        assert (
            f"{I18n.get('ai_north_holding')}: 0{I18n.get('ai_shares')}, "
            f"{I18n.get('ai_circulating_ratio')}: 0.00%" in text
        )


class TestPromptPayloadNoFabricatedZero:
    def test_top_inst_missing_net_buy_unknown(self):
        ti_df = pd.DataFrame([{"ts_code": TS_CODE, "net_buy": float("nan"), "trade_date": "20240102"}])
        with patch(
            "data.external.tushare_client.TushareClient.is_api_covered_by_tier",
            return_value=True,
        ):
            text = _text({"top_inst_df": ti_df})
        assert f"{I18n.get('ai_net_buy')}: {_unknown()}" in text
        assert f"{I18n.get('ai_net_buy')}: 0" not in text

    def test_missing_sections_produce_no_fake_zero_in_prompt(self):
        """三段落全缺失时，注入 LLM 的文本既无 0 金额也无 0 股/0% 伪造值。"""
        text = _text(
            {
                "moneyflow_df": _mf_df(
                    buy_lg_amount=None,
                    sell_lg_amount=None,
                    buy_elg_amount=None,
                    sell_elg_amount=None,
                    net_mf_amount=None,
                ),
                "northbound_df": _nb_df(vol=None, ratio=None),
            }
        )
        unknown = _unknown()
        assert text.count(unknown) == 4  # 主力净流入 / 全市场净流入 / 北向股数 / 北向比例
        assert f"0{I18n.get('ai_unit_yuan')}" not in text
        assert f"0{I18n.get('ai_shares')}" not in text
        assert "0.00%" not in text
