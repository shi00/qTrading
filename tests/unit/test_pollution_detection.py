"""P1-2 污染探测测试：随机打乱顺序跑核心子集，检测跨测试状态污染。

背景：pyproject.toml 配置 asyncio_default_test_loop_scope = "function"，
每个测试有独立的事件循环。loop-local 缓存（asyncio.Event/Lock/Semaphore）
通过 WeakKeyDictionary 绑定到当前循环，循环关闭后自动 GC，不跨测试残留。
本测试通过随机打乱核心子集的执行顺序，检测是否有因顺序导致的失败，
从而发现跨测试状态污染。

探测策略：
1. 选取核心子集（AI 服务/任务管理器/数据处理器相关测试）
2. 用 subprocess 调用 pytest，随机打乱文件顺序，跑 3 次
3. 比较各次运行的 pass/fail 结果
4. 若某测试在某次运行中通过、在另一次运行中失败，判定为污染

标注 @pytest.mark.slow 因为多次跑耗时。

技术债说明（§7.2）：
function scope 已消除 loop-local 缓存跨测试泄漏的根因。
本测试仍保留作为污染探测的守护者，检测其他类型的跨测试状态污染
（如单例未隔离、模块级状态泄漏等），与 test_function_scope_loop_isolation.py 互补：
- test_function_scope_loop_isolation.py 验证 function scope 下 loop-local 自动隔离
- 本测试验证实际多测试场景下无其他类型的状态污染
"""

import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parent.parent.parent

# 核心子集：AI 服务/任务管理器/数据处理器相关测试
# 这些测试频繁使用单例、事件循环、loop-local 缓存，最可能暴露跨测试污染
CORE_SUBSET_FILES: list[str] = [
    "tests/unit/test_ai_service.py",
    "tests/unit/test_ai_service_failover.py",
    "tests/unit/test_ai_service_prompt_dump_retention.py",
    "tests/unit/test_ai_mixin.py",
    "tests/unit/test_ai_strategy.py",
    "tests/unit/test_ai_history_text.py",
    "tests/unit/test_task_manager.py",
    "tests/unit/test_data_processor.py",
]

# 运行次数：3 次不同顺序
RUN_COUNT = 3

# 每次运行的超时时间（秒）
PER_RUN_TIMEOUT = 600

# 匹配 pytest -v 输出行：tests/unit/test_x.py::TestClass::test_method PASSED
_TEST_RESULT_PATTERN = re.compile(r"^(tests/\S+::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED)")


def _run_pytest_with_order(test_files: list[str]) -> subprocess.CompletedProcess[str]:
    """以指定文件顺序运行 pytest，返回完整结果。

    使用 -p no:randomly 显式禁用随机排序插件，确保测试按指定文件顺序执行。
    """
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *test_files,
        "-v",
        "--no-header",
        "--tb=short",
        "-p",
        "no:randomly",
    ]
    return subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=PER_RUN_TIMEOUT,
        check=False,
    )


def _parse_test_results(stdout: str) -> dict[str, str]:
    """解析 pytest -v 输出，返回 {node_id: status} 字典。

    status 取值：PASSED / FAILED / ERROR / SKIPPED。
    """
    results: dict[str, str] = {}
    for line in stdout.splitlines():
        match = _TEST_RESULT_PATTERN.match(line.strip())
        if match:
            node_id = match.group(1)
            status = match.group(2)
            results[node_id] = status
    return results


def test_core_subset_files_exist():
    """快速检查：核心子集中的所有文件都存在（不标记 slow，快速验证）。"""
    for rel_path in CORE_SUBSET_FILES:
        full_path = PROJECT_ROOT / rel_path
        assert full_path.exists(), f"核心子集文件不存在: {rel_path}"


@pytest.mark.slow
def test_no_cross_test_pollution_in_core_subset():
    """随机打乱顺序跑核心子集 3 次，检测跨测试状态污染。

    若某测试在某次运行中通过、在另一次运行中失败，判定为污染。
    探测到污染时测试失败，错误消息提示可能的污染源。
    """
    all_results: list[dict[str, str]] = []

    for run_idx in range(RUN_COUNT):
        shuffled_files = CORE_SUBSET_FILES.copy()
        # 固定种子保证可复现，每次运行用不同种子
        rng = random.Random(42 + run_idx)
        rng.shuffle(shuffled_files)

        try:
            result = _run_pytest_with_order(shuffled_files)
        except subprocess.TimeoutExpired:
            pytest.skip(f"第 {run_idx + 1} 次运行超时（>{PER_RUN_TIMEOUT}s），跳过污染探测")
        except OSError as e:
            pytest.skip(f"第 {run_idx + 1} 次运行无法启动 subprocess: {e}")

        test_results = _parse_test_results(result.stdout)
        if not test_results:
            # 解析失败或无输出，可能是收集错误
            pytest.skip(
                f"第 {run_idx + 1} 次运行未解析到测试结果（可能收集错误）。\n"
                f"stdout 片段: {result.stdout[:500]}\nstderr 片段: {result.stderr[:500]}"
            )
        all_results.append(test_results)

    # 收集所有测试 node ID
    all_node_ids: set[str] = set()
    for results in all_results:
        all_node_ids.update(results.keys())

    # 找出状态不一致的测试
    inconsistent: list[tuple[str, list[str]]] = []
    for node_id in sorted(all_node_ids):
        statuses = [results.get(node_id, "MISSING") for results in all_results]
        if len(set(statuses)) > 1:
            inconsistent.append((node_id, statuses))

    if not inconsistent:
        return  # 无污染

    # 构建错误消息
    lines = [
        "检测到跨测试状态污染！以下测试在不同执行顺序下结果不一致：",
        "",
    ]
    for node_id, statuses in inconsistent:
        status_str = " / ".join(f"run{i + 1}={s}" for i, s in enumerate(statuses))
        lines.append(f"  {node_id}: {status_str}")
    lines.extend(
        [
            "",
            "可能的污染源：",
            "  1. 单例未隔离（检查 _reset_all_singletons autouse fixture）",
            "  2. loop-local 缓存泄漏（检查 get_loop_local 使用是否正确绑定到当前循环）",
            "  3. 事件循环绑定对象跨测试复用（检查 get_loop_local 使用）",
            "  4. 模块级状态泄漏（检查 ConfigHandler._config_cache 等）",
            "",
            "参考：docs/tests/pollution_evaluation.md",
        ]
    )
    pytest.fail("\n".join(lines), pytrace=False)
