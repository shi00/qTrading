"""Spike #6: 验证 `dataclasses.replace` 在高频更新场景的性能。

场景：LLM 流式响应，每秒 50+ 次 append logs。
目标态：frozen dataclass state snapshot，每次 append 用 `dataclasses.replace`
生成新 tuple。

验证项：
- 6.1 frozen dataclass replace 单次开销
- 6.2 tuple append（replace 生成新 tuple）单次开销
- 6.3 模拟 LLM 流式 50 次/秒 × 10 秒 = 500 次 replace 的总耗时
- 6.4 与 mutable list.append 对比（性能基线）
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, replace
from statistics import mean

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, PASS if cond else FAIL, detail))


def time_ms(func, runs: int = 3) -> float:
    """运行 func runs 次，返回均值 ms。"""
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        func()
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)
    return mean(times)


# --- frozen dataclass state snapshot 定义 ---
@dataclass(frozen=True)
class LogEntry:
    text: str
    timestamp: float = 0.0


@dataclass(frozen=True)
class AppState:
    logs: tuple[LogEntry, ...] = ()
    count: int = 0
    name: str = ""


# --- 验证 6.1: frozen dataclass replace 单次开销 ---


def bench_replace_single():
    state = AppState(logs=(LogEntry("init"),), count=0, name="test")
    for i in range(1000):
        state = replace(state, count=i)


mean_61 = time_ms(bench_replace_single, runs=5) / 1000  # per-op ms
check(
    "6.1 frozen dataclass replace 单次 < 0.05ms",
    mean_61 < 0.05,
    f"per-op={mean_61:.5f}ms",
)


# --- 验证 6.2: tuple append（replace 生成新 tuple）单次开销 ---


def bench_tuple_append():
    state = AppState(logs=(), count=0, name="test")
    for i in range(500):
        new_log = LogEntry(text=f"log_{i}", timestamp=float(i))
        # 目标态写法: replace 生成新 tuple
        state = replace(state, logs=state.logs + (new_log,))


mean_62 = time_ms(bench_tuple_append, runs=5) / 500  # per-op ms
check(
    "6.2 tuple append via replace 单次 < 0.5ms（500 次累积）",
    mean_62 < 0.5,
    f"per-op={mean_62:.5f}ms",
)


# --- 验证 6.3: LLM 流式 50 次/秒 × 10 秒 = 500 次 replace ---
# 关键：tuple 持续增长，每次 +1 元素，500 次后 tuple 长度 500


def bench_llm_stream_500():
    state = AppState(logs=(), count=0, name="stream")
    for i in range(500):
        new_log = LogEntry(text=f"chunk_{i}", timestamp=float(i))
        state = replace(state, logs=state.logs + (new_log,))
    return state


mean_63 = time_ms(bench_llm_stream_500, runs=3)
# 500 次累积 replace 应在 50ms 内（每秒 50 次 = 20ms/次预算，500 次 = 10s 数据量）
check(
    "6.3 LLM 流式 500 次 replace 总耗时 < 50ms",
    mean_63 < 50,
    f"total={mean_63:.2f}ms for 500 appends",
)
# 验证最终 tuple 长度
final_state = bench_llm_stream_500()
check(
    "6.3 500 次 append 后 tuple 长度 = 500",
    len(final_state.logs) == 500,
    f"len={len(final_state.logs)}",
)


# --- 验证 6.4: 与 mutable list.append 对比 ---


def bench_mutable_list():
    logs: list[LogEntry] = []
    state = {"logs": logs, "count": 0, "name": "test"}
    for i in range(500):
        new_log = LogEntry(text=f"log_{i}", timestamp=float(i))
        state["logs"].append(new_log)


mean_64 = time_ms(bench_mutable_list, runs=3)
ratio = mean_63 / mean_64 if mean_64 > 0 else float("inf")
check(
    "6.4 frozen replace 比 mutable list 慢 < 100x（可接受范围）",
    ratio < 100,
    f"frozen={mean_63:.2f}ms, mutable={mean_64:.2f}ms, ratio={ratio:.1f}x",
)


# --- 验证 6.5: tuple 拼接 vs extend 性能对比 ---
# state.logs + (new,) 每次创建新 tuple，O(n) 拷贝
# 替代方案：用 list 转 tuple


def bench_tuple_extend():
    state = AppState(logs=(), count=0, name="test")
    for i in range(500):
        new_log = LogEntry(text=f"log_{i}", timestamp=float(i))
        # 替代：临时 list
        state = replace(state, logs=tuple([*state.logs, new_log]))


mean_65 = time_ms(bench_tuple_extend, runs=3)
check(
    "6.5 tuple 拼接 vs [*logs, x] 性能相当（< 2x）",
    mean_65 / mean_63 < 2 if mean_63 > 0 else True,
    f"concat={mean_63:.2f}ms, splat={mean_65:.2f}ms",
)


# --- 验证 6.6: frozen dataclass 不可变性保证 ---
state_immutable = AppState(logs=(LogEntry("a"),), count=1)
mutation_blocked = False
try:
    state_immutable.count = 2  # type: ignore[misc]
except Exception:
    mutation_blocked = True
check(
    "6.6 frozen dataclass 阻止属性突变",
    mutation_blocked,
    "frozen=True 确保 state snapshot 不可变",
)


# --- 输出结论 ---
print("\n" + "=" * 70)
print("Spike #6: frozen dataclass state snapshot 性能验证结果")
print("=" * 70)
for name, status, detail in results:
    line = f"[{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)

passed = sum(1 for _, s, _ in results if s == PASS)
print(f"\n总计: {passed}/{len(results)} 通过")
if passed != len(results):
    print("结论: frozen dataclass 性能不达标，需调整方案")
    sys.exit(1)
else:
    print("结论: frozen dataclass + replace + tuple 性能可接受")
    print(f"  - replace 单次: {mean_61:.5f}ms")
    print(f"  - tuple append 单次（500 累积）: {mean_62:.5f}ms")
    print(f"  - LLM 流式 500 次 replace: {mean_63:.2f}ms（预算 50ms）")
    print(f"  - vs mutable list: {ratio:.1f}x 慢（可接受）")
    print(f"  - tuple 拼接 vs splat: {mean_65 / mean_63:.2f}x（性能相当）")
    print("  - frozen=True 保证不可变性")
    print("  - 适用场景: LLM 流式响应 50次/秒 无压力")
    sys.exit(0)
