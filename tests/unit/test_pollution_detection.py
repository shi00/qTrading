"""P1-2 污染探测测试：随机打乱顺序跑核心子集，检测跨测试状态污染。

背景：pyproject.toml 配置 asyncio_default_test_loop_scope = "function"，
每个测试有独立的事件循环。loop-local 缓存（asyncio.Event/Lock/Semaphore）
通过 WeakKeyDictionary 绑定到当前循环，循环关闭后自动 GC，不跨测试残留。
本测试通过随机打乱核心子集的执行顺序，检测是否有因顺序导致的失败，
从而发现跨测试状态污染。

探测策略：
1. 选取核心子集（AI 服务/任务管理器/数据处理器相关测试）
2. 用 subprocess 调用 pytest，随机打乱文件顺序，跑 3 次
3. 校验每轮子 pytest 退出码为 0 且解析出的节点集合一致，否则判定探测无效
4. 比较各次运行的 pass/fail 结果
5. 若某测试在某次运行中通过、在另一次运行中失败，判定为污染

标注 @pytest.mark.slow 因为多次跑耗时。CI 中仅在 nightly flaky-detection job 的专用 step
执行一次；PR 单测与 10 轮 flaky 循环均以 --deselect 排除（详见 docs/guides/ci-cd.md）。

技术债说明（§7.2）：
function scope 已消除 loop-local 缓存跨测试泄漏的根因。
本测试仍保留作为污染探测的守护者，检测其他类型的跨测试状态污染
（如单例未隔离、模块级状态泄漏等），与 test_function_scope_loop_isolation.py 互补：
- test_function_scope_loop_isolation.py 验证 function scope 下 loop-local 自动隔离
- 本测试验证实际多测试场景下无其他类型的状态污染
"""

import os
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

    - 使用 -p no:randomly 显式禁用随机排序插件，确保测试按指定文件顺序执行。
    - 使用 -n 0 覆盖 pyproject addopts 中的 ``-n auto``，强制串行执行：
      ① xdist 会把文件分发到独立 worker 进程，各进程互相隔离，反而掩盖
      跨测试状态污染，与探测目标相悖；② xdist 的 ``-v`` 输出为
      ``[gwN] PASSED <nodeid>``，与本模块解析的 ``<nodeid> PASSED`` 格式不符，
      会导致解析为空并 skip。
    - 使用 --color=no 避免 CI 环境变量注入 ANSI 转义符污染解析。
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
        "-n",
        "0",
        "--color=no",
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


def _abort(message: str) -> None:
    """CI 中解析/启动异常应 fail，避免守护测试静默变绿；本地开发仍 skip 以便快速迭代。"""
    if os.environ.get("CI"):
        pytest.fail(message, pytrace=False)
    pytest.skip(message)


# 子 pytest 非零退出码的含义（pytest ExitCode）；未列出者按信号终止处理
_PROBE_EXIT_REASONS: dict[int, str] = {
    1: "存在失败测试，可能为基线失败，不能据此判定顺序污染",
    2: "测试收集或执行被中断",
    3: "pytest 内部错误",
    4: "pytest 用法错误",
    5: "未收集到任何测试",
}


def _ensure_run_completed(result: subprocess.CompletedProcess[str], run_idx: int) -> None:
    """校验子 pytest 完整成功完成；非零退出说明输出可能只是部分结果，不能据此判定污染。"""
    if result.returncode == 0:
        return
    reason = _PROBE_EXIT_REASONS.get(result.returncode, "子进程被信号终止或异常退出（可能 OOM/崩溃/中断）")
    _abort(
        f"第 {run_idx + 1} 次运行退出码 {result.returncode}（{reason}），探测无效：\n"
        "子 pytest 未完整成功完成，其输出可能只是部分结果，不能据此判定无污染。\n"
        f"stdout 片段: {result.stdout[:500]}\nstderr 片段: {result.stderr[:500]}"
    )


def test_core_subset_files_exist():
    """快速检查：核心子集中的所有文件都存在（不标记 slow，快速验证）。"""
    for rel_path in CORE_SUBSET_FILES:
        full_path = PROJECT_ROOT / rel_path
        assert full_path.exists(), f"核心子集文件不存在: {rel_path}"


def test_parse_test_results_parses_serial_output():
    """守护：-n 0 串行输出必须可解析；回归到 xdist `[gwN]` 格式时会解析为空并 skip。"""
    stdout = (
        "tests/unit/test_a.py::test_pass PASSED   [ 50%]\n"
        "tests/unit/test_b.py::TestX::test_fail FAILED   [100%]\n"
        "[gw0] [ 50%] PASSED tests/unit/test_a.py::test_pass\n"
    )
    assert _parse_test_results(stdout) == {
        "tests/unit/test_a.py::test_pass": "PASSED",
        "tests/unit/test_b.py::TestX::test_fail": "FAILED",
    }


def test_run_command_forces_serial_mode(monkeypatch):
    """守护：子进程命令必须含 -n 0，覆盖 addopts 的 -n auto（xdist 会掩盖跨测试污染）。"""
    captured: dict[str, list[str]] = {}

    class _Result:
        stdout = ""
        stderr = ""
        returncode = 0

    def _fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    _run_pytest_with_order(CORE_SUBSET_FILES)

    cmd = captured["cmd"]
    assert cmd[cmd.index("-n") + 1] == "0"


@pytest.mark.parametrize("returncode", [1, 2])
def test_nonzero_exit_invalidates_probe(monkeypatch, returncode):
    """守护：子 pytest 非零退出时，即使有可解析的部分 PASSED 输出也必须判为探测无效。"""

    def _fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=returncode,
            stdout="tests/unit/test_a.py::test_pass PASSED   [ 50%]\n",
            stderr="E   collection error\n",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setenv("CI", "1")

    with pytest.raises(pytest.fail.Exception) as excinfo:
        test_no_cross_test_pollution_in_core_subset()

    message = str(excinfo.value)
    assert f"退出码 {returncode}" in message
    assert "探测无效" in message
    assert "stdout 片段" in message
    assert "stderr 片段" in message
    assert "检测到跨测试状态污染" not in message


def test_zero_exit_with_consistent_results_passes(monkeypatch):
    """守护：三轮 0 退出且节点/状态一致时探测正常通过。"""

    def _fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="tests/unit/test_a.py::test_pass PASSED   [100%]\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setenv("CI", "1")

    test_no_cross_test_pollution_in_core_subset()


def test_incomplete_node_set_invalidates_probe(monkeypatch):
    """守护：0 退出但各轮节点集合不一致（报告截断）时判为探测无效。"""
    outputs = iter(
        [
            "tests/unit/test_a.py::test_pass PASSED   [100%]\n",
            "tests/unit/test_a.py::test_pass PASSED   [ 50%]\ntests/unit/test_b.py::test_extra PASSED   [100%]\n",
            "tests/unit/test_a.py::test_pass PASSED   [100%]\n",
        ]
    )

    def _fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=next(outputs), stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setenv("CI", "1")

    with pytest.raises(pytest.fail.Exception) as excinfo:
        test_no_cross_test_pollution_in_core_subset()

    message = str(excinfo.value)
    assert "节点集合" in message
    assert "探测无效" in message


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
            _abort(f"第 {run_idx + 1} 次运行超时（>{PER_RUN_TIMEOUT}s）")
        except OSError as e:
            _abort(f"第 {run_idx + 1} 次运行无法启动 subprocess: {e}")

        # 非零退出说明子 pytest 未完整完成，其输出可能只是部分结果，禁止据此下结论
        _ensure_run_completed(result, run_idx)

        test_results = _parse_test_results(result.stdout)
        if not test_results:
            # 解析失败或无输出，可能是收集错误
            _abort(
                f"第 {run_idx + 1} 次运行未解析到测试结果（可能收集错误）。\n"
                f"stdout 片段: {result.stdout[:500]}\nstderr 片段: {result.stderr[:500]}"
            )
        all_results.append(test_results)

    # 节点集合必须三轮一致：不一致说明报告不完整/被截断，同样不能比较顺序结果
    expected_node_ids = set(all_results[0])
    for run_idx, results in enumerate(all_results[1:], start=2):
        run_node_ids = set(results)
        if run_node_ids == expected_node_ids:
            continue
        missing = sorted(expected_node_ids - run_node_ids)
        extra = sorted(run_node_ids - expected_node_ids)
        _abort(
            f"第 {run_idx} 次运行节点集合与第 1 次不一致，探测无效："
            "子 pytest 报告可能被截断或收集不完整，不能据此比较执行顺序结果。\n"
            f"缺失节点: {missing[:5]}\n新增节点: {extra[:5]}"
        )

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
