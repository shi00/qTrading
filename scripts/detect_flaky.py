"""重复运行 pytest 收集 pass/fail 列表，精确定位 flaky 测试 nodeid。

基于 pytest-json-report 输出对比多轮结果，识别 outcome 不一致的测试。
默认 --runs=10，支持 --parallel N（pytest-xdist）、--reruns N（pytest-rerunfailures）、--lf（仅复跑上次失败用例）。

用法：
    python scripts/detect_flaky.py
    python scripts/detect_flaky.py --path tests/unit/ --runs 10
    python scripts/detect_flaky.py --parallel 4 --reruns 2
    python scripts/detect_flaky.py --lf  # 仅复跑上次失败用例
    python scripts/detect_flaky.py --deselect tests/unit/slow_probe.py::test_x  # 排除不应每轮重复的慢测试

退出码：
    0: 未检测到 flaky（所有运行结果一致且无失败用例）
    1: 检测到 flaky（运行结果不一致）
    2: 无法判定（路径不存在、运行出错，或本轮无有效运行：
       报告缺失/为空/不可解析、收集失败、全部轮次一致失败）
"""

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# pytest 仅在这两个退出码下仍产出可判定的完整测试结果：
# 0=全部通过，1=存在失败/错误；2=被中断/收集失败，3=内部错误，4=用法错误，5=未收集到测试。
_VALID_PYTEST_RETURNCODES = frozenset({0, 1})
_FAILED_OUTCOMES = frozenset({"failed", "error"})


@dataclass
class FlakyResult:
    """单个 flaky 测试的对比结果。"""

    nodeid: str
    outcomes_by_run: list[str | None]
    failed_runs: list[int]  # 1-based 轮次
    stdout_summary: str = ""


def parse_json_report(report_path: Path) -> dict[str, str]:
    """解析 pytest-json-report 输出，返回 {nodeid: outcome} 映射。

    缺失 tests 字段时返回空字典（collection 失败或空运行）。
    """
    data = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("pytest json-report 顶层不是 JSON 对象")
    tests = data.get("tests", [])
    return {t["nodeid"]: t["outcome"] for t in tests}


def validate_run_result(returncode: int, outcomes: dict[str, str]) -> str | None:
    """校验单轮运行结果是否可用于 flaky 判定。

    只有 pytest 正常完成（退出码 0/1）且报告含与退出码一致的用例结果时才算有效。
    无效运行不能参与 flaky 判定，否则空报告/收集失败会被误判为「无 flaky」。

    Args:
        returncode: pytest 子进程退出码
        outcomes: 该轮 {nodeid: outcome} 映射

    Returns:
        None 表示有效；否则返回无效原因（供 stderr 提示）
    """
    if not outcomes:
        return "未收集到任何测试（收集失败或空运行）"
    if returncode not in _VALID_PYTEST_RETURNCODES:
        return f"pytest 退出码 {returncode}（收集失败、被中断或运行错误）"
    has_failure = any(outcome in _FAILED_OUTCOMES for outcome in outcomes.values())
    if returncode == 1 and not has_failure:
        return "pytest 退出码 1 但 json-report 中无 failed/error 用例"
    if returncode == 0 and has_failure:
        return "pytest 退出码 0 但 json-report 含 failed/error 用例"
    return None


def identify_flaky_tests(runs: list[dict[str, str]]) -> list[FlakyResult]:
    """对比多轮结果，识别 outcome 不一致的 nodeid。

    Args:
        runs: 每轮 {nodeid: outcome} 映射列表

    Returns:
        flaky 测试结果列表（按 nodeid 排序稳定输出）
    """
    if len(runs) < 2:
        return []

    all_nodeids: set[str] = set()
    for run in runs:
        all_nodeids.update(run.keys())

    flaky: list[FlakyResult] = []
    for nodeid in sorted(all_nodeids):
        outcomes: list[str | None] = [run.get(nodeid) for run in runs]
        # outcome 不一致 或 某轮缺失（None）→ flaky
        unique_outcomes = {o for o in outcomes if o is not None}
        if len(unique_outcomes) > 1 or (None in outcomes and len(unique_outcomes) >= 1):
            failed_runs = [i + 1 for i, o in enumerate(outcomes) if o in (None, "failed", "error")]
            flaky.append(
                FlakyResult(
                    nodeid=nodeid,
                    outcomes_by_run=outcomes,
                    failed_runs=failed_runs,
                    stdout_summary="",
                )
            )
    return flaky


def format_flaky_report(flaky: list[FlakyResult], runs: int, path: str) -> str:
    """格式化 flaky 测试报告输出。

    Args:
        flaky: flaky 测试列表
        runs: 总运行轮数
        path: 测试路径

    Returns:
        格式化的报告字符串
    """
    lines = [f"路径: {path}", f"运行次数: {runs}", ""]

    if not flaky:
        lines.append(f"✓ 未检测到 flaky 测试（{runs} 次运行结果一致）")
        return "\n".join(lines)

    lines.append(f"✗ 检测到 {len(flaky)} 个 flaky 测试：")
    lines.append("")
    for i, result in enumerate(flaky, 1):
        outcomes_str = ", ".join(str(o) for o in result.outcomes_by_run)
        failed_runs_str = str(result.failed_runs)
        lines.append(f"  {i}. {result.nodeid}")
        lines.append(f"     失败轮次: {failed_runs_str}")
        lines.append(f"     outcome 序列: [{outcomes_str}]")
        if result.stdout_summary:
            summary = result.stdout_summary
            if len(summary) > 500:
                summary = summary[:500] + "..."
            lines.append(f"     stdout 摘要: {summary}")
        lines.append("")
    return "\n".join(lines)


def run_pytest_with_json_report(
    test_path: str,
    report_path: Path,
    parallel: int = 1,
    reruns: int = 0,
    lf_mode: bool = False,
    deselect: list[str] | None = None,
) -> tuple[int, Path]:
    """运行 pytest 并生成 json-report，返回 (returncode, report_path)。

    Args:
        test_path: pytest 测试路径
        report_path: json-report 输出路径
        parallel: 并行 worker 数（1 时不传 -n）
        reruns: 失败重试次数（0 时不传 --reruns）
        lf_mode: 是否仅复跑上次失败用例
        deselect: 需排除的 nodeid 列表（逐个透传为 --deselect）

    Returns:
        (returncode, report_path)
    """
    cmd: list[str] = [
        sys.executable,
        "-m",
        "pytest",
        test_path,
        "--json-report",
        "--json-report-file",
        str(report_path),
        "-q",
        "--tb=short",
    ]
    if parallel > 1:
        cmd.extend(["-n", str(parallel)])
    if reruns > 0:
        cmd.extend(["--reruns", str(reruns)])
    for node_id in deselect or []:
        cmd.extend(["--deselect", node_id])
    if lf_mode:
        cmd.append("--lf")

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return result.returncode, report_path


def main(argv: list[str] | None = None) -> int:
    """主入口：解析参数、循环运行 pytest、识别 flaky、输出报告。

    Args:
        argv: 命令行参数（None 时用 sys.argv）

    Returns:
        退出码（0=无 flaky, 1=有 flaky, 2=无法判定：运行错误/无有效运行/一致失败）
    """
    parser = argparse.ArgumentParser(description="精确定位 flaky 测试")
    parser.add_argument("--path", default="tests/unit/", help="测试路径（默认 tests/unit/）")
    parser.add_argument("--runs", type=int, default=10, help="运行次数（默认 10）")
    parser.add_argument("--parallel", type=int, default=1, help="并行 worker 数（默认 1）")
    parser.add_argument("--reruns", type=int, default=0, help="每轮内失败重试次数（默认 0）")
    parser.add_argument("--lf", action="store_true", help="仅复跑上次失败用例")
    parser.add_argument(
        "--workdir",
        default=None,
        help="临时报告目录（默认系统临时目录）",
    )
    parser.add_argument(
        "--deselect",
        action="append",
        default=[],
        metavar="NODEID",
        help="排除指定 nodeid（可重复指定），用于跳过成本高且不应每轮重复的慢测试",
    )
    args = parser.parse_args(argv)

    if args.workdir:
        workdir = Path(args.workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        temp_workdir = None
    else:
        # 自动创建的临时目录必须显式清理，避免每次运行残留 detect_flaky_* 目录
        temp_workdir = tempfile.TemporaryDirectory(prefix="detect_flaky_")
        workdir = Path(temp_workdir.name)

    runs_results: list[dict[str, str]] = []

    for i in range(args.runs):
        print(f"运行 {i + 1}/{args.runs}...", flush=True)
        report_path = workdir / f"run_{i + 1}.json"
        returncode, _ = run_pytest_with_json_report(
            test_path=args.path,
            report_path=report_path,
            parallel=args.parallel,
            reruns=args.reruns,
            lf_mode=args.lf,
            deselect=args.deselect,
        )
        if not report_path.exists():
            print(f"  ✗ 运行 {i + 1} 未生成 json-report", file=sys.stderr)
            if temp_workdir is not None:
                temp_workdir.cleanup()
            return 2
        try:
            outcomes = parse_json_report(report_path)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            invalid_reason = f"json-report 不可解析（{type(exc).__name__}: {exc}）"
        else:
            invalid_reason = validate_run_result(returncode, outcomes)
        if invalid_reason is not None:
            # 无效轮次不能参与 flaky 判定，否则空报告/收集失败会以退出码 0 冒充「无 flaky」
            print(f"  ✗ 运行 {i + 1} 无效：{invalid_reason}", file=sys.stderr)
            if temp_workdir is not None:
                temp_workdir.cleanup()
            return 2
        runs_results.append(outcomes)
        print(f"  returncode={returncode}, collected={len(outcomes)}", flush=True)

    if not runs_results:
        print("✗ 无有效运行（--runs 必须大于 0）", file=sys.stderr)
        if temp_workdir is not None:
            temp_workdir.cleanup()
        return 2

    flaky = identify_flaky_tests(runs_results)

    if not flaky:
        # 结果一致但存在失败用例 → 一致失败，不是「无 flaky」，必须 fail-closed
        consistent_failures = sorted(
            {nodeid for run in runs_results for nodeid, outcome in run.items() if outcome in _FAILED_OUTCOMES}
        )
        if consistent_failures:
            print(
                f"✗ {args.runs} 轮结果一致，但存在 {len(consistent_failures)} 个一致失败用例，无法判定为无 flaky",
                file=sys.stderr,
            )
            for nodeid in consistent_failures[:10]:
                print(f"    {nodeid}", file=sys.stderr)
            if len(consistent_failures) > 10:
                print(f"    ...（其余 {len(consistent_failures) - 10} 个省略）", file=sys.stderr)
            if temp_workdir is not None:
                temp_workdir.cleanup()
            return 2

    # 补充 stdout 摘要（从失败轮的 report 提取 longrepr）
    for result in flaky:
        summaries: list[str] = []
        for i, run_result in enumerate(runs_results):
            if result.nodeid in run_result and run_result[result.nodeid] in ("failed", "error"):
                report_path = workdir / f"run_{i + 1}.json"
                if report_path.exists():
                    data = json.loads(report_path.read_text(encoding="utf-8"))
                    for t in data.get("tests", []):
                        if t["nodeid"] == result.nodeid:
                            call = t.get("call", {})
                            longrepr = call.get("longrepr", "")
                            if longrepr:
                                summaries.append(f"run {i + 1}: {longrepr}")
        result.stdout_summary = " | ".join(summaries)

    report = format_flaky_report(flaky, runs=args.runs, path=args.path)
    print(report)

    if temp_workdir is not None:
        temp_workdir.cleanup()
    return 1 if flaky else 0


if __name__ == "__main__":
    sys.exit(main())
