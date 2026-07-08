"""Spike #5: 验证 `ft.use_state(factory())` 实例化行为。

关键问题：use_viewmodel hook 的实现依赖 use_state 持久化 VM 实例。
若 `use_state(factory())` 每次渲染都重新实例化 factory，需改用 `ft.use_ref`。

源码分析（flet/components/hooks/use_state.py）：
```python
def use_state(initial: StateT | Callable[[], StateT]):
    component = current_component()
    hook = component.use_hook(
        lambda: StateHook(
            component,
            initial() if callable(initial) else initial,
        )
    )
```
- `use_hook(default)` 中 `default` 只在 `i >= len(self._state.hooks)`（首次）时调用。
- 所以 `use_state(factory)` 的 factory 只在首次渲染调用。
- 但若调用者写 `use_state(factory())`，`factory()` 在调用 use_state 前就求值，
  每次渲染都会实例化，造成浪费（但 use_state 只首次采用）。

验证项：
- 5.1 use_state(callable) — factory 只首次调用
- 5.2 use_state(value()) — value() 每次渲染都执行（调用者陷阱）
- 5.3 use_state(value) — 直接值，每次渲染传入但只首次采用
- 5.4 use_ref(factory) — 持久化 ref.current，factory 只首次调用
- 5.5 use_ref 持久化 VM 实例的可行性
"""

from __future__ import annotations

import sys

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


# --- 辅助：计数 factory ---
class Factory:
    def __init__(self, name: str):
        self.name = name
        self.call_count = 0

    def __call__(self):
        self.call_count += 1
        return {"id": self.call_count, "name": self.name}


# --- 验证 5.1: use_state(callable) — factory 只首次调用 ---
factory_51 = Factory("vm_5_1")

counter_51 = {"renders": 0}


@ft.component
def comp_5_1():
    counter_51["renders"] += 1
    vm, _set_vm = ft.use_state(factory_51)  # 传入 callable
    return ft.Text(str(vm["id"]))


c51 = make_component(comp_5_1)
render_once(c51)  # 首次渲染
render_once(c51)  # 第 2 次渲染
render_once(c51)  # 第 3 次渲染
check(
    "5.1 use_state(callable) factory 仅首次调用 1 次",
    factory_51.call_count == 1,
    f"factory.call_count={factory_51.call_count}, renders={counter_51['renders']}",
)
# 验证 hook.value 保持首次实例
hook51 = c51._state.hooks[0]
check(
    "5.1 use_state 持久化首次 factory 返回值",
    hook51.value["id"] == 1,
    f"hook.value={hook51.value}",
)


# --- 验证 5.2: use_state(value()) — value() 每次渲染都执行（陷阱） ---
factory_52 = Factory("vm_5_2")
counter_52 = {"renders": 0}


@ft.component
def comp_5_2():
    counter_52["renders"] += 1
    # 陷阱：factory_52() 在调用 use_state 前求值，每次渲染都执行
    vm, _set_vm = ft.use_state(factory_52())
    return ft.Text(str(vm["id"]))


c52 = make_component(comp_5_2)
render_once(c52)  # 首次
render_once(c52)  # 第 2 次
render_once(c52)  # 第 3 次
check(
    "5.2 use_state(factory()) 每次渲染都实例化（调用者陷阱）",
    factory_52.call_count == counter_52["renders"],
    f"factory.call_count={factory_52.call_count}, renders={counter_52['renders']}",
)
# hook.value 仍是首次实例（use_state 只首次采用）
hook52 = c52._state.hooks[0]
check(
    "5.2 hook.value 仍持久化首次实例（use_state 只首次采用）",
    hook52.value["id"] == 1,
    f"hook.value={hook52.value}（被丢弃的实例造成 GC 压力）",
)


# --- 验证 5.3: use_state(value) — 直接值，每次传入但只首次采用 ---
counter_53 = {"renders": 0, "values": []}


@ft.component
def comp_5_3():
    counter_53["renders"] += 1
    # 每次渲染传入新值，但 use_state 只首次采用
    val = {"render": counter_53["renders"]}
    counter_53["values"].append(val)
    vm, _set_vm = ft.use_state(val)
    return ft.Text(str(vm))


c53 = make_component(comp_5_3)
render_once(c53)
render_once(c53)
render_once(c53)
hook53 = c53._state.hooks[0]
check(
    "5.3 use_state(value) hook.value 持久化首次值",
    hook53.value is counter_53["values"][0],
    f"hook.value is first value: {hook53.value is counter_53['values'][0]}",
)


# --- 验证 5.4: use_ref(factory) — 持久化 ref.current，factory 只首次调用 ---
factory_54 = Factory("ref_5_4")
counter_54 = {"renders": 0}


@ft.component
def comp_5_4():
    counter_54["renders"] += 1
    ref = ft.use_ref(factory_54)  # callable lazy init
    return ft.Text(str(ref.current["id"]))


c54 = make_component(comp_5_4)
render_once(c54)
render_once(c54)
render_once(c54)
check(
    "5.4 use_ref(callable) factory 仅首次调用 1 次",
    factory_54.call_count == 1,
    f"factory.call_count={factory_54.call_count}, renders={counter_54['renders']}",
)
# 验证 ref.current 跨渲染稳定
hook54 = c54._state.hooks[0]
ref_id_render1 = id(hook54.ref.current)
render_once(c54)
ref_id_render4 = id(hook54.ref.current)
check(
    "5.4 use_ref ref.current 跨渲染稳定（同一对象）",
    ref_id_render1 == ref_id_render4,
    f"id render1={ref_id_render1}, id render4={ref_id_render4}",
)


# --- 验证 5.5: use_ref 持久化 VM 实例的可行性（use_viewmodel 模式） ---


class FakeViewModel:
    def __init__(self, name: str):
        self.name = name
        self.created_at = id(self)

    def mutate(self, v: int) -> None:
        self.value = v


vm_creation_count = {"count": 0}


def make_vm() -> FakeViewModel:
    vm_creation_count["count"] += 1
    return FakeViewModel("persistent_vm")


counter_55 = {"renders": 0}


@ft.component
def comp_5_5():
    counter_55["renders"] += 1
    # use_viewmodel 模式：用 use_ref 持久化 VM 实例
    vm_ref = ft.use_ref(make_vm)
    vm = vm_ref.current
    # VM 状态可自由突变，不触发重渲染（需配合 use_state 触发）
    return ft.Text(vm.name)


c55 = make_component(comp_5_5)
render_once(c55)
render_once(c55)
render_once(c55)
check(
    "5.5 use_ref(make_vm) VM 实例仅创建 1 次",
    vm_creation_count["count"] == 1,
    f"vm creations={vm_creation_count['count']}, renders={counter_55['renders']}",
)
hook55 = c55._state.hooks[0]
vm_id_r1 = id(hook55.ref.current)
render_once(c55)
vm_id_r4 = id(hook55.ref.current)
check(
    "5.5 VM 实例跨渲染保持同一对象",
    vm_id_r1 == vm_id_r4,
    f"vm id stable: {vm_id_r1 == vm_id_r4}",
)


# --- 验证 5.6: use_state + use_ref 组合模式（推荐 use_viewmodel 实现） ---
# VM 实例用 use_ref 持久化，VM 内部状态变更通过 use_state 触发重渲染
vm_creation_count_56 = {"count": 0}
counter_56 = {"renders": 0}


def make_vm_56() -> FakeViewModel:
    vm_creation_count_56["count"] += 1
    return FakeViewModel("combined_vm")


@ft.component
def comp_5_6():
    counter_56["renders"] += 1
    vm_ref = ft.use_ref(make_vm_56)
    _vm = vm_ref.current
    # version state 触发重渲染
    _version, set_version = ft.use_state(0)
    # 模拟 VM.mutate 后 set_version(v+1) 触发重渲染
    return ft.Text(f"v{_version}")


c56 = make_component(comp_5_6)
render_once(c56)
render_once(c56)
render_once(c56)
check(
    "5.6 use_ref+use_state 组合：VM 仅创建 1 次，渲染 3 次",
    vm_creation_count_56["count"] == 1 and counter_56["renders"] == 3,
    f"vm={vm_creation_count_56['count']}, renders={counter_56['renders']}",
)


# --- 输出结论 ---
print("\n" + "=" * 70)
print("Spike #5: use_state(factory()) 实例化行为验证结果")
print("=" * 70)
for name, status, detail in results:
    line = f"[{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)

passed = sum(1 for _, s, _ in results if s == PASS)
print(f"\n总计: {passed}/{len(results)} 通过")
if passed != len(results):
    print("结论: use_state factory 行为不符预期，需调整方案")
    sys.exit(1)
else:
    print("结论: use_viewmodel hook 实现方案确定")
    print("  - use_state(callable) factory 仅首次调用，但 VM 实例需配合触发重渲染")
    print("  - use_state(factory()) 每次渲染都实例化（调用者陷阱，浪费但 hook 只首次采用）")
    print("  - 推荐 use_viewmodel 实现：use_ref(make_vm) 持久化 VM + use_state(version) 触发重渲染")
    print("  - VM 状态突变不自动触发重渲染，需显式 set_version(v+1)")
    sys.exit(0)
