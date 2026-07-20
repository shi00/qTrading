"""Tests for scripts/scan_weak_assertions.py 弱断言扫描与增量门禁。

验证：
- 弱断言扫描核心逻辑（assert True / assert 1 / 空 test / Mock 弱断言）
- 带理由白名单（`# noqa: weak-assertion <reason>`）识别
- baseline 文件加载/序列化
- --base diff 模式：新增弱断言被检测
- baseline 数量只能下降不能上升（git ref 对比）
- --update-baseline 模式：用当前扫描结果覆盖 baseline
"""

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from scan_weak_assertions import (  # noqa: E402 - sys.path 注入后导入
    WeakAssertion,
    is_whitelisted,
    load_baseline,
    make_signature,
    save_baseline,
    scan_directory,
    scan_file,
)


# ============================================================================
# 工具：构造临时测试目录
# ============================================================================


def _write_test_file(root: Path, rel: str, content: str) -> Path:
    """在 root 下创建 rel 路径的测试文件，返回绝对路径。"""
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


# ============================================================================
# 弱断言扫描核心逻辑
# ============================================================================


class TestScanFile:
    """scan_file 核心检测逻辑。"""

    def test_detects_assert_true(self, tmp_path):
        f = _write_test_file(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
        issues = scan_file(f)
        assert any(i.issue_type == "weak_assert" for i in issues)

    def test_detects_assert_one(self, tmp_path):
        f = _write_test_file(tmp_path, "test_x.py", "def test_a():\n    assert 1\n")
        issues = scan_file(f)
        assert any(i.issue_type == "weak_assert" for i in issues)

    def test_ignores_assert_with_condition(self, tmp_path):
        f = _write_test_file(tmp_path, "test_x.py", "def test_a():\n    x = 1\n    assert x == 1\n")
        issues = scan_file(f)
        assert issues == []

    def test_detects_empty_test_body(self, tmp_path):
        f = _write_test_file(tmp_path, "test_x.py", "def test_a():\n    pass\n")
        issues = scan_file(f)
        assert any(i.issue_type == "empty_test" for i in issues)

    def test_detects_weak_mock_assert(self, tmp_path):
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    m = Mock()\n    m()\n    m.assert_called()\n",
        )
        issues = scan_file(f)
        assert any(i.issue_type == "weak_mock" for i in issues)

    def test_strong_mock_assert_not_flagged(self, tmp_path):
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    m = Mock()\n    m(1)\n    m.assert_called_with(1)\n",
        )
        issues = scan_file(f)
        assert not any(i.issue_type == "weak_mock" for i in issues)

    def test_scan_file_returns_weak_assertion_with_source_line(self, tmp_path):
        f = _write_test_file(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
        issues = scan_file(f)
        assert len(issues) == 1
        issue = issues[0]
        assert isinstance(issue, WeakAssertion)
        assert issue.line_no == 2
        assert issue.source_line.strip() == "assert True"

    def test_scan_file_carries_relative_path(self, tmp_path):
        f = _write_test_file(tmp_path, "sub/test_x.py", "def test_a():\n    assert True\n")
        issues = scan_file(f, rel_path="sub/test_x.py")
        assert len(issues) == 1
        assert issues[0].rel_path == "sub/test_x.py"


# ============================================================================
# scan_directory：目录与单文件路径支持
# ============================================================================


class TestScanDirectory:
    """scan_directory 对目录与单文件路径的扫描行为。

    回归覆盖：``--path <single_file>`` 模式应能扫描该文件，
    而非返回空列表（root.rglob 对文件路径返回空迭代器的 bug 修复）。
    """

    def test_scan_directory_scans_subdir_test_files(self, tmp_path):
        """目录模式：rglob 递归扫描所有 test_*.py。"""
        _write_test_file(tmp_path, "test_a.py", "def test_a():\n    assert True\n")
        _write_test_file(tmp_path, "sub/test_b.py", "def test_b():\n    assert 1\n")
        issues = scan_directory(tmp_path)
        assert len(issues) == 2

    def test_scan_directory_handles_single_file_path(self, tmp_path):
        """单文件模式：root 为文件时应直接扫描该文件，不返回空列表。

        Regression: ``Path.rglob`` 对文件路径返回空迭代器，
        导致 ``--path tests/unit/x.py`` 报告 0 处弱断言。
        """
        f = _write_test_file(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
        issues = scan_directory(f)
        assert len(issues) == 1
        assert issues[0].rel_path == "test_x.py"

    def test_scan_directory_single_file_preserves_filename_as_rel_path(self, tmp_path):
        """单文件模式：rel_path 使用文件名（不报绝对路径）。"""
        f = _write_test_file(tmp_path, "test_unique.py", "def test_a():\n    assert True\n")
        issues = scan_directory(f)
        assert issues[0].rel_path == "test_unique.py"


# ============================================================================
# 白名单（行内 noqa）
# ============================================================================


class TestWhitelist:
    """`# noqa: weak-assertion <reason>` 行内白名单。"""

    def test_whitelisted_with_reason(self):
        line = "    assert True  # noqa: weak-assertion guard for smoke test"
        assert is_whitelisted(line) is True

    def test_whitelisted_with_multi_word_reason(self):
        line = "    assert True  # noqa: weak-assertion smoke guard"
        assert is_whitelisted(line) is True

    def test_not_whitelisted_without_reason(self):
        line = "    assert True  # noqa: weak-assertion"
        assert is_whitelisted(line) is False

    def test_not_whitelisted_without_comment(self):
        line = "    assert True"
        assert is_whitelisted(line) is False

    def test_not_whitelisted_unrelated_noqa(self):
        line = "    assert True  # noqa: E501"
        assert is_whitelisted(line) is False

    def test_scan_file_skips_whitelisted(self, tmp_path):
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    assert True  # noqa: weak-assertion smoke guard\n",
        )
        issues = scan_file(f)
        assert issues == []


# ============================================================================
# 签名
# ============================================================================


class TestSignature:
    """make_signature 生成 (rel_path, type, normalized_line) 三元组。"""

    def test_signature_strips_and_lowercases(self):
        sig = make_signature("tests/test_x.py", "weak_assert", "    assert True\n")
        assert sig == ("tests/test_x.py", "weak_assert", "assert true")

    def test_signature_stable_across_indentation(self):
        sig1 = make_signature("a.py", "weak_assert", "    assert True")
        sig2 = make_signature("a.py", "weak_assert", "\tassert True\n")
        assert sig1 == sig2


# ============================================================================
# baseline 加载与保存
# ============================================================================


class TestBaseline:
    """load_baseline / save_baseline 往返。"""

    def test_save_and_load_roundtrip(self, tmp_path):
        entries = [
            WeakAssertion(
                rel_path="tests/test_a.py",
                line_no=2,
                issue_type="weak_assert",
                detail="assert True / assert 1",
                source_line="    assert True",
            )
        ]
        path = tmp_path / "baseline.json"
        save_baseline(path, entries)
        assert path.exists()
        loaded = load_baseline(path)
        assert len(loaded) == 1
        assert loaded[0].rel_path == "tests/test_a.py"
        assert loaded[0].issue_type == "weak_assert"
        assert loaded[0].source_line.strip() == "assert True"

    def test_baseline_file_includes_total_field(self, tmp_path):
        entries = [
            WeakAssertion(
                rel_path="tests/test_a.py",
                line_no=2,
                issue_type="weak_assert",
                detail="d",
                source_line="    assert True",
            ),
            WeakAssertion(
                rel_path="tests/test_b.py",
                line_no=5,
                issue_type="empty_test",
                detail="d",
                source_line="    pass",
            ),
        ]
        path = tmp_path / "baseline.json"
        save_baseline(path, entries)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert data["total"] == 2
        assert len(data["entries"]) == 2

    def test_load_baseline_missing_file_returns_empty(self, tmp_path):
        loaded = load_baseline(tmp_path / "nonexistent.json")
        assert loaded == []

    def test_load_baseline_invalid_json_returns_empty(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        loaded = load_baseline(path)
        assert loaded == []


# ============================================================================
# --base diff 模式：新增检测
# ============================================================================


class TestDiffMode:
    """scan_directory + baseline 对比，识别新增弱断言。"""

    def test_diff_detects_new_weak_assertion(self, tmp_path):
        _write_test_file(tmp_path, "test_new.py", "def test_a():\n    assert True\n")
        # baseline 为空
        new_issues = _diff(tmp_path, baseline_entries=[])
        assert len(new_issues) == 1
        assert new_issues[0].rel_path == "test_new.py"

    def test_diff_ignores_existing_in_baseline(self, tmp_path):
        _write_test_file(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
        baseline = [
            WeakAssertion(
                rel_path="test_x.py",
                line_no=2,
                issue_type="weak_assert",
                detail="d",
                source_line="    assert True",
            )
        ]
        new_issues = _diff(tmp_path, baseline)
        assert new_issues == []

    def test_diff_ignores_whitelisted(self, tmp_path):
        _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    assert True  # noqa: weak-assertion smoke\n",
        )
        new_issues = _diff(tmp_path, baseline_entries=[])
        assert new_issues == []

    def test_diff_line_drift_tolerant(self, tmp_path):
        """行号漂移不影响识别（按签名匹配）。"""
        _write_test_file(
            tmp_path,
            "test_x.py",
            "# comment\n# comment\n# comment\ndef test_a():\n    assert True\n",
        )
        baseline = [
            WeakAssertion(
                rel_path="test_x.py",
                line_no=2,  # 旧行号
                issue_type="weak_assert",
                detail="d",
                source_line="    assert True",
            )
        ]
        new_issues = _diff(tmp_path, baseline)
        assert new_issues == []

    def test_diff_reports_multiple_new(self, tmp_path):
        _write_test_file(
            tmp_path,
            "test_a.py",
            "def test_a():\n    assert True\n",
        )
        _write_test_file(
            tmp_path,
            "test_b.py",
            "def test_b():\n    assert 1\n",
        )
        new_issues = _diff(tmp_path, baseline_entries=[])
        assert len(new_issues) == 2


def _diff(root: Path, baseline_entries: list) -> list:
    """辅助：扫描 root 并对比 baseline，返回新增列表。"""
    from scan_weak_assertions import compute_new_issues

    current = scan_directory(root)
    return compute_new_issues(current=current, baseline=baseline_entries)


# ============================================================================
# baseline 数量只能下降
# ============================================================================


class TestBaselineShrink:
    """check_baseline_shrink：对比当前 baseline 与 git ref 旧 baseline。"""

    def test_shrink_ok_when_total_equal(self, tmp_path, monkeypatch):
        path = tmp_path / "baseline.json"
        save_baseline(
            path,
            [
                WeakAssertion(
                    rel_path="a.py",
                    line_no=1,
                    issue_type="weak_assert",
                    detail="d",
                    source_line="assert True",
                )
            ],
        )
        # mock git show 返回同样 total 的 baseline
        old_content = json.dumps({"version": 1, "total": 1, "entries": []})
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: old_content,
        )
        from scan_weak_assertions import check_baseline_shrink

        ok, current_total, old_total = check_baseline_shrink(path, ref="origin/main")
        assert ok is True
        assert current_total == 1
        assert old_total == 1

    def test_shrink_ok_when_total_decreased(self, tmp_path, monkeypatch):
        path = tmp_path / "baseline.json"
        save_baseline(path, [])  # 当前 0
        old_content = json.dumps({"version": 1, "total": 3, "entries": []})
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: old_content,
        )
        from scan_weak_assertions import check_baseline_shrink

        ok, current_total, old_total = check_baseline_shrink(path, ref="origin/main")
        assert ok is True
        assert current_total == 0
        assert old_total == 3

    def test_shrink_fails_when_total_increased(self, tmp_path, monkeypatch):
        path = tmp_path / "baseline.json"
        save_baseline(
            path,
            [
                WeakAssertion(
                    rel_path="a.py",
                    line_no=1,
                    issue_type="weak_assert",
                    detail="d",
                    source_line="assert True",
                ),
                WeakAssertion(
                    rel_path="b.py",
                    line_no=2,
                    issue_type="weak_assert",
                    detail="d",
                    source_line="assert 1",
                ),
            ],
        )  # 当前 2
        old_content = json.dumps({"version": 1, "total": 1, "entries": []})
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: old_content,
        )
        from scan_weak_assertions import check_baseline_shrink

        ok, current_total, old_total = check_baseline_shrink(path, ref="origin/main")
        assert ok is False
        assert current_total == 2
        assert old_total == 1

    def test_shrink_skipped_when_git_unavailable(self, tmp_path, monkeypatch):
        path = tmp_path / "baseline.json"
        save_baseline(path, [])
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: None,
        )
        from scan_weak_assertions import check_baseline_shrink

        ok, current_total, old_total = check_baseline_shrink(path, ref="origin/main")
        # git 不可用时跳过 shrink 检查，返回 ok=True，old_total=-1 表示未获取
        assert ok is True
        assert current_total == 0
        assert old_total == -1

    def test_shrink_allows_growth_with_justification(self, tmp_path, monkeypatch):
        """当前 baseline 含非空 growth_justification 字段时，数量增长也允许通过。

        用于修复扫描器 BUG 后的合理增长声明，避免开发者随意绕过门禁的同时
        给工具修复留出可控通道。
        """
        path = tmp_path / "baseline.json"
        save_baseline(
            path,
            [
                WeakAssertion(
                    rel_path="a.py",
                    line_no=1,
                    issue_type="weak_assert",
                    detail="d",
                    source_line="assert True",
                ),
                WeakAssertion(
                    rel_path="b.py",
                    line_no=2,
                    issue_type="weak_assert",
                    detail="d",
                    source_line="assert 1",
                ),
            ],
            growth_justification="修复 _is_is_not_none_assert BUG 后浮现的真弱断言纳入 baseline",
        )  # 当前 2
        old_content = json.dumps({"version": 1, "total": 1, "entries": []})
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: old_content,
        )
        from scan_weak_assertions import check_baseline_shrink

        ok, current_total, old_total = check_baseline_shrink(path, ref="origin/main")
        assert ok is True
        assert current_total == 2
        assert old_total == 1

    def test_shrink_fails_when_growth_with_empty_justification(self, tmp_path, monkeypatch):
        """growth_justification 为空字符串时，数量增长仍失败（防偷懒绕过）。"""
        path = tmp_path / "baseline.json"
        save_baseline(
            path,
            [
                WeakAssertion(
                    rel_path="a.py",
                    line_no=1,
                    issue_type="weak_assert",
                    detail="d",
                    source_line="assert True",
                ),
                WeakAssertion(
                    rel_path="b.py",
                    line_no=2,
                    issue_type="weak_assert",
                    detail="d",
                    source_line="assert 1",
                ),
            ],
            growth_justification="",
        )  # 当前 2，justification 空
        old_content = json.dumps({"version": 1, "total": 1, "entries": []})
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: old_content,
        )
        from scan_weak_assertions import check_baseline_shrink

        ok, current_total, old_total = check_baseline_shrink(path, ref="origin/main")
        assert ok is False
        assert current_total == 2
        assert old_total == 1


# ============================================================================
# --update-baseline 模式
# ============================================================================


class TestUpdateBaseline:
    """--update-baseline 用当前扫描结果覆盖 baseline。"""

    def test_update_writes_current_state(self, tmp_path):
        _write_test_file(tmp_path, "test_a.py", "def test_a():\n    assert True\n")
        _write_test_file(
            tmp_path,
            "test_b.py",
            "def test_b():\n    assert True  # noqa: weak-assertion smoke\n",
        )
        baseline_path = tmp_path / "baseline.json"
        current = scan_directory(tmp_path)
        save_baseline(baseline_path, current)
        loaded = load_baseline(baseline_path)
        # 白名单的不进 baseline（已被允许，不算存量弱断言）
        assert len(loaded) == 1
        assert loaded[0].rel_path == "test_a.py"

    def test_update_overwrites_existing(self, tmp_path):
        baseline_path = tmp_path / "baseline.json"
        # 旧 baseline 有 3 条
        save_baseline(
            baseline_path,
            [
                WeakAssertion(
                    rel_path="old.py",
                    line_no=i,
                    issue_type="weak_assert",
                    detail="d",
                    source_line=f"assert {i}",
                )
                for i in range(3)
            ],
        )
        # 当前只有 1 条
        _write_test_file(tmp_path, "test_new.py", "def test_a():\n    assert True\n")
        current = scan_directory(tmp_path)
        save_baseline(baseline_path, current)
        loaded = load_baseline(baseline_path)
        assert len(loaded) == 1
        assert loaded[0].rel_path == "test_new.py"


# ============================================================================
# 集成测试：main()
# ============================================================================


class TestMainIntegration:
    """main() 命令行入口的集成测试。"""

    def test_main_base_mode_fails_on_new(self, tmp_path, capsys, monkeypatch):
        _write_test_file(tmp_path, "test_new.py", "def test_a():\n    assert True\n")
        baseline_path = tmp_path / "baseline.json"
        save_baseline(baseline_path, [])
        # 跳过 shrink 检查
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: None,
        )
        from scan_weak_assertions import main

        rc = main(
            [
                "--path",
                str(tmp_path),
                "--base",
                str(baseline_path),
            ]
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert "新增" in out or "new" in out.lower()

    def test_main_base_mode_passes_on_clean(self, tmp_path, capsys, monkeypatch):
        _write_test_file(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
        baseline_path = tmp_path / "baseline.json"
        current = scan_directory(tmp_path)
        save_baseline(baseline_path, current)
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: json.dumps({"version": 1, "total": 1, "entries": []}),
        )
        from scan_weak_assertions import main

        rc = main(
            [
                "--path",
                str(tmp_path),
                "--base",
                str(baseline_path),
            ]
        )
        assert rc == 0

    def test_main_base_mode_fails_on_baseline_growth(self, tmp_path, capsys, monkeypatch):
        _write_test_file(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
        _write_test_file(tmp_path, "test_y.py", "def test_b():\n    assert 1\n")
        baseline_path = tmp_path / "baseline.json"
        current = scan_directory(tmp_path)
        save_baseline(baseline_path, current)  # 当前 2
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: json.dumps({"version": 1, "total": 1, "entries": []}),
        )
        from scan_weak_assertions import main

        rc = main(
            [
                "--path",
                str(tmp_path),
                "--base",
                str(baseline_path),
            ]
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert "baseline" in out.lower() or "上升" in out

    def test_main_update_baseline_writes_file(self, tmp_path, capsys):
        _write_test_file(tmp_path, "test_a.py", "def test_a():\n    assert True\n")
        baseline_path = tmp_path / "baseline.json"
        from scan_weak_assertions import main

        rc = main(
            [
                "--path",
                str(tmp_path),
                "--update-baseline",
                str(baseline_path),
            ]
        )
        assert rc == 0
        assert baseline_path.exists()
        loaded = load_baseline(baseline_path)
        assert len(loaded) == 1

    def test_main_advisory_mode_still_works(self, tmp_path, capsys):
        """原有 advisory 模式（不带 --strict）应保持向后兼容。"""
        _write_test_file(tmp_path, "test_a.py", "def test_a():\n    assert True\n")
        from scan_weak_assertions import main

        rc = main(["--path", str(tmp_path)])
        # advisory 模式退出码 0（仅警告）
        assert rc == 0


# ============================================================================
# 扩展规则（Task 2.3 新增 4 条）
# ============================================================================


class TestWeakCalledFlag:
    """规则 ①：assert m.called is True / assert m.called。

    Mock.called 是裸布尔标志，不验证调用参数。
    """

    def test_detects_assert_mock_called_is_true(self, tmp_path):
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    m = Mock()\n    m()\n    assert m.called is True\n",
        )
        issues = scan_file(f)
        assert any(i.issue_type == "weak_called_flag" for i in issues)

    def test_detects_assert_mock_called_alone(self, tmp_path):
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    m = Mock()\n    m()\n    assert m.called\n",
        )
        issues = scan_file(f)
        assert any(i.issue_type == "weak_called_flag" for i in issues)

    def test_ignores_assert_with_call_args(self, tmp_path):
        """assert m.call_args is not None 不算弱断言（验了 call_args 属性）。"""
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    m = Mock()\n    m(1, 2)\n    assert m.call_args is not None\n",
        )
        issues = scan_file(f)
        assert not any(i.issue_type == "weak_called_flag" for i in issues)


class TestWeakCallCount:
    """规则 ②：assert len(mock.calls) >= 1 / assert len(mock.call_args_list) >= 1。

    仅验证调用次数，不验证参数。
    """

    def test_detects_assert_len_mock_calls_ge_1(self, tmp_path):
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    m = Mock()\n    m()\n    assert len(m.calls) >= 1\n",
        )
        issues = scan_file(f)
        assert any(i.issue_type == "weak_call_count" for i in issues)

    def test_detects_assert_len_mock_call_args_list(self, tmp_path):
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    m = Mock()\n    m()\n    assert len(m.call_args_list) >= 1\n",
        )
        issues = scan_file(f)
        assert any(i.issue_type == "weak_call_count" for i in issues)

    def test_ignores_assert_len_with_content_check(self, tmp_path):
        """assert len(m.call_args_list) == 2 + 后续断言 call_args 不算弱断言。"""
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    m = Mock()\n    m(1)\n    m(2)\n    assert len(m.call_args_list) == 2\n    assert m.call_args_list[0] == call(1)\n",
        )
        issues = scan_file(f)
        assert not any(i.issue_type == "weak_call_count" for i in issues)


class TestWeakRaisesOnly:
    """规则 ③：pytest.raises(SomeError) 后无进一步断言。

    仅验证抛异常不验 message/type 细节。
    """

    def test_detects_pytest_raises_no_further_assert(self, tmp_path):
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    with pytest.raises(ValueError):\n        func()\n",
        )
        issues = scan_file(f)
        assert any(i.issue_type == "weak_raises_only" for i in issues)

    def test_ignores_pytest_raises_with_match(self, tmp_path):
        """pytest.raises(ValueError, match=...) 不算弱断言。"""
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    with pytest.raises(ValueError, match='invalid'):\n        func()\n",
        )
        issues = scan_file(f)
        assert not any(i.issue_type == "weak_raises_only" for i in issues)

    def test_ignores_pytest_raises_with_exception_info_check(self, tmp_path):
        """with pytest.raises(...) as exc_info: + assert str(exc_info.value) 不算弱断言。"""
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    with pytest.raises(ValueError) as exc_info:\n        func()\n    assert 'invalid' in str(exc_info.value)\n",
        )
        issues = scan_file(f)
        assert not any(i.issue_type == "weak_raises_only" for i in issues)


class TestWeakPrint:
    """规则 ④：print(...) 替代断言。

    测试中用 print 输出而非 assert 验证，属弱断言。
    """

    def test_detects_print_in_test_function(self, tmp_path):
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    result = func()\n    print(result)\n",
        )
        issues = scan_file(f)
        assert any(i.issue_type == "weak_print" for i in issues)

    def test_ignores_print_in_non_test_function(self, tmp_path):
        """非 test_ 开头函数中的 print 不算弱断言。"""
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def helper():\n    print('debug')\n",
        )
        issues = scan_file(f)
        assert not any(i.issue_type == "weak_print" for i in issues)

    def test_ignores_print_with_assert_following(self, tmp_path):
        """print 后紧跟 assert 的不算弱断言（print 仅作调试输出）。"""
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    result = func()\n    print(result)\n    assert result == 1\n",
        )
        issues = scan_file(f)
        assert not any(i.issue_type == "weak_print" for i in issues)


class TestWeakIsNotNone:
    """规则 ⑤：assert X is not None 作为函数唯一终态（链式非终态除外）。

    仅验证存在性不验证行为/参数的弱断言。注意：``assert X is None`` 是
    验证负向返回值的强断言（如边界测试 ``assert func(None) is None``），
    绝不可被误判为弱断言。
    """

    def test_detects_assert_is_not_none_as_terminal(self, tmp_path):
        """assert X is not None 作为函数唯一终态 → weak_is_not_none。"""
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    result = func()\n    assert result is not None\n",
        )
        issues = scan_file(f)
        assert any(i.issue_type == "weak_is_not_none" for i in issues)

    def test_ignores_assert_is_none(self, tmp_path):
        """assert X is None 是验证负向返回值的强断言，不应被识别为弱断言。

        Regression: ``_is_is_not_none_assert`` 曾误将 ``ast.Is`` 判定为
        ``is not None`` 形式，导致大量边界测试被误报。
        """
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    result = func(None)\n    assert result is None\n",
        )
        issues = scan_file(f)
        assert not any(i.issue_type == "weak_is_not_none" for i in issues)

    def test_ignores_is_not_none_with_other_assertions(self, tmp_path):
        """函数内还有其他内容断言时，is not None 是前置条件，不算弱断言。"""
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    result = func()\n    assert result is not None\n    assert result.value == 42\n",
        )
        issues = scan_file(f)
        assert not any(i.issue_type == "weak_is_not_none" for i in issues)

    def test_ignores_is_not_none_in_non_test_function(self, tmp_path):
        """非 test_ 开头函数内的 is not None 不算弱断言。"""
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def helper():\n    result = func()\n    assert result is not None\n",
        )
        issues = scan_file(f)
        assert not any(i.issue_type == "weak_is_not_none" for i in issues)

    def test_detects_is_not_none_with_subscript(self, tmp_path):
        """assert obj.attr is not None 作为唯一终态 → weak_is_not_none。"""
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    obj = build()\n    assert obj.attr is not None\n",
        )
        issues = scan_file(f)
        assert any(i.issue_type == "weak_is_not_none" for i in issues)
