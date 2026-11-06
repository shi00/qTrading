"""scripts/classify_changed_paths.py 单测。

覆盖：代码类路径命中、非代码类路径、空集合、CLI 输出格式。
该脚本决定 CI Gate 是否要求重型 job，分类错误会导致门禁误放行或误阻塞，故逐类断言。

另含 ``ci_cd.yml`` 的分类输入契约：GitHub 的重命名文件只在新路径上给出 ``filename``，
旧路径在 ``previous_filename``；workflow 必须同时提取两列并按文件行计数，否则
「代码 → 文档」的移动会被误判为非代码类（CI-F02）。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import yaml

from scripts.classify_changed_paths import CODE_PATTERNS, classify, is_code_path, main

pytestmark = [pytest.mark.unit, pytest.mark.meta]

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci_cd.yml"
CLASSIFY_STEP_NAME = "Classify changed paths"


def _api_pair_lines(entries: list[dict[str, str]]) -> str:
    """模拟 workflow 的 jq 投影：``[filename, previous_filename // ""] | @tsv``。

    测试 fixture 的路径不含 TAB / 换行，故无需复现 jq ``@tsv`` 的字段内转义。
    """
    return "".join(f"{entry['filename']}\t{entry.get('previous_filename', '')}\n" for entry in entries)


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
            ".github/workflows/finbert-train.yml",
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
            ".github/workflows/sidecar.yml",
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

    def test_finbert_workflow_only_collection_is_code(self) -> None:
        """D18-S01：仅改 finbert-train.yml 时须判为代码类。

        该 workflow 仅 workflow_dispatch，PR 不触发自身；其契约测试
        test_workflow_finbert_train_split.py 须随 unit 重型 job 在合并前执行。
        """
        assert classify([".github/workflows/finbert-train.yml"]) is True

    def test_sidecar_workflow_only_collection_is_not_code(self) -> None:
        """sidecar.yml 有独立 PR 补偿（sidecar.yml 自身工作流），不重复拉入重型 job。"""
        assert classify([".github/workflows/sidecar.yml"]) is False


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


def test_finbert_workflow_pattern_is_exact_not_broad() -> None:
    """D18-S01：仅登记精确路径，不用 ``.github/workflows/**`` 扩大分类面。

    泛匹配会把已有独立补偿的 sidecar / precommit 路径重新拉入重型 job；
    精确路径只为让 finbert-train 现有契约测试在合并前随 unit job 执行。
    """
    assert ".github/workflows/finbert-train.yml" in CODE_PATTERNS
    assert ".github/workflows/**" not in CODE_PATTERNS
    assert ".github/workflows/*.yml" not in CODE_PATTERNS
    assert is_code_path(".github/workflows/sidecar.yml") is False
    assert is_code_path(".github/workflows/precommit-config-ci.yml") is False


class TestRenamedClassification:
    """CI-F02：重命名文件的 filename 与 previous_filename 必须取并集分类。"""

    @pytest.mark.parametrize(
        ("previous_filename", "filename", "expected"),
        [
            ("services/example.py", "docs/example.txt", "code=true"),
            ("docs/example.txt", "services/example.py", "code=true"),
            ("services/old.py", "services/new.py", "code=true"),
            ("docs/old.md", "docs/new.md", "code=false"),
        ],
        ids=["code-to-doc", "doc-to-code", "code-to-code", "doc-to-doc"],
    )
    def test_renamed_direction(
        self,
        previous_filename: str,
        filename: str,
        expected: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """前三类凡任一列命中代码规则即 code=true；文档 → 文档仍可轻量运行。"""
        entries = [{"status": "renamed", "filename": filename, "previous_filename": previous_filename}]
        monkeypatch.setattr("sys.stdin", io.StringIO(_api_pair_lines(entries)))
        assert main() == 0
        assert capsys.readouterr().out.strip() == expected

    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            ({"status": "renamed", "filename": "docs/only.md"}, "code=false"),
            ({"status": "renamed", "filename": "docs/only.md", "previous_filename": ""}, "code=false"),
            ({"status": "renamed", "filename": "main.py"}, "code=true"),
        ],
        ids=["missing-previous", "empty-previous", "missing-previous-code"],
    )
    def test_renamed_without_previous_filename_falls_back_to_filename(
        self,
        entry: dict[str, str],
        expected: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """previous_filename 缺失或为空时旧列为空，仅按 filename 分类，不误报也不崩溃。"""
        monkeypatch.setattr("sys.stdin", io.StringIO(_api_pair_lines([entry])))
        assert main() == 0
        assert capsys.readouterr().out.strip() == expected

    def test_renamed_alongside_other_files(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """同一页多文件：重命名对与普通文档行共存时，任一代码路径即可判为代码类。"""
        entries = [
            {"status": "modified", "filename": "docs/a.md"},
            {"status": "renamed", "filename": "docs/example.txt", "previous_filename": "services/example.py"},
            {"status": "renamed", "filename": "docs/b.md", "previous_filename": "docs/b-old.md"},
        ]
        monkeypatch.setattr("sys.stdin", io.StringIO(_api_pair_lines(entries)))
        assert main() == 0
        assert capsys.readouterr().out.strip() == "code=true"

    def test_paginated_pages_are_concatenated(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """分页边界：每页独立输出两列，拼接后旧路径仍参与分类。"""
        page_one = [{"status": "modified", "filename": "docs/a.md"}]
        page_two = [{"status": "renamed", "filename": "docs/moved.txt", "previous_filename": "core/errors.py"}]
        monkeypatch.setattr("sys.stdin", io.StringIO(_api_pair_lines(page_one) + _api_pair_lines(page_two)))
        assert main() == 0
        assert capsys.readouterr().out.strip() == "code=true"

    def test_delete_add_split_is_classified(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """GitHub 拆成 delete/add 时 removed 的 filename 仍是旧代码路径，应判为代码类。"""
        entries = [
            {"status": "removed", "filename": "services/example.py"},
            {"status": "added", "filename": "docs/example.txt"},
        ]
        monkeypatch.setattr("sys.stdin", io.StringIO(_api_pair_lines(entries)))
        assert main() == 0
        assert capsys.readouterr().out.strip() == "code=true"

    def test_pair_line_count_matches_file_count(self) -> None:
        """每文件恰输出一行两列，重命名不会使行数翻倍（3000 上限判断依赖行数）。"""
        entries = [
            {"status": "modified", "filename": "docs/a.md"},
            {"status": "renamed", "filename": "docs/b.md", "previous_filename": "services/b.py"},
            {"status": "added", "filename": "docs/c.md"},
        ]
        assert len(_api_pair_lines(entries).splitlines()) == len(entries)


@pytest.fixture(scope="module")
def classify_step_run() -> str:
    """加载 Changes job 中「Classify changed paths」步骤的 shell 脚本。"""
    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["changes"]["steps"]
    matches = [step for step in steps if step.get("name") == CLASSIFY_STEP_NAME]
    assert len(matches) == 1, f"changes job 应恰有一个 `{CLASSIFY_STEP_NAME}` 步骤，实际: {len(matches)}"
    return matches[0]["run"]


class TestWorkflowClassificationContract:
    """CI-F02：workflow 分类输入必须带 previous_filename，且不破坏计数/fail-closed 语义。"""

    def test_jq_extracts_both_paths_with_pagination(self, classify_step_run: str) -> None:
        """静态契约：jq 表达式同时包含 filename 与 previous_filename，且仍逐页应用。"""
        assert "--paginate" in classify_step_run
        assert "repos/$REPO/pulls/$PR_NUMBER/files" in classify_step_run
        assert ".filename" in classify_step_run
        assert ".previous_filename" in classify_step_run
        assert "@tsv" in classify_step_run, "两列必须以 TAB 分隔输出，供分类脚本拆列"
        assert "--jq '.[].filename'" not in classify_step_run, "不得退回只取新路径的旧表达式"

    def test_file_count_is_derived_from_pair_lines(self, classify_step_run: str) -> None:
        """计数必须作用于每文件一行两列的输出，拆分两列只发生在送入分类器时。"""
        count_line = 'count="$(printf \'%s\\n\' "$files" | grep -c . || true)"'
        classify_line = 'result="$(printf \'%s\\n\' "$files" | python scripts/classify_changed_paths.py)"'
        assert count_line in classify_step_run
        assert classify_line in classify_step_run
        assert classify_step_run.index(count_line) < classify_step_run.index(classify_line)

    def test_fail_closed_branches_are_preserved(self, classify_step_run: str) -> None:
        """非 PR、fork PR、3000 上限三条早退分支仍按代码类处理（fail-closed）。"""
        assert 'if [ "$EVENT_NAME" != "pull_request" ]' in classify_step_run
        assert 'if [ "$IS_FORK" = "true" ]' in classify_step_run
        assert 'if [ "$count" -ge 3000 ]' in classify_step_run
        assert classify_step_run.count('echo "code=true" >> "$GITHUB_OUTPUT"') == 3
        assert classify_step_run.count("exit 0") == 3
