"""detect_flaky.py 单测。

覆盖核心逻辑：
- parse_json_report: 解析 pytest-json-report 输出，提取 nodeid + outcome
- identify_flaky_tests: 对比多轮结果，识别 flaky 测试
- format_flaky_report: 格式化输出报告
- run_pytest_with_json_report: subprocess 调用 pytest + json-report（mock subprocess）
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.detect_flaky import (
    FlakyResult,
    format_flaky_report,
    identify_flaky_tests,
    parse_json_report,
    run_pytest_with_json_report,
)


class TestParseJsonReport:
    """parse_json_report: 解析 pytest-json-report 文件，返回 {nodeid: outcome}."""

    def test_parse_passed_failed_tests(self, tmp_path: Path) -> None:
        """正例：JSON 含 passed/failed 测试，正确解析 nodeid 与 outcome."""
        data = {
            "summary": {"total": 3, "passed": 2, "failed": 1},
            "tests": [
                {"nodeid": "test_a.py::test_pass1", "outcome": "passed"},
                {"nodeid": "test_a.py::test_fail", "outcome": "failed", "call": {"longrepr": "AssertionError"}},
                {"nodeid": "test_b.py::test_pass2", "outcome": "passed"},
            ],
        }
        report_path = tmp_path / "report.json"
        report_path.write_text(json.dumps(data), encoding="utf-8")

        results = parse_json_report(report_path)

        assert results == {
            "test_a.py::test_pass1": "passed",
            "test_a.py::test_fail": "failed",
            "test_b.py::test_pass2": "passed",
        }

    def test_parse_empty_report(self, tmp_path: Path) -> None:
        """边界：JSON 无 tests 字段（空运行）返回 {}."""
        data = {"summary": {"total": 0, "collected": 0}, "tests": []}
        report_path = tmp_path / "empty.json"
        report_path.write_text(json.dumps(data), encoding="utf-8")

        results = parse_json_report(report_path)

        assert results == {}

    def test_parse_skipped_xfail_xpass(self, tmp_path: Path) -> None:
        """边界：skipped/xfail/xpass outcome 也被记录（不丢失）."""
        data = {
            "summary": {"total": 3},
            "tests": [
                {"nodeid": "test_x.py::test_skip", "outcome": "skipped"},
                {"nodeid": "test_x.py::test_xfail", "outcome": "xfailed"},
                {"nodeid": "test_x.py::test_xpass", "outcome": "xpassed"},
            ],
        }
        report_path = tmp_path / "report.json"
        report_path.write_text(json.dumps(data), encoding="utf-8")

        results = parse_json_report(report_path)

        assert results == {
            "test_x.py::test_skip": "skipped",
            "test_x.py::test_xfail": "xfailed",
            "test_x.py::test_xpass": "xpassed",
        }


class TestIdentifyFlakyTests:
    """identify_flaky_tests: 对比多轮结果，识别 outcome 不一致的 nodeid."""

    def test_no_flaky_when_all_runs_consistent(self) -> None:
        """正例：N 轮全部 pass，无 flaky."""
        runs = [
            {"test_a.py::test_1": "passed", "test_a.py::test_2": "passed"},
            {"test_a.py::test_1": "passed", "test_a.py::test_2": "passed"},
            {"test_a.py::test_1": "passed", "test_a.py::test_2": "passed"},
        ]

        flaky = identify_flaky_tests(runs)

        assert flaky == []

    def test_identifies_flaky_when_outcome_varies(self) -> None:
        """正例：test_1 在轮 1 pass 轮 2 fail 轮 3 pass，识别为 flaky."""
        runs = [
            {"test_a.py::test_1": "passed", "test_a.py::test_2": "passed"},
            {"test_a.py::test_1": "failed", "test_a.py::test_2": "passed"},
            {"test_a.py::test_1": "passed", "test_a.py::test_2": "passed"},
        ]

        flaky = identify_flaky_tests(runs)

        assert len(flaky) == 1
        assert flaky[0].nodeid == "test_a.py::test_1"
        assert flaky[0].outcomes_by_run == ["passed", "failed", "passed"]
        assert flaky[0].failed_runs == [2]  # 1-based 轮次

    def test_identifies_multiple_flaky_tests(self) -> None:
        """正例：两个 flaky 测试都被识别."""
        runs = [
            {"test_a.py::test_1": "passed", "test_a.py::test_2": "passed"},
            {"test_a.py::test_1": "failed", "test_a.py::test_2": "failed"},
            {"test_a.py::test_1": "passed", "test_a.py::test_2": "passed"},
        ]

        flaky = identify_flaky_tests(runs)

        assert len(flaky) == 2
        nodeids = {f.nodeid for f in flaky}
        assert nodeids == {"test_a.py::test_1", "test_a.py::test_2"}

    def test_no_flaky_when_nodeid_only_in_some_runs_due_to_collection_failure(self) -> None:
        """边界：nodeid 仅在部分轮次出现（收集失败），视为 flaky."""
        runs = [
            {"test_a.py::test_1": "passed", "test_a.py::test_2": "passed"},
            {"test_a.py::test_2": "passed"},  # test_1 未收集到
            {"test_a.py::test_1": "passed", "test_a.py::test_2": "passed"},
        ]

        flaky = identify_flaky_tests(runs)

        # test_1 在轮 2 缺失（outcome=None），视为 flaky
        assert len(flaky) == 1
        assert flaky[0].nodeid == "test_a.py::test_1"
        assert flaky[0].outcomes_by_run == ["passed", None, "passed"]
        assert flaky[0].failed_runs == [2]

    def test_single_run_never_flaky(self) -> None:
        """边界：单轮运行无法判定 flaky，返回空."""
        runs = [{"test_a.py::test_1": "passed"}]

        flaky = identify_flaky_tests(runs)

        assert flaky == []


class TestFormatFlakyReport:
    """format_flaky_report: 格式化 flaky 测试报告输出."""

    def test_format_empty_when_no_flaky(self) -> None:
        """正例：无 flaky 测试时输出"未检测到 flaky 测试"."""
        output = format_flaky_report([], runs=3, path="tests/unit/")
        assert "未检测到 flaky 测试" in output
        assert "3 次运行" in output

    def test_format_lists_flaky_tests_with_nodeid_and_failed_runs(self) -> None:
        """正例：有 flaky 时列出 nodeid、失败轮次、outcome 序列."""
        flaky = [
            FlakyResult(
                nodeid="test_a.py::test_flaky_func",
                outcomes_by_run=["passed", "failed", "passed"],
                failed_runs=[2],
                stdout_summary="AssertionError: ...",
            ),
            FlakyResult(
                nodeid="test_b.py::test_another_flaky",
                outcomes_by_run=["failed", "passed", "failed"],
                failed_runs=[1, 3],
                stdout_summary="TimeoutError: ...",
            ),
        ]

        output = format_flaky_report(flaky, runs=3, path="tests/unit/")

        assert "检测到 2 个 flaky 测试" in output
        assert "test_a.py::test_flaky_func" in output
        assert "test_b.py::test_another_flaky" in output
        assert "[2]" in output  # test_a 失败轮次
        assert "[1, 3]" in output  # test_b 失败轮次
        assert "passed, failed, passed" in output  # outcome 序列

    def test_format_truncates_long_stdout_summary(self) -> None:
        """边界：stdout 摘要过长（>500 字符）截断."""
        long_summary = "x" * 1000
        flaky = [
            FlakyResult(
                nodeid="test_x.py::test_long",
                outcomes_by_run=["passed", "failed"],
                failed_runs=[2],
                stdout_summary=long_summary,
            ),
        ]

        output = format_flaky_report(flaky, runs=2, path="tests/unit/")

        # 截断到 500 字符 + 省略提示
        assert "x" * 500 in output
        assert "..." in output


class TestRunPytestWithJsonReport:
    """run_pytest_with_json_report: subprocess 调用 pytest，返回 (returncode, json_path)."""

    def test_invokes_pytest_with_json_report_flag(self, tmp_path: Path) -> None:
        """正例：subprocess 命令含 --json-report --json-report-file."""
        report_path = tmp_path / "run_1.json"
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            returncode, returned_path = run_pytest_with_json_report(
                test_path="tests/unit/",
                report_path=report_path,
                parallel=2,
                reruns=1,
                lf_mode=False,
            )

        assert returncode == 0
        assert returned_path == report_path
        # 验证 subprocess.run 被调用
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "--json-report" in cmd
        assert "--json-report-file" in cmd
        assert "tests/unit/" in cmd
        # --parallel 2 透传为 -n 2
        assert "-n" in cmd
        assert "2" in cmd
        # --reruns 1 透传
        assert "--reruns" in cmd

    def test_lf_mode_passes_lf_flag(self, tmp_path: Path) -> None:
        """正例：lf_mode=True 透传 --lf."""
        report_path = tmp_path / "run_1.json"
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            run_pytest_with_json_report(
                test_path="tests/unit/",
                report_path=report_path,
                parallel=1,
                reruns=0,
                lf_mode=True,
            )

        cmd = mock_run.call_args[0][0]
        assert "--lf" in cmd

    def test_default_parallel_omits_n_flag(self, tmp_path: Path) -> None:
        """边界：parallel=1 时不传 -n 标志（避免 pytest-xdist 单 worker 开销）."""
        report_path = tmp_path / "run_1.json"
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            run_pytest_with_json_report(
                test_path="tests/unit/",
                report_path=report_path,
                parallel=1,
                reruns=0,
                lf_mode=False,
            )

        cmd = mock_run.call_args[0][0]
        assert "-n" not in cmd

    def test_default_reruns_zero_omits_reruns_flag(self, tmp_path: Path) -> None:
        """边界：reruns=0 时不传 --reruns 标志."""
        report_path = tmp_path / "run_1.json"
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            run_pytest_with_json_report(
                test_path="tests/unit/",
                report_path=report_path,
                parallel=1,
                reruns=0,
                lf_mode=False,
            )

        cmd = mock_run.call_args[0][0]
        assert "--reruns" not in cmd


class TestMainIntegration:
    """main() 集成测试：用 mock subprocess 模拟 flaky 测试场景."""

    def test_main_returns_zero_when_all_runs_consistent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """正例：N 轮全部一致，退出码 0."""
        report_data = {
            "summary": {"total": 1, "passed": 1},
            "tests": [{"nodeid": "test_x.py::test_pass", "outcome": "passed"}],
        }
        report_json = json.dumps(report_data)

        def fake_run(cmd, **_kwargs):
            # 找到 --json-report-file 参数对应的路径，写入报告
            report_path_idx = cmd.index("--json-report-file") + 1
            report_path = cmd[report_path_idx]
            Path(report_path).write_text(report_json, encoding="utf-8")
            mock = MagicMock(spec=subprocess.CompletedProcess)
            mock.returncode = 0
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            from scripts.detect_flaky import main

            exit_code = main(["--path", "tests/unit/", "--runs", "3", "--workdir", str(tmp_path)])

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "未检测到 flaky 测试" in captured.out

    def test_main_returns_one_when_flaky_detected(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """正例：注入 flaky（轮 2 fail），退出码 1 且输出定位 nodeid."""
        # 轮 1 pass, 轮 2 fail, 轮 3 pass
        reports = [
            {
                "summary": {"total": 1, "passed": 1},
                "tests": [{"nodeid": "test_x.py::test_flaky", "outcome": "passed"}],
            },
            {
                "summary": {"total": 1, "failed": 1},
                "tests": [
                    {
                        "nodeid": "test_x.py::test_flaky",
                        "outcome": "failed",
                        "call": {"longrepr": "AssertionError: flaky"},
                    }
                ],
            },
            {
                "summary": {"total": 1, "passed": 1},
                "tests": [{"nodeid": "test_x.py::test_flaky", "outcome": "passed"}],
            },
        ]

        call_count = [0]

        def fake_run(cmd, **_kwargs):
            idx = call_count[0]
            call_count[0] += 1
            report_path_idx = cmd.index("--json-report-file") + 1
            report_path = cmd[report_path_idx]
            Path(report_path).write_text(json.dumps(reports[idx]), encoding="utf-8")
            mock = MagicMock(spec=subprocess.CompletedProcess)
            mock.returncode = 1 if idx == 1 else 0
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            from scripts.detect_flaky import main

            exit_code = main(["--path", "tests/unit/", "--runs", "3", "--workdir", str(tmp_path)])

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "test_x.py::test_flaky" in captured.out
        assert "[2]" in captured.out
        assert "检测到 1 个 flaky 测试" in captured.out
