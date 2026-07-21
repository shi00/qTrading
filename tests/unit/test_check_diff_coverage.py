"""Tests for scripts/check_diff_coverage.py diff-coverage 门禁。

验证:
- parse_diff: git diff 输出解析，提取源码文件新增行号
- load_coverage: coverage.json 加载，statements = executed ∪ missing
- compute_diff_coverage: 排除非可执行行（空行/注释）、跳过被 omit 文件、路径规范化
- _is_source_file: 源码目录/文件判定
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_diff_coverage import (  # noqa: E402 - sys.path 注入后导入
    _is_source_file,
    compute_diff_coverage,
    load_coverage,
    parse_diff,
)


# ============================================================================
# _is_source_file: 源码目录/文件判定
# ============================================================================


class TestIsSourceFile:
    """_is_source_file: 判断文件是否在覆盖率源码目录内。"""

    def test_source_dir_app(self):
        assert _is_source_file("app/bootstrap.py") is True

    def test_source_dir_data(self):
        assert _is_source_file("data/persistence/dao.py") is True

    def test_source_file_main(self):
        assert _is_source_file("main.py") is True

    def test_source_file_config(self):
        assert _is_source_file("config.py") is True

    def test_non_source_file(self):
        assert _is_source_file("scripts/check.py") is False

    def test_non_python_file(self):
        assert _is_source_file("app/bootstrap.js") is False

    def test_test_file(self):
        assert _is_source_file("tests/unit/test_foo.py") is False


# ============================================================================
# parse_diff: git diff 输出解析
# ============================================================================


class TestParseDiff:
    """parse_diff: 解析 git diff 输出，提取源码文件的新增行号。"""

    def test_single_file_single_hunk(self):
        """单个文件、单个 hunk → 返回 {filepath: [line_no, ...]}。"""
        diff = """diff --git a/app/bootstrap.py b/app/bootstrap.py
index e0a3df45..696267e4 100644
--- a/app/bootstrap.py
+++ b/app/bootstrap.py
@@ -335,0 +336,3 @@ def mask_sensitive(value):
+line1
+line2
+line3
"""
        result = parse_diff(diff)
        assert result == {"app/bootstrap.py": [336, 337, 338]}

    def test_multiple_hunks(self):
        """同一文件多个 hunk → 行号按 hunk header 正确推进。"""
        diff = """diff --git a/app/bootstrap.py b/app/bootstrap.py
--- a/app/bootstrap.py
+++ b/app/bootstrap.py
@@ -10,0 +11,2 @@
+new1
+new2
@@ -50,0 +53,1 @@
+new3
"""
        result = parse_diff(diff)
        assert result == {"app/bootstrap.py": [11, 12, 53]}

    def test_non_source_file_skipped(self):
        """非源码文件（如 scripts/）→ 跳过。"""
        diff = """diff --git a/scripts/helper.py b/scripts/helper.py
--- a/scripts/helper.py
+++ b/scripts/helper.py
@@ -1,0 +2,1 @@
+new
"""
        result = parse_diff(diff)
        assert result == {}

    def test_deleted_lines_ignored(self):
        """删除行（-开头）不影响新文件行号。"""
        diff = """diff --git a/app/foo.py b/app/foo.py
--- a/app/foo.py
+++ b/app/foo.py
@@ -10,2 +10,1 @@
-removed
 kept
"""
        result = parse_diff(diff)
        # 只有 kept 行（context line），没有新增行
        assert result == {}

    def test_context_lines_advance_line_number(self):
        """context 行（空格开头）推进行号，不算新增。"""
        diff = """diff --git a/app/foo.py b/app/foo.py
--- a/app/foo.py
+++ b/app/foo.py
@@ -10,0 +11,3 @@
+new1
 kept1
+new2
"""
        result = parse_diff(diff)
        # new1 在 L11, kept1 是 context（L12）, new2 在 L13
        assert result == {"app/foo.py": [11, 13]}


# ============================================================================
# load_coverage: coverage.json 加载
# ============================================================================


class TestLoadCoverage:
    """load_coverage: 加载 coverage.json，返回 statements 和 executed。"""

    def test_basic_load(self, tmp_path: Path):
        """正常加载 → 返回 {filepath: {"executed": set, "statements": set}}。"""
        cov_data = {
            "files": {
                "app/bootstrap.py": {
                    "executed_lines": [1, 2, 3],
                    "missing_lines": [4, 5],
                    "excluded_lines": [],
                }
            }
        }
        cov_file = tmp_path / "coverage.json"
        cov_file.write_text(json.dumps(cov_data), encoding="utf-8")

        result = load_coverage(cov_file)
        assert "app/bootstrap.py" in result
        assert result["app/bootstrap.py"]["executed"] == {1, 2, 3}
        assert result["app/bootstrap.py"]["statements"] == {1, 2, 3, 4, 5}

    def test_windows_path_normalization(self, tmp_path: Path):
        """Windows 反斜杠路径 → 规范化为正斜杠。"""
        cov_data = {
            "files": {
                "app\\bootstrap.py": {
                    "executed_lines": [1],
                    "missing_lines": [2],
                    "excluded_lines": [],
                }
            }
        }
        cov_file = tmp_path / "coverage.json"
        cov_file.write_text(json.dumps(cov_data), encoding="utf-8")

        result = load_coverage(cov_file)
        # 反斜杠应被替换为正斜杠
        assert "app/bootstrap.py" in result
        assert "app\\bootstrap.py" not in result

    def test_empty_coverage(self, tmp_path: Path):
        """空 coverage.json → 返回空字典。"""
        cov_file = tmp_path / "coverage.json"
        cov_file.write_text(json.dumps({"files": {}}), encoding="utf-8")

        result = load_coverage(cov_file)
        assert result == {}

    def test_missing_lines_field_defaults_to_empty(self, tmp_path: Path):
        """coverage.json 无 missing_lines 字段 → missing 默认空集，statements = executed。"""
        cov_data = {
            "files": {
                "app/foo.py": {
                    "executed_lines": [1, 2],
                }
            }
        }
        cov_file = tmp_path / "coverage.json"
        cov_file.write_text(json.dumps(cov_data), encoding="utf-8")

        result = load_coverage(cov_file)
        assert result["app/foo.py"]["executed"] == {1, 2}
        assert result["app/foo.py"]["statements"] == {1, 2}


# ============================================================================
# compute_diff_coverage: diff coverage 计算
# ============================================================================


class TestComputeDiffCoverage:
    """compute_diff_coverage: 计算 diff coverage。"""

    def test_all_covered(self):
        """所有 diff 行都在 executed 中 → 100% 覆盖。"""
        diff_lines = {"app/foo.py": [1, 2, 3]}
        coverage = {
            "app/foo.py": {
                "executed": {1, 2, 3},
                "statements": {1, 2, 3},
            }
        }
        covered, total, uncovered = compute_diff_coverage(diff_lines, coverage)
        assert covered == 3
        assert total == 3
        assert uncovered == {}

    def test_partial_coverage(self):
        """部分 diff 行未覆盖 → 返回未覆盖行列表。"""
        diff_lines = {"app/foo.py": [1, 2, 3, 4, 5]}
        coverage = {
            "app/foo.py": {
                "executed": {1, 2, 3},
                "statements": {1, 2, 3, 4, 5},
            }
        }
        covered, total, uncovered = compute_diff_coverage(diff_lines, coverage)
        assert covered == 3
        assert total == 5
        assert uncovered == {"app/foo.py": [4, 5]}

    def test_non_executable_lines_filtered(self):
        """diff 中的非可执行行（空行/注释，不在 statements 中）→ 被过滤，不计入 total。"""
        diff_lines = {"app/foo.py": [1, 2, 3, 4, 5, 6, 7, 8]}
        coverage = {
            "app/foo.py": {
                "executed": {1, 2, 3},
                "statements": {1, 2, 3, 6, 7},  # 4, 5, 8 是空行/注释
            }
        }
        covered, total, uncovered = compute_diff_coverage(diff_lines, coverage)
        # 只统计 statements 中的行（1,2,3,6,7），共 5 行
        assert covered == 3
        assert total == 5
        assert uncovered == {"app/foo.py": [6, 7]}

    def test_omitted_file_skipped(self):
        """被 omit 的文件（statements 为空）→ 跳过，不计入 total。"""
        diff_lines = {
            "app/foo.py": [1, 2, 3],
            "main.py": [4, 5, 6],  # main.py 被 omit
        }
        coverage = {
            "app/foo.py": {
                "executed": {1, 2, 3},
                "statements": {1, 2, 3},
            },
            "main.py": {
                "executed": set(),
                "statements": set(),  # 被 omit
            },
        }
        covered, total, uncovered = compute_diff_coverage(diff_lines, coverage)
        # main.py 被跳过，只统计 app/foo.py 的 3 行
        assert covered == 3
        assert total == 3
        assert uncovered == {}

    def test_file_not_in_coverage_skipped(self):
        """diff 中的文件不在 coverage 数据中 → 跳过（statements 为空）。"""
        diff_lines = {
            "app/foo.py": [1, 2, 3],
            "app/missing.py": [4, 5],  # 不在 coverage 中
        }
        coverage = {
            "app/foo.py": {
                "executed": {1, 2, 3},
                "statements": {1, 2, 3},
            },
        }
        covered, total, uncovered = compute_diff_coverage(diff_lines, coverage)
        # app/missing.py 不在 coverage 中，statements 默认空集，被跳过
        assert covered == 3
        assert total == 3
        assert uncovered == {}

    def test_multiple_files(self):
        """多文件 diff → 分别统计。"""
        diff_lines = {
            "app/foo.py": [1, 2, 3],
            "app/bar.py": [10, 20],
        }
        coverage = {
            "app/foo.py": {
                "executed": {1, 2},
                "statements": {1, 2, 3},
            },
            "app/bar.py": {
                "executed": {10},
                "statements": {10, 20},
            },
        }
        covered, total, uncovered = compute_diff_coverage(diff_lines, coverage)
        assert covered == 3
        assert total == 5
        assert uncovered == {"app/foo.py": [3], "app/bar.py": [20]}

    def test_empty_diff(self):
        """空 diff → 0 行，100% 覆盖。"""
        covered, total, uncovered = compute_diff_coverage({}, {})
        assert covered == 0
        assert total == 0
        assert uncovered == {}

    def test_diff_lines_all_non_executable(self):
        """diff 中所有行都是非可执行行（都不在 statements 中）→ 文件被跳过。"""
        diff_lines = {"app/foo.py": [4, 5, 8]}  # 全是空行/注释
        coverage = {
            "app/foo.py": {
                "executed": {1, 2, 3},
                "statements": {1, 2, 3},  # 4, 5, 8 不在 statements 中
            }
        }
        covered, total, uncovered = compute_diff_coverage(diff_lines, coverage)
        # 所有 diff 行都是非可执行行，relevant 为空，文件被跳过
        assert covered == 0
        assert total == 0
        assert uncovered == {}


# ============================================================================
# 集成测试: parse_diff + load_coverage + compute_diff_coverage
# ============================================================================


class TestDiffCoverageIntegration:
    """端到端集成：parse_diff → load_coverage → compute_diff_coverage。"""

    def test_realistic_scenario(self, tmp_path: Path):
        """模拟真实场景：diff 含空行/注释/代码，coverage 有对应数据。"""
        # 模拟 git diff 输出（unified=0）
        # diff 新增 9 行：L336-L344
        diff_text = """diff --git a/app/bootstrap.py b/app/bootstrap.py
--- a/app/bootstrap.py
+++ b/app/bootstrap.py
@@ -335,0 +336,9 @@ def mask_sensitive(value):
+
+
+async def prepare_database_runtime() -> None:
+    \"\"\"docstring\"\"\"
+    import os
+    # comment line
+
+    mode = os.environ.get("X", "external")
+    return
"""
        diff_lines = parse_diff(diff_text)
        # parse_diff 应返回 L336-L344（9 行）
        assert diff_lines == {"app/bootstrap.py": [336, 337, 338, 339, 340, 341, 342, 343, 344]}

        # 模拟 coverage.json
        # coverage.py 只把可执行行算作 statements：
        # - L336, L337, L342 是空行 → 不在 statements 中
        # - L341 是纯注释 → 不在 statements 中
        # - L338 (def), L339 (docstring), L340 (import), L343 (代码), L344 (return) → 在 statements 中
        cov_data = {
            "files": {
                "app/bootstrap.py": {
                    "executed_lines": [338, 339, 340, 343, 344],
                    "missing_lines": [],
                    "excluded_lines": [],
                }
            }
        }
        cov_file = tmp_path / "coverage.json"
        cov_file.write_text(json.dumps(cov_data), encoding="utf-8")
        coverage = load_coverage(cov_file)

        covered, total, uncovered = compute_diff_coverage(diff_lines, coverage)

        # diff 行：336-344（9 行）
        # statements：338, 339, 340, 343, 344（5 行，排除空行 336, 337, 342 和注释 341）
        # executed：338, 339, 340, 343, 344（全部已执行）
        assert covered == 5
        assert total == 5
        assert uncovered == {}
