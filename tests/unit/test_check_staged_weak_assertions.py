"""Tests for scripts/check_staged_weak_assertions.py pre-commit hook。

验证：
- 无文件/非 test_*.py 文件过滤
- 新增弱断言（不在 baseline 中）返回 1 阻断
- 历史弱断言（在 baseline 中）不阻断，返回 0
- _to_tests_relative 路径转换
"""

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_staged_weak_assertions  # noqa: E402
from check_staged_weak_assertions import _to_tests_relative, main  # noqa: E402


class TestToTestsRelative:
    """路径转换，与 CI baseline 签名格式一致。"""

    def test_tests_prefix_stripped(self):
        assert _to_tests_relative("tests/unit/test_foo.py") == "unit/test_foo.py"

    def test_non_tests_path_unchanged(self):
        assert _to_tests_relative("scripts/foo.py") == "scripts/foo.py"


class TestMainFileFilter:
    """文件过滤逻辑。"""

    def test_no_args_returns_zero(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["script.py"])
        assert main() == 0

    def test_non_test_files_filtered(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["script.py", "scripts/foo.py"])
        assert main() == 0


def _write_weak_test_file(tmp_path: Path, name: str = "test_foo.py") -> Path:
    """创建含弱断言（assert True）的测试文件，返回路径。"""
    f = tmp_path / name
    f.write_text("def test_weak():\n    assert True\n", encoding="utf-8")
    return f


def _write_baseline(tmp_path: Path, entries: list) -> Path:
    """创建临时 baseline JSON 文件，格式与 load_baseline 一致：{"entries": [...]}。"""
    f = tmp_path / "baseline.json"
    f.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return f


class TestMainWithBaseline:
    """P1-2: baseline 增量门禁。"""

    def test_new_weak_assertion_returns_one(self, monkeypatch, tmp_path, capsys):
        # 新增弱断言（不在 baseline 中）→ 阻断
        test_file = _write_weak_test_file(tmp_path)
        baseline = _write_baseline(tmp_path, [])
        monkeypatch.setattr(check_staged_weak_assertions, "BASELINE_PATH", baseline)
        monkeypatch.setattr(sys, "argv", ["script.py", str(test_file)])

        rc = main()
        assert rc == 1
        captured = capsys.readouterr()
        assert "新增弱断言" in captured.out
        assert "weak_assert" in captured.out

    def test_historical_weak_assertion_returns_zero(self, monkeypatch, tmp_path):
        # 历史弱断言（在 baseline 中）→ 不阻断
        test_file = _write_weak_test_file(tmp_path)
        # baseline 字段名与 load_baseline 一致：file/line/type/source_line/detail
        entries = [
            {
                "file": str(test_file),
                "line": 2,
                "type": "weak_assert",
                "source_line": "    assert True",
                "detail": "assert True is weak",
            }
        ]
        baseline = _write_baseline(tmp_path, entries)
        monkeypatch.setattr(check_staged_weak_assertions, "BASELINE_PATH", baseline)
        monkeypatch.setattr(sys, "argv", ["script.py", str(test_file)])

        rc = main()
        assert rc == 0

    def test_tests_prefix_path_matches_baseline_e2e(self, monkeypatch, tmp_path):
        """端到端：pre-commit 传 ``tests/unit/test_foo.py`` → 转为 ``unit/test_foo.py`` → 与 baseline 签名匹配。

        验证 _to_tests_relative 路径转换在 main() 流程中正确生效：
        baseline 中 file 字段存相对 tests/ 的路径（与 CI scan_directory 一致），
        pre-commit 传 git 路径（tests/unit/...），hook 转换后签名应匹配。
        """
        # 在 tmp_path 下构造 tests/unit/ 目录结构
        tests_dir = tmp_path / "tests" / "unit"
        tests_dir.mkdir(parents=True)
        test_file = tests_dir / "test_foo.py"
        test_file.write_text("def test_weak():\n    assert True\n", encoding="utf-8")

        # baseline 中 file 用相对 tests/ 的路径（与 CI 一致）
        entries = [
            {
                "file": "unit/test_foo.py",
                "line": 2,
                "type": "weak_assert",
                "source_line": "    assert True",
                "detail": "assert True is weak",
            }
        ]
        baseline = _write_baseline(tmp_path, entries)
        monkeypatch.setattr(check_staged_weak_assertions, "BASELINE_PATH", baseline)
        # pre-commit 传 git 路径（含 tests/ 前缀）
        monkeypatch.setattr(sys, "argv", ["script.py", "tests/unit/test_foo.py"])

        # 需要让 main() 能读到 tests/unit/test_foo.py，切换 cwd 到 tmp_path
        monkeypatch.chdir(tmp_path)

        rc = main()
        assert rc == 0

    def test_nonexistent_file_skipped(self, monkeypatch, tmp_path):
        # 文件不存在时跳过，不崩溃
        baseline = _write_baseline(tmp_path, [])
        monkeypatch.setattr(check_staged_weak_assertions, "BASELINE_PATH", baseline)
        monkeypatch.setattr(sys, "argv", ["script.py", str(tmp_path / "test_missing.py")])

        assert main() == 0
