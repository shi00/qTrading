"""重复运行 pytest 收集 pass/fail 列表，精确定位 flaky 测试 nodeid。

基于 pytest-json-report 输出对比多轮结果，识别 outcome 不一致的测试。
默认 --runs=10，支持 --parallel N（pytest-xdist）、--reruns N（pytest-rerunfailures）、--lf（仅复跑上次失败用例）。

用法：
    python scripts/detect_flaky.py
    python scripts/detect_flaky.py --path tests/unit/ --runs 10
    python scripts/detect_flaky.py --parallel 4 --reruns 2
    python scripts/detect_flaky.py --lf  # 仅复跑上次失败用例

退出码：
    0: 未检测到 flaky（所有运行结果一致）
    1: 检测到 flaky（运行结果不一致）
    2: 路径不存在或运行出错
"""

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


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
    tests = data.get("tests", [])
    return {t["nodeid"]: t["outcome"] for t in tests}


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
) -> tuple[int, Path]:
    """运行 pytest 并生成 json-report，返回 (returncode, report_path)。

    Args:
        test_path: pytest 测试路径
        report_path: json-report 输出路径
        parallel: 并行 worker 数（1 时不传 -n）
        reruns: 失败重试次数（0 时不传 --reruns）
        lf_mode: 是否仅复跑上次失败用例

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
    if lf_mode:
        cmd.append("--lf")

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return result.returncode, report_path


def main(argv: list[str] | None = None) -> int:
    """主入口：解析参数、循环运行 pytest、识别 flaky、输出报告。

    Args:
        argv: 命令行参数（None 时用 sys.argv）

    Returns:
        退出码（0=无 flaky, 1=有 flaky, 2=运行错误）
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
    args = parser.parse_args(argv)

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="detect_flaky_"))
    workdir.mkdir(parents=True, exist_ok=True)

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
        )
        if not report_path.exists():
            print(f"  ✗ 运行 {i + 1} 未生成 json-report", file=sys.stderr)
            return 2
        runs_results.append(parse_json_report(report_path))
        summary_count = len(runs_results[-1])
        print(f"  returncode={returncode}, collected={summary_count}", flush=True)

    flaky = identify_flaky_tests(runs_results)

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

    return 1 if flaky else 0


if __name__ == "__main__":
    sys.exit(main())
