"""strategies.ai_context — AI 上下文渲染器集合（review01-A5a）。

自 ``strategies/ai_mixin.py`` 拆出的上下文渲染器（原 ``AIStrategyMixin`` 的
``_build_*_text`` / ``_format_*_section`` / ``_compute_technical_structure`` /
``_get_limit_pct`` / ``_build_stale_section``）与 ``PreFetchedContext`` 数据容器。
按数据域分文件：

- ``context.py``：``PreFetchedContext`` 数据类 + ``ContextBuilder`` 类型
- ``common.py``：``_build_stale_section``（stale 标注公共辅助）
- ``history.py``：``_build_history_text``
- ``capital_flow.py``：``_build_capital_flow_text``
- ``financials.py``：多期财务 + 业绩预告/质押/解禁/增减持/快报/估值 渲染器
- ``auxiliary.py``：``_build_auxiliary_data_text``
- ``macro.py``：``_build_macro_context``
- ``technical.py``：``_compute_technical_structure`` / ``_get_limit_pct``

本子包为 strategies 层叶子模块：不得反向 import ``strategies`` 上层模块
（``AIStrategyMixin`` / ``PolarsBaseStrategy`` 等），仅依赖 data/utils/core/strategies.utils。
"""

from strategies.ai_context.auxiliary import _build_auxiliary_data_text
from strategies.ai_context.capital_flow import _build_capital_flow_text
from strategies.ai_context.common import _build_stale_section
from strategies.ai_context.context import ContextBuilder, PreFetchedContext
from strategies.ai_context.financials import (
    _build_financials_text,
    _build_multi_period_financials,
    _format_express_section,
    _format_forecast_section,
    _format_holder_trade_section,
    _format_pledge_detail_section,
    _format_share_float_section,
)
from strategies.ai_context.history import _build_history_text
from strategies.ai_context.macro import _build_macro_context
from strategies.ai_context.technical import _compute_technical_structure, _get_limit_pct

__all__ = [
    "ContextBuilder",
    "PreFetchedContext",
    "_build_stale_section",
    "_build_history_text",
    "_build_capital_flow_text",
    "_build_multi_period_financials",
    "_format_forecast_section",
    "_format_pledge_detail_section",
    "_format_share_float_section",
    "_format_holder_trade_section",
    "_format_express_section",
    "_build_auxiliary_data_text",
    "_build_macro_context",
    "_build_financials_text",
    "_compute_technical_structure",
    "_get_limit_pct",
]
