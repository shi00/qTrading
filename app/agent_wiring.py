"""app 层 Agent 组装注入点（E-01）：把统一单位换算入口注入 services。

services/core/data 不得上行 import strategies（R1）；唯一合法注入点在 app 层。
"""

from __future__ import annotations

from services.agent_service.units import UnitConversion


def agent_unit_converter() -> UnitConversion:
    """返回统一换算入口（``strategies.utils.threshold_in_data_unit``）。"""

    from strategies.utils import threshold_in_data_unit

    return threshold_in_data_unit
