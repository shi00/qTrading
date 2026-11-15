"""Tests for scripts/run_pyright_changed.py pre-commit hook。

验证：
- 无文件/非 .py 文件过滤
- FileNotFoundError（pyright 未安装）返回 2 + 友好提示
- JSON 解析失败按工具失败返回 2（不因退出码 0 放行）
- summary.fatalError 返回 2
- error 级别诊断返回 1 + 输出格式
- warning 不阻断
- 工具非零/输出结构异常不得因空诊断放行（D22-005）
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from run_pyright_changed import main  # noqa: E402


def _mock_result(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    mock = MagicMock()
    mock.stdout = stdout
    mock.stderr = stderr
    mock.returncode = returncode
    return mock


def _run_with(monkeypatch, stdout: str = "", returncode: int = 0, stderr: str = "") -> int:
    """以受控 subprocess 结果调用 main（不依赖真实 pyright）。"""
    result = _mock_result(stdout=stdout, returncode=returncode, stderr=stderr)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: result)
    monkeypatch.setattr(sys, "argv", ["script.py", "foo.py"])
    return main()


class TestMainFileFilter:
    """文件过滤逻辑。"""

    def test_no_args_returns_zero(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["script.py"])
        assert main() == 0

    def test_non_python_files_filtered(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["script.py", "README.md", "config.json"])
        assert main() == 0


class TestMainPyrightErrors:
    """pyright 诊断结果处理。"""

    def test_has_errors_returns_one(self, monkeypatch, capsys):
        output = json.dumps(
            {
                "generalDiagnostics": [
                    {
                        "file": "foo.py",
                        "severity": "error",
                        "message": "Type int is not assignable to str",
                        "rule": "reportReturnType",
                        "range": {"start": {"line": 10, "character": 4}},
                    }
                ],
                "summary": {},
            }
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _mock_result(stdout=output, returncode=1))
        monkeypatch.setattr(sys, "argv", ["script.py", "foo.py"])

        rc = main()
        assert rc == 1
        captured = capsys.readouterr()
        assert "reportReturnType" in captured.out
        assert "foo.py:11:5" in captured.out
        assert "1 个 error" in captured.out

    def test_no_errors_returns_zero(self, monkeypatch):
        output = json.dumps({"generalDiagnostics": [], "summary": {}})
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _mock_result(stdout=output))
        monkeypatch.setattr(sys, "argv", ["script.py", "foo.py"])

        assert main() == 0

    def test_warning_does_not_block(self, monkeypatch):
        output = json.dumps(
            {
                "generalDiagnostics": [
                    {
                        "file": "foo.py",
                        "severity": "warning",
                        "message": "Unused variable",
                        "rule": "reportUnusedVariable",
                        "range": {"start": {"line": 0, "character": 0}},
                    }
                ],
                "summary": {},
            }
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _mock_result(stdout=output))
        monkeypatch.setattr(sys, "argv", ["script.py", "foo.py"])

        assert main() == 0


class TestMainPyrightNotInstalled:
    """P1-1: pyright 未安装时友好提示。"""

    def test_filenotfound_returns_two(self, monkeypatch, capsys):
        def raise_filenotfound(*args, **kwargs):
            raise FileNotFoundError("pyright")

        monkeypatch.setattr(subprocess, "run", raise_filenotfound)
        monkeypatch.setattr(sys, "argv", ["script.py", "foo.py"])

        rc = main()
        assert rc == 2
        captured = capsys.readouterr()
        assert "未安装" in captured.err
        assert "requirements-dev.txt" in captured.err


class TestMainPyrightFatalError:
    """P2-1: summary.fatalError 配置错误处理。"""

    def test_fatal_error_returns_two(self, monkeypatch, capsys):
        output = json.dumps(
            {
                "generalDiagnostics": [],
                "summary": {"fatalError": "pyrightconfig.json not found"},
            }
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _mock_result(stdout=output, returncode=2))
        monkeypatch.setattr(sys, "argv", ["script.py", "foo.py"])

        rc = main()
        assert rc == 2
        captured = capsys.readouterr()
        assert "配置错误" in captured.err
        assert "pyrightconfig.json not found" in captured.err

    def test_json_decode_error_returns_two(self, monkeypatch, capsys):
        mock = _mock_result(stdout="not json", stderr="some stderr", returncode=2)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock)
        monkeypatch.setattr(sys, "argv", ["script.py", "foo.py"])

        rc = main()
        assert rc == 2
        captured = capsys.readouterr()
        assert "some stderr" in captured.err
        assert "未输出可解析的 JSON" in captured.err

    def test_json_decode_error_with_exit_zero_returns_two(self, monkeypatch, capsys):
        """退出码 0 但输出不是 JSON 时同样不得放行。"""
        assert _run_with(monkeypatch, stdout="not json", returncode=0) == 2
        assert "未输出可解析的 JSON" in capsys.readouterr().err


class TestMainToolFailure:
    """D22-005: 工具非零退出不得被空诊断/仅 warning 掩盖成通过。"""

    def test_exit_two_with_empty_diagnostics_returns_two(self, monkeypatch, capsys):
        output = json.dumps({"generalDiagnostics": [], "summary": {}})
        rc = _run_with(monkeypatch, stdout=output, returncode=2, stderr="pyright: config error")

        assert rc == 2
        captured = capsys.readouterr()
        assert "退出码 2" in captured.err
        assert "pyright: config error" in captured.err

    def test_exit_two_with_warning_only_returns_two(self, monkeypatch, capsys):
        output = json.dumps(
            {
                "generalDiagnostics": [{"severity": "warning", "message": "unused"}],
                "summary": {},
            }
        )
        rc = _run_with(monkeypatch, stdout=output, returncode=2, stderr="tool failed")

        assert rc == 2
        captured = capsys.readouterr()
        assert "tool failed" in captured.err
        # 工具失败时不应渲染成「无 error」的通过输出
        assert "Pyright 发现" not in captured.out

    def test_unexpected_exit_code_returns_two(self, monkeypatch):
        output = json.dumps({"generalDiagnostics": [], "summary": {}})
        assert _run_with(monkeypatch, stdout=output, returncode=3) == 2

    def test_exit_one_with_empty_diagnostics_returns_one(self, monkeypatch, capsys):
        output = json.dumps({"generalDiagnostics": [], "summary": {}})
        rc = _run_with(monkeypatch, stdout=output, returncode=1, stderr="internal error")

        assert rc == 1
        captured = capsys.readouterr()
        assert "无 error 级诊断" in captured.err
        assert "internal error" in captured.err

    def test_exit_zero_with_error_diagnostic_still_returns_one(self, monkeypatch):
        """退出码 0 但 JSON 含 error 诊断时按诊断阻断（不因退出码放行）。"""
        output = json.dumps(
            {
                "generalDiagnostics": [
                    {
                        "file": "foo.py",
                        "severity": "error",
                        "message": "boom",
                        "range": {"start": {"line": 0, "character": 0}},
                    }
                ],
                "summary": {},
            }
        )
        assert _run_with(monkeypatch, stdout=output, returncode=0) == 1


class TestMainPayloadStructure:
    """D22-005: 错型/不完整 JSON 不得回落为「无 error」。"""

    @pytest.mark.parametrize(
        ("payload", "fragment"),
        [
            ([], "顶层不是 JSON 对象"),
            ("ok", "顶层不是 JSON 对象"),
            ({"generalDiagnostics": []}, "缺少 summary 对象"),
            ({"summary": []}, "缺少 summary 对象"),
            ({"summary": {}}, "缺少 generalDiagnostics 数组"),
            ({"summary": {}, "generalDiagnostics": {}}, "缺少 generalDiagnostics 数组"),
            ({"summary": {}, "generalDiagnostics": ["oops"]}, "含非对象元素"),
        ],
    )
    def test_structural_error_returns_two(self, monkeypatch, capsys, payload, fragment):
        rc = _run_with(monkeypatch, stdout=json.dumps(payload), returncode=0)

        assert rc == 2
        assert fragment in capsys.readouterr().err

    def test_realistic_payload_with_counts_passes(self, monkeypatch):
        output = json.dumps(
            {
                "version": "1.1.414",
                "time": "1700000000000",
                "generalDiagnostics": [],
                "summary": {
                    "filesAnalyzed": 1,
                    "errorCount": 0,
                    "warningCount": 0,
                    "informationCount": 0,
                    "timeInSec": 0.12,
                },
            }
        )
        assert _run_with(monkeypatch, stdout=output, returncode=0) == 0


class TestBuildPyrightCmd:
    """_build_pyright_cmd: .venv 自动检测与 --pythonpath 注入。"""

    def test_venv_present_injects_pythonpath(self, monkeypatch, tmp_path):
        """项目根存在 .venv 时，命令应包含 --pythonpath 指向 venv 内 python。"""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        if sys.platform == "win32":
            venv_dir = tmp_path / ".venv" / "Scripts"
            python_exe = venv_dir / "python.exe"
        else:
            venv_dir = tmp_path / ".venv" / "bin"
            python_exe = venv_dir / "python"
        venv_dir.mkdir(parents=True)
        python_exe.write_text("")

        import run_pyright_changed

        monkeypatch.setattr(run_pyright_changed, "__file__", str(scripts_dir / "run_pyright_changed.py"))
        cmd = run_pyright_changed._build_pyright_cmd(["foo.py"])
        assert "--pythonpath" in cmd
        idx = cmd.index("--pythonpath")
        assert cmd[idx + 1] == str(python_exe)
        assert "foo.py" in cmd

    def test_venv_absent_no_pythonpath(self, monkeypatch, tmp_path):
        """项目根无 .venv 时，命令不含 --pythonpath。"""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()

        import run_pyright_changed

        monkeypatch.setattr(run_pyright_changed, "__file__", str(scripts_dir / "run_pyright_changed.py"))
        cmd = run_pyright_changed._build_pyright_cmd(["foo.py"])
        assert "--pythonpath" not in cmd
        assert cmd == ["pyright", "--outputjson", "foo.py"]
