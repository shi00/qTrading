"""Tests for scripts/run_pyright_changed.py pre-commit hook。

验证：
- 无文件/非 .py 文件过滤
- FileNotFoundError（pyright 未安装）返回 2 + 友好提示
- JSON 解析失败透传返回码
- summary.fatalError 返回 2
- error 级别诊断返回 1 + 输出格式
- warning 不阻断
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from run_pyright_changed import main  # noqa: E402


def _mock_result(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    mock = MagicMock()
    mock.stdout = stdout
    mock.stderr = stderr
    mock.returncode = returncode
    return mock


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

    def test_json_decode_error_returns_returncode(self, monkeypatch, capsys):
        mock = _mock_result(stdout="not json", stderr="some stderr", returncode=2)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock)
        monkeypatch.setattr(sys, "argv", ["script.py", "foo.py"])

        rc = main()
        assert rc == 2
        captured = capsys.readouterr()
        assert "some stderr" in captured.err


class TestBuildPyrightCmd:
    """_build_pyright_cmd: .venv 自动检测与 --pythonpath 注入。"""

    def test_venv_present_injects_pythonpath(self, monkeypatch, tmp_path):
        """项目根存在 .venv 时，命令应包含 --pythonpath 指向 venv 内 python。"""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        venv_scripts = tmp_path / ".venv" / "Scripts"
        venv_scripts.mkdir(parents=True)
        python_exe = venv_scripts / "python.exe"
        python_exe.write_text("")

        import run_pyright_changed

        monkeypatch.setattr(run_pyright_changed, "__file__", str(scripts_dir / "run_pyright_changed.py"))
        if sys.platform == "win32":
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
