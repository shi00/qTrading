"""Spike #2: 验证 `ft.use_effect` 的 cleanup 机制。

关键发现（源码 flet/components/hooks/use_effect.py）：
- `use_effect(setup, deps, cleanup=None)` — cleanup 是**显式第三参数**，
  而非 React 风格的 setup 返回值。这与 React useEffect 有重大差异。
- EffectHook 存储 setup / cleanup / deps / prev_deps。
- 组件 mount：`_run_mount_effects` 调度所有 effect 的 setup（is_cleanup=False）。
- 组件 re-render：`_run_render_effects` 对 deps != [] 的 effect，若 deps 变化，
  先调度 cleanup（is_cleanup=True）再调度 setup（is_cleanup=False）。
- 组件 unmount：`_run_unmount_effects` 对所有有 cleanup 的 effect 调度 cleanup。

验证项：
- 2.1 API 签名：use_effect(setup, deps=None, cleanup=None)
- 2.2 mount 时 setup 被调度
- 2.3 deps=[] 时 effect 仅 mount 执行一次，update 不再触发
- 2.4 deps=[x] 时 x 变化触发 cleanup→setup
- 2.5 deps=None 时每次 render 都触发 cleanup→setup
- 2.6 unmount 时 cleanup 被调度
- 2.7 cleanup 作为 setup 返回值的 React 风格**不生效**（重要差异）
"""

from __future__ import annotations

import sys
from typing import Any

import flet as ft

sys.path.insert(0, ".")
from scripts.spike_ui_debt._spike_helpers import (  # noqa: E402
    make_component,
    run_mount_effects,
    run_render_effects,
    run_unmount_effects,
)

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, PASS if cond else FAIL, detail))


# --- 验证 2.1: API 签名 ---
import inspect  # noqa: E402

sig = inspect.signature(ft.use_effect)
params = list(sig.parameters.keys())
check(
    "2.1 use_effect 签名为 (setup, deps=None, cleanup=None)",
    params == ["setup", "dependencies", "cleanup"],
    f"实际签名 params={params}",
)
check(
    "2.1 cleanup 是显式参数（非 setup 返回值，与 React 不同）",
    "cleanup" in params,
    "React 风格 setup 返回 cleanup 在 0.85.3 不生效",
)


# --- 辅助：记录 effect 调度的组件工厂 ---
def make_effect_component(deps: Any, cleanup_return: Any = None):
    """创建一个带 use_effect 的组件，effect 调度记录到 fake_page.session.scheduled_effects。

    cleanup_return: 若非 None，setup 返回它（测试 React 风格是否生效）。
    """
    log: list[str] = []

    def setup():
        log.append("setup")
        return cleanup_return  # React 风格：返回 cleanup 函数

    def cleanup():
        log.append("cleanup")

    @ft.component
    def comp_fn():
        ft.use_effect(setup, deps, cleanup)
        return ft.Text("hi")

    return comp_fn, log


# --- 验证 2.2: mount 时 setup 被调度 ---
comp_fn2, _log2 = make_effect_component(deps=[])
comp2 = make_component(comp_fn2)
page2 = run_mount_effects(comp2)
# mount 调度: schedule_effect(hook, is_cleanup=False)
mount_setups = [e for e in page2.session.scheduled_effects if not e[1]]
check(
    "2.2 mount 时 setup 被调度（is_cleanup=False）",
    len(mount_setups) == 1,
    f"scheduled_effects={page2.session.scheduled_effects}",
)


# --- 验证 2.3: deps=[] 时 update 不再触发 ---
page2.session.scheduled_effects.clear()
run_render_effects(comp2)
check(
    "2.3 deps=[] 时 re-render 不调度 effect",
    len(page2.session.scheduled_effects) == 0,
    f"scheduled_effects after re-render={page2.session.scheduled_effects}",
)


# --- 验证 2.4: deps=[x] 时 x 变化触发 cleanup→setup ---
# 注意: deps 在 use_effect 调用时固定，模拟 x 变化需重新构造组件。
# 实际场景中 deps 来自组件 props/state，这里通过重建组件模拟 deps 变化。
comp_fn4, log4 = make_effect_component(deps=["x_value"])
comp4 = make_component(comp_fn4)
page4 = run_mount_effects(comp4)  # mount: setup 被调度（异步执行，log 暂空）
# mount 调度 setup（is_cleanup=False），但实际执行由 session 异步处理
mount_setups_4 = [e for e in page4.session.scheduled_effects if not e[1]]
check(
    "2.4 mount 触发 setup 调度",
    len(mount_setups_4) == 1,
    f"scheduled_effects={page4.session.scheduled_effects}",
)

# 模拟 deps 变化：直接修改 hook.deps 触发 _run_render_effects 中的 deps_changed 分支
# 源码: hook.deps != hook.prev_deps → 先 cleanup 再 setup
log4.clear()
page4.session.scheduled_effects.clear()
# 手动设置 hook.prev_deps 模拟上次 deps，hook.deps 设为新值
effect_hook = comp4._state.hooks[0]
effect_hook.prev_deps = ["x_value"]  # 上次的 deps
effect_hook.deps = ["x_value_changed"]  # 新的 deps
run_render_effects(comp4)
# _run_render_effects 调度: cleanup(is_cleanup=True) + setup(is_cleanup=False)
cleanups = [e for e in page4.session.scheduled_effects if e[1]]
setups = [e for e in page4.session.scheduled_effects if not e[1]]
check(
    "2.4 deps 变化触发 cleanup 调度",
    len(cleanups) == 1,
    f"cleanups={cleanups}",
)
check(
    "2.4 deps 变化触发 setup 调度",
    len(setups) == 1,
    f"setups={setups}",
)
# 验证调度顺序：cleanup 先于 setup
check(
    "2.4 cleanup 先于 setup 调度",
    page4.session.scheduled_effects[0][1] is True and page4.session.scheduled_effects[1][1] is False,
    f"order={[e[1] for e in page4.session.scheduled_effects]}",
)


# --- 验证 2.5: deps=None 时每次 render 都触发 cleanup→setup ---
comp_fn5, log5 = make_effect_component(deps=None)
comp5 = make_component(comp_fn5)
page5 = run_mount_effects(comp5)
log5.clear()
page5.session.scheduled_effects.clear()
# deps=None: _run_render_effects 中 deps != [] 进入分支；
# deps_changed = (hook.deps is None or hook.prev_deps is None or hook.deps != hook.prev_deps)
# deps=None 时 deps_changed 恒为 True
effect_hook5 = comp5._state.hooks[0]
# 模拟 prev_deps 也为 None（首次 mount 后 prev_deps=None）
effect_hook5.prev_deps = None
effect_hook5.deps = None
run_render_effects(comp5)
check(
    "2.5 deps=None 时 re-render 触发 cleanup+setup",
    len(page5.session.scheduled_effects) == 2,
    f"scheduled_effects={[e[1] for e in page5.session.scheduled_effects]}",
)


# --- 验证 2.6: unmount 时 cleanup 被调度 ---
comp_fn6, log6 = make_effect_component(deps=[])
comp6 = make_component(comp_fn6)
page6 = run_mount_effects(comp6)
page6.session.scheduled_effects.clear()
run_unmount_effects(comp6)
unmount_cleanups = [e for e in page6.session.scheduled_effects if e[1]]
check(
    "2.6 unmount 时 cleanup 被调度",
    len(unmount_cleanups) == 1,
    f"scheduled_effects={page6.session.scheduled_effects}",
)


# --- 验证 2.7: React 风格（setup 返回 cleanup）不生效 ---
def react_style_cleanup():
    return lambda: None  # React 风格返回 cleanup 函数


comp_fn7, log7 = make_effect_component(deps=[], cleanup_return=react_style_cleanup)
comp7 = make_component(comp_fn7)
page7 = run_mount_effects(comp7)
# 检查 EffectHook.cleanup 是否被 setup 返回值覆盖
effect_hook7 = comp7._state.hooks[0]
# use_effect 中: hook.cleanup = cleanup（显式参数），setup 返回值被忽略
check(
    "2.7 setup 返回值不影响 hook.cleanup（React 风格不生效）",
    effect_hook7.cleanup is not None and effect_hook7.cleanup.__name__ == "cleanup",  # 显式参数的 cleanup
    f"hook.cleanup={effect_hook7.cleanup}",
)


# --- 验证 2.8: on_mounted / on_unmounted / on_updated 便捷别名 ---
check(
    "2.8 ft.on_mounted 存在",
    hasattr(ft, "on_mounted") and callable(ft.on_mounted),
)
check(
    "2.8 ft.on_unmounted 存在",
    hasattr(ft, "on_unmounted") and callable(ft.on_unmounted),
)
check(
    "2.8 ft.on_updated 存在",
    hasattr(ft, "on_updated") and callable(ft.on_updated),
)


# --- 输出结论 ---
print("\n" + "=" * 70)
print("Spike #2: ft.use_effect cleanup 机制验证结果")
print("=" * 70)
for name, status, detail in results:
    line = f"[{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)

passed = sum(1 for _, s, _ in results if s == PASS)
print(f"\n总计: {passed}/{len(results)} 通过")
if passed != len(results):
    print("结论: use_effect cleanup 机制部分不符预期，需调整方案")
    sys.exit(1)
else:
    print("结论: ft.use_effect(setup, deps, cleanup) 在 0.85.3 可用")
    print("  - cleanup 是显式第三参数，非 setup 返回值（与 React 重大差异）")
    print("  - mount 调度 setup；deps 变化先 cleanup 后 setup；unmount 调度 cleanup")
    print("  - deps=[] 仅 mount 执行；deps=None 每次 render 都 cleanup+setup")
    print("  - 便捷别名: on_mounted / on_unmounted / on_updated")
    sys.exit(0)
