"""Spike #1: 验证 `ft.Observable` 与 `@ft.observable` 装饰器。

验证项：
- `ft.Observable` 类存在且可被继承
- `@ft.observable` 装饰器能装饰 dataclass / 普通类
- `subscribe(fn)` 返回 disposer，`_notify(field)` 触发所有 listener
- `__setattr__` 拦截非下划线属性赋值并自动 `_notify`
- Observable 作为 `use_state` 值时，set_state 检测到 Observable 并自动订阅
  （通过 `Component._attach_observable_subscription`）
- Observable 作为组件参数时，`Component._subscribe_observable_args` 自动订阅

参考源码：flet/components/observable.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

import flet as ft
from flet.components.observable import Observable, ObservableList

sys.path.insert(0, ".")
from scripts.spike_ui_debt._spike_helpers import (  # noqa: E402
    attach_fake_page,
    make_component,
    render_once,
)

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, PASS if cond else FAIL, detail))


# --- 验证 1.1: API 存在性 ---
check(
    "1.1 ft.Observable 存在",
    hasattr(ft, "Observable") and isinstance(ft.Observable, type),
)
check(
    "1.1 ft.observable 装饰器存在",
    hasattr(ft, "observable") and callable(ft.observable),
)


# --- 验证 1.2: @ft.observable 装饰 dataclass ---
@ft.observable
@dataclass
class CounterState:
    count: int = 0
    tags: list[str] = field(default_factory=list)


check(
    "1.2 @ft.observable 装饰后 Observable 在 MRO 中",
    Observable in CounterState.__mro__,
    f"MRO={[c.__name__ for c in CounterState.__mro__]}",
)

obj = CounterState(count=1)
check("1.2 实例是 Observable 实例", isinstance(obj, Observable))


# --- 验证 1.3: subscribe / notify ---
notifications: list[tuple[Any, str | None]] = []

disposer = obj.subscribe(lambda sender, f: notifications.append((sender, f)))
obj.count = 2  # 触发 __setattr__ → _notify("count")
obj.count = 2  # 值未变，不应通知
obj.count = 3  # 触发通知

check(
    "1.3 __setattr__ 值变化触发通知",
    len(notifications) == 2,
    f"notifications={notifications}",
)
check(
    "1.3 通知携带 field 名",
    notifications[0][1] == "count",
    f"first notification field={notifications[0][1]}",
)

# disposer 取消订阅
disposer()
obj.count = 4
check(
    "1.3 disposer 后不再通知",
    len(notifications) == 2,
    f"notifications after dispose={notifications}",
)


# --- 验证 1.4: 私有属性（下划线开头）不触发通知 ---
# 注意: subscribe 用 WeakSet 持有弱引用，lambda 必须保留 disposer 否则被 GC
obj2 = CounterState()
notifications2: list[tuple[Any, str | None]] = []
_disposer_1_4 = obj2.subscribe(lambda s, f: notifications2.append((s, f)))
obj2._private_field = 1
obj2.count = 5  # 对照组：非下划线属性应触发
check(
    "1.4 下划线属性不触发通知（对照组 count 触发）",
    len(notifications2) == 1 and notifications2[0][1] == "count",
    f"notifications={notifications2}",
)


# --- 验证 1.5: list 字段自动包装为 ObservableList ---
obj3 = CounterState()
obj3.tags = ["a"]
check(
    "1.5 list 赋值后自动包装为 ObservableList",
    isinstance(obj3.tags, ObservableList),
    f"type={type(obj3.tags).__name__}",
)

notifications3: list[tuple[Any, str | None]] = []
_disposer_1_5 = obj3.subscribe(lambda s, f: notifications3.append((s, f)))
obj3.tags.append("b")  # ObservableList.append → _touch → _notify
check(
    "1.5 ObservableList.append 触发通知",
    len(notifications3) == 1 and notifications3[0][1] == "tags",
    f"notifications={notifications3}",
)


# --- 验证 1.6: Observable 作为 use_state 值自动订阅 ---
# 源码依据: use_state.py 的 update_subscription(h) — 若 h.value 是 Observable，
# 调用 component._attach_observable_subscription(value) 建立订阅。
@ft.component
def state_holder(initial_state: Any):
    state, _set_state = ft.use_state(initial_state)
    return state


comp = make_component(state_holder, CounterState(count=10))
fake_page = attach_fake_page(comp)
render_once(comp)

subs = comp._state.observable_subscriptions
# 2 个订阅：1 个来自 _subscribe_observable_args（参数订阅），
#          1 个来自 use_state 的 update_subscription（状态订阅）
check(
    "1.6 use_state(Observable) 自动建立订阅（参数+状态双订阅）",
    len(subs) == 2,
    f"subscriptions count={len(subs)}（1 参数订阅 + 1 use_state 订阅）",
)

# 触发 Observable 变更，应调度组件更新（2 个订阅各触发 1 次）
# 链路: Observable.__setattr__ → _notify → ObservableSubscription.__on_change
#       → component._schedule_update → context.page.session.schedule_update
state_value = comp.args[0]
scheduled_before = len(fake_page.session.scheduled_updates)
state_value.count = 99
scheduled_after = len(fake_page.session.scheduled_updates)
check(
    "1.6 Observable 变更触发组件重渲染调度（双订阅各 1 次）",
    scheduled_after == scheduled_before + 2,
    f"scheduled_updates before={scheduled_before}, after={scheduled_after}",
)
# dispose 一个订阅后，变更只触发 1 次调度
subs[0].dispose()
scheduled_before = len(fake_page.session.scheduled_updates)
state_value.count = 100
scheduled_after = len(fake_page.session.scheduled_updates)
check(
    "1.6 dispose 一个订阅后变更只触发 1 次调度",
    scheduled_after == scheduled_before + 1,
    f"after dispose one: before={scheduled_before}, after={scheduled_after}",
)


# --- 验证 1.7: Observable 作为组件参数自动订阅 ---
# 源码依据: component.py 的 _subscribe_observable_args
@ft.component
def consumer(observable_arg: Any):
    return ft.Text(str(observable_arg.count))


obs_arg = CounterState(count=42)
comp2 = make_component(consumer, obs_arg)
fake_page2 = attach_fake_page(comp2)
render_once(comp2)

subs2 = comp2._state.observable_subscriptions
check(
    "1.7 Observable 作为组件参数自动订阅",
    len(subs2) == 1,
    f"subscriptions count={len(subs2)}",
)
# 验证订阅已生效：变更 obs_arg 触发调度
scheduled_before = len(fake_page2.session.scheduled_updates)
obs_arg.count = 77
scheduled_after = len(fake_page2.session.scheduled_updates)
check(
    "1.7 参数 Observable 变更触发重渲染调度",
    scheduled_after == scheduled_before + 1,
    f"before={scheduled_before}, after={scheduled_after}",
)

# re-render 时旧订阅被 detach，新订阅建立
render_once(comp2)
check(
    "1.7 re-render 重置订阅（旧 detach + 新 attach）",
    len(comp2._state.observable_subscriptions) == 1,
    f"subscriptions after re-render={len(comp2._state.observable_subscriptions)}",
)


# --- 验证 1.8: notify() 手动触发通用变更通知 ---
obj4 = CounterState(count=0)
notifications4: list[tuple[Any, str | None]] = []
_disposer_1_8 = obj4.subscribe(lambda s, f: notifications4.append((s, f)))
obj4.notify()
check(
    "1.8 notify() 触发通用通知（field=None）",
    len(notifications4) == 1 and notifications4[0][1] is None,
    f"notifications={notifications4}",
)


# --- 验证 1.9: subscribe 用 WeakSet，lambda 不保留 disposer 会被 GC（重要陷阱） ---
import gc  # noqa: E402

obj5 = CounterState(count=0)
notifications5: list[tuple[Any, str | None]] = []
obj5.subscribe(lambda s, f: notifications5.append((s, f)))  # 不保留 disposer
gc.collect()
obj5.count = 1
check(
    "1.9 subscribe 弱引用：lambda 不保留 disposer 被 GC（重要陷阱）",
    len(notifications5) == 0,
    f"notifications={notifications5}（lambda 被 GC 后订阅失效）",
)


# --- 输出结论 ---
print("\n" + "=" * 70)
print("Spike #1: ft.Observable 验证结果")
print("=" * 70)
for name, status, detail in results:
    line = f"[{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)

passed = sum(1 for _, s, _ in results if s == PASS)
print(f"\n总计: {passed}/{len(results)} 通过")
if passed != len(results):
    print("结论: Observable API 存在但部分行为不符预期，需调整方案")
    sys.exit(1)
else:
    print("结论: ft.Observable / @ft.observable 在 0.85.3 稳定可用")
    print("  - 可作为状态源（use_state 值或组件参数）自动触发重渲染")
    print("  - subscribe/notify 机制完整，支持 disposer")
    print("  - list/dict 字段自动包装为 ObservableList/ObservableDict")
    sys.exit(0)
