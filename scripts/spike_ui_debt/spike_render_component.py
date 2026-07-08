"""Spike #8: 验证 `render_component` helper 可行性。

目标：验证无状态组件能否通过 `__wrapped__` / `__component_impl__` 绕过
Renderer 上下文直接调用（用于测试或工具函数）。

源码分析（flet/components/component_decorator.py）：
```python
def component(fn):
    fn.__is_component__ = True
    @wraps(fn)
    def component_wrapper(*args, **kwargs):
        key = kwargs.pop("key", None)
        r = current_renderer()
        return r.render_component(fn, args, kwargs, key=key)
    component_wrapper.__is_component__ = True
    component_wrapper.__component_impl__ = fn  # 原始函数
    return component_wrapper
```
- `@wraps(fn)` 设置 `__wrapped__ = fn`（标准库行为）
- 显式设置 `__component_impl__ = fn`
- 两者都可访问原始函数

验证项：
- 8.1 @ft.component 装饰器设置 __is_component__ / __component_impl__ / __wrapped__
- 8.2 直接调用 __component_impl__ 绕过 Renderer（无 current_renderer 报错）
- 8.3 直接调用 __wrapped__ 绕过 Renderer
- 8.4 无状态组件直接调用返回控件（不创建 Component 包装）
- 8.5 unwrap_component helper 解析 Component._b
"""

from __future__ import annotations

import sys
from typing import Any

import flet as ft

sys.path.insert(0, ".")
from scripts.spike_ui_debt._spike_helpers import (  # noqa: E402
    make_component,
    render_once,
)

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, PASS if cond else FAIL, detail))


# --- 验证 8.1: 装饰器设置的属性 ---


@ft.component
def greeting(name: str):
    return ft.Text(f"Hello, {name}!")


check(
    "8.1 @ft.component 设置 __is_component__",
    getattr(greeting, "__is_component__", False) is True,
)
check(
    "8.1 @ft.component 设置 __component_impl__",
    hasattr(greeting, "__component_impl__"),
)
check(
    "8.1 @ft.component 设置 __wrapped__（@wraps 标准行为）",
    hasattr(greeting, "__wrapped__"),
)
check(
    "8.1 __component_impl__ 与 __wrapped__ 指向同一函数",
    greeting.__component_impl__ is greeting.__wrapped__,
    f"impl={greeting.__component_impl__}, wrapped={greeting.__wrapped__}",
)


# --- 验证 8.2: 直接调用 __component_impl__ 绕过 Renderer ---
# 正常调用 greeting() 需要 current_renderer，否则报错
direct_call_blocked = False
try:
    greeting("world")  # 无 Renderer 上下文
except RuntimeError as e:
    direct_call_blocked = "current renderer" in str(e).lower()

check(
    "8.2 无 Renderer 时调用 @component 函数报 RuntimeError",
    direct_call_blocked,
    "需通过 __component_impl__ 绕过",
)

# 通过 __component_impl__ 直接调用
impl = greeting.__component_impl__
result = impl("world")
check(
    "8.2 __component_impl__ 绕过 Renderer 直接调用成功",
    isinstance(result, ft.Text),
    f"result type={type(result).__name__}",
)
check(
    "8.2 直接调用返回控件内容正确",
    result.value == "Hello, world!",
    f"value={result.value!r}",
)


# --- 验证 8.3: 直接调用 __wrapped__ 等效 ---


wrapped = greeting.__wrapped__
result2 = wrapped("alice")
check(
    "8.3 __wrapped__ 绕过 Renderer 直接调用成功",
    isinstance(result2, ft.Text) and result2.value == "Hello, alice!",
    f"value={result2.value!r}",
)


# --- 验证 8.4: 无状态组件直接调用返回控件（不创建 Component 包装） ---


@ft.component
def stateless(label: str):
    # 无 hooks 的纯函数组件
    return ft.Container(content=ft.Text(label))


direct_result = stateless.__component_impl__("test")
check(
    "8.4 无状态组件 __component_impl__ 返回 Control（非 Component）",
    isinstance(direct_result, ft.Control) and not isinstance(direct_result, ft.Component),
    f"type={type(direct_result).__name__}",
)


# --- 验证 8.5: unwrap_component helper 解析 Component._b ---
# unwrap_component 反复跟随 Component._b 直到非 Component


@ft.component
def outer(x: int):
    return ft.Text(f"x={x}")


comp = make_component(outer, 42)
render_once(comp)
# 手动设置 _b 模拟渲染结果（render_once 不设置 _b）
comp._b = ft.Text("rendered")
unwrapped = ft.unwrap_component(comp)
check(
    "8.5 unwrap_component 解析 Component._b 到实际控件",
    unwrapped is comp._b,
    f"unwrapped type={type(unwrapped).__name__}",
)


# --- 验证 8.6: render_component helper 可行性结论 ---
# 推荐实现: def render_component(comp_fn, *args, **kwargs):
#     return comp_fn.__component_impl__(*args, **kwargs)
def render_component_helper(comp_fn: Any, *args: Any, **kwargs: Any) -> Any:
    """绕过 Renderer 上下文直接调用组件函数，返回控件。

    用于测试或工具场景（非 UI 渲染路径）。
    """
    impl = getattr(comp_fn, "__component_impl__", None)
    if impl is None:
        raise ValueError(f"{comp_fn} is not a @ft.component function")
    return impl(*args, **kwargs)


helper_result = render_component_helper(greeting, "helper")
check(
    "8.6 render_component_helper 可行（返回正确控件）",
    isinstance(helper_result, ft.Text) and helper_result.value == "Hello, helper!",
    f"value={helper_result.value!r}",
)


# 非 @component 函数应报错
def not_a_component():
    return ft.Text("nope")


err_raised = False
try:
    render_component_helper(not_a_component)  # type: ignore[arg-type]
except ValueError:
    err_raised = True
check(
    "8.6 非 @component 函数调用 helper 报 ValueError",
    err_raised,
    "类型安全：仅接受 @ft.component 装饰的函数",
)


# --- 输出结论 ---
print("\n" + "=" * 70)
print("Spike #8: render_component helper 可行性验证结果")
print("=" * 70)
for name, status, detail in results:
    line = f"[{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)

passed = sum(1 for _, s, _ in results if s == PASS)
print(f"\n总计: {passed}/{len(results)} 通过")
if passed != len(results):
    print("结论: render_component helper 不可行，需调整方案")
    sys.exit(1)
else:
    print("结论: render_component helper 可行")
    print("  - @ft.component 设置 __is_component__ / __component_impl__ / __wrapped__")
    print("  - 通过 __component_impl__ 或 __wrapped__ 可绕过 Renderer 直接调用")
    print("  - 无状态组件直接调用返回 Control（非 Component 包装）")
    print("  - ft.unwrap_component 可解析 Component._b 到实际控件")
    print("  - 推荐实现: render_component_helper(comp_fn, *args) = comp_fn.__component_impl__(*args)")
    sys.exit(0)
