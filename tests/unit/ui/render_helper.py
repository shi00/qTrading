"""声明式组件渲染辅助工具（方案 §3.3.2）。

为无状态 ``@ft.component`` 组件提供单测渲染能力：通过 ``__wrapped__`` 绕过
Renderer 上下文检查，直接调用原函数返回控件树。

限制（spike 验证）：
- 仅支持**无状态**组件（不含 ``use_state``/``use_effect``）
- **有状态**组件需走集成测试（``flet_test_page`` fixture）

契约见 ``docs/ui-tech-debt-repayment-plan.md`` §3.3.2。
"""

from collections.abc import Callable

import flet as ft


def render_component(component: Callable, **props) -> ft.Control:
    """渲染无状态声明式组件用于单测。

    通过 ``__wrapped__`` 绕过 Renderer 上下文检查，直接调用组件原函数。
    仅支持无状态组件；有状态组件（含 ``use_state``/``use_effect``）会抛
    ``RuntimeError``，需走集成测试（``flet_test_page`` fixture）。

    Args:
        component: ``@ft.component`` 装饰的函数。
        **props: 组件属性。

    Returns:
        控件树根节点。

    Raises:
        RuntimeError: 组件含状态 hooks（``use_state``/``use_effect``）时。
    """
    unwrapped = getattr(component, "__wrapped__", component)
    return unwrapped(**props)
