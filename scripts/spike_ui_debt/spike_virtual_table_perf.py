"""Spike #3: 验证 `ft.ListView` 在声明式渲染 1000+ 行时的性能。

目标态采用声明式分页/窗口化/懒加载，但需先验证基线性能：
- 1000 行静态渲染时间
- 5000 行静态渲染时间（压力测试）
- ListView 控件构造本身的开销

注意：声明式渲染 1000+ 行指的是组件树包含 1000+ 子控件。
真正的瓶颈在 session.patch_control 序列化与传输，spike 只验证控件构造开销。
"""

from __future__ import annotations

import sys
import time
from statistics import mean

import flet as ft

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, PASS if cond else FAIL, detail))


def time_ms(func, runs: int = 3) -> tuple[float, float]:
    """运行 func runs 次，返回 (mean_ms, min_ms)。"""
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        func()
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)
    return mean(times), min(times)


# --- 验证 3.1: ListView 控件构造开销（1000 行） ---
def build_listview_1000():
    return ft.ListView(
        controls=[ft.Text(f"Item {i}") for i in range(1000)],
        spacing=2,
    )


mean_1k, min_1k = time_ms(build_listview_1000, runs=5)
# 1000 行 Text 控件构造应在 100ms 内（保守阈值）
check(
    "3.1 ListView 1000 行控件构造均值 < 100ms",
    mean_1k < 100,
    f"mean={mean_1k:.2f}ms, min={min_1k:.2f}ms",
)


# --- 验证 3.2: ListView 5000 行压力测试 ---
def build_listview_5000():
    return ft.ListView(
        controls=[ft.Text(f"Item {i}") for i in range(5000)],
        spacing=2,
    )


mean_5k, min_5k = time_ms(build_listview_5000, runs=3)
# 5000 行应在 500ms 内
check(
    "3.2 ListView 5000 行控件构造均值 < 500ms",
    mean_5k < 500,
    f"mean={mean_5k:.2f}ms, min={min_5k:.2f}ms",
)


# --- 验证 3.3: 声明式组件内构造 1000 行 ---
# 模拟 @ft.component 内部返回 ListView(1000 行)
def build_component_tree_1000():
    items = [ft.Text(f"Row {i}") for i in range(1000)]
    return ft.ListView(controls=items)


mean_comp, min_comp = time_ms(build_component_tree_1000, runs=5)
check(
    "3.3 声明式组件树 1000 行构造均值 < 100ms",
    mean_comp < 100,
    f"mean={mean_comp:.2f}ms, min={min_comp:.2f}ms",
)


# --- 验证 3.4: 单行控件构造开销（基准） ---
def build_single_text():
    return ft.Text("single")


mean_single, _ = time_ms(build_single_text, runs=1000)
per_row_1k = mean_1k / 1000
check(
    "3.4 单行 Text 构造开销 < 0.2ms",
    mean_single < 0.2,
    f"single={mean_single:.4f}ms, per_row(1k)={per_row_1k:.4f}ms",
)


# --- 验证 3.5: ListView build_controls_on_demand 属性存在 ---
# 源码: list_view.py 有 build_controls_on_demand / item_extent / prototype_item
lv = ft.ListView()
check(
    "3.5 ListView 支持懒加载属性 build_controls_on_demand",
    hasattr(lv, "build_controls_on_demand"),
    f"attrs={[a for a in dir(lv) if 'demand' in a or 'extent' in a or 'prototype' in a]}",
)


# --- 验证 3.6: 线性扩展性验证 ---
# 1000 行 vs 5000 行，时间比应接近 5x（线性扩展）
ratio = mean_5k / mean_1k if mean_1k > 0 else float("inf")
check(
    "3.6 1000→5000 行扩展性接近线性（ratio 3-7x）",
    3 <= ratio <= 7,
    f"ratio={ratio:.2f}x (5000行={mean_5k:.2f}ms / 1000行={mean_1k:.2f}ms)",
)


# --- 输出结论 ---
print("\n" + "=" * 70)
print("Spike #3: ListView 1000+ 行声明式性能验证结果")
print("=" * 70)
for name, status, detail in results:
    line = f"[{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)

passed = sum(1 for _, s, _ in results if s == PASS)
print(f"\n总计: {passed}/{len(results)} 通过")
if passed != len(results):
    print("结论: ListView 性能不达标，需调整方案")
    sys.exit(1)
else:
    print("结论: ListView 1000+ 行声明式渲染性能可接受")
    print(f"  - 1000 行控件构造: {mean_1k:.2f}ms (均值), {min_1k:.2f}ms (最小)")
    print(f"  - 5000 行控件构造: {mean_5k:.2f}ms (均值), {min_5k:.2f}ms (最小)")
    print(f"  - 单行 Text 构造: {mean_single:.4f}ms")
    print(f"  - 扩展性: 线性 (ratio={ratio:.2f}x)")
    print("  - 注意: 此为控件构造开销，不含 session.patch 序列化/传输")
    print("  - 目标态建议: 分页/窗口化，单次渲染 < 200 行")
    print("  - ListView 支持 build_controls_on_demand / item_extent 懒加载属性")
    sys.exit(0)
