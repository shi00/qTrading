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
    validate_run_result,
)

pytestmark = [pytest.mark.unit, pytest.mark.meta]


def _make_fake_run(runs: list[tuple[str, int]]):
    """按序写入 json-report，并返回对应 returncode 的 subprocess.run 替身。"""
    call_count = [0]

    def fake_run(cmd, **_kwargs):
        idx = call_count[0]
        call_count[0] += 1
        report_json, returncode = runs[idx]
        Path(cmd[cmd.index("--json-report-file") + 1]).write_text(report_json, encoding="utf-8")
        mock = MagicMock(spec=subprocess.CompletedProcess)
        mock.returncode = returncode
        return mock

    return fake_run


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


class TestValidateRunResult:
    """validate_run_result: 判定单轮结果能否参与 flaky 判定."""

    def test_valid_when_all_passed(self) -> None:
        """正例：退出码 0 且全部通过，结果有效."""
        assert validate_run_result(0, {"test_a.py::test_1": "passed"}) is None

    def test_valid_when_returncode_one_and_has_failure(self) -> None:
        """正例：退出码 1 且报告含 failed，结果有效（可用于 flaky 对比）."""
        assert validate_run_result(1, {"test_a.py::test_1": "failed"}) is None

    def test_invalid_when_no_tests_collected(self) -> None:
        """边界：空报告（收集失败/空运行）无效."""
        assert validate_run_result(0, {}) == "未收集到任何测试（收集失败或空运行）"

    def test_invalid_when_collection_failure_returncode(self) -> None:
        """边界：pytest 退出码 2（收集中断）无效，即使报告含用例."""
        assert (
            validate_run_result(2, {"test_a.py::test_1": "passed"}) == "pytest 退出码 2（收集失败、被中断或运行错误）"
        )

    def test_invalid_when_returncode_one_but_all_passed(self) -> None:
        """边界：退出码 1 但报告无失败用例，报告与退出码矛盾，无效."""
        assert (
            validate_run_result(1, {"test_a.py::test_1": "passed"})
            == "pytest 退出码 1 但 json-report 中无 failed/error 用例"
        )

    def test_invalid_when_returncode_zero_but_has_failure(self) -> None:
        """边界：退出码 0 但报告含失败用例，报告与退出码矛盾，无效."""
        assert (
            validate_run_result(0, {"test_a.py::test_1": "failed"})
            == "pytest 退出码 0 但 json-report 含 failed/error 用例"
        )


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
        # 验证 subprocess.run 恰好被调用一次
        assert mock_run.call_count == 1
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

    def test_deselect_passes_each_nodeid(self, tmp_path: Path) -> None:
        """正例：deselect 列表逐个透传为 --deselect."""
        report_path = tmp_path / "run_1.json"
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            run_pytest_with_json_report(
                test_path="tests/unit/",
                report_path=report_path,
                deselect=["tests/unit/a.py::test_x", "tests/unit/b.py::test_y"],
            )

        cmd = mock_run.call_args[0][0]
        assert cmd.count("--deselect") == 2
        assert "tests/unit/a.py::test_x" in cmd
        assert "tests/unit/b.py::test_y" in cmd


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

    def test_main_passes_deselect_to_pytest(self, tmp_path: Path) -> None:
        """正例：--deselect 透传到每轮 pytest 子进程."""
        report_json = json.dumps(
            {"summary": {"total": 1, "passed": 1}, "tests": [{"nodeid": "t.py::a", "outcome": "passed"}]}
        )
        cmds: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            cmds.append(cmd)
            Path(cmd[cmd.index("--json-report-file") + 1]).write_text(report_json, encoding="utf-8")
            mock = MagicMock(spec=subprocess.CompletedProcess)
            mock.returncode = 0
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            from scripts.detect_flaky import main

            exit_code = main(
                [
                    "--path",
                    "tests/unit/",
                    "--runs",
                    "1",
                    "--workdir",
                    str(tmp_path),
                    "--deselect",
                    "tests/unit/a.py::test_x",
                ]
            )

        assert exit_code == 0
        assert cmds[0].count("--deselect") == 1
        assert "tests/unit/a.py::test_x" in cmds[0]

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


class TestMainFailClosed:
    """main() fail-closed：无有效运行/一致失败必须返回非零，不得冒充「无 flaky」."""

    def test_returns_two_when_collection_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """边界：收集失败（退出码 2、空报告），不得以 0 退出."""
        empty_report = json.dumps({"summary": {"total": 0, "collected": 0}, "tests": []})

        with patch("subprocess.run", side_effect=_make_fake_run([(empty_report, 2)] * 3)):
            from scripts.detect_flaky import main

            exit_code = main(["--path", "tests/unit/", "--runs", "3", "--workdir", str(tmp_path)])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert "运行 1 无效" in captured.err
        assert "未收集到任何测试" in captured.err
        assert "未检测到 flaky 测试" not in captured.out

    def test_returns_two_when_all_rounds_fail_consistently(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """契约：全部轮次一致失败不是「无 flaky」，必须 fail-closed."""
        failed_report = json.dumps(
            {
                "summary": {"total": 1, "failed": 1},
                "tests": [
                    {
                        "nodeid": "test_x.py::test_always_fails",
                        "outcome": "failed",
                        "call": {"longrepr": "AssertionError: always"},
                    }
                ],
            }
        )

        with patch("subprocess.run", side_effect=_make_fake_run([(failed_report, 1)] * 3)):
            from scripts.detect_flaky import main

            exit_code = main(["--path", "tests/unit/", "--runs", "3", "--workdir", str(tmp_path)])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert "一致失败用例" in captured.err
        assert "test_x.py::test_always_fails" in captured.err
        assert "未检测到 flaky 测试" not in captured.out

    def test_returns_two_when_unparsable_report(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """边界：json-report 不可解析，不得以 0 退出."""
        with patch("subprocess.run", side_effect=_make_fake_run([("{not-json", 0)] * 2)):
            from scripts.detect_flaky import main

            exit_code = main(["--path", "tests/unit/", "--runs", "2", "--workdir", str(tmp_path)])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert "json-report 不可解析" in captured.err
        assert "未检测到 flaky 测试" not in captured.out

    def test_returns_two_when_some_round_invalid(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """边界：部分轮次成功但有一轮收集失败，整体无法判定."""
        passed_report = json.dumps(
            {"summary": {"total": 1, "passed": 1}, "tests": [{"nodeid": "t.py::a", "outcome": "passed"}]}
        )
        empty_report = json.dumps({"summary": {"total": 0, "collected": 0}, "tests": []})

        with patch(
            "subprocess.run",
            side_effect=_make_fake_run([(passed_report, 0), (empty_report, 2), (passed_report, 0)]),
        ):
            from scripts.detect_flaky import main

            exit_code = main(["--path", "tests/unit/", "--runs", "3", "--workdir", str(tmp_path)])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert "运行 2 无效" in captured.err

    def test_returns_two_when_single_run_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """边界：单轮运行即失败，不得因「不足两轮」而返回 0."""
        failed_report = json.dumps(
            {"summary": {"total": 1, "failed": 1}, "tests": [{"nodeid": "t.py::fail", "outcome": "failed"}]}
        )

        with patch("subprocess.run", side_effect=_make_fake_run([(failed_report, 1)])):
            from scripts.detect_flaky import main

            exit_code = main(["--path", "tests/unit/", "--runs", "1", "--workdir", str(tmp_path)])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert "一致失败用例" in captured.err

    def test_returns_two_when_returncode_one_but_report_all_passed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """边界：退出码与报告矛盾（rc=1 但无失败用例），按无效运行失败关闭."""
        passed_report = json.dumps(
            {"summary": {"total": 1, "passed": 1}, "tests": [{"nodeid": "t.py::a", "outcome": "passed"}]}
        )

        with patch("subprocess.run", side_effect=_make_fake_run([(passed_report, 1)] * 2)):
            from scripts.detect_flaky import main

            exit_code = main(["--path", "tests/unit/", "--runs", "2", "--workdir", str(tmp_path)])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert "退出码 1 但 json-report 中无 failed/error 用例" in captured.err

    def test_returns_two_when_runs_is_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """边界：--runs 0 没有任何有效运行，不得以 0 退出."""
        from scripts.detect_flaky import main

        exit_code = main(["--path", "tests/unit/", "--runs", "0", "--workdir", str(tmp_path)])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert "无有效运行" in captured.err
