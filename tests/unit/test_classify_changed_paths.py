"""scripts/classify_changed_paths.py 单测。

覆盖：代码类路径命中、非代码类路径、空集合、CLI 输出格式。
该脚本决定 CI Gate 是否要求重型 job，分类错误会导致门禁误放行或误阻塞，故逐类断言。
"""

from __future__ import annotations

import io

import pytest

from scripts.classify_changed_paths import CODE_PATTERNS, classify, is_code_path, main

pytestmark = [pytest.mark.unit, pytest.mark.meta]


class TestIsCodePath:
    """is_code_path: 单路径白名单判定。"""

    @pytest.mark.parametrize(
        "path",
        [
            "main.py",
            "core/errors.py",
            "tests/unit/test_x.py",
            "scripts/check_docs_consistency.py",
            "requirements.txt",
            "requirements-dev.in",
            "pyproject.toml",
            "pyrightconfig.json",
            "alembic.ini",
            "alembic/versions/0001_init.py",
            "AStockScreener.spec",
            "installer.iss",
            "locales/en/LC_MESSAGES/app.po",
            "assets/icon.png",
            "package.json",
            "package-lock.json",
            ".github/workflows/ci_cd.yml",
        ],
    )
    def test_code_paths(self, path: str) -> None:
        """正例：白名单内路径判为代码类。"""
        assert is_code_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "README.md",
            "docs/guides/ci-cd.md",
            "LICENSE",
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/workflows/docs-ci.yml",
            "CHANGELOG.md",
        ],
    )
    def test_non_code_paths(self, path: str) -> None:
        """反例：纯文档/许可证/其他 workflow 不算代码类。"""
        assert is_code_path(path) is False

    def test_nested_python_file_matches_star_glob(self) -> None:
        """``*`` 在 fnmatch 中可跨目录，深层 Python 文件也能命中 ``*.py``。"""
        assert is_code_path("services/deep/nested/module.py") is True

    def test_empty_path_is_not_code(self) -> None:
        """空字符串不命中任何模式。"""
        assert is_code_path("") is False


class TestClassify:
    """classify: 变更集合级判定。"""

    def test_empty_collection_is_not_code(self) -> None:
        """空集合（无变更）判为非代码类。"""
        assert classify([]) is False

    def test_docs_only_collection_is_not_code(self) -> None:
        """仅文档变更判为非代码类。"""
        assert classify(["README.md", "docs/guides/ci-cd.md"]) is False

    def test_mixed_collection_is_code(self) -> None:
        """文档 + 代码混合时以代码类为准。"""
        assert classify(["README.md", "core/errors.py"]) is True

    def test_single_code_file_is_code(self) -> None:
        """单个代码文件即判为代码类。"""
        assert classify(["scripts/classify_changed_paths.py"]) is True


class TestMain:
    """main: 从 stdin 读取路径并输出 GITHUB_OUTPUT 可用的单行结果。"""

    def test_outputs_code_true(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """stdin 含代码路径时输出 code=true。"""
        monkeypatch.setattr("sys.stdin", io.StringIO("docs/a.md\ncore/errors.py\n"))
        assert main() == 0
        assert capsys.readouterr().out.strip() == "code=true"

    def test_outputs_code_false(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """stdin 仅文档路径时输出 code=false。"""
        monkeypatch.setattr("sys.stdin", io.StringIO("docs/a.md\n\nREADME.md\n"))
        assert main() == 0
        assert capsys.readouterr().out.strip() == "code=false"

    def test_ignores_blank_lines(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """空白行被忽略，不影响判定。"""
        monkeypatch.setattr("sys.stdin", io.StringIO("\n  \n"))
        assert main() == 0
        assert capsys.readouterr().out.strip() == "code=false"


def test_code_patterns_is_non_empty_tuple() -> None:
    """白名单本身不可为空，且为不可变序列（防止运行时被误改）。"""
    assert isinstance(CODE_PATTERNS, tuple)
    assert len(CODE_PATTERNS) > 0
