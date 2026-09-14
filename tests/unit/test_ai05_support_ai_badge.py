# pyright: reportArgumentType=false
# 本文件含测试替身对象（SimpleNamespace 代替真实策略/manager），触发 参数类型不兼容。
# 替身仅用于验证 mixin 装配逻辑，测试行为由断言本身验证。

"""AI-05 策略完成：supports_ai 派生 + VM 装配（UI 徽章数据源）。

覆盖 review 04 §1.5 AI-05：策略元数据暴露 ``supports_ai: bool``（派生自
``enable_ai_analysis``），并经 VM ``_build_strategy_dep_rows`` 装配进
``StrategyDepRow``。View 渲染徽章为声明式文本拼接，按项目单测边界不覆盖。
"""

from types import SimpleNamespace

import pytest

from strategies.all_strategies import StrategyManager
from strategies.base_strategy import BaseStrategy
from ui.viewmodels.screener_types import StrategyDepRow
from ui.viewmodels.strategy_meta_mixin import StrategyMetaMixin

pytestmark = pytest.mark.unit

# 已知事实（review 04 §1.5）：纯数学策略关闭 AI，Oversold/Institutional 开启 AI
_AI_DISABLED_KEYS = {
    "volume_breakout",
    "northbound_holding",
    "northbound_flow",
    "block_trade",
}
_AI_ENABLED_KEYS = {"oversold", "institutional"}


class _PlainStrategy(BaseStrategy):
    """未继承 AIStrategyMixin 的最小策略（无 enable_ai_analysis）。"""

    def filter(self, context):  # 测试替身: 仅满足抽象方法以允许实例化
        return context


class _AISubclass(_PlainStrategy):
    enable_ai_analysis = True


class _AIOverridden(_PlainStrategy):
    enable_ai_analysis = False


def _mgr() -> StrategyManager:
    return StrategyManager()


def test_supports_ai_matches_enable_ai_analysis_for_all_registered():
    """派生恒等性：supports_ai 与策略 enable_ai_analysis 一致（缺失时默认 False 兜底）。"""
    mgr = _mgr()
    for key in mgr.get_all_with_dependencies():
        st = mgr.get_strategy(key)
        expected = bool(getattr(st, "enable_ai_analysis", False))
        assert st.supports_ai is expected, f"supports_ai 派生不一致 key={key}"


def test_ai_disabled_math_strategies():
    for key in _AI_DISABLED_KEYS:
        assert _mgr().get_strategy(key).supports_ai is False, key


def test_ai_enabled_strategies():
    for key in _AI_ENABLED_KEYS:
        assert _mgr().get_strategy(key).supports_ai is True, key


def test_supports_ai_derivation_rules():
    assert _PlainStrategy("k", "d").supports_ai is False  # 无 mixin → False
    assert _AISubclass("k", "d").supports_ai is True  # 继承且开启 → True
    assert _AIOverridden("k", "d").supports_ai is False  # 类级覆盖关闭 → False


def test_build_strategy_dep_rows_injects_supports_ai():
    """VM 装配：StrategyDepRow 携带 supports_ai，供 View 按下拉渲染徽章。"""
    strategies = {
        "plain": SimpleNamespace(name_key="k1", supports_ai=False),
        "ai": SimpleNamespace(name_key="k2", supports_ai=True),
    }
    host = SimpleNamespace(
        strategy_mgr=SimpleNamespace(
            get_all_with_dependencies=lambda: {k: {"missing_apis": ()} for k in strategies},
            get_strategy=lambda key: strategies[key],
        )
    )

    rows = StrategyMetaMixin._build_strategy_dep_rows(host)
    by_key = {r.key: r for r in rows}

    assert by_key["plain"].supports_ai is False
    assert by_key["ai"].supports_ai is True
    assert isinstance(by_key["ai"], StrategyDepRow)
